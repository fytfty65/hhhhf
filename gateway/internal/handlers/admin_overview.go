package handlers

import (
	"encoding/json"
	"net/http"
	"runtime"
	"sort"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/metrics"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
)

// 只读运维看板。
//
// 三条硬约束：
//  1. 身份来自服务端 ADMIN_USER_IDS 白名单（与照片审核同一套），不接受请求里自报的角色；
//  2. 只返回聚合计数，绝不返回任何用户的路书正文、聊天内容、账号或行程明细；
//  3. 每个比率都带真实分母；分母为 0 时返回 0，不用默认值编一个好看的数字。
//
// 进程启动时间按 handler 包加载时间记账，用于展示 uptime；
// 更细的请求级指标仍然在 /metrics（受 MetricsGuardMiddleware 保护）。
var adminProcessStartedAt = time.Now()

type adminEventRow struct {
	EventType string `gorm:"column:event_type"`
	Count     int64  `gorm:"column:count"`
}

func AdminOverviewHandler(c *gin.Context) {
	if !isAdminUser(c) {
		c.JSON(http.StatusForbidden, gin.H{"error": "需要管理员身份", "code": "ADMIN_FORBIDDEN"})
		return
	}
	db := database.DB
	if db == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "数据库不可用", "code": "ADMIN_DB_UNAVAILABLE"})
		return
	}

	now := time.Now().UTC()
	dayAgo := now.Add(-24 * time.Hour)
	weekAgo := now.AddDate(0, 0, -7)

	warnings := make([]string, 0, 4)
	count := func(model any, query string, args ...any) int64 {
		var total int64
		q := db.Model(model)
		if query != "" {
			q = q.Where(query, args...)
		}
		if err := q.Count(&total).Error; err != nil {
			warnings = append(warnings, "部分计数查询失败")
			return 0
		}
		return total
	}

	users := gin.H{
		"total":      count(&models.User{}, ""),
		"guests":     count(&models.User{}, "is_guest = ?", true),
		"new_7d":     count(&models.User{}, "created_at >= ?", weekAgo),
		"active_24h": count(&models.User{}, "last_active_at >= ?", dayAgo),
	}

	trips := gin.H{
		"total":   count(&models.TripPlan{}, ""),
		"last_7d": count(&models.TripPlan{}, "created_at >= ?", weekAgo),
	}

	rooms := gin.H{
		"total":   count(&models.Room{}, ""),
		"members": count(&models.RoomMember{}, ""),
		"last_7d": count(&models.Room{}, "created_at >= ?", weekAgo),
	}

	feedback := gin.H{
		"total":       count(&models.FeedbackLog{}, ""),
		"last_7d":     count(&models.FeedbackLog{}, "created_at >= ?", weekAgo),
		"positive_7d": count(&models.FeedbackLog{}, "created_at >= ? AND score > 0", weekAgo),
		"negative_7d": count(&models.FeedbackLog{}, "created_at >= ? AND score < 0", weekAgo),
	}

	// 规划事件：按类型聚合，只取最近 7 天、最多 15 种，避免把库里的历史噪音带出来。
	//
	// provider_quality 不进入这个列表：它的计数单位是「请求 × 品类 × 供应商」，
	// 一次用户点击可能产生 2~4 条（多供应商 / 交通同时探航班与火车），而
	// plan_generated 是「用户点一次生成记一条」。两者放同一个条形图里比较，
	// 会被读成「质量采样比生成还频繁」，是口径错误而不是业务异常。
	// 供应商探针单独聚合到 planning.provider_probes，并给出成功率与死供应商。
	events := make([]adminEventRow, 0, 15)
	if err := db.Model(&models.PlanningEvent{}).
		Select("event_type, COUNT(*) AS count").
		Where("created_at >= ?", weekAgo).
		Group("event_type").
		Order("count DESC").
		Limit(15).
		Scan(&events).Error; err != nil {
		warnings = append(warnings, "规划事件聚合失败")
	}
	sort.SliceStable(events, func(i, j int) bool { return events[i].Count > events[j].Count })

	eventTotal := int64(0)
	byType := make([]gin.H, 0, len(events))
	for _, row := range events {
		if row.EventType == "provider_quality" {
			continue
		}
		eventTotal += row.Count
		byType = append(byType, gin.H{"type": row.EventType, "count": row.Count})
	}

	// 采纳率 = plan_adopted / plan_generated（同为最近 7 天）。
	// 这两个数单独查，不依赖上面被 top-15 截断的列表：类型多起来时
	// plan_generated 掉出榜单会把采纳率的分子分母一起算成 0。
	// 分母为 0 时返回 0 并保留分母字段，前端能看出"没有样本"而不是"采纳率为 0%"。
	generated := count(&models.PlanningEvent{}, "event_type = ? AND created_at >= ?", "plan_generated", weekAgo)
	adopted := count(&models.PlanningEvent{}, "event_type = ? AND created_at >= ?", "plan_adopted", weekAgo)
	adoptionRate := 0.0
	if generated > 0 {
		adoptionRate = float64(adopted) / float64(generated)
	}

	providerProbes := adminProviderProbeSummary(weekAgo, generated)
	if providerProbes == nil {
		warnings = append(warnings, "供应商探针聚合失败")
	}

	planning := gin.H{
		"last_7d":         eventTotal,
		"by_type":         byType,
		"generated_7d":    generated,
		"adopted_7d":      adopted,
		"adoption_rate":   roundRate(adoptionRate),
		"provider_probes": providerProbes,
	}

	var memStats runtime.MemStats
	runtime.ReadMemStats(&memStats)
	runtimeBlock := gin.H{
		"uptime_seconds": int64(time.Since(adminProcessStartedAt).Seconds()),
		"goroutines":     runtime.NumGoroutine(),
		"heap_alloc_mb":  roundRate(float64(memStats.HeapAlloc) / (1024 * 1024)),
		"gc_cycles":      memStats.NumGC,
		"go_version":     runtime.Version(),
	}

	dbOK := true
	if sqlDB, err := db.DB(); err != nil || sqlDB.Ping() != nil {
		dbOK = false
		warnings = append(warnings, "数据库 ping 失败")
	}

	c.JSON(http.StatusOK, gin.H{
		"data": gin.H{
			"generated_at": now.Format(time.RFC3339),
			"window_days":  7,
			"scope":        "aggregates_only",
			"health": gin.H{
				"db_ok": dbOK,
			},
			"users":    users,
			"trips":    trips,
			"rooms":    rooms,
			"feedback": feedback,
			"planning": planning,
			"security": metrics.SecurityEventCounts(),
			"runtime":  runtimeBlock,
			"warnings": warnings,
		},
	})
}

