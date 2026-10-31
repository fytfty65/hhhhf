package handlers

import (
	"net/http"
	"path/filepath"
	"testing"

	"gateway/internal/database"

	"github.com/gin-gonic/gin"
)

// 移动端认证契约测试。
//
// 这些用例钉住的是原生客户端最容易踩到的性质：令牌对必须通过响应体下发
// （不能依赖 Cookie）、refresh 必须单次使用并轮换、撤销后旧 refresh token
// 与 access token 都必须立即失效、以及登出在 access token 过期后仍然有效。
// 只读 handler 无法验证这些行为，因此用真实 SQLite + 真实路由驱动。

func mobileAuthEnv(t *testing.T) *gin.Engine {
	t.Helper()
	gin.SetMode(gin.TestMode)
	dir := t.TempDir()
	t.Setenv("DB_PATH", filepath.Join(dir, "mobile-auth.db"))
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
	r.POST("/api/auth/mobile/login", MobileLoginHandler)
	r.POST("/api/auth/mobile/refresh", MobileRefreshHandler)
	r.POST("/api/auth/mobile/logout", MobileLogoutHandler)
	r.GET("/api/user/profile", AuthMiddleware(), GetProfileHandler)
	return r
}

func mobileTokens(t *testing.T, body map[string]any) (accessToken, refreshToken string) {
	t.Helper()
	accessToken, _ = body["access_token"].(string)
	refreshToken, _ = body["refresh_token"].(string)
	if accessToken == "" || refreshToken == "" {
		t.Fatalf("响应缺少 access_token/refresh_token: %v", body)
	}
	return accessToken, refreshToken
}

func registerMobileUser(t *testing.T, r *gin.Engine, username, password string) {
	t.Helper()
	res, _ := authDo(t, r, http.MethodPost, "/api/auth/register", "", map[string]any{
		"username": username,
		"password": password,
	}, nil)
	if res.Code != http.StatusOK {
		t.Fatalf("register %s: %d", username, res.Code)
	}
}

func mobileLogin(t *testing.T, r *gin.Engine, username, password string) (accessToken, refreshToken string) {
	t.Helper()
	res, body := authDo(t, r, http.MethodPost, "/api/auth/mobile/login", "", map[string]any{
		"username": username,
		"password": password,
	}, nil)
	if res.Code != http.StatusOK {
		t.Fatalf("mobile login: %d %v", res.Code, body)
	}
	return mobileTokens(t, body)
}

// 移动端登录不得下发浏览器 Cookie：Android 端不应该被动拿到一个 30 天的
// HttpOnly 凭据，令牌必须在响应体里显式返回并由客户端负责安全存储。
func TestMobileLoginReturnsTokensWithoutCookie(t *testing.T) {
	r := mobileAuthEnv(t)
	registerMobileUser(t, r, "mobile-user", "secret123")

	res, body := authDo(t, r, http.MethodPost, "/api/auth/mobile/login", "", map[string]any{
		"username": "mobile-user",
		"password": "secret123",
	}, nil)
	if res.Code != http.StatusOK {
		t.Fatalf("login: %d %v", res.Code, body)
	}
	for _, cookie := range res.Result().Cookies() {
		if cookie.Name == refreshCookieName && cookie.Value != "" {
			t.Fatalf("mobile login 不应下发 refresh cookie")
		}
	}
	if body["expires_in"].(float64) != float64(accessTokenTTL.Seconds()) {
		t.Fatalf("expires_in = %v, want %v", body["expires_in"], int(accessTokenTTL.Seconds()))
	}
	if _, ok := body["user"].(map[string]any); !ok {
		t.Fatalf("响应缺少 user: %v", body)
	}

	accessToken, _ := mobileTokens(t, body)
	if status := authStatus(t, r, http.MethodGet, "/api/user/profile", accessToken, nil, nil); status != http.StatusOK {
		t.Fatalf("access token 应可用于受保护接口, got %d", status)
	}
}

