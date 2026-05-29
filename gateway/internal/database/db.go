package database

import (
	"log"

	"gateway/internal/models"

	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

var DB *gorm.DB

func InitDB() {
	var err error
	// 启动时在根目录生成 omniroute.db 文件，并开启静默日志避免刷屏
	DB, err = gorm.Open(sqlite.Open("omniroute.db"), &gorm.Config{
		Logger: logger.Default.LogMode(logger.Silent),
	})
	if err != nil {
		log.Fatalf("🔥 数据库连接失败: %v", err)
	}

	// 自动同步表结构，省去了写 SQL 建表语句的麻烦
	err = DB.AutoMigrate(
		&models.User{},
		&models.Room{},
		&models.RoomMember{},
		&models.Message{},
	)
	if err != nil {
		log.Fatalf("🔥 数据库表结构同步失败: %v", err)
	}

	log.Println("✅ SQLite 数据库初始化成功！")
}
