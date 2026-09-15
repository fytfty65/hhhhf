package service

import (
	"os"
	"testing"
)

func TestAssignABVariantStable(t *testing.T) {
	// 同一用户恒定分组，保证 A/B 实验长期可观测
	a := AssignABVariant("user-123")
	b := AssignABVariant("user-123")
	if a != b {
		t.Fatalf("同一用户分组应恒定，got %q vs %q", a, b)
	}
	if a != "control" && a != "treatment" {
		t.Fatalf("分组只能是 control/treatment，got %q", a)
	}
}

func TestDimOfClassify(t *testing.T) {
	cases := map[string][]string{
		"武汉长江大桥": {},
		"龙门石窟":     {"culture"},
		"蜀南竹海":     {"nature"},
		"长沙小吃街":   {"food"},
	}
	for target, want := range cases {
		got := dimOf(target)
		if len(got) != len(want) {
			t.Fatalf("dimOf(%q) = %v, 期望 %v", target, got, want)
		}
		for i := range want {
			if got[i] != want[i] {
				t.Fatalf("dimOf(%q) = %v, 期望 %v", target, got, want)
			}
		}
	}
}

func hasTag(tags []string, want string) bool {
	for _, t := range tags {
		if t == want {
			return true
		}
	}
	return false
}

func TestFineTagsOf(t *testing.T) {
	cases := map[string][]string{
		"龙门石窟":  {"宗教古迹"},
		"三亚海滩":  {"湖海水系"},
		"长沙小吃街": {"小吃夜市"},
		"成都火锅店": {"火锅串串"},
		"故宫博物院": {"博物馆"},
		// —— 白名单召回：收紧后遗漏的高频真实地名词仍应正确归类 ——
		"洱海":   {"湖海水系"},
		"黄河":   {"湖海水系"},
		"珠江":   {"湖海水系"},
		"海南岛":  {"湖海水系"},
		"蜀南竹海": {"园林花木"},
		"白云观":  {"宗教古迹"},
	}
	for target, want := range cases {
		got := fineTagsOf(target)
		for _, w := range want {
			if !hasTag(got, w) {
				t.Fatalf("fineTagsOf(%q) = %v, 期望包含 %q", target, got, w)
			}
		}
	}
}

func TestFineTagsNoFalsePositive(t *testing.T) {
	// 单字关键词收紧后：跨类别词不应再误命中自然水系 / 宗教古迹等维度
	cases := map[string]string{
		"海底捞":      "湖海水系",
		"观景台":      "宗教古迹",
		"青岛啤酒博物馆": "湖海水系",
	}
	for target, notWant := range cases {
		if got := fineTagsOf(target); hasTag(got, notWant) {
			t.Fatalf("fineTagsOf(%q) 不应命中 %q，got %v", target, notWant, got)
		}
	}
}

func TestFineCuisinePrefs(t *testing.T) {
	// 细标签应落入 CuisinePrefs：火锅类反馈 -> 命中「火锅串串」细分标签
	p := BuildProfile("user-f", []FeedbackSample{
		{Target: "成都老火锅", Score: 1, Reason: "好吃"},
	})
	if !hasTag(p.CuisinePrefs, "火锅串串") {
		t.Fatalf("CuisinePrefs 应包含火锅串串细分标签，got %v", p.CuisinePrefs)
	}
	// 文化类正向反馈也应产出细分标签（宗教古迹），而非仅粗维度
	p2 := BuildProfile("user-c", []FeedbackSample{
		{Target: "龙门石窟", Score: 1, Reason: "历史文化"},
	})
	if !hasTag(p2.LikedTags, "宗教古迹") {
		t.Fatalf("LikedTags 应包含宗教古迹细分标签，got %v", p2.LikedTags)
	}
}

func TestBuildProfileAggregation(t *testing.T) {
	samples := []FeedbackSample{
		{Target: "龙门石窟", Score: 1, Reason: "历史文化深厚"},
		{Target: "白马寺", Score: 1, Reason: "静谧"},
		{Target: "洛阳老街", Score: 1, Reason: "性价比高"},
		{Target: "蜀南竹海", Score: -1, Reason: "太远"},
	}
	p := BuildProfile("user-9", samples)

	if p.FeedbackCount != 4 {
		t.Fatalf("FeedbackCount 应为 4，got %d", p.FeedbackCount)
	}
	// 三个文化正向 + 老街（culture）=> 文化权重应最高
	if p.CultureRatio <= p.NatureRatio && p.CultureRatio <= p.FoodRatio {
		t.Fatalf("文化维度应占主导，ratios=%v/%v/%v", p.NatureRatio, p.CultureRatio, p.FoodRatio)
	}
	// 三条「性价比」相关正向反馈 => 推断为低预算倾向
	if p.BudgetTendency != "low" {
		t.Fatalf("应推断为低预算 habit，got %q", p.BudgetTendency)
	}
}

