package handlers

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
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
	"constraint_violation": true, "satisfaction": true,
	"plan_favorited": true, "plan_swapped": true, "node_visited": true,
	"node_started": true, "node_skipped": true, "node_delayed": true,
	"execution_updated": true,
	"replan_triggered":  true, "emergency_evaluated": true,
	"replan_proposal_accepted": true, "replan_proposal_rejected": true,
	"replan_applied":         true,
	"official_channel_click": true,
	"model_shadow_executed":  true,
}

var planningEventTrainableTypes = map[string]bool{
	"plan_adopted": true, "plan_rejected": true, "satisfaction": true,
	"plan_favorited": true, "plan_swapped": true, "node_visited": true,
	"node_started": true, "node_skipped": true, "node_delayed": true,
}

// RecordPlanningEventHandler is deliberately append-only. It separates
// explicit feedback from passive interaction so future training can avoid
// treating a button click as a satisfaction label.
func RecordPlanningEventHandler(c *gin.Context) {
	var req struct {
		UserID    string         `json:"user_id"`
		TripID    string         `json:"trip_id"`
		RoomID    string         `json:"room_id"`
		EventType string         `json:"event_type" binding:"required"`
		PlanID    string         `json:"plan_id"`
		Value     float64        `json:"value"`
		Payload   map[string]any `json:"payload"`
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
	planningData(c, gin.H{"events": total, "window_days": windowDays, "funnel": gin.H{"impressions": impressions, "generated": generated, "adopted": adopted, "edited": edited, "rejected": rejected}, "rates": gin.H{"generation": conversion(generated, impressions), "adoption": conversion(adopted, generated), "edit": conversion(edited, adopted), "rejection": conversion(rejected, generated)}, "segments": segments, "model_versions": versions, "drift": drift, "learning": learning, "training_ready": total >= 100})
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
	holdoutDays := windowDays / 5
	if holdoutDays < 1 {
		holdoutDays = 1
	}
	for _, event := range events {
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
		for _, key := range []string{"module", "base_node_count", "segment", "model_version", "hard_satisfied", "objective", "action", "propensity", "reward"} {
			if value, exists := payload[key]; exists {
				whitelisted[key] = value
			}
		}
		planHash := ""
		if event.PlanID != "" {
			planHash = stableUserHash(event.PlanID)
		}
		split := "train"
		// Time-based holdout: the newest 20% of the requested window is never
		// mixed into training rows, preventing future feedback leakage.
		if event.CreatedAt.After(time.Now().Add(-time.Duration(holdoutDays) * 24 * time.Hour)) {
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
	planningData(c, gin.H{"dataset_version": "planning-events-v2", "window_days": windowDays, "samples": samples, "count": len(samples), "splits": gin.H{"train": trainCount, "validation": validationCount, "policy": "latest_20_percent_by_time_validation"}, "label_policy": gin.H{"adopted": 1, "rejected": 0, "other": "null"}, "trainable_event_types": planningEventTrainableTypes})
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
