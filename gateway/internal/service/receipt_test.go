package service

import "testing"

func TestExtractAmountFromTotalLine(t *testing.T) {
	text := "川西坝子火锅\n毛肚 38.00\n锅底 58.00\n合计：128.50元"
	amount, ok := ExtractAmount(text)
	if !ok || amount != 128.50 {
		t.Fatalf("应从合计行提取 128.50，got %v (ok=%v)", amount, ok)
	}
}

func TestExtractAmountFromCurrencySymbol(t *testing.T) {
	text := "停车费 ¥15"
	if amount, ok := ExtractAmount(text); !ok || amount != 15 {
		t.Fatalf("应从货币符号提取 15，got %v (ok=%v)", amount, ok)
	}
}

func TestExtractAmountWithThousandsSeparator(t *testing.T) {
	text := "住宿发票\nTotal: 1,280.00"
	if amount, ok := ExtractAmount(text); !ok || amount != 1280.00 {
		t.Fatalf("应解析千分位 1280，got %v (ok=%v)", amount, ok)
	}
}

func TestExtractAmountDecimalComma(t *testing.T) {
	text := "餐厅小票 总计 128,50"
	if amount, ok := ExtractAmount(text); !ok || amount != 128.50 {
		t.Fatalf("应解析小数逗号 128.50，got %v (ok=%v)", amount, ok)
	}
}

func TestExtractAmountNone(t *testing.T) {
	if _, ok := ExtractAmount("谢谢惠顾，欢迎再次光临"); ok {
		t.Fatal("无金额文本不应解析成功")
	}
}

func TestCategorizeReceipt(t *testing.T) {
	cases := map[string]string{
		"川西坝子火锅 合计 128":   "餐饮",
		"滴滴打车 15":          "交通",
		"如家酒店房费 260":       "住宿",
		"故宫博物院门票 60":      "门票",
		"永辉超市购物 98":        "购物",
		"合理消费 50":          "其他",
	}
	for text, want := range cases {
		if got := CategorizeReceipt(text); got != want {
			t.Fatalf("CategorizeReceipt(%q)=%q, want %q", text, got, want)
		}
	}
}

func TestParseReceipt(t *testing.T) {
	r, ok := ParseReceipt("川西坝子火锅\n合计：128.50元")
	if !ok || r.Amount != 128.50 || r.Category != "餐饮" || r.Currency != "CNY" {
		t.Fatalf("ParseReceipt 结果异常: %+v ok=%v", r, ok)
	}
}