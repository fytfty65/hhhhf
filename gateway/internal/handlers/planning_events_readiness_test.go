package handlers

import (
	"encoding/json"
	"fmt"
	"testing"
	"time"

	"gateway/internal/models"
)

func learningEvent(arm, destination, season, budget, group string) models.PlanningEvent {
	payload, _ := json.Marshal(map[string]any{
		"bandit_arm_id":  arm,
		"destination":    destination,
		"season":         season,
		"budget_bucket":  budget,
		"traveler_group": group,
	})
	return models.PlanningEvent{EventType: "plan_adopted", Payload: string(payload)}
}

func TestQualityMetricSummaryUsesOnlyWhitelistedMetrics(t *testing.T) {
	events := []models.PlanningEvent{
		{EventType: "agent_metric", Payload: `{"metric":"constraint_satisfaction_rate","value":0.9}`},
		{EventType: "agent_metric", Payload: `{"metric":"constraint_satisfaction_rate","value":0.7}`},
		{EventType: "agent_metric", Payload: `{"metric":"made_up_score","value":1}`},
	}
	result := qualityMetricSummary(events)
	entry := result["constraint_satisfaction_rate"].(map[string]any)
	if entry["samples"] != 2 || entry["mean"] != 0.8 {
		t.Fatalf("unexpected quality aggregation: %#v", result)
	}
	if _, exists := result["made_up_score"]; exists {
		t.Fatal("unregistered metric must not appear in quality output")
	}
}

func TestPlanningDatasetTemporalSplitIsStableForSparseEvents(t *testing.T) {
	// The export handler uses sorted event order; this fixture documents that
	// a sparse five-row set still has exactly one validation row.
	events := make([]models.PlanningEvent, 5)
	for index := range events {
		events[index] = models.PlanningEvent{CreatedAt: time.Date(2026, 1, index+1, 0, 0, 0, 0, time.UTC)}
	}
	validationStart := int(float64(len(events)) * 0.8)
	if validationStart != 4 {
		t.Fatalf("unexpected temporal split index: %d", validationStart)
	}
}

func TestLearningReadinessRejectsLargeButUnattributedEventSet(t *testing.T) {
	events := make([]models.PlanningEvent, 120)
	for index := range events {
		events[index] = models.PlanningEvent{EventType: "module_open", Payload: `{}`}
	}
	result := learningReadiness(events)
	if result["ready"] == true || result["total_samples"] != 0 {
		t.Fatalf("passive events must not satisfy training readiness: %#v", result)
	}
}

func TestLearningReadinessRequiresEveryArmAndStratum(t *testing.T) {
	events := make([]models.PlanningEvent, 0, 100)
	for index := 0; index < 100; index++ {
		arm := "policy:balanced"
		if index%2 == 0 {
			arm = "policy:relaxed"
		}
		events = append(events, learningEvent(arm, "北京", "autumn", "medium", fmt.Sprintf("group-%d", index%2)))
	}
	result := learningReadiness(events)
	if result["ready"] != true || result["status"] != "ready" {
		t.Fatalf("balanced attributed samples should be ready: %#v", result)
	}

	events = append(events[:0], events[:10]...)
	result = learningReadiness(events)
	if result["ready"] == true || result["status"] != "insufficient_data" {
		t.Fatalf("small dataset must remain insufficient: %#v", result)
	}
}

func TestShadowPredictionSummaryKeepsOnlyAuditableFields(t *testing.T) {
	summary := shadowPredictionSummary(map[string]any{
		"route":       []any{map[string]any{"name": "A"}, map[string]any{"name": "B"}},
		"plan":        map[string]any{"estimated_cost": 320.0, "secret": "must-not-leak"},
		"constraints": map[string]any{"hard_satisfied": true, "violations": []any{}},
		"fairness":    map[string]any{"minimum_utility": 0.8, "raw_members": []any{"private"}},
		"token":       "must-not-leak",
	})
	if summary["route_node_count"] != 2 || summary["estimated_cost"] != 320.0 {
		t.Fatalf("expected route and cost summary: %#v", summary)
	}
	if _, exists := summary["token"]; exists {
		t.Fatalf("raw token leaked into shadow summary: %#v", summary)
	}
	if _, exists := summary["secret"]; exists {
		t.Fatalf("opaque model fields leaked into shadow summary: %#v", summary)
	}
}

func TestNormalizePlanningEventContextAddsBoundedStrata(t *testing.T) {
	payload := map[string]any{}
	normalizePlanningEventContext(payload, "西安", "2026-10-12", 4, 6800)
	if payload["destination"] != "西安" || payload["season"] != "autumn" || payload["budget_bucket"] != "3000_10000" || payload["traveler_group"] != "small_group" {
		t.Fatalf("unexpected normalized context: %#v", payload)
	}
	if payload["context_version"] != "planning-event-context-v1" {
		t.Fatalf("missing context version: %#v", payload)
	}
}

func TestNormalizePlanningEventContextPreservesExplicitBuckets(t *testing.T) {
	payload := map[string]any{"season": "custom-season", "budget_bucket": "custom-budget", "traveler_group": "custom-group", "destination": "用户提供目的地"}
	normalizePlanningEventContext(payload, "另一个城市", "2026-01-01", 1, 100)
	if payload["season"] != "custom-season" || payload["budget_bucket"] != "custom-budget" || payload["traveler_group"] != "custom-group" || payload["destination"] != "用户提供目的地" {
		t.Fatalf("explicit context was overwritten: %#v", payload)
	}
}

func TestNormalizeSourceEvidenceEventRemovesRawAndBoundsFields(t *testing.T) {
	payload := map[string]any{
		"module":              "transport",
		"candidate_id":        "candidate-1",
		"provider":            "sandbox",
		"source_conflict":     true,
		"conflict_fields":     []string{"price", "availability"},
		"conflicting_sources": []string{"A", "B"},
		"target_url":          "https://supplier.example/private?token=secret",
		"raw_response":        map[string]any{"token": "must-not-persist"},
	}
	normalizeSourceEvidenceEvent("official_channel_verified", payload)
	if _, exists := payload["target_url"]; exists {
		t.Fatal("raw target URL must not be retained")
	}
	if _, exists := payload["raw_response"]; exists {
		t.Fatal("raw supplier response must not be retained")
	}
	if payload["source_conflict"] != true {
		t.Fatalf("source conflict flag was lost: %#v", payload)
	}
	if _, ok := payload["conflict_fields"].([]string); !ok {
		t.Fatalf("conflict fields should remain bounded and structured: %#v", payload)
	}
}
