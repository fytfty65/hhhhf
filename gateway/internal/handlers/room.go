package handlers

import (
	"log"
	"net/http"
	
	"gateway/internal/models"  // 🚨 必须导入 models
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
)

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

// GlobalHub 全局的房间管理器
var GlobalHub *service.Hub

// RoomWebSocketHandler 处理前端的 WS 连接请求
func RoomWebSocketHandler(c *gin.Context) {
	roomID := c.Param("id")
	
	// 🚨 修复点：尝试从 URL 参数获取 userID，如果没有则给个默认值
	// 测试脚本连接时可以写成：ws://localhost:8080/api/v1/room/test-room-001/ws?userId=test_user
	userID := c.DefaultQuery("userId", "anonymous_user")

	conn, err := upgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		log.Printf("升级 WS 失败: %v", err)
		return
	}

	client := &service.Client{
		Hub:    GlobalHub,
		Conn:   conn,
		Send:   make(chan models.WSMessage, 256),
		RoomID: roomID,
		UserID: userID, // 此时 userID 已经有定义了
	}

	client.Hub.Register <- client

	// 启动读写协程
	go client.WritePump()
	go client.ReadPump()
	
	log.Printf("用户 %s 已进入房间 %s", userID, roomID)
}