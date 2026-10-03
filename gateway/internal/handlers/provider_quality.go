package handlers

import (
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"gateway/internal/contracts"
	"gateway/internal/database"
	"gateway/internal/models"
	"github.com/gin-gonic/gin"
)

// providerQualityFields is deliberately a small, domain-specific allow-list.
// It keeps the quality endpoint about field coverage rather than exposing a
// supplier response (which may contain private or credential-bearing data).
func providerQualityFields(kind string) []string {
	switch strings.ToLower(strings.TrimSpace(kind)) {
	case "transport":
		return []string{"price", "duration_minutes", "availability", "booking_url"}
	case "lodging":
		return []string{"price", "availability", "opening_hours", "commute_minutes", "booking_url"}
	case "scenic", "destination":
		return []string{"opening_hours", "crowd_level", "booking_url"}
	case "dining":
		return []string{"price", "opening_hours", "cuisine", "allergens", "booking_url"}
	case "weather":
		return []string{"condition", "temperature", "precipitation", "alert"}
	case "crowd":
		return []string{"crowd_level", "score", "forecast_hour"}
	default:
		return []string{"price", "duration_minutes", "availability", "booking_url"}
	}
}

func providerQualityHasField(candidate contracts.Candidate, field string) bool {
	if field == "price" {
		return candidate.Price > 0
	}
	if field == "duration_minutes" {
		return candidate.Duration > 0
	}
	if candidate.Extra == nil {
		return false
	}
	keys := map[string][]string{
		"booking_url":     {"booking_url", "bookingUrl", "deep_link", "url", "link"},
		"opening_hours":   {"opening_hours", "openingHours", "open_time", "openTime", "open_hours"},
		"commute_minutes": {"commute_minutes", "commuteMinutes"},
		"crowd_level":     {"crowd_level", "crowdLevel", "crowd_level_text", "crowdLevelText"},
		"forecast_hour":   {"forecast_hour", "forecastHour"},
		"temperature":     {"temperature", "temp"},
		"precipitation":   {"precipitation", "precipitation_probability"},
		"condition":       {"condition", "weather", "weather_condition", "weatherType", "weather_desc"},
		"alert":           {"alert", "weather_alert"},
		"score":           {"score", "crowd_score"},
		"availability":    {"availability", "available", "inventory"},
		"cuisine":         {"cuisine", "cuisine_type"},
		"allergens":       {"allergens", "allergen"},
	}
	for _, key := range keys[field] {
		value, exists := candidate.Extra[key]
		if !exists || value == nil {
			continue
		}
		if text := strings.TrimSpace(toQualityString(value)); text != "" && text != "<nil>" {
			return true
		}
	}
	return false
}

func toQualityString(value any) string {
	return strings.TrimSpace(fmt.Sprint(value))
}

func providerQualityBool(value any) bool {
	parsed, ok := value.(bool)
	return ok && parsed
}

func providerQualityFactUsable(candidate contracts.Candidate, kind string) bool {
	if strings.TrimSpace(candidate.Source.Provider) == "" || candidate.Source.Estimated {
		return false
	}
	if candidate.Extra != nil && providerQualityBool(candidate.Extra["observed_at_provided"]) == false {
		return false
	}
	if candidate.Price > 0 && strings.TrimSpace(fieldSource(&candidate, "price")) == "" {
		return false
	}
	if candidate.Duration > 0 && strings.TrimSpace(fieldSource(&candidate, "duration_minutes")) == "" {
		return false
	}
	_ = kind
	return !candidate.Source.Retrieved.IsZero()
}

// providerQualityDecisionEligible mirrors the runtime decision gate without
// enriching or mutating the candidate. Quality telemetry is recorded at the
// supplier boundary, before route ranking, so dashboards can distinguish
// "provider returned rows" from "rows safe for live fact decisions".
func providerQualityDecisionEligible(candidate contracts.Candidate, kind string) bool {
	if strings.TrimSpace(candidate.Source.Provider) == "" || candidate.Source.Estimated || candidate.Source.Retrieved.IsZero() {
		return false
	}
	if candidate.Extra == nil || !providerQualityBool(candidate.Extra["observed_at_provided"]) {
		return false
	}
	age := time.Since(candidate.Source.Retrieved)
	if age < 0 {
		age = 0
	}
	if age > time.Duration(providerFreshnessLimitSeconds(kind))*time.Second {
		return false
	}
	for _, group := range decisionRequiredFieldGroups(kind) {
		matched := false
		for _, field := range group {
			if decisionCandidateHasField(&candidate, field) {
				matched = true
				break
			}
		}
		if !matched {
			return false
		}
	}
	return true
}

