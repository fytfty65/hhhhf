package service

import (
	"encoding/json"
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
	return BuildProfile(userID, samples)
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