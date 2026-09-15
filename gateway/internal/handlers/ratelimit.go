package handlers

import (
	"context"
	"crypto/sha256"
	"fmt"
	"log"
	"math"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
)

// fixedWindowLimiter is deliberately small and dependency-free. It protects
// a single gateway process from accidental retry storms; production deployments
// should pair it with a distributed Redis limiter when multiple replicas run.
type fixedWindowLimiter struct {
	mu      sync.Mutex
	entries map[string]rateWindow
	limit   int
	window  time.Duration
}

type rateWindow struct {
	started time.Time
	count   int
}

func newFixedWindowLimiter(limit int, window time.Duration) *fixedWindowLimiter {
	return &fixedWindowLimiter{
		entries: make(map[string]rateWindow),
		limit:   limit,
		window:  window,
	}
}

func (l *fixedWindowLimiter) allow(key string, now time.Time) (bool, time.Duration) {
	key = strings.TrimSpace(key)
	if key == "" {
		return false, l.window
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	entry, ok := l.entries[key]
	if !ok || now.Sub(entry.started) >= l.window {
		l.entries[key] = rateWindow{started: now, count: 1}
		l.pruneLocked(now)
		return true, 0
	}
	if entry.count >= l.limit {
		remaining := l.window - now.Sub(entry.started)
		if remaining < 0 {
			remaining = 0
		}
		return false, remaining
	}
	entry.count++
	l.entries[key] = entry
	return true, 0
}

func (l *fixedWindowLimiter) pruneLocked(now time.Time) {
	// Keep an attacker from growing this map without bound. The normal path
	// remains O(1); pruning is only done after a new window is created.
	if len(l.entries) <= 10000 {
		return
	}
	for key, entry := range l.entries {
		if now.Sub(entry.started) >= l.window {
			delete(l.entries, key)
		}
	}
}

// distributedAllow uses Redis when configured so limits are shared by all
// gateway replicas. The hashed key avoids putting user or room identifiers in
// Redis key names. The third return value tells callers whether Redis was
// configured; an unconfigured local run can safely use the in-process limiter.
func distributedAllow(namespace, key string, limit int, window time.Duration) (allowed bool, retryAfter time.Duration, configured bool, err error) {
	if strings.TrimSpace(os.Getenv("REDIS_URL")) == "" {
		return false, 0, false, nil
	}
	client, err := sessionRedis()
	if err != nil {
		return false, 0, true, err
	}
	if client == nil {
		return false, 0, true, fmt.Errorf("redis client is nil")
	}
	bucketSeconds := int64(window / time.Second)
	if bucketSeconds < 1 {
		bucketSeconds = 1
	}
	bucket := time.Now().Unix() / bucketSeconds
	digest := sha256.Sum256([]byte(key))
	redisKey := fmt.Sprintf("omni:ratelimit:%s:%x:%d", namespace, digest[:8], bucket)
	ctx, cancel := context.WithTimeout(context.Background(), 700*time.Millisecond)
	defer cancel()
	count, err := client.Incr(ctx, redisKey).Result()
	if err != nil {
		return false, 0, true, err
	}
	if count == 1 {
		_ = client.Expire(ctx, redisKey, window).Err()
	}
	ttl, ttlErr := client.TTL(ctx, redisKey).Result()
	if ttlErr != nil || ttl <= 0 {
		ttl = window
	}
	return count <= int64(limit), ttl, true, nil
}

var (
	// Expensive external calls: 30 requests per principal per minute.
	aiHTTPLimiter = newFixedWindowLimiter(30, time.Minute)
	// A full multi-agent negotiation can fan out to many provider calls.
	agentLimiter      = newFixedWindowLimiter(4, time.Minute)
	roomActionLimiter = newFixedWindowLimiter(20, time.Minute)
	// Authentication is unauthenticated by definition, so it is limited by
	// client IP rather than by principal. Registration is tighter because each
	// request costs a bcrypt hash.
	authLoginLimiter    = newFixedWindowLimiter(10, time.Minute)
	authRegisterLimiter = newFixedWindowLimiter(5, time.Minute)
)

// authFailureMu guards authFailureCount, which implements a per-account
// lockout. A pure IP window is not enough: a distributed attacker can rotate
// source addresses, but the targeted account stays the same.
var (
	authFailureMu    sync.Mutex
	authFailureCount = make(map[string]authFailureWindow)
)

type authFailureWindow struct {
	count       int
	lastAttempt time.Time
}

const (
	// authFailureThreshold consecutive failures lock the account temporarily.
	authFailureThreshold = 8
	// authFailureLockout is how long the lock lasts after the threshold.
	authFailureLockout = 15 * time.Minute
	// authFailureDecay forgets failures for an account after a quiet period so
	// an occasional typo never accumulates into a lockout.
	authFailureDecay = 15 * time.Minute
)

// authClientKey derives a stable per-client rate limit key. The key is hashed
// downstream by distributedAllow so raw IPs never appear in Redis.
func authClientKey(c *gin.Context, namespace string) string {
	return namespace + ":ip:" + c.ClientIP()
}

// authAccountLocked reports whether an account is currently locked out.
func authAccountLocked(username string) (bool, time.Duration) {
	username = strings.ToLower(strings.TrimSpace(username))
	if username == "" {
		return false, 0
	}
	authFailureMu.Lock()
	defer authFailureMu.Unlock()
	entry, ok := authFailureCount[username]
	if !ok {
		return false, 0
	}
	if time.Since(entry.lastAttempt) > authFailureDecay {
		delete(authFailureCount, username)
		return false, 0
	}
	if entry.count < authFailureThreshold {
		return false, 0
	}
	remaining := authFailureLockout - time.Since(entry.lastAttempt)
	if remaining <= 0 {
		delete(authFailureCount, username)
		return false, 0
	}
	return true, remaining
}

// recordAuthFailure increments the failure counter for an account.
func recordAuthFailure(username string) {
	username = strings.ToLower(strings.TrimSpace(username))
	if username == "" {
		return
	}
	authFailureMu.Lock()
	defer authFailureMu.Unlock()
	entry := authFailureCount[username]
	if time.Since(entry.lastAttempt) > authFailureDecay {
		entry = authFailureWindow{}
	}
	entry.count++
	entry.lastAttempt = time.Now()
	authFailureCount[username] = entry
	// Bound the map so a spray across many usernames cannot grow it forever.
	if len(authFailureCount) > 10000 {
		cutoff := time.Now().Add(-authFailureDecay)
		for key, value := range authFailureCount {
			if value.lastAttempt.Before(cutoff) {
				delete(authFailureCount, key)
			}
		}
	}
}

// clearAuthFailures resets an account's counter after a successful login.
func clearAuthFailures(username string) {
	username = strings.ToLower(strings.TrimSpace(username))
	if username == "" {
		return
	}
	authFailureMu.Lock()
	delete(authFailureCount, username)
	authFailureMu.Unlock()
}

// allowAuthAttempt applies the IP-window limiter, optionally backed by Redis.
//
// If Redis is configured but unreachable we fall back to the in-process
// limiter rather than failing the request. Failing closed here would mean a
// Redis blip makes login impossible, which is a worse outcome than temporarily
// enforcing the limit per replica instead of globally.
func allowAuthAttempt(c *gin.Context, namespace string, limit int, local *fixedWindowLimiter) bool {
	key := authClientKey(c, namespace)
	if allowed, retryAfter, configured, err := distributedAllow(namespace, key, limit, time.Minute); configured {
		if err != nil {
			log.Printf("认证限流降级: 共享限流后端不可用，改用进程内限流: %v", err)
		} else if !allowed {
			seconds := int(math.Ceil(retryAfter.Seconds()))
			if seconds < 1 {
				seconds = 1
			}
			c.Header("Retry-After", strconv.Itoa(seconds))
			c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{
				"error": "尝试过于频繁，请稍后再试",
				"code":  "AUTH_RATE_LIMITED",
			})
			return false
		} else {
			return true
		}
	}
	if allowed, retryAfter := local.allow(key, time.Now()); !allowed {
		seconds := int(math.Ceil(retryAfter.Seconds()))
		if seconds < 1 {
			seconds = 1
		}
		c.Header("Retry-After", strconv.Itoa(seconds))
		c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{
			"error": "尝试过于频繁，请稍后再试",
			"code":  "AUTH_RATE_LIMITED",
		})
		return false
	}
	return true
}

