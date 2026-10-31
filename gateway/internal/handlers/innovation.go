package handlers

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
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
	database.Reader().Model(&models.FeedbackLog{}).Where("user_id = ?", userID).Count(&feedback)
	database.Reader().Model(&models.PlanningEvent{}).Where("user_id = ?", userID).Count(&events)
	profile := planningService.ProfilePayload(userID)
	delete(profile, "prompt_hint")
	behavior := map[string]int{}
	channelBehavior := map[string]any{"clicks": 0, "returned": 0, "verified": 0, "adopted": 0, "by_module": map[string]map[string]int{}, "by_channel": map[string]map[string]int{}, "candidate_actions": map[string]map[string]int{}}
	var behaviorEvents []models.PlanningEvent
	behaviorQuery := database.Reader().Where("user_id = ?", userID)
	if tripID != "" {
		behaviorQuery = behaviorQuery.Where("trip_id = ?", tripID)
	}
	_ = behaviorQuery.Find(&behaviorEvents).Error
	for _, event := range behaviorEvents {
		if planningEventTrainableTypes[event.EventType] {
			behavior[event.EventType]++
		}
		if event.EventType == "official_channel_click" || event.EventType == "official_channel_returned" || event.EventType == "official_channel_verified" || event.EventType == "candidate_adopted" {
			var payload map[string]any
			if json.Unmarshal([]byte(event.Payload), &payload) != nil {
				continue
			}
			module := boundedFeatureText(payload["module"], "unknown")
			channel := boundedFeatureText(payload["channel"], "unknown")
			candidate := stableFeatureHash(boundedFeatureText(payload["candidate_id"], "unknown"))
			action := map[string]string{"official_channel_click": "clicks", "official_channel_returned": "returned", "official_channel_verified": "verified", "candidate_adopted": "adopted"}[event.EventType]
			if action == "" {
				continue
			}
			channelBehavior[action] = channelBehavior[action].(int) + 1
			byModule := channelBehavior["by_module"].(map[string]map[string]int)
			if byModule[module] == nil {
				byModule[module] = map[string]int{}
			}
			byModule[module][action]++
			byChannel := channelBehavior["by_channel"].(map[string]map[string]int)
			if byChannel[channel] == nil {
				byChannel[channel] = map[string]int{}
			}
			byChannel[channel][action]++
			candidateActions := channelBehavior["candidate_actions"].(map[string]map[string]int)
			if candidateActions[candidate] == nil {
				candidateActions[candidate] = map[string]int{}
			}
			candidateActions[candidate][action]++
		}
	}
	journey := map[string]any{"status": "not_selected", "trip_id": tripID}
	if tripID != "" {
		journey["status"] = "available"
		var run models.PlanningRun
		if err := database.Reader().Where("user_id = ? AND trip_id = ?", userID, tripID).Order("created_at desc").First(&run).Error; err == nil {
			var stored map[string]any
			if json.Unmarshal([]byte(run.Features), &stored) == nil {
				journey["features"] = stored
			}
			journey["model_version"] = run.ModelVersion
			journey["observed_at"] = run.CreatedAt
		}
	}
	planningData(c, gin.H{
		"user_id": userID, "trip_id": tripID, "feature_version": "features-v1",
		"groups": gin.H{
			"long_term": gin.H{"values": profile, "source": "preference_profile", "observed_at": time.Now().UTC()},
			"journey":   journey,
			"realtime":  gin.H{"status": "not_persisted", "fields": []string{"weather", "crowd", "delay", "open_status"}, "policy": "provider_snapshot_only"},
			"behavior":  gin.H{"counts": behavior, "channel_funnel": channelBehavior, "source": "planning_events", "raw_payload_excluded": true, "candidate_ids_hashed": true},
		},
		"aggregates":     gin.H{"feedback_events": feedback, "planning_events": events},
		"feature_policy": gin.H{"raw_text_excluded": true, "prompt_injection": "disabled", "future_feedback": "explicit_and_implicit_separate", "retention_days": 730},
		"generated_at":   time.Now().UTC(),
	})
}

func boundedFeatureText(value any, fallback string) string {
	text := strings.TrimSpace(fmt.Sprint(value))
	if text == "" || text == "<nil>" {
		return fallback
	}
	if len(text) > 80 {
		text = text[:80]
	}
	return text
}

func stableFeatureHash(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])[:16]
}

