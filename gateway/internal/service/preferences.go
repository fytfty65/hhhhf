package service

import (
	"hash/fnv"
	"math"
	"sort"
	"strings"
)

// FeedbackSample 归一化的反馈样本（算法层不依赖数据库模型，便于单元测试）
type FeedbackSample struct {
	Target string
	Score  int // 1 表示喜欢，-1 表示不喜欢
	Reason string
}

// Profile 用户旅行画像（个性化推荐引擎的产物）
type Profile struct {
	LikedTags      []string
	DislikedTags   []string
	CuisinePrefs   []string
	BudgetTendency string // low / mid / high
	NatureRatio    float64
	CultureRatio   float64
	FoodRatio      float64
	ABVariant      string // control / treatment
	FeedbackCount  int
}

// 自然景观维度关键词
var natureKeywords = []string{
	"山", "公园", "湖", "河", "谷", "峡", "林", "草原", "沙漠", "海", "岛", "瀑布",
	"温泉", "雪山", "冰川", "植物园", "天池", "花海", "湿地", "滩", "湾", "峰",
}

// 人文历史维度关键词
var cultureKeywords = []string{
	"博物", "寺", "庙", "宫", "塔", "古城", "老街", "遗址", "陵", "馆", "阁", "楼",
	"石窟", "文庙", "故居", "书院", "清真寺", "城墙", "祠", "钟楼", "鼓楼",
}

// 美食维度关键词
var foodKeywords = []string{
	"餐", "美食", "火锅", "烤", "面", "饭", "小吃", "抓饭", "拌面", "包子", "馕",
	"夜市", "小吃街", "咖啡", "甜品", "奶茶", "肉", "串", "汤", "饼", "餐厅", "菜馆",
}

// —— 细粒度偏好标签：在 nature/culture/food 三大粗维度下再分细标签 ——
// 用于把「自然风光/人文历史/地道美食」进一步细化为可展示、可注入 Prompt 的具体偏好，
// 例如「山岳/湖海水系/宗教古迹/火锅串串」，在不改变顶部三大比例口径的前提下提升推荐粒度。
type fineTag struct {
	tag      string
	dim      string
	keywords []string
}

var fineTags = []fineTag{
	// —— nature 自然风光细分 ——
	{"山岳", "nature", []string{"山", "峰", "岭", "雪山", "冰川", "天池", "峡", "谷", "瀑布"}},
	{"湖海水系", "nature", []string{"湖", "洱海", "北海", "什刹海", "黄河", "秦淮河", "珠江", "漓江", "钱塘江", "海滨", "海滩", "海湾", "海域", "海洋", "河流", "河畔", "河边", "河谷", "运河", "长江", "江河", "江畔", "海南岛", "涠洲岛", "海岛", "岛屿", "小岛", "群岛", "滩", "湾", "湿地", "泉"}},
	{"草原沙漠", "nature", []string{"草原", "沙漠", "戈壁", "绿洲"}},
	{"园林花木", "nature", []string{"公园", "植物园", "园林", "花园", "花海", "竹林", "竹海"}},
	// —— culture 人文历史细分 ——
	{"博物馆", "culture", []string{"博物", "科技馆", "美术馆", "陈列", "展览"}},
	{"古迹遗址", "culture", []string{"古城", "遗址", "陵", "城墙", "故城", "古都"}},
	{"宗教古迹", "culture", []string{"寺", "庙", "石窟", "清真寺", "祠", "道观", "宫观", "寺观", "白云观", "青羊观", "教堂", "塔"}},
	{"名人故居", "culture", []string{"故居", "书院", "文庙", "纪念馆", "名人"}},
	{"老街坊", "culture", []string{"老街", "古镇", "街区", "胡同", "里弄"}},
	// —— food 美食细分 ——
	{"火锅串串", "food", []string{"火锅", "串串", "关东煮"}},
	{"烧烤", "food", []string{"烧烤", "串烧", "烤"}},
	{"面食", "food", []string{"面", "拌面", "拉面", "馕", "饼", "包子", "饺子", "饭"}},
	{"地方菜", "food", []string{"餐厅", "菜馆", "饭馆", "川菜", "粤菜", "湘菜", "本帮菜"}},
	{"小吃夜市", "food", []string{"小吃", "夜市", "小吃街", "美食街", "摊"}},
	{"甜饮咖啡", "food", []string{"甜品", "奶茶", "咖啡", "茶饮", "冰淇淋"}},
}

