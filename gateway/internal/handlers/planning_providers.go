package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"math"
	"net/http"
	"os"
	"regexp"
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
	for _, config := range configs {
		provider := config.label
		if provider == "" {
			provider = providerName(key, config.base)
		}
		options, ok := fetchOnePlanningProvider(ctx, key, input, config.base, config.token, provider)
		if ok {
			all = append(all, options...)
		}
	}
	if len(all) == 0 {
		return nil, contracts.Source{}, false
	}
	return all, all[0].Source, true
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
		name := firstString(row, "name", "title", "hotelName", "hotel_name", "scenicName", "scenic_name", "resName", "trainNum", "trainNo", "routeName", "route_name", "restaurant_name")
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
		candidate := contracts.Candidate{ID: firstStringOrNumber(row, "id", "hotelId", "hotel_id", "productId", "product_id", "resId", "resourceId", "resource_id", "scenicId", "offerId", "offer_id", "provider_id"), Name: name, Score: score, Price: price, Duration: duration, Tags: firstStrings(row, "tags", "labels", "features"), Reasons: firstStrings(row, "reasons", "highlights"), Constraints: firstStrings(row, "constraints", "warnings"), Source: contracts.Source{Provider: provider, Retrieved: time.Now().UTC(), Estimated: false}, Extra: map[string]any{}}
		if candidate.ID == "" {
			candidate.ID = uuid.NewString()
		}
		for _, key := range []string{"address", "location", "availability", "mode", "currency", "rating", "provider_id", "open_time", "check_in", "check_out", "room_type", "nightly_price", "queue_minutes", "allergens", "cuisine", "carbon_kg", "carbon_kg_estimate", "transfer_buffer_minutes", "delay_risk", "commute_minutes", "crowd_level", "url", "link", "deep_link", "booking_url", "bookingUrl", "imageUrl", "image_url", "starName", "brand", "areaCode", "departureTime", "arrivalTime"} {
			if value, exists := row[key]; exists {
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
		out = append(out, candidate)
	}
	return out
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
		if candidate.Extra == nil {
			candidate.Extra = map[string]any{}
		}
		candidate.Extra["freshness_seconds"] = candidate.FreshnessSeconds
		candidate.Extra["confidence"] = candidate.Confidence
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
	if seconds := firstNumber(row, "duration_seconds", "duration_sec", "travel_seconds"); seconds > 0 {
		return int(math.Round(seconds / 60))
	}
	minutes := firstNumber(row, "duration_minutes", "travel_minutes")
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
	if p := firstNumber(row, "lowestPrice", "minPrice", "salePrice", "startPrice", "price"); p > 0 {
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
	return firstNumber(row, "fare", "cost", "amount", "nightlyPrice", "nightly_price")
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
