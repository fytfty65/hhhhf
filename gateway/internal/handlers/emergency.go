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
	"github.com/gin-gonic/gin"
)

type emergencyAction struct {
	ID              string   `json:"id"`
	Type            string   `json:"type"`
	Title           string   `json:"title"`
	Reason          string   `json:"reason"`
	AffectedNodes   []string `json:"affected_nodes,omitempty"`
	PreserveNodes   []string `json:"preserve_nodes,omitempty"`
	ReplacementKind string   `json:"replacement_kind,omitempty"`
	RequiresConfirm bool     `json:"requires_confirmation"`
	Estimated       bool     `json:"estimated"`
}

// EmergencyEvaluateHandler converts external risk signals into safe,
// confirmable actions. It is deliberately deterministic and does not call a
// supplier or mutate a plan; the caller can pass an action to /planning/replan
// after the user confirms it.
func EmergencyEvaluateHandler(c *gin.Context) {
	var req struct {
		contracts.PlanningContext
		ChangedSignals []map[string]any `json:"changed_signals"`
		CurrentRoute   []map[string]any `json:"current_route"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_EMERGENCY_REQUEST", "应急评估参数不完整", nil)
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
	if len(req.CurrentRoute) > 200 {
		planningError(c, http.StatusBadRequest, "ROUTE_TOO_LARGE", "当前路线节点过多", nil)
		return
	}

	allNames := routeNames(req.CurrentRoute)
	actions := make([]emergencyAction, 0, 4)
	for index, signal := range req.ChangedSignals {
		text := strings.ToLower(fmt.Sprint(signal["type"], " ", signal["field"], " ", signal["title"], " ", signal["detail"], " ", signal["severity"]))
		affected, exact := affectedRouteNodes(signal, req.CurrentRoute)
		preserved := subtractRouteNames(allNames, affected)
		switch {
		case containsAny(text, "关闭", "closed", "closure", "cancel", "取消"):
			if exact {
				actions = append(actions, emergencyAction{ID: fmt.Sprintf("closure-%d", index), Type: "replace_closed_node", Title: "替换已关闭节点", Reason: "检测到关闭或取消信号", AffectedNodes: affected, PreserveNodes: preserved, ReplacementKind: "DESTINATION", RequiresConfirm: true, Estimated: true})
			} else {
				actions = append(actions, emergencyAction{ID: fmt.Sprintf("review-closure-%d", index), Type: "manual_review", Title: "需要确认关闭影响范围", Reason: "检测到关闭或取消信号，但未能匹配具体路线节点", PreserveNodes: allNames, RequiresConfirm: true, Estimated: true})
			}
		case containsAny(text, "delay", "延误", "拥堵", "traffic"):
			if exact {
				actions = append(actions, emergencyAction{ID: fmt.Sprintf("delay-%d", index), Type: "reorder_remaining", Title: "重排剩余路线", Reason: "检测到延误或拥堵信号，优先保留未受影响节点", AffectedNodes: affected, PreserveNodes: preserved, ReplacementKind: "TRANSPORT", RequiresConfirm: true, Estimated: true})
			} else {
				actions = append(actions, emergencyAction{ID: fmt.Sprintf("review-delay-%d", index), Type: "manual_review", Title: "需要确认延误影响范围", Reason: "检测到延误或拥堵信号，但未能匹配具体路线节点", PreserveNodes: allNames, RequiresConfirm: true, Estimated: true})
			}
		case containsAny(text, "weather", "天气", "暴雨", "台风", "高温", "降雪"):
			if exact {
				actions = append(actions, emergencyAction{ID: fmt.Sprintf("weather-%d", index), Type: "switch_weather_safe", Title: "切换天气友好路线", Reason: "检测到天气变化，优先室内或低暴露活动", AffectedNodes: affected, PreserveNodes: preserved, ReplacementKind: "DESTINATION", RequiresConfirm: true, Estimated: true})
			} else {
				actions = append(actions, emergencyAction{ID: fmt.Sprintf("review-weather-%d", index), Type: "manual_review", Title: "需要确认天气影响范围", Reason: "检测到天气变化，但未能匹配具体路线节点", PreserveNodes: allNames, RequiresConfirm: true, Estimated: true})
			}
		case containsAny(text, "budget", "超支", "价格", "涨价"):
			actions = append(actions, emergencyAction{ID: fmt.Sprintf("budget-%d", index), Type: "reduce_future_cost", Title: "压缩后续可选开支", Reason: "检测到预算压力，保留必须节点并降低可选消费", PreserveNodes: allNames, ReplacementKind: "DINING", RequiresConfirm: true, Estimated: true})
		}
	}
	// No recognized signal is still actionable: surface a safe review state
	// instead of silently claiming that the route is unaffected.
	if len(actions) == 0 && len(req.ChangedSignals) > 0 {
		actions = append(actions, emergencyAction{ID: "review-0", Type: "manual_review", Title: "需要人工确认影响范围", Reason: "风险信号无法安全归类，暂不自动替换节点", PreserveNodes: allNames, RequiresConfirm: true, Estimated: true})
	}
	proposalID := fmt.Sprintf("emergency-%d", time.Now().UTC().UnixNano())
	proposal := gin.H{"proposal_id": proposalID, "trip_id": ctx.TripID, "actions": actions, "signal_count": len(req.ChangedSignals), "route_node_count": len(req.CurrentRoute), "generated_at": time.Now().UTC(), "policy": "deterministic_confirm_before_mutation"}
	if payload, err := json.Marshal(proposal); err == nil && database.DB != nil {
		_ = database.DB.Create(&models.PlanningEvent{UserID: ctx.UserID, TripID: ctx.TripID, EventType: "emergency_evaluated", Payload: string(payload)}).Error
	}
	planningData(c, proposal)
}

func routeNames(route []map[string]any) []string {
	names := make([]string, 0, len(route))
	for _, node := range route {
		name := strings.TrimSpace(fmt.Sprint(node["name"]))
		if name == "" || name == "<nil>" {
			name = strings.TrimSpace(fmt.Sprint(node["location"]))
		}
		if name != "" && name != "<nil>" {
			names = append(names, name)
		}
	}
	return names
}

// affectedRouteNodes only returns nodes explicitly named by the signal. A
// broad weather/traffic signal is unsafe to apply to every node, so callers
// must review it manually when no exact match is available.
func affectedRouteNodes(signal map[string]any, route []map[string]any) ([]string, bool) {
	candidates := []string{}
	for _, key := range []string{"affected_nodes", "nodes", "node_names"} {
		if values, ok := signal[key].([]any); ok {
			for _, value := range values {
				if name := strings.TrimSpace(fmt.Sprint(value)); name != "" && name != "<nil>" {
					candidates = append(candidates, name)
				}
			}
		}
		if value, ok := signal[key].(string); ok && strings.TrimSpace(value) != "" {
			candidates = append(candidates, strings.TrimSpace(value))
		}
	}
	for _, key := range []string{"node_name", "location", "poi", "name"} {
		if value := strings.TrimSpace(fmt.Sprint(signal[key])); value != "" && value != "<nil>" {
			candidates = append(candidates, value)
		}
	}
	known := map[string]bool{}
	for _, name := range routeNames(route) {
		known[name] = true
	}
	matched := []string{}
	seen := map[string]bool{}
	for _, candidate := range candidates {
		for name := range known {
			if (name == candidate || strings.Contains(name, candidate) || strings.Contains(candidate, name)) && !seen[name] {
				matched, seen[name] = append(matched, name), true
			}
		}
	}
	return matched, len(matched) > 0
}

func subtractRouteNames(all, excluded []string) []string {
	blocked := map[string]bool{}
	for _, name := range excluded {
		blocked[name] = true
	}
	result := make([]string, 0, len(all))
	for _, name := range all {
		if !blocked[name] {
			result = append(result, name)
		}
	}
	return result
}

func containsAny(text string, terms ...string) bool {
	for _, term := range terms {
		if strings.Contains(text, strings.ToLower(term)) {
			return true
		}
	}
	return false
}