// fineTagDims 细标签 -> 所属粗维度（由 fineTags 派生，供 CuisinePrefs 提取等反查）。
var fineTagDims = buildFineTagDims()

func buildFineTagDims() map[string]string {
	m := map[string]string{}
	for _, ft := range fineTags {
		m[ft.tag] = ft.dim
	}
	return m
}

// fineTagsOf 返回地点名命中的细粒度偏好标签（去重、排序）。
func fineTagsOf(target string) []string {
	set := map[string]bool{}
	for _, ft := range fineTags {
		for _, kw := range ft.keywords {
			if strings.Contains(target, kw) {
				set[ft.tag] = true
				break
			}
		}
	}
	out := make([]string, 0, len(set))
	for t := range set {
		out = append(out, t)
	}
	sort.Strings(out)
	return out
}

// dimOf 返回地点名称所属的偏好维度（nature/culture/food，可能多标签）
func dimOf(target string) []string {
	dimSet := map[string]bool{}
	for _, kw := range natureKeywords {
		if strings.Contains(target, kw) {
			dimSet["nature"] = true
			break
		}
	}
	for _, kw := range cultureKeywords {
		if strings.Contains(target, kw) {
			dimSet["culture"] = true
			break
		}
	}
	for _, kw := range foodKeywords {
		if strings.Contains(target, kw) {
			dimSet["food"] = true
			break
		}
	}
	out := make([]string, 0, len(dimSet))
	for d := range dimSet {
		out = append(out, d)
	}
	sort.Strings(out)
	return out
}

// AssignABVariant 稳定分配 A/B 实验分组（同一用户恒定分组，便于长期观察效果）
func AssignABVariant(userID string) string {
	h := fnv.New32a()
	_, _ = h.Write([]byte(userID))
	if h.Sum32()%2 == 0 {
		return "control"
	}
	return "treatment"
}

