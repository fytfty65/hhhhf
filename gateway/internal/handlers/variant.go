package handlers

import (
	"net/http"
	"sort"
	"strings"

	"gateway/internal/database"
	"gateway/internal/models"
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

// SaveVariantHandler 保存一套行程方案到房间（多方案对比池），并实时广播给房间成员。
func SaveVariantHandler(c *gin.Context) {
	var req struct {
		RoomID    string `json:"room_id" binding:"required"`
		Name      string `json:"name" binding:"required"`
		Style     string `json:"style"`
		Route     string `json:"route" binding:"required"`
		CreatedBy string `json:"created_by"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := requireRoomMember(c, req.RoomID)
	if !ok {
		return
	}

	variant := models.PlanVariant{
		ID:     uuid.New().String(),
		RoomID: req.RoomID,
		Name:   req.Name,
		Style:  req.Style,
		Route:  req.Route,
		// Never trust a caller-supplied creator; the authenticated principal is
		// the only identity allowed to author a room variant.
		CreatedBy: userID,
	}
	if err := database.DB.Create(&variant).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "方案保存失败"})
		return
	}

	if GlobalHub != nil {
		GlobalHub.Broadcast <- models.WSMessage{
			Type:   "plan_variant_sync",
			RoomID: req.RoomID,
			UserID: userID,
			Payload: gin.H{
				"action":  "add",
				"variant": variantBrief(variant),
			},
		}
	}

	c.JSON(http.StatusOK, gin.H{"message": "方案已保存", "variant": variantBrief(variant)})
}

// ListVariantsHandler 拉取某房间的全部方案（含得票数与胜出标记）。
func ListVariantsHandler(c *gin.Context) {
	roomID := c.Query("room_id")
	if roomID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少 room_id"})
		return
	}
	if _, ok := requireRoomMember(c, roomID); !ok {
		return
	}

	variants, votes := loadRoomVariants(roomID)
	tally := service.TallyVotes(votes)
	winner, _, winnerFound := service.WinningVariant(tally)

	result := make([]gin.H, 0, len(variants))
	for _, v := range variants {
		result = append(result, variantBriefWith(v, tally[v.ID], winnerFound && winner == v.ID))
	}

	// 按得票降序返回，便于前端直接展示热门方案
	sort.SliceStable(result, func(i, j int) bool {
		return result[i]["votes"].(int) > result[j]["votes"].(int)
	})

	c.JSON(http.StatusOK, gin.H{"variants": result, "winner_id": winner, "has_winner": winnerFound})
}

// VoteVariantHandler 成员为方案投票（同一房间内一人一票，后票覆盖前票）。
func VoteVariantHandler(c *gin.Context) {
	var req struct {
		VariantID string `json:"variant_id" binding:"required"`
		UserID    string `json:"user_id" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}

	var variant models.PlanVariant
	if err := database.DB.Where("id = ?", req.VariantID).First(&variant).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "方案不存在"})
		return
	}
	if _, ok := requireRoomMember(c, variant.RoomID); !ok {
		return
	}
	req.UserID = userID

	// 一人一票：清除该用户在此前投给同房间其他方案的票
	var roomVariantIDs []string
	database.DB.Model(&models.PlanVariant{}).Where("room_id = ?", variant.RoomID).Pluck("id", &roomVariantIDs)
	database.DB.Where("user_id = ? AND variant_id IN ?", req.UserID, roomVariantIDs).Delete(&models.PlanVariantVote{})

	if err := database.DB.Create(&models.PlanVariantVote{
		VariantID: req.VariantID,
		UserID:    req.UserID,
	}).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "投票失败"})
		return
	}

	// 广播最新计票结果（保证多端一致）
	if GlobalHub != nil {
		_, votes := loadRoomVariants(variant.RoomID)
		tally := service.TallyVotes(votes)
		GlobalHub.Broadcast <- models.WSMessage{
			Type:   "plan_variant_sync",
			RoomID: variant.RoomID,
			UserID: req.UserID,
			Payload: gin.H{
				"action":     "vote",
				"variant_id": req.VariantID,
				"tally":      tally,
			},
		}
	}

	c.JSON(http.StatusOK, gin.H{"message": "投票成功"})
}