func candidateBehaviorFeatures(userID, tripID, module string) map[string]any {
	features := map[string]any{"module": module, "verified": 0, "adopted": 0, "sample_count": 0, "enabled": false, "reason": "sample_insufficient", "candidate_actions": map[string]map[string]int{}}
	if database.DB == nil || strings.TrimSpace(userID) == "" {
		return features
	}
	var events []models.PlanningEvent
	query := database.Reader().Where("user_id = ? AND event_type IN ?", userID, []string{"official_channel_verified", "candidate_adopted"})
	if strings.TrimSpace(tripID) != "" {
		query = query.Where("trip_id = ?", tripID)
	}
	if query.Find(&events).Error != nil {
		return features
	}
	for _, event := range events {
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		if strings.EqualFold(boundedFeatureText(payload["module"], ""), module) || strings.TrimSpace(module) == "" {
			features["sample_count"] = features["sample_count"].(int) + 1
			if event.EventType == "official_channel_verified" {
				features["verified"] = features["verified"].(int) + 1
			} else if event.EventType == "candidate_adopted" {
				features["adopted"] = features["adopted"].(int) + 1
			}
			candidateID := boundedFeatureText(payload["candidate_id"], "")
			if candidateID != "" {
				actions := features["candidate_actions"].(map[string]map[string]int)
				key := stableFeatureHash(candidateID)
				if actions[key] == nil {
					actions[key] = map[string]int{}
				}
				actions[key][map[string]string{"official_channel_verified": "verified", "candidate_adopted": "adopted"}[event.EventType]]++
			}
		}
	}
	if features["sample_count"].(int) >= 5 && (features["verified"].(int) > 0 || features["adopted"].(int) > 0) {
		features["enabled"] = true
		features["reason"] = "sample_sufficient"
	}
	return features
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
	rejectedFacts := make([]gin.H, 0)
	allowedRelations := map[string]bool{"city_attraction": true, "attraction_theme": true, "attraction_opening": true, "attraction_season": true, "attraction_audience": true, "attraction_transport": true, "attraction_risk": true, "dining_cuisine": true, "dining_allergen": true, "lodging_commute": true, "supplier_field": true}
	for index, raw := range req.Nodes {
		name := strings.TrimSpace(fmt.Sprint(raw["name"]))
		if name == "" || name == "<nil>" {
			name = strings.TrimSpace(fmt.Sprint(raw["location"]))
		}
		if name == "" || name == "<nil>" {
			continue
		}
		id := fmt.Sprintf("node-%d", index)
		nodes = append(nodes, gin.H{"id": id, "name": name, "day": raw["day"], "type": raw["type"], "source": "user_route_structure", "observed_at": time.Now().UTC(), "confidence": 1.0, "estimated": false})
		if index > 0 {
			edges = append(edges, gin.H{"from": fmt.Sprintf("node-%d", index-1), "to": id, "relation": "route_sequence", "source": "user_route_structure", "observed_at": time.Now().UTC(), "confidence": 1.0, "estimated": false})
		}
		// Facts are accepted only through an explicit verified_facts array. This
		// prevents an LLM-generated description from silently becoming graph truth.
		if facts, ok := raw["verified_facts"].([]any); ok {
			for factIndex, item := range facts {
				fact, ok := item.(map[string]any)
				if !ok {
					rejectedFacts = append(rejectedFacts, gin.H{"node": name, "index": factIndex, "reason": "fact_not_object"})
					continue
				}
				relation := strings.TrimSpace(fmt.Sprint(fact["relation"]))
				target := strings.TrimSpace(fmt.Sprint(fact["target"]))
				source := strings.TrimSpace(fmt.Sprint(fact["source"]))
				confidence, confidenceOK := numericPlanningValue(fact["confidence"])
				if relation == "" || !allowedRelations[relation] || target == "" || source == "" || strings.EqualFold(source, "llm") || !confidenceOK || confidence < 0 || confidence > 1 {
					rejectedFacts = append(rejectedFacts, gin.H{"node": name, "index": factIndex, "reason": "missing_or_untrusted_source_fields"})
					continue
				}
				rawObserved := strings.TrimSpace(fmt.Sprint(fact["observed_at"]))
				if rawObserved == "" || rawObserved == "<nil>" {
					rejectedFacts = append(rejectedFacts, gin.H{"node": name, "index": factIndex, "reason": "observed_at_required"})
					continue
				}
				observedAt, err := time.Parse(time.RFC3339, rawObserved)
				if err != nil {
					rejectedFacts = append(rejectedFacts, gin.H{"node": name, "index": factIndex, "reason": "observed_at_invalid"})
					continue
				}
				observedAt = observedAt.UTC()
				edges = append(edges, gin.H{"from": id, "to": target, "relation": relation, "source": source, "observed_at": observedAt, "confidence": confidence, "estimated": fact["estimated"] == true})
			}
		}
	}
	planningData(c, gin.H{"trip_id": ctx.TripID, "graph_version": "knowledge-graph-v1", "nodes": nodes, "edges": edges, "rejected_facts": rejectedFacts, "fact_policy": "verified_facts_only_no_llm_writes", "source": candidateSource("omniroute-route-graph", true), "updated_at": time.Now().UTC()})
}

