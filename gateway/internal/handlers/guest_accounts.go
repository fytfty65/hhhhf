package handlers

import (
	"context"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"golang.org/x/crypto/bcrypt"
	"gorm.io/gorm"
)

// 临时账号（免注册试用）的转正与过期清理。
//
// 背景：前端「先随便逛逛 · 免注册试用」会用一个随机用户名 + 随机密码注册真实账号，
// 密码从不展示给用户。于是账号在服务器上真实存在（能建房、协作、保存行程），
// 但对用户来说是不可恢复的：清掉浏览器数据或换设备就再也登不进去。
// 之前没有任何机制处理这件事 —— 既不能转正，也不会过期，试用账号会永久堆积。
//
// 这里补三件事：
//  1. 在账号标记上区分临时/正式（IsGuest），并兼容早期只用 guest_ 前缀的历史数据；
//  2. 支持原地转正：账号 ID 不变，所以行程、记账、事件、偏好全部保留；
//  3. 长期未使用的临时账号会被定时清理，窗口可配置，默认 30 天。

const guestUsernamePrefix = "guest_"

const (
	defaultGuestRetentionDays            = 30
	minGuestRetentionDays                = 7
	maxGuestRetentionDays                = 365
	defaultGuestCleanupIntervalMinutes   = 1440
	guestCleanupBatchSize                = 200
)

// userIsGuest：新数据看 IsGuest，历史数据靠 guest_ 前缀兜底。
// 早期试用账号没有标记字段，只有随机用户名，不能因为升级前创建就漏掉。
func userIsGuest(user models.User) bool {
	if user.IsGuest {
		return true
	}
	return strings.HasPrefix(strings.ToLower(strings.TrimSpace(user.Username)), guestUsernamePrefix)
}

// guestRetentionDays 返回清理窗口，越界或非法一律回落到默认值。
func guestRetentionDays() int {
	raw := strings.TrimSpace(os.Getenv("GUEST_ACCOUNT_RETENTION_DAYS"))
	if raw == "" {
		return defaultGuestRetentionDays
	}
	parsed, err := strconv.Atoi(raw)
	if err != nil || parsed < minGuestRetentionDays || parsed > maxGuestRetentionDays {
		return defaultGuestRetentionDays
	}
	return parsed
}

// guestCleanupEnabled：默认开启。临时账号的过期清理是产品承诺的一部分
// （界面会明说“长期不用会被清理”），因此用显式关闭而不是显式开启。
func guestCleanupEnabled() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("GUEST_ACCOUNT_CLEANUP_ENABLED"))) {
	case "0", "false", "off", "no":
		return false
	}
	return true
}

// touchUserActivity 记录最近活跃时间，供临时账号过期判断。
// 只在登录/续期时写，不在每个请求上写，避免给单写库加无谓的写放大。
func touchUserActivity(userID string) {
	if userID == "" || database.DB == nil {
		return
	}
	now := time.Now().UTC()
	database.DB.Model(&models.User{}).Where("id = ?", userID).UpdateColumn("last_active_at", now)
}

