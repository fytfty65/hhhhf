package handlers

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"gateway/internal/contracts"
	"gateway/internal/database"
	"gateway/internal/models"
	planningService "gateway/internal/service"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

func envFloat(key string, fallback float64) float64 {
	if value, err := strconv.ParseFloat(strings.TrimSpace(os.Getenv(key)), 64); err == nil && value >= 0 {
		return value
	}
	return fallback
}

func meanFloat(values []float64) float64 {
	if len(values) == 0 {
		return 0
	}
	total := 0.0
	for _, value := range values {
		total += value
	}
	return total / float64(len(values))
}

func fairnessStats(values []float64) (minimum, variance, stddev, maxRegret float64) {
	if len(values) == 0 {
		return
	}
	minimum = values[0]
	maximum := values[0]
	for _, value := range values {
		if value < minimum {
			minimum = value
		}
		if value > maximum {
			maximum = value
		}
	}
	mean := meanFloat(values)
	for _, value := range values {
		delta := value - mean
		variance += delta * delta
	}
	variance /= float64(len(values))
	stddev = math.Sqrt(variance)
	maxRegret = maximum - minimum
	return
}

func seasonalCalibration(startDate string, signals gin.H) gin.H {
	month := int(time.Now().Month())
	if parsed, err := time.Parse("2006-01-02", strings.TrimSpace(startDate)); err == nil {
		month = int(parsed.Month())
	}
	season := "winter"
	switch {
	case month >= 3 && month <= 5:
		season = "spring"
	case month >= 6 && month <= 8:
		season = "summer"
	case month >= 9 && month <= 11:
		season = "autumn"
	}
	seasonFactor := map[string]float64{"spring": 1.05, "summer": 1.15, "autumn": 1.0, "winter": 0.9}[season]
	holidayFactor := 1.0
	if month == 1 || month == 5 || month == 10 {
		holidayFactor = 1.2
	}
	crowdFactor := 1.0
	crowdSource := "estimated_historical_pattern"
	if crowd, ok := signals["crowd"].(gin.H); ok && crowd["available"] == true {
		if data, ok := crowd["data"].(map[string]any); ok {
			for _, key := range []string{"crowd_factor", "crowdFactor", "level", "score", "occupancy"} {
				if value, exists := data[key]; exists {
					if n, ok := numericPlanningValue(value); ok {
						crowdFactor = n
						if crowdFactor > 2 {
							crowdFactor /= 100
						}
						break
					}
				}
			}
		}
		if source, ok := crowd["source"].(contracts.Source); ok {
			crowdSource = source.Provider
		}
	}
	return gin.H{"month": month, "season": season, "season_factor": seasonFactor, "holiday_factor": holidayFactor, "crowd_factor": crowdFactor, "crowd_source": crowdSource, "weather_switch": "雨天优先室内，晴天优先户外", "estimated": crowdSource == "estimated_historical_pattern"}
}

func jointParetoAudit(items []gin.H) gin.H {
	frontier := []gin.H{}
	rejected := []gin.H{}
	for i, candidate := range items {
		cost, _ := numericPlanningValue(candidate["estimated_cost"])
		duration, _ := numericPlanningValue(candidate["estimated_duration_minutes"])
		carbon, _ := numericPlanningValue(candidate["carbon_kg"])
		satisfaction, _ := numericPlanningValue(candidate["satisfaction"])
		dominatedBy := []string{}
		for j, other := range items {
			if i == j {
				continue
			}
			oc, _ := numericPlanningValue(other["estimated_cost"])
			od, _ := numericPlanningValue(other["estimated_duration_minutes"])
			ob, _ := numericPlanningValue(other["carbon_kg"])
			os, _ := numericPlanningValue(other["satisfaction"])
			if oc <= cost && od <= duration && ob <= carbon && os >= satisfaction && (oc < cost || od < duration || ob < carbon || os > satisfaction) {
				dominatedBy = append(dominatedBy, fmt.Sprint(other["name"]))
			}
		}
		if len(dominatedBy) == 0 {
			frontier = append(frontier, candidate)
		} else {
			rejected = append(rejected, gin.H{"candidate": candidate["name"], "reason": "dominated", "dominated_by": dominatedBy})
		}
	}
	return gin.H{"frontier": frontier, "rejected": rejected, "frontier_count": len(frontier), "rejected_count": len(rejected)}
}

// replanParetoAudit compares only dimensions that are actually supplied by a
// candidate. Unknown supplier fields are never replaced with guessed values;
// candidates with insufficient evidence remain on the frontier and are
// explicitly marked for validation.
func replanParetoAudit(candidates []contracts.Candidate) gin.H {
	items := make([]gin.H, 0, len(candidates))
	for _, candidate := range candidates {
		item := gin.H{"id": candidate.ID, "name": candidate.Name, "score": candidate.Score, "satisfaction": candidate.Score, "source": candidate.Source, "estimated": candidate.Source.Estimated}
		known := []string{"satisfaction"}
		if candidate.Price > 0 {
			item["estimated_cost"], known = candidate.Price, append(known, "cost")
		}
		if candidate.Duration > 0 {
			item["estimated_duration_minutes"], known = candidate.Duration, append(known, "duration")
		}
		if candidate.Extra != nil {
			for _, key := range []string{"carbon_kg", "carbon_kg_estimate", "carbon"} {
				if value, ok := numericPlanningValue(candidate.Extra[key]); ok && value >= 0 {
					item["carbon_kg"], known = value, append(known, "carbon")
					break
				}
			}
		}
		item["known_dimensions"] = known
		items = append(items, item)
	}
	frontier := make([]gin.H, 0, len(items))
	rejected := make([]gin.H, 0)
	for i, item := range items {
		dominatedBy := []string{}
		for j, other := range items {
			if i == j {
				continue
			}
			betterOrEqual, strictlyBetter, shared := true, false, 0
			for _, dimension := range []struct {
				key           string
				lowerIsBetter bool
			}{
				{"estimated_cost", true}, {"estimated_duration_minutes", true}, {"carbon_kg", true}, {"satisfaction", false},
			} {
				left, lok := numericPlanningValue(item[dimension.key])
				right, rok := numericPlanningValue(other[dimension.key])
				if !lok || !rok {
					continue
				}
				shared++
				if dimension.lowerIsBetter {
					if right > left {
						betterOrEqual = false
					}
					if right < left {
						strictlyBetter = true
					}
				} else {
					if right < left {
						betterOrEqual = false
					}
					if right > left {
						strictlyBetter = true
					}
				}
			}
			if shared > 0 && betterOrEqual && strictlyBetter {
				dominatedBy = append(dominatedBy, fmt.Sprint(other["name"]))
			}
		}
		if len(dominatedBy) == 0 {
			item["pareto_status"] = "frontier"
			if len(item["known_dimensions"].([]string)) < 3 {
				item["validation_note"] = "供应商字段不足，应用前必须补齐约束数据"
			}
			frontier = append(frontier, item)
		} else {
			rejected = append(rejected, gin.H{"candidate": item["name"], "reason": "dominated_on_known_dimensions", "dominated_by": dominatedBy})
		}
	}
	return gin.H{"frontier": frontier, "rejected": rejected, "frontier_count": len(frontier), "rejected_count": len(rejected), "policy": "compare_known_supplier_dimensions_only"}
}

