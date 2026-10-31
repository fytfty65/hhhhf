package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
	"golang.org/x/crypto/bcrypt"
)

// Redis dialling is the shared backend for access tokens, WebSocket tickets and
// refresh rotation. Production deployments must set REDIS_URL so tokens are
// shared across gateway instances.
var (
	// Redis dialling is guarded by a mutex rather than a sync.Once. A Once
	// caches the first failure permanently, which made every later login return
	// 503 even after Redis came back — an optional dependency outage took the
	// whole authentication system down. Failures are now retried after a
	// cooldown, and only a successful client is retained.
	redisMu        sync.Mutex
	redisClient    *redis.Client
	redisInitErr   error
	redisNextRetry time.Time
	// redisLastLogErr suppresses repeating the same degradation warning.
	redisLastLogErr string
)

// redisRetryCooldown bounds how often a failing Redis endpoint is re-dialled.
// Without it every request that touches the session store would pay the full
// go-redis dial timeout (5 attempts) before falling back.
const redisRetryCooldown = 5 * time.Second

// dummyPasswordHash is a valid bcrypt hash of a value no user can supply. Login
// compares against it when the username does not exist so that a missing
// account and a wrong password cost the same time, removing the timing oracle
// that would otherwise let an attacker enumerate valid usernames.
const dummyPasswordHash = "$2a$10$RNXM8JO52yFh70dqHvUUKuv4jjmRFOkaWzPRng7xamjoG0Azqcc7u"

// ValidateSessionStore enforces a shared session backend in production. The
// in-memory fallback is intentionally available only for local development.
func ValidateSessionStore() error {
	env := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
	if env != "production" && env != "prod" {
		return nil
	}
	if strings.TrimSpace(os.Getenv("REDIS_URL")) == "" {
		return fmt.Errorf("REDIS_URL is required when APP_ENV=production")
	}
	if _, err := sessionRedis(); err != nil {
		return fmt.Errorf("redis session store unavailable: %w", err)
	}
	return nil
}

// ValidateOriginAllowlist prevents a production process from silently falling
// back to development localhost origins.
func ValidateOriginAllowlist() error {
	env := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
	if env != "production" && env != "prod" {
		return nil
	}
	if strings.TrimSpace(os.Getenv("ALLOWED_ORIGINS")) == "" {
		return fmt.Errorf("ALLOWED_ORIGINS is required when APP_ENV=production")
	}
	for _, candidate := range strings.Split(os.Getenv("ALLOWED_ORIGINS"), ",") {
		if strings.TrimSpace(candidate) == "*" {
			return fmt.Errorf("ALLOWED_ORIGINS must not contain wildcard origins")
		}
	}
	return nil
}

// sessionRedis returns a live Redis client, dialling lazily on first use.
//
// A missing REDIS_URL is not an error: local development intentionally runs on
// the in-process session store. When REDIS_URL is configured but unreachable,
// dial attempts are throttled by redisRetryCooldown so a down Redis degrades
// into a fast failure instead of paying the dial timeout on every request.
func sessionRedis() (*redis.Client, error) {
	if strings.TrimSpace(os.Getenv("REDIS_URL")) == "" {
		return nil, nil
	}

	redisMu.Lock()
	defer redisMu.Unlock()

	if redisClient != nil {
		return redisClient, nil
	}
	if redisInitErr != nil && time.Now().Before(redisNextRetry) {
		return nil, redisInitErr
	}

	redisInitErr = nil
	options, err := redis.ParseURL(strings.TrimSpace(os.Getenv("REDIS_URL")))
	if err != nil {
		redisInitErr = err
		redisNextRetry = time.Now().Add(redisRetryCooldown)
		return nil, redisInitErr
	}

	client := redis.NewClient(options)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := client.Ping(ctx).Err(); err != nil {
		_ = client.Close()
		redisInitErr = err
		redisNextRetry = time.Now().Add(redisRetryCooldown)
		return nil, redisInitErr
	}

	redisClient = client
	redisNextRetry = time.Time{}
	return redisClient, nil
}

// redisSessionStore reports whether the shared Redis session store is the
// active backend. It returns nil when Redis is unconfigured OR currently
// unreachable, so callers transparently fall back to the in-process store
// instead of failing the request.
//
// Availability beats strict session sharing here: returning a hard error when
// Redis is merely down turned an optional dependency outage into a total login
// outage. A production deployment still requires Redis at startup via
// ValidateSessionStore, so a misconfigured environment cannot reach this path
// silently — only a runtime blip does.
func redisSessionStore() *redis.Client {
	if strings.TrimSpace(os.Getenv("REDIS_URL")) == "" {
		return nil
	}
	client, err := sessionRedis()
	if err != nil {
		// Log once per cooldown window rather than per request.
		redisMu.Lock()
		shouldLog := redisLastLogErr != err.Error()
		if shouldLog {
			redisLastLogErr = err.Error()
		}
		redisMu.Unlock()
		if shouldLog {
			log.Printf("会话存储降级: Redis 不可用，回退到进程内存会话（多副本将不共享会话）: %v", err)
		}
		return nil
	}
	redisMu.Lock()
	redisLastLogErr = ""
	redisMu.Unlock()
	return client
}

