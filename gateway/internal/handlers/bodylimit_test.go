package handlers

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

// Tests for the global request-body ceiling.
//
// The gateway had per-endpoint limits in a few handlers, but nothing global, and
// AuthMiddleware's own 12 MiB check is gated on `Content-Type: application/json`.
// A request with any other content type therefore reached its handler with an
// unbounded body. These tests pin the transport-level limit that closes that gap.

// bodyLimitRouter reports how many body bytes the handler could actually read,
// and surfaces the read error the way real handlers do (io.ReadAll is what the
// identity probe and every ShouldBindJSON path use).
func bodyLimitRouter() (*gin.Engine, *int64) {
	gin.SetMode(gin.TestMode)
	read := new(int64)
	r := gin.New()
	r.Use(BodyLimitMiddleware())
	r.POST("/probe", func(c *gin.Context) {
		body, err := io.ReadAll(c.Request.Body)
		*read = int64(len(body))
		if err != nil {
			c.AbortWithStatusJSON(http.StatusRequestEntityTooLarge, gin.H{"code": "PAYLOAD_TOO_LARGE"})
			return
		}
		c.Status(http.StatusOK)
	})
	return r, read
}

// TestBodyLimitAllowsNormalPayloads guards against a limit so tight that real
// requests break. The largest legitimate body is a base64 reference-route
// screenshot (~5.6 MiB), which must still pass.
func TestBodyLimitAllowsNormalPayloads(t *testing.T) {
	r, _ := bodyLimitRouter()

	for _, size := range []int{0, 1 << 10, 5 << 20} {
		body := bytes.Repeat([]byte("a"), size)
		req := httptest.NewRequest(http.MethodPost, "/probe", bytes.NewReader(body))
		req.Header.Set("Content-Type", "application/octet-stream")
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		if res.Code != http.StatusOK {
			t.Fatalf("a %d-byte body must be accepted, got %d", size, res.Code)
		}
	}
}

// TestBodyLimitRejectsOversizedNonJSONBody is the regression: this path had no
// limit at all, because the only existing check required a JSON content type.
func TestBodyLimitRejectsOversizedNonJSONBody(t *testing.T) {
	r, _ := bodyLimitRouter()

	body := bytes.Repeat([]byte("a"), MaxRequestBodyBytes+(1<<20))
	req := httptest.NewRequest(http.MethodPost, "/probe", bytes.NewReader(body))
	// Deliberately NOT application/json: this is the case that used to be
	// unbounded.
	req.Header.Set("Content-Type", "application/octet-stream")
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)

	if res.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("an oversized non-JSON body must be refused with 413, got %d", res.Code)
	}
	if !strings.Contains(res.Body.String(), "PAYLOAD_TOO_LARGE") {
		t.Fatalf("expected a machine-readable code, got %s", res.Body.String())
	}
}

// TestBodyLimitAppliesRegardlessOfContentType pins that the ceiling is a property
// of the transport, not of a particular content type.
func TestBodyLimitAppliesRegardlessOfContentType(t *testing.T) {
	r, _ := bodyLimitRouter()

	for _, contentType := range []string{
		"application/octet-stream",
		"text/plain",
		"application/x-www-form-urlencoded",
		"", // no content type at all
	} {
		t.Run("ct="+contentType, func(t *testing.T) {
			body := bytes.Repeat([]byte("a"), MaxRequestBodyBytes+(1<<20))
			req := httptest.NewRequest(http.MethodPost, "/probe", bytes.NewReader(body))
			if contentType != "" {
				req.Header.Set("Content-Type", contentType)
			}
			res := httptest.NewRecorder()
			r.ServeHTTP(res, req)
			if res.Code != http.StatusRequestEntityTooLarge {
				t.Fatalf("content type %q must not bypass the limit, got %d", contentType, res.Code)
			}
		})
	}
}

// TestBodyLimitCannotBeBypassedByLyingContentLength covers a chunked request,
// where Content-Length is -1 and the size is only discoverable by reading.
func TestBodyLimitCannotBeBypassedByLyingContentLength(t *testing.T) {
	r, _ := bodyLimitRouter()

	body := bytes.Repeat([]byte("a"), MaxRequestBodyBytes+(1<<20))
	req := httptest.NewRequest(http.MethodPost, "/probe", bytes.NewReader(body))
	req.ContentLength = -1 // simulates chunked encoding
	req.Header.Set("Content-Type", "application/octet-stream")
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)

	if res.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("a chunked oversized body must still be refused, got %d", res.Code)
	}
}

