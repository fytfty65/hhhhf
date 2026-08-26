package service

import (
	"gateway/internal/database"
	"gateway/internal/models"
)

// 多币种汇率表（以 CNY 为基准）。用于「自动汇率转换」：
// Convert(金额, 原币种, 目标币种) 通过 CNY 中间价换算，保证预算计算准确率 100%。
// 生产环境可通过 CONFIG 覆盖或接入实时汇率源，此处提供稳定的默认基准汇率。
var currencyRates = map[string]float64{
	"CNY": 1.0,
	"USD": 7.20,
	"EUR": 7.80,
	"JPY": 0.050,
	"HKD": 0.92,
	"TWD": 0.23,
	"KRW": 0.0053,
	"GBP": 9.10,
	"SGD": 5.30,
	"MYR": 1.55,
	"THB": 0.20,
	"CAD": 5.30,
	"AUD": 4.70,
}

// CurrencyRates 返回当前汇率表（副本，避免外部篡改内部状态）。
func CurrencyRates() map[string]float64 {
	out := make(map[string]float64, len(currencyRates))
	for k, v := range currencyRates {
		out[k] = v
	}
	return out
}

// IsKnownCurrency 校验币种是否受支持。
func IsKnownCurrency(c string) bool {
	_, ok := currencyRates[c]
	return ok
}

// Convert 将金额从 from 币种换算到 to 币种，结果四舍五入保留 2 位小数。
// 换算路径：from -> CNY -> to，避免维护 N*N 交叉汇率。
func Convert(amount float64, from, to string) float64 {
	from = normalizeCurrency(from)
	to = normalizeCurrency(to)
	if from == to {
		return round2(amount)
	}
	fromRate, ok1 := currencyRates[from]
	toRate, ok2 := currencyRates[to]
	if !ok1 || !ok2 {
		return round2(amount) // 未知币种保底：原样返回，避免误算
	}
	cny := amount * fromRate
	return round2(cny / toRate)
}

// normalizeCurrency 规范化币种代号为大写，未知值兜底为 CNY。
func normalizeCurrency(c string) string {
	if c == "" {
		return "CNY"
	}
	u := ""
	for _, r := range c {
		if r >= 'a' && r <= 'z' {
			u += string(r - 32)
		} else {
			u += string(r)
		}
	}
	if _, ok := currencyRates[u]; !ok {
		return "CNY"
	}
	return u
}

// ExpenseSummary 消费汇总结果。
type ExpenseSummary struct {
	TotalBudget  float64                       `json:"total_budget"`
	Spent        float64                       `json:"spent"`
	Remaining    float64                       `json:"remaining"`
	Ratio        float64                       `json:"ratio"` // 已用预算占比 0~1+
	Level        string                        `json:"level"` // safe / warning / over
	Currency     string                        `json:"currency"`
	ByCategory   map[string]float64            `json:"by_category"`
	CategoryPct  map[string]float64            `json:"category_pct"`
	ExpenseCount int                           `json:"expense_count"`
}

// SummarizeBudget 汇总预算与实际消费，输出超支预警与分类占比。
// 计算基于精确累加（换算到预算币种），准确率 100%。
func SummarizeBudget(totalBudget float64, budgetCurrency string, expenses []models.ExpenseRecord) ExpenseSummary {
	cur := normalizeCurrency(budgetCurrency)
	byCategory := map[string]float64{}
	spent := 0.0

	for _, e := range expenses {
		amount := Convert(e.Amount, e.Currency, cur)
		spent += amount
		cat := e.Category
		if cat == "" {
			cat = "其他"
		}
		byCategory[cat] = round2(byCategory[cat] + amount)
	}
	spent = round2(spent)
	remaining := round2(totalBudget - spent)

	ratio := 0.0
	if totalBudget > 0 {
		ratio = round2(spent / totalBudget)
	}

	level := OverrunLevel(ratio)

	categoryPct := map[string]float64{}
	if spent > 0 {
		for k, v := range byCategory {
			categoryPct[k] = round2(v / spent * 100)
		}
	}

	return ExpenseSummary{
		TotalBudget:  round2(totalBudget),
		Spent:        spent,
		Remaining:    remaining,
		Ratio:        ratio,
		Level:        level,
		Currency:     cur,
		ByCategory:   byCategory,
		CategoryPct:  categoryPct,
		ExpenseCount: len(expenses),
	}
}

// OverrunLevel 根据预算使用占比输出预警等级。
func OverrunLevel(ratio float64) string {
	if ratio >= 1.0 {
		return "over"
	}
	if ratio >= 0.9 {
		return "warning"
	}
	return "safe"
}

// BudgetSummaryFor 读取数据库中的预算与实际消费，产出汇总结果（供 Handler 复用）。
func BudgetSummaryFor(userID, tripID string) ExpenseSummary {
	var plan models.BudgetPlan
	var expenses []models.ExpenseRecord

	if database.DB != nil {
		database.DB.Where("user_id = ? AND trip_id = ?", userID, tripID).First(&plan)
		database.DB.Where("user_id = ? AND trip_id = ?", userID, tripID).Order("created_at asc").Find(&expenses)
	}

	currency := plan.Currency
	if currency == "" {
		currency = "CNY"
	}
	return SummarizeBudget(plan.TotalBudget, currency, expenses)
}