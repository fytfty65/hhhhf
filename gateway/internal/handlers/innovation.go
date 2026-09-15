package handlers

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"gateway/internal/contracts"
	"gateway/internal/database"
	"gateway/internal/models"
	planningService "gateway/internal/service"
	"github.com/gin-gonic/gin"
)

// FeatureSnapshotHandler exposes a privacy-scoped feature view. Raw event
// payloads are not returned; only aggregate counts and the existing profile
// projection are made available to planning agents.
func FeatureSnapshotHandler(c *gin.Context) {
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
	var feedback, events int64
	database.DB.Model(&models.FeedbackLog{}).Where("user_id = ?", userID).Count(&feedback)
	database.DB.Model(&models.PlanningEvent{}).Where("user_id = ?", userID).Count(&events)
	planningData(c, gin.H{"user_id": userID, "trip_id": tripID, "profile": planningService.ProfilePayload(userID), "aggregates": gin.H{"feedback_events": feedback, "planning_events": events}, "feature_policy": gin.H{"raw_text_excluded": true, "future_feedback": "explicit_and_implicit_separate"}, "generated_at": time.Now().UTC()})
}

// KnowledgeGraphHandler builds a lightweight route knowledge graph from
// user-supplied nodes. It represents structural relationships only; external
// cultural facts must be added by a sourced provider later.
func KnowledgeGraphHandler(c *gin.Context) {
	var req struct {
		contracts.PlanningContext
		Nodes []map[string]any `json:"nodes"`
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
	if _, ok := requireTripOrRoomAccess(c, ctx.TripID); !ok {
		return
	}
	if len(req.Nodes) > 200 {
		planningError(c, http.StatusBadRequest, "NODES_TOO_LARGE", "节点数量过多", nil)
		return
	}
	nodes := make([]gin.H, 0, len(req.Nodes))
	edges := make([]gin.H, 0)
	for index, raw := range req.Nodes {
		name := strings.TrimSpace(fmt.Sprint(raw["name"]))
		if name == "" || name == "<nil>" {
			name = strings.TrimSpace(fmt.Sprint(raw["location"]))
		}
		if name == "" || name == "<nil>" {
			continue
		}
		id := fmt.Sprintf("node-%d", index)
		nodes = append(nodes, gin.H{"id": id, "name": name, "day": raw["day"], "type": raw["type"], "source": "user_route_structure"})
		if index > 0 {
			edges = append(edges, gin.H{"from": fmt.Sprintf("node-%d", index-1), "to": id, "relation": "route_sequence"})
		}
	}
	planningData(c, gin.H{"trip_id": ctx.TripID, "nodes": nodes, "edges": edges, "fact_policy": "structural_only_until_sourced_knowledge", "source": candidateSource("omniroute-route-graph", true), "updated_at": time.Now().UTC()})
}

// AgentEvaluationHandler reports observable success/error counts by agent or
// module. It intentionally avoids inventing quality scores when no samples
// exist.
func AgentEvaluationHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	query := database.DB.Where("user_id = ?", userID)
	if tripID := strings.TrimSpace(c.Query("trip_id")); tripID != "" {
		if _, ok := requireTripOrRoomAccess(c, tripID); !ok {
			return
		}
		query = query.Where("trip_id = ?", tripID)
	}
	var events []models.PlanningEvent
	_ = query.Find(&events).Error
	metrics := map[string]map[string]int{}
	for _, event := range events {
		var payload map[string]any
		_ = json.Unmarshal([]byte(event.Payload), &payload)
		name := strings.TrimSpace(fmt.Sprint(payload["agent"]))
		if name == "" || name == "<nil>" {
			name = strings.TrimSpace(fmt.Sprint(payload["module"]))
		}
		if name == "" || name == "<nil>" {
			name = "gateway"
		}
		if metrics[name] == nil {
			metrics[name] = map[string]int{"events": 0, "success": 0, "errors": 0}
		}
		metrics[name]["events"]++
		if strings.Contains(strings.ToLower(event.EventType), "error") || strings.EqualFold(fmt.Sprint(payload["status"]), "error") {
			metrics[name]["errors"]++
		} else {
			metrics[name]["success"]++
		}
	}
	planningData(c, gin.H{"agents": metrics, "sample_count": len(events), "quality_policy": "aggregate_observability_only", "status": func() string {
		if len(events) == 0 {
			return "insufficient_data"
		}
		return "ready_for_offline_review"
	}()})
}

