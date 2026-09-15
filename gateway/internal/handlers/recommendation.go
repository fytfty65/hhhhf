package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/metrics"
	"gateway/internal/models"
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
)

// postBanditFeedback 将行为信号异步回传 AI 服务（/agent/bandit/feedback），
// 打通「真实行为 → bandit 学习状态」的闭环。fire-and-forget，不阻塞主响应。
// An empty arm id is not silently ignored: it means the client never received an
// arm to attribute feedback to, which is exactly how the learning loop was
// broken before (the satisfaction form posted an empty bandit_arm_id, this
// function returned immediately, and the policy silently accumulated nothing).
// It is now counted and logged so the gap is visible in production.
func postBanditFeedback(armID string, signal map[string]any) {
	armID = strings.TrimSpace(armID)
	if armID == "" {
		metrics.RecordBanditFeedback(metrics.OutcomeMissingArm)
		if total := metrics.MissingArmCount(); total <= 5 || total%100 == 0 {
			log.Printf("[bandit-feedback] 丢弃一次学习信号：arm_id 为空（累计 %d 次）。这表示客户端未回传 bandit arm，学习闭环未生效", total)
		}
		return
	}
	metrics.RecordBanditFeedback(metrics.OutcomeSent)
	go func() {
		body := map[string]any{"arm_id": armID}
		for k, v := range signal {
			body[k] = v
		}
		payload, err := json.Marshal(body)
		if err != nil {
			log.Printf("[bandit-feedback] marshal error: %v", err)
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, aiServiceBaseURL()+"/api/v1/agent/bandit/feedback", bytes.NewReader(payload))
		if err != nil {
			log.Printf("[bandit-feedback] build request error: %v", err)
			return
		}
		req.Header.Set("Content-Type", "application/json")
		if token := strings.TrimSpace(os.Getenv("AI_SERVICE_INTERNAL_TOKEN")); token != "" {
			req.Header.Set("X-Omni-Internal-Token", token)
		}
		resp, err := (&http.Client{Timeout: 5 * time.Second}).Do(req)
		if err != nil {
			log.Printf("[bandit-feedback] post error: %v", err)
			return
		}
		defer resp.Body.Close()
		if resp.StatusCode >= 300 {
			// A rejected reward is a real signal that the arm is unknown, so it
			// must not look like success in the logs.
			detail, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
			metrics.RecordBanditFeedback(metrics.OutcomeRejected)
			log.Printf("[bandit-feedback] AI 服务拒绝该奖励 arm=%s status=%d body=%s", armID, resp.StatusCode, strings.TrimSpace(string(detail)))
			return
		}
		metrics.RecordBanditFeedback(metrics.OutcomeAccepted)
		_, _ = io.Copy(io.Discard, resp.Body)
	}()
}

// GetUserPreferenceHandler 获取用户旅行画像（前端「我的画像」页数据源）。
func GetUserPreferenceHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
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
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	req.UserID = userID

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
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	req.UserID = userID

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

// recommendationAccuracy 以「用户正反馈 = 推荐命中」近似评估推荐准确率（=85% 目标）。
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

