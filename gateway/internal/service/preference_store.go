package service

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
)

// BuildProfileFromFeedback 从数据库中的真实反馈样本聚合出用户旅行画像。
// 这是「画像动态更新」的入口：每次调用都会重新读取全部反馈并聚合，保证画像始终最新。
func BuildProfileFromFeedback(userID string) Profile {
	if database.DB == nil {
		return Profile{ABVariant: AssignABVariant(userID)}
	}

	var logs []models.FeedbackLog
	database.DB.Where("user_id = ?", userID).Order("created_at asc").Find(&logs)

	samples := make([]FeedbackSample, 0, len(logs))
	for _, l := range logs {
		if l.Target == "" {
			continue
		}
		samples = append(samples, FeedbackSample{
			Target: l.Target,
			Score:  l.Score,
			Reason: l.Reason,
		})
	}
	// P6：融合「历史行程 + 满意度」派生的偏好信号（满意度高分正向强化、低分负向弱化）
	samples = append(samples, collectHistorySamples(userID)...)
	// P7：融合「实际消费记录」派生的偏好信号（餐饮消费强化美食维度，让消费行为回流画像）
	samples = append(samples, collectExpenseSamples(userID)...)
	p := BuildProfile(userID, samples)
	// P8：融合「消费金额」驱动的预算档位——仅当反馈关键词无明确结论（mid）时，
	// 由实际住宿消费档位补位，避免削弱用户主观诉求（反馈已判 high/low 则保持不变）。
	if p.BudgetTendency == "mid" {
		if t := expenseAmountTendency(userID); t != "" {
			p.BudgetTendency = t
		}
	}
	return p
}

// collectHistorySamples 从历史行程（TripPlan）与满意度（TripSatisfaction）派生偏好信号。
// 满意度 >=4 星视为正向、<=2 星视为负向（3 星中性，不产生信号），
// 信号 Target 取自关联行程的目的地/标题/路线节点名称，评论作为 Reason 以命中消费倾向关键词。
func collectHistorySamples(userID string) []FeedbackSample {
	if database.DB == nil {
		return nil
	}

	var sats []models.TripSatisfaction
	database.DB.Where("user_id = ?", userID).Order("created_at asc").Find(&sats)
	if len(sats) == 0 {
		return nil
	}

	var trips []models.TripPlan
	database.DB.Where("user_id = ?", userID).Find(&trips)
	tripMap := make(map[string]models.TripPlan, len(trips))
	for _, t := range trips {
		tripMap[t.ID] = t
	}

	samples := make([]FeedbackSample, 0)
	seen := make(map[string]bool)
	for _, s := range sats {
		score := 0
		if s.Score >= 4 {
			score = 1
		} else if s.Score <= 2 {
			score = -1
		} else {
			continue // 3 星中性，不参与偏好强化
		}

		reason := strings.TrimSpace(s.Comment)
		if reason == "" {
			if score >= 1 {
				reason = "行程满意度高"
			} else {
				reason = "行程满意度低"
			}
		}

		targets := make([]string, 0, 8)
		if trip, ok := tripMap[s.TripID]; ok {
			targets = append(targets, trip.DestCity, trip.Title)
			targets = append(targets, extractNamesFromJSON(trip.Content)...)
		}
		if len(targets) == 0 && reason != "" && s.Comment != "" {
			targets = append(targets, reason)
		}

		for _, t := range targets {
			t = strings.TrimSpace(t)
			if t == "" {
				continue
			}
			key := t + "|" + fmt.Sprint(score)
			if seen[key] {
				continue
			}
			seen[key] = true
			samples = append(samples, FeedbackSample{Target: t, Score: score, Reason: reason})
		}
	}
	return samples
}

// collectExpenseSamples 从实际消费记录（ExpenseRecord）派生正向偏好信号，让「真金白银的消费行为」
// 回流为画像的一部分：餐饮类消费强化美食维度并命中「地方菜」细分标签；备注字段一并写入 Reason，
// 以便命中消费倾向关键词（如「五星/豪华」→ 高预算倾向）。
func collectExpenseSamples(userID string) []FeedbackSample {
	if database.DB == nil {
		return nil
	}

	var expenses []models.ExpenseRecord
	database.DB.Where("user_id = ?", userID).Order("created_at asc").Find(&expenses)

	samples := make([]FeedbackSample, 0, len(expenses))
	for _, e := range expenses {
		target := expenseCategoryTarget(e.Category)
		if target == "" {
			continue // 不映射三大预维度的分类（如住宿/交通/购物）不产生偏好噪声
		}
		reason := fmt.Sprintf("消费记录: %s %.2f%s", e.Category, e.Amount, e.Currency)
		if note := strings.TrimSpace(e.Note); note != "" {
			reason += " " + note
		}
		samples = append(samples, FeedbackSample{Target: target, Score: 1, Reason: reason})
	}
	return samples
}

