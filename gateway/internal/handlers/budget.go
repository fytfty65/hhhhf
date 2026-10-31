package handlers

import (
	"math"
	"net/http"
	"strings"

	"gateway/internal/database"
	"gateway/internal/models"
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

// maxMoneyCents bounds input at 10 billion major units, far beyond any travel
// budget, so int64 accumulation can never overflow.
const maxMoneyCents int64 = 1_000_000_000_000

// resolveMoneyInput accepts the canonical integer-minor-unit field and, during
// the migration window, the legacy float field. The conversion happens once at
// the API boundary; nothing downstream sees a float.
func resolveMoneyInput(cents *int64, major *float64) (int64, bool) {
	if cents != nil {
		return *cents, true
	}
	if major != nil {
		if math.IsNaN(*major) || math.IsInf(*major, 0) {
			return 0, false
		}
		return int64(math.Round(*major * 100)), true
	}
	return 0, false
}

// normalizeMoneyCurrency rejects unknown codes instead of silently treating
// them as CNY, which used to turn a EUR amount into a CNY amount.
func normalizeMoneyCurrency(raw string) (string, bool) {
	code := strings.ToUpper(strings.TrimSpace(raw))
	if code == "" {
		return "CNY", true
	}
	if !service.IsKnownCurrency(code) {
		return "", false
	}
	return code, true
}

// SetBudgetHandler 创建或更新某行程的预算（支持多币种，金额以整数分存储）。
func SetBudgetHandler(c *gin.Context) {
	var req struct {
		UserID           string   `json:"user_id"` // legacy field; authenticated principal is authoritative
		TripID           string   `json:"trip_id" binding:"required"`
		TotalBudgetCents *int64   `json:"total_budget_cents"`
		TotalBudget      *float64 `json:"total_budget"` // legacy major-unit field
		Currency         string   `json:"currency"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整", "code": "INVALID_BUDGET_REQUEST"})
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
	cents, ok := resolveMoneyInput(req.TotalBudgetCents, req.TotalBudget)
	if !ok {
		c.JSON(http.StatusBadRequest, gin.H{"error": "请提供 total_budget_cents（整数分）", "code": "BUDGET_AMOUNT_REQUIRED"})
		return
	}
	if cents <= 0 || cents > maxMoneyCents {
		c.JSON(http.StatusBadRequest, gin.H{"error": "预算必须大于 0 且不超过合理上限", "code": "BUDGET_AMOUNT_INVALID"})
		return
	}
	currency, ok := normalizeMoneyCurrency(req.Currency)
	if !ok {
		c.JSON(http.StatusBadRequest, gin.H{"error": "不支持该币种", "code": "UNSUPPORTED_CURRENCY"})
		return
	}

	var plan models.BudgetPlan
	err := database.DB.Where("user_id = ? AND trip_id = ?", req.UserID, req.TripID).First(&plan).Error
	if err != nil {
		plan = models.BudgetPlan{
			ID:               uuid.New().String(),
			UserID:           req.UserID,
			TripID:           req.TripID,
			TotalBudgetCents: cents,
			Currency:         currency,
		}
		if e := database.DB.Create(&plan).Error; e != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "预算创建失败", "code": "BUDGET_CREATE_FAILED"})
			return
		}
	} else {
		plan.TotalBudgetCents = cents
		plan.Currency = currency
		database.DB.Save(&plan)
	}

	c.JSON(http.StatusOK, gin.H{
		"message":     "预算已设置",
		"budget_plan": plan,
		// Echo the normalised precision so clients never guess.
		"precision": service.CurrencyPrecision,
	})
}

// AddExpenseHandler 记录一笔实际消费（支持多币种，自动换算）。
func AddExpenseHandler(c *gin.Context) {
	var req struct {
		UserID      string   `json:"user_id"` // legacy field; authenticated principal is authoritative
		TripID      string   `json:"trip_id" binding:"required"`
		Category    string   `json:"category"`
		AmountCents *int64   `json:"amount_cents"`
		Amount      *float64 `json:"amount"` // legacy major-unit field
		Currency    string   `json:"currency"`
		Note        string   `json:"note"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整", "code": "INVALID_EXPENSE_REQUEST"})
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
	cents, ok := resolveMoneyInput(req.AmountCents, req.Amount)
	if !ok {
		c.JSON(http.StatusBadRequest, gin.H{"error": "请提供 amount_cents（整数分）", "code": "EXPENSE_AMOUNT_REQUIRED"})
		return
	}
	if cents <= 0 || cents > maxMoneyCents {
		c.JSON(http.StatusBadRequest, gin.H{"error": "消费金额必须大于 0 且不超过合理上限", "code": "EXPENSE_AMOUNT_INVALID"})
		return
	}
	if req.Category == "" {
		req.Category = "其他"
	}
	currency, ok := normalizeMoneyCurrency(req.Currency)
	if !ok {
		c.JSON(http.StatusBadRequest, gin.H{"error": "不支持该币种", "code": "UNSUPPORTED_CURRENCY"})
		return
	}

	exp := models.ExpenseRecord{
		ID:          uuid.New().String(),
		UserID:      req.UserID,
		TripID:      req.TripID,
		Category:    req.Category,
		AmountCents: cents,
		Currency:    currency,
		Note:        req.Note,
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

	currency, okCurrency := normalizeMoneyCurrency(req.Currency)
	if !okCurrency {
		c.JSON(http.StatusBadRequest, gin.H{"error": "不支持该币种", "code": "UNSUPPORTED_CURRENCY"})
		return
	}
	if strings.TrimSpace(req.Currency) == "" {
		currency = receipt.Currency
	}

	exp := models.ExpenseRecord{
		ID:          uuid.New().String(),
		UserID:      req.UserID,
		TripID:      req.TripID,
		Category:    receipt.Category,
		AmountCents: receipt.AmountCents,
		Currency:    currency,
		Note:        "拍照记账（OCR 自动识别）",
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
			"highest_category": topCategory(summary.ByCategoryCents),
			"advice":           budgetAdvice(summary.Level),
		},
	})
}

// topCategory 返回消费最高分类（金额为整数分）。
func topCategory(byCategory map[string]int64) string {
	top := "其他"
	max := int64(-1)
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
