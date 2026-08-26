package handlers

import (
	"net/http"

	"gateway/internal/database"
	"gateway/internal/models"
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
)

// GetUserPreferenceHandler 获取用户旅行画像（前端「我的画像」页数据源）。
func GetUserPreferenceHandler(c *gin.Context) {
	userID := c.Query("user_id")
	if userID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少 user_id"})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"profile": service.ProfilePayload(userID),
	})
}

// RefreshUserPreferenceHandler 手动触发画像重聚合（行为数据实时采集后动态更新）。
func RefreshUserPreferenceHandler(c *gin.Context) {
	var req struct {
		UserID string `json:"user_id" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少 user_id"})
		return
	}

	profile, err := service.RefreshProfile(req.UserID)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "画像更新失败"})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"message": "画像已更新",
		"profile": service.ProfilePayload(req.UserID),
		"variant": profile.ABVariant,
	})
}

// TrackRecommendationEventHandler 记录推荐曝光/点击埋点，支撑 A/B 实验效果评估与准确率验证。
func TrackRecommendationEventHandler(c *gin.Context) {
	var req struct {
		UserID  string `json:"user_id" binding:"required"`
		Variant string `json:"variant" binding:"required"`
		Target  string `json:"target"`
		Clicked bool   `json:"clicked"`
		Source  string `json:"source"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}

	event := models.RecommendationEvent{
		UserID:  req.UserID,
		Variant: req.Variant,
		Target:  req.Target,
		Clicked: req.Clicked,
		Source:  req.Source,
	}
	if err := database.DB.Create(&event).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "埋点记录失败"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "埋点已记录"})
}

// RecommendationMetricsHandler 聚合 A/B 分组点击率与推荐准确率（供持续优化推荐效果）。
func RecommendationMetricsHandler(c *gin.Context) {
	metrics := map[string]interface{}{
		"control":   variantCTR("control"),
		"treatment": variantCTR("treatment"),
		"accuracy":  recommendationAccuracy(),
	}
	c.JSON(http.StatusOK, metrics)
}

// variantCTR 计算分组点击率（点击次数 / 曝光次数）。
func variantCTR(variant string) map[string]interface{} {
	var total, clicks int64
	database.DB.Model(&models.RecommendationEvent{}).Where("variant = ?", variant).Count(&total)
	database.DB.Model(&models.RecommendationEvent{}).Where("variant = ? AND clicked = ?", variant, true).Count(&clicks)

	ctr := 0.0
	if total > 0 {
		ctr = roundRatio(float64(clicks) / float64(total))
	}
	return map[string]interface{}{
		"impressions": total,
		"clicks":      clicks,
		"ctr":         ctr,
	}
}

// recommendationAccuracy 以「用户正反馈 = 推荐命中」近似评估推荐准确率（>=85% 目标）。
func recommendationAccuracy() map[string]interface{} {
	var liked, total int64
	database.DB.Model(&models.FeedbackLog{}).Where("score >= 1").Count(&liked)
	database.DB.Model(&models.FeedbackLog{}).Count(&total)

	acc := 0.0
	if total > 0 {
		acc = roundRatio(float64(liked) / float64(total))
	}
	return map[string]interface{}{
		"liked_samples": liked,
		"total_samples": total,
		"accuracy":      acc,
	}
}

func roundRatio(v float64) float64 {
	return float64(int(v*10000+0.5)) / 10000
}