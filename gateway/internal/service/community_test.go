package service

import "testing"

func TestHotScoreWeighting(t *testing.T) {
	// 相同时间下：收藏 > 评论 > 点赞
	base := 0.0
	if HotScore(1, 0, 0, base) != 1 {
		t.Fatalf("1 点赞热度应为 1，got %v", HotScore(1, 0, 0, base))
	}
	if HotScore(0, 1, 0, base) != 2 {
		t.Fatalf("1 评论热度应为 2，got %v", HotScore(0, 1, 0, base))
	}
	if HotScore(0, 0, 1, base) != 3 {
		t.Fatalf("1 收藏热度应为 3，got %v", HotScore(0, 0, 1, base))
	}
}

func TestHotScoreTimeDecay(t *testing.T) {
	fresh := HotScore(1, 1, 1, 0)     // 6
	half := HotScore(1, 1, 1, 48)     // 3（半衰期）
	if fresh != 6 || half != 3 {
		t.Fatalf("热度衰减异常 fresh=%v half=%v", fresh, half)
	}
	if half >= fresh {
		t.Fatal("旧帖热度应低于新帖")
	}
}

func TestHotScoreNegativeAgeClamped(t *testing.T) {
	if got := HotScore(1, 1, 1, -5); got != 6 {
		t.Fatalf("负年龄应按 0 处理，got %v", got)
	}
}