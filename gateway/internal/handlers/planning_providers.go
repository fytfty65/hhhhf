package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"gateway/internal/contracts"
	"github.com/google/uuid"
)

// fetchPlanningProvider is an optional adapter boundary. Every provider must
// return {options:[...]} (or the shared envelope's data.options); malformed or
// slow providers are ignored and the caller keeps its deterministic fallback.
func fetchPlanningProvider(ctx context.Context, kind string, input contracts.PlanningContext) ([]contracts.Candidate, contracts.Source, bool) {
	key := strings.ToUpper(strings.TrimSpace(kind))
	configs := resolveProviderConfigs(key)
	if len(configs) == 0 {
		return nil, contracts.Source{}, false
	}
	all := make([]contracts.Candidate, 0)
	aggregateStarted := time.Now()
	for _, config := range configs {
		provider := config.label
		if provider == "" {
			provider = providerName(key, config.base)
		}
		started := time.Now()
		options, ok := fetchOnePlanningProvider(ctx, key, input, config.base, config.token, provider)
		qualityStatus := "empty"
		if ok {
			qualityStatus = "ok"
		}
		recordProviderQualityObservation(input, key, provider, options, qualityStatus, time.Since(started))
		if ok {
			all = append(all, options...)
		}
	}
	if len(all) == 0 {
		return nil, contracts.Source{}, false
	}
	all = annotateProviderConflicts(all)
	if len(configs) > 1 {
		// Keep per-provider observations for latency/coverage, then add one
		// aggregate-only observation so cross-source conflicts are measurable.
		recordProviderQualityObservation(input, key, "multi-source", all, "ok", time.Since(aggregateStarted))
	}
	return all, all[0].Source, true
}

// annotateProviderConflicts keeps every supplier option visible while making
// cross-source disagreements explicit. We intentionally do not silently pick
// a price or availability value: the caller can rank candidates, but the user
// must be able to see that the sources disagree and open an official link.
func annotateProviderConflicts(candidates []contracts.Candidate) []contracts.Candidate {
	groups := make(map[string][]int)
	for index := range candidates {
		name := strings.ToLower(strings.TrimSpace(candidates[index].Name))
		if name == "" {
			continue
		}
		groups[name] = append(groups[name], index)
	}
	for _, indexes := range groups {
		if len(indexes) < 2 {
			continue
		}
		providers := make(map[string]bool)
		conflictingFields := make(map[string]bool)
		for _, index := range indexes {
			provider := strings.TrimSpace(candidates[index].Source.Provider)
			if provider == "" {
				provider = "unknown"
			}
			providers[provider] = true
		}
		if len(providers) < 2 {
			continue
		}
		for _, field := range []string{"price", "duration_minutes", "availability"} {
			first := providerCandidateFieldValue(candidates[indexes[0]], field)
			for _, index := range indexes[1:] {
				if providerCandidateFieldValue(candidates[index], field) != first {
					conflictingFields[field] = true
					break
				}
			}
		}
		if len(conflictingFields) == 0 {
			continue
		}
		providerList := make([]string, 0, len(providers))
		for provider := range providers {
			providerList = append(providerList, provider)
		}
		sort.Strings(providerList)
		fieldList := make([]string, 0, len(conflictingFields))
		for field := range conflictingFields {
			fieldList = append(fieldList, field)
		}
		sort.Strings(fieldList)
		conflictValues := make([]map[string]any, 0, len(indexes))
		for _, index := range indexes {
			values := make(map[string]any, len(fieldList))
			for _, field := range fieldList {
				switch field {
				case "price":
					values[field] = candidates[index].Price
				case "duration_minutes":
					values[field] = candidates[index].Duration
				case "availability":
					values[field] = candidates[index].Extra[field]
				}
			}
			conflictValues = append(conflictValues, map[string]any{
				"provider":     strings.TrimSpace(candidates[index].Source.Provider),
				"candidate_id": candidates[index].ID,
				"values":       values,
			})
		}
		for _, index := range indexes {
			candidates[index].Extra = ensureCandidateExtra(candidates[index].Extra)
			candidates[index].Extra["source_conflict"] = true
			candidates[index].Extra["conflicting_sources"] = providerList
			candidates[index].Extra["conflict_fields"] = fieldList
			candidates[index].Extra["source_conflict_values"] = conflictValues
			candidates[index].Extra["conflict_resolution"] = "kept_per_source_for_user_review"
		}
	}
	return candidates
}

func providerCandidateFieldValue(candidate contracts.Candidate, field string) string {
	switch field {
	case "price":
		return fmt.Sprintf("%.6f", candidate.Price)
	case "duration_minutes":
		return strconv.Itoa(candidate.Duration)
	case "availability":
		return strings.TrimSpace(fmt.Sprint(candidate.Extra[field]))
	default:
		return ""
	}
}

func fetchOnePlanningProvider(ctx context.Context, key string, input contracts.PlanningContext, base, token, provider string) ([]contracts.Candidate, bool) {
	if mcpEndpoint(base) || mcpConfiguredProtocol(key, provider) {
		options, ok := fetchMCPProvider(ctx, base, token, mcpAuthHeader(key, provider, base), provider, key, input)
		return options, ok
	}
	body, err := json.Marshal(supplierContext(input))
	if err != nil {
		return nil, false
	}
	reqCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(reqCtx, http.MethodPost, base, bytes.NewReader(body))
	if err != nil {
		return nil, false
	}
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		authHeader := mcpAuthHeader(key, provider, base)
		if authHeader == "apiKey" {
			req.Header.Set(authHeader, token)
		} else {
			req.Header.Set(authHeader, "Bearer "+token)
		}
	}
	resp, err := (&http.Client{Timeout: 3 * time.Second}).Do(req)
	if err != nil || resp.StatusCode < 200 || resp.StatusCode >= 300 {
		if resp != nil {
			_ = resp.Body.Close()
		}
		return nil, false
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 2<<20))
	if err != nil {
		return nil, false
	}
	var payload any
	if json.Unmarshal(raw, &payload) != nil {
		return nil, false
	}
	options := normalizeProviderCandidates(payload, provider)
	return options, len(options) > 0
}

