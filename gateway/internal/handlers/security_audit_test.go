package handlers

import (
	"bytes"
	"encoding/json"
	"log"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"gateway/internal/database"

	"github.com/gin-gonic/gin"
)

// Tests for security-event audit logging.
//
// These pin the two properties that make the log useful and safe: the events are
// emitted with the fields an operator needs to investigate, and they never carry
// a credential.

// captureSecurityLog runs fn while collecting everything written to the standard
// logger, and returns the decoded JSON records.
//
// The default logger prefixes each line with its own date/time flags, so the
// parser takes the JSON object that starts at the first "{". Production keeps
// those flags (they are useful for correlation); this only trims them.
func captureSecurityLog(t *testing.T, fn func()) []map[string]any {
	t.Helper()
	var buf bytes.Buffer
	original := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(original)

	fn()

	records := make([]map[string]any, 0, 4)
	for _, line := range strings.Split(strings.TrimSpace(buf.String()), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		start := strings.Index(line, "{")
		if start < 0 {
			t.Fatalf("audit line carries no JSON object: %q", line)
		}
		payload := line[start:]
		record := map[string]any{}
		if err := json.Unmarshal([]byte(payload), &record); err != nil {
			t.Fatalf("audit output must be one JSON object per line, got %q (%v)", payload, err)
		}
		records = append(records, record)
	}
	return records
}

// auditTestContext builds a gin context carrying the request metadata the audit
// helper reads (path, headers, client address).
func auditTestContext(method, path, remoteAddr string) (*gin.Context, *httptest.ResponseRecorder) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(method, path, nil)
	c.Request.RemoteAddr = remoteAddr
	c.Request.Header.Set("X-Request-ID", "rid-test-1")
	c.Request.Header.Set("User-Agent", "audit-test-agent")
	return c, recorder
}

// TestLoginFailureIsAudited pins the brute-force signal: a rejected credential
// attempt must leave a record naming the account and the client.
func TestLoginFailureIsAudited(t *testing.T) {
	c, _ := auditTestContext(http.MethodPost, "/api/auth/login", "203.0.113.5:4444")

	records := captureSecurityLog(t, func() {
		logLoginFailure(c, "VictimUser", "INVALID_CREDENTIALS")
	})

	if len(records) != 1 {
		t.Fatalf("expected exactly one security record, got %d", len(records))
	}
	rec := records[0]
	if rec["event"] != "security" {
		t.Fatalf("record must be tagged as a security event, got %v", rec["event"])
	}
	if rec["type"] != secEventAuthLogin || rec["outcome"] != secOutcomeFailure {
		t.Fatalf("unexpected type/outcome: %v/%v", rec["type"], rec["outcome"])
	}
	// Usernames are normalised so a log query does not have to guess casing.
	if rec["username"] != "victimuser" {
		t.Fatalf("username must be lower-cased, got %v", rec["username"])
	}
	if rec["client_ip"] != "203.0.113.5" {
		t.Fatalf("client_ip must be recorded, got %v", rec["client_ip"])
	}
	if rec["request_id"] != "rid-test-1" {
		t.Fatalf("request_id must correlate with the access log, got %v", rec["request_id"])
	}
	if rec["code"] != "INVALID_CREDENTIALS" {
		t.Fatalf("code must be recorded, got %v", rec["code"])
	}
}

// TestAccountLockoutIsAudited pins the lockout signal, including the retry hint.
func TestAccountLockoutIsAudited(t *testing.T) {
	c, _ := auditTestContext(http.MethodPost, "/api/auth/login", "198.51.100.9:1111")

	records := captureSecurityLog(t, func() {
		logAccountLockout(c, "LockedUser", 900)
	})

	rec := records[0]
	if rec["type"] != secEventAuthLockout || rec["outcome"] != secOutcomeBlocked {
		t.Fatalf("unexpected type/outcome: %v/%v", rec["type"], rec["outcome"])
	}
	if rec["code"] != "ACCOUNT_TEMPORARILY_LOCKED" {
		t.Fatalf("unexpected code: %v", rec["code"])
	}
	if seconds, ok := rec["retry_after_seconds"].(float64); !ok || int(seconds) != 900 {
		t.Fatalf("retry_after_seconds must be recorded as 900, got %v", rec["retry_after_seconds"])
	}
}

// TestRateLimitTripIsAudited pins the enforcement-scope field so an operator can
// tell which limiter fired.
func TestRateLimitTripIsAudited(t *testing.T) {
	c, _ := auditTestContext(http.MethodPost, "/api/auth/login", "203.0.113.7:2222")

	records := captureSecurityLog(t, func() {
		logRateLimited(c, "auth-login", 42)
	})

	rec := records[0]
	if rec["type"] != secEventRateLimit {
		t.Fatalf("unexpected type: %v", rec["type"])
	}
	if rec["namespace"] != "auth-login" {
		t.Fatalf("namespace must be recorded, got %v", rec["namespace"])
	}
	if rec["code"] != "AUTH_RATE_LIMITED" {
		t.Fatalf("unexpected code: %v", rec["code"])
	}
}

// TestIdentityMismatchIsAudited records the claimed identity but not the acting
// one, so the log shows the probe without duplicating session data.
func TestIdentityMismatchIsAudited(t *testing.T) {
	c, _ := auditTestContext(http.MethodPost, "/api/user/profile/update", "203.0.113.8:3333")

	records := captureSecurityLog(t, func() {
		logIdentityMismatch(c, "  some-other-user-id  ")
	})

	rec := records[0]
	if rec["type"] != secEventIdentityCheck || rec["code"] != "USER_MISMATCH" {
		t.Fatalf("unexpected type/code: %v/%v", rec["type"], rec["code"])
	}
	if rec["claimed_user_id"] != "some-other-user-id" {
		t.Fatalf("claimed id must be recorded and trimmed, got %q", rec["claimed_user_id"])
	}
}

