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

	// ==========================================
	// 认证接口：注册 & 登录 (前端调用 /api/auth/*)
	// ==========================================
	authGroup := r.Group("/api/auth")
	{
		authGroup.POST("/register", handlers.RegisterHandler)
		authGroup.POST("/login", handlers.LoginHandler)
	}

	// ==========================================
	// 个人主页接口：资料 / 历史行程 / 头像
	// ==========================================
	userGroup := r.Group("/api/user")
	{
		userGroup.GET("/profile", handlers.GetProfileHandler)
		userGroup.POST("/trip", handlers.SaveTripHandler)
		userGroup.POST("/avatar", handlers.UpdateAvatarHandler)
		userGroup.POST("/avatar/upload", handlers.UploadAvatarHandler)
		userGroup.POST("/profile/update", handlers.UpdateProfileHandler)
	}
	// 用户自定义头像等上传资源的静态托管
	r.Static("/uploads", "./uploads")

	// ==========================================
	// 社区接口：发布 / 列表 / 评论 / 点赞
	// ==========================================
	communityGroup := r.Group("/api/community")
	{
		communityGroup.GET("/list", handlers.ListPostsHandler)
		communityGroup.POST("/post", handlers.PublishPostHandler)
		communityGroup.GET("/comments", handlers.GetCommentsHandler)
		communityGroup.POST("/comment", handlers.CommentHandler)
		communityGroup.POST("/like", handlers.LikePostHandler)
	}

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

		// 用户路线反馈（正/负样本），形成持续学习进化闭环
		apiGroup.POST("/feedback", handlers.SubmitFeedbackHandler)

		// ==========================================
		// 功能1：个性化旅行画像 + 推荐引擎 + A/B 测试
		// ==========================================
		apiGroup.GET("/user/preference", handlers.GetUserPreferenceHandler)
		apiGroup.POST("/user/preference/refresh", handlers.RefreshUserPreferenceHandler)
		apiGroup.POST("/recommendation/track", handlers.TrackRecommendationEventHandler)
		apiGroup.GET("/recommendation/metrics", handlers.RecommendationMetricsHandler)

		// ==========================================
		// 功能3：行程节点级协作批注（评论 / 回复 / 投票 / 历史）
		// ==========================================
		apiGroup.POST("/annotation", handlers.CreateAnnotationHandler)
		apiGroup.GET("/annotations", handlers.ListAnnotationsHandler)
		apiGroup.GET("/annotation/history", handlers.AnnotationHistoryHandler)
		apiGroup.POST("/annotation/resolve", handlers.ResolveAnnotationHandler)
		apiGroup.POST("/annotation/vote", handlers.VoteAnnotationHandler)

		// ==========================================
		// 功能5：智能预算管家 + 行程复盘（多币种 + 超支预警 + 复盘报告）
		// ==========================================
		apiGroup.POST("/budget", handlers.SetBudgetHandler)
		apiGroup.POST("/expense", handlers.AddExpenseHandler)
		apiGroup.GET("/expenses", handlers.ListExpensesHandler)
		apiGroup.GET("/budget/summary", handlers.BudgetSummaryHandler)
		apiGroup.GET("/budget/review", handlers.BudgetReviewHandler)
	}
}
