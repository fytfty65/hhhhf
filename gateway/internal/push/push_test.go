package push

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// —— 测试辅助（放这里是为了让上面的用例读起来直接）——
func decodeSegment(segment string) ([]byte, error) { return base64.RawURLEncoding.DecodeString(segment) }

func sha256Sum(text string) []byte {
	sum := sha256.Sum256([]byte(text))
	return sum[:]
}

var cryptoSHA256 = crypto.SHA256

func writeFile(path, content string) error { return os.WriteFile(path, []byte(content), 0o600) }

func ioReadAll(r *http.Request) (string, error) {
	raw, err := io.ReadAll(r.Body)
	return string(raw), err
}

// 造一把测试用 RSA 私钥 + 服务账号（真签真验，不 mock 签名）。
func testAccount(t *testing.T) (*ServiceAccount, *rsa.PrivateKey) {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("生成测试密钥失败: %v", err)
	}
	pemBytes := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: mustPKCS8(t, key)})
	return &ServiceAccount{
		ClientEmail: "probe@example.iam.gserviceaccount.com",
		PrivateKey:  string(pemBytes),
		TokenURI:    "https://oauth2.example/token",
		ProjectID:   "demo-project",
	}, key
}

func mustPKCS8(t *testing.T, key *rsa.PrivateKey) []byte {
	t.Helper()
	der, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatalf("PKCS8 序列化失败: %v", err)
	}
	return der
}

func TestSignJWTIsVerifiableRS256(t *testing.T) {
	account, key := testAccount(t)
	now := time.Date(2026, 9, 19, 12, 0, 0, 0, time.UTC)
	token, err := signJWT(account, account.TokenURI, now, time.Hour)
	if err != nil {
		t.Fatalf("签 JWT 失败: %v", err)
	}
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		t.Fatalf("JWT 应该是三段: %s", token)
	}
	// 头必须是 RS256
	header, err := decodeSegment(parts[0])
	if err != nil {
		t.Fatalf("解头失败: %v", err)
	}
	if !strings.Contains(string(header), "RS256") {
		t.Fatalf("算法应为 RS256: %s", header)
	}
	// 声明要带 iss/scope/aud/exp（FCM 会校验这些）
	payload, err := decodeSegment(parts[1])
	if err != nil {
		t.Fatalf("解声明失败: %v", err)
	}
	var claims map[string]any
	if err := json.Unmarshal(payload, &claims); err != nil {
		t.Fatalf("声明不是 JSON: %v", err)
	}
	if claims["iss"] != account.ClientEmail {
		t.Fatalf("iss 不对: %v", claims["iss"])
	}
	if claims["scope"] != "https://www.googleapis.com/auth/firebase.messaging" {
		t.Fatalf("scope 不对: %v", claims["scope"])
	}
	if int64(claims["exp"].(float64)) <= now.Unix() {
		t.Fatal("exp 必须在将来")
	}
	// 签名要能被对应公钥验过（否则 Google 一定拒）
	signature, err := decodeSegment(parts[2])
	if err != nil {
		t.Fatalf("解签名失败: %v", err)
	}
	digest := sha256Sum(parts[0] + "." + parts[1])
	if err := rsa.VerifyPKCS1v15(&key.PublicKey, cryptoSHA256, digest, signature); err != nil {
		t.Fatalf("签名验不过: %v", err)
	}
}

func TestLoadServiceAccountValidates(t *testing.T) {
	if _, err := LoadServiceAccount(""); err == nil {
		t.Fatal("空路径必须报错（未配置就该如实说未配置）")
	}
	dir := t.TempDir()
	path := dir + "/sa.json"
	// 缺 private_key
	if err := writeFile(path, `{"client_email":"a@b.c"}`); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadServiceAccount(path); err == nil {
		t.Fatal("缺 private_key 必须报错")
	}
	account, _ := testAccount(t)
	raw, _ := json.Marshal(account)
	if err := writeFile(path, string(raw)); err != nil {
		t.Fatal(err)
	}
	loaded, err := LoadServiceAccount(path)
	if err != nil {
		t.Fatalf("合法服务账号应能加载: %v", err)
	}
	if loaded.ProjectID != "demo-project" {
		t.Fatalf("project_id 解析不对: %s", loaded.ProjectID)
	}
}