// TestAuditRecordsNeverCarryCredentials is the safety property that makes this
// logging safe to enable in production: the log must not become a credential
// store. It asserts against the fields we control, and fails loudly if a future
// edit adds a token/password/body field to the record.
func TestAuditRecordsNeverCarryCredentials(t *testing.T) {
	c, _ := auditTestContext(http.MethodPost, "/api/auth/login", "203.0.113.6:5555")
	// Put a secret in the places a careless implementation might scrape: the
	// query string and the authorization header.
	c.Request.URL.RawQuery = "access_token=super-secret-token&user_id=victim"
	c.Request.Header.Set("Authorization", "Bearer another-secret-token")

	records := captureSecurityLog(t, func() {
		logLoginFailure(c, "someone", "INVALID_CREDENTIALS")
		logIdentityMismatch(c, "victim")
	})

	forbidden := []string{"secret", "password", "authorization", "body", "token"}
	for _, rec := range records {
		for key := range rec {
			for _, word := range forbidden {
				if strings.Contains(strings.ToLower(key), word) {
					t.Fatalf("audit record must not contain a %q field: %v", word, key)
				}
			}
		}
		encoded, _ := json.Marshal(rec)
		if strings.Contains(string(encoded), "super-secret-token") ||
			strings.Contains(string(encoded), "another-secret-token") {
			t.Fatalf("audit record leaked a credential value: %s", encoded)
		}
	}
}

// TestUserAgentIsBounded keeps a caller-controlled header from bloating the log.
func TestUserAgentIsBounded(t *testing.T) {
	c, _ := auditTestContext(http.MethodPost, "/api/auth/login", "203.0.113.9:6666")
	c.Request.Header.Set("User-Agent", strings.Repeat("A", 5000))

	records := captureSecurityLog(t, func() {
		logLoginFailure(c, "someone", "INVALID_CREDENTIALS")
	})

	agent, _ := records[0]["user_agent"].(string)
	if len([]rune(agent)) > 120 {
		t.Fatalf("user_agent must be bounded to 120 runes, got %d", len([]rune(agent)))
	}
}

// TestRealLoginHandlerEmitsAuditRecords is the integration half: it drives the
// actual LoginHandler through a real router and asserts the audit record appears.
// Testing the helpers alone would not catch a call site that was never wired up —
// which is exactly how the logging gap existed in the first place.
func TestRealLoginHandlerEmitsAuditRecords(t *testing.T) {
	gin.SetMode(gin.TestMode)
	dir := t.TempDir()
	t.Setenv("DB_PATH", filepath.Join(dir, "audit.db"))
	t.Setenv("REDIS_URL", "")
	t.Setenv("APP_ENV", "development")
	if err := database.InitDB(); err != nil {
		t.Fatalf("InitDB: %v", err)
	}
	t.Cleanup(func() {
		if database.DB == nil {
			return
		}
		if sqlDB, err := database.DB.DB(); err == nil && sqlDB != nil {
			_ = sqlDB.Close()
		}
	})

	router := gin.New()
	router.POST("/api/auth/login", LoginHandler)

	login := func(username string) {
		body, _ := json.Marshal(map[string]any{"username": username, "password": "definitely-wrong"})
		req := httptest.NewRequest(http.MethodPost, "/api/auth/login", strings.NewReader(string(body)))
		req.Header.Set("Content-Type", "application/json")
		req.RemoteAddr = "203.0.113.50:12345"
		req.Header.Set("X-Request-ID", "rid-login-e2e")
		res := httptest.NewRecorder()
		router.ServeHTTP(res, req)
		// A wrong password is 401 until the account locks, at which point the same
		// request is refused earlier with 429. Both are expected; anything else is
		// a real failure.
		if res.Code != http.StatusUnauthorized && res.Code != http.StatusTooManyRequests {
			t.Fatalf("wrong password must be 401 (or 429 once locked), got %d (%s)", res.Code, res.Body.String())
		}
	}

	// One failed attempt against a real (but nonexistent) account must be audited.
	records := captureSecurityLog(t, func() { login("audit-victim") })
	if len(records) == 0 {
		t.Fatal("a failed login through the real handler produced no audit record")
	}
	rec := records[0]
	if rec["type"] != secEventAuthLogin {
		t.Fatalf("expected an auth_login record, got %v", rec["type"])
	}
	if rec["username"] != "audit-victim" {
		t.Fatalf("the attacked account must be recorded, got %v", rec["username"])
	}
	if rec["request_id"] != "rid-login-e2e" {
		t.Fatalf("request_id must come from the request, got %v", rec["request_id"])
	}

	// Push the account to the lockout threshold and confirm the lockout event
	// fires too — a threshold-crossing record is the one an alert would use.
	records = captureSecurityLog(t, func() {
		for i := 0; i < authFailureThreshold+1; i++ {
			login("audit-lockme")
		}
	})
	seenLockout := false
	for _, r := range records {
		if r["type"] == secEventAuthLockout {
			seenLockout = true
			if r["code"] != "ACCOUNT_TEMPORARILY_LOCKED" {
				t.Fatalf("unexpected lockout code: %v", r["code"])
			}
		}
	}
	if !seenLockout {
		t.Fatalf("crossing the failure threshold produced no lockout record (%d records seen)", len(records))
	}
}
