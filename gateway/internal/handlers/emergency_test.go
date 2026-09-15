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

func TestEmergencyEvaluateReturnsConfirmableActions(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.TripPlan{}, &models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.TripPlan{ID: "trip-e", UserID: "user-e", DestCity: "杭州"}).Error; err != nil {
		t.Fatal(err)
	}
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/planning/emergency/evaluate", func(c *gin.Context) { c.Set("user_id", "user-e"); EmergencyEvaluateHandler(c) })
	req := httptest.NewRequest(http.MethodPost, "/planning/emergency/evaluate", strings.NewReader(`{"trip_id":"trip-e","changed_signals":[{"type":"WEATHER_CHANGE","detail":"暴雨"}],"current_route":[{"name":"西湖"},{"name":"灵隐寺"}]}`))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", res.Code, res.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(res.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	data, ok := payload["data"].(map[string]any)
	if !ok {
		t.Fatalf("missing data envelope: %s", res.Body.String())
	}
	actions, ok := data["actions"].([]any)
	if !ok || len(actions) != 1 {
		t.Fatalf("expected one action: %s", res.Body.String())
	}
	if actions[0].(map[string]any)["requires_confirmation"] != true {
		t.Fatalf("action must require confirmation: %s", res.Body.String())
	}
}

func TestEmergencyEvaluateScopesAffectedNodes(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.TripPlan{}, &models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.TripPlan{ID: "trip-scope", UserID: "user-scope", DestCity: "杭州"}).Error; err != nil {
		t.Fatal(err)
	}
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/planning/emergency/evaluate", func(c *gin.Context) { c.Set("user_id", "user-scope"); EmergencyEvaluateHandler(c) })
	req := httptest.NewRequest(http.MethodPost, "/planning/emergency/evaluate", strings.NewReader(`{"trip_id":"trip-scope","changed_signals":[{"type":"CLOSURE","node_name":"西湖"}],"current_route":[{"name":"西湖"},{"name":"灵隐寺"}]}`))
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
	action := data["actions"].([]any)[0].(map[string]any)
	if action["type"] != "replace_closed_node" {
		t.Fatalf("expected closure action: %v", action)
	}
	affected := action["affected_nodes"].([]any)
	if len(affected) != 1 || affected[0] != "西湖" {
		t.Fatalf("unexpected affected nodes: %v", affected)
	}
	preserved := action["preserve_nodes"].([]any)
	if len(preserved) != 1 || preserved[0] != "灵隐寺" {
		t.Fatalf("unexpected preserved nodes: %v", preserved)
	}
}
