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

func TestUpdateAndReadExecutionState(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.TripPlan{}, &models.TripExecutionState{}, &models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.TripPlan{ID: "trip-1", UserID: "user-1", DestCity: "杭州"}).Error; err != nil {
		t.Fatal(err)
	}

	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/planning/execution", func(c *gin.Context) { c.Set("user_id", "user-1"); UpdateExecutionStateHandler(c) })
	r.GET("/planning/execution", func(c *gin.Context) { c.Set("user_id", "user-1"); GetExecutionStateHandler(c) })
	request := httptest.NewRequest(http.MethodPost, "/planning/execution", strings.NewReader(`{"trip_id":"trip-1","node_key":"1:西湖","status":"visited"}`))
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	r.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("expected update 200, got %d: %s", response.Code, response.Body.String())
	}
	var update map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &update); err != nil {
		t.Fatal(err)
	}
	if update["data"] == nil {
		t.Fatalf("expected data envelope: %s", response.Body.String())
	}

	response = httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/planning/execution?trip_id=trip-1", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("expected read 200, got %d: %s", response.Code, response.Body.String())
	}
	var read map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &read); err != nil {
		t.Fatal(err)
	}
	data, ok := read["data"].(map[string]any)
	if !ok || len(data["states"].([]any)) != 1 {
		t.Fatalf("expected one execution state: %s", response.Body.String())
	}
}

func TestUpdateExecutionStateRejectsInvalidStatus(t *testing.T) {
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
	if err := db.Create(&models.TripPlan{ID: "trip-2", UserID: "user-2"}).Error; err != nil {
		t.Fatal(err)
	}
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Set("user_id", "user-2")
	c.Request = httptest.NewRequest(http.MethodPost, "/planning/execution", strings.NewReader(`{"trip_id":"trip-2","node_key":"n","status":"unknown"}`))
	UpdateExecutionStateHandler(c)
	if c.Writer.Status() != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", c.Writer.Status())
	}
}
