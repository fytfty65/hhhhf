package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"gateway/internal/contracts"
	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func TestPlanningContextRequiresTripID(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/planning/context", func(c *gin.Context) { c.Set("user_id", "u-1"); PlanningContextHandler(c) })
	req := httptest.NewRequest(http.MethodPost, "/planning/context", strings.NewReader(`{"destination":"杭州"}`))
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rec.Code)
	}
	var payload map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["error"].(map[string]any)["code"] != "TRIP_ID_REQUIRED" {
		t.Fatalf("unexpected payload: %s", rec.Body.String())
	}
}

func TestProviderQualityGateSummarySeparatesSparseAndBlockedEvidence(t *testing.T) {
	events := make([]models.PlanningEvent, 0, 10)
	for index := 0; index < 5; index++ {
		events = append(events, models.PlanningEvent{EventType: "provider_quality", Payload: `{"kind":"transport","provider":"fixture","candidate_count":10,"decision_eligible_count":9,"decision_blocked_count":1}`})
	}
	for index := 0; index < 5; index++ {
		events = append(events, models.PlanningEvent{EventType: "provider_quality", Payload: `{"kind":"weather","provider":"fixture","candidate_count":10,"decision_eligible_count":9,"decision_blocked_count":1,"enum_invalid_count":2}`})
	}
	result := providerQualityGateSummary(events)
	if result["status"] != "blocked" || result["providers"] != 2 || result["ready_providers"] != 1 || result["blocked_providers"] != 1 {
		t.Fatalf("unexpected provider quality gate: %#v", result)
	}
	alerts, ok := result["alerts"].([]map[string]any)
	if !ok || len(alerts) < 2 {
		t.Fatalf("expected bounded provider quality alerts, got %#v", result["alerts"])
	}
	if digest, ok := result["alert_digest"].(string); !ok || len(digest) != 16 {
		t.Fatalf("expected stable alert digest, got %#v", result["alert_digest"])
	}
	for _, alert := range alerts {
		if _, leaked := alert["provider"]; leaked {
			t.Fatal("provider identity must not be included in operator alerts")
		}
		if _, leaked := alert["payload"]; leaked {
			t.Fatal("raw provider payload must not be included in operator alerts")
		}
	}
	if providerQualityGateSummary([]models.PlanningEvent{{EventType: "provider_quality", Payload: `{"kind":"transport","provider":"fixture","candidate_count":1}`}})["status"] != "insufficient_data" {
		t.Fatal("sparse provider telemetry must remain insufficient_data")
	}
}

func TestProviderQualityAlertDigestIsStableAndSensitiveDataFree(t *testing.T) {
	alerts := []map[string]any{{"code": "provider_quality_blocked", "severity": "critical", "affected_providers": 2}}
	first := providerQualityAlertDigest(alerts)
	second := providerQualityAlertDigest([]map[string]any{{"code": "provider_quality_blocked", "severity": "critical", "affected_providers": 2}})
	if first == "" || first != second || len(first) != 16 {
		t.Fatalf("digest must be stable and bounded: %q %q", first, second)
	}
	if strings.Contains(first, "provider") || strings.Contains(first, "secret") {
		t.Fatal("digest must not contain raw alert text")
	}
	if providerQualityAlertDigest(nil) != "" {
		t.Fatal("empty alert set should not produce a digest")
	}
}

func TestRiskGlobalRejectsEmptyCities(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/risk/global", RiskGlobalHandler)
	req := httptest.NewRequest(http.MethodPost, "/risk/global", strings.NewReader(`{"cities":[]}`))
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rec.Code)
	}
	var payload map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["error"] != "cities 必须为非空数组" {
		t.Fatalf("unexpected payload: %s", rec.Body.String())
	}
}

func TestRiskGlobalRejectsTooManyCitiesBeforeAI(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/risk/global", RiskGlobalHandler)
	req := httptest.NewRequest(http.MethodPost, "/risk/global", strings.NewReader(`{"cities":[{"city":"a"},{"city":"b"},{"city":"c"},{"city":"d"},{"city":"e"},{"city":"f"},{"city":"g"}]}`))
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "CITY_LIMIT") {
		t.Fatalf("expected CITY_LIMIT, got %s", rec.Body.String())
	}
}

func TestPlanningDataEnvelopeIncludesContractMetadata(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/planning/test", func(c *gin.Context) {
		c.Header("X-Request-ID", "rid-test")
		planningData(c, map[string]any{"ok": true})
	})
	req := httptest.NewRequest(http.MethodGet, "/planning/test", bytes.NewReader(nil))
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	var payload map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	meta := payload["meta"].(map[string]any)
	if meta["contract_version"] != "2026-09-04" {
		t.Fatalf("unexpected contract version: %v", meta)
	}
}

func TestEventTypeDriftComparesRecentAndPreviousWindows(t *testing.T) {
	now := time.Now()
	events := []models.PlanningEvent{
		{EventType: "plan_generated", CreatedAt: now.Add(-24 * time.Hour)},
		{EventType: "plan_adopted", CreatedAt: now.Add(-48 * time.Hour)},
		{EventType: "plan_rejected", CreatedAt: now.Add(-10 * 24 * time.Hour)},
		{EventType: "plan_rejected", CreatedAt: now.Add(-11 * 24 * time.Hour)},
	}
	result := eventTypeDrift(events, now.Add(-30*24*time.Hour))
	if result["status"] != "alert" && result["status"] != "watch" {
		t.Fatalf("expected drift signal, got %v", result)
	}
}

