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

func TestSummarizeBudgetAccuracy(t *testing.T) {
	expenses := []models.ExpenseRecord{
		{Category: "餐饮", Amount: 100, Currency: "CNY"},
		{Category: "交通", Amount: 10, Currency: "USD"},  // 10*7.2 = 72 CNY
		{Category: "住宿", Amount: 200, Currency: "CNY"},
		{Category: "门票", Amount: 0.05, Currency: "CNY"},
	}
	// 已消费 = 100 + 72 + 200 + 0.05 = 372.05
	s := SummarizeBudget(500, "CNY", expenses)
	if s.Spent != 372.05 {
		t.Fatalf("已消费应精确为 372.05，got %v", s.Spent)
	}
	if s.Remaining != 127.95 {
		t.Fatalf("剩余应精确为 127.95，got %v", s.Remaining)
	}
	if s.ExpenseCount != 4 {
		t.Fatalf("消费笔数应为 4，got %d", s.ExpenseCount)
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
		{Category: "餐饮", Amount: 50, Currency: "CNY"},
		{Category: "交通", Amount: 50, Currency: "CNY"},
	}
	s := SummarizeBudget(200, "CNY", expenses)
	if s.CategoryPct["餐饮"] != 50 || s.CategoryPct["交通"] != 50 {
		t.Fatalf("分类占比应为各 50%%，got %v", s.CategoryPct)
	}
}