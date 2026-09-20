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
	r.GET("/healthz", HealthzHandler)
	r.GET("/readyz", ReadyzHandler)
	r.GET("/ws", handlers.AuthMiddleware(), handlers.HandleWebSocket)

	// ==========================================
	// 认证接口：注册 & 登录 (前端调用 /api/auth/*)
	// 登录/注册是唯一无需认证的写入口，因此按客户端 IP 限流；
	// 登录另有按账号的失败锁定（见 handlers/authAccountLocked）。
	// ==========================================
	authGroup := r.Group("/api/auth")
	{
		authGroup.POST("/register", handlers.AuthRegisterRateLimitMiddleware(), handlers.RegisterHandler)
		authGroup.POST("/login", handlers.AuthLoginRateLimitMiddleware(), handlers.LoginHandler)
		authGroup.POST("/logout", handlers.AuthMiddleware(), handlers.LogoutHandler)
	}

	// ==========================================
	// 个人主页接口：资料 / 历史行程 / 头像
	// ==========================================
	userGroup := r.Group("/api/user")
	userGroup.Use(handlers.AuthMiddleware())
	{
		userGroup.GET("/profile", handlers.GetProfileHandler)
		// Compatibility alias for the standalone profile screen.
		userGroup.GET("/trips", handlers.GetTripsHandler)
		userGroup.POST("/trip", handlers.SaveTripHandler)
		userGroup.POST("/avatar", handlers.UpdateAvatarHandler)
		userGroup.POST("/avatar/upload", handlers.UploadAvatarHandler)
		userGroup.POST("/profile/update", handlers.UpdateProfileHandler)
		userGroup.PUT("/profile", handlers.UpdateProfileHandler)
	}
	// 用户自定义头像等上传资源的静态托管
	// Only expose the avatar subtree; serving the entire uploads directory can
	// accidentally publish backups or future private artifacts.
	r.Static("/uploads/avatars", "./uploads/avatars")

	// ==========================================
	// 社区接口：发布 / 列表 / 评论 / 点赞
	// ==========================================
	communityGroup := r.Group("/api/community")
	communityGroup.Use(handlers.AuthMiddleware())
	{
		communityGroup.GET("/list", handlers.ListPostsHandler)
		communityGroup.POST("/post", handlers.PublishPostHandler)
		communityGroup.GET("/comments", handlers.GetCommentsHandler)
		communityGroup.POST("/comment", handlers.CommentHandler)
		communityGroup.POST("/like", handlers.LikePostHandler)
		communityGroup.POST("/favorite", handlers.FavoritePostHandler)
		communityGroup.GET("/tags", handlers.ListTagsHandler)
	}

	apiGroup := r.Group("/api/v1")
	apiGroup.Use(handlers.AuthMiddleware())
	{
		// ==========================================
		// 🚨 新增：HTTP 群聊组队接口 (新建群 & 凭邀请码加群)
		// ==========================================
		apiGroup.POST("/room/create", handlers.RoomRateLimitMiddleware(), handlers.CreateRoomHandler)
		apiGroup.POST("/room/join", handlers.RoomRateLimitMiddleware(), handlers.JoinRoomHandler)

		// ==========================================
		// 用户实拍照片（⑥ 网络版）：上传 / 我的列表 / 取图 / 删除
		// ⚠️ 路径刻意避开 `/photos/mine` 与 `/photos/:id` 的静态+通配同级冲突
		//    （gin 在这类组合上会 panic，整个 gateway 起不来），列表走 /my/photos。
		// 新上传一律 pending：只有上传者自己可见，审核通过才公开（见 internal/photos）。
		// ==========================================
		apiGroup.POST("/photos", handlers.UploadPhotoHandler)
		apiGroup.GET("/my/photos", handlers.ListMyPhotosHandler)
		apiGroup.GET("/photos/:id", handlers.GetPhotoHandler)
		apiGroup.DELETE("/photos/:id", handlers.DeletePhotoHandler)

		// 房间与协同相关路由 (WebSocket 核心)
		// ⚠️ 前端连接示例: ws://localhost:8080/api/v1/room/你的RoomID/ws?userId=你的UserID
		apiGroup.GET("/room/:id/ws", handlers.RoomWebSocketHandler)

		// 行程规划相关路由 (HTTP)
		apiGroup.POST("/planning/init", handlers.InitPlanningHandler)
		// Canonical planning contract and first-phase pre-trip capabilities.
		apiGroup.POST("/planning/context", handlers.PlanningContextHandler)
		apiGroup.POST("/planning/digital-twin", handlers.DigitalTwinHandler)
		apiGroup.POST("/planning/destination/recommend", handlers.DestinationRecommendHandler)
		apiGroup.POST("/planning/transport/options", handlers.TransportOptionsHandler)
		apiGroup.POST("/planning/lodging/options", handlers.LodgingOptionsHandler)
		apiGroup.POST("/planning/dining/options", handlers.DiningOptionsHandler)
		apiGroup.POST("/planning/packing/checklist", handlers.PackingChecklistHandler)
		apiGroup.POST("/planning/secondary", handlers.SecondaryItineraryHandler)
		apiGroup.POST("/planning/replan", handlers.MinimalPerturbationReplanHandler)
		apiGroup.POST("/planning/replan/validate", handlers.ValidateReplanCandidateHandler)
		apiGroup.GET("/planning/features", handlers.FeatureSnapshotHandler)
		apiGroup.POST("/planning/knowledge/graph", handlers.KnowledgeGraphHandler)
		apiGroup.GET("/planning/agents/evaluation", handlers.AgentEvaluationHandler)
		apiGroup.POST("/planning/specialists/evaluate", handlers.SpecialistAdviceHandler)
		apiGroup.POST("/planning/crowd/predict", handlers.CrowdForecastHandler)
		apiGroup.POST("/planning/guide/multilingual", handlers.MultilingualGuideHandler)
		apiGroup.POST("/planning/ar/guide", handlers.ARGuideHandler)
		apiGroup.POST("/planning/channels/click", handlers.OfficialChannelClickHandler)
		apiGroup.GET("/planning/channels/metrics", handlers.OfficialChannelMetricsHandler)
		apiGroup.GET("/planning/execution", handlers.GetExecutionStateHandler)
		apiGroup.POST("/planning/execution", handlers.UpdateExecutionStateHandler)
		apiGroup.GET("/planning/journey/state", handlers.JourneyStateHandler)
		apiGroup.POST("/planning/emergency/evaluate", handlers.EmergencyEvaluateHandler)
		apiGroup.POST("/planning/pretrip/tasks", handlers.PreTripTasksHandler)
		apiGroup.POST("/planning/joint", handlers.JointPlanHandler)
		apiGroup.POST("/planning/orders", handlers.CreateOrderHandler)
		apiGroup.DELETE("/planning/orders/:id", handlers.CancelOrderHandler)
		apiGroup.POST("/planning/events", handlers.RecordPlanningEventHandler)
		apiGroup.GET("/planning/quality", handlers.PlanningQualityHandler)
		apiGroup.GET("/planning/dataset/export", handlers.ExportPlanningDatasetHandler)
		apiGroup.GET("/planning/providers/health", handlers.PlanningProviderHealthHandler)
		apiGroup.GET("/planning/providers/probe", handlers.PlanningProviderProbeHandler)
		apiGroup.GET("/planning/providers/tools", handlers.MCPToolsSchemaHandler)
		apiGroup.POST("/planning/signals", handlers.PlanningSignalsHandler)
		apiGroup.GET("/planning/models", handlers.ListModelDeploymentsHandler)
		apiGroup.POST("/planning/models", handlers.RegisterModelDeploymentHandler)
		apiGroup.POST("/planning/models/:id/evaluate", handlers.EvaluateModelDeploymentHandler)
		apiGroup.POST("/planning/models/:id/promote", handlers.PromoteModelDeploymentHandler)

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
		// P6：埋点 A/B 漏斗（曝光 → 点击 → 采纳 → 满意）
		apiGroup.GET("/recommendation/funnel", handlers.FunnelMetricsHandler)

		// ==========================================
		// 功能3：多方案投票（保存方案 / 列表 / 投票 / 胜出）
		// ==========================================
		apiGroup.POST("/plan/variant", handlers.SaveVariantHandler)
		apiGroup.GET("/plan/variants", handlers.ListVariantsHandler)
		apiGroup.POST("/plan/variant/vote", handlers.VoteVariantHandler)
		apiGroup.POST("/plan/variant/adopt", handlers.AdoptVariantHandler)
		apiGroup.GET("/plan/winning", handlers.WinningVariantHandler)

		// ==========================================
		// 功能4：推荐闭环（替换即信号 / 满意度问卷 / 协同过滤）
		// ==========================================
		apiGroup.POST("/recommendation/swap", handlers.RecordSwapSignalHandler)
		apiGroup.POST("/satisfaction", handlers.SubmitSatisfactionHandler)
		apiGroup.GET("/recommendation/collaborative", handlers.CollaborativeRecommendHandler)

		// ==========================================
		// 功能7：省数据动态化（季节 + 埋点权重重排 + 高德 POI）
		// ==========================================
		apiGroup.POST("/province/dynamic", handlers.ProvinceDynamicHandler)
		apiGroup.POST("/province/click", handlers.ProvincePoiClickHandler)
		apiGroup.GET("/amap/poi", handlers.AIRateLimitMiddleware(), handlers.AmapPoiSearchHandler)

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
		apiGroup.POST("/expense/ocr", handlers.OCRExpenseHandler)
		apiGroup.GET("/expenses", handlers.ListExpensesHandler)
		apiGroup.GET("/budget/summary", handlers.BudgetSummaryHandler)
		apiGroup.GET("/budget/review", handlers.BudgetReviewHandler)

		// 预算预测
		apiGroup.GET("/budget/predict", handlers.BudgetPredictHandler)

		// AI 安全简报
		apiGroup.POST("/briefing/safety", handlers.SafetyBriefingHandler)

		// 旅中风险推送中心：实时重取风险快照 + 变更事件检测
		apiGroup.POST("/risk/realtime", handlers.AIRateLimitMiddleware(), handlers.RiskRealtimeHandler)

		// ==========================================
		// 功能8：目的地风险订阅 + 碳足迹评估（P5）
		// ==========================================
		apiGroup.POST("/risk/subscribe", handlers.AIRateLimitMiddleware(), handlers.SubscribeRiskHandler)
		apiGroup.POST("/risk/unsubscribe", handlers.AIRateLimitMiddleware(), handlers.UnsubscribeRiskHandler)
		apiGroup.GET("/risk/subscriptions", handlers.ListRiskSubscriptionsHandler)
		apiGroup.POST("/risk/subscriptions/check", handlers.AIRateLimitMiddleware(), handlers.CheckRiskSubscriptionsHandler)
		apiGroup.POST("/carbon/footprint", handlers.AIRateLimitMiddleware(), handlers.CarbonFootprintHandler)
	}
}
