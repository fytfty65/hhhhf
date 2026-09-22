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
	"gorm.io/gorm"
)

// ConfirmReplanHandler is the server-side commit point for a minimal
// perturbation proposal. The browser may preview candidates, but it cannot
// mutate the persisted itinerary without an unexpired, user-owned proposal
// and a final hard-constraint/visited-node check in this transaction.
func ConfirmReplanHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	var req struct {
		TripID        string         `json:"trip_id"`
		ProposalID    string         `json:"proposal_id"`
		Candidate     map[string]any `json:"candidate"`
		AffectedNodes []string       `json:"affected_nodes"`
		Confirmation  string         `json:"confirmation"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_JSON", "请求数据格式不正确", nil)
		return
	}
	req.TripID = strings.TrimSpace(req.TripID)
	req.ProposalID = strings.TrimSpace(req.ProposalID)
	if req.TripID == "" || req.ProposalID == "" || len(req.AffectedNodes) != 1 || req.Candidate == nil {
		planningError(c, http.StatusBadRequest, "REPLAN_CONFIRMATION_INCOMPLETE", "缺少行程、提案、受影响节点或候选方案", nil)
		return
	}
	if req.Confirmation != "user_confirmed" {
		planningError(c, http.StatusBadRequest, "REPLAN_CONFIRMATION_REQUIRED", "必须由用户明确确认后才能应用重规划", nil)
		return
	}
	if _, ok := requireTripOrRoomAccess(c, req.TripID); !ok {
		return
	}

	var result gin.H
	err := database.DB.Transaction(func(tx *gorm.DB) error {
		var trip models.TripPlan
		if err := tx.Where("id = ? AND user_id = ?", req.TripID, userID).First(&trip).Error; err != nil {
			return replanConfirmError{status: http.StatusNotFound, code: "TRIP_NOT_FOUND", message: "行程不存在"}
		}
		var proposalEvent models.PlanningEvent
		var events []models.PlanningEvent
		if err := tx.Where("user_id = ? AND trip_id = ? AND event_type = ?", userID, req.TripID, "replan_triggered").Order("created_at desc").Limit(50).Find(&events).Error; err != nil {
			return replanConfirmError{status: http.StatusInternalServerError, code: "REPLAN_PROPOSAL_QUERY_FAILED", message: "重规划提案查询失败"}
		}
		found := false
		var proposal map[string]any
		for _, event := range events {
			var payload map[string]any
			if json.Unmarshal([]byte(event.Payload), &payload) != nil || strings.TrimSpace(fmt.Sprint(payload["proposal_id"])) != req.ProposalID {
				continue
			}
			proposalEvent, proposal, found = event, payload, true
			break
		}
		if !found {
			return replanConfirmError{status: http.StatusNotFound, code: "REPLAN_PROPOSAL_NOT_FOUND", message: "重规划提案不存在或不属于当前行程"}
		}
		if strings.TrimSpace(fmt.Sprint(proposal["status"])) != "pending_confirmation" {
			return replanConfirmError{status: http.StatusConflict, code: "REPLAN_PROPOSAL_NOT_PENDING", message: "重规划提案已经处理，不能重复应用"}
		}
		if expires, err := time.Parse(time.RFC3339, strings.TrimSpace(fmt.Sprint(proposal["expires_at"]))); err == nil && expires.Before(time.Now().UTC()) {
			return replanConfirmError{status: http.StatusConflict, code: "REPLAN_PROPOSAL_EXPIRED", message: "重规划提案已过期，请重新评估风险"}
		}
		proposalAffected := proposalAffectedNodes(proposal)
		if len(proposalAffected) != 1 || proposalAffected[0] != strings.TrimSpace(req.AffectedNodes[0]) {
			return replanConfirmError{status: http.StatusConflict, code: "REPLAN_AFFECTED_NODE_MISMATCH", message: "确认节点与提案不一致"}
		}

		var document map[string]any
		if err := json.Unmarshal([]byte(trip.Content), &document); err != nil {
			return replanConfirmError{status: http.StatusConflict, code: "TRIP_CONTENT_INVALID", message: "行程内容无法解析，未应用重规划"}
		}
		route, key := routeFromDocument(document)
		if len(route) == 0 {
			return replanConfirmError{status: http.StatusConflict, code: "TRIP_ROUTE_NOT_FOUND", message: "行程中没有可替换路线"}
		}
		target := strings.TrimSpace(req.AffectedNodes[0])
		index := -1
		for i, node := range route {
			if nodeName(node) == target {
				index = i
				break
			}
		}
		if index < 0 {
			return replanConfirmError{status: http.StatusConflict, code: "REPLAN_AFFECTED_NODE_NOT_FOUND", message: "受影响节点不在当前行程中"}
		}
		var state models.TripExecutionState
		if tx.Where("user_id = ? AND trip_id = ? AND node_key IN ?", userID, req.TripID, []string{nodeKey(route[index]), target}).First(&state).Error == nil && (state.Status == "visited" || state.Status == "skipped") {
			return replanConfirmError{status: http.StatusConflict, code: "NODE_ALREADY_COMPLETED", message: "已执行节点不可替换"}
		}
		candidateName := strings.TrimSpace(fmt.Sprint(req.Candidate["name"]))
		if candidateName == "" || candidateName == "<nil>" {
			candidateName = strings.TrimSpace(fmt.Sprint(req.Candidate["location"]))
		}
		if candidateName == "" || candidateName == "<nil>" {
			return replanConfirmError{status: http.StatusBadRequest, code: "CANDIDATE_NAME_REQUIRED", message: "替代候选缺少名称"}
		}
		if !candidateHasSource(req.Candidate) {
			return replanConfirmError{status: http.StatusBadRequest, code: "CANDIDATE_SOURCE_REQUIRED", message: "替代候选缺少来源，不能应用"}
		}
		previous := route[index]
		next := map[string]any{}
		for field, value := range previous {
			next[field] = value
		}
		for field, value := range req.Candidate {
			next[field] = value
		}
		next["name"] = candidateName
		if _, exists := next["location"]; !exists {
			next["location"] = candidateName
		}
		next["replacement_reason"] = fmt.Sprint(proposal["reason"])
		route[index] = next
		setRouteOnDocument(document, key, route)
		encoded, err := json.Marshal(document)
		if err != nil {
			return replanConfirmError{status: http.StatusInternalServerError, code: "TRIP_CONTENT_ENCODE_FAILED", message: "重规划结果保存失败"}
		}
		if err := tx.Model(&trip).Update("content", string(encoded)).Error; err != nil {
			return replanConfirmError{status: http.StatusInternalServerError, code: "REPLAN_APPLY_FAILED", message: "重规划结果保存失败"}
		}
		acceptedPayload, _ := json.Marshal(map[string]any{"proposal_id": req.ProposalID, "affected_nodes": req.AffectedNodes, "confirmation": req.Confirmation})
		appliedPayload, _ := json.Marshal(map[string]any{"proposal_id": req.ProposalID, "changed_nodes": req.AffectedNodes, "preserved_nodes": len(route) - 1, "candidate_name": candidateName, "confirmation": req.Confirmation})
		if err := tx.Create(&models.PlanningEvent{UserID: userID, TripID: req.TripID, EventType: "replan_proposal_accepted", Payload: string(acceptedPayload), CreatedAt: time.Now().UTC()}).Error; err != nil {
			return replanConfirmError{status: http.StatusInternalServerError, code: "REPLAN_EVENT_WRITE_FAILED", message: "重规划确认记录失败"}
		}
		if err := tx.Create(&models.PlanningEvent{UserID: userID, TripID: req.TripID, EventType: "replan_applied", Payload: string(appliedPayload), CreatedAt: time.Now().UTC()}).Error; err != nil {
			return replanConfirmError{status: http.StatusInternalServerError, code: "REPLAN_EVENT_WRITE_FAILED", message: "重规划应用记录失败"}
		}
		proposal["status"] = "applied"
		proposalBytes, _ := json.Marshal(proposal)
		if err := tx.Model(&proposalEvent).Update("payload", string(proposalBytes)).Error; err != nil {
			return replanConfirmError{status: http.StatusInternalServerError, code: "REPLAN_PROPOSAL_UPDATE_FAILED", message: "重规划提案状态更新失败"}
		}
		result = gin.H{"trip_id": req.TripID, "proposal_id": req.ProposalID, "status": "applied", "changed_nodes": req.AffectedNodes, "preserved_nodes": len(route) - 1, "route": route, "candidate": next}
		return nil
	})
	if err != nil {
		if typed, ok := err.(replanConfirmError); ok {
			planningError(c, typed.status, typed.code, typed.message, nil)
			return
		}
		planningError(c, http.StatusInternalServerError, "REPLAN_APPLY_FAILED", "重规划结果保存失败", nil)
		return
	}
	planningData(c, result)
}

type replanConfirmError struct {
	status  int
	code    string
	message string
}

func (e replanConfirmError) Error() string { return e.message }

func proposalAffectedNodes(proposal map[string]any) []string {
	if raw, ok := proposal["affected_nodes"].([]any); ok {
		out := make([]string, 0, len(raw))
		for _, item := range raw {
			if name := strings.TrimSpace(fmt.Sprint(item)); name != "" && name != "<nil>" {
				out = append(out, name)
			}
		}
		return out
	}
	if node := strings.TrimSpace(fmt.Sprint(proposal["node_key"])); node != "" && node != "<nil>" {
		if index := strings.Index(node, ":"); index >= 0 {
			return []string{node[index+1:]}
		}
		return []string{node}
	}
	return nil
}

func routeFromDocument(document map[string]any) ([]map[string]any, string) {
	for _, key := range []string{"routes", "route"} {
		if raw, ok := document[key].([]any); ok {
			out := make([]map[string]any, 0, len(raw))
			for _, item := range raw {
				if node, ok := item.(map[string]any); ok {
					out = append(out, node)
				}
			}
			return out, key
		}
	}
	return nil, "routes"
}

func setRouteOnDocument(document map[string]any, key string, route []map[string]any) {
	items := make([]any, 0, len(route))
	for _, node := range route {
		items = append(items, node)
	}
	document[key] = items
}

func nodeName(node map[string]any) string {
	for _, key := range []string{"name", "location"} {
		if value := strings.TrimSpace(fmt.Sprint(node[key])); value != "" && value != "<nil>" {
			return value
		}
	}
	return ""
}

func nodeKey(node map[string]any) string {
	day := strings.TrimSpace(fmt.Sprint(node["day"]))
	if day == "" || day == "<nil>" {
		day = "1"
	}
	return day + ":" + nodeName(node)
}

func candidateHasSource(candidate map[string]any) bool {
	source := strings.TrimSpace(fmt.Sprint(candidate["source"]))
	if source != "" && source != "<nil>" && source != "map[]" {
		return true
	}
	if nested, ok := candidate["source"].(map[string]any); ok {
		provider := strings.TrimSpace(fmt.Sprint(nested["provider"]))
		if provider == "" || provider == "<nil>" {
			provider = strings.TrimSpace(fmt.Sprint(nested["Provider"]))
		}
		return provider != "" && provider != "<nil>"
	}
	return false
}
