package database

import (
	"path/filepath"
	"strings"
	"testing"

	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

// Migration tests for #5/#6.
//
// The migrations themselves run during InitDB, but InitDB always starts from the
// current model definitions — it cannot reproduce a database written by an older
// build. These tests therefore build a legacy schema by hand (REAL money columns,
// no foreign keys), then run the two migration functions directly. That is the
// only way to prove the float→cents backfill and the orphan cleanup actually act
// on legacy rows rather than being no-ops against the new schema.

func legacyDB(t *testing.T) *gorm.DB {
	t.Helper()
	path := filepath.Join(t.TempDir(), "legacy.db")
	// Foreign keys off, as on a pre-v20 database.
	db, err := gorm.Open(sqlite.Open(path+"?_foreign_keys=0"), &gorm.Config{
		Logger: logger.Default.LogMode(logger.Silent),
	})
	if err != nil {
		t.Fatalf("open legacy db: %v", err)
	}
	// PRAGMA foreign_keys is per connection; a single connection keeps the test's
	// explicit toggles deterministic.
	sqlDB, err := db.DB()
	if err != nil {
		t.Fatalf("legacy pool: %v", err)
	}
	sqlDB.SetMaxOpenConns(1)
	t.Cleanup(func() {
		_ = sqlDB.Close()
	})

	stmts := []string{
		`CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, password_hash TEXT, nickname TEXT, created_at DATETIME)`,
		`CREATE TABLE rooms (id TEXT PRIMARY KEY, invite_code TEXT, name TEXT, creator_id TEXT, status TEXT, created_at DATETIME)`,
		`CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, room_id TEXT, user_id TEXT, role_type TEXT, content TEXT, created_at DATETIME)`,
		`CREATE TABLE feedback_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, room_id TEXT, user_id TEXT, target TEXT, score INTEGER, reason TEXT, created_at DATETIME)`,
		`CREATE TABLE trip_plans (id TEXT PRIMARY KEY, user_id TEXT, title TEXT, dest_city TEXT, content TEXT, days INTEGER, travelers INTEGER, created_at DATETIME)`,
		`CREATE TABLE budget_plans (id TEXT PRIMARY KEY, user_id TEXT, trip_id TEXT, total_budget REAL, currency TEXT, created_at DATETIME)`,
		`CREATE TABLE expense_records (id TEXT PRIMARY KEY, user_id TEXT, trip_id TEXT, category TEXT, amount REAL, currency TEXT, note TEXT, created_at DATETIME)`,
		`CREATE TABLE travel_orders (id TEXT PRIMARY KEY, provider TEXT, provider_order_id TEXT, user_id TEXT, trip_id TEXT, order_type TEXT, status TEXT, idempotency_key TEXT, cancellation_note TEXT, created_at DATETIME, updated_at DATETIME)`,
	}
	for _, stmt := range stmts {
		if err := db.Exec(stmt).Error; err != nil {
			t.Fatalf("create legacy table: %v\n%s", err, stmt)
		}
	}
	return db
}

// TestMigrateMoneyToCentsBackfillsLegacyRows is the reconciliation test: every
// legacy REAL amount must survive as the same value in cents, and the float
// column must be gone afterwards so no path can write it again.
func TestMigrateMoneyToCentsBackfillsLegacyRows(t *testing.T) {
	db := legacyDB(t)
	mustExec := func(sql string, args ...any) {
		t.Helper()
		if err := db.Exec(sql, args...).Error; err != nil {
			t.Fatalf("seed: %v", err)
		}
	}
	mustExec(`INSERT INTO users (id, username) VALUES ('u1', 'legacy-user')`)
	mustExec(`INSERT INTO budget_plans (id, user_id, trip_id, total_budget, currency) VALUES ('b1', 'u1', 't1', 128.50, NULL)`)
	mustExec(`INSERT INTO budget_plans (id, user_id, trip_id, total_budget, currency) VALUES ('b2', 'u1', 't2', 0, 'JPY')`)
	mustExec(`INSERT INTO expense_records (id, user_id, trip_id, category, amount) VALUES ('e1', 'u1', 't1', 'food', 0.10)`)
	mustExec(`INSERT INTO expense_records (id, user_id, trip_id, category, amount) VALUES ('e2', 'u1', 't1', 'hotel', 23.45)`)
	mustExec(`INSERT INTO expense_records (id, user_id, trip_id, category, amount) VALUES ('e3', 'u1', 't1', 'ticket', 0)`)
	mustExec(`INSERT INTO travel_orders (id, user_id, trip_id) VALUES ('o1', 'u1', 't1')`)

	if err := migrateMoneyToCents(db); err != nil {
		t.Fatalf("migrateMoneyToCents: %v", err)
	}

	cases := []struct {
		query string
		want  int64
	}{
		{`SELECT total_budget_cents FROM budget_plans WHERE id = 'b1'`, 12850},
		{`SELECT total_budget_cents FROM budget_plans WHERE id = 'b2'`, 0},
		{`SELECT amount_cents FROM expense_records WHERE id = 'e1'`, 10},
		{`SELECT amount_cents FROM expense_records WHERE id = 'e2'`, 2345},
		{`SELECT amount_cents FROM expense_records WHERE id = 'e3'`, 0},
	}
	for _, c := range cases {
		var got int64
		if err := db.Raw(c.query).Scan(&got).Error; err != nil {
			t.Fatalf("%s: %v", c.query, err)
		}
		if got != c.want {
			t.Fatalf("%s: expected %d cents, got %d", c.query, c.want, got)
		}
	}

	// The float columns must not survive: leaving them behind is how a later
	// writer reintroduces the precision bug.
	if columnExists(db, "budget_plans", "total_budget") {
		t.Fatal("budget_plans.total_budget must be dropped")
	}
	if columnExists(db, "expense_records", "amount") {
		t.Fatal("expense_records.amount must be dropped")
	}
	var currency string
	if err := db.Raw(`SELECT currency FROM budget_plans WHERE id = 'b1'`).Scan(&currency).Error; err != nil {
		t.Fatal(err)
	}
	if currency != "CNY" {
		t.Fatalf("an empty currency must default to CNY, got %q", currency)
	}

	// Re-running must be harmless (deployments can retry a failed migration).
	if err := migrateMoneyToCents(db); err != nil {
		t.Fatalf("migrateMoneyToCents must be idempotent: %v", err)
	}
}

// TestMigrateMoneyToCentsAbortsOnReconciliationMismatch pins the safety valve:
// if a pre-existing cents value disagrees with its REAL source, the migration
// must fail loudly instead of silently accepting divergent money.
func TestMigrateMoneyToCentsAbortsOnReconciliationMismatch(t *testing.T) {
	db := legacyDB(t)
	// Pre-create the cents column and a value that disagrees with its REAL source.
	if err := db.Exec(`ALTER TABLE budget_plans ADD COLUMN total_budget_cents INTEGER NOT NULL DEFAULT 0`).Error; err != nil {
		t.Fatalf("prepare cents column: %v", err)
	}
	if err := db.Exec(`INSERT INTO budget_plans (id, user_id, trip_id, total_budget, total_budget_cents) VALUES ('b1', 'u1', 't1', 10.00, 999)`).Error; err != nil {
		t.Fatalf("seed budget with conflicting cents: %v", err)
	}

	err := migrateMoneyToCents(db)
	if err == nil {
		t.Fatal("a cents/REAL mismatch must abort the migration")
	}
	if !strings.Contains(err.Error(), "mismatch") {
		t.Fatalf("expected a reconciliation error, got %v", err)
	}
}

// TestMigrateForeignKeysCleansOrphansAndEnforcesCascade covers the sequence the
// reviewer asked for: remove pre-existing orphans, rebuild with constraints, then
// verify enforcement and the declared ON DELETE strategy on real rows.
func TestMigrateForeignKeysCleansOrphansAndEnforcesCascade(t *testing.T) {
	db := legacyDB(t)
	mustExec := func(sql string, args ...any) {
		t.Helper()
		if err := db.Exec(sql, args...).Error; err != nil {
			t.Fatalf("seed: %v", err)
		}
	}
	mustExec(`PRAGMA foreign_keys=OFF`)
	mustExec(`INSERT INTO users (id, username) VALUES ('u1', 'legacy-user')`)
	// One valid room, one whose creator does not exist.
	mustExec(`INSERT INTO rooms (id, invite_code, creator_id) VALUES ('r1', 'R1', 'u1')`)
	mustExec(`INSERT INTO rooms (id, invite_code, creator_id) VALUES ('r2', 'R2', 'ghost')`)
	// Two valid messages in r1, one whose room never existed.
	mustExec(`INSERT INTO messages (room_id, user_id, content) VALUES ('r1', 'u1', 'keep-1')`)
	mustExec(`INSERT INTO messages (room_id, user_id, content) VALUES ('r1', 'u1', 'keep-2')`)
	mustExec(`INSERT INTO messages (room_id, user_id, content) VALUES ('r999', 'u1', 'orphan')`)
	// Orphan feedback, trip, budget, expense and order rows for a missing user.
	mustExec(`INSERT INTO feedback_logs (room_id, user_id, target, score) VALUES ('r1', 'u1', 't', 1)`)
	mustExec(`INSERT INTO feedback_logs (room_id, user_id, target, score) VALUES ('r1', 'ghost', 't', 1)`)
	mustExec(`INSERT INTO trip_plans (id, user_id, title) VALUES ('tp1', 'u1', 'keep')`)
	mustExec(`INSERT INTO trip_plans (id, user_id, title) VALUES ('tp2', 'ghost', 'orphan')`)
	mustExec(`INSERT INTO budget_plans (id, user_id, trip_id, total_budget) VALUES ('b1', 'u1', 't1', 10.0)`)
	mustExec(`INSERT INTO budget_plans (id, user_id, trip_id, total_budget) VALUES ('b2', 'ghost', 't1', 10.0)`)
	mustExec(`INSERT INTO expense_records (id, user_id, trip_id, amount) VALUES ('e1', 'u1', 't1', 1.0)`)
	mustExec(`INSERT INTO expense_records (id, user_id, trip_id, amount) VALUES ('e2', 'ghost', 't1', 1.0)`)
	mustExec(`INSERT INTO travel_orders (id, user_id, trip_id) VALUES ('o1', 'u1', 't1')`)
	mustExec(`INSERT INTO travel_orders (id, user_id, trip_id) VALUES ('o2', 'ghost', 't1')`)

	// Production runs money (v19) before foreign keys (v20); the legacy order
	// table has no cents columns, so mirror that order here. Enforcement stays
	// off during the rebuild, exactly as in InitDB.
	if err := migrateMoneyToCents(db); err != nil {
		t.Fatalf("migrateMoneyToCents: %v", err)
	}
	if err := migrateForeignKeys(db); err != nil {
		t.Fatalf("migrateForeignKeys: %v", err)
	}
	if err := db.Exec(`PRAGMA foreign_keys=ON`).Error; err != nil {
		t.Fatalf("enable foreign keys: %v", err)
	}

	count := func(table string) int64 {
		var n int64
		if err := db.Raw("SELECT COUNT(1) FROM " + table).Scan(&n).Error; err != nil {
			t.Fatalf("count %s: %v", table, err)
		}
		return n
	}
	expect := map[string]int64{
		"rooms":           1, // r2 removed
		"messages":        2, // r999 message removed, valid rows kept
		"feedback_logs":   1, // ghost feedback removed
		"trip_plans":      1, // ghost trip removed
		"budget_plans":    1,
		"expense_records": 1,
		"travel_orders":   1,
	}
	for table, want := range expect {
		if got := count(table); got != want {
			t.Fatalf("%s: expected %d rows after cleanup, got %d", table, want, got)
		}
	}

	// The migration must end with a clean integrity check.
	if err := verifyForeignKeys(db); err != nil {
		t.Fatalf("foreign_key_check: %v", err)
	}

	// Enforcement is real: a new orphan is refused.
	if err := db.Exec(`INSERT INTO messages (room_id, user_id, content) VALUES ('missing', 'u1', 'should fail')`).Error; err == nil {
		t.Fatal("foreign key enforcement must reject a message whose room does not exist")
	}

	// Declared delete policy: deleting the room cascades to its messages.
	if err := db.Exec(`DELETE FROM rooms WHERE id = 'r1'`).Error; err != nil {
		t.Fatalf("delete room: %v", err)
	}
	if got := count("messages"); got != 0 {
		t.Fatalf("ON DELETE CASCADE must remove the room's messages, %d remain", got)
	}
}
