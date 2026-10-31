package handlers

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"hash/fnv"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/metrics"
	"gateway/internal/models"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

func planningModelDecision(userID string) map[string]any {
	// Runtime health is checked before assigning a user so a breached canary is
	// removed from traffic even when no administrator is watching the dashboard.
	monitorCanaryDeployments()
	var deployments []models.ModelDeployment
	if database.DB == nil || database.DB.Find(&deployments).Error != nil {
		return map[string]any{"name": "estimator", "version": "builtin", "status": "fallback", "shadow": false}
	}
	var active *models.ModelDeployment
	for i := range deployments {
		if deployments[i].Status == "active" {
			active = &deployments[i]
			break
		}
	}
	if active == nil {
		return map[string]any{"name": "estimator", "version": "builtin", "status": "fallback", "shadow": false}
	}
	decision := map[string]any{"name": active.Name, "version": active.Version, "status": "active", "shadow": false}
	for i := range deployments {
		if deployments[i].Status != "canary" || deployments[i].Traffic <= 0 {
			continue
		}
		if !deploymentPassesRuntimeGate(deployments[i]) {
			// Quality metrics are server-written by the evaluator. A canary that
			// breaches the gate is removed before any user is assigned to it.
			deployments[i].Status, deployments[i].Traffic = "rolled_back", 0
			_ = database.DB.Save(&deployments[i]).Error
			continue
		}
		h := fnv.New32a()
		_, _ = h.Write([]byte(userID + ":" + deployments[i].Version))
		bucket := int(h.Sum32() % 100)
		if bucket < deployments[i].Traffic {
			decision = map[string]any{"name": deployments[i].Name, "version": deployments[i].Version, "status": "canary", "shadow": false}
			break
		}
	}
	return decision
}

const (
	canaryRuntimeWindow     = 24 * time.Hour
	canaryMinRuntimeSamples = 10
	canaryMaxErrorRate      = 0.20
	canaryMinConstraintRate = 0.95
	canaryMaxFairnessStddev = 0.10
	canaryMaxAdoptionGap    = 0.15
	canaryMinPolicySamples  = 5
	canaryMinPolicyLift     = 0.01
)

// canaryRuntimeHealth aggregates only version-attributed, bounded metrics from
// the event stream.  It intentionally reports "insufficient_data" until the
// minimum sample count is reached; a quiet canary must not be rolled back just
// because it has not received enough traffic yet.
func canaryRuntimeHealth(deployment models.ModelDeployment, events []models.PlanningEvent, now time.Time) map[string]any {
	version := strings.TrimSpace(deployment.Version)
	generated, adopted, rejected := 0, 0, 0
	quality := map[string][]float64{}
	for _, event := range events {
		if !event.CreatedAt.IsZero() && now.Sub(event.CreatedAt) > canaryRuntimeWindow {
			continue
		}
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		if strings.TrimSpace(fmt.Sprint(payload["model_version"])) != version {
			continue
		}
		switch event.EventType {
		case "plan_generated":
			generated++
		case "plan_adopted":
			adopted++
		case "plan_rejected":
			rejected++
		case "agent_metric":
			metric := strings.TrimSpace(fmt.Sprint(payload["metric"]))
			if value, ok := numericPayload(payload, "value"); ok {
				quality[metric] = append(quality[metric], value)
			}
		}
	}
	totalOutcomes := adopted + rejected
	health := map[string]any{
		"version":           version,
		"deployment_status": deployment.Status,
		"window_hours":      int(canaryRuntimeWindow / time.Hour),
		"samples":           generated,
		"outcomes":          totalOutcomes,
		"adopted":           adopted,
		"rejected":          rejected,
		"adoption_rate":     ratioOrNil(adopted, totalOutcomes),
		"status":            "insufficient_data",
		"rollback_required": false,
	}
	if generated < canaryMinRuntimeSamples {
		health["reason"] = "灰度样本不足，暂不自动回滚"
		return health
	}
	for _, metric := range []string{"error_rate", "constraint_satisfaction_rate", "fairness_utility_variance"} {
		if values := quality[metric]; len(values) > 0 {
			health[metric] = meanFinite(values)
		}
	}
	rollbackReasons := []string{}
	if value, ok := health["error_rate"].(float64); ok && value > canaryMaxErrorRate {
		rollbackReasons = append(rollbackReasons, "error_rate_exceeded")
	}
	if value, ok := health["constraint_satisfaction_rate"].(float64); ok && value < canaryMinConstraintRate {
		rollbackReasons = append(rollbackReasons, "constraint_satisfaction_below_threshold")
	}
	if value, ok := health["fairness_utility_variance"].(float64); ok && value > canaryMaxFairnessStddev {
		rollbackReasons = append(rollbackReasons, "fairness_variance_exceeded")
	}
	if len(rollbackReasons) > 0 {
		health["status"] = "rollback_required"
		health["rollback_required"] = true
		health["reasons"] = rollbackReasons
	} else {
		health["status"] = "healthy"
		health["reason"] = "灰度运行指标在门限内"
	}
	return health
}

func ratioOrNil(numerator, denominator int) any {
	if denominator <= 0 {
		return nil
	}
	return roundRatio(float64(numerator) / float64(denominator))
}

func meanFinite(values []float64) float64 {
	if len(values) == 0 {
		return 0
	}
	total := 0.0
	for _, value := range values {
		if !math.IsNaN(value) && !math.IsInf(value, 0) {
			total += value
		}
	}
	return roundRatio(total / float64(len(values)))
}

func monitorCanaryDeployments() {
	if database.DB == nil {
		return
	}
	var deployments []models.ModelDeployment
	if database.DB.Where("status = ? AND traffic > 0", "canary").Find(&deployments).Error != nil || len(deployments) == 0 {
		return
	}
	cutoff := time.Now().Add(-canaryRuntimeWindow)
	var events []models.PlanningEvent
	_ = database.DB.Where("created_at >= ?", cutoff).Find(&events).Error
	var active models.ModelDeployment
	activeHealth := map[string]any{}
	if database.DB.Where("status = ?", "active").First(&active).Error == nil {
		activeHealth = canaryRuntimeHealth(active, events, time.Now())
	}
	for index := range deployments {
		health := canaryRuntimeHealth(deployments[index], events, time.Now())
		if policyReasons := segmentFairnessReasons(deployments[index]); len(policyReasons) > 0 {
			health["status"] = "rollback_required"
			health["rollback_required"] = true
			existing, _ := health["reasons"].([]string)
			health["reasons"] = append(existing, policyReasons...)
		}
		// Compare against the active policy only when both sides have enough
		// outcome evidence. A sparse active baseline must not cause a rollback.
		if canaryRate, ok := health["adoption_rate"].(float64); ok {
			baselineHealthy, _ := activeHealth["status"].(string)
			if activeRate, baselineOK := activeHealth["adoption_rate"].(float64); baselineOK && baselineHealthy != "insufficient_data" && canaryRate+canaryMaxAdoptionGap < activeRate {
				health["status"] = "rollback_required"
				health["rollback_required"] = true
				reasons, _ := health["reasons"].([]string)
				health["reasons"] = append(reasons, "adoption_rate_below_active")
			}
		}
		metrics := map[string]float64{}
		_ = json.Unmarshal([]byte(deployments[index].Metrics), &metrics)
		if value, ok := health["adoption_rate"].(float64); ok {
			metrics["runtime_adoption_rate"] = value
		}
		metrics["runtime_samples"] = float64(health["samples"].(int))
		rollbackAt := time.Time{}
		if health["rollback_required"] == true {
			rollbackAt = time.Now().UTC()
			deployments[index].Status = "rolled_back"
			deployments[index].Traffic = 0
			metrics["runtime_rollback"] = 1
			metrics["runtime_rollback_at_unix"] = float64(rollbackAt.Unix())
			if reasons, ok := health["reasons"].([]string); ok {
				metrics["runtime_rollback_reason_count"] = float64(len(reasons))
			}
		}
		encoded, _ := json.Marshal(metrics)
		deployments[index].Metrics = string(encoded)
		if health["rollback_required"] == true {
			deployment := deployments[index]
			_ = database.DB.Transaction(func(tx *gorm.DB) error {
				if err := tx.Save(&deployment).Error; err != nil {
					return err
				}
				reasons, _ := health["reasons"].([]string)
				reasonCount := len(reasons)
				recordModelAuditEventReasonsTx(tx, "rollback", deployment.Version, "rolled_back", "运行时健康门禁触发自动回滚", int(health["samples"].(int)), reasonCount, reasons, rollbackAt)
				return nil
			})
		} else {
			_ = database.DB.Save(&deployments[index]).Error
		}
	}
}

// ModelRuntimeHealthHandler exposes the same bounded health calculation used
// by automatic assignment. It is diagnostic only; it never returns raw events.
func ModelRuntimeHealthHandler(c *gin.Context) {
	// Deliberately NOT admin-gated. The frontend fetches this together with the
	// other diagnostics endpoints in a single Promise.all whose catch clears every
	// panel at once, so returning 403 here would blank the whole diagnostics
	// surface for ordinary users rather than just this tile. The payload is a
	// bounded health calculation (sample counts, error rate, fairness spread) and
	// never includes the model Endpoint, so it is safe to keep readable while
	// ListModelDeploymentsHandler — which does expose Endpoint — is gated.
	if _, ok := currentUserIDOrReject(c); !ok {
		return
	}
	monitorCanaryDeployments()
	var deployments []models.ModelDeployment
	if err := database.DB.Where("status IN ?", []string{"canary", "active", "rolled_back"}).Find(&deployments).Error; err != nil {
		planningError(c, http.StatusInternalServerError, "MODEL_RUNTIME_HEALTH_FAILED", "模型运行状态读取失败", nil)
		return
	}
	cutoff := time.Now().Add(-canaryRuntimeWindow)
	var events []models.PlanningEvent
	_ = database.DB.Where("created_at >= ?", cutoff).Find(&events).Error
	health := make([]map[string]any, 0, len(deployments))
	for _, deployment := range deployments {
		health = append(health, canaryRuntimeHealth(deployment, events, time.Now()))
	}
	planningData(c, gin.H{"window_hours": int(canaryRuntimeWindow / time.Hour), "models": health, "thresholds": gin.H{"minimum_samples": canaryMinRuntimeSamples, "maximum_error_rate": canaryMaxErrorRate, "minimum_constraint_satisfaction_rate": canaryMinConstraintRate, "maximum_fairness_stddev": canaryMaxFairnessStddev, "maximum_adoption_gap": canaryMaxAdoptionGap}, "automatic_rollback": true})
}

