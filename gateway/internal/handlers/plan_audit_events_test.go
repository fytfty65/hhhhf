package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

// 网关侧用户动作发点的回归锁。
//
// 事故背景：事件台账里有 5 个类型只可能由网关发出（user.accepted / user.rejected /
// node.replaced / replan.confirmed / trip.visited），但**一个发点都没有** ——
// 事件流里永远只有"推演开始/结束"，用户采纳了什么、旅中改没改、到访没有，全都查不到。
// 这些测试把发点钉死：动作成功 → 事件必须发；而且必须带关联键与来源标注。

type recordedEvent struct {
	EventType   string
	Correlation string
	Producer    string
	Target      string
	RoomID      string
	Payload     map[string]any
}

func capturePlanEvents(t *testing.T) *[]recordedEvent {
	t.Helper()
	recorded := &[]recordedEvent{}
	original := emitPlanEvent
	emitPlanEvent = func(eventType, correlationID, producer, target, roomID string, payload map[string]any) {
		*recorded = append(*recorded, recordedEvent{
			EventType: eventType, Correlation: correlationID, Producer: producer,
			Target: target, RoomID: roomID, Payload: payload,
		})
	}
	t.Cleanup(func() { emitPlanEvent = original })
	return recorded
}

func findEvent(events []recordedEvent, eventType string) *recordedEvent {
	for index := range events {
		if events[index].EventType == eventType {
			return &events[index]
		}
	}
	return nil
}

func planAuditDB(t *testing.T, modelsToMigrate ...any) *gorm.DB {
	t.Helper()
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(modelsToMigrate...); err != nil {
		t.Fatal(err)
	}
	return db
}

// 行程内容里带着 ai-service 盖的关联键时，事件必须挂到同一条轨迹上。
func TestPlanAuditConfirmEmitsConfirmedAndNodeReplaced(t *testing.T) {
	db := planAuditDB(t, &models.TripPlan{}, &models.TripExecutionState{}, &models.PlanningEvent{})
	route, _ := json.Marshal(map[string]any{
		"correlation_id": "plan-abc123",
		"routes": []any{
			map[string]any{"day": 1, "name": "西湖"},
			map[string]any{"day": 1, "name": "灵隐寺"},
		},
	})
	if err := db.Create(&models.TripPlan{ID: "trip-audit", UserID: "user-audit", DestCity: "杭州", Content: string(route)}).Error; err != nil {
		t.Fatal(err)
	}
	proposal, _ := json.Marshal(map[string]any{
		"proposal_id": "risk-audit", "status": "pending_confirmation",
		"affected_nodes": []string{"灵隐寺"}, "expires_at": time.Now().UTC().Add(10 * time.Minute).Format(time.RFC3339),
	})
	if err := db.Create(&models.PlanningEvent{UserID: "user-audit", TripID: "trip-audit", EventType: "replan_triggered", Payload: string(proposal)}).Error; err != nil {
		t.Fatal(err)
	}
	recorded := capturePlanEvents(t)

	r := gin.New()
	r.POST("/planning/replan/confirm", func(c *gin.Context) { c.Set("user_id", "user-audit"); ConfirmReplanHandler(c) })
	body := `{"trip_id":"trip-audit","proposal_id":"risk-audit","affected_nodes":["灵隐寺"],"confirmation":"user_confirmed","candidate":{"name":"西溪湿地","source":{"provider":"amap"}}}`
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/planning/replan/confirm", strings.NewReader(body)))
	if response.Code != http.StatusOK {
		t.Fatalf("confirm failed: %d %s", response.Code, response.Body.String())
	}

	confirmed := findEvent(*recorded, "replan.confirmed")
	if confirmed == nil {
		t.Fatalf("replan.confirmed 没发出：%+v", *recorded)
	}
	if confirmed.Producer != "web-client" {
		t.Fatalf("确认重规划是用户决定，producer 应为 web-client：%+v", confirmed)
	}
	if confirmed.Correlation != "plan-abc123" || confirmed.Payload["correlation_source"] != "plan_content" {
		t.Fatalf("关联键必须取自行程内容并标注来源：%+v", confirmed)
	}
	if confirmed.Payload["candidate_name"] != "西溪湿地" {
		t.Fatalf("确认事件要写清换成了哪个候选：%+v", confirmed.Payload)
	}

	replaced := findEvent(*recorded, "node.replaced")
	if replaced == nil {
		t.Fatalf("node.replaced 没发出：%+v", *recorded)
	}
	if replaced.Producer != "gateway" {
		t.Fatalf("节点替换是网关执行的动作，producer 应为 gateway：%+v", replaced)
	}
	if replaced.Payload["replaced_node"] != "灵隐寺" || replaced.Payload["replacement"] != "西溪湿地" {
		t.Fatalf("节点替换事件要写清换掉了谁：%+v", replaced.Payload)
	}
}

