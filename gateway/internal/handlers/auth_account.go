package handlers

import (
	"bytes"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"golang.org/x/crypto/bcrypt"
	"gorm.io/gorm"
)

const passwordResetTTL = 30 * time.Minute

// RefreshHandler exchanges the HttpOnly refresh cookie for a new short-lived
// access token. The refresh token is rotated on every use, so a stolen copy is
// only useful until the legitimate client refreshes.
func RefreshHandler(c *gin.Context) {
	refreshToken := requestRefreshToken(c)
	if refreshToken == "" {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "登录状态已失效，请重新登录", "code": "REFRESH_REQUIRED"})
		return
	}
	accessToken, newRefresh, userID, err := rotateRefreshToken(refreshToken)
	if err != nil {
		clearRefreshCookie(c)
		c.JSON(http.StatusUnauthorized, gin.H{"error": "登录状态已失效，请重新登录", "code": "REFRESH_REQUIRED"})
		return
	}
	setRefreshCookie(c, newRefresh)
	c.Header("Cache-Control", "no-store")

	var user models.User
	if err := database.DB.Where("id = ?", userID).First(&user).Error; err != nil {
		revokeAccessToken(accessToken)
		clearRefreshCookie(c)
		c.JSON(http.StatusUnauthorized, gin.H{"error": "账号不存在，请重新登录", "code": "REFRESH_REQUIRED"})
		return
	}
	c.JSON(http.StatusOK, gin.H{
		"message":    "登录状态已续期",
		"token":      accessToken,
		"expires_in": int(accessTokenTTL.Seconds()),
		"user":       buildAuthUser(user),
	})
}

// IssueWSTicketHandler mints a single-use ticket for a WebSocket handshake.
// Clients must call this with a normal bearer token and then connect with
// `?ticket=...`, so the long-lived credential never appears in a URL.
func IssueWSTicketHandler(c *gin.Context) {
	ticket, err := issueWSTicket(CurrentUserID(c), SessionID(c))
	if err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "实时连接暂不可用", "code": "WS_TICKET_UNAVAILABLE"})
		return
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{"ticket": ticket, "expires_in": int(wsTicketTTL.Seconds())})
}

// LogoutAllHandler revokes every device session for the current user.
func LogoutAllHandler(c *gin.Context) {
	userID := CurrentUserID(c)
	if err := revokeAllDeviceSessions(userID, ""); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "退出所有设备失败，请稍后重试", "code": "LOGOUT_ALL_FAILED"})
		return
	}
	revokeAccessToken(requestToken(c))
	clearRefreshCookie(c)
	c.JSON(http.StatusOK, gin.H{"message": "已退出所有设备"})
}

// ListSessionsHandler lists this account's non-revoked, unexpired devices.
func ListSessionsHandler(c *gin.Context) {
	userID := CurrentUserID(c)
	currentSessionID := SessionID(c)
	var sessions []models.DeviceSession
	if err := database.DB.Where("user_id = ? AND revoked_at IS NULL AND expires_at > ?", userID, time.Now()).
		Order("last_used_at desc").Find(&sessions).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "会话列表读取失败", "code": "SESSION_LIST_FAILED"})
		return
	}
	type sessionView struct {
		ID         string    `json:"id"`
		UserAgent  string    `json:"user_agent,omitempty"`
		IP         string    `json:"ip,omitempty"`
		CreatedAt  time.Time `json:"created_at"`
		LastUsedAt time.Time `json:"last_used_at"`
		ExpiresAt  time.Time `json:"expires_at"`
		Current    bool      `json:"current"`
	}
	views := make([]sessionView, 0, len(sessions))
	for _, session := range sessions {
		views = append(views, sessionView{
			ID:         session.ID,
			UserAgent:  session.UserAgent,
			IP:         session.IP,
			CreatedAt:  session.CreatedAt,
			LastUsedAt: session.LastUsedAt,
			ExpiresAt:  session.ExpiresAt,
			Current:    session.ID == currentSessionID,
		})
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{"sessions": views})
}

