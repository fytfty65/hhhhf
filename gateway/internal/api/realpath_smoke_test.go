package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"gateway/internal/database"

	"github.com/gin-gonic/gin"
)

// Real-path smoke test.
//
// Why this exists: every other gateway test exercises a handler or a helper in
// isolation, and the frontend e2e suite mocks the backend entirely, so nothing
// verified that routing + middleware + database + session issuance actually work
// together. That is where the expensive bugs live — the project's own handoff
// notes (SPLIT_PLAN.md) record a photo-ownership defect that was only ever
// reproduced with a real HTTP call against a running gateway, never by a test.
//
// This test wires the real router (RegisterRoutes) against a real migrated
// SQLite database in a temp dir and drives it with real HTTP requests: register
// -> login -> authenticated profile -> authenticated write -> read it back.
// It needs no network and no Redis (the in-process session store is the
// documented development fallback).

// smokeEnv prepares an isolated database and the minimum env the router needs.
func smokeEnv(t *testing.T) {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("DB_PATH", filepath.Join(dir, "omniroute.db"))
	// Keep sessions in-process: no Redis required, and no shared state with a
	// developer's running instance.
	t.Setenv("REDIS_URL", "")
	t.Setenv("APP_ENV", "development")
	t.Setenv("ALLOWED_ORIGINS", "")
	if err := database.InitDB(); err != nil {
		t.Fatalf("InitDB: %v", err)
	}
	// database.DB is process-global. Close its pool before the temp dir is
	// removed, otherwise Windows refuses to delete the still-open SQLite file and
	// the t.TempDir cleanup fails the test for an unrelated reason. Cleanups run
	// LIFO, so this (registered after TempDir) runs before the directory removal.
	t.Cleanup(func() {
		if database.DB == nil {
			return
		}
		if sqlDB, err := database.DB.DB(); err == nil && sqlDB != nil {
			_ = sqlDB.Close()
		}
	})
	// Reset the in-process metric maps so tests do not accumulate each other's
	// labels (they are process-global too).
	resetPrometheusState()
}

// smokeRouter builds the same shape as cmd/server/main.go.
func smokeRouter() *gin.Engine {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	// Routing through the real logging middleware also exercises the bounded
	// metric labels on live routes (not just synthetic ones).
	r.Use(RequestIDMiddleware(), LoggingMiddleware())
	RegisterRoutes(r)
	return r
}

func doJSON(t *testing.T, r *gin.Engine, method, path, token string, body any) (int, map[string]any) {
	t.Helper()
	var reader *bytes.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatalf("marshal body: %v", err)
		}
		reader = bytes.NewReader(raw)
	} else {
		reader = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, path, reader)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)

	out := map[string]any{}
	if res.Body.Len() > 0 {
		_ = json.Unmarshal(res.Body.Bytes(), &out)
	}
	return res.Code, out
}

func str(m map[string]any, key string) string {
	if v, ok := m[key].(string); ok {
		return v
	}
	return ""
}

