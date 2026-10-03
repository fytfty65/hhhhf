// Package push 实现 Web 推送（FCM HTTP v1）的发送端与设备 token 存储。
//
// 设计要点（都对应用户环境里真实的约束）：
//  1. **不引入新依赖**：服务账号 JWT(RS256) 用标准库签（crypto/rsa + crypto/x509），
//     再换 OAuth access token。go.mod 里没有 golang.org/x/oauth2，为推送加一个依赖不划算。
//  2. **token 落 SQLite**（与 gateway 现有 gorm/DB 同源），避免"重启即丢 / 多实例不一致"。
//  3. **密钥只走服务端**：服务账号 JSON 从文件路径读（`FCM_SERVICE_ACCOUNT_JSON_PATH`），
//     绝不进前端包；前端只拿 VAPID **公钥**与 web 配置。
//  4. **失败如实暴露**：发送结果按 token 分类（成功/失效/其它错误），失效 token 会被清理，
//     而不是静默重试到天荒地老 —— 这与项目"不粉饰"的口径一致。
package push

import (
	"bytes"
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"gateway/internal/database"
)

const (
	// 这两个地址可以覆盖，便于单测用 httptest 打本地服务
	defaultTokenURI   = "https://oauth2.googleapis.com/token"
	defaultFCMBaseURL = "https://fcm.googleapis.com"
	// access token 提前刷新，避免边界过期
	tokenRefreshSkew = 5 * time.Minute
)

var (
	ErrServiceAccountMissing = errors.New("未配置 FCM 服务账号（FCM_SERVICE_ACCOUNT_JSON_PATH）")
	ErrNoTokens              = errors.New("该用户没有已注册的推送设备")
)

// ConfigPreflight is a credential-free summary of FCM configuration health.
// It deliberately contains booleans and bounded reason codes only; callers
// must never expose the service-account path, private key, project id, or
// parser error text in an HTTP response or metric label.
type ConfigPreflight struct {
	Environment              string `json:"environment"`
	EnvironmentAllowed       bool   `json:"environment_allowed"`
	ServiceAccountConfigured bool   `json:"service_account_configured"`
	ServiceAccountReadable   bool   `json:"service_account_readable"`
	ServiceAccountParsed     bool   `json:"service_account_parsed"`
	ProjectIDConfigured      bool   `json:"project_id_configured"`
	Status                   string `json:"status"`
	Reason                   string `json:"reason,omitempty"`
}

// PreflightFromEnv validates only local configuration. It does not exchange
// an OAuth token and never calls an external endpoint.
func PreflightFromEnv(appEnv string) ConfigPreflight {
	env := strings.ToLower(strings.TrimSpace(appEnv))
	result := ConfigPreflight{Environment: env, Status: "blocked"}
	result.EnvironmentAllowed = env == "staging" || env == "test"
	if !result.EnvironmentAllowed {
		result.Reason = "environment_not_staging_or_test"
		return result
	}
	path := strings.TrimSpace(os.Getenv("FCM_SERVICE_ACCOUNT_JSON_PATH"))
	result.ServiceAccountConfigured = path != ""
	if path == "" {
		result.Reason = "service_account_path_missing"
		return result
	}
	info, err := os.Stat(path)
	if err != nil || info.IsDir() {
		result.Reason = "service_account_file_unavailable"
		return result
	}
	result.ServiceAccountReadable = true
	account, err := LoadServiceAccount(path)
	if err != nil || account == nil {
		result.Reason = "service_account_invalid"
		return result
	}
	// Validate the PEM/RSA material locally without exchanging a token or
	// contacting Google. signJWT only performs parsing and an in-memory
	// signature, and its output is deliberately discarded.
	if _, err := signJWT(account, account.TokenURI, time.Now().UTC(), time.Minute); err != nil {
		result.Reason = "service_account_invalid"
		return result
	}
	result.ServiceAccountParsed = true
	projectID := strings.TrimSpace(os.Getenv("FCM_PROJECT_ID"))
	if projectID == "" {
		projectID = strings.TrimSpace(account.ProjectID)
	}
	result.ProjectIDConfigured = projectID != ""
	if !result.ProjectIDConfigured {
		result.Reason = "project_id_missing"
		return result
	}
	result.Status = "ready"
	return result
}

