package handlers

import (
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestValidateSessionStoreRequiresRedisInProduction(t *testing.T) {
	oldEnv, oldRedis := os.Getenv("APP_ENV"), os.Getenv("REDIS_URL")
	defer os.Setenv("APP_ENV", oldEnv)
	defer os.Setenv("REDIS_URL", oldRedis)
	os.Setenv("APP_ENV", "production")
	os.Unsetenv("REDIS_URL")
	if err := ValidateSessionStore(); err == nil {
		t.Fatal("expected production startup to require REDIS_URL")
	}
}

func TestAuthMiddlewareAcceptsBearerAndSetsPrincipal(t *testing.T) {
	gin.SetMode(gin.TestMode)
	token, _ := newSessionToken("user-1")
	r := gin.New()
	r.POST("/protected", AuthMiddleware(), func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"user_id": CurrentUserID(c)})
	})
	req := httptest.NewRequest(http.MethodPost, "/protected", strings.NewReader(`{"user_id":"user-1"}`))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	if res.Code != http.StatusOK || !strings.Contains(res.Body.String(), `"user_id":"user-1"`) {
		t.Fatalf("expected authenticated request, got %d %s", res.Code, res.Body.String())
	}
}

func TestAuthMiddlewareRejectsIdentityImpersonation(t *testing.T) {
	gin.SetMode(gin.TestMode)
	token, _ := newSessionToken("user-1")
	r := gin.New()
	r.POST("/protected", AuthMiddleware(), func(c *gin.Context) { c.Status(http.StatusOK) })
	req := httptest.NewRequest(http.MethodPost, "/protected", strings.NewReader(`{"user_id":"user-2"}`))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	if res.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for impersonation, got %d %s", res.Code, res.Body.String())
	}
}

// 真实缺口（2026-10-04 实测）：身份探测中间件原来只读 2MB 就把这段截断内容当作请求体，
// 于是超过 2MB 的合法 JSON（参考路线截图 base64 约 5.6MB）在 handler 眼里成了坏 JSON，
// 客户端只收到"请求数据格式不正确"。下面两个用例把口径钉死：不许截断，超限如实 413。
func TestAuthMiddlewarePreservesLargeJSONBodyForHandlers(t *testing.T) {
	gin.SetMode(gin.TestMode)
	token, _ := newSessionToken("user-1")
	const payloadSize = 3 << 20 // 3MB > 旧的 2MB 截断阈值
	big := strings.Repeat("a", payloadSize)
	r := gin.New()
	r.POST("/protected", AuthMiddleware(), func(c *gin.Context) {
		var body struct {
			Blob string `json:"blob"`
		}
		if err := c.ShouldBindJSON(&body); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"code": "INVALID_JSON"})
			return
		}
		c.JSON(http.StatusOK, gin.H{"blob_len": len(body.Blob)})
	})
	req := httptest.NewRequest(http.MethodPost, "/protected", strings.NewReader(`{"blob":"`+big+`"}`))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("大请求体不该被中间件截断成坏 JSON: %d %s", res.Code, res.Body.String())
	}
	if !strings.Contains(res.Body.String(), `"blob_len":3145728`) {
		t.Fatalf("handler 必须收到完整请求体，实际 %s", res.Body.String())
	}
}

func TestAuthMiddlewareRejectsOversizedBodyWith413NotInvalidJSON(t *testing.T) {
	gin.SetMode(gin.TestMode)
	token, _ := newSessionToken("user-1")
	r := gin.New()
	r.POST("/protected", AuthMiddleware(), func(c *gin.Context) { c.Status(http.StatusOK) })
	oversized := strings.Repeat("a", maxIdentityProbeBodyBytes+1024)
	req := httptest.NewRequest(http.MethodPost, "/protected", strings.NewReader(`{"blob":"`+oversized+`"}`))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	if res.Code != http.StatusRequestEntityTooLarge || !strings.Contains(res.Body.String(), "PAYLOAD_TOO_LARGE") {
		t.Fatalf("超限必须如实说「太大」，而不是含糊的格式错误: %d %s", res.Code, res.Body.String())
	}
}

func TestAuthMiddlewareRejectsMissingToken(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/protected", AuthMiddleware(), func(c *gin.Context) { c.Status(http.StatusOK) })
	res := httptest.NewRecorder()
	r.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/protected", nil))
	if res.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 without token, got %d", res.Code)
	}
}

func TestLogoutRevokesCurrentToken(t *testing.T) {
	gin.SetMode(gin.TestMode)
	token, _ := newSessionToken("user-logout")
	r := gin.New()
	r.POST("/logout", AuthMiddleware(), LogoutHandler)
	req := httptest.NewRequest(http.MethodPost, "/logout", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("expected logout success, got %d %s", res.Code, res.Body.String())
	}
	if _, ok := LookupSessionUserID(token); ok {
		t.Fatal("expected token to be revoked")
	}
}