// expenseCategoryTarget 将消费分类映射为可被画像维度/细标签稳定识别的目标词。
// 仅映射能落到 nature/culture/food 三大预维度（或其细分标签）的分类，其余返回空。
func expenseCategoryTarget(category string) string {
	switch category {
	case "餐饮", "美食", "小吃", "餐厅":
		return "餐厅" // food 维度 + 「地方菜」细分标签
	default:
		return ""
	}
}

// budgetSignalCategories 参与预算档位推断的「硬性支出」品类。
// 住宿/交通/门票是旅行刚性支出，金额高低与消费层级强正相关；
// 餐饮/购物受个人口味与随机因素影响大，排除在外以降低噪声。
var budgetSignalCategories = []string{"住宿", "交通", "门票"}

// 消费金额档位启发式阈值（换算为 CNY），作为「绝对消费层级」的锚点。
// 阈值支持通过环境变量动态配置（默认：高档 ≥1000、经济 ≤300）；
// 实际判定还会结合该用户自身消费分布的分位数动态校准（见 budgetLevelFromAmounts），
// 避免固定阈值对高/低消费人群的失准。
var (
	highEndHotelCnyThreshold = getEnvAsFloat("HIGH_END_HOTEL_THRESHOLD_CNY", 1000.0) // 绝对高档锚点：单笔支出 ≥阈值 倾向高档
	budgetHotelCnyThreshold  = getEnvAsFloat("BUDGET_HOTEL_THRESHOLD_CNY", 300.0)   // 绝对经济锚点：单笔支出 ≤阈值 倾向经济
)

// getEnvAsFloat 读取环境变量并解析为 float64；缺失、格式非法或为负值时回退默认值，保证服务不因配置错误而 panic。
func getEnvAsFloat(key string, defaultValue float64) float64 {
	valStr := os.Getenv(key)
	if valStr == "" {
		return defaultValue
	}
	val, err := strconv.ParseFloat(valStr, 64)
	if err != nil || val < 0 {
		return defaultValue
	}
	return val
}

// isBudgetSignalCategory 判断消费分类是否属于参与预算推断的硬性支出品类。
func isBudgetSignalCategory(cat string) bool {
	for _, c := range budgetSignalCategories {
		if c == cat {
			return true
		}
	}
	return false
}

// percentile 计算升序切片在 [0,1] 分位点上的数值（线性插值）。
func percentile(sorted []float64, p float64) float64 {
	if len(sorted) == 0 {
		return 0
	}
	pos := p * float64(len(sorted)-1)
	lo := int(math.Floor(pos))
	hi := int(math.Ceil(pos))
	if lo == hi {
		return sorted[lo]
	}
	frac := pos - float64(lo)
	return sorted[lo]*(1-frac) + sorted[hi]*frac
}

// budgetLevelFromAmounts 纯函数：给定若干笔硬性支出金额（已换算 CNY），按多数投票推断预算档位。
// 阈值动态化：样本 ≥3 笔时，用该用户自身分布的 25/75 分位数与绝对锚点校准——
// 高档线取「绝对高档锚点」与「自身高档分布 p75」的较大者，经济线取「绝对经济锚点」与「自身低档分布 p25」的较小者，
// 既保留绝对消费层级语义，又让判定阈值随个体消费分布自适应。
func budgetLevelFromAmounts(amounts []float64) string {
	if len(amounts) == 0 {
		return ""
	}
	asc := append([]float64(nil), amounts...)
	sort.Float64s(asc)

	highTh, lowTh := highEndHotelCnyThreshold, budgetHotelCnyThreshold
	if len(asc) >= 3 {
		p25, p75 := percentile(asc, 0.25), percentile(asc, 0.75)
		highTh = math.Max(highTh, p75)
		lowTh = math.Min(lowTh, p25)
	}

	high, low := 0, 0
	for _, v := range asc {
		switch {
		case v >= highTh:
			high++
		case v <= lowTh:
			low++
		}
	}
	switch {
	case high > low:
		return "high"
	case low > high:
		return "low"
	default:
		return ""
	}
}

