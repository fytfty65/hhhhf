package handlers

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"gateway/internal/push"
	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

type fakeProviderQualityPushSender struct {
	result *push.SendResult
	err    error
	calls  int
}

func (sender *fakeProviderQualityPushSender) SendToUser(_ context.Context, _ string, _ push.Message) (*push.SendResult, error) {
	sender.calls++
	return sender.result, sender.err
}

func TestProviderQualityAlertStateHonorsCooldown(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.ProviderQualityAlertState{}, &models.RiskNotification{}, &models.ProviderQualityAlertDelivery{}); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PROVIDER_QUALITY_ALERT_COOLDOWN_SECONDS", "60")
	base := time.Date(2026, 9, 27, 0, 0, 0, 0, time.UTC)
	first, err := recordProviderQualityAlertState(db, "user-private", "trip-private", "digest-a", "blocked", base)
	if err != nil || !first {
		t.Fatalf("first alert should be emitted: %v %v", first, err)
	}
	second, err := recordProviderQualityAlertState(db, "user-private", "trip-private", "digest-a", "blocked", base.Add(30*time.Second))
	if err != nil || second {
		t.Fatalf("duplicate inside cooldown must be suppressed: %v %v", second, err)
	}
	third, err := recordProviderQualityAlertState(db, "user-private", "trip-private", "digest-a", "blocked", base.Add(61*time.Second))
	if err != nil || !third {
		t.Fatalf("alert after cooldown should be emitted: %v %v", third, err)
	}
	var state models.ProviderQualityAlertState
	if err := db.First(&state).Error; err != nil {
		t.Fatal(err)
	}
	if state.NotificationCount != 2 || state.ScopeHash == "user-private" || state.ScopeHash == "trip-private" {
		t.Fatalf("state must keep aggregate counters and hashed scope: %#v", state)
	}
}

func TestProviderQualityNotificationMaterializationIsIdempotentPerCooldown(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.ProviderQualityAlertState{}, &models.RiskNotification{}, &models.ProviderQualityAlertDelivery{}); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PROVIDER_QUALITY_ALERT_COOLDOWN_SECONDS", "60")
	base := time.Date(2026, 9, 27, 0, 0, 0, 0, time.UTC)
	if err := materializeProviderQualityNotification(db, "user-private", "trip-private", "digest-a", "blocked", base); err != nil {
		t.Fatal(err)
	}
	if err := materializeProviderQualityNotification(db, "user-private", "trip-private", "digest-a", "blocked", base.Add(30*time.Second)); err != nil {
		t.Fatal(err)
	}
	if err := materializeProviderQualityNotification(db, "user-private", "trip-private", "digest-a", "blocked", base.Add(61*time.Second)); err != nil {
		t.Fatal(err)
	}
	var notifications []models.RiskNotification
	if err := db.Find(&notifications).Error; err != nil {
		t.Fatal(err)
	}
	if len(notifications) != 2 {
		t.Fatalf("expected one notification per cooldown bucket, got %d", len(notifications))
	}
	for _, notification := range notifications {
		if notification.Source != "omniroute-quality-gate" || notification.Type != "provider_quality_gate" || notification.Severity != "HIGH" || notification.Status != "unread" {
			t.Fatalf("unexpected notification contract: %#v", notification)
		}
		if notification.Detail == "" || notification.UserID != "user-private" {
			t.Fatalf("notification missing in-app fields: %#v", notification)
		}
	}
	var deliveries []models.ProviderQualityAlertDelivery
	if err := db.Find(&deliveries).Error; err != nil || len(deliveries) != 2 {
		t.Fatalf("expected pending delivery ledger per notification bucket: %d %v", len(deliveries), err)
	}
	for _, delivery := range deliveries {
		if delivery.Channel != "in_app" || delivery.Status != "pending" || delivery.Attempts != 0 || delivery.ScopeHash == "user-private" {
			t.Fatalf("unexpected pending delivery: %#v", delivery)
		}
	}
}

