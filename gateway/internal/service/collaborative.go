package service

import (
	"math"
	"sort"
)

// RatingRow 归一化的「用户-物品」评分样本（协同过滤层不依赖数据库模型，便于单元测试）。
type RatingRow struct {
	UserID string
	ItemID string
	Score  float64
}

// BuildRatingMatrix 将评分样本聚合为 userID -> (itemID -> score) 矩阵。
// 同一用户对同一物品的多次评分取最后一次。
func BuildRatingMatrix(rows []RatingRow) map[string]map[string]float64 {
	matrix := make(map[string]map[string]float64)
	for _, r := range rows {
		if r.UserID == "" || r.ItemID == "" {
			continue
		}
		if matrix[r.UserID] == nil {
			matrix[r.UserID] = make(map[string]float64)
		}
		matrix[r.UserID][r.ItemID] = r.Score
	}
	return matrix
}

// CosineSimilarity 计算两个评分向量的余弦相似度（仅比较共同评分项）。
func CosineSimilarity(a, b map[string]float64) float64 {
	var dot, na, nb float64
	for item, ra := range a {
		rb, ok := b[item]
		if !ok {
			continue
		}
		dot += ra * rb
		na += ra * ra
		nb += rb * rb
	}
	if na == 0 || nb == 0 {
		return 0
	}
	return dot / (math.Sqrt(na) * math.Sqrt(nb))
}

// TopSimilarUsers 返回与目标用户最相似的 k 个用户（按余弦相似度降序，排除自身）。
func TopSimilarUsers(userID string, matrix map[string]map[string]float64, k int) []string {
	target := matrix[userID]
	if target == nil {
		return nil
	}

	type sim struct {
		user string
		v    float64
	}
	sims := make([]sim, 0, len(matrix))
	for u, vec := range matrix {
		if u == userID {
			continue
		}
		if s := CosineSimilarity(target, vec); s > 0 {
			sims = append(sims, sim{u, s})
		}
	}
	sort.Slice(sims, func(i, j int) bool { return sims[i].v > sims[j].v })
	if k > len(sims) {
		k = len(sims)
	}
	out := make([]string, 0, k)
	for i := 0; i < k; i++ {
		out = append(out, sims[i].user)
	}
	return out
}

// RecommendItems 基于协同过滤推荐物品：挑选「相似用户评分高、但目标用户未评分」的物品。
// 预测分 = Σ(相似度 × 相似用户对该物品评分) / Σ|相似度|，按预测分降序返回前 k 个。
func RecommendItems(userID string, matrix map[string]map[string]float64, k int) []string {
	target := matrix[userID]
	if target == nil {
		return nil
	}

	simScores := make(map[string]float64)
	for u, vec := range matrix {
		if u == userID {
			continue
		}
		if s := CosineSimilarity(target, vec); s > 0 {
			simScores[u] = s
		}
	}

	score := make(map[string]float64)
	weight := make(map[string]float64)
	for u, s := range simScores {
		for item, r := range matrix[u] {
			if _, rated := target[item]; rated {
				continue
			}
			if r <= 0 {
				continue
			}
			score[item] += s * r
			weight[item] += s
		}
	}

	type rec struct {
		item string
		v    float64
	}
	recs := make([]rec, 0, len(score))
	for item, s := range score {
		if weight[item] > 0 {
			recs = append(recs, rec{item, s / weight[item]})
		}
	}
	sort.Slice(recs, func(i, j int) bool { return recs[i].v > recs[j].v })
	if k > len(recs) {
		k = len(recs)
	}
	out := make([]string, 0, k)
	for i := 0; i < k; i++ {
		out = append(out, recs[i].item)
	}
	return out
}