// TestRealPathRegisterLoginAndAuthenticatedRead drives the full happy path.
func TestRealPathRegisterLoginAndAuthenticatedRead(t *testing.T) {
	smokeEnv(t)
	r := smokeRouter()

	// 1. Registration creates the account. It deliberately does not mint a
	//    refresh session (the UI asks the user to sign in afterwards).
	code, reg := doJSON(t, r, http.MethodPost, "/api/auth/register", "", map[string]any{
		"username": "smoke_user_1",
		"password": "smoke-password-1",
		"nickname": "冒烟测试",
	})
	if code != http.StatusOK {
		t.Fatalf("register: expected 200, got %d (%v)", code, reg)
	}
	user, _ := reg["user"].(map[string]any)
	regUserID := str(user, "id")
	if regUserID == "" {
		t.Fatalf("register must return user.id, got %v", reg)
	}

	// 2. Login must issue a working token AND resolve to the same account.
	code, login := doJSON(t, r, http.MethodPost, "/api/auth/login", "", map[string]any{
		"username": "smoke_user_1",
		"password": "smoke-password-1",
	})
	if code != http.StatusOK {
		t.Fatalf("login: expected 200, got %d (%v)", code, login)
	}
	token := str(login, "token")
	if token == "" {
		t.Fatalf("login must return a token, got %v", login)
	}
	loginUser, _ := login["user"].(map[string]any)
	if got := str(loginUser, "id"); got != regUserID {
		// This is exactly the class of defect the project hit with photos: the
		// token is accepted but resolves to a different (or empty) identity.
		t.Fatalf("login resolved to a different user: register=%q login=%q", regUserID, got)
	}

	// 3. The profile endpoint must report the SAME identity the token was issued
	//    for. A mismatch here means session->user resolution is broken, which is
	//    invisible to every isolated handler test.
	code, prof := doJSON(t, r, http.MethodGet, "/api/user/profile", token, nil)
	if code != http.StatusOK {
		t.Fatalf("profile: expected 200, got %d (%v)", code, prof)
	}
	profUser, _ := prof["user"].(map[string]any)
	if got := str(profUser, "id"); got != regUserID {
		t.Fatalf("profile identity mismatch: registered=%q got=%q (session resolution bug)", regUserID, got)
	}

	// 4. An authenticated write must persist and be readable back. This is the
	//    round trip no unit test covers. The handler requires `user_id` in the
	//    body; AuthMiddleware additionally rejects a body identity that differs
	//    from the session identity (see the tampering case below), so the happy
	//    path must send the caller's own id.
	code, saved := doJSON(t, r, http.MethodPost, "/api/user/trip", token, map[string]any{
		"user_id":   regUserID,
		"title":     "喀什 3 日游",
		"dest_city": "喀什",
		"content":   `{"nodes":[]}`,
		"days":      3,
	})
	if code != http.StatusOK && code != http.StatusCreated {
		t.Fatalf("save trip: expected 200/201, got %d (%v)", code, saved)
	}

	code, trips := doJSON(t, r, http.MethodGet, "/api/user/trips", token, nil)
	if code != http.StatusOK {
		t.Fatalf("list trips: expected 200, got %d (%v)", code, trips)
	}
	// The payload shape may evolve; assert only that the write is visible to the
	// same account, which is the property that matters.
	raw, _ := json.Marshal(trips)
	if !strings.Contains(string(raw), "喀什") {
		t.Fatalf("the trip written by this user must be readable back; got %s", raw)
	}

	// 5. Identity tampering must be refused: claiming someone else's user_id in
	//    the body while holding this session is exactly the IDOR shape. This is
	//    enforced by AuthMiddleware buffering the body before the handler runs.
	code, mismatch := doJSON(t, r, http.MethodPost, "/api/user/trip", token, map[string]any{
		"user_id":   "00000000-0000-0000-0000-000000000000",
		"title":     "不该被写入的行程",
		"dest_city": "伊宁",
		"content":   `{"nodes":[]}`,
	})
	if code != http.StatusForbidden {
		t.Fatalf("a body user_id that differs from the session must be refused with 403, got %d (%v)", code, mismatch)
	}
	if got := str(mismatch, "code"); got != "USER_MISMATCH" {
		t.Fatalf("expected USER_MISMATCH, got %q (%v)", got, mismatch)
	}
}

// TestRealPathRejectsUnauthenticatedAndTamperedTokens pins the auth boundary on
// real routes: no token, a garbage token, and a token from another session all
// have to be refused.
func TestRealPathRejectsUnauthenticatedAndTamperedTokens(t *testing.T) {
	smokeEnv(t)
	r := smokeRouter()

	for _, path := range []string{"/api/user/profile", "/api/user/trips"} {
		if code, _ := doJSON(t, r, http.MethodGet, path, "", nil); code != http.StatusUnauthorized {
			t.Fatalf("%s without a token: expected 401, got %d", path, code)
		}
		if code, _ := doJSON(t, r, http.MethodGet, path, "not-a-real-token", nil); code != http.StatusUnauthorized {
			t.Fatalf("%s with a garbage token: expected 401, got %d", path, code)
		}
	}

	// A token that is well-formed but never issued must also fail.
	if code, _ := doJSON(t, r, http.MethodGet, "/api/user/profile",
		"11111111-2222-3333-4444-555555555555", nil); code != http.StatusUnauthorized {
		t.Fatalf("unissued token: expected 401, got %d", code)
	}
}

