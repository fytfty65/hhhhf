package handlers

import (
	"encoding/json"
	"sort"
	"strconv"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"github.com/gin-gonic/gin"
)

// effectMetricMinSamples 决定“能不能给结论”。少于 3 次观察时只回原始计数，
// 前端据此显示“样本不足”，而不是把 1 次结果说成稳定效果。
const effectMetricMinSamples = 3

// PlanningEffectMetricsHandler 汇总可核对的效果指标。
//
// 这些指标只从已经落库的事实计算：规划事件、记账、预算设置、风险通知的已读时间。
// 算不出来的（例如需要人工标注真值的“未知数据识别准确率”）明确返回 unavailable，
// 不用别的近似字段顶替。
func PlanningEffectMetricsHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	tripID := strings.TrimSpace(c.Query("trip_id"))
	if tripID != "" {
		if _, ok := requireTripOrRoomAccess(c, tripID); !ok {
			return
		}
	}
	windowDays := 30
	if raw := strings.TrimSpace(c.Query("window_days")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed >= 1 && parsed <= 365 {
			windowDays = parsed
		}
	}
	cutoff := time.Now().UTC().Add(-time.Duration(windowDays) * 24 * time.Hour)

	planningData(c, gin.H{
		"window_days":           windowDays,
		"adoption":              effectAdoptionMetrics(userID, tripID, cutoff),
		"preservation":          effectPreservationMetrics(userID, tripID, cutoff),
		"user_edits":            effectEditMetrics(userID, tripID, cutoff),
		"budget_overrun":        effectBudgetOverrunMetrics(userID, tripID, cutoff),
		"risk_response":         effectRiskResponseMetrics(userID, tripID, cutoff),
		"unknown_data_accuracy": gin.H{
			"status":  "unavailable",
			"samples": 0,
			"reason":  "需要人工标注“实际未知/实际已知”才能计算准确率；目前只记录供应商返回里无法识别的字段比例，不能把它当作准确率。",
		},
	})
}

func effectEventCount(userID, tripID, kind string, cutoff time.Time) int64 {
	q := database.DB.Model(&models.PlanningEvent{}).
		Where("user_id = ? AND event_type = ? AND created_at >= ?", userID, kind, cutoff)
	if tripID != "" {
		q = q.Where("trip_id = ?", tripID)
	}
	var count int64
	if err := q.Count(&count).Error; err != nil {
		return 0
	}
	return count
}

// effectAdoptionMetrics：生成过的方案里，用户真正采纳的比例。
func effectAdoptionMetrics(userID, tripID string, cutoff time.Time) gin.H {
	generated := effectEventCount(userID, tripID, "plan_generated", cutoff)
	adopted := effectEventCount(userID, tripID, "plan_adopted", cutoff)
	status := "available"
	if generated < effectMetricMinSamples {
		status = "insufficient_data"
	}
	var rate any
	if generated > 0 {
		value := float64(adopted) / float64(generated)
		if value > 1 {
			// 事件可能先于 plan_generated 落库（例如补报采纳），比例超过 100% 没有意义。
			value = 1
		}
		rate = roundRatio(value)
	}
	return gin.H{"status": status, "generated": generated, "adopted": adopted, "rate": rate}
}

// effectPreservationMetrics：重规划后，原有节点被保留下来的比例。
// 分母取“保留 + 受影响”，只用真正记录了 affected_nodes 的事件。
func effectPreservationMetrics(userID, tripID string, cutoff time.Time) gin.H {
	q := database.DB.Where("user_id = ? AND event_type = ? AND created_at >= ?", userID, "route_replanned", cutoff)
	if tripID != "" {
		q = q.Where("trip_id = ?", tripID)
	}
	var events []models.PlanningEvent
	if err := q.Find(&events).Error; err != nil {
		return gin.H{"status": "insufficient_data", "samples": 0, "mean": nil}
	}
	samples := 0
	total := 0.0
	for _, event := range events {
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		preserved, valid := numericPayload(payload, "preserved_nodes")
		if !valid || preserved < 0 {
			continue
		}
		changed := 0
		if nodes, ok := payload["changed_nodes"].([]any); ok {
			changed = len(nodes)
		}
		denominator := preserved + float64(changed)
		if denominator <= 0 {
			continue
		}
		total += preserved / denominator
		samples++
	}
	status := "available"
	if samples < effectMetricMinSamples {
		status = "insufficient_data"
	}
	var mean any
	if samples > 0 {
		mean = roundRatio(total / float64(samples))
	}
	return gin.H{"status": status, "samples": samples, "mean": mean}
}

