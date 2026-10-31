package main

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
)

// Tests for the trusted-proxy configuration.
//
// Why this exists: gin.New() defaults to trustedProxies = {"0.0.0.0/0", "::/0"}
// with ForwardedByClientIP=true, so c.ClientIP() returns the caller-supplied
// X-Forwarded-For header. Every IP-keyed control in the gateway — the
// login/register limiters, RoomRateLimitMiddleware, and the
// AIRateLimitMiddleware guarding the expensive AI proxy routes — keys on
// ClientIP(), so with the default an attacker rotates one header per attempt and
// never hits a limit. configureTrustedProxies is what closes that hole, and
// these tests pin it so the default cannot silently come back.

// clientIPWithHeader builds a router shaped like main.go's and returns the
// ClientIP() as the handlers would see it for the given peer + header.
func clientIPWithHeader(t *testing.T, peer, header string) string {
	t.Helper()
	gin.SetMode(gin.TestMode)
	r := gin.New()
	if err := configureTrustedProxies(r); err != nil {
		t.Fatalf("configureTrustedProxies: %v", err)
	}
	var seen string
	r.GET("/probe", func(c *gin.Context) {
		seen = c.ClientIP()
		c.Status(http.StatusOK)
	})

	req := httptest.NewRequest(http.MethodGet, "/probe", nil)
	req.RemoteAddr = peer
	if header != "" {
		req.Header.Set("X-Forwarded-For", header)
	}
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("probe route returned %d", res.Code)
	}
	return seen
}

// TestSpoofedForwardedHeaderIsIgnoredByDefault is the core regression: with no
// TRUSTED_PROXIES configured, a caller must not be able to choose its own client
// IP. Before the fix this returned "1.2.3.4" and made rate limiting a no-op.
func TestSpoofedForwardedHeaderIsIgnoredByDefault(t *testing.T) {
	t.Setenv("TRUSTED_PROXIES", "")
	got := clientIPWithHeader(t, "203.0.113.9:5555", "1.2.3.4")
	if got != "203.0.113.9" {
		t.Fatalf("X-Forwarded-For must be ignored by default: want the peer 203.0.113.9, got %q", got)
	}
}

// TestSpoofedForwardedHeaderIsIgnoredForUntrustedPeer covers the subtler case:
// even when a proxy tier IS declared, a peer outside it must not be believed.
func TestSpoofedForwardedHeaderIsIgnoredForUntrustedPeer(t *testing.T) {
	t.Setenv("TRUSTED_PROXIES", "10.0.0.0/8")
	got := clientIPWithHeader(t, "203.0.113.9:5555", "1.2.3.4")
	if got != "203.0.113.9" {
		t.Fatalf("an untrusted peer must not set the client IP: want 203.0.113.9, got %q", got)
	}
}

// TestDeclaredProxyPeerIsBelieved documents the opt-in path working as intended:
// when the operator declares the real proxy tier, header trust is restored so
// per-client limits still work behind a load balancer.
func TestDeclaredProxyPeerIsBelieved(t *testing.T) {
	t.Setenv("TRUSTED_PROXIES", "10.0.0.0/8")
	got := clientIPWithHeader(t, "10.1.2.3:5555", "198.51.100.7")
	if got != "198.51.100.7" {
		t.Fatalf("a declared proxy peer must be believed: want 198.51.100.7, got %q", got)
	}
}

// TestMalformedTrustedProxiesIsAnError pins fail-loud: a typo must not be
// silently ignored, because the operator believes a proxy tier is configured.
func TestMalformedTrustedProxiesIsAnError(t *testing.T) {
	t.Setenv("TRUSTED_PROXIES", "not-an-ip")
	gin.SetMode(gin.TestMode)
	if err := configureTrustedProxies(gin.New()); err == nil {
		t.Fatal("a malformed TRUSTED_PROXIES value must return an error")
	}
}