func TestProviderMappingAcceptsSupplierAliases(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"results":[{"offer_id":"rail-1","title":"铁路联运","fare":"¥299","duration":180,"rating":4.5,"labels":["低碳"]}]}`))
	}))
	defer server.Close()
	old := os.Getenv("TRANSPORT_PROVIDER_URL")
	defer os.Setenv("TRANSPORT_PROVIDER_URL", old)
	_ = os.Setenv("TRANSPORT_PROVIDER_URL", server.URL)
	options, source, ok := fetchPlanningProvider(context.Background(), "TRANSPORT", contracts.PlanningContext{})
	if !ok || len(options) != 1 {
		t.Fatalf("expected mapped option, got %v / %v", options, ok)
	}
	if options[0].ID != "rail-1" || options[0].Price != 299 || options[0].Duration != 180 || source.Estimated {
		t.Fatalf("unexpected normalized candidate: %+v", options[0])
	}
}

func TestProviderMappingUnwrapsNestedPayloadAndSeconds(t *testing.T) {
	payload := map[string]any{"data": map[string]any{"result": map[string]any{"options": []any{
		map[string]any{"id": "bus-1", "name": "接驳巴士", "duration_seconds": 7200, "rating": 4.5},
	}}}}
	options := normalizeProviderCandidates(payload, "mock")
	if len(options) != 1 {
		t.Fatalf("expected one nested option, got %d", len(options))
	}
	if options[0].Duration != 120 || options[0].Score != 0.9 {
		t.Fatalf("expected seconds/rating normalization, got duration=%d score=%v", options[0].Duration, options[0].Score)
	}
}

func TestProviderMappingNormalizesCrossDomainSupplierAliases(t *testing.T) {
	payload := map[string]any{"data": map[string]any{"items": []any{
		map[string]any{
			"poi_id": "poi-7", "poi_name": "天山景区", "display_price": "¥128",
			"durationMin": 95, "data_time": "2026-09-27T08:00:00Z",
			"crowdLevelText": "moderate", "weatherType": "sunny", "open_hours": "09:00-18:00",
		},
	}}}
	options := normalizeProviderCandidates(payload, "Amap")
	if len(options) != 1 {
		t.Fatalf("expected one aliased option, got %d", len(options))
	}
	option := options[0]
	if option.ID != "poi-7" || option.Name != "天山景区" || option.Price != 128 || option.Duration != 95 {
		t.Fatalf("unexpected aliased fields: %+v", option)
	}
	if option.Source.Retrieved.Format(time.RFC3339) != "2026-09-27T08:00:00Z" || option.Extra["crowdLevelText"] != "moderate" || option.Extra["weatherType"] != "sunny" {
		t.Fatalf("observation or domain fields were not preserved: %+v", option)
	}
}

func TestProviderMappingFixtureIsVersionedAndRedacted(t *testing.T) {
	raw, err := os.ReadFile("testdata/providers/planning_aliases_v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatal(err)
	}
	if payload["contract_version"] != "planning-provider-mapping-v1" || payload["source"] != "redacted_fixture" {
		t.Fatalf("fixture must declare its contract and redacted status: %#v", payload)
	}
	options := normalizeProviderCandidates(payload, "fixture")
	if len(options) != 1 || options[0].ID != "poi-redacted-1" || options[0].Price != 128 || options[0].Duration != 95 {
		t.Fatalf("fixture did not map through the supplier boundary: %#v", options)
	}
	validation, ok := options[0].Extra["enum_validation"].(map[string]any)
	if !ok || validation["crowd_level"].(map[string]any)["status"] != "valid" || validation["condition"].(map[string]any)["status"] != "valid" {
		t.Fatalf("enum validation missing from fixture mapping: %#v", options[0].Extra)
	}
	if strings.Contains(string(raw), "token") || strings.Contains(string(raw), "secret") {
		t.Fatal("fixture must not contain credentials")
	}
}

func TestUnknownProviderEnumCannotPassCrowdDecisionGate(t *testing.T) {
	items := normalizeProviderCandidates(map[string]any{"items": []any{map[string]any{
		"id": "crowd-1", "name": "示例景点", "crowdLevelText": "jammed",
		"observed_at": time.Now().UTC().Format(time.RFC3339),
	}}}, "fixture")
	annotated := annotateCandidates(items, "crowd")
	if annotated[0].Extra["decision_eligible"] == true {
		t.Fatalf("unrecognized crowd enum must not pass the decision gate: %#v", annotated[0].Extra)
	}
	validation := annotated[0].Extra["enum_validation"].(map[string]any)["crowd_level"].(map[string]any)
	if validation["status"] != "unrecognized" {
		t.Fatalf("expected unrecognized enum status: %#v", validation)
	}
}

func TestProviderMappingSanitizesUntrustedLinksAndCredentialQueries(t *testing.T) {
	payload := map[string]any{"results": []any{map[string]any{
		"id": "offer-1", "name": "官方交通方案",
		"url":         "javascript:alert(1)",
		"booking_url": "https://official.example/booking?token=private-token&id=offer-1",
		"link":        "https://official.example/detail?signature=secret-signature&ref=planner",
	}}}
	options := normalizeProviderCandidates(payload, "transport")
	if len(options) != 1 {
		t.Fatalf("expected one normalized option, got %d", len(options))
	}
	if _, exists := options[0].Extra["url"]; exists {
		t.Fatalf("javascript URL must be dropped: %#v", options[0].Extra)
	}
	if got := options[0].Extra["booking_url"]; got != "https://official.example/booking?id=offer-1" {
		t.Fatalf("credential query was not removed: %#v", got)
	}
	if got := options[0].Extra["link"]; got != "https://official.example/detail?ref=planner" {
		t.Fatalf("signature query was not removed: %#v", got)
	}
}

func TestProviderMappingPreservesObservationTimeAndFreshnessPolicy(t *testing.T) {
	payload := map[string]any{"results": []any{map[string]any{
		"id": "flight-1", "name": "航班 A", "price": 420,
		"observed_at": "2026-09-22T08:00:00Z",
	}}}
	options := normalizeProviderCandidates(payload, "supplier")
	if len(options) != 1 {
		t.Fatalf("expected one option, got %d", len(options))
	}
	if options[0].Source.Retrieved.Format(time.RFC3339) != "2026-09-22T08:00:00Z" {
		t.Fatalf("supplier timestamp was not preserved: %+v", options[0].Source)
	}
	annotated := annotateCandidates(options, "transport")
	if annotated[0].Extra["freshness_limit_seconds"] != 300 {
		t.Fatalf("unexpected transport freshness limit: %+v", annotated[0].Extra)
	}
	if annotated[0].Extra["freshness_status"] != "stale" {
		t.Fatalf("old supplier data must be marked stale: %+v", annotated[0].Extra)
	}
}

func TestProviderConflictsRemainVisibleAcrossSources(t *testing.T) {
	candidates := []contracts.Candidate{
		{ID: "a", Name: "同一酒店", Price: 320, Source: contracts.Source{Provider: "RollingGo"}, Extra: map[string]any{"availability": "available"}},
		{ID: "b", Name: "同一酒店", Price: 388, Source: contracts.Source{Provider: "Amap"}, Extra: map[string]any{"availability": "unknown"}},
	}
	annotated := annotateProviderConflicts(candidates)
	for _, candidate := range annotated {
		if candidate.Extra["source_conflict"] != true {
			t.Fatalf("expected source conflict marker: %#v", candidate)
		}
		fields := candidate.Extra["conflict_fields"].([]string)
		if len(fields) != 2 || fields[0] != "availability" || fields[1] != "price" {
			t.Fatalf("unexpected conflict fields: %#v", candidate.Extra)
		}
		if candidate.Extra["conflict_resolution"] != "kept_per_source_for_user_review" {
			t.Fatalf("conflict resolution must remain transparent: %#v", candidate.Extra)
		}
		values := candidate.Extra["source_conflict_values"].([]map[string]any)
		if len(values) != 2 {
			t.Fatalf("expected sanitized per-source values: %#v", candidate.Extra)
		}
		for _, entry := range values {
			if _, ok := entry["values"].(map[string]any); !ok {
				t.Fatalf("source values must remain structured: %#v", entry)
			}
		}
	}
}

func TestProviderConflictsDoNotFlagIdenticalSources(t *testing.T) {
	candidates := []contracts.Candidate{
		{ID: "a", Name: "同一景点", Price: 50, Source: contracts.Source{Provider: "RollingGo"}, Extra: map[string]any{"availability": "available"}},
		{ID: "b", Name: "同一景点", Price: 50, Source: contracts.Source{Provider: "Amap"}, Extra: map[string]any{"availability": "available"}},
	}
	annotated := annotateProviderConflicts(candidates)
	for _, candidate := range annotated {
		if candidate.Extra["source_conflict"] == true {
			t.Fatalf("identical source facts must not be flagged: %#v", candidate.Extra)
		}
	}
}

func TestProviderSourcePriorityIsExplicitAndBounded(t *testing.T) {
	t.Setenv("PLANNING_PROVIDER_PRIORITY", "RollingGo:120,Amap:80,invalid:not-a-number")
	if got := providerSourcePriority("rollinggo"); got != 120 {
		t.Fatalf("expected configured priority, got %d", got)
	}
	if got := providerSourcePriority("AMAP"); got != 80 {
		t.Fatalf("expected case-insensitive priority, got %d", got)
	}
	if got := providerSourcePriority("unknown"); got != 0 {
		t.Fatalf("unknown providers must retain neutral priority, got %d", got)
	}
	t.Setenv("PLANNING_PROVIDER_PRIORITY", "RollingGo:9999,Amap:-10")
	if got := providerSourcePriority("RollingGo"); got != 1000 {
		t.Fatalf("priority must be bounded, got %d", got)
	}
	if got := providerSourcePriority("Amap"); got != 0 {
		t.Fatalf("negative priority must be neutralized, got %d", got)
	}
}

func TestAnnotateCandidatesProvidesEvidenceFreshnessAndAlternatives(t *testing.T) {
	retrieved := time.Now().UTC().Add(-2 * time.Minute)
	items := []contracts.Candidate{
		{ID: "a", Name: "方案 A", Price: 100, Source: contracts.Source{Provider: "mock", Retrieved: retrieved}},
		{ID: "b", Name: "方案 B", Source: contracts.Source{Provider: "fallback", Retrieved: retrieved, Estimated: true}},
	}
	annotated := annotateCandidates(items, "transport")
	if annotated[0].Confidence <= 0 || annotated[0].FreshnessSeconds < 100 || len(annotated[0].Evidence) < 2 {
		t.Fatalf("expected auditable provider metadata, got %+v", annotated[0])
	}
	if len(annotated[0].Alternatives) != 1 || annotated[0].Alternatives[0].ID != "b" {
		t.Fatalf("expected alternative reference, got %+v", annotated[0].Alternatives)
	}
	if annotated[1].Confidence >= annotated[0].Confidence {
		t.Fatalf("estimated candidate should have lower confidence: %+v", annotated)
	}
	if annotated[0].Extra["fact_usable"] != true {
		t.Fatalf("supplier candidate with complete evidence should be fact-usable: %#v", annotated[0].Extra)
	}
	if annotated[1].Extra["fact_usable"] != false {
		t.Fatalf("estimated candidate must not be fact-usable: %#v", annotated[1].Extra)
	}
}

func TestEvidenceContractMarksMissingProviderAsNonFact(t *testing.T) {
	items := []contracts.Candidate{{ID: "unknown-source", Name: "未标注来源", Source: contracts.Source{Retrieved: time.Now().UTC()}}}
	annotated := annotateCandidates(items, "transport")
	if annotated[0].Extra["fact_usable"] != false {
		t.Fatalf("missing provider must not be fact-usable: %#v", annotated[0].Extra)
	}
	issues, ok := annotated[0].Extra["evidence_issues"].([]string)
	if !ok || len(issues) == 0 || issues[0] != "source_provider_missing" {
		t.Fatalf("missing provider reason not exposed: %#v", annotated[0].Extra)
	}
}

func TestAnnotateCandidatesAddsPublicVerificationChannels(t *testing.T) {
	items := []contracts.Candidate{{ID: "transport-1", Name: "乌鲁木齐 到 西安 铁路", Source: contracts.Source{Provider: "fallback", Estimated: true}}}
	annotated := annotateCandidates(items, "transport")
	links, ok := annotated[0].Extra["public_sources"].([]map[string]string)
	if !ok || len(links) < 3 {
		t.Fatalf("expected public verification channels, got %#v", annotated[0].Extra["public_sources"])
	}
	if annotated[0].Extra["verification_url"] == nil || annotated[0].Extra["verification_label"] == nil {
		t.Fatalf("expected default verification link metadata, got %#v", annotated[0].Extra)
	}
	for _, link := range links {
		if strings.Contains(link["url"], "secret") || strings.Contains(link["url"], "token") {
			t.Fatalf("public link must never contain credentials: %#v", link)
		}
	}
}

func TestAnnotatePlanningCandidatesAddsContextAwareLinks(t *testing.T) {
	items := []contracts.Candidate{{ID: "transport-ctx", Name: "铁路", Source: contracts.Source{Provider: "fallback", Estimated: true}}}
	ctx := contracts.PlanningContext{Origin: "乌鲁木齐", Destination: "西安", StartDate: "2026-10-01", EndDate: "2026-10-05", Travelers: 2}
	annotated := annotatePlanningCandidates(items, "transport", ctx)
	links, ok := annotated[0].Extra["public_sources"].([]map[string]string)
	if !ok || len(links) == 0 {
		t.Fatalf("expected context-aware links, got %#v", annotated[0].Extra)
	}
	if !strings.Contains(links[0]["url"], "%E4%B9%8C%E9%B2%81%E6%9C%A8%E9%BD%90") || !strings.Contains(links[0]["url"], "%E8%A5%BF%E5%AE%89") {
		t.Fatalf("origin/destination missing from verification URL: %#v", links[0])
	}
	if annotated[0].Extra["verification_label"] != "按出发地目的地核验" {
		t.Fatalf("unexpected verification label: %#v", annotated[0].Extra["verification_label"])
	}
}

func TestDecisionGateRequiresFreshObservedSupplierFacts(t *testing.T) {
	now := time.Now().UTC()
	base := contracts.Candidate{
		ID: "verified-transport", Name: "供应商交通", Price: 320,
		Source: contracts.Source{Provider: "supplier", Retrieved: now},
		Extra:  map[string]any{"observed_at_provided": true},
	}
	annotated := annotateCandidates([]contracts.Candidate{base}, "transport")
	if annotated[0].Extra["decision_eligible"] != true {
		t.Fatalf("fresh observed supplier candidate should pass gate: %#v", annotated[0].Extra)
	}
	if annotated[0].Extra["official_fallback_available"] != true {
		t.Fatalf("candidate should retain public verification fallback: %#v", annotated[0].Extra)
	}

	cases := []struct {
		name   string
		mutate func(*contracts.Candidate)
		reason string
	}{
		{name: "missing observed timestamp", mutate: func(c *contracts.Candidate) { delete(c.Extra, "observed_at_provided") }, reason: "observed_at_missing"},
		{name: "estimated", mutate: func(c *contracts.Candidate) { c.Source.Estimated = true }, reason: "estimated_source"},
		{name: "stale", mutate: func(c *contracts.Candidate) { c.Source.Retrieved = now.Add(-10 * time.Minute) }, reason: "source_stale"},
		{name: "missing domain field", mutate: func(c *contracts.Candidate) { c.Price = 0 }, reason: "required_fields_missing_group_1"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			candidate := base
			candidate.Extra = map[string]any{"observed_at_provided": true}
			tc.mutate(&candidate)
			got := annotateCandidates([]contracts.Candidate{candidate}, "transport")[0]
			if got.Extra["decision_eligible"] != false {
				t.Fatalf("expected gate rejection: %#v", got.Extra)
			}
			reasons, ok := got.Extra["decision_gate_reasons"].([]string)
			if !ok {
				t.Fatalf("gate reasons have unexpected type: %#v", got.Extra["decision_gate_reasons"])
			}
			found := false
			for _, reason := range reasons {
				if reason == tc.reason {
					found = true
				}
			}
			if !found {
				t.Fatalf("expected reason %q in %#v", tc.reason, reasons)
			}
		})
	}
}

func TestDecisionGateHandlesMalformedPublicSourcesWithoutPanic(t *testing.T) {
	candidate := contracts.Candidate{
		ID: "malformed-links", Name: "公开渠道候选",
		Source: contracts.Source{Provider: "supplier", Retrieved: time.Now().UTC()},
		Price:  100,
		Extra:  map[string]any{"observed_at_provided": true, "freshness_status": "fresh", "public_sources": "not-a-list"},
	}
	// Exercise the gate directly so the malformed value is not first replaced
	// by the public-link enrichment layer.
	annotateDecisionGate(&candidate, "transport")
	if candidate.Extra["official_fallback_available"] != false {
		t.Fatalf("malformed public sources must not be treated as available: %#v", candidate.Extra)
	}
}

func TestRankingEvaluationUsesServerSideBehaviorGate(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 5; i++ {
		if err := db.Create(&models.PlanningEvent{UserID: "rank-user", TripID: "rank-trip", EventType: "official_channel_verified", Payload: `{"module":"transport","candidate_id":"a"}`}).Error; err != nil {
			t.Fatal(err)
		}
	}
	r := gin.New()
	r.POST("/planning/ranking/evaluate", func(c *gin.Context) { c.Set("user_id", "rank-user"); RankingEvaluationHandler(c) })
	body := `{"trip_id":"","destination":"西安","module":"transport","preferences":{},"candidates":[{"id":"a","name":"铁路方案","score":0.8,"tags":["交通"]},{"id":"b","name":"航班方案","score":0.83,"tags":["交通"]}],"behavior":{"enabled":true,"adopted":999}}`
	res := httptest.NewRecorder()
	r.ServeHTTP(res, httptest.NewRequest(http.MethodPost, "/planning/ranking/evaluate", strings.NewReader(body)))
	if res.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", res.Code, res.Body.String())
	}
	var envelope map[string]any
	if err := json.Unmarshal(res.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	data := envelope["data"].(map[string]any)
	signal := data["behavior_signal"].(map[string]any)
	if signal["sample_count"] != float64(5) || signal["enabled"] != true {
		t.Fatalf("unexpected server-side behavior signal: %#v", signal)
	}
	if strings.Contains(res.Body.String(), "999") {
		t.Fatal("client-supplied behavior counts must be ignored")
	}
	var audit models.PlanningEvent
	if err := db.Where("event_type = ?", "planning_decision_audit").First(&audit).Error; err != nil {
		t.Fatal(err)
	}
	if strings.Contains(audit.Payload, "candidate-private") || !strings.Contains(audit.Payload, "ranking-behavior-v1") {
		t.Fatalf("audit must be redacted and versioned: %s", audit.Payload)
	}
}

func TestReplanParetoAuditKeepsFrontierAndExplainsDominatedCandidates(t *testing.T) {
	items := []contracts.Candidate{
		{ID: "frontier", Name: "低价方案", Price: 100, Duration: 120, Source: contracts.Source{Provider: "mock"}},
		{ID: "dominated", Name: "被支配方案", Price: 150, Duration: 180, Source: contracts.Source{Provider: "mock"}},
	}
	audit := replanParetoAudit(items)
	if audit["frontier_count"] != 1 || audit["rejected_count"] != 1 {
		t.Fatalf("unexpected pareto counts: %+v", audit)
	}
	rejected := audit["rejected"].([]gin.H)
	if rejected[0]["reason"] != "dominated_on_known_dimensions" {
		t.Fatalf("missing rejection reason: %+v", rejected[0])
	}
}

func TestValidateReplanCandidateRequiresProvenanceAndSingleTarget(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.TripPlan{}); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.TripPlan{ID: "trip-validate", UserID: "user-validate", DestCity: "杭州"}).Error; err != nil {
		t.Fatal(err)
	}
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/planning/replan/validate", func(c *gin.Context) { c.Set("user_id", "user-validate"); ValidateReplanCandidateHandler(c) })
	req := httptest.NewRequest(http.MethodPost, "/planning/replan/validate", strings.NewReader(`{"trip_id":"trip-validate","existing_route":[{"name":"西湖"}],"affected_nodes":["西湖"],"candidate":{"name":"替代景点","source":{"provider":"mock"}}}`))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", res.Code, res.Body.String())
	}
	var envelope map[string]any
	if err := json.Unmarshal(res.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	if envelope["data"].(map[string]any)["hard_satisfied"] != true {
		t.Fatalf("expected valid candidate: %s", res.Body.String())
	}
}

func TestProviderFetchPassesBearerToken(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer secret-token" {
			http.Error(w, "missing token", http.StatusUnauthorized)
			return
		}
		_, _ = w.Write([]byte(`{"options":[{"id":"x","name":"供应商方案","price":"1,200"}]}`))
	}))
	defer server.Close()
	oldURL, oldToken := os.Getenv("DINING_PROVIDER_URL"), os.Getenv("DINING_PROVIDER_TOKEN")
	t.Cleanup(func() { _ = os.Setenv("DINING_PROVIDER_URL", oldURL); _ = os.Setenv("DINING_PROVIDER_TOKEN", oldToken) })
	_ = os.Setenv("DINING_PROVIDER_URL", server.URL)
	_ = os.Setenv("DINING_PROVIDER_TOKEN", "secret-token")
	options, _, ok := fetchPlanningProvider(context.Background(), "DINING", contracts.PlanningContext{})
	if !ok || len(options) != 1 || options[0].Price != 1200 {
		t.Fatalf("expected authenticated provider option, got ok=%v options=%+v", ok, options)
	}
}

func TestVendorAliasConfigUsesURLWithoutExposingSecret(t *testing.T) {
	oldURL, oldKey := os.Getenv("ROLLINGGO_API_URL"), os.Getenv("ROLLINGGO_API_KEY")
	t.Cleanup(func() {
		_ = os.Setenv("ROLLINGGO_API_URL", oldURL)
		_ = os.Setenv("ROLLINGGO_API_KEY", oldKey)
	})
	_ = os.Setenv("ROLLINGGO_API_URL", "https://supplier.invalid/plan")
	_ = os.Setenv("ROLLINGGO_API_KEY", "secret-value")
	base, token, label := resolveProviderConfig("LODGING")
	if base != "https://supplier.invalid/plan" || token != "secret-value" || label != "RollingGo" {
		t.Fatalf("unexpected alias resolution: base=%q token_set=%v label=%q", base, token != "", label)
	}
}

func TestMCPProviderHandshakeAndTuniuAPIKey(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("apiKey") != "mcp-test-key" {
			http.Error(w, "missing apiKey", http.StatusUnauthorized)
			return
		}
		var request map[string]any
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Fatal(err)
		}
		w.Header().Set("Content-Type", "application/json")
		switch request["method"] {
		case "initialize", "notifications/initialized":
			_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18"}}`))
		case "tools/list":
			_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"search_hotels","description":"hotel search"}]}}`))
		case "tools/call":
			_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":3,"result":{"structuredContent":{"options":[{"id":"hotel-1","name":"联调酒店","price":300,"score":4.5}]}}}`))
		default:
			http.Error(w, "unknown method", http.StatusBadRequest)
		}
	}))
	defer server.Close()
	oldURL, oldKey := os.Getenv("TUNIU_API_URL"), os.Getenv("TUNIU_API_KEY")
	t.Cleanup(func() {
		_ = os.Setenv("TUNIU_API_URL", oldURL)
		_ = os.Setenv("TUNIU_API_KEY", oldKey)
	})
	_ = os.Setenv("TUNIU_API_URL", server.URL+"/mcp")
	_ = os.Setenv("TUNIU_API_KEY", "mcp-test-key")
	options, source, ok := fetchPlanningProvider(context.Background(), "LODGING", contracts.PlanningContext{Destination: "杭州", StartDate: "2026-10-01", EndDate: "2026-10-02"})
	if !ok || len(options) != 1 || options[0].Name != "联调酒店" || source.Provider != "Tuniu" || source.Estimated {
		t.Fatalf("unexpected MCP options: ok=%v source=%+v options=%+v", ok, source, options)
	}
}