// ModelEvaluationAuditHandler proxies the AI service's allowlisted scheduler
// audit. The gateway remains the browser boundary and never exposes the audit
// file path or internal token.
func ModelEvaluationAuditHandler(c *gin.Context) {
	if _, ok := currentUserIDOrReject(c); !ok {
		return
	}
	limit := 20
	versionFilter := strings.TrimSpace(c.Query("version"))
	cursor := strings.TrimSpace(c.Query("cursor"))
	if cursor != "" {
		if _, valid := decodeModelAuditCursor(cursor); !valid {
			planningError(c, http.StatusBadRequest, "MODEL_AUDIT_CURSOR_INVALID", "审计时间线分页游标无效", nil)
			return
		}
	}
	since := time.Now().Add(-30 * 24 * time.Hour)
	if raw := strings.TrimSpace(c.Query("since")); raw != "" {
		if parsed, err := time.Parse(time.RFC3339, raw); err == nil {
			since = parsed
		}
	}
	if raw := strings.TrimSpace(c.Query("limit")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed >= 1 && parsed <= 100 {
			limit = parsed
		}
	}
	// Read the durable timeline before contacting the optional AI scheduler.
	// This lets the gateway provide useful diagnostics during an AI-service
	// outage instead of turning an audit read into an all-or-nothing dependency.
	persistedTimeline, nextCursor, hasMore := loadPersistedModelAuditPage(limit, versionFilter, since, cursor)
	degradedAudit := func() gin.H {
		return gin.H{
			"status": "degraded", "entries": []any{}, "count": 0,
			"timeline": persistedTimeline, "timeline_source": "persisted",
			"retention_days": modelAuditRetentionDays(),
			"filters":        gin.H{"version": versionFilter, "since": since.UTC().Format(time.RFC3339), "limit": limit},
			"pagination":     gin.H{"has_more": hasMore, "next_cursor": nextCursor},
		}
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, aiServiceBaseURL()+"/api/v1/evaluation/audit?limit="+strconv.Itoa(limit), nil)
	if err != nil {
		planningData(c, degradedAudit())
		return
	}
	if token := strings.TrimSpace(os.Getenv("AI_SERVICE_INTERNAL_TOKEN")); token != "" {
		request.Header.Set("X-Omni-Internal-Token", token)
	}
	response, err := (&http.Client{Timeout: 3 * time.Second}).Do(request)
	if err != nil || response.StatusCode != http.StatusOK {
		if response != nil {
			_ = response.Body.Close()
		}
		planningData(c, degradedAudit())
		return
	}
	defer response.Body.Close()
	var audit map[string]any
	if err := json.NewDecoder(io.LimitReader(response.Body, 256*1024)).Decode(&audit); err != nil {
		planningData(c, degradedAudit())
		return
	}
	var deployments []models.ModelDeployment
	_ = database.DB.Where("status IN ?", []string{"shadow", "canary", "active", "rolled_back"}).Find(&deployments).Error
	cutoff := time.Now().Add(-30 * 24 * time.Hour)
	var events []models.PlanningEvent
	_ = database.DB.Where("created_at >= ? AND event_type = ?", cutoff, "model_shadow_executed").Find(&events).Error
	derivedTimeline := buildModelAuditTimeline(audit, deployments, events)
	if len(persistedTimeline) > 0 {
		// The endpoint is intentionally read-only. Persisted lifecycle events are
		// authoritative; scheduler observations are merged in-memory so polling
		// never mutates the audit table.
		audit["timeline"] = mergeModelAuditTimelines(persistedTimeline, derivedTimeline, limit, versionFilter, since)
	} else {
		audit["timeline"] = mergeModelAuditTimelines(nil, derivedTimeline, limit, versionFilter, since)
	}
	audit["timeline_source"] = "persisted+derived"
	if len(persistedTimeline) == 0 {
		audit["timeline_source"] = "derived"
	}
	audit["retention_days"] = modelAuditRetentionDays()
	audit["filters"] = gin.H{"version": versionFilter, "since": since.UTC().Format(time.RFC3339), "limit": limit}
	audit["pagination"] = gin.H{"has_more": hasMore, "next_cursor": nextCursor}
	planningData(c, audit)
}

// persistModelAuditTimeline makes lifecycle observations durable and
// idempotent. The event ID is a hash of bounded public fields, so retries from
// dashboard polling cannot create duplicates.
func persistModelAuditTimeline(timeline []map[string]any) {
	for _, item := range timeline {
		kind := strings.TrimSpace(fmt.Sprint(item["kind"]))
		version := strings.TrimSpace(fmt.Sprint(item["version"]))
		status := strings.TrimSpace(fmt.Sprint(item["status"]))
		summary := strings.TrimSpace(fmt.Sprint(item["summary"]))
		if kind == "" || version == "" || summary == "" {
			continue
		}
		stamp, ok := evaluationNumber(item["timestamp"])
		if !ok || stamp <= 0 {
			continue
		}
		sampleCount := 0
		if value, ok := evaluationNumber(item["sample_count"]); ok && value >= 0 {
			sampleCount = int(value)
		}
		reasonCount := 0
		if value, ok := evaluationNumber(item["reason_count"]); ok && value >= 0 {
			reasonCount = int(value)
		}
		var reasonCodes []string
		if raw, ok := item["reason_codes"].([]any); ok {
			for _, value := range raw {
				reasonCodes = append(reasonCodes, fmt.Sprint(value))
			}
		}
		recordModelAuditEventReasonsTx(database.DB, kind, version, status, summary, sampleCount, reasonCount, reasonCodes, time.Unix(int64(stamp), 0).UTC())
	}
}

func recordModelAuditEvent(kind, version, status, summary string, sampleCount, reasonCount int, occurredAt time.Time) {
	recordModelAuditEventReasonsTx(database.DB, kind, version, status, summary, sampleCount, reasonCount, nil, occurredAt)
}

// recordModelAuditEventTx writes an audit event using the caller's database
// handle. Callers that are already inside a transaction must use this helper
// so the lifecycle record commits or rolls back together with the business
// state it describes. The stable ID keeps retries idempotent.
func recordModelAuditEventTx(tx *gorm.DB, kind, version, status, summary string, sampleCount, reasonCount int, occurredAt time.Time) {
	recordModelAuditEventReasonsTx(tx, kind, version, status, summary, sampleCount, reasonCount, nil, occurredAt)
}

var allowedModelAuditReasonCodes = map[string]struct{}{
	"evaluation_samples_insufficient": {}, "validation_samples_insufficient": {},
	"offline_score_below_threshold": {}, "drift_above_threshold": {},
	"adoption_rate_below_active": {}, "error_rate_above_threshold": {},
	"constraint_satisfaction_below_threshold": {}, "fairness_variance_above_threshold": {},
	"segment_fairness_insufficient": {}, "segment_fairness_variance_exceeded": {},
	"segment_fairness_threshold_exceeded": {}, "policy_comparison_insufficient": {},
	"policy_reward_lift_below_threshold": {},
	"database_unavailable":               {}, "cleanup_failed": {},
}

func normalizeModelAuditReasonCodes(codes []string) []string {
	seen := make(map[string]struct{}, len(codes))
	result := make([]string, 0, len(codes))
	for _, code := range codes {
		code = strings.TrimSpace(code)
		if _, allowed := allowedModelAuditReasonCodes[code]; !allowed {
			continue
		}
		if _, duplicate := seen[code]; duplicate {
			continue
		}
		seen[code] = struct{}{}
		result = append(result, code)
	}
	sort.Strings(result)
	return result
}

func recordModelAuditEventReasonsTx(tx *gorm.DB, kind, version, status, summary string, sampleCount, reasonCount int, reasonCodes []string, occurredAt time.Time) {
	if tx == nil || strings.TrimSpace(kind) == "" || strings.TrimSpace(version) == "" || strings.TrimSpace(summary) == "" {
		return
	}
	if occurredAt.IsZero() {
		occurredAt = time.Now().UTC()
	}
	if sampleCount < 0 {
		sampleCount = 0
	}
	if reasonCount < 0 {
		reasonCount = 0
	}
	reasonCodes = normalizeModelAuditReasonCodes(reasonCodes)
	reasonJSON, _ := json.Marshal(reasonCodes)
	idSource := fmt.Sprintf("%s|%s|%s|%d|%s|%s", kind, version, status, occurredAt.Unix(), summary, reasonJSON)
	id := fmt.Sprintf("audit-%x", sha256.Sum256([]byte(idSource)))
	event := models.ModelAuditEvent{ID: id, Kind: kind, Version: version, Status: status, Summary: summary, SampleCount: sampleCount, ReasonCount: reasonCount, ReasonCodes: string(reasonJSON), OccurredAt: occurredAt.UTC()}
	_ = tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&event).Error
}

func cleanupModelAuditEvents() {
	if database.DB == nil {
		metrics.RecordAuditCleanup(0, fmt.Errorf("database_unavailable"))
		return
	}
	days := modelAuditRetentionDays()
	cutoff := time.Now().UTC().Add(-time.Duration(days) * 24 * time.Hour)
	result := database.DB.Where("occurred_at < ?", cutoff).Delete(&models.ModelAuditEvent{})
	metrics.RecordAuditCleanup(result.RowsAffected, result.Error)
}

