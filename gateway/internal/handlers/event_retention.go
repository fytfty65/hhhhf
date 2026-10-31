package handlers

import (
	"context"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
)

// Retention for the high-volume event tables.
//
// Background: `messages`, `planning_events`, `recommendation_events` and
// `feedback_logs` had no TTL, no archival and no cleanup. They are the four
// tables that grow with *usage* rather than with the number of users, so the
// single-writer SQLite file they live in grows without bound and every indexed
// scan over them gets slower over time. Retention already existed for
// ModelAuditEvent and ProviderQualityAlertDelivery; this extends the same pattern
// to the four that were missing it.
//
// Opt-in by default, deliberately. Deleting a user's chat history or feedback is
// not a change to make silently on upgrade: an operator who wants a bounded
// database sets EVENT_RETENTION_DAYS, and until then nothing is removed. This is
// the conservative half of the fix (the mechanism, the bound, the reporting); the
// aggressive half (choosing a default and deleting on first boot) is a product
// decision, not a maintenance one.

const (
	// defaultEventRetentionDays is used once retention is enabled but no explicit
	// window is given. A year keeps seasonal comparisons usable.
	defaultEventRetentionDays = 365
	// minEventRetentionDays refuses to delete anything younger than a month, so a
	// typo cannot wipe recent history.
	minEventRetentionDays = 30
	// maxEventRetentionDays bounds the window so a nonsense value cannot disable
	// retention while appearing to enable it.
	maxEventRetentionDays = 3650
	// defaultEventCleanupIntervalMinutes is how often the loop runs (daily).
	defaultEventCleanupIntervalMinutes = 1440
)

// eventRetentionState records the outcome of the last cleanup run so the health
// surface can report it without re-querying.
var (
	eventRetentionMu       sync.Mutex
	eventRetentionLastRun  time.Time
	eventRetentionLastRows map[string]int64
	eventRetentionLastErr  string
)

// eventRetentionEnabled reports whether pruning is switched on.
func eventRetentionEnabled() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("EVENT_RETENTION_ENABLED"))) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

// eventRetentionDays returns the configured window, clamped to a safe range.
func eventRetentionDays() int {
	raw := strings.TrimSpace(os.Getenv("EVENT_RETENTION_DAYS"))
	if raw == "" {
		return defaultEventRetentionDays
	}
	parsed, err := strconv.Atoi(raw)
	if err != nil || parsed < minEventRetentionDays || parsed > maxEventRetentionDays {
		return defaultEventRetentionDays
	}
	return parsed
}

// eventRetentionTables lists the tables to prune and the column carrying their
// timestamp. Keeping this as data means adding a table is a one-line change and
// the loop body cannot drift between tables.
func eventRetentionTables() []struct {
	Name      string
	Timestamp string
	Model     any
} {
	return []struct {
		Name      string
		Timestamp string
		Model     any
	}{
		{"messages", "created_at", &models.Message{}},
		{"planning_events", "created_at", &models.PlanningEvent{}},
		{"recommendation_events", "created_at", &models.RecommendationEvent{}},
		{"feedback_logs", "created_at", &models.FeedbackLog{}},
	}
}

// cleanupExpiredEvents deletes rows older than the window from every table in
// eventRetentionTables, and records what happened.
//
// It is a no-op unless retention is explicitly enabled, so calling it is safe
// from a startup path.
func cleanupExpiredEvents() {
	rows := map[string]int64{}
	if !eventRetentionEnabled() {
		eventRetentionMu.Lock()
		eventRetentionLastRun = time.Now().UTC()
		eventRetentionLastRows = rows
		eventRetentionLastErr = ""
		eventRetentionMu.Unlock()
		return
	}
	if database.DB == nil {
		eventRetentionMu.Lock()
		eventRetentionLastRun = time.Now().UTC()
		eventRetentionLastRows = rows
		eventRetentionLastErr = "database_unavailable"
		eventRetentionMu.Unlock()
		return
	}

	days := eventRetentionDays()
	cutoff := time.Now().UTC().Add(-time.Duration(days) * 24 * time.Hour)
	var lastErr string
	for _, table := range eventRetentionTables() {
		if table.Model == nil {
			continue
		}
		result := database.DB.Where(table.Timestamp+" < ?", cutoff).Delete(table.Model)
		rows[table.Name] = result.RowsAffected
		if result.Error != nil && lastErr == "" {
			// Record the first failure rather than aborting: one unreadable table
			// should not stop the others from being pruned.
			lastErr = table.Name + ": " + result.Error.Error()
		}
	}

	eventRetentionMu.Lock()
	eventRetentionLastRun = time.Now().UTC()
	eventRetentionLastRows = rows
	eventRetentionLastErr = lastErr
	eventRetentionMu.Unlock()
}

// eventRetentionSnapshot reports the retention configuration and last run.
func eventRetentionSnapshot() map[string]any {
	eventRetentionMu.Lock()
	defer eventRetentionMu.Unlock()

	deleted := map[string]int64{}
	for name, count := range eventRetentionLastRows {
		deleted[name] = count
	}
	snapshot := map[string]any{
		"enabled":         eventRetentionEnabled(),
		"retention_days":  eventRetentionDays(),
		"tables":          []string{"messages", "planning_events", "recommendation_events", "feedback_logs"},
		"last_run_unix":   int64(0),
		"last_deleted":    deleted,
		"last_error":      eventRetentionLastErr,
		"min_days":        minEventRetentionDays,
		"max_days":        maxEventRetentionDays,
		"default_enabled": false,
	}
	if !eventRetentionLastRun.IsZero() {
		snapshot["last_run_unix"] = eventRetentionLastRun.Unix()
	}
	return snapshot
}

// EventRetentionCleanupHealthHandler exposes the retention state for operators.
// Like the model-audit health handler it reports configuration and counts only,
// never row contents.
func EventRetentionCleanupHealthHandler(c *gin.Context) {
	if _, ok := currentUserIDOrReject(c); !ok {
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "available", "retention": eventRetentionSnapshot()})
}

// StartEventRetentionCleanupLoop prunes the event tables on an interval.
//
// The first run happens immediately so an operator who enables retention sees its
// effect without waiting a day. Returns a cancel func for graceful shutdown.
func StartEventRetentionCleanupLoop(parent context.Context) context.CancelFunc {
	if parent == nil {
		parent = context.Background()
	}
	minutes := defaultEventCleanupIntervalMinutes
	if raw := strings.TrimSpace(os.Getenv("EVENT_RETENTION_CLEANUP_INTERVAL_MINUTES")); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed >= 1 && parsed <= 10080 {
			minutes = parsed
		}
	}
	ctx, cancel := context.WithCancel(parent)
	go func() {
		cleanupExpiredEvents()
		ticker := time.NewTicker(time.Duration(minutes) * time.Minute)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				cleanupExpiredEvents()
			case <-ctx.Done():
				return
			}
		}
	}()
	return cancel
}
