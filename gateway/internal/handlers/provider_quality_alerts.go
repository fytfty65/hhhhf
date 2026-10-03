package handlers

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"os"
	"strconv"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/metrics"
	"gateway/internal/models"
	"gateway/internal/push"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const (
	defaultProviderQualityAlertCooldown = 30 * time.Minute
	maxProviderQualityAlertCooldown     = 7 * 24 * time.Hour
	defaultProviderQualityDeliveryLease = 5 * time.Minute
	defaultProviderQualityMaxAttempts   = 5
)

func providerQualityAlertCooldown() time.Duration {
	seconds := int(defaultProviderQualityAlertCooldown / time.Second)
	if raw := strings.TrimSpace(os.Getenv("PROVIDER_QUALITY_ALERT_COOLDOWN_SECONDS")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil {
			seconds = parsed
		}
	}
	if seconds < 60 {
		seconds = 60
	}
	if time.Duration(seconds)*time.Second > maxProviderQualityAlertCooldown {
		return maxProviderQualityAlertCooldown
	}
	return time.Duration(seconds) * time.Second
}

func providerQualityDeliveryMaxAttempts() int {
	value := defaultProviderQualityMaxAttempts
	if raw := strings.TrimSpace(os.Getenv("PROVIDER_QUALITY_DELIVERY_MAX_ATTEMPTS")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil {
			value = parsed
		}
	}
	if value < 1 {
		return 1
	}
	if value > 20 {
		return 20
	}
	return value
}

func providerQualityDeliveryBackoff(attempts int) time.Duration {
	if attempts < 1 {
		attempts = 1
	}
	seconds := 30 * (1 << minInt(attempts-1, 6))
	if seconds > 3600 {
		seconds = 3600
	}
	return time.Duration(seconds) * time.Second
}

func minInt(left, right int) int {
	if left < right {
		return left
	}
	return right
}

func providerQualityScopeHash(userID, tripID string) string {
	digest := sha256.Sum256([]byte(strings.TrimSpace(userID) + "\x00" + strings.TrimSpace(tripID)))
	return hex.EncodeToString(digest[:])[:24]
}

func providerQualityAlertStateID(scopeHash, digest string) string {
	digestValue := sha256.Sum256([]byte(scopeHash + "\x00" + strings.TrimSpace(digest)))
	return "pqa-" + hex.EncodeToString(digestValue[:])[:32]
}

func providerQualityNotificationID(scopeHash, digest string, now time.Time) string {
	window := int64(providerQualityAlertCooldown() / time.Second)
	if window < 1 {
		window = 1
	}
	value := sha256.Sum256([]byte(fmt.Sprintf("%s\x00%s\x00%d", scopeHash, strings.TrimSpace(digest), now.Unix()/window)))
	return "pqn-" + hex.EncodeToString(value[:])[:32]
}

// materializeProviderQualityNotification creates an in-app, aggregate-only
// notification. It does not send Web Push or email and is idempotent within
// the cooldown bucket.
func materializeProviderQualityNotification(tx *gorm.DB, userID, tripID, digest, status string, now time.Time) error {
	if tx == nil || strings.TrimSpace(userID) == "" || strings.TrimSpace(digest) == "" {
		return nil
	}
	scopeHash := providerQualityScopeHash(userID, tripID)
	severity, detail := "WARNING", "供应商质量样本不足，系统将保守处理实时字段，并等待更多可验证数据。"
	if strings.TrimSpace(status) == "blocked" {
		severity = "HIGH"
		detail = "部分供应商数据质量未达发布门槛，系统已暂停将其作为实时事实使用。"
	}
	notificationID := providerQualityNotificationID(scopeHash, digest, now)
	notification := models.RiskNotification{
		ID: notificationID, UserID: strings.TrimSpace(userID), TripID: strings.TrimSpace(tripID),
		Type: "provider_quality_gate", Severity: severity, Title: "供应商数据质量提醒", Detail: detail,
		Source: "omniroute-quality-gate", Estimated: false, Status: "unread", CreatedAt: now.UTC(),
	}
	if err := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&notification).Error; err != nil {
		return err
	}
	next := now.UTC()
	delivery := models.ProviderQualityAlertDelivery{
		ID: "pqad-" + notificationID, NotificationID: notificationID, ScopeHash: scopeHash,
		Channel: "in_app", Status: "pending", Attempts: 0, NextAttemptAt: &next, CreatedAt: now.UTC(), UpdatedAt: now.UTC(),
	}
	if err := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&delivery).Error; err != nil {
		return err
	}
	// Only schedule external push when the user has already registered an FCM
	// token. With no explicit consent, the in-app notification remains the sole
	// delivery channel and no external work is created.
	if tokens, err := push.TokensForUser(strings.TrimSpace(userID)); err == nil && len(tokens) > 0 {
		webPush := models.ProviderQualityAlertDelivery{
			ID: "pqad-web-" + notificationID, NotificationID: notificationID, ScopeHash: scopeHash,
			Channel: "web_push", Status: "pending", Attempts: 0, NextAttemptAt: &next, CreatedAt: now.UTC(), UpdatedAt: now.UTC(),
		}
		return tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&webPush).Error
	}
	return nil
}

