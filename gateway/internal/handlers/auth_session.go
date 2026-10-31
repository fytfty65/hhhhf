package handlers

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

// Credential lifecycle.
//
// The gateway used to mint one opaque token that lived for 24 hours, was stored
// in the browser's localStorage, and could also be passed through a WebSocket
// URL. That single credential was simultaneously the session, the refresh
// secret and a URL parameter — one XSS, one logged URL or one shared device was
// enough to replay it for a whole day.
//
// This file replaces it with the standard split:
//
//   - a short-lived access token (15 min) in memory only, sent as
//     `Authorization: Bearer`;
//   - a long-lived refresh token in an HttpOnly/Secure/SameSite cookie scoped to
//     /api/auth, stored server-side only as a SHA-256 hash and rotated on every
//     use;
//   - a device-session row per sign-in so a user can list and revoke individual
//     devices or all of them;
//   - single-use, 60-second WebSocket tickets so no long-lived credential ever
//     appears in a URL.

const (
	accessTokenTTL  = 15 * time.Minute
	refreshTokenTTL = 30 * 24 * time.Hour
	wsTicketTTL     = 60 * time.Second

	refreshCookieName = "omni_refresh"
	refreshCookiePath = "/api/auth"
)

var errRefreshRejected = errors.New("refresh token rejected")

// accessEntry mirrors one live access token. SessionID is empty only for
// compatibility tokens created by newSessionToken in tests.
type accessEntry struct {
	UserID    string    `json:"user_id"`
	SessionID string    `json:"session_id,omitempty"`
	ExpiresAt time.Time `json:"expires_at"`
}

var (
	accessMu    sync.Mutex
	accessStore = make(map[string]accessEntry)

	ticketMu    sync.Mutex
	ticketStore = make(map[string]accessEntry)
)

func randomToken(bytes int) (string, error) {
	buf := make([]byte, bytes)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return hex.EncodeToString(buf), nil
}

func hashToken(token string) string {
	sum := sha256.Sum256([]byte(strings.TrimSpace(token)))
	return hex.EncodeToString(sum[:])
}

func accessRedisKey(token string) string  { return "omni:access:" + token }
func ticketRedisKey(ticket string) string { return "omni:ws-ticket:" + ticket }

func purgeExpiredAccessLocked(now time.Time) {
	for token, entry := range accessStore {
		if !now.Before(entry.ExpiresAt) {
			delete(accessStore, token)
		}
	}
}

func storeAccessToken(token string, entry accessEntry) error {
	if client := redisSessionStore(); client != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		encoded, err := json.Marshal(entry)
		if err != nil {
			return err
		}
		return client.Set(ctx, accessRedisKey(token), encoded, time.Until(entry.ExpiresAt)).Err()
	}
	accessMu.Lock()
	defer accessMu.Unlock()
	now := time.Now()
	purgeExpiredAccessLocked(now)
	accessStore[token] = entry
	return nil
}

func lookupAccessToken(token string) (accessEntry, bool) {
	token = strings.TrimSpace(token)
	if token == "" {
		return accessEntry{}, false
	}
	if client := redisSessionStore(); client != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		raw, err := client.Get(ctx, accessRedisKey(token)).Result()
		if err != nil {
			return accessEntry{}, false
		}
		var entry accessEntry
		if json.Unmarshal([]byte(raw), &entry) != nil {
			return accessEntry{}, false
		}
		return entry, entry.UserID != "" && time.Now().Before(entry.ExpiresAt)
	}
	accessMu.Lock()
	defer accessMu.Unlock()
	entry, ok := accessStore[token]
	if !ok || entry.UserID == "" || !time.Now().Before(entry.ExpiresAt) {
		delete(accessStore, token)
		return accessEntry{}, false
	}
	return entry, true
}

func revokeAccessToken(token string) {
	token = strings.TrimSpace(token)
	if token == "" {
		return
	}
	if client := redisSessionStore(); client != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = client.Del(ctx, accessRedisKey(token)).Err()
		return
	}
	accessMu.Lock()
	delete(accessStore, token)
	accessMu.Unlock()
}

// newSessionToken is kept for compatibility with existing tests and call sites.
// It creates a short-lived access token without a device row; production login
// goes through createDeviceSession so the refresh cookie and device entry exist.
func newSessionToken(userID string) (string, error) {
	token, err := randomToken(32)
	if err != nil {
		return "", err
	}
	entry := accessEntry{UserID: userID, ExpiresAt: time.Now().Add(accessTokenTTL)}
	if err := storeAccessToken(token, entry); err != nil {
		return "", err
	}
	return token, nil
}

