package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"hash/fnv"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"github.com/gin-gonic/gin"
)

func planningModelDecision(userID string) map[string]any {
	var deployments []models.ModelDeployment
	if database.DB == nil || database.DB.Find(&deployments).Error != nil {
		return map[string]any{"name": "estimator", "version": "builtin", "status": "fallback", "shadow": false}
	}
	var active *models.ModelDeployment
	for i := range deployments {
		if deployments[i].Status == "active" {
			active = &deployments[i]
			break
		}
	}
	if active == nil {
		return map[string]any{"name": "estimator", "version": "builtin", "status": "fallback", "shadow": false}
	}
	decision := map[string]any{"name": active.Name, "version": active.Version, "status": "active", "shadow": false}
	for i := range deployments {
		if deployments[i].Status != "canary" || deployments[i].Traffic <= 0 {
			continue
		}
		if !deploymentPassesRuntimeGate(deployments[i]) {
			// Quality metrics are server-written by the evaluator. A canary that
			// breaches the gate is removed before any user is assigned to it.
			deployments[i].Status, deployments[i].Traffic = "rolled_back", 0
			_ = database.DB.Save(&deployments[i]).Error
			continue
		}
		h := fnv.New32a()
		_, _ = h.Write([]byte(userID + ":" + deployments[i].Version))
		bucket := int(h.Sum32() % 100)
		if bucket < deployments[i].Traffic {
			decision = map[string]any{"name": deployments[i].Name, "version": deployments[i].Version, "status": "canary", "shadow": false}
			break
		}
	}
	return decision
}

func deploymentPassesRuntimeGate(deployment models.ModelDeployment) bool {
	if strings.TrimSpace(deployment.Metrics) == "" {
		return true
	}
	var metrics map[string]float64
	if json.Unmarshal([]byte(deployment.Metrics), &metrics) != nil {
		return true
	}
	if score, ok := metrics["offline_score"]; ok && score < 0.80 {
		return false
	}
	if drift, ok := metrics["drift_score"]; ok && drift > 0.20 {
		return false
	}
	return true
}

// isPlanningAdmin reports whether userID is an explicitly configured planning
// administrator.
//
// The previous implementation split an empty env var into [""], which matched
// the empty user ID. It was saved only by an unrelated `userID != ""` guard, so
// any caller that reached a handler without AuthMiddleware — or any future
// middleware reordering — silently became a planning admin, able to register
// arbitrary model endpoints and export the full training dataset.
func isPlanningAdmin(userID string) bool {
	userID = strings.TrimSpace(userID)
	if userID == "" {
		return false
	}
	raw := strings.TrimSpace(os.Getenv("PLANNING_ADMIN_USER_IDS"))
	if raw == "" {
		// No allowlist configured means nobody is an admin.
		return false
	}
	for _, candidate := range strings.Split(raw, ",") {
		if candidate = strings.TrimSpace(candidate); candidate != "" && candidate == userID {
			return true
		}
	}
	return false
}

// validateModelEndpointHost rejects model execution endpoints that point at
// loopback, private, link-local or cloud-metadata addresses.
//
// The endpoint is admin-supplied but stored in the database and later POSTed to
// with an attacker-influenced body (planning.go promotion/shadow paths), which
// made it an SSRF primitive: http://169.254.169.254/… reaches cloud metadata and
// http://127.0.0.1:<port>/ scans the internal network by timing and error text.
func validateModelEndpointHost(raw string) error {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return fmt.Errorf("模型执行端点无法解析")
	}
	host := parsed.Hostname()
	if host == "" {
		return fmt.Errorf("模型执行端点缺少主机名")
	}
	if strings.EqualFold(host, "localhost") || strings.HasSuffix(strings.ToLower(host), ".localhost") {
		return fmt.Errorf("模型执行端点不允许指向本机地址")
	}

	// An IP literal can be judged directly; a hostname is resolved so that a
	// name pointing into private space is rejected too.
	ips := []net.IP{}
	if ip := net.ParseIP(host); ip != nil {
		ips = append(ips, ip)
	} else {
		resolved, lookupErr := net.LookupIP(host)
		if lookupErr != nil {
			return fmt.Errorf("模型执行端点域名无法解析")
		}
		ips = append(ips, resolved...)
	}
	for _, ip := range ips {
		if ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() ||
			ip.IsLinkLocalMulticast() || ip.IsUnspecified() || ip.IsMulticast() {
			return fmt.Errorf("模型执行端点不允许指向内网或链路本地地址")
		}
		// 169.254.169.254 / fd00:ec2::254 — the cloud metadata services.
		if ip.IsLinkLocalUnicast() || ip.String() == "169.254.169.254" {
			return fmt.Errorf("模型执行端点不允许指向云元数据地址")
		}
	}
	return nil
}

