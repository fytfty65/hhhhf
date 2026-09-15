package service

import (
	"math"
	"sort"
	"strings"
)

// 模块 7 · 省数据动态化纯函数层：
// 将静态省数据中的候选景点，依据「当前季节相关度 + 埋点互动热度」动态重排，
// 使目的地灵感图谱随季节与用户真实行为持续进化，而非写死排序。

// 季节常量
const (
	SeasonSpring = "spring" // 春 3-5
	SeasonSummer = "summer" // 夏 6-8
	SeasonAutumn = "autumn" // 秋 9-11
	SeasonWinter = "winter" // 冬 12-2
)

// SeasonOf 依据公历月份返回所属季节（天文/气候四季节划分）。
func SeasonOf(month int) string {
	switch {
	case month >= 3 && month <= 5:
		return SeasonSpring
	case month >= 6 && month <= 8:
		return SeasonSummer
	case month >= 9 && month <= 11:
		return SeasonAutumn
	default:
		return SeasonWinter
	}
}

// 各季节对应关键词，用于计算景点与季节的相关度。
var seasonKeywords = map[string][]string{
	SeasonSpring: {"赏花", "樱", "桃", "油菜", "郁金香", "踏青", "花海", "春"},
	SeasonSummer: {"避暑", "海滨", "沙滩", "漂流", "瀑布", "海岛", "水上", "荷花", "草原", "夏"},
	SeasonAutumn: {"赏枫", "红叶", "晒秋", "银杏", "胡杨", "秋", "登高", "满山红"},
	SeasonWinter: {"冰雪", "冰雕", "雾凇", "温泉", "滑雪", "冬捕", "灯会", "雪", "冬"},
}

// SeasonRelevance 计算景点与给定季节的相关度（0~1）。
// 名称/描述/标签命中季节关键词越多，分值越高；无任何季节特征时给中性偏低分，
// 避免过度惩罚四季皆宜的核心地标。
func SeasonRelevance(name, desc string, tags []string, season string) float64 {
	kws := seasonKeywords[season]
	if len(kws) == 0 {
		return 0.5
	}
	text := strings.ToLower(name + " " + desc + " " + strings.Join(tags, " "))
	hits := 0
	for _, kw := range kws {
		if strings.Contains(text, strings.ToLower(kw)) {
			hits++
		}
	}
	if hits == 0 {
		return 0.3
	}
	return math.Min(1.0, 0.5+0.1667*float64(hits))
}

// InteractionWeight 埋点互动热度权重（0~1，指数饱和）。
// 点击与正反馈聚合后经 1-exp(-x/10) 饱和，保证权重自然有界、批次无关，
// 少量互动提供温和加权，海量互动趋近 1。权重倾向：一次正反馈 ≈ 1.5 次点击。
func InteractionWeight(clicks, likes int64) float64 {
	raw := float64(clicks*2 + likes*3)
	return 1 - math.Exp(-raw/10.0)
}

// Hotspot 参与排名的候选景点（算法层不依赖数据库模型，便于单测）。
type Hotspot struct {
	Name string
	Desc string
	Tags []string
}

// RankedHotspot 综合排序结果（附带各项分数，便于前端解释「为什么排在前」）。
type RankedHotspot struct {
	Name        string
	SeasonMatch float64
	Weight      float64 // 归一化后的热度权重（0~1，批次内相对值）
	Score       float64 // 综合分 = 0.6*季节 + 0.4*热度
	Clicks      int64
	Likes       int64
}

// RankHotspots 综合「季节相关度 + 埋点热度权重」对候选景点排序。
// 综合分 = 0.6*季节相关度 + 0.4*热度权重（两者均在 0~1），
// 排序稳定，得分相同时热度高者优先。
func RankHotspots(hotspots []Hotspot, clicks, likes map[string]int64, season string) []RankedHotspot {
	out := make([]RankedHotspot, 0, len(hotspots))
	for _, h := range hotspots {
		sm := SeasonRelevance(h.Name, h.Desc, h.Tags, season)
		w := InteractionWeight(clicks[h.Name], likes[h.Name])
		out = append(out, RankedHotspot{
			Name:        h.Name,
			SeasonMatch: round2(sm),
			Weight:      round2(w),
			Score:       round2(0.6*sm + 0.4*w),
			Clicks:      clicks[h.Name],
			Likes:       likes[h.Name],
		})
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Score != out[j].Score {
			return out[i].Score > out[j].Score
		}
		return out[i].Weight > out[j].Weight
	})
	return out
}