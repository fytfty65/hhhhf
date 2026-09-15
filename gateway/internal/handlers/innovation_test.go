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