// ProviderQualityPushSender is the narrow adapter required by the delivery
// worker. The concrete FCM sender is injected by the caller so tests and
// staging can remain credential-free.
type ProviderQualityPushSender interface {
	SendToUser(context.Context, string, push.Message) (*push.SendResult, error)
}

// ProcessProviderQualityWebPush claims due authorized FCM deliveries and
// updates their lifecycle. It never creates a subscription and never logs
// tokens; callers decide when to enable this worker in production.
func ProcessProviderQualityWebPush(ctx context.Context, tx *gorm.DB, sender ProviderQualityPushSender, limit int, now time.Time) (map[string]int, error) {
	result := map[string]int{"claimed": 0, "sent": 0, "retrying": 0, "failed": 0, "invalid_token": 0, "no_token": 0}
	if tx == nil {
		return result, nil
	}
	if sender == nil {
		return result, errors.New("provider quality push sender is required")
	}
	claimed, err := claimProviderQualityDeliveriesForChannel(tx, "web_push", limit, now)
	if err != nil {
		return result, err
	}
	result["claimed"] = len(claimed)
	for _, delivery := range claimed {
		var notification models.RiskNotification
		if err := tx.Where("id = ?", delivery.NotificationID).First(&notification).Error; err != nil {
			_ = markProviderQualityDeliveryFailed(tx, delivery.ID, "notification_not_found", now)
			result["failed"]++
			continue
		}
		sendResult, sendErr := sender.SendToUser(ctx, notification.UserID, push.Message{
			Title: notification.Title,
			Body:  notification.Detail,
			Data:  map[string]string{"type": notification.Type, "notification_id": notification.ID, "link": "/"},
		})
		// A user may have only stale devices. FCM then reports zero successful
		// sends but one or more explicitly invalid tokens; those tokens have
		// already been removed by the sender, so retrying this notification would
		// never make progress. Treat the delivery as terminal when every result
		// was either sent or classified invalid.
		if sendErr == nil && sendResult != nil && sendResult.Failed == 0 && (sendResult.Sent > 0 || len(sendResult.Invalid) > 0) {
			result["invalid_token"] += len(sendResult.Invalid)
			if err := markProviderQualityDeliverySent(tx, delivery.ID, now); err != nil {
				return result, err
			}
			result["sent"]++
			continue
		}
		if sendErr != nil && errors.Is(sendErr, push.ErrNoTokens) {
			result["no_token"]++
		}
		if sendResult != nil {
			result["invalid_token"] += len(sendResult.Invalid)
		}
		reason := "push_delivery_failed"
		if sendErr != nil {
			reason = sendErr.Error()
		} else if sendResult != nil && sendResult.LastError != "" {
			reason = sendResult.LastError
		}
		if err := markProviderQualityDeliveryFailed(tx, delivery.ID, reason, now); err != nil {
			return result, err
		}
		var current models.ProviderQualityAlertDelivery
		if err := tx.First(&current, "id = ?", delivery.ID).Error; err == nil && current.Status == "retrying" {
			result["retrying"]++
		} else {
			result["failed"]++
		}
	}
	return result, nil
}

func providerQualityWebPushWorkerEnabled() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv("PROVIDER_QUALITY_WEB_PUSH_WORKER_ENABLED")), "true")
}