// WinningVariantHandler 返回房间内得票最高的方案（含完整路线 JSON，供前端一键采纳）。
func WinningVariantHandler(c *gin.Context) {
	roomID := c.Query("room_id")
	if roomID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少 room_id"})
		return
	}
	if _, ok := requireRoomMember(c, roomID); !ok {
		return
	}

	variants, votes := loadRoomVariants(roomID)
	tally := service.TallyVotes(votes)
	winnerID, winnerVotes, found := service.WinningVariant(tally)
	if !found {
		c.JSON(http.StatusOK, gin.H{"has_winner": false, "tally": tally})
		return
	}

	for _, v := range variants {
		if v.ID == winnerID {
			c.JSON(http.StatusOK, gin.H{
				"has_winner": true,
				"variant":    variantBriefWith(v, winnerVotes, true),
				"tally":      tally,
			})
			return
		}
	}
	c.JSON(http.StatusOK, gin.H{"has_winner": false, "tally": tally})
}

// AdoptVariantHandler 采纳某方案为最终行程，并根据方案路线自动建立预算基线（打通「采纳→费用追踪」闭环）。
// 预算基线由 service.EstimateRouteBudget 从路线节点费用字段累加估算，作为后续消费记录的对比基准。
func AdoptVariantHandler(c *gin.Context) {
	var req struct {
		VariantID string `json:"variant_id" binding:"required"`
		UserID    string `json:"user_id" binding:"required"`
		TripID    string `json:"trip_id"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}

	var variant models.PlanVariant
	if err := database.DB.Where("id = ?", req.VariantID).First(&variant).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "方案不存在"})
		return
	}
	if _, ok := requireRoomMember(c, variant.RoomID); !ok {
		return
	}
	req.UserID = userID
	if strings.TrimSpace(req.TripID) != "" && strings.TrimSpace(req.TripID) != variant.RoomID {
		if _, ok := requireTripOwner(c, req.TripID); !ok {
			return
		}
	}

	tripID := req.TripID
	if tripID == "" {
		tripID = variant.RoomID
	}

	// 从路线节点费用字段估算预算基线（无法解析时降级为 0，不影响主流程）
	estimated := service.EstimateRouteBudget(variant.Route)

	// 建立/更新预算计划，使后续「花费报告」可即时对比预算
	var plan models.BudgetPlan
	if err := database.DB.Where("user_id = ? AND trip_id = ?", req.UserID, tripID).First(&plan).Error; err != nil {
		plan = models.BudgetPlan{
			ID:          uuid.New().String(),
			UserID:      req.UserID,
			TripID:      tripID,
			TotalBudget: estimated,
			Currency:    "CNY",
		}
		database.DB.Create(&plan)
	} else {
		plan.TotalBudget = estimated
		database.DB.Save(&plan)
	}

	if GlobalHub != nil {
		GlobalHub.Broadcast <- models.WSMessage{
			Type:   "plan_variant_sync",
			RoomID: variant.RoomID,
			UserID: req.UserID,
			Payload: gin.H{
				"action":     "adopt",
				"variant_id": variant.ID,
				"name":       variant.Name,
			},
		}
	}

	c.JSON(http.StatusOK, gin.H{
		"message": "方案已采纳，预算基线已建立",
		"variant": variantBrief(variant),
		"budget":  gin.H{"trip_id": tripID, "total_budget": estimated, "currency": "CNY"},
	})
}

// loadRoomVariants 拉取房间内全部方案及其投票记录。
func loadRoomVariants(roomID string) ([]models.PlanVariant, []service.VariantVote) {
	var variants []models.PlanVariant
	database.DB.Where("room_id = ?", roomID).Order("created_at asc").Find(&variants)

	ids := make([]string, 0, len(variants))
	for _, v := range variants {
		ids = append(ids, v.ID)
	}

	votes := make([]service.VariantVote, 0)
	if len(ids) > 0 {
		var vs []models.PlanVariantVote
		database.DB.Where("variant_id IN ?", ids).Order("created_at asc").Find(&vs)
		for _, v := range vs {
			votes = append(votes, service.VariantVote{VariantID: v.VariantID, UserID: v.UserID})
		}
	}
	return variants, votes
}

// variantBrief 方案对外展示结构（不含投票数）。
func variantBrief(v models.PlanVariant) gin.H {
	return variantBriefWith(v, 0, false)
}

// variantBriefWith 方案对外展示结构（含投票数与胜出标记）。
func variantBriefWith(v models.PlanVariant, votes int, winner bool) gin.H {
	return gin.H{
		"id":         v.ID,
		"room_id":    v.RoomID,
		"name":       v.Name,
		"style":      v.Style,
		"route":      v.Route,
		"created_by": v.CreatedBy,
		"votes":      votes,
		"winner":     winner,
		"created_at": v.CreatedAt,
	}
}