// SpecialistAdviceHandler provides deterministic, explainable specialist
// outputs. These are planning hints, not medical, accessibility or safety
// guarantees.
func SpecialistAdviceHandler(c *gin.Context) {
	var req struct{ contracts.PlanningContext }
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
	if _, ok := requireTripOrRoomAccess(c, ctx.TripID); !ok {
		return
	}
	accessibility := map[string]any{"status": "not_requested", "recommendations": []string{}}
	if value := strings.TrimSpace(fmt.Sprint(mapValue(ctx.Profile, "accessibility"))); value != "" && value != "<nil>" {
		accessibility = map[string]any{"status": "requested", "recommendations": []string{"优先无障碍入口与电梯路线", "为换乘和休息预留缓冲"}, "source": "profile_rule_estimate", "estimated": true}
	}
	health := map[string]any{"status": "not_requested", "recommendations": []string{}}
	if mapValue(ctx.Profile, "health") != nil {
		health = map[string]any{"status": "requested", "recommendations": []string{"避开连续高强度节点", "准备个人药品与紧急联系人信息"}, "source": "profile_rule_estimate", "estimated": true}
	}
	culture := map[string]any{"status": "available", "recommendations": []string{"为人文节点补充历史背景与礼仪提示", "优先使用官方讲解或博物馆资料"}, "source": "culture_agent_rules", "estimated": true}
	green := map[string]any{"status": "available", "recommendations": []string{"优先公共交通、步行和景区接驳", "在时间可接受范围内比较碳排差异"}, "source": "carbon_agent_rules", "estimated": true}
	planningData(c, gin.H{"trip_id": ctx.TripID, "specialists": gin.H{"culture": culture, "accessibility": accessibility, "health": health, "green": green}, "disclaimer": "专业建议需由用户与实际服务提供方确认"})
}