func TestTuniuDomainEndpointsResolveSeparately(t *testing.T) {
	keys := []string{"TUNIU_API_URL", "TUNIU_HOTEL_API_URL", "TUNIU_FLIGHT_API_URL", "TUNIU_TRAIN_API_URL", "TUNIU_TICKET_API_URL", "TUNIU_API_KEY"}
	old := map[string]string{}
	for _, key := range keys {
		old[key] = os.Getenv(key)
	}
	t.Cleanup(func() {
		for key, value := range old {
			_ = os.Setenv(key, value)
		}
	})
	for _, key := range keys {
		_ = os.Unsetenv(key)
	}
	_ = os.Setenv("TUNIU_HOTEL_API_URL", "https://openapi.tuniu.cn/mcp/hotel")
	_ = os.Setenv("TUNIU_FLIGHT_API_URL", "https://openapi.tuniu.cn/mcp/flight")
	_ = os.Setenv("TUNIU_TRAIN_API_URL", "https://openapi.tuniu.cn/mcp/train")
	configs := resolveProviderConfigs("TRANSPORT")
	if len(configs) != 2 || !strings.Contains(configs[0].base, "/flight") || !strings.Contains(configs[1].base, "/train") {
		t.Fatalf("expected flight and train MCP endpoints, got %+v", configs)
	}
}

