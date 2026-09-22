package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"gateway/internal/database"
	"gateway/internal/models"
	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func TestKnowledgeGraphBuildsStructuralEdges(t *testing.T) {
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
	if err := db.Create(&models.TripPlan{ID: "trip-graph", UserID: "user-graph", DestCity: "杭州"}).Error; err != nil {
		t.Fatal(err)
	}
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/planning/knowledge/graph", func(c *gin.Context) { c.Set("user_id", "user-graph"); KnowledgeGraphHandler(c) })
	req := httptest.NewRequest(http.MethodPost, "/planning/knowledge/graph", strings.NewReader(`{"trip_id":"trip-graph","nodes":[{"name":"西湖","day":1},{"name":"灵隐寺","day":1}]}`))
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
	data := envelope["data"].(map[string]any)
	if len(data["nodes"].([]any)) != 2 || len(data["edges"].([]any)) != 1 {
		t.Fatalf("unexpected graph: %s", res.Body.String())
	}
}

func TestKnowledgeGraphRequiresSourcedTimestampedFacts(t *testing.T) {
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
	if err := db.Create(&models.TripPlan{ID: "trip-facts", UserID: "user-facts", DestCity: "苏州"}).Error; err != nil {
		t.Fatal(err)
	}
	r := gin.New()
	r.POST("/planning/knowledge/graph", func(c *gin.Context) { c.Set("user_id", "user-facts"); KnowledgeGraphHandler(c) })
	body := `{"trip_id":"trip-facts","nodes":[{"name":"拙政园","verified_facts":[{"relation":"attraction_theme","target":"园林","source":"amap","confidence":0.9,"observed_at":"2026-09-22T08:00:00Z"},{"relation":"attraction_opening","target":"09:00-17:00","source":"llm","confidence":0.8,"observed_at":"2026-09-22T08:00:00Z"},{"relation":"attraction_season","target":"秋季","source":"weather-feed","confidence":0.7}]}]}`
	req := httptest.NewRequest(http.MethodPost, "/planning/knowledge/graph", strings.NewReader(body))
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
	data := envelope["data"].(map[string]any)
	if len(data["edges"].([]any)) != 1 {
		t.Fatalf("expected one accepted fact edge: %s", res.Body.String())
	}
	if len(data["rejected_facts"].([]any)) != 2 {
		t.Fatalf("expected two rejected facts: %s", res.Body.String())
	}
}

func TestFeatureSnapshotSeparatesBehaviorAndHidesPromptHint(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.UserPreference{}, &models.FeedbackLog{}, &models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.PlanningEvent{UserID: "user-features", EventType: "plan_adopted", Payload: `{"secret":"do-not-return"}`}).Error; err != nil {
		t.Fatal(err)
	}
	r := gin.New()
	r.GET("/planning/features", func(c *gin.Context) { c.Set("user_id", "user-features"); FeatureSnapshotHandler(c) })
	res := httptest.NewRecorder()
	r.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/planning/features", nil))
	if res.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", res.Code, res.Body.String())
	}
	var envelope map[string]any
	if err := json.Unmarshal(res.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	data := envelope["data"].(map[string]any)
	groups := data["groups"].(map[string]any)
	longTerm := groups["long_term"].(map[string]any)
	if _, exists := longTerm["prompt_hint"]; exists {
		t.Fatal("prompt_hint must not be exposed")
	}
	behavior := groups["behavior"].(map[string]any)
	if _, exists := behavior["raw_payload"]; exists {
		t.Fatal("raw behavior payload must not be exposed")
	}
}

func TestProviderRegistryDoesNotExposeCredentials(t *testing.T) {
	t.Setenv("TRANSPORT_PROVIDER_URL", "https://supplier.invalid/api")
	t.Setenv("TRANSPORT_PROVIDER_TOKEN", "secret-token")
	t.Setenv("TRANSPORT_PROVIDER_NAME", "test-provider")
	r := gin.New()
	r.GET("/planning/providers/registry", ProviderRegistryHandler)
	res := httptest.NewRecorder()
	r.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/planning/providers/registry", nil))
	if res.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", res.Code, res.Body.String())
	}
	if strings.Contains(res.Body.String(), "secret-token") {
		t.Fatal("provider credentials must never be returned")
	}
	if !strings.Contains(res.Body.String(), "transport") || !strings.Contains(res.Body.String(), "observed_at_required") {
		t.Fatalf("registry contract missing: %s", res.Body.String())
	}
}

func TestCrowdForecastExplanationIsActionable(t *testing.T) {
	explanation := crowdForecastExplanation(13, true, true)
	if explanation["recommended_window"] == "" || explanation["alternative_required"] != true {
		t.Fatalf("expected actionable crowd explanation: %+v", explanation)
	}
	reasons, ok := explanation["reasons"].([]string)
	if !ok || len(reasons) < 2 {
		t.Fatalf("expected explainable reasons: %+v", explanation)
	}
}
