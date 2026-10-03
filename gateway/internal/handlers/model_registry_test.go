package handlers

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func TestInvokeModelEndpointUsesVersionHeaderAndParsesJSON(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if got := request.Header.Get("X-OmniRoute-Model-Version"); got != "canary-v1" {
			t.Fatalf("version header = %q", got)
		}
		var payload map[string]any
		if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
			t.Fatalf("decode request: %v", err)
		}
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"plan":{"name":"provider-plan","estimated_cost":123}}`))
	}))
	defer server.Close()

	output, err := invokeModelEndpoint(context.Background(), models.ModelDeployment{Version: "canary-v1", Endpoint: server.URL}, map[string]any{"days": 2})
	if err != nil {
		t.Fatalf("invokeModelEndpoint returned error: %v", err)
	}
	plan, ok := output["plan"].(map[string]any)
	if !ok || plan["name"] != "provider-plan" {
		t.Fatalf("unexpected model output: %#v", output)
	}
}

func TestInvokeModelEndpointWithoutEndpointIsExplicitNoop(t *testing.T) {
	output, err := invokeModelEndpoint(context.Background(), models.ModelDeployment{Version: "shadow-v1"}, map[string]any{})
	if err != nil || output != nil {
		t.Fatalf("expected nil output without endpoint, got output=%#v err=%v", output, err)
	}
}

func TestDeploymentPassesRuntimeGateFailsLowQualityCanary(t *testing.T) {
	base := `{"offline_score":0.9,"drift_score":0.1,"evaluation_samples":20,"validation_samples":5,"rollback_seconds":60}`
	if deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: `{"offline_score":0.79,"drift_score":0.1,"evaluation_samples":20,"validation_samples":5,"rollback_seconds":60}`}) {
		t.Fatal("low offline score must fail the runtime gate")
	}
	if deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: `{"offline_score":0.9,"drift_score":0.21,"evaluation_samples":20,"validation_samples":5,"rollback_seconds":60}`}) {
		t.Fatal("high drift must fail the runtime gate")
	}
	if !deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: base}) {
		t.Fatal("deployment at the gate should remain eligible")
	}
	if deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: `{"offline_score":0.9,"drift_score":0.1,"evaluation_samples":19,"validation_samples":5,"rollback_seconds":60}`}) {
		t.Fatal("insufficient evaluation samples must fail the runtime gate")
	}
}

func TestEvaluationMetricsFromReportNormalizesGatewayContract(t *testing.T) {
	metrics, err := evaluationMetricsFromReport(map[string]any{
		"status":                    "ready",
		"promotion_ready":           true,
		"evaluation_samples":        float64(40),
		"validation_samples":        float64(8),
		"offline_score":             0.91,
		"drift_score":               0.04,
		"counterfactual_samples":    float64(35),
		"fairness_min_utility":      0.78,
		"fairness_utility_variance": 0.03,
	})
	if err != nil {
		t.Fatalf("evaluation report rejected: %v", err)
	}
	if metrics["evaluation_ready"] != 1 || metrics["counterfactual_samples"] != 35 {
		t.Fatalf("unexpected normalized metrics: %#v", metrics)
	}
	if !deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: mustJSON(t, metrics)}) {
		t.Fatal("ready evaluator report should pass the runtime gate")
	}
}

func TestEvaluationMetricsFromBlockedReportDoesNotInventMissingEvidence(t *testing.T) {
	metrics, err := evaluationMetricsFromReport(map[string]any{
		"status":          "insufficient_evidence",
		"promotion_ready": false,
		"reasons":         []any{"样本不足"},
	})
	if err != nil {
		t.Fatalf("blocked report should be storable: %v", err)
	}
	if metrics["evaluation_ready"] != 0 {
		t.Fatalf("blocked report must not be ready: %#v", metrics)
	}
	if _, exists := metrics["offline_score"]; exists {
		t.Fatal("missing offline score must not be converted to zero")
	}
	if deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: mustJSON(t, metrics)}) {
		t.Fatal("blocked evaluator report must fail the runtime gate")
	}
}

func TestProviderQualityGateIsOptionalWhenNoSupplierReportExists(t *testing.T) {
	metrics, err := evaluationMetricsFromReport(map[string]any{
		"status": "ready", "promotion_ready": true,
		"evaluation_samples": 40.0, "validation_samples": 8.0,
		"offline_score": 0.91, "drift_score": 0.04, "counterfactual_samples": 35.0,
		"fairness_utility_variance": 0.03,
		"provider_quality_required": false, "provider_quality_status": "not_required",
	})
	if err != nil || metrics["provider_quality_required"] != 0 {
		t.Fatalf("legacy report should remain optional: metrics=%#v err=%v", metrics, err)
	}
	if !deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: mustJSON(t, metrics)}) {
		t.Fatalf("missing supplier telemetry must not block legacy evidence")
	}
}

func TestProviderQualityGateBlocksSparseOrUnsafeSupplierEvidence(t *testing.T) {
	base := map[string]any{
		"status": "ready", "promotion_ready": true,
		"evaluation_samples": 40.0, "validation_samples": 8.0,
		"offline_score": 0.91, "drift_score": 0.04, "counterfactual_samples": 35.0,
		"fairness_utility_variance": 0.03,
		"provider_quality_required": true, "provider_quality_status": "available",
		"provider_quality_providers": 1.0, "provider_quality_ready_providers": 1.0,
		"provider_quality_insufficient_providers": 0.0, "provider_quality_blocked_providers": 0.0,
	}
	metrics, err := evaluationMetricsFromReport(base)
	if err != nil || !deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: mustJSON(t, metrics)}) {
		t.Fatalf("healthy supplier evidence should pass: metrics=%#v err=%v", metrics, err)
	}
	base["provider_quality_status"] = "insufficient_data"
	metrics, err = evaluationMetricsFromReport(base)
	if err != nil {
		t.Fatalf("sparse supplier report should be stored: %v", err)
	}
	if deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: mustJSON(t, metrics)}) {
		t.Fatal("sparse supplier evidence must fail the runtime gate")
	}
	base["provider_quality_status"] = "available"
	base["provider_quality_blocked_providers"] = 1.0
	metrics, err = evaluationMetricsFromReport(base)
	if err != nil {
		t.Fatalf("blocked supplier report should be stored: %v", err)
	}
	if deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: mustJSON(t, metrics)}) {
		t.Fatal("unsafe supplier evidence must fail the runtime gate")
	}
}

func TestBehaviorPolicyGateBlocksInsufficientComparison(t *testing.T) {
	metrics, err := evaluationMetricsFromReport(map[string]any{
		"status": "ready", "promotion_ready": true,
		"evaluation_samples": 40.0, "validation_samples": 8.0,
		"offline_score": 0.91, "drift_score": 0.04, "counterfactual_samples": 35.0,
		"fairness_utility_variance":  0.03,
		"policy_comparison_required": true, "policy_comparison_status": "insufficient_data",
		"policy_baseline_samples": 8.0, "policy_behavior_samples": 2.0,
	})
	if err != nil {
		t.Fatalf("policy report should be stored as blocked evidence: %v", err)
	}
	if deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: mustJSON(t, metrics)}) {
		t.Fatalf("insufficient policy comparison must fail gate: %#v", metrics)
	}
}

func TestBehaviorPolicyGateRequiresPositiveLiftAndBothArms(t *testing.T) {
	base := map[string]any{
		"status": "ready", "promotion_ready": true,
		"evaluation_samples": 40.0, "validation_samples": 8.0,
		"offline_score": 0.91, "drift_score": 0.04, "counterfactual_samples": 35.0,
		"fairness_utility_variance":  0.03,
		"policy_comparison_required": true, "policy_comparison_status": "available",
		"policy_comparison_ready": true, "policy_baseline_samples": 5.0, "policy_behavior_samples": 5.0,
	}
	metrics, err := evaluationMetricsFromReport(base)
	if err != nil {
		t.Fatalf("policy report rejected: %v", err)
	}
	if deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: mustJSON(t, metrics)}) {
		t.Fatal("missing reward lift must not pass the gate")
	}
	base["policy_reward_lift"] = -0.01
	metrics, err = evaluationMetricsFromReport(base)
	if err != nil {
		t.Fatalf("negative lift report rejected unexpectedly: %v", err)
	}
	if deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: mustJSON(t, metrics)}) {
		t.Fatal("negative reward lift must fail the gate")
	}
	base["policy_reward_lift"] = 0.02
	metrics, err = evaluationMetricsFromReport(base)
	if err != nil || !deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: mustJSON(t, metrics)}) {
		t.Fatalf("positive lift with both arms should pass: metrics=%#v err=%v", metrics, err)
	}
}

func TestSegmentFairnessGateBlocksBreachedGroupSpread(t *testing.T) {
	base := map[string]any{
		"status": "ready", "promotion_ready": true,
		"evaluation_samples": 40.0, "validation_samples": 8.0,
		"offline_score": 0.91, "drift_score": 0.04, "counterfactual_samples": 35.0,
		"fairness_utility_variance": 0.03,
		"fairness_segment_required": true, "fairness_segment_status": "available",
		"fairness_segment_max_stddev": 0.08, "fairness_segment_threshold_exceeded": 0.0,
	}
	metrics, err := evaluationMetricsFromReport(base)
	if err != nil || !deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: mustJSON(t, metrics)}) {
		t.Fatalf("healthy segment fairness should pass: metrics=%#v err=%v", metrics, err)
	}
	base["fairness_segment_max_stddev"] = 0.11
	metrics, err = evaluationMetricsFromReport(base)
	if err != nil {
		t.Fatalf("breached segment report rejected unexpectedly: %v", err)
	}
	if deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: mustJSON(t, metrics)}) {
		t.Fatal("segment fairness spread above threshold must fail the gate")
	}
	base["fairness_segment_max_stddev"] = 0.08
	base["fairness_segment_threshold_exceeded"] = 1.0
	metrics, err = evaluationMetricsFromReport(base)
	if err != nil {
		t.Fatalf("threshold-exceeded report rejected unexpectedly: %v", err)
	}
	if deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: mustJSON(t, metrics)}) {
		t.Fatal("any segment threshold breach must fail the gate")
	}
}

func TestSegmentFairnessReasonsProtectCanaryAfterPromotion(t *testing.T) {
	deployment := models.ModelDeployment{Metrics: `{"fairness_segment_required":1,"fairness_segment_ready":1,"fairness_segment_max_stddev":0.08,"fairness_segment_threshold_exceeded":0}`}
	if reasons := segmentFairnessReasons(deployment); len(reasons) != 0 {
		t.Fatalf("healthy persisted segment metrics should not rollback: %#v", reasons)
	}
	deployment.Metrics = `{"fairness_segment_required":1,"fairness_segment_ready":1,"fairness_segment_max_stddev":0.18,"fairness_segment_threshold_exceeded":0}`
	reasons := segmentFairnessReasons(deployment)
	if len(reasons) != 1 || reasons[0] != "segment_fairness_variance_exceeded" {
		t.Fatalf("variance breach should be explicit: %#v", reasons)
	}
}

func TestModelSegmentFairnessEstimateRequiresPerGroupEvidence(t *testing.T) {
	events := make([]models.PlanningEvent, 0, 11)
	for index := 0; index < 10; index++ {
		events = append(events, models.PlanningEvent{EventType: "planning_decision_audit", Payload: fmt.Sprintf(`{"traveler_group":"pair","fairness_utility_stddev":0.04,"fairness_threshold_exceeded":false,"destination":"杭州","season":"summer","budget_bucket":"3000_10000"}`)})
	}
	events = append(events, models.PlanningEvent{EventType: "planning_decision_audit", Payload: `{"traveler_group":"family","fairness_utility_stddev":0.2,"fairness_threshold_exceeded":true}`})
	result := modelSegmentFairnessEstimate(events)
	if result["status"] != "available" || result["max_stddev"] != 0.04 {
		t.Fatalf("unexpected segment fairness estimate: %#v", result)
	}
	if result["threshold_exceeded"] != 0 {
		t.Fatalf("sparse family segment must not borrow evidence: %#v", result)
	}
}

func mustJSON(t *testing.T, value any) string {
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("marshal test metrics: %v", err)
	}
	return string(encoded)
}

func TestCanaryRuntimeHealthRequiresMinimumSamples(t *testing.T) {
	events := []models.PlanningEvent{{
		EventType: "plan_generated", CreatedAt: time.Now(),
		Payload: `{"model_version":"canary-v1"}`,
	}}
	health := canaryRuntimeHealth(models.ModelDeployment{Version: "canary-v1"}, events, time.Now())
	if health["status"] != "insufficient_data" || health["rollback_required"] == true {
		t.Fatalf("sparse canary should not rollback: %#v", health)
	}
}

func TestCanaryRuntimeHealthFlagsHighErrorRate(t *testing.T) {
	events := make([]models.PlanningEvent, 0, 12)
	for index := 0; index < 10; index++ {
		events = append(events, models.PlanningEvent{EventType: "plan_generated", CreatedAt: time.Now(), Payload: `{"model_version":"canary-v1"}`})
	}
	events = append(events,
		models.PlanningEvent{EventType: "agent_metric", CreatedAt: time.Now(), Payload: `{"model_version":"canary-v1","metric":"error_rate","value":0.31}`},
		models.PlanningEvent{EventType: "agent_metric", CreatedAt: time.Now(), Payload: `{"model_version":"canary-v1","metric":"constraint_satisfaction_rate","value":0.99}`},
	)
	health := canaryRuntimeHealth(models.ModelDeployment{Version: "canary-v1"}, events, time.Now())
	if health["status"] != "rollback_required" || health["rollback_required"] != true {
		t.Fatalf("high error rate must trigger rollback: %#v", health)
	}
}

func TestCanaryRuntimeHealthIgnoresOtherModelVersions(t *testing.T) {
	events := make([]models.PlanningEvent, 0, 10)
	for index := 0; index < 10; index++ {
		events = append(events, models.PlanningEvent{EventType: "plan_generated", CreatedAt: time.Now(), Payload: `{"model_version":"active-v1"}`})
	}
	health := canaryRuntimeHealth(models.ModelDeployment{Version: "canary-v1"}, events, time.Now())
	if health["samples"] != 0 || health["status"] != "insufficient_data" {
		t.Fatalf("other versions must not count toward canary health: %#v", health)
	}
}

func TestMonitorCanaryDeploymentsRollsBackAndAuditsSegmentFairnessBreach(t *testing.T) {
	oldDB := database.DB
	db, err := gorm.Open(sqlite.Open("file:canary-segment-rollback-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	database.DB = db
	defer func() { database.DB = oldDB }()
	if err := db.AutoMigrate(&models.ModelDeployment{}, &models.PlanningEvent{}, &models.ModelAuditEvent{}); err != nil {
		t.Fatalf("migrate canary tables: %v", err)
	}
	canary := models.ModelDeployment{
		Name: "planner", Version: "canary-segment-v1", Status: "canary", Traffic: 25,
		Metrics: `{"fairness_segment_required":1,"fairness_segment_ready":1,"fairness_segment_max_stddev":0.18,"fairness_segment_threshold_exceeded":0}`,
	}
	if err := db.Create(&canary).Error; err != nil {
		t.Fatalf("seed canary deployment: %v", err)
	}
	if err := db.Create(&models.ModelDeployment{Name: "planner", Version: "active-v1", Status: "active", Traffic: 100, Metrics: `{}`}).Error; err != nil {
		t.Fatalf("seed active deployment: %v", err)
	}
	monitorCanaryDeployments()
	var loaded models.ModelDeployment
	if err := db.Where("version = ?", "canary-segment-v1").First(&loaded).Error; err != nil {
		t.Fatalf("load canary deployment: %v", err)
	}
	if loaded.Status != "rolled_back" || loaded.Traffic != 0 {
		t.Fatalf("segment fairness breach must rollback canary, got status=%s traffic=%d", loaded.Status, loaded.Traffic)
	}
	var audit models.ModelAuditEvent
	if err := db.Where("kind = ? AND version = ?", "rollback", "canary-segment-v1").First(&audit).Error; err != nil {
		t.Fatalf("rollback audit missing: %v", err)
	}
	reasons := decodeModelAuditReasonCodes(audit.ReasonCodes)
	if len(reasons) != 1 || reasons[0] != "segment_fairness_variance_exceeded" {
		t.Fatalf("unexpected segment rollback reasons: %#v", reasons)
	}
}

func TestBuildModelAuditTimelineRedactsAndSortsEvents(t *testing.T) {
	created := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	updated := created.Add(2 * time.Hour)
	deployments := []models.ModelDeployment{{
		Version: "planner-v4", Status: "rolled_back", CreatedAt: created, UpdatedAt: updated,
		Metrics: `{"runtime_rollback":1,"runtime_rollback_at_unix":1760000000,"runtime_rollback_reason_count":2}`,
	}}
	events := []models.PlanningEvent{{
		EventType: "model_shadow_executed", CreatedAt: created.Add(30 * time.Minute),
		UserID: "private-user", TripID: "private-trip", Payload: `{"model_version":"planner-v4","prediction_summary":{"token":"secret"}}`,
	}}
	audit := map[string]any{"entries": []any{map[string]any{
		"recorded_at": float64(created.Add(time.Hour).Unix()), "status": "insufficient_evidence", "model_version": "planner-v4", "sample_count": float64(3),
	}}}
	timeline := buildModelAuditTimeline(audit, deployments, events)
	if len(timeline) < 3 {
		t.Fatalf("expected combined timeline, got %#v", timeline)
	}
	firstAt := timeline[0]["timestamp"].(int64)
	for _, item := range timeline[1:] {
		if item["timestamp"].(int64) > firstAt {
			t.Fatalf("timeline is not newest-first: %#v", timeline)
		}
		if _, exists := item["user_id"]; exists {
			t.Fatal("timeline must not expose user id")
		}
		if _, exists := item["trip_id"]; exists {
			t.Fatal("timeline must not expose trip id")
		}
	}
	for _, item := range timeline {
		if _, exists := item["token"]; exists {
			t.Fatal("timeline must not expose raw payload fields")
		}
	}
}

func TestPersistModelAuditTimelineIsIdempotentAndFilterable(t *testing.T) {
	oldDB := database.DB
	db, err := gorm.Open(sqlite.Open("file:model-audit-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	database.DB = db
	defer func() { database.DB = oldDB }()
	if err := db.AutoMigrate(&models.ModelAuditEvent{}); err != nil {
		t.Fatalf("migrate audit table: %v", err)
	}
	when := time.Date(2026, 2, 1, 0, 0, 0, 0, time.UTC)
	timeline := []map[string]any{{"kind": "evaluation", "version": "planner-v4", "status": "ready", "summary": "评估任务已执行", "sample_count": 30, "timestamp": when.Unix()}}
	persistModelAuditTimeline(timeline)
	persistModelAuditTimeline(timeline)
	var count int64
	db.Model(&models.ModelAuditEvent{}).Count(&count)
	if count != 1 {
		t.Fatalf("expected idempotent single event, got %d", count)
	}
	loaded := loadPersistedModelAuditTimeline(30, "planner-v4", when.Add(-time.Hour))
	if len(loaded) != 1 || loaded[0]["version"] != "planner-v4" {
		t.Fatalf("unexpected filtered timeline: %#v", loaded)
	}
	if filtered := loadPersistedModelAuditTimeline(30, "other-version", when.Add(-time.Hour)); len(filtered) != 0 {
		t.Fatalf("version filter leaked events: %#v", filtered)
	}
}

func TestRecordModelAuditEventTxRollsBackWithBusinessTransaction(t *testing.T) {
	oldDB := database.DB
	db, err := gorm.Open(sqlite.Open("file:model-audit-tx-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	database.DB = db
	defer func() { database.DB = oldDB }()
	if err := db.AutoMigrate(&models.ModelAuditEvent{}); err != nil {
		t.Fatalf("migrate audit table: %v", err)
	}
	when := time.Date(2026, 3, 1, 0, 0, 0, 0, time.UTC)
	rollbackErr := db.Transaction(func(tx *gorm.DB) error {
		recordModelAuditEventTx(tx, "evaluation", "planner-tx", "ready", "should roll back", 2, 0, when)
		return fmt.Errorf("force rollback")
	})
	if rollbackErr == nil {
		t.Fatal("expected transaction rollback")
	}
	var count int64
	db.Model(&models.ModelAuditEvent{}).Count(&count)
	if count != 0 {
		t.Fatalf("audit event escaped rolled back transaction: %d", count)
	}
	if err := db.Transaction(func(tx *gorm.DB) error {
		recordModelAuditEventTx(tx, "evaluation", "planner-tx", "ready", "should commit", 2, 0, when)
		return nil
	}); err != nil {
		t.Fatalf("commit transaction: %v", err)
	}
	db.Model(&models.ModelAuditEvent{}).Count(&count)
	if count != 1 {
		t.Fatalf("expected committed audit event, got %d", count)
	}
}

func TestModelAuditReasonCodesAreWhitelistedAndPersisted(t *testing.T) {
	oldDB := database.DB
	db, err := gorm.Open(sqlite.Open("file:model-audit-reasons-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	database.DB = db
	defer func() { database.DB = oldDB }()
	if err := db.AutoMigrate(&models.ModelAuditEvent{}); err != nil {
		t.Fatalf("migrate audit table: %v", err)
	}
	when := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	recordModelAuditEventReasonsTx(db, "evaluation", "planner-reasons", "insufficient_evidence", "blocked", 3, 9, []string{"drift_above_threshold", "not_allowed", "evaluation_samples_insufficient", "drift_above_threshold"}, when)
	var event models.ModelAuditEvent
	if err := db.First(&event).Error; err != nil {
		t.Fatalf("read audit event: %v", err)
	}
	if got := decodeModelAuditReasonCodes(event.ReasonCodes); len(got) != 2 || got[0] != "drift_above_threshold" || got[1] != "evaluation_samples_insufficient" {
		t.Fatalf("unexpected reason codes: %#v", got)
	}
	loaded := loadPersistedModelAuditTimeline(10, "planner-reasons", when.Add(-time.Hour))
	if len(loaded) != 1 {
		t.Fatalf("expected one loaded event, got %#v", loaded)
	}
	if codes, ok := loaded[0]["reason_codes"].([]string); !ok || len(codes) != 2 {
		t.Fatalf("reason codes not exposed in timeline: %#v", loaded[0]["reason_codes"])
	}
}

func TestCleanupModelAuditEventsHonorsRetentionAndBounds(t *testing.T) {
	oldDB := database.DB
	db, err := gorm.Open(sqlite.Open("file:model-audit-cleanup-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	database.DB = db
	defer func() { database.DB = oldDB }()
	if err := db.AutoMigrate(&models.ModelAuditEvent{}); err != nil {
		t.Fatalf("migrate audit table: %v", err)
	}
	now := time.Now().UTC()
	events := []models.ModelAuditEvent{
		{ID: "cleanup-old", Kind: "evaluation", Version: "v1", Status: "ready", Summary: "old", OccurredAt: now.Add(-10 * 24 * time.Hour)},
		{ID: "cleanup-new", Kind: "evaluation", Version: "v1", Status: "ready", Summary: "new", OccurredAt: now.Add(-2 * 24 * time.Hour)},
	}
	if err := db.Create(&events).Error; err != nil {
		t.Fatalf("seed events: %v", err)
	}
	previous := os.Getenv("MODEL_AUDIT_RETENTION_DAYS")
	defer os.Setenv("MODEL_AUDIT_RETENTION_DAYS", previous)
	if err := os.Setenv("MODEL_AUDIT_RETENTION_DAYS", "7"); err != nil {
		t.Fatal(err)
	}
	cleanupModelAuditEvents()
	var remaining []models.ModelAuditEvent
	if err := db.Find(&remaining).Error; err != nil {
		t.Fatalf("read remaining events: %v", err)
	}
	if len(remaining) != 1 || remaining[0].ID != "cleanup-new" {
		t.Fatalf("unexpected retention result: %#v", remaining)
	}
	if err := os.Setenv("MODEL_AUDIT_RETENTION_DAYS", "1"); err != nil {
		t.Fatal(err)
	}
	cleanupModelAuditEvents()
	if err := db.First(&models.ModelAuditEvent{}, "id = ?", "cleanup-new").Error; err != nil {
		t.Fatalf("retention lower bound should clamp to seven days: %v", err)
	}
}

func TestModelAuditCleanupSnapshotReportsRunAndDeletion(t *testing.T) {
	oldDB := database.DB
	db, err := gorm.Open(sqlite.Open("file:model-audit-cleanup-health-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	database.DB = db
	defer func() { database.DB = oldDB }()
	if err := db.AutoMigrate(&models.ModelAuditEvent{}); err != nil {
		t.Fatalf("migrate audit table: %v", err)
	}
	if err := db.Create(&models.ModelAuditEvent{ID: "health-old", Kind: "evaluation", Version: "v1", Status: "ready", Summary: "old", OccurredAt: time.Now().UTC().Add(-400 * 24 * time.Hour)}).Error; err != nil {
		t.Fatalf("seed old event: %v", err)
	}
	previous := os.Getenv("MODEL_AUDIT_RETENTION_DAYS")
	defer os.Setenv("MODEL_AUDIT_RETENTION_DAYS", previous)
	if err := os.Setenv("MODEL_AUDIT_RETENTION_DAYS", "7"); err != nil {
		t.Fatal(err)
	}
	cleanupModelAuditEvents()
	snapshot := modelAuditCleanupSnapshot()
	if snapshot["status"] != "healthy" || snapshot["last_deleted"] != int64(1) {
		t.Fatalf("unexpected cleanup snapshot: %#v", snapshot)
	}
	if snapshot["retention_days"] != 7 {
		t.Fatalf("unexpected retention in snapshot: %#v", snapshot["retention_days"])
	}
}

func TestMergeModelAuditTimelinesFiltersAndDoesNotMutateInputs(t *testing.T) {
	since := time.Date(2026, 4, 1, 0, 0, 0, 0, time.UTC)
	persisted := []map[string]any{{"id": "p1", "kind": "evaluation", "version": "v1", "status": "ready", "summary": "persisted", "timestamp": since.Add(time.Hour).Unix()}}
	derived := []map[string]any{{"kind": "evaluation", "version": "v2", "status": "ready", "summary": "filtered", "timestamp": since.Add(2 * time.Hour).Unix()}}
	merged := mergeModelAuditTimelines(persisted, derived, 10, "v1", since)
	if len(merged) != 1 || merged[0]["id"] != "p1" {
		t.Fatalf("unexpected merged timeline: %#v", merged)
	}
	if len(persisted) != 1 || len(derived) != 1 {
		t.Fatal("merge must not mutate source slices")
	}
}

func TestModelEvaluationAuditHandlerDegradesToPersistedTimeline(t *testing.T) {
	oldDB := database.DB
	db, err := gorm.Open(sqlite.Open("file:model-audit-handler-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	database.DB = db
	defer func() { database.DB = oldDB }()
	if err := db.AutoMigrate(&models.ModelAuditEvent{}); err != nil {
		t.Fatalf("migrate audit table: %v", err)
	}
	when := time.Now().UTC().Add(-time.Minute)
	if err := db.Create(&models.ModelAuditEvent{ID: "handler-event", Kind: "evaluation", Version: "planner-v9", Status: "ready", Summary: "持久化事件", OccurredAt: when}).Error; err != nil {
		t.Fatalf("seed audit event: %v", err)
	}
	previousURL := os.Getenv("AI_SERVICE_URL")
	defer os.Setenv("AI_SERVICE_URL", previousURL)
	if err := os.Setenv("AI_SERVICE_URL", "http://127.0.0.1:1"); err != nil {
		t.Fatal(err)
	}

	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/audit", func(c *gin.Context) {
		c.Set("user_id", "audit-user")
		ModelEvaluationAuditHandler(c)
	})
	request := httptest.NewRequest(http.MethodGet, "/audit?version=planner-v9&limit=5", nil)
	response := httptest.NewRecorder()
	r.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("expected degraded audit response to remain 200, got %d: %s", response.Code, response.Body.String())
	}
	var envelope struct {
		Data map[string]any `json:"data"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if envelope.Data["status"] != "degraded" || envelope.Data["timeline_source"] != "persisted" {
		t.Fatalf("unexpected degraded metadata: %#v", envelope.Data)
	}
	timeline, ok := envelope.Data["timeline"].([]any)
	if !ok || len(timeline) != 1 {
		t.Fatalf("expected one persisted timeline event, got %#v", envelope.Data["timeline"])
	}
	filters, ok := envelope.Data["filters"].(map[string]any)
	if !ok || filters["version"] != "planner-v9" || filters["limit"] != float64(5) {
		t.Fatalf("unexpected filter metadata: %#v", envelope.Data["filters"])
	}
}

