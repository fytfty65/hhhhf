package handlers

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

var executionStatuses = map[string]bool{
	"planned": true, "in_progress": true, "visited": true, "skipped": true, "delayed": true,
}

// GetExecutionStateHandler returns only the caller's execution state for a
// trip. Generated plan content remains immutable and can be compared later.
func GetExecutionStateHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	tripID := strings.TrimSpace(c.Query("trip_id"))
	if tripID == "" {
		planningError(c, http.StatusBadRequest, "TRIP_ID_REQUIRED", "缺少 trip_id", nil)
		return
	}
	if _, ok := requireTripOrRoomAccess(c, tripID); !ok {
		return
	}
	var states []models.TripExecutionState
	if err := database.DB.Where("user_id = ? AND trip_id = ?", userID, tripID).Order("updated_at asc").Find(&states).Error; err != nil {
		planningError(c, http.StatusInternalServerError, "EXECUTION_QUERY_FAILED", "执行状态查询失败", nil)
		return
	}
	counts := map[string]int{}
	for _, state := range states {
		counts[state.Status]++
	}
	planningData(c, gin.H{"trip_id": tripID, "states": states, "summary": gin.H{"total": len(states), "by_status": counts}})
}

// UpdateExecutionStateHandler records an explicit user action for one node.
// The endpoint is idempotent for (trip_id,node_key) and emits a trainable
// planning event without changing the generated itinerary.
func UpdateExecutionStateHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	var req struct {
		TripID       string `json:"trip_id"`
		PlanID       string `json:"plan_id"`
		NodeKey      string `json:"node_key"`
		Status       string `json:"status"`
		DelayMinutes int    `json:"delay_minutes"`
		Note         string `json:"note"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_EXECUTION", "执行状态参数不完整", nil)
		return
	}
	req.TripID, req.NodeKey, req.Status = strings.TrimSpace(req.TripID), strings.TrimSpace(req.NodeKey), strings.ToLower(strings.TrimSpace(req.Status))
	if req.TripID == "" {
		planningError(c, http.StatusBadRequest, "TRIP_ID_REQUIRED", "缺少 trip_id", nil)
		return
	}
	if req.NodeKey == "" || len(req.NodeKey) > 200 {
		planningError(c, http.StatusBadRequest, "NODE_KEY_REQUIRED", "缺少有效 node_key", nil)
		return
	}
	if !executionStatuses[req.Status] {
		planningError(c, http.StatusBadRequest, "EXECUTION_STATUS_UNSUPPORTED", "不支持的执行状态", nil)
		return
	}
	if req.DelayMinutes < 0 || req.DelayMinutes > 1440 {
		planningError(c, http.StatusBadRequest, "DELAY_MINUTES_INVALID", "delay_minutes 必须在 0 到 1440 之间", nil)
		return
	}
	if _, ok := requireTripOrRoomAccess(c, req.TripID); !ok {
		return
	}
	var state models.TripExecutionState
	err := database.DB.Where("user_id = ? AND trip_id = ? AND node_key = ?", userID, req.TripID, req.NodeKey).First(&state).Error
	if err != nil {
		state = models.TripExecutionState{ID: uuid.NewString(), UserID: userID, TripID: req.TripID, NodeKey: req.NodeKey, CreatedAt: time.Now().UTC()}
	}
	state.PlanID, state.Status, state.DelayMinutes, state.Note, state.UpdatedAt = strings.TrimSpace(req.PlanID), req.Status, req.DelayMinutes, strings.TrimSpace(req.Note), time.Now().UTC()
	if err := database.DB.Save(&state).Error; err != nil {
		planningError(c, http.StatusInternalServerError, "EXECUTION_WRITE_FAILED", "执行状态保存失败", nil)
		return
	}
	eventType := map[string]string{"in_progress": "node_started", "visited": "node_visited", "skipped": "node_skipped", "delayed": "node_delayed", "planned": "execution_updated"}[req.Status]
	payload := map[string]any{"node_key": req.NodeKey, "status": req.Status, "delay_minutes": req.DelayMinutes, "note": req.Note}
	if req.PlanID != "" {
		payload["plan_id"] = req.PlanID
	}
	// Event writes are best effort after the state itself is durable.
	payloadBytes, _ := json.Marshal(payload)
	if database.DB != nil {
		_ = database.DB.Create(&models.PlanningEvent{UserID: userID, TripID: req.TripID, PlanID: req.PlanID, EventType: eventType, Value: float64(req.DelayMinutes), Payload: string(payloadBytes), CreatedAt: time.Now().UTC()}).Error
	}
	response := gin.H{"state": state, "event_type": eventType}
	if req.Status == "delayed" && req.DelayMinutes >= 30 {
		if proposal := createExecutionReplanProposal(userID, req.TripID, req.NodeKey, req.DelayMinutes); proposal != nil {
			response["replan_proposal"] = proposal
		}
	}
	planningData(c, response)
}

// createExecutionReplanProposal turns a material execution delay into a
// pending confirmation. It never mutates the generated route and suppresses
// duplicate pending proposals for the same trip/node.
func createExecutionReplanProposal(userID, tripID, nodeKey string, delayMinutes int) gin.H {
	if database.DB == nil {
		return nil
	}
	var events []models.PlanningEvent
	if err := database.DB.Where("user_id = ? AND trip_id = ? AND event_type = ?", userID, tripID, "replan_triggered").Order("created_at desc").Limit(20).Find(&events).Error; err == nil {
		for _, event := range events {
			var payload map[string]any
			if json.Unmarshal([]byte(event.Payload), &payload) == nil && payload["status"] == "pending_confirmation" && strings.TrimSpace(fmt.Sprint(payload["node_key"])) == nodeKey {
				return gin.H{"proposal_id": payload["proposal_id"], "status": "pending_confirmation", "trip_id": tripID, "node_key": nodeKey, "reason": "execution_delay", "delay_minutes": delayMinutes, "deduplicated": true}
			}
		}
	}
	proposal := gin.H{"proposal_id": fmt.Sprintf("execution-%d", time.Now().UTC().UnixNano()), "status": "pending_confirmation", "trip_id": tripID, "node_key": nodeKey, "reason": "execution_delay", "delay_minutes": delayMinutes, "changed_signals": []string{"delay_minutes"}, "confirmation_required": true, "generated_at": time.Now().UTC()}
	payload, _ := json.Marshal(proposal)
	if err := database.DB.Create(&models.PlanningEvent{UserID: userID, TripID: tripID, EventType: "replan_triggered", Payload: string(payload), CreatedAt: time.Now().UTC()}).Error; err != nil {
		return nil
	}
	return proposal
}
