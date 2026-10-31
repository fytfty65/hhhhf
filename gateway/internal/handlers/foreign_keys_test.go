package handlers

import (
	"path/filepath"
	"testing"

	"gateway/internal/database"
	"gateway/internal/models"
)

// Foreign-key enforcement: the post-v20 reality.
//
// v20 cleans orphan rows, rebuilds every related table with an explicit
// FOREIGN KEY clause (plus ON DELETE CASCADE/SET NULL), and turns
// `PRAGMA foreign_keys` on for every pooled connection via the DSN. These tests
// pin the three observable outcomes: enforcement is on, orphan writes are
// refused, and deleting a parent cascades to its children.

func initFKDatabase(t *testing.T) {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("DB_PATH", filepath.Join(dir, "fk.db"))
	t.Setenv("SKIP_MIGRATION_BACKUP", "true")
	if err := database.InitDB(); err != nil {
		t.Fatalf("InitDB: %v", err)
	}
	t.Cleanup(func() {
		if sqlDB, err := database.DB.DB(); err == nil && sqlDB != nil {
			_ = sqlDB.Close()
		}
	})
}

func TestForeignKeyEnforcementIsOn(t *testing.T) {
	initFKDatabase(t)
	enabled, err := database.ForeignKeysEnabled()
	if err != nil {
		t.Fatalf("ForeignKeysEnabled: %v", err)
	}
	if !enabled {
		t.Fatal("foreign_keys must be ON for every gateway connection")
	}
	violations, err := database.ForeignKeyReport()
	if err != nil {
		t.Fatalf("ForeignKeyReport: %v", err)
	}
	if violations != 0 {
		t.Fatalf("fresh database must have zero FK violations, got %d", violations)
	}
}

func TestOrphanRowsAreRejected(t *testing.T) {
	initFKDatabase(t)

	// A message must belong to a room.
	orphan := models.Message{RoomID: "no-such-room", UserID: "u", Content: "orphan"}
	if err := database.DB.Create(&orphan).Error; err == nil {
		t.Fatal("an orphan Message must be refused once the FK is declared and enforced")
	}

	// A room must belong to a real user.
	orphanRoom := models.Room{ID: "room-orphan", InviteCode: "ORPHAN", CreatorID: "no-such-user"}
	if err := database.DB.Create(&orphanRoom).Error; err == nil {
		t.Fatal("a Room with an unknown creator must be refused")
	}
}

func TestDeletingParentCascadesChildren(t *testing.T) {
	initFKDatabase(t)

	user := models.User{ID: "u-cascade", Username: "cascade-user"}
	if err := database.DB.Create(&user).Error; err != nil {
		t.Fatalf("create user: %v", err)
	}
	room := models.Room{ID: "r-cascade", InviteCode: "CASCADE", CreatorID: user.ID}
	if err := database.DB.Create(&room).Error; err != nil {
		t.Fatalf("create room: %v", err)
	}
	member := models.RoomMember{RoomID: room.ID, UserID: user.ID, Role: "owner"}
	if err := database.DB.Create(&member).Error; err != nil {
		t.Fatalf("create member: %v", err)
	}
	message := models.Message{RoomID: room.ID, UserID: user.ID, Content: "hello"}
	if err := database.DB.Create(&message).Error; err != nil {
		t.Fatalf("create message: %v", err)
	}
	trip := models.TripPlan{ID: "t-cascade", UserID: user.ID, Title: "test"}
	if err := database.DB.Create(&trip).Error; err != nil {
		t.Fatalf("create trip: %v", err)
	}

	if err := database.DB.Delete(&room).Error; err != nil {
		t.Fatalf("delete room: %v", err)
	}
	var members, messages int64
	database.DB.Model(&models.RoomMember{}).Where("room_id = ?", room.ID).Count(&members)
	database.DB.Model(&models.Message{}).Where("room_id = ?", room.ID).Count(&messages)
	if members != 0 || messages != 0 {
		t.Fatalf("deleting a room must cascade to members/messages, got members=%d messages=%d", members, messages)
	}

	// Deleting the owner removes their trips and the remaining room.
	if err := database.DB.Delete(&user).Error; err != nil {
		t.Fatalf("delete user: %v", err)
	}
	var trips, rooms int64
	database.DB.Model(&models.TripPlan{}).Where("user_id = ?", user.ID).Count(&trips)
	database.DB.Model(&models.Room{}).Where("creator_id = ?", user.ID).Count(&rooms)
	if trips != 0 || rooms != 0 {
		t.Fatalf("deleting a user must cascade to trips/rooms, got trips=%d rooms=%d", trips, rooms)
	}
}

func TestForeignKeyCoverageIsDeclared(t *testing.T) {
	initFKDatabase(t)

	var ddlRows []struct {
		Name string
		SQL  string
	}
	if err := database.DB.Raw("SELECT name, sql FROM sqlite_master WHERE type='table'").Scan(&ddlRows).Error; err != nil {
		t.Fatalf("read schema: %v", err)
	}
	declared, total := 0, 0
	for _, row := range ddlRows {
		total++
		if containsForeignKeyClause(row.SQL) {
			declared++
		}
	}
	// v20 declares constraints on 20 tables (users are referenced, not rebuilt).
	if declared < 15 {
		t.Fatalf("expected at least 15 tables to declare FOREIGN KEY, got %d of %d", declared, total)
	}
	t.Logf("FK coverage: %d of %d tables declare a FOREIGN KEY", declared, total)
}

func containsForeignKeyClause(ddl string) bool {
	const needle = "FOREIGN KEY"
	return len(ddl) >= len(needle) && (func() bool {
		for i := 0; i+len(needle) <= len(ddl); i++ {
			if ddl[i:i+len(needle)] == needle {
				return true
			}
		}
		return false
	})()
}
