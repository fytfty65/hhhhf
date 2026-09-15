package main

import (
	"context"
	"errors"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"gateway/internal/api"
	"gateway/internal/database" // 🚨 新增：引入数据库包
	"gateway/internal/handlers"
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
	"github.com/joho/godotenv"
)

// shutdownTimeout bounds how long a graceful stop waits for in-flight requests
// (including long-lived WebSocket upgrades) before forcing the listener closed.
const shutdownTimeout = 15 * time.Second

func main() {
	// 1. 加载环境变量
	if err := godotenv.Load(); err != nil {
		if wd, wdErr := os.Getwd(); wdErr == nil {
			log.Printf("警告: 加载 .env 失败 (cwd=%s): %v", wd, err)
		} else {
			log.Printf("警告: 加载 .env 失败: %v", err)
		}
	}
	if err := handlers.ValidateSessionStore(); err != nil {
		log.Fatalf("认证会话存储校验失败: %v", err)
	}
	if err := handlers.ValidateOriginAllowlist(); err != nil {
		log.Fatalf("来源白名单校验失败: %v", err)
	}
	if api.MetricsTokenRequired() {
		log.Fatalf("APP_ENV=production 时必须设置 METRICS_TOKEN，否则指标端点无法安全暴露")
	}

	// =====================================================
	// 🚨 核心新增点：点火！初始化 SQLite 数据库 (自动建表)
	// =====================================================
	if err := database.InitDB(); err != nil {
		log.Fatalf("数据库初始化失败: %v", err)
	}
	if strings.EqualFold(strings.TrimSpace(os.Getenv("DB_AUTO_BACKUP")), "true") {
		if path, err := database.Backup(""); err != nil {
			log.Printf("数据库自动备份失败: %v", err)
		} else {
			log.Printf("数据库自动备份完成: %s", path)
		}
	}
	startBackupLoop()

	// =====================================================
	// 核心修复点：初始化全局 WebSocket 房间管理器 (Hub)
	// =====================================================
	handlers.GlobalHub = service.NewHub()
	go handlers.GlobalHub.Run() // 启动 Hub 的后台守护协程

	// P6：Redis Stream 事件桥接 —— 消费 AI 服务写入的全局事件流，按房间广播到 WebSocket
	service.StartEventStreamBridge(handlers.GlobalHub)

	// 2. 设置 Gin 路由引擎
	if strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV"))) == "production" ||
		strings.ToLower(strings.TrimSpace(os.Getenv("GIN_MODE"))) == "release" {
		gin.SetMode(gin.ReleaseMode)
	}
	r := gin.New()
	r.Use(gin.Recovery())

	// 3. 注册中间件（P3 可观测性：X-Request-ID + 结构化日志 + 延迟指标）
	r.Use(api.RequestIDMiddleware(), api.LoggingMiddleware(), corsMiddleware())

	// 指标端点：需要访问令牌，无令牌时仅允许本机回环访问
	r.GET("/metrics", api.MetricsGuardMiddleware(), api.MetricsHandler)

	// 4. 注册 API 路由 (非常好的工程规范)
	api.RegisterRoutes(r)

	// 5. 启动服务
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	bindHost := strings.TrimSpace(os.Getenv("BIND_HOST"))
	if bindHost == "" {
		// Default to all interfaces for containerised deploys, but make the
		// bind address explicit and log the real one — the previous banner
		// claimed "localhost" while actually listening on 0.0.0.0.
		bindHost = "0.0.0.0"
	}

	// Timeouts are mandatory: without them a slowloris client holds a
	// connection and a goroutine open indefinitely. ReadHeaderTimeout bounds
	// the header phase, and IdleTimeout reclaims keep-alive sockets.
	srv := &http.Server{
		Addr:              net.JoinHostPort(bindHost, port),
		Handler:           r,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       60 * time.Second,
		WriteTimeout:      120 * time.Second,
		IdleTimeout:       120 * time.Second,
		MaxHeaderBytes:    1 << 20,
	}

	// Serve in the background so the main goroutine can wait for a signal and
	// drain in-flight requests instead of severing live WebSockets and the
	// long-running AI negotiation streams.
	go func() {
		log.Printf("🚀 OmniRoute 网关服务已启动，监听 %s", srv.Addr)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("服务启动失败: %v", err)
		}
	}()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	<-ctx.Done()
	stop()

	log.Printf("收到退出信号，开始优雅停机（最多等待 %s）...", shutdownTimeout)
	shutdownCtx, cancel := context.WithTimeout(context.Background(), shutdownTimeout)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		log.Printf("优雅停机超时，强制关闭: %v", err)
		_ = srv.Close()
	}
	if sqlDB, err := database.DB.DB(); err == nil {
		_ = sqlDB.Close()
	}
	log.Printf("OmniRoute 网关已停止")
}

// startBackupLoop is opt-in. Platforms with their own scheduler can leave the
// interval unset; deployments that need an application-managed cadence can
// set DB_BACKUP_INTERVAL_MINUTES to a positive integer.
func startBackupLoop() {
	minutes, err := strconv.Atoi(strings.TrimSpace(os.Getenv("DB_BACKUP_INTERVAL_MINUTES")))
	if err != nil || minutes <= 0 {
		return
	}
	interval := time.Duration(minutes) * time.Minute
	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for range ticker.C {
			if path, err := database.Backup(""); err != nil {
				log.Printf("数据库定时备份失败: %v", err)
			} else {
				log.Printf("数据库定时备份完成: %s", path)
			}
		}
	}()
}

// corsMiddleware 只允许显式配置的前端来源，避免反射任意 Origin。
func corsMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		origin := c.Request.Header.Get("Origin")
		allowed := false
		if origin != "" {
			origins := os.Getenv("ALLOWED_ORIGINS")
			if origins == "" {
				// Local development uses several Next ports. Production must set
				// ALLOWED_ORIGINS explicitly and never relies on this fallback.
				origins = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001,http://localhost:3101,http://127.0.0.1:3101,http://localhost:3106,http://127.0.0.1:3106,http://localhost:3107,http://127.0.0.1:3107,http://localhost:3110,http://127.0.0.1:3110,http://localhost:3120,http://127.0.0.1:3120,http://localhost:3130,http://127.0.0.1:3130"
			}
			for _, candidate := range strings.Split(origins, ",") {
				if strings.TrimSpace(candidate) == origin {
					allowed = true
					break
				}
			}
			if !allowed {
				c.AbortWithStatusJSON(403, gin.H{"error": "不受信任的来源", "code": "ORIGIN_FORBIDDEN"})
				return
			}
			c.Writer.Header().Set("Access-Control-Allow-Origin", origin)
			c.Writer.Header().Set("Vary", "Origin")
		}
		c.Writer.Header().Set("Access-Control-Allow-Credentials", "true")
		c.Writer.Header().Set("Access-Control-Allow-Headers", "Content-Type, Content-Length, Accept-Encoding, X-CSRF-Token, Authorization, accept, origin, Cache-Control, X-Requested-With")
		c.Writer.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS, GET, PUT, DELETE, PATCH")
		c.Writer.Header().Set("Access-Control-Max-Age", "86400")
		// 允许暴露自定义响应头，便于前端读取错误信息
		c.Writer.Header().Set("Access-Control-Expose-Headers", "Content-Length, Content-Type")

		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(204)
			return
		}
		c.Next()
	}
}
