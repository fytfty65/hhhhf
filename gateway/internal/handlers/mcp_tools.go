package handlers

import (
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strings"

	"gateway/internal/contracts"
	"github.com/gin-gonic/gin"
)

// MCPToolsSchemaHandler exposes vendor tool schemas without credentials or
// session identifiers. Operators can use it to verify exact input properties
// before enabling a provider in production.
func MCPToolsSchemaHandler(c *gin.Context) {
	if _, ok := currentUserIDOrReject(c); !ok {
		return
	}
	kind := strings.ToUpper(strings.TrimSpace(c.Query("kind")))
	kinds := []string{"DESTINATION", "SCENIC", "TRANSPORT", "LODGING", "DINING"}
	if kind != "" {
		kinds = []string{kind}
	}
	result := make([]map[string]any, 0)
	for _, candidateKind := range kinds {
		for _, config := range resolveProviderConfigs(candidateKind) {
			provider := firstNonEmpty(config.label, providerName(candidateKind, config.base))
			if !mcpEndpoint(config.base) && !mcpConfiguredProtocol(candidateKind, provider) {
				continue
			}
			tools, err := discoverMCPTools(c.Request.Context(), config.base, config.token, mcpAuthHeader(candidateKind, provider, config.base))
			audits := make([]map[string]any, 0, len(tools))
			safeTools := make([]mcpTool, 0, len(tools))
			for _, tool := range tools {
				audits = append(audits, mcpToolSchemaAudit(tool, candidateKind))
				safeTools = append(safeTools, redactMCPToolSchema(tool))
			}
			entry := map[string]any{"kind": strings.ToLower(candidateKind), "provider": provider, "tools": safeTools, "schema_audit": audits, "selection": mcpToolSelectionSummary(provider, tools, candidateKind, err), "available": err == nil}
			if safeURL, parseErr := url.Parse(config.base); parseErr == nil {
				entry["endpoint"] = safeURL.Scheme + "://" + safeURL.Host + safeURL.Path
			}
			if err != nil {
				entry["error"] = err.Error()
			}
			result = append(result, entry)
		}
	}
	if len(result) == 0 {
		planningError(c, http.StatusNotFound, "MCP_PROVIDER_NOT_CONFIGURED", "没有配置 MCP 供应商端点", nil)
		return
	}
	planningData(c, gin.H{"providers": result, "schema_version": "mcp-2025-06-18", "mapping_contract": "mcp-schema-mapping-v1", "note": "工具 schema 仅用于联调；密钥和会话标识不会返回"})
}

// mcpToolSelectionSummary makes the read-only/mutation boundary explicit for
// operators. It contains only tool names and audit states; credentials,
// session identifiers and supplier payloads never enter this diagnostic.
func mcpToolSelectionSummary(provider string, tools []mcpTool, kind string, discoveryErr error) map[string]any {
	blockedMutations := make([]string, 0)
	readOnlyCandidates := make([]string, 0)
	for _, tool := range tools {
		if isMCPMutationTool(tool) {
			blockedMutations = append(blockedMutations, tool.Name)
			continue
		}
		readOnlyCandidates = append(readOnlyCandidates, tool.Name)
	}
	sort.Strings(blockedMutations)
	sort.Strings(readOnlyCandidates)
	result := map[string]any{
		"read_only_candidates": readOnlyCandidates,
		"blocked_mutations":    blockedMutations,
		"status":               "not_collected",
	}
	if discoveryErr != nil {
		return result
	}
	selected := chooseMCPTool(provider, tools, kind)
	if selected.Name == "" {
		result["status"] = "no_read_only_tool"
		return result
	}
	result["read_only_candidate"] = selected.Name
	audit := mcpToolSchemaAudit(selected, kind)
	result["status"] = audit["status"]
	if audit["status"] != "ready" {
		result["blocked_reason"] = "selected_schema_not_ready"
	}
	return result
}

func redactMCPToolSchema(tool mcpTool) mcpTool {
	return mcpTool{Name: tool.Name, Description: sanitizeMCPDescription(tool.Description), InputSchema: sanitizeMCPMap(tool.InputSchema)}
}

func sanitizeMCPDescription(value string) string {
	text := strings.TrimSpace(value)
	for _, marker := range []string{"token", "secret", "password", "authorization", "session"} {
		if strings.Contains(strings.ToLower(text), marker) {
			return "工具描述已省略敏感字段"
		}
	}
	if len(text) > 500 {
		return text[:500]
	}
	return text
}

func sanitizeMCPMap(input map[string]any) map[string]any {
	if input == nil {
		return nil
	}
	result := make(map[string]any, len(input))
	for key, value := range input {
		lower := strings.ToLower(strings.ReplaceAll(strings.ReplaceAll(strings.TrimSpace(key), "-", "_"), " ", "_"))
		if strings.Contains(lower, "token") || strings.Contains(lower, "secret") || strings.Contains(lower, "password") || strings.Contains(lower, "authorization") || strings.Contains(lower, "session") || strings.Contains(lower, "api_key") || strings.Contains(lower, "apikey") {
			continue
		}
		switch typed := value.(type) {
		case map[string]any:
			result[key] = sanitizeMCPMap(typed)
		case []any:
			items := make([]any, 0, len(typed))
			for _, item := range typed {
				if nested, ok := item.(map[string]any); ok {
					items = append(items, sanitizeMCPMap(nested))
				} else {
					items = append(items, item)
				}
			}
			result[key] = items
		default:
			result[key] = value
		}
	}
	return result
}

