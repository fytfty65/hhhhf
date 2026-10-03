package service

import (
	"crypto/sha256"
	"encoding/hex"
	"sort"
	"strings"

	"gateway/internal/contracts"
)

// RankCandidates is the shared deterministic ranking stage for destination,
// transport, lodging and dining candidates. Provider scores remain primary;
// explicit preference tags provide a bounded, explainable adjustment.
func RankCandidates(candidates []contracts.Candidate, preferences map[string]any) []contracts.Candidate {
	result := append([]contracts.Candidate(nil), candidates...)
	sort.SliceStable(result, func(i, j int) bool {
		left := adjustedScore(result[i], preferences)
		right := adjustedScore(result[j], preferences)
		if left == right {
			return result[i].Name < result[j].Name
		}
		return left > right
	})
	return result
}

// RankCandidatesWithBehavior applies a bounded, opt-in behavior signal. The
// signal is intentionally aggregate-only: callers pass counts derived from
// the versioned feature snapshot, never raw events or prompt text.
func RankCandidatesWithBehavior(candidates []contracts.Candidate, preferences map[string]any, behavior map[string]any) []contracts.Candidate {
	result := append([]contracts.Candidate(nil), candidates...)
	sort.SliceStable(result, func(i, j int) bool {
		left := adjustedScore(result[i], preferences) + behaviorNudge(result[i], behavior)
		right := adjustedScore(result[j], preferences) + behaviorNudge(result[j], behavior)
		if left == right {
			return result[i].Name < result[j].Name
		}
		return left > right
	})
	return result
}

func behaviorNudge(candidate contracts.Candidate, behavior map[string]any) float64 {
	if behavior == nil {
		return 0
	}
	if enabled, ok := behavior["enabled"].(bool); ok && !enabled {
		return 0
	}
	module := strings.ToLower(strings.TrimSpace(toText(behavior["module"])))
	if module != "" && !candidateMatchesModule(candidate, module) {
		return 0
	}
	verified := boundedNumber(behavior["verified"])
	adopted := boundedNumber(behavior["adopted"])
	if actions, ok := behavior["candidate_actions"].(map[string]map[string]int); ok && candidate.ID != "" {
		if candidateActions, exists := actions[stableBehaviorHash(candidate.ID)]; exists {
			verified = boundedNumber(candidateActions["verified"])
			adopted = boundedNumber(candidateActions["adopted"])
		}
	}
	// Verification raises confidence slightly; adoption is a stronger but
	// still capped signal. One user's history can never move a candidate by
	// more than 0.04 score points.
	return clamp(verified*0.005+adopted*0.01, 0, 0.04)
}

func stableBehaviorHash(value string) string {
	// Kept local to the ranking service so raw candidate IDs never become a
	// feature key exposed to callers. This matches the feature snapshot hash.
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])[:16]
}

func candidateMatchesModule(candidate contracts.Candidate, module string) bool {
	text := strings.ToLower(candidate.Name + " " + strings.Join(candidate.Tags, " "))
	keywords := map[string][]string{
		"transport": {"铁路", "航班", "公交", "地铁", "自驾", "交通"},
		"lodging":   {"酒店", "住宿", "民宿", "旅馆"},
		"dining":    {"餐", "美食", "小馆", "街区", "菜"},
	}[module]
	for _, keyword := range keywords {
		if strings.Contains(text, keyword) {
			return true
		}
	}
	return module == "destination" || module == "scenic_micro"
}

func boundedNumber(value any) float64 {
	switch number := value.(type) {
	case int:
		return float64(maxInt(minInt(number, 4), 0))
	case int64:
		return float64(maxInt(int(minInt64(number, 4)), 0))
	case float64:
		return clamp(number, 0, 4)
	default:
		return 0
	}
}

func minInt(value, limit int) int {
	if value < limit {
		return value
	}
	return limit
}
func maxInt(value, limit int) int {
	if value > limit {
		return value
	}
	return limit
}
func minInt64(value int64, limit int64) int64 {
	if value < limit {
		return value
	}
	return limit
}

func preferenceKeywords(preferences map[string]any) []string {
	keywords := []string{}
	for _, key := range []string{"interest", "style", "pace", "dietary", "transport_preference", "accommodation_style"} {
		if value, exists := preferences[key]; exists {
			text := strings.TrimSpace(strings.ToLower(toText(value)))
			if text != "" {
				keywords = append(keywords, text)
			}
		}
	}
	return keywords
}

func adjustedScore(candidate contracts.Candidate, preferences map[string]any) float64 {
	score := candidate.Score
	text := strings.ToLower(candidate.Name + " " + strings.Join(candidate.Tags, " "))
	for _, keyword := range preferenceKeywords(preferences) {
		if keyword != "" && strings.Contains(text, keyword) {
			score += 0.05
		}
	}
	return score + quantitativeNudge(candidate, preferences)
}

// quantitativeNudge applies bounded, explainable adjustments from real numeric
// evidence (price, duration, carbon, commute, delay risk, transfer buffer).
// Each dimension is tightly capped so the provider score stays the primary
// signal — these are "tie-breaking" adjustments rather than a new objective.
func quantitativeNudge(candidate contracts.Candidate, preferences map[string]any) float64 {
	nudge := 0.0

	// Budget-conscious travellers prefer a lower price.
	if preferLower(preferences, "budget") && candidate.Price > 0 {
		nudge += clamp((300.0-candidate.Price)/3000.0, -0.02, 0.05)
	}
	// Low-carbon travellers prefer smaller footprints.
	if preferLower(preferences, "low_carbon") {
		if carbon, ok := numeric(candidate.Extra["carbon_kg"], candidate.Extra["carbon_kg_estimate"]); ok {
			nudge += clamp((80.0-carbon)/800.0, -0.01, 0.05)
		}
	}
	// Shorter inter-hotel commute is always weakly better.
	if commute, ok := numeric(candidate.Extra["commute_minutes"]); ok {
		nudge += clamp((30.0-commute)/600.0, -0.02, 0.04)
	}
	// Lower delay risk is always weakly better.
	if delay, ok := numeric(candidate.Extra["delay_risk"]); ok {
		nudge += clamp((0.5-delay)/5.0, -0.02, 0.04)
	}
	// A smaller transfer buffer (fewer/lower-risk transfers) is weakly better.
	if buffer, ok := numeric(candidate.Extra["transfer_buffer_minutes"]); ok {
		nudge += clamp((45.0-buffer)/900.0, 0.0, 0.02)
	}
	return nudge
}

// numeric extracts the first non-empty numeric value from the supplied values.
func numeric(values ...any) (float64, bool) {
	for _, value := range values {
		switch v := value.(type) {
		case float64:
			if v != 0 {
				return v, true
			}
		case float32:
			if v != 0 {
				return float64(v), true
			}
		case int:
			if v != 0 {
				return float64(v), true
			}
		case int64:
			if v != 0 {
				return float64(v), true
			}
		}
	}
	return 0, false
}

func clamp(value, min, max float64) float64 {
	if value < min {
		return min
	}
	if value > max {
		return max
	}
	return value
}

func preferLower(preferences map[string]any, key string) bool {
	switch value := preferences[key].(type) {
	case bool:
		return value
	case string:
		return strings.EqualFold(strings.TrimSpace(value), "true") || strings.EqualFold(strings.TrimSpace(value), "low")
	default:
		return false
	}
}

func toText(value any) string {
	if text, ok := value.(string); ok {
		return text
	}
	return ""
}
