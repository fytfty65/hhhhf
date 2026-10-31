package service

import (
	"regexp"
	"strconv"
	"strings"
)

// Receipt 从 OCR 文本中解析出的小票结构化信息（纯函数层不依赖数据库模型）。
// AmountCents 使用整数分，避免 128.50 在二进制浮点里变成 128.499999……
type Receipt struct {
	AmountCents int64
	Currency    string
	Category    string
}

// 合计/总计行金额匹配：优先锁定「合计、总计、应收、实收、应付、总额、总价」等关键词所在行的金额（英文关键词大小写不敏感）
var moneyLineRe = regexp.MustCompile(`(?i)(?:合计金额|合计|总计|应收|实收|应付|总额|总价|消费金额|TOTAL|AMOUNT)\s*[:：]?\s*[¥￥]?\s*(\d[\d,]*(?:[.,]\d{1,2})?)`)

// 货币符号金额匹配：兜底匹配带 ¥/￥ 符号的金额
var plainMoneyRe = regexp.MustCompile(`[¥￥]\s*(\d[\d,]*(?:[.,]\d{1,2})?)`)

// normalizeNumeric 将 "1,280.00" / "128.50" / "128,50" 归一化为可解析数字串。
func normalizeNumeric(s string) (string, bool) {
	s = strings.TrimSpace(s)
	if s == "" {
		return "", false
	}
	hasComma := strings.Contains(s, ",")
	hasDot := strings.Contains(s, ".")
	switch {
	case hasComma && hasDot:
		// "1,280.00" → 逗号视为千分位
		s = strings.ReplaceAll(s, ",", "")
	case hasComma && !hasDot:
		// "128,50" → 尾随 1~2 位视为小数逗号；"1,280" → 逗号视为千分位
		idx := strings.LastIndex(s, ",")
		after := s[idx+1:]
		if len(after) == 1 || len(after) == 2 {
			s = s[:idx] + "." + after
		} else {
			s = strings.ReplaceAll(s, ",", "")
		}
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return "", false
	}
	return strconv.FormatFloat(f, 'f', 2, 64), true
}

// parseAmountString 从金额字符串解析出浮点金额（仅用于兼容旧调用）。
func parseAmountString(s string) (float64, bool) {
	norm, ok := normalizeNumeric(s)
	if !ok {
		return 0, false
	}
	f, err := strconv.ParseFloat(norm, 64)
	if err != nil {
		return 0, false
	}
	return f, true
}

// parseAmountCents 将 "1,280.00" / "128.50" 精确解析为整数分，不做浮点运算。
func parseAmountCents(s string) (int64, bool) {
	norm, ok := normalizeNumeric(s)
	if !ok {
		return 0, false
	}
	parts := strings.SplitN(norm, ".", 2)
	whole, err := strconv.ParseInt(parts[0], 10, 64)
	if err != nil || whole < 0 {
		return 0, false
	}
	frac := int64(0)
	if len(parts) == 2 {
		digits := parts[1] + "00"
		frac, err = strconv.ParseInt(digits[:2], 10, 64)
		if err != nil {
			return 0, false
		}
	}
	return whole*100 + frac, true
}

// ExtractAmount 从小票 OCR 文本提取首个可靠金额（优先合计行、其次货币符号行）。
func ExtractAmount(text string) (float64, bool) {
	text = strings.ReplaceAll(text, "\u00a0", " ") // 全角空格归一
	lines := strings.Split(text, "\n")

	// 1) 合计/总计关键词所在行
	for _, line := range lines {
		if m := moneyLineRe.FindStringSubmatch(line); m != nil {
			if f, ok := parseAmountString(m[1]); ok {
				return f, true
			}
		}
	}

	// 2) 带货币符号的金额
	for _, m := range plainMoneyRe.FindAllStringSubmatch(text, -1) {
		if f, ok := parseAmountString(m[1]); ok {
			return f, true
		}
	}

	return 0, false
}

// ExtractAmountCents 提取整数分金额，供存储层直接落库。
func ExtractAmountCents(text string) (int64, bool) {
	text = strings.ReplaceAll(text, "\u00a0", " ")
	lines := strings.Split(text, "\n")
	for _, line := range lines {
		if m := moneyLineRe.FindStringSubmatch(line); m != nil {
			if cents, ok := parseAmountCents(m[1]); ok {
				return cents, true
			}
		}
	}
	for _, m := range plainMoneyRe.FindAllStringSubmatch(text, -1) {
		if cents, ok := parseAmountCents(m[1]); ok {
			return cents, true
		}
	}
	return 0, false
}

// CategorizeReceipt 根据 OCR 文本关键词推断消费分类。
func CategorizeReceipt(text string) string {
	type rule struct {
		cat      string
		keywords []string
	}
	rules := []rule{
		{"餐饮", []string{"餐", "火锅", "面", "饭", "咖啡", "奶茶", "小吃", "煎", "烤", "酒吧", "汤", "菜", "粉", "串"}},
		{"交通", []string{"车", "出租", "滴滴", "高铁", "火车", "机场", "地铁", "公交", "加油", "停车", "的士", "打车", "航空", "客运"}},
		{"住宿", []string{"酒店", "民宿", "宾馆", "客栈", "房费", "住宿", "入住", "客房"}},
		{"门票", []string{"门票", "景区", "博物馆", "展览", "演出", "电影", "入场", "乐园", "观景"}},
		{"购物", []string{"超市", "商场", "购物", "便利店", "零售", "购买", "旗舰店", "百货"}},
	}
	for _, r := range rules {
		for _, kw := range r.keywords {
			if strings.Contains(text, kw) {
				return r.cat
			}
		}
	}
	return "其他"
}

// ParseReceipt 将 OCR 文本解析为结构化消费信息；无法识别金额时返回 found=false。
func ParseReceipt(text string) (Receipt, bool) {
	amountCents, ok := ExtractAmountCents(text)
	if !ok {
		return Receipt{}, false
	}
	return Receipt{
		AmountCents: amountCents,
		Currency:    "CNY",
		Category:    CategorizeReceipt(text),
	}, true
}