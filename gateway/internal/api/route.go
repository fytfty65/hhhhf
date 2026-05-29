package api

import (
	"gateway/internal/handlers"

	"github.com/gin-gonic/gin"
)

func RegisterRoutes(r *gin.Engine) {
	// 健康检查接口
	r.GET("/ping", func(c *gin.Context) {
		c.JSON(200, gin.H{"message": "pong", "service": "OmniRoute-Gateway"})
	})
	r.GET("/ws", handlers.HandleWebSocket)

	apiGroup := r.Group("/api/v1")
	{
		// ==========================================
		// 🚨 新增：HTTP 群聊组队接口 (新建群 & 凭邀请码加群)
		// ==========================================
		apiGroup.POST("/room/create", handlers.CreateRoomHandler)
		apiGroup.POST("/room/join", handlers.JoinRoomHandler)

		// 房间与协同相关路由 (WebSocket 核心)
		// ⚠️ 前端连接示例: ws://localhost:8080/api/v1/room/你的RoomID/ws?userId=你的UserID
		apiGroup.GET("/room/:id/ws", handlers.RoomWebSocketHandler)

		// 行程规划相关路由 (HTTP)
		apiGroup.POST("/planning/init", handlers.InitPlanningHandler)

		// AI 代理状态同步路由
		apiGroup.POST("/agent/sync", handlers.AgentSyncHandler)
	}
}