func mcpConfiguredProtocol(kind, provider string) bool {
	if strings.EqualFold(strings.TrimSpace(os.Getenv(strings.ToUpper(kind)+"_PROVIDER_PROTOCOL")), "mcp") {
		return true
	}
	upper := strings.ToUpper(strings.TrimSpace(provider))
	return strings.EqualFold(strings.TrimSpace(os.Getenv(upper+"_PROVIDER_PROTOCOL")), "mcp")
}

// resolveProviderConfig supports vendor-specific aliases without coupling the
// planner to a vendor schema. A token by itself is never treated as a URL.
func resolveProviderConfig(kind string) (base, token, label string) {
	configs := resolveProviderConfigs(kind)
	if len(configs) == 0 {
		return "", "", ""
	}
	return configs[0].base, configs[0].token, configs[0].label
}

type providerConfig struct {
	base  string
	token string
	label string
}

func resolveProviderConfigs(kind string) []providerConfig {
	kind = strings.ToUpper(strings.TrimSpace(kind))
	base := strings.TrimRight(strings.TrimSpace(os.Getenv(kind+"_PROVIDER_URL")), "/")
	token := strings.TrimSpace(os.Getenv(kind + "_PROVIDER_TOKEN"))
	label := strings.TrimSpace(os.Getenv(kind + "_PROVIDER_NAME"))
	if base != "" {
		return []providerConfig{{base: base, token: token, label: label}}
	}
	configs := make([]providerConfig, 0)
	for _, alias := range providerAliases(kind) {
		for _, endpoint := range providerAliasEndpoints(alias, kind) {
			if endpoint == "" {
				continue
			}
			configs = append(configs, providerConfig{base: endpoint, token: firstEnv(alias+"_API_KEY", alias+"_API_TOKEN", alias+"_PROVIDER_TOKEN"), label: firstNonEmpty(firstEnv(alias+"_PROVIDER_NAME"), canonicalProviderLabel(alias))})
		}
	}
	return configs
}

func providerAliasEndpoints(alias, kind string) []string {
	keys := []string{alias + "_" + kind + "_API_URL"}
	if alias == "TUNIU" {
		switch kind {
		case "LODGING":
			keys = append(keys, alias+"_HOTEL_API_URL")
		case "TRANSPORT":
			keys = append(keys, alias+"_FLIGHT_API_URL", alias+"_TRAIN_API_URL")
		case "SCENIC":
			keys = append(keys, alias+"_TICKET_API_URL")
		}
	}
	keys = append(keys, alias+"_API_URL", alias+"_PROVIDER_URL")
	seen := map[string]bool{}
	out := make([]string, 0, len(keys))
	for _, key := range keys {
		value := strings.TrimRight(strings.TrimSpace(os.Getenv(key)), "/")
		if value != "" && !seen[value] {
			seen[value] = true
			out = append(out, value)
		}
	}
	return out
}

func canonicalProviderLabel(alias string) string {
	switch strings.ToUpper(strings.TrimSpace(alias)) {
	case "ROLLINGGO":
		return "RollingGo"
	case "TUNIU":
		return "Tuniu"
	case "TRIPCOM":
		return "Trip.com"
	default:
		return alias
	}
}

func providerAliases(kind string) []string {
	switch kind {
	case "TRANSPORT":
		return []string{"TUNIU", "TRIPCOM"}
	case "LODGING":
		return []string{"TUNIU", "ROLLINGGO", "TRIPCOM"}
	case "DINING", "DESTINATION", "SCENIC":
		return []string{"TUNIU", "TRIPCOM"}
	default:
		return nil
	}
}

func firstEnv(keys ...string) string {
	for _, key := range keys {
		if value := strings.TrimSpace(os.Getenv(key)); value != "" {
			return value
		}
	}
	return ""
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}

func normalizeProviderCandidates(payload any, provider string) []contracts.Candidate {
	rows := findProviderRows(payload, 0)
	if len(rows) == 0 {
		return nil
	}
	out := make([]contracts.Candidate, 0, len(rows))
	for _, raw := range rows {
		row, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		name := firstString(row, "name", "title", "hotelName", "hotel_name", "scenicName", "scenic_name", "poi_name", "attractionName", "resName", "trainNum", "trainNo", "routeName", "route_name", "restaurant_name", "restaurantName")
		if name == "" {
			name = strings.TrimSpace(firstString(row, "airlineCompany", "airlineName") + " " + firstString(row, "flightNumber", "flightNo", "trainNum"))
		}
		if name == "" {
			continue
		}
		score := firstNumber(row, "commentScore", "rating", "starRating", "scenicSpotStar", "satisfaction", "rank_score", "score")
		if score > 5 && score <= 100 {
			score /= 100
		} else if score > 1 && score <= 5 {
			score /= 5
		}
		score = math.Max(0, math.Min(1, score))
		price := providerPrice(row)
		duration := normalizeProviderDuration(row)
		observedAt, observed := providerObservedAt(row)
		if !observed {
			observedAt = time.Now().UTC()
		}
		candidate := contracts.Candidate{ID: firstStringOrNumber(row, "id", "hotelId", "hotel_id", "productId", "product_id", "resId", "resourceId", "resource_id", "scenicId", "poiId", "poi_id", "attractionId", "attraction_id", "offerId", "offer_id", "flight_id", "train_id", "provider_id"), Name: name, Score: score, Price: price, Duration: duration, Tags: firstStrings(row, "tags", "labels", "features", "theme_tags"), Reasons: firstStrings(row, "reasons", "highlights", "selling_points"), Constraints: firstStrings(row, "constraints", "warnings", "notices"), Source: contracts.Source{Provider: provider, Retrieved: observedAt, Estimated: false}, Extra: map[string]any{}}
		candidate.Extra["observed_at_provided"] = observed
		if candidate.ID == "" {
			candidate.ID = uuid.NewString()
		}
		for _, key := range []string{"address", "location", "availability", "mode", "currency", "rating", "provider_id", "open_time", "openTime", "open_hours", "opening_hours", "openingHours", "check_in", "check_out", "room_type", "nightly_price", "nightlyPrice", "queue_minutes", "allergens", "cuisine", "carbon_kg", "carbon_kg_estimate", "transfer_buffer_minutes", "delay_risk", "commute_minutes", "crowd_level", "crowdLevel", "crowd_level_text", "crowdLevelText", "forecast_hour", "forecastHour", "temperature", "temp", "precipitation", "precipitation_probability", "condition", "weatherType", "weather_desc", "alert", "url", "link", "deep_link", "deepLink", "booking_url", "bookingUrl", "bookingLink", "imageUrl", "image_url", "starName", "brand", "areaCode", "departureTime", "departure_time", "arrivalTime", "arrival_time"} {
			if value, exists := row[key]; exists {
				if isProviderURLField(key) {
					if sanitized := sanitizeProviderURL(fmt.Sprint(value)); sanitized != "" {
						candidate.Extra[key] = sanitized
					}
					continue
				}
				candidate.Extra[key] = value
			}
		}
		candidate.FieldSources = map[string]string{
			"name":             provider,
			"price":            map[bool]string{true: provider, false: "unavailable"}[price > 0],
			"duration_minutes": map[bool]string{true: provider, false: "unavailable"}[duration > 0],
			"availability":     map[bool]string{true: provider, false: "unavailable"}[candidate.Extra["availability"] != nil],
		}
		candidate.Extra["field_sources"] = candidate.FieldSources
		annotateProviderEnumValidation(&candidate)
		out = append(out, candidate)
	}
	return out
}

