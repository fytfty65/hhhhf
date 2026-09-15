package api

import (
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

func containsSecret(s, secret string) bool {
	for i := 0; i+len(secret) <= len(s); i++ {
		if s[i:i+len(secret)] == secret {
			return true
		}
	}
	return false
}

func TestRedactSensitiveText(t *testing.T) {
	got := RedactSensitiveText("authorization: Bearer secret-token, user=alice password=hunter2")
	if got == "" || got == "authorization: Bearer secret-token, user=alice password=hunter2" {
		t.Fatalf("expected sensitive text to be redacted: %q", got)
	}
	if containsSecret(got, "secret-token") || containsSecret(got, "hunter2") {
		t.Fatalf("credential leaked: %q", got)
	}
}

// metricsTestRouter mounts /metrics behind the guard for a given request.
func metricsTestRouter() *gin.Engine {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/metrics", MetricsGuardMiddleware(), MetricsHandler)
	return r
}

// TestMetricsGuardAllowsLoopbackWithoutToken documents the development
// ergonomics: with no METRICS_TOKEN configured, an operator on the box can
// still scrape, because the request comes from a loopback address.
func TestMetricsGuardAllowsLoopbackWithoutToken(t *testing.T) {
	old := os.Getenv("METRICS_TOKEN")
	defer os.Setenv("METRICS_TOKEN", old)
	os.Unsetenv("METRICS_TOKEN")

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	req.RemoteAddr = "127.0.0.1:5555"
	res := httptest.NewRecorder()
	metricsTestRouter().ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("loopback scrape should succeed without a token, got %d %s", res.Code, res.Body.String())
	}
}

// TestMetricsGuardRejectsRemoteWithoutToken is the regression for the public
// /metrics endpoint, which exposed a per-path request and latency inventory to
// anyone who could reach the port.
func TestMetricsGuardRejectsRemoteWithoutToken(t *testing.T) {
	old := os.Getenv("METRICS_TOKEN")
	defer os.Setenv("METRICS_TOKEN", old)
	os.Unsetenv("METRICS_TOKEN")

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	req.RemoteAddr = "203.0.113.9:44444"
	res := httptest.NewRecorder()
	metricsTestRouter().ServeHTTP(res, req)

	if res.Code != http.StatusUnauthorized {
		t.Fatalf("remote scrape without a token must be rejected, got %d %s", res.Code, res.Body.String())
	}
	if !strings.Contains(res.Body.String(), "METRICS_UNAUTHORIZED") {
		t.Fatalf("expected a machine-readable code, got %s", res.Body.String())
	}
	// The inventory must not leak in the rejection path.
	if strings.Contains(res.Body.String(), "requests_by_path") {
		t.Fatal("rejected response leaked the metrics inventory")
	}
}

// TestMetricsGuardHonoursToken asserts that when a token is configured it is
// required from every caller, including loopback.
func TestMetricsGuardHonoursToken(t *testing.T) {
	old := os.Getenv("METRICS_TOKEN")
	defer os.Setenv("METRICS_TOKEN", old)
	os.Setenv("METRICS_TOKEN", "metrics-secret-value")

	// Wrong token from loopback must fail: a configured token wins.
	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	req.RemoteAddr = "127.0.0.1:5555"
	req.Header.Set("X-Metrics-Token", "wrong")
	res := httptest.NewRecorder()
	metricsTestRouter().ServeHTTP(res, req)
	if res.Code != http.StatusUnauthorized {
		t.Fatalf("wrong token must be rejected even from loopback, got %d", res.Code)
	}

	// Correct token via the dedicated header.
	req = httptest.NewRequest(http.MethodGet, "/metrics", nil)
	req.RemoteAddr = "203.0.113.9:44444"
	req.Header.Set("X-Metrics-Token", "metrics-secret-value")
	res = httptest.NewRecorder()
	metricsTestRouter().ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("correct header token must be accepted, got %d %s", res.Code, res.Body.String())
	}

	// Correct token via Authorization: Bearer.
	req = httptest.NewRequest(http.MethodGet, "/metrics", nil)
	req.RemoteAddr = "203.0.113.9:44444"
	req.Header.Set("Authorization", "Bearer metrics-secret-value")
	res = httptest.NewRecorder()
	metricsTestRouter().ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("correct bearer token must be accepted, got %d %s", res.Code, res.Body.String())
	}
}

// TestMetricsTokenRequiredInProduction asserts the startup guard that stops a
// production process from exposing /metrics without a token.
func TestMetricsTokenRequiredInProduction(t *testing.T) {
	oldEnv, oldToken := os.Getenv("APP_ENV"), os.Getenv("METRICS_TOKEN")
	defer func() {
		os.Setenv("APP_ENV", oldEnv)
		os.Setenv("METRICS_TOKEN", oldToken)
	}()

	os.Setenv("APP_ENV", "production")
	os.Unsetenv("METRICS_TOKEN")
	if !MetricsTokenRequired() {
		t.Fatal("production without METRICS_TOKEN must be rejected at startup")
	}

	os.Setenv("METRICS_TOKEN", "set")
	if MetricsTokenRequired() {
		t.Fatal("a configured token satisfies the production requirement")
	}

	os.Setenv("APP_ENV", "development")
	os.Unsetenv("METRICS_TOKEN")
	if MetricsTokenRequired() {
		t.Fatal("development must not require a metrics token")
	}
}

// TestIsLoopbackRequest covers the address parsing used by the guard.
func TestIsLoopbackRequest(t *testing.T) {
	cases := map[string]bool{
		"127.0.0.1:1234": true,
		"[::1]:1234":     true,
		"203.0.113.9:80": false,
		"10.1.2.3:80":    false,
	}
	for remote, want := range cases {
		gin.SetMode(gin.TestMode)
		c, _ := gin.CreateTestContext(httptest.NewRecorder())
		c.Request = httptest.NewRequest(http.MethodGet, "/", nil)
		c.Request.RemoteAddr = remote
		if got := isLoopbackRequest(c); got != want {
			t.Fatalf("isLoopbackRequest(%q) = %v, want %v", remote, got, want)
		}
	}
}