func planningData(c *gin.Context, data any) {
	c.JSON(http.StatusOK, contracts.Envelope{Data: data, Meta: contracts.Meta{RequestID: planningRequestID(c), Timestamp: time.Now().UTC(), Version: "2026-09-04"}})
}

func planningError(c *gin.Context, status int, code, message string, details map[string]string) {
	rid := planningRequestID(c)
	c.JSON(status, contracts.Envelope{Meta: contracts.Meta{RequestID: rid, Timestamp: time.Now().UTC(), Version: "2026-09-04"}, Error: &contracts.Error{Code: code, Message: message, RequestID: rid, Details: details}})
}

func planningRequestID(c *gin.Context) string {
	if rid := c.GetHeader("X-Request-ID"); rid != "" {
		return rid
	}
	return c.Writer.Header().Get("X-Request-ID")
}

func decodePlanningContext(c *gin.Context) (contracts.PlanningContext, bool) {
	var ctx contracts.PlanningContext
	if err := c.ShouldBindJSON(&ctx); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_JSON", "请求数据格式不正确", nil)
		return ctx, false
	}
	ctx.UserID = CurrentUserID(c)
	ctx.TripID = strings.TrimSpace(ctx.TripID)
	ctx.Destination = strings.TrimSpace(ctx.Destination)
	if ctx.UserID == "" {
		planningError(c, http.StatusUnauthorized, "AUTH_REQUIRED", "请先登录", nil)
		return ctx, false
	}
	if ctx.TripID == "" {
		planningError(c, http.StatusBadRequest, "TRIP_ID_REQUIRED", "缺少 trip_id，请先保存或创建行程", nil)
		return ctx, false
	}
	if _, authorized := requireTripOrRoomAccess(c, ctx.TripID); !authorized {
		return ctx, false
	}
	if ctx.Destination == "" {
		planningError(c, http.StatusBadRequest, "DESTINATION_REQUIRED", "缺少目的地", nil)
		return ctx, false
	}
	if ctx.Travelers < 1 {
		ctx.Travelers = 1
	}
	if ctx.Currency == "" {
		ctx.Currency = "CNY"
	}
	return ctx, true
}

// PlanningContextHandler validates and echoes the canonical context so the
// frontend can establish identity before calling domain agents.
func PlanningContextHandler(c *gin.Context) {
	ctx, ok := decodePlanningContext(c)
	if !ok {
		return
	}
	planningData(c, gin.H{"context": ctx, "ready": true})
}

// DigitalTwinHandler materializes the complete planning object used by all
// downstream modules. It does not fabricate live values; realtime fields are
// explicitly marked awaiting_provider until a supplier signal is attached.
func DigitalTwinHandler(c *gin.Context) {
	ctx, ok := decodePlanningContext(c)
	if !ok {
		return
	}
	hard := map[string]any{
		"start_date":         ctx.StartDate,
		"end_date":           ctx.EndDate,
		"budget":             ctx.Budget,
		"currency":           ctx.Currency,
		"must_visit":         []string{},
		"latest_return_time": "",
	}
	for key, value := range ctx.HardConstraints {
		hard[key] = value
	}
	if ctx.Preferences != nil {
		if raw, exists := ctx.Preferences["must_visit"]; exists {
			hard["must_visit"] = raw
		}
		if raw, exists := ctx.Preferences["latest_return_time"]; exists {
			hard["latest_return_time"] = raw
		}
	}
	soft := map[string]any{}
	for key, value := range ctx.Preferences {
		soft[key] = value
	}
	for key, value := range ctx.SoftPreferences {
		soft[key] = value
	}
	realtime := map[string]any{"status": "awaiting_provider", "fields": []string{"price", "availability", "weather", "crowd", "open_time", "risk"}}
	for key, value := range ctx.RealtimeState {
		realtime[key] = value
	}
	planningData(c, gin.H{"digital_twin": gin.H{
		"identity":         gin.H{"user_id": ctx.UserID, "trip_id": ctx.TripID, "room_id": ctx.RoomID, "travelers": ctx.Travelers, "origin": ctx.Origin, "destination": ctx.Destination},
		"profile":          ctx.Profile,
		"hard_constraints": hard,
		"soft_preferences": soft,
		"realtime_state":   realtime,
	}})
}

// DestinationRecommendHandler is a standalone destination ranking boundary.
// A configured DESTINATION provider can replace these transparent candidates;
// local rows remain explicitly estimated and contain no invented prices.
func DestinationRecommendHandler(c *gin.Context) {
	ctx, ok := decodePlanningContext(c)
	if !ok {
		return
	}
	if options, source, available := fetchPlanningProvider(c.Request.Context(), "DESTINATION", ctx); available {
		options = planningService.RankCandidates(options, ctx.Preferences)
		options = annotateCandidates(options, "destination")
		planningData(c, gin.H{"context": ctx, "candidates": options, "ranking": []string{"季节匹配", "兴趣匹配", "约束可行性", "实时风险"}, "source": source, "fallback": false})
		return
	}
	preferences := ctx.Preferences
	interest := "综合兴趣"
	if preferences != nil {
		if value, exists := preferences["interest"]; exists && strings.TrimSpace(fmt.Sprint(value)) != "" {
			interest = fmt.Sprint(value)
		}
	}
	names := []string{ctx.Destination + "核心人文与自然组合", ctx.Destination + "慢游街区与本地生活", ctx.Destination + "周边小众一日延展"}
	candidates := make([]contracts.Candidate, 0, len(names))
	for index, name := range names {
		candidates = append(candidates, contracts.Candidate{ID: uuid.NewString(), Name: name, Score: 0.72 - float64(index)*0.06, Tags: []string{interest}, Reasons: []string{"基于当前目的地和用户偏好生成", "等待季节与实时供应商信号校正"}, Constraints: []string{"需在日期、预算和实时风险校验后确认"}, Source: candidateSource("omniroute-destination-ranker", true)})
	}
	candidates = annotateCandidates(candidates, "destination")
	planningData(c, gin.H{"context": ctx, "candidates": candidates, "ranking": []string{"季节匹配", "兴趣匹配", "约束可行性", "实时风险"}, "fallback": true})
}