func TestProviderQualityDeliveryHandlerReturnsLifecycleWithoutScopeIdentity(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.ProviderQualityAlertState{}, &models.RiskNotification{}, &models.ProviderQualityAlertDelivery{}); err != nil {
		t.Fatal(err)
	}
	when := time.Now().UTC()
	if err := materializeProviderQualityNotification(db, "user-private", "", "digest-a", "blocked", when); err != nil {
		t.Fatal(err)
	}
	r := gin.New()
	r.GET("/planning/providers/quality/alerts/delivery", func(c *gin.Context) {
		c.Set("user_id", "user-private")
		ProviderQualityAlertDeliveryHandler(c)
	})
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/planning/providers/quality/alerts/delivery", nil))
	if response.Code != http.StatusOK || strings.Contains(response.Body.String(), "user-private") || strings.Contains(response.Body.String(), "trip-private") {
		t.Fatalf("unexpected delivery response: %d %s", response.Code, response.Body.String())
	}
	var envelope map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	data := envelope["data"].(map[string]any)
	deliveries := data["deliveries"].([]any)
	if len(deliveries) != 1 || deliveries[0].(map[string]any)["status"] != "pending" {
		t.Fatalf("unexpected delivery lifecycle: %#v", deliveries)
	}
}

func TestProviderQualityDeliveryClaimLeaseAndRetryStateMachine(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.ProviderQualityAlertDelivery{}); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PROVIDER_QUALITY_DELIVERY_MAX_ATTEMPTS", "2")
	now := time.Date(2026, 9, 27, 0, 0, 0, 0, time.UTC)
	if err := db.Create(&models.ProviderQualityAlertDelivery{ID: "delivery-1", ScopeHash: "scope-hash", NotificationID: "notice-1", Channel: "web_push", Status: "pending", CreatedAt: now, UpdatedAt: now}).Error; err != nil {
		t.Fatal(err)
	}
	claimed, err := claimProviderQualityDeliveries(db, 10, now)
	if err != nil || len(claimed) != 1 || claimed[0].Status != "sending" || claimed[0].Attempts != 1 || claimed[0].LeaseUntil == nil {
		t.Fatalf("unexpected first claim: %#v %v", claimed, err)
	}
	claimedAgain, err := claimProviderQualityDeliveries(db, 10, now.Add(time.Minute))
	if err != nil || len(claimedAgain) != 0 {
		t.Fatalf("leased row must not be claimed twice: %#v %v", claimedAgain, err)
	}
	if err := markProviderQualityDeliveryFailed(db, "delivery-1", "temporary provider timeout", now.Add(time.Minute)); err != nil {
		t.Fatal(err)
	}
	var delivery models.ProviderQualityAlertDelivery
	if err := db.First(&delivery, "id = ?", "delivery-1").Error; err != nil {
		t.Fatal(err)
	}
	if delivery.Status != "retrying" || delivery.NextAttemptAt == nil || delivery.LastError != "temporary provider timeout" {
		t.Fatalf("expected retry state: %#v", delivery)
	}
	claimed, err = claimProviderQualityDeliveries(db, 10, delivery.NextAttemptAt.Add(time.Second))
	if err != nil || len(claimed) != 1 || claimed[0].Attempts != 2 {
		t.Fatalf("expected second claim: %#v %v", claimed, err)
	}
	if err := markProviderQualityDeliveryFailed(db, "delivery-1", "permanent failure", delivery.NextAttemptAt.Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	delivery = models.ProviderQualityAlertDelivery{}
	if err := db.First(&delivery, "id = ?", "delivery-1").Error; err != nil {
		t.Fatal(err)
	}
	if delivery.Status != "failed" || delivery.NextAttemptAt != nil || delivery.Attempts != 2 {
		t.Fatalf("max attempts must stop retries: %#v", delivery)
	}
}

