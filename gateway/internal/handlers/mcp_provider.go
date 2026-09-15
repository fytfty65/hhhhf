package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"sync/atomic"
	"time"

	"gateway/internal/contracts"
)

var mcpRequestID uint64

type mcpRPCResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      any             `json:"id"`
	Result  json.RawMessage `json:"result"`
	Error   *struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
	} `json:"error,omitempty"`
}

type mcpTool struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	InputSchema map[string]any `json:"inputSchema,omitempty"`
}

// fetchMCPProvider speaks the MCP Streamable HTTP JSON-RPC flow. It keeps the
// protocol details inside the gateway and returns the same candidate contract
// used by ordinary REST suppliers.
func fetchMCPProvider(ctx context.Context, endpoint, token, authHeader, provider, kind string, input contracts.PlanningContext) ([]contracts.Candidate, bool) {
	client := &http.Client{Timeout: 20 * time.Second}
	session := ""
	call := func(method string, params any, notify bool) (json.RawMessage, bool) {
		id := any(nil)
		if !notify {
			id = atomic.AddUint64(&mcpRequestID, 1)
		}
		requestBody := map[string]any{"jsonrpc": "2.0", "method": method, "params": params}
		if id != nil {
			requestBody["id"] = id
		}
		body, err := json.Marshal(requestBody)
		if err != nil {
			return nil, false
		}
		reqCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
		defer cancel()
		req, err := http.NewRequestWithContext(reqCtx, http.MethodPost, endpoint, bytes.NewReader(body))
		if err != nil {
			return nil, false
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		req.Header.Set("MCP-Protocol-Version", "2025-06-18")
		if session != "" {
			req.Header.Set("Mcp-Session-Id", session)
		}
		if token != "" {
			req.Header.Set(authHeader, mcpAuthValue(authHeader, token))
		}
		resp, err := client.Do(req)
		if err != nil {
			return nil, false
		}
		defer resp.Body.Close()
		if value := resp.Header.Get("Mcp-Session-Id"); value != "" {
			session = value
		}
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			return nil, false
		}
		raw, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
		if err != nil {
			return nil, false
		}
		if notify {
			return nil, true
		}
		decoded, ok := decodeMCPJSON(raw, resp.Header.Get("Content-Type"))
		if !ok {
			return nil, false
		}
		var envelope mcpRPCResponse
		if json.Unmarshal(decoded, &envelope) != nil || envelope.Error != nil {
			return nil, false
		}
		return envelope.Result, true
	}

	if _, ok := call("initialize", map[string]any{"protocolVersion": "2025-06-18", "capabilities": map[string]any{}, "clientInfo": map[string]any{"name": "omniroute-gateway", "version": "2026-09-04"}}, false); !ok {
		return nil, false
	}
	if _, ok := call("notifications/initialized", map[string]any{}, true); !ok {
		return nil, false
	}
	toolsRaw, ok := call("tools/list", map[string]any{}, false)
	if !ok {
		return nil, false
	}
	var listed struct {
		Tools []mcpTool `json:"tools"`
	}
	if json.Unmarshal(toolsRaw, &listed) != nil || len(listed.Tools) == 0 {
		return nil, false
	}
	tool := chooseMCPTool(provider, listed.Tools, kind)
	if tool.Name == "" {
		return nil, false
	}
	arguments := mcpArgumentsForTool(tool, input, kind)
	if missing := mcpRequiredProperties(tool, arguments); len(missing) > 0 {
		return nil, false
	}
	toolResult, ok := call("tools/call", map[string]any{"name": tool.Name, "arguments": arguments}, false)
	if !ok {
		return nil, false
	}
	payload := extractMCPToolPayload(toolResult)
	if payload == nil {
		return nil, false
	}
	options := normalizeProviderCandidates(payload, provider)
	return options, len(options) > 0
}