// LookupSessionUserID resolves a bearer token created by the auth handlers.
func LookupSessionUserID(token string) (string, bool) {
	userID, _, ok := LookupAccessSession(token)
	return userID, ok
}

func requestToken(c *gin.Context) string {
	header := strings.TrimSpace(c.GetHeader("Authorization"))
	if strings.HasPrefix(strings.ToLower(header), "bearer ") {
		return strings.TrimSpace(header[len("Bearer "):])
	}
	// Browser WebSocket clients cannot set arbitrary headers. Two channels are
	// accepted, and both only during an actual upgrade request (accepting either
	// on ordinary HTTP URLs would leak credentials through logs and referrers):
	//
	//   1. `Sec-WebSocket-Protocol: bearer.<token>` — preferred. The frontend
	//      sends the token as a subprotocol and we echo it back on the handshake.
	//   2. `?access_token=` — legacy fallback, kept so older clients keep working.
	if c.Request.Method == http.MethodGet && strings.EqualFold(c.GetHeader("Upgrade"), "websocket") {
		if token := websocketSubprotocolToken(c.Request); token != "" {
			return token
		}
		return strings.TrimSpace(c.Query("access_token"))
	}
	return ""
}

const websocketTokenPrefix = "bearer."

// websocketSubprotocolToken extracts the token from the `bearer.<token>`
// subprotocol the browser sends during a WebSocket handshake.
func websocketSubprotocolToken(r *http.Request) string {
	for _, protocol := range strings.Split(r.Header.Get("Sec-WebSocket-Protocol"), ",") {
		protocol = strings.TrimSpace(protocol)
		if len(protocol) <= len(websocketTokenPrefix) {
			continue
		}
		if strings.EqualFold(protocol[:len(websocketTokenPrefix)], websocketTokenPrefix) {
			if token := strings.TrimSpace(protocol[len(websocketTokenPrefix):]); token != "" {
				return token
			}
		}
	}
	return ""
}

// websocketSubprotocols returns the client-requested subprotocols in order, with
// the bearer token first. The upgrader must echo at least one of them: browsers
// reject a handshake whose requested subprotocol the server did not select.
func websocketSubprotocols(r *http.Request) []string {
	requested := strings.Split(r.Header.Get("Sec-WebSocket-Protocol"), ",")
	ordered := make([]string, 0, len(requested))
	tokenProtocol := ""
	for _, protocol := range requested {
		protocol = strings.TrimSpace(protocol)
		if protocol == "" {
			continue
		}
		if tokenProtocol == "" && len(protocol) >= len(websocketTokenPrefix) && strings.EqualFold(protocol[:len(websocketTokenPrefix)], websocketTokenPrefix) {
			tokenProtocol = protocol
			continue
		}
		ordered = append(ordered, protocol)
	}
	if tokenProtocol != "" {
		ordered = append([]string{tokenProtocol}, ordered...)
	}
	if len(ordered) > 8 {
		ordered = ordered[:8]
	}
	return ordered
}

// CurrentUserID returns the authenticated principal for the request.
func CurrentUserID(c *gin.Context) string {
	value, _ := c.Get("user_id")
	userID, _ := value.(string)
	return userID
}

// AuthMiddleware authenticates all protected API and WebSocket routes. It
// also rejects a client-supplied user_id/created_by that attempts to impersonate
// another user, while preserving the request body for downstream handlers.
//
// maxIdentityProbeBodyBytes 是身份探测允许缓冲的请求体上限（12MB）：
// 参考路线截图 base64 约 5.6MB，留足余量；超过就明确回 413，绝不截断。
const maxIdentityProbeBodyBytes = 12 << 20

func AuthMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		if origin := strings.TrimSpace(c.GetHeader("Origin")); origin != "" && !originAllowed(origin) {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "不受信任的来源", "code": "ORIGIN_FORBIDDEN"})
			return
		}
		userID, sessionID, ok := authenticateRequest(c)
		if !ok {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "未登录或登录已过期", "code": "AUTH_REQUIRED"})
			return
		}
		c.Set("user_id", userID)
		c.Set("session_id", sessionID)

		if supplied := strings.TrimSpace(c.Query("user_id")); supplied != "" && supplied != userID {
			logIdentityMismatch(c, supplied)
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "无权访问其他用户数据", "code": "USER_MISMATCH"})
			return
		}

		// Check common identity fields without binding the body twice in every
		// handler. Restore the bytes so Gin handlers can still call ShouldBindJSON.
		if c.Request.Body != nil && c.Request.ContentLength != 0 && c.Request.Method != http.MethodGet && c.Request.Method != http.MethodHead && strings.Contains(c.GetHeader("Content-Type"), "application/json") {
			// 真实缺口（2026-10-04 实测）：这里原来只读 2MB 然后用这段截断内容替换请求体，
			// 于是**任何超过 2MB 的 JSON 请求都会在 handler 眼里变成坏 JSON**，客户端收到
			// 一句"请求数据格式不正确" —— 参考路线截图（base64 后约 5.6MB）就是这么失败的。
			// 现在：绝不静默截断，超限直接如实回 413。
			if c.Request.ContentLength > maxIdentityProbeBodyBytes {
				c.AbortWithStatusJSON(http.StatusRequestEntityTooLarge, gin.H{
					"error": "请求体过大", "code": "PAYLOAD_TOO_LARGE",
					"max_bytes": maxIdentityProbeBodyBytes,
				})
				return
			}
			body, err := io.ReadAll(io.LimitReader(c.Request.Body, maxIdentityProbeBodyBytes+1))
			if err != nil {
				c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": "请求体读取失败", "code": "INVALID_BODY"})
				return
			}
			if int64(len(body)) > maxIdentityProbeBodyBytes {
				// ContentLength 可能是 -1（chunked），所以读完还要再判一次
				c.AbortWithStatusJSON(http.StatusRequestEntityTooLarge, gin.H{
					"error": "请求体过大", "code": "PAYLOAD_TOO_LARGE",
					"max_bytes": maxIdentityProbeBodyBytes,
				})
				return
			}
			c.Request.Body = io.NopCloser(bytes.NewReader(body))
			var fields map[string]interface{}
			if len(bytes.TrimSpace(body)) > 0 {
				if err := json.Unmarshal(body, &fields); err == nil {
					for _, key := range []string{"user_id", "created_by"} {
						if value, exists := fields[key]; exists {
							if supplied, isString := value.(string); isString && strings.TrimSpace(supplied) != "" && supplied != userID {
								logIdentityMismatch(c, supplied)
								c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "请求身份与登录用户不一致", "code": "USER_MISMATCH"})
								return
							}
						}
					}
				}
			}
		}
		c.Next()
	}
}

func originAllowed(origin string) bool {
	raw := os.Getenv("ALLOWED_ORIGINS")
	if raw == "" {
		env := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
		if env == "production" || env == "prod" {
			return false
		}
		raw = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001,http://localhost:3101,http://127.0.0.1:3101,http://localhost:3106,http://127.0.0.1:3106,http://localhost:3107,http://127.0.0.1:3107,http://localhost:3110,http://127.0.0.1:3110,http://localhost:3120,http://127.0.0.1:3120,http://localhost:3130,http://127.0.0.1:3130"
	}
	for _, candidate := range strings.Split(raw, ",") {
		if strings.TrimSpace(candidate) == origin {
			return true
		}
	}
	return false
}

// websocketOriginAllowed is used by both websocket upgraders as a second
// line of defense. Browser handshakes include Origin; non-browser clients may
// omit it, in which case bearer authentication remains the gate.
func websocketOriginAllowed(r *http.Request) bool {
	origin := strings.TrimSpace(r.Header.Get("Origin"))
	return origin == "" || originAllowed(origin)
}

// buildAuthUser 将数据库用户转换为前端期望的 user 对象。
func buildAuthUser(u models.User) gin.H {
	nickname := u.Nickname
	if nickname == "" {
		nickname = u.Username
	}
	avatarSeed := u.AvatarSeed
	if avatarSeed == "" {
		avatarSeed = u.ID
	}
	return gin.H{
		"id":         u.ID,
		"username":   u.Username,
		"nickname":   nickname,
		"avatarSeed": avatarSeed,
		"avatarUrl":  u.AvatarURL,
		"signature":  u.Signature,
	}
}