// TestRealPathLogoutInvalidatesTheSession proves logout actually revokes, which
// matters because a stolen token otherwise stays valid for its whole TTL.
func TestRealPathLogoutInvalidatesTheSession(t *testing.T) {
	smokeEnv(t)
	r := smokeRouter()

	code, reg := doJSON(t, r, http.MethodPost, "/api/auth/register", "", map[string]any{
		"username": "smoke_user_2",
		"password": "smoke-password-2",
	})
	if code != http.StatusOK {
		t.Fatalf("register: %d (%v)", code, reg)
	}
	code, login := doJSON(t, r, http.MethodPost, "/api/auth/login", "", map[string]any{
		"username": "smoke_user_2",
		"password": "smoke-password-2",
	})
	if code != http.StatusOK {
		t.Fatalf("login: %d (%v)", code, login)
	}
	token := str(login, "token")
	if token == "" {
		t.Fatalf("login must return a token, got %v", login)
	}
	if code, _ := doJSON(t, r, http.MethodGet, "/api/user/profile", token, nil); code != http.StatusOK {
		t.Fatalf("token must work before logout, got %d", code)
	}
	if code, _ := doJSON(t, r, http.MethodPost, "/api/auth/logout", token, nil); code != http.StatusOK {
		t.Fatalf("logout: expected 200, got %d", code)
	}
	if code, _ := doJSON(t, r, http.MethodGet, "/api/user/profile", token, nil); code != http.StatusUnauthorized {
		t.Fatalf("revoked token must stop working, got %d", code)
	}
}

// TestRealPathRequiresDistinctUsernames protects the account model: if two
// registrations could share a username, login would resolve to whichever row the
// database returned first, which is an account-takeover-shaped bug.
func TestRealPathRequiresDistinctUsernames(t *testing.T) {
	smokeEnv(t)
	r := smokeRouter()

	first := map[string]any{"username": "smoke_user_3", "password": "smoke-password-3"}
	if code, body := doJSON(t, r, http.MethodPost, "/api/auth/register", "", first); code != http.StatusOK {
		t.Fatalf("first register: %d (%v)", code, body)
	}
	code, body := doJSON(t, r, http.MethodPost, "/api/auth/register", "", first)
	if code == http.StatusOK {
		// If this ever fails, the users table has lost its uniqueness guarantee
		// and the second account now shadows the first.
		t.Fatalf("duplicate username registration must be refused, got 200 (%v)", body)
	}
}

// TestRealPathHealthAndReadiness documents the probe contract: liveness must not
// depend on Redis, readiness must report it.
func TestRealPathHealthAndReadiness(t *testing.T) {
	smokeEnv(t)
	r := smokeRouter()

	if code, body := doJSON(t, r, http.MethodGet, "/healthz", "", nil); code != http.StatusOK {
		t.Fatalf("healthz must be 200, got %d (%v)", code, body)
	} else if str(body, "status") != "ok" {
		t.Fatalf("healthz status must be ok, got %v", body)
	}

	code, body := doJSON(t, r, http.MethodGet, "/readyz", "", nil)
	if code != http.StatusOK {
		t.Fatalf("readyz must be 200 in development with Redis optional, got %d (%v)", code, body)
	}
	checks, _ := body["checks"].(map[string]any)
	if str(checks, "database") != "ok" {
		t.Fatalf("readyz must report the database as ok, got %v", body)
	}
	if got := str(checks, "redis"); got != "optional" {
		t.Fatalf("readyz must report redis as optional without REDIS_URL, got %q", got)
	}
}

// TestRealPathMetricsLabelsStayBoundedOnLiveRoutes checks the cardinality guard
// against the real route table rather than a synthetic one: a bunch of distinct
// unmatched paths must still collapse.
func TestRealPathMetricsLabelsStayBoundedOnLiveRoutes(t *testing.T) {
	smokeEnv(t)
	r := smokeRouter()

	for i := 0; i < 300; i++ {
		res := httptest.NewRecorder()
		// Deliberately no auth header: unauthenticated junk must not be able to
		// inflate the metric maps.
		r.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/no-such-route/"+strconv.Itoa(i), nil))
	}
	if code, body := doJSON(t, r, http.MethodGet, "/healthz", "", nil); code != http.StatusOK {
		t.Fatalf("healthz: %d (%v)", code, body)
	}

	metricsMu.Lock()
	labels := len(requestsByPath)
	metricsMu.Unlock()
	if labels > maxMetricPaths {
		t.Fatalf("metric path labels grew to %d (cap %d)", labels, maxMetricPaths)
	}
	// The live route table is small and all junk collapses into one bucket.
	if labels > 5 {
		t.Fatalf("expected a handful of labels for a live router, got %d: %v", labels, labelKeys())
	}
}

func labelKeys() []string {
	metricsMu.Lock()
	defer metricsMu.Unlock()
	keys := make([]string, 0, len(requestsByPath))
	for k := range requestsByPath {
		keys = append(keys, k)
	}
	return keys
}
