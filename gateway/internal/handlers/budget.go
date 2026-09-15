package handlers

import (
	"net/http"

	"gateway/internal/database"
	"gateway/internal/models"
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

// SetBudgetHandler 创建或更新某行程的预算（支持多币种）。
func SetBudgetHandler(c *gin.Context) {
	var req struct {
		UserID      string  `json:"user_id"` // legacy field; authenticated principal is authoritative
		TripID      string  `json:"trip_id" binding:"required"`
		TotalBudget float64 `json:"total_budget" binding:"required"`
		Currency    string  `json:"currency"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if _, ok := requireTripOrRoomAccess(c, req.TripID); !ok {
		return
	}
	// The authenticated principal, never the body, owns the budget record.
	req.UserID = userID
	if req.TotalBudget <= 0 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "预算必须大于 0"})
		return
	}
	if req.Currency == "" {
		req.Currency = "CNY"
	}
	if !service.IsKnownCurrency(req.Currency) {
		req.Currency = "CNY"
	}

	var plan models.BudgetPlan
	err := database.DB.Where("user_id = ? AND trip_id = ?", req.UserID, req.TripID).First(&plan).Error
	if err != nil {
		plan = models.BudgetPlan{
			ID:          uuid.New().String(),
			UserID:      req.UserID,
			TripID:      req.TripID,
			TotalBudget: req.TotalBudget,
			Currency:    req.Currency,
		}
		if e := database.DB.Create(&plan).Error; e != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "预算创建失败"})
			return
		}
	} else {
		plan.TotalBudget = req.TotalBudget
		plan.Currency = req.Currency
		database.DB.Save(&plan)
	}

	c.JSON(http.StatusOK, gin.H{"message": "预算已设置", "budget_plan": plan})
}

// AddExpenseHandler 记录一笔实际消费（支持多币种，自动换算）。
func AddExpenseHandler(c *gin.Context) {
	var req struct {
		UserID   string  `json:"user_id"` // legacy field; authenticated principal is authoritative
		TripID   string  `json:"trip_id" binding:"required"`
		Category string  `json:"category"`
		Amount   float64 `json:"amount" binding:"required"`
		Currency string  `json:"currency"`
		Note     string  `json:"note"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if _, ok := requireTripOrRoomAccess(c, req.TripID); !ok {
		return
	}
	req.UserID = userID
	if req.Amount <= 0 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "消费金额必须大于 0"})
		return
	}
	if req.Category == "" {
		req.Category = "其他"
	}
	if req.Currency == "" {
		req.Currency = "CNY"
	}
	if !service.IsKnownCurrency(req.Currency) {
		req.Currency = "CNY"
	}

	exp := models.ExpenseRecord{
		ID:       uuid.New().String(),
		UserID:   req.UserID,
		TripID:   req.TripID,
		Category: req.Category,
		Amount:   req.Amount,
		Currency: req.Currency,
		Note:     req.Note,
	}
	if err := database.DB.Create(&exp).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "消费记录失败"})
		return
	}

	// 记录后立即检查预算，触发超支预警
	summary := service.BudgetSummaryFor(req.UserID, req.TripID)

	c.JSON(http.StatusOK, gin.H{
		"message": "消费已记录",
		"expense": exp,
		"summary": summary,
	})
}

// OCRExpenseHandler 拍照记账：接收小票 OCR 文本，自动识别金额与分类并记一笔。
// 该端点与具体 OCR 引擎解耦（前端/第三方负责产出 ocr_text），金额识别逻辑集中在 service.ParseReceipt。
func OCRExpenseHandler(c *gin.Context) {
	var req struct {
		UserID   string `json:"user_id"` // legacy field; authenticated principal is authoritative
		TripID   string `json:"trip_id" binding:"required"`
		OCRText  string `json:"ocr_text" binding:"required"`
		Currency string `json:"currency"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if _, ok := requireTripOrRoomAccess(c, req.TripID); !ok {
		return
	}
	req.UserID = userID

	receipt, ok := service.ParseReceipt(req.OCRText)
	if !ok {
		c.JSON(http.StatusBadRequest, gin.H{"error": "未能从小票中识别出金额，请手动录入"})
		return
	}

	if req.Currency == "" || !service.IsKnownCurrency(req.Currency) {
		req.Currency = receipt.Currency
	}

	exp := models.ExpenseRecord{
		ID:       uuid.New().String(),
		UserID:   req.UserID,
		TripID:   req.TripID,
		Category: receipt.Category,
		Amount:   receipt.Amount,
		Currency: req.Currency,
		Note:     "拍照记账（OCR 自动识别）",
	}
	if err := database.DB.Create(&exp).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "消费记录失败"})
		return
	}

	summary := service.BudgetSummaryFor(req.UserID, req.TripID)
	c.JSON(http.StatusOK, gin.H{
		"message": "拍照记账成功",
		"expense": exp,
		"receipt": receipt,
		"summary": summary,
	})
}

// ListExpensesHandler 列出某行程的全部消费记录（供前端复盘展示）。
func ListExpensesHandler(c *gin.Context) {
	tripID := c.Query("trip_id")
	userID, ok := requireTripOrRoomAccess(c, tripID)
	if !ok {
		return
	}

	var expenses []models.ExpenseRecord
	database.DB.Where("user_id = ? AND trip_id = ?", userID, tripID).Order("created_at asc").Find(&expenses)

	c.JSON(http.StatusOK, gin.H{"expenses": expenses})
}

// BudgetSummaryHandler 获取预算汇总与超支预警（预算管家核心）。
func BudgetSummaryHandler(c *gin.Context) {
	tripID := c.Query("trip_id")
	userID, ok := requireTripOrRoomAccess(c, tripID)
	if !ok {
		return
	}

	summary := service.BudgetSummaryFor(userID, tripID)
	c.JSON(http.StatusOK, gin.H{"summary": summary})
}

// BudgetReviewHandler 行程结束后的消费复盘报告（分类明细 + 可视化数据）。
func BudgetReviewHandler(c *gin.Context) {
	tripID := c.Query("trip_id")
	userID, ok := requireTripOrRoomAccess(c, tripID)
	if !ok {
		return
	}

	summary := service.BudgetSummaryFor(userID, tripID)

	var expenses []models.ExpenseRecord
	database.DB.Where("user_id = ? AND trip_id = ?", userID, tripID).Order("created_at asc").Find(&expenses)

	c.JSON(http.StatusOK, gin.H{
		"summary":  summary,
		"expenses": expenses,
		"report": gin.H{
			"highest_category": topCategory(summary.ByCategory),
			"advice":           budgetAdvice(summary.Level),
		},
	})
}

// topCategory 返回消费最高分类。
func topCategory(byCategory map[string]float64) string {
	top := "其他"
	max := -1.0
	for k, v := range byCategory {
		if v > max {
			max = v
			top = k
		}
	}
	return top
}

// budgetAdvice 根据预警等级给出复盘建议。
func budgetAdvice(level string) string {
	switch level {
	case "over":
		return "本行程已超出预算，建议复盘交通与餐饮支出的可优化空间。"
	case "warning":
		return "预算使用已接近上限，注意控制后续消费节奏。"
	default:
		return "预算控制良好，可继续保持当前消费节奏。"
	}
}
