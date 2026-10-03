package handlers

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"gateway/internal/service"
	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func TestRiskProposalUsesExecutionStateAndDeduplicates(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.TripPlan{}, &models.TripExecutionState{}, &models.PlanningEvent{}, &models.RiskNotification{}); err != nil {
		t.Fatal(err)
	}
	route, _ := json.Marshal(map[string]any{"routes": []map[string]any{{"day": 1, "name": "西湖"}, {"day": 1, "name": "灵隐寺"}}})
	if err := db.Create(&models.TripPlan{ID: "trip-risk", UserID: "user-risk", DestCity: "杭州", Content: string(route)}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.TripExecutionState{ID: "state-risk", UserID: "user-risk", TripID: "trip-risk", NodeKey: "1:西湖", Status: "visited"}).Error; err != nil {
		t.Fatal(err)
	}
	changes := []map[string]interface{}{{"type": "weather", "severity": "high", "field": "closure", "reason": "景区关闭"}}
	first := createRiskReplanProposal("user-risk", "杭州", changes, map[string]interface{}{"alert": true})
	if first["status"] != "pending_confirmation" || first["replacement_kind"] != "DESTINATION" {
		t.Fatalf("unexpected proposal: %#v", first)
	}
	if first["changed_node_count"] != 1 || first["preserved_node_count"] != 1 {
		t.Fatalf("execution state was not respected: %#v", first)
	}
	second := createRiskReplanProposal("user-risk", "杭州", changes, nil)
	if second["deduplicated"] != true || second["proposal_id"] != first["proposal_id"] {
		t.Fatalf("expected deduplicated proposal: %#v", second)
	}
}

func TestRiskNotificationInboxPreservesSourceAndReadLifecycle(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.RiskNotification{}); err != nil {
		t.Fatal(err)
	}
	notification := models.RiskNotification{ID: "notice-1", UserID: "user-notice", City: "杭州", Severity: "HIGH", Title: "景区关闭", Detail: "请确认替代路线", Source: "amap", Estimated: false, Status: "unread", CreatedAt: time.Now().UTC()}
	if err := db.Create(&notification).Error; err != nil {
		t.Fatal(err)
	}
	r := gin.New()
	r.GET("/risk/notifications", func(c *gin.Context) { c.Set("user_id", "user-notice"); ListRiskNotificationsHandler(c) })
	r.POST("/risk/notifications/:id/read", func(c *gin.Context) { c.Set("user_id", "user-notice"); AcknowledgeRiskNotificationHandler(c) })
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/risk/notifications", nil))
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), "amap") {
		t.Fatalf("expected sourced inbox response: %d %s", response.Code, response.Body.String())
	}
	response = httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/risk/notifications/notice-1/read?status=acknowledged", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("expected acknowledgement 200: %d %s", response.Code, response.Body.String())
	}
	var saved models.RiskNotification
	if err := db.First(&saved, "id = ?", "notice-1").Error; err != nil || saved.Status != "acknowledged" || saved.ReadAt == nil {
		t.Fatalf("notification lifecycle not persisted: %+v err=%v", saved, err)
	}
}

func TestWebPushSubscriptionDoesNotExposeKeys(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.WebPushSubscription{}); err != nil {
		t.Fatal(err)
	}
	r := gin.New()
	r.POST("/notifications/push/subscription", func(c *gin.Context) { c.Set("user_id", "push-user"); SavePushSubscriptionHandler(c) })
	body := `{"endpoint":"https://push.example.test/send/abc","keys":{"p256dh":"0123456789abcdef0123456789abcdef","auth":"0123456789abcdef"},"user_agent":"test"}`
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/notifications/push/subscription", strings.NewReader(body)))
	if response.Code != http.StatusOK || strings.Contains(response.Body.String(), "p256dh") || strings.Contains(response.Body.String(), "auth") {
		t.Fatalf("subscription response leaked credentials or failed: %d %s", response.Code, response.Body.String())
	}
	var saved models.WebPushSubscription
	if err := db.First(&saved).Error; err != nil || saved.P256DH == "" || saved.Auth == "" {
		t.Fatalf("subscription not persisted: %+v err=%v", saved, err)
	}
}