// CrowdForecastHandler supplies a transparent forecast baseline when no live
// crowd provider is configured. A live provider response is passed through
// the existing candidate contract and is never replaced by this estimate.
func CrowdForecastHandler(c *gin.Context) {
	var req struct {
		contracts.PlanningContext
		Date    string `json:"date"`
		Hour    int    `json:"hour"`
		Weekend bool   `json:"weekend"`
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
	if _, ok := requireTripOrRoomAccess(c, ctx.TripID); !ok {
		return
	}
	if options, source, available := fetchPlanningProvider(c.Request.Context(), "CROWD", ctx); available {
		planningData(c, gin.H{"forecast": options, "source": source, "estimated": false, "policy": "provider_first"})
		return
	}
	level := "medium"
	score := 0.55
	if req.Weekend {
		level, score = "high", 0.78
	}
	if req.Hour >= 10 && req.Hour <= 15 {
		score += 0.1
	}
	planningData(c, gin.H{"forecast": gin.H{"level": level, "score": score, "confidence": 0.35, "date": req.Date, "hour": req.Hour}, "source": candidateSource("historical_calendar_estimator", true), "estimated": true, "policy": "provider_first_fallback_estimate"})
}

func MultilingualGuideHandler(c *gin.Context) {
	var req struct {
		contracts.PlanningContext
		TargetLanguage string   `json:"target_language"`
		Phrases        []string `json:"phrases"`
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
	if _, ok := requireTripOrRoomAccess(c, ctx.TripID); !ok {
		return
	}
	lang := strings.ToLower(strings.TrimSpace(req.TargetLanguage))
	if lang == "" {
		lang = "en"
	}
	phrasebook := map[string]map[string]string{"en": {"请问洗手间在哪里": "Where is the restroom?", "谢谢": "Thank you", "请帮帮我": "Please help me"}, "ja": {"谢谢": "ありがとうございます", "请帮帮我": "助けてください"}}
	translations := map[string]string{}
	for _, phrase := range req.Phrases {
		if value, ok := phrasebook[lang][phrase]; ok {
			translations[phrase] = value
		} else {
			translations[phrase] = ""
		}
	}
	planningData(c, gin.H{"trip_id": ctx.TripID, "language": lang, "translations": translations, "untranslated": func() []string {
		missing := []string{}
		for _, phrase := range req.Phrases {
			if translations[phrase] == "" {
				missing = append(missing, phrase)
			}
		}
		return missing
	}(), "source": candidateSource("phrasebook-template", true), "translation_status": "template_only"})
}

func ARGuideHandler(c *gin.Context) {
	var req struct {
		contracts.PlanningContext
		Route []map[string]any `json:"route"`
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
	if _, ok := requireTripOrRoomAccess(c, ctx.TripID); !ok {
		return
	}
	anchors := []gin.H{}
	for index, node := range req.Route {
		if lnglat, ok := node["lnglat"].([]any); ok && len(lnglat) >= 2 {
			anchors = append(anchors, gin.H{"id": fmt.Sprintf("anchor-%d", index), "name": node["name"], "lnglat": lnglat, "day": node["day"], "tracking": "geo_anchor"})
		}
	}
	planningData(c, gin.H{"trip_id": ctx.TripID, "anchors": anchors, "render_mode": "web_ar_anchor_data", "camera_permission": "client_required", "source": candidateSource("omniroute-ar-anchor", true), "estimated": true})
}

func OfficialChannelClickHandler(c *gin.Context) {
	var req struct {
		TripID      string `json:"trip_id"`
		Channel     string `json:"channel"`
		Destination string `json:"destination"`
		TargetURL   string `json:"target_url"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_JSON", "请求数据格式不正确", nil)
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	req.TripID, req.Channel = strings.TrimSpace(req.TripID), strings.TrimSpace(req.Channel)
	if req.TripID != "" {
		if _, ok := requireTripOrRoomAccess(c, req.TripID); !ok {
			return
		}
	}
	if req.Channel == "" {
		planningError(c, http.StatusBadRequest, "CHANNEL_REQUIRED", "缺少渠道标识", nil)
		return
	}
	payload, _ := json.Marshal(gin.H{"channel": req.Channel, "destination": req.Destination, "target_url": req.TargetURL})
	event := models.PlanningEvent{UserID: userID, TripID: req.TripID, EventType: "official_channel_click", Payload: string(payload), CreatedAt: time.Now().UTC()}
	if err := database.DB.Create(&event).Error; err != nil {
		planningError(c, http.StatusInternalServerError, "CHANNEL_EVENT_WRITE_FAILED", "渠道点击记录失败", nil)
		return
	}
	planningData(c, gin.H{"accepted": true, "event_id": event.ID})
}

func OfficialChannelMetricsHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	var events []models.PlanningEvent
	query := database.DB.Where("user_id = ? AND event_type = ?", userID, "official_channel_click")
	if tripID := strings.TrimSpace(c.Query("trip_id")); tripID != "" {
		if _, ok := requireTripOrRoomAccess(c, tripID); !ok {
			return
		}
		query = query.Where("trip_id = ?", tripID)
	}
	_ = query.Find(&events).Error
	byChannel := map[string]int{}
	for _, event := range events {
		var payload map[string]any
		_ = json.Unmarshal([]byte(event.Payload), &payload)
		channel := strings.TrimSpace(fmt.Sprint(payload["channel"]))
		if channel == "" || channel == "<nil>" {
			channel = "unknown"
		}
		byChannel[channel]++
	}
	planningData(c, gin.H{"total_clicks": len(events), "by_channel": byChannel, "conversion_policy": "click_only_until_external_completion_callback", "status": "attribution_ready"})
}