func TestProviderQualityExpiredLeaseIsReclaimed(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.ProviderQualityAlertDelivery{}); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 9, 27, 0, 0, 0, 0, time.UTC)
	expired := now.Add(-time.Minute)
	if err := db.Create(&models.ProviderQualityAlertDelivery{ID: "delivery-expired", ScopeHash: "scope-hash", NotificationID: "notice-expired", Channel: "web_push", Status: "sending", Attempts: 1, LeaseUntil: &expired, CreatedAt: now.Add(-time.Hour), UpdatedAt: now.Add(-time.Hour)}).Error; err != nil {
		t.Fatal(err)
	}
	claimed, err := claimProviderQualityDeliveries(db, 1, now)
	if err != nil || len(claimed) != 1 || claimed[0].Status != "sending" || claimed[0].Attempts != 2 {
		t.Fatalf("expired lease should be reclaimed: %#v %v", claimed, err)
	}
}

func TestProcessProviderQualityWebPushUsesAuthorizedTokenAndUpdatesSent(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.ProviderQualityAlertState{}, &models.RiskNotification{}, &models.ProviderQualityAlertDelivery{}, &push.DeviceToken{}); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&push.DeviceToken{UserID: "user-push", Token: "token-redacted", Platform: "web"}).Error; err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	if err := materializeProviderQualityNotification(db, "user-push", "", "digest-push", "blocked", now); err != nil {
		t.Fatal(err)
	}
	sender := &fakeProviderQualityPushSender{result: &push.SendResult{Sent: 1}}
	result, err := ProcessProviderQualityWebPush(context.Background(), db, sender, 5, now)
	if err != nil || result["claimed"] != 1 || result["sent"] != 1 || sender.calls != 1 {
		t.Fatalf("unexpected push processing result: %#v err=%v calls=%d", result, err, sender.calls)
	}
	var delivery models.ProviderQualityAlertDelivery
	if err := db.Where("channel = ?", "web_push").First(&delivery).Error; err != nil {
		t.Fatal(err)
	}
	if delivery.Status != "sent" || delivery.Attempts != 1 || delivery.SentAt == nil {
		t.Fatalf("web push delivery must be sent: %#v", delivery)
	}
}

func TestProcessProviderQualityWebPushRetriesTransientFailure(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.RiskNotification{}, &models.ProviderQualityAlertDelivery{}); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PROVIDER_QUALITY_DELIVERY_MAX_ATTEMPTS", "2")
	now := time.Now().UTC()
	if err := db.Create(&models.RiskNotification{ID: "notice-fail", UserID: "user-fail", Type: "provider_quality_gate", Title: "质量提醒", Detail: "详情", Source: "omniroute-quality-gate", Status: "unread", CreatedAt: now}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.ProviderQualityAlertDelivery{ID: "pqad-fail", NotificationID: "notice-fail", ScopeHash: "scope", Channel: "web_push", Status: "pending", CreatedAt: now, UpdatedAt: now}).Error; err != nil {
		t.Fatal(err)
	}
	sender := &fakeProviderQualityPushSender{err: context.DeadlineExceeded}
	result, err := ProcessProviderQualityWebPush(context.Background(), db, sender, 1, now)
	if err != nil || result["claimed"] != 1 || result["retrying"] != 1 {
		t.Fatalf("transient failure should retry: %#v err=%v", result, err)
	}
	var delivery models.ProviderQualityAlertDelivery
	if err := db.First(&delivery, "id = ?", "pqad-fail").Error; err != nil {
		t.Fatal(err)
	}
	if delivery.Status != "retrying" || delivery.NextAttemptAt == nil || delivery.LastError == "" {
		t.Fatalf("retry state missing: %#v", delivery)
	}
}