// UpgradeGuestAccountHandler 把当前临时账号原地转成正式账号。
//
// 不改账号 ID：行程、记账、计划事件、偏好都挂在同一个 user_id 上，
// 因此“转正后数据还在”不是靠迁移数据，而是根本不需要迁移。
func UpgradeGuestAccountHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	var req struct {
		Username string `json:"username" binding:"required"`
		Password string `json:"password" binding:"required,min=6"`
		Nickname string `json:"nickname"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{
			"error": "用户名或密码格式不正确（密码至少 6 位）",
			"code":  "INVALID_UPGRADE_REQUEST",
		})
		return
	}

	username := strings.TrimSpace(req.Username)
	if username == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "用户名不能为空", "code": "USERNAME_REQUIRED"})
		return
	}
	if len([]rune(username)) > 64 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "用户名过长", "code": "USERNAME_TOO_LONG"})
		return
	}
	// 保留前缀给试用账号，否则“转正后的账号”下次会被当成临时账号清理。
	if strings.HasPrefix(strings.ToLower(username), guestUsernamePrefix) {
		c.JSON(http.StatusBadRequest, gin.H{
			"error": "正式账号用户名不能使用 guest_ 前缀",
			"code":  "USERNAME_RESERVED",
		})
		return
	}

	var user models.User
	if err := database.DB.Where("id = ?", userID).First(&user).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "账号不存在", "code": "USER_NOT_FOUND"})
		return
	}
	if !userIsGuest(user) {
		c.JSON(http.StatusConflict, gin.H{"error": "当前已是正式账号", "code": "NOT_GUEST_ACCOUNT"})
		return
	}

	var existing models.User
	if err := database.DB.Where("username = ?", username).First(&existing).Error; err == nil && existing.ID != userID {
		c.JSON(http.StatusConflict, gin.H{"error": "用户名已被注册", "code": "USERNAME_TAKEN"})
		return
	}

	hash, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcrypt.DefaultCost)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "密码加密失败", "code": "PASSWORD_HASH_FAILED"})
		return
	}

	now := time.Now().UTC()
	updates := map[string]any{
		"username":       username,
		"password_hash":  string(hash),
		"is_guest":       false,
		"last_active_at": now,
	}
	if nickname := strings.TrimSpace(req.Nickname); nickname != "" {
		updates["nickname"] = nickname
	}
	if err := database.DB.Model(&models.User{}).Where("id = ?", userID).Updates(updates).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "转正失败，请稍后再试", "code": "UPGRADE_FAILED"})
		return
	}

	user.Username = username
	user.PasswordHash = string(hash)
	user.IsGuest = false
	user.LastActiveAt = &now
	if nickname, ok := updates["nickname"].(string); ok {
		user.Nickname = nickname
	}
	c.JSON(http.StatusOK, gin.H{
		"message": "已转为正式账号，行程与偏好都保留在原账号上",
		"user":    buildAuthUser(user),
	})
}

// guestOwnedTables 列出按 user_id 归属的表，清理临时账号时逐张删除。
// 用数据而不是循环里写死，新增用户级表时只需加一行。
func guestOwnedTables() []struct {
	Name  string
	Model any
} {
	return []struct {
		Name  string
		Model any
	}{
		{"trip_plans", &models.TripPlan{}},
		{"community_posts", &models.CommunityPost{}},
		{"post_favorites", &models.PostFavorite{}},
		{"comments", &models.Comment{}},
		{"room_members", &models.RoomMember{}},
		{"messages", &models.Message{}},
		{"feedback_logs", &models.FeedbackLog{}},
		{"user_preferences", &models.UserPreference{}},
		{"node_annotations", &models.NodeAnnotation{}},
		{"node_annotation_votes", &models.NodeAnnotationVote{}},
		{"recommendation_events", &models.RecommendationEvent{}},
		{"planning_events", &models.PlanningEvent{}},
		{"trip_execution_states", &models.TripExecutionState{}},
		{"planning_runs", &models.PlanningRun{}},
		{"budget_plans", &models.BudgetPlan{}},
		{"expense_records", &models.ExpenseRecord{}},
		{"plan_variant_votes", &models.PlanVariantVote{}},
		{"trip_satisfactions", &models.TripSatisfaction{}},
		{"risk_subscriptions", &models.RiskSubscription{}},
		{"risk_notifications", &models.RiskNotification{}},
		{"web_push_subscriptions", &models.WebPushSubscription{}},
		{"travel_orders", &models.TravelOrder{}},
	}
}

// deleteGuestAccount 在一个事务里删掉临时账号及其全部归属数据。
func deleteGuestAccount(userID string) error {
	return database.DB.Transaction(func(tx *gorm.DB) error {
		for _, table := range guestOwnedTables() {
			if err := tx.Where("user_id = ?", userID).Delete(table.Model).Error; err != nil {
				return err
			}
		}
		if err := tx.Where("user_id = ?", userID).Delete(&models.DeviceSession{}).Error; err != nil {
			return err
		}
		return tx.Where("id = ?", userID).Delete(&models.User{}).Error
	})
}

var (
	guestCleanupMu         sync.Mutex
	guestCleanupLastRun    time.Time
	guestCleanupLastUsers  int64
	guestCleanupLastErr    string
)

// cleanupExpiredGuestAccounts 清理超过窗口未活跃的临时账号。
// 只碰临时账号：正式账号无论多久不用都不会被删。
func cleanupExpiredGuestAccounts() {
	guestCleanupMu.Lock()
	defer func() {
		guestCleanupLastRun = time.Now().UTC()
		guestCleanupMu.Unlock()
	}()

	if !guestCleanupEnabled() || database.DB == nil {
		guestCleanupLastUsers = 0
		guestCleanupLastErr = ""
		return
	}

	days := guestRetentionDays()
	cutoff := time.Now().UTC().Add(-time.Duration(days) * 24 * time.Hour)

	var expired []models.User
	query := database.DB.
		Where("(is_guest = ? OR username LIKE ?)", true, guestUsernamePrefix+"%").
		Where("(last_active_at IS NULL AND created_at < ?) OR last_active_at < ?", cutoff, cutoff).
		Order("created_at asc").
		Limit(guestCleanupBatchSize)
	if err := query.Find(&expired).Error; err != nil {
		guestCleanupLastUsers = 0
		guestCleanupLastErr = err.Error()
		return
	}

	var deleted int64
	var lastErr string
	for _, user := range expired {
		if err := deleteGuestAccount(user.ID); err != nil {
			if lastErr == "" {
				lastErr = user.ID + ": " + err.Error()
			}
			continue
		}
		deleted++
	}
	guestCleanupLastUsers = deleted
	guestCleanupLastErr = lastErr
	if deleted > 0 {
		log.Printf("[guest-cleanup] 清理临时账号 %d 个（窗口 %d 天）", deleted, days)
	}
}

// guestCleanupSnapshot 报告清理配置与上次执行结果，供运维查看。
func guestCleanupSnapshot() map[string]any {
	guestCleanupMu.Lock()
	defer guestCleanupMu.Unlock()

	snapshot := map[string]any{
		"enabled":        guestCleanupEnabled(),
		"retention_days": guestRetentionDays(),
		"min_days":       minGuestRetentionDays,
		"max_days":       maxGuestRetentionDays,
		"last_run_unix":  int64(0),
		"last_deleted":   guestCleanupLastUsers,
		"last_error":     guestCleanupLastErr,
	}
	if !guestCleanupLastRun.IsZero() {
		snapshot["last_run_unix"] = guestCleanupLastRun.Unix()
	}
	return snapshot
}

// GuestCleanupHealthHandler 暴露临时账号清理状态（只报配置与计数，不含账号内容）。
func GuestCleanupHealthHandler(c *gin.Context) {
	if _, ok := currentUserIDOrReject(c); !ok {
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "available", "guest_cleanup": guestCleanupSnapshot()})
}

// StartGuestAccountCleanupLoop 按天清理过期临时账号。
// 首次启动立即执行一次，让开启后的效果无需等待。
func StartGuestAccountCleanupLoop(parent context.Context) context.CancelFunc {
	if parent == nil {
		parent = context.Background()
	}
	minutes := defaultGuestCleanupIntervalMinutes
	if raw := strings.TrimSpace(os.Getenv("GUEST_ACCOUNT_CLEANUP_INTERVAL_MINUTES")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed >= 1 && parsed <= 10080 {
			minutes = parsed
		}
	}
	ctx, cancel := context.WithCancel(parent)
	go func() {
		cleanupExpiredGuestAccounts()
		ticker := time.NewTicker(time.Duration(minutes) * time.Minute)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				cleanupExpiredGuestAccounts()
			}
		}
	}()
	return cancel
}