// ---------------------------------------------------------------------------
// 设备 token 存储（SQLite）
// ---------------------------------------------------------------------------

// DeviceToken 一条设备推送 token（同一用户可有多台设备）。
type DeviceToken struct {
	ID        uint   `gorm:"primaryKey"`
	UserID    string `gorm:"index;size:64;not null"`
	Token     string `gorm:"uniqueIndex;size:512;not null"`
	Platform  string `gorm:"size:32"`
	CreatedAt time.Time
	UpdatedAt time.Time
}

// RegisterDevice 登记（或刷新）一台设备的 token。同一 token 换用户时会改归属 ——
// 设备转手/换账号登录是真实存在的，不能留下"推给上一个人的手机"这种问题。
func RegisterDevice(userID, token, platform string) error {
	userID = strings.TrimSpace(userID)
	token = strings.TrimSpace(token)
	if userID == "" || token == "" {
		return errors.New("user_id 与 token 都不能为空")
	}
	if err := database.DB.AutoMigrate(&DeviceToken{}); err != nil {
		return fmt.Errorf("推送表初始化失败: %w", err)
	}
	var existing DeviceToken
	err := database.DB.Where("token = ?", token).First(&existing).Error
	if err == nil {
		existing.UserID = userID
		existing.Platform = platform
		return database.DB.Save(&existing).Error
	}
	return database.DB.Create(&DeviceToken{UserID: userID, Token: token, Platform: platform}).Error
}

// UnregisterDevice 注销一台设备（用户关掉推送开关 / 退出登录时调用）。
func UnregisterDevice(userID, token string) error {
	if err := database.DB.AutoMigrate(&DeviceToken{}); err != nil {
		return err
	}
	query := database.DB.Where("token = ?", strings.TrimSpace(token))
	if strings.TrimSpace(userID) != "" {
		query = query.Where("user_id = ?", strings.TrimSpace(userID))
	}
	return query.Delete(&DeviceToken{}).Error
}

// TokensForUser 取某用户的所有设备 token。
func TokensForUser(userID string) ([]string, error) {
	if err := database.DB.AutoMigrate(&DeviceToken{}); err != nil {
		return nil, err
	}
	var rows []DeviceToken
	if err := database.DB.Where("user_id = ?", strings.TrimSpace(userID)).Find(&rows).Error; err != nil {
		return nil, err
	}
	out := make([]string, 0, len(rows))
	for _, row := range rows {
		out = append(out, row.Token)
	}
	return out, nil
}

// ---------------------------------------------------------------------------
// 服务账号与 JWT
// ---------------------------------------------------------------------------

// ServiceAccount 是 Google 服务账号 JSON 里我们用到的字段。
type ServiceAccount struct {
	ClientEmail string `json:"client_email"`
	PrivateKey  string `json:"private_key"`
	TokenURI    string `json:"token_uri"`
	ProjectID   string `json:"project_id"`
}

// LoadServiceAccount 从文件读取服务账号（空路径 → ErrServiceAccountMissing，绝不 panic）。
func LoadServiceAccount(path string) (*ServiceAccount, error) {
	path = strings.TrimSpace(path)
	if path == "" {
		return nil, ErrServiceAccountMissing
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("读取服务账号失败: %w", err)
	}
	var account ServiceAccount
	if err := json.Unmarshal(raw, &account); err != nil {
		return nil, fmt.Errorf("服务账号 JSON 解析失败: %w", err)
	}
	if account.ClientEmail == "" || account.PrivateKey == "" {
		return nil, errors.New("服务账号缺少 client_email 或 private_key")
	}
	if account.TokenURI == "" {
		account.TokenURI = defaultTokenURI
	}
	return &account, nil
}

