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

func setupOrderTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.TravelOrder{}); err != nil {
		t.Fatal(err)
	}
	return db
}

func cancelOrderContext(userID, orderID string) (*gin.Context, *httptest.ResponseRecorder) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Set("user_id", userID)
	c.Params = gin.Params{{Key: "id", Value: orderID}}
	c.Request = httptest.NewRequest(http.MethodDelete, "/planning/orders/"+orderID, nil)
	return c, recorder
}

// 数据库未初始化时取消订单必须返回 503，而不是在 nil DB 上 panic。
func TestCancelOrderRequiresDatabase(t *testing.T) {
	old := database.DB
	database.DB = nil
	t.Cleanup(func() { database.DB = old })

	c, recorder := cancelOrderContext("user-1", "order-1")
	CancelOrderHandler(c)
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d: %s", recorder.Code, recorder.Body.String())
	}
}

// 不能取消其他账号的订单，且错误信息不泄露订单是否存在。
func TestCancelOrderRejectsOtherUser(t *testing.T) {
	db := setupOrderTestDB(t)
	if err := db.Create(&models.TravelOrder{
		ID: "order-other", Provider: "supplier", ProviderOrderID: "P-1",
		UserID: "user-2", TripID: "trip-1", OrderType: "TRANSPORT", Status: "created",
		IdempotencyKey: "key-other",
	}).Error; err != nil {
		t.Fatal(err)
	}

	c, recorder := cancelOrderContext("user-1", "order-other")
	CancelOrderHandler(c)
	if recorder.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d: %s", recorder.Code, recorder.Body.String())
	}
}

// 数据库未初始化时读取订单列表必须返回 503。
func TestListOrdersRequiresDatabase(t *testing.T) {
	old := database.DB
	database.DB = nil
	t.Cleanup(func() { database.DB = old })

	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Set("user_id", "user-1")
	c.Request = httptest.NewRequest(http.MethodGet, "/planning/orders", nil)

	ListOrdersHandler(c)
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d: %s", recorder.Code, recorder.Body.String())
	}
}

// 订单列表只返回当前账号的订单，并按创建时间倒序。
func TestListOrdersIsolatesUsersAndOrdersDesc(t *testing.T) {
	db := setupOrderTestDB(t)
	now := time.Now().UTC()
	for _, order := range []models.TravelOrder{
		{ID: "order-old", Provider: "supplier", ProviderOrderID: "P-OLD", UserID: "user-1", TripID: "trip-1", OrderType: "TRANSPORT", Status: "created", IdempotencyKey: "key-old", CreatedAt: now.Add(-2 * time.Hour), UpdatedAt: now},
		{ID: "order-new", Provider: "supplier", ProviderOrderID: "P-NEW", UserID: "user-1", TripID: "trip-1", OrderType: "LODGING", Status: "created", IdempotencyKey: "key-new", CreatedAt: now, UpdatedAt: now},
		{ID: "order-other", Provider: "supplier", ProviderOrderID: "P-OTHER", UserID: "user-2", TripID: "trip-2", OrderType: "SCENIC", Status: "created", IdempotencyKey: "key-other-2", CreatedAt: now, UpdatedAt: now},
	} {
		if err := db.Create(&order).Error; err != nil {
			t.Fatal(err)
		}
	}

	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Set("user_id", "user-1")
	c.Request = httptest.NewRequest(http.MethodGet, "/planning/orders", nil)

	ListOrdersHandler(c)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var payload struct {
		Orders []map[string]any `json:"orders"`
		Count  int              `json:"count"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload.Count != 2 || len(payload.Orders) != 2 {
		t.Fatalf("expected only 2 orders for user-1, got %#v", payload)
	}
	if payload.Orders[0]["order_id"] != "order-new" || payload.Orders[1]["order_id"] != "order-old" {
		t.Fatalf("expected newest first, got %#v", payload.Orders)
	}
	for _, order := range payload.Orders {
		if order["order_id"] == "order-other" {
			t.Fatalf("leaked another user's order: %#v", order)
		}
	}
}

func TestCancelOrderIdempotentForCancelledOrder(t *testing.T) {
	db := setupOrderTestDB(t)
	if err := db.Create(&models.TravelOrder{
		ID: "order-cancelled", Provider: "supplier", ProviderOrderID: "P-2",
		UserID: "user-1", TripID: "trip-1", OrderType: "TRANSPORT", Status: "cancelled",
		IdempotencyKey: "key-cancelled",
	}).Error; err != nil {
		t.Fatal(err)
	}

	c, recorder := cancelOrderContext("user-1", "order-cancelled")
	CancelOrderHandler(c)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["status"] != "cancelled" || payload["cancelled"] != true {
		t.Fatalf("unexpected cancellation response: %#v", payload)
	}
}