func modelAuditCleanupSnapshot() map[string]any {
	snapshot := metrics.AuditCleanupSnapshot()
	snapshot["retention_days"] = modelAuditRetentionDays()
	return snapshot
}

// ModelAuditCleanupHealthHandler exposes operational state for the retention
// worker without exposing database paths or raw audit records.
func ModelAuditCleanupHealthHandler(c *gin.Context) {
	if _, ok := currentUserIDOrReject(c); !ok {
		return
	}
	planningData(c, modelAuditCleanupSnapshot())
}

// ModelEvaluationAuditSummaryHandler returns bounded aggregates for the
// professional diagnostics surface. It intentionally exposes counts and
// whitelisted reason codes only, never raw audit summaries or payloads.
func ModelEvaluationAuditSummaryHandler(c *gin.Context) {
	if _, ok := currentUserIDOrReject(c); !ok {
		return
	}
	if database.DB == nil {
		planningData(c, gin.H{"status": "degraded", "count": 0, "versions": gin.H{}, "statuses": gin.H{}, "reason_codes": gin.H{}})
		return
	}
	versionFilter := strings.TrimSpace(c.Query("version"))
	since := time.Now().UTC().Add(-30 * 24 * time.Hour)
	if raw := strings.TrimSpace(c.Query("since")); raw != "" {
		if parsed, err := time.Parse(time.RFC3339, raw); err == nil {
			since = parsed.UTC()
		}
	}
	var events []models.ModelAuditEvent
	query := database.DB.Where("occurred_at >= ?", since)
	if versionFilter != "" {
		query = query.Where("version = ?", versionFilter)
	}
	if err := query.Find(&events).Error; err != nil {
		planningData(c, gin.H{"status": "degraded", "count": 0, "versions": gin.H{}, "statuses": gin.H{}, "reason_codes": gin.H{}})
		return
	}
	versions := map[string]int{}
	statuses := map[string]int{}
	reasons := map[string]int{}
	totalSamples := 0
	for _, event := range events {
		versions[event.Version]++
		statuses[event.Status]++
		totalSamples += event.SampleCount
		for _, code := range decodeModelAuditReasonCodes(event.ReasonCodes) {
			reasons[code]++
		}
	}
	planningData(c, gin.H{"status": "available", "count": len(events), "total_samples": totalSamples, "versions": versions, "statuses": statuses, "reason_codes": reasons, "filters": gin.H{"version": versionFilter, "since": since.Format(time.RFC3339)}})
}

// ModelEvaluationAuditCompareHandler compares versions only on bounded,
// auditable aggregates. A version with fewer samples is explicitly marked
// insufficient rather than being presented as a better model.
func ModelEvaluationAuditCompareHandler(c *gin.Context) {
	if _, ok := currentUserIDOrReject(c); !ok {
		return
	}
	if database.DB == nil {
		planningData(c, gin.H{"status": "degraded", "versions": []any{}})
		return
	}
	since := time.Now().UTC().Add(-30 * 24 * time.Hour)
	if raw := strings.TrimSpace(c.Query("since")); raw != "" {
		if parsed, err := time.Parse(time.RFC3339, raw); err == nil {
			since = parsed.UTC()
		}
	}
	requested := make(map[string]struct{})
	for _, raw := range strings.Split(c.Query("versions"), ",") {
		if value := strings.TrimSpace(raw); value != "" {
			requested[value] = struct{}{}
		}
	}
	var events []models.ModelAuditEvent
	if err := database.DB.Where("occurred_at >= ?", since).Order("occurred_at desc").Find(&events).Error; err != nil {
		planningData(c, gin.H{"status": "degraded", "versions": []any{}})
		return
	}
	type aggregate struct {
		Version    string    `json:"version"`
		Events     int       `json:"events"`
		Samples    int       `json:"samples"`
		Ready      int       `json:"ready"`
		Blocked    int       `json:"blocked"`
		Rollbacks  int       `json:"rollbacks"`
		BlockRate  float64   `json:"block_rate"`
		LatestAt   time.Time `json:"latest_at"`
		Sufficient bool      `json:"sufficient_evidence"`
	}
	groups := map[string]*aggregate{}
	for _, event := range events {
		if len(requested) > 0 {
			if _, ok := requested[event.Version]; !ok {
				continue
			}
		}
		entry := groups[event.Version]
		if entry == nil {
			entry = &aggregate{Version: event.Version, LatestAt: event.OccurredAt.UTC()}
			groups[event.Version] = entry
		}
		entry.Events++
		entry.Samples += event.SampleCount
		if event.OccurredAt.After(entry.LatestAt) {
			entry.LatestAt = event.OccurredAt.UTC()
		}
		switch event.Status {
		case "ready":
			entry.Ready++
		case "insufficient_evidence":
			entry.Blocked++
		case "rolled_back":
			entry.Rollbacks++
		}
	}
	result := make([]aggregate, 0, len(groups))
	for _, entry := range groups {
		if entry.Events > 0 {
			entry.BlockRate = roundRatio(float64(entry.Blocked) / float64(entry.Events))
		}
		entry.Sufficient = entry.Samples >= 20
		result = append(result, *entry)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Version < result[j].Version })
	planningData(c, gin.H{"status": "available", "versions": result, "filters": gin.H{"since": since.Format(time.RFC3339), "versions": requested}})
}

func modelAuditRetentionDays() int {
	days := 180
	if raw := strings.TrimSpace(os.Getenv("MODEL_AUDIT_RETENTION_DAYS")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed >= 7 && parsed <= 3650 {
			days = parsed
		}
	}
	return days
}

// StartModelAuditCleanupLoop schedules bounded retention cleanup outside the
// request path. A context is returned so the server can stop the goroutine
// during graceful shutdown. The first cleanup runs immediately, then follows
// MODEL_AUDIT_CLEANUP_INTERVAL_MINUTES (default: once per day).
func StartModelAuditCleanupLoop(parent context.Context) context.CancelFunc {
	if parent == nil {
		parent = context.Background()
	}
	minutes := 1440
	if raw := strings.TrimSpace(os.Getenv("MODEL_AUDIT_CLEANUP_INTERVAL_MINUTES")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed >= 1 && parsed <= 10080 {
			minutes = parsed
		}
	}
	ctx, cancel := context.WithCancel(parent)
	go func() {
		cleanupModelAuditEvents()
		ticker := time.NewTicker(time.Duration(minutes) * time.Minute)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				cleanupModelAuditEvents()
			case <-ctx.Done():
				return
			}
		}
	}()
	return cancel
}

// mergeModelAuditTimelines keeps persisted lifecycle events and read-only
// compatibility observations together without writing from a GET request.
func mergeModelAuditTimelines(persisted, derived []map[string]any, limit int, versionFilter string, since time.Time) []map[string]any {
	if limit < 1 || limit > 100 {
		limit = 30
	}
	seen := make(map[string]struct{}, len(persisted)+len(derived))
	merged := make([]map[string]any, 0, len(persisted)+len(derived))
	add := func(item map[string]any) {
		if item == nil {
			return
		}
		version := strings.TrimSpace(fmt.Sprint(item["version"]))
		if versionFilter != "" && version != versionFilter {
			return
		}
		stamp, ok := evaluationNumber(item["timestamp"])
		if !ok || time.Unix(int64(stamp), 0).Before(since) {
			return
		}
		key := strings.TrimSpace(fmt.Sprint(item["id"]))
		if key == "" || key == "<nil>" {
			key = fmt.Sprintf("%s|%s|%s|%d|%s", item["kind"], version, item["status"], int64(stamp), item["summary"])
		}
		if _, exists := seen[key]; exists {
			return
		}
		seen[key] = struct{}{}
		merged = append(merged, item)
	}
	for _, item := range persisted {
		add(item)
	}
	for _, item := range derived {
		add(item)
	}
	sort.SliceStable(merged, func(i, j int) bool {
		left, _ := evaluationNumber(merged[i]["timestamp"])
		right, _ := evaluationNumber(merged[j]["timestamp"])
		return left > right
	})
	if len(merged) > limit {
		merged = merged[:limit]
	}
	return merged
}

func loadPersistedModelAuditTimeline(limit int, versionFilter string, since time.Time) []map[string]any {
	items, _, _ := loadPersistedModelAuditPage(limit, versionFilter, since, "")
	return items
}

type modelAuditCursor struct {
	OccurredAt time.Time `json:"occurred_at"`
	ID         string    `json:"id"`
}

func encodeModelAuditCursor(cursor modelAuditCursor) string {
	payload, err := json.Marshal(cursor)
	if err != nil {
		return ""
	}
	return base64.RawURLEncoding.EncodeToString(payload)
}

func decodeModelAuditCursor(raw string) (modelAuditCursor, bool) {
	if strings.TrimSpace(raw) == "" {
		return modelAuditCursor{}, true
	}
	payload, err := base64.RawURLEncoding.DecodeString(raw)
	if err != nil {
		return modelAuditCursor{}, false
	}
	var cursor modelAuditCursor
	if json.Unmarshal(payload, &cursor) != nil || cursor.ID == "" || cursor.OccurredAt.IsZero() {
		return modelAuditCursor{}, false
	}
	return cursor, true
}