func TestModelEvaluationAuditHandlerDegradesWithoutDatabase(t *testing.T) {
	oldDB := database.DB
	database.DB = nil
	defer func() { database.DB = oldDB }()
	previousURL := os.Getenv("AI_SERVICE_URL")
	defer os.Setenv("AI_SERVICE_URL", previousURL)
	if err := os.Setenv("AI_SERVICE_URL", "http://127.0.0.1:1"); err != nil {
		t.Fatal(err)
	}
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/audit", func(c *gin.Context) {
		c.Set("user_id", "audit-user")
		ModelEvaluationAuditHandler(c)
	})
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/audit", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("database outage should still produce a degraded diagnostic response, got %d", response.Code)
	}
	var envelope struct {
		Data map[string]any `json:"data"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if envelope.Data["status"] != "degraded" {
		t.Fatalf("expected degraded status, got %#v", envelope.Data["status"])
	}
}

func TestLoadPersistedModelAuditPageUsesOpaqueCursor(t *testing.T) {
	oldDB := database.DB
	db, err := gorm.Open(sqlite.Open("file:model-audit-page-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	database.DB = db
	defer func() { database.DB = oldDB }()
	if err := db.AutoMigrate(&models.ModelAuditEvent{}); err != nil {
		t.Fatalf("migrate audit table: %v", err)
	}
	base := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	events := []models.ModelAuditEvent{
		{ID: "page-1", Kind: "evaluation", Version: "v1", Status: "ready", Summary: "one", OccurredAt: base.Add(3 * time.Hour)},
		{ID: "page-2", Kind: "evaluation", Version: "v1", Status: "ready", Summary: "two", OccurredAt: base.Add(2 * time.Hour)},
		{ID: "page-3", Kind: "evaluation", Version: "v1", Status: "ready", Summary: "three", OccurredAt: base.Add(time.Hour)},
	}
	if err := db.Create(&events).Error; err != nil {
		t.Fatalf("seed events: %v", err)
	}
	first, cursor, more := loadPersistedModelAuditPage(2, "v1", base, "")
	if len(first) != 2 || !more || cursor == "" {
		t.Fatalf("expected first page with cursor, got items=%#v cursor=%q more=%v", first, cursor, more)
	}
	second, next, more := loadPersistedModelAuditPage(2, "v1", base, cursor)
	if len(second) != 1 || second[0]["id"] != "page-3" || more || next != "" {
		t.Fatalf("unexpected second page: items=%#v cursor=%q more=%v", second, next, more)
	}
	if _, valid := decodeModelAuditCursor(cursor); !valid {
		t.Fatal("returned cursor must be decodable by the server")
	}
}

func TestModelEvaluationAuditHandlerRejectsInvalidCursor(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/audit", func(c *gin.Context) {
		c.Set("user_id", "audit-user")
		ModelEvaluationAuditHandler(c)
	})
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/audit?cursor=not-a-cursor", nil))
	if response.Code != http.StatusBadRequest {
		t.Fatalf("expected invalid cursor 400, got %d: %s", response.Code, response.Body.String())
	}
	if !strings.Contains(response.Body.String(), "MODEL_AUDIT_CURSOR_INVALID") {
		t.Fatalf("missing cursor error code: %s", response.Body.String())
	}
}

func TestModelEvaluationAuditSummaryAggregatesWhitelistedReasons(t *testing.T) {
	oldDB := database.DB
	db, err := gorm.Open(sqlite.Open("file:model-audit-summary-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	database.DB = db
	defer func() { database.DB = oldDB }()
	if err := db.AutoMigrate(&models.ModelAuditEvent{}); err != nil {
		t.Fatalf("migrate audit table: %v", err)
	}
	when := time.Now().UTC().Add(-time.Hour)
	events := []models.ModelAuditEvent{
		{ID: "summary-1", Kind: "evaluation", Version: "v1", Status: "insufficient_evidence", Summary: "blocked", SampleCount: 3, ReasonCodes: `["evaluation_samples_insufficient","drift_above_threshold"]`, OccurredAt: when},
		{ID: "summary-2", Kind: "rollback", Version: "v1", Status: "rolled_back", Summary: "rollback", SampleCount: 4, ReasonCodes: `["drift_above_threshold"]`, OccurredAt: when.Add(time.Minute)},
		{ID: "summary-3", Kind: "evaluation", Version: "v2", Status: "ready", Summary: "ready", SampleCount: 20, OccurredAt: when.Add(2 * time.Minute)},
	}
	if err := db.Create(&events).Error; err != nil {
		t.Fatalf("seed events: %v", err)
	}
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/summary", func(c *gin.Context) {
		c.Set("user_id", "audit-user")
		ModelEvaluationAuditSummaryHandler(c)
	})
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/summary?version=v1", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
	var envelope struct {
		Data map[string]any `json:"data"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if envelope.Data["count"] != float64(2) || envelope.Data["total_samples"] != float64(7) {
		t.Fatalf("unexpected summary totals: %#v", envelope.Data)
	}
	reasons := envelope.Data["reason_codes"].(map[string]any)
	if reasons["drift_above_threshold"] != float64(2) || reasons["evaluation_samples_insufficient"] != float64(1) {
		t.Fatalf("unexpected reason aggregates: %#v", reasons)
	}
}