func TestSenderExchangesTokenAndSends(t *testing.T) {
	account, _ := testAccount(t)
	var gotAuth string
	var gotPath string
	var gotBody map[string]any

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/token"):
			// 断言我们发的是 jwt-bearer 授权
			body, _ := ioReadAll(r)
			if !strings.Contains(body, "grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer") {
				t.Errorf("授权类型不对: %s", body)
			}
			if !strings.Contains(body, "assertion=") {
				t.Errorf("缺少 assertion: %s", body)
			}
			fmt.Fprint(w, `{"access_token":"ya29.test","expires_in":3600}`)
		case strings.Contains(r.URL.Path, "/messages:send"):
			gotAuth = r.Header.Get("Authorization")
			gotPath = r.URL.Path
			_ = json.NewDecoder(r.Body).Decode(&gotBody)
			fmt.Fprint(w, `{"name":"projects/demo/messages/1"}`)
		default:
			t.Errorf("意外请求: %s", r.URL.Path)
		}
	}))
	defer server.Close()

	sender := &Sender{
		projectID:  account.ProjectID,
		account:    &ServiceAccount{ClientEmail: account.ClientEmail, PrivateKey: account.PrivateKey, TokenURI: server.URL + "/token", ProjectID: account.ProjectID},
		client:     server.Client(),
		fcmBaseURL: server.URL,
		now:        func() time.Time { return time.Date(2026, 9, 19, 12, 0, 0, 0, time.UTC) },
	}

	// access token 缓存：连续取两次只应请求一次 token 端点
	first, err := sender.accessTokenValue(context.Background())
	if err != nil {
		t.Fatalf("取 token 失败: %v", err)
	}
	second, _ := sender.accessTokenValue(context.Background())
	if first != "ya29.test" || second != first {
		t.Fatalf("token 取值/缓存异常: %q %q", first, second)
	}

	status, _, err := sender.sendOne(context.Background(), first, "device-token-1", Message{
		Title: "行程已就绪", Body: "点击查看多智能体推演结果", Data: map[string]string{"link": "/trip/1"},
	})
	if err != nil || status != http.StatusOK {
		t.Fatalf("发送应成功: status=%d err=%v", status, err)
	}
	if gotAuth != "Bearer ya29.test" {
		t.Fatalf("Authorization 头不对: %q", gotAuth)
	}
	if gotPath != "/v1/projects/demo-project/messages:send" {
		t.Fatalf("投递路径不对: %s", gotPath)
	}
	message := gotBody["message"].(map[string]any)
	if message["token"] != "device-token-1" {
		t.Fatalf("token 未带上: %v", message["token"])
	}
	notification := message["notification"].(map[string]any)
	if notification["title"] != "行程已就绪" {
		t.Fatalf("标题不对: %v", notification["title"])
	}
	// data 里的 link 要原样带给前端 SW，点开才能跳到对应行程
	if message["data"].(map[string]any)["link"] != "/trip/1" {
		t.Fatal("data.link 必须原样带上（点开要跳转）")
	}
	// webpush.fcm_options.link 也要有
	webpush := message["webpush"].(map[string]any)
	if webpush["fcm_options"].(map[string]any)["link"] != "/trip/1" {
		t.Fatal("webpush.fcm_options.link 缺失")
	}
}

func TestTokenEndpointFailureIsReported(t *testing.T) {
	account, _ := testAccount(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		fmt.Fprint(w, `{"error":"invalid_grant"}`)
	}))
	defer server.Close()

	sender := &Sender{
		projectID:  "demo-project",
		account:    &ServiceAccount{ClientEmail: account.ClientEmail, PrivateKey: account.PrivateKey, TokenURI: server.URL + "/token"},
		client:     server.Client(),
		fcmBaseURL: server.URL,
		now:        time.Now,
	}
	if _, err := sender.accessTokenValue(context.Background()); err == nil {
		t.Fatal("取 token 失败必须报错，不能静默继续")
	} else if !strings.Contains(err.Error(), "401") {
		t.Fatalf("错误信息应带上状态码便于排查: %v", err)
	}
}

func TestSignJWTRejectsBrokenKey(t *testing.T) {
	account := &ServiceAccount{ClientEmail: "a@b.c", PrivateKey: "not-a-pem"}
	if _, err := signJWT(account, "https://x/token", time.Now(), time.Hour); err == nil {
		t.Fatal("坏私钥必须报错")
	}
}
