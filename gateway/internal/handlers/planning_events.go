package handlers

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"gateway/internal/database"
	"gateway/internal/metrics"
	"gateway/internal/models"
	"github.com/gin-gonic/gin"
)

var planningEventTypes = map[string]bool{
	"impression": true, "module_open": true, "plan_generated": true,
	"plan_adopted": true, "plan_edited": true, "plan_rejected": true,
	"plan_replaced": true, "plan_saved": true, "plan_viewed": true,
	"constraint_violation": true, "satisfaction": true,
	"plan_favorited": true, "plan_swapped": true, "node_visited": true,
	"node_started": true, "node_skipped": true, "node_delayed": true,
	"execution_updated": true,
	"replan_triggered":  true, "emergency_evaluated": true,
	"replan_proposal_accepted": true, "replan_proposal_rejected": true,
	"replan_proposal_expired": true,
	"replan_failed":           true,
	"replan_applied":          true,
	"route_replanned":         true, "agent_metric": true,
	"planning_decision_audit": true,
	"official_channel_click":  true, "official_channel_returned": true,
	"official_channel_verified": true, "candidate_adopted": true,
	"model_shadow_executed": true,
}

var planningEventTrainableTypes = map[string]bool{
	"plan_adopted": true, "plan_rejected": true, "satisfaction": true,
	"candidate_adopted": true, "official_channel_verified": true,
	"plan_favorited": true, "plan_swapped": true, "node_visited": true,
	"node_started": true, "node_skipped": true, "node_delayed": true,
}

var planningQualityMetrics = map[string]bool{
	"constraint_satisfaction_rate": true,
	"candidate_validity_rate":      true,
	"fact_citation_rate":           true,
	"latency_ms":                   true,
	"adoption_rate":                true,
	"replacement_rate":             true,
	"replan_success_rate":          true,
	"fairness_min_utility":         true,
	"fairness_utility_variance":    true,
	"provider_stale_rate":          true,
	"error_rate":                   true,
}

