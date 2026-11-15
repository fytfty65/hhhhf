package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

// 临时账号（免注册试用）的行为契约：
//  - 转正是原地改字段，账号 ID 不变，所以行程不需要迁移；
//  - 只有临时账号能被转正，正式账号不会被误转；
//  - 过期清理只删临时账号，且窗口内的临时账号与任何正式账号都不受影响。

func guestTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })

	migrate := []any{
		&models.User{}, &models.TripPlan{}, &models.DeviceSession{},
	}
	for _, table := range guestOwnedTables() {
		migrate = append(migrate, table.Model)
	}
	if err := db.AutoMigrate(migrate...); err != nil {
		t.Fatal(err)
	}
	return db
}

func guestRouter(userID string, method, path string, handler gin.HandlerFunc) *gin.Engine {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Handle(method, path, func(c *gin.Context) {
		c.Set("user_id", userID)
		handler(c)
	})
	return r
}

func decodeBody(t *testing.T, response *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var body map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode %s: %v", response.Body.String(), err)
	}
	return body
}

func createGuestUser(t *testing.T, db *gorm.DB, id, username string) models.User {
	t.Helper()
	now := time.Now().UTC()
	user := models.User{
		ID:           id,
		Username:     username,
		PasswordHash: "hash-placeholder",
		IsGuest:      true,
		LastActiveAt: &now,
	}
	if err := db.Create(&user).Error; err != nil {
		t.Fatal(err)
	}
	return user
}

func TestUpgradeGuestAccountKeepsDataAndUpdatesIdentity(t *testing.T) {
	db := guestTestDB(t)
	createGuestUser(t, db, "guest-1", "guest_tour_abc")
	if err := db.Create(&models.TripPlan{ID: "trip-1", UserID: "guest-1", Title: "洛阳 3 日游"}).Error; err != nil {
		t.Fatal(err)
	}

	r := guestRouter("guest-1", http.MethodPost, "/upgrade", UpgradeGuestAccountHandler)
	payload, _ := json.Marshal(map[string]any{
		"username": "alice",
		"password": "secret123",
		"nickname": "Alice",
	})
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/upgrade", bytes.NewReader(payload)))
	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}

	body := decodeBody(t, response)
	user, _ := body["user"].(map[string]any)
	if user["username"] != "alice" || user["nickname"] != "Alice" {
		t.Fatalf("unexpected upgraded user: %#v", user)
	}
	if user["isGuest"] != false {
		t.Fatalf("upgraded account must not stay a guest: %#v", user)
	}
	if _, ok := user["id"].(string); !ok {
		t.Fatalf("expected user id to be returned: %#v", user)
	}

	var stored models.User
	if err := db.Where("id = ?", "guest-1").First(&stored).Error; err != nil {
		t.Fatal(err)
	}
	if stored.IsGuest {
		t.Fatalf("is_guest should be cleared in the database")
	}
	if stored.Username != "alice" {
		t.Fatalf("expected username alice, got %q", stored.Username)
	}
	if stored.PasswordHash == "hash-placeholder" {
		t.Fatalf("password hash should be replaced by a bcrypt hash")
	}

	// 数据归属仍然是同一个 user_id：转正不需要迁移任何行。
	var trips int64
	if err := db.Model(&models.TripPlan{}).Where("user_id = ?", "guest-1").Count(&trips).Error; err != nil {
		t.Fatal(err)
	}
	if trips != 1 {
		t.Fatalf("expected the trip to stay on the same account, found %d", trips)
	}
}

func TestUpgradeGuestAccountRejectsInvalidRequests(t *testing.T) {
	db := guestTestDB(t)
	now := time.Now().UTC()
	if err := db.Create(&models.User{ID: "guest-2", Username: "guest_tour_xyz", IsGuest: true, LastActiveAt: &now}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.User{ID: "formal-1", Username: "bobby", LastActiveAt: &now}).Error; err != nil {
		t.Fatal(err)
	}

	cases := []struct {
		name     string
		body     map[string]any
		userID   string
		expected int
		code     string
	}{
		{"非临时账号不能转正", map[string]any{"username": "bobby2", "password": "secret123"}, "formal-1", http.StatusConflict, "NOT_GUEST_ACCOUNT"},
		{"用户名已被占用", map[string]any{"username": "bobby", "password": "secret123"}, "guest-2", http.StatusConflict, "USERNAME_TAKEN"},
		{"保留 guest_ 前缀", map[string]any{"username": "guest_alice", "password": "secret123"}, "guest-2", http.StatusBadRequest, "USERNAME_RESERVED"},
		{"密码过短", map[string]any{"username": "alice", "password": "12345"}, "guest-2", http.StatusBadRequest, "INVALID_UPGRADE_REQUEST"},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			r := guestRouter(item.userID, http.MethodPost, "/upgrade", UpgradeGuestAccountHandler)
			payload, _ := json.Marshal(item.body)
			response := httptest.NewRecorder()
			r.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/upgrade", bytes.NewReader(payload)))
			if response.Code != item.expected {
				t.Fatalf("expected %d, got %d: %s", item.expected, response.Code, response.Body.String())
			}
			body := decodeBody(t, response)
			if body["code"] != item.code {
				t.Fatalf("expected code %s, got %#v", item.code, body["code"])
			}
		})
	}
}

func TestCleanupExpiredGuestAccountsOnlyRemovesExpiredGuests(t *testing.T) {
	db := guestTestDB(t)
	now := time.Now().UTC()
	old := now.Add(-40 * 24 * time.Hour)
	older := now.Add(-60 * 24 * time.Hour)

	lastActive := func(at time.Time) *time.Time { return &at }
	if err := db.Create(&models.User{ID: "guest-expired", Username: "guest_a", IsGuest: true, LastActiveAt: lastActive(old), CreatedAt: old}).Error; err != nil {
		t.Fatal(err)
	}
	// 早期只有 guest_ 前缀、没有标记字段的历史账号也必须被清理。
	if err := db.Create(&models.User{ID: "guest-legacy", Username: "guest_b", CreatedAt: older}).Error; err != nil {
		t.Fatal(err)
	}
	// 窗口内仍是临时账号：保留。
	if err := db.Create(&models.User{ID: "guest-fresh", Username: "guest_c", IsGuest: true, LastActiveAt: lastActive(now)}).Error; err != nil {
		t.Fatal(err)
	}
	// 从未活跃但创建时间还很近：保留。
	if err := db.Create(&models.User{ID: "guest-new-null", Username: "guest_d", IsGuest: true, CreatedAt: now}).Error; err != nil {
		t.Fatal(err)
	}
	// 正式账号长期不用也不会被删。
	if err := db.Create(&models.User{ID: "formal-old", Username: "carol", LastActiveAt: lastActive(older), CreatedAt: older}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.TripPlan{ID: "trip-expired", UserID: "guest-expired", Title: "过期账号的行程"}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.TripPlan{ID: "trip-formal", UserID: "formal-old", Title: "正式账号的行程"}).Error; err != nil {
		t.Fatal(err)
	}

	cleanupExpiredGuestAccounts()

	var remaining []models.User
	if err := db.Order("id asc").Find(&remaining).Error; err != nil {
		t.Fatal(err)
	}
	remainingIDs := make([]string, 0, len(remaining))
	for _, user := range remaining {
		remainingIDs = append(remainingIDs, user.ID)
	}
	expected := []string{"formal-old", "guest-fresh", "guest-new-null"}
	if len(remainingIDs) != len(expected) {
		t.Fatalf("expected %v, got %v", expected, remainingIDs)
	}
	for index, id := range expected {
		if remainingIDs[index] != id {
			t.Fatalf("expected %v, got %v", expected, remainingIDs)
		}
	}

	var expiredTrips int64
	if err := db.Model(&models.TripPlan{}).Where("user_id = ?", "guest-expired").Count(&expiredTrips).Error; err != nil {
		t.Fatal(err)
	}
	if expiredTrips != 0 {
		t.Fatalf("expired guest trip should be removed, found %d", expiredTrips)
	}
	var formalTrips int64
	if err := db.Model(&models.TripPlan{}).Where("user_id = ?", "formal-old").Count(&formalTrips).Error; err != nil {
		t.Fatal(err)
	}
	if formalTrips != 1 {
		t.Fatalf("formal account trip must be kept, found %d", formalTrips)
	}
}

func TestGuestCleanupCanBeDisabledByEnv(t *testing.T) {
	db := guestTestDB(t)
	now := time.Now().UTC()
	old := now.Add(-40 * 24 * time.Hour)
	value := old
	if err := db.Create(&models.User{ID: "guest-keep", Username: "guest_keep", IsGuest: true, LastActiveAt: &value, CreatedAt: old}).Error; err != nil {
		t.Fatal(err)
	}

	t.Setenv("GUEST_ACCOUNT_CLEANUP_ENABLED", "false")
	cleanupExpiredGuestAccounts()

	var count int64
	if err := db.Model(&models.User{}).Where("id = ?", "guest-keep").Count(&count).Error; err != nil {
		t.Fatal(err)
	}
	if count != 1 {
		t.Fatalf("cleanup must be a no-op when disabled, found %d", count)
	}
}

func TestRegisterMarksGuestAccountsAndReportsPolicy(t *testing.T) {
	db := guestTestDB(t)
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/api/auth/register", RegisterHandler)
	r.POST("/api/auth/login", LoginHandler)
	r.GET("/api/user/profile", AuthMiddleware(), GetProfileHandler)

	payload, _ := json.Marshal(map[string]any{
		"username": "guest_trial_1",
		"password": "secret123",
		"nickname": "试用中",
		"guest":    true,
	})
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/api/auth/register", bytes.NewReader(payload)))
	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
	body := decodeBody(t, response)
	user, _ := body["user"].(map[string]any)
	if user["isGuest"] != true {
		t.Fatalf("guest registration must be flagged: %#v", user)
	}

	var stored models.User
	if err := db.Where("username = ?", "guest_trial_1").First(&stored).Error; err != nil {
		t.Fatal(err)
	}
	if !stored.IsGuest {
		t.Fatalf("is_guest should be persisted")
	}
	if stored.LastActiveAt == nil {
		t.Fatalf("registration must record last_active_at")
	}

	// 登录后临时账号带上短期 access token，个人主页要同时报告
	// 该账号是临时的以及清理窗口，前端据此展示数据归属文案。
	loginPayload, _ := json.Marshal(map[string]any{"username": "guest_trial_1", "password": "secret123"})
	login := httptest.NewRecorder()
	r.ServeHTTP(login, httptest.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader(loginPayload)))
	if login.Code != http.StatusOK {
		t.Fatalf("expected login 200, got %d: %s", login.Code, login.Body.String())
	}
	loginBody := decodeBody(t, login)
	token, _ := loginBody["token"].(string)
	if token == "" {
		t.Fatalf("expected an access token: %#v", loginBody)
	}

	request := httptest.NewRequest(http.MethodGet, "/api/user/profile", nil)
	request.Header.Set("Authorization", "Bearer "+token)
	profile := httptest.NewRecorder()
	r.ServeHTTP(profile, request)
	if profile.Code != http.StatusOK {
		t.Fatalf("expected profile 200, got %d: %s", profile.Code, profile.Body.String())
	}
	profileBody := decodeBody(t, profile)
	profileUser, _ := profileBody["user"].(map[string]any)
	if profileUser["isGuest"] != true {
		t.Fatalf("profile must report isGuest: %#v", profileUser)
	}
	policy, _ := profileBody["guest_policy"].(map[string]any)
	if policy["retention_days"] != float64(30) || policy["cleanup_enabled"] != true {
		t.Fatalf("unexpected guest policy: %#v", policy)
	}
}