func providerQualityEnumStatus(candidate contracts.Candidate) (validated, invalid bool) {
	if candidate.Extra == nil {
		return false, false
	}
	validation, ok := candidate.Extra["enum_validation"].(map[string]any)
	if !ok || len(validation) == 0 {
		return false, false
	}
	validated = true
	for _, raw := range validation {
		entry, ok := raw.(map[string]any)
		if ok && strings.TrimSpace(fmt.Sprint(entry["status"])) == "unrecognized" {
			return true, true
		}
	}
	return true, false
}

// providerQualitySnapshot converts normalized candidates into a safe event
// payload. It intentionally contains counts and field names only; raw
// supplier rows, URLs, credentials, user input and trip data are excluded.
func providerQualitySnapshot(kind, provider string, candidates []contracts.Candidate, status string, latency time.Duration) map[string]any {
	fields := providerQualityFields(kind)
	fieldCounts := make(map[string]int, len(fields))
	sourceCount, observedCount, factUsableCount, estimatedCount, staleCount, conflictCount := 0, 0, 0, 0, 0, 0
	enumValidatedCount, enumInvalidCount := 0, 0
	decisionEligibleCount := 0
	latestObserved := time.Time{}
	limit := providerFreshnessLimitSeconds(kind)
	for _, candidate := range candidates {
		if strings.TrimSpace(candidate.Source.Provider) != "" {
			sourceCount++
		}
		observed := candidate.Extra != nil && providerQualityBool(candidate.Extra["observed_at_provided"])
		if observed {
			observedCount++
			if candidate.Source.Retrieved.After(latestObserved) {
				latestObserved = candidate.Source.Retrieved
			}
			age := int(time.Since(candidate.Source.Retrieved).Seconds())
			if age > limit {
				staleCount++
			}
		}
		if providerQualityFactUsable(candidate, kind) {
			factUsableCount++
		}
		if providerQualityDecisionEligible(candidate, kind) {
			decisionEligibleCount++
		}
		if candidate.Source.Estimated {
			estimatedCount++
		}
		if candidate.Extra != nil && providerQualityBool(candidate.Extra["source_conflict"]) {
			conflictCount++
		}
		if validated, invalid := providerQualityEnumStatus(candidate); validated {
			enumValidatedCount++
			if invalid {
				enumInvalidCount++
			}
		}
		for _, field := range fields {
			if providerQualityHasField(candidate, field) {
				fieldCounts[field]++
			}
		}
	}
	result := map[string]any{
		"kind":                     strings.ToLower(strings.TrimSpace(kind)),
		"provider":                 strings.TrimSpace(provider),
		"status":                   status,
		"candidate_count":          len(candidates),
		"source_count":             sourceCount,
		"observed_timestamp_count": observedCount,
		"fact_usable_count":        factUsableCount,
		"decision_eligible_count":  decisionEligibleCount,
		"decision_blocked_count":   len(candidates) - decisionEligibleCount,
		"decision_gate_contract":   "provider-decision-gate-v1",
		"estimated_count":          estimatedCount,
		"stale_count":              staleCount,
		"source_conflict_count":    conflictCount,
		"enum_validated_count":     enumValidatedCount,
		"enum_invalid_count":       enumInvalidCount,
		"field_counts":             fieldCounts,
		"field_names":              fields,
		"latency_ms":               latency.Milliseconds(),
		"quality_contract":         "provider-quality-v1",
		"raw_response_persisted":   false,
		"credentials_persisted":    false,
	}
	if !latestObserved.IsZero() {
		result["latest_observed_at"] = latestObserved.UTC().Format(time.RFC3339)
	}
	if status != "ok" {
		result["error_code"] = "provider_empty_or_unavailable"
	}
	return result
}

