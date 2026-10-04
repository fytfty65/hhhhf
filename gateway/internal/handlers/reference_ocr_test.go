package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

// 参考路线**截图识别**的网关侧回归锁。
//
// 两条最要紧的口径：
//  1. 图片不落库 —— 用户上传的是别人的帖子截图，网关只能把它转发出去换成文字；
//  2. 识别不了就如实把原因带回前端 —— 绝不能返回一段"看起来像行程"的假文本。

func newReferenceOcrRouter(t *testing.T, userID string) *gin.Engine {
	t.Helper()
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.UserPreference{}); err != nil {
		t.Fatal(err)
	}
	router := gin.New()
	router.POST("/user/preference/reference-route/ocr", func(c *gin.Context) {
		c.Set("user_id", userID)
		RecognizeReferenceRouteHandler(c)
	})
	return router
}

func postOcr(router *gin.Engine, body map[string]any) *httptest.ResponseRecorder {
	raw, _ := json.Marshal(body)
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/user/preference/reference-route/ocr", strings.NewReader(string(raw))))
	return response
}

func TestReferenceOcrReturnsTextAndNeverPersistsTheImage(t *testing.T) {
	stub := newReferenceStub(t)
	stub.responseBody = `{"ok":true,"text":"第一天 博斯腾湖 环湖骑行 门票45元\n第二天 库车老街 烤包子","model":"qwen-vl-max","chars":30,"warnings":[],"note":"请先核对","privacy_note":"图片不会保存","image_stored":false}`
	router := newReferenceOcrRouter(t, "user-ocr")

	image := "data:image/png;base64," + strings.Repeat("A", 4096)
	response := postOcr(router, map[string]any{"image_base64": image, "mime_type": "image/png"})
	if response.Code != http.StatusOK {
		t.Fatalf("识别应成功: %d %s", response.Code, response.Body.String())
	}
	body := response.Body.String()
	if !strings.Contains(body, "博斯腾湖") {
		t.Fatalf("应回识别出的文字: %s", body)
	}
	if !strings.Contains(body, `"image_stored":false`) {
		t.Fatalf("必须显式告知图片未保存（前端据此如实展示）: %s", body)
	}
	if !strings.Contains(body, "图片不会保存") {
		t.Fatalf("应把隐私说明带回前端: %s", body)
	}
	// 转发给 ai-service 的应当是图片本身
	if sent, _ := stub.lastBody["image_base64"].(string); !strings.HasPrefix(sent, "data:image/png;base64,") {
		t.Fatalf("应把图片转发给识别服务: %#v", stub.lastBody)
	}

	// 硬约束：识别成功后库里也不能留下图片/文字（本接口只做识别，不落任何东西）
	var count int64
	database.DB.Model(&models.UserPreference{}).Where("user_id = ?", "user-ocr").Count(&count)
	if count != 0 {
		t.Fatal("识别接口不许写画像")
	}
	var prefs []models.UserPreference
	database.DB.Find(&prefs)
	for _, pref := range prefs {
		if strings.Contains(pref.ReferenceProfile, strings.Repeat("A", 64)) {
			t.Fatal("图片内容绝不能落库")
		}
	}
}

