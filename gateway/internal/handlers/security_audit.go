package handlers

import (
	"encoding/json"
	"log"
	"strings"

	"gateway/internal/metrics"

	"github.com/gin-gonic/gin"
)

// Security event logging.
//
// Background: the gateway had a structured access log (method/path/status/
// request_id) but no security-event record at all. A failed login, an account
// lockout, a rate-limit trip and an identity-mismatch 403 were all invisible —
// you could see that *some* request returned 401, but not which account was
// being attacked, from where, or whether the attacker was rotating IPs and
// tripping limits. Without that, brute-force and lockout abuse are neither
// alertable nor forensically traceable.
//
// Design constraints:
//   - Emit one JSON object per line on the same stream as the access log, with
//     "event":"security" so a log pipeline can filter it out cheaply.
//   - Never log a password, a session token, or a request body. The account name
//     is the security principal and is required; the token is not, and logging it
//     would turn the log file into a credential store.
//   - Never log a raw client IP for the *rate-limit key*: the limiter hashes it
//     downstream for the same reason. The IP is included here because an operator
//     investigating an attack needs it, and it is bounded to the current client
//     identity the rate limiter already uses.

// securityEvent writes one structured security record.
//
// username should already be normalised by the caller when it is a lookup key.
// extra entries are merged in for event-specific context (e.g. lock duration).
func securityEvent(c *gin.Context, event, outcome, username, code string, extra map[string]any) {
	entry := map[string]any{
		"event":   "security",
		"type":    event,
		"outcome": outcome,
	}
	if requestID := strings.TrimSpace(c.GetHeader("X-Request-ID")); requestID != "" {
		entry["request_id"] = requestID
	}
	if username = strings.TrimSpace(username); username != "" {
		entry["username"] = username
	}
	if c != nil && c.Request != nil {
		entry["client_ip"] = c.ClientIP()
		entry["method"] = c.Request.Method
		if c.FullPath() != "" {
			entry["route"] = c.FullPath()
		}
		if agent := strings.TrimSpace(c.GetHeader("User-Agent")); agent != "" {
			// Bounded: a caller controls this header, so it must not be able to
			// bloat the log line.
			entry["user_agent"] = truncateRunes(agent, 120)
		}
	}
	if code != "" {
		entry["code"] = code
	}
	for key, value := range extra {
		entry[key] = value
	}
	if encoded, err := json.Marshal(entry); err == nil {
		log.Println(string(encoded))
	}
	// 日志用于取证，计数用于运维看板的即时读数。
	metrics.RecordSecurityEvent(event)
}

// Event type and outcome constants, so call sites cannot drift into a dozen
// spellings that a log query would have to guess at.
const (
	secEventAuthLogin     = "auth_login"
	secEventAuthLockout   = "auth_lockout"
	secEventRateLimit     = "rate_limit"
	secEventIdentityCheck = "identity_check"

	secOutcomeFailure = "failure"
	secOutcomeBlocked = "blocked"
)

// logLoginFailure records a rejected credential attempt.
func logLoginFailure(c *gin.Context, username, code string) {
	securityEvent(c, secEventAuthLogin, secOutcomeFailure, strings.ToLower(strings.TrimSpace(username)), code, nil)
}

// logAccountLockout records that an account was refused (or became) locked.
func logAccountLockout(c *gin.Context, username string, remainingSeconds int) {
	securityEvent(c, secEventAuthLockout, secOutcomeBlocked,
		strings.ToLower(strings.TrimSpace(username)), "ACCOUNT_TEMPORARILY_LOCKED",
		map[string]any{"retry_after_seconds": remainingSeconds})
}

// logRateLimited records a rate-limit rejection with the enforcement scope.
func logRateLimited(c *gin.Context, namespace string, retryAfterSeconds int) {
	securityEvent(c, secEventRateLimit, secOutcomeBlocked, "", "AUTH_RATE_LIMITED",
		map[string]any{"namespace": namespace, "retry_after_seconds": retryAfterSeconds})
}

// logIdentityMismatch records an attempt to act as a different user.
//
// This is the signature of an authorization probe rather than a mistake: the
// caller holds a valid session for one account and named another. The claimed
// identity is recorded, the actual one is not (it is already in the access log
// via the session), and neither is a token.
func logIdentityMismatch(c *gin.Context, claimed string) {
	securityEvent(c, secEventIdentityCheck, secOutcomeBlocked, "", "USER_MISMATCH",
		map[string]any{"claimed_user_id": truncateRunes(strings.TrimSpace(claimed), 64)})
}