func TestSupplierContextExcludesPrivateIdentityAndSensitiveFields(t *testing.T) {
	context := supplierContext(contracts.PlanningContext{UserID: "user-secret", TripID: "trip-secret", Destination: "杭州", Profile: map[string]any{"health": "private"}, HardConstraints: map[string]any{"documents": "private"}})
	if _, exists := context["user_id"]; exists {
		t.Fatal("supplier context must not contain user_id")
	}
	if _, exists := context["trip_id"]; exists {
		t.Fatal("supplier context must not contain trip_id")
	}
	if _, exists := context["profile"]; exists {
		t.Fatal("supplier context must not contain profile")
	}
}

func TestMCPArgumentsFollowToolInputSchema(t *testing.T) {
	tool := mcpTool{InputSchema: map[string]any{"required": []any{"city", "checkIn", "adults"}, "properties": map[string]any{"city": map[string]any{"type": "string"}, "checkIn": map[string]any{"type": "string"}, "adults": map[string]any{"type": "integer"}, "private_field": map[string]any{"type": "string"}}}}
	args := mcpArgumentsForTool(tool, contracts.PlanningContext{Destination: "杭州", StartDate: "2026-10-01", Travelers: 2, UserID: "private", Profile: map[string]any{"health": "private"}}, "LODGING")
	if args["city"] != "杭州" || args["checkIn"] != "2026-10-01" || args["adults"] != 2 {
		t.Fatalf("expected schema-mapped arguments, got %+v", args)
	}
	if _, exists := args["private_field"]; exists {
		t.Fatal("unknown schema property must not receive guessed data")
	}
	if missing := mcpRequiredProperties(tool, args); len(missing) != 0 {
		t.Fatalf("required schema fields should be mapped, missing=%v", missing)
	}
}