func TestModelEvaluationAuditCompareMarksInsufficientVersions(t *testing.T) {
	oldDB := database.DB
	db, err := gorm.Open(sqlite.Open("file:model-audit-compare-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	database.DB = db
	defer func() { database.DB = oldDB }()
	if err := db.AutoMigrate(&models.ModelAuditEvent{}); err != nil {
		t.Fatalf("migrate audit table: %v", err)
	}
	when := time.Now().UTC().Add(-time.Hour)
	events := []models.ModelAuditEvent{
		{ID: "compare-1", Kind: "evaluation", Version: "v1", Status: "ready", SampleCount: 20, OccurredAt: when},
		{ID: "compare-2", Kind: "evaluation", Version: "v1", Status: "insufficient_evidence", SampleCount: 4, OccurredAt: when.Add(time.Minute)},
		{ID: "compare-3", Kind: "evaluation", Version: "v2", Status: "insufficient_evidence", SampleCount: 3, OccurredAt: when.Add(2 * time.Minute)},
	}
	if err := db.Create(&events).Error; err != nil {
		t.Fatalf("seed events: %v", err)
	}
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/compare", func(c *gin.Context) {
		c.Set("user_id", "audit-user")
		ModelEvaluationAuditCompareHandler(c)
	})
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/compare?versions=v1,v2", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
	var envelope struct {
		Data struct {
			Versions []struct {
				Version    string `json:"version"`
				Samples    int    `json:"samples"`
				Sufficient bool   `json:"sufficient_evidence"`
				Blocked    int    `json:"blocked"`
			} `json:"versions"`
		} `json:"data"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(envelope.Data.Versions) != 2 || envelope.Data.Versions[0].Version != "v1" || envelope.Data.Versions[0].Samples != 24 || !envelope.Data.Versions[0].Sufficient || envelope.Data.Versions[0].Blocked != 1 {
		t.Fatalf("unexpected v1 comparison: %#v", envelope.Data.Versions)
	}
	if envelope.Data.Versions[1].Sufficient {
		t.Fatal("v2 with three samples must be marked insufficient")
	}
}