func candidateSource(provider string, estimated bool) contracts.Source {
	return contracts.Source{Provider: provider, Retrieved: time.Now().UTC(), Estimated: estimated}
}

// TransportOptionsHandler returns explainable multimodal candidates. Supplier
// adapters can replace these estimates without changing the contract.
func TransportOptionsHandler(c *gin.Context) {
	ctx, ok := decodePlanningContext(c)
	if !ok {
		return
	}
	if options, source, ok := fetchPlanningProvider(c.Request.Context(), "TRANSPORT", ctx); ok {
		options = planningService.RankCandidates(options, ctx.Preferences)
		options = annotateCandidates(options, "transport")
		planningData(c, gin.H{"context": ctx, "options": options, "optimization": gin.H{"objective": "time_cost_carbon", "fallback": false}, "source": source})
		return
	}
	amapQuery := url.QueryEscape(strings.TrimSpace(ctx.Origin + " 到 " + ctx.Destination))
	options := []contracts.Candidate{
		{ID: uuid.NewString(), Name: "铁路 + 城市公共交通", Score: 0.91, Duration: 285, Tags: []string{"低碳", "稳定"}, Reasons: []string{"换乘次数少", "到达后接驳成本低"}, Source: candidateSource("omniroute-estimator", true), Extra: map[string]any{"segments": []string{"铁路", "地铁/公交"}, "transfer_buffer_minutes": 45, "carbon_kg_estimate": 32, "estimated_price_range": "¥200–500/人", "price_status": "estimated", "booking_url": "https://www.12306.cn/index/", "booking_label": "前往 12306 核验车次"}},
		{ID: uuid.NewString(), Name: "航班 + 地铁", Score: 0.86, Duration: 210, Tags: []string{"快速"}, Reasons: []string{"换乘次数少", "预留机场安检时间"}, Source: candidateSource("omniroute-estimator", true), Extra: map[string]any{"segments": []string{"航班", "地铁"}, "transfer_buffer_minutes": 90, "carbon_kg_estimate": 128, "estimated_price_range": "¥500–1200/人", "price_status": "estimated", "booking_url": "https://flights.trip.com/", "booking_label": "前往 Trip.com 核验航班"}},
		{ID: uuid.NewString(), Name: "自驾 + 景区接驳", Score: 0.78, Duration: 330, Tags: []string{"灵活"}, Reasons: []string{"适合多人同行", "可携带更多行李"}, Source: candidateSource("omniroute-estimator", true), Extra: map[string]any{"segments": []string{"自驾", "景区接驳"}, "transfer_buffer_minutes": 20, "carbon_kg_estimate": 96, "estimated_price_range": "¥300–800/车", "price_status": "estimated", "booking_url": "https://www.amap.com/search?query=" + amapQuery, "booking_label": "在高德核验路线"}},
	}
	if pref, exists := ctx.Preferences["low_carbon"]; exists && pref == true {
		sort.SliceStable(options, func(i, j int) bool { return options[i].Tags[0] == "低碳" })
	}
	options = planningService.RankCandidates(options, ctx.Preferences)
	options = annotateCandidates(options, "transport")
	planningData(c, gin.H{"context": ctx, "options": options, "optimization": gin.H{"objective": "time_cost_carbon", "dimensions": []string{"fare", "duration", "transfer_buffer", "carbon_kg", "delay_risk"}, "fallback": true}})
}

func LodgingOptionsHandler(c *gin.Context) {
	ctx, ok := decodePlanningContext(c)
	if !ok {
		return
	}
	if options, source, ok := fetchPlanningProvider(c.Request.Context(), "LODGING", ctx); ok {
		options = planningService.RankCandidates(options, ctx.Preferences)
		options = annotateCandidates(options, "lodging")
		planningData(c, gin.H{"context": ctx, "options": options, "ranking": []string{"通勤成本", "价格", "安全", "用户偏好"}, "source": source})
		return
	}
	options := []contracts.Candidate{
		{ID: uuid.NewString(), Name: ctx.Destination + "核心景区附近精品酒店", Score: 0.90, Duration: 12, Tags: []string{"步行友好", "安静"}, Reasons: []string{"平均通勤短", "适合连续多日行程"}, Source: candidateSource("omniroute-estimator", true), Extra: map[string]any{"area": "景区周边", "estimated_price_range": "¥350–600/晚", "price_status": "estimated"}},
		{ID: uuid.NewString(), Name: ctx.Destination + "交通枢纽商圈酒店", Score: 0.84, Duration: 20, Tags: []string{"性价比", "换乘方便"}, Reasons: []string{"公共交通覆盖好", "预算压力较低"}, Source: candidateSource("omniroute-estimator", true), Extra: map[string]any{"area": "交通枢纽", "estimated_price_range": "¥220–420/晚", "price_status": "estimated"}},
	}
	options = planningService.RankCandidates(options, ctx.Preferences)
	options = annotateCandidates(options, "lodging")
	planningData(c, gin.H{"context": ctx, "options": options, "ranking": []string{"每日通勤成本", "价格", "安全", "用户偏好"}, "ranking_service": "planning-ranker-v1"})
}