// parseDaysFromTitle 从行程标题提取天数（如「洛阳 3 日游」「3天2晚」→ 3），失败返回 0。
func parseDaysFromTitle(title string) int {
	re := regexp.MustCompile(`(\d{1,2})\s*[天日]`)
	m := re.FindStringSubmatch(title)
	if m == nil {
		return 0
	}
	d, err := strconv.Atoi(m[1])
	if err != nil || d < 1 || d > 365 {
		return 0
	}
	return d
}

// tripScale 行程规模：天数与同行人数，供「人均日消费」折算作分母。
// 天数优先取 TripPlan.Days；历史数据缺失时回退标题正则解析（parseDaysFromTitle）。
// Travelers 含本人，<1 按 1 计避免除零。
type tripScale struct {
	Days      int
	Travelers int
}

// tripScalePerTrip 返回该用户每个行程的规模映射，供「人均日消费」折算作分母。
func tripScalePerTrip(userID string) map[string]tripScale {
	if database.DB == nil {
		return nil
	}
	var trips []models.TripPlan
	database.DB.Where("user_id = ?", userID).Find(&trips)
	m := make(map[string]tripScale, len(trips))
	for _, t := range trips {
		days := t.Days
		if days < 1 {
			days = parseDaysFromTitle(t.Title)
		}
		travelers := t.Travelers
		if travelers < 1 {
			travelers = 1
		}
		m[t.ID] = tripScale{Days: days, Travelers: travelers}
	}
	return m
}

// expenseAmountTendency 综合两类金额信号推断预算档位，返回 high/low/空（空表示维持 mid）：
// 1) 单笔硬性支出金额分布（住宿/交通/门票，跨币种经 Convert 换算 CNY）——最强信号；
// 2) 行程「人均日消费」口径（行程硬性总支出 / 天数 / 同行人数）——单笔无明确结论时作为兜底，覆盖消费密度信号。
func expenseAmountTendency(userID string) string {
	if database.DB == nil {
		return ""
	}
	var expenses []models.ExpenseRecord
	database.DB.Where("user_id = ?", userID).Find(&expenses)

	scalePerTrip := tripScalePerTrip(userID)

	single := make([]float64, 0, len(expenses))
	tripTotal := make(map[string]float64)
	for _, e := range expenses {
		if !isBudgetSignalCategory(e.Category) {
			continue
		}
		cny := Convert(e.Amount, e.Currency, "CNY")
		single = append(single, cny)
		tripTotal[e.TripID] += cny
	}

	if lvl := budgetLevelFromAmounts(single); lvl != "" {
		return lvl
	}

	daily := make([]float64, 0, len(tripTotal))
	for tripID, total := range tripTotal {
		if s := scalePerTrip[tripID]; s.Days > 0 && s.Travelers > 0 {
			daily = append(daily, total/float64(s.Days)/float64(s.Travelers))
		}
	}
	return budgetLevelFromAmounts(daily)
}

// extractNamesFromJSON 递归提取 JSON 中所有 "name" 字符串字段，稳健解析路线内容里的地点名。
func extractNamesFromJSON(content string) []string {
	var v interface{}
	if err := json.Unmarshal([]byte(content), &v); err != nil {
		return nil
	}

	names := make([]string, 0, 16)
	var walk func(x interface{})
	walk = func(x interface{}) {
		switch t := x.(type) {
		case map[string]interface{}:
			for k, val := range t {
				if k == "name" {
					if s, ok := val.(string); ok {
						if s = strings.TrimSpace(s); s != "" {
							names = append(names, s)
						}
					}
				}
				walk(val)
			}
		case []interface{}:
			for _, item := range t {
				walk(item)
			}
		}
	}
	walk(v)
	return names
}

// encodeStringSlice 将 []string 序列化为 JSON 字符串数组（避免 SQLite 复杂关联）。
func encodeStringSlice(s []string) string {
	if len(s) == 0 {
		return "[]"
	}
	b, err := json.Marshal(s)
	if err != nil {
		return "[]"
	}
	return string(b)
}