func b64url(raw []byte) string { return base64.RawURLEncoding.EncodeToString(raw) }

// signJWT 按 Google 服务账号规范签一个 RS256 JWT（纯函数式：时间由参数传入，便于单测）。
func signJWT(account *ServiceAccount, audience string, now time.Time, ttl time.Duration) (string, error) {
	if account == nil {
		return "", ErrServiceAccountMissing
	}
	block, _ := pem.Decode([]byte(strings.ReplaceAll(account.PrivateKey, `\n`, "\n")))
	if block == nil {
		return "", errors.New("服务账号 private_key 不是合法的 PEM")
	}
	var privateKey *rsa.PrivateKey
	if parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes); err == nil {
		key, ok := parsed.(*rsa.PrivateKey)
		if !ok {
			return "", errors.New("服务账号私钥不是 RSA 密钥")
		}
		privateKey = key
	} else if key, err := x509.ParsePKCS1PrivateKey(block.Bytes); err == nil {
		privateKey = key
	} else {
		return "", fmt.Errorf("解析服务账号私钥失败: %w", err)
	}

	header := b64url([]byte(`{"alg":"RS256","typ":"JWT"}`))
	claims := map[string]any{
		"iss":   account.ClientEmail,
		"scope": "https://www.googleapis.com/auth/firebase.messaging",
		"aud":   audience,
		"iat":   now.Unix(),
		"exp":   now.Add(ttl).Unix(),
	}
	payloadJSON, err := json.Marshal(claims)
	if err != nil {
		return "", err
	}
	signingInput := header + "." + b64url(payloadJSON)
	digest := sha256.Sum256([]byte(signingInput))
	signature, err := rsa.SignPKCS1v15(rand.Reader, privateKey, crypto.SHA256, digest[:])
	if err != nil {
		return "", err
	}
	return signingInput + "." + b64url(signature), nil
}

// ---------------------------------------------------------------------------
// 发送
// ---------------------------------------------------------------------------

// Message 一条推送内容。Data 里的键值会原样带给前端 SW（便于点开跳到指定行程）。
type Message struct {
	Title string
	Body  string
	Data  map[string]string
}

// Sender 负责取 access token 并投递到 FCM HTTP v1。
type Sender struct {
	projectID  string
	account    *ServiceAccount
	client     *http.Client
	fcmBaseURL string

	mu          sync.Mutex
	accessToken string
	tokenExpiry time.Time
	now         func() time.Time
}

// NewSenderFromEnv 按环境变量构造；未配置服务账号时返回错误（调用方应据此**如实报告
// "推送未启用"**，而不是假装成功）。
func NewSenderFromEnv() (*Sender, error) {
	account, err := LoadServiceAccount(os.Getenv("FCM_SERVICE_ACCOUNT_JSON_PATH"))
	if err != nil {
		return nil, err
	}
	if _, err := signJWT(account, account.TokenURI, time.Now().UTC(), time.Minute); err != nil {
		return nil, errors.New("FCM 服务账号私钥无效")
	}
	projectID := strings.TrimSpace(os.Getenv("FCM_PROJECT_ID"))
	if projectID == "" {
		projectID = account.ProjectID
	}
	if projectID == "" {
		return nil, errors.New("缺少 FCM_PROJECT_ID（服务账号里也没有 project_id）")
	}
	return &Sender{
		projectID:  projectID,
		account:    account,
		client:     &http.Client{Timeout: 15 * time.Second},
		fcmBaseURL: defaultFCMBaseURL,
		now:        time.Now,
	}, nil
}