// annotateProviderEnumValidation records canonical values for the small set
// of supplier enums that influence live decisions. Unknown values are kept
// for user visibility but marked unrecognized, so they cannot silently pass
// a fact gate as if they were a known status.
func annotateProviderEnumValidation(candidate *contracts.Candidate) {
	if candidate == nil {
		return
	}
	candidate.Extra = ensureCandidateExtra(candidate.Extra)
	validation := map[string]any{}
	for field, keys := range map[string][]string{
		"availability": {"availability", "available", "inventory"},
		"crowd_level":  {"crowd_level", "crowdLevel", "crowd_level_text", "crowdLevelText"},
		"condition":    {"condition", "weather", "weather_condition", "weatherType", "weather_desc"},
	} {
		var raw any
		found := false
		for _, key := range keys {
			if value, ok := candidate.Extra[key]; ok && value != nil && strings.TrimSpace(fmt.Sprint(value)) != "" {
				raw = value
				found = true
				break
			}
		}
		if !found {
			continue
		}
		text := strings.ToLower(strings.TrimSpace(fmt.Sprint(raw)))
		canonical, ok := canonicalProviderEnum(field, text)
		entry := map[string]any{"status": "valid", "normalized": canonical}
		if !ok {
			entry["status"] = "unrecognized"
			entry["normalized"] = ""
		}
		validation[field] = entry
	}
	if len(validation) > 0 {
		candidate.Extra["enum_validation"] = validation
	}
}

func canonicalProviderEnum(field, value string) (string, bool) {
	value = strings.NewReplacer(" ", "", "-", "_", "/", "_").Replace(value)
	value = strings.TrimSpace(value)
	sets := map[string]map[string]string{
		"availability": {
			"available": "available", "in_stock": "available", "on_sale": "available", "可用": "available", "有票": "available", "可预订": "available", "true": "available",
			"unavailable": "unavailable", "sold_out": "unavailable", "soldout": "unavailable", "无票": "unavailable", "售罄": "unavailable", "不可用": "unavailable", "false": "unavailable",
			"limited": "limited", "少量": "limited", "紧张": "limited",
			"unknown": "unknown", "未知": "unknown",
		},
		"crowd_level": {
			"low": "low", "低": "low", "少": "low", "不拥挤": "low",
			"medium": "medium", "moderate": "medium", "中": "medium", "一般": "medium",
			"high": "high", "高": "high", "拥挤": "high",
			"very_high": "very_high", "极高": "very_high", "爆满": "very_high",
			"unknown": "unknown", "未知": "unknown",
		},
		"condition": {
			"sunny": "sunny", "clear": "sunny", "晴": "sunny",
			"cloudy": "cloudy", "overcast": "cloudy", "多云": "cloudy", "阴": "cloudy",
			"rain": "rain", "rainy": "rain", "小雨": "rain", "中雨": "rain", "大雨": "rain",
			"snow": "snow", "snowy": "snow", "小雪": "snow", "大雪": "snow",
			"storm": "storm", "thunderstorm": "storm", "雷雨": "storm",
			"fog": "fog", "雾": "fog", "unknown": "unknown", "未知": "unknown",
		},
	}
	canonical, ok := sets[field][value]
	return canonical, ok
}

func isProviderURLField(key string) bool {
	switch strings.ToLower(strings.TrimSpace(key)) {
	case "url", "link", "deep_link", "booking_url", "bookingurl":
		return true
	default:
		return false
	}
}

// sanitizeProviderURL keeps supplier-provided links usable as public
// verification/deep links without allowing javascript URLs, embedded
// credentials, or common API credential query parameters to cross the
// provider boundary into the browser.
func sanitizeProviderURL(raw string) string {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || parsed.Host == "" || parsed.User != nil {
		return ""
	}
	if scheme := strings.ToLower(parsed.Scheme); scheme != "http" && scheme != "https" {
		return ""
	}
	query := parsed.Query()
	for key := range query {
		lower := strings.ToLower(strings.ReplaceAll(key, "-", "_"))
		if strings.Contains(lower, "token") || strings.Contains(lower, "secret") || strings.Contains(lower, "password") || strings.Contains(lower, "api_key") || strings.Contains(lower, "apikey") || strings.Contains(lower, "signature") {
			query.Del(key)
		}
	}
	parsed.RawQuery = query.Encode()
	return parsed.String()
}