// createDeviceSession persists a new device row and returns its first access
// token plus the refresh token that belongs in the cookie.
func createDeviceSession(c *gin.Context, userID string) (accessToken, refreshToken, sessionID string, err error) {
	refreshToken, err = randomToken(32)
	if err != nil {
		return "", "", "", err
	}
	sessionID = uuid.New().String()
	now := time.Now()
	session := models.DeviceSession{
		ID:               sessionID,
		UserID:           userID,
		RefreshTokenHash: hashToken(refreshToken),
		UserAgent:        truncateRunes(strings.TrimSpace(c.GetHeader("User-Agent")), 200),
		IP:               c.ClientIP(),
		CreatedAt:        now,
		LastUsedAt:       now,
		ExpiresAt:        now.Add(refreshTokenTTL),
	}
	if err := database.DB.Create(&session).Error; err != nil {
		return "", "", "", err
	}
	accessToken, err = issueAccessToken(userID, sessionID)
	if err != nil {
		return "", "", "", err
	}
	return accessToken, refreshToken, sessionID, nil
}

func issueAccessToken(userID, sessionID string) (string, error) {
	token, err := randomToken(32)
	if err != nil {
		return "", err
	}
	entry := accessEntry{UserID: userID, SessionID: sessionID, ExpiresAt: time.Now().Add(accessTokenTTL)}
	if err := storeAccessToken(token, entry); err != nil {
		return "", err
	}
	return token, nil
}

// rotateRefreshToken validates a refresh token, rotates it (single use) and
// issues a fresh access token. Re-use of an old token is rejected because its
// hash no longer matches the session row.
func rotateRefreshToken(refreshToken string) (accessToken, newRefreshToken, userID string, err error) {
	hash := hashToken(refreshToken)
	var session models.DeviceSession
	if err := database.DB.Where("refresh_token_hash = ?", hash).First(&session).Error; err != nil {
		return "", "", "", errRefreshRejected
	}
	now := time.Now()
	if !session.Active(now) {
		return "", "", "", errRefreshRejected
	}
	newRefreshToken, err = randomToken(32)
	if err != nil {
		return "", "", "", err
	}
	updates := map[string]any{
		"refresh_token_hash": hashToken(newRefreshToken),
		"last_used_at":       now,
	}
	if err := database.DB.Model(&models.DeviceSession{}).Where("id = ?", session.ID).Updates(updates).Error; err != nil {
		return "", "", "", err
	}
	accessToken, err = issueAccessToken(session.UserID, session.ID)
	if err != nil {
		return "", "", "", err
	}
	return accessToken, newRefreshToken, session.UserID, nil
}

func revokeDeviceSession(userID, sessionID string) error {
	if sessionID == "" {
		return nil
	}
	now := time.Now()
	return database.DB.Model(&models.DeviceSession{}).
		Where("id = ? AND user_id = ?", sessionID, userID).
		Update("revoked_at", now).Error
}

func revokeAllDeviceSessions(userID, exceptSessionID string) error {
	query := database.DB.Model(&models.DeviceSession{}).
		Where("user_id = ? AND revoked_at IS NULL", userID)
	if exceptSessionID != "" {
		query = query.Where("id <> ?", exceptSessionID)
	}
	return query.Update("revoked_at", time.Now()).Error
}

// LookupAccessSession resolves an access token to its principal and device row.
func LookupAccessSession(token string) (userID, sessionID string, ok bool) {
	entry, ok := lookupAccessToken(token)
	return entry.UserID, entry.SessionID, ok
}

// ---- WebSocket tickets -------------------------------------------------

// issueWSTicket mints a single-use, 60-second ticket bound to the current
// session. It is safe to put in a WebSocket URL: by the time it is logged it is
// normally already consumed, and even if it is not it only opens one connection.
func issueWSTicket(userID, sessionID string) (string, error) {
	ticket, err := randomToken(24)
	if err != nil {
		return "", err
	}
	entry := accessEntry{UserID: userID, SessionID: sessionID, ExpiresAt: time.Now().Add(wsTicketTTL)}
	if client := redisSessionStore(); client != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		encoded, err := json.Marshal(entry)
		if err != nil {
			return "", err
		}
		if err := client.Set(ctx, ticketRedisKey(ticket), encoded, wsTicketTTL).Err(); err != nil {
			return "", err
		}
		return ticket, nil
	}
	ticketMu.Lock()
	defer ticketMu.Unlock()
	now := time.Now()
	for key, value := range ticketStore {
		if !now.Before(value.ExpiresAt) {
			delete(ticketStore, key)
		}
	}
	ticketStore[ticket] = entry
	return ticket, nil
}