func providerQualityWebPushDryRunEnabled() bool {
	if !strings.EqualFold(strings.TrimSpace(os.Getenv("PROVIDER_QUALITY_WEB_PUSH_DRY_RUN")), "true") {
		return false
	}
	env := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
	return env == "staging" || env == "test"
}

func providerQualityDeliveryRetentionDays() int {
	days := 30
	if raw := strings.TrimSpace(os.Getenv("PROVIDER_QUALITY_DELIVERY_RETENTION_DAYS")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil {
			days = parsed
		}
	}
	if days < 1 {
		return 1
	}
	if days > 3650 {
		return 3650
	}
	return days
}

func providerQualityDeliveryCleanupInterval() time.Duration {
	seconds := 3600
	if raw := strings.TrimSpace(os.Getenv("PROVIDER_QUALITY_DELIVERY_CLEANUP_INTERVAL_SECONDS")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil {
			seconds = parsed
		}
	}
	if seconds < 60 {
		seconds = 60
	}
	if seconds > 7*24*60*60 {
		seconds = 7 * 24 * 60 * 60
	}
	return time.Duration(seconds) * time.Second
}

// CleanupProviderQualityDeliveries removes only terminal delivery rows older
// than the bounded retention window. Notification and alert state rows are
// deliberately retained for auditability.
func CleanupProviderQualityDeliveries(tx *gorm.DB, now time.Time) (int64, error) {
	if tx == nil {
		return 0, nil
	}
	cutoff := now.UTC().Add(-time.Duration(providerQualityDeliveryRetentionDays()) * 24 * time.Hour)
	result := tx.Where("status IN ? AND updated_at < ?", []string{"sent", "failed"}, cutoff).Delete(&models.ProviderQualityAlertDelivery{})
	return result.RowsAffected, result.Error
}

func previewProviderQualityWebPush(tx *gorm.DB, limit int, now time.Time) (int, error) {
	if tx == nil {
		return 0, nil
	}
	if limit < 1 {
		limit = 1
	}
	if limit > 100 {
		limit = 100
	}
	var count int64
	query := tx.Model(&models.ProviderQualityAlertDelivery{}).
		Where("channel = ? AND status IN ? AND (next_attempt_at IS NULL OR next_attempt_at <= ?) AND attempts < ?", "web_push", []string{"pending", "retrying"}, now.UTC(), providerQualityDeliveryMaxAttempts())
	if err := query.Limit(limit).Count(&count).Error; err != nil {
		return 0, err
	}
	return int(count), nil
}

// RunProviderQualityWebPushOnce executes exactly one worker tick. It is
// intentionally separate from the timer loop so staging smoke checks and
// controlled operators can validate scheduling without waiting for the
// configured interval. Dry-run never claims rows, sends network requests, or
// mutates delivery state.
func RunProviderQualityWebPushOnce(ctx context.Context, tx *gorm.DB, sender ProviderQualityPushSender, limit int, now time.Time, dryRun bool) (map[string]int, error) {
	var result map[string]int
	var processErr error
	if dryRun {
		count, err := previewProviderQualityWebPush(tx, limit, now.UTC())
		result = map[string]int{"dry_run": count}
		processErr = err
	} else {
		result, processErr = ProcessProviderQualityWebPush(ctx, tx, sender, limit, now.UTC())
	}
	metrics.RecordProviderQualityPush(result, processErr)
	return result, processErr
}