func TestProcessProviderQualityWebPushAllInvalidTokensBecomeTerminal(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.RiskNotification{}, &models.ProviderQualityAlertDelivery{}); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	if err := db.Create(&models.RiskNotification{ID: "notice-invalid-only", UserID: "user-invalid-only", Type: "provider_quality_gate", Title: "质量提醒", Detail: "详情", Source: "omniroute-quality-gate", Status: "unread", CreatedAt: now}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.ProviderQualityAlertDelivery{ID: "delivery-invalid-only", NotificationID: "notice-invalid-only", ScopeHash: "scope", Channel: "web_push", Status: "pending", CreatedAt: now, UpdatedAt: now}).Error; err != nil {
		t.Fatal(err)
	}
	sender := &fakeProviderQualityPushSender{result: &push.SendResult{Invalid: []string{"redacted-token"}}}
	result, err := ProcessProviderQualityWebPush(context.Background(), db, sender, 1, now)
	if err != nil || result["claimed"] != 1 || result["sent"] != 1 || result["failed"] != 0 || result["retrying"] != 0 || result["invalid_token"] != 1 {
		t.Fatalf("invalid-only delivery should be terminal: %#v err=%v", result, err)
	}
	var delivery models.ProviderQualityAlertDelivery
	if err := db.First(&delivery, "id = ?", "delivery-invalid-only").Error; err != nil {
		t.Fatal(err)
	}
	if delivery.Status != "sent" || delivery.NextAttemptAt != nil || delivery.LastError != "" {
		t.Fatalf("invalid-only delivery must not retry: %#v", delivery)
	}
}

func TestRunProviderQualityWebPushOnceDryRunDoesNotClaimOrMutate(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.ProviderQualityAlertDelivery{}); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 9, 28, 0, 0, 0, 0, time.UTC)
	due := now.Add(-time.Minute)
	if err := db.Create(&models.ProviderQualityAlertDelivery{
		ID: "dry-run-delivery", NotificationID: "notice", ScopeHash: "scope", Channel: "web_push",
		Status: "pending", Attempts: 0, NextAttemptAt: &due, CreatedAt: now, UpdatedAt: now,
	}).Error; err != nil {
		t.Fatal(err)
	}
	sender := &fakeProviderQualityPushSender{result: &push.SendResult{Sent: 1}}
	result, err := RunProviderQualityWebPushOnce(context.Background(), db, sender, 10, now, true)
	if err != nil || result["dry_run"] != 1 || sender.calls != 0 {
		t.Fatalf("dry-run should only preview due work: result=%#v err=%v calls=%d", result, err, sender.calls)
	}
	var delivery models.ProviderQualityAlertDelivery
	if err := db.First(&delivery, "id = ?", "dry-run-delivery").Error; err != nil {
		t.Fatal(err)
	}
	if delivery.Status != "pending" || delivery.Attempts != 0 || delivery.LeaseUntil != nil {
		t.Fatalf("dry-run must not claim or mutate delivery state: %#v", delivery)
	}
}

func TestRunProviderQualityWebPushOnceRequiresSenderOutsideDryRun(t *testing.T) {
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&models.ProviderQualityAlertDelivery{}); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	if err := db.Create(&models.ProviderQualityAlertDelivery{
		ID: "sender-required", NotificationID: "notice", ScopeHash: "scope", Channel: "web_push",
		Status: "pending", CreatedAt: now, UpdatedAt: now,
	}).Error; err != nil {
		t.Fatal(err)
	}
	result, err := RunProviderQualityWebPushOnce(context.Background(), db, nil, 1, now, false)
	if err == nil || !strings.Contains(err.Error(), "sender is required") {
		t.Fatalf("missing sender must be an explicit error: result=%#v err=%v", result, err)
	}
	var delivery models.ProviderQualityAlertDelivery
	if err := db.First(&delivery, "id = ?", "sender-required").Error; err != nil {
		t.Fatal(err)
	}
	if delivery.Status != "pending" || delivery.Attempts != 0 {
		t.Fatalf("missing sender must not claim delivery: %#v", delivery)
	}
}

