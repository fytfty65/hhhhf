package handlers

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"gateway/internal/contracts"
)

// newMCPFixtureServer is deliberately local and synthetic. It proves the
// planner's protocol boundary without contacting a real supplier or carrying
// credentials in a test fixture.
func newMCPFixtureServer(t *testing.T, tools []mcpTool, result any, calls *int32) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request struct {
			Method string `json:"method"`
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Fatalf("decode MCP request: %v", err)
		}
		w.Header().Set("Content-Type", "application/json")
		switch request.Method {
		case "initialize":
			_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":1,"result":{}}`))
		case "notifications/initialized":
			w.WriteHeader(http.StatusAccepted)
		case "tools/list":
			payload, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 2, "result": map[string]any{"tools": tools}})
			_, _ = w.Write(payload)
		case "tools/call":
			atomic.AddInt32(calls, 1)
			payload, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 3, "result": map[string]any{"structuredContent": result}})
			_, _ = w.Write(payload)
		default:
			http.Error(w, "unexpected MCP method", http.StatusBadRequest)
		}
	}))
}

func TestFetchMCPProviderDoesNotCallMutationOnlyFixture(t *testing.T) {
	var calls int32
	server := newMCPFixtureServer(t, []mcpTool{{
		Name: "tuniuHotelCreateOrder", Description: "Create an order",
		InputSchema: map[string]any{"properties": map[string]any{"hotelId": map[string]any{}}, "required": []any{"hotelId"}},
	}}, nil, &calls)
	defer server.Close()

	_, ok := fetchMCPProvider(context.Background(), server.URL, "", "Authorization", "Tuniu", "LODGING", contracts.PlanningContext{Destination: "杭州", StartDate: "2026-10-01", EndDate: "2026-10-02", Travelers: 1})
	if ok {
		t.Fatal("mutation-only MCP fixture must fall back instead of returning candidates")
	}
	if got := atomic.LoadInt32(&calls); got != 0 {
		t.Fatalf("mutation tool must never be called by planning discovery, got %d calls", got)
	}
}

func TestFetchMCPProviderCallsReadyReadOnlyFixture(t *testing.T) {
	var calls int32
	tools := []mcpTool{{
		Name: "tuniuHotelSearch", Description: "Search hotels",
		InputSchema: map[string]any{
			"properties": map[string]any{
				"city":        map[string]any{},
				"check-in":    map[string]any{},
				"check-out":   map[string]any{},
				"guest-count": map[string]any{},
			},
			"required": []any{"city", "check-in", "check-out", "guest-count"},
		},
	}}
	server := newMCPFixtureServer(t, tools, map[string]any{"options": []any{map[string]any{
		"id": "fixture-hotel", "name": "Fixture Hotel", "price": 300,
		"observed_at": "2026-09-27T00:00:00Z",
	}}}, &calls)
	defer server.Close()

	options, ok := fetchMCPProvider(context.Background(), server.URL, "", "Authorization", "Tuniu", "LODGING", contracts.PlanningContext{Destination: "杭州", StartDate: "2026-10-01", EndDate: "2026-10-02", Travelers: 2})
	if !ok || len(options) != 1 || options[0].Name != "Fixture Hotel" {
		t.Fatalf("expected one normalized read-only candidate, got ok=%v options=%#v", ok, options)
	}
	if got := atomic.LoadInt32(&calls); got != 1 {
		t.Fatalf("expected exactly one read-only tool call, got %d", got)
	}
}

func TestFetchMCPProviderBlocksRequestOnlySchema(t *testing.T) {
	var calls int32
	server := newMCPFixtureServer(t, []mcpTool{{
		Name: "tuniuHotelCreateOrder", Description: "Create an order",
		InputSchema: map[string]any{
			"properties": map[string]any{"hotelId": map[string]any{}, "contact": map[string]any{}},
			"required":   []any{"hotelId", "contact"},
		},
	}}, nil, &calls)
	defer server.Close()

	_, ok := fetchMCPProvider(context.Background(), server.URL, "", "Authorization", "unknown", "LODGING", contracts.PlanningContext{Destination: "杭州"})
	if ok || atomic.LoadInt32(&calls) != 0 {
		t.Fatalf("request-only schema must be blocked without invoking tools/call: ok=%v calls=%d", ok, atomic.LoadInt32(&calls))
	}
}