// TestTrustedProxiesWhitespaceAndEmptyEntries ensures a trailing comma or spaces
// (easy in a .env file) does not break startup or silently disable the opt-in.
func TestTrustedProxiesWhitespaceAndEmptyEntries(t *testing.T) {
	t.Setenv("TRUSTED_PROXIES", " 10.0.0.0/8 , , 192.168.0.0/16 ")
	gin.SetMode(gin.TestMode)
	if err := configureTrustedProxies(gin.New()); err != nil {
		t.Fatalf("whitespace/comma tolerance broke configuration: %v", err)
	}
	got := clientIPWithHeader(t, "192.168.5.5:1234", "198.51.100.7")
	if got != "198.51.100.7" {
		t.Fatalf("second CIDR in the list must be honoured: got %q", got)
	}
}

// TestEmptyTrustedProxiesValueIsTreatedAsUnset guards the "comma only" case:
// an all-empty value must behave like unset (trust nobody), not error out and
// not fall back to trusting everyone.
func TestEmptyTrustedProxiesValueIsTreatedAsUnset(t *testing.T) {
	t.Setenv("TRUSTED_PROXIES", " , , ")
	gin.SetMode(gin.TestMode)
	if err := configureTrustedProxies(gin.New()); err == nil {
		t.Fatal("a value with no usable entries must be reported as invalid")
	}
}

// TestCORSRejectsUnknownOrigin checks the origin gate that main.go composes. It
// lives here rather than in internal/api because corsMiddleware is part of this
// package's router assembly — the same assembly the real process uses.
func TestCORSRejectsUnknownOrigin(t *testing.T) {
	t.Setenv("ALLOWED_ORIGINS", "http://localhost:3000")
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(corsMiddleware())
	r.GET("/ping", func(c *gin.Context) { c.JSON(http.StatusOK, gin.H{"message": "pong"}) })

	req := httptest.NewRequest(http.MethodGet, "/ping", nil)
	req.Header.Set("Origin", "https://evil.example.com")
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	if res.Code != http.StatusForbidden {
		t.Fatalf("an origin outside ALLOWED_ORIGINS must be refused, got %d", res.Code)
	}

	// A request with no Origin (same-origin navigation, curl, server-to-server)
	// must still work: rejecting it would break non-browser clients.
	res2 := httptest.NewRecorder()
	r.ServeHTTP(res2, httptest.NewRequest(http.MethodGet, "/ping", nil))
	if res2.Code != http.StatusOK {
		t.Fatalf("a request without Origin must pass, got %d", res2.Code)
	}

	// A declared origin is allowed and echoed back so the browser can match it.
	req3 := httptest.NewRequest(http.MethodGet, "/ping", nil)
	req3.Header.Set("Origin", "http://localhost:3000")
	res3 := httptest.NewRecorder()
	r.ServeHTTP(res3, req3)
	if res3.Code != http.StatusOK {
		t.Fatalf("a declared origin must be allowed, got %d", res3.Code)
	}
	if got := res3.Header().Get("Access-Control-Allow-Origin"); got != "http://localhost:3000" {
		t.Fatalf("allowed origin must be echoed, got %q", got)
	}
}

// TestCORSPreflightIsAnsweredWithoutTouchingHandlers pins the OPTIONS path: a
// preflight must short-circuit with 204 rather than reaching the route handler.
func TestCORSPreflightIsAnsweredWithoutTouchingHandlers(t *testing.T) {
	t.Setenv("ALLOWED_ORIGINS", "http://localhost:3000")
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(corsMiddleware())
	reached := false
	r.POST("/api/v1/thing", func(c *gin.Context) { reached = true; c.Status(http.StatusOK) })

	req := httptest.NewRequest(http.MethodOptions, "/api/v1/thing", nil)
	req.Header.Set("Origin", "http://localhost:3000")
	req.Header.Set("Access-Control-Request-Method", "POST")
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)

	if res.Code != http.StatusNoContent {
		t.Fatalf("preflight must return 204, got %d", res.Code)
	}
	if reached {
		t.Fatal("preflight must not reach the handler")
	}
}