func recordProviderQualityObservation(input contracts.PlanningContext, kind, provider string, candidates []contracts.Candidate, status string, latency time.Duration) {
	if database.DB == nil {
		return
	}
	payload, err := json.Marshal(providerQualitySnapshot(kind, provider, candidates, status, latency))
	if err != nil {
		return
	}
	// Quality telemetry is best effort. A provider outage must never make a
	// planning request fail, so persistence errors are deliberately ignored.
	event := &models.PlanningEvent{
		UserID:    strings.TrimSpace(input.UserID),
		TripID:    strings.TrimSpace(input.TripID),
		EventType: "provider_quality",
		Payload:   string(payload),
	}
	if err := database.DB.Create(event).Error; err != nil {
		return
	}
	if event.UserID == "" || event.TripID == "" {
		return
	}
	// Evaluate only this user's/trip's recent aggregate so an alert state never
	// mixes tenants. The state write is best effort and cannot fail planning.
	var recent []models.PlanningEvent
	cutoff := time.Now().UTC().Add(-30 * 24 * time.Hour)
	if err := database.DB.Where("user_id = ? AND trip_id = ? AND event_type = ? AND created_at >= ?", event.UserID, event.TripID, "provider_quality", cutoff).Find(&recent).Error; err != nil {
		return
	}
	gate := providerQualityGateSummary(recent)
	digest := strings.TrimSpace(fmt.Sprint(gate["alert_digest"]))
	if digest == "" || strings.TrimSpace(fmt.Sprint(gate["status"])) == "available" {
		return
	}
	now := time.Now().UTC()
	shouldNotify, err := recordProviderQualityAlertState(database.DB, event.UserID, event.TripID, digest, strings.TrimSpace(fmt.Sprint(gate["status"])), now)
	if err == nil && shouldNotify {
		_ = materializeProviderQualityNotification(database.DB, event.UserID, event.TripID, digest, strings.TrimSpace(fmt.Sprint(gate["status"])), now)
	}
}

type providerQualityAggregate struct {
	Kind                   string
	Provider               string
	Observations           int
	SuccessfulObservations int
	EmptyObservations      int
	CandidateCount         int
	SourceCount            int
	ObservedCount          int
	FactUsableCount        int
	DecisionEligibleCount  int
	DecisionBlockedCount   int
	DecisionGateObserved   bool
	EstimatedCount         int
	StaleCount             int
	SourceConflictCount    int
	EnumValidatedCount     int
	EnumInvalidCount       int
	FieldCounts            map[string]int
	FieldNames             []string
	LastStatus             string
	LastErrorCode          string
	LastObservedAt         string
	LastEventAt            time.Time
	LatencySumMs           int64
	LatencySamples         int
}

func (aggregate *providerQualityAggregate) add(payload map[string]any, createdAt time.Time) {
	aggregate.Observations++
	status := strings.TrimSpace(stringValue(payload["status"]))
	if status == "ok" {
		aggregate.SuccessfulObservations++
	} else {
		aggregate.EmptyObservations++
	}
	aggregate.CandidateCount += intValue(payload["candidate_count"])
	aggregate.SourceCount += intValue(payload["source_count"])
	aggregate.ObservedCount += intValue(payload["observed_timestamp_count"])
	aggregate.FactUsableCount += intValue(payload["fact_usable_count"])
	aggregate.DecisionEligibleCount += intValue(payload["decision_eligible_count"])
	aggregate.DecisionBlockedCount += intValue(payload["decision_blocked_count"])
	if strings.TrimSpace(stringValue(payload["decision_gate_contract"])) == "provider-decision-gate-v1" || payload["decision_eligible_count"] != nil || payload["decision_blocked_count"] != nil {
		aggregate.DecisionGateObserved = true
	}
	aggregate.EstimatedCount += intValue(payload["estimated_count"])
	aggregate.StaleCount += intValue(payload["stale_count"])
	aggregate.SourceConflictCount += intValue(payload["source_conflict_count"])
	aggregate.EnumValidatedCount += intValue(payload["enum_validated_count"])
	aggregate.EnumInvalidCount += intValue(payload["enum_invalid_count"])
	if latency := intValue(payload["latency_ms"]); latency >= 0 {
		aggregate.LatencySumMs += int64(latency)
		aggregate.LatencySamples++
	}
	if fields, ok := payload["field_counts"].(map[string]any); ok {
		for field, value := range fields {
			aggregate.FieldCounts[field] += intValue(value)
		}
	}
	if names, ok := payload["field_names"].([]any); ok && len(aggregate.FieldNames) == 0 {
		for _, value := range names {
			if name := strings.TrimSpace(stringValue(value)); name != "" {
				aggregate.FieldNames = append(aggregate.FieldNames, name)
			}
		}
	}
	if createdAt.After(aggregate.LastEventAt) {
		aggregate.LastEventAt = createdAt
		aggregate.LastStatus = status
		aggregate.LastErrorCode = strings.TrimSpace(stringValue(payload["error_code"]))
		aggregate.LastObservedAt = strings.TrimSpace(stringValue(payload["latest_observed_at"]))
	}
}

