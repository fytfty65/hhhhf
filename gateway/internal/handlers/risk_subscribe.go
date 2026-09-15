package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

// aiServiceBaseURL returns the configured internal AI service address. Keep
// credentials out of URLs and use a bounded client timeout for all proxies.
func aiServiceBaseURL() string {
	base := strings.TrimSpace(os.Getenv("AI_SERVICE_URL"))
	if base == "" {
		base = "http://127.0.0.1:8000"
	}
	return strings.TrimRight(base, "/")
}

// callAI 通用转发：POST 到 AI 服务，透传 X-Request-ID（P3 跨服务追踪），返回响应体与状态码。
func callAI(c *gin.Context, path string, body []byte) ([]byte, int, error) {
	ctx, cancel := context.WithTimeout(c.Request.Context(), 45*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, aiServiceBaseURL()+path, bytes.NewReader(body))
	if err != nil {
		return nil, http.StatusBadGateway, err
	}
	req.Header.Set("Content-Type", "application/json")
	if internalToken := strings.TrimSpace(os.Getenv("AI_SERVICE_INTERNAL_TOKEN")); internalToken != "" {
		req.Header.Set("X-Omni-Internal-Token", internalToken)
	}
	if rid := c.GetHeader("X-Request-ID"); rid != "" {
		req.Header.Set("X-Request-ID", rid)
	}
	resp, err := (&http.Client{Timeout: 45 * time.Second}).Do(req)
	if err != nil {
		return nil, http.StatusBadGateway, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20+1))
	if err != nil {
		return nil, http.StatusBadGateway, err
	}
	if len(data) > 4<<20 {
		return nil, http.StatusBadGateway, fmt.Errorf("AI response too large")
	}
	return data, resp.StatusCode, nil
}

// coordToString 将前端传入的坐标（[]lng,lat 或 "lng,lat"）统一归一化为 "lng,lat" 字符串。
func coordToString(coord interface{}) string {
	switch v := coord.(type) {
	case string:
		return v
	case []interface{}:
		if len(v) >= 2 {
			return fmt.Sprintf("%v,%v", v[0], v[1])
		}
	case []float64:
		if len(v) >= 2 {
			return fmt.Sprintf("%v,%v", v[0], v[1])
		}
	}
	return ""
}

// SubscribeRiskHandler 订阅/更新目的地风险监控（P5）。
// 首次订阅时向 AI 服务拉取当前风险快照作为基线，供旅中变更对比。
func SubscribeRiskHandler(c *gin.Context) {
	var req struct {
		UserID     string      `json:"user_id"` // authenticated principal is authoritative
		City       string      `json:"city" binding:"required"`
		Coordinate interface{} `json:"coordinate"` // 可选：lnglat 数组或 "lng,lat"
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	req.UserID = userID
	if len([]rune(req.City)) > 100 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "城市名称过长"})
		return
	}

	body, _ := json.Marshal(map[string]interface{}{"city": req.City, "coordinate": req.Coordinate})
	data, status, err := callAI(c, "/api/v1/risk/realtime", body)
	if err != nil || status != http.StatusOK {
		c.JSON(http.StatusBadGateway, gin.H{"error": "AI 服务不可用"})
		return
	}
	var snapResp struct {
		Snapshot map[string]interface{} `json:"snapshot"`
	}
	_ = json.Unmarshal(data, &snapResp)
	baselineJSON, _ := json.Marshal(snapResp.Snapshot)
	coordStr := coordToString(req.Coordinate)

	// 幂等订阅：已订阅则仅刷新基线与坐标，避免重复记录
	var sub models.RiskSubscription
	if err := database.DB.Where("user_id = ? AND city = ?", req.UserID, req.City).First(&sub).Error; err != nil {
		sub = models.RiskSubscription{
			ID:         uuid.New().String(),
			UserID:     req.UserID,
			City:       req.City,
			Coordinate: coordStr,
			Baseline:   string(baselineJSON),
		}
		database.DB.Create(&sub)
	} else {
		sub.Baseline = string(baselineJSON)
		sub.Coordinate = coordStr
		database.DB.Save(&sub)
	}

	c.JSON(http.StatusOK, gin.H{
		"message":      "已订阅目的地风险监控",
		"subscription": gin.H{"id": sub.ID, "city": sub.City, "baseline": snapResp.Snapshot},
	})
}