// mcpToolSchemaAudit validates only the shape and mapping coverage of a
// discovered tool. It uses synthetic, non-user values so diagnostics never
// echo a real traveller, token, session or supplier response.
func mcpToolSchemaAudit(tool mcpTool, kind string) map[string]any {
	properties, _ := tool.InputSchema["properties"].(map[string]any)
	required := mcpRequiredNames(tool)
	context := contracts.PlanningContext{Destination: "示例城市", Origin: "示例出发地", StartDate: "2099-01-01", EndDate: "2099-01-02", Travelers: 1, Budget: 1, Currency: "CNY"}
	contextBase := mcpArguments(context, kind)
	orderBase := map[string]any{
		"hotelId": "fixture-hotel", "roomType": "fixture-room", "passengerName": "fixture-passenger", "certificateNo": "fixture-certificate",
		"contact": "fixture-contact", "flightId": "fixture-flight", "trainId": "fixture-train", "ticketId": "fixture-ticket", "orderId": "fixture-order",
	}
	contextMapped, defaultMapped, explicitFields, unresolved, invalid, typeMismatch := make([]string, 0), make([]string, 0), make([]string, 0), make([]string, 0), make([]string, 0), make([]string, 0)
	mapping := make(map[string]any, len(required))
	for _, name := range required {
		if _, exists := properties[name]; !exists {
			invalid = append(invalid, name)
			mapping[name] = map[string]any{"canonical": normalizeMCPPropertyName(name), "status": "invalid_schema"}
			continue
		}
		canonical := normalizeMCPPropertyName(name)
		if value, ok := mcpValueForProperty(canonical, contextBase); ok && value != nil && strings.TrimSpace(toSchemaString(value)) != "" {
			contextMapped = append(contextMapped, name)
			field := map[string]any{"canonical": canonical, "source": "planning_context", "status": "mapped"}
			if issue := mcpSchemaValueIssue(properties[name], value); issue != "" {
				typeMismatch = append(typeMismatch, name)
				field["status"] = "type_mismatch"
				field["reason"] = issue
			}
			mapping[name] = field
			continue
		}
		if value, ok := mcpLiteralDefaults[canonical]; ok {
			defaultMapped = append(defaultMapped, name)
			field := map[string]any{"canonical": canonical, "source": "safe_default", "status": "mapped"}
			if issue := mcpSchemaValueIssue(properties[name], value); issue != "" {
				typeMismatch = append(typeMismatch, name)
				field["status"] = "type_mismatch"
				field["reason"] = issue
			}
			mapping[name] = field
			continue
		}
		if value, ok := mcpValueForProperty(canonical, orderBase); ok && value != nil && strings.TrimSpace(toSchemaString(value)) != "" {
			explicitFields = append(explicitFields, name)
			field := map[string]any{"canonical": canonical, "source": "explicit_request", "status": "request_field"}
			if issue := mcpSchemaValueIssue(properties[name], value); issue != "" {
				typeMismatch = append(typeMismatch, name)
				field["status"] = "type_mismatch"
				field["reason"] = issue
			}
			mapping[name] = field
			continue
		}
		unresolved = append(unresolved, name)
		mapping[name] = map[string]any{"canonical": canonical, "status": "unmapped"}
	}
	sort.Strings(contextMapped)
	sort.Strings(defaultMapped)
	sort.Strings(explicitFields)
	sort.Strings(unresolved)
	sort.Strings(invalid)
	sort.Strings(typeMismatch)
	status := "ready"
	if len(invalid) > 0 {
		status = "invalid_schema"
	} else if len(typeMismatch) > 0 {
		status = "requires_mapping"
	} else if len(unresolved) > 0 {
		status = "requires_mapping"
	} else if len(explicitFields) > 0 {
		status = "requires_request_fields"
	}
	return map[string]any{
		"name": tool.Name, "property_count": len(properties), "required": required,
		"context_mapped_required": contextMapped, "default_mapped_required": defaultMapped, "explicit_request_fields": explicitFields,
		"unmapped_required": unresolved, "invalid_required": invalid, "type_mismatch_required": typeMismatch,
		"field_mapping": mapping, "status": status,
	}
}

func mcpRequiredNames(tool mcpTool) []string {
	result := make([]string, 0)
	switch values := tool.InputSchema["required"].(type) {
	case []any:
		for _, value := range values {
			if name := strings.TrimSpace(toSchemaString(value)); name != "" {
				result = append(result, name)
			}
		}
	case []string:
		for _, value := range values {
			if name := strings.TrimSpace(value); name != "" {
				result = append(result, name)
			}
		}
	}
	return result
}

func toSchemaString(value any) string {
	if value == nil {
		return ""
	}
	return strings.TrimSpace(strings.Trim(fmt.Sprint(value), "\""))
}