// providerObservedAt extracts a supplier timestamp without guessing. A
// missing timestamp is treated as "retrieved now" for compatibility, but the
// candidate is explicitly marked so callers can distinguish it from a
// field-level observation supplied by the vendor.
func providerObservedAt(row map[string]any) (time.Time, bool) {
	for _, key := range []string{"observed_at", "observedAt", "retrieved_at", "retrievedAt", "updated_at", "updatedAt", "updatedTime", "collected_at", "data_time", "timestamp"} {
		value := strings.TrimSpace(firstString(row, key))
		if value == "" {
			if number, ok := toFloat(row[key]); ok && number > 0 {
				if number > 1e12 {
					number /= 1000
				}
				return time.Unix(int64(number), 0).UTC(), true
			}
			continue
		}
		if parsed, err := time.Parse(time.RFC3339, value); err == nil {
			return parsed.UTC(), true
		}
		if parsed, err := time.Parse("2006-01-02 15:04:05", value); err == nil {
			return parsed.UTC(), true
		}
	}
	return time.Time{}, false
}

// annotateCandidates gives every recommendation the same evidence contract.
// It is intentionally derived only from fields present in the candidate and
// its source, so estimated fallbacks remain visibly estimated.
func annotateCandidates(candidates []contracts.Candidate, domain string) []contracts.Candidate {
	now := time.Now().UTC()
	for i := range candidates {
		candidate := &candidates[i]
		if candidate.Source.Provider == "" {
			candidate.Source.Provider = "unknown"
		}
		if candidate.Source.Retrieved.IsZero() {
			candidate.Source.Retrieved = now
		}
		age := int(now.Sub(candidate.Source.Retrieved).Seconds())
		if age < 0 {
			age = 0
		}
		candidate.FreshnessSeconds = age
		candidate.Extra = ensureCandidateExtra(candidate.Extra)
		attachPublicVerificationLinks(candidate, domain)
		freshnessLimit := providerFreshnessLimitSeconds(domain)
		candidate.Extra["freshness_limit_seconds"] = freshnessLimit
		candidate.Extra["freshness_status"] = freshnessStatus(age, freshnessLimit, candidate.Source.Estimated)
		if candidate.Confidence <= 0 {
			candidate.Confidence = 0.82
			if candidate.Source.Estimated {
				candidate.Confidence = 0.45
			}
		}
		if candidate.Confidence > 1 {
			candidate.Confidence = 1
		}
		if len(candidate.Evidence) == 0 {
			candidate.Evidence = []contracts.Evidence{{Field: "candidate", Value: domain, Source: candidate.Source.Provider, ObservedAt: candidate.Source.Retrieved, Estimated: candidate.Source.Estimated}}
			if candidate.Price > 0 {
				candidate.Evidence = append(candidate.Evidence, contracts.Evidence{Field: "price", Value: candidate.Price, Source: fieldSource(candidate, "price"), ObservedAt: candidate.Source.Retrieved, Estimated: candidate.Source.Estimated})
			}
			if candidate.Duration > 0 {
				candidate.Evidence = append(candidate.Evidence, contracts.Evidence{Field: "duration_minutes", Value: candidate.Duration, Source: fieldSource(candidate, "duration_minutes"), ObservedAt: candidate.Source.Retrieved, Estimated: candidate.Source.Estimated})
			}
		}
		if candidate.FieldSources == nil {
			candidate.FieldSources = map[string]string{}
		}
		candidate.Extra = ensureCandidateExtra(candidate.Extra)
		candidate.Extra["freshness_seconds"] = candidate.FreshnessSeconds
		candidate.Extra["confidence"] = candidate.Confidence
		annotateEvidenceContract(candidate)
		annotateDecisionGate(candidate, domain)
	}
	for i := range candidates {
		for j := range candidates {
			if i == j || len(candidates[i].Alternatives) >= 3 {
				continue
			}
			candidates[i].Alternatives = append(candidates[i].Alternatives, contracts.CandidateRef{ID: candidates[j].ID, Name: candidates[j].Name, Reason: "同类候选，可在硬约束校验后替换"})
		}
	}
	return candidates
}

// annotateEvidenceContract is the final supplier boundary before ranking or
// route selection. A candidate can still be displayed when evidence is weak,
// but fact_usable=false prevents downstream code from treating estimated or
// incomplete fields as live supplier facts.
func annotateEvidenceContract(candidate *contracts.Candidate) {
	issues := make([]string, 0, 4)
	if strings.TrimSpace(candidate.Source.Provider) == "" || candidate.Source.Provider == "unknown" {
		issues = append(issues, "source_provider_missing")
	}
	if candidate.Source.Retrieved.IsZero() {
		issues = append(issues, "source_timestamp_missing")
	}
	if candidate.Source.Estimated {
		issues = append(issues, "estimated_source")
	}
	if candidate.Price > 0 && strings.TrimSpace(fieldSource(candidate, "price")) == "" {
		issues = append(issues, "price_source_missing")
	}
	if candidate.Duration > 0 && strings.TrimSpace(fieldSource(candidate, "duration_minutes")) == "" {
		issues = append(issues, "duration_source_missing")
	}
	candidate.Extra["evidence_contract"] = "candidate-evidence-v1"
	candidate.Extra["evidence_issues"] = issues
	candidate.Extra["fact_usable"] = len(issues) == 0
}

func annotatePlanningCandidates(candidates []contracts.Candidate, domain string, ctx contracts.PlanningContext) []contracts.Candidate {
	annotated := annotateCandidates(candidates, domain)
	attachContextVerificationLinks(annotated, domain, ctx)
	for index := range annotated {
		annotated[index].Extra = ensureCandidateExtra(annotated[index].Extra)
		annotated[index].Extra["source_priority"] = providerSourcePriority(annotated[index].Source.Provider)
	}
	// Keep provider-verified candidates ahead of advisory/fallback candidates,
	// while retaining every option for transparent UI comparison and official
	// channel verification.
	sort.SliceStable(annotated, func(i, j int) bool {
		left, _ := annotated[i].Extra["decision_eligible"].(bool)
		right, _ := annotated[j].Extra["decision_eligible"].(bool)
		if left != right {
			return left && !right
		}
		leftPriority := intValue(annotated[i].Extra["source_priority"])
		rightPriority := intValue(annotated[j].Extra["source_priority"])
		if leftPriority != rightPriority {
			return leftPriority > rightPriority
		}
		return false
	})
	return annotated
}