// discoverMCPTools performs only the MCP handshake and tools/list call. It is
// used by the operator diagnostics endpoint to inspect a vendor schema before
// enabling production traffic.
func discoverMCPTools(ctx context.Context, endpoint, token, authHeader string) ([]mcpTool, error) {
	client := &http.Client{Timeout: 20 * time.Second}
	session := ""
	call := func(method string, params any, notify bool) (json.RawMessage, error) {
		id := atomic.AddUint64(&mcpRequestID, 1)
		body, err := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": id, "method": method, "params": params})
		if err != nil {
			return nil, err
		}
		reqCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
		defer cancel()
		req, err := http.NewRequestWithContext(reqCtx, http.MethodPost, endpoint, bytes.NewReader(body))
		if err != nil {
			return nil, err
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		req.Header.Set("MCP-Protocol-Version", "2025-06-18")
		if session != "" {
			req.Header.Set("Mcp-Session-Id", session)
		}
		if token != "" {
			req.Header.Set(authHeader, mcpAuthValue(authHeader, token))
		}
		resp, err := client.Do(req)
		if err != nil {
			return nil, err
		}
		defer resp.Body.Close()
		if value := resp.Header.Get("Mcp-Session-Id"); value != "" {
			session = value
		}
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			return nil, fmt.Errorf("mcp_http_%d", resp.StatusCode)
		}
		raw, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
		if err != nil {
			return nil, err
		}
		if notify {
			return nil, nil
		}
		decoded, ok := decodeMCPJSON(raw, resp.Header.Get("Content-Type"))
		if !ok {
			return nil, fmt.Errorf("mcp_invalid_json")
		}
		var envelope mcpRPCResponse
		if json.Unmarshal(decoded, &envelope) != nil {
			return nil, fmt.Errorf("mcp_invalid_response")
		}
		if envelope.Error != nil {
			return nil, fmt.Errorf("mcp_%d", envelope.Error.Code)
		}
		return envelope.Result, nil
	}
	if _, err := call("initialize", map[string]any{"protocolVersion": "2025-06-18", "capabilities": map[string]any{}, "clientInfo": map[string]any{"name": "omniroute-gateway", "version": "2026-09-04"}}, false); err != nil {
		return nil, err
	}
	if _, err := call("notifications/initialized", map[string]any{}, true); err != nil {
		return nil, err
	}
	result, err := call("tools/list", map[string]any{}, false)
	if err != nil {
		return nil, err
	}
	var listed struct {
		Tools []mcpTool `json:"tools"`
	}
	if json.Unmarshal(result, &listed) != nil {
		return nil, fmt.Errorf("mcp_tools_invalid")
	}
	return listed.Tools, nil
}

// callMCPTool performs a complete short-lived MCP session for a mutation. A
// fresh session avoids leaking supplier session state between users/orders.
func callMCPTool(ctx context.Context, endpoint, token, authHeader, name string, arguments map[string]any) (any, error) {
	client := &http.Client{Timeout: 20 * time.Second}
	session := ""
	call := func(method string, params any, notify bool) (json.RawMessage, error) {
		var id any
		if !notify {
			id = atomic.AddUint64(&mcpRequestID, 1)
		}
		requestBody := map[string]any{"jsonrpc": "2.0", "method": method, "params": params}
		if id != nil {
			requestBody["id"] = id
		}
		body, err := json.Marshal(requestBody)
		if err != nil {
			return nil, err
		}
		reqCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
		defer cancel()
		req, err := http.NewRequestWithContext(reqCtx, http.MethodPost, endpoint, bytes.NewReader(body))
		if err != nil {
			return nil, err
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		req.Header.Set("MCP-Protocol-Version", "2025-06-18")
		if session != "" {
			req.Header.Set("Mcp-Session-Id", session)
		}
		if token != "" {
			req.Header.Set(authHeader, mcpAuthValue(authHeader, token))
		}
		resp, err := client.Do(req)
		if err != nil {
			return nil, err
		}
		defer resp.Body.Close()
		if value := resp.Header.Get("Mcp-Session-Id"); value != "" {
			session = value
		}
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			return nil, fmt.Errorf("mcp_http_%d", resp.StatusCode)
		}
		raw, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
		if err != nil {
			return nil, err
		}
		if notify {
			return nil, nil
		}
		decoded, ok := decodeMCPJSON(raw, resp.Header.Get("Content-Type"))
		if !ok {
			return nil, fmt.Errorf("mcp_invalid_json")
		}
		var envelope mcpRPCResponse
		if err := json.Unmarshal(decoded, &envelope); err != nil {
			return nil, fmt.Errorf("mcp_invalid_response")
		}
		if envelope.Error != nil {
			return nil, fmt.Errorf("mcp_%d: %s", envelope.Error.Code, envelope.Error.Message)
		}
		return envelope.Result, nil
	}
	if _, err := call("initialize", map[string]any{"protocolVersion": "2025-06-18", "capabilities": map[string]any{}, "clientInfo": map[string]any{"name": "omniroute-gateway", "version": "2026-09-04"}}, false); err != nil {
		return nil, err
	}
	if _, err := call("notifications/initialized", map[string]any{}, true); err != nil {
		return nil, err
	}
	result, err := call("tools/call", map[string]any{"name": name, "arguments": arguments}, false)
	if err != nil {
		return nil, err
	}
	payload := extractMCPToolPayload(result)
	if payload == nil {
		return nil, fmt.Errorf("mcp_empty_tool_result")
	}
	return payload, nil
}