func TestPlanAuditRejectEmitsUserRejected(t *testing.T) {
	db := planAuditDB(t, &models.TripPlan{}, &models.PlanningEvent{})
	route, _ := json.Marshal(map[string]any{"routes": []any{map[string]any{"day": 1, "name": "西湖"}}})
	if err := db.Create(&models.TripPlan{ID: "trip-reject-audit", UserID: "user-audit", DestCity: "杭州", Content: string(route)}).Error; err != nil {
		t.Fatal(err)
	}
	proposal, _ := json.Marshal(map[string]any{
		"proposal_id": "risk-reject-audit", "status": "pending_confirmation",
		"affected_nodes": []string{"西湖"}, "expires_at": time.Now().UTC().Add(10 * time.Minute).Format(time.RFC3339),
	})
	if err := db.Create(&models.PlanningEvent{UserID: "user-audit", TripID: "trip-reject-audit", EventType: "replan_triggered", Payload: string(proposal)}).Error; err != nil {
		t.Fatal(err)
	}
	recorded := capturePlanEvents(t)

	r := gin.New()
	r.POST("/planning/replan/reject", func(c *gin.Context) { c.Set("user_id", "user-audit"); RejectReplanHandler(c) })
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/planning/replan/reject",
		strings.NewReader(`{"trip_id":"trip-reject-audit","proposal_id":"risk-reject-audit","reason":"keep_original"}`)))
	if response.Code != http.StatusOK {
		t.Fatalf("reject failed: %d %s", response.Code, response.Body.String())
	}
	rejected := findEvent(*recorded, "user.rejected")
	if rejected == nil {
		t.Fatalf("user.rejected 没发出：%+v", *recorded)
	}
	if rejected.Payload["reason"] != "keep_original" || rejected.Payload["proposal_id"] != "risk-reject-audit" {
		t.Fatalf("拒绝事件要带受控原因与提案号：%+v", rejected.Payload)
	}
	// 内容里没有 correlation_id 时必须退回 trip_id 并如实标注，不能假装是同一条链
	if rejected.Correlation != "trip-reject-audit" || rejected.Payload["correlation_source"] != "trip_id" {
		t.Fatalf("关联键来源必须如实标注：%+v", rejected)
	}
}

func TestPlanAuditVisitedEmitsTripVisitedOnlyForVisited(t *testing.T) {
	db := planAuditDB(t, &models.TripPlan{}, &models.TripExecutionState{}, &models.PlanningEvent{})
	if err := db.Create(&models.TripPlan{ID: "trip-visit", UserID: "user-audit", DestCity: "杭州"}).Error; err != nil {
		t.Fatal(err)
	}
	recorded := capturePlanEvents(t)
	r := gin.New()
	r.POST("/planning/execution", func(c *gin.Context) { c.Set("user_id", "user-audit"); UpdateExecutionStateHandler(c) })

	post := func(status string) {
		response := httptest.NewRecorder()
		r.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/planning/execution",
			strings.NewReader(`{"trip_id":"trip-visit","node_key":"1:西湖","status":"`+status+`"}`)))
		if response.Code != http.StatusOK {
			t.Fatalf("execution update(%s) failed: %d %s", status, response.Code, response.Body.String())
		}
	}

	post("planned")
	if visited := findEvent(*recorded, "trip.visited"); visited != nil {
		t.Fatalf("计划内状态变更不该发 trip.visited（否则审计被噪音淹没）：%+v", visited)
	}

	post("visited")
	visited := findEvent(*recorded, "trip.visited")
	if visited == nil {
		t.Fatalf("到访必须发 trip.visited：%+v", *recorded)
	}
	if visited.Payload["node_key"] != "1:西湖" || visited.Payload["scope"] != "trip_execution" {
		t.Fatalf("到访事件要写清哪个节点：%+v", visited.Payload)
	}
}

func TestPlanAuditAdoptVariantEmitsUserAccepted(t *testing.T) {
	db := planAuditDB(t, &models.PlanVariant{}, &models.RoomMember{}, &models.BudgetPlan{})
	if err := db.Create(&models.RoomMember{RoomID: "room-audit", UserID: "user-audit"}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.PlanVariant{
		ID: "variant-audit", RoomID: "room-audit", Name: "慢游方案", Style: "relaxed",
		Route: `[{"day":1,"name":"西湖","cost_estimate":"¥0"}]`, CreatedBy: "user-audit",
	}).Error; err != nil {
		t.Fatal(err)
	}
	recorded := capturePlanEvents(t)

	r := gin.New()
	r.POST("/plan/variant/adopt", func(c *gin.Context) { c.Set("user_id", "user-audit"); AdoptVariantHandler(c) })
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/plan/variant/adopt",
		strings.NewReader(`{"variant_id":"variant-audit","user_id":"user-audit"}`)))
	if response.Code != http.StatusOK {
		t.Fatalf("adopt failed: %d %s", response.Code, response.Body.String())
	}
	accepted := findEvent(*recorded, "user.accepted")
	if accepted == nil {
		t.Fatalf("user.accepted 没发出：%+v", *recorded)
	}
	if accepted.Payload["variant"] != "慢游方案" || accepted.Payload["scope"] != "plan_variant" {
		t.Fatalf("采纳事件要写清采纳了哪套方案：%+v", accepted.Payload)
	}
	if accepted.RoomID != "room-audit" {
		t.Fatalf("采纳事件应能广播到房间：%+v", accepted)
	}
}
