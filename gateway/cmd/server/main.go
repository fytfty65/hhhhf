package main

import (
	"log"
	"os"

	"gateway/internal/api"
	"gateway/internal/database" // 🚨 新增：引入数据库包
	"gateway/internal/handlers"
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
	"github.com/joho/godotenv"
)

func main() {
	// 1. 加载环境变量
	if err := godotenv.Load(); err != nil {
		log.Println("警告: 未找到 .env 文件，将使用系统环境变量")
	}

	// =====================================================
	// 🚨 核心新增点：点火！初始化 SQLite 数据库 (自动建表)
	// =====================================================
	database.InitDB()

	// =====================================================
	// 核心修复点：初始化全局 WebSocket 房间管理器 (Hub)
	// =====================================================
	handlers.GlobalHub = service.NewHub()
	go handlers.GlobalHub.Run() // 启动 Hub 的后台守护协程

	// 2. 设置 Gin 路由引擎
	r := gin.Default()

	// 3. 注册中间件
	r.Use(corsMiddleware())

	// 4. 注册 API 路由 (非常好的工程规范)
	api.RegisterRoutes(r)

	// 5. 启动服务
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	log.Printf("🚀 OmniRoute 网关服务已启动在 http://localhost:%s", port)
	if err := r.Run(":" + port); err != nil {
		log.Fatalf("服务启动失败: %v", err)
	}
}

// corsMiddleware 处理跨域请求
func corsMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Writer.Header().Set("Access-Control-Allow-Origin", "*")
		c.Writer.Header().Set("Access-Control-Allow-Credentials", "true")
		c.Writer.Header().Set("Access-Control-Allow-Headers", "Content-Type, Content-Length, Accept-Encoding, X-CSRF-Token, Authorization, accept, origin, Cache-Control, X-Requested-With")
		c.Writer.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS, GET, PUT")

		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(204)
			return
		}
		c.Next()
	}
}
