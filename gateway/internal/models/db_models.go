package models

import (
	"time"
)

// User 用户表
type User struct {
	ID           string `gorm:"primaryKey"` // 使用 UUID 作为用户ID
	Username     string `gorm:"not null"`
	PasswordHash string // bcrypt 哈希，空值表示尚未通过注册设置密码
	Nickname     string // 展示昵称，注册时可选
	AvatarSeed   string // 头像种子，用于生成唯一卡通头像
	AvatarURL    string // 用户自定义上传头像的相对路径（空则回退到 AvatarSeed 生成）
	Signature    string // 个性签名（个人主页展示）
	CreatedAt    time.Time
}

// TripPlan 用户行程规划表（用于个人主页展示历史路书与画像）
type TripPlan struct {
	ID        string `gorm:"primaryKey"` // UUID
	UserID    string `gorm:"index"`      // 归属用户
	Title     string // 例如 "洛阳 3 日游"
	DestCity  string `gorm:"index"` // 目的地城市
	Content   string // 规划的完整 JSON 内容（路线节点等）
	CreatedAt time.Time
}

// CommunityPost 社区分享帖（用户发布满意的行程或智能体规划）
type CommunityPost struct {
	ID        string `gorm:"primaryKey"` // UUID
	UserID    string `gorm:"index"`      // 发帖用户
	Title     string
	Content   string // 行程 JSON 或文字描述
	DestCity  string `gorm:"index"`
	Likes     int    `gorm:"default:0"`
	CreatedAt time.Time
}

// Comment 社区帖子的评论与回复
type Comment struct {
	ID        string `gorm:"primaryKey"` // UUID
	PostID    string `gorm:"index"`      // 所属帖子
	UserID    string `gorm:"index"`      // 评论用户
	Content   string
	ParentID  string // 为空表示直接评论帖子，非空表示回复某条评论
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
	UserID    string `gorm:"index"` // 谁提出的反馈（加索引，支撑个性化画像聚合 <100ms）
	Target    string // 针对哪个地点或标签 (例如: "广州塔", 或 "步行接驳")
	Score     int    // 1 表示正反馈(喜欢), -1 表示负反馈(踩/不喜欢)
	Reason    string // 隐性原因 (例如: "人太多", "门票太贵", "太累了")
	CreatedAt time.Time
}

// UserPreference 用户旅行画像（结构化偏好，个性化推荐引擎的核心数据源）
// 采用「反馈样本聚合 + 维度权重」结构，查询为单行主键命中，保证响应 <100ms
type UserPreference struct {
	UserID string `gorm:"primaryKey"` // 归属用户
	// 偏好标签（JSON 字符串数组，避免 SQLite 复杂关联）
	LikedTags    string
	DislikedTags string
	CuisinePrefs string
	// 消费倾向 low / mid / high
	BudgetTendency string
	// 三大维度权重（0~1，合计为 1）
	NatureRatio  float64
	CultureRatio float64
	FoodRatio    float64
	// A/B 实验分组（control / treatment），用于持续优化推荐效果
	ABVariant string
	// 累计有效反馈样本数（用于信心度判断）
	FeedbackCount int
	UpdatedAt     time.Time
}

// NodeAnnotation 行程节点级批注（多人实时协作评论）
type NodeAnnotation struct {
	ID        string `gorm:"primaryKey"` // UUID
	RoomID    string `gorm:"index"`      // 所属房间
	TripID    string `gorm:"index"`      // 所属行程（可为空表示未关联具体路书）
	NodeKey   string `gorm:"index"`      // 定位到具体行程节点（地点名或节点ID）
	UserID    string `gorm:"index"`      // 批注发起人
	Content   string // 批注内容
	ParentID  string // 为空表示直接批注，非空表示回复某条批注
	Status    string `gorm:"default:'open'"` // open / resolved（批注状态，便于版本管理）
	CreatedAt time.Time
}

// RecommendationEvent 推荐事件埋点（A/B 实验效果评估与准确率验证的数据源）
type RecommendationEvent struct {
	ID        uint   `gorm:"primaryKey;autoIncrement"`
	UserID    string `gorm:"index"`
	Variant   string `gorm:"index"` // control / treatment
	Target    string // 被推荐的地点或标签
	Clicked   bool   // 是否点击（用于计算个性化推荐点击率）
	Source    string // 注入环节：planning / poi_recommend
	CreatedAt time.Time
}

// NodeAnnotationVote 批注投票（支持团队成员对具体行程节点即时反馈）
type NodeAnnotationVote struct {
	ID           uint   `gorm:"primaryKey;autoIncrement"`
	AnnotationID string `gorm:"index"`
	UserID       string `gorm:"index"`
	Value        int    // +1 赞成，-1 反对
	CreatedAt    time.Time
}

// BudgetPlan 行程预算设置（智能预算管家）
type BudgetPlan struct {
	ID           string `gorm:"primaryKey"` // UUID
	UserID       string `gorm:"index"`      // 归属用户
	TripID       string `gorm:"index"`      // 关联行程
	TotalBudget  float64 // 总预算金额
	Currency     string `gorm:"default:'CNY'"` // 币种
	CreatedAt    time.Time
}

// ExpenseRecord 实际消费记录（预算管家与行程复盘数据源）
type ExpenseRecord struct {
	ID        string `gorm:"primaryKey"` // UUID
	UserID    string `gorm:"index"`      // 归属用户
	TripID    string `gorm:"index"`      // 关联行程
	Category  string // 分类：餐饮/住宿/交通/门票/购物/其他
	Amount    float64 // 金额（原始币种）
	Currency  string `gorm:"default:'CNY'"` // 币种
	Note      string // 备注
	CreatedAt time.Time
}
