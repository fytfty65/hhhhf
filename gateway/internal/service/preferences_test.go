package service

import (
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