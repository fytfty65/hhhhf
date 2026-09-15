package api

import (
	"net/http"
	"time"

	"gateway/internal/contracts"
	"github.com/gin-gonic/gin"
)

const contractVersion = "2026-09-04"

func writeData(c *gin.Context, status int, data any) {
	c.JSON(status, contracts.Envelope{Data: data, Meta: contracts.Meta{
		RequestID: c.GetHeader("X-Request-ID"), Timestamp: time.Now().UTC(), Version: contractVersion,
	}})
}

func writeError(c *gin.Context, status int, code, message string, details map[string]string) {
	c.JSON(status, contracts.Envelope{Meta: contracts.Meta{
		RequestID: c.GetHeader("X-Request-ID"), Timestamp: time.Now().UTC(), Version: contractVersion,
	}, Error: &contracts.Error{Code: code, Message: message, RequestID: c.GetHeader("X-Request-ID"), Details: details}})
}

func badRequest(c *gin.Context, code, message string) {
	writeError(c, http.StatusBadRequest, code, message, nil)
}