func mcpArguments(input contracts.PlanningContext, kind string) map[string]any {
	args := map[string]any{"context": supplierContext(input), "destination": input.Destination, "origin": input.Origin, "start_date": input.StartDate, "end_date": input.EndDate, "travelers": input.Travelers, "budget": input.Budget, "currency": input.Currency}
	switch strings.ToUpper(kind) {
	case "LODGING":
		args["city"] = input.Destination
		args["check_in"] = input.StartDate
		args["check_out"] = input.EndDate
	case "DINING", "SCENIC", "DESTINATION", "CROWD", "WEATHER":
		args["city"] = input.Destination
	case "TRANSPORT":
		args["from"] = input.Origin
		args["to"] = input.Destination
	}
	return args
}

// mcpArgumentsForTool projects the canonical context onto the properties the
// server actually declares. This prevents sending guessed fields to strict
// MCP tools while retaining a useful fallback for schema-less tools.
// mcpLiteralDefaults supplies required enum/literal arguments that cannot be
// derived from the planning context. placeType=城市 matches RollingGo's declared
// searchHotels enum (城市/机场/景点/火车站/地铁站/酒店/区/县/详细地址) for a
// city-level hotel search; the numeric "CITY" value is not part of that enum and
// caused the server to fall back to a default (e.g. Bangkok) location.
var mcpLiteralDefaults = map[string]any{"placetype": "城市"}

func mcpArgumentsForTool(tool mcpTool, input contracts.PlanningContext, kind string) map[string]any {
	base := mcpArguments(input, kind)
	properties, _ := tool.InputSchema["properties"].(map[string]any)
	if len(properties) == 0 {
		return base
	}
	projected := make(map[string]any, len(properties))
	for property, rawSchema := range properties {
		canonical := strings.ToLower(strings.NewReplacer("_", "", "-", " ").Replace(property))
		if value, ok := mcpValueForProperty(canonical, base); ok {
			projected[property] = value
			continue
		}
		if value, ok := mcpLiteralDefaults[canonical]; ok {
			projected[property] = value
			continue
		}
		if schema, ok := rawSchema.(map[string]any); ok {
			text := strings.ToLower(fmt.Sprint(schema["title"]) + " " + fmt.Sprint(schema["description"]))
			if strings.Contains(text, "city") || strings.Contains(text, "城市") {
				projected[property] = input.Destination
			}
		}
	}
	return projected
}

// mcpArgumentsForOrder projects only fields declared by a mutation tool. This
// lets vendors use hotelId, passengerName, certificateNo, etc. without
// sending private identity fields to tools that do not declare them.
func mcpArgumentsForOrder(tool mcpTool, input contracts.PlanningContext, fields map[string]any) map[string]any {
	base := mcpArguments(input, "")
	for key, value := range fields {
		base[key] = value
	}
	properties, _ := tool.InputSchema["properties"].(map[string]any)
	if len(properties) == 0 {
		return base
	}
	projected := make(map[string]any, len(properties))
	for property := range properties {
		canonical := strings.ToLower(strings.NewReplacer("_", "", "-", " ").Replace(property))
		if value, ok := mcpValueForProperty(canonical, base); ok {
			projected[property] = value
		}
	}
	return projected
}