// decodeStringSlice 将 JSON 字符串数组反序列化为 []string。
func decodeStringSlice(s string) []string {
	var out []string
	if s == "" {
		return []string{}
	}
	_ = json.Unmarshal([]byte(s), &out)
	if out == nil {
		out = []string{}
	}
	return out
}

// UpsertProfile 将画像写入 UserPreference 表（存在则更新，不存在则创建）。
// 单行主键命中，满足查询响应 <100ms 的结构化存储要求。
func UpsertProfile(userID string, p Profile) error {
	if database.DB == nil {
		return nil
	}

	pref := models.UserPreference{
		UserID:         userID,
		LikedTags:      encodeStringSlice(p.LikedTags),
		DislikedTags:   encodeStringSlice(p.DislikedTags),
		CuisinePrefs:   encodeStringSlice(p.CuisinePrefs),
		BudgetTendency: p.BudgetTendency,
		NatureRatio:    p.NatureRatio,
		CultureRatio:   p.CultureRatio,
		FoodRatio:      p.FoodRatio,
		ABVariant:      p.ABVariant,
		FeedbackCount:  p.FeedbackCount,
		UpdatedAt:      time.Now(),
	}

	var existing models.UserPreference
	err := database.DB.Where("user_id = ?", userID).First(&existing).Error
	if err != nil {
		return database.DB.Create(&pref).Error
	}
	pref.UpdatedAt = time.Now()
	return database.DB.Model(&existing).Updates(map[string]interface{}{
		"liked_tags":      pref.LikedTags,
		"disliked_tags":   pref.DislikedTags,
		"cuisine_prefs":   pref.CuisinePrefs,
		"budget_tendency": pref.BudgetTendency,
		"nature_ratio":    pref.NatureRatio,
		"culture_ratio":   pref.CultureRatio,
		"food_ratio":      pref.FoodRatio,
		"ab_variant":      pref.ABVariant,
		"feedback_count":  pref.FeedbackCount,
		"updated_at":      pref.UpdatedAt,
	}).Error
}

// GetStoredProfile 读取已持久化的画像；若无记录则按 A/B 分组返回空画像兜底。
func GetStoredProfile(userID string) (Profile, bool) {
	if database.DB == nil {
		return Profile{ABVariant: AssignABVariant(userID)}, false
	}

	var pref models.UserPreference
	if err := database.DB.Where("user_id = ?", userID).First(&pref).Error; err != nil {
		return Profile{ABVariant: AssignABVariant(userID)}, false
	}

	return Profile{
		LikedTags:      decodeStringSlice(pref.LikedTags),
		DislikedTags:   decodeStringSlice(pref.DislikedTags),
		CuisinePrefs:   decodeStringSlice(pref.CuisinePrefs),
		BudgetTendency: pref.BudgetTendency,
		NatureRatio:    pref.NatureRatio,
		CultureRatio:   pref.CultureRatio,
		FoodRatio:      pref.FoodRatio,
		ABVariant:      pref.ABVariant,
		FeedbackCount:  pref.FeedbackCount,
	}, true
}

// RefreshProfile 重聚合画像并持久化，用于「实时采集 → 动态更新」闭环。
func RefreshProfile(userID string) (Profile, error) {
	p := BuildProfileFromFeedback(userID)
	if err := UpsertProfile(userID, p); err != nil {
		return p, err
	}
	return p, nil
}

// ProfilePayload 画像对外传输结构（供前端展示与注入 Python Prompt）。
func ProfilePayload(userID string) map[string]interface{} {
	p, ok := GetStoredProfile(userID)
	if !ok {
		// 尚未形成画像，实时聚合一次（不落库，避免无反馈用户产生脏数据）
		p = BuildProfileFromFeedback(userID)
	}

	return map[string]interface{}{
		"liked_tags":      p.LikedTags,
		"disliked_tags":   p.DislikedTags,
		"cuisine_prefs":   p.CuisinePrefs,
		"budget_tendency": p.BudgetTendency,
		"nature_ratio":    p.NatureRatio,
		"culture_ratio":   p.CultureRatio,
		"food_ratio":      p.FoodRatio,
		"ab_variant":      p.ABVariant,
		"feedback_count":  p.FeedbackCount,
		"prompt_hint":     ProfileToPrompt(p),
	}
}