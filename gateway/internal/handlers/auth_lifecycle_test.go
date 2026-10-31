package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"gateway/internal/database"

	"github.com/gin-gonic/gin"
)

// Credential lifecycle tests.
//
// These exist because the previous single-token model (24h opaque token in
// localStorage, also accepted in a WebSocket URL) had no way to be revoked
// early, no rotation, and no per-device visibility. The properties pinned here
// are the ones a reviewer cannot verify by reading the handler in isolation:
// refresh rotation is single-use, logout-all really kills sibling devices, a
// password change keeps the current device but evicts the others, a reset token
// burns after one use, and a WebSocket ticket cannot be replayed.

func authLifecycleEnv(t *testing.T) *gin.Engine {
	t.Helper()
	gin.SetMode(gin.TestMode)
	dir := t.TempDir()
	t.Setenv("DB_PATH", filepath.Join(dir, "auth.db"))
	t.Setenv("REDIS_URL", "")
	t.Setenv("APP_ENV", "development")
	t.Setenv("ALLOWED_ORIGINS", "")
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

	r := gin.New()
	r.POST("/api/auth/register", RegisterHandler)
	r.POST("/api/auth/login", LoginHandler)
	r.POST("/api/auth/refresh", RefreshHandler)
	r.POST("/api/auth/logout-all", AuthMiddleware(), LogoutAllHandler)
	r.POST("/api/auth/ws-ticket", AuthMiddleware(), IssueWSTicketHandler)
	r.POST("/api/auth/password/change", AuthMiddleware(), ChangePasswordHandler)
	r.POST("/api/auth/password/forgot", ForgotPasswordHandler)
	r.POST("/api/auth/password/reset", ResetPasswordHandler)
	r.GET("/api/user/profile", AuthMiddleware(), GetProfileHandler)
	return r
}

// authDo performs one request and returns the recorder (for Set-Cookie) plus the
// decoded JSON body.
func authDo(t *testing.T, r *gin.Engine, method, path, token string, body any, cookie *http.Cookie) (*httptest.ResponseRecorder, map[string]any) {
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
	if cookie != nil {
		req.AddCookie(cookie)
	}
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	out := map[string]any{}
	if res.Body.Len() > 0 {
		_ = json.Unmarshal(res.Body.Bytes(), &out)
	}
	return res, out
}

func refreshCookieFrom(res *httptest.ResponseRecorder) *http.Cookie {
	for _, cookie := range res.Result().Cookies() {
		if cookie.Name == refreshCookieName && cookie.Value != "" {
			return cookie
		}
	}
	return nil
}

// authStatus is the common "just the status code" form used by the negative
// assertions below.
func authStatus(t *testing.T, r *gin.Engine, method, path, token string, body any, cookie *http.Cookie) int {
	t.Helper()
	res, _ := authDo(t, r, method, path, token, body, cookie)
	return res.Code
}

func registerAndLogin(t *testing.T, r *gin.Engine, username, password string) (string, *http.Cookie) {
	t.Helper()
	res, _ := authDo(t, r, http.MethodPost, "/api/auth/register", "", map[string]any{
		"username": username,
		"password": password,
	}, nil)
	if res.Code != http.StatusOK {
		t.Fatalf("register %s: %d", username, res.Code)
	}
	res, login := authDo(t, r, http.MethodPost, "/api/auth/login", "", map[string]any{
		"username": username,
		"password": password,
	}, nil)
	if res.Code != http.StatusOK {
		t.Fatalf("login %s: %d (%v)", username, res.Code, login)
	}
	token, _ := login["token"].(string)
	if token == "" {
		t.Fatalf("login %s did not return a token: %v", username, login)
	}
	cookie := refreshCookieFrom(res)
	if cookie == nil {
		t.Fatalf("login %s did not install a refresh cookie", username)
	}
	return token, cookie
}