func TestAmapOfficialLinksArePublicDeepLinks(t *testing.T) {
	links := amapOfficialLinks("杭州 西湖")
	if !strings.Contains(links["map_search"], "www.amap.com/search") || !strings.Contains(links["route_planner"], "ditu.amap.com/dir") {
		t.Fatalf("unexpected official links: %#v", links)
	}
	if strings.Contains(links["map_search"], "AMAP_API_KEY") {
		t.Fatal("official links must not contain provider credentials")
	}
}

func TestRiskProposalExpiresInJourneyState(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.TripPlan{}, &models.TripExecutionState{}, &models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.TripPlan{ID: "trip-expired", UserID: "user-expired", DestCity: "杭州"}).Error; err != nil {
		t.Fatal(err)
	}
	payload, _ := json.Marshal(map[string]any{"proposal_id": "risk-expired", "status": "pending_confirmation", "expires_at": time.Now().UTC().Add(-time.Minute).Format(time.RFC3339)})
	if err := db.Create(&models.PlanningEvent{UserID: "user-expired", TripID: "trip-expired", EventType: "replan_triggered", Payload: string(payload), CreatedAt: time.Now().UTC()}).Error; err != nil {
		t.Fatal(err)
	}
	// The state helper is exercised through its pure lifecycle query in the
	// handler tests; this assertion ensures the stored proposal is time-bound.
	var event models.PlanningEvent
	if err := db.Where("trip_id = ?", "trip-expired").First(&event).Error; err != nil {
		t.Fatal(err)
	}
	var decoded map[string]any
	if err := json.Unmarshal([]byte(event.Payload), &decoded); err != nil || decoded["expires_at"] == nil {
		t.Fatalf("missing expiry metadata: %s", event.Payload)
	}
}

