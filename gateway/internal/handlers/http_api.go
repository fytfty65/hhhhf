package handlers

import (
	"math/rand"
	"net/http"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

// 生成 6 位大写字母+数字的邀请码
func generateInviteCode() string {
	const charset = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789" // 剔除容易混淆的 0,1,I,O
	seededRand := rand.New(rand.NewSource(time.Now().UnixNano()))
	b := make([]byte, 6)
	for i := range b {
		b[i] = charset[seededRand.Intn(len(charset))]
	}
	return string(b)
}

// CreateRoomHandler 【队长专用】建群并获取邀请码
func CreateRoomHandler(c *gin.Context) {
	var req struct {
		Username string `json:"username" binding:"required"`
		RoomName string `json:"room_name" binding:"required"`
		Role     string `json:"role"` // 可选，如：队长/主理人
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数错误"})
		return
	}

	// 1. 创建用户
	userID := uuid.New().String()
	user := models.User{ID: userID, Username: req.Username}
	database.DB.Create(&user)

	// 2. 创建房间
	roomID := uuid.New().String()
	inviteCode := generateInviteCode()
	room := models.Room{
		ID:         roomID,
		InviteCode: inviteCode,
		Name:       req.RoomName,
		CreatorID:  userID,
	}
	database.DB.Create(&room)

	// 3. 将队长拉入房间成员表
	if req.Role == "" {
		req.Role = "主理人"
	}
	database.DB.Create(&models.RoomMember{RoomID: roomID, UserID: userID, Role: req.Role})

	// 返回给前端，前端拿到 ID 后就可以立刻去连 WebSocket 了
	c.JSON(http.StatusOK, gin.H{
		"message":     "房间创建成功",
		"invite_code": inviteCode,
		"room_id":     roomID,
		"user_id":     userID,
	})
}

// JoinRoomHandler 【队员专用】输入邀请码空降群聊
func JoinRoomHandler(c *gin.Context) {
	var req struct {
		Username   string `json:"username" binding:"required"`
		InviteCode string `json:"invite_code" binding:"required"`
		Role       string `json:"role"` // 可选，如：吃货/司机/摄影师
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数错误"})
		return
	}

	// 1. 验证邀请码
	var room models.Room
	if err := database.DB.Where("invite_code = ?", req.InviteCode).First(&room).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "邀请码无效或房间不存在"})
		return
	}

	// 2. 创建新队员用户
	userID := uuid.New().String()
	user := models.User{ID: userID, Username: req.Username}
	database.DB.Create(&user)

	// 3. 将队员拉入房间成员表
	if req.Role == "" {
		req.Role = "成员"
	}
	database.DB.Create(&models.RoomMember{RoomID: room.ID, UserID: userID, Role: req.Role})

	// 返回给前端连接 WS 必要的凭证
	c.JSON(http.StatusOK, gin.H{
		"message":   "成功加入房间",
		"room_name": room.Name,
		"room_id":   room.ID,
		"user_id":   userID,
	})
}

// 把这两段加在 http_api.go 文件的最底部

// InitPlanningHandler 接收前端初始化的行程偏好
func InitPlanningHandler(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{
		"status":  "success",
		"message": "行程规划初始化接口已就绪",
	})
}

// AgentSyncHandler 接收 Python AI 传回来的异步状态
func AgentSyncHandler(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{
		"status":  "success",
		"message": "智能体状态同步接口已就绪",
	})
}

// SubmitFeedbackHandler 接收用户的路线反馈，形成持续学习闭环
func SubmitFeedbackHandler(c *gin.Context) {
	var req struct {
		RoomID string `json:"room_id" binding:"required"`
		UserID string `json:"user_id" binding:"required"`
		Target string `json:"target" binding:"required"`
		Score  int    `json:"score" binding:"required"` // 1 或 -1
		Reason string `json:"reason"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "反馈参数不完整"})
		return
	}

	// 将真实反馈刻录进大数据库，形成系统记忆
	feedback := models.FeedbackLog{
		RoomID: req.RoomID,
		UserID: req.UserID,
		Target: req.Target,
		Score:  req.Score,
		Reason: req.Reason,
	}
	database.DB.Create(&feedback)

	c.JSON(http.StatusOK, gin.H{
		"message": "反馈已收入进化知识库，下次推演将自动修正",
		"status":  "success",
	})
}