// AgentEvaluationHandler reports observable success/error counts by agent or
// module. It intentionally avoids inventing quality scores when no samples
// exist.
func AgentEvaluationHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	query := database.Reader().Where("user_id = ?", userID)
	if tripID := strings.TrimSpace(c.Query("trip_id")); tripID != "" {
		if _, ok := requireTripOrRoomAccess(c, tripID); !ok {
			return
		}
		query = query.Where("trip_id = ?", tripID)
	}
	var events []models.PlanningEvent
	_ = query.Find(&events).Error
	metrics := map[string]map[string]int{}
	quality := map[string]map[string]any{}
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
		metric := strings.TrimSpace(fmt.Sprint(payload["metric"]))
		if metric != "" && metric != "<nil>" {
			if quality[name] == nil {
				quality[name] = map[string]any{}
			}
			entry, _ := quality[name][metric].(map[string]any)
			if entry == nil {
				entry = map[string]any{"samples": 0, "sum": 0.0}
			}
			if value, ok := numericPlanningValue(payload["value"]); ok {
				entry["samples"] = entry["samples"].(int) + 1
				entry["sum"] = entry["sum"].(float64) + value
				entry["mean"] = entry["sum"].(float64) / float64(entry["samples"].(int))
			}
			quality[name][metric] = entry
		}
	}
	planningData(c, gin.H{"agents": metrics, "quality_metrics": quality, "sample_count": len(events), "quality_policy": "aggregate_observability_only_no_inferred_scores", "status": func() string {
		if len(events) == 0 {
			return "insufficient_data"
		}
		return "ready_for_offline_review"
	}()})
}

// ProviderRegistryHandler exposes configured supplier capabilities without
// returning endpoints' credentials or raw responses. Field freshness is a
// policy contract; a provider response must carry its own observed_at/source.
func ProviderRegistryHandler(c *gin.Context) {
	definitions := []struct {
		kind   string
		fields []string
		fresh  int
	}{
		{"destination", []string{"name", "address", "opening_hours", "season", "audience", "url"}, 86400},
		{"scenic", []string{"name", "opening_hours", "ticket_url", "crowd_level", "url"}, 3600},
		{"transport", []string{"origin", "destination", "departure_time", "arrival_time", "availability", "price", "booking_url"}, 300},
		{"lodging", []string{"name", "room_type", "availability", "nightly_price", "commute_minutes", "booking_url"}, 900},
		{"dining", []string{"name", "cuisine", "allergens", "opening_hours", "price", "url"}, 3600},
		{"weather", []string{"observed_at", "condition", "temperature", "precipitation", "alert"}, 900},
		{"crowd", []string{"observed_at", "level", "score", "forecast_hour"}, 900},
	}
	providers := make([]gin.H, 0, len(definitions))
	for _, definition := range definitions {
		key := strings.ToUpper(definition.kind)
		base, token, label := resolveProviderConfig(key)
		providers = append(providers, gin.H{
			"kind":                     definition.kind,
			"provider":                 firstNonEmpty(label, providerName(key, base), "unconfigured"),
			"configured":               base != "",
			"endpoint_count":           len(resolveProviderConfigs(key)),
			"authenticated":            token != "",
			"fields":                   definition.fields,
			"freshness_policy_seconds": definition.fresh,
			"source_required":          true,
			"observed_at_required":     true,
			"estimated_explicit":       true,
		})
	}
	planningData(c, gin.H{"registry_version": "provider-registry-v1", "providers": providers, "privacy": gin.H{"credentials_excluded": true, "raw_payload_excluded": true}, "generated_at": time.Now().UTC()})
}