// StartProviderQualityWebPushLoop starts the external delivery worker only
// when explicitly enabled. The default is disabled, so deployments cannot
// accidentally send notifications merely by upgrading the gateway binary.
func StartProviderQualityWebPushLoop(parent context.Context) context.CancelFunc {
	ctx, cancel := context.WithCancel(parent)
	if !providerQualityWebPushWorkerEnabled() {
		return cancel
	}
	dryRun := providerQualityWebPushDryRunEnabled()
	var sender ProviderQualityPushSender
	if !dryRun {
		configured, err := push.NewSenderFromEnv()
		if err != nil {
			log.Printf("供应商质量 Web Push worker 未启动：推送配置不可用")
			return cancel
		}
		sender = configured
	}
	interval := 5 * time.Minute
	if raw := strings.TrimSpace(os.Getenv("PROVIDER_QUALITY_WEB_PUSH_INTERVAL_SECONDS")); raw != "" {
		if parsed, parseErr := strconv.Atoi(raw); parseErr == nil && parsed >= 30 && parsed <= 24*60*60 {
			interval = time.Duration(parsed) * time.Second
		}
	}
	limit := 20
	if raw := strings.TrimSpace(os.Getenv("PROVIDER_QUALITY_WEB_PUSH_BATCH_SIZE")); raw != "" {
		if parsed, parseErr := strconv.Atoi(raw); parseErr == nil && parsed >= 1 && parsed <= 100 {
			limit = parsed
		}
	}
	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		lastCleanup := time.Time{}
		for {
			select {
			case <-ctx.Done():
				return
			case now := <-ticker.C:
				result, processErr := RunProviderQualityWebPushOnce(ctx, database.DB, sender, limit, now.UTC(), dryRun)
				if lastCleanup.IsZero() || now.Sub(lastCleanup) >= providerQualityDeliveryCleanupInterval() {
					deleted, cleanupErr := CleanupProviderQualityDeliveries(database.DB, now.UTC())
					metrics.RecordProviderQualityDeliveryCleanup(deleted, cleanupErr)
					if cleanupErr != nil {
						log.Printf("供应商质量投递清理失败：%v", cleanupErr)
					}
					lastCleanup = now
				}
				if processErr != nil {
					log.Printf("供应商质量 Web Push worker 执行失败：%v", processErr)
					continue
				}
				if !dryRun && result["claimed"] > 0 {
					log.Printf("供应商质量 Web Push worker 完成：领取 %d，发送 %d，重试 %d，失败 %d", result["claimed"], result["sent"], result["retrying"], result["failed"])
				} else if dryRun && result["dry_run"] > 0 {
					log.Printf("供应商质量 Web Push worker dry-run：发现 %d 条待投递记录（未发送、未修改状态）", result["dry_run"])
				}
			}
		}
	}()
	return cancel
}

func claimProviderQualityDeliveriesForChannel(tx *gorm.DB, channel string, limit int, now time.Time) ([]models.ProviderQualityAlertDelivery, error) {
	if tx == nil {
		return nil, nil
	}
	if limit < 1 {
		limit = 1
	}
	if limit > 100 {
		limit = 100
	}
	leaseUntil := now.UTC().Add(defaultProviderQualityDeliveryLease)
	if err := tx.Model(&models.ProviderQualityAlertDelivery{}).Where("channel = ? AND status = ? AND lease_until IS NOT NULL AND lease_until < ?", channel, "sending", now.UTC()).Updates(map[string]any{"status": "retrying", "lease_until": nil, "next_attempt_at": now.UTC()}).Error; err != nil {
		return nil, err
	}
	var candidates []models.ProviderQualityAlertDelivery
	if err := tx.Where("channel = ? AND status IN ? AND (next_attempt_at IS NULL OR next_attempt_at <= ?) AND attempts < ?", channel, []string{"pending", "retrying"}, now.UTC(), providerQualityDeliveryMaxAttempts()).Order("created_at asc").Limit(limit).Find(&candidates).Error; err != nil {
		return nil, err
	}
	claimed := make([]models.ProviderQualityAlertDelivery, 0, len(candidates))
	for _, candidate := range candidates {
		updated := tx.Model(&models.ProviderQualityAlertDelivery{}).Where("id = ? AND channel = ? AND status IN ? AND attempts < ?", candidate.ID, channel, []string{"pending", "retrying"}, providerQualityDeliveryMaxAttempts()).Updates(map[string]any{"status": "sending", "attempts": candidate.Attempts + 1, "lease_until": leaseUntil, "updated_at": now.UTC()})
		if updated.Error != nil {
			return nil, updated.Error
		}
		if updated.RowsAffected == 1 {
			candidate.Status = "sending"
			candidate.Attempts++
			candidate.LeaseUntil = &leaseUntil
			claimed = append(claimed, candidate)
		}
	}
	return claimed, nil
}

