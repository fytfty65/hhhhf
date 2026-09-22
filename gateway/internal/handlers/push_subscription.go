package handlers

import (
	"net/http"
	"net/url"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

type pushSubscriptionRequest struct {
	Endpoint string `json:"endpoint"`
	Keys     struct {
		P256DH string `json:"p256dh"`
		Auth   string `json:"auth"`
	} `json:"keys"`
	UserAgent string `json:"user_agent"`
}

// GetPushSubscriptionStatusHandler returns capability/status only. It never
// returns endpoint or browser encryption keys.
func GetPushSubscriptionStatusHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	var count int64
	if err := database.DB.Model(&models.WebPushSubscription{}).Where("user_id = ?", userID).Count(&count).Error; err != nil {
		planningError(c, http.StatusInternalServerError, "PUSH_STATUS_QUERY_FAILED", "通知订阅状态读取失败", nil)
		return
	}
	planningData(c, gin.H{"subscribed": count > 0, "subscription_count": count, "delivery": "web_push_requires_explicit_consent"})
}

// SavePushSubscriptionHandler persists a browser subscription after explicit
// permission. The server stores only the minimum fields needed by a future
// push worker and does not echo the credentials.
func SavePushSubscriptionHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	var req pushSubscriptionRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_JSON", "通知订阅数据格式不正确", nil)
		return
	}
	endpoint := strings.TrimSpace(req.Endpoint)
	parsed, err := url.Parse(endpoint)
	if err != nil || (parsed.Scheme != "https" && parsed.Scheme != "http") || parsed.Host == "" || len(endpoint) > 2048 {
		planningError(c, http.StatusBadRequest, "PUSH_ENDPOINT_INVALID", "通知订阅地址无效", nil)
		return
	}
	p256dh := strings.TrimSpace(req.Keys.P256DH)
	auth := strings.TrimSpace(req.Keys.Auth)
	if len(p256dh) < 16 || len(p256dh) > 512 || len(auth) < 8 || len(auth) > 512 {
		planningError(c, http.StatusBadRequest, "PUSH_KEYS_INVALID", "通知订阅密钥不完整", nil)
		return
	}
	var existing models.WebPushSubscription
	queryErr := database.DB.Where("endpoint = ?", endpoint).First(&existing).Error
	now := time.Now().UTC()
	if queryErr == nil {
		if existing.UserID != userID {
			planningError(c, http.StatusConflict, "PUSH_ENDPOINT_OWNED", "该浏览器订阅已绑定其他账号", nil)
			return
		}
		existing.P256DH, existing.Auth, existing.UserAgent, existing.UpdatedAt = p256dh, auth, truncateRunes(strings.TrimSpace(req.UserAgent), 300), now
		if err := database.DB.Save(&existing).Error; err != nil {
			planningError(c, http.StatusInternalServerError, "PUSH_SUBSCRIPTION_SAVE_FAILED", "通知订阅保存失败", nil)
			return
		}
	} else {
		existing = models.WebPushSubscription{ID: uuid.NewString(), UserID: userID, Endpoint: endpoint, P256DH: p256dh, Auth: auth, UserAgent: truncateRunes(strings.TrimSpace(req.UserAgent), 300), CreatedAt: now, UpdatedAt: now}
		if err := database.DB.Create(&existing).Error; err != nil {
			planningError(c, http.StatusInternalServerError, "PUSH_SUBSCRIPTION_SAVE_FAILED", "通知订阅保存失败", nil)
			return
		}
	}
	planningData(c, gin.H{"subscribed": true, "subscription_id": existing.ID, "delivery": "web_push_registered"})
}

func DeletePushSubscriptionHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	endpoint := strings.TrimSpace(c.Query("endpoint"))
	query := database.DB.Where("user_id = ?", userID)
	if endpoint != "" {
		query = query.Where("endpoint = ?", endpoint)
	}
	if err := query.Delete(&models.WebPushSubscription{}).Error; err != nil {
		planningError(c, http.StatusInternalServerError, "PUSH_SUBSCRIPTION_DELETE_FAILED", "通知订阅取消失败", nil)
		return
	}
	planningData(c, gin.H{"subscribed": false, "delivery": "in_app"})
}