func DiningOptionsHandler(c *gin.Context) {
	ctx, ok := decodePlanningContext(c)
	if !ok {
		return
	}
	if options, source, ok := fetchPlanningProvider(c.Request.Context(), "DINING", ctx); ok {
		options = planningService.RankCandidates(options, ctx.Preferences)
		options = annotateCandidates(options, "dining")
		planningData(c, gin.H{"context": ctx, "options": options, "ranking": []string{"营业时间", "距离", "口味偏好", "人均预算"}, "source": source})
		return
	}
	options := []contracts.Candidate{
		{ID: uuid.NewString(), Name: ctx.Destination + "本地风味小馆", Score: 0.92, Duration: 70, Tags: []string{"本地特色", "可预约"}, Reasons: []string{"靠近当日景点", "人均价格适中"}, Source: candidateSource("omniroute-estimator", true), Extra: map[string]any{"estimated_price_range": "¥60–120/人", "price_status": "estimated"}},
		{ID: uuid.NewString(), Name: ctx.Destination + "错峰早午餐路线", Score: 0.87, Duration: 50, Tags: []string{"错峰", "轻食"}, Reasons: []string{"避开晚餐排队", "适合紧凑行程"}, Source: candidateSource("omniroute-estimator", true), Extra: map[string]any{"estimated_price_range": "¥35–80/人", "price_status": "estimated"}},
		{ID: uuid.NewString(), Name: ctx.Destination + "夜间特色街区", Score: 0.83, Duration: 100, Tags: []string{"夜游", "美食"}, Reasons: []string{"与夜间路线衔接", "可分散客流"}, Source: candidateSource("omniroute-estimator", true), Extra: map[string]any{"estimated_price_range": "¥80–180/人", "price_status": "estimated"}},
	}
	options = planningService.RankCandidates(options, ctx.Preferences)
	options = annotateCandidates(options, "dining")
	planningData(c, gin.H{"context": ctx, "options": options, "ranking": []string{"营业时间", "距离", "口味偏好", "人均预算"}, "ranking_service": "planning-ranker-v1"})
}