// providerSourcePriority is an explicit operator policy, not an implicit
// trust claim. Configure comma-separated entries such as
// `RollingGo:100,Amap:80`; absent entries remain at zero and retain stable
// arrival order. The raw setting never leaves the server.
func providerSourcePriority(provider string) int {
	target := strings.ToLower(strings.TrimSpace(provider))
	if target == "" {
		return 0
	}
	raw := strings.TrimSpace(os.Getenv("PLANNING_PROVIDER_PRIORITY"))
	if raw == "" {
		return 0
	}
	for _, entry := range strings.Split(raw, ",") {
		parts := strings.SplitN(entry, ":", 2)
		if len(parts) != 2 || !strings.EqualFold(strings.TrimSpace(parts[0]), target) {
			continue
		}
		value, err := strconv.Atoi(strings.TrimSpace(parts[1]))
		if err == nil {
			if value < 0 {
				return 0
			}
			if value > 1000 {
				return 1000
			}
			return value
		}
	}
	return 0
}

// decisionRequiredFieldGroups defines the minimum supplier facts needed before
// a candidate can influence a real-time fact decision. Groups are OR-ed within
// each entry; every entry must have at least one field. This keeps the gate
// strict about domain evidence without requiring every vendor to expose the
// same optional schema.
func decisionRequiredFieldGroups(domain string) [][]string {
	switch strings.ToLower(strings.TrimSpace(domain)) {
	case "transport":
		return [][]string{{"price", "duration_minutes", "availability", "departure_time", "arrival_time"}}
	case "lodging":
		return [][]string{{"price", "availability", "room_type"}}
	case "scenic", "destination", "scenic_micro":
		return [][]string{{"opening_hours", "crowd_level", "booking_url"}}
	case "dining":
		return [][]string{{"price", "opening_hours", "cuisine"}}
	case "weather":
		return [][]string{{"condition", "temperature", "precipitation", "alert"}}
	case "crowd":
		return [][]string{{"crowd_level", "score", "forecast_hour"}}
	default:
		return [][]string{{"price", "duration_minutes", "availability"}}
	}
}

func decisionCandidateHasField(candidate *contracts.Candidate, field string) bool {
	if field == "price" {
		return candidate.Price > 0
	}
	if field == "duration_minutes" {
		return candidate.Duration > 0
	}
	if field == "departure_time" || field == "arrival_time" {
		if candidate.Extra == nil {
			return false
		}
		keys := map[string][]string{"departure_time": {"departureTime", "departure_time"}, "arrival_time": {"arrivalTime", "arrival_time"}}
		for _, key := range keys[field] {
			if value, ok := candidate.Extra[key]; ok && strings.TrimSpace(fmt.Sprint(value)) != "" {
				return true
			}
		}
		return false
	}
	if !candidateEnumFieldUsable(candidate, field) {
		return false
	}
	return providerQualityHasField(*candidate, field)
}

func candidateEnumFieldUsable(candidate *contracts.Candidate, field string) bool {
	if candidate == nil || candidate.Extra == nil {
		return true
	}
	validation, ok := candidate.Extra["enum_validation"].(map[string]any)
	if !ok {
		return true
	}
	entry, ok := validation[field].(map[string]any)
	if !ok {
		return true
	}
	return strings.TrimSpace(fmt.Sprint(entry["status"])) != "unrecognized"
}

func annotateDecisionGate(candidate *contracts.Candidate, domain string) {
	issues := make([]string, 0, 4)
	if candidate.Source.Estimated {
		issues = append(issues, "estimated_source")
	}
	if candidate.Source.Retrieved.IsZero() {
		issues = append(issues, "source_timestamp_missing")
	}
	if candidate.Extra == nil || candidate.Extra["observed_at_provided"] != true {
		issues = append(issues, "observed_at_missing")
	}
	if status := strings.TrimSpace(fmt.Sprint(candidate.Extra["freshness_status"])); status != "fresh" {
		if status == "stale" {
			issues = append(issues, "source_stale")
		} else if status != "<nil>" && status != "" {
			issues = append(issues, "freshness_not_verified")
		}
	}
	for index, group := range decisionRequiredFieldGroups(domain) {
		matched := false
		for _, field := range group {
			if decisionCandidateHasField(candidate, field) {
				matched = true
				break
			}
		}
		if !matched {
			issues = append(issues, fmt.Sprintf("required_fields_missing_group_%d", index+1))
		}
	}
	if validation, ok := candidate.Extra["enum_validation"].(map[string]any); ok {
		for field, raw := range validation {
			entry, ok := raw.(map[string]any)
			if ok && strings.TrimSpace(fmt.Sprint(entry["status"])) == "unrecognized" {
				issues = append(issues, "enum_unrecognized_"+field)
			}
		}
	}
	candidate.Extra["decision_gate_contract"] = "provider-decision-gate-v1"
	candidate.Extra["decision_gate_reasons"] = issues
	candidate.Extra["decision_eligible"] = len(issues) == 0
	// public_sources is an API boundary value and may have been decoded from
	// JSON as []any (or may be absent entirely). Never assert its concrete type
	// directly here: a malformed/legacy candidate must be blocked gracefully,
	// not panic the planning request.
	candidate.Extra["official_fallback_available"] = hasPublicVerificationSources(candidate.Extra["public_sources"])
}