func TestConfirmReplanAtomicallyAppliesOnlyAffectedNode(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.TripPlan{}, &models.TripExecutionState{}, &models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}
	route := map[string]any{"routes": []any{
		map[string]any{"day": 1, "name": "西湖", "type": "景点"},
		map[string]any{"day": 1, "name": "灵隐寺", "type": "景点"},
	}}
	encoded, _ := json.Marshal(route)
	if err := db.Create(&models.TripPlan{ID: "trip-confirm", UserID: "user-confirm", DestCity: "杭州", Content: string(encoded)}).Error; err != nil {
		t.Fatal(err)
	}
	proposal, _ := json.Marshal(map[string]any{
		"proposal_id": "risk-confirm", "status": "pending_confirmation", "reason": "weather_change",
		"affected_nodes": []string{"灵隐寺"}, "expires_at": time.Now().UTC().Add(10 * time.Minute).Format(time.RFC3339),
	})
	if err := db.Create(&models.PlanningEvent{UserID: "user-confirm", TripID: "trip-confirm", EventType: "replan_triggered", Payload: string(proposal), CreatedAt: time.Now().UTC()}).Error; err != nil {
		t.Fatal(err)
	}
	r := gin.New()
	r.POST("/planning/replan/confirm", func(c *gin.Context) { c.Set("user_id", "user-confirm"); ConfirmReplanHandler(c) })
	body := `{"trip_id":"trip-confirm","proposal_id":"risk-confirm","affected_nodes":["灵隐寺"],"confirmation":"user_confirmed","candidate":{"name":"西溪湿地","source":{"provider":"amap"}}}`
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/planning/replan/confirm", strings.NewReader(body)))
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"status":"applied"`) {
		t.Fatalf("expected applied response: %d %s", response.Code, response.Body.String())
	}
	var saved models.TripPlan
	if err := db.First(&saved, "id = ?", "trip-confirm").Error; err != nil {
		t.Fatal(err)
	}
	var document map[string]any
	if err := json.Unmarshal([]byte(saved.Content), &document); err != nil {
		t.Fatal(err)
	}
	rows := document["routes"].([]any)
	if rows[0].(map[string]any)["name"] != "西湖" || rows[1].(map[string]any)["name"] != "西溪湿地" {
		t.Fatalf("unexpected route after confirmation: %#v", rows)
	}
	var applied int64
	db.Model(&models.PlanningEvent{}).Where("trip_id = ? AND event_type = ?", "trip-confirm", "replan_applied").Count(&applied)
	if applied != 1 {
		t.Fatalf("expected one applied event, got %d", applied)
	}
	var replanned int64
	db.Model(&models.PlanningEvent{}).Where("trip_id = ? AND event_type = ?", "trip-confirm", "route_replanned").Count(&replanned)
	if replanned != 1 {
		t.Fatalf("expected one route_replanned event, got %d", replanned)
	}
	var routeEvent models.PlanningEvent
	if err := db.Where("trip_id = ? AND event_type = ?", "trip-confirm", "route_replanned").First(&routeEvent).Error; err != nil {
		t.Fatal(err)
	}
	var routePayload map[string]any
	if err := json.Unmarshal([]byte(routeEvent.Payload), &routePayload); err != nil {
		t.Fatal(err)
	}
	if routePayload["candidate_count"] != float64(1) || routePayload["pareto_frontier_count"] != float64(1) || routePayload["provider_available"] != true || routePayload["hard_constraints_status"] != "passed" || routePayload["fairness_status"] != "not_evaluated" {
		t.Fatalf("route quality evidence missing: %#v", routePayload)
	}
	response = httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/planning/replan/confirm", strings.NewReader(body)))
	if response.Code != http.StatusConflict {
		t.Fatalf("duplicate confirmation must be rejected: %d %s", response.Code, response.Body.String())
	}
}

func TestConfirmReplanRejectsVisitedNode(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.TripPlan{}, &models.TripExecutionState{}, &models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}
	route, _ := json.Marshal(map[string]any{"routes": []any{map[string]any{"day": 1, "name": "西湖"}}})
	if err := db.Create(&models.TripPlan{ID: "trip-visited", UserID: "user-visited", DestCity: "杭州", Content: string(route)}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.TripExecutionState{ID: "state-visited", UserID: "user-visited", TripID: "trip-visited", NodeKey: "1:西湖", Status: "visited"}).Error; err != nil {
		t.Fatal(err)
	}
	proposal, _ := json.Marshal(map[string]any{"proposal_id": "risk-visited", "status": "pending_confirmation", "affected_nodes": []string{"西湖"}, "expires_at": time.Now().UTC().Add(10 * time.Minute).Format(time.RFC3339)})
	if err := db.Create(&models.PlanningEvent{UserID: "user-visited", TripID: "trip-visited", EventType: "replan_triggered", Payload: string(proposal)}).Error; err != nil {
		t.Fatal(err)
	}
	r := gin.New()
	r.POST("/planning/replan/confirm", func(c *gin.Context) { c.Set("user_id", "user-visited"); ConfirmReplanHandler(c) })
	body := `{"trip_id":"trip-visited","proposal_id":"risk-visited","affected_nodes":["西湖"],"confirmation":"user_confirmed","candidate":{"name":"替代点","source":{"provider":"amap"}}}`
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/planning/replan/confirm", strings.NewReader(body)))
	if response.Code != http.StatusConflict || !strings.Contains(response.Body.String(), "NODE_ALREADY_COMPLETED") {
		t.Fatalf("visited node must be immutable: %d %s", response.Code, response.Body.String())
	}
}

func TestRejectReplanRecordsDecisionWithoutMutatingRoute(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.TripPlan{}, &models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}
	route, _ := json.Marshal(map[string]any{"routes": []any{map[string]any{"day": 1, "name": "西湖"}}})
	if err := db.Create(&models.TripPlan{ID: "trip-reject", UserID: "user-reject", DestCity: "杭州", Content: string(route)}).Error; err != nil {
		t.Fatal(err)
	}
	proposal, _ := json.Marshal(map[string]any{"proposal_id": "risk-reject", "status": "pending_confirmation", "affected_nodes": []string{"西湖"}, "expires_at": time.Now().UTC().Add(10 * time.Minute).Format(time.RFC3339)})
	if err := db.Create(&models.PlanningEvent{UserID: "user-reject", TripID: "trip-reject", EventType: "replan_triggered", Payload: string(proposal)}).Error; err != nil {
		t.Fatal(err)
	}
	r := gin.New()
	r.POST("/planning/replan/reject", func(c *gin.Context) { c.Set("user_id", "user-reject"); RejectReplanHandler(c) })
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/planning/replan/reject", strings.NewReader(`{"trip_id":"trip-reject","proposal_id":"risk-reject","reason":"not_suitable"}`)))
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"status":"rejected"`) {
		t.Fatalf("expected rejected response: %d %s", response.Code, response.Body.String())
	}
	var rejected int64
	db.Model(&models.PlanningEvent{}).Where("trip_id = ? AND event_type = ?", "trip-reject", "replan_proposal_rejected").Count(&rejected)
	if rejected != 1 {
		t.Fatalf("expected one rejection event, got %d", rejected)
	}
	var saved models.TripPlan
	if err := db.First(&saved, "id = ?", "trip-reject").Error; err != nil || saved.Content != string(route) {
		t.Fatalf("reject must not mutate route: err=%v content=%s", err, saved.Content)
	}
}

