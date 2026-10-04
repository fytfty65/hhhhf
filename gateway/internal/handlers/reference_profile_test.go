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

// 参考路线画像的网关侧回归锁。
//
// 三条硬约束里最要紧的是**不存原文** —— 用户粘贴的是别人的帖子正文，
// 只允许保存"抽取后的特征"。这条用测试钉死，而不是靠注释。

type referenceStub struct {
	server       *httptest.Server
	lastBody     map[string]any
	responseBody string
	responseCode int
	requestCount int
}

func newReferenceStub(t *testing.T) *referenceStub {
	t.Helper()
	stub := &referenceStub{
		responseCode: http.StatusOK,
		responseBody: `{"profile":{"features":{"category_mix":{"scenic":3,"food":2}},"evidence":{"pace":"节奏轻松，不赶"},"confidence":0.6,"reference_count":2},"hints":{"prefer_scenic":true},"note":"只保存抽取后的特征"}`,
	}
	stub.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		stub.requestCount++
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err == nil {
			stub.lastBody = body
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(stub.responseCode)
		_, _ = w.Write([]byte(stub.responseBody))
	}))
	t.Cleanup(stub.server.Close)
	t.Setenv("AI_SERVICE_URL", stub.server.URL)
	return stub
}

func newReferenceRouter(t *testing.T, userID string) *gin.Engine {
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
	router.POST("/user/preference/reference-route", func(c *gin.Context) { c.Set("user_id", userID); SaveReferenceRouteHandler(c) })
	router.GET("/user/preference/reference-route", func(c *gin.Context) { c.Set("user_id", userID); GetReferenceRouteHandler(c) })
	router.DELETE("/user/preference/reference-route", func(c *gin.Context) { c.Set("user_id", userID); ClearReferenceRouteHandler(c) })
	return router
}

const rawReferenceText = "乌鲁木齐4天，人均300，住民宿，南山牧场看雪山，晚上夜市吃烧烤大盘鸡，节奏轻松"

func TestReferenceRouteStoresFeaturesAndFragmentsButNeverTheWholeText(t *testing.T) {
	stub := newReferenceStub(t)
	router := newReferenceRouter(t, "user-ref")

	body, _ := json.Marshal(map[string]any{"text": rawReferenceText, "source_label": "小红书"})
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/user/preference/reference-route", strings.NewReader(string(body))))
	if response.Code != http.StatusOK {
		t.Fatalf("保存参考路线失败: %d %s", response.Code, response.Body.String())
	}
	if stub.requestCount != 1 {
		t.Fatalf("应当调用一次抽取服务，实际 %d", stub.requestCount)
	}

	var preference models.UserPreference
	if err := database.DB.Where("user_id = ?", "user-ref").First(&preference).Error; err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(preference.ReferenceProfile, "category_mix") {
		t.Fatalf("特征没有落库: %q", preference.ReferenceProfile)
	}
	// 默认会保存出处片段（用于在界面上解释依据）—— 这是**有意**的，文案里也必须这么写
	if !strings.Contains(preference.ReferenceProfile, "evidence") {
		t.Fatalf("默认应保存出处片段: %q", preference.ReferenceProfile)
	}
	// 但**整篇原文**绝不落库
	if strings.Contains(preference.ReferenceProfile, rawReferenceText) {
		t.Fatalf("整篇原文不允许落库: %q", preference.ReferenceProfile)
	}
	if preference.ReferenceCount != 2 {
		t.Fatalf("参考条数应来自抽取结果（2），实际 %d", preference.ReferenceCount)
	}
	if !strings.Contains(response.Body.String(), "出处片段") {
		t.Fatalf("响应必须如实说明保存了出处片段: %s", response.Body.String())
	}
}