// AuthLoginRateLimitMiddleware limits unauthenticated credential attempts.
func AuthLoginRateLimitMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		if !allowAuthAttempt(c, "auth-login", 10, authLoginLimiter) {
			return
		}
		c.Next()
	}
}

// AuthRegisterRateLimitMiddleware limits unauthenticated account creation.
func AuthRegisterRateLimitMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		if !allowAuthAttempt(c, "auth-register", 5, authRegisterLimiter) {
			return
		}
		c.Next()
	}
}

func rateLimitKey(c *gin.Context) string {
	userID := CurrentUserID(c)
	if roomID := strings.TrimSpace(c.Query("room_id")); roomID != "" {
		return userID + ":room:" + roomID
	}
	return userID
}

// AIRateLimitMiddleware protects HTTP endpoints that proxy to paid/external
// AI, map, weather, or traffic providers. AuthMiddleware must run first.
// checkLimit consults the shared Redis limiter when it is configured and
// healthy, and otherwise enforces the same limit with the in-process limiter.
//
// The important property is availability: a Redis outage must not turn every
// rate-limited endpoint into a 503. Degrading to per-replica enforcement is
// strictly better than refusing traffic, because the caller is still bounded
// within one process even though the bound is no longer global.
func checkLimit(namespace, key string, limit int, window time.Duration, local *fixedWindowLimiter) (allowed bool, retryAfter time.Duration) {
	if allowed, retryAfter, configured, err := distributedAllow(namespace, key, limit, window); configured {
		if err == nil {
			return allowed, retryAfter
		}
		log.Printf("限流降级[%s]: 共享限流后端不可用，改用进程内限流: %v", namespace, err)
	}
	return local.allow(key, time.Now())
}

