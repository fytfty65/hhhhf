package handlers

import (
	"net/http"
	"strings"

	"gateway/internal/push"
	"github.com/gin-gonic/gin"
)

// FCM Web Push 的设备 token 登记（B 方案）。
//
// 与既有的 `push_subscription.go`（原生 Web Push 的 endpoint+p256dh+auth）**并存**：
// 既有那张表与接口保持不动（避免破坏现有前端与测试），这里新增的是 FCM 这条运输层。
// 等前端切换完成后，原生那条可以标记为 legacy 下线 —— 这是有意为之的迁移路径，
// 而不是"两套并行长期共存"。
type fcmTokenRequest struct {
	Token    string `json:"token"`
	Platform string `json:"platform"`
}

// RegisterFCMTokenHandler 保存/刷新当前用户的 FCM 设备 token。
func RegisterFCMTokenHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	var body fcmTokenRequest
	if err := c.ShouldBindJSON(&body); err != nil {
		planningError(c, http.StatusBadRequest, "PUSH_TOKEN_INVALID", "推送凭据格式不对", nil)
		return
	}
	token := strings.TrimSpace(body.Token)
	if token == "" {
		planningError(c, http.StatusBadRequest, "PUSH_TOKEN_INVALID", "缺少推送凭据", nil)
		return
	}
	platform := strings.TrimSpace(body.Platform)
	if platform == "" {
		platform = strings.TrimSpace(c.GetHeader("User-Agent"))
		if len(platform) > 32 {
			platform = platform[:32]
		}
	}
	if err := push.RegisterDevice(userID, token, platform); err != nil {
		planningError(c, http.StatusInternalServerError, "PUSH_TOKEN_SAVE_FAILED", "推送凭据保存失败", nil)
		return
	}
	planningData(c, gin.H{"subscribed": true, "channel": "fcm"})
}

// UnregisterFCMTokenHandler 注销当前用户的一台设备（关开关/退出登录时调用）。
func UnregisterFCMTokenHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	token := strings.TrimSpace(c.Query("token"))
	if token == "" {
		var body fcmTokenRequest
		if err := c.ShouldBindJSON(&body); err == nil {
			token = strings.TrimSpace(body.Token)
		}
	}
	if token == "" {
		planningError(c, http.StatusBadRequest, "PUSH_TOKEN_INVALID", "缺少要注销的推送凭据", nil)
		return
	}
	if err := push.UnregisterDevice(userID, token); err != nil {
		planningError(c, http.StatusInternalServerError, "PUSH_TOKEN_DELETE_FAILED", "推送凭据注销失败", nil)
		return
	}
	planningData(c, gin.H{"subscribed": false, "channel": "fcm"})
}
