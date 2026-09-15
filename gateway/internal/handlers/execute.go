package handlers

import (
	"fmt"
	"net/http"
	"strings"
	"time"

	"gateway/internal/contracts"
	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

var mutationTools = map[string][]string{
	"LODGING":   {"tuniuHotelCreateOrder", "saveOrder"},
	"TRANSPORT": {"bookTrain", "saveOrder"},
	"SCENIC":    {"create_ticket_order", "saveOrder"},
	"DINING":    {"saveOrder"},
}

// CreateOrderHandler executes a supplier mutation directly through MCP. It is
// deliberately separate from LLM planning: confirmation and idempotency are
// required before any supplier side effect is attempted.
func CreateOrderHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	var req struct {
		contracts.PlanningContext
		Provider  string         `json:"provider"`
		OrderType string         `json:"order_type" binding:"required"`
		Confirm   bool           `json:"confirm"`
		Fields    map[string]any `json:"fields"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "请求数据格式不正确", "code": "INVALID_JSON"})
		return
	}
	if !req.Confirm {
		c.JSON(http.StatusBadRequest, gin.H{"error": "下单必须显式确认", "code": "ORDER_CONFIRMATION_REQUIRED"})
		return
	}
	req.TripID = strings.TrimSpace(req.TripID)
	if req.TripID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少 trip_id", "code": "TRIP_ID_REQUIRED"})
		return
	}
	if _, ok := requireTripOrRoomAccess(c, req.TripID); !ok {
		return
	}
	if database.DB == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "数据服务暂不可用", "code": "DATABASE_UNAVAILABLE"})
		return
	}
	idempotency := strings.TrimSpace(c.GetHeader("Idempotency-Key"))
	if idempotency == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少 Idempotency-Key", "code": "IDEMPOTENCY_KEY_REQUIRED"})
		return
	}
	var prior models.TravelOrder
	if database.DB != nil && database.DB.Where("idempotency_key = ? AND user_id = ?", idempotency, userID).First(&prior).Error == nil {
		c.JSON(http.StatusOK, orderResponse(prior))
		return
	}
	kind := normalizeOrderKind(req.OrderType)
	configs := resolveProviderConfigs(kind)
	provider := strings.TrimSpace(req.Provider)
	var config providerConfig
	for _, candidate := range configs {
		label := candidate.label
		if label == "" {
			label = providerName(kind, candidate.base)
		}
		if provider == "" || strings.EqualFold(provider, label) || strings.Contains(strings.ToLower(candidate.base), strings.ToLower(provider)) {
			config, provider = candidate, candidate.label
			if provider == "" {
				provider = label
			}
			break
		}
	}
	if config.base == "" {
		c.JSON(http.StatusBadGateway, gin.H{"error": "未配置可用供应商", "code": "PROVIDER_NOT_CONFIGURED"})
		return
	}
	tools, err := discoverMCPTools(c.Request.Context(), config.base, config.token, mcpAuthHeader(kind, provider, config.base))
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "供应商工具发现失败", "code": "PROVIDER_TOOLS_UNAVAILABLE"})
		return
	}
	tool := chooseMutationTool(tools, kind, false)
	if tool.Name == "" {
		c.JSON(http.StatusBadGateway, gin.H{"error": "供应商未提供下单工具", "code": "ORDER_TOOL_UNAVAILABLE"})
		return
	}
	req.UserID = userID
	args := mcpArgumentsForOrder(tool, req.PlanningContext, req.Fields)
	if missing := mcpRequiredProperties(tool, args); len(missing) > 0 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "下单字段不完整", "code": "ORDER_FIELDS_REQUIRED", "details": gin.H{"fields": missing}})
		return
	}
	payload, err := callMCPTool(c.Request.Context(), config.base, config.token, mcpAuthHeader(kind, provider, config.base), tool.Name, args)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "供应商下单失败", "code": "ORDER_PROVIDER_FAILED"})
		return
	}
	providerOrderID := extractOrderString(payload, "order_id", "orderId", "orderNo", "orderNumber", "booking_id", "bookingId", "id")
	if providerOrderID == "" {
		c.JSON(http.StatusBadGateway, gin.H{"error": "供应商未返回订单号", "code": "ORDER_ID_MISSING"})
		return
	}
	status := normalizeOrderStatus(extractOrderString(payload, "status", "orderStatus", "state"))
	if status == "" {
		status = "pending"
	}
	order := models.TravelOrder{ID: uuid.NewString(), Provider: provider, ProviderOrderID: providerOrderID, UserID: userID, TripID: req.TripID, OrderType: kind, Status: status, IdempotencyKey: idempotency}
	if err := database.DB.Create(&order).Error; err != nil {
		var duplicate models.TravelOrder
		if database.DB.Where("idempotency_key = ? AND user_id = ?", idempotency, userID).First(&duplicate).Error == nil {
			c.JSON(http.StatusOK, orderResponse(duplicate))
			return
		}
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "订单记录失败", "code": "ORDER_PERSIST_FAILED"})
		return
	}
	c.JSON(http.StatusOK, orderResponse(order))
}

func CancelOrderHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	id := strings.TrimSpace(c.Param("id"))
	if id == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少订单号", "code": "ORDER_ID_REQUIRED"})
		return
	}
	var order models.TravelOrder
	if err := database.DB.Where("id = ? AND user_id = ?", id, userID).First(&order).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "订单不存在", "code": "ORDER_NOT_FOUND"})
		return
	}
	if order.Status == "cancelled" {
		c.JSON(http.StatusOK, orderResponse(order))
		return
	}
	configs := resolveProviderConfigs(order.OrderType)
	var config providerConfig
	for _, candidate := range configs {
		label := candidate.label
		if label == "" {
			label = providerName(order.OrderType, candidate.base)
		}
		if strings.EqualFold(label, order.Provider) {
			config = candidate
			break
		}
	}
	if config.base == "" {
		c.JSON(http.StatusBadGateway, gin.H{"error": "未配置可用供应商", "code": "PROVIDER_NOT_CONFIGURED"})
		return
	}
	tools, err := discoverMCPTools(c.Request.Context(), config.base, config.token, mcpAuthHeader(order.OrderType, order.Provider, config.base))
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "供应商工具发现失败", "code": "PROVIDER_TOOLS_UNAVAILABLE"})
		return
	}
	tool := chooseMutationTool(tools, order.OrderType, true)
	if tool.Name == "" {
		c.JSON(http.StatusBadGateway, gin.H{"error": "供应商未提供取消工具", "code": "CANCEL_TOOL_UNAVAILABLE"})
		return
	}
	args := mcpArgumentsForOrder(tool, contracts.PlanningContext{UserID: userID, TripID: order.TripID}, map[string]any{"order_id": order.ProviderOrderID, "orderId": order.ProviderOrderID, "booking_id": order.ProviderOrderID})
	if missing := mcpRequiredProperties(tool, args); len(missing) > 0 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "取消字段不完整", "code": "CANCEL_FIELDS_REQUIRED"})
		return
	}
	if _, err := callMCPTool(c.Request.Context(), config.base, config.token, mcpAuthHeader(order.OrderType, order.Provider, config.base), tool.Name, args); err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "供应商取消失败", "code": "CANCEL_PROVIDER_FAILED"})
		return
	}
	order.Status = "cancelled"
	order.CancellationNote = "provider_confirmed"
	order.UpdatedAt = time.Now().UTC()
	_ = database.DB.Save(&order).Error
	c.JSON(http.StatusOK, orderResponse(order))
}

func chooseMutationTool(tools []mcpTool, kind string, cancel bool) mcpTool {
	if cancel {
		for _, item := range tools {
			if strings.EqualFold(item.Name, "cancelOrder") {
				return item
			}
		}
		return mcpTool{}
	}
	for _, preferred := range mutationTools[strings.ToUpper(kind)] {
		for _, item := range tools {
			if strings.EqualFold(item.Name, preferred) {
				return item
			}
		}
	}
	return mcpTool{}
}

func normalizeOrderKind(value string) string {
	switch strings.ToUpper(strings.TrimSpace(value)) {
	case "HOTEL", "LODGING", "住宿":
		return "LODGING"
	case "FLIGHT", "TRAIN", "TRANSPORT", "交通", "机票", "火车":
		return "TRANSPORT"
	case "TICKET", "SCENIC", "门票", "景点":
		return "SCENIC"
	case "DINING", "餐饮":
		return "DINING"
	default:
		return strings.ToUpper(strings.TrimSpace(value))
	}
}

func extractOrderString(value any, keys ...string) string {
	if object, ok := value.(map[string]any); ok {
		for _, key := range keys {
			for actual, candidate := range object {
				if strings.EqualFold(actual, key) {
					if text := strings.TrimSpace(fmt.Sprint(candidate)); text != "" && text != "<nil>" {
						return text
					}
				}
			}
		}
		for _, candidate := range object {
			if nested := extractOrderString(candidate, keys...); nested != "" {
				return nested
			}
		}
	}
	if list, ok := value.([]any); ok {
		for _, candidate := range list {
			if nested := extractOrderString(candidate, keys...); nested != "" {
				return nested
			}
		}
	}
	return ""
}

func normalizeOrderStatus(status string) string {
	switch strings.ToLower(strings.TrimSpace(status)) {
	case "created", "success", "confirmed", "已支付":
		return "created"
	case "cancelled", "canceled", "已取消":
		return "cancelled"
	case "failed", "error", "失败":
		return "failed"
	case "pending", "processing", "处理中":
		return "pending"
	}
	return ""
}

func orderResponse(order models.TravelOrder) gin.H {
	return gin.H{"order_id": order.ID, "provider_order_id": order.ProviderOrderID, "status": order.Status, "provider": order.Provider, "order_type": order.OrderType, "trip_id": order.TripID, "cancelled": order.Status == "cancelled"}
}