func TestReferenceRouteCanSkipEvidenceAndGetReturnsCount(t *testing.T) {
	newReferenceStub(t)
	router := newReferenceRouter(t, "user-privacy")

	body, _ := json.Marshal(map[string]any{"text": rawReferenceText, "keep_evidence": false})
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/user/preference/reference-route", strings.NewReader(string(body))))
	if response.Code != http.StatusOK {
		t.Fatalf("保存失败: %d %s", response.Code, response.Body.String())
	}
	var preference models.UserPreference
	if err := database.DB.Where("user_id = ?", "user-privacy").First(&preference).Error; err != nil {
		t.Fatal(err)
	}
	if strings.Contains(preference.ReferenceProfile, "evidence") || strings.Contains(preference.ReferenceProfile, "节奏轻松") {
		t.Fatalf("选择不留片段时不允许保存任何原文片段: %q", preference.ReferenceProfile)
	}
	if !strings.Contains(preference.ReferenceProfile, "category_mix") {
		t.Fatalf("特征仍要保存: %q", preference.ReferenceProfile)
	}

	// GET 回读要能报出参考条数（前端要靠它显示"参考了 N 条"）
	readResponse := httptest.NewRecorder()
	router.ServeHTTP(readResponse, httptest.NewRequest(http.MethodGet, "/user/preference/reference-route", nil))
	if readResponse.Code != http.StatusOK || !strings.Contains(readResponse.Body.String(), `"reference_count":2`) {
		t.Fatalf("GET 回读条数不对: %d %s", readResponse.Code, readResponse.Body.String())
	}
}

func TestReferenceRoutePassesExistingProfileForMerge(t *testing.T) {
	stub := newReferenceStub(t)
	// 注意顺序：newReferenceRouter 会新建内存库，必须**先建 router 再插数据**
	router := newReferenceRouter(t, "user-merge")
	if err := database.DB.Create(&models.UserPreference{
		UserID: "user-merge", ReferenceProfile: `{"features":{"category_mix":{"scenic":1}},"reference_count":1}`,
	}).Error; err != nil {
		t.Fatal(err)
	}

	body, _ := json.Marshal(map[string]any{"text": rawReferenceText})
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/user/preference/reference-route", strings.NewReader(string(body))))
	if response.Code != http.StatusOK {
		t.Fatalf("保存失败: %d", response.Code)
	}
	existing, ok := stub.lastBody["existing_profile"].(map[string]any)
	if !ok || len(existing) == 0 {
		t.Fatalf("已有画像必须一起送给抽取服务（否则多条参考无法合并）: %#v", stub.lastBody)
	}
}

func TestReferenceRouteReportsExtractFailureInsteadOfFakingSuccess(t *testing.T) {
	stub := newReferenceStub(t)
	stub.responseCode = http.StatusBadGateway
	router := newReferenceRouter(t, "user-fail")

	body, _ := json.Marshal(map[string]any{"text": rawReferenceText})
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/user/preference/reference-route", strings.NewReader(string(body))))
	if response.Code != http.StatusBadGateway || !strings.Contains(response.Body.String(), "REFERENCE_EXTRACT_UNAVAILABLE") {
		t.Fatalf("抽取服务不可用时必须如实报错，不能让用户以为学到了: %d %s", response.Code, response.Body.String())
	}
	var count int64
	database.DB.Model(&models.UserPreference{}).Where("user_id = ?", "user-fail").Count(&count)
	if count != 0 {
		t.Fatal("失败时不能落任何画像")
	}
}

func TestReferenceRouteRejectsEmptyTextAndClearsOnDelete(t *testing.T) {
	newReferenceStub(t)
	router := newReferenceRouter(t, "user-clear")

	body, _ := json.Marshal(map[string]any{"text": "   "})
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/user/preference/reference-route", strings.NewReader(string(body))))
	if response.Code != http.StatusBadRequest || !strings.Contains(response.Body.String(), "REFERENCE_TEXT_REQUIRED") {
		t.Fatalf("空文本应被拒绝并说明原因: %d %s", response.Code, response.Body.String())
	}

	if err := database.DB.Create(&models.UserPreference{
		UserID: "user-clear", ReferenceProfile: `{"features":{"category_mix":{"scenic":2}}}`, ReferenceCount: 1,
	}).Error; err != nil {
		t.Fatal(err)
	}
	response = httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodDelete, "/user/preference/reference-route", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("清除失败: %d %s", response.Code, response.Body.String())
	}
	var preference models.UserPreference
	if err := database.DB.Where("user_id = ?", "user-clear").First(&preference).Error; err != nil {
		t.Fatal(err)
	}
	if preference.ReferenceProfile != "" || preference.ReferenceCount != 0 {
		t.Fatalf("清除后应不留画像: %+v", preference)
	}
}