// consumeWSTicket redeems a ticket exactly once.
func consumeWSTicket(ticket string) (accessEntry, bool) {
	ticket = strings.TrimSpace(ticket)
	if ticket == "" {
		return accessEntry{}, false
	}
	if client := redisSessionStore(); client != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		raw, err := client.GetDel(ctx, ticketRedisKey(ticket)).Result()
		if err != nil {
			return accessEntry{}, false
		}
		var entry accessEntry
		if json.Unmarshal([]byte(raw), &entry) != nil {
			return accessEntry{}, false
		}
		return entry, entry.UserID != "" && time.Now().Before(entry.ExpiresAt)
	}
	ticketMu.Lock()
	defer ticketMu.Unlock()
	entry, ok := ticketStore[ticket]
	delete(ticketStore, ticket)
	if !ok || entry.UserID == "" || !time.Now().Before(entry.ExpiresAt) {
		return accessEntry{}, false
	}
	return entry, true
}

// authenticateRequest resolves the request credential. WebSocket upgrade
// requests may use a single-use ticket (preferred) or the legacy bearer
// subprotocol; ordinary HTTP requests only accept the Authorization header.
func authenticateRequest(c *gin.Context) (userID, sessionID string, ok bool) {
	if c.Request.Method == http.MethodGet && strings.EqualFold(c.GetHeader("Upgrade"), "websocket") {
		if ticket := strings.TrimSpace(c.Query("ticket")); ticket != "" {
			if entry, valid := consumeWSTicket(ticket); valid {
				if entry.SessionID != "" && !deviceSessionActive(entry.UserID, entry.SessionID) {
					return "", "", false
				}
				return entry.UserID, entry.SessionID, true
			}
			return "", "", false
		}
	}
	userID, sessionID, ok = LookupAccessSession(requestToken(c))
	if !ok {
		return "", "", false
	}
	// Revocation must take effect immediately even though the access token is
	// still inside its 15-minute window: logout, logout-all and password changes
	// all mark the device row revoked.
	if sessionID != "" && !deviceSessionActive(userID, sessionID) {
		return "", "", false
	}
	return userID, sessionID, true
}

func deviceSessionActive(userID, sessionID string) bool {
	if database.DB == nil || sessionID == "" {
		return true
	}
	var session models.DeviceSession
	if err := database.DB.Where("id = ? AND user_id = ?", sessionID, userID).First(&session).Error; err != nil {
		return false
	}
	return session.Active(time.Now())
}

// ---- Cookie helpers ----------------------------------------------------

func cookieSecure(c *gin.Context) bool {
	if explicit := strings.TrimSpace(os.Getenv("AUTH_COOKIE_SECURE")); explicit != "" {
		return strings.EqualFold(explicit, "true") || explicit == "1"
	}
	env := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
	return env == "production" || env == "prod" || c.Request.TLS != nil
}

func cookieSameSite() http.SameSite {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("AUTH_COOKIE_SAMESITE"))) {
	case "none":
		return http.SameSiteNoneMode
	case "strict":
		return http.SameSiteStrictMode
	default:
		return http.SameSiteLaxMode
	}
}

func setRefreshCookie(c *gin.Context, token string) {
	c.SetSameSite(cookieSameSite())
	c.SetCookie(refreshCookieName, token, int(refreshTokenTTL.Seconds()), refreshCookiePath, "", cookieSecure(c), true)
}

func clearRefreshCookie(c *gin.Context) {
	c.SetSameSite(cookieSameSite())
	c.SetCookie(refreshCookieName, "", -1, refreshCookiePath, "", cookieSecure(c), true)
}

func requestRefreshToken(c *gin.Context) string {
	token, err := c.Cookie(refreshCookieName)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(token)
}

// respondWithSession writes the login/registration payload and installs the
// refresh cookie. The access token is returned in the body (memory only on the
// client) and never persisted by the browser.
func respondWithSession(c *gin.Context, message string, user models.User) {
	accessToken, refreshToken, _, err := createDeviceSession(c, user.ID)
	if err != nil {
		securityEvent(c, secEventAuthLogin, secOutcomeFailure, user.Username, "SESSION_STORE_UNAVAILABLE",
			map[string]any{"reason": fmt.Sprintf("%v", err)})
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "认证服务暂不可用", "code": "SESSION_STORE_UNAVAILABLE"})
		return
	}
	setRefreshCookie(c, refreshToken)
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{
		"message":    message,
		"token":      accessToken,
		"expires_in": int(accessTokenTTL.Seconds()),
		"user":       buildAuthUser(user),
	})
}
