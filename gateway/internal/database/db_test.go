package database

import (
	"os"
	"path/filepath"
	"testing"
)

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