func TestBuildProfileEmptyFallback(t *testing.T) {
	p := BuildProfile("user-x", nil)
	if len(p.LikedTags) == 0 {
		t.Fatal("空样本应回退为通用画像，LikedTags 不应为空")
	}
	if p.NatureRatio+p.CultureRatio+p.FoodRatio < 0.99 {
		t.Fatalf("三大维度权重应归一化为 1，got %v", p.NatureRatio+p.CultureRatio+p.FoodRatio)
	}
	if p.BudgetTendency != "mid" {
		t.Fatalf("空样本默认中等预算，got %q", p.BudgetTendency)
	}
}

func TestProfileToPromptInjection(t *testing.T) {
	p := BuildProfile("user-1", []FeedbackSample{{Target: "成都熊猫基地", Score: 1, Reason: "喜欢"}})
	s := ProfileToPrompt(p)
	if s == "" {
		t.Fatal("Prompt 注入内容不应为空")
	}
	if len(s) < 10 {
		t.Fatalf("Prompt 注入内容过短，got %q", s)
	}
}

func TestBuildProfileHighBudgetTendency(t *testing.T) {
	// 正向反馈中出现「五星/豪华」品质诉求时应推断为高预算，防止 high 分支成为死代码（回归防护）
	p := BuildProfile("user-lux", []FeedbackSample{
		{Target: "五星酒店", Score: 1, Reason: "五星酒店体验很好"},
	})
	if p.BudgetTendency != "high" {
		t.Fatalf("应推断为高预算 habit，got %q", p.BudgetTendency)
	}
}

func TestBuildProfileHighBudgetOverridesLow(t *testing.T) {
	// 同时存在省钱与品质信号时，应以当前更强的高消费诉求优先
	p := BuildProfile("user-mix", []FeedbackSample{
		{Target: "青年旅舍", Score: 1, Reason: "性价比高"},
		{Target: "五星酒店", Score: 1, Reason: "豪华体验"},
	})
	if p.BudgetTendency != "high" {
		t.Fatalf("品质诉求应优先于省钱信号，got %q", p.BudgetTendency)
	}
}

func TestExpenseCategoryTarget(t *testing.T) {
	if got := expenseCategoryTarget("餐饮"); got != "餐厅" {
		t.Fatalf("餐饮 应映射到 餐厅（food 维度），got %q", got)
	}
	if got := expenseCategoryTarget("住宿"); got != "" {
		t.Fatalf("住宿 不应映射三大预维度，got %q", got)
	}
}

func TestAccommodationAmountTendency(t *testing.T) {
	cases := []struct {
		name    string
		amounts []float64
		want    string
	}{
		{"空样本无信号", nil, ""},
		{"全高档", []float64{1200, 1500}, "high"},
		{"全经济", []float64{200, 150}, "low"},
		{"高档多", []float64{1200, 1200, 200}, "high"},
		{"经济多", []float64{1200, 200, 200}, "low"},
		{"打平", []float64{1200, 200}, ""},
		{"中间档无信号", []float64{500, 600}, ""},
		{"边界高档", []float64{1000}, "high"},
		{"边界经济", []float64{300}, "low"},
	}
	for _, c := range cases {
		if got := budgetLevelFromAmounts(c.amounts); got != c.want {
			t.Fatalf("%s: budgetLevelFromAmounts(%v) = %q, 期望 %q", c.name, c.amounts, got, c.want)
		}
	}
}

func TestAccommodationAmountTendencyCrossCurrency(t *testing.T) {
	// 跨币种住宿金额经 Convert 换算 CNY 后应正确命中档位阈值
	if got := budgetLevelFromAmounts([]float64{Convert(200, "USD", "CNY")}); got != "high" {
		t.Fatalf("200 USD 住宿（约 1440 CNY）应判 high，got %q", got)
	}
	if got := budgetLevelFromAmounts([]float64{Convert(30, "USD", "CNY")}); got != "low" {
		t.Fatalf("30 USD 住宿（约 216 CNY）应判 low，got %q", got)
	}
}

func TestBudgetSignalCategory(t *testing.T) {
	for _, cat := range []string{"住宿", "交通", "门票"} {
		if !isBudgetSignalCategory(cat) {
			t.Errorf("硬性支出品类 %q 应参与推断", cat)
		}
	}
	for _, cat := range []string{"餐饮", "美食", "购物", "其他", ""} {
		if isBudgetSignalCategory(cat) {
			t.Errorf("非硬性支出品类 %q 不应参与推断", cat)
		}
	}
}

