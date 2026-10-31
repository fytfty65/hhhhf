package service

import (
	"testing"

	"gateway/internal/models"
)

func TestConvertCurrency(t *testing.T) {
	cases := []struct {
		amount   float64
		from, to string
		want     float64
	}{
		{100, "USD", "CNY", 720},
		{720, "CNY", "USD", 100},
		{100, "CNY", "CNY", 100},
		{1, "JPY", "CNY", 0.05},
		{50, "USD", "EUR", 46.15}, // 50*7.2/7.8 = 46.15
	}
	for _, c := range cases {
		got := Convert(c.amount, c.from, c.to)
		if got != c.want {
			t.Fatalf("Convert(%v,%q,%q)=%v, want %v", c.amount, c.from, c.to, got, c.want)
		}
	}
}

func TestConvertUnknownCurrencyFallback(t *testing.T) {
	if got := Convert(100, "XYZ", "CNY"); got != 100 {
		t.Fatalf("未知币种应原样返回，got %v", got)
	}
	if got := Convert(100, "", "CNY"); got != 100 {
		t.Fatalf("空币种应回退 CNY 原样处理，got %v", got)
	}
}

func TestSummarizeBudgetAccuracyUsesIntegerCents(t *testing.T) {
	expenses := []models.ExpenseRecord{
		{Category: "餐饮", AmountCents: 10000, Currency: "CNY"},
		{Category: "交通", AmountCents: 1000, Currency: "USD"}, // 10 USD = 72 CNY
		{Category: "住宿", AmountCents: 20000, Currency: "CNY"},
		{Category: "门票", AmountCents: 5, Currency: "CNY"},
	}
	// 已消费 = 10000 + 7200 + 20000 + 5 = 37205 分
	s := SummarizeBudget(50000, "CNY", expenses)
	if s.SpentCents != 37205 {
		t.Fatalf("已消费应精确为 37205 分，got %d", s.SpentCents)
	}
	if s.RemainingCents != 12795 {
		t.Fatalf("剩余应精确为 12795 分，got %d", s.RemainingCents)
	}
	if s.ExpenseCount != 4 {
		t.Fatalf("消费笔数应为 4，got %d", s.ExpenseCount)
	}
	if s.Precision != 2 || s.Currency != "CNY" {
		t.Fatalf("响应应明确币种与精度，got %+v", s)
	}
}

func TestConvertCents(t *testing.T) {
	if got := ConvertCents(10000, "USD", "CNY"); got != 72000 {
		t.Fatalf("100.00 USD 应为 72000 分 CNY，got %d", got)
	}
	if got := ConvertCents(72000, "CNY", "USD"); got != 10000 {
		t.Fatalf("720.00 CNY 应为 10000 分 USD，got %d", got)
	}
	if got := ConvertCents(5, "CNY", "CNY"); got != 5 {
		t.Fatalf("同币种不应改动，got %d", got)
	}
}

func TestOverrunLevel(t *testing.T) {
	if OverrunLevel(0.5) != "safe" {
		t.Fatal("50% 应为 safe")
	}
	if OverrunLevel(0.9) != "warning" {
		t.Fatal("90% 应为 warning")
	}
	if OverrunLevel(1.2) != "over" {
		t.Fatal("120% 应为 over")
	}
}

func TestCategoryBreakdownPercentage(t *testing.T) {
	expenses := []models.ExpenseRecord{
		{Category: "餐饮", AmountCents: 5000, Currency: "CNY"},
		{Category: "交通", AmountCents: 5000, Currency: "CNY"},
	}
	s := SummarizeBudget(20000, "CNY", expenses)
	if s.CategoryPct["餐饮"] != 50 || s.CategoryPct["交通"] != 50 {
		t.Fatalf("分类占比应为各 50%%，got %v", s.CategoryPct)
	}
}