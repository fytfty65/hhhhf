package handlers

import (
	"path/filepath"
	"testing"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
)

// Tests for event-table retention.
//
// The four tables here (messages, planning_events, recommendation_events,
// feedback_logs) grow with usage rather than with user count and previously had
// no TTL at all. The single most important property to pin is the conservative
// default: retention must not delete anything until an operator asks for it,
// because silently deleting a user's chat history on upgrade would be a data-loss
// bug, not a maintenance fix.

// retentionTestDB points the global DB at a real migrated schema in a temp file.
//
// A real schema (rather than AutoMigrate of just four models) is used so the test
// fails if a table name or timestamp column in eventRetentionTables stops
// matching what the schema actually defines — the mistake that would make
// retention silently delete nothing.
func retentionTestDB(t *testing.T) {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("DB_PATH", filepath.Join(dir, "retention.db"))
	if err := database.InitDB(); err != nil {
		t.Fatalf("InitDB: %v", err)
	}
	t.Cleanup(func() {
		if database.DB == nil {
			return
		}
		if sqlDB, err := database.DB.DB(); err == nil && sqlDB != nil {
			_ = sqlDB.Close()
		}
	})
}

// seedRetentionParents creates the user/room that now back the declared
// foreign keys on messages and feedback_logs.
func seedRetentionParents(t *testing.T) {
	t.Helper()
	if err := database.DB.Create(&models.User{ID: "u1", Username: "retention-u1"}).Error; err != nil {
		t.Fatal(err)
	}
	if err := database.DB.Create(&models.Room{ID: "r1", InviteCode: "RET001", CreatorID: "u1"}).Error; err != nil {
		t.Fatal(err)
	}
}

// seedOldAndNew inserts one old row and one fresh row into every retained table,
// via the real models so a schema change surfaces as a test failure.
func seedOldAndNew(t *testing.T, oldAgeDays int) {
	t.Helper()
	seedRetentionParents(t)
	old := time.Now().UTC().Add(-time.Duration(oldAgeDays) * 24 * time.Hour)
	fresh := time.Now().UTC()

	if err := database.DB.Create(&models.Message{RoomID: "r1", UserID: "u1", Content: "old", CreatedAt: old}).Error; err != nil {
		t.Fatal(err)
	}
	if err := database.DB.Create(&models.Message{RoomID: "r1", UserID: "u1", Content: "new", CreatedAt: fresh}).Error; err != nil {
		t.Fatal(err)
	}
	if err := database.DB.Create(&models.PlanningEvent{UserID: "u1", EventType: "plan", CreatedAt: old}).Error; err != nil {
		t.Fatal(err)
	}
	if err := database.DB.Create(&models.PlanningEvent{UserID: "u1", EventType: "plan", CreatedAt: fresh}).Error; err != nil {
		t.Fatal(err)
	}
	if err := database.DB.Create(&models.RecommendationEvent{UserID: "u1", Target: "t", CreatedAt: old}).Error; err != nil {
		t.Fatal(err)
	}
	if err := database.DB.Create(&models.RecommendationEvent{UserID: "u1", Target: "t", CreatedAt: fresh}).Error; err != nil {
		t.Fatal(err)
	}
	if err := database.DB.Create(&models.FeedbackLog{RoomID: "r1", UserID: "u1", Target: "t", Score: 1, CreatedAt: old}).Error; err != nil {
		t.Fatal(err)
	}
	if err := database.DB.Create(&models.FeedbackLog{RoomID: "r1", UserID: "u1", Target: "t", Score: 1, CreatedAt: fresh}).Error; err != nil {
		t.Fatal(err)
	}
}

func countRows(t *testing.T, model any) int64 {
	t.Helper()
	var count int64
	if err := database.DB.Model(model).Count(&count).Error; err != nil {
		t.Fatal(err)
	}
	return count
}

// TestEventRetentionIsDisabledByDefault is the data-safety property.
func TestEventRetentionIsDisabledByDefault(t *testing.T) {
	t.Setenv("EVENT_RETENTION_ENABLED", "")
	if eventRetentionEnabled() {
		t.Fatal("retention must be off unless explicitly enabled")
	}

	retentionTestDB(t)
	seedOldAndNew(t, 4000) // far older than any default window

	cleanupExpiredEvents()

	// Nothing may be deleted while retention is opt-in and not opted into.
	for _, table := range eventRetentionTables() {
		if got := countRows(t, table.Model); got != 2 {
			t.Fatalf("%s: retention must not delete anything by default, %d of 2 rows remain", table.Name, got)
		}
	}
}