// effectEditMetrics：用户主动修改方案的次数。只数用户动作，不把系统执行的重排算进去。
func effectEditMetrics(userID, tripID string, cutoff time.Time) gin.H {
	kinds := []string{"plan_edited", "plan_swapped", "replan_triggered"}
	q := database.DB.Where("user_id = ? AND created_at >= ? AND event_type IN ?", userID, cutoff, kinds)
	if tripID != "" {
		q = q.Where("trip_id = ?", tripID)
	}
	var events []models.PlanningEvent
	if err := q.Find(&events).Error; err != nil {
		return gin.H{"status": "insufficient_data", "total": 0, "by_type": gin.H{}, "trips": 0}
	}
	byType := map[string]int{}
	trips := map[string]bool{}
	for _, event := range events {
		byType[event.EventType]++
		if event.TripID != "" {
			trips[event.TripID] = true
		}
	}
	status := "available"
	if len(events) == 0 {
		status = "insufficient_data"
	}
	return gin.H{"status": status, "total": len(events), "by_type": byType, "trips": len(trips)}
}

// effectBudgetOverrunMetrics：实际记账金额超过预算的行程占比。
// 只有“设了预算 + 有记账 + 币种一致”的行程才参与计算；没有记账不等于没超支。
func effectBudgetOverrunMetrics(userID, tripID string, cutoff time.Time) gin.H {
	q := database.DB.Where("user_id = ?", userID)
	if tripID != "" {
		q = q.Where("trip_id = ?", tripID)
	}
	var budgets []models.BudgetPlan
	if err := q.Find(&budgets).Error; err != nil {
		return gin.H{"status": "insufficient_data", "trips": 0, "overruns": 0, "rate": nil}
	}
	evaluated := 0
	overruns := 0
	for _, budget := range budgets {
		if budget.TotalBudgetCents <= 0 {
			continue
		}
		var expenses []models.ExpenseRecord
		if err := database.DB.Where("user_id = ? AND trip_id = ? AND created_at >= ?", userID, budget.TripID, cutoff).Find(&expenses).Error; err != nil {
			continue
		}
		if len(expenses) == 0 {
			continue
		}
		spent := int64(0)
		comparable := true
		for _, expense := range expenses {
			if !strings.EqualFold(strings.TrimSpace(expense.Currency), strings.TrimSpace(budget.Currency)) {
				comparable = false
				break
			}
			spent += expense.AmountCents
		}
		if !comparable {
			// 币种不一致时无法比较，跳过而不是按汇率猜一个数。
			continue
		}
		evaluated++
		if spent > budget.TotalBudgetCents {
			overruns++
		}
	}
	status := "available"
	if evaluated < effectMetricMinSamples {
		status = "insufficient_data"
	}
	var rate any
	if evaluated > 0 {
		rate = roundRatio(float64(overruns) / float64(evaluated))
	}
	return gin.H{"status": status, "trips": evaluated, "overruns": overruns, "rate": rate}
}

// effectRiskResponseMetrics：风险通知从推送到用户处理（已读/已确认）的耗时中位数。
// 未处理的提醒不计入分母，避免把“没人看”当成“响应得快”。
func effectRiskResponseMetrics(userID, tripID string, cutoff time.Time) gin.H {
	q := database.DB.Where("user_id = ? AND created_at >= ?", userID, cutoff)
	if tripID != "" {
		q = q.Where("trip_id = ?", tripID)
	}
	var notifications []models.RiskNotification
	if err := q.Find(&notifications).Error; err != nil {
		return gin.H{"status": "insufficient_data", "notifications": 0, "responded": 0, "response_rate": nil, "median_seconds": nil}
	}
	responded := 0
	durations := []float64{}
	for _, notification := range notifications {
		if notification.ReadAt == nil {
			continue
		}
		seconds := notification.ReadAt.Sub(notification.CreatedAt).Seconds()
		if seconds < 0 {
			seconds = 0
		}
		durations = append(durations, seconds)
		responded++
	}
	var median any
	if len(durations) > 0 {
		sort.Float64s(durations)
		middle := len(durations) / 2
		if len(durations)%2 == 0 {
			median = roundSeconds((durations[middle-1] + durations[middle]) / 2)
		} else {
			median = roundSeconds(durations[middle])
		}
	}
	var responseRate any
	if len(notifications) > 0 {
		responseRate = roundRatio(float64(responded) / float64(len(notifications)))
	}
	status := "available"
	if responded < effectMetricMinSamples {
		status = "insufficient_data"
	}
	return gin.H{"status": status, "notifications": len(notifications), "responded": responded, "response_rate": responseRate, "median_seconds": median}
}

func roundSeconds(value float64) float64 {
	if value < 0 {
		return 0
	}
	return float64(int(value*10+0.5)) / 10
}
