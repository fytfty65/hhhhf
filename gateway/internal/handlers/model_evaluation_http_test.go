package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"gateway/internal/database"
	"gateway/internal/models"
	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func TestIngestModelEvaluationHTTPContractPersistsBoundedMetrics(t *testing.T) {
	oldDB := database.DB
	db, err := gorm.Open(sqlite.Open("file:model-evaluation-http-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	database.DB = db
	defer func() { database.DB = oldDB }()
	if err := db.AutoMigrate(&models.ModelDeployment{}, &models.ModelAuditEvent{}); err != nil {
		t.Fatalf("migrate evaluation tables: %v", err)
	}
	if err := db.Create(&models.ModelDeployment{Name: "planner", Version: "planner-http-v1", Status: "shadow", Traffic: 0}).Error; err != nil {
		t.Fatalf("seed deployment: %v", err)
	}
	previousToken := os.Getenv("AI_SERVICE_INTERNAL_TOKEN")
	defer os.Setenv("AI_SERVICE_INTERNAL_TOKEN", previousToken)
	if err := os.Setenv("AI_SERVICE_INTERNAL_TOKEN", "evaluation-test-token"); err != nil {
		t.Fatal(err)
	}

	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/internal/v1/planning/models/:id/evaluation", IngestModelEvaluationHandler)
	report := map[string]any{
		"status":                                  "ready",
		"promotion_ready":                         true,
		"model_version":                           "planner-http-v1",
		"evaluation_samples":                      40,
		"validation_samples":                      8,
		"offline_score":                           0.91,
		"drift_score":                             0.04,
		"counterfactual_samples":                  35,
		"fairness_min_utility":                    0.78,
		"fairness_utility_variance":               0.03,
		"fairness_segment_required":               true,
		"fairness_segment_status":                 "available",
		"fairness_segment_max_stddev":             0.08,
		"fairness_segment_threshold_exceeded":     0,
		"provider_quality_required":               true,
		"provider_quality_status":                 "available",
		"provider_quality_providers":              2,
		"provider_quality_ready_providers":        2,
		"provider_quality_insufficient_providers": 0,
		"provider_quality_blocked_providers":      0,
	}
	body, err := json.Marshal(report)
	if err != nil {
		t.Fatalf("marshal report: %v", err)
	}

	unauthorized := httptest.NewRecorder()
	unauthorizedRequest := httptest.NewRequest(http.MethodPost, "/internal/v1/planning/models/1/evaluation", bytes.NewReader(body))
	r.ServeHTTP(unauthorized, unauthorizedRequest)
	if unauthorized.Code != http.StatusUnauthorized {
		t.Fatalf("missing internal token should be rejected, got %d", unauthorized.Code)
	}

	response := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/internal/v1/planning/models/1/evaluation", bytes.NewReader(body))
	request.Header.Set("X-Omni-Internal-Token", "evaluation-test-token")
	r.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("authorized evaluation callback failed: %d %s", response.Code, response.Body.String())
	}
	var envelope struct {
		Data map[string]any `json:"data"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
		t.Fatalf("decode callback response: %v", err)
	}
	if envelope.Data["promotion_ready"] != true {
		t.Fatalf("expected promotion-ready response, got %#v", envelope.Data)
	}

	var deployment models.ModelDeployment
	if err := db.First(&deployment, 1).Error; err != nil {
		t.Fatalf("load updated deployment: %v", err)
	}
	var metrics map[string]any
	if err := json.Unmarshal([]byte(deployment.Metrics), &metrics); err != nil {
		t.Fatalf("decode persisted metrics: %v", err)
	}
	if metrics["fairness_segment_ready"] != float64(1) || metrics["fairness_segment_max_stddev"] != 0.08 {
		t.Fatalf("segment fairness metrics were not persisted: %#v", metrics)
	}
	if metrics["provider_quality_ready"] != float64(1) || metrics["provider_quality_providers"] != float64(2) || metrics["provider_quality_ready_providers"] != float64(2) {
		t.Fatalf("provider quality gate metrics were not persisted: %#v", metrics)
	}
	var audit models.ModelAuditEvent
	if err := db.Where("kind = ? AND version = ?", "evaluation", "planner-http-v1").First(&audit).Error; err != nil {
		t.Fatalf("evaluation audit missing: %v", err)
	}
	if audit.SampleCount != 40 || audit.ReasonCount != 0 {
		t.Fatalf("unexpected evaluation audit: %#v", audit)
	}
}