// TestLoginIssuesShortLivedAccessAndHttpOnlyRefreshCookie pins the split: the
// body carries a short-lived access token, the refresh secret lives in an
// HttpOnly cookie scoped to /api/auth.
func TestLoginIssuesShortLivedAccessAndHttpOnlyRefreshCookie(t *testing.T) {
	r := authLifecycleEnv(t)
	res, login := authDo(t, r, http.MethodPost, "/api/auth/register", "", map[string]any{
		"username": "lifecycle_cookie",
		"password": "cookie-password",
	}, nil)
	if res.Code != http.StatusOK {
		t.Fatalf("register: %d (%v)", res.Code, login)
	}
	// Registration must not silently sign the user in.
	if token, _ := login["token"].(string); token != "" {
		t.Fatal("registration must not issue a session")
	}

	res, login = authDo(t, r, http.MethodPost, "/api/auth/login", "", map[string]any{
		"username": "lifecycle_cookie",
		"password": "cookie-password",
	}, nil)
	if res.Code != http.StatusOK {
		t.Fatalf("login: %d (%v)", res.Code, login)
	}
	if got := login["expires_in"]; got != float64(int(accessTokenTTL.Seconds())) {
		t.Fatalf("expires_in must be %v, got %v", int(accessTokenTTL.Seconds()), got)
	}
	cookie := refreshCookieFrom(res)
	if cookie == nil {
		t.Fatal("login must set the refresh cookie")
	}
	if !cookie.HttpOnly {
		t.Fatal("the refresh cookie must be HttpOnly (no JS access)")
	}
	if cookie.Path != refreshCookiePath {
		t.Fatalf("refresh cookie path must be %q, got %q", refreshCookiePath, cookie.Path)
	}
	if cookie.SameSite != http.SameSiteLaxMode {
		t.Fatalf("development refresh cookie must default to SameSite=Lax, got %v", cookie.SameSite)
	}

	token, _ := login["token"].(string)
	if code := authStatus(t, r, http.MethodGet, "/api/user/profile", token, nil, nil); code != http.StatusOK {
		t.Fatalf("the access token must authenticate, got %d", code)
	}
}

// TestRefreshRotatesAndRejectsReuse: the old cookie is dead the moment it is
// exchanged, so a stolen copy is only useful until the legitimate client
// refreshes.
func TestRefreshRotatesAndRejectsReuse(t *testing.T) {
	r := authLifecycleEnv(t)
	_, firstCookie := registerAndLogin(t, r, "lifecycle_rotate", "rotate-password")

	res, refreshed := authDo(t, r, http.MethodPost, "/api/auth/refresh", "", nil, firstCookie)
	if res.Code != http.StatusOK {
		t.Fatalf("refresh: %d (%v)", res.Code, refreshed)
	}
	newToken, _ := refreshed["token"].(string)
	if newToken == "" {
		t.Fatalf("refresh must return a new access token: %v", refreshed)
	}
	secondCookie := refreshCookieFrom(res)
	if secondCookie == nil {
		t.Fatal("refresh must rotate the refresh cookie")
	}
	if secondCookie.Value == firstCookie.Value {
		t.Fatal("refresh must rotate the token, not re-issue the same value")
	}

	// Re-using the consumed cookie must fail.
	if code := authStatus(t, r, http.MethodPost, "/api/auth/refresh", "", nil, firstCookie); code != http.StatusUnauthorized {
		t.Fatalf("a reused refresh token must be refused with 401, got %d", code)
	}
	if code := authStatus(t, r, http.MethodGet, "/api/user/profile", newToken, nil, nil); code != http.StatusOK {
		t.Fatalf("the rotated access token must work, got %d", code)
	}
}

// TestWSTicketIsSingleUse: a URL-visible ticket must not be replayable.
func TestWSTicketIsSingleUse(t *testing.T) {
	r := authLifecycleEnv(t)
	token, _ := registerAndLogin(t, r, "lifecycle_ticket", "ticket-password")

	res, issued := authDo(t, r, http.MethodPost, "/api/auth/ws-ticket", token, nil, nil)
	if res.Code != http.StatusOK {
		t.Fatalf("ws-ticket: %d (%v)", res.Code, issued)
	}
	ticket, _ := issued["ticket"].(string)
	if ticket == "" {
		t.Fatalf("ws-ticket must return a ticket: %v", issued)
	}
	if _, ok := consumeWSTicket(ticket); !ok {
		t.Fatal("the first redemption must succeed")
	}
	if _, ok := consumeWSTicket(ticket); ok {
		t.Fatal("a WebSocket ticket must be single-use")
	}
}

// TestLogoutAllRevokesEveryDevice kills the sibling session too, which is the
// whole point of "退出所有设备".
func TestLogoutAllRevokesEveryDevice(t *testing.T) {
	r := authLifecycleEnv(t)
	tokenA, _ := registerAndLogin(t, r, "lifecycle_logout_all", "logout-all-password")
	_, loginB := authDo(t, r, http.MethodPost, "/api/auth/login", "", map[string]any{
		"username": "lifecycle_logout_all",
		"password": "logout-all-password",
	}, nil)
	tokenB, _ := loginB["token"].(string)
	if tokenB == "" {
		t.Fatalf("second login failed: %v", loginB)
	}

	if code := authStatus(t, r, http.MethodPost, "/api/auth/logout-all", tokenA, nil, nil); code != http.StatusOK {
		t.Fatalf("logout-all: expected 200, got %d", code)
	}
	for name, token := range map[string]string{"current": tokenA, "sibling": tokenB} {
		if code := authStatus(t, r, http.MethodGet, "/api/user/profile", token, nil, nil); code != http.StatusUnauthorized {
			t.Fatalf("%s device token must be revoked, got %d", name, code)
		}
	}
}