// accessTokenValue 取（并缓存）OAuth access token。
func (s *Sender) accessTokenValue(ctx context.Context) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now()
	if s.accessToken != "" && now.Add(tokenRefreshSkew).Before(s.tokenExpiry) {
		return s.accessToken, nil
	}
	assertion, err := signJWT(s.account, s.account.TokenURI, now, time.Hour)
	if err != nil {
		return "", err
	}
	form := "grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=" + assertion
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.account.TokenURI, bytes.NewBufferString(form))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	resp, err := s.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("获取 access token 失败: %w", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("获取 access token 被拒（HTTP %d）: %s", resp.StatusCode, truncate(string(body), 200))
	}
	var parsed struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int    `json:"expires_in"`
	}
	if err := json.Unmarshal(body, &parsed); err != nil || parsed.AccessToken == "" {
		return "", errors.New("access token 响应无法解析")
	}
	if parsed.ExpiresIn <= 0 {
		parsed.ExpiresIn = 3600
	}
	s.accessToken = parsed.AccessToken
	s.tokenExpiry = now.Add(time.Duration(parsed.ExpiresIn) * time.Second)
	return s.accessToken, nil
}

// SendResult 一次发送的**如实**结果统计。
type SendResult struct {
	Sent      int      // 成功投递的设备数
	Invalid   []string // 已被 FCM 判定失效、并已从库里清理的 token
	Failed    int      // 其它失败
	LastError string   // 最后一次失败的原文（便于排查，不吞错）
}

// SendToUser 给某用户的所有设备投一条推送。
// 返回的 SendResult 一定非 nil：调用方据此如实记录"推给了几台、几台失效、几台失败"。
func (s *Sender) SendToUser(ctx context.Context, userID string, message Message) (*SendResult, error) {
	result := &SendResult{}
	tokens, err := TokensForUser(userID)
	if err != nil {
		return result, err
	}
	if len(tokens) == 0 {
		return result, ErrNoTokens
	}
	accessToken, err := s.accessTokenValue(ctx)
	if err != nil {
		return result, err
	}
	for _, token := range tokens {
		status, body, err := s.sendOne(ctx, accessToken, token, message)
		switch {
		case err != nil:
			result.Failed++
			result.LastError = err.Error()
		case status == http.StatusOK:
			result.Sent++
		case isInvalidTokenResponse(status, body):
			// FCM UNREGISTERED (通常 404) 或明确的 token INVALID_ARGUMENT：
			// token 已失效 → 清掉，别一直重试。普通 400 不能直接删，
			// 因为它也可能代表 payload 配置错误而不是设备授权失效。
			result.Invalid = append(result.Invalid, token)
			_ = UnregisterDevice("", token)
		default:
			result.Failed++
			result.LastError = fmt.Sprintf("HTTP %d: %s", status, truncate(body, 200))
		}
	}
	return result, nil
}

func isInvalidTokenResponse(status int, body string) bool {
	if status == http.StatusNotFound {
		return true
	}
	if status != http.StatusBadRequest {
		return false
	}
	upper := strings.ToUpper(body)
	return strings.Contains(upper, "UNREGISTERED") || strings.Contains(upper, "INVALID_ARGUMENT")
}

func (s *Sender) sendOne(ctx context.Context, accessToken, deviceToken string, message Message) (int, string, error) {
	data := map[string]string{}
	for key, value := range message.Data {
		data[key] = value
	}
	payload := map[string]any{
		"message": map[string]any{
			"token": deviceToken,
			"notification": map[string]string{
				"title": message.Title,
				"body":  message.Body,
			},
			"data": data,
			"webpush": map[string]any{
				"notification": map[string]any{"icon": "/icon-192.png", "requireInteraction": false},
				"fcm_options":  map[string]any{"link": data["link"]},
			},
		},
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		return 0, "", err
	}
	url := fmt.Sprintf("%s/v1/projects/%s/messages:send", strings.TrimRight(s.fcmBaseURL, "/"), s.projectID)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(raw))
	if err != nil {
		return 0, "", err
	}
	req.Header.Set("Authorization", "Bearer "+accessToken)
	req.Header.Set("Content-Type", "application/json")
	resp, err := s.client.Do(req)
	if err != nil {
		return 0, "", err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	return resp.StatusCode, string(body), nil
}

func truncate(text string, limit int) string {
	if len(text) <= limit {
		return text
	}
	return text[:limit] + "…"
}
