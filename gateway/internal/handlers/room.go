package handlers

import (
	"log"
	"net/http"
	"strings"

	"gateway/internal/database"
	"gateway/internal/models"
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
)

var upgrader = websocket.Upgrader{
	CheckOrigin: websocketOriginAllowed,
}

// GlobalHub 全局的房间管理器
var GlobalHub *service.Hub

// RoomWebSocketHandler 处理前端的 WS 连接请求
func RoomWebSocketHandler(c *gin.Context) {
	roomID := c.Param("id")

	userID := CurrentUserID(c)
	if userID == "" {
		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "未登录", "code": "AUTH_REQUIRED"})
		return
	}
	var member models.RoomMember
	if err := database.DB.Where("room_id = ? AND user_id = ?", roomID, userID).First(&member).Error; err != nil {
		c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "不是该房间成员", "code": "ROOM_MEMBERSHIP_REQUIRED"})
		return
	}
	nickname := c.DefaultQuery("nickname", userID)
	role := c.DefaultQuery("role", "成员")
	avatarUrl := c.Query("avatar_url")
	nickname = truncateRunes(strings.TrimSpace(nickname), 80)
	role = truncateRunes(strings.TrimSpace(role), 40)
	avatarUrl = truncateRunes(strings.TrimSpace(avatarUrl), 1024)

	conn, err := upgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		log.Printf("升级 WS 失败: %v", err)
		return
	}
	conn.SetReadLimit(1 << 20)

	client := &service.Client{
		Hub:       GlobalHub,
		Conn:      conn,
		Send:      make(chan models.WSMessage, 256),
		RoomID:    roomID,
		UserID:    userID,
		Nickname:  nickname,
		Role:      role,
		AvatarUrl: avatarUrl,
	}

	client.Hub.Register <- client

	// 启动读写协程
	go client.WritePump()
	go client.ReadPump()

	log.Printf("用户 %s 已进入房间 %s", userID, roomID)
}