// TestChangePasswordRevokesOtherDevices: a stolen session must not survive a
// password change, but the device that changed it stays signed in.
func TestChangePasswordRevokesOtherDevices(t *testing.T) {
	r := authLifecycleEnv(t)
	tokenA, _ := registerAndLogin(t, r, "lifecycle_change_pw", "old-password")
	_, loginB := authDo(t, r, http.MethodPost, "/api/auth/login", "", map[string]any{
		"username": "lifecycle_change_pw",
		"password": "old-password",
	}, nil)
	tokenB, _ := loginB["token"].(string)

	res, changed := authDo(t, r, http.MethodPost, "/api/auth/password/change", tokenA, map[string]any{
		"current_password": "old-password",
		"new_password":     "new-password",
	}, nil)
	if res.Code != http.StatusOK {
		t.Fatalf("change password: %d (%v)", res.Code, changed)
	}
	if code := authStatus(t, r, http.MethodGet, "/api/user/profile", tokenA, nil, nil); code != http.StatusOK {
		t.Fatalf("the current device must stay signed in, got %d", code)
	}
	if code := authStatus(t, r, http.MethodGet, "/api/user/profile", tokenB, nil, nil); code != http.StatusUnauthorized {
		t.Fatalf("sibling device must be revoked, got %d", code)
	}
	if code := authStatus(t, r, http.MethodPost, "/api/auth/login", "", map[string]any{
		"username": "lifecycle_change_pw",
		"password": "old-password",
	}, nil); code != http.StatusUnauthorized {
		t.Fatalf("the old password must stop working, got %d", code)
	}
	if code := authStatus(t, r, http.MethodPost, "/api/auth/login", "", map[string]any{
		"username": "lifecycle_change_pw",
		"password": "new-password",
	}, nil); code != http.StatusOK {
		t.Fatalf("the new password must work, got %d", code)
	}
}

// TestPasswordResetIsSingleUseAndRevokesSessions covers the recovery path end to
// end: request, consume once, and every session is gone afterwards.
func TestPasswordResetIsSingleUseAndRevokesSessions(t *testing.T) {
	r := authLifecycleEnv(t)
	token, _ := registerAndLogin(t, r, "lifecycle_reset", "reset-old-password")

	res, forgot := authDo(t, r, http.MethodPost, "/api/auth/password/forgot", "", map[string]any{
		"username": "lifecycle_reset",
	}, nil)
	if res.Code != http.StatusOK {
		t.Fatalf("forgot: %d (%v)", res.Code, forgot)
	}
	resetToken, _ := forgot["reset_token"].(string)
	if resetToken == "" {
		t.Fatalf("development reset must return a token: %v", forgot)
	}

	res, reset := authDo(t, r, http.MethodPost, "/api/auth/password/reset", "", map[string]any{
		"token":        resetToken,
		"new_password": "reset-new-password",
	}, nil)
	if res.Code != http.StatusOK {
		t.Fatalf("reset: %d (%v)", res.Code, reset)
	}
	if code := authStatus(t, r, http.MethodGet, "/api/user/profile", token, nil, nil); code != http.StatusUnauthorized {
		t.Fatalf("password reset must revoke existing sessions, got %d", code)
	}
	if code := authStatus(t, r, http.MethodPost, "/api/auth/password/reset", "", map[string]any{
		"token":        resetToken,
		"new_password": "another-password",
	}, nil); code != http.StatusBadRequest {
		t.Fatalf("a reset token must be single-use, got %d", code)
	}
	if code := authStatus(t, r, http.MethodPost, "/api/auth/login", "", map[string]any{
		"username": "lifecycle_reset",
		"password": "reset-new-password",
	}, nil); code != http.StatusOK {
		t.Fatalf("the new password must work, got %d", code)
	}
}

// TestRefreshCookieIsSecureInProduction documents the production flag: without
// Secure the cookie travels over plain HTTP.
func TestRefreshCookieIsSecureInProduction(t *testing.T) {
	t.Setenv("APP_ENV", "production")
	t.Setenv("AUTH_COOKIE_SECURE", "")
	gin.SetMode(gin.TestMode)
	res := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(res)
	c.Request = httptest.NewRequest(http.MethodPost, "/api/auth/login", strings.NewReader(""))

	setRefreshCookie(c, "token-value")
	cookie := refreshCookieFrom(res)
	if cookie == nil {
		t.Fatal("expected a Set-Cookie header")
	}
	if !cookie.Secure {
		t.Fatal("production refresh cookie must be Secure")
	}
}