func TestPlanningProviderProbeReportsReachabilityAndAuth(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodHead || r.Header.Get("Authorization") != "Bearer probe-token" {
			http.Error(w, "bad probe", http.StatusBadRequest)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	oldURL, oldToken := os.Getenv("TRANSPORT_PROVIDER_URL"), os.Getenv("TRANSPORT_PROVIDER_TOKEN")
	t.Cleanup(func() {
		_ = os.Setenv("TRANSPORT_PROVIDER_URL", oldURL)
		_ = os.Setenv("TRANSPORT_PROVIDER_TOKEN", oldToken)
	})
	_ = os.Setenv("TRANSPORT_PROVIDER_URL", server.URL)
	_ = os.Setenv("TRANSPORT_PROVIDER_TOKEN", "probe-token")
	result := probePlanningProvider(context.Background(), "TRANSPORT")
	if result["status"] != "reachable" || result["status_code"] != http.StatusNoContent || result["reachable"] != true {
		t.Fatalf("unexpected probe result: %+v", result)
	}
}

func TestPlanningProviderProbeFallsBackWhenHeadUnsupported(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodHead {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	oldURL := os.Getenv("WEATHER_PROVIDER_URL")
	t.Cleanup(func() { _ = os.Setenv("WEATHER_PROVIDER_URL", oldURL) })
	_ = os.Setenv("WEATHER_PROVIDER_URL", server.URL)
	result := probePlanningProvider(context.Background(), "WEATHER")
	if result["status"] != "reachable" || result["status_code"] != http.StatusNoContent {
		t.Fatalf("expected GET fallback to be reachable, got %+v", result)
	}
}

func TestRecordPlanningEventAttributesGenerationModelVersion(t *testing.T) {
	oldDB := database.DB
	t.Cleanup(func() { database.DB = oldDB })
	db, err := gorm.Open(sqlite.Open("file:planning-attribution-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&models.TripPlan{}, &models.PlanningRun{}, &models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}
	database.DB = db
	if err := db.Create(&models.TripPlan{ID: "trip-1", UserID: "user-1", Title: "测试行程"}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.PlanningRun{ID: "plan-1", UserID: "user-1", TripID: "trip-1", ModelVersion: "canary-v7"}).Error; err != nil {
		t.Fatal(err)
	}

	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/planning/events", func(c *gin.Context) { c.Set("user_id", "user-1"); RecordPlanningEventHandler(c) })
	req := httptest.NewRequest(http.MethodPost, "/planning/events", strings.NewReader(`{"trip_id":"trip-1","event_type":"plan_adopted","plan_id":"plan-1","payload":{"model_version":"forged-v0"}}`))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("expected event accepted, got %d %s", res.Code, res.Body.String())
	}
	var event models.PlanningEvent
	if err := db.Where("event_type = ? AND plan_id = ?", "plan_adopted", "plan-1").First(&event).Error; err != nil {
		t.Fatal(err)
	}
	var payload map[string]any
	if err := json.Unmarshal([]byte(event.Payload), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["model_version"] != "canary-v7" {
		t.Fatalf("expected server attribution, got %v", payload["model_version"])
	}
	if payload["action"] != "plan_adopted" || payload["reward"] != 1.0 {
		t.Fatalf("expected normalized counterfactual fields, got %+v", payload)
	}
}

func TestRecordPlanningEventsBatchInsertsAllEvents(t *testing.T) {
	oldDB := database.DB
	t.Cleanup(func() { database.DB = oldDB })
	db, err := gorm.Open(sqlite.Open("file:planning-batch-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&models.TripPlan{}, &models.PlanningRun{}, &models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}
	database.DB = db
	if err := db.Create(&models.TripPlan{ID: "trip-batch", UserID: "user-1", Title: "批量测试"}).Error; err != nil {
		t.Fatal(err)
	}

	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/planning/events/batch", func(c *gin.Context) { c.Set("user_id", "user-1"); RecordPlanningEventsBatchHandler(c) })
	body := `{"events":[
		{"trip_id":"trip-batch","event_type":"plan_generated","payload":{"module":"route"}},
		{"trip_id":"trip-batch","event_type":"node_visited","payload":{"module":"route"}}
	]}`
	req := httptest.NewRequest(http.MethodPost, "/planning/events/batch", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("expected batch accepted, got %d %s", res.Code, res.Body.String())
	}
	var count int64
	if err := db.Model(&models.PlanningEvent{}).Where("trip_id = ?", "trip-batch").Count(&count).Error; err != nil {
		t.Fatal(err)
	}
	if count != 2 {
		t.Fatalf("expected 2 inserted events, got %d", count)
	}
}

func TestRecordPlanningEventsBatchRejectsEmptyBatch(t *testing.T) {
	oldDB := database.DB
	t.Cleanup(func() { database.DB = oldDB })
	db, err := gorm.Open(sqlite.Open("file:planning-batch-empty-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&models.TripPlan{}, &models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}
	database.DB = db

	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Set("user_id", "user-1")
	c.Request = httptest.NewRequest(http.MethodPost, "/planning/events/batch", strings.NewReader(`{"events":[]}`))
	c.Request.Header.Set("Content-Type", "application/json")
	RecordPlanningEventsBatchHandler(c)
	if c.Writer.Status() != http.StatusBadRequest {
		t.Fatalf("expected empty batch rejected, got %d", c.Writer.Status())
	}
}

func TestRecordPlanningEventRejectsInvalidPropensity(t *testing.T) {
	oldDB := database.DB
	t.Cleanup(func() { database.DB = oldDB })
	db, err := gorm.Open(sqlite.Open("file:planning-propensity-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&models.TripPlan{}, &models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}
	database.DB = db
	if err := db.Create(&models.TripPlan{ID: "trip-prop", UserID: "user-prop", Title: "测试行程"}).Error; err != nil {
		t.Fatal(err)
	}
	gin.SetMode(gin.TestMode)
	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	c.Set("user_id", "user-prop")
	c.Request = httptest.NewRequest(http.MethodPost, "/planning/events", strings.NewReader(`{"trip_id":"trip-prop","event_type":"plan_generated","payload":{"propensity":1.5}}`))
	c.Request.Header.Set("Content-Type", "application/json")
	RecordPlanningEventHandler(c)
	if c.Writer.Status() != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "PROPENSITY_INVALID") {
		t.Fatalf("expected invalid propensity rejection, got %d %s", c.Writer.Status(), rec.Body.String())
	}
}

func TestRecordPlanningEventRejectsUnknownFeedbackPlan(t *testing.T) {
	oldDB := database.DB
	t.Cleanup(func() { database.DB = oldDB })
	db, err := gorm.Open(sqlite.Open("file:planning-unknown-plan-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&models.TripPlan{}, &models.PlanningRun{}, &models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}
	database.DB = db
	if err := db.Create(&models.TripPlan{ID: "trip-2", UserID: "user-2", Title: "测试行程"}).Error; err != nil {
		t.Fatal(err)
	}
	gin.SetMode(gin.TestMode)
	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	c.Set("user_id", "user-2")
	c.Request = httptest.NewRequest(http.MethodPost, "/planning/events", strings.NewReader(`{"trip_id":"trip-2","event_type":"plan_rejected","plan_id":"missing"}`))
	c.Request.Header.Set("Content-Type", "application/json")
	RecordPlanningEventHandler(c)
	if c.Writer.Status() != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "PLAN_RUN_NOT_FOUND") {
		t.Fatalf("expected missing plan rejection, got %d %s", c.Writer.Status(), rec.Body.String())
	}
}

func TestExportPlanningDatasetIsDeidentifiedAndVersioned(t *testing.T) {
	oldDB := database.DB
	oldAdmins := os.Getenv("PLANNING_ADMIN_USER_IDS")
	t.Cleanup(func() { database.DB = oldDB; _ = os.Setenv("PLANNING_ADMIN_USER_IDS", oldAdmins) })
	db, err := gorm.Open(sqlite.Open("file:planning-dataset-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&models.PlanningEvent{}, &models.PlanningRun{}); err != nil {
		t.Fatal(err)
	}
	database.DB = db
	_ = os.Setenv("PLANNING_ADMIN_USER_IDS", "admin-1")
	created := time.Now().UTC().Add(-time.Minute)
	if err := db.Create(&models.PlanningRun{ID: "plan-export", UserID: "user-export", TripID: "trip-export", ModelVersion: "joint-v2"}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.PlanningEvent{ID: 7, UserID: "user-export", TripID: "trip-export", PlanID: "plan-export", EventType: "plan_adopted", Payload: `{"module":"joint","model_version":"joint-v2","objective":["time"],"private":"should-not-export"}`, CreatedAt: created}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.PlanningEvent{ID: 8, UserID: "user-export", TripID: "trip-export", EventType: "provider_quality", Payload: `{"kind":"transport","provider":"sandbox","status":"ok","candidate_count":2,"decision_eligible_count":1,"source_conflict_count":1,"enum_invalid_count":1,"raw_response":"must-not-export"}`, CreatedAt: created.Add(time.Second)}).Error; err != nil {
		t.Fatal(err)
	}
	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	c.Set("user_id", "admin-1")
	c.Request = httptest.NewRequest(http.MethodGet, "/planning/dataset/export?window_days=30", nil)
	ExportPlanningDatasetHandler(c)
	if c.Writer.Status() != http.StatusOK {
		t.Fatalf("expected export success, got %d %s", c.Writer.Status(), rec.Body.String())
	}
	var envelope map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	data := envelope["data"].(map[string]any)
	if data["dataset_version"] != "planning-events-v4" {
		t.Fatalf("unexpected dataset version: %v", data["dataset_version"])
	}
	sample := data["samples"].([]any)[0].(map[string]any)
	if sample["model_version"] != "joint-v2" || sample["label"] != 1.0 {
		t.Fatalf("unexpected version/label: %+v", sample)
	}
	if _, exists := sample["plan_id"]; exists {
		t.Fatal("raw plan_id must not be exported")
	}
	payload := sample["payload"].(map[string]any)
	if _, exists := payload["private"]; exists {
		t.Fatal("non-whitelisted payload field must not be exported")
	}
	providerSample := data["samples"].([]any)[1].(map[string]any)
	providerPayload := providerSample["payload"].(map[string]any)
	if providerPayload["provider"] != "sandbox" || providerPayload["enum_invalid_count"] != float64(1) {
		t.Fatalf("provider quality metrics must be exported: %#v", providerPayload)
	}
	if _, exists := providerPayload["raw_response"]; exists {
		t.Fatal("raw provider response must not be exported")
	}
	strata := data["strata"].(map[string]any)
	if _, ok := strata["provider_kind"].(map[string]any)["transport"]; !ok {
		t.Fatalf("provider kind stratum missing: %#v", strata)
	}
}

func TestStableUserHashUsesConfiguredSalt(t *testing.T) {
	oldSalt := os.Getenv("PLANNING_DATASET_HASH_SALT")
	t.Cleanup(func() { _ = os.Setenv("PLANNING_DATASET_HASH_SALT", oldSalt) })
	_ = os.Setenv("PLANNING_DATASET_HASH_SALT", "salt-a")
	first := stableUserHash("user-1")
	if len(first) != 64 || first == "user-1" {
		t.Fatalf("expected sha256-length deidentified hash, got %q", first)
	}
	_ = os.Setenv("PLANNING_DATASET_HASH_SALT", "salt-b")
	if second := stableUserHash("user-1"); second == first {
		t.Fatal("changing dataset salt should change the identifier hash")
	}
}

func TestReplanTriggerQualityIsStratified(t *testing.T) {
	events := []models.PlanningEvent{
		{EventType: "replan_triggered", Payload: `{"proposal_id":"p-weather","trigger_types":["weather_change"],"model_version":"route-v3"}`},
		{EventType: "replan_failed", Payload: `{"proposal_id":"p-weather","failure_code":"REPLAN_CANDIDATES_INSUFFICIENT"}`},
		{EventType: "route_replanned", Payload: `{"proposal_id":"p-weather"}`},
		{EventType: "replan_triggered", Payload: `{"proposal_id":"p-traffic","trigger_types":["transport_disruption"]}`},
		{EventType: "replan_proposal_rejected", Payload: `{"proposal_id":"p-traffic"}`},
	}
	result := replanTriggerQuality(events)
	weather := result["weather_change"].(map[string]any)
	if weather["triggered"] != 1 || weather["successful"] != 1 || weather["failed"] != 1 || weather["success_rate"] != 1.0 {
		t.Fatalf("unexpected weather stats: %#v", weather)
	}
	failures := weather["failure_code_distribution"].(map[string]int)
	if failures["REPLAN_CANDIDATES_INSUFFICIENT"] != 1 {
		t.Fatalf("expected stable failure code distribution, got %#v", failures)
	}
	weatherModels := weather["models"].(map[string]any)
	weatherModel := weatherModels["route-v3"].(map[string]any)
	if weatherModel["failed"] != 1 || weatherModel["failure_code_distribution"].(map[string]int)["REPLAN_CANDIDATES_INSUFFICIENT"] != 1 {
		t.Fatalf("expected model-level failure code distribution, got %#v", weatherModel)
	}
	traffic := result["transport_disruption"].(map[string]any)
	if traffic["triggered"] != 1 || traffic["rejected"] != 1 || traffic["successful"] != 0 {
		t.Fatalf("unexpected traffic stats: %#v", traffic)
	}
	models := traffic["models"].(map[string]any)
	if _, ok := models["<nil>"]; ok {
		t.Fatal("unversioned event must not create a fake model version")
	}
}

func TestReplanTriggerQualityCoversAllRiskSignals(t *testing.T) {
	events := []models.PlanningEvent{
		{EventType: "replan_triggered", Payload: `{"proposal_id":"p-weather","trigger_types":["weather_change"]}`},
		{EventType: "replan_triggered", Payload: `{"proposal_id":"p-traffic","trigger_types":["transport_disruption"]}`},
		{EventType: "replan_triggered", Payload: `{"proposal_id":"p-closure","trigger_types":["attraction_closure"]}`},
		{EventType: "replan_triggered", Payload: `{"proposal_id":"p-budget","trigger_types":["budget_change"]}`},
		{EventType: "replan_failed", Payload: `{"proposal_id":"p-weather","failure_code":"REPLAN_CANDIDATES_INSUFFICIENT"}`},
		{EventType: "route_replanned", Payload: `{"proposal_id":"p-traffic"}`},
		{EventType: "replan_proposal_rejected", Payload: `{"proposal_id":"p-closure"}`},
	}
	result := replanTriggerQuality(events)
	for _, trigger := range []string{"weather_change", "transport_disruption", "attraction_closure", "budget_change"} {
		value, ok := result[trigger].(map[string]any)
		if !ok || value["triggered"] != 1 {
			t.Fatalf("expected trigger sample for %s, got %#v", trigger, result[trigger])
		}
	}
	if result["budget_change"].(map[string]any)["status"] != "available" {
		t.Fatal("a triggered but unresolved risk should still be reported as available with explicit zero outcomes")
	}
}
