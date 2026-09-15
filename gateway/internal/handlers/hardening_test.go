package handlers

import (
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
)

// Regression tests for the phase-1 hardening changes:
//   - Redis outage must not permanently disable the session store
//   - authentication must be rate limited and accounts must lock out
//   - an empty PLANNING_ADMIN_USER_IDS list must grant nobody admin
//   - model endpoints must not be able to target internal addresses

// resetRedisDialState clears the lazily cached Redis dial result so a test can
// control it deterministically.
func resetRedisDialState() {
	redisMu.Lock()
	redisClient, redisInitErr, redisNextRetry = nil, nil, time.Time{}
	redisMu.Unlock()
}

// TestSessionRedisUnconfiguredIsNotAnError covers the local-development path:
// with no REDIS_URL the helper reports "not configured" rather than failing, so
// the in-process store remains usable.
func TestSessionRedisUnconfiguredIsNotAnError(t *testing.T) {
	old := os.Getenv("REDIS_URL")
	defer os.Setenv("REDIS_URL", old)
	os.Unsetenv("REDIS_URL")

	if redisSessionStore() != nil {
		t.Fatal("expected no Redis store when REDIS_URL is unset")
	}
	// The in-process fallback must still mint and resolve tokens.
	token, err := newSessionToken("user-fallback")
	if err != nil {
		t.Fatalf("minting a fallback token must not fail: %v", err)
	}
	if got, ok := LookupSessionUserID(token); !ok || got != "user-fallback" {
		t.Fatalf("fallback session lookup failed: %q %v", got, ok)
	}
}

// TestSessionRedisFailureIsRetriedNotCachedForever is the direct regression for
// the production incident: a sync.Once cached the first dial failure, so every
// later login returned 503 even after Redis recovered. A failure must now be
// retried after the cooldown instead of being latched permanently.
func TestSessionRedisFailureIsRetriedNotCachedForever(t *testing.T) {
	old := os.Getenv("REDIS_URL")
	defer func() {
		os.Setenv("REDIS_URL", old)
		resetRedisDialState()
	}()

	// Port 1 is never a Redis server; the dial fails fast locally.
	os.Setenv("REDIS_URL", "redis://127.0.0.1:1/0")
	resetRedisDialState()

	if _, err := sessionRedis(); err == nil {
		t.Fatal("expected a dial failure against a closed port")
	}

	// A retry deadline must be recorded, and it must be in the future — that is
	// what distinguishes a throttle from the old permanent latch.
	redisMu.Lock()
	deadline := redisNextRetry
	redisMu.Unlock()
	if deadline.IsZero() {
		t.Fatal("expected a retry deadline after a dial failure")
	}
	if !deadline.After(time.Now()) {
		t.Fatal("retry deadline must be in the future so the dial is throttled")
	}

	// Verify the throttle short-circuits without re-dialling...
	start := time.Now()
	if _, err := sessionRedis(); err == nil {
		t.Fatal("expected the throttled call to still report failure")
	}
	if elapsed := time.Since(start); elapsed > 500*time.Millisecond {
		t.Fatalf("throttled call should not re-dial, took %v", elapsed)
	}

	// ...and that once the cooldown elapses the code dials again rather than
	// returning a permanently cached error.
	resetRedisDialState()
	os.Setenv("REDIS_URL", "redis://127.0.0.1:1/0")
	if _, err := sessionRedis(); err == nil {
		t.Fatal("expected the fresh dial attempt to fail again")
	}
}

// TestAuthFailureLockoutAndClear verifies the per-account lockout.
func TestAuthFailureLockoutAndClear(t *testing.T) {
	const user = "lockout-target"
	clearAuthFailures(user)
	t.Cleanup(func() { clearAuthFailures(user) })

	if locked, _ := authAccountLocked(user); locked {
		t.Fatal("a fresh account must not be locked")
	}

	for i := 0; i < authFailureThreshold-1; i++ {
		recordAuthFailure(user)
		if locked, _ := authAccountLocked(user); locked {
			t.Fatalf("account locked too early after %d failures", i+1)
		}
	}

	recordAuthFailure(user)
	locked, remaining := authAccountLocked(user)
	if !locked {
		t.Fatalf("account must lock after %d consecutive failures", authFailureThreshold)
	}
	if remaining <= 0 {
		t.Fatalf("expected a positive lockout duration, got %v", remaining)
	}

	clearAuthFailures(user)
	if locked, _ := authAccountLocked(user); locked {
		t.Fatal("a successful login must clear the failure counter")
	}
}

