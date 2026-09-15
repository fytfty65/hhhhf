package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"gateway/internal/database"
	"gateway/internal/models"
	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func TestJourneyStateUsesLatestProposalLifecycle(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.TripPlan{}, &models.PlanningEvent{}, &models.TripExecutionState{}, &models.BudgetPlan{}, &models.ExpenseRecord{}); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.TripPlan{ID: "trip-state", UserID: "user-state", DestCity: "杭州"}).Error; err != nil {
		t.Fatal(err)
	}
	proposal := `{"proposal_id":"risk-1","trip_id":"trip-state","status":"pending_confirmation"}`
	if err := db.Create(&models.PlanningEvent{UserID: "user-state", TripID: "trip-state", EventType: "replan_triggered", Payload: proposal}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.PlanningEvent{UserID: "user-state", TripID: "trip-state", EventType: "replan_proposal_accepted", Payload: `{"proposal_id":"risk-1"}`}).Error; err != nil {
		t.Fatal(err)
	}
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/planning/journey/state", func(c *gin.Context) { c.Set("user_id", "user-state"); JourneyStateHandler(c) })
	res := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/planning/journey/state?trip_id=trip-state", nil)
	r.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", res.Code, res.Body.String())
	}
	var envelope map[string]any
	if err := json.Unmarshal(res.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	risk := envelope["data"].(map[string]any)["risk"].(map[string]any)
	if risk["status"] != "accepted" {
		t.Fatalf("expected accepted lifecycle, got %v", risk["status"])
	}
}
