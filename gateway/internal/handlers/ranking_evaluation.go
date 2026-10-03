package handlers

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"gateway/internal/contracts"
	"gateway/internal/database"
	"gateway/internal/models"
	planningService "gateway/internal/service"
	"github.com/gin-gonic/gin"
)

// RankingEvaluationHandler compares baseline and behavior-aware ordering for
// the same candidate set. It does not mutate a plan; it persists only a
// redacted planning_decision_audit summary for offline evaluation. Client-
// supplied behavior counts are ignored.
func RankingEvaluationHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	var req struct {
		contracts.PlanningContext
		Module     string                `json:"module"`
		Candidates []contracts.Candidate `json:"candidates"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_JSON", "请求数据格式不正确", nil)
		return
	}
	req.UserID = userID
	req.Module = strings.ToLower(strings.TrimSpace(req.Module))
	if req.Module == "" {
		req.Module = "destination"
	}
	if req.TripID != "" {
		if _, ok := requireTripOrRoomAccess(c, req.TripID); !ok {
			return
		}
	}
	if len(req.Candidates) == 0 || len(req.Candidates) > 100 {
		planningError(c, http.StatusBadRequest, "CANDIDATES_REQUIRED", "候选数量必须在 1 到 100 之间", nil)
		return
	}
	behavior := candidateBehaviorFeatures(userID, req.TripID, req.Module)
	baseline := planningService.RankCandidates(req.Candidates, req.Preferences)
	behaviorRanked := planningService.RankCandidatesWithBehavior(req.Candidates, req.Preferences, behavior)
	baselineSummary := rankingSummary(baseline)
	behaviorSummary := rankingSummary(behaviorRanked)
	changed := 0
	for index := range baselineSummary {
		if index < len(behaviorSummary) && baselineSummary[index]["id"] != behaviorSummary[index]["id"] {
			changed++
		}
	}
	status := map[bool]string{true: "available", false: "insufficient_data"}[behavior["enabled"] == true]
	auditPayload, _ := json.Marshal(map[string]any{
		"module": req.Module, "model_version": "ranking-behavior-v1",
		"candidate_count": len(req.Candidates), "baseline_changed_positions": changed,
		"behavior_enabled": behavior["enabled"], "behavior_sample_count": behavior["sample_count"],
		"behavior_verified": behavior["verified"], "behavior_adopted": behavior["adopted"],
		"ranking_status": status, "ranking_policy": "baseline_provider_score_primary_bounded_behavior_weight",
	})
	auditEvent := models.PlanningEvent{UserID: userID, TripID: req.TripID, EventType: "planning_decision_audit", Payload: string(auditPayload), CreatedAt: time.Now().UTC()}
	if database.DB != nil {
		if err := database.DB.Create(&auditEvent).Error; err != nil {
			planningError(c, http.StatusInternalServerError, "RANKING_AUDIT_WRITE_FAILED", "排序评估记录失败", nil)
			return
		}
	}
	planningData(c, gin.H{
		"module":          req.Module,
		"baseline":        baselineSummary,
		"behavior_aware":  behaviorSummary,
		"comparison":      gin.H{"changed_positions": changed, "candidate_count": len(req.Candidates), "status": status},
		"behavior_signal": gin.H{"enabled": behavior["enabled"], "reason": behavior["reason"], "sample_count": behavior["sample_count"], "verified": behavior["verified"], "adopted": behavior["adopted"]},
		"policy":          "baseline_provider_score_remains_primary_behavior_weight_bounded_and_gated",
		"generated_at":    time.Now().UTC(),
		"audit_event_id":  auditEvent.ID,
	})
}

func rankingSummary(candidates []contracts.Candidate) []gin.H {
	result := make([]gin.H, 0, len(candidates))
	for index, candidate := range candidates {
		result = append(result, gin.H{"rank": index + 1, "id": candidate.ID, "name": candidate.Name, "score": candidate.Score, "provider": candidate.Source.Provider, "estimated": candidate.Source.Estimated})
	}
	return result
}
