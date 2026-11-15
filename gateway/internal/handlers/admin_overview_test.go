package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func adminOverviewRouter(userID string) *gin.Engine {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(func(c *gin.Context) {
		c.Set("user_id", userID)
		c.Next()
	})
	router.GET("/api/v1/admin/overview", AdminOverviewHandler)
	return router
}

func callAdminOverview(t *testing.T, userID string) *httptest.ResponseRecorder {
	t.Helper()
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/admin/overview", nil)
	adminOverviewRouter(userID).ServeHTTP(recorder, request)
	return recorder
}

// 非管理员必须被服务端拒绝，且拒绝响应不能顺带泄露任何统计字段。
func TestAdminOverviewRejectsNonAdmin(t *testing.T) {
	old := os.Getenv("ADMIN_USER_IDS")
	defer os.Setenv("ADMIN_USER_IDS", old)
	os.Setenv("ADMIN_USER_IDS", "admin-1")

	recorder := callAdminOverview(t, "someone-else")
	if recorder.Code != http.StatusForbidden {
		t.Fatalf("非管理员应返回 403，实际 %d", recorder.Code)
	}
	var body map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
		t.Fatalf("解析响应失败: %v", err)
	}
	if _, leaked := body["data"]; leaked {
		t.Fatalf("拒绝响应不应包含统计字段: %s", recorder.Body.String())
	}
	if body["code"] != "ADMIN_FORBIDDEN" {
		t.Fatalf("错误码应为 ADMIN_FORBIDDEN，实际 %v", body["code"])
	}
}

// 管理员能拿到聚合数字，且计数来自真实表；比率在分母为 0 时返回 0 而不是编造。
func TestAdminOverviewAggregates(t *testing.T) {
	oldDB := database.DB
	oldAdmins := os.Getenv("ADMIN_USER_IDS")
	defer func() {
		database.DB = oldDB
		os.Setenv("ADMIN_USER_IDS", oldAdmins)
	}()
	os.Setenv("ADMIN_USER_IDS", "admin-1")

	db, err := gorm.Open(sqlite.Open("file:admin-overview-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if err := db.AutoMigrate(&models.User{}, &models.TripPlan{}, &models.Room{}, &models.RoomMember{}, &models.FeedbackLog{}, &models.PlanningEvent{}); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	database.DB = db

	if err := db.Create(&models.User{ID: "u1", Username: "u1"}).Error; err != nil {
		t.Fatalf("seed user: %v", err)
	}
	if err := db.Create(&models.User{ID: "u2", Username: "u2", IsGuest: true}).Error; err != nil {
		t.Fatalf("seed guest: %v", err)
	}
	if err := db.Create(&models.TripPlan{ID: "t1", UserID: "u1", Title: "杭州 3 日"}).Error; err != nil {
		t.Fatalf("seed trip: %v", err)
	}

	recorder := callAdminOverview(t, "admin-1")
	if recorder.Code != http.StatusOK {
		t.Fatalf("管理员应返回 200，实际 %d：%s", recorder.Code, recorder.Body.String())
	}

	var payload struct {
		Data struct {
			Users struct {
				Total  int64 `json:"total"`
				Guests int64 `json:"guests"`
			} `json:"users"`
			Trips struct {
				Total int64 `json:"total"`
			} `json:"trips"`
			Planning struct {
				Generated    int64   `json:"generated_7d"`
				AdoptionRate float64 `json:"adoption_rate"`
			} `json:"planning"`
			Scope string `json:"scope"`
		} `json:"data"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("解析响应失败: %v", err)
	}
	if payload.Data.Users.Total != 2 || payload.Data.Users.Guests != 1 {
		t.Fatalf("用户计数不对: %+v", payload.Data.Users)
	}
	if payload.Data.Trips.Total != 1 {
		t.Fatalf("路书计数不对: %+v", payload.Data.Trips)
	}
	if payload.Data.Planning.AdoptionRate != 0 {
		t.Fatalf("没有生成样本时采纳率应为 0，实际 %v", payload.Data.Planning.AdoptionRate)
	}
	if payload.Data.Scope != "aggregates_only" {
		t.Fatalf("必须声明只返回聚合数据，实际 %q", payload.Data.Scope)
	}
}

// 管理员身份必须在登录/资料响应里显式带出，否则前端永远看不到管理入口；
// 判断依据只有服务端 ADMIN_USER_IDS。
func TestBuildAuthUserMarksAdmin(t *testing.T) {
	t.Setenv("ADMIN_USER_IDS", "admin-1, admin-2")

	admin := buildAuthUser(models.User{ID: "admin-2", Username: "root"})
	if admin["isAdmin"] != true {
		t.Fatalf("白名单成员应带 isAdmin=true：%+v", admin)
	}
	normal := buildAuthUser(models.User{ID: "user-1", Username: "someone"})
	if normal["isAdmin"] != false {
		t.Fatalf("普通用户应带 isAdmin=false：%+v", normal)
	}
}

// provider_quality 是「请求 × 品类 × 供应商」的探针计数，不能混进用户行为漏斗；
// 看板必须单独给出探针总数、成功率与死供应商，否则会误导成"质量采样比生成频繁"。
func TestAdminOverviewSeparatesProviderProbesFromFunnel(t *testing.T) {
	oldDB := database.DB
	oldAdmins := os.Getenv("ADMIN_USER_IDS")
	defer func() {
		database.DB = oldDB
		os.Setenv("ADMIN_USER_IDS", oldAdmins)
	}()
	os.Setenv("ADMIN_USER_IDS", "admin-1")

	db, err := gorm.Open(sqlite.Open("file:admin-overview-probes?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if err := db.AutoMigrate(&models.User{}, &models.TripPlan{}, &models.Room{}, &models.RoomMember{}, &models.FeedbackLog{}, &models.PlanningEvent{}); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	database.DB = db

	events := []models.PlanningEvent{
		{EventType: "plan_generated", Payload: `{"module":"transport"}`},
		{EventType: "plan_generated", Payload: `{"module":"lodging"}`},
		{EventType: "provider_quality", Payload: `{"kind":"transport","provider":"Tuniu","status":"empty","candidate_count":0,"latency_ms":90}`},
		{EventType: "provider_quality", Payload: `{"kind":"transport","provider":"Tuniu","status":"empty","candidate_count":0,"latency_ms":110}`},
		{EventType: "provider_quality", Payload: `{"kind":"lodging","provider":"RollingGo","status":"ok","candidate_count":5,"latency_ms":400}`},
	}
	for index := range events {
		if err := db.Create(&events[index]).Error; err != nil {
			t.Fatalf("seed event: %v", err)
		}
	}

	recorder := callAdminOverview(t, "admin-1")
	if recorder.Code != http.StatusOK {
		t.Fatalf("管理员应返回 200，实际 %d：%s", recorder.Code, recorder.Body.String())
	}

	var payload struct {
		Data struct {
			Planning struct {
				Generated int64 `json:"generated_7d"`
				ByType    []struct {
					Type  string `json:"type"`
					Count int64  `json:"count"`
				} `json:"by_type"`
				ProviderProbes struct {
					Total        int64   `json:"total"`
					OK           int64   `json:"ok"`
					Empty        int64   `json:"empty"`
					SuccessRate  float64 `json:"success_rate"`
					PerGenerated float64 `json:"per_generated"`
					ByKind       []struct {
						Name   string `json:"name"`
						Probes int64  `json:"probes"`
					} `json:"by_kind"`
					Failing []struct {
						Provider string `json:"provider"`
						Probes   int64  `json:"probes"`
					} `json:"failing_providers"`
				} `json:"provider_probes"`
			} `json:"planning"`
		} `json:"data"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("解析响应失败: %v", err)
	}

	planning := payload.Data.Planning
	if planning.Generated != 2 {
		t.Fatalf("plan_generated 应为 2，实际 %d", planning.Generated)
	}
	for _, row := range planning.ByType {
		if row.Type == "provider_quality" {
			t.Fatalf("provider_quality 不应出现在用户行为漏斗里: %s", recorder.Body.String())
		}
	}
	probes := planning.ProviderProbes
	if probes.Total != 3 || probes.OK != 1 || probes.Empty != 2 {
		t.Fatalf("探针计数不对: %+v", probes)
	}
	if probes.SuccessRate != 0.33 {
		t.Fatalf("成功率应为 0.33，实际 %v", probes.SuccessRate)
	}
	if probes.PerGenerated != 1.5 {
		t.Fatalf("每次生成对应 1.5 次探针，实际 %v", probes.PerGenerated)
	}
	if len(probes.ByKind) != 2 || probes.ByKind[0].Name != "transport" || probes.ByKind[0].Probes != 2 {
		t.Fatalf("按品类聚合不对: %+v", probes.ByKind)
	}
	if len(probes.Failing) != 1 || probes.Failing[0].Provider != "Tuniu" || probes.Failing[0].Probes != 2 {
		t.Fatalf("应识别出零成功率的供应商 Tuniu: %+v", probes.Failing)
	}
}