// RecordPlanningEventHandler is deliberately append-only. It separates
// explicit feedback from passive interaction so future training can avoid
// treating a button click as a satisfaction label.
func RecordPlanningEventHandler(c *gin.Context) {
	var req struct {
		UserID      string         `json:"user_id"`
		TripID      string         `json:"trip_id"`
		RoomID      string         `json:"room_id"`
		Destination string         `json:"destination"`
		StartDate   string         `json:"start_date"`
		Travelers   int            `json:"travelers"`
		Budget      float64        `json:"budget"`
		EventType   string         `json:"event_type" binding:"required"`
		PlanID      string         `json:"plan_id"`
		Value       float64        `json:"value"`
		Payload     map[string]any `json:"payload"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_EVENT", "事件参数不完整", nil)
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if strings.TrimSpace(req.TripID) == "" {
		if req.EventType != "plan_favorited" && req.EventType != "plan_swapped" && req.EventType != "node_visited" {
			planningError(c, http.StatusBadRequest, "TRIP_ID_REQUIRED", "缺少 trip_id，请先保存或创建行程", nil)
			return
		}
	} else if _, ok := requireTripOrRoomAccess(c, req.TripID); !ok {
		return
	}
	if strings.TrimSpace(req.RoomID) != "" {
		if _, ok := requireRoomMember(c, req.RoomID); !ok {
			return
		}
	}
	req.UserID = userID
	req.EventType = strings.TrimSpace(req.EventType)
	if !planningEventTypes[req.EventType] {
		planningError(c, http.StatusBadRequest, "EVENT_TYPE_UNSUPPORTED", "不支持的规划事件类型", map[string]string{"event_type": req.EventType})
		return
	}
	if len(req.PlanID) > 200 {
		planningError(c, http.StatusBadRequest, "PLAN_ID_TOO_LONG", "方案标识过长", nil)
		return
	}
	if req.Payload == nil {
		req.Payload = map[string]any{}
	}
	if req.EventType == "agent_metric" {
		metric := strings.TrimSpace(fmt.Sprint(req.Payload["metric"]))
		if !planningQualityMetrics[metric] {
			planningError(c, http.StatusBadRequest, "QUALITY_METRIC_UNSUPPORTED", "不支持的智能体质量指标", map[string]string{"metric": metric})
			return
		}
		value, valid := numericPayload(req.Payload, "value")
		if !valid || math.IsNaN(value) || math.IsInf(value, 0) {
			planningError(c, http.StatusBadRequest, "QUALITY_METRIC_VALUE_INVALID", "质量指标值无效", nil)
			return
		}
		if strings.HasSuffix(metric, "_rate") || strings.Contains(metric, "fairness_") {
			if value < 0 || value > 1 {
				planningError(c, http.StatusBadRequest, "QUALITY_METRIC_VALUE_INVALID", "比例类质量指标必须在 0 到 1 之间", nil)
				return
			}
		}
		req.Payload["metric"] = metric
		req.Payload["value"] = value
	}
	normalizePlanningEventContext(req.Payload, req.Destination, req.StartDate, req.Travelers, req.Budget)
	// Normalize the fields required for counterfactual evaluation. The client
	// may provide propensity/reward, but the gateway validates their ranges and
	// derives only the unambiguous adopted/rejected reward labels.
	if _, exists := req.Payload["propensity"]; exists {
		propensity, valid := numericPayload(req.Payload, "propensity")
		if !valid || propensity <= 0 || propensity > 1 {
			planningError(c, http.StatusBadRequest, "PROPENSITY_INVALID", "propensity 必须在 0 到 1 之间", nil)
			return
		}
		req.Payload["propensity"] = propensity
	}
	if _, exists := req.Payload["action"]; !exists {
		req.Payload["action"] = req.EventType
	}
	if req.EventType == "plan_adopted" || req.EventType == "plan_rejected" {
		if _, exists := req.Payload["reward"]; !exists {
			if req.EventType == "plan_adopted" {
				req.Payload["reward"] = 1.0
			} else {
				req.Payload["reward"] = 0.0
			}
		}
	}
	normalizeSourceEvidenceEvent(req.EventType, req.Payload)
	// The gateway is authoritative for attribution; delayed feedback resolves
	// the version pinned when this plan was generated.
	version := "builtin-module-v1"
	if req.PlanID != "" {
		var run models.PlanningRun
		if err := database.DB.Where("id = ? AND user_id = ? AND trip_id = ?", req.PlanID, userID, strings.TrimSpace(req.TripID)).First(&run).Error; err == nil {
			version = run.ModelVersion
		} else if req.EventType == "plan_adopted" || req.EventType == "plan_rejected" || req.EventType == "plan_edited" {
			planningError(c, http.StatusBadRequest, "PLAN_RUN_NOT_FOUND", "找不到该方案的生成记录，无法归因反馈", nil)
			return
		}
	}
	req.Payload["model_version"] = version
	payloadBytes, err := json.Marshal(req.Payload)
	if err != nil || len(payloadBytes) > 64*1024 {
		planningError(c, http.StatusBadRequest, "PAYLOAD_INVALID", "事件附加数据无效或过大", nil)
		return
	}
	event := models.PlanningEvent{UserID: req.UserID, TripID: strings.TrimSpace(req.TripID), RoomID: strings.TrimSpace(req.RoomID), EventType: req.EventType, PlanID: req.PlanID, Value: req.Value, Payload: string(payloadBytes)}
	if err := database.DB.Create(&event).Error; err != nil {
		planningError(c, http.StatusInternalServerError, "EVENT_WRITE_FAILED", "规划事件记录失败", nil)
		return
	}
	if (req.EventType == "plan_adopted" || req.EventType == "plan_rejected") && strings.TrimSpace(fmt.Sprint(req.Payload["bandit_arm_id"])) != "" {
		reward := 0.0
		if req.EventType == "plan_adopted" {
			reward = 1.0
		}
		if value, valid := numericPayload(req.Payload, "reward"); valid {
			reward = value
		}
		feedback, _ := json.Marshal(map[string]any{"arm_id": strings.TrimSpace(fmt.Sprint(req.Payload["bandit_arm_id"])), "reward": reward, "propensity": req.Payload["propensity"]})
		// Feedback is best-effort: the durable gateway event remains the source
		// of truth when the AI service is temporarily unavailable.
		_, _, _ = callAI(c, "/api/v1/agent/bandit/feedback", feedback)
	}
	planningData(c, gin.H{"accepted": true, "event_id": event.ID, "recorded_at": time.Now().UTC()})
}

// normalizeSourceEvidenceEvent keeps the source-verification loop structured
// and bounded. It deliberately accepts provider names, field names and
// booleans, but strips URLs, raw supplier responses and arbitrary user text.
func normalizeSourceEvidenceEvent(eventType string, payload map[string]any) {
	switch eventType {
	case "official_channel_click", "official_channel_returned", "official_channel_verified", "candidate_adopted":
	default:
		return
	}
	allowed := map[string]bool{
		"module": true, "candidate_id": true, "channel": true, "host": true,
		"provider": true, "source_conflict": true, "verification_state": true,
		"conflict_fields": true, "conflicting_sources": true,
	}
	for key := range payload {
		if !allowed[key] {
			delete(payload, key)
		}
	}
	for _, key := range []string{"module", "candidate_id", "channel", "host", "provider", "verification_state"} {
		if value, ok := payload[key]; ok {
			text := strings.TrimSpace(fmt.Sprint(value))
			if text == "" || text == "<nil>" {
				delete(payload, key)
				continue
			}
			payload[key] = truncateEventText(text, 120)
		}
	}
	if value, ok := payload["source_conflict"]; ok {
		if parsed, valid := value.(bool); valid {
			payload["source_conflict"] = parsed
		} else {
			delete(payload, "source_conflict")
		}
	}
	for _, key := range []string{"conflict_fields", "conflicting_sources"} {
		value, exists := payload[key]
		if !exists {
			continue
		}
		items, ok := value.([]any)
		if !ok {
			if strings, valid := value.([]string); valid {
				items = make([]any, 0, len(strings))
				for _, item := range strings {
					items = append(items, item)
				}
			} else {
				delete(payload, key)
				continue
			}
		}
		if len(items) > 10 {
			items = items[:10]
		}
		bounded := make([]string, 0, len(items))
		for _, item := range items {
			text := strings.TrimSpace(fmt.Sprint(item))
			if text != "" && text != "<nil>" {
				bounded = append(bounded, truncateEventText(text, 80))
			}
		}
		payload[key] = bounded
	}
}

// normalizePlanningEventContext turns request context into bounded, non-PII
// feature buckets. The client may omit these fields, but it must not be able
// to make the quality endpoint believe an event is richly stratified when it
// was not.
func normalizePlanningEventContext(payload map[string]any, destination, startDate string, travelers int, budget float64) {
	if payload == nil {
		return
	}
	if strings.TrimSpace(fmt.Sprint(payload["destination"])) == "" || strings.TrimSpace(fmt.Sprint(payload["destination"])) == "<nil>" {
		if value := strings.TrimSpace(destination); value != "" {
			payload["destination"] = truncateEventText(value, 120)
		}
	}
	if strings.TrimSpace(fmt.Sprint(payload["season"])) == "" || strings.TrimSpace(fmt.Sprint(payload["season"])) == "<nil>" {
		payload["season"] = planningEventSeason(startDate)
	}
	if strings.TrimSpace(fmt.Sprint(payload["budget_bucket"])) == "" || strings.TrimSpace(fmt.Sprint(payload["budget_bucket"])) == "<nil>" {
		payload["budget_bucket"] = planningBudgetBucket(budget)
	}
	if strings.TrimSpace(fmt.Sprint(payload["traveler_group"])) == "" || strings.TrimSpace(fmt.Sprint(payload["traveler_group"])) == "<nil>" {
		payload["traveler_group"] = planningTravelerGroup(travelers)
	}
	payload["context_version"] = "planning-event-context-v1"
}

func planningEventSeason(startDate string) string {
	month := int(time.Now().Month())
	if parsed, err := time.Parse("2006-01-02", strings.TrimSpace(startDate)); err == nil {
		month = int(parsed.Month())
	}
	switch {
	case month >= 3 && month <= 5:
		return "spring"
	case month >= 6 && month <= 8:
		return "summer"
	case month >= 9 && month <= 11:
		return "autumn"
	default:
		return "winter"
	}
}

func planningBudgetBucket(budget float64) string {
	if budget <= 0 || math.IsNaN(budget) || math.IsInf(budget, 0) {
		return "unknown"
	}
	switch {
	case budget < 3000:
		return "under_3000"
	case budget < 10000:
		return "3000_10000"
	default:
		return "over_10000"
	}
}

func planningTravelerGroup(travelers int) string {
	switch {
	case travelers <= 1:
		return "solo"
	case travelers == 2:
		return "pair"
	case travelers <= 5:
		return "small_group"
	default:
		return "large_group"
	}
}

func truncateEventText(value string, maxRunes int) string {
	runes := []rune(value)
	if len(runes) <= maxRunes {
		return value
	}
	return string(runes[:maxRunes])
}

// PlanningQualityHandler provides a small, auditable quality surface for
// dashboards and model promotion gates.
func PlanningQualityHandler(c *gin.Context) {
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
	cutoff := time.Now().Add(-time.Duration(windowDays) * 24 * time.Hour)
	query := database.DB.Model(&models.PlanningEvent{}).Where("user_id = ? AND created_at >= ?", userID, cutoff)
	if tripID != "" {
		query = query.Where("trip_id = ?", tripID)
	}
	var total int64
	query.Count(&total)
	count := func(kind string) int64 {
		q := database.DB.Model(&models.PlanningEvent{}).Where("user_id = ? AND event_type = ? AND created_at >= ?", userID, kind, cutoff)
		if tripID != "" {
			q = q.Where("trip_id = ?", tripID)
		}
		var n int64
		q.Count(&n)
		return n
	}
	impressions, generated, adopted, edited, rejected := count("impression"), count("plan_generated"), count("plan_adopted"), count("plan_edited"), count("plan_rejected")
	replanTriggered, replanned, replanRejected := count("replan_triggered"), count("route_replanned"), count("replan_proposal_rejected")
	conversion := func(n, d int64) float64 {
		if d == 0 {
			return 0
		}
		return roundRatio(float64(n) / float64(d))
	}
	var events []models.PlanningEvent
	eventQuery := database.DB.Where("user_id = ? AND created_at >= ?", userID, cutoff)
	if tripID != "" {
		eventQuery = eventQuery.Where("trip_id = ?", tripID)
	}
	_ = eventQuery.Find(&events).Error
	segments := map[string]int{}
	versions := map[string]int{}
	for _, event := range events {
		var payload map[string]any
		_ = json.Unmarshal([]byte(event.Payload), &payload)
		segment, _ := payload["segment"].(string)
		if segment == "" {
			segment = "unknown"
		}
		segments[segment]++
		version, _ := payload["model_version"].(string)
		if version == "" {
			version = "unversioned"
		}
		versions[version]++
	}
	drift := eventTypeDrift(events, cutoff)
	readiness := learningReadiness(events)
	// Learning-state surface. This is what makes "is the policy learning?"
	// answerable from the product rather than from a log dig: it reports the
	// policy's own posterior per arm plus how many reward signals the gateway
	// forwarded, dropped, or had rejected.
	learning := gin.H{
		"feedback_counters": metrics.BanditFeedbackCounters(),
		"policy":            fetchBanditSnapshot(c),
	}
	if metrics.BanditFeedbackCounters()["missing_arm"] > 0 {
		learning["warning"] = "有奖励信号因缺少 bandit arm 被丢弃，学习闭环未完整生效"
	}
	quality := qualityMetricSummary(events)
	triggerStats := replanTriggerQuality(events)
	quality["replan_success_rate_by_trigger"] = triggerStats
	quality["replan_success_rate"] = map[string]any{"samples": replanTriggered, "successful": replanned, "rejected": replanRejected, "mean": conversion(replanned, replanTriggered), "status": func() string {
		if replanTriggered == 0 {
			return "insufficient_data"
		}
		return "available"
	}()}
	planningData(c, gin.H{"events": total, "window_days": windowDays, "funnel": gin.H{"impressions": impressions, "generated": generated, "adopted": adopted, "edited": edited, "rejected": rejected}, "rates": gin.H{"generation": conversion(generated, impressions), "adoption": conversion(adopted, generated), "edit": conversion(edited, adopted), "rejection": conversion(rejected, generated)}, "replan": gin.H{"triggered": replanTriggered, "successful": replanned, "rejected": replanRejected, "success_rate": conversion(replanned, replanTriggered), "by_trigger": triggerStats}, "segments": segments, "model_versions": versions, "drift": drift, "quality_metrics": quality, "provider_quality_gate": providerQualityGateSummary(events), "learning": learning, "training_ready": readiness["ready"], "learning_readiness": readiness})
}

// providerQualityGateSummary is a dashboard-safe release signal. It consumes
// only the bounded provider_quality event aggregates and never returns raw
// supplier rows, URLs, credentials, user IDs or trip IDs.
func providerQualityGateSummary(events []models.PlanningEvent) map[string]any {
	type aggregate struct{ observations, candidates, eligible, blocked, stale, conflicts, enumInvalid int }
	groups := map[string]*aggregate{}
	for _, event := range events {
		if event.EventType != "provider_quality" {
			continue
		}
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		kind := strings.TrimSpace(fmt.Sprint(payload["kind"]))
		provider := strings.TrimSpace(fmt.Sprint(payload["provider"]))
		if kind == "" || provider == "" || kind == "<nil>" || provider == "<nil>" {
			continue
		}
		key := truncateEventText(kind+":"+provider, 120)
		item := groups[key]
		if item == nil {
			item = &aggregate{}
			groups[key] = item
		}
		item.observations++
		item.candidates += intValue(payload["candidate_count"])
		item.eligible += intValue(payload["decision_eligible_count"])
		item.blocked += intValue(payload["decision_blocked_count"])
		item.stale += intValue(payload["stale_count"])
		item.conflicts += intValue(payload["source_conflict_count"])
		item.enumInvalid += intValue(payload["enum_invalid_count"])
	}
	if len(groups) == 0 {
		return map[string]any{"status": "not_required", "providers": 0, "ready_providers": 0, "insufficient_providers": 0, "blocked_providers": 0, "reasons": []string{}, "alerts": []any{}, "alert_digest": ""}
	}
	const minObservations = 5
	const maxStale, maxConflict, maxEnumInvalid, minEligible = 0.20, 0.20, 0.05, 0.70
	ready, insufficient, blocked := 0, 0, 0
	reasons := []string{}
	for key, item := range groups {
		if item.observations < minObservations || item.candidates <= 0 {
			insufficient++
			continue
		}
		stale := float64(item.stale) / float64(item.candidates)
		conflict := float64(item.conflicts) / float64(item.candidates)
		enumInvalid := float64(item.enumInvalid) / float64(item.candidates)
		eligible := float64(item.eligible) / float64(item.candidates)
		issues := []string{}
		if stale > maxStale { issues = append(issues, "stale_ratio") }
		if conflict > maxConflict { issues = append(issues, "source_conflict_ratio") }
		if enumInvalid > maxEnumInvalid { issues = append(issues, "enum_invalid_ratio") }
		if eligible < minEligible { issues = append(issues, "eligible_ratio") }
		if len(issues) > 0 {
			blocked++
			reasons = append(reasons, key+":"+strings.Join(issues, ","))
		} else {
			ready++
		}
	}
	status := "available"
	if blocked > 0 { status = "blocked" } else if insufficient > 0 { status = "insufficient_data" }
	// Alerts are a bounded operator contract. They intentionally expose only
	// severity, stable codes and affected counts; provider names and raw
	// supplier payloads remain in neither the alert nor the dashboard contract.
	alerts := make([]map[string]any, 0, 2)
	if blocked > 0 {
		alerts = append(alerts, map[string]any{"code": "provider_quality_blocked", "severity": "critical", "affected_providers": blocked})
	}
	if insufficient > 0 {
		alerts = append(alerts, map[string]any{"code": "provider_quality_insufficient_data", "severity": "warning", "affected_providers": insufficient})
	}
	issueCounts := map[string]int{}
	for _, reason := range reasons {
		for issue, code := range map[string]string{
			"stale_ratio": "provider_quality_stale_data",
			"source_conflict_ratio": "provider_quality_source_conflict",
			"enum_invalid_ratio": "provider_quality_enum_invalid",
			"eligible_ratio": "provider_quality_low_eligibility",
		} {
			if strings.Contains(reason, issue) {
				issueCounts[code]++
			}
		}
	}
	for _, code := range []string{"provider_quality_stale_data", "provider_quality_source_conflict", "provider_quality_enum_invalid", "provider_quality_low_eligibility"} {
		if count := issueCounts[code]; count > 0 {
			alerts = append(alerts, map[string]any{"code": code, "severity": "critical", "affected_providers": count})
		}
	}
	return map[string]any{"status": status, "providers": len(groups), "ready_providers": ready, "insufficient_providers": insufficient, "blocked_providers": blocked, "reasons": reasons, "alerts": alerts, "alert_digest": providerQualityAlertDigest(alerts), "thresholds": map[string]any{"minimum_observations": minObservations, "maximum_stale_ratio": maxStale, "maximum_source_conflict_ratio": maxConflict, "maximum_enum_invalid_ratio": maxEnumInvalid, "minimum_eligible_ratio": minEligible}}
}

// providerQualityAlertDigest is a stable, non-sensitive fingerprint for the
// current alert set. Notification workers can use it with a cooldown store to
// suppress duplicate pushes without persisting provider identities or raw
// supplier responses.
func providerQualityAlertDigest(alerts []map[string]any) string {
	if len(alerts) == 0 {
		return ""
	}
	parts := make([]string, 0, len(alerts))
	for _, alert := range alerts {
		code := strings.TrimSpace(fmt.Sprint(alert["code"]))
		severity := strings.TrimSpace(fmt.Sprint(alert["severity"]))
		affected := intValue(alert["affected_providers"])
		if code == "" || code == "<nil>" {
			continue
		}
		parts = append(parts, fmt.Sprintf("%s|%s|%d", code, severity, affected))
	}
	if len(parts) == 0 {
		return ""
	}
	digest := sha256.Sum256([]byte(strings.Join(parts, ";")))
	return hex.EncodeToString(digest[:])[:16]
}

func replanTriggerQuality(events []models.PlanningEvent) map[string]any {
	type stat struct {
		triggered, successful, rejected, failed int
		failureCodes                            map[string]int
	}
	byProposal := map[string][]string{}
	byProposalModel := map[string]string{}
	stats := map[string]*stat{}
	modelStats := map[string]map[string]*stat{}
	for _, event := range events {
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		proposalID := strings.TrimSpace(fmt.Sprint(payload["proposal_id"]))
		if proposalID == "" || proposalID == "<nil>" {
			continue
		}
		if event.EventType == "replan_triggered" {
			types := []string{"risk_change"}
			if raw, ok := payload["trigger_types"].([]any); ok {
				types = make([]string, 0, len(raw))
				for _, value := range raw {
					if name := strings.TrimSpace(fmt.Sprint(value)); name != "" && name != "<nil>" {
						types = append(types, name)
					}
				}
			}
			byProposal[proposalID] = types
			modelVersion := strings.TrimSpace(fmt.Sprint(payload["model_version"]))
			if modelVersion == "" || modelVersion == "<nil>" {
				modelVersion = "unversioned"
			}
			byProposalModel[proposalID] = modelVersion
			for _, typeName := range types {
				if stats[typeName] == nil {
					stats[typeName] = &stat{failureCodes: map[string]int{}}
				}
				stats[typeName].triggered++
				if modelVersion != "" {
					if modelStats[typeName] == nil {
						modelStats[typeName] = map[string]*stat{}
					}
					if modelStats[typeName][modelVersion] == nil {
						modelStats[typeName][modelVersion] = &stat{failureCodes: map[string]int{}}
					}
					modelStats[typeName][modelVersion].triggered++
				}
			}
			continue
		}
		if event.EventType != "route_replanned" && event.EventType != "replan_proposal_rejected" && event.EventType != "replan_failed" {
			continue
		}
		for _, typeName := range byProposal[proposalID] {
			if stats[typeName] == nil {
				stats[typeName] = &stat{failureCodes: map[string]int{}}
			}
			if event.EventType == "route_replanned" {
				stats[typeName].successful++
			} else if event.EventType == "replan_proposal_rejected" {
				stats[typeName].rejected++
			} else {
				stats[typeName].failed++
				if event.EventType == "replan_failed" {
					if code := normalizedReplanFailureCode(payload["failure_code"]); code != "" {
						stats[typeName].failureCodes[code]++
					}
				}
			}
			if modelVersion := byProposalModel[proposalID]; modelVersion != "" {
				if modelStats[typeName] == nil {
					modelStats[typeName] = map[string]*stat{}
				}
				if modelStats[typeName][modelVersion] == nil {
					modelStats[typeName][modelVersion] = &stat{failureCodes: map[string]int{}}
				}
				model := modelStats[typeName][modelVersion]
				switch event.EventType {
				case "route_replanned":
					model.successful++
				case "replan_proposal_rejected":
					model.rejected++
				case "replan_failed":
					model.failed++
					if code := normalizedReplanFailureCode(payload["failure_code"]); code != "" {
						model.failureCodes[code]++
					}
				}
			}
		}
	}
	result := map[string]any{}
	for typeName, value := range stats {
		status := "available"
		if value.triggered == 0 {
			status = "insufficient_data"
		}
		models := map[string]any{}
		for modelVersion, model := range modelStats[typeName] {
			models[modelVersion] = map[string]any{"triggered": model.triggered, "successful": model.successful, "rejected": model.rejected, "failed": model.failed, "failure_code_distribution": model.failureCodes, "success_rate": conversionIntRatio(model.successful, model.triggered), "status": func() string {
				if model.triggered == 0 {
					return "insufficient_data"
				}
				return "available"
			}()}
		}
		result[typeName] = map[string]any{"triggered": value.triggered, "successful": value.successful, "rejected": value.rejected, "failed": value.failed, "failure_code_distribution": value.failureCodes, "success_rate": conversionIntRatio(value.successful, value.triggered), "status": status, "models": models}
	}
	return result
}

func normalizedReplanFailureCode(value any) string {
	code := strings.TrimSpace(fmt.Sprint(value))
	if code == "" || code == "<nil>" {
		return "UNKNOWN"
	}
	return code
}

func conversionIntRatio(n, d int) float64 {
	if d == 0 {
		return 0
	}
	return roundRatio(float64(n) / float64(d))
}

func qualityMetricSummary(events []models.PlanningEvent) map[string]any {
	result := map[string]any{}
	for _, event := range events {
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		metric := strings.TrimSpace(fmt.Sprint(payload["metric"]))
		if !planningQualityMetrics[metric] {
			continue
		}
		value, valid := numericPayload(payload, "value")
		if !valid {
			continue
		}
		entry, _ := result[metric].(map[string]any)
		if entry == nil {
			entry = map[string]any{"samples": 0, "sum": 0.0}
		}
		entry["samples"] = entry["samples"].(int) + 1
		entry["sum"] = entry["sum"].(float64) + value
		entry["mean"] = roundRatio(entry["sum"].(float64) / float64(entry["samples"].(int)))
		entry["status"] = "available"
		result[metric] = entry
	}
	return result
}

// learningReadiness is intentionally conservative. A large event count alone
// does not prove that an arm or a user/context segment has enough evidence to
// train safely. The returned shape is also consumed by the UI, so keep the
// reasons explicit instead of collapsing them into a boolean.
func learningReadiness(events []models.PlanningEvent) map[string]any {
	const minTotal = 100
	const minArm = 20
	const minStratum = 5
	armCounts := map[string]int{}
	strata := map[string]map[string]int{
		"destination":    {},
		"season":         {},
		"budget_bucket":  {},
		"traveler_group": {},
	}
	trainableSamples := 0
	for _, event := range events {
		if !planningEventTrainableTypes[event.EventType] {
			continue
		}
		trainableSamples++
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		arm := strings.TrimSpace(fmt.Sprint(payload["bandit_arm_id"]))
		if arm == "" || arm == "<nil>" {
			arm = strings.TrimSpace(fmt.Sprint(payload["arm_id"]))
		}
		if arm == "" || arm == "<nil>" {
			arm = strings.TrimSpace(fmt.Sprint(payload["model_version"]))
		}
		if arm != "" && arm != "<nil>" {
			armCounts[arm]++
		}
		for _, key := range []string{"destination", "season", "budget_bucket", "traveler_group"} {
			value := strings.TrimSpace(fmt.Sprint(payload[key]))
			if value != "" && value != "<nil>" {
				strata[key][value]++
			}
		}
	}
	armStatus := map[string]any{}
	allArmsReady := len(armCounts) > 0
	for arm, count := range armCounts {
		ready := count >= minArm
		armStatus[arm] = gin.H{"samples": count, "minimum": minArm, "ready": ready}
		if !ready {
			allArmsReady = false
		}
	}
	strataStatus := map[string]any{}
	allStrataReady := true
	missingStrata := make([]string, 0)
	for key, values := range strata {
		ready := len(values) > 0
		for value, count := range values {
			if count < minStratum {
				ready = false
			}
			values[value] = count
		}
		if !ready {
			allStrataReady = false
			missingStrata = append(missingStrata, key)
		}
		strataStatus[key] = gin.H{"samples": values, "minimum": minStratum, "ready": ready}
	}
	ready := trainableSamples >= minTotal && allArmsReady && allStrataReady
	status := "insufficient_data"
	if ready {
		status = "ready"
	}
	return map[string]any{
		"status":                status,
		"ready":                 ready,
		"minimum_total_samples": minTotal,
		"total_samples":         trainableSamples,
		"minimum_arm_samples":   minArm,
		"arms":                  armStatus,
		"strata":                strataStatus,
		"missing_strata":        missingStrata,
		"reason": func() string {
			if trainableSamples < minTotal {
				return "总样本不足"
			}
			if !allArmsReady {
				return "至少一个策略臂样本不足"
			}
			return "关键分层样本不足或未标注"
		}(),
	}
}

// fetchBanditSnapshot asks the AI service for the current policy posterior.
//
// Returns nil when the service is unreachable or the bandit is disabled, so a
// quality dashboard degrades to "policy unavailable" instead of failing.
func fetchBanditSnapshot(c *gin.Context) any {
	ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, aiServiceBaseURL()+"/api/v1/agent/bandit", nil)
	if err != nil {
		return nil
	}
	if token := strings.TrimSpace(os.Getenv("AI_SERVICE_INTERNAL_TOKEN")); token != "" {
		req.Header.Set("X-Omni-Internal-Token", token)
	}
	resp, err := (&http.Client{Timeout: 3 * time.Second}).Do(req)
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil
	}
	var snapshot any
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&snapshot); err != nil {
		return nil
	}
	return snapshot
}

func eventTypeDrift(events []models.PlanningEvent, cutoff time.Time) map[string]any {
	recent, previous := map[string]int{}, map[string]int{}
	midpoint := time.Now().Add(-7 * 24 * time.Hour)
	for _, event := range events {
		if event.CreatedAt.After(midpoint) {
			recent[event.EventType]++
		} else {
			previous[event.EventType]++
		}
	}
	recentTotal, previousTotal := 0, 0
	for _, n := range recent {
		recentTotal += n
	}
	for _, n := range previous {
		previousTotal += n
	}
	if recentTotal == 0 || previousTotal == 0 {
		return map[string]any{"score": 0.0, "status": "insufficient_data", "baseline": "previous_7_days"}
	}
	keys := map[string]bool{}
	for key := range recent {
		keys[key] = true
	}
	for key := range previous {
		keys[key] = true
	}
	drift := 0.0
	for key := range keys {
		drift += 0.5 * absFloat(float64(recent[key])/float64(recentTotal)-float64(previous[key])/float64(previousTotal))
	}
	_ = cutoff
	status := "stable"
	if drift > 0.2 {
		status = "watch"
	}
	if drift > 0.4 {
		status = "alert"
	}
	return map[string]any{"score": roundRatio(drift), "status": status, "baseline": "previous_7_days", "recent_sample_size": recentTotal, "previous_sample_size": previousTotal}
}

func absFloat(value float64) float64 {
	if value < 0 {
		return -value
	}
	return value
}

func PlanningProviderHealthHandler(c *gin.Context) {
	planningData(c, gin.H{"providers": planningProviderHealth(), "fallback_policy": "provider_timeout_or_invalid_response_uses_estimator"})
}

// PlanningProviderProbeHandler performs bounded, authenticated reachability
// checks against every configured supplier. It is intentionally separate from
// the cheap configuration-only health endpoint so dashboards can choose when
// to incur network calls, while operators still get one consistent contract.
func PlanningProviderProbeHandler(c *gin.Context) {
	kinds := []string{"DESTINATION", "SCENIC", "TRANSPORT", "LODGING", "DINING", "WEATHER", "CROWD"}
	type probeResult struct {
		kind string
		data map[string]any
	}
	results := make(chan probeResult, len(kinds))
	var wg sync.WaitGroup
	for _, kind := range kinds {
		kind := kind
		wg.Add(1)
		go func() {
			defer wg.Done()
			results <- probeResult{kind: strings.ToLower(kind), data: probePlanningProvider(c.Request.Context(), kind)}
		}()
	}
	wg.Wait()
	close(results)
	providers := make(map[string]any, len(kinds))
	for result := range results {
		providers[result.kind] = result.data
	}
	planningData(c, gin.H{"providers": providers, "checked_at": time.Now().UTC(), "timeout_seconds": 2, "fallback_policy": "provider_timeout_or_invalid_response_uses_estimator"})
}

// ExportPlanningDatasetHandler produces an auditable, de-identified training
// slice. It is admin-only and labels explicit outcomes separately from passive
// generation events.
func ExportPlanningDatasetHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if !isPlanningAdmin(userID) {
		planningError(c, http.StatusForbidden, "DATASET_ADMIN_REQUIRED", "只有规划服务管理员可以导出训练数据", nil)
		return
	}
	limit := 5000
	if raw := strings.TrimSpace(c.Query("limit")); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil && n > 0 && n <= 10000 {
			limit = n
		}
	}
	windowDays := 90
	if raw := strings.TrimSpace(c.Query("window_days")); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil && n > 0 && n <= 730 {
			windowDays = n
		}
	}
	query := database.DB.Where("created_at >= ?", time.Now().Add(-time.Duration(windowDays)*24*time.Hour)).Order("created_at asc").Limit(limit)
	if tripID := strings.TrimSpace(c.Query("trip_id")); tripID != "" {
		query = query.Where("trip_id = ?", tripID)
	}
	var events []models.PlanningEvent
	if err := query.Find(&events).Error; err != nil {
		planningError(c, http.StatusInternalServerError, "DATASET_QUERY_FAILED", "训练数据查询失败", nil)
		return
	}
	type sample struct {
		EventID      uint           `json:"event_id"`
		UserHash     string         `json:"user_hash"`
		TripHash     string         `json:"trip_hash"`
		EventType    string         `json:"event_type"`
		PlanHash     string         `json:"plan_hash,omitempty"`
		ModelVersion string         `json:"model_version"`
		Split        string         `json:"split"`
		Label        *float64       `json:"label"`
		Payload      map[string]any `json:"payload"`
		CreatedAt    time.Time      `json:"created_at"`
	}
	samples := make([]sample, 0, len(events))
	validationStart := int(math.Floor(float64(len(events)) * 0.8))
	if validationStart >= len(events) && len(events) > 0 {
		validationStart = len(events) - 1
	}
	for eventIndex, event := range events {
		var payload map[string]any
		_ = json.Unmarshal([]byte(event.Payload), &payload)
		version, _ := payload["model_version"].(string)
		if version == "" && strings.TrimSpace(event.PlanID) != "" {
			var run models.PlanningRun
			if err := database.DB.Where("id = ? AND user_id = ? AND trip_id = ?", event.PlanID, event.UserID, event.TripID).First(&run).Error; err == nil {
				version = run.ModelVersion
			}
		}
		var label *float64
		switch event.EventType {
		case "plan_adopted":
			v := 1.0
			label = &v
		case "plan_rejected":
			v := 0.0
			label = &v
		}
		whitelisted := map[string]any{}
		for _, key := range []string{"module", "agent", "base_node_count", "segment", "destination", "season", "budget_bucket", "traveler_group", "model_version", "hard_satisfied", "objective", "action", "propensity", "reward", "metric", "value", "candidate_count", "pareto_frontier_count", "pareto_rejected_count", "fairness_minimum_utility", "fairness_utility_stddev", "fairness_threshold", "fairness_threshold_exceeded", "fairness_repair_status", "specialist_completed", "specialist_degraded", "kind", "provider", "status", "source_count", "observed_timestamp_count", "fact_usable_count", "decision_eligible_count", "decision_blocked_count", "decision_gate_contract", "estimated_count", "stale_count", "source_conflict_count", "enum_validated_count", "enum_invalid_count", "latency_ms", "quality_contract", "field_counts", "field_names"} {
			if value, exists := payload[key]; exists {
				whitelisted[key] = value
			}
		}
		planHash := ""
		if event.PlanID != "" {
			planHash = stableUserHash(event.PlanID)
		}
		split := "train"
		// Deterministic time-ordered holdout: the newest 20% is never mixed
		// into training, preventing future feedback leakage for sparse data.
		if eventIndex >= validationStart {
			split = "validation"
		}
		samples = append(samples, sample{EventID: event.ID, UserHash: stableUserHash(event.UserID), TripHash: stableUserHash(event.TripID), EventType: event.EventType, PlanHash: planHash, ModelVersion: version, Split: split, Label: label, Payload: whitelisted, CreatedAt: event.CreatedAt})
	}
	trainCount, validationCount := 0, 0
	for _, item := range samples {
		if item.Split == "validation" {
			validationCount++
		} else {
			trainCount++
		}
	}
	strata := map[string]map[string]int{"destination": {}, "season": {}, "budget_bucket": {}, "traveler_group": {}, "provider_kind": {}, "provider": {}}
	counterfactualSamples, counterfactualTrain, counterfactualValidation := 0, 0, 0
	for _, item := range samples {
		for key := range strata {
			stratumKey := key
			if key == "provider_kind" {
				stratumKey = "kind"
			}
			if value, ok := item.Payload[stratumKey].(string); ok && strings.TrimSpace(value) != "" {
				strata[key][value]++
			}
		}
		if _, hasPropensity := item.Payload["propensity"]; hasPropensity {
			if _, hasReward := item.Payload["reward"]; hasReward {
				counterfactualSamples++
				if item.Split == "validation" {
					counterfactualValidation++
				} else {
					counterfactualTrain++
				}
			}
		}
	}
	cfStatus := "available"
	if counterfactualSamples == 0 {
		cfStatus = "insufficient_data"
	}
	planningData(c, gin.H{"dataset_version": "planning-events-v4", "window_days": windowDays, "samples": samples, "count": len(samples), "splits": gin.H{"train": trainCount, "validation": validationCount, "policy": "oldest_80_percent_newest_20_percent_by_created_at"}, "strata": strata, "counterfactual": gin.H{"status": cfStatus, "samples": counterfactualSamples, "train": counterfactualTrain, "validation": counterfactualValidation, "method": "ips", "required_fields": []string{"propensity", "reward"}}, "label_policy": gin.H{"adopted": 1, "rejected": 0, "other": "null"}, "trainable_event_types": planningEventTrainableTypes, "provider_quality_policy": "aggregate_only_no_raw_supplier_payload"})
}

func stableUserHash(value string) string {
	key := strings.TrimSpace(os.Getenv("PLANNING_DATASET_HASH_SALT"))
	if key == "" {
		// Keep local exports deterministic while making production configuration
		// explicit through PLANNING_DATASET_HASH_SALT.
		key = "omniroute-planning-dataset-development-key"
	}
	h := hmac.New(sha256.New, []byte(key))
	_, _ = h.Write([]byte(value))
	return hex.EncodeToString(h.Sum(nil))
}