// JointPlanHandler performs a deterministic, explainable second-stage
// optimization. Provider-backed agents can later replace candidate values;
// the response contract and constraint checks remain stable.
func JointPlanHandler(c *gin.Context) {
	var req struct {
		contracts.PlanningContext
		Days      int              `json:"days"`
		BaseNodes []map[string]any `json:"base_nodes"`
		Members   []struct {
			ID      string  `json:"id"`
			Weight  float64 `json:"weight"`
			Utility float64 `json:"utility"`
		} `json:"members"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_JSON", "请求数据格式不正确", nil)
		return
	}
	ctx := req.PlanningContext
	ctx.UserID = CurrentUserID(c)
	ctx.TripID = strings.TrimSpace(ctx.TripID)
	ctx.Destination = strings.TrimSpace(ctx.Destination)
	if ctx.UserID == "" {
		planningError(c, http.StatusUnauthorized, "AUTH_REQUIRED", "请先登录", nil)
		return
	}
	if ctx.TripID == "" {
		planningError(c, http.StatusBadRequest, "TRIP_ID_REQUIRED", "缺少 trip_id，请先保存或创建行程", nil)
		return
	}
	if _, authorized := requireTripOrRoomAccess(c, ctx.TripID); !authorized {
		return
	}
	if ctx.Destination == "" {
		planningError(c, http.StatusBadRequest, "DESTINATION_REQUIRED", "缺少目的地", nil)
		return
	}
	days := req.Days
	if days < 1 {
		days = 1
	}
	if days > 30 {
		days = 30
	}
	travelers := ctx.Travelers
	if travelers < 1 {
		travelers = 1
	}
	if travelers > 50 {
		travelers = 50
	}
	baseCost := 320.0 + 420.0*float64(days) + 180.0*float64(days) + 150.0*float64(len(req.BaseNodes))
	baseCost *= float64(travelers)
	budget := ctx.Budget
	feasible := budget <= 0 || baseCost <= budget
	selected := "铁路 + 城市公共交通 / 景区周边住宿 / 本地风味小馆"
	if budget > 0 && baseCost > budget {
		selected = "铁路 + 公共交通 / 交通枢纽住宿 / 错峰餐饮（已压缩非核心节点）"
	}
	memberCount := len(req.Members)
	if memberCount == 0 {
		memberCount = travelers
	}
	if memberCount < 1 {
		memberCount = 1
	}
	utilities := make([]float64, 0, memberCount)
	for _, member := range req.Members {
		u := member.Utility
		if u <= 0 {
			u = 0.78
		}
		if budget > 0 && baseCost > budget {
			u -= 0.07
		}
		if u < 0 {
			u = 0
		}
		if u > 1 {
			u = 1
		}
		utilities = append(utilities, u)
	}
	if len(utilities) == 0 {
		for i := 0; i < memberCount; i++ {
			utilities = append(utilities, 0.78)
		}
	}
	minSatisfaction, variance, stddev, maxRegret := fairnessStats(utilities)
	fairnessThreshold := envFloat("PLANNING_FAIRNESS_MAX_STD", 0.15)
	fairnessExceeded := stddev > fairnessThreshold
	signals := gin.H{"weather": gin.H{"available": false}, "crowd": gin.H{"available": false}}
	if data, source, available := fetchPlanningSignal(c.Request.Context(), "WEATHER", ctx); available {
		signals["weather"] = gin.H{"available": true, "data": data, "source": source}
	}
	if data, source, available := fetchPlanningSignal(c.Request.Context(), "CROWD", ctx); available {
		signals["crowd"] = gin.H{"available": true, "data": data, "source": source}
	}
	calibration := seasonalCalibration(ctx.StartDate, signals)
	// Resolve the server-side model assignment, then execute it only when an
	// endpoint is configured. This keeps attribution truthful in local fallback
	// environments while enabling real canary/shadow serving in production.
	decision := planningModelDecision(ctx.UserID)
	version, _ := decision["version"].(string)
	modelName, _ := decision["name"].(string)
	modelStatus, _ := decision["status"].(string)
	if version == "" {
		version = "builtin-joint-v1"
	}
	if modelName == "" {
		modelName = "estimator"
	}
	executionStatus := "fallback"
	var modelOutput map[string]any
	var primary models.ModelDeployment
	if err := database.DB.Where("version = ?", version).First(&primary).Error; err == nil && strings.TrimSpace(primary.Endpoint) != "" {
		execCtx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Second)
		modelOutput, err = invokeModelEndpoint(execCtx, primary, gin.H{"context": ctx, "days": days, "base_nodes": req.BaseNodes, "members": req.Members})
		cancel()
		if err != nil {
			executionStatus = "fallback_endpoint_error"
			// A canary that cannot serve is immediately removed from traffic.
			if modelStatus == "canary" {
				primary.Status, primary.Traffic = "rolled_back", 0
				_ = database.DB.Save(&primary).Error
				decision = planningModelDecision(ctx.UserID)
				version, _ = decision["version"].(string)
				modelName, _ = decision["name"].(string)
				modelStatus, _ = decision["status"].(string)
				if version == "" {
					version = "builtin-joint-v1"
				}
			}
		} else {
			executionStatus = "executed"
		}
	} else if modelStatus == "active" || modelStatus == "canary" {
		// The registry assignment is still useful for attribution, but without an
		// endpoint this is an explicit builtin fallback rather than a fake call.
		executionStatus = "fallback_no_endpoint"
	}
	if modelOutput != nil {
		if plan, ok := modelOutput["plan"].(map[string]any); ok {
			if name, ok := plan["name"].(string); ok && strings.TrimSpace(name) != "" {
				selected = name
			}
			if cost, ok := numericPlanningValue(plan["estimated_cost"]); ok && cost >= 0 && (budget <= 0 || cost <= budget) {
				baseCost = cost
				feasible = true
			}
		}
	}
	// Shadow models execute in parallel and never affect the selected route.
	shadowRuns := runShadowDeployments(c.Request.Context(), ctx, days, req.BaseNodes, req.Members)
	features, _ := json.Marshal(map[string]any{"module": "joint", "days": days, "travelers": travelers, "base_node_count": len(req.BaseNodes), "budget": budget, "estimated_cost": baseCost})
	run := models.PlanningRun{ID: uuid.NewString(), UserID: ctx.UserID, TripID: ctx.TripID, ModelVersion: version, Features: string(features), Estimated: executionStatus != "executed"}
	err := database.DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(&run).Error; err != nil {
			return err
		}
		payload, _ := json.Marshal(map[string]any{"module": "joint", "model_version": version, "estimated": run.Estimated, "execution_status": executionStatus})
		if err := tx.Create(&models.PlanningEvent{UserID: ctx.UserID, TripID: ctx.TripID, PlanID: run.ID, EventType: "plan_generated", Payload: string(payload)}).Error; err != nil {
			return err
		}
		for _, shadow := range shadowRuns {
			shadow["plan_id"] = run.ID
			shadow["module"] = "joint"
			shadow["model_version"] = shadow["version"]
			shadowPayload, _ := json.Marshal(shadow)
			if err := tx.Create(&models.PlanningEvent{UserID: ctx.UserID, TripID: ctx.TripID, PlanID: run.ID, EventType: "model_shadow_executed", Payload: string(shadowPayload)}).Error; err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		planningError(c, 503, "RUN_PERSIST_FAILED", "方案归因记录失败，请重试", nil)
		return
	}
	jointSource := candidateSource(modelName, executionStatus != "executed")
	alternatives := []gin.H{
		{"name": "时间优先", "estimated_cost": baseCost * 1.35, "estimated_duration_minutes": float64(days) * 420, "carbon_kg": float64(days) * 18, "satisfaction": 0.82, "estimated": true, "source": jointSource, "tradeoffs": []string{"耗时更短", "预算增加"}},
		{"name": "低碳优先", "estimated_cost": baseCost * 0.92, "estimated_duration_minutes": float64(days) * 560, "carbon_kg": float64(days) * 7, "satisfaction": 0.76, "estimated": true, "source": jointSource, "tradeoffs": []string{"碳排更低", "总耗时略长"}},
		{"name": "公平均衡", "estimated_cost": baseCost, "estimated_duration_minutes": float64(days) * 490, "carbon_kg": float64(days) * 12, "satisfaction": minSatisfaction, "estimated": true, "source": jointSource, "tradeoffs": []string{"成员满意度方差更低"}},
	}
	pareto := jointParetoAudit(alternatives)
	planningData(c, gin.H{
		"plan_id": run.ID,
		"context": ctx, "model": gin.H{"name": modelName, "version": version, "status": modelStatus, "execution": executionStatus, "estimated": executionStatus != "executed"}, "plan": gin.H{"name": selected, "estimated_cost": baseCost, "estimated": executionStatus != "executed", "source": jointSource, "currency": ctx.Currency, "days": days, "travelers": travelers, "preserved_nodes": len(req.BaseNodes)},
		"source":       jointSource,
		"alternatives": alternatives, "optimization": gin.H{"method": "multi_objective_pareto", "objectives": []string{"cost", "duration", "carbon", "satisfaction", "fairness"}, "weights": gin.H{"cost": 0.25, "duration": 0.2, "carbon": 0.15, "satisfaction": 0.25, "fairness": 0.15}, "pareto": pareto},
		"constraints": gin.H{"hard_satisfied": feasible, "violations": func() []string {
			if feasible {
				return []string{}
			}
			return []string{"预算上限"}
		}(), "objective": []string{"time", "cost", "carbon", "satisfaction"}},
		"fairness": gin.H{"members": memberCount, "member_utilities": utilities, "minimum_utility": minSatisfaction, "mean_utility": meanFloat(utilities), "utility_variance": variance, "utility_stddev": stddev, "max_regret": maxRegret, "threshold": fairnessThreshold, "threshold_exceeded": fairnessExceeded, "action": func() string {
			if fairnessExceeded {
				return "repair_route_or_reselect"
			}
			return "accepted"
		}(), "method": "maximin_utility_variance_regret", "minimum_satisfaction": minSatisfaction, "satisfaction_variance": variance},
		"signals": signals, "ranking_calibration": calibration, "shadow_runs": shadowRuns,
		"explain": []string{"优先保留总规划地点", "按每日通勤成本重排住宿", "餐饮避开核心拥堵时段"},
	})
}

func PackingChecklistHandler(c *gin.Context) {
	ctx, ok := decodePlanningContext(c)
	if !ok {
		return
	}
	items := make([]gin.H, 0, 12)
	seen := map[string]bool{}
	add := func(id, name, priority, category, reason string, estimated bool) {
		if seen[id] {
			return
		}
		seen[id] = true
		items = append(items, gin.H{"id": id, "name": name, "priority": priority, "category": category, "reason": reason, "source": candidateSource("omniroute-packing-rules", estimated), "estimated": estimated})
	}
	// Identity and booking evidence are always required to execute a trip;
	// the exact document set is refined when a supplier or user profile says so.
	add("documents", "身份证件、订单与保险凭证", "必带", "证件", "行程执行与入住核验", true)
	add("power", "充电器、充电宝与转换插头", "必带", "电子", "跨日出行基础装备", true)
	add("medicine", "常用药、个人处方药", "建议", "健康", "根据个人健康信息确认剂量", true)

	weather := mapValue(ctx.RealtimeState, "weather")
	if weatherMap, ok := weather.(map[string]any); ok {
		if rain, ok := weatherMap["rain"].(bool); ok && rain {
			add("rain", "轻便雨具与防水收纳袋", "必带", "天气", "天气供应商提示降雨", false)
		}
		if temperature, ok := numericPlanningValue(weatherMap["temperature"]); ok {
			if temperature < 10 {
				add("warm", "保暖层与围巾", "建议", "天气", "天气供应商温度低于 10°C", false)
			} else if temperature > 28 {
				add("sun", "防晒用品与遮阳帽", "建议", "天气", "天气供应商温度高于 28°C", false)
			}
		}
	} else {
		add("weather", "适配当地天气的换洗衣物", "必带", "衣物", "等待天气供应商信号后细化", true)
	}

	activity := strings.ToLower(fmt.Sprint(mapValue(ctx.Profile, "activity")))
	if activity == "" {
		activity = strings.ToLower(fmt.Sprint(mapValue(ctx.Preferences, "activity")))
	}
	if activity == "" && ctx.Preferences != nil {
		if _, exists := ctx.Preferences["hiking"]; exists {
			activity = "hiking"
		}
	}
	if strings.Contains(activity, "hike") || strings.Contains(activity, "徒步") || strings.Contains(activity, "登山") {
		add("hiking", "防滑鞋、速干衣与轻量雨具", "建议", "活动", "活动类型包含徒步/登山", true)
	}

	health := mapValue(ctx.Profile, "health")
	if health != nil {
		add("health_specific", "个人健康/过敏信息卡与应急药品", "必带", "健康", "根据用户健康画像生成", true)
	}
	if dietary, ok := ctx.Preferences["dietary"].(string); ok && strings.TrimSpace(dietary) != "" {
		add("dietary", "饮食过敏/忌口说明卡", "建议", "健康", "根据饮食偏好生成", true)
	}

	documents := mapValue(ctx.HardConstraints, "documents")
	if documents != nil {
		add("documents_extra", "按目的地规则准备签证/通行证原件", "证件事项", "证件", "硬约束 documents 字段要求", true)
	}
	if limit, ok := numericPlanningValue(mapValue(ctx.Profile, "luggage_limit")); ok && limit > 0 {
		add("luggage_check", fmt.Sprintf("按 %.0f kg 行李额度精简并称重", limit), "必带", "行李", "用户画像 luggage_limit", true)
	} else if limit, ok := numericPlanningValue(mapValue(ctx.HardConstraints, "luggage_limit")); ok && limit > 0 {
		add("luggage_check", fmt.Sprintf("按 %.0f kg 行李额度精简并称重", limit), "必带", "行李", "硬约束 luggage_limit", true)
	} else {
		add("luggage_check", "出发前核对承运人行李规则", "建议", "行李", "等待交通供应商行李规则", true)
	}
	add("local_purchase", "可在目的地购买的消耗品单独列账", "当地购买", "预算", "避免占用行李额度", true)
	planningData(c, gin.H{"context": ctx, "items": items, "generated_at": time.Now().UTC(), "linkage": gin.H{"weather": weather != nil, "activity": activity != "", "health": health != nil, "documents": documents != nil, "luggage_limit": mapValue(ctx.Profile, "luggage_limit") != nil || mapValue(ctx.HardConstraints, "luggage_limit") != nil}, "source": candidateSource("omniroute-packing-rules", true)})
}

func mapValue(values map[string]any, key string) any {
	if values == nil {
		return nil
	}
	return values[key]
}

func SecondaryItineraryHandler(c *gin.Context) {
	var req struct {
		contracts.PlanningContext
		Requirements string           `json:"requirements"`
		BaseNodes    []map[string]any `json:"base_nodes"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_JSON", "请求数据格式不正确", nil)
		return
	}
	ctx := req.PlanningContext
	ctx.UserID = CurrentUserID(c)
	ctx.TripID = strings.TrimSpace(ctx.TripID)
	ctx.Destination = strings.TrimSpace(ctx.Destination)
	if ctx.UserID == "" {
		planningError(c, http.StatusUnauthorized, "AUTH_REQUIRED", "请先登录", nil)
		return
	}
	if ctx.TripID == "" {
		planningError(c, http.StatusBadRequest, "TRIP_ID_REQUIRED", "缺少 trip_id，请先保存或创建行程", nil)
		return
	}
	if _, authorized := requireTripOrRoomAccess(c, ctx.TripID); !authorized {
		return
	}
	if ctx.Destination == "" {
		planningError(c, http.StatusBadRequest, "DESTINATION_REQUIRED", "缺少目的地", nil)
		return
	}
	req.Requirements = strings.TrimSpace(req.Requirements)
	if req.Requirements == "" {
		req.Requirements = "避开拥挤路线，优先特色店铺与短距离步行"
	}
	if options, source, available := fetchPlanningProvider(c.Request.Context(), "SCENIC", ctx); available {
		options = annotateCandidates(planningService.RankCandidates(options, ctx.Preferences), "scenic_micro")
		route := make([]gin.H, 0, len(options))
		for _, option := range options {
			crowd := "未知"
			if option.Extra != nil {
				if value, ok := option.Extra["crowd_level"].(string); ok && strings.TrimSpace(value) != "" {
					crowd = value
				}
			}
			route = append(route, gin.H{"name": option.Name, "type": "scenic", "crowd": crowd, "duration_minutes": option.Duration, "source": option.Source, "evidence": option.Evidence, "confidence": option.Confidence, "alternatives": option.Alternatives})
		}
		planningData(c, gin.H{"context": ctx, "level": "scenic_micro", "parent_plan_id": ctx.TripID, "requirements": req.Requirements, "route": route, "preserved_destination": true, "source": source, "fallback": false})
		return
	}
	planningData(c, gin.H{"context": ctx, "level": "scenic_micro", "parent_plan_id": ctx.TripID, "requirements": req.Requirements, "route": []gin.H{{"name": ctx.Destination + "特色入口", "type": "scenic", "crowd": "未知", "duration_minutes": 45, "source": candidateSource("omniroute-secondary-planner", true)}, {"name": ctx.Destination + "本地特色街区", "type": "dining", "crowd": "未知", "duration_minutes": 70, "source": candidateSource("omniroute-secondary-planner", true)}}, "preserved_destination": true, "source": candidateSource("omniroute-secondary-planner", true)})
}

