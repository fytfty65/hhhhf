package handlers

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"gateway/internal/service"
	"github.com/gin-gonic/gin"
)

// JourneyStateHandler aggregates durable execution, risk and budget signals
// into one read-only state machine snapshot. It never mutates the itinerary.
func JourneyStateHandler(c *gin.Context) {
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
	if err := database.DB.Where("user_id = ? AND trip_id = ?", userID, tripID).Find(&states).Error; err != nil {
		planningError(c, http.StatusInternalServerError, "JOURNEY_STATE_QUERY_FAILED", "旅程状态查询失败", nil)
		return
	}
	counts := map[string]int{}
	for _, state := range states {
		counts[state.Status]++
	}
	completed := counts["visited"] + counts["skipped"]
	phase := "planned"
	if len(states) > 0 && completed == len(states) {
		phase = "completed"
	} else if counts["in_progress"] > 0 || counts["delayed"] > 0 || completed > 0 {
		phase = "in_progress"
	}

	var latestRisk models.PlanningEvent
	riskQuery := database.DB.Where("user_id = ? AND trip_id = ? AND event_type = ?", userID, tripID, "replan_triggered").Order("created_at desc").First(&latestRisk)
	var risk any
	if riskQuery.Error == nil && strings.TrimSpace(latestRisk.Payload) != "" {
		var payload map[string]any
		if json.Unmarshal([]byte(latestRisk.Payload), &payload) == nil {
			status := "pending_confirmation"
			proposalID := strings.TrimSpace(fmt.Sprint(payload["proposal_id"]))
			if proposalID != "" {
				var lifecycle []models.PlanningEvent
				_ = database.DB.Where("user_id = ? AND trip_id = ? AND event_type IN ?", userID, tripID, []string{"replan_proposal_accepted", "replan_proposal_rejected", "replan_applied"}).Order("created_at desc").Find(&lifecycle).Error
				for _, event := range lifecycle {
					var eventPayload map[string]any
					if json.Unmarshal([]byte(event.Payload), &eventPayload) == nil && strings.TrimSpace(fmt.Sprint(eventPayload["proposal_id"])) == proposalID {
						switch event.EventType {
						case "replan_proposal_accepted":
							status = "accepted"
						case "replan_proposal_rejected":
							status = "rejected"
						case "replan_applied":
							status = "applied"
						}
						break
					}
				}
			}
			risk = gin.H{"status": status, "triggered_at": latestRisk.CreatedAt, "proposal": payload}
		}
	}
	budget := service.BudgetSummaryFor(userID, tripID)
	progress := 0.0
	if len(states) > 0 {
		progress = float64(completed) / float64(len(states))
	}
	planningData(c, gin.H{
		"trip_id":      tripID,
		"phase":        phase,
		"progress":     progress,
		"execution":    gin.H{"total": len(states), "completed": completed, "by_status": counts, "states": states},
		"risk":         risk,
		"budget":       budget,
		"updated_at":   time.Now().UTC(),
		"next_actions": journeyNextActions(phase, risk, budget),
	})
}

func journeyNextActions(phase string, risk any, budget service.ExpenseSummary) []string {
	actions := []string{}
	if phase == "completed" {
		actions = append(actions, "查看行程复盘")
	} else {
		actions = append(actions, "继续执行当前节点")
	}
	if risk != nil {
		actions = append(actions, "确认风险提案后重排受影响节点")
	}
	if strings.EqualFold(strings.TrimSpace(budget.Level), "over") {
		actions = append(actions, "检查预算超支并调整后续节点")
	}
	return actions
}
