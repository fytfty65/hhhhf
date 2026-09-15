package handlers

import (
	"net/http"
	"net/url"
	"strings"

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
			entry := map[string]any{"kind": strings.ToLower(candidateKind), "provider": provider, "tools": tools, "available": err == nil}
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
	planningData(c, gin.H{"providers": result, "schema_version": "mcp-2025-06-18", "note": "工具 schema 仅用于联调；密钥和会话标识不会返回"})
}