// claimProviderQualityDeliveries atomically transitions due work into
// sending. A short lease makes crashed workers reclaimable without allowing
// two workers to process the same row concurrently.
func claimProviderQualityDeliveries(tx *gorm.DB, limit int, now time.Time) ([]models.ProviderQualityAlertDelivery, error) {
	if tx == nil {
		return nil, nil
	}
	if limit < 1 {
		limit = 1
	}
	if limit > 100 {
		limit = 100
	}
	leaseUntil := now.UTC().Add(defaultProviderQualityDeliveryLease)
	if err := tx.Model(&models.ProviderQualityAlertDelivery{}).
		Where("status = ? AND lease_until IS NOT NULL AND lease_until < ?", "sending", now.UTC()).
		Updates(map[string]any{"status": "retrying", "lease_until": nil, "next_attempt_at": now.UTC()}).Error; err != nil {
		return nil, err
	}
	var candidates []models.ProviderQualityAlertDelivery
	if err := tx.Where("status IN ? AND (next_attempt_at IS NULL OR next_attempt_at <= ?) AND attempts < ?", []string{"pending", "retrying"}, now.UTC(), providerQualityDeliveryMaxAttempts()).Order("created_at asc").Limit(limit).Find(&candidates).Error; err != nil {
		return nil, err
	}
	claimed := make([]models.ProviderQualityAlertDelivery, 0, len(candidates))
	for _, candidate := range candidates {
		result := tx.Model(&models.ProviderQualityAlertDelivery{}).Where("id = ? AND status IN ? AND attempts < ?", candidate.ID, []string{"pending", "retrying"}, providerQualityDeliveryMaxAttempts()).Updates(map[string]any{
			"status": "sending", "attempts": candidate.Attempts + 1, "lease_until": leaseUntil, "updated_at": now.UTC(),
		})
		if result.Error != nil {
			return nil, result.Error
		}
		if result.RowsAffected == 1 {
			candidate.Status = "sending"
			candidate.Attempts++
			candidate.LeaseUntil = &leaseUntil
			candidate.UpdatedAt = now.UTC()
			claimed = append(claimed, candidate)
		}
	}
	return claimed, nil
}

func markProviderQualityDeliverySent(tx *gorm.DB, id string, now time.Time) error {
	if tx == nil || strings.TrimSpace(id) == "" {
		return nil
	}
	sentAt := now.UTC()
	return tx.Model(&models.ProviderQualityAlertDelivery{}).Where("id = ? AND status = ?", id, "sending").Updates(map[string]any{"status": "sent", "sent_at": sentAt, "lease_until": nil, "last_error": "", "updated_at": sentAt}).Error
}

func markProviderQualityDeliveryFailed(tx *gorm.DB, id, reason string, now time.Time) error {
	if tx == nil || strings.TrimSpace(id) == "" {
		return nil
	}
	var delivery models.ProviderQualityAlertDelivery
	if err := tx.Where("id = ?", id).First(&delivery).Error; err != nil {
		return err
	}
	status := "retrying"
	var next *time.Time
	if delivery.Attempts >= providerQualityDeliveryMaxAttempts() {
		status = "failed"
	} else {
		value := now.UTC().Add(providerQualityDeliveryBackoff(delivery.Attempts))
		next = &value
	}
	if len([]rune(reason)) > 240 {
		reason = string([]rune(reason)[:240])
	}
	if err := tx.Model(&delivery).Updates(map[string]any{"status": status, "last_error": strings.TrimSpace(reason), "updated_at": now.UTC()}).Error; err != nil {
		return err
	}
	if status == "failed" {
		if err := tx.Model(&models.ProviderQualityAlertDelivery{}).Where("id = ?", delivery.ID).UpdateColumn("next_attempt_at", gorm.Expr("NULL")).Error; err != nil {
			return err
		}
		return tx.Model(&models.ProviderQualityAlertDelivery{}).Where("id = ?", delivery.ID).UpdateColumn("lease_until", gorm.Expr("NULL")).Error
	}
	if err := tx.Model(&delivery).Updates(map[string]any{"next_attempt_at": next}).Error; err != nil {
		return err
	}
	return tx.Model(&models.ProviderQualityAlertDelivery{}).Where("id = ?", delivery.ID).UpdateColumn("lease_until", gorm.Expr("NULL")).Error
}