// PreTripTasksHandler exposes deadline-aware tasks that belong to the same
// digital twin as the route. Rule-generated tasks are marked estimated until
// visa/insurance/telecom suppliers are connected.
func PreTripTasksHandler(c *gin.Context) {
	ctx, ok := decodePlanningContext(c)
	if !ok {
		return
	}
	tasks := []gin.H{
		{"id": "identity", "title": "核验身份证件与订单姓名", "category": "证件", "priority": "required", "due_date": ctx.StartDate, "source": candidateSource("omniroute-pretrip-rules", true)},
		{"id": "insurance", "title": "确认旅行保险保障范围", "category": "保险", "priority": "recommended", "due_date": ctx.StartDate, "source": candidateSource("omniroute-pretrip-rules", true)},
		{"id": "connectivity", "title": "准备通信、支付与紧急联系人", "category": "通信", "priority": "recommended", "due_date": ctx.StartDate, "source": candidateSource("omniroute-pretrip-rules", true)},
	}
	if ctx.Origin != "" && ctx.Origin != ctx.Destination {
		tasks = append(tasks, gin.H{"id": "transport_documents", "title": "确认跨城交通证件与换乘缓冲", "category": "交通", "priority": "required", "due_date": ctx.StartDate, "source": candidateSource("omniroute-pretrip-rules", true)})
	}
	planningData(c, gin.H{"context": ctx, "tasks": tasks, "deadline_policy": "due_date_defaults_to_start_date_until_provider_deadlines_are_available"})
}