// AgentCapabilitiesHandler proxies the AI service's canonical capability
// registry. The gateway remains the browser-facing boundary and strips any
// accidental upstream envelope before returning the secret-free contract.
func AgentCapabilitiesHandler(c *gin.Context) {
	ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(aiServiceBaseURL(), "/")+"/api/v1/agent/capabilities", nil)
	if err != nil {
		planningError(c, http.StatusBadGateway, "AGENT_CAPABILITIES_UNAVAILABLE", "智能体能力矩阵暂不可用", nil)
		return
	}
	if token := strings.TrimSpace(os.Getenv("AI_SERVICE_INTERNAL_TOKEN")); token != "" {
		request.Header.Set("X-Omni-Internal-Token", token)
	}
	response, err := (&http.Client{Timeout: 3 * time.Second}).Do(request)
	if err != nil {
		planningError(c, http.StatusBadGateway, "AGENT_CAPABILITIES_UNAVAILABLE", "智能体能力矩阵暂不可用", nil)
		return
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		planningError(c, http.StatusBadGateway, "AGENT_CAPABILITIES_UNAVAILABLE", "智能体能力矩阵暂不可用", map[string]string{"upstream_status": fmt.Sprint(response.StatusCode)})
		return
	}
	var payload map[string]any
	if err := json.NewDecoder(io.LimitReader(response.Body, 256<<10)).Decode(&payload); err != nil {
		planningError(c, http.StatusBadGateway, "AGENT_CAPABILITIES_INVALID", "智能体能力矩阵格式无法识别", nil)
		return
	}
	planningData(c, payload)
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
		planningData(c, gin.H{"forecast": options, "source": source, "estimated": false, "policy": "provider_first", "explanation": crowdForecastExplanation(req.Hour, req.Weekend, false)})
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
	if score >= 0.75 {
		level = "high"
	} else if score <= 0.4 {
		level = "low"
	}
	planningData(c, gin.H{"forecast": gin.H{"level": level, "score": score, "confidence": 0.35, "date": req.Date, "hour": req.Hour}, "source": candidateSource("historical_calendar_estimator", true), "estimated": true, "policy": "provider_first_fallback_estimate", "explanation": crowdForecastExplanation(req.Hour, req.Weekend, true)})
}

func crowdForecastExplanation(hour int, weekend, estimated bool) gin.H {
	window := "09:00-11:00"
	if hour >= 10 && hour <= 15 {
		window = "08:00-09:30 或 16:00 后"
	}
	if !weekend && (hour < 10 || hour > 15) {
		window = "当前时段可优先考虑"
	}
	reasons := []string{"根据日期类型与时段规则生成"}
	if weekend {
		reasons = append(reasons, "周末通常存在更高到访压力")
	}
	if hour >= 10 && hour <= 15 {
		reasons = append(reasons, "日间核心时段通常更拥挤")
	}
	return gin.H{"recommended_window": window, "diversion_strategy": "优先错峰；无合适窗口时选择同主题替代点", "reasons": reasons, "alternative_required": true, "estimated": estimated}
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
	if err := database.CreateWithRetry(database.DB, &event); err != nil {
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
	query := database.Reader().Where("user_id = ? AND event_type IN ?", userID, []string{"official_channel_click", "official_channel_returned", "official_channel_verified", "candidate_adopted"})
	if tripID := strings.TrimSpace(c.Query("trip_id")); tripID != "" {
		if _, ok := requireTripOrRoomAccess(c, tripID); !ok {
			return
		}
		query = query.Where("trip_id = ?", tripID)
	}
	_ = query.Find(&events).Error
	byChannel := map[string]int{}
	counts := map[string]int{"clicks": 0, "returned": 0, "verified": 0, "adopted": 0}
	for _, event := range events {
		var payload map[string]any
		_ = json.Unmarshal([]byte(event.Payload), &payload)
		channel := strings.TrimSpace(fmt.Sprint(payload["channel"]))
		if channel == "" || channel == "<nil>" {
			channel = "unknown"
		}
		switch event.EventType {
		case "official_channel_click":
			counts["clicks"]++
			byChannel[channel]++
		case "official_channel_returned":
			counts["returned"]++
		case "official_channel_verified":
			counts["verified"]++
		case "candidate_adopted":
			counts["adopted"]++
		}
	}
	rate := func(n, d int) float64 {
		if d == 0 {
			return 0
		}
		return roundRatio(float64(n) / float64(d))
	}
	planningData(c, gin.H{"total_clicks": counts["clicks"], "by_channel": byChannel, "funnel": counts, "rates": gin.H{"return_rate": rate(counts["returned"], counts["clicks"]), "verification_rate": rate(counts["verified"], counts["returned"]), "adoption_rate": rate(counts["adopted"], counts["verified"])}, "conversion_policy": "user_confirmed_external_verification_until_provider_callback", "status": "attribution_ready"})
}