func hasPublicVerificationSources(raw any) bool {
	switch links := raw.(type) {
	case []map[string]string:
		for _, link := range links {
			if strings.TrimSpace(link["url"]) != "" {
				return true
			}
		}
		return false
	case []any:
		for _, value := range links {
			switch link := value.(type) {
			case map[string]string:
				if strings.TrimSpace(link["url"]) != "" {
					return true
				}
			case map[string]any:
				if strings.TrimSpace(fmt.Sprint(link["url"])) != "" && strings.TrimSpace(fmt.Sprint(link["url"])) != "<nil>" {
					return true
				}
			}
		}
		return false
	case map[string]string:
		return strings.TrimSpace(links["url"]) != ""
	case map[string]any:
		value := strings.TrimSpace(fmt.Sprint(links["url"]))
		return value != "" && value != "<nil>"
	default:
		return false
	}
}

// attachContextVerificationLinks adds query context only to the outbound
// verification URL. It does not persist the query or treat the destination
// page as a confirmed quote/order.
func attachContextVerificationLinks(candidates []contracts.Candidate, domain string, ctx contracts.PlanningContext) {
	origin := strings.TrimSpace(ctx.Origin)
	destination := strings.TrimSpace(ctx.Destination)
	if origin == "" && destination == "" {
		return
	}
	for i := range candidates {
		candidate := &candidates[i]
		candidate.Extra = ensureCandidateExtra(candidate.Extra)
		query := strings.TrimSpace(candidate.Name + " " + destination)
		if strings.EqualFold(domain, "transport") && origin != "" {
			query = strings.TrimSpace(origin + " 到 " + destination + " " + candidate.Name)
		}
		encodedQuery := url.QueryEscape(query)
		links := []map[string]string{}
		if raw, ok := candidate.Extra["public_sources"].([]map[string]string); ok {
			links = append(links, raw...)
		}
		if len(links) == 0 {
			links = append(links, map[string]string{"label": "高德地图核验", "url": "https://www.amap.com/search?query=" + encodedQuery})
		}
		contextLink := map[string]string{"label": "按当前行程核验", "url": "https://www.bing.com/search?q=" + encodedQuery}
		switch strings.ToLower(strings.TrimSpace(domain)) {
		case "transport":
			contextLink = map[string]string{"label": "按出发地目的地核验", "url": "https://www.amap.com/dir?from=" + url.QueryEscape(origin) + "&to=" + url.QueryEscape(destination)}
		case "lodging":
			contextLink = map[string]string{"label": "按目的地核验住宿", "url": "https://www.trip.com/hotels/list?searchWord=" + url.QueryEscape(destination)}
		}
		links = append([]map[string]string{contextLink}, links...)
		candidate.Extra["public_sources"] = links
		candidate.Extra["verification_url"] = contextLink["url"]
		candidate.Extra["verification_label"] = contextLink["label"]
	}
}

// attachPublicVerificationLinks provides a transparent web fallback when a
// commercial provider cannot return a quote. These are search/detail links,
// not claims of live inventory or price. The client must label them as
// user-verification channels and never treat them as booked orders.
func attachPublicVerificationLinks(candidate *contracts.Candidate, domain string) {
	query := url.QueryEscape(strings.TrimSpace(candidate.Name))
	if query == "" {
		return
	}
	links := []map[string]string{{"label": "高德地图核验", "url": "https://www.amap.com/search?query=" + query}}
	switch strings.ToLower(strings.TrimSpace(domain)) {
	case "transport":
		links = append(links,
			map[string]string{"label": "12306 官方核验", "url": "https://www.12306.cn/index/"},
			map[string]string{"label": "Trip.com 航班核验", "url": "https://www.trip.com/flights/"},
		)
	case "lodging":
		links = append(links, map[string]string{"label": "Trip.com 酒店核验", "url": "https://www.trip.com/hotels/list?searchWord=" + query})
	case "dining", "destination", "scenic":
		links = append(links, map[string]string{"label": "公开网页搜索", "url": "https://www.bing.com/search?q=" + query})
	}
	if existing, ok := candidate.Extra["public_sources"].([]map[string]string); ok && len(existing) > 0 {
		return
	}
	candidate.Extra["public_sources"] = links
	if _, exists := candidate.Extra["verification_url"]; !exists {
		candidate.Extra["verification_url"] = links[0]["url"]
	}
	if _, exists := candidate.Extra["verification_label"]; !exists {
		candidate.Extra["verification_label"] = "打开公开渠道核验"
	}
}

func ensureCandidateExtra(extra map[string]any) map[string]any {
	if extra == nil {
		return map[string]any{}
	}
	return extra
}

func providerFreshnessLimitSeconds(domain string) int {
	switch strings.ToLower(strings.TrimSpace(domain)) {
	case "transport":
		return 300
	case "lodging":
		return 900
	case "weather", "crowd":
		return 900
	case "scenic", "dining":
		return 3600
	default:
		return 86400
	}
}

func freshnessStatus(age, limit int, estimated bool) string {
	if estimated {
		return "estimated"
	}
	if age > limit {
		return "stale"
	}
	return "fresh"
}

func fieldSource(candidate *contracts.Candidate, field string) string {
	if candidate.FieldSources != nil {
		if source := strings.TrimSpace(candidate.FieldSources[field]); source != "" {
			return source
		}
	}
	return candidate.Source.Provider
}

func findProviderRows(payload any, depth int) []any {
	if depth > 3 {
		return nil
	}
	if rows, ok := payload.([]any); ok {
		return rows
	}
	root, ok := payload.(map[string]any)
	if !ok {
		return nil
	}
	for _, key := range []string{"options", "results", "items", "offers", "hotels", "hotelInformationList", "hotelList", "routes", "restaurants", "destinations", "pois"} {
		if list, ok := root[key].([]any); ok {
			return list
		}
	}
	// Supplier gateways commonly wrap the result more than once (for example
	// {data:{result:{options:[]}}}). Search each known wrapper in order so a
	// metadata-only `data` object cannot hide a valid `result` sibling.
	for _, key := range []string{"data", "result", "response"} {
		if nested, ok := root[key]; ok {
			if rows := findProviderRows(nested, depth+1); len(rows) > 0 {
				return rows
			}
		}
	}
	return nil
}

