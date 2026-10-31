package handlers

import (
	"net/http"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
)

// 移动端认证接口（Android 原生客户端）。
//
// 浏览器流程把 refresh token 放在 HttpOnly Cookie（见 auth_session.go）。原生
// 客户端没有可信的 Cookie 容器，Android 的推荐做法是：access token 只留内存，
// refresh token 用 Keystore 加密保存。所以这三个接口把 refresh token 每次交换
// 时通过响应体返回一次。与 Cookie 流程共享同一套安全属性：随机生成、服务端只
// 存 SHA-256 哈希、一次性、每次刷新轮换、绑定可撤销的设备会话行。

func mobileTokenResponse(accessToken, refreshToken string, user models.User) gin.H {
	return gin.H{
		"access_token":  accessToken,
		"refresh_token": refreshToken,
		"expires_in":    int(accessTokenTTL.Seconds()),
		"user":          buildAuthUser(user),
	}
}

// MobileLoginHandler 为原生客户端签发 access/refresh token 对。
// 失败分支与浏览器登录完全一致（账号锁定、等时比较、失败审计），由
// verifyCredentials 统一处理。
func MobileLoginHandler(c *gin.Context) {
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

	accessToken, refreshToken, _, err := createDeviceSession(c, user.ID)
	if err != nil {
		securityEvent(c, secEventAuthLogin, secOutcomeFailure, user.Username, "SESSION_STORE_UNAVAILABLE",
			map[string]any{"reason": err.Error(), "client": "mobile"})
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "认证服务暂不可用", "code": "SESSION_STORE_UNAVAILABLE"})
		return
	}

	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, mobileTokenResponse(accessToken, refreshToken, user))
}

// MobileRefreshHandler 用请求体里的 refresh token 换取新的一对令牌。
// 轮换是单次使用：旧 token 在成功刷新后立即失效，被盗副本只有在合法客户端
// 下一次刷新之前有用。
func MobileRefreshHandler(c *gin.Context) {
	refreshToken := mobileRefreshTokenFrom(c)
	if refreshToken == "" {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "登录状态已失效，请重新登录", "code": "REFRESH_REQUIRED"})
		return
	}

	accessToken, newRefreshToken, userID, err := rotateRefreshToken(refreshToken)
	if err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "登录状态已失效，请重新登录", "code": "REFRESH_REQUIRED"})
		return
	}

	var user models.User
	if err := database.DB.Where("id = ?", userID).First(&user).Error; err != nil {
		revokeAccessToken(accessToken)
		c.JSON(http.StatusUnauthorized, gin.H{"error": "账号不存在，请重新登录", "code": "REFRESH_REQUIRED"})
		return
	}

	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, mobileTokenResponse(accessToken, newRefreshToken, user))
}

// MobileLogoutHandler 撤销当前设备会话。有两种凭据都能触发撤销，任一即可：
//   - Authorization: Bearer <access token>（可直接定位设备会话行）；
//   - 请求体 {"refresh_token": "..."}，用于 access token 已过期时仍能退出
//     登录——移动端没有 Cookie 可清除，否则本地令牌失效了服务端会话还活着。
//
// 未提供任何凭据时也返回 200：接口幂等，客户端清理本地状态时无需关心重试。
func MobileLogoutHandler(c *gin.Context) {
	var body struct {
		RefreshToken string `json:"refresh_token"`
	}
	if c.Request.Body != nil {
		_ = c.ShouldBindJSON(&body)
	}

	if token := requestToken(c); token != "" {
		if userID, sessionID, ok := LookupAccessSession(token); ok {
			_ = revokeDeviceSession(userID, sessionID)
		}
		revokeAccessToken(token)
	}

	if refreshToken := strings.TrimSpace(body.RefreshToken); refreshToken != "" {
		revokeDeviceSessionByRefreshToken(refreshToken)
	}

	c.JSON(http.StatusOK, gin.H{"message": "已退出登录"})
}

func mobileRefreshTokenFrom(c *gin.Context) string {
	var body struct {
		RefreshToken string `json:"refresh_token"`
	}
	if c.Request.Body != nil {
		_ = c.ShouldBindJSON(&body)
	}
	return strings.TrimSpace(body.RefreshToken)
}

// revokeDeviceSessionByRefreshToken 只按哈希匹配，不打印、不缓存明文令牌。
func revokeDeviceSessionByRefreshToken(refreshToken string) {
	refreshToken = strings.TrimSpace(refreshToken)
	if refreshToken == "" {
		return
	}
	_ = database.DB.Model(&models.DeviceSession{}).
		Where("refresh_token_hash = ? AND revoked_at IS NULL", hashToken(refreshToken)).
		Update("revoked_at", time.Now()).Error
}