func loadPersistedModelAuditPage(limit int, versionFilter string, since time.Time, rawCursor string) ([]map[string]any, string, bool) {
	if database.DB == nil {
		return nil, "", false
	}
	if limit < 1 || limit > 100 {
		limit = 30
	}
	var events []models.ModelAuditEvent
	query := database.DB.Where("occurred_at >= ?", since).Order("occurred_at desc").Order("id desc").Limit(limit + 1)
	if versionFilter != "" {
		query = query.Where("version = ?", versionFilter)
	}
	if cursor, ok := decodeModelAuditCursor(rawCursor); !ok {
		return nil, "", false
	} else if rawCursor != "" {
		query = query.Where("(occurred_at < ? OR (occurred_at = ? AND id < ?))", cursor.OccurredAt, cursor.OccurredAt, cursor.ID)
	}
	if query.Find(&events).Error != nil {
		return nil, "", false
	}
	hasMore := len(events) > limit
	if hasMore {
		events = events[:limit]
	}
	result := make([]map[string]any, 0, len(events))
	for _, event := range events {
		result = append(result, map[string]any{
			"id": event.ID, "kind": event.Kind, "version": event.Version,
			"status": event.Status, "summary": event.Summary,
			"sample_count": event.SampleCount, "reason_count": event.ReasonCount,
			"reason_codes": decodeModelAuditReasonCodes(event.ReasonCodes),
			"at":           event.OccurredAt.UTC().Format(time.RFC3339), "timestamp": event.OccurredAt.Unix(),
		})
	}
	nextCursor := ""
	if hasMore && len(events) > 0 {
		last := events[len(events)-1]
		nextCursor = encodeModelAuditCursor(modelAuditCursor{OccurredAt: last.OccurredAt.UTC(), ID: last.ID})
	}
	return result, nextCursor, hasMore
}

func decodeModelAuditReasonCodes(raw string) []string {
	if strings.TrimSpace(raw) == "" {
		return nil
	}
	var codes []string
	if json.Unmarshal([]byte(raw), &codes) != nil {
		return nil
	}
	return normalizeModelAuditReasonCodes(codes)
}

// buildModelAuditTimeline joins scheduler audit, deployment state and shadow
// execution into a bounded, de-identified timeline for diagnostics. It never
// copies raw planning payloads or user/trip identifiers.
func buildModelAuditTimeline(audit map[string]any, deployments []models.ModelDeployment, events []models.PlanningEvent) []map[string]any {
	type timelineItem struct {
		at   time.Time
		item map[string]any
	}
	items := make([]timelineItem, 0, 32)
	add := func(at time.Time, item map[string]any) {
		if at.IsZero() {
			at = time.Unix(0, 0).UTC()
		}
		item["at"] = at.UTC().Format(time.RFC3339)
		item["timestamp"] = at.Unix()
		items = append(items, timelineItem{at: at, item: item})
	}
	if rawEntries, ok := audit["entries"].([]any); ok {
		for _, raw := range rawEntries {
			entry, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			stamp, _ := evaluationNumber(entry["recorded_at"])
			version, _ := entry["model_version"].(string)
			status, _ := entry["status"].(string)
			summary := "评估任务已执行"
			if status == "insufficient_evidence" {
				summary = "评估完成，但证据不足"
			} else if status == "error" || status == "blocked" {
				summary = "评估任务执行失败或被阻断"
			}
			add(time.Unix(int64(stamp), 0), map[string]any{"kind": "evaluation", "status": status, "version": version, "summary": summary, "sample_count": entry["sample_count"], "validation_samples": entry["validation_samples"]})
		}
	}
	for _, deployment := range deployments {
		if !deployment.CreatedAt.IsZero() {
			add(deployment.CreatedAt, map[string]any{"kind": "deployment", "status": "registered", "version": deployment.Version, "deployment_status": deployment.Status, "summary": "模型版本已登记"})
		}
		if !deployment.UpdatedAt.IsZero() && deployment.UpdatedAt.After(deployment.CreatedAt) {
			add(deployment.UpdatedAt, map[string]any{"kind": "deployment", "status": deployment.Status, "version": deployment.Version, "deployment_status": deployment.Status, "summary": "模型注册状态更新"})
		}
		var metrics map[string]float64
		if json.Unmarshal([]byte(deployment.Metrics), &metrics) == nil && metrics["runtime_rollback"] >= 1 {
			at := time.Unix(int64(metrics["runtime_rollback_at_unix"]), 0)
			add(at, map[string]any{"kind": "rollback", "status": "rolled_back", "version": deployment.Version, "summary": "运行时健康门禁触发自动回滚", "reason_count": metrics["runtime_rollback_reason_count"]})
		}
	}
	shadowCounts := map[string]int{}
	shadowLast := map[string]time.Time{}
	for _, event := range events {
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		version := strings.TrimSpace(fmt.Sprint(payload["model_version"]))
		if version == "" || version == "<nil>" {
			continue
		}
		shadowCounts[version]++
		if event.CreatedAt.After(shadowLast[version]) {
			shadowLast[version] = event.CreatedAt
		}
	}
	for version, count := range shadowCounts {
		add(shadowLast[version], map[string]any{"kind": "shadow", "status": "executed", "version": version, "summary": "影子模型运行记录汇总", "sample_count": count})
	}
	sort.SliceStable(items, func(i, j int) bool { return items[i].at.After(items[j].at) })
	if len(items) > 30 {
		items = items[:30]
	}
	result := make([]map[string]any, 0, len(items))
	for _, item := range items {
		result = append(result, item.item)
	}
	return result
}

func deploymentPassesRuntimeGate(deployment models.ModelDeployment) bool {
	if strings.TrimSpace(deployment.Metrics) == "" {
		return false
	}
	var metrics map[string]float64
	if json.Unmarshal([]byte(deployment.Metrics), &metrics) != nil {
		return false
	}
	if metrics["evaluation_samples"] < 20 || metrics["validation_samples"] < 5 {
		return false
	}
	if metrics["rollback_seconds"] <= 0 {
		return false
	}
	if score, ok := metrics["offline_score"]; ok && score < 0.80 {
		return false
	}
	if drift, ok := metrics["drift_score"]; ok && drift > 0.20 {
		return false
	}
	if value, ok := metrics["constraint_satisfaction_rate"]; ok && value < 0.99 {
		return false
	}
	if value, ok := metrics["fairness_min_utility"]; ok && value < 0.60 {
		return false
	}
	if value, ok := metrics["fairness_utility_variance"]; ok && value > 0.10 {
		return false
	}
	// Behaviour-aware ranking is an opt-in policy. Once an evaluator marks a
	// deployment as requiring this comparison, both policy arms need independent
	// evidence and a strictly positive reward lift. Missing values remain
	// missing; they are never treated as zero.
	if required, ok := metrics["policy_comparison_required"]; ok && required >= 1 {
		if ready, exists := metrics["policy_comparison_ready"]; !exists || ready < 1 {
			return false
		}
		if samples, exists := metrics["policy_baseline_samples"]; !exists || samples < canaryMinPolicySamples {
			return false
		}
		if samples, exists := metrics["policy_behavior_samples"]; !exists || samples < canaryMinPolicySamples {
			return false
		}
		if lift, exists := metrics["policy_reward_lift"]; !exists || lift < canaryMinPolicyLift {
			return false
		}
	}
	// Segment-level fairness is a separate safety gate. A globally healthy
	// average must not hide a group whose utility spread breaches the limit.
	if required, ok := metrics["fairness_segment_required"]; ok && required >= 1 {
		if ready, exists := metrics["fairness_segment_ready"]; !exists || ready < 1 {
			return false
		}
		if value, exists := metrics["fairness_segment_max_stddev"]; !exists || value > canaryMaxFairnessStddev {
			return false
		}
		if value, exists := metrics["fairness_segment_threshold_exceeded"]; !exists || value > 0 {
			return false
		}
	}
	// Provider quality is an opt-in input-boundary gate. A report with no
	// provider observations remains legacy-compatible, but once the evaluator
	// supplies this gate, sparse or blocked supplier evidence must not promote a
	// model that would consume untrusted live facts.
	if required, ok := metrics["provider_quality_required"]; ok && required >= 1 {
		if status, exists := metrics["provider_quality_ready"]; !exists || status < 1 {
			return false
		}
		providers, exists := metrics["provider_quality_providers"]
		if !exists || providers < 1 {
			return false
		}
		if value, exists := metrics["provider_quality_insufficient_providers"]; !exists || value > 0 {
			return false
		}
		if value, exists := metrics["provider_quality_blocked_providers"]; !exists || value > 0 {
			return false
		}
		if ready, exists := metrics["provider_quality_ready_providers"]; !exists || ready < providers {
			return false
		}
	}
	// Reports produced by the offline evaluator carry these fields.  Older
	// locally-created deployments may omit them, so absence remains compatible;
	// once present, a blocked/unknown report cannot enter canary traffic.
	if value, ok := metrics["evaluation_ready"]; ok && value < 1 {
		return false
	}
	if value, ok := metrics["counterfactual_samples"]; ok && value <= 0 {
		return false
	}
	return true
}

// segmentFairnessReasons re-checks the persisted aggregate during every
// canary health sweep. This protects against a later evaluator update that
// discovers a subgroup regression after the original promotion gate passed.
func segmentFairnessReasons(deployment models.ModelDeployment) []string {
	if strings.TrimSpace(deployment.Metrics) == "" {
		return nil
	}
	var metrics map[string]float64
	if json.Unmarshal([]byte(deployment.Metrics), &metrics) != nil || metrics["fairness_segment_required"] < 1 {
		return nil
	}
	reasons := []string{}
	if metrics["fairness_segment_ready"] < 1 {
		reasons = append(reasons, "segment_fairness_insufficient")
	}
	if value, exists := metrics["fairness_segment_max_stddev"]; !exists || value > canaryMaxFairnessStddev {
		reasons = append(reasons, "segment_fairness_variance_exceeded")
	}
	if value, exists := metrics["fairness_segment_threshold_exceeded"]; !exists || value > 0 {
		reasons = append(reasons, "segment_fairness_threshold_exceeded")
	}
	return reasons
}