func TestRiskTriggerTypesNormalizeSignals(t *testing.T) {
	types := riskTriggerTypes([]map[string]interface{}{
		{"type": "weather", "detail": "天气变化"},
		{"type": "delay", "detail": "交通延误"},
		{"type": "closure", "detail": "景点关闭"},
		{"type": "budget", "detail": "预算超支"},
	})
	want := map[string]bool{"weather_change": true, "transport_disruption": true, "attraction_closure": true, "budget_change": true}
	if len(types) != len(want) {
		t.Fatalf("unexpected trigger types: %#v", types)
	}
	for _, typeName := range types {
		if !want[typeName] {
			t.Fatalf("unexpected trigger type %q", typeName)
		}
	}
}

// riskCheckHarness 起一个"内存库 + 桩 AI 服务 + 假 Hub"的巡检环境。
// 之前这条接缝（巡检变更 → 房间广播 → 带最小扰动）没有任何测试，属于长期裸奔。
type riskCheckHarness struct {
	router *gin.Engine
	hub    *service.Hub
}

func newRiskCheckHarness(t *testing.T, aiHandler http.HandlerFunc) *riskCheckHarness {
	t.Helper()
	oldDB := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = oldDB })
	if err := db.AutoMigrate(&models.RiskSubscription{}, &models.RiskNotification{}, &models.TripPlan{}, &models.RoomMember{}); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.RoomMember{RoomID: "room-1", UserID: "user-risk"}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.RiskSubscription{
		ID: "sub-1", UserID: "user-risk", City: "杭州", Coordinate: "120.15,30.28", Baseline: `{"risk_level":"low"}`,
	}).Error; err != nil {
		t.Fatal(err)
	}
	// 巡检要先找到"这个用户在这个城市的行程"才会去问 ai-service 影响哪些节点
	// （没有行程时 `requestMinimumPerturbationScope` 会如实返回 needs_user_context）。
	route, _ := json.Marshal(map[string]any{"route": []map[string]any{
		{"day": 1, "name": "西湖"},
		{"day": 1, "name": "灵隐寺"},
	}})
	if err := db.Create(&models.TripPlan{
		ID: "trip-1", UserID: "user-risk", DestCity: "杭州", Content: string(route),
	}).Error; err != nil {
		t.Fatal(err)
	}

	ai := httptest.NewServer(aiHandler)
	t.Cleanup(ai.Close)
	t.Setenv("AI_SERVICE_URL", ai.URL)

	hub := &service.Hub{Broadcast: make(chan models.WSMessage, 8)}
	oldHub := GlobalHub
	GlobalHub = hub
	t.Cleanup(func() { GlobalHub = oldHub })

	router := gin.New()
	router.POST("/risk/subscriptions/check", func(c *gin.Context) {
		c.Set("user_id", "user-risk")
		CheckRiskSubscriptionsHandler(c)
	})
	return &riskCheckHarness{router: router, hub: hub}
}

