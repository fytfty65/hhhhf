package service

import (
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
