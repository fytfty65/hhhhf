package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func TestPlanningEffectMetricsAggregatesDurableFacts(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.PlanningEvent{}, &models.BudgetPlan{}, &models.ExpenseRecord{}, &models.RiskNotification{}); err != nil {
		t.Fatal(err)
	}

	now := time.Now().UTC()
	events := []models.PlanningEvent{
		{UserID: "user-effect", TripID: "trip-a", EventType: "plan_generated", CreatedAt: now},
		{UserID: "user-effect", TripID: "trip-a", EventType: "plan_adopted", CreatedAt: now},
		{UserID: "user-effect", TripID: "trip-b", EventType: "plan_generated", CreatedAt: now},
		{UserID: "user-effect", TripID: "trip-b", EventType: "plan_adopted", CreatedAt: now},
		{UserID: "user-effect", TripID: "trip-b", EventType: "plan_generated", CreatedAt: now},
		{UserID: "user-effect", TripID: "trip-b", EventType: "plan_edited", CreatedAt: now},
		{UserID: "user-effect", TripID: "trip-b", EventType: "plan_swapped", CreatedAt: now},
		{UserID: "user-effect", TripID: "trip-b", EventType: "replan_triggered", CreatedAt: now},
		// 其它用户的数据不能混进来
		{UserID: "someone-else", TripID: "trip-a", EventType: "plan_generated", CreatedAt: now},
	}
	for index := 0; index < 3; index++ {
		events = append(events, models.PlanningEvent{
			UserID: "user-effect", TripID: "trip-b", EventType: "route_replanned", CreatedAt: now,
			Payload: `{"preserved_nodes":4,"changed_nodes":["西湖"]}`,
		})
	}
	if err := db.Create(&events).Error; err != nil {
		t.Fatal(err)
	}

	if err := db.Create(&models.BudgetPlan{ID: "budget-a", UserID: "user-effect", TripID: "trip-a", TotalBudgetCents: 100000, Currency: "CNY"}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.BudgetPlan{ID: "budget-b", UserID: "user-effect", TripID: "trip-b", TotalBudgetCents: 200000, Currency: "CNY"}).Error; err != nil {
		t.Fatal(err)
	}
	expenses := []models.ExpenseRecord{
		{ID: "expense-a", UserID: "user-effect", TripID: "trip-a", AmountCents: 120000, Currency: "CNY", CreatedAt: now},
		{ID: "expense-b", UserID: "user-effect", TripID: "trip-b", AmountCents: 150000, Currency: "CNY", CreatedAt: now},
		// 币种不同，不能拿来和 CNY 预算比较
		{ID: "expense-c", UserID: "user-effect", TripID: "trip-b", AmountCents: 900000, Currency: "USD", CreatedAt: now},
	}
	if err := db.Create(&expenses).Error; err != nil {
		t.Fatal(err)
	}

	readAt := func(offset time.Duration) *time.Time {
		value := now.Add(offset)
		return &value
	}
	notifications := []models.RiskNotification{
		{ID: "notice-1", UserID: "user-effect", TripID: "trip-a", Status: "read", CreatedAt: now, ReadAt: readAt(time.Minute)},
		{ID: "notice-2", UserID: "user-effect", TripID: "trip-a", Status: "acknowledged", CreatedAt: now, ReadAt: readAt(2 * time.Minute)},
		{ID: "notice-3", UserID: "user-effect", TripID: "trip-a", Status: "read", CreatedAt: now, ReadAt: readAt(5 * time.Minute)},
		// 未读通知不计入响应时间，但计入推送总量
		{ID: "notice-4", UserID: "user-effect", TripID: "trip-a", Status: "unread", CreatedAt: now},
	}
	if err := db.Create(&notifications).Error; err != nil {
		t.Fatal(err)
	}

	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/planning/effect-metrics", func(c *gin.Context) {
		c.Set("user_id", "user-effect")
		PlanningEffectMetricsHandler(c)
	})
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/planning/effect-metrics?window_days=30", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
	var body map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	data, ok := body["data"].(map[string]any)
	if !ok {
		t.Fatalf("expected data envelope: %s", response.Body.String())
	}

	adoption, _ := data["adoption"].(map[string]any)
	if adoption["generated"] != float64(3) || adoption["adopted"] != float64(2) || adoption["rate"] != 0.6667 {
		t.Fatalf("unexpected adoption: %#v", adoption)
	}

	preservation, _ := data["preservation"].(map[string]any)
	if preservation["samples"] != float64(3) || preservation["status"] != "available" {
		t.Fatalf("unexpected preservation: %#v", preservation)
	}
	// 4 个保留 + 1 个受影响 = 0.8
	if preservation["mean"] != 0.8 {
		t.Fatalf("expected preservation mean 0.8, got %#v", preservation["mean"])
	}

	edits, _ := data["user_edits"].(map[string]any)
	if edits["total"] != float64(3) || edits["trips"] != float64(1) {
		t.Fatalf("unexpected user edits: %#v", edits)
	}
	byType, _ := edits["by_type"].(map[string]any)
	if byType["plan_edited"] != float64(1) || byType["replan_triggered"] != float64(1) {
		t.Fatalf("unexpected edit breakdown: %#v", byType)
	}

	overrun, _ := data["budget_overrun"].(map[string]any)
	// trip-b 有一笔 USD 记账，和 CNY 预算不可比 → 整趟跳过；只剩 trip-a 可评估
	if overrun["trips"] != float64(1) || overrun["overruns"] != float64(1) {
		t.Fatalf("unexpected overrun: %#v", overrun)
	}
	if overrun["rate"] != 1.0 || overrun["status"] != "insufficient_data" {
		t.Fatalf("one evaluable trip should not be claimed as a stable rate: %#v", overrun)
	}

	riskResponse, _ := data["risk_response"].(map[string]any)
	if riskResponse["notifications"] != float64(4) || riskResponse["responded"] != float64(3) {
		t.Fatalf("unexpected risk response counts: %#v", riskResponse)
	}
	// 60s / 120s / 300s 的中位数是 120 秒
	if riskResponse["median_seconds"] != 120.0 {
		t.Fatalf("expected median 120s, got %#v", riskResponse["median_seconds"])
	}

	unknown, _ := data["unknown_data_accuracy"].(map[string]any)
	if unknown["status"] != "unavailable" {
		t.Fatalf("unknown accuracy must stay unavailable without ground truth: %#v", unknown)
	}
}

func TestPlanningEffectMetricsDoesNotInventValuesWithoutSamples(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.PlanningEvent{}, &models.BudgetPlan{}, &models.ExpenseRecord{}, &models.RiskNotification{}); err != nil {
		t.Fatal(err)
	}

	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/planning/effect-metrics", func(c *gin.Context) {
		c.Set("user_id", "user-empty")
		PlanningEffectMetricsHandler(c)
	})
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/planning/effect-metrics", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
	var body map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	data, _ := body["data"].(map[string]any)
	adoption, _ := data["adoption"].(map[string]any)
	if adoption["status"] != "insufficient_data" || adoption["rate"] != nil {
		t.Fatalf("no events must not produce an adoption percentage: %#v", adoption)
	}
	overrun, _ := data["budget_overrun"].(map[string]any)
	if overrun["rate"] != nil || overrun["trips"] != float64(0) {
		t.Fatalf("no expenses must not produce an overrun rate: %#v", overrun)
	}
	riskResponse, _ := data["risk_response"].(map[string]any)
	if riskResponse["median_seconds"] != nil {
		t.Fatalf("no responded notifications must not produce a response time: %#v", riskResponse)
	}
}
