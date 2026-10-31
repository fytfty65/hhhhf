package handlers

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

// MaxRequestBodyBytes is the absolute per-request body ceiling for the gateway.
//
// It is deliberately larger than maxIdentityProbeBodyBytes (the 12 MiB the
// identity probe is willing to buffer for a base64 reference-route screenshot)
// so that legitimate large uploads still produce the probe's precise
// PAYLOAD_TOO_LARGE response rather than this coarser one.
const MaxRequestBodyBytes = maxIdentityProbeBodyBytes + (4 << 20)

// BodyLimitMiddleware caps the number of bytes any handler can read from a
// request body.
//
// Why this is needed even though AuthMiddleware already rejects oversized JSON:
// that check is gated on `Content-Type: application/json`. Any request with a
// different (or absent) content type skipped it entirely and streamed an
// unbounded body straight into a handler, and any handler that reads the body
// into memory would allocate whatever the client sent. Wrapping the body in
// http.MaxBytesReader moves the ceiling out of individual handlers and into the
// transport, so the limit holds regardless of content type, route or handler
// implementation, and an over-limit body fails as an explicit 413.
//
// Ordering note: this runs before AuthMiddleware, which reads the body for its
// identity probe. With the limit in place that read is bounded no matter what
// the client claims in Content-Length (chunked requests included).
func BodyLimitMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		if c.Request.Body != nil {
			c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, MaxRequestBodyBytes)
		}
		c.Next()
	}
}