func TestProviderQualityWebPushWorkerIsOptIn(t *testing.T) {
	t.Setenv("PROVIDER_QUALITY_WEB_PUSH_WORKER_ENABLED", "false")
	if providerQualityWebPushWorkerEnabled() {
		t.Fatal("web push worker must be disabled by default")
	}
	cancel := StartProviderQualityWebPushLoop(context.Background())
	cancel()
	t.Setenv("PROVIDER_QUALITY_WEB_PUSH_WORKER_ENABLED", "true")
	if !providerQualityWebPushWorkerEnabled() {
		t.Fatal("explicit true should enable worker policy")
	}
	t.Setenv("APP_ENV", "staging")
	t.Setenv("PROVIDER_QUALITY_WEB_PUSH_DRY_RUN", "true")
	if !providerQualityWebPushDryRunEnabled() {
		t.Fatal("staging dry-run should be enabled only with explicit opt-in")
	}
	t.Setenv("APP_ENV", "production")
	if providerQualityWebPushDryRunEnabled() {
		t.Fatal("production must never use the staging dry-run mode")
	}
}

func TestCleanupProviderQualityDeliveriesKeepsRecentAndNonTerminalRows(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.ProviderQualityAlertDelivery{}); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 9, 27, 0, 0, 0, 0, time.UTC)
	oldSent := now.Add(-40 * 24 * time.Hour)
	recentSent := now.Add(-2 * 24 * time.Hour)
	oldPending := now.Add(-40 * 24 * time.Hour)
	rows := []models.ProviderQualityAlertDelivery{
		{ID: "old-sent", Channel: "web_push", Status: "sent", UpdatedAt: oldSent, CreatedAt: oldSent},
		{ID: "recent-sent", Channel: "web_push", Status: "sent", UpdatedAt: recentSent, CreatedAt: recentSent},
		{ID: "old-pending", Channel: "web_push", Status: "pending", UpdatedAt: oldPending, CreatedAt: oldPending},
		{ID: "old-failed", Channel: "web_push", Status: "failed", UpdatedAt: oldSent, CreatedAt: oldSent},
	}
	if err := db.Create(&rows).Error; err != nil {
		t.Fatal(err)
	}
	deleted, err := CleanupProviderQualityDeliveries(db, now)
	if err != nil || deleted != 2 {
		t.Fatalf("expected only old terminal rows to be deleted, deleted=%d err=%v", deleted, err)
	}
	var remaining []models.ProviderQualityAlertDelivery
	if err := db.Find(&remaining).Error; err != nil {
		t.Fatal(err)
	}
	if len(remaining) != 2 {
		t.Fatalf("recent sent and pending rows must remain: %#v", remaining)
	}
}

func TestProviderQualityAlertStateHandlerDoesNotExposeScopeIdentity(t *testing.T) {
	t.Setenv("PROVIDER_QUALITY_ALERT_COOLDOWN_SECONDS", "")
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.ProviderQualityAlertState{}); err != nil {
		t.Fatal(err)
	}
	if _, err := recordProviderQualityAlertState(db, "user-private", "", "digest-a", "blocked", time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
	r := gin.New()
	r.GET("/planning/providers/quality/alerts", func(c *gin.Context) {
		c.Set("user_id", "user-private")
		ProviderQualityAlertStateHandler(c)
	})
	response := httptest.NewRecorder()
	r.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/planning/providers/quality/alerts", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
	if body := response.Body.String(); body == "" || strings.Contains(body, "user-private") || strings.Contains(body, "trip-private") {
		t.Fatalf("alert state must not expose scope identity: %s", body)
	}
	var envelope map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	data := envelope["data"].(map[string]any)
	if data["cooldown_seconds"] != float64(1800) {
		t.Fatalf("unexpected default cooldown: %#v", data["cooldown_seconds"])
	}
}