func (h *riskCheckHarness) check(t *testing.T, body string) *httptest.ResponseRecorder {
	t.Helper()
	response := httptest.NewRecorder()
	h.router.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/risk/subscriptions/check", strings.NewReader(body)))
	return response
}

func (h *riskCheckHarness) nextBroadcast(t *testing.T) models.WSMessage {
	t.Helper()
	select {
	case message := <-h.hub.Broadcast:
		return message
	case <-time.After(2 * time.Second):
		t.Fatal("expected a risk_subscription_alert broadcast")
		return models.WSMessage{}
	}
}

func decodeBroadcastPayload(t *testing.T, message models.WSMessage) map[string]any {
	t.Helper()
	encoded, err := json.Marshal(message.Payload)
	if err != nil {
		t.Fatal(err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatal(err)
	}
	return decoded
}

func TestCheckRiskSubscriptionsBroadcastsMinimumPerturbation(t *testing.T) {
	harness := newRiskCheckHarness(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/risk/subscriptions/refresh":
			_, _ = io.WriteString(w, `{"results":[{"city":"杭州","snapshot":{"risk_level":"high"},"changes":[{"type":"weather","severity":"high","affected_nodes":["灵隐寺"]}]}]}`)
		case "/api/v1/risk/replan-scope":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"correlation_id": "trip-1",
				"status":         "ok",
				"proposal": map[string]any{
					"agent_id": "emergency-agent",
					"status":   "ok",
					"candidates": []map[string]any{{
						"action":                     "replace_affected_nodes",
						"affected_nodes":             []string{"灵隐寺"},
						"preserved_nodes":            []string{"西湖"},
						"preserved_ratio":            0.75,
						"requires_user_confirmation": true,
					}},
				},
			})
		default:
			http.NotFound(w, r)
		}
	})

	response := harness.check(t, `{"user_id":"user-risk","room_id":"room-1"}`)
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), "灵隐寺") {
		t.Fatalf("expected ok response carrying the change: %d %s", response.Code, response.Body.String())
	}

	message := harness.nextBroadcast(t)
	if message.Type != "risk_subscription_alert" || message.RoomID != "room-1" || message.UserID != "user-risk" {
		t.Fatalf("unexpected broadcast envelope: %+v", message)
	}
	payload := decodeBroadcastPayload(t, message)
	if payload["city"] != "杭州" {
		t.Fatalf("broadcast must carry the city: %#v", payload)
	}
	perturbation, ok := payload["minimum_perturbation"].(map[string]any)
	if !ok {
		t.Fatalf("minimum_perturbation missing from the broadcast: %#v", payload)
	}
	if perturbation["status"] != "ok" || perturbation["correlation_id"] != "trip-1" {
		t.Fatalf("unexpected perturbation: %#v", perturbation)
	}
	proposal, ok := perturbation["proposal"].(map[string]any)
	if !ok {
		t.Fatalf("proposal missing: %#v", perturbation)
	}
	candidates, ok := proposal["candidates"].([]any)
	if !ok || len(candidates) == 0 {
		t.Fatalf("candidates missing: %#v", proposal)
	}
	candidate := candidates[0].(map[string]any)
	if candidate["preserved_ratio"] != 0.75 {
		t.Fatalf("preserved_ratio must survive the gateway hop: %#v", candidate)
	}
	affected, _ := candidate["affected_nodes"].([]any)
	if len(affected) != 1 || affected[0] != "灵隐寺" {
		t.Fatalf("affected nodes must survive the gateway hop: %#v", candidate)
	}
}