// requireEvaluationServiceToken keeps the report ingestion boundary separate
// from browser authentication.  The AI service calls this endpoint directly;
// the token is never accepted from a query parameter or persisted.
func requireEvaluationServiceToken(c *gin.Context) bool {
	want := strings.TrimSpace(os.Getenv("AI_SERVICE_INTERNAL_TOKEN"))
	have := strings.TrimSpace(c.GetHeader("X-Omni-Internal-Token"))
	if want == "" || have == "" || subtle.ConstantTimeCompare([]byte(want), []byte(have)) != 1 {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "评估服务未授权", "code": "EVALUATION_UNAUTHORIZED"})
		return false
	}
	return true
}

func evaluationNumber(value any) (float64, bool) {
	switch typed := value.(type) {
	case float64:
		return typed, !math.IsNaN(typed) && !math.IsInf(typed, 0)
	case float32:
		return float64(typed), !math.IsNaN(float64(typed)) && !math.IsInf(float64(typed), 0)
	case int:
		return float64(typed), true
	case int64:
		return float64(typed), true
	case uint:
		return float64(typed), true
	case uint64:
		return float64(typed), true
	case json.Number:
		parsed, err := typed.Float64()
		return parsed, err == nil && !math.IsNaN(parsed) && !math.IsInf(parsed, 0)
	default:
		return 0, false
	}
}

// evaluationMetricsFromReport accepts only the aggregate fields needed by the
// runtime gate.  Raw samples, user identifiers and provider payloads are never
// copied into the model registry.
func evaluationMetricsFromReport(report map[string]any) (map[string]float64, error) {
	status, _ := report["status"].(string)
	promotionReady, hasPromotionReady := report["promotion_ready"].(bool)
	if status != "ready" && status != "insufficient_evidence" {
		return nil, fmt.Errorf("评估状态无效")
	}
	read := func(key string, min, max float64, required bool) (float64, bool, error) {
		value, exists := report[key]
		if !exists {
			if required {
				return 0, false, fmt.Errorf("缺少 %s", key)
			}
			return 0, false, nil
		}
		parsed, ok := evaluationNumber(value)
		if !ok || parsed < min || parsed > max {
			return 0, false, fmt.Errorf("%s 超出范围", key)
		}
		return parsed, true, nil
	}
	strict := status == "ready"
	evaluationSamples, hasEvaluationSamples, err := read("evaluation_samples", 0, 1000000000, strict)
	if err != nil {
		return nil, err
	}
	validationSamples, hasValidationSamples, err := read("validation_samples", 0, 1000000000, strict)
	if err != nil {
		return nil, err
	}
	offlineScore, hasOfflineScore, err := read("offline_score", 0, 1, strict)
	if err != nil {
		return nil, err
	}
	driftScore, hasDriftScore, err := read("drift_score", 0, 1, strict)
	if err != nil {
		return nil, err
	}
	counterfactualSamples, hasCounterfactualSamples, err := read("counterfactual_samples", 0, 1000000000, strict)
	if err != nil {
		return nil, err
	}
	fairnessVariance, hasFairnessVariance, err := read("fairness_utility_variance", 0, 1, strict)
	if err != nil {
		return nil, err
	}
	fairnessMinimum, hasFairnessMinimum, err := read("fairness_min_utility", 0, 1, false)
	if err != nil {
		return nil, err
	}
	ready := status == "ready" && (!hasPromotionReady || promotionReady)
	if hasPromotionReady && status == "ready" && !promotionReady {
		return nil, fmt.Errorf("评估报告状态与 promotion_ready 冲突")
	}
	metrics := map[string]float64{"evaluation_ready": boolMetric(ready), "rollback_seconds": 60}
	if hasEvaluationSamples {
		metrics["evaluation_samples"] = evaluationSamples
	}
	if hasValidationSamples {
		metrics["validation_samples"] = validationSamples
	}
	if hasOfflineScore {
		metrics["offline_score"] = offlineScore
	}
	if hasDriftScore {
		metrics["drift_score"] = driftScore
	}
	if hasCounterfactualSamples {
		metrics["counterfactual_samples"] = counterfactualSamples
	}
	if hasFairnessVariance {
		metrics["fairness_utility_variance"] = fairnessVariance
	}
	if hasFairnessMinimum {
		metrics["fairness_min_utility"] = fairnessMinimum
	}
	// Policy comparison fields are optional for legacy deployments. Their
	// presence opts the deployment into the stricter two-arm gate above.
	policyRequired := false
	if _, exists := report["policy_comparison"]; exists {
		policyRequired = true
	}
	if value, exists := report["policy_comparison_required"].(bool); exists {
		policyRequired = value
	}
	if policyRequired {
		metrics["policy_comparison_required"] = 1
		policyStatus := strings.TrimSpace(fmt.Sprint(report["policy_comparison_status"]))
		if policyStatus == "<nil>" || policyStatus == "" {
			if comparison, ok := report["policy_comparison"].(map[string]any); ok {
				policyStatus = strings.TrimSpace(fmt.Sprint(comparison["status"]))
			}
		}
		metrics["policy_comparison_status"] = boolMetric(policyStatus == "available")
		ready := policyStatus == "available"
		if value, exists := report["policy_comparison_ready"].(bool); exists {
			ready = ready && value
		}
		metrics["policy_comparison_ready"] = boolMetric(ready)
		for key, bounds := range map[string][2]float64{
			"policy_baseline_samples": {0, 1000000000},
			"policy_behavior_samples": {0, 1000000000},
		} {
			if value, exists := report[key]; exists {
				parsed, ok := evaluationNumber(value)
				if !ok || parsed < bounds[0] || parsed > bounds[1] {
					return nil, fmt.Errorf("%s 超出范围", key)
				}
				metrics[key] = parsed
			}
		}
		if comparison, ok := report["policy_comparison"].(map[string]any); ok {
			for source, target := range map[string]string{"baseline": "policy_baseline_samples", "behavior_aware": "policy_behavior_samples"} {
				if _, exists := metrics[target]; exists {
					continue
				}
				if group, groupOK := comparison[source].(map[string]any); groupOK {
					if parsed, parsedOK := evaluationNumber(group["samples"]); parsedOK {
						metrics[target] = parsed
					}
				}
			}
		}
		if value, exists := report["policy_reward_lift"]; exists {
			parsed, ok := evaluationNumber(value)
			if !ok || parsed < -1 || parsed > 1 {
				return nil, fmt.Errorf("policy_reward_lift 超出范围")
			}
			metrics["policy_reward_lift"] = parsed
		} else if comparison, ok := report["policy_comparison"].(map[string]any); ok {
			if parsed, parsedOK := evaluationNumber(comparison["reward_lift"]); parsedOK {
				metrics["policy_reward_lift"] = parsed
			}
		}
		if value, exists := report["policy_adoption_signal_lift"]; exists {
			if parsed, parsedOK := evaluationNumber(value); parsedOK {
				metrics["policy_adoption_signal_lift"] = parsed
			}
		} else if comparison, ok := report["policy_comparison"].(map[string]any); ok {
			if parsed, parsedOK := evaluationNumber(comparison["adoption_signal_lift"]); parsedOK {
				metrics["policy_adoption_signal_lift"] = parsed
			}
		}
	}
	fairnessSegmentRequired := false
	if _, exists := report["fairness_segment_summary"]; exists {
		fairnessSegmentRequired = true
	}
	if value, exists := report["fairness_segment_required"].(bool); exists {
		fairnessSegmentRequired = value
	}
	if fairnessSegmentRequired {
		metrics["fairness_segment_required"] = 1
		status := strings.TrimSpace(fmt.Sprint(report["fairness_segment_status"]))
		if status == "<nil>" || status == "" {
			if summary, ok := report["fairness_segment_summary"].(map[string]any); ok {
				status = strings.TrimSpace(fmt.Sprint(summary["status"]))
			}
		}
		metrics["fairness_segment_ready"] = boolMetric(status == "available")
		if value, exists := report["fairness_segment_max_stddev"]; exists {
			parsed, ok := evaluationNumber(value)
			if !ok || parsed < 0 || parsed > 1 {
				return nil, fmt.Errorf("fairness_segment_max_stddev 超出范围")
			}
			metrics["fairness_segment_max_stddev"] = parsed
		} else if summary, ok := report["fairness_segment_summary"].(map[string]any); ok {
			if parsed, parsedOK := evaluationNumber(summary["max_stddev"]); parsedOK {
				metrics["fairness_segment_max_stddev"] = parsed
			}
		}
		if value, exists := report["fairness_segment_threshold_exceeded"]; exists {
			parsed, ok := evaluationNumber(value)
			if !ok || parsed < 0 {
				return nil, fmt.Errorf("fairness_segment_threshold_exceeded 无效")
			}
			metrics["fairness_segment_threshold_exceeded"] = parsed
		} else if summary, ok := report["fairness_segment_summary"].(map[string]any); ok {
			if parsed, parsedOK := evaluationNumber(summary["threshold_exceeded"]); parsedOK {
				metrics["fairness_segment_threshold_exceeded"] = parsed
			}
		}
	}
	providerQualityRequired := false
	if value, exists := report["provider_quality_required"].(bool); exists {
		providerQualityRequired = value
	}
	if value, exists := report["provider_quality_status"]; exists && strings.TrimSpace(fmt.Sprint(value)) != "not_required" {
		providerQualityRequired = true
	}
	if providerQualityRequired {
		metrics["provider_quality_required"] = 1
		status := strings.TrimSpace(fmt.Sprint(report["provider_quality_status"]))
		metrics["provider_quality_ready"] = boolMetric(status == "available")
		for key, bounds := range map[string][2]float64{
			"provider_quality_providers":              {0, 1000000000},
			"provider_quality_ready_providers":        {0, 1000000000},
			"provider_quality_insufficient_providers": {0, 1000000000},
			"provider_quality_blocked_providers":      {0, 1000000000},
		} {
			if value, exists := report[key]; exists {
				parsed, ok := evaluationNumber(value)
				if !ok || parsed < bounds[0] || parsed > bounds[1] {
					return nil, fmt.Errorf("%s 超出范围", key)
				}
				metrics[key] = parsed
			}
		}
	}
	return metrics, nil
}

