package service

import "math"

// HotScore 社区「热门排序」热度分：点赞/评论/收藏加权互动量经时间衰减（牛顿冷却）。
// likes/comment/favorites 为互动计数，ageHours 为帖子发布至今的小时数。
// 权重：收藏 3 > 评论 2 > 点赞 1；半衰期 48 小时——越新、互动越高的帖子分越高。
func HotScore(likes, comments, favorites int, ageHours float64) float64 {
	if ageHours < 0 {
		ageHours = 0
	}
	interaction := float64(likes*1 + comments*2 + favorites*3)
	decay := math.Pow(0.5, ageHours/48.0)
	return interaction * decay
}