func ListModelDeploymentsHandler(c *gin.Context) {
	if _, ok := currentUserIDOrReject(c); !ok {
		return
	}
	var modelsList []models.ModelDeployment
	if err := database.DB.Order("created_at desc").Find(&modelsList).Error; err != nil {
		planningError(c, http.StatusInternalServerError, "MODEL_REGISTRY_UNAVAILABLE", "模型注册表暂不可用", nil)
		return
	}
	planningData(c, gin.H{"models": modelsList, "active": activeModel(modelsList), "policy": gin.H{"canary_max_percent": 20, "promotion_requires": []string{"offline_evaluation", "drift_check", "rollback_ready"}}})
}

func RegisterModelDeploymentHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if !isPlanningAdmin(userID) {
		planningError(c, http.StatusForbidden, "MODEL_ADMIN_REQUIRED", "只有规划服务管理员可以注册模型", nil)
		return
	}
	var req struct {
		Name     string             `json:"name"`
		Version  string             `json:"version"`
		Status   string             `json:"status"`
		Traffic  int                `json:"traffic_percent"`
		Endpoint string             `json:"endpoint"`
		Metrics  map[string]float64 `json:"metrics"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_MODEL", "模型参数不完整", nil)
		return
	}
	req.Name, req.Version = strings.TrimSpace(req.Name), strings.TrimSpace(req.Version)
	if req.Name == "" || req.Version == "" {
		planningError(c, http.StatusBadRequest, "MODEL_ID_REQUIRED", "模型名称和版本不能为空", nil)
		return
	}
	if len(req.Name) > 100 || len(req.Version) > 100 {
		planningError(c, http.StatusBadRequest, "MODEL_ID_TOO_LONG", "模型名称或版本过长", nil)
		return
	}
	if req.Status == "" {
		req.Status = "shadow"
	}
	if req.Status != "shadow" && req.Status != "canary" {
		planningError(c, http.StatusBadRequest, "MODEL_STATUS_INVALID", "新模型只能以 shadow 或 canary 状态注册", nil)
		return
	}
	if req.Traffic < 0 || req.Traffic > 20 {
		planningError(c, http.StatusBadRequest, "MODEL_TRAFFIC_INVALID", "灰度流量必须在 0 到 20 之间", nil)
		return
	}
	if req.Endpoint != "" {
		if !strings.HasPrefix(req.Endpoint, "https://") && !strings.HasPrefix(req.Endpoint, "http://") {
			planningError(c, http.StatusBadRequest, "MODEL_ENDPOINT_INVALID", "模型执行端点必须使用 http 或 https", nil)
			return
		}
		if len(req.Endpoint) > 500 {
			planningError(c, http.StatusBadRequest, "MODEL_ENDPOINT_TOO_LONG", "模型执行端点过长", nil)
			return
		}
		if err := validateModelEndpointHost(req.Endpoint); err != nil {
			planningError(c, http.StatusBadRequest, "MODEL_ENDPOINT_UNSAFE", err.Error(), nil)
			return
		}
	}
	metricsJSON, _ := json.Marshal(req.Metrics)
	deployment := models.ModelDeployment{Name: req.Name, Version: req.Version, Status: req.Status, Traffic: req.Traffic, Endpoint: strings.TrimSpace(req.Endpoint), Metrics: string(metricsJSON)}
	if err := database.DB.Create(&deployment).Error; err != nil {
		planningError(c, http.StatusConflict, "MODEL_VERSION_EXISTS", "模型版本已存在或注册失败", nil)
		return
	}
	planningData(c, gin.H{"model": deployment, "next": "先执行离线评估，再允许晋级"})
}

func PromoteModelDeploymentHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if !isPlanningAdmin(userID) {
		planningError(c, http.StatusForbidden, "MODEL_ADMIN_REQUIRED", "只有规划服务管理员可以晋级模型", nil)
		return
	}
	id, err := strconv.ParseUint(c.Param("id"), 10, 64)
	if err != nil {
		planningError(c, http.StatusBadRequest, "MODEL_ID_INVALID", "模型标识无效", nil)
		return
	}
	var deployment models.ModelDeployment
	if err := database.DB.First(&deployment, id).Error; err != nil {
		planningError(c, http.StatusNotFound, "MODEL_NOT_FOUND", "模型版本不存在", nil)
		return
	}
	var metrics map[string]float64
	_ = json.Unmarshal([]byte(deployment.Metrics), &metrics)
	if metrics["offline_score"] < 0.80 || metrics["drift_score"] > 0.20 || metrics["rollback_seconds"] <= 0 {
		planningError(c, http.StatusConflict, "MODEL_GATE_FAILED", "模型未通过离线质量、漂移或回滚门禁", map[string]string{"required_offline_score": "0.80", "max_drift_score": "0.20", "rollback_seconds": "must_be_positive"})
		return
	}
	var active models.ModelDeployment
	if err := database.DB.Where("status = ?", "active").First(&active).Error; err == nil {
		active.Status, active.Traffic = "rolled_back", 0
		_ = database.DB.Save(&active).Error
	}
	deployment.Status, deployment.Traffic = "active", 100
	if err := database.DB.Save(&deployment).Error; err != nil {
		planningError(c, http.StatusInternalServerError, "MODEL_PROMOTION_FAILED", "模型晋级失败", nil)
		return
	}
	planningData(c, gin.H{"model": deployment, "message": "模型已晋级为 active，旧版本已标记为 rolled_back"})
}

func EvaluateModelDeploymentHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if !isPlanningAdmin(userID) {
		planningError(c, http.StatusForbidden, "MODEL_ADMIN_REQUIRED", "只有规划服务管理员可以评估模型", nil)
		return
	}
	id, err := strconv.ParseUint(c.Param("id"), 10, 64)
	if err != nil {
		planningError(c, http.StatusBadRequest, "MODEL_ID_INVALID", "模型标识无效", nil)
		return
	}
	var deployment models.ModelDeployment
	if err := database.DB.First(&deployment, id).Error; err != nil {
		planningError(c, http.StatusNotFound, "MODEL_NOT_FOUND", "模型版本不存在", nil)
		return
	}
	var events []models.PlanningEvent
	database.DB.Where("event_type IN ?", []string{"plan_generated", "plan_adopted", "plan_rejected"}).Order("created_at asc").Find(&events)
	generated, adopted, rejected := 0, 0, 0
	versioned := make([]models.PlanningEvent, 0, len(events))
	for _, event := range events {
		var payload map[string]any
		_ = json.Unmarshal([]byte(event.Payload), &payload)
		if version, _ := payload["model_version"].(string); version != deployment.Version {
			continue
		}
		versioned = append(versioned, event)
		switch event.EventType {
		case "plan_generated":
			generated++
		case "plan_adopted":
			adopted++
		case "plan_rejected":
			rejected++
		}
	}
	// Use the newest 20% of versioned events as a temporal validation slice.
	// This prevents feedback from the future leaking into the score used for
	// promotion. With too little data, the score remains conservative.
	validationStart := len(versioned)
	if len(versioned) > 0 {
		validationStart = int(math.Floor(float64(len(versioned)) * 0.8))
	}
	trainGenerated, trainAdopted := 0, 0
	validationGenerated, validationAdopted := 0, 0
	for index, event := range versioned {
		if event.EventType != "plan_generated" && event.EventType != "plan_adopted" {
			continue
		}
		if index >= validationStart {
			if event.EventType == "plan_generated" {
				validationGenerated++
			} else {
				validationAdopted++
			}
		} else if event.EventType == "plan_generated" {
			trainGenerated++
		} else {
			trainAdopted++
		}
	}
	trainScore, validationScore := 0.0, 0.0
	if trainGenerated > 0 {
		trainScore = float64(trainAdopted) / float64(trainGenerated)
	}
	if validationGenerated > 0 {
		validationScore = float64(validationAdopted) / float64(validationGenerated)
	}
	offlineScore := validationScore
	if validationGenerated == 0 {
		offlineScore = trainScore
	}
	drift := eventTypeDrift(versioned, time.Now().Add(-14*24*time.Hour))
	driftScore, _ := drift["score"].(float64)
	counterfactual := modelCounterfactualEstimate(versioned)
	metrics := map[string]float64{"offline_score": offlineScore, "train_score": trainScore, "validation_score": validationScore, "drift_score": driftScore, "rollback_seconds": 60, "generated": float64(generated), "adopted": float64(adopted), "rejected": float64(rejected), "validation_generated": float64(validationGenerated), "validation_adopted": float64(validationAdopted), "counterfactual_samples": float64(counterfactual["samples"].(int)), "evaluated_at_unix": float64(time.Now().Unix())}
	encoded, _ := json.Marshal(metrics)
	deployment.Metrics = string(encoded)
	_ = database.DB.Save(&deployment).Error
	planningData(c, gin.H{"model": deployment, "evaluation": metrics, "temporal_split": gin.H{"policy": "oldest_80_percent_train_newest_20_percent_validation", "train_generated": trainGenerated, "validation_generated": validationGenerated}, "counterfactual": counterfactual, "promotion_ready": offlineScore >= 0.80 && driftScore <= 0.20})
}

func modelCounterfactualEstimate(events []models.PlanningEvent) map[string]any {
	weightedReward, weight := 0.0, 0.0
	samples := 0
	for _, event := range events {
		var payload map[string]any
		if json.Unmarshal([]byte(event.Payload), &payload) != nil {
			continue
		}
		propensity, ok := numericPayload(payload, "propensity")
		if !ok || propensity <= 0 || propensity > 1 {
			continue
		}
		reward, ok := numericPayload(payload, "reward")
		if !ok {
			if event.EventType == "plan_adopted" {
				reward = 1
			} else if event.EventType == "plan_rejected" {
				reward = 0
			} else {
				continue
			}
		}
		weightedReward += reward / propensity
		weight += 1 / propensity
		samples++
	}
	if samples == 0 || weight == 0 {
		return map[string]any{"status": "insufficient_data", "samples": 0, "method": "ips", "required_fields": []string{"propensity", "reward"}}
	}
	return map[string]any{"status": "available", "samples": samples, "method": "ips", "estimate": roundRatio(weightedReward / weight)}
}

func numericPayload(payload map[string]any, key string) (float64, bool) {
	value, exists := payload[key]
	if !exists {
		return 0, false
	}
	switch typed := value.(type) {
	case float64:
		return typed, true
	case float32:
		return float64(typed), true
	case int:
		return float64(typed), true
	case json.Number:
		parsed, err := typed.Float64()
		return parsed, err == nil
	default:
		return 0, false
	}
}

func activeModel(items []models.ModelDeployment) *models.ModelDeployment {
	for i := range items {
		if items[i].Status == "active" {
			return &items[i]
		}
	}
	return nil
}

// invokeModelEndpoint is the only gateway boundary for a deployed planner.
// Responses remain opaque until the caller applies route/constraint checks;
// a missing endpoint is an explicit fallback, never a fake model run.
func invokeModelEndpoint(ctx context.Context, deployment models.ModelDeployment, input any) (map[string]any, error) {
	if strings.TrimSpace(deployment.Endpoint) == "" {
		return nil, nil
	}
	body, err := json.Marshal(input)
	if err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, deployment.Endpoint, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("X-OmniRoute-Model-Version", deployment.Version)
	client := &http.Client{Timeout: 3 * time.Second}
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("model endpoint returned %s", response.Status)
	}
	var output map[string]any
	if err := json.NewDecoder(io.LimitReader(response.Body, 256<<10)).Decode(&output); err != nil {
		return nil, err
	}
	return output, nil
}