func normalizeProviderDuration(row map[string]any) int {
	if seconds := firstNumber(row, "duration_seconds", "duration_sec", "travel_seconds", "travelDurationSeconds"); seconds > 0 {
		return int(math.Round(seconds / 60))
	}
	minutes := firstNumber(row, "duration_minutes", "durationMin", "travel_minutes", "travelTimeMinutes", "time_minutes")
	if minutes <= 0 {
		minutes = firstNumber(row, "duration")
		// Legacy suppliers use duration as seconds. Values over a day are
		// unambiguously seconds; ordinary values retain the historical minutes
		// interpretation for compatibility.
		if minutes > 86400 {
			minutes /= 60
		}
	}
	if minutes <= 0 {
		// Compact or Chinese durations observed from real vendors: "2h5m",
		// "18时46分", "3.0-4.0HOUR", "1天".
		if text := firstString(row, "totalDuration", "duration", "flyTime", "travelTime", "travel_time"); text != "" {
			if parsed := parseDurationString(text); parsed > 0 {
				return parsed
			}
		}
	}
	if minutes <= 0 {
		return 0
	}
	return int(math.Round(minutes))
}

var durationRe = regexp.MustCompile(`(\d+(?:\.\d+)?)\s*(天|日|时|小时|h|hr|hour|分|分钟|min|m|秒|s|sec|d|day)`)

func parseDurationString(text string) int {
	matches := durationRe.FindAllStringSubmatch(strings.ToLower(strings.TrimSpace(text)), -1)
	if len(matches) == 0 {
		return 0
	}
	total := 0.0
	for _, match := range matches {
		if len(match) < 3 {
			continue
		}
		value, err := strconv.ParseFloat(match[1], 64)
		if err != nil {
			continue
		}
		switch match[2] {
		case "天", "日", "d", "day":
			total += value * 1440
		case "时", "小时", "h", "hr", "hour":
			total += value * 60
		case "分", "分钟", "m", "min":
			total += value
		case "秒", "s", "sec":
			total += value / 60
		}
	}
	if total <= 0 {
		return 0
	}
	return int(math.Round(total))
}

// firstStringOrNumber returns the first matching field as a string, accepting
// both string and numeric identifiers (e.g. hotelId/productId returned as
// JSON numbers by real vendors).
func firstStringOrNumber(row map[string]any, keys ...string) string {
	for _, key := range keys {
		switch value := row[key].(type) {
		case string:
			if strings.TrimSpace(value) != "" {
				return strings.TrimSpace(value)
			}
		case float64:
			if value != 0 {
				return strconv.FormatFloat(value, 'f', -1, 64)
			}
		case int:
			if value != 0 {
				return strconv.Itoa(value)
			}
		case int64:
			if value != 0 {
				return strconv.FormatInt(value, 10)
			}
		case json.Number:
			return value.String()
		}
	}
	return ""
}

// providerPrice normalizes the many real price shapes observed: flat numbers,
// nested {price:{lowestPrice}}, flight {basePrice + totalTax}, and multi-seat
// train fare blocks where the lowest positive fare is the useful value.
func providerPrice(row map[string]any) float64 {
	if p := firstNumber(row, "lowestPrice", "lowest_price", "minPrice", "min_price", "salePrice", "sale_price", "startPrice", "start_price", "displayPrice", "display_price", "price"); p > 0 {
		return p
	}
	if nested, ok := row["price"].(map[string]any); ok {
		if p := firstNumber(nested, "lowestPrice", "minPrice", "salePrice", "amount"); p > 0 {
			return p
		}
		best := math.MaxFloat64
		for _, value := range nested {
			if n, ok := toFloat(value); ok && n > 0 && n < best {
				best = n
			}
		}
		if best != math.MaxFloat64 {
			return best
		}
	}
	if base := firstNumber(row, "basePrice", "base_price"); base > 0 {
		return base + firstNumber(row, "totalTax", "total_tax")
	}
	return firstNumber(row, "fare", "cost", "amount", "totalPrice", "total_price", "nightlyPrice", "nightly_price")
}

func toFloat(value any) (float64, bool) {
	switch v := value.(type) {
	case float64:
		return v, !math.IsNaN(v) && !math.IsInf(v, 0)
	case float32:
		return float64(v), true
	case int:
		return float64(v), true
	case int64:
		return float64(v), true
	case json.Number:
		f, err := v.Float64()
		return f, err == nil
	case string:
		cleaned := strings.NewReplacer(",", "", "¥", "", "$", "").Replace(v)
		f, err := strconv.ParseFloat(strings.TrimSpace(cleaned), 64)
		return f, err == nil
	}
	return 0, false
}