// UnsubscribeRiskHandler 取消指定城市的风险订阅（P5）。
func UnsubscribeRiskHandler(c *gin.Context) {
	var req struct {
		UserID string `json:"user_id"` // authenticated principal is authoritative
		City   string `json:"city" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	req.UserID = userID
	database.DB.Where("user_id = ? AND city = ?", req.UserID, req.City).Delete(&models.RiskSubscription{})
	c.JSON(http.StatusOK, gin.H{"message": "已取消订阅"})
}

// ListRiskSubscriptionsHandler 列出某用户全部目的地风险订阅（P5）。
func ListRiskSubscriptionsHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	var subs []models.RiskSubscription
	database.DB.Where("user_id = ?", userID).Order("created_at desc").Find(&subs)

	result := make([]gin.H, 0, len(subs))
	for _, s := range subs {
		result = append(result, gin.H{"id": s.ID, "city": s.City, "created_at": s.CreatedAt})
	}
	c.JSON(http.StatusOK, gin.H{"subscriptions": result})
}

// CheckRiskSubscriptionsHandler 刷新订阅城市风险，检测变更事件并下发通知（P5）。
// 前端可定时轮询本端点；若携带 room_id，则同时通过 WebSocket 广播风险变更通知给房间成员。
func CheckRiskSubscriptionsHandler(c *gin.Context) {
	var req struct {
		UserID string `json:"user_id"` // authenticated principal is authoritative
		RoomID string `json:"room_id"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	req.UserID = userID
	if req.RoomID != "" {
		if _, ok := requireRoomMember(c, req.RoomID); !ok {
			return
		}
	}

	var subs []models.RiskSubscription
	database.DB.Where("user_id = ?", req.UserID).Find(&subs)
	if len(subs) == 0 {
		c.JSON(http.StatusOK, gin.H{"alerts": []interface{}{}, "snapshots": []interface{}{}})
		return
	}

	payload := make([]map[string]interface{}, 0, len(subs))
	for _, s := range subs {
		var baseline map[string]interface{}
		_ = json.Unmarshal([]byte(s.Baseline), &baseline)
		item := map[string]interface{}{"city": s.City, "baseline": baseline}
		if s.Coordinate != "" {
			item["coordinate"] = s.Coordinate
		}
		payload = append(payload, item)
	}
	body, _ := json.Marshal(map[string]interface{}{"subscriptions": payload})
	data, status, err := callAI(c, "/api/v1/risk/subscriptions/refresh", body)
	if err != nil || status != http.StatusOK {
		c.JSON(http.StatusBadGateway, gin.H{"error": "AI 服务不可用", "alerts": []interface{}{}, "snapshots": []interface{}{}})
		return
	}

	var resp struct {
		Results []struct {
			City     string                   `json:"city"`
			Snapshot map[string]interface{}   `json:"snapshot"`
			Changes  []map[string]interface{} `json:"changes"`
		} `json:"results"`
	}
	_ = json.Unmarshal(data, &resp)

	alerts := []gin.H{}
	for _, r := range resp.Results {
		// Advance the persisted baseline after each successful refresh. Without
		// this, the same weather/risk change is emitted on every 30s poll.
		if r.Snapshot != nil {
			for i := range subs {
				if subs[i].City != r.City {
					continue
				}
				if nextBaseline, marshalErr := json.Marshal(r.Snapshot); marshalErr == nil {
					subs[i].Baseline = string(nextBaseline)
					_ = database.DB.Save(&subs[i]).Error
				}
				break
			}
		}
		if len(r.Changes) == 0 {
			continue
		}
		for _, ch := range r.Changes {
			alerts = append(alerts, gin.H{"city": r.City, "change": ch})
		}
		// A high-severity change creates a persisted replan proposal. The
		// current route is never overwritten; clients must explicitly accept it.
		replan := createRiskReplanProposal(req.UserID, r.City, r.Changes, r.Snapshot)
		if replan != nil {
			for i := range alerts {
				if alerts[i]["city"] == r.City {
					alerts[i]["replan"] = replan
				}
			}
		}
		// 定向房间广播风险变更通知（多人协作场景下实时同步）
		if req.RoomID != "" && GlobalHub != nil {
			GlobalHub.Broadcast <- models.WSMessage{
				Type:    "risk_subscription_alert",
				RoomID:  req.RoomID,
				UserID:  req.UserID,
				Payload: gin.H{"city": r.City, "snapshot": r.Snapshot, "changes": r.Changes},
			}
		}
	}

	c.JSON(http.StatusOK, gin.H{"alerts": alerts, "snapshots": resp.Results})
}

func createRiskReplanProposal(userID, city string, changes []map[string]interface{}, snapshot map[string]interface{}) gin.H {
	if !riskChangesRequireReplan(changes) || database.DB == nil {
		return nil
	}
	var trip models.TripPlan
	if database.DB.Where("user_id = ? AND dest_city = ?", userID, city).Order("created_at desc").First(&trip).Error != nil {
		return gin.H{"status": "needs_user_context", "reason": "trip_not_found", "city": city}
	}
	var document map[string]interface{}
	_ = json.Unmarshal([]byte(trip.Content), &document)
	route, _ := document["routes"].([]interface{})
	if route == nil {
		route, _ = document["route"].([]interface{})
	}
	proposal := gin.H{"proposal_id": fmt.Sprintf("risk-%d", time.Now().UTC().UnixNano()), "status": "pending_confirmation", "trip_id": trip.ID, "reason": "risk_signal_changed", "changed_signals": changes, "preserved_nodes": len(route), "generated_at": time.Now().UTC()}
	payload, _ := json.Marshal(proposal)
	_ = database.DB.Create(&models.PlanningEvent{UserID: userID, TripID: trip.ID, EventType: "replan_triggered", Payload: string(payload)}).Error
	_ = snapshot
	return proposal
}

func riskChangesRequireReplan(changes []map[string]interface{}) bool {
	for _, change := range changes {
		text := strings.ToLower(fmt.Sprint(change["severity"], " ", change["level"], " ", change["type"], " ", change["field"]))
		if strings.Contains(text, "high") || strings.Contains(text, "severe") || strings.Contains(text, "critical") || strings.Contains(text, "严重") || strings.Contains(text, "关闭") || strings.Contains(text, "cancel") || strings.Contains(text, "delay") || strings.Contains(text, "超支") {
			return true
		}
	}
	return len(changes) > 0
}
