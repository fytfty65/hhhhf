package database

import (
	"fmt"
	"strings"

	"gateway/internal/models"

	"gorm.io/gorm"
)

// This file contains the two migrations that cannot be expressed with
// AutoMigrate:
//
//   19. money columns move from REAL to INTEGER minor units;
//   20. SQLite tables gain real FOREIGN KEY constraints (SQLite can only add
//       them by rebuilding the table).
//
// Both run inside the versioned transaction in InitDB. v20 is preceded by a
// file backup, and it refuses to report success when `PRAGMA foreign_key_check`
// still finds violations.

// ---- money -------------------------------------------------------------

func columnExists(tx *gorm.DB, table, column string) bool {
	type columnInfo struct {
		Name string
	}
	var columns []columnInfo
	if err := tx.Raw(fmt.Sprintf("PRAGMA table_info(%q)", table)).Scan(&columns).Error; err != nil {
		return false
	}
	for _, c := range columns {
		if c.Name == column {
			return true
		}
	}
	return false
}

func tableExists(tx *gorm.DB, table string) bool {
	var count int64
	if err := tx.Raw("SELECT COUNT(1) FROM sqlite_master WHERE type='table' AND name = ?", table).Scan(&count).Error; err != nil {
		return false
	}
	return count > 0
}

// migrateMoneyToCents converts the legacy REAL columns to INTEGER minor units
// and removes the float columns so no code path can write to them again.
func migrateMoneyToCents(tx *gorm.DB) error {
	if !tableExists(tx, "budget_plans") || !tableExists(tx, "expense_records") {
		return nil
	}
	if err := tx.Exec("ALTER TABLE budget_plans ADD COLUMN total_budget_cents INTEGER NOT NULL DEFAULT 0").Error; err != nil &&
		!strings.Contains(strings.ToLower(err.Error()), "duplicate column") {
		return fmt.Errorf("add budget_plans.total_budget_cents: %w", err)
	}
	if err := tx.Exec("ALTER TABLE expense_records ADD COLUMN amount_cents INTEGER NOT NULL DEFAULT 0").Error; err != nil &&
		!strings.Contains(strings.ToLower(err.Error()), "duplicate column") {
		return fmt.Errorf("add expense_records.amount_cents: %w", err)
	}

	// Backfill before dropping the source columns, then reconcile counts so a
	// rounding bug aborts the migration instead of silently changing budgets.
	if columnExists(tx, "budget_plans", "total_budget") {
		if err := tx.Exec(`UPDATE budget_plans
			SET total_budget_cents = CAST(ROUND(total_budget * 100.0) AS INTEGER)
			WHERE total_budget_cents = 0 AND total_budget > 0`).Error; err != nil {
			return fmt.Errorf("backfill budget_plans: %w", err)
		}
		var mismatches int64
		if err := tx.Raw(`SELECT COUNT(1) FROM budget_plans
			WHERE total_budget > 0 AND total_budget_cents <> CAST(ROUND(total_budget * 100.0) AS INTEGER)`).
			Scan(&mismatches).Error; err != nil {
			return fmt.Errorf("verify budget_plans: %w", err)
		}
		if mismatches > 0 {
			return fmt.Errorf("budget backfill mismatch: %d rows", mismatches)
		}
		if err := tx.Exec("ALTER TABLE budget_plans DROP COLUMN total_budget").Error; err != nil {
			return fmt.Errorf("drop budget_plans.total_budget: %w", err)
		}
	}
	if columnExists(tx, "expense_records", "amount") {
		if err := tx.Exec(`UPDATE expense_records
			SET amount_cents = CAST(ROUND(amount * 100.0) AS INTEGER)
			WHERE amount_cents = 0 AND amount > 0`).Error; err != nil {
			return fmt.Errorf("backfill expense_records: %w", err)
		}
		var mismatches int64
		if err := tx.Raw(`SELECT COUNT(1) FROM expense_records
			WHERE amount > 0 AND amount_cents <> CAST(ROUND(amount * 100.0) AS INTEGER)`).
			Scan(&mismatches).Error; err != nil {
			return fmt.Errorf("verify expense_records: %w", err)
		}
		if mismatches > 0 {
			return fmt.Errorf("expense backfill mismatch: %d rows", mismatches)
		}
		if err := tx.Exec("ALTER TABLE expense_records DROP COLUMN amount").Error; err != nil {
			return fmt.Errorf("drop expense_records.amount: %w", err)
		}
	}

	for _, table := range []string{"budget_plans", "expense_records"} {
		if err := tx.Exec(fmt.Sprintf("UPDATE %s SET currency = 'CNY' WHERE currency IS NULL OR TRIM(currency) = ''", table)).Error; err != nil {
			return err
		}
	}
	// TravelOrder gained amount_cents/currency in the same release. AutoMigrate
	// only adds the new columns; existing orders have no amount to convert.
	return tx.AutoMigrate(&models.TravelOrder{})
}

// ---- foreign keys ------------------------------------------------------

type fkTableSpec struct {
	name    string
	columns []string
	// ddl contains one %s placeholder for the temporary table name.
	ddl string
	// indexes contain one %s placeholder for the final table name.
	indexes []string
}

func fkSpecs() []fkTableSpec {
	return []fkTableSpec{
		{
			name:    "rooms",
			columns: []string{"id", "invite_code", "name", "creator_id", "status", "created_at"},
			ddl: `CREATE TABLE %s (
				id TEXT PRIMARY KEY,
				invite_code TEXT,
				name TEXT,
				creator_id TEXT,
				status TEXT DEFAULT 'planning',
				created_at DATETIME,
				FOREIGN KEY (creator_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE UNIQUE INDEX IF NOT EXISTS ux_rooms_invite_code ON %s(invite_code)",
				"CREATE INDEX IF NOT EXISTS idx_rooms_creator_id ON %s(creator_id)",
			},
		},
		{
			name:    "trip_plans",
			columns: []string{"id", "user_id", "title", "dest_city", "content", "days", "travelers", "created_at"},
			ddl: `CREATE TABLE %s (
				id TEXT PRIMARY KEY,
				user_id TEXT,
				title TEXT,
				dest_city TEXT,
				content TEXT,
				days INTEGER DEFAULT 0,
				travelers INTEGER DEFAULT 0,
				created_at DATETIME,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE INDEX IF NOT EXISTS idx_trip_plans_user_id ON %s(user_id)",
				"CREATE INDEX IF NOT EXISTS idx_trip_plans_dest_city ON %s(dest_city)",
			},
		},
		{
			name:    "room_members",
			columns: []string{"room_id", "user_id", "role"},
			ddl: `CREATE TABLE %s (
				room_id TEXT NOT NULL,
				user_id TEXT NOT NULL,
				role TEXT DEFAULT 'member',
				PRIMARY KEY (room_id, user_id),
				FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE INDEX IF NOT EXISTS idx_room_members_user_id ON %s(user_id)",
			},
		},
		{
			name:    "messages",
			columns: []string{"id", "room_id", "user_id", "role_type", "content", "created_at"},
			ddl: `CREATE TABLE %s (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				room_id TEXT,
				user_id TEXT,
				role_type TEXT,
				content TEXT,
				created_at DATETIME,
				FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE INDEX IF NOT EXISTS idx_messages_room_id ON %s(room_id)",
			},
		},
		{
			name:    "feedback_logs",
			columns: []string{"id", "room_id", "user_id", "target", "score", "reason", "created_at"},
			ddl: `CREATE TABLE %s (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				room_id TEXT,
				user_id TEXT,
				target TEXT,
				score INTEGER,
				reason TEXT,
				created_at DATETIME,
				FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE INDEX IF NOT EXISTS idx_feedback_logs_room_id ON %s(room_id)",
				"CREATE INDEX IF NOT EXISTS idx_feedback_logs_user_id ON %s(user_id)",
			},
		},
		{
			name:    "plan_variants",
			columns: []string{"id", "room_id", "name", "style", "route", "created_by", "created_at"},
			ddl: `CREATE TABLE %s (
				id TEXT PRIMARY KEY,
				room_id TEXT,
				name TEXT,
				style TEXT,
				route TEXT,
				created_by TEXT,
				created_at DATETIME,
				FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE,
				FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE INDEX IF NOT EXISTS idx_plan_variants_room_id ON %s(room_id)",
			},
		},
		{
			name:    "plan_variant_votes",
			columns: []string{"id", "variant_id", "user_id", "created_at"},
			ddl: `CREATE TABLE %s (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				variant_id TEXT,
				user_id TEXT,
				created_at DATETIME,
				FOREIGN KEY (variant_id) REFERENCES plan_variants(id) ON DELETE CASCADE,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE UNIQUE INDEX IF NOT EXISTS ux_variant_vote ON %s(variant_id, user_id)",
			},
		},
		{
			name:    "node_annotations",
			columns: []string{"id", "room_id", "trip_id", "node_key", "user_id", "content", "parent_id", "status", "created_at"},
			ddl: `CREATE TABLE %s (
				id TEXT PRIMARY KEY,
				room_id TEXT,
				trip_id TEXT,
				node_key TEXT,
				user_id TEXT,
				content TEXT,
				parent_id TEXT,
				status TEXT DEFAULT 'open',
				created_at DATETIME,
				FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE INDEX IF NOT EXISTS idx_node_annotations_room_id ON %s(room_id)",
				"CREATE INDEX IF NOT EXISTS idx_node_annotations_trip_id ON %s(trip_id)",
				"CREATE INDEX IF NOT EXISTS idx_node_annotations_node_key ON %s(node_key)",
				"CREATE INDEX IF NOT EXISTS idx_node_annotations_user_id ON %s(user_id)",
			},
		},
		{
			name:    "node_annotation_votes",
			columns: []string{"id", "annotation_id", "user_id", "value", "created_at"},
			ddl: `CREATE TABLE %s (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				annotation_id TEXT,
				user_id TEXT,
				value INTEGER,
				created_at DATETIME,
				FOREIGN KEY (annotation_id) REFERENCES node_annotations(id) ON DELETE CASCADE,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE UNIQUE INDEX IF NOT EXISTS ux_annotation_vote ON %s(annotation_id, user_id)",
			},
		},
		{
			name:    "trip_satisfactions",
			columns: []string{"id", "user_id", "trip_id", "score", "comment", "created_at"},
			ddl: `CREATE TABLE %s (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				user_id TEXT,
				trip_id TEXT,
				score INTEGER,
				comment TEXT,
				created_at DATETIME,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE UNIQUE INDEX IF NOT EXISTS ux_satisfaction_user_trip ON %s(user_id, trip_id)",
				"CREATE INDEX IF NOT EXISTS idx_trip_satisfactions_trip_id ON %s(trip_id)",
			},
		},
		{
			name:    "budget_plans",
			columns: []string{"id", "user_id", "trip_id", "total_budget_cents", "currency", "created_at"},
			ddl: `CREATE TABLE %s (
				id TEXT PRIMARY KEY,
				user_id TEXT,
				trip_id TEXT,
				total_budget_cents INTEGER NOT NULL DEFAULT 0,
				currency TEXT DEFAULT 'CNY',
				created_at DATETIME,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE UNIQUE INDEX IF NOT EXISTS ux_budget_trip ON %s(user_id, trip_id)",
			},
		},
		{
			name:    "expense_records",
			columns: []string{"id", "user_id", "trip_id", "category", "amount_cents", "currency", "note", "created_at"},
			ddl: `CREATE TABLE %s (
				id TEXT PRIMARY KEY,
				user_id TEXT,
				trip_id TEXT,
				category TEXT,
				amount_cents INTEGER NOT NULL DEFAULT 0,
				currency TEXT DEFAULT 'CNY',
				note TEXT,
				created_at DATETIME,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE INDEX IF NOT EXISTS idx_expense_records_user_id ON %s(user_id)",
				"CREATE INDEX IF NOT EXISTS idx_expense_records_trip_id ON %s(trip_id)",
			},
		},
		{
			name:    "travel_orders",
			columns: []string{"id", "provider", "provider_order_id", "user_id", "trip_id", "order_type", "amount_cents", "currency", "status", "idempotency_key", "cancellation_note", "created_at", "updated_at"},
			ddl: `CREATE TABLE %s (
				id TEXT PRIMARY KEY,
				provider TEXT,
				provider_order_id TEXT,
				user_id TEXT,
				trip_id TEXT,
				order_type TEXT,
				amount_cents INTEGER NOT NULL DEFAULT 0,
				currency TEXT DEFAULT 'CNY',
				status TEXT,
				idempotency_key TEXT,
				cancellation_note TEXT,
				created_at DATETIME,
				updated_at DATETIME,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE UNIQUE INDEX IF NOT EXISTS ux_travel_orders_idempotency_key ON %s(idempotency_key)",
				"CREATE INDEX IF NOT EXISTS idx_travel_orders_provider ON %s(provider)",
				"CREATE INDEX IF NOT EXISTS idx_travel_orders_provider_order_id ON %s(provider_order_id)",
				"CREATE INDEX IF NOT EXISTS idx_travel_orders_user_id ON %s(user_id)",
				"CREATE INDEX IF NOT EXISTS idx_travel_orders_trip_id ON %s(trip_id)",
				"CREATE INDEX IF NOT EXISTS idx_travel_orders_status ON %s(status)",
			},
		},
		{
			name:    "trip_execution_states",
			columns: []string{"id", "user_id", "trip_id", "plan_id", "node_key", "status", "delay_minutes", "note", "updated_at", "created_at"},
			ddl: `CREATE TABLE %s (
				id TEXT PRIMARY KEY,
				user_id TEXT,
				trip_id TEXT,
				plan_id TEXT,
				node_key TEXT,
				status TEXT,
				delay_minutes INTEGER DEFAULT 0,
				note TEXT,
				updated_at DATETIME,
				created_at DATETIME,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE UNIQUE INDEX IF NOT EXISTS ux_execution_trip_node ON %s(trip_id, node_key)",
				"CREATE INDEX IF NOT EXISTS idx_trip_execution_states_user_id ON %s(user_id)",
				"CREATE INDEX IF NOT EXISTS idx_trip_execution_states_status ON %s(status)",
			},
		},
		{
			name:    "planning_runs",
			columns: []string{"id", "user_id", "trip_id", "model_version", "features", "estimated", "created_at"},
			ddl: `CREATE TABLE %s (
				id TEXT PRIMARY KEY,
				user_id TEXT,
				trip_id TEXT,
				model_version TEXT,
				features TEXT,
				estimated NUMERIC,
				created_at DATETIME,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE INDEX IF NOT EXISTS idx_planning_runs_user_id ON %s(user_id)",
				"CREATE INDEX IF NOT EXISTS idx_planning_runs_trip_id ON %s(trip_id)",
				"CREATE INDEX IF NOT EXISTS idx_planning_runs_model_version ON %s(model_version)",
				"CREATE INDEX IF NOT EXISTS idx_planning_runs_created_at ON %s(created_at)",
			},
		},
		{
			name:    "community_posts",
			columns: []string{"id", "user_id", "title", "content", "dest_city", "tags", "likes", "created_at"},
			ddl: `CREATE TABLE %s (
				id TEXT PRIMARY KEY,
				user_id TEXT,
				title TEXT,
				content TEXT,
				dest_city TEXT,
				tags TEXT,
				likes INTEGER DEFAULT 0,
				created_at DATETIME,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE INDEX IF NOT EXISTS idx_community_posts_user_id ON %s(user_id)",
				"CREATE INDEX IF NOT EXISTS idx_community_posts_dest_city ON %s(dest_city)",
			},
		},
		{
			name:    "post_favorites",
			columns: []string{"id", "post_id", "user_id", "created_at"},
			ddl: `CREATE TABLE %s (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				post_id TEXT,
				user_id TEXT,
				created_at DATETIME,
				FOREIGN KEY (post_id) REFERENCES community_posts(id) ON DELETE CASCADE,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE UNIQUE INDEX IF NOT EXISTS ux_post_favorite ON %s(post_id, user_id)",
				"CREATE INDEX IF NOT EXISTS idx_post_favorites_post_id ON %s(post_id)",
				"CREATE INDEX IF NOT EXISTS idx_post_favorites_user_id ON %s(user_id)",
			},
		},
		{
			name:    "comments",
			columns: []string{"id", "post_id", "user_id", "content", "parent_id", "created_at"},
			ddl: `CREATE TABLE %s (
				id TEXT PRIMARY KEY,
				post_id TEXT,
				user_id TEXT,
				content TEXT,
				parent_id TEXT,
				created_at DATETIME,
				FOREIGN KEY (post_id) REFERENCES community_posts(id) ON DELETE CASCADE,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE INDEX IF NOT EXISTS idx_comments_post_id ON %s(post_id)",
				"CREATE INDEX IF NOT EXISTS idx_comments_user_id ON %s(user_id)",
			},
		},
		{
			name:    "risk_subscriptions",
			columns: []string{"id", "user_id", "city", "coordinate", "baseline", "created_at"},
			ddl: `CREATE TABLE %s (
				id TEXT PRIMARY KEY,
				user_id TEXT,
				city TEXT,
				coordinate TEXT,
				baseline TEXT,
				created_at DATETIME,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE INDEX IF NOT EXISTS idx_risk_subscriptions_user_id ON %s(user_id)",
				"CREATE INDEX IF NOT EXISTS idx_risk_subscriptions_city ON %s(city)",
			},
		},
		{
			name:    "risk_notifications",
			columns: []string{"id", "user_id", "trip_id", "city", "type", "severity", "title", "detail", "source", "estimated", "observed_at", "signal_sources", "replan_proposal", "status", "created_at", "read_at"},
			ddl: `CREATE TABLE %s (
				id TEXT PRIMARY KEY,
				user_id TEXT,
				trip_id TEXT,
				city TEXT,
				type TEXT,
				severity TEXT,
				title TEXT,
				detail TEXT,
				source TEXT,
				estimated NUMERIC,
				observed_at DATETIME,
				signal_sources TEXT,
				replan_proposal TEXT,
				status TEXT,
				created_at DATETIME,
				read_at DATETIME,
				FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
			)`,
			indexes: []string{
				"CREATE INDEX IF NOT EXISTS idx_risk_notifications_user_id ON %s(user_id)",
				"CREATE INDEX IF NOT EXISTS idx_risk_notifications_trip_id ON %s(trip_id)",
				"CREATE INDEX IF NOT EXISTS idx_risk_notifications_city ON %s(city)",
				"CREATE INDEX IF NOT EXISTS idx_risk_notifications_severity ON %s(severity)",
				"CREATE INDEX IF NOT EXISTS idx_risk_notifications_status ON %s(status)",
				"CREATE INDEX IF NOT EXISTS idx_risk_notifications_created_at ON %s(created_at)",
			},
		},
	}
}

// cleanupOrphans deletes rows that would violate the new constraints. It runs
// before the rebuild, with enforcement still off, so each statement is a plain
// data-repair operation.
func cleanupOrphans(tx *gorm.DB) error {
	if !tableExists(tx, "users") {
		return nil
	}
	steps := []struct {
		table string
		sql   string
	}{
		{"rooms", "DELETE FROM rooms WHERE creator_id NOT IN (SELECT id FROM users)"},
		{"room_members", "DELETE FROM room_members WHERE room_id NOT IN (SELECT id FROM rooms) OR user_id NOT IN (SELECT id FROM users)"},
		{"messages", "DELETE FROM messages WHERE room_id NOT IN (SELECT id FROM rooms)"},
		{"feedback_logs", "DELETE FROM feedback_logs WHERE room_id NOT IN (SELECT id FROM rooms) OR user_id NOT IN (SELECT id FROM users)"},
		{"plan_variant_votes", "DELETE FROM plan_variant_votes WHERE variant_id NOT IN (SELECT id FROM plan_variants) OR user_id NOT IN (SELECT id FROM users)"},
		{"plan_variants", "DELETE FROM plan_variants WHERE room_id NOT IN (SELECT id FROM rooms) OR created_by NOT IN (SELECT id FROM users)"},
		{"plan_variant_votes", "DELETE FROM plan_variant_votes WHERE variant_id NOT IN (SELECT id FROM plan_variants)"},
		{"node_annotation_votes", "DELETE FROM node_annotation_votes WHERE annotation_id NOT IN (SELECT id FROM node_annotations) OR user_id NOT IN (SELECT id FROM users)"},
		{"node_annotations", "DELETE FROM node_annotations WHERE room_id NOT IN (SELECT id FROM rooms) OR user_id NOT IN (SELECT id FROM users)"},
		{"node_annotation_votes", "DELETE FROM node_annotation_votes WHERE annotation_id NOT IN (SELECT id FROM node_annotations)"},
		{"trip_plans", "DELETE FROM trip_plans WHERE user_id NOT IN (SELECT id FROM users)"},
		{"trip_satisfactions", "DELETE FROM trip_satisfactions WHERE user_id NOT IN (SELECT id FROM users)"},
		{"budget_plans", "DELETE FROM budget_plans WHERE user_id NOT IN (SELECT id FROM users)"},
		{"expense_records", "DELETE FROM expense_records WHERE user_id NOT IN (SELECT id FROM users)"},
		{"travel_orders", "DELETE FROM travel_orders WHERE user_id NOT IN (SELECT id FROM users)"},
		{"trip_execution_states", "DELETE FROM trip_execution_states WHERE user_id NOT IN (SELECT id FROM users)"},
		{"planning_runs", "DELETE FROM planning_runs WHERE user_id NOT IN (SELECT id FROM users)"},
		{"comments", "DELETE FROM comments WHERE post_id NOT IN (SELECT id FROM community_posts) OR user_id NOT IN (SELECT id FROM users)"},
		{"post_favorites", "DELETE FROM post_favorites WHERE post_id NOT IN (SELECT id FROM community_posts) OR user_id NOT IN (SELECT id FROM users)"},
		{"community_posts", "DELETE FROM community_posts WHERE user_id NOT IN (SELECT id FROM users)"},
		{"comments", "DELETE FROM comments WHERE post_id NOT IN (SELECT id FROM community_posts)"},
		{"post_favorites", "DELETE FROM post_favorites WHERE post_id NOT IN (SELECT id FROM community_posts)"},
		{"risk_subscriptions", "DELETE FROM risk_subscriptions WHERE user_id NOT IN (SELECT id FROM users)"},
		{"risk_notifications", "DELETE FROM risk_notifications WHERE user_id NOT IN (SELECT id FROM users)"},
	}
	for _, step := range steps {
		if !tableExists(tx, step.table) {
			continue
		}
		// PLAN: Aggregated orphan cleanup before enforcing foreign keys.
		if err := tx.Exec(step.sql).Error; err != nil {
			return fmt.Errorf("cleanup %s: %w", step.table, err)
		}
	}
	return nil
}

func rebuildTable(tx *gorm.DB, spec fkTableSpec) error {
	tmp := spec.name + "__fk_tmp"
	if err := tx.Exec("DROP TABLE IF EXISTS " + tmp).Error; err != nil {
		return err
	}
	if err := tx.Exec(fmt.Sprintf(spec.ddl, tmp)).Error; err != nil {
		return fmt.Errorf("create %s: %w", tmp, err)
	}
	columnList := strings.Join(spec.columns, ", ")
	if err := tx.Exec(fmt.Sprintf("INSERT INTO %s (%s) SELECT %s FROM %s", tmp, columnList, columnList, spec.name)).Error; err != nil {
		return fmt.Errorf("copy %s: %w", spec.name, err)
	}
	if err := tx.Exec("DROP TABLE " + spec.name).Error; err != nil {
		return fmt.Errorf("drop old %s: %w", spec.name, err)
	}
	if err := tx.Exec("ALTER TABLE " + tmp + " RENAME TO " + spec.name).Error; err != nil {
		return fmt.Errorf("rename %s: %w", spec.name, err)
	}
	for _, index := range spec.indexes {
		if err := tx.Exec(fmt.Sprintf(index, spec.name)).Error; err != nil {
			return fmt.Errorf("index %s: %w", spec.name, err)
		}
	}
	return nil
}

// migrateForeignKeys cleans orphans, rebuilds the related tables with explicit
// ON DELETE policies, and verifies the result with `PRAGMA foreign_key_check`.
func migrateForeignKeys(tx *gorm.DB) error {
	if !tableExists(tx, "users") {
		return nil
	}
	if err := cleanupOrphans(tx); err != nil {
		return err
	}
	for _, spec := range fkSpecs() {
		if !tableExists(tx, spec.name) {
			continue
		}
		if err := rebuildTable(tx, spec); err != nil {
			return err
		}
	}
	return verifyForeignKeys(tx)
}

type fkViolation struct {
	Table  string `gorm:"column:table"`
	RowID  int64  `gorm:"column:rowid"`
	Parent string `gorm:"column:parent"`
	FKID   int    `gorm:"column:fkid"`
}

func verifyForeignKeys(tx *gorm.DB) error {
	var violations []fkViolation
	if err := tx.Raw("PRAGMA foreign_key_check").Scan(&violations).Error; err != nil {
		return fmt.Errorf("foreign_key_check failed: %w", err)
	}
	if len(violations) > 0 {
		return fmt.Errorf("foreign_key_check found %d violations, first: %s rowid %d -> %s",
			len(violations), violations[0].Table, violations[0].RowID, violations[0].Parent)
	}
	return nil
}

// ForeignKeyReport exposes the integrity check to health endpoints and tests.
func ForeignKeyReport() (int, error) {
	if DB == nil {
		return 0, fmt.Errorf("database is not initialized")
	}
	var violations []fkViolation
	if err := DB.Raw("PRAGMA foreign_key_check").Scan(&violations).Error; err != nil {
		return 0, err
	}
	return len(violations), nil
}

// ForeignKeysEnabled reports the current connection's enforcement flag.
func ForeignKeysEnabled() (bool, error) {
	if DB == nil {
		return false, fmt.Errorf("database is not initialized")
	}
	var enabled int
	if err := DB.Raw("PRAGMA foreign_keys").Row().Scan(&enabled); err != nil {
		return false, err
	}
	return enabled == 1, nil
}
