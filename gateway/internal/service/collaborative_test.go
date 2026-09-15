package service

import (
	"math"
	"testing"
)

func TestBuildRatingMatrix(t *testing.T) {
	rows := []RatingRow{
		{UserID: "u1", ItemID: "故宫", Score: 1},
		{UserID: "u1", ItemID: "长城", Score: 1},
		{UserID: "u1", ItemID: "故宫", Score: 3}, // 覆盖前值
		{UserID: "u2", ItemID: "故宫", Score: 1},
	}
	m := BuildRatingMatrix(rows)
	if m["u1"]["故宫"] != 3 {
		t.Fatalf("重复评分应取最后一次，got %v", m["u1"]["故宫"])
	}
	if len(m["u1"]) != 2 {
		t.Fatalf("u1 应覆盖 2 个物品，got %d", len(m["u1"]))
	}
}

func TestBuildRatingMatrixSkipsEmpty(t *testing.T) {
	m := BuildRatingMatrix([]RatingRow{
		{UserID: "", ItemID: "A", Score: 1},
		{UserID: "u1", ItemID: "", Score: 1},
	})
	if len(m) != 0 {
		t.Fatalf("空字段应被忽略，got %v", m)
	}
}

func TestCosineSimilarity(t *testing.T) {
	a := map[string]float64{"x": 1, "y": 1, "z": 1}
	b := map[string]float64{"x": 1, "y": 1, "z": 1}
	if got := CosineSimilarity(a, b); math.Abs(got-1.0) > 1e-9 {
		t.Fatalf("同向量相似度应为 1，got %v", got)
	}
	// 无交集
	if got := CosineSimilarity(a, map[string]float64{"w": 1}); got != 0 {
		t.Fatalf("无共同物品相似度应为 0，got %v", got)
	}
}

func TestTopSimilarUsers(t *testing.T) {
	m := BuildRatingMatrix([]RatingRow{
		{UserID: "u1", ItemID: "A", Score: 1},
		{UserID: "u1", ItemID: "B", Score: 1},
		{UserID: "u2", ItemID: "A", Score: 1}, // 与 u1 重叠 1
		{UserID: "u3", ItemID: "C", Score: 1}, // 与 u1 无重叠
	})
	sims := TopSimilarUsers("u1", m, 5)
	if len(sims) != 1 || sims[0] != "u2" {
		t.Fatalf("最相似用户应为 u2，got %v", sims)
	}
}

func TestRecommendItems(t *testing.T) {
	m := BuildRatingMatrix([]RatingRow{
		{UserID: "u1", ItemID: "A", Score: 1},
		{UserID: "u2", ItemID: "A", Score: 1},
		{UserID: "u2", ItemID: "B", Score: 1}, // u2 额外喜欢 B
	})
	recs := RecommendItems("u1", m, 5)
	if len(recs) != 1 || recs[0] != "B" {
		t.Fatalf("应推荐 B，got %v", recs)
	}
}

func TestRecommendItemsEmpty(t *testing.T) {
	if recs := RecommendItems("unknown", map[string]map[string]float64{}, 5); recs != nil {
		t.Fatalf("未知用户应返回 nil，got %v", recs)
	}
}