// BuildProfile 聚合反馈样本生成用户旅行画像，是推荐引擎的核心算法。
// 纯函数，不依赖数据库，输出可被前端展示、亦可在网关/Python 之间传递。
func BuildProfile(userID string, samples []FeedbackSample) Profile {
	profile := Profile{
		BudgetTendency: "mid",
		ABVariant:      AssignABVariant(userID),
		FeedbackCount:  len(samples),
	}

	likedTags := map[string]int{}
	dislikedTags := map[string]int{}
	dimLike := map[string]int{"nature": 0, "culture": 0, "food": 0}

	lowBudgetSignals := 0
	highBudgetSignals := 0

	for _, s := range samples {
		// 粗维度仅用于三大比例归一化；偏好标签/Dislike 改用细标签，表达更精确的偏好。
		dims := dimOf(s.Target)
		for _, d := range dims {
			if s.Score >= 1 {
				dimLike[d]++
			}
		}
		fine := fineTagsOf(s.Target)
		if s.Score >= 1 {
			for _, t := range fine {
				likedTags[t]++
			}
		} else {
			for _, t := range fine {
				dislikedTags[t]++
			}
		}

		// 消费习惯推断（启发式）
		if s.Score >= 1 && (strings.Contains(s.Reason, "免费") || strings.Contains(s.Reason, "划算") || strings.Contains(s.Reason, "性价比")) {
			lowBudgetSignals++
		}
		if s.Score >= 1 && (strings.Contains(s.Reason, "高档") || strings.Contains(s.Reason, "豪华") || strings.Contains(s.Reason, "五星") || strings.Contains(s.Reason, "奢华") || strings.Contains(s.Reason, "品质") || strings.Contains(s.Reason, "高端")) {
			highBudgetSignals++
		}
		if s.Score < 0 && (strings.Contains(s.Reason, "太贵") || strings.Contains(s.Reason, "贵")) {
			lowBudgetSignals++
		}
	}

	// 三大维度权重归一化
	total := dimLike["nature"] + dimLike["culture"] + dimLike["food"]
	if total > 0 {
		profile.NatureRatio = round2(float64(dimLike["nature"]) / float64(total))
		profile.CultureRatio = round2(float64(dimLike["culture"]) / float64(total))
		profile.FoodRatio = round2(float64(dimLike["food"]) / float64(total))
	} else {
		profile.NatureRatio = 0.33
		profile.CultureRatio = 0.33
		profile.FoodRatio = 0.34
	}

	// 品质/高消费信号优先于省钱信号：当用户出现明确「高档/豪华/五星」正向反馈时，
	// 即使历史中存在省钱倾向，也应按当前更强诉求判定为高预算（避免 high 分支成为死代码）。
	if highBudgetSignals >= 1 {
		profile.BudgetTendency = "high"
	} else if lowBudgetSignals >= 1 {
		profile.BudgetTendency = "low"
	}

	profile.LikedTags = topKeys(likedTags)
	profile.DislikedTags = topKeys(dislikedTags)
	profile.CuisinePrefs = foodTags(likedTags)

	// 无负向/正向标签时兜底为通用画像，保证 Prompt 永远有内容可注入
	if len(profile.LikedTags) == 0 {
		profile.LikedTags = []string{"风景", "美食", "人文"}
	}
	if len(profile.CuisinePrefs) == 0 {
		profile.CuisinePrefs = []string{"地道特色"}
	}

	return profile
}

func foodTags(liked map[string]int) []string {
	out := []string{}
	for tag, cnt := range liked {
		if cnt > 0 && fineTagDims[tag] == "food" {
			out = append(out, tag)
		}
	}
	sort.Strings(out)
	return out
}

func topKeys(m map[string]int) []string {
	type kv struct {
		k string
		v int
	}
	arr := make([]kv, 0, len(m))
	for k, v := range m {
		arr = append(arr, kv{k, v})
	}
	sort.Slice(arr, func(i, j int) bool { return arr[i].v > arr[j].v })
	out := make([]string, 0, len(arr))
	for _, item := range arr {
		out = append(out, item.k)
	}
	return out
}

func round2(v float64) float64 {
	return math.Round(v*100) / 100
}

var dimName = map[string]string{
	"nature":  "自然风光",
	"culture": "人文历史",
	"food":    "地道美食",
}

// ProfileToPrompt 将画像转为自然语言片段，用于注入个性化 Prompt。
func ProfileToPrompt(p Profile) string {
	if p.FeedbackCount == 0 {
		return "该用户暂无历史偏好记录，请根据当前诉求与热门推荐均衡规划。"
	}
	liked := strings.Join(mapNames(p.LikedTags), "、")
	disliked := "无"
	if len(p.DislikedTags) > 0 {
		disliked = strings.Join(mapNames(p.DislikedTags), "、")
	}
	budget := map[string]string{"low": "精打细算/中低预算", "mid": "中等预算", "high": "品质高预算"}[p.BudgetTendency]
	return "该用户历史偏好画像：偏好维度（" + strings.Join(mapNames(p.LikedTags), "、") + "）；" +
		"消费习惯为" + budget + "；" +
		"喜欢 " + liked + "；" +
		"不希望出现 " + disliked + "。规划时应优先匹配其偏好维度与消费习惯。"
}

func mapNames(tags []string) []string {
	out := make([]string, 0, len(tags))
	for _, t := range tags {
		if n, ok := dimName[t]; ok {
			out = append(out, n)
		} else {
			out = append(out, t)
		}
	}
	return out
}