// TestEventRetentionDeletesOnlyExpiredRows is the functional case.
func TestEventRetentionDeletesOnlyExpiredRows(t *testing.T) {
	t.Setenv("EVENT_RETENTION_ENABLED", "true")
	t.Setenv("EVENT_RETENTION_DAYS", "365")

	retentionTestDB(t)
	seedOldAndNew(t, 400) // older than 365d; the fresh row is not

	cleanupExpiredEvents()

	for _, table := range eventRetentionTables() {
		if got := countRows(t, table.Model); got != 1 {
			t.Fatalf("%s: expected only the fresh row to survive, %d of 2 rows remain", table.Name, got)
		}
	}

	snapshot := eventRetentionSnapshot()
	deleted, ok := snapshot["last_deleted"].(map[string]int64)
	if !ok {
		t.Fatalf("snapshot must report per-table deletion counts, got %#v", snapshot["last_deleted"])
	}
	for _, table := range eventRetentionTables() {
		if deleted[table.Name] != 1 {
			t.Fatalf("%s: expected 1 deletion reported, got %d", table.Name, deleted[table.Name])
		}
	}
}

// TestEventRetentionKeepsRecentRows guards against an inverted comparison, which
// would delete everything *newer* than the cutoff.
func TestEventRetentionKeepsRecentRows(t *testing.T) {
	t.Setenv("EVENT_RETENTION_ENABLED", "true")
	t.Setenv("EVENT_RETENTION_DAYS", "30")

	retentionTestDB(t)
	seedRetentionParents(t)
	// Both rows inside the 30-day window.
	if err := database.DB.Create(&models.Message{RoomID: "r1", UserID: "u1", Content: "a", CreatedAt: time.Now().UTC().Add(-1 * 24 * time.Hour)}).Error; err != nil {
		t.Fatal(err)
	}
	if err := database.DB.Create(&models.Message{RoomID: "r1", UserID: "u1", Content: "b", CreatedAt: time.Now().UTC()}).Error; err != nil {
		t.Fatal(err)
	}

	cleanupExpiredEvents()

	if got := countRows(t, &models.Message{}); got != 2 {
		t.Fatalf("recent rows must survive, %d of 2 remain", got)
	}
}

// TestEventRetentionDaysClamping pins the window bounds: a typo must not be able
// to wipe recent history (too small) or silently disable retention (too large).
func TestEventRetentionDaysClamping(t *testing.T) {
	cases := map[string]int{
		"":      defaultEventRetentionDays,
		"abc":   defaultEventRetentionDays,
		"0":     defaultEventRetentionDays,
		"7":     defaultEventRetentionDays, // below min: refuse
		"29":    defaultEventRetentionDays, // just below min
		"30":    30,                        // min boundary
		"365":   365,
		"3650":  3650,                      // max boundary
		"3651":  defaultEventRetentionDays, // just above max
		"99999": defaultEventRetentionDays,
	}
	for raw, want := range cases {
		t.Run("days="+raw, func(t *testing.T) {
			t.Setenv("EVENT_RETENTION_DAYS", raw)
			if got := eventRetentionDays(); got != want {
				t.Fatalf("EVENT_RETENTION_DAYS=%q: expected %d, got %d", raw, want, got)
			}
		})
	}
}

// TestEventRetentionEnabledParsing accepts the spellings an operator might use.
func TestEventRetentionEnabledParsing(t *testing.T) {
	for _, raw := range []string{"1", "true", "TRUE", "yes", "on", " on "} {
		t.Run(raw, func(t *testing.T) {
			t.Setenv("EVENT_RETENTION_ENABLED", raw)
			if !eventRetentionEnabled() {
				t.Fatalf("%q must enable retention", raw)
			}
		})
	}
	for _, raw := range []string{"", "0", "false", "no", "off", "maybe"} {
		t.Run("off:"+raw, func(t *testing.T) {
			t.Setenv("EVENT_RETENTION_ENABLED", raw)
			if eventRetentionEnabled() {
				t.Fatalf("%q must not enable retention", raw)
			}
		})
	}
}

// TestEventRetentionTablesMatchTheSchema is the guard that matters most for
// correctness over time: if a model is renamed or its timestamp column changes,
// the DELETE would match zero rows and retention would silently stop working.
func TestEventRetentionTablesMatchTheSchema(t *testing.T) {
	retentionTestDB(t)

	for _, table := range eventRetentionTables() {
		t.Run(table.Name, func(t *testing.T) {
			if !database.DB.Migrator().HasTable(table.Model) {
				t.Fatalf("model does not map to an existing table (name drift?)")
			}
			if !database.DB.Migrator().HasColumn(table.Model, table.Timestamp) {
				t.Fatalf("timestamp column %q does not exist on this model", table.Timestamp)
			}
		})
	}
}

// TestEventRetentionSnapshotShape documents the operator-facing payload.
func TestEventRetentionSnapshotShape(t *testing.T) {
	t.Setenv("EVENT_RETENTION_ENABLED", "true")
	t.Setenv("EVENT_RETENTION_DAYS", "90")

	snapshot := eventRetentionSnapshot()
	if snapshot["enabled"] != true {
		t.Fatalf("enabled must reflect the env, got %v", snapshot["enabled"])
	}
	if snapshot["retention_days"] != 90 {
		t.Fatalf("retention_days must be 90, got %v", snapshot["retention_days"])
	}
	if snapshot["default_enabled"] != false {
		t.Fatal("the payload must state that retention is off by default")
	}
	if _, ok := snapshot["tables"].([]string); !ok {
		t.Fatalf("tables must be a list of names, got %#v", snapshot["tables"])
	}
}