// MinimalPerturbationReplanHandler keeps unaffected nodes intact and marks
// only impacted nodes for replacement. It never fabricates a new price,
// opening time or availability value when a provider is unavailable.
func MinimalPerturbationReplanHandler(c *gin.Context) {
	var req struct {
		contracts.PlanningContext
		ExistingRoute   []map[string]any `json:"existing_route"`
		AffectedNodes   []string         `json:"affected_nodes"`
		ChangedSignals  any              `json:"changed_signals"`
		ReplacementKind string           `json:"replacement_kind"`
		MemberUtilities []float64        `json:"member_utilities"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_JSON", "请求数据格式不正确", nil)
		return
	}
	ctx := req.PlanningContext
	ctx.UserID = CurrentUserID(c)
	ctx.TripID = strings.TrimSpace(ctx.TripID)
	ctx.Destination = strings.TrimSpace(ctx.Destination)
	if ctx.UserID == "" {
		planningError(c, http.StatusUnauthorized, "AUTH_REQUIRED", "请先登录", nil)
		return
	}
	if ctx.TripID == "" {
		planningError(c, http.StatusBadRequest, "TRIP_ID_REQUIRED", "缺少 trip_id，请先保存或创建行程", nil)
		return
	}
	if _, authorized := requireTripOrRoomAccess(c, ctx.TripID); !authorized {
		return
	}
	affected := map[string]bool{}
	for _, name := range req.AffectedNodes {
		affected[strings.TrimSpace(name)] = true
	}
	changed := 0
	for _, node := range req.ExistingRoute {
		name := strings.TrimSpace(fmt.Sprint(node["name"]))
		if name == "" || name == "<nil>" {
			name = strings.TrimSpace(fmt.Sprint(node["location"]))
		}
		if affected[name] {
			changed++
		}
	}
	// Executed nodes are immutable: a replan may only replace work that has
	// not already been visited or explicitly skipped.
	if database.DB != nil && len(affected) > 0 {
		var states []models.TripExecutionState
		if err := database.DB.Where("user_id = ? AND trip_id = ?", ctx.UserID, ctx.TripID).Find(&states).Error; err == nil {
			for _, state := range states {
				if state.Status == "visited" || state.Status == "skipped" {
					for name := range affected {
						if strings.HasSuffix(state.NodeKey, ":"+name) || state.NodeKey == name {
							planningError(c, http.StatusConflict, "NODE_ALREADY_COMPLETED", "已执行节点不可替换", map[string]string{"node": name, "status": state.Status})
							return
						}
					}
				}
			}
		}
	}
	perturbation := 0.0
	if len(req.ExistingRoute) > 0 {
		perturbation = float64(changed) / float64(len(req.ExistingRoute))
	}
	kind := strings.ToUpper(strings.TrimSpace(req.ReplacementKind))
	if kind == "" {
		kind = "DESTINATION"
	}
	allowedKinds := map[string]bool{"DESTINATION": true, "TRANSPORT": true, "LODGING": true, "DINING": true}
	if !allowedKinds[kind] {
		kind = "DESTINATION"
	}
	var replacements []contracts.Candidate
	replacementSource := candidateSource("omniroute-minimal-replan", true)
	if changed > 0 {
		if options, source, available := fetchPlanningProvider(c.Request.Context(), kind, ctx); available {
			replacements = annotateCandidates(planningService.RankCandidates(options, ctx.Preferences), strings.ToLower(kind))
			replacementSource = source
		}
	}
	replacementStatus := "awaiting_provider"
	if len(replacements) > 0 {
		replacementStatus = "provider_candidates_require_constraint_validation"
	}
	minimum, variance, stddev, maxRegret := fairnessStats(req.MemberUtilities)
	fairnessThreshold := envFloat("PLANNING_FAIRNESS_MAX_STD", 0.15)
	fairness := gin.H{"status": "not_evaluated", "threshold": fairnessThreshold, "threshold_exceeded": false}
	if len(req.MemberUtilities) > 0 {
		fairness = gin.H{"status": "evaluated", "member_utilities": req.MemberUtilities, "minimum_utility": minimum, "utility_variance": variance, "utility_stddev": stddev, "max_regret": maxRegret, "threshold": fairnessThreshold, "threshold_exceeded": stddev > fairnessThreshold, "action": func() string {
			if stddev > fairnessThreshold {
				return "repair_or_reselect"
			}
			return "within_threshold"
		}()}
	}
	planningData(c, gin.H{"context": ctx, "route": req.ExistingRoute, "preserved_nodes": len(req.ExistingRoute) - changed, "changed_nodes": changed, "perturbation_ratio": roundRatio(perturbation), "changed_signals": req.ChangedSignals, "replacement_kind": strings.ToLower(kind), "replacement_candidates": replacements, "replacement_status": replacementStatus, "pareto": replanParetoAudit(replacements), "fairness": fairness, "next_action": "仅替换 changed_nodes，并对候选执行硬约束、Pareto 与公平性裁决", "source": replacementSource})
}

// ValidateReplanCandidateHandler is the final, provider-agnostic hard
// constraint gate before a client persists a replacement node. It does not
// mutate the trip; callers must explicitly apply a passing result.
func ValidateReplanCandidateHandler(c *gin.Context) {
	var req struct {
		contracts.PlanningContext
		ExistingRoute []map[string]any `json:"existing_route"`
		AffectedNodes []string         `json:"affected_nodes"`
		Candidate     map[string]any   `json:"candidate"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_JSON", "请求数据格式不正确", nil)
		return
	}
	ctx := req.PlanningContext
	ctx.UserID = CurrentUserID(c)
	ctx.TripID = strings.TrimSpace(ctx.TripID)
	if ctx.UserID == "" {
		planningError(c, http.StatusUnauthorized, "AUTH_REQUIRED", "请先登录", nil)
		return
	}
	if ctx.TripID == "" {
		planningError(c, http.StatusBadRequest, "TRIP_ID_REQUIRED", "缺少 trip_id", nil)
		return
	}
	if _, authorized := requireTripOrRoomAccess(c, ctx.TripID); !authorized {
		return
	}
	violations := []string{}
	warnings := []string{}
	candidateName := strings.TrimSpace(fmt.Sprint(req.Candidate["name"]))
	if candidateName == "" || candidateName == "<nil>" {
		candidateName = strings.TrimSpace(fmt.Sprint(req.Candidate["location"]))
	}
	if candidateName == "" || candidateName == "<nil>" {
		violations = append(violations, "candidate_name_required")
	}
	if len(req.AffectedNodes) != 1 {
		violations = append(violations, "exactly_one_affected_node_required")
	}
	targetName := ""
	for _, name := range req.AffectedNodes {
		targetName = strings.TrimSpace(name)
		break
	}
	targetFound := false
	for _, node := range req.ExistingRoute {
		name := strings.TrimSpace(fmt.Sprint(node["name"]))
		if name == "" || name == "<nil>" {
			name = strings.TrimSpace(fmt.Sprint(node["location"]))
		}
		if name == targetName {
			targetFound = true
			break
		}
	}
	if !targetFound && targetName != "" {
		violations = append(violations, "affected_node_not_found")
	}
	// A candidate without provenance cannot be applied, even when its display
	// fields look valid. Estimated candidates are allowed but clearly warned.
	sourceProvider := strings.TrimSpace(fmt.Sprint(req.Candidate["source"]))
	if source, ok := req.Candidate["source"].(map[string]any); ok {
		sourceProvider = strings.TrimSpace(fmt.Sprint(source["provider"]))
		if sourceProvider == "" || sourceProvider == "<nil>" {
			sourceProvider = strings.TrimSpace(fmt.Sprint(source["Provider"]))
		}
		if estimated, ok := source["estimated"].(bool); ok && estimated {
			warnings = append(warnings, "candidate_fields_estimated_require_supplier_confirmation")
		}
	}
	if sourceProvider == "" || sourceProvider == "<nil>" || sourceProvider == "map[]" {
		violations = append(violations, "candidate_source_required")
	}
	if lnglat, ok := req.Candidate["lnglat"].([]any); ok {
		if len(lnglat) < 2 {
			violations = append(violations, "invalid_coordinates")
		} else {
			lng, lok := numericPlanningValue(lnglat[0])
			lat, latok := numericPlanningValue(lnglat[1])
			if !lok || !latok || lng < -180 || lng > 180 || lat < -90 || lat > 90 {
				violations = append(violations, "invalid_coordinates")
			}
		}
	} else if _, exists := req.Candidate["lnglat"]; exists {
		warnings = append(warnings, "coordinates_unavailable")
	} else {
		warnings = append(warnings, "coordinates_unavailable")
	}
	if price, ok := numericPlanningValue(req.Candidate["price"]); ok && price < 0 {
		violations = append(violations, "price_must_be_non_negative")
	}
	planningData(c, gin.H{"trip_id": ctx.TripID, "candidate": gin.H{"name": candidateName, "source": sourceProvider}, "hard_satisfied": len(violations) == 0, "violations": violations, "warnings": warnings, "checked_at": time.Now().UTC(), "policy": "validate_before_apply"})
}