func boolMetric(value bool) float64 {
	if value {
		return 1
	}
	return 0
}

// IngestModelEvaluationHandler is called by the offline evaluator, not by the
// browser.  It stores only validated aggregate metrics and never promotes a
// model; promotion remains a separate explicit, gated operation.
func IngestModelEvaluationHandler(c *gin.Context) {
	if !requireEvaluationServiceToken(c) {
		return
	}
	id, err := strconv.ParseUint(c.Param("id"), 10, 64)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "模型标识无效", "code": "MODEL_ID_INVALID"})
		return
	}
	var report map[string]any
	decoder := json.NewDecoder(io.LimitReader(c.Request.Body, 256*1024))
	if err := decoder.Decode(&report); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "评估报告格式无效", "code": "EVALUATION_INVALID"})
		return
	}
	var deployment models.ModelDeployment
	if err := database.DB.First(&deployment, id).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "模型版本不存在", "code": "MODEL_NOT_FOUND"})
		return
	}
	if supplied, ok := report["model_version"].(string); ok && strings.TrimSpace(supplied) != "" && strings.TrimSpace(supplied) != deployment.Version {
		c.JSON(http.StatusConflict, gin.H{"error": "评估报告模型版本与注册表不一致", "code": "MODEL_VERSION_MISMATCH"})
		return
	}
	metrics, err := evaluationMetricsFromReport(report)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error(), "code": "EVALUATION_METRICS_INVALID"})
		return
	}
	encoded, _ := json.Marshal(metrics)
	deployment.Metrics = string(encoded)
	evaluationStatus := "insufficient_evidence"
	if metrics["evaluation_ready"] >= 1 {
		evaluationStatus = "ready"
	}
	sampleCount := 0
	if value, ok := metrics["evaluation_samples"]; ok && value >= 0 {
		sampleCount = int(value)
	}
	if err := database.DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Save(&deployment).Error; err != nil {
			return err
		}
		// The callback is the scheduler's durable observation point. Keep the
		// audit record in the same transaction as metrics so a partial callback
		// cannot make the diagnostics timeline claim a result that was not saved.
		recordModelAuditEventTx(tx, "evaluation", deployment.Version, evaluationStatus, "周期评估回写已接收", sampleCount, 0, time.Now().UTC())
		return nil
	}); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "评估结果写入失败", "code": "EVALUATION_WRITE_FAILED"})
		return
	}
	planningData(c, gin.H{"accepted": true, "model": deployment, "promotion_ready": metrics["evaluation_ready"] == 1})
}

// isPlanningAdmin reports whether userID is an explicitly configured planning
// administrator.
//
// The previous implementation split an empty env var into [""], which matched
// the empty user ID. It was saved only by an unrelated `userID != ""` guard, so
// any caller that reached a handler without AuthMiddleware — or any future
// middleware reordering — silently became a planning admin, able to register
// arbitrary model endpoints and export the full training dataset.
func isPlanningAdmin(userID string) bool {
	userID = strings.TrimSpace(userID)
	if userID == "" {
		return false
	}
	raw := strings.TrimSpace(os.Getenv("PLANNING_ADMIN_USER_IDS"))
	if raw == "" {
		// No allowlist configured means nobody is an admin.
		return false
	}
	for _, candidate := range strings.Split(raw, ",") {
		if candidate = strings.TrimSpace(candidate); candidate != "" && candidate == userID {
			return true
		}
	}
	return false
}

// validateModelEndpointHost rejects model execution endpoints that point at
// loopback, private, link-local or cloud-metadata addresses.
//
// The endpoint is admin-supplied but stored in the database and later POSTed to
// with an attacker-influenced body (planning.go promotion/shadow paths), which
// made it an SSRF primitive: http://169.254.169.254/… reaches cloud metadata and
// http://127.0.0.1:<port>/ scans the internal network by timing and error text.
func validateModelEndpointHost(raw string) error {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return fmt.Errorf("模型执行端点无法解析")
	}
	host := parsed.Hostname()
	if host == "" {
		return fmt.Errorf("模型执行端点缺少主机名")
	}
	if strings.EqualFold(host, "localhost") || strings.HasSuffix(strings.ToLower(host), ".localhost") {
		return fmt.Errorf("模型执行端点不允许指向本机地址")
	}

	// An IP literal can be judged directly; a hostname is resolved so that a
	// name pointing into private space is rejected too.
	ips := []net.IP{}
	if ip := net.ParseIP(host); ip != nil {
		ips = append(ips, ip)
	} else {
		resolved, lookupErr := net.LookupIP(host)
		if lookupErr != nil {
			return fmt.Errorf("模型执行端点域名无法解析")
		}
		ips = append(ips, resolved...)
	}
	for _, ip := range ips {
		if ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() ||
			ip.IsLinkLocalMulticast() || ip.IsUnspecified() || ip.IsMulticast() {
			return fmt.Errorf("模型执行端点不允许指向内网或链路本地地址")
		}
		// 169.254.169.254 / fd00:ec2::254 — the cloud metadata services.
		if ip.IsLinkLocalUnicast() || ip.String() == "169.254.169.254" {
			return fmt.Errorf("模型执行端点不允许指向云元数据地址")
		}
	}
	return nil
}

// currentPlanningAdminOrReject resolves the caller and enforces the planning-admin
// allowlist, writing a 403 and returning false when they are not an admin.
//
// It exists because the write side of the model registry (register / evaluate /
// promote) was already admin-gated while the read side was not. The registry is
// infrastructure configuration, not user data: a ModelDeployment row carries the
// internal inference Endpoint, and the runtime view exposes whether a model is in
// canary, its error rate and its fairness spread. Serving that to any logged-in
// account is internal-topology disclosure and free reconnaissance for the SSRF
// surface that validateModelEndpointHost exists to contain.
func currentPlanningAdminOrReject(c *gin.Context) (string, bool) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return "", false
	}
	if !isPlanningAdmin(userID) {
		planningError(c, http.StatusForbidden, "MODEL_ADMIN_REQUIRED", "只有规划服务管理员可以查看模型注册表", nil)
		return "", false
	}
	return userID, true
}

func ListModelDeploymentsHandler(c *gin.Context) {
	if _, ok := currentPlanningAdminOrReject(c); !ok {
		return
	}
	var modelsList []models.ModelDeployment
	if err := database.DB.Order("created_at desc").Find(&modelsList).Error; err != nil {
		planningError(c, http.StatusInternalServerError, "MODEL_REGISTRY_UNAVAILABLE", "模型注册表暂不可用", nil)
		return
	}
	planningData(c, gin.H{"models": modelsList, "active": activeModel(modelsList), "policy": gin.H{"canary_max_percent": 20, "promotion_requires": []string{"offline_evaluation", "drift_check", "rollback_ready"}}})
}

func RegisterModelDeploymentHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if !isPlanningAdmin(userID) {
		planningError(c, http.StatusForbidden, "MODEL_ADMIN_REQUIRED", "只有规划服务管理员可以注册模型", nil)
		return
	}
	var req struct {
		Name     string             `json:"name"`
		Version  string             `json:"version"`
		Status   string             `json:"status"`
		Traffic  int                `json:"traffic_percent"`
		Endpoint string             `json:"endpoint"`
		Metrics  map[string]float64 `json:"metrics"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_MODEL", "模型参数不完整", nil)
		return
	}
	req.Name, req.Version = strings.TrimSpace(req.Name), strings.TrimSpace(req.Version)
	if req.Name == "" || req.Version == "" {
		planningError(c, http.StatusBadRequest, "MODEL_ID_REQUIRED", "模型名称和版本不能为空", nil)
		return
	}
	if len(req.Name) > 100 || len(req.Version) > 100 {
		planningError(c, http.StatusBadRequest, "MODEL_ID_TOO_LONG", "模型名称或版本过长", nil)
		return
	}
	if req.Status == "" {
		req.Status = "shadow"
	}
	if req.Status != "shadow" && req.Status != "canary" {
		planningError(c, http.StatusBadRequest, "MODEL_STATUS_INVALID", "新模型只能以 shadow 或 canary 状态注册", nil)
		return
	}
	if req.Traffic < 0 || req.Traffic > 20 {
		planningError(c, http.StatusBadRequest, "MODEL_TRAFFIC_INVALID", "灰度流量必须在 0 到 20 之间", nil)
		return
	}
	if req.Endpoint != "" {
		if !strings.HasPrefix(req.Endpoint, "https://") && !strings.HasPrefix(req.Endpoint, "http://") {
			planningError(c, http.StatusBadRequest, "MODEL_ENDPOINT_INVALID", "模型执行端点必须使用 http 或 https", nil)
			return
		}
		if len(req.Endpoint) > 500 {
			planningError(c, http.StatusBadRequest, "MODEL_ENDPOINT_TOO_LONG", "模型执行端点过长", nil)
			return
		}
		if err := validateModelEndpointHost(req.Endpoint); err != nil {
			planningError(c, http.StatusBadRequest, "MODEL_ENDPOINT_UNSAFE", err.Error(), nil)
			return
		}
	}
	metricsJSON, _ := json.Marshal(req.Metrics)
	deployment := models.ModelDeployment{Name: req.Name, Version: req.Version, Status: req.Status, Traffic: req.Traffic, Endpoint: strings.TrimSpace(req.Endpoint), Metrics: string(metricsJSON)}
	if err := database.DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(&deployment).Error; err != nil {
			return err
		}
		recordModelAuditEventTx(tx, "deployment", deployment.Version, "registered", "模型版本已登记", 0, 0, deployment.CreatedAt)
		return nil
	}); err != nil {
		planningError(c, http.StatusConflict, "MODEL_VERSION_EXISTS", "模型版本已存在或注册失败", nil)
		return
	}
	planningData(c, gin.H{"model": deployment, "next": "先执行离线评估，再允许晋级"})
}

func PromoteModelDeploymentHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if !isPlanningAdmin(userID) {
		planningError(c, http.StatusForbidden, "MODEL_ADMIN_REQUIRED", "只有规划服务管理员可以晋级模型", nil)
		return
	}
	id, err := strconv.ParseUint(c.Param("id"), 10, 64)
	if err != nil {
		planningError(c, http.StatusBadRequest, "MODEL_ID_INVALID", "模型标识无效", nil)
		return
	}
	var deployment models.ModelDeployment
	if err := database.DB.First(&deployment, id).Error; err != nil {
		planningError(c, http.StatusNotFound, "MODEL_NOT_FOUND", "模型版本不存在", nil)
		return
	}
	var metrics map[string]float64
	_ = json.Unmarshal([]byte(deployment.Metrics), &metrics)
	if !deploymentPassesRuntimeGate(deployment) {
		planningError(c, http.StatusConflict, "MODEL_GATE_FAILED", "模型未通过离线质量、样本量、公平性、漂移或回滚门禁", map[string]string{"required_offline_score": "0.80", "max_drift_score": "0.20", "minimum_evaluation_samples": "20", "minimum_validation_samples": "5", "rollback_seconds": "must_be_positive"})
		return
	}
	if err := database.DB.Transaction(func(tx *gorm.DB) error {
		var active models.ModelDeployment
		if err := tx.Where("status = ?", "active").First(&active).Error; err == nil {
			active.Status, active.Traffic = "rolled_back", 0
			if err := tx.Save(&active).Error; err != nil {
				return err
			}
			recordModelAuditEventTx(tx, "deployment", active.Version, "rolled_back", "旧模型因新版本晋级而退出 active", 0, 0, time.Now().UTC())
		} else if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		deployment.Status, deployment.Traffic = "active", 100
		if err := tx.Save(&deployment).Error; err != nil {
			return err
		}
		recordModelAuditEventTx(tx, "deployment", deployment.Version, "active", "模型版本已晋级为 active", 0, 0, time.Now().UTC())
		return nil
	}); err != nil {
		planningError(c, http.StatusInternalServerError, "MODEL_PROMOTION_FAILED", "模型晋级失败", nil)
		return
	}
	planningData(c, gin.H{"model": deployment, "message": "模型已晋级为 active，旧版本已标记为 rolled_back"})
}

func EvaluateModelDeploymentHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if !isPlanningAdmin(userID) {
		planningError(c, http.StatusForbidden, "MODEL_ADMIN_REQUIRED", "只有规划服务管理员可以评估模型", nil)
		return
	}
	id, err := strconv.ParseUint(c.Param("id"), 10, 64)
	if err != nil {
		planningError(c, http.StatusBadRequest, "MODEL_ID_INVALID", "模型标识无效", nil)
		return
	}
	var deployment models.ModelDeployment
	if err := database.DB.First(&deployment, id).Error; err != nil {
		planningError(c, http.StatusNotFound, "MODEL_NOT_FOUND", "模型版本不存在", nil)
		return
	}
	var events []models.PlanningEvent
	database.DB.Where("event_type IN ?", []string{"plan_generated", "plan_adopted", "plan_rejected"}).Order("created_at asc").Find(&events)
	generated, adopted, rejected := 0, 0, 0
	versioned := make([]models.PlanningEvent, 0, len(events))
	for _, event := range events {
		var payload map[string]any
		_ = json.Unmarshal([]byte(event.Payload), &payload)
		if version, _ := payload["model_version"].(string); version != deployment.Version {
			continue
		}
		versioned = append(versioned, event)
		switch event.EventType {
		case "plan_generated":
			generated++
		case "plan_adopted":
			adopted++
		case "plan_rejected":
			rejected++
		}
	}
	// Use the newest 20% of versioned events as a temporal validation slice.
	// This prevents feedback from the future leaking into the score used for
	// promotion. With too little data, the score remains conservative.
	validationStart := len(versioned)
	if len(versioned) > 0 {
		validationStart = int(math.Floor(float64(len(versioned)) * 0.8))
	}
	trainGenerated, trainAdopted := 0, 0
	validationGenerated, validationAdopted := 0, 0
	for index, event := range versioned {
		if event.EventType != "plan_generated" && event.EventType != "plan_adopted" {
			continue
		}
		if index >= validationStart {
			if event.EventType == "plan_generated" {
				validationGenerated++
			} else {
				validationAdopted++
			}
		} else if event.EventType == "plan_generated" {
			trainGenerated++
		} else {
			trainAdopted++
		}
	}
	trainScore, validationScore := 0.0, 0.0
	if trainGenerated > 0 {
		trainScore = float64(trainAdopted) / float64(trainGenerated)
	}
	if validationGenerated > 0 {
		validationScore = float64(validationAdopted) / float64(validationGenerated)
	}
	offlineScore := validationScore
	if validationGenerated == 0 {
		offlineScore = trainScore
	}
	drift := eventTypeDrift(versioned, time.Now().Add(-14*24*time.Hour))
	driftScore, _ := drift["score"].(float64)
	counterfactual := modelCounterfactualEstimate(versioned)
	quality := qualityMetricSummary(versioned)
	policyComparison := modelPolicyComparisonEstimate(versioned)
	segmentFairness := modelSegmentFairnessEstimate(versioned)
	metrics := map[string]float64{"offline_score": offlineScore, "train_score": trainScore, "validation_score": validationScore, "drift_score": driftScore, "rollback_seconds": 60, "generated": float64(generated), "adopted": float64(adopted), "rejected": float64(rejected), "validation_generated": float64(validationGenerated), "validation_adopted": float64(validationAdopted), "evaluation_samples": float64(len(versioned)), "validation_samples": float64(len(versioned) - validationStart), "counterfactual_samples": float64(counterfactual["samples"].(int)), "evaluated_at_unix": float64(time.Now().Unix())}
	for _, key := range []string{"constraint_satisfaction_rate", "candidate_validity_rate", "fact_citation_rate", "latency_ms", "adoption_rate", "replacement_rate", "replan_success_rate", "fairness_min_utility", "fairness_utility_variance", "provider_stale_rate", "error_rate"} {
		if entry, ok := quality[key].(map[string]any); ok {
			if mean, ok := numericPlanningValue(entry["mean"]); ok {
				metrics[key] = mean
			}
		}
	}
	if policyComparison["required"] == true {
		metrics["policy_comparison_required"] = 1
		metrics["policy_comparison_status"] = boolMetric(policyComparison["status"] == "available")
		metrics["policy_comparison_ready"] = boolMetric(policyComparison["status"] == "available")
		if value, ok := evaluationNumber(policyComparison["baseline_samples"]); ok {
			metrics["policy_baseline_samples"] = value
		}
		if value, ok := evaluationNumber(policyComparison["behavior_samples"]); ok {
			metrics["policy_behavior_samples"] = value
		}
		if value, ok := evaluationNumber(policyComparison["reward_lift"]); ok {
			metrics["policy_reward_lift"] = value
		}
		if value, ok := evaluationNumber(policyComparison["adoption_signal_lift"]); ok {
			metrics["policy_adoption_signal_lift"] = value
		}
	}
	if segmentFairness["required"] == true {
		metrics["fairness_segment_required"] = 1
		metrics["fairness_segment_ready"] = boolMetric(segmentFairness["status"] == "available")
		if value, ok := evaluationNumber(segmentFairness["max_stddev"]); ok {
			metrics["fairness_segment_max_stddev"] = value
		}
		if value, ok := evaluationNumber(segmentFairness["threshold_exceeded"]); ok {
			metrics["fairness_segment_threshold_exceeded"] = value
		}
	}
	encoded, _ := json.Marshal(metrics)
	deployment.Metrics = string(encoded)
	evaluationStatus := map[bool]string{true: "ready", false: "insufficient_evidence"}[deploymentPassesRuntimeGate(deployment)]
	gateReady := deploymentPassesRuntimeGate(deployment)
	reasons := []string{}
	if len(versioned) < 20 {
		reasons = append(reasons, "evaluation_samples_insufficient")
	}
	if len(versioned)-validationStart < 5 {
		reasons = append(reasons, "validation_samples_insufficient")
	}
	if offlineScore < 0.80 {
		reasons = append(reasons, "offline_score_below_threshold")
	}
	if driftScore > 0.20 {
		reasons = append(reasons, "drift_above_threshold")
	}
	if policyComparison["required"] == true {
		if policyComparison["status"] != "available" {
			reasons = append(reasons, "policy_comparison_insufficient")
		} else if lift, ok := evaluationNumber(policyComparison["reward_lift"]); !ok || lift < canaryMinPolicyLift {
			reasons = append(reasons, "policy_reward_lift_below_threshold")
		}
	}
	if segmentFairness["required"] == true {
		if segmentFairness["status"] != "available" {
			reasons = append(reasons, "segment_fairness_insufficient")
		} else if value, ok := evaluationNumber(segmentFairness["max_stddev"]); !ok || value > canaryMaxFairnessStddev {
			reasons = append(reasons, "segment_fairness_variance_exceeded")
		} else if value, ok := evaluationNumber(segmentFairness["threshold_exceeded"]); !ok || value > 0 {
			reasons = append(reasons, "segment_fairness_threshold_exceeded")
		}
	}
	evaluationAt := time.Now().UTC()
	if err := database.DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Save(&deployment).Error; err != nil {
			return err
		}
		recordModelAuditEventReasonsTx(tx, "evaluation", deployment.Version, evaluationStatus, "离线评估结果已生成", len(versioned), len(reasons), reasons, evaluationAt)
		return nil
	}); err != nil {
		planningError(c, http.StatusInternalServerError, "MODEL_EVALUATION_PERSIST_FAILED", "模型评估结果保存失败", nil)
		return
	}
	reasonMessages := map[string]string{"evaluation_samples_insufficient": "评估样本不足", "validation_samples_insufficient": "验证集样本不足", "offline_score_below_threshold": "离线分数低于 0.80", "drift_above_threshold": "数据漂移超过 0.20", "policy_comparison_insufficient": "基线/行为排序样本不足或缺少 reward", "policy_reward_lift_below_threshold": "行为排序 reward 提升低于 0.01", "segment_fairness_insufficient": "分层公平样本不足", "segment_fairness_variance_exceeded": "分层满意度方差超过 0.10", "segment_fairness_threshold_exceeded": "存在分层公平阈值超标样本"}
	reasonLabels := make([]string, 0, len(reasons))
	for _, code := range reasons {
		if label, ok := reasonMessages[code]; ok {
			reasonLabels = append(reasonLabels, label)
		}
	}
	planningData(c, gin.H{"model": deployment, "evaluation": metrics, "quality_metrics": quality, "policy_comparison": policyComparison, "segment_fairness": segmentFairness, "temporal_split": gin.H{"policy": "oldest_80_percent_train_newest_20_percent_validation", "train_generated": trainGenerated, "validation_generated": validationGenerated}, "counterfactual": counterfactual, "gate": gin.H{"ready": gateReady, "reasons": reasonLabels, "reason_codes": reasons, "automatic_rollback": true}, "promotion_ready": gateReady})
}

func modelSegmentFairnessEstimate(events []models.PlanningEvent) map[string]any {
	dimensions := []string{"traveler_group", "destination", "season", "budget_bucket"}
	values := map[string]map[string][]float64{}
	exceeded := map[string]map[string]int{}
	for _, dimension := range dimensions {
		values[dimension] = map[string][]float64{}
		exceeded[dimension] = map[string]int{}
	}
	required := false
	for _, event := range events {
		if event.EventType != "planning_decision_audit" {
			continue
		}
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		stddev, ok := numericPayload(payload, "fairness_utility_stddev")
		if !ok {
			stddev, ok = numericPayload(payload, "utility_stddev")
		}
		if !ok {
			if variance, varianceOK := numericPayload(payload, "fairness_utility_variance"); varianceOK && variance >= 0 {
				stddev, ok = math.Sqrt(variance), true
			}
		}
		if !ok || stddev < 0 || stddev > 1 {
			continue
		}
		for _, dimension := range dimensions {
			value := strings.TrimSpace(fmt.Sprint(payload[dimension]))
			if value == "" || value == "<nil>" {
				continue
			}
			required = true
			values[dimension][value] = append(values[dimension][value], stddev)
			if exceededFlag, flagOK := payload["fairness_threshold_exceeded"].(bool); flagOK && exceededFlag {
				exceeded[dimension][value]++
			}
		}
	}
	result := map[string]any{"required": required, "status": "insufficient_data", "max_stddev": nil, "threshold_exceeded": 0}
	if !required {
		result["status"] = "not_required"
		return result
	}
	available, maxStddev, thresholdCount := 0, 0.0, 0
	availableDimensions := map[string]bool{}
	for dimension, groups := range values {
		for value, samples := range groups {
			if len(samples) < canaryMinPolicySamples {
				continue
			}
			available++
			availableDimensions[dimension] = true
			for _, sample := range samples {
				maxStddev = math.Max(maxStddev, sample)
			}
			thresholdCount += exceeded[dimension][value]
		}
	}
	if len(availableDimensions) == len(values) {
		result["status"] = "available"
		result["max_stddev"] = roundRatio(maxStddev)
	} else if available > 0 {
		result["status"] = "partial"
		result["max_stddev"] = roundRatio(maxStddev)
	}
	result["threshold_exceeded"] = thresholdCount
	return result
}

// modelPolicyComparisonEstimate derives only bounded aggregates from ranking
// audit events. It is deliberately opt-in: legacy model evaluations without a
// behavior_enabled field remain compatible, while behavior-aware deployments
// cannot bypass the two-arm evidence gate.
func modelPolicyComparisonEstimate(events []models.PlanningEvent) map[string]any {
	type group struct {
		samples int
		rewards []float64
		adopted []float64
	}
	groups := map[string]*group{"baseline": {}, "behavior_aware": {}}
	required := false
	outcomeRewards := map[string][]float64{}
	for _, event := range events {
		if event.EventType != "plan_adopted" && event.EventType != "plan_rejected" && event.EventType != "satisfaction" {
			continue
		}
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		reward, ok := numericPayload(payload, "reward")
		if !ok {
			switch event.EventType {
			case "plan_adopted":
				reward, ok = 1, true
			case "plan_rejected":
				reward, ok = 0, true
			case "satisfaction":
				score, scoreOK := numericPayload(payload, "score")
				if scoreOK {
					reward, ok = score, score <= 1
					if score > 1 {
						reward, ok = score/5, score <= 5
					}
				}
			}
		}
		if ok && reward >= 0 && reward <= 1 {
			outcomeRewards[event.TripID] = append(outcomeRewards[event.TripID], reward)
		}
	}
	outcomeIndex := map[string]int{}
	for _, event := range events {
		if event.EventType != "planning_decision_audit" {
			continue
		}
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		rawEnabled, exists := payload["behavior_enabled"]
		if !exists {
			continue
		}
		required = true
		enabled := false
		switch value := rawEnabled.(type) {
		case bool:
			enabled = value
		case string:
			enabled = strings.EqualFold(strings.TrimSpace(value), "true")
		case float64:
			enabled = value >= 1
		}
		name := "baseline"
		if enabled {
			name = "behavior_aware"
		}
		entry := groups[name]
		entry.samples++
		if reward, ok := numericPayload(payload, "reward"); ok && reward >= 0 && reward <= 1 {
			entry.rewards = append(entry.rewards, reward)
		} else if rewards := outcomeRewards[event.TripID]; len(rewards) > outcomeIndex[event.TripID] {
			entry.rewards = append(entry.rewards, rewards[outcomeIndex[event.TripID]])
			outcomeIndex[event.TripID]++
		}
		if adopted, ok := numericPayload(payload, "behavior_adopted"); ok {
			entry.adopted = append(entry.adopted, adopted)
		}
	}
	result := map[string]any{"required": required, "status": "insufficient_data", "baseline_samples": groups["baseline"].samples, "behavior_samples": groups["behavior_aware"].samples, "reasons": []string{}}
	if !required {
		result["status"] = "not_required"
		result["reasons"] = []string{"未发现行为排序审计字段"}
		return result
	}
	reasons := []string{}
	if groups["baseline"].samples < canaryMinPolicySamples {
		reasons = append(reasons, "baseline_samples_insufficient")
	}
	if groups["behavior_aware"].samples < canaryMinPolicySamples {
		reasons = append(reasons, "behavior_samples_insufficient")
	}
	if len(groups["baseline"].rewards) == 0 || len(groups["behavior_aware"].rewards) == 0 {
		reasons = append(reasons, "policy_reward_missing")
	}
	if len(reasons) == 0 {
		baselineMean := meanFinite(groups["baseline"].rewards)
		behaviorMean := meanFinite(groups["behavior_aware"].rewards)
		result["status"] = "available"
		result["reward_lift"] = roundRatio(behaviorMean - baselineMean)
		if len(groups["baseline"].adopted) > 0 && len(groups["behavior_aware"].adopted) > 0 {
			result["adoption_signal_lift"] = roundRatio(meanFinite(groups["behavior_aware"].adopted) - meanFinite(groups["baseline"].adopted))
		}
	} else {
		result["reasons"] = reasons
	}
	return result
}

func modelCounterfactualEstimate(events []models.PlanningEvent) map[string]any {
	weightedReward, weight := 0.0, 0.0
	samples := 0
	for _, event := range events {
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		propensity, ok := numericPayload(payload, "propensity")
		if !ok || propensity <= 0 || propensity > 1 {
			continue
		}
		reward, ok := numericPayload(payload, "reward")
		if !ok {
			if event.EventType == "plan_adopted" {
				reward = 1
			} else if event.EventType == "plan_rejected" {
				reward = 0
			} else {
				continue
			}
		}
		weightedReward += reward / propensity
		weight += 1 / propensity
		samples++
	}
	if samples == 0 || weight == 0 {
		return map[string]any{"status": "insufficient_data", "samples": 0, "method": "ips", "required_fields": []string{"propensity", "reward"}}
	}
	return map[string]any{"status": "available", "samples": samples, "method": "ips", "estimate": roundRatio(weightedReward / weight)}
}

func numericPayload(payload map[string]any, key string) (float64, bool) {
	value, exists := payload[key]
	if !exists {
		return 0, false
	}
	switch typed := value.(type) {
	case float64:
		return typed, true
	case float32:
		return float64(typed), true
	case int:
		return float64(typed), true
	case json.Number:
		parsed, err := typed.Float64()
		return parsed, err == nil
	default:
		return 0, false
	}
}

func activeModel(items []models.ModelDeployment) *models.ModelDeployment {
	for i := range items {
		if items[i].Status == "active" {
			return &items[i]
		}
	}
	return nil
}

// invokeModelEndpoint is the only gateway boundary for a deployed planner.
// Responses remain opaque until the caller applies route/constraint checks;
// a missing endpoint is an explicit fallback, never a fake model run.
func invokeModelEndpoint(ctx context.Context, deployment models.ModelDeployment, input any) (map[string]any, error) {
	if strings.TrimSpace(deployment.Endpoint) == "" {
		return nil, nil
	}
	body, err := json.Marshal(input)
	if err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, deployment.Endpoint, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("X-OmniRoute-Model-Version", deployment.Version)
	client := &http.Client{Timeout: 3 * time.Second}
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("model endpoint returned %s", response.Status)
	}
	var output map[string]any
	if err := json.NewDecoder(io.LimitReader(response.Body, 256<<10)).Decode(&output); err != nil {
		return nil, err
	}
	return output, nil
}
