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
	"gateway/internal/push"
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

// maxMetricPaths bounds how many distinct path labels the in-process metrics and
// the Prometheus histograms may hold. Everything past the cap is folded into
// metricPathOverflow so cardinality (and therefore memory) can never grow with
// caller-supplied input.
const maxMetricPaths = 200

// metricPathOverflow is the label every path beyond maxMetricPaths is recorded
// under. It is deliberately not a valid URL so it cannot collide with a route.
const metricPathOverflow = "__other__"

// metricPath returns the bounded label used for per-path counters and
// histograms.
//
// It MUST be the matched route pattern (c.FullPath()), never the raw request
// URL. The previous implementation used c.Request.URL.Path, which made the
// label attacker-controlled: LoggingMiddleware runs globally (including for
// unmatched 404/401 requests), so any unauthenticated caller could add one
// permanent entry per unique path to requestsByPath, latencyByPath,
// latencyCountByPath and pathHistogram — unbounded memory growth from a
// trivial request loop. Unmatched requests now collapse into a single
// "unmatched" series instead of one series per junk path.
func metricPath(c *gin.Context) string {
	if full := c.FullPath(); full != "" {
		return full
	}
	return "unmatched"
}

// metricStatus keeps the status dimension bounded. The set of valid HTTP status
// codes is fixed, so this is not strictly necessary for memory safety; it keeps
// the series count (and the /metrics payload) small while preserving every code
// that actually signals something about this service.
func metricStatus(status int) string {
	switch status {
	case 200, 201, 202, 204, 301, 302, 304, 400, 401, 403, 404, 405, 409, 413, 415, 422, 429, 500, 502, 503, 504:
		return fmt.Sprintf("%d", status)
	default:
		// Bucket by class so a scanner cannot mint thousands of distinct codes.
		return fmt.Sprintf("%dxx", status/100)
	}
}

var (
	metricsMu          sync.Mutex
	requestsTotal      int
	requestsByPath     = map[string]int{}
	requestsByStatus   = map[string]int{}
	latencyByPath      = map[string]float64{}
	latencyCountByPath = map[string]int{}
	// requestsDroppedFromPath counts requests whose path label was folded into
	// metricPathOverflow because the cardinality cap was reached. If this is
	// climbing, either the route table grew past maxMetricPaths or something is
	// generating synthetic paths; either way it is alertable rather than silent.
	requestsDroppedFromPath int
	startTime               = time.Now()
)

func round2(v float64) float64 {
	return math.Round(v*100) / 100
}

// recordRequest accumulates the in-process counters. The path argument must
// already be bounded (see metricPath).
func recordRequest(method, path string, status int, latencyMs float64) {
	metricsMu.Lock()
	defer metricsMu.Unlock()
	requestsTotal++
	key := method + " " + path
	// Cardinality guard. Two rules matter here:
	//   1. The main cap is maxMetricPaths-1 so that the overflow bucket always has
	//      a slot of its own — otherwise the bucket created on first overflow
	//      pushes the map to maxMetricPaths+1 and the cap leaks by one.
	//   2. A label that literally collides with the reserved bucket name is
	//      folded into the bucket rather than trusted, so a caller cannot occupy
	//      (or dilute) the overflow series by requesting that path.
	if _, seen := requestsByPath[key]; !seen && (len(requestsByPath) >= maxMetricPaths-1 || path == metricPathOverflow) {
		requestsDroppedFromPath++
		key = method + " " + metricPathOverflow
	}
	requestsByPath[key]++
	requestsByStatus[metricStatus(status)]++
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

		// Metrics use the matched route pattern (bounded); the log line keeps the
		// raw path so 404s stay debuggable without polluting the metric labels.
		label := metricPath(c)
		recordRequest(c.Request.Method, label, status, latencyMs)
		recordPrometheusLatency(c.Request.Method, label, latencyMs)

		entry := map[string]interface{}{
			"ts":         time.Now().UnixMilli(),
			"service":    "gateway",
			"event":      "http_request",
			"request_id": rid,
			"method":     c.Request.Method,
			"path":       c.Request.URL.Path,
			"route":      label,
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
		// Cardinality guard visibility: how many requests were folded into the
		// overflow bucket because the path cap was reached.
		"metric_paths_dropped_to_overflow": requestsDroppedFromPath,
		"metric_path_cap":                  maxMetricPaths,
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
	warnings := map[string]string{}
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

	// External Web Push is opt-in. Report missing FCM configuration explicitly
	// so operators do not mistake an enabled-but-nonfunctional worker for a
	// healthy notification path. Staging dry-run does not need credentials.
	workerEnabled := strings.EqualFold(strings.TrimSpace(os.Getenv("PROVIDER_QUALITY_WEB_PUSH_WORKER_ENABLED")), "true")
	dryRun := strings.EqualFold(strings.TrimSpace(os.Getenv("PROVIDER_QUALITY_WEB_PUSH_DRY_RUN")), "true") && (env == "staging" || env == "test")
	switch {
	case !workerEnabled:
		checks["provider_quality_web_push"] = "disabled"
	case dryRun:
		checks["provider_quality_web_push"] = "dry_run"
	default:
		if _, err := push.NewSenderFromEnv(); err != nil {
			checks["provider_quality_web_push"] = "warning"
			warnings["provider_quality_web_push"] = "worker enabled but FCM configuration is unavailable"
			if env == "production" || env == "prod" {
				ready = false
			}
		} else {
			checks["provider_quality_web_push"] = "configured"
		}
	}

	status := http.StatusOK
	if !ready {
		status = http.StatusServiceUnavailable
	}
	response := gin.H{"status": map[bool]string{true: "ready", false: "not_ready"}[ready], "checks": checks}
	if len(warnings) > 0 {
		response["warnings"] = warnings
	}
	c.JSON(status, response)
}