func TestParseDaysFromTitle(t *testing.T) {
	cases := map[string]int{
		"洛阳 3 日游":    3,
		"3天2晚":       3,
		"杭州 5 日自由行": 5,
		"无天数标题":      0,
		"":            0,
	}
	for title, want := range cases {
		if got := parseDaysFromTitle(title); got != want {
			t.Errorf("parseDaysFromTitle(%q) = %d, 期望 %d", title, got, want)
		}
	}
}

func TestBudgetLevelDynamicQuantile(t *testing.T) {
	// 5 笔硬性支出 [250,250,1100,1200,2000]：
	// 固定阈值会判 3 高档(1100/1200/2000)、2 经济(250) → high；
	// 动态分位数把高档线抬到 p75=1200，1100 不再算高档，使高档票降为 2、经济票 2 → 打平（保持 mid）。
	if got := budgetLevelFromAmounts([]float64{250, 250, 1100, 1200, 2000}); got != "" {
		t.Fatalf("动态分位数下应打平返回空，got %q", got)
	}
	// 3 笔均高档、无低档噪声时仍判 high
	if got := budgetLevelFromAmounts([]float64{1100, 1200, 2000}); got != "high" {
		t.Fatalf("三笔高档应判 high，got %q", got)
	}
}

func TestEnvConfigOverridesDefaultThresholds(t *testing.T) {
	const (
		highKey = "HIGH_END_HOTEL_THRESHOLD_CNY"
		lowKey  = "BUDGET_HOTEL_THRESHOLD_CNY"
	)
	origHigh, origLow := os.Getenv(highKey), os.Getenv(lowKey)
	defer func() {
		// 恢复环境变量与阈值全局变量，避免污染后续用例
		if origHigh == "" {
			os.Unsetenv(highKey)
		} else {
			os.Setenv(highKey, origHigh)
		}
		if origLow == "" {
			os.Unsetenv(lowKey)
		} else {
			os.Setenv(lowKey, origLow)
		}
		highEndHotelCnyThreshold = getEnvAsFloat(highKey, 1000.0)
		budgetHotelCnyThreshold = getEnvAsFloat(lowKey, 300.0)
	}()

	os.Setenv(highKey, "1500")
	os.Setenv(lowKey, "500")
	highEndHotelCnyThreshold = getEnvAsFloat(highKey, 1000.0)
	budgetHotelCnyThreshold = getEnvAsFloat(lowKey, 300.0)

	if got := budgetLevelFromAmounts([]float64{1499}); got != "" {
		t.Errorf("1499 < 1500 应无信号，got %q", got)
	}
	if got := budgetLevelFromAmounts([]float64{1500}); got != "high" {
		t.Errorf("1500 == 1500 应判 high，got %q", got)
	}
	if got := budgetLevelFromAmounts([]float64{500}); got != "low" {
		t.Errorf("500 == 500 应判 low，got %q", got)
	}
	if got := budgetLevelFromAmounts([]float64{501}); got != "" {
		t.Errorf("501 > 500 应无信号，got %q", got)
	}
}

func TestGetEnvAsFloatFallback(t *testing.T) {
	const fakeKey = "OMNIROUTE_FAKE_THRESHOLD_UNSET"
	os.Unsetenv(fakeKey)
	if got := getEnvAsFloat(fakeKey, 888.0); got != 888.0 {
		t.Errorf("缺失环境变量应回退默认值，got %v", got)
	}
	os.Setenv(fakeKey, "not-a-number")
	if got := getEnvAsFloat(fakeKey, 888.0); got != 888.0 {
		t.Errorf("非法数值应回退默认值，got %v", got)
	}
	os.Setenv(fakeKey, "-5")
	if got := getEnvAsFloat(fakeKey, 888.0); got != 888.0 {
		t.Errorf("负值应回退默认值，got %v", got)
	}
	os.Unsetenv(fakeKey)
}

func TestRound2(t *testing.T) {
	if got := round2(1.005); got != 1.01 && got != 1.0 { // 浮点边界不严格断言，仅确保在合理范围
		t.Fatalf("round2(1.005)=%v 超出预期", got)
	}
	if got := round2(2.345); got != 2.35 && got != 2.34 {
		t.Fatalf("round2(2.345)=%v 超出预期", got)
	}
	if got := round2(-1.005); got > 0 {
		t.Fatalf("负数舍入异常，got %v", got)
	}
}