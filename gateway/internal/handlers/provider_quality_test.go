package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
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

func TestProviderQualitySnapshotAggregatesEvidenceWithoutRawPayload(t *testing.T) {
	candidates := []contracts.Candidate{
		{
			ID: "transport-1", Name: "铁路", Price: 320, Duration: 180,
			Source:       contracts.Source{Provider: "sandbox", Retrieved: time.Now().UTC()},
			FieldSources: map[string]string{"price": "sandbox", "duration_minutes": "sandbox"},
			Extra:        map[string]any{"observed_at_provided": true, "availability": "available", "booking_url": "https://official.example/train"},
		},
		{
			ID: "transport-2", Name: "航班",
			Source: contracts.Source{Provider: "sandbox", Retrieved: time.Now().UTC(), Estimated: true},
			Extra: map[string]any{"observed_at_provided": false, "source_conflict": true, "enum_validation": map[string]any{
				"availability": map[string]any{"status": "unrecognized"},
			}},
		},
	}
	snapshot := providerQualitySnapshot("transport", "sandbox", candidates, "ok", 125*time.Millisecond)
	if snapshot["candidate_count"] != 2 || snapshot["source_count"] != 2 || snapshot["observed_timestamp_count"] != 1 {
		t.Fatalf("unexpected evidence counts: %#v", snapshot)
	}
	if snapshot["fact_usable_count"] != 1 || snapshot["estimated_count"] != 1 {
		t.Fatalf("unexpected quality counts: %#v", snapshot)
	}
	if snapshot["source_conflict_count"] != 1 {
		t.Fatalf("source conflict count must be observable: %#v", snapshot)
	}
	if snapshot["enum_validated_count"] != 1 || snapshot["enum_invalid_count"] != 1 {
		t.Fatalf("enum quality counts must be observable: %#v", snapshot)
	}
	if snapshot["decision_eligible_count"] != 1 || snapshot["decision_blocked_count"] != 1 {
		t.Fatalf("unexpected decision gate counts: %#v", snapshot)
	}
	if snapshot["decision_gate_contract"] != "provider-decision-gate-v1" {
		t.Fatalf("missing decision gate contract: %#v", snapshot)
	}
	if strings.Contains(string(mustMarshalQuality(snapshot)), "official.example") {
		t.Fatal("quality snapshot must not persist raw supplier URLs")
	}
}

func mustMarshalQuality(value any) []byte {
	encoded, _ := json.Marshal(value)
	return encoded
}

func TestProviderQualityHandlerAggregatesRecentEvents(t *testing.T) {
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
	for _, payload := range []string{
		`{"kind":"transport","provider":"sandbox","status":"ok","candidate_count":2,"source_count":2,"observed_timestamp_count":1,"fact_usable_count":1,"decision_eligible_count":1,"decision_blocked_count":1,"estimated_count":1,"stale_count":0,"field_names":["price","booking_url"],"field_counts":{"price":1,"booking_url":1}}`,
		`{"kind":"transport","provider":"sandbox","status":"empty","candidate_count":0,"field_names":["price","booking_url"],"field_counts":{"price":0,"booking_url":0},"error_code":"provider_empty_or_unavailable"}`,
	} {
		if err := db.Create(&models.PlanningEvent{EventType: "provider_quality", Payload: payload, CreatedAt: time.Now().UTC()}).Error; err != nil {
			t.Fatal(err)
		}
	}
	r := gin.New()
	r.GET("/planning/providers/quality", ProviderQualityHandler)
	res := httptest.NewRecorder()
	r.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/planning/providers/quality?window_days=7", nil))
	if res.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", res.Code, res.Body.String())
	}
	var envelope map[string]any
	if err := json.Unmarshal(res.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	data, ok := envelope["data"].(map[string]any)
	if !ok {
		t.Fatalf("missing data envelope: %s", res.Body.String())
	}
	providers, ok := data["providers"].([]any)
	if !ok || len(providers) != 1 {
		t.Fatalf("unexpected provider aggregate: %#v", data["providers"])
	}
	item := providers[0].(map[string]any)
	if item["observations"] != float64(2) || item["successful_observations"] != float64(1) || item["empty_observations"] != float64(1) {
		t.Fatalf("unexpected observation aggregate: %#v", item)
	}
	gate := item["decision_gate"].(map[string]any)
	if gate["eligible_count"] != float64(1) || gate["blocked_count"] != float64(1) {
		t.Fatalf("unexpected decision gate aggregate: %#v", gate)
	}
	if strings.Contains(res.Body.String(), "official.example") || strings.Contains(res.Body.String(), "raw_response") {
		t.Fatal("quality endpoint must not expose raw provider data")
	}
}

func TestProviderQualityTrendHandlerAggregatesDailyWithoutIdentity(t *testing.T) {
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
	now := time.Now().UTC()
	for _, event := range []models.PlanningEvent{
		{UserID: "private-user", TripID: "private-trip", EventType: "provider_quality", Payload: `{"kind":"transport","provider":"sandbox","candidate_count":4,"decision_eligible_count":3,"source_conflict_count":1,"enum_invalid_count":1}`, CreatedAt: now},
		{UserID: "private-user", TripID: "private-trip", EventType: "provider_quality", Payload: `{"kind":"transport","provider":"sandbox","candidate_count":2,"decision_eligible_count":2,"source_conflict_count":0}`, CreatedAt: now.Add(-24 * time.Hour)},
	} {
		if err := db.Create(&event).Error; err != nil {
			t.Fatal(err)
		}
	}
	r := gin.New()
	r.GET("/planning/providers/quality/trend", ProviderQualityTrendHandler)
	res := httptest.NewRecorder()
	r.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/planning/providers/quality/trend?window_days=14", nil))
	if res.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", res.Code, res.Body.String())
	}
	var envelope map[string]any
	if err := json.Unmarshal(res.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	data, ok := envelope["data"].(map[string]any)
	if !ok {
		t.Fatalf("missing data envelope: %s", res.Body.String())
	}
	points, ok := data["points"].([]any)
	if !ok || len(points) != 2 {
		t.Fatalf("expected two daily points, got %#v", data["points"])
	}
	if strings.Contains(res.Body.String(), "private-user") || strings.Contains(res.Body.String(), "private-trip") {
		t.Fatal("trend endpoint must not expose user or trip identity")
	}
	point := points[0].(map[string]any)
	if point["eligible_ratio"] == nil || point["source_conflict_ratio"] == nil || point["enum_invalid_ratio"] == nil {
		t.Fatalf("trend point must expose aggregate ratios: %#v", point)
	}
}
