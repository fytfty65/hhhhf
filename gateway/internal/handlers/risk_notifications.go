package handlers

import (
	"net/http"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"github.com/gin-gonic/gin"
)

// ListRiskNotificationsHandler returns the caller's in-app risk inbox. It is
// intentionally separate from supplier refresh so a browser can reopen a
// notification after a transient network failure.
func ListRiskNotificationsHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	query := database.DB.Where("user_id = ?", userID).Order("created_at desc").Limit(100)
	if status := strings.TrimSpace(c.Query("status")); status != "" {
		if status != "unread" && status != "read" && status != "acknowledged" {
			planningError(c, http.StatusBadRequest, "NOTIFICATION_STATUS_INVALID", "通知状态无效", nil)
			return
		}
		query = query.Where("status = ?", status)
	}
	var notifications []models.RiskNotification
	if err := query.Find(&notifications).Error; err != nil {
		planningError(c, http.StatusInternalServerError, "NOTIFICATION_QUERY_FAILED", "风险通知查询失败", nil)
		return
	}
	var unread int64
	database.DB.Model(&models.RiskNotification{}).Where("user_id = ? AND status = ?", userID, "unread").Count(&unread)
	planningData(c, gin.H{"notifications": notifications, "unread_count": unread, "delivery": "in_app", "provider_policy": "source_and_estimated_are_preserved"})
}

// MarkAllRiskNotificationsReadHandler marks every unread notification of the
// caller as read in one update, so the client does not need N requests.
func MarkAllRiskNotificationsReadHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	status := strings.TrimSpace(c.Query("status"))
	if status == "" {
		status = "read"
	}
	if status != "read" && status != "acknowledged" {
		planningError(c, http.StatusBadRequest, "NOTIFICATION_STATUS_INVALID", "通知状态只能为 read 或 acknowledged", nil)
		return
	}
	now := time.Now().UTC()
	result := database.DB.Model(&models.RiskNotification{}).
		Where("user_id = ? AND status = ?", userID, "unread").
		Updates(map[string]any{"status": status, "read_at": now})
	if result.Error != nil {
		planningError(c, http.StatusInternalServerError, "NOTIFICATION_UPDATE_FAILED", "风险通知状态更新失败", nil)
		return
	}
	planningData(c, gin.H{"updated": result.RowsAffected, "status": status})
}

// AcknowledgeRiskNotificationHandler marks one notification as read or
// acknowledged. Ownership is checked through the authenticated user ID.
func AcknowledgeRiskNotificationHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	status := strings.TrimSpace(c.Query("status"))
	if status == "" {
		status = "read"
	}
	if status != "read" && status != "acknowledged" {
		planningError(c, http.StatusBadRequest, "NOTIFICATION_STATUS_INVALID", "通知状态只能为 read 或 acknowledged", nil)
		return
	}
	var notification models.RiskNotification
	if err := database.DB.Where("id = ? AND user_id = ?", strings.TrimSpace(c.Param("id")), userID).First(&notification).Error; err != nil {
		planningError(c, http.StatusNotFound, "NOTIFICATION_NOT_FOUND", "风险通知不存在", nil)
		return
	}
	now := time.Now().UTC()
	if err := database.DB.Model(&notification).Updates(map[string]any{"status": status, "read_at": now}).Error; err != nil {
		planningError(c, http.StatusInternalServerError, "NOTIFICATION_UPDATE_FAILED", "风险通知状态更新失败", nil)
		return
	}
	notification.Status = status
	notification.ReadAt = &now
	planningData(c, gin.H{"notification": notification})
}