// 保留两位小数，避免看板出现 0.30000000000000004 这类浮点噪声。
func roundRate(value float64) float64 {
	return float64(int(value*100+0.5)) / 100
}

// adminProbeStat 是供应商探针的聚合结果。name 的含义由所在列表决定：
// by_kind 里是品类（transport/lodging/scenic…），by_provider 里是供应商标签。
type adminProbeStat struct {
	Name         string `json:"name"`
	Probes       int64  `json:"probes"`
	OK           int64  `json:"ok"`
	Empty        int64  `json:"empty"`
	Candidates   int64  `json:"candidates"`
	AvgLatencyMs int64  `json:"avg_latency_ms"`
	latencySum   int64
}

// adminProviderProbeSummary 聚合最近窗口内的 provider_quality 事件。
//
// 这些事件由网关在每次调用供应商后写入，一条 = 一次「请求 × 品类 × 供应商」
// 的探测，不是用户行为。所以它必须与 plan_generated 分开计数、单独展示：
// 拿它和生成数并列，会被读成"质量采样比生成还频繁"，属于口径错误。
//
// 返回 nil 表示查询失败（调用方会把它折进 warnings）。只读 payload 中的
// 聚合字段，不涉及任何用户内容。
func adminProviderProbeSummary(since time.Time, generated int64) gin.H {
	db := database.DB
	if db == nil {
		return nil
	}
	type probeRow struct {
		Payload string `gorm:"column:payload"`
	}
	rows := make([]probeRow, 0, 256)
	if err := db.Model(&models.PlanningEvent{}).
		Select("payload").
		Where("event_type = ? AND created_at >= ?", "provider_quality", since).
		Order("id DESC").
		Limit(5000).
		Scan(&rows).Error; err != nil {
		return nil
	}

	kinds := make(map[string]*adminProbeStat, 8)
	providers := make(map[string]*adminProbeStat, 8)
	var total, okTotal, candidates, latencySum, latencySamples int64
	bucket := func(m map[string]*adminProbeStat, name string) *adminProbeStat {
		stat, exists := m[name]
		if !exists {
			stat = &adminProbeStat{Name: name}
			m[name] = stat
		}
		return stat
	}
	for _, row := range rows {
		var payload map[string]any
		if json.Unmarshal([]byte(row.Payload), &payload) != nil {
			continue
		}
		kind := strings.ToLower(strings.TrimSpace(stringValue(payload["kind"])))
		provider := strings.TrimSpace(stringValue(payload["provider"]))
		if kind == "" || provider == "" {
			continue
		}
		status := strings.ToLower(strings.TrimSpace(stringValue(payload["status"])))
		probeCandidates := int64(intValue(payload["candidate_count"]))
		latency := int64(intValue(payload["latency_ms"]))
		total++
		candidates += probeCandidates
		latencySum += latency
		latencySamples++
		if status == "ok" {
			okTotal++
		}
		for _, stat := range []*adminProbeStat{bucket(kinds, kind), bucket(providers, provider)} {
			stat.Probes++
			stat.Candidates += probeCandidates
			stat.latencySum += latency
			if status == "ok" {
				stat.OK++
			} else {
				stat.Empty++
			}
		}
	}

	// 按探针次数降序；次数相同按名称稳定排序，避免看板每次刷新顺序抖动。
	list := func(m map[string]*adminProbeStat) []adminProbeStat {
		out := make([]adminProbeStat, 0, len(m))
		for _, stat := range m {
			if stat.Probes > 0 {
				stat.AvgLatencyMs = stat.latencySum / stat.Probes
			}
			out = append(out, *stat)
		}
		sort.SliceStable(out, func(i, j int) bool {
			if out[i].Probes != out[j].Probes {
				return out[i].Probes > out[j].Probes
			}
			return out[i].Name < out[j].Name
		})
		return out
	}

	byProvider := list(providers)
	// 死供应商 = 有探针但一次都没成功。这类条目通常是缺凭据或上游改版，
	// 直接暴露出来，比让运营从「成功率」里自己算更快。
	failing := make([]gin.H, 0, 4)
	for _, stat := range byProvider {
		if stat.OK == 0 {
			failing = append(failing, gin.H{"provider": stat.Name, "probes": stat.Probes})
		}
	}

	successRate := 0.0
	if total > 0 {
		successRate = float64(okTotal) / float64(total)
	}
	avgLatency := int64(0)
	if latencySamples > 0 {
		avgLatency = latencySum / latencySamples
	}
	perGenerated := 0.0
	if generated > 0 {
		perGenerated = float64(total) / float64(generated)
	}

	return gin.H{
		"unit":              "request_x_kind_x_provider",
		"total":             total,
		"ok":                okTotal,
		"empty":             total - okTotal,
		"success_rate":      roundRate(successRate),
		"candidates":        candidates,
		"avg_latency_ms":    avgLatency,
		"per_generated":     roundRate(perGenerated),
		"by_kind":           list(kinds),
		"by_provider":       byProvider,
		"failing_providers": failing,
	}
}
