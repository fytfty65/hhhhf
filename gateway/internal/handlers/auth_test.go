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