func mcpRequiredProperties(tool mcpTool, arguments map[string]any) []string {
	var required []any
	switch values := tool.InputSchema["required"].(type) {
	case []any:
		required = values
	case []string:
		for _, value := range values {
			required = append(required, value)
		}
	}
	missing := make([]string, 0)
	for _, value := range required {
		name := strings.TrimSpace(fmt.Sprint(value))
		if name == "" {
			continue
		}
		provided, exists := arguments[name]
		if !exists || provided == nil || strings.TrimSpace(fmt.Sprint(provided)) == "" {
			missing = append(missing, name)
		}
	}
	return missing
}

func mcpValueForProperty(property string, base map[string]any) (any, bool) {
	aliases := map[string][]string{
		"destination": {"destination", "city", "tocity", "arrivalcity", "targetcity", "to", "query", "keyword", "keywords", "searchterm", "place", "cityname", "arrivalcityname", "originquery", "scenicname"},
		"origin":      {"origin", "fromcity", "departurecity", "from", "departurecityname"},
		"startdate":   {"startdate", "checkin", "departuredate", "date", "departdate"},
		"enddate":     {"enddate", "checkout", "returndate", "checkoutparam"},
		"travelers":   {"travelers", "traveller", "passengers", "passengercount", "adults", "guests", "adultnum"},
		"budget":      {"budget", "maxprice", "pricelimit", "maxbudget"},
		"currency":    {"currency", "currencycode"},
		"preferences": {"preferences", "preference", "filters"},
		"context":     {"context", "planningcontext"},
		"hotelid":     {"hotelid", "hotel", "propertyid", "productid", "resourceid"},
		"roomtype":    {"roomtype", "room", "roomname", "roomcategory"},
		"passenger":   {"passenger", "passengers", "guest", "guestinfo", "travelerinfo"},
		"contact":     {"contact", "contactinfo", "contactname", "phone", "mobile", "email"},
		"certificate": {"certificate", "certificateno", "certificateid", "documentno", "idcard", "idnumber"},
		"flightid":    {"flightid", "flight", "flightnumber", "flightno"},
		"trainid":     {"trainid", "train", "trainnumber", "trainno"},
		"ticketid":    {"ticketid", "ticket", "scenicid", "productid"},
		"orderid":     {"orderid", "orderno", "ordernumber", "bookingid", "reservationid"},
	}
	propertyAliases := []string{property}
	for canonical, candidates := range aliases {
		if property == canonical {
			propertyAliases = candidates
			break
		}
		for _, candidate := range candidates {
			if property == candidate {
				propertyAliases = candidates
				break
			}
		}
	}
	for source, value := range base {
		normalized := strings.ToLower(strings.NewReplacer("_", "", "-", " ").Replace(source))
		for _, alias := range propertyAliases {
			if normalized == alias {
				return value, value != nil && fmt.Sprint(value) != ""
			}
		}
	}
	return nil, false
}

// supplierContext is the privacy-minimized outbound contract. Identity,
// health, document and private collaboration fields stay inside the gateway.
func supplierContext(input contracts.PlanningContext) map[string]any {
	return map[string]any{"destination": input.Destination, "origin": input.Origin, "start_date": input.StartDate, "end_date": input.EndDate, "travelers": input.Travelers, "budget": input.Budget, "currency": input.Currency, "preferences": input.Preferences}
}

// mcpPreferredDiscoveryTool pins the exact search/list tool per vendor and
// domain, derived from the real schemas discovered during联调. This keeps the
// planner on the price-discovery tool instead of detail/booking/mutation tools.
var mcpPreferredDiscoveryTool = map[string]map[string][]string{
	"ROLLINGGO": {
		"LODGING": {"searchHotels"},
	},
	"TUNIU": {
		"LODGING":   {"tuniuHotelSearch"},
		"SCENIC":    {"query_cheapest_tickets"},
		"TRANSPORT": {"searchLowestPriceFlight", "searchLowestPriceTrain"},
	},
}

