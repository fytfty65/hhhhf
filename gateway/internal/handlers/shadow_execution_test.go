package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"gateway/internal/contracts"
	"gateway/internal/database"
	"gateway/internal/models"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func TestRunShadowDeploymentsPersistsOnlyBoundedPredictionSummary(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"route":[{"name":"A"},{"name":"B"}],"plan":{"estimated_cost":320,"secret":"private"},"constraints":{"hard_satisfied":true,"violations":[]},"token":"private"}`))
	}))
	defer server.Close()

	oldDB := database.DB
	db, err := gorm.Open(sqlite.Open("file:shadow-execution-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open database: %v", err)
	}
	database.DB = db
	defer func() { database.DB = oldDB }()
	if err := db.AutoMigrate(&models.ModelDeployment{}); err != nil {
		t.Fatalf("migrate model deployment: %v", err)
	}
	if err := db.Create(&models.ModelDeployment{Name: "shadow", Version: "shadow-v1", Status: "shadow", Endpoint: server.URL}).Error; err != nil {
		t.Fatalf("create deployment: %v", err)
	}

	runs := runShadowDeployments(t.Context(), contracts.PlanningContext{UserID: "user", TripID: "trip", Destination: "杭州"}, 2, []map[string]any{{"name": "A"}}, nil)
	if len(runs) != 1 || runs[0]["status"] != "executed" {
		t.Fatalf("unexpected shadow result: %#v", runs)
	}
	summary, ok := runs[0]["prediction_summary"].(map[string]any)
	if !ok || summary["route_node_count"] != 2 || summary["estimated_cost"] != 320.0 {
		t.Fatalf("unexpected bounded summary: %#v", runs)
	}
	if _, exists := summary["secret"]; exists {
		t.Fatal("shadow summary leaked opaque model fields")
	}
	if _, exists := runs[0]["token"]; exists {
		t.Fatal("shadow result leaked raw model output")
	}
	encoded, _ := json.Marshal(runs)
	if string(encoded) == "" {
		t.Fatal("shadow result should be serializable")
	}
}