// RecordSwapSignalHandler 记录「替换景点」偏好信号：被替换景点记为负向、替换为正。
func RecordSwapSignalHandler(c *gin.Context) {
	var req struct {
		UserID         string `json:"user_id" binding:"required"`
		RoomID         string `json:"room_id"`
		ReplacedTarget string `json:"replaced_target" binding:"required"`
		Replacement    string `json:"replacement"`
		Reason         string `json:"reason"`
		BanditArmID    string `json:"bandit_arm_id"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	req.UserID = userID
	if req.RoomID != "" {
		if _, ok := requireRoomMember(c, req.RoomID); !ok {
			return
		}
	}

	reason := req.Reason
	if reason == "" {
		reason = "用户替换该景点"
	}

	// 被替换景点 = 负向偏好信号
	database.DB.Create(&models.FeedbackLog{
		RoomID: req.RoomID,
		UserID: req.UserID,
		Target: req.ReplacedTarget,
		Score:  -1,
		Reason: reason,
	})

	// 若有明确替换目标，记为正向偏好信号
	if req.Replacement != "" {
		database.DB.Create(&models.FeedbackLog{
			RoomID: req.RoomID,
			UserID: req.UserID,
			Target: req.Replacement,
			Score:  1,
			Reason: "用户选择的替换景点",
		})
	}

	// 回传 bandit 学习信号：替换动作 = 弱负反馈（有替换目标记 swapped，无则 rejected）
	swapSignal := "swapped"
	if req.Replacement == "" {
		swapSignal = "rejected"
	}
	postBanditFeedback(req.BanditArmID, map[string]any{"signal": swapSignal})

	// 采集后立即重聚合画像，保证推荐随行为实时进化
	if profile, err := service.RefreshProfile(req.UserID); err == nil {
		c.JSON(http.StatusOK, gin.H{
			"message": "替换信号已记录，画像已更新",
			"profile": service.ProfilePayload(req.UserID),
			"variant": profile.ABVariant,
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "替换信号已记录"})
}

// SubmitSatisfactionHandler 行程完成后提交满意度问卷（1-5 星），显式反馈来源。
func SubmitSatisfactionHandler(c *gin.Context) {
	var req struct {
		// User identity is taken from the authenticated token below. Keep this
		// field optional for legacy clients; requiring a client-supplied ID made
		// valid authenticated submissions fail with "参数不完整".
		UserID      string `json:"user_id"`
		TripID      string `json:"trip_id"`
		Score       int    `json:"score" binding:"required"`
		Comment     string `json:"comment"`
		BanditArmID string `json:"bandit_arm_id"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	req.UserID = userID
	if _, ok := requireOptionalTripOrRoomAccess(c, req.TripID); !ok {
		return
	}
	if req.Score < 1 || req.Score > 5 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "评分仅支持 1~5 星"})
		return
	}
	if len([]rune(req.Comment)) > 1000 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "评价内容过长"})
		return
	}

	sat := models.TripSatisfaction{
		UserID:  req.UserID,
		TripID:  req.TripID,
		Score:   req.Score,
		Comment: req.Comment,
	}
	if err := database.DB.Create(&sat).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "问卷提交失败"})
		return
	}

	// 回传 bandit 学习信号：满意度（1-5）→ 显式奖励 = score/5
	postBanditFeedback(req.BanditArmID, map[string]any{"satisfaction": req.Score})

	// P6：满意度是显式反馈，提交后立即重聚合画像（高分/低分分别强化/弱化偏好）
	profile := service.ProfilePayload(req.UserID)
	if _, err := service.RefreshProfile(req.UserID); err == nil {
		profile = service.ProfilePayload(req.UserID)
	}

	c.JSON(http.StatusOK, gin.H{"message": "满意度已提交", "score": sat.Score, "profile": profile})
}

// CollaborativeRecommendHandler 基于协同过滤给用户推荐景点（相似用户喜欢但本人未评分的）。
func CollaborativeRecommendHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}

	// 从正反馈样本构建用户-物品评分矩阵（仅正的点赞行为参与相似度计算）
	var logs []models.FeedbackLog
	database.DB.Where("score >= ?", 1).Find(&logs)

	rows := make([]service.RatingRow, 0, len(logs))
	for _, l := range logs {
		if l.Target == "" {
			continue
		}
		rows = append(rows, service.RatingRow{UserID: l.UserID, ItemID: l.Target, Score: 1})
	}

	matrix := service.BuildRatingMatrix(rows)
	items := service.RecommendItems(userID, matrix, 10)

	if items == nil {
		items = []string{}
	}
	similar := service.TopSimilarUsers(userID, matrix, 5)
	if similar == nil {
		similar = []string{}
	}

	c.JSON(http.StatusOK, gin.H{
		"recommendations": items,
		"similar_users":   similar,
	})
}

// FunnelMetricsHandler 埋点 A/B 漏斗：曝光 → 点击 → 采纳 → 满意，各阶段独立用户数与转化率。
func FunnelMetricsHandler(c *gin.Context) {
	// Every RecommendationEvent row is an exposure, so distinct users over the
	// whole table are the funnel's top stage; the narrower stages are then
	// subsets of it.
	impression := distinctUsers(&models.RecommendationEvent{}, "")
	click := distinctUsers(&models.RecommendationEvent{}, "clicked = ?", true)
	adopt := distinctUsers(&models.TripPlan{}, "")
	satisfied := distinctUsers(&models.TripSatisfaction{}, "score >= ?", 4)

	c.JSON(http.StatusOK, map[string]interface{}{
		"funnel": []map[string]interface{}{
			{"stage": "impression", "label": "曝光", "users": impression},
			{"stage": "click", "label": "点击", "users": click},
			{"stage": "adopt", "label": "采纳", "users": adopt},
			{"stage": "satisfied", "label": "满意", "users": satisfied},
		},
		"conversion": map[string]interface{}{
			"impression_to_click": conversionRatio(click, impression),
			"click_to_adopt":      conversionRatio(adopt, click),
			"adopt_to_satisfied":  conversionRatio(satisfied, adopt),
		},
	})
}

// distinctUsers 统计某模型满足条件后的独立用户数（按 user_id 去重）。
func distinctUsers(model interface{}, where string, args ...interface{}) int64 {
	query := database.DB.Model(model)
	if where != "" {
		query = query.Where(where, args...)
	}
	var n int64
	query.Distinct("user_id").Count(&n)
	return n
}

// conversionRatio 计算漏斗相邻阶段转化率（分母为 0 时返回 0，避免除零）。
func conversionRatio(numerator, denominator int64) float64 {
	if denominator == 0 {
		return 0
	}
	return roundRatio(float64(numerator) / float64(denominator))
}