// RevokeSessionHandler signs out one device by id.
func RevokeSessionHandler(c *gin.Context) {
	userID := CurrentUserID(c)
	target := strings.TrimSpace(c.Param("id"))
	if target == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少会话 ID", "code": "SESSION_ID_REQUIRED"})
		return
	}
	var session models.DeviceSession
	if err := database.DB.Where("id = ? AND user_id = ?", target, userID).First(&session).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "会话不存在", "code": "SESSION_NOT_FOUND"})
		return
	}
	if err := revokeDeviceSession(userID, target); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "退出设备失败", "code": "SESSION_REVOKE_FAILED"})
		return
	}
	if target == SessionID(c) {
		revokeAccessToken(requestToken(c))
		clearRefreshCookie(c)
	}
	c.JSON(http.StatusOK, gin.H{"message": "已退出该设备"})
}

// ChangePasswordHandler verifies the current password, sets a new one and
// revokes every other device so a stolen session cannot survive a password
// change.
func ChangePasswordHandler(c *gin.Context) {
	var req struct {
		CurrentPassword string `json:"current_password" binding:"required"`
		NewPassword     string `json:"new_password" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "请填写当前密码与新密码", "code": "INVALID_PASSWORD_CHANGE_REQUEST"})
		return
	}
	if len(req.NewPassword) < 6 || len(req.NewPassword) > 72 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "新密码长度需为 6-72 位", "code": "PASSWORD_LENGTH_INVALID"})
		return
	}
	userID := CurrentUserID(c)
	var user models.User
	if err := database.DB.Where("id = ?", userID).First(&user).Error; err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "账号不存在，请重新登录", "code": "AUTH_REQUIRED"})
		return
	}
	if bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(req.CurrentPassword)) != nil {
		securityEvent(c, secEventAuthLogin, secOutcomeFailure, user.Username, "INVALID_CURRENT_PASSWORD", nil)
		c.JSON(http.StatusUnauthorized, gin.H{"error": "当前密码不正确", "code": "INVALID_CURRENT_PASSWORD"})
		return
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(req.NewPassword), bcrypt.DefaultCost)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "密码加密失败", "code": "PASSWORD_HASH_FAILED"})
		return
	}
	if err := database.DB.Model(&models.User{}).Where("id = ?", userID).Update("password_hash", string(hash)).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "密码更新失败", "code": "PASSWORD_UPDATE_FAILED"})
		return
	}
	// Keep this device signed in; everything else must re-authenticate.
	if err := revokeAllDeviceSessions(userID, SessionID(c)); err != nil {
		log.Printf("密码修改后撤销其他设备失败: %v", err)
	}
	securityEvent(c, "auth_password_change", "success", user.Username, "", nil)
	c.JSON(http.StatusOK, gin.H{"message": "密码已修改，其他设备已退出登录"})
}

// ForgotPasswordHandler starts a reset. It never reveals whether an account
// exists, and it never pretends delivery succeeded: when no delivery channel is
// configured in production it returns an explicit 503 instead of a fake 200.
func ForgotPasswordHandler(c *gin.Context) {
	var req struct {
		Username string `json:"username" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "请填写用户名", "code": "USERNAME_REQUIRED"})
		return
	}
	username := strings.TrimSpace(req.Username)
	generic := gin.H{"message": "如果该账号存在，重置说明已发送"}

	webhook := strings.TrimSpace(os.Getenv("AUTH_RESET_WEBHOOK"))
	env := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
	production := env == "production" || env == "prod"
	if webhook == "" && production {
		c.JSON(http.StatusServiceUnavailable, gin.H{
			"error": "找回密码暂不可用：未配置重置通知通道",
			"code":  "RESET_DELIVERY_UNAVAILABLE",
		})
		return
	}

	var user models.User
	if err := database.DB.Where("username = ?", username).First(&user).Error; err != nil {
		// Same shape as success to avoid account enumeration.
		c.JSON(http.StatusOK, generic)
		return
	}
	token, err := randomToken(32)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "重置令牌生成失败", "code": "RESET_TOKEN_FAILED"})
		return
	}
	record := models.PasswordResetToken{
		ID:        uuid.New().String(),
		UserID:    user.ID,
		TokenHash: hashToken(token),
		ExpiresAt: time.Now().Add(passwordResetTTL),
		CreatedAt: time.Now(),
	}
	if err := database.DB.Create(&record).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "重置请求保存失败", "code": "RESET_TOKEN_FAILED"})
		return
	}
	securityEvent(c, "auth_password_reset_request", "success", user.Username, "", nil)

	if webhook != "" {
		payload, _ := json.Marshal(map[string]any{
			"username":   user.Username,
			"token":      token,
			"expires_at": record.ExpiresAt,
		})
		client := &http.Client{Timeout: 5 * time.Second}
		resp, err := client.Post(webhook, "application/json", bytes.NewReader(payload))
		if err != nil {
			c.JSON(http.StatusBadGateway, gin.H{"error": "重置说明发送失败，请稍后重试", "code": "RESET_DELIVERY_FAILED"})
			return
		}
		_ = resp.Body.Close()
		if resp.StatusCode >= 300 {
			c.JSON(http.StatusBadGateway, gin.H{"error": "重置说明发送失败，请稍后重试", "code": "RESET_DELIVERY_FAILED"})
			return
		}
		c.JSON(http.StatusOK, generic)
		return
	}

	// Development only: no mail/IM channel exists, so return the token directly
	// for local verification. Never reached in production (guarded above).
	c.JSON(http.StatusOK, gin.H{
		"message":     "开发环境：重置令牌已生成",
		"delivery":    "dev",
		"reset_token": token,
		"expires_in":  int(passwordResetTTL.Seconds()),
	})
}

// ResetPasswordHandler consumes a single-use reset token and revokes every
// session for that account.
func ResetPasswordHandler(c *gin.Context) {
	var req struct {
		Token       string `json:"token" binding:"required"`
		NewPassword string `json:"new_password" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "请填写重置令牌与新密码", "code": "INVALID_PASSWORD_RESET_REQUEST"})
		return
	}
	if len(req.NewPassword) < 6 || len(req.NewPassword) > 72 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "新密码长度需为 6-72 位", "code": "PASSWORD_LENGTH_INVALID"})
		return
	}
	var record models.PasswordResetToken
	if err := database.DB.Where("token_hash = ?", hashToken(req.Token)).First(&record).Error; err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "重置链接无效或已使用", "code": "RESET_TOKEN_INVALID"})
		return
	}
	if record.UsedAt != nil || time.Now().After(record.ExpiresAt) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "重置链接已过期，请重新申请", "code": "RESET_TOKEN_EXPIRED"})
		return
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(req.NewPassword), bcrypt.DefaultCost)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "密码加密失败", "code": "PASSWORD_HASH_FAILED"})
		return
	}
	if err := updatePasswordAndConsumeReset(record, string(hash)); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "密码重置失败，请稍后重试", "code": "PASSWORD_RESET_FAILED"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"message": "密码已重置，请使用新密码登录"})
}

// updatePasswordAndConsumeReset changes the password, burns the token and
// revokes every device in one transaction so no partial state can leave either
// the old password valid or the reset token reusable.
func updatePasswordAndConsumeReset(record models.PasswordResetToken, passwordHash string) error {
	return database.DB.Transaction(func(tx *gorm.DB) error {
		now := time.Now()
		if err := tx.Model(&models.User{}).Where("id = ?", record.UserID).
			Update("password_hash", passwordHash).Error; err != nil {
			return err
		}
		if err := tx.Model(&models.PasswordResetToken{}).Where("id = ?", record.ID).
			Update("used_at", now).Error; err != nil {
			return err
		}
		return tx.Model(&models.DeviceSession{}).
			Where("user_id = ? AND revoked_at IS NULL", record.UserID).
			Update("revoked_at", now).Error
	})
}