func TestReferenceOcrPreservesHonestFailureReasons(t *testing.T) {
	cases := []struct {
		name       string
		stubBody   string
		wantStatus int
		wantCode   string
		wantText   string
	}{
		{
			name:       "HEIC 等不支持的格式",
			stubBody:   `{"ok":false,"code":"unsupported_mime","message":"暂不支持 HEIC（iPhone 原图）：请先截图或转成 PNG/JPEG 再上传"}`,
			wantStatus: http.StatusBadRequest,
			wantCode:   "REFERENCE_OCR_UNSUPPORTED_MIME",
			wantText:   "HEIC",
		},
		{
			name:       "图片过大",
			stubBody:   `{"ok":false,"code":"too_large","message":"图片 5120KB 超过上限 4096KB"}`,
			wantStatus: http.StatusRequestEntityTooLarge,
			wantCode:   "REFERENCE_OCR_TOO_LARGE",
			wantText:   "超过上限",
		},
		{
			name:       "纯风景图没有文字",
			stubBody:   `{"ok":false,"code":"no_text_in_image","message":"这张图里没有识别到行程文字"}`,
			wantStatus: http.StatusUnprocessableEntity,
			wantCode:   "REFERENCE_OCR_NO_TEXT_IN_IMAGE",
			wantText:   "没有识别到行程文字",
		},
		{
			name:       "视觉模型未配置",
			stubBody:   `{"ok":false,"code":"vision_not_configured","message":"服务端未配置视觉模型（缺少 LLM_API_KEY），请改用粘贴文字"}`,
			wantStatus: http.StatusServiceUnavailable,
			wantCode:   "REFERENCE_OCR_VISION_NOT_CONFIGURED",
			wantText:   "LLM_API_KEY",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			stub := newReferenceStub(t)
			stub.responseBody = tc.stubBody
			router := newReferenceOcrRouter(t, "user-ocr-fail")

			response := postOcr(router, map[string]any{"image_base64": "data:image/png;base64,AAAA"})
			if response.Code != tc.wantStatus {
				t.Fatalf("状态码应为 %d: %d %s", tc.wantStatus, response.Code, response.Body.String())
			}
			body := response.Body.String()
			if !strings.Contains(body, tc.wantCode) {
				t.Fatalf("应带原因码 %s: %s", tc.wantCode, body)
			}
			if !strings.Contains(body, tc.wantText) {
				t.Fatalf("应把原因原话带回来（用户才知道怎么办）: %s", body)
			}
			if strings.Contains(body, `"text"`) {
				t.Fatalf("失败时绝不能带 text 字段（那会被误当成识别结果）: %s", body)
			}
		})
	}
}

func TestReferenceOcrRejectsEmptyAndOversizedImagesWithoutCallingUpstream(t *testing.T) {
	stub := newReferenceStub(t)
	router := newReferenceOcrRouter(t, "user-ocr-guard")

	response := postOcr(router, map[string]any{"image_base64": "   "})
	if response.Code != http.StatusBadRequest || !strings.Contains(response.Body.String(), "REFERENCE_IMAGE_REQUIRED") {
		t.Fatalf("空图片应被拒绝: %d %s", response.Code, response.Body.String())
	}

	response = postOcr(router, map[string]any{"image_base64": strings.Repeat("A", referenceImageMaxBase64Chars+1)})
	if response.Code != http.StatusRequestEntityTooLarge || !strings.Contains(response.Body.String(), "REFERENCE_IMAGE_TOO_LARGE") {
		t.Fatalf("超大图片应被拒绝: %d %s", response.Code, response.Body.String())
	}

	if stub.requestCount != 0 {
		t.Fatalf("本地就能拒绝的请求不许烧一次上游调用，实际调用 %d 次", stub.requestCount)
	}
}

func TestReferenceOcrReportsServiceOutageInsteadOfFakingText(t *testing.T) {
	stub := newReferenceStub(t)
	stub.responseCode = http.StatusBadGateway
	router := newReferenceOcrRouter(t, "user-ocr-outage")

	response := postOcr(router, map[string]any{"image_base64": "data:image/png;base64,AAAA"})
	if response.Code != http.StatusBadGateway || !strings.Contains(response.Body.String(), "REFERENCE_OCR_UNAVAILABLE") {
		t.Fatalf("识别服务不可用时应如实报错并引导粘贴文字: %d %s", response.Code, response.Body.String())
	}
	if !strings.Contains(response.Body.String(), "粘贴文字") {
		t.Fatalf("错误话术应给出替代路径: %s", response.Body.String())
	}
}

func TestReferenceOcrSurvivesUnparsableUpstreamPayload(t *testing.T) {
	stub := newReferenceStub(t)
	stub.responseBody = `{"ok":true,"text":""}`
	router := newReferenceOcrRouter(t, "user-ocr-empty")

	response := postOcr(router, map[string]any{"image_base64": "data:image/png;base64,AAAA"})
	if response.Code == http.StatusOK {
		t.Fatalf("上游 ok=true 但没文字时也不能算成功: %s", response.Body.String())
	}
	if !strings.Contains(response.Body.String(), "REFERENCE_OCR_") {
		t.Fatalf("应带原因码: %s", response.Body.String())
	}
}