// TestAuthFailureDecayForgetsStaleFailures ensures an occasional typo spread
// over time never accumulates into a lockout.
func TestAuthFailureDecayForgetsStaleFailures(t *testing.T) {
	const user = "decay-target"
	clearAuthFailures(user)
	t.Cleanup(func() { clearAuthFailures(user) })

	authFailureMu.Lock()
	authFailureCount[user] = authFailureWindow{
		count:       authFailureThreshold,
		lastAttempt: time.Now().Add(-authFailureDecay * 2),
	}
	authFailureMu.Unlock()

	if locked, _ := authAccountLocked(user); locked {
		t.Fatal("failures older than the decay window must not lock the account")
	}
}

// TestAuthRateLimitMiddlewareRejectsBurst asserts the unauthenticated login
// endpoint is actually bounded. The limiter is keyed by client IP, so a burst
// from one address must eventually be answered with 429.
func TestAuthRateLimitMiddlewareRejectsBurst(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/login", AuthLoginRateLimitMiddleware(), func(c *gin.Context) { c.Status(http.StatusOK) })

	seen429 := false
	for i := 0; i < 40; i++ {
		req := httptest.NewRequest(http.MethodPost, "/login", strings.NewReader(`{}`))
		req.RemoteAddr = "203.0.113.77:12345"
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		if res.Code == http.StatusTooManyRequests {
			seen429 = true
			if res.Header().Get("Retry-After") == "" {
				t.Fatal("a 429 must carry Retry-After")
			}
			if !strings.Contains(res.Body.String(), "AUTH_RATE_LIMITED") {
				t.Fatalf("expected a machine-readable code, got %s", res.Body.String())
			}
			break
		}
	}
	if !seen429 {
		t.Fatal("login burst was never rate limited")
	}
}

// TestIsPlanningAdminEmptyAllowlistGrantsNobody is the regression for the
// fail-open admin check: strings.Split("", ",") yields [""], which compared
// equal to an empty principal.
func TestIsPlanningAdminEmptyAllowlistGrantsNobody(t *testing.T) {
	old := os.Getenv("PLANNING_ADMIN_USER_IDS")
	defer os.Setenv("PLANNING_ADMIN_USER_IDS", old)

	os.Setenv("PLANNING_ADMIN_USER_IDS", "")
	for _, candidate := range []string{"", "  ", "admin", "user-1"} {
		if isPlanningAdmin(candidate) {
			t.Fatalf("empty allowlist must grant nobody admin, but %q was granted", candidate)
		}
	}

	os.Setenv("PLANNING_ADMIN_USER_IDS", " , ,")
	if isPlanningAdmin("") || isPlanningAdmin("admin") {
		t.Fatal("a whitespace-only allowlist must grant nobody admin")
	}

	os.Setenv("PLANNING_ADMIN_USER_IDS", "admin-1, admin-2")
	if !isPlanningAdmin("admin-1") || !isPlanningAdmin("admin-2") {
		t.Fatal("configured admins must still be recognised")
	}
	if isPlanningAdmin("admin-3") || isPlanningAdmin("") {
		t.Fatal("unlisted principals must not be admins")
	}
}

// TestValidateModelEndpointHostBlocksInternalTargets covers the SSRF guard.
// Hostnames are resolved, so metadata.google.internal is expected to fail
// lookup rather than resolve locally — either way it must be rejected.
func TestValidateModelEndpointHostBlocksInternalTargets(t *testing.T) {
	blocked := []string{
		"http://127.0.0.1:9000/predict",
		"http://localhost:8000/v1",
		"http://169.254.169.254/latest/meta-data/",
		"http://10.0.0.5/model",
		"http://192.168.1.10/model",
		"http://172.16.4.4/model",
		"http://[::1]:8080/model",
		"http://0.0.0.0/model",
		"http://127.0.0.1.nip.io/model",
	}
	for _, endpoint := range blocked {
		if err := validateModelEndpointHost(endpoint); err == nil {
			t.Fatalf("expected %s to be rejected as an internal target", endpoint)
		}
	}
}

// TestValidateModelEndpointHostAllowsPublicTargets keeps the guard from being
// so strict that a legitimate endpoint cannot be registered.
func TestValidateModelEndpointHostAllowsPublicTargets(t *testing.T) {
	allowed := []string{
		"https://8.8.8.8/model",
		"https://1.1.1.1/v1/predict",
	}
	for _, endpoint := range allowed {
		if err := validateModelEndpointHost(endpoint); err != nil {
			t.Fatalf("expected %s to be allowed, got %v", endpoint, err)
		}
	}
}
