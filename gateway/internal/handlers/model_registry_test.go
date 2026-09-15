package handlers

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"gateway/internal/models"
)

func TestInvokeModelEndpointUsesVersionHeaderAndParsesJSON(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if got := request.Header.Get("X-OmniRoute-Model-Version"); got != "canary-v1" {
			t.Fatalf("version header = %q", got)
		}
		var payload map[string]any
		if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
			t.Fatalf("decode request: %v", err)
		}
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"plan":{"name":"provider-plan","estimated_cost":123}}`))
	}))
	defer server.Close()

	output, err := invokeModelEndpoint(context.Background(), models.ModelDeployment{Version: "canary-v1", Endpoint: server.URL}, map[string]any{"days": 2})
	if err != nil {
		t.Fatalf("invokeModelEndpoint returned error: %v", err)
	}
	plan, ok := output["plan"].(map[string]any)
	if !ok || plan["name"] != "provider-plan" {
		t.Fatalf("unexpected model output: %#v", output)
	}
}

func TestInvokeModelEndpointWithoutEndpointIsExplicitNoop(t *testing.T) {
	output, err := invokeModelEndpoint(context.Background(), models.ModelDeployment{Version: "shadow-v1"}, map[string]any{})
	if err != nil || output != nil {
		t.Fatalf("expected nil output without endpoint, got output=%#v err=%v", output, err)
	}
}

func TestDeploymentPassesRuntimeGateFailsLowQualityCanary(t *testing.T) {
	if deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: `{"offline_score":0.79,"drift_score":0.1}`}) {
		t.Fatal("low offline score must fail the runtime gate")
	}
	if deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: `{"offline_score":0.9,"drift_score":0.21}`}) {
		t.Fatal("high drift must fail the runtime gate")
	}
	if !deploymentPassesRuntimeGate(models.ModelDeployment{Metrics: `{"offline_score":0.9,"drift_score":0.2}`}) {
		t.Fatal("deployment at the gate should remain eligible")
	}
}
