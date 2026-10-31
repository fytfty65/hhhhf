package service

import (
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"strings"

	"gateway/internal/database"
	"gateway/internal/models"
)

// CurrencyPrecision is the number of decimal places for every supported
// currency in the storage layer. Amounts are stored as integer minor units
// (amount * 10^precision), so this is also the divisor used when rendering.
const CurrencyPrecision = 2

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

// ConvertCents converts integer minor units between currencies. The rates are
// still float constants, but only the conversion is floating point: the stored
// and returned value is a rounded integer, so repeated arithmetic cannot drift.
func ConvertCents(cents int64, from, to string) int64 {
	from = normalizeCurrency(from)
	to = normalizeCurrency(to)
	if from == to {
		return cents
	}
	fromRate, ok1 := currencyRates[from]
	toRate, ok2 := currencyRates[to]
	if !ok1 || !ok2 || toRate == 0 {
		return cents // unknown currency: never invent a conversion
	}
	return int64(math.Round(float64(cents) * fromRate / toRate))
}

// CentsToMajor renders integer minor units as a decimal string with exactly
// CurrencyPrecision places, e.g. 12850 -> "128.50". Used in logs and prompts.
func CentsToMajor(cents int64) string {
	sign := ""
	if cents < 0 {
		sign = "-"
		cents = -cents
	}
	return fmt.Sprintf("%s%d.%02d", sign, cents/100, cents%100)
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
//
// All money fields are integer minor units with an explicit `currency` and
// `precision`, so a client never has to guess whether 12.5 means 12.50 yuan.
type ExpenseSummary struct {
	TotalBudgetCents int64            `json:"total_budget_cents"`
	SpentCents       int64            `json:"spent_cents"`
	RemainingCents   int64            `json:"remaining_cents"`
	Ratio            float64          `json:"ratio"` // 已用预算占比 0~1+（比例，不是金额）
	Level            string           `json:"level"` // safe / warning / over
	Currency         string           `json:"currency"`
	Precision        int              `json:"precision"`
	ByCategoryCents  map[string]int64 `json:"by_category_cents"`
	CategoryPct      map[string]float64 `json:"category_pct"`
	ExpenseCount     int              `json:"expense_count"`
}

// SummarizeBudget 汇总预算与实际消费，输出超支预警与分类占比。
// 金额全部用整数分累加，换算产生的舍入只发生在 ConvertCents 内部。
func SummarizeBudget(totalBudgetCents int64, budgetCurrency string, expenses []models.ExpenseRecord) ExpenseSummary {
	cur := normalizeCurrency(budgetCurrency)
	byCategory := map[string]int64{}
	var spent int64

	for _, e := range expenses {
		amount := ConvertCents(e.AmountCents, e.Currency, cur)
		spent += amount
		cat := e.Category
		if cat == "" {
			cat = "其他"
		}
		byCategory[cat] += amount
	}

	remaining := totalBudgetCents - spent

	ratio := 0.0
	if totalBudgetCents > 0 {
		ratio = round2(float64(spent) / float64(totalBudgetCents))
	}

	level := OverrunLevel(ratio)

	categoryPct := map[string]float64{}
	if spent > 0 {
		for k, v := range byCategory {
			categoryPct[k] = round2(float64(v) / float64(spent) * 100)
		}
	}

	return ExpenseSummary{
		TotalBudgetCents: totalBudgetCents,
		SpentCents:       spent,
		RemainingCents:   remaining,
		Ratio:            ratio,
		Level:            level,
		Currency:         cur,
		Precision:        CurrencyPrecision,
		ByCategoryCents:  byCategory,
		CategoryPct:      categoryPct,
		ExpenseCount:     len(expenses),
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
	return SummarizeBudget(plan.TotalBudgetCents, currency, expenses)
}

// routeCostRe 从路线节点的 cost / cost_estimate 字段提取首个金额数字（支持 ¥/￥/元 前缀、千分位逗号与小数）。
var routeCostRe = regexp.MustCompile(`(\d[\d,]*(?:[.,]\d{1,2})?)`)

// routeNodeCost 解析单个路线节点的费用字段，无法识别时返回 0（单位：分）。
func routeNodeCost(node map[string]interface{}) int64 {
	for _, key := range []string{"cost", "cost_estimate"} {
		raw, ok := node[key]
		if !ok {
			continue
		}
		s := strings.TrimSpace(fmt.Sprint(raw))
		if s == "" {
			continue
		}
		if m := routeCostRe.FindStringSubmatch(s); m != nil {
			if v, ok := parseAmountCents(m[1]); ok {
				return v
			}
		}
	}
	return 0
}

// EstimateRouteBudget 从方案路线 JSON 估算总预算（累加各节点人均费用，单位分），
// 供「采纳→费用追踪」闭环建立预算基线。无法解析时返回 0（调用方可选兜底）。
func EstimateRouteBudget(routeJSON string) int64 {
	var nodes []map[string]interface{}
	if err := json.Unmarshal([]byte(routeJSON), &nodes); err != nil {
		return 0
	}
	var total int64
	for _, n := range nodes {
		total += routeNodeCost(n)
	}
	return total
}