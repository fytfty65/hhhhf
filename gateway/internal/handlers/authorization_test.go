package handlers

import (
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func TestRequireRoomMemberRejectsNonMember(t *testing.T) {
	oldDB := database.DB
	t.Cleanup(func() { database.DB = oldDB })
	db, err := gorm.Open(sqlite.Open("file:authz-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&models.Room{}, &models.RoomMember{}); err != nil {
		t.Fatal(err)
	}
	database.DB = db
	if err := db.Create(&models.Room{ID: "room-a", InviteCode: "ROOMA", Name: "A", CreatorID: "owner"}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.RoomMember{RoomID: "room-a", UserID: "member"}).Error; err != nil {
		t.Fatal(err)
	}

	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Set("user_id", "outsider")
	if _, ok := requireRoomMember(c, "room-a"); ok {
		t.Fatal("expected non-member to be rejected")
	}
	if c.Writer.Status() != http.StatusForbidden {
		t.Fatalf("expected 403, got %d", c.Writer.Status())
	}
}

func TestRequireRoomMemberAcceptsMember(t *testing.T) {
	oldDB := database.DB
	t.Cleanup(func() { database.DB = oldDB })
	db, err := gorm.Open(sqlite.Open("file:authz-member-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&models.RoomMember{}); err != nil {
		t.Fatal(err)
	}
	database.DB = db
	if err := db.Create(&models.RoomMember{RoomID: "room-b", UserID: "member"}).Error; err != nil {
		t.Fatal(err)
	}
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Set("user_id", "member")
	got, ok := requireRoomMember(c, "room-b")
	if !ok || got != "member" {
		t.Fatalf("expected member authorization, got %q / %v", got, ok)
	}
}

func TestRequireTripOrRoomAccessScopesBothResourceKinds(t *testing.T) {
	oldDB := database.DB
	t.Cleanup(func() { database.DB = oldDB })
	db, err := gorm.Open(sqlite.Open("file:trip-or-room-authz-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&models.TripPlan{}, &models.RoomMember{}); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.TripPlan{ID: "trip-owner", UserID: "owner", Title: "我的行程"}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.RoomMember{RoomID: "ROOM01", UserID: "member"}).Error; err != nil {
		t.Fatal(err)
	}
	database.DB = db

	newContext := func(userID string) *gin.Context {
		c, _ := gin.CreateTestContext(httptest.NewRecorder())
		c.Set("user_id", userID)
		return c
	}
	if _, ok := requireTripOrRoomAccess(newContext("owner"), "trip-owner"); !ok {
		t.Fatal("trip owner should be authorized")
	}
	if _, ok := requireTripOrRoomAccess(newContext("member"), "ROOM01"); !ok {
		t.Fatal("room member should be authorized")
	}
	outsider := newContext("outsider")
	if _, ok := requireTripOrRoomAccess(outsider, "trip-owner"); ok || outsider.Writer.Status() != http.StatusForbidden {
		t.Fatalf("trip outsider should be rejected with 403, got ok=%v status=%d", ok, outsider.Writer.Status())
	}
	missing := newContext("owner")
	if _, ok := requireTripOrRoomAccess(missing, "a-city-name"); ok || missing.Writer.Status() != http.StatusNotFound {
		t.Fatalf("arbitrary display value should not authorize, got ok=%v status=%d", ok, missing.Writer.Status())
	}
}

func TestFixedWindowLimiter(t *testing.T) {
	limiter := newFixedWindowLimiter(2, time.Minute)
	now := time.Now()
	if ok, _ := limiter.allow("u1", now); !ok {
		t.Fatal("first request should pass")
	}
	if ok, _ := limiter.allow("u1", now); !ok {
		t.Fatal("second request should pass")
	}
	if ok, retry := limiter.allow("u1", now); ok || retry <= 0 {
		t.Fatalf("third request should be limited, retry=%v", retry)
	}
	if ok, _ := limiter.allow("u1", now.Add(time.Minute)); !ok {
		t.Fatal("request after window should pass")
	}
}

func TestRequestTokenQueryOnlyForWebSocket(t *testing.T) {
	gin.SetMode(gin.TestMode)
	httpReq := httptest.NewRequest(http.MethodGet, "/protected?access_token=query-token", nil)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Request = httpReq
	if got := requestToken(c); got != "" {
		t.Fatalf("query token must not authenticate ordinary HTTP, got %q", got)
	}
	wsReq := httptest.NewRequest(http.MethodGet, "/ws?access_token=query-token", nil)
	wsReq.Header.Set("Upgrade", "websocket")
	c.Request = wsReq
	if got := requestToken(c); got != "query-token" {
		t.Fatalf("expected websocket query token, got %q", got)
	}
}

func TestValidateOriginAllowlistProduction(t *testing.T) {
	oldEnv, oldOrigins := os.Getenv("APP_ENV"), os.Getenv("ALLOWED_ORIGINS")
	t.Cleanup(func() {
		_ = os.Setenv("APP_ENV", oldEnv)
		_ = os.Setenv("ALLOWED_ORIGINS", oldOrigins)
	})
	_ = os.Setenv("APP_ENV", "production")
	_ = os.Unsetenv("ALLOWED_ORIGINS")
	if err := ValidateOriginAllowlist(); err == nil {
		t.Fatal("expected production origin allowlist validation to fail when unset")
	}
}
