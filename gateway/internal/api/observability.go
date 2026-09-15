package api

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"log"
	"math"
	"net"
	"net/http"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"

	"gateway/internal/database"
	"gateway/internal/metrics"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// RedactSensitiveText removes credential-like values before text reaches logs.
var sensitiveLogValue = regexp.MustCompile(`(?i)(authorization|api[_-]?key|token|cookie|password)\s*[:=]\s*[^,;\r\n]+`)

func RedactSensitiveText(value string) string {
	return sensitiveLogValue.ReplaceAllString(value, "$1=[REDACTED]")
}

// ==========================================
// P3 可观测性：结构化请求日志 + 延迟指标 + X-Request-ID 追踪
// ==========================================

const requestIDKey = "request_id"

var (
	metricsMu          sync.Mutex
	requestsTotal      int
	requestsByPath     = map[string]int{}
	requestsByStatus   = map[string]int{}
	latencyByPath      = map[string]float64{}
	latencyCountByPath = map[string]int{}
	startTime          = time.Now()
)

func round2(v float64) float64 {
	return math.Round(v*100) / 100
}

func recordRequest(method, path string, status int, latencyMs float64) {
	metricsMu.Lock()
	defer metricsMu.Unlock()
	requestsTotal++
	key := method + " " + path
	requestsByPath[key]++
	requestsByStatus[fmt.Sprintf("%d", status)]++
	latencyByPath[key] += latencyMs
	latencyCountByPath[key]++
}

// RequestIDMiddleware 生成/透传 X-Request-ID，写入上下文供下游调用与日志关联。
func RequestIDMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		rid := c.GetHeader("X-Request-ID")
		if rid == "" {
			rid = uuid.New().String()
		}
		c.Set(requestIDKey, rid)
		c.Header("X-Request-ID", rid)
		c.Next()
	}
}

// LoggingMiddleware 输出结构化 JSON 单行日志并累加请求延迟指标。
func LoggingMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()

		latencyMs := float64(time.Since(start).Microseconds()) / 1000.0
		status := c.Writer.Status()
		rid, _ := c.Get(requestIDKey)

		recordRequest(c.Request.Method, c.Request.URL.Path, status, latencyMs)

		entry := map[string]interface{}{
			"ts":         time.Now().UnixMilli(),
			"service":    "gateway",
			"event":      "http_request",
			"request_id": rid,
			"method":     c.Request.Method,
			"path":       c.Request.URL.Path,
			"status":     status,
			"latency_ms": round2(latencyMs),
		}
		if b, err := json.Marshal(entry); err == nil {
			log.Println(string(b))
		}
	}
}

// MetricsTokenRequired reports whether a metrics token is configured. In
// production a token is mandatory; the caller (main) refuses to start without
// one so the endpoint can never be world-readable in a real deployment.
func MetricsTokenRequired() bool {
	env := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
	return (env == "production" || env == "prod") && strings.TrimSpace(os.Getenv("METRICS_TOKEN")) == ""
}

// validMetricsToken compares the supplied bearer/header token in constant time.
func validMetricsToken(supplied string) bool {
	expected := strings.TrimSpace(os.Getenv("METRICS_TOKEN"))
	if expected == "" {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(strings.TrimSpace(supplied)), []byte(expected)) == 1
}

// isLoopbackRequest reports whether the request originates from the local host.
func isLoopbackRequest(c *gin.Context) bool {
	host, _, err := net.SplitHostPort(strings.TrimSpace(c.Request.RemoteAddr))
	if err != nil {
		host = strings.TrimSpace(c.Request.RemoteAddr)
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// MetricsGuardMiddleware protects /metrics. The endpoint exposes a per-path
// request and latency inventory, which is a free recon map for an attacker, so
// it is not public: a configured METRICS_TOKEN always wins, and without one
// only loopback callers (a sidecar scraper or an operator on the box) may read.
func MetricsGuardMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		supplied := c.GetHeader("X-Metrics-Token")
		if supplied == "" {
			if header := strings.TrimSpace(c.GetHeader("Authorization")); strings.HasPrefix(strings.ToLower(header), "bearer ") {
				supplied = strings.TrimSpace(header[len("Bearer "):])
			}
		}
		if supplied != "" && validMetricsToken(supplied) {
			c.Next()
			return
		}
		if strings.TrimSpace(os.Getenv("METRICS_TOKEN")) == "" && isLoopbackRequest(c) {
			c.Next()
			return
		}
		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
			"error": "指标端点需要访问令牌",
			"code":  "METRICS_UNAUTHORIZED",
		})
	}
}

// MetricsHandler 返回进程内指标：请求总数 / 分路径计数与平均延迟 / 分状态计数。
func MetricsHandler(c *gin.Context) {
	metricsMu.Lock()
	defer metricsMu.Unlock()

	paths := map[string]interface{}{}
	for k, cnt := range requestsByPath {
		avg := 0.0
		if latencyCountByPath[k] > 0 {
			avg = latencyByPath[k] / float64(latencyCountByPath[k])
		}
		paths[k] = map[string]interface{}{
			"requests":       cnt,
			"avg_latency_ms": round2(avg),
		}
	}

	c.JSON(http.StatusOK, gin.H{
		"service":            "gateway",
		"uptime_seconds":     int(time.Since(startTime).Seconds()),
		"requests_total":     requestsTotal,
		"requests_by_status": requestsByStatus,
		"requests_by_path":   paths,
		// Learning-loop health. A climbing "missing_arm" means clients are not
		// returning a bandit arm, so rewards are dropped and the policy cannot
		// learn — the failure this instrumentation exists to surface.
		"bandit_feedback": metrics.BanditFeedbackCounters(),
	})
}

// HealthzHandler is a cheap liveness probe: it deliberately does not call
// external providers so an upstream outage cannot restart a healthy process.
func HealthzHandler(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"status": "ok", "service": "gateway"})
}

// ReadyzHandler verifies the dependencies required to serve authenticated
// traffic. In production Redis is mandatory because it backs shared sessions
// and distributed rate limits; in local development it remains optional.
func ReadyzHandler(c *gin.Context) {
	checks := map[string]string{}
	ready := true
	if database.DB == nil {
		checks["database"] = "unavailable"
		ready = false
	} else if sqlDB, err := database.DB.DB(); err != nil || sqlDB.Ping() != nil {
		checks["database"] = "unavailable"
		ready = false
	} else {
		checks["database"] = "ok"
	}

	redisURL := strings.TrimSpace(os.Getenv("REDIS_URL"))
	env := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
	if redisURL == "" {
		if env == "production" || env == "prod" {
			checks["redis"] = "missing"
			ready = false
		} else {
			checks["redis"] = "optional"
		}
	} else if opt, err := redis.ParseURL(redisURL); err != nil {
		checks["redis"] = "invalid"
		ready = false
	} else {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		client := redis.NewClient(opt)
		err = client.Ping(ctx).Err()
		_ = client.Close()
		cancel()
		if err != nil {
			checks["redis"] = "unavailable"
			ready = false
		} else {
			checks["redis"] = "ok"
		}
	}

	status := http.StatusOK
	if !ready {
		status = http.StatusServiceUnavailable
	}
	c.JSON(status, gin.H{"status": map[bool]string{true: "ready", false: "not_ready"}[ready], "checks": checks})
}
