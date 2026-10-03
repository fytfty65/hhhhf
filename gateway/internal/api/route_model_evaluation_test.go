package api

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

func TestRegisteredModelEvaluationRouteUsesInternalCallbackContract(t *testing.T) {
	oldDB := database.DB
	db, err := gorm.Open(sqlite.Open("file:registered-model-evaluation-route?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	database.DB = db
	defer func() { database.DB = oldDB }()
	if err := db.AutoMigrate(&models.ModelDeployment{}, &models.ModelAuditEvent{}); err != nil {
		t.Fatalf("migrate evaluation tables: %v", err)
	}
	if err := db.Create(&models.ModelDeployment{Name: "planner", Version: "route-v1", Status: "shadow"}).Error; err != nil {
		t.Fatalf("seed deployment: %v", err)
	}
	previousToken := os.Getenv("AI_SERVICE_INTERNAL_TOKEN")
	defer os.Setenv("AI_SERVICE_INTERNAL_TOKEN", previousToken)
	if err := os.Setenv("AI_SERVICE_INTERNAL_TOKEN", "route-test-token"); err != nil {
		t.Fatal(err)
	}
	report := map[string]any{
		"status": "insufficient_evidence", "promotion_ready": false,
		"model_version": "route-v1", "evaluation_samples": 3,
		"validation_samples": 1, "offline_score": 0.5, "drift_score": 0.1,
		"counterfactual_samples": 1, "fairness_utility_variance": 0.2,
	}
	body, err := json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	gin.SetMode(gin.TestMode)
	r := gin.New()
	RegisterRoutes(r)
	request := httptest.NewRequest(http.MethodPost, "/internal/v1/planning/models/1/evaluation", bytes.NewReader(body))
	request.Header.Set("X-Omni-Internal-Token", "route-test-token")
	response := httptest.NewRecorder()
	r.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("registered callback route returned %d: %s", response.Code, response.Body.String())
	}
	var envelope struct {
		Data struct {
			Accepted       bool `json:"accepted"`
			PromotionReady bool `json:"promotion_ready"`
		} `json:"data"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
		t.Fatalf("decode registered callback response: %v", err)
	}
	if !envelope.Data.Accepted || envelope.Data.PromotionReady {
		t.Fatalf("unexpected callback envelope: %#v", envelope.Data)
	}
}