func numericPlanningValue(value any) (float64, bool) {
	switch typed := value.(type) {
	case float64:
		return typed, true
	case float32:
		return float64(typed), true
	case int:
		return float64(typed), true
	case int64:
		return float64(typed), true
	case json.Number:
		parsed, err := typed.Float64()
		return parsed, err == nil
	default:
		return 0, false
	}
}

// runShadowDeployments invokes configured shadow endpoints concurrently. The
// returned records are persisted as observability events and are deliberately
// excluded from route selection.
func runShadowDeployments(parent context.Context, ctx contracts.PlanningContext, days int, baseNodes []map[string]any, members any) []map[string]any {
	var deployments []models.ModelDeployment
	if database.DB == nil || database.DB.Where("status = ? AND endpoint <> ''", "shadow").Find(&deployments).Error != nil || len(deployments) == 0 {
		return []map[string]any{}
	}
	type result struct {
		item map[string]any
	}
	results := make(chan result, len(deployments))
	var wait sync.WaitGroup
	for _, deployment := range deployments {
		deployment := deployment
		wait.Add(1)
		go func() {
			defer wait.Done()
			callCtx, cancel := context.WithTimeout(parent, 3*time.Second)
			defer cancel()
			output, err := invokeModelEndpoint(callCtx, deployment, gin.H{"context": ctx, "days": days, "base_nodes": baseNodes, "members": members, "shadow": true})
			item := map[string]any{"version": deployment.Version, "name": deployment.Name, "status": "executed", "endpoint": deployment.Endpoint != ""}
			if err != nil {
				item["status"] = "error"
				item["error"] = err.Error()
			} else if output == nil {
				item["status"] = "skipped"
			}
			results <- result{item: item}
		}()
	}
	wait.Wait()
	close(results)
	output := make([]map[string]any, 0, len(deployments))
	for item := range results {
		output = append(output, item.item)
	}
	sort.SliceStable(output, func(i, j int) bool { return output[i]["version"].(string) < output[j]["version"].(string) })
	return output
}
