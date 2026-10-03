package handlers

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"gateway/internal/contracts"
)

func loadMCPFixture(t *testing.T, name string) []mcpTool {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", "mcp", name))
	if err != nil {
		t.Fatal(err)
	}
	var payload struct {
		Tools []mcpTool `json:"tools"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatal(err)
	}
	if len(payload.Tools) == 0 {
		t.Fatalf("fixture %s contains no tools", name)
	}
	return payload.Tools
}

func TestMCPToolSchemaAuditSeparatesContextAndExplicitFields(t *testing.T) {
	search := mcpTool{
		Name: "searchHotels",
		InputSchema: map[string]any{
			"required":   []any{"city", "check-in", "adults"},
			"properties": map[string]any{"city": map[string]any{}, "check-in": map[string]any{}, "adults": map[string]any{}},
		},
	}
	audit := mcpToolSchemaAudit(search, "LODGING")
	if audit["status"] != "ready" {
		t.Fatalf("expected search tool to be ready, got %#v", audit)
	}
	if len(audit["unmapped_required"].([]string)) != 0 {
		t.Fatalf("search tool contains unmapped fields: %#v", audit)
	}
	mapping := audit["field_mapping"].(map[string]any)
	if mapping["check-in"].(map[string]any)["source"] != "planning_context" {
		t.Fatalf("expected check-in to map from planning context: %#v", mapping)
	}

	mutation := mcpTool{
		Name: "tuniuHotelCreateOrder",
		InputSchema: map[string]any{
			"required":   []any{"hotelId", "roomType", "contact", "certificateNo"},
			"properties": map[string]any{"hotelId": map[string]any{}, "roomType": map[string]any{}, "contact": map[string]any{}, "certificateNo": map[string]any{}},
		},
	}
	mutationAudit := mcpToolSchemaAudit(mutation, "LODGING")
	if mutationAudit["status"] != "requires_request_fields" {
		t.Fatalf("expected mutation tool to require explicit request fields, got %#v", mutationAudit)
	}
	if len(mutationAudit["explicit_request_fields"].([]string)) != 4 {
		t.Fatalf("expected four explicit fields, got %#v", mutationAudit)
	}
}

func TestMCPToolSchemaRedactionRemovesCredentialLikeFields(t *testing.T) {
	tool := redactMCPToolSchema(mcpTool{
		Name:        "lookup",
		Description: "uses a session token to query",
		InputSchema: map[string]any{"properties": map[string]any{"city": map[string]any{"type": "string"}, "apiKey": map[string]any{"default": "secret"}, "nested": map[string]any{"session_id": "private"}}},
	})
	if strings.Contains(tool.Description, "token") || strings.Contains(tool.Description, "session") {
		t.Fatal("sensitive description should be replaced")
	}
	properties := tool.InputSchema["properties"].(map[string]any)
	if _, exists := properties["apiKey"]; exists {
		t.Fatal("credential-like schema key leaked")
	}
	nested := properties["nested"].(map[string]any)
	if _, exists := nested["session_id"]; exists {
		t.Fatal("nested session field leaked")
	}
}

func TestMCPArgumentsNormalizeHyphenatedSchemaProperties(t *testing.T) {
	tool := mcpTool{InputSchema: map[string]any{"properties": map[string]any{"check-in": map[string]any{}, "check-out": map[string]any{}, "guest-count": map[string]any{}}}}
	args := mcpArgumentsForTool(tool, contracts.PlanningContext{Destination: "杭州", StartDate: "2026-10-01", EndDate: "2026-10-02", Travelers: 2}, "LODGING")
	if args["check-in"] != "2026-10-01" || args["check-out"] != "2026-10-02" || args["guest-count"] != 2 {
		t.Fatalf("hyphenated schema properties were not mapped: %#v", args)
	}
}

func TestMCPOrderArgumentsMapCommonVendorAliases(t *testing.T) {
	tool := mcpTool{InputSchema: map[string]any{"properties": map[string]any{
		"passenger_name": map[string]any{}, "phone": map[string]any{}, "id_card_number": map[string]any{},
		"checkInDate": map[string]any{}, "check-out-date": map[string]any{},
	}}}
	args := mcpArgumentsForOrder(tool, contracts.PlanningContext{StartDate: "2026-10-01", EndDate: "2026-10-02"}, map[string]any{
		"passengerName": "fixture-passenger", "contact": "13800000000", "certificateNo": "fixture-id",
	})
	if args["passenger_name"] != "fixture-passenger" || args["phone"] != "13800000000" || args["id_card_number"] != "fixture-id" {
		t.Fatalf("common identity aliases were not projected: %#v", args)
	}
	if args["checkInDate"] != "2026-10-01" || args["check-out-date"] != "2026-10-02" {
		t.Fatalf("date aliases were not projected: %#v", args)
	}
}

func TestMCPToolSchemaAuditFlagsUnmappedRequiredFields(t *testing.T) {
	tool := mcpTool{
		Name: "vendorSearch",
		InputSchema: map[string]any{
			"required":   []any{"city", "supplier-specific-filter"},
			"properties": map[string]any{"city": map[string]any{}, "supplier-specific-filter": map[string]any{}},
		},
	}
	audit := mcpToolSchemaAudit(tool, "DESTINATION")
	if audit["status"] != "requires_mapping" {
		t.Fatalf("expected unmapped required field to block tool: %#v", audit)
	}
	if got := audit["unmapped_required"].([]string); len(got) != 1 || got[0] != "supplier-specific-filter" {
		t.Fatalf("unexpected unmapped fields: %#v", got)
	}
	if mapping := audit["field_mapping"].(map[string]any)["supplier-specific-filter"].(map[string]any); mapping["status"] != "unmapped" {
		t.Fatalf("unmapped field status missing: %#v", mapping)
	}
}

func TestMCPToolSchemaAuditBlocksTypeAndEnumMismatches(t *testing.T) {
	tool := mcpTool{
		Name: "strictSearch",
		InputSchema: map[string]any{
			"required": []any{"adults", "placeType"},
			"properties": map[string]any{
				"adults":    map[string]any{"type": "string"},
				"placeType": map[string]any{"type": "string", "enum": []any{"CITY"}},
			},
		},
	}
	audit := mcpToolSchemaAudit(tool, "LODGING")
	if audit["status"] != "requires_mapping" {
		t.Fatalf("expected schema mismatch to be blocked, got %#v", audit)
	}
	if got := audit["type_mismatch_required"].([]string); len(got) != 2 {
		t.Fatalf("expected both mismatches to be reported, got %#v", audit)
	}
	args := mcpArgumentsForTool(tool, contracts.PlanningContext{Destination: "杭州", Travelers: 2}, "LODGING")
	if missing := mcpRequiredProperties(tool, args); len(missing) != 2 {
		t.Fatalf("runtime schema validation should reject mismatched values, args=%#v missing=%v", args, missing)
	}
}

func TestMCPFixtureSelectsReadOnlySearchAndKeepsMutationsOut(t *testing.T) {
	tools := loadMCPFixture(t, "tuniu_tools.json")
	selected := chooseMCPTool("Tuniu", tools, "LODGING")
	if selected.Name != "tuniuHotelSearch" {
		t.Fatalf("discovery selected a mutation or wrong tool: %q", selected.Name)
	}
	if isMCPMutationTool(selected) {
		t.Fatal("discovery tool must be read-only")
	}
	audit := mcpToolSchemaAudit(selected, "LODGING")
	if audit["status"] != "ready" {
		t.Fatalf("fixture search schema should be ready: %#v", audit)
	}
	if chosen := chooseMCPTool("unknown", tools[1:], "LODGING"); chosen.Name != "" {
		t.Fatalf("mutation-only tool list must not be selected for discovery: %q", chosen.Name)
	}
	if chosen := chooseMCPTool("unknown", tools[2:], "LODGING"); chosen.Name != "" {
		t.Fatalf("a single mutation tool must not be selected for discovery: %q", chosen.Name)
	}
}

func TestMCPFixtureOrderToolRequiresExplicitFields(t *testing.T) {
	tools := loadMCPFixture(t, "tuniu_tools.json")
	var order mcpTool
	for _, tool := range tools {
		if tool.Name == "tuniuHotelCreateOrder" {
			order = tool
		}
	}
	if order.Name == "" {
		t.Fatal("order tool missing from fixture")
	}
	audit := mcpToolSchemaAudit(order, "LODGING")
	if audit["status"] != "requires_request_fields" {
		t.Fatalf("order tool must require explicit request fields: %#v", audit)
	}
	if got := audit["explicit_request_fields"].([]string); len(got) != 5 {
		t.Fatalf("unexpected explicit fields: %#v", audit)
	}
	if got := audit["context_mapped_required"].([]string); len(got) != 2 {
		t.Fatalf("expected check-in/check-out to map from planning context: %#v", audit)
	}
}

func TestMCPToolSelectionSummaryMakesMutationBoundaryVisible(t *testing.T) {
	tools := loadMCPFixture(t, "tuniu_tools.json")
	summary := mcpToolSelectionSummary("Tuniu", tools, "LODGING", nil)
	if summary["status"] != "ready" {
		t.Fatalf("expected ready read-only selection, got %#v", summary)
	}
	if summary["read_only_candidate"] != "tuniuHotelSearch" {
		t.Fatalf("unexpected selected search tool: %#v", summary)
	}
	blocked := summary["blocked_mutations"].([]string)
	if len(blocked) != 2 || blocked[0] != "cancelOrder" || blocked[1] != "tuniuHotelCreateOrder" {
		t.Fatalf("unexpected blocked mutation tools: %#v", summary)
	}
}
