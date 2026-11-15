package handlers

import (
	"testing"

	"gateway/internal/database"
	"gateway/internal/models"

	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

// 昵称映射：昵称优先、没有昵称退化成用户名、查不到的 ID 不编造名字。
func TestLookupNicknamesMapsActorIDs(t *testing.T) {
	oldDB := database.DB
	defer func() { database.DB = oldDB }()

	db, err := gorm.Open(sqlite.Open("file:photo-nickname-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if err := db.AutoMigrate(&models.User{}); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	database.DB = db

	if err := db.Create(&models.User{ID: "u1", Username: "alice", Nickname: "小艾"}).Error; err != nil {
		t.Fatalf("seed user: %v", err)
	}
	if err := db.Create(&models.User{ID: "u2", Username: "bob"}).Error; err != nil {
		t.Fatalf("seed user: %v", err)
	}

	names := lookupNicknames([]string{"u1", "u2", "u1", "ghost", ""})
	if names["u1"] != "小艾" {
		t.Fatalf("应优先用昵称，得到 %q", names["u1"])
	}
	if names["u2"] != "bob" {
		t.Fatalf("没有昵称时应退化为用户名，得到 %q", names["u2"])
	}
	if _, ok := names["ghost"]; ok {
		t.Fatalf("已删除账号不应编造展示名: %+v", names)
	}
	if _, ok := names[""]; ok {
		t.Fatalf("空 ID 不应进入映射: %+v", names)
	}
}