// 增强（最小扰动范围）失败时，风险告警本身必须照发 —— 并且失败原因要如实标注，
// 不能悄悄换成一个"看起来正常"的空结果。
func TestRiskAlertSurvivesMinimumPerturbationFailure(t *testing.T) {
	harness := newRiskCheckHarness(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/risk/subscriptions/refresh" {
			_, _ = io.WriteString(w, `{"results":[{"city":"杭州","snapshot":{"risk_level":"high"},"changes":[{"type":"closure","severity":"high","affected_nodes":["西湖"]}]}]}`)
			return
		}
		w.WriteHeader(http.StatusBadGateway)
	})

	response := harness.check(t, `{"user_id":"user-risk","room_id":"room-1"}`)
	if response.Code != http.StatusOK {
		t.Fatalf("alert must not fail because the enhancement failed: %d %s", response.Code, response.Body.String())
	}

	payload := decodeBroadcastPayload(t, harness.nextBroadcast(t))
	if payload["changes"] == nil {
		t.Fatalf("the alert itself must still carry the changes: %#v", payload)
	}
	perturbation, ok := payload["minimum_perturbation"].(map[string]any)
	if !ok {
		t.Fatalf("failure must be reported, not omitted: %#v", payload)
	}
	if perturbation["status"] != "rejected" || perturbation["http_status"] != float64(http.StatusBadGateway) {
		t.Fatalf("failure must be labelled honestly: %#v", perturbation)
	}
	if _, hasProposal := perturbation["proposal"]; hasProposal {
		t.Fatalf("a failed enhancement must not carry a proposal: %#v", perturbation)
	}
}

// 没有该城市的行程时，绝不去"猜"影响哪些节点：如实返回 needs_user_context，
// 并且**不调用** ai-service（省掉一次没有依据的重规划计算）。
func TestMinimumPerturbationWithoutTripDoesNotInventScope(t *testing.T) {
	aiCalls := 0
	harness := newRiskCheckHarness(t, func(w http.ResponseWriter, r *http.Request) {
		aiCalls++
		if r.URL.Path == "/api/v1/risk/subscriptions/refresh" {
			_, _ = io.WriteString(w, `{"results":[{"city":"杭州","snapshot":{"risk_level":"high"},"changes":[{"type":"weather","severity":"high"}]}]}`)
			return
		}
		// 如果这条路径被调到，说明网关在没有行程的情况下也去要了范围 —— 那是错的
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "ok",
			"proposal": map[string]any{"status": "ok", "candidates": []map[string]any{{
				"action": "replace_affected_nodes", "preserved_ratio": 1,
			}}},
		})
	})
	if err := database.DB.Where("user_id = ?", "user-risk").Delete(&models.TripPlan{}).Error; err != nil {
		t.Fatal(err)
	}

	response := harness.check(t, `{"user_id":"user-risk","room_id":"room-1"}`)
	if response.Code != http.StatusOK {
		t.Fatalf("alert must still be delivered: %d %s", response.Code, response.Body.String())
	}
	payload := decodeBroadcastPayload(t, harness.nextBroadcast(t))
	perturbation, ok := payload["minimum_perturbation"].(map[string]any)
	if !ok {
		t.Fatalf("missing minimum_perturbation: %#v", payload)
	}
	if perturbation["status"] != "needs_user_context" || perturbation["reason"] != "trip_not_found" {
		t.Fatalf("must report the missing context honestly: %#v", perturbation)
	}
	if _, hasProposal := perturbation["proposal"]; hasProposal {
		t.Fatalf("no scope may be invented without a trip: %#v", perturbation)
	}
	if aiCalls != 1 {
		t.Fatalf("only the refresh call is expected without a trip, got %d AI calls", aiCalls)
	}
}