// allowAgentNegotiation bounds full multi-agent negotiations per principal.
func allowAgentNegotiation(userID string) (bool, time.Duration) {
	return checkLimit("agent", userID, 4, time.Minute, agentLimiter)
}

// AIRateLimitMiddleware protects HTTP endpoints that proxy to paid/external
// AI, map, weather, or traffic providers. AuthMiddleware must run first.
func AIRateLimitMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		if CurrentUserID(c) == "" {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "未登录", "code": "AUTH_REQUIRED"})
			return
		}
		if allowed, retryAfter := checkLimit("ai-http", rateLimitKey(c), 30, time.Minute, aiHTTPLimiter); !allowed {
			seconds := int(math.Ceil(retryAfter.Seconds()))
			if seconds < 1 {
				seconds = 1
			}
			c.Header("Retry-After", strconv.Itoa(seconds))
			c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{"error": "智能服务请求过于频繁，请稍后再试", "code": "AI_RATE_LIMITED"})
			return
		}
		c.Next()
	}
}

// RoomRateLimitMiddleware limits invite-code probing and room spam while
// keeping normal collaboration comfortably below the threshold.
func RoomRateLimitMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		key := CurrentUserID(c) + ":" + c.ClientIP()
		if allowed, retryAfter := checkLimit("room", key, 20, time.Minute, roomActionLimiter); !allowed {
			seconds := int(math.Ceil(retryAfter.Seconds()))
			if seconds < 1 {
				seconds = 1
			}
			c.Header("Retry-After", strconv.Itoa(seconds))
			c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{"error": "房间操作过于频繁，请稍后再试", "code": "ROOM_RATE_LIMITED"})
			return
		}
		c.Next()
	}
}