// refresh 必须轮换：旧 token 用一次即失效，新 token 可以继续用。
func TestMobileRefreshRotatesAndInvalidatesOldToken(t *testing.T) {
	r := mobileAuthEnv(t)
	registerMobileUser(t, r, "rotate-user", "secret123")
	accessToken, refreshToken := mobileLogin(t, r, "rotate-user", "secret123")

	res, body := authDo(t, r, http.MethodPost, "/api/auth/mobile/refresh", "", map[string]any{
		"refresh_token": refreshToken,
	}, nil)
	if res.Code != http.StatusOK {
		t.Fatalf("refresh: %d %v", res.Code, body)
	}
	newAccess, newRefresh := mobileTokens(t, body)
	if newRefresh == refreshToken {
		t.Fatalf("refresh token 必须轮换")
	}
	if newAccess == accessToken {
		t.Fatalf("access token 必须换新")
	}
	rotatedUser, _ := body["user"].(map[string]any)
	if rotatedUser == nil || rotatedUser["username"] != "rotate-user" {
		t.Fatalf("刷新后用户身份应保持不变: %v", body["user"])
	}
	if status := authStatus(t, r, http.MethodGet, "/api/user/profile", newAccess, nil, nil); status != http.StatusOK {
		t.Fatalf("新 access token 应可用, got %d", status)
	}

	// 旧 refresh token 已被消费，重放必须被拒绝。
	res, body = authDo(t, r, http.MethodPost, "/api/auth/mobile/refresh", "", map[string]any{
		"refresh_token": refreshToken,
	}, nil)
	if res.Code != http.StatusUnauthorized || body["code"] != "REFRESH_REQUIRED" {
		t.Fatalf("旧 refresh token 应被拒绝: %d %v", res.Code, body)
	}
}

func TestMobileRefreshRejectsUnknownToken(t *testing.T) {
	r := mobileAuthEnv(t)
	res, body := authDo(t, r, http.MethodPost, "/api/auth/mobile/refresh", "", map[string]any{
		"refresh_token": "not-a-real-token",
	}, nil)
	if res.Code != http.StatusUnauthorized || body["code"] != "REFRESH_REQUIRED" {
		t.Fatalf("未知 refresh token 应返回 401: %d %v", res.Code, body)
	}
}

// 登出必须同时撤销 access token 和 refresh token：否则攻击者拿着旧 refresh
// token 还能把会话续回来。
func TestMobileLogoutWithAccessTokenRevokesBothTokens(t *testing.T) {
	r := mobileAuthEnv(t)
	registerMobileUser(t, r, "logout-user", "secret123")
	accessToken, refreshToken := mobileLogin(t, r, "logout-user", "secret123")

	res, body := authDo(t, r, http.MethodPost, "/api/auth/mobile/logout", accessToken, map[string]any{
		"refresh_token": refreshToken,
	}, nil)
	if res.Code != http.StatusOK {
		t.Fatalf("logout: %d %v", res.Code, body)
	}
	if status := authStatus(t, r, http.MethodGet, "/api/user/profile", accessToken, nil, nil); status != http.StatusUnauthorized {
		t.Fatalf("撤销后 access token 应失效, got %d", status)
	}
	if status := authStatus(t, r, http.MethodPost, "/api/auth/mobile/refresh", "", map[string]any{"refresh_token": refreshToken}, nil); status != http.StatusUnauthorized {
		t.Fatalf("撤销后 refresh token 应失效, got %d", status)
	}
}

// access token 过期（或已丢失）时，仅凭 refresh token 也必须能退出登录。
func TestMobileLogoutWithRefreshTokenOnly(t *testing.T) {
	r := mobileAuthEnv(t)
	registerMobileUser(t, r, "logout-refresh-user", "secret123")
	_, refreshToken := mobileLogin(t, r, "logout-refresh-user", "secret123")

	res, body := authDo(t, r, http.MethodPost, "/api/auth/mobile/logout", "", map[string]any{
		"refresh_token": refreshToken,
	}, nil)
	if res.Code != http.StatusOK {
		t.Fatalf("logout: %d %v", res.Code, body)
	}
	if status := authStatus(t, r, http.MethodPost, "/api/auth/mobile/refresh", "", map[string]any{"refresh_token": refreshToken}, nil); status != http.StatusUnauthorized {
		t.Fatalf("登出后 refresh token 应失效, got %d", status)
	}
}

func TestMobileLoginRejectsWrongPassword(t *testing.T) {
	r := mobileAuthEnv(t)
	registerMobileUser(t, r, "wrong-pass-user", "secret123")

	res, body := authDo(t, r, http.MethodPost, "/api/auth/mobile/login", "", map[string]any{
		"username": "wrong-pass-user",
		"password": "wrong-password",
	}, nil)
	if res.Code != http.StatusUnauthorized || body["code"] != "INVALID_CREDENTIALS" {
		t.Fatalf("错误密码应返回 401 INVALID_CREDENTIALS: %d %v", res.Code, body)
	}
	if _, ok := body["access_token"]; ok {
		t.Fatalf("失败响应不应包含 access_token")
	}
}