func intValue(value any) int {
	switch item := value.(type) {
	case int:
		return item
	case int64:
		return int(item)
	case float64:
		return int(item)
	case json.Number:
		parsed, _ := item.Int64()
		return int(parsed)
	default:
		return 0
	}
}

func stringValue(value any) string {
	if value == nil {
		return ""
	}
	return strings.TrimSpace(fmt.Sprint(value))
}

func qualityRatio(count, total int) float64 {
	if total <= 0 {
		return 0
	}
	return float64(count) / float64(total)
}

// ProviderQualityHandler returns aggregate, secret-free supplier quality
// metrics. It is intentionally event-backed so dashboards can inspect recent
// observations without receiving raw supplier responses.
func ProviderQualityHandler(c *gin.Context) {
	windowDays := 7
	if raw := strings.TrimSpace(c.Query("window_days")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed >= 1 && parsed <= 30 {
			windowDays = parsed
		}
	}
	aggregates := map[string]*providerQualityAggregate{}
	var events []models.PlanningEvent
	queryStatus := "available"
	if database.DB != nil {
		if err := database.DB.Where("event_type = ? AND created_at >= ?", "provider_quality", time.Now().Add(-time.Duration(windowDays)*24*time.Hour)).Order("created_at asc").Limit(5000).Find(&events).Error; err != nil {
			queryStatus = "unavailable"
		}
	} else {
		queryStatus = "unavailable"
	}
	for _, event := range events {
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		kind := strings.ToLower(strings.TrimSpace(stringValue(payload["kind"])))
		provider := strings.TrimSpace(stringValue(payload["provider"]))
		if kind == "" || provider == "" {
			continue
		}
		key := kind + "\x00" + provider
		aggregate := aggregates[key]
		if aggregate == nil {
			aggregate = &providerQualityAggregate{Kind: kind, Provider: provider, FieldCounts: map[string]int{}}
			aggregates[key] = aggregate
		}
		aggregate.add(payload, event.CreatedAt)
	}
	items := make([]gin.H, 0, len(aggregates))
	for _, aggregate := range aggregates {
		fields := gin.H{}
		for _, field := range aggregate.FieldNames {
			count := aggregate.FieldCounts[field]
			fields[field] = gin.H{"count": count, "coverage": qualityRatio(count, aggregate.CandidateCount)}
		}
		items = append(items, gin.H{
			"kind": aggregate.Kind, "provider": aggregate.Provider,
			"observations": aggregate.Observations, "successful_observations": aggregate.SuccessfulObservations, "empty_observations": aggregate.EmptyObservations,
			"candidate_count": aggregate.CandidateCount,
			"coverage":        gin.H{"source": qualityRatio(aggregate.SourceCount, aggregate.CandidateCount), "observed_at": qualityRatio(aggregate.ObservedCount, aggregate.CandidateCount), "fact_usable": qualityRatio(aggregate.FactUsableCount, aggregate.CandidateCount)},
			"decision_gate":   gin.H{"contract": "provider-decision-gate-v1", "status": map[bool]string{true: "available", false: "not_collected"}[aggregate.DecisionGateObserved], "eligible_count": aggregate.DecisionEligibleCount, "blocked_count": aggregate.DecisionBlockedCount, "eligible_ratio": qualityRatio(aggregate.DecisionEligibleCount, aggregate.CandidateCount)},
			"estimated_ratio": qualityRatio(aggregate.EstimatedCount, aggregate.CandidateCount), "stale_ratio": qualityRatio(aggregate.StaleCount, aggregate.CandidateCount),
			"source_conflict_count": aggregate.SourceConflictCount, "source_conflict_ratio": qualityRatio(aggregate.SourceConflictCount, aggregate.CandidateCount),
			"enum_validated_count": aggregate.EnumValidatedCount, "enum_invalid_count": aggregate.EnumInvalidCount, "enum_invalid_ratio": qualityRatio(aggregate.EnumInvalidCount, aggregate.CandidateCount),
			"field_coverage": fields, "last_status": aggregate.LastStatus, "last_error_code": aggregate.LastErrorCode,
			"latest_observed_at": aggregate.LastObservedAt, "last_event_at": aggregate.LastEventAt.UTC(),
			"mean_latency_ms": func() any {
				if aggregate.LatencySamples == 0 {
					return nil
				}
				return float64(aggregate.LatencySumMs) / float64(aggregate.LatencySamples)
			}(),
		})
	}
	sort.Slice(items, func(i, j int) bool {
		return items[i]["kind"].(string)+items[i]["provider"].(string) < items[j]["kind"].(string)+items[j]["provider"].(string)
	})
	status := queryStatus
	if len(items) == 0 {
		if status == "available" {
			status = "insufficient_data"
		}
	}
	planningData(c, gin.H{"quality_contract": "provider-quality-v1", "window_days": windowDays, "sample_count": len(events), "providers": items, "status": status, "policy": "aggregate_only_no_raw_supplier_payload"})
}

