package database

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// initTestDB points DB_PATH at a temp file and initializes the global DB,
// returning the underlying *sql.DB with cleanup registered.
func initTestDB(t *testing.T) *sql.DB {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("DB_PATH", filepath.Join(dir, "omniroute.db"))
	if err := InitDB(); err != nil {
		t.Fatalf("InitDB: %v", err)
	}
	sqlDB, err := DB.DB()
	if err != nil {
		t.Fatalf("DB.DB(): %v", err)
	}
	t.Cleanup(func() { _ = sqlDB.Close() })
	return sqlDB
}

// TestInitDBConfiguresSQLitePool pins the connection-pool settings.
//
// Why this matters: SQLite permits exactly one writer. With database/sql's
// defaults (unlimited open connections, 2 idle) concurrent writers race for the
// write lock and surface as SQLITE_BUSY / 500s under load rather than queueing
// politely, and the open file-descriptor count scales with concurrency. Capping
// the pool at one connection serialises access in Go, where a bounded wait is
// preferable to an error the caller has to interpret.
func TestInitDBConfiguresSQLitePool(t *testing.T) {
	sqlDB := initTestDB(t)

	if got := sqlDB.Stats().MaxOpenConnections; got != 1 {
		t.Fatalf("MaxOpenConnections must be 1 for SQLite (one writer), got %d", got)
	}
	if got := sqlDB.Stats().OpenConnections; got > 1 {
		t.Fatalf("at most one connection may be open, got %d", got)
	}
}

// TestBusyTimeoutAppliesToEveryPooledConnection guards a subtle regression: a
// `PRAGMA busy_timeout` executed once through GORM only configures whichever
// pooled connection happened to serve it, because the setting is per-connection.
// Every other connection would keep the default, so a concurrent write could fail
// immediately instead of waiting. It must therefore be applied by the DSN (or a
// connection hook) so it holds for every connection, not just the first.
//
// The loop forces genuine connection churn: idle connections are dropped and a
// fresh one is opened for each measurement.
func TestBusyTimeoutAppliesToEveryPooledConnection(t *testing.T) {
	sqlDB := initTestDB(t)

	for attempt := 0; attempt < 3; attempt++ {
		// Force the next query onto a brand-new connection.
		sqlDB.SetMaxIdleConns(0)
		var timeout int
		if err := sqlDB.QueryRow("PRAGMA busy_timeout").Scan(&timeout); err != nil {
			t.Fatalf("read busy_timeout (attempt %d): %v", attempt, err)
		}
		if timeout < sqliteBusyTimeoutMs {
			t.Fatalf("busy_timeout must be >= %d on every connection, got %d (attempt %d)",
				sqliteBusyTimeoutMs, timeout, attempt)
		}
	}
}

// TestSQLiteDSNAppliesPerConnectionPragmas covers the helper directly, including
// the rule that a caller-supplied value is not overwritten.
func TestSQLiteDSNAppliesPerConnectionPragmas(t *testing.T) {
	got := sqliteDSN("omniroute.db")
	if !strings.Contains(got, "_busy_timeout=") {
		t.Fatalf("DSN must set _busy_timeout, got %q", got)
	}
	if !strings.Contains(got, "_journal_mode=WAL") {
		t.Fatalf("DSN must set _journal_mode=WAL, got %q", got)
	}
	if !strings.HasPrefix(got, "omniroute.db?") {
		t.Fatalf("a plain path must gain a query string, got %q", got)
	}

	// A caller-supplied value wins: appending a second one would make the
	// effective timeout depend on driver parsing order.
	explicit := sqliteDSN("omniroute.db?_busy_timeout=1234")
	if strings.Count(explicit, "_busy_timeout=") != 1 {
		t.Fatalf("an explicit _busy_timeout must not be duplicated, got %q", explicit)
	}
	if !strings.Contains(explicit, "_busy_timeout=1234") {
		t.Fatalf("the caller's value must survive, got %q", explicit)
	}

	// Paths that already carry parameters must be extended with '&', not '?'.
	withParam := sqliteDSN("omniroute.db?cache=shared")
	if !strings.Contains(withParam, "cache=shared&_busy_timeout=") {
		t.Fatalf("parameters must be appended with '&', got %q", withParam)
	}
}

func TestIsMemoryDSN(t *testing.T) {
	for _, dsn := range []string{":memory:", "file::memory:?cache=shared", "file:x?mode=memory"} {
		if !isMemoryDSN(dsn) {
			t.Fatalf("%q must be detected as in-memory", dsn)
		}
	}
	for _, dsn := range []string{"omniroute.db", "/tmp/x.db", `C:\data\omni.db`} {
		if isMemoryDSN(dsn) {
			t.Fatalf("%q must not be detected as in-memory", dsn)
		}
	}
}

// TestJournalModeIsWAL keeps the durability/concurrency mode that the retention
// and backup paths were written against.
func TestJournalModeIsWAL(t *testing.T) {
	sqlDB := initTestDB(t)
	var mode string
	if err := sqlDB.QueryRow("PRAGMA journal_mode").Scan(&mode); err != nil {
		t.Fatalf("read journal_mode: %v", err)
	}
	if mode != "memory" && mode != "wal" {
		t.Fatalf("expected WAL journal mode, got %q", mode)
	}
}

// TestForeignKeysPragmaIsEnabled pins the post-migration state: every gateway
// connection must enforce the constraints declared by v20.
func TestForeignKeysPragmaIsEnabled(t *testing.T) {
	sqlDB := initTestDB(t)
	var fk int
	if err := sqlDB.QueryRow("PRAGMA foreign_keys").Scan(&fk); err != nil {
		t.Fatalf("read foreign_keys: %v", err)
	}
	if fk != 1 {
		t.Fatalf("foreign_keys must be ON after v20, got %d", fk)
	}
	enabled, err := ForeignKeysEnabled()
	if err != nil {
		t.Fatalf("ForeignKeysEnabled: %v", err)
	}
	if !enabled {
		t.Fatal("ForeignKeysEnabled reported OFF")
	}
}

func TestInitDBAndBackupRestore(t *testing.T) {
	dir := t.TempDir()
	old := os.Getenv("DB_PATH")
	defer os.Setenv("DB_PATH", old)
	os.Setenv("DB_PATH", filepath.Join(dir, "omniroute.db"))
	if err := InitDB(); err != nil {
		t.Fatal(err)
	}
	if !DB.Migrator().HasTable("planning_events") || !DB.Migrator().HasTable("model_deployments") {
		t.Fatal("schema v4 tables were not migrated")
	}
	if sqlDB, err := DB.DB(); err == nil {
		defer sqlDB.Close()
	}
	if _, err := os.Stat(filepath.Join(dir, "omniroute.db")); err != nil {
		t.Fatal(err)
	}
	backup, err := Backup(filepath.Join(dir, "backups"))
	if err != nil {
		t.Fatal(err)
	}
	if err := RestoreBackup(backup); err != nil {
		t.Fatal(err)
	}
}

func TestRestoreRejectsInvalidBackup(t *testing.T) {
	p := filepath.Join(t.TempDir(), "bad.db")
	if err := os.WriteFile(p, []byte("not sqlite"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := RestoreBackup(p); err == nil {
		t.Fatal("expected invalid backup error")
	}
}
