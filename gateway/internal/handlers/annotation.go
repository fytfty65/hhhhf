package handlers

import (
	"net/http"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

// CreateAnnotationHandler 在行程节点级创建批注或回复（多人实时协作）。
func CreateAnnotationHandler(c *gin.Context) {
	var req struct {
		RoomID   string `json:"room_id" binding:"required"`
		TripID   string `json:"trip_id"`
		NodeKey  string `json:"node_key" binding:"required"`
		UserID   string `json:"user_id" binding:"required"`
		Content  string `json:"content" binding:"required"`
		ParentID string `json:"parent_id"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "批注参数不完整"})
		return
	}
	userID, ok := requireRoomMember(c, req.RoomID)
	if !ok {
		return
	}
	req.UserID = userID

	ann := models.NodeAnnotation{
		ID:       uuid.New().String(),
		RoomID:   req.RoomID,
		TripID:   req.TripID,
		NodeKey:  req.NodeKey,
		UserID:   req.UserID,
		Content:  req.Content,
		ParentID: req.ParentID,
		Status:   "open",
	}
	if err := database.DB.Create(&ann).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "批注创建失败"})
		return
	}

	// 实时同步：向房间内所有在线成员广播新批注（延迟控制在 300ms 内）
	if GlobalHub != nil {
		GlobalHub.Broadcast <- models.WSMessage{
			Type:   "annotation_sync",
			RoomID: req.RoomID,
			UserID: req.UserID,
			Payload: gin.H{
				"action":     "add",
				"annotation": annotationBrief(ann),
			},
		}
	}

	c.JSON(http.StatusOK, gin.H{"message": "批注已发布", "annotation": annotationBrief(ann)})
}

// ListAnnotationsHandler 拉取某行程节点的全部批注（含回复、投票数与状态，用于历史与版本管理）。
func ListAnnotationsHandler(c *gin.Context) {
	roomID := c.Query("room_id")
	nodeKey := c.Query("node_key")
	if roomID == "" && nodeKey == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少 room_id 或 node_key"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if roomID != "" {
		if _, ok := requireRoomMember(c, roomID); !ok {
			return
		}
	}

	var anns []models.NodeAnnotation
	q := database.DB.Order("created_at asc")
	if roomID != "" {
		q = q.Where("room_id = ?", roomID)
	} else {
		// A node key is not globally unique. When room_id is omitted, scope the
		// query to rooms in which the authenticated user is a member.
		q = q.Where("room_id IN (?)", database.DB.Model(&models.RoomMember{}).Select("room_id").Where("user_id = ?", userID))
	}
	if nodeKey != "" {
		q = q.Where("node_key = ?", nodeKey)
	}
	q.Find(&anns)

	result := make([]gin.H, 0, len(anns))
	for _, a := range anns {
		author, avatar, _ := userBrief(a.UserID)
		up, down := voteCounts(a.ID)
		result = append(result, gin.H{
			"id":         a.ID,
			"room_id":    a.RoomID,
			"trip_id":    a.TripID,
			"node_key":   a.NodeKey,
			"user_id":    a.UserID,
			"author":     author,
			"avatar":     avatar,
			"content":    a.Content,
			"parent_id":  a.ParentID,
			"status":     a.Status,
			"votes":      up - down,
			"upvotes":    up,
			"downvotes":  down,
			"created_at": a.CreatedAt,
		})
	}
	c.JSON(http.StatusOK, gin.H{"annotations": result})
}

// ResolveAnnotationHandler 将批注标记为已解决/重新打开（版本管理状态流转）。
func ResolveAnnotationHandler(c *gin.Context) {
	var req struct {
		AnnotationID string `json:"annotation_id" binding:"required"`
		Status       string `json:"status" binding:"required"` // open / resolved
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	if req.Status != "open" && req.Status != "resolved" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "状态仅支持 open / resolved"})
		return
	}

	var ann models.NodeAnnotation
	if err := database.DB.Where("id = ?", req.AnnotationID).First(&ann).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "批注不存在"})
		return
	}
	if _, ok := requireRoomMember(c, ann.RoomID); !ok {
		return
	}
	ann.Status = req.Status
	if err := database.DB.Save(&ann).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "状态更新失败"})
		return
	}

	if GlobalHub != nil {
		GlobalHub.Broadcast <- models.WSMessage{
			Type:   "annotation_sync",
			RoomID: ann.RoomID,
			UserID: "system",
			Payload: gin.H{
				"action":        "resolve",
				"annotation_id": ann.ID,
				"status":        ann.Status,
			},
		}
	}

	c.JSON(http.StatusOK, gin.H{"message": "状态已更新", "status": ann.Status})
}

// VoteAnnotationHandler 对某条批注投票（赞成/反对），用于团队成员即时反馈。
func VoteAnnotationHandler(c *gin.Context) {
	var req struct {
		AnnotationID string `json:"annotation_id" binding:"required"`
		UserID       string `json:"user_id" binding:"required"`
		Value        int    `json:"value" binding:"required"` // +1 / -1
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if req.Value != 1 && req.Value != -1 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "投票值仅支持 1 / -1"})
		return
	}

	var ann models.NodeAnnotation
	if err := database.DB.Where("id = ?", req.AnnotationID).First(&ann).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "批注不存在"})
		return
	}
	if _, ok := requireRoomMember(c, ann.RoomID); !ok {
		return
	}
	req.UserID = userID

	// 一人一票：先清除该用户此前的投票，再写入新票
	database.DB.Where("annotation_id = ? AND user_id = ?", req.AnnotationID, req.UserID).Delete(&models.NodeAnnotationVote{})
	if err := database.DB.Create(&models.NodeAnnotationVote{
		AnnotationID: req.AnnotationID,
		UserID:       req.UserID,
		Value:        req.Value,
	}).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "投票失败"})
		return
	}

	if GlobalHub != nil {
		GlobalHub.Broadcast <- models.WSMessage{
			Type:   "annotation_sync",
			RoomID: ann.RoomID,
			UserID: req.UserID,
			Payload: gin.H{
				"action":        "vote",
				"annotation_id": ann.ID,
			},
		}
	}

	c.JSON(http.StatusOK, gin.H{"message": "投票成功"})
}

// AnnotationHistoryHandler 查看某节点的批注历史轨迹（按时间正序，含状态变更）。
func AnnotationHistoryHandler(c *gin.Context) {
	nodeKey := c.Query("node_key")
	roomID := c.Query("room_id")
	if nodeKey == "" && roomID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少查询条件"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if roomID != "" {
		if _, ok := requireRoomMember(c, roomID); !ok {
			return
		}
	}

	var anns []models.NodeAnnotation
	q := database.DB.Order("created_at asc")
	if roomID != "" {
		q = q.Where("room_id = ?", roomID)
	} else {
		q = q.Where("room_id IN (?)", database.DB.Model(&models.RoomMember{}).Select("room_id").Where("user_id = ?", userID))
	}
	if nodeKey != "" {
		q = q.Where("node_key = ?", nodeKey)
	}
	q.Find(&anns)

	history := make([]gin.H, 0, len(anns))
	for _, a := range anns {
		author, _, _ := userBrief(a.UserID)
		history = append(history, gin.H{
			"annotation_id": a.ID,
			"author":        author,
			"content":       a.Content,
			"status":        a.Status,
			"created_at":    a.CreatedAt,
		})
	}
	c.JSON(http.StatusOK, gin.H{"history": history})
}

// voteCounts 返回某批注的赞成数 / 反对数。
func voteCounts(annotationID string) (int, int) {
	var up, down int64
	database.DB.Model(&models.NodeAnnotationVote{}).Where("annotation_id = ? AND value = ?", annotationID, 1).Count(&up)
	database.DB.Model(&models.NodeAnnotationVote{}).Where("annotation_id = ? AND value = ?", annotationID, -1).Count(&down)
	return int(up), int(down)
}

// annotationBrief 拼接批注对外展示结构。
func annotationBrief(a models.NodeAnnotation) gin.H {
	author, avatar, _ := userBrief(a.UserID)
	up, down := voteCounts(a.ID)
	return gin.H{
		"id":         a.ID,
		"room_id":    a.RoomID,
		"trip_id":    a.TripID,
		"node_key":   a.NodeKey,
		"user_id":    a.UserID,
		"author":     author,
		"avatar":     avatar,
		"content":    a.Content,
		"parent_id":  a.ParentID,
		"status":     a.Status,
		"votes":      up - down,
		"created_at": a.CreatedAt,
	}
}