// TestBodyLimitCeilingExceedsTheIdentityProbe documents the relationship between
// the two limits: the transport ceiling must sit above the probe's buffer so that
// legitimate large JSON still gets the probe's precise error instead of the
// coarser transport one.
func TestBodyLimitCeilingExceedsTheIdentityProbe(t *testing.T) {
	if MaxRequestBodyBytes <= maxIdentityProbeBodyBytes {
		t.Fatalf("the transport ceiling (%d) must exceed the identity-probe buffer (%d), "+
			"otherwise large legitimate JSON is rejected by the wrong layer",
			MaxRequestBodyBytes, maxIdentityProbeBodyBytes)
	}
	// But it must still be a real bound, not effectively unlimited.
	if MaxRequestBodyBytes > 64<<20 {
		t.Fatalf("the transport ceiling (%d) is too permissive to bound memory", MaxRequestBodyBytes)
	}
}

// TestPhotoReportIsRateLimited pins the takedown-throttle.
//
// Reporting is a takedown primitive: the handler immediately flips a photo to
// `pending`, hiding it from every other user until review. With no limit a single
// authenticated account could un-publish the entire gallery, so the limiter is
// part of the security model, not a nicety.
func TestPhotoReportIsRateLimited(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	// A stable principal and client address, so the limit is actually reached
	// rather than spread across keys.
	router.Use(func(c *gin.Context) {
		c.Set("user_id", "reporter-1")
		c.Next()
	})
	router.POST("/photos/:id/report", PhotoReportRateLimitMiddleware(), func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"reported": true})
	})

	attempt := func() *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodPost, "/photos/p1/report", nil)
		req.RemoteAddr = "203.0.113.77:1234"
		res := httptest.NewRecorder()
		router.ServeHTTP(res, req)
		return res
	}

	allowed := 0
	var limited *httptest.ResponseRecorder
	for i := 0; i < 12; i++ {
		res := attempt()
		switch res.Code {
		case http.StatusOK:
			allowed++
		case http.StatusTooManyRequests:
			if limited == nil {
				limited = res
			}
		default:
			t.Fatalf("unexpected status %d: %s", res.Code, res.Body.String())
		}
	}

	if limited == nil {
		t.Fatal("repeated photo reports were never rate limited")
	}
	if allowed == 0 {
		t.Fatal("the limiter refused every request, including the first legitimate ones")
	}
	if allowed > 5 {
		t.Fatalf("expected at most 5 allowed reports per window, got %d", allowed)
	}
	if limited.Header().Get("Retry-After") == "" {
		t.Fatal("a 429 must carry Retry-After")
	}
	if !strings.Contains(limited.Body.String(), "PHOTO_REPORT_RATE_LIMITED") {
		t.Fatalf("expected a machine-readable code, got %s", limited.Body.String())
	}
}

// TestPhotoReportLimitIsPerPrincipal ensures one abusive account cannot exhaust
// another account's budget.
func TestPhotoReportLimitIsPerPrincipal(t *testing.T) {
	gin.SetMode(gin.TestMode)
	newRouter := func(userID string) *gin.Engine {
		r := gin.New()
		r.Use(func(c *gin.Context) {
			c.Set("user_id", userID)
			c.Next()
		})
		r.POST("/photos/:id/report", PhotoReportRateLimitMiddleware(), func(c *gin.Context) {
			c.JSON(http.StatusOK, gin.H{"reported": true})
		})
		return r
	}
	hit := func(r *gin.Engine) int {
		req := httptest.NewRequest(http.MethodPost, "/photos/p1/report", nil)
		req.RemoteAddr = "203.0.113.90:2222"
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		return res.Code
	}

	abuser := newRouter("abuser")
	for i := 0; i < 8; i++ {
		hit(abuser)
	}
	if code := hit(abuser); code != http.StatusTooManyRequests {
		t.Fatalf("the abuser should be limited by now, got %d", code)
	}

	// A different principal on the same client address must still be served: the
	// key includes the user, not just the IP.
	victim := newRouter("innocent-user")
	if code := hit(victim); code != http.StatusOK {
		t.Fatalf("an unrelated account must not inherit the abuser's limit, got %d", code)
	}
}
