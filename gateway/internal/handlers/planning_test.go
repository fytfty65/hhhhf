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
	if data["dataset_version"] != "planning-events-v2" {
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
