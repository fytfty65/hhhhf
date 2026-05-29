package models

import (
	"time"
)

// User 用户表
type User struct {
	ID        string `gorm:"primaryKey"` // 使用 UUID 作为用户ID
	Username  string `gorm:"not null"`
	CreatedAt time.Time
}

// Room 房间表
type Room struct {
	ID         string `gorm:"primaryKey"`  // UUID
	InviteCode string `gorm:"uniqueIndex"` // 6位邀请码 (如 X8B2Y9)
	Name       string
	CreatorID  string
	Status     string `gorm:"default:'planning'"`
	CreatedAt  time.Time
}

// RoomMember 房间成员表 (多对多)
type RoomMember struct {
	RoomID string `gorm:"primaryKey"`
	UserID string `gorm:"primaryKey"`
	Role   string `gorm:"default:'member'"` // 司机、财务、吃货等
}

// Message 聊天与系统推演记录 (大模型的长记忆来源)
type Message struct {
	ID        uint   `gorm:"primaryKey;autoIncrement"`
	RoomID    string `gorm:"index"`
	UserID    string // 传 "0" 或 "system" 代表是 AI 发送
	RoleType  string // "human" 或 "ai"
	Content   string
	CreatedAt time.Time
}

// FeedbackLog 用户真实交互反馈表 (系统进化的负样本/正样本来源)
type FeedbackLog struct {
	ID        uint   `gorm:"primaryKey;autoIncrement"`
	RoomID    string `gorm:"index"` // 绑定群聊上下文
	UserID    string // 谁提出的反馈
	Target    string // 针对哪个地点或标签 (例如: "广州塔", 或 "步行接驳")
	Score     int    // 1 表示正反馈(喜欢), -1 表示负反馈(踩/不喜欢)
	Reason    string // 隐性原因 (例如: "人太多", "门票太贵", "太累了")
	CreatedAt time.Time
}