// recordProviderQualityAlertState persists only an aggregate digest and the
// delivery timestamps. It returns whether a notification may be emitted now;
// the caller can later hand that decision to an explicitly consented channel.
func recordProviderQualityAlertState(tx *gorm.DB, userID, tripID, digest, status string, now time.Time) (bool, error) {
	if tx == nil || strings.TrimSpace(userID) == "" || strings.TrimSpace(digest) == "" {
		return false, nil
	}
	scopeHash := providerQualityScopeHash(userID, tripID)
	id := providerQualityAlertStateID(scopeHash, digest)
	var state models.ProviderQualityAlertState
	err := tx.Where("id = ?", id).First(&state).Error
	if err == gorm.ErrRecordNotFound {
		state = models.ProviderQualityAlertState{
			ID: id, ScopeHash: scopeHash, Digest: strings.TrimSpace(digest), Status: strings.TrimSpace(status),
			LastSeenAt: now.UTC(), LastNotifiedAt: now.UTC(), NotificationCount: 1,
		}
		return true, tx.Create(&state).Error
	}
	if err != nil {
		return false, err
	}
	shouldNotify := state.LastNotifiedAt.IsZero() || now.Sub(state.LastNotifiedAt) >= providerQualityAlertCooldown()
	updates := map[string]any{"status": strings.TrimSpace(status), "last_seen_at": now.UTC()}
	if shouldNotify {
		updates["last_notified_at"] = now.UTC()
		updates["notification_count"] = state.NotificationCount + 1
	}
	return shouldNotify, tx.Model(&state).Updates(updates).Error
}

// ProviderQualityAlertStateHandler exposes aggregate delivery state to the
// authenticated diagnostics surface. It never returns user/trip identifiers.
func ProviderQualityAlertStateHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	tripID := strings.TrimSpace(c.Query("trip_id"))
	if tripID != "" {
		if _, ok := requireTripOrRoomAccess(c, tripID); !ok {
			return
		}
	}
	scopeHash := providerQualityScopeHash(userID, tripID)
	var states []models.ProviderQualityAlertState
	query := database.DB.Where("scope_hash = ?", scopeHash).Order("last_seen_at desc").Limit(50)
	if err := query.Find(&states).Error; err != nil {
		planningError(c, 500, "PROVIDER_ALERT_STATE_FAILED", "供应商告警状态读取失败", nil)
		return
	}
	items := make([]gin.H, 0, len(states))
	for _, state := range states {
		items = append(items, gin.H{
			"digest": state.Digest, "status": state.Status,
			"last_seen_at": state.LastSeenAt.UTC(), "last_notified_at": state.LastNotifiedAt.UTC(),
			"notification_count": state.NotificationCount,
		})
	}
	planningData(c, gin.H{"states": items, "cooldown_seconds": int(providerQualityAlertCooldown() / time.Second), "policy": "aggregate_only_explicit_delivery_required"})
}

// ProviderQualityAlertDeliveryHandler exposes only the delivery lifecycle for
// the authenticated scope. It is diagnostic state, not proof that an external
// push/email was sent.
func ProviderQualityAlertDeliveryHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	tripID := strings.TrimSpace(c.Query("trip_id"))
	if tripID != "" {
		if _, ok := requireTripOrRoomAccess(c, tripID); !ok {
			return
		}
	}
	scopeHash := providerQualityScopeHash(userID, tripID)
	var deliveries []models.ProviderQualityAlertDelivery
	if err := database.DB.Where("scope_hash = ?", scopeHash).Order("updated_at desc").Limit(50).Find(&deliveries).Error; err != nil {
		planningError(c, 500, "PROVIDER_ALERT_DELIVERY_FAILED", "供应商告警投递状态读取失败", nil)
		return
	}
	items := make([]gin.H, 0, len(deliveries))
	for _, delivery := range deliveries {
		items = append(items, gin.H{
			"notification_id": delivery.NotificationID, "channel": delivery.Channel, "status": delivery.Status,
			"attempts": delivery.Attempts, "last_error": delivery.LastError,
			"next_attempt_at": delivery.NextAttemptAt, "sent_at": delivery.SentAt,
			"lease_until": delivery.LeaseUntil,
			"updated_at":  delivery.UpdatedAt.UTC(),
		})
	}
	planningData(c, gin.H{"deliveries": items, "policy": "pending_until_explicit_channel_worker"})
}