// RegisterHandler 用户注册：用户名 + 密码（可选昵称）
func RegisterHandler(c *gin.Context) {
	var req struct {
		Username string `json:"username" binding:"required"`
		Password string `json:"password" binding:"required,min=6"`
		Nickname string `json:"nickname"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "用户名或密码格式不正确（密码至少 6 位）", "code": "INVALID_REGISTRATION_REQUEST"})
		return
	}

	username := strings.TrimSpace(req.Username)
	if username == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "用户名不能为空", "code": "USERNAME_REQUIRED"})
		return
	}
	if len([]rune(username)) > 64 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "用户名过长", "code": "USERNAME_TOO_LONG"})
		return
	}

	var existing models.User
	if err := database.DB.Where("username = ?", username).First(&existing).Error; err == nil {
		c.JSON(http.StatusConflict, gin.H{"error": "用户名已被注册", "code": "USERNAME_TAKEN"})
		return
	}

	hash, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcrypt.DefaultCost)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "密码加密失败", "code": "PASSWORD_HASH_FAILED"})
		return
	}

	user := models.User{
		ID:           uuid.New().String(),
		Username:     username,
		PasswordHash: string(hash),
		Nickname:     strings.TrimSpace(req.Nickname),
		AvatarSeed:   username,
	}
	if err := database.DB.Create(&user).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "注册失败，请稍后再试", "code": "REGISTRATION_FAILED"})
		return
	}

	// Registration deliberately does not create a device session: the UI asks
	// the user to sign in afterwards, and minting a refresh cookie here would
	// silently log them in on the next page load.
	c.JSON(http.StatusOK, gin.H{
		"message": "注册成功",
		"user":    buildAuthUser(user),
	})
}

// LoginHandler 用户登录：校验用户名与密码
func LoginHandler(c *gin.Context) {
	var req struct {
		Username string `json:"username" binding:"required"`
		Password string `json:"password" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "请填写用户名与密码", "code": "INVALID_CREDENTIALS_REQUEST"})
		return
	}

	user, ok := verifyCredentials(c, strings.TrimSpace(req.Username), req.Password)
	if !ok {
		return
	}

	respondWithSession(c, "登录成功", user)
}

// verifyCredentials applies the rules shared by every password sign-in
// transport (browser cookie session and mobile token session): per-account
// lockout, timing-equalised unknown-username handling, failure accounting and
// auditing. It writes the failure response itself and returns ok=false, so each
// caller keeps its own success payload.
func verifyCredentials(c *gin.Context, username, password string) (models.User, bool) {
	var user models.User

	// Per-account lockout. Checked before any database or bcrypt work so a
	// locked account costs the attacker nothing and costs us nothing.
	if locked, remaining := authAccountLocked(username); locked {
		seconds := int(math.Ceil(remaining.Seconds()))
		if seconds < 1 {
			seconds = 1
		}
		// Audited: without this line a lockout is invisible, so neither the
		// account owner nor an operator can tell that someone is cycling 8
		// failures to keep a known account locked.
		logAccountLockout(c, username, seconds)
		c.Header("Retry-After", strconv.Itoa(seconds))
		c.JSON(http.StatusTooManyRequests, gin.H{
			"error": "该账号尝试次数过多，请稍后再试",
			"code":  "ACCOUNT_TEMPORARILY_LOCKED",
		})
		return models.User{}, false
	}

	if err := database.DB.Where("username = ?", username).First(&user).Error; err != nil {
		// Run a dummy comparison so an unknown username and a wrong password
		// take comparable time; otherwise response latency reveals which
		// accounts exist.
		_ = bcrypt.CompareHashAndPassword([]byte(dummyPasswordHash), []byte(password))
		recordAuthFailure(username)
		// Audited: this is the brute-force signal (unknown account).
		logLoginFailure(c, username, "INVALID_CREDENTIALS")
		c.JSON(http.StatusUnauthorized, gin.H{"error": "用户名或密码错误", "code": "INVALID_CREDENTIALS"})
		return models.User{}, false
	}

	if err := bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(password)); err != nil {
		recordAuthFailure(username)
		// Audited: brute-force signal against a real account.
		logLoginFailure(c, username, "INVALID_CREDENTIALS")
		c.JSON(http.StatusUnauthorized, gin.H{"error": "用户名或密码错误", "code": "INVALID_CREDENTIALS"})
		return models.User{}, false
	}

	clearAuthFailures(username)
	return user, true
}

// LogoutHandler revokes the current device session (access token + refresh
// token) and clears the refresh cookie. Idempotent, so clients can safely call
// it during cleanup or expiry recovery.
func LogoutHandler(c *gin.Context) {
	current := requestToken(c)
	_, sessionID, ok := LookupAccessSession(current)
	if ok {
		_ = revokeDeviceSession(CurrentUserID(c), sessionID)
	}
	revokeAccessToken(current)
	clearRefreshCookie(c)
	c.JSON(http.StatusOK, gin.H{"message": "已退出登录"})
}

// SessionID returns the device session bound to the current access token.
func SessionID(c *gin.Context) string {
	value, _ := c.Get("session_id")
	sessionID, _ := value.(string)
	return sessionID
}
