package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

// 2026-11-01 事故回归：前端 apiClient 给每个请求都加了 X-Request-Id，
// 预订下单还会加 Idempotency-Key。如果它们不在 Access-Control-Allow-Headers 里，
// 浏览器会在预检阶段直接失败（net::ERR_FAILED），而服务端日志里连请求都看不到，
// 排查时极易误判为「网关没启动」。这条测试把白名单与客户端实际发送的头钉在一起。
func TestCORSAllowsHeadersSentByWebClient(t *testing.T) {
	t.Setenv("ALLOWED_ORIGINS", "")
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(corsMiddleware())
	r.POST("/probe", func(c *gin.Context) { c.Status(http.StatusOK) })

	requested := "content-type,x-request-id,idempotency-key"
	request := httptest.NewRequest(http.MethodOptions, "/probe", nil)
	request.Header.Set("Origin", "http://localhost:3000")
	request.Header.Set("Access-Control-Request-Method", "POST")
	request.Header.Set("Access-Control-Request-Headers", requested)

	recorder := httptest.NewRecorder()
	r.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusNoContent {
		t.Fatalf("expected 204 preflight, got %d", recorder.Code)
	}

	allowed := strings.ToLower(recorder.Header().Get("Access-Control-Allow-Headers"))
	for _, header := range strings.Split(requested, ",") {
		if !strings.Contains(allowed, strings.ToLower(header)) {
			t.Fatalf("Access-Control-Allow-Headers 缺少 %q → 浏览器预检失败；当前白名单: %s", header, allowed)
		}
	}
	if origin := recorder.Header().Get("Access-Control-Allow-Origin"); origin != "http://localhost:3000" {
		t.Fatalf("expected the requesting origin to be echoed, got %q", origin)
	}
}