func firstString(row map[string]any, keys ...string) string {
	for _, key := range keys {
		if value, ok := row[key].(string); ok && strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}
func firstNumber(row map[string]any, keys ...string) float64 {
	for _, key := range keys {
		switch value := row[key].(type) {
		case float64:
			if !math.IsNaN(value) && !math.IsInf(value, 0) {
				return value
			}
		case float32:
			return float64(value)
		case int:
			return float64(value)
		case int64:
			return float64(value)
		case int32:
			return float64(value)
		case json.Number:
			if parsed, err := value.Float64(); err == nil {
				return parsed
			}
		case string:
			cleaned := strings.NewReplacer(",", "", "¥", "", "$", "").Replace(value)
			if parsed, err := strconv.ParseFloat(strings.TrimSpace(cleaned), 64); err == nil {
				return parsed
			}
		}
	}
	return 0
}
func firstStrings(row map[string]any, keys ...string) []string {
	for _, key := range keys {
		switch value := row[key].(type) {
		case []any:
			out := []string{}
			for _, item := range value {
				if text, ok := item.(string); ok && strings.TrimSpace(text) != "" {
					out = append(out, strings.TrimSpace(text))
				}
			}
			return out
		case []string:
			return value
		case string:
			if strings.TrimSpace(value) != "" {
				return []string{strings.TrimSpace(value)}
			}
		}
	}
	return []string{}
}

func providerName(kind, fallback string) string {
	if name := strings.TrimSpace(os.Getenv(kind + "_PROVIDER_NAME")); name != "" {
		return name
	}
	return fallback
}

func planningProviderHealth() map[string]any {
	result := map[string]any{}
	for _, kind := range []string{"DESTINATION", "SCENIC", "TRANSPORT", "LODGING", "DINING", "WEATHER", "CROWD"} {
		url, token, label := resolveProviderConfig(kind)
		result[strings.ToLower(kind)] = map[string]any{"configured": url != "", "endpoint_count": len(resolveProviderConfigs(kind)), "provider": firstNonEmpty(label, providerName(kind, url)), "authenticated": token != ""}
	}
	return result
}

func probePlanningProvider(ctx context.Context, kind string) map[string]any {
	key := strings.ToUpper(strings.TrimSpace(kind))
	base, token, label := resolveProviderConfig(key)
	result := map[string]any{"configured": base != "", "provider": firstNonEmpty(label, providerName(key, base)), "authenticated": token != ""}
	if base == "" {
		result["reachable"] = false
		result["status"] = "not_configured"
		return result
	}
	reqCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	client := &http.Client{Timeout: 2 * time.Second}
	authHeader := mcpAuthHeader(key, firstNonEmpty(label, providerName(key, base)), base)
	doProbe := func(method string) (*http.Response, error) {
		req, err := http.NewRequestWithContext(reqCtx, method, base, nil)
		if err != nil {
			return nil, err
		}
		if token != "" {
			if authHeader == "apiKey" {
				req.Header.Set(authHeader, token)
			} else {
				req.Header.Set(authHeader, "Bearer "+token)
			}
		}
		return client.Do(req)
	}
	resp, err := doProbe(http.MethodHead)
	if err != nil {
		result["reachable"] = false
		result["status"] = "unreachable"
		return result
	}
	// HEAD is preferred because it avoids response bodies, but many supplier
	// gateways reject it. A read-only GET is a safe compatibility fallback.
	if resp.StatusCode == http.StatusMethodNotAllowed || resp.StatusCode == http.StatusNotImplemented {
		_ = resp.Body.Close()
		resp, err = doProbe(http.MethodGet)
		if err != nil {
			result["reachable"] = false
			result["status"] = "unreachable"
			return result
		}
	}
	_ = resp.Body.Close()
	result["reachable"] = resp.StatusCode >= 200 && resp.StatusCode < 500
	result["status_code"] = resp.StatusCode
	result["status"] = map[bool]string{true: "reachable", false: "unreachable"}[result["reachable"].(bool)]
	return result
}

func fetchPlanningSignal(ctx context.Context, kind string, input contracts.PlanningContext) (map[string]any, contracts.Source, bool) {
	key := strings.ToUpper(strings.TrimSpace(kind))
	configs := resolveProviderConfigs(key)
	if len(configs) == 0 {
		base := strings.TrimRight(strings.TrimSpace(os.Getenv(key+"_PROVIDER_URL")), "/")
		if base != "" {
			configs = []providerConfig{{base: base, token: strings.TrimSpace(os.Getenv(key + "_PROVIDER_TOKEN")), label: providerName(key, base)}}
		}
	}
	if len(configs) == 0 {
		return nil, contracts.Source{}, false
	}
	for _, config := range configs {
		base, token := config.base, config.token
		provider := config.label
		if provider == "" {
			provider = providerName(key, base)
		}
		if mcpEndpoint(base) || mcpConfiguredProtocol(key, provider) {
			tools, err := discoverMCPTools(ctx, base, token, mcpAuthHeader(key, provider, base))
			if err != nil {
				continue
			}
			tool := chooseMCPTool(provider, tools, key)
			if tool.Name == "" {
				continue
			}
			// Signal lookups are part of the read-only planning path. A vendor
			// may advertise mutation tools beside weather/crowd tools, but a
			// malformed or request-only schema must never be invoked here.
			if audit := mcpToolSchemaAudit(tool, key); audit["status"] != "ready" {
				continue
			}
			args := mcpArgumentsForTool(tool, input, key)
			if len(mcpRequiredProperties(tool, args)) > 0 {
				continue
			}
			payload, err := callMCPTool(ctx, base, token, mcpAuthHeader(key, provider, base), tool.Name, args)
			if err != nil {
				continue
			}
			if result, ok := payload.(map[string]any); ok && len(result) > 0 {
				return result, contracts.Source{Provider: provider, Retrieved: time.Now().UTC(), Estimated: false}, true
			}
			continue
		}
		body, err := json.Marshal(supplierContext(input))
		if err != nil {
			return nil, contracts.Source{}, false
		}
		reqCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
		defer cancel()
		req, err := http.NewRequestWithContext(reqCtx, http.MethodPost, base, bytes.NewReader(body))
		if err != nil {
			return nil, contracts.Source{}, false
		}
		req.Header.Set("Content-Type", "application/json")
		if token != "" {
			authHeader := mcpAuthHeader(key, provider, base)
			req.Header.Set(authHeader, mcpAuthValue(authHeader, token))
		}
		resp, err := (&http.Client{Timeout: 3 * time.Second}).Do(req)
		if err != nil {
			return nil, contracts.Source{}, false
		}
		defer resp.Body.Close()
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			return nil, contracts.Source{}, false
		}
		raw, err := io.ReadAll(io.LimitReader(resp.Body, 512*1024))
		if err != nil {
			return nil, contracts.Source{}, false
		}
		var payload map[string]any
		if json.Unmarshal(raw, &payload) != nil {
			return nil, contracts.Source{}, false
		}
		if data, ok := payload["data"].(map[string]any); ok {
			payload = data
		}
		if len(payload) == 0 {
			return nil, contracts.Source{}, false
		}
		return payload, contracts.Source{Provider: provider, Retrieved: time.Now().UTC(), Estimated: false}, true
	}
	return nil, contracts.Source{}, false
}