func chooseMCPTool(provider string, tools []mcpTool, kind string) mcpTool {
	if byDomain, ok := mcpPreferredDiscoveryTool[strings.ToUpper(provider)]; ok {
		for _, preferred := range byDomain[strings.ToUpper(kind)] {
			for _, tool := range tools {
				if strings.EqualFold(strings.TrimSpace(tool.Name), preferred) {
					return tool
				}
			}
		}
	}
	keywords := map[string][]string{
		"TRANSPORT":   {"transport", "flight", "train", "route", "ticket", "交通", "机票", "火车"},
		"LODGING":     {"hotel", "lodging", "room", "住宿", "酒店"},
		"DINING":      {"dining", "restaurant", "food", "餐饮", "餐厅"},
		"SCENIC":      {"scenic", "poi", "ticket", "attraction", "景区", "景点", "门票"},
		"DESTINATION": {"destination", "recommend", "旅行", "目的地"},
		"CROWD":       {"crowd", "traffic", "capacity", "客流", "拥堵", "人流"},
		"WEATHER":     {"weather", "forecast", "天气", "气象"},
	}
	best := mcpTool{}
	bestScore := 0
	for _, candidate := range tools {
		text := strings.ToLower(candidate.Name + " " + candidate.Description)
		score := 0
		for _, keyword := range keywords[strings.ToUpper(kind)] {
			if strings.Contains(text, strings.ToLower(keyword)) {
				score++
			}
		}
		if score > bestScore {
			best, bestScore = candidate, score
		}
	}
	if bestScore == 0 && len(tools) == 1 {
		return tools[0]
	}
	return best
}

func extractMCPToolPayload(raw json.RawMessage) any {
	var result struct {
		Content []struct {
			Type, Text string
			Data       json.RawMessage
		} `json:"content"`
		StructuredContent json.RawMessage `json:"structuredContent"`
		IsError           bool            `json:"isError"`
	}
	if json.Unmarshal(raw, &result) != nil || result.IsError {
		return nil
	}
	if len(result.StructuredContent) > 0 && string(result.StructuredContent) != "null" {
		var value any
		if json.Unmarshal(result.StructuredContent, &value) == nil {
			return value
		}
	}
	for _, item := range result.Content {
		if item.Type == "text" && strings.TrimSpace(item.Text) != "" {
			var value any
			if json.Unmarshal([]byte(item.Text), &value) == nil {
				return value
			}
		}
	}
	return nil
}

func decodeMCPJSON(raw []byte, contentType string) ([]byte, bool) {
	trimmed := bytes.TrimSpace(raw)
	if json.Valid(trimmed) {
		return trimmed, true
	}
	// Streamable HTTP servers may answer with SSE even for a single response.
	for _, line := range bytes.Split(trimmed, []byte("\n")) {
		line = bytes.TrimSpace(line)
		if bytes.HasPrefix(line, []byte("data:")) {
			candidate := bytes.TrimSpace(bytes.TrimPrefix(line, []byte("data:")))
			if json.Valid(candidate) {
				return candidate, true
			}
		}
	}
	_ = contentType
	return nil, false
}

func mcpEndpoint(base string) bool {
	return strings.HasSuffix(strings.ToLower(strings.TrimRight(base, "/")), "/mcp")
}

func mcpAuthHeader(kind, provider, endpoint string) string {
	if configured := strings.TrimSpace(os.Getenv(strings.ToUpper(kind) + "_PROVIDER_AUTH_HEADER")); configured != "" {
		return configured
	}
	text := strings.ToLower(provider + " " + endpoint)
	if strings.Contains(text, "tuniu") {
		return "apiKey"
	}
	return "Authorization"
}

// mcpAuthValue builds the header value for a given auth header name. Tuniu
// carries the raw token in its `apiKey` header; standard providers such as
// RollingGo expect `Authorization: Bearer <token>`.
func mcpAuthValue(authHeader, token string) string {
	if strings.EqualFold(authHeader, "apiKey") {
		return token
	}
	return "Bearer " + token
}