// ProviderQualityTrendHandler returns daily, aggregate-only quality points so
// dashboards can show freshness, eligibility and source-conflict trends
// without exposing any supplier response or user/trip identifiers.
func ProviderQualityTrendHandler(c *gin.Context) {
	windowDays := 30
	if raw := strings.TrimSpace(c.Query("window_days")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed >= 1 && parsed <= 90 {
			windowDays = parsed
		}
	}
	cutoff := time.Now().Add(-time.Duration(windowDays) * 24 * time.Hour)
	var events []models.PlanningEvent
	status := "available"
	if database.DB == nil || database.DB.Where("event_type = ? AND created_at >= ?", "provider_quality", cutoff).Order("created_at asc").Limit(10000).Find(&events).Error != nil {
		status = "unavailable"
	}
	type bucket struct {
		Date, Kind, Provider                                       string
		Observations, Candidates, Eligible, Conflicts, EnumInvalid int
	}
	buckets := map[string]*bucket{}
	for _, event := range events {
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		kind := strings.ToLower(strings.TrimSpace(stringValue(payload["kind"])))
		provider := strings.TrimSpace(stringValue(payload["provider"]))
		if kind == "" || provider == "" {
			continue
		}
		date := event.CreatedAt.UTC().Format("2006-01-02")
		key := date + "\x00" + kind + "\x00" + provider
		item := buckets[key]
		if item == nil {
			item = &bucket{Date: date, Kind: kind, Provider: provider}
			buckets[key] = item
		}
		item.Observations++
		item.Candidates += intValue(payload["candidate_count"])
		item.Eligible += intValue(payload["decision_eligible_count"])
		item.Conflicts += intValue(payload["source_conflict_count"])
		item.EnumInvalid += intValue(payload["enum_invalid_count"])
	}
	points := make([]gin.H, 0, len(buckets))
	for _, item := range buckets {
		points = append(points, gin.H{
			"date": item.Date, "kind": item.Kind, "provider": item.Provider,
			"observations": item.Observations, "candidate_count": item.Candidates,
			"decision_eligible_count": item.Eligible, "source_conflict_count": item.Conflicts,
			"eligible_ratio":        qualityRatio(item.Eligible, item.Candidates),
			"source_conflict_ratio": qualityRatio(item.Conflicts, item.Candidates),
			"enum_invalid_count":    item.EnumInvalid, "enum_invalid_ratio": qualityRatio(item.EnumInvalid, item.Candidates),
		})
	}
	sort.Slice(points, func(i, j int) bool {
		left := points[i]["date"].(string) + points[i]["kind"].(string) + points[i]["provider"].(string)
		right := points[j]["date"].(string) + points[j]["kind"].(string) + points[j]["provider"].(string)
		return left < right
	})
	if len(points) == 0 && status == "available" {
		status = "insufficient_data"
	}
	planningData(c, gin.H{"quality_contract": "provider-quality-v1", "window_days": windowDays, "points": points, "status": status, "policy": "daily_aggregate_only_no_raw_supplier_payload"})
}
