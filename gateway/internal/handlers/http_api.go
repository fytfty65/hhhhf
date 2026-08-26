package handlers

import (
	"fmt"
	"io"
	"log"
	"math/rand"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

// 生成 6 位大写字母+数字的邀请码
func generateInviteCode() string {
	const charset = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789" // 剔除容易混淆的 0,1,I,O
	seededRand := rand.New(rand.NewSource(time.Now().UnixNano()))
	b := make([]byte, 6)
	for i := range b {
		b[i] = charset[seededRand.Intn(len(charset))]
	}
	return string(b)
}

// CreateRoomHandler 【队长专用】建群并获取邀请码
func CreateRoomHandler(c *gin.Context) {
	var req struct {
		Username string `json:"username" binding:"required"`
		RoomName string `json:"room_name" binding:"required"`
		Role     string `json:"role"` // 可选，如：队长/主理人
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数错误"})
		return
	}

	// 1. 创建用户
	userID := uuid.New().String()
	user := models.User{ID: userID, Username: req.Username}
	database.DB.Create(&user)

	// 2. 创建房间
	roomID := uuid.New().String()
	inviteCode := generateInviteCode()
	room := models.Room{
		ID:         roomID,
		InviteCode: inviteCode,
		Name:       req.RoomName,
		CreatorID:  userID,
	}
	database.DB.Create(&room)

	// 3. 将队长拉入房间成员表
	if req.Role == "" {
		req.Role = "主理人"
	}
	database.DB.Create(&models.RoomMember{RoomID: roomID, UserID: userID, Role: req.Role})

	// 返回给前端，前端拿到 ID 后就可以立刻去连 WebSocket 了
	c.JSON(http.StatusOK, gin.H{
		"message":     "房间创建成功",
		"invite_code": inviteCode,
		"room_id":     roomID,
		"user_id":     userID,
	})
}

// JoinRoomHandler 【队员专用】输入邀请码空降群聊
func JoinRoomHandler(c *gin.Context) {
	var req struct {
		Username   string `json:"username" binding:"required"`
		InviteCode string `json:"invite_code" binding:"required"`
		Role       string `json:"role"` // 可选，如：吃货/司机/摄影师
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数错误"})
		return
	}

	// 1. 验证邀请码
	var room models.Room
	if err := database.DB.Where("invite_code = ?", req.InviteCode).First(&room).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "邀请码无效或房间不存在"})
		return
	}

	// 2. 创建新队员用户
	userID := uuid.New().String()
	user := models.User{ID: userID, Username: req.Username}
	database.DB.Create(&user)

	// 3. 将队员拉入房间成员表
	if req.Role == "" {
		req.Role = "成员"
	}
	database.DB.Create(&models.RoomMember{RoomID: room.ID, UserID: userID, Role: req.Role})

	// 返回给前端连接 WS 必要的凭证
	c.JSON(http.StatusOK, gin.H{
		"message":   "成功加入房间",
		"room_name": room.Name,
		"room_id":   room.ID,
		"user_id":   userID,
	})
}

// 把这两段加在 http_api.go 文件的最底部

// InitPlanningHandler 接收前端初始化的行程偏好
func InitPlanningHandler(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{
		"status":  "success",
		"message": "行程规划初始化接口已就绪",
	})
}

// AgentSyncHandler 接收 Python AI 传回来的异步状态
func AgentSyncHandler(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{
		"status":  "success",
		"message": "智能体状态同步接口已就绪",
	})
}

// SubmitFeedbackHandler 接收用户的路线反馈，形成持续学习闭环
func SubmitFeedbackHandler(c *gin.Context) {
	var req struct {
		RoomID string `json:"room_id" binding:"required"`
		UserID string `json:"user_id" binding:"required"`
		Target string `json:"target" binding:"required"`
		Score  int    `json:"score" binding:"required"` // 1 或 -1
		Reason string `json:"reason"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "反馈参数不完整"})
		return
	}

	// 将真实反馈刻录进大数据库，形成系统记忆
	feedback := models.FeedbackLog{
		RoomID: req.RoomID,
		UserID: req.UserID,
		Target: req.Target,
		Score:  req.Score,
		Reason: req.Reason,
	}
	database.DB.Create(&feedback)

	// 🎯 实时采集 → 画像动态更新：提交反馈后立即重聚合用户旅行画像
	if profile, err := service.RefreshProfile(req.UserID); err == nil {
		log.Println("✅ 用户画像已实时更新:", profile.BudgetTendency, profile.LikedTags)
	}

	c.JSON(http.StatusOK, gin.H{
		"message": "反馈已收入进化知识库，下次推演将自动修正",
		"status":  "success",
	})
}

// ==========================================
// 个人主页相关接口：资料 / 历史行程 / 头像
// ==========================================

// GetProfileHandler 获取用户资料与历史行程列表（个人主页数据源）
func GetProfileHandler(c *gin.Context) {
	userID := c.Query("user_id")
	if userID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少 user_id"})
		return
	}

	var user models.User
	if err := database.DB.Where("id = ?", userID).First(&user).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "用户不存在"})
		return
	}

	var trips []models.TripPlan
	database.DB.Where("user_id = ?", userID).Order("created_at desc").Find(&trips)

	c.JSON(http.StatusOK, gin.H{
		"user": gin.H{
			"id":         user.ID,
			"username":   user.Username,
			"nickname":   user.Nickname,
			"avatarSeed": user.AvatarSeed,
			"avatarUrl":  user.AvatarURL,
			"signature":  user.Signature,
			"createdAt":  user.CreatedAt,
		},
		"trips": trips,
	})
}

// SaveTripHandler 保存一次行程规划（个人主页历史路书）
func SaveTripHandler(c *gin.Context) {
	var req struct {
		UserID   string `json:"user_id" binding:"required"`
		Title    string `json:"title"`
		DestCity string `json:"dest_city"`
		Content  string `json:"content"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}

	trip := models.TripPlan{
		ID:       uuid.New().String(),
		UserID:   req.UserID,
		Title:    req.Title,
		DestCity: req.DestCity,
		Content:  req.Content,
	}
	if err := database.DB.Create(&trip).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "行程保存失败"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "行程已保存", "trip_id": trip.ID})
}

// UpdateAvatarHandler 更新头像种子（初期用 dicebear 种子，后续可替换为图片 URL / 上传）
func UpdateAvatarHandler(c *gin.Context) {
	var req struct {
		UserID     string `json:"user_id" binding:"required"`
		AvatarSeed string `json:"avatar_seed"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}

	if err := database.DB.Model(&models.User{}).Where("id = ?", req.UserID).Update("avatar_seed", req.AvatarSeed).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "头像更新失败"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "头像已更新", "avatar_seed": req.AvatarSeed})
}

// UploadAvatarHandler 上传自定义头像（multipart/form-data）
// 支持 JPG/PNG，大小不超过 5MB；保存到 uploads/avatars 并静态托管
func UploadAvatarHandler(c *gin.Context) {
	userID := c.PostForm("user_id")
	if userID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少 user_id"})
		return
	}

	file, header, err := c.Request.FormFile("file")
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "未接收到图片文件"})
		return
	}
	defer file.Close()

	// 大小校验：不超过 5MB
	const maxSize = 5 * 1024 * 1024
	if header.Size > maxSize {
		c.JSON(http.StatusBadRequest, gin.H{"error": "图片大小不能超过 5MB"})
		return
	}

	// 读取文件头校验真实格式（防止仅依赖扩展名伪造）
	buff := make([]byte, 512)
	n, _ := file.Read(buff)
	contentType := http.DetectContentType(buff[:n])
	if contentType != "image/jpeg" && contentType != "image/png" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "仅支持 JPG / PNG 格式图片"})
		return
	}
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "读取图片失败"})
		return
	}

	ext := ".jpg"
	if contentType == "image/png" {
		ext = ".png"
	}

	// 确保上传目录存在
	dir := filepath.Join("uploads", "avatars")
	if err := os.MkdirAll(dir, os.ModePerm); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "头像存储目录初始化失败"})
		return
	}

	filename := fmt.Sprintf("%s_%d%s", userID, time.Now().UnixNano(), ext)
	fullPath := filepath.Join(dir, filename)

	out, err := os.Create(fullPath)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "头像保存失败"})
		return
	}
	defer out.Close()
	if _, err := io.Copy(out, file); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "头像写入失败"})
		return
	}

	avatarURL := "/uploads/avatars/" + filename
	if err := database.DB.Model(&models.User{}).Where("id = ?", userID).Update("avatar_url", avatarURL).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "头像信息更新失败"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "头像上传成功", "avatar_url": avatarURL})
}

// UpdateProfileHandler 编辑个人资料（昵称 + 个性签名）
func UpdateProfileHandler(c *gin.Context) {
	var req struct {
		UserID    string `json:"user_id" binding:"required"`
		Nickname  string `json:"nickname"`
		Signature string `json:"signature"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}

	nickname := strings.TrimSpace(req.Nickname)
	if nickname == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "昵称不能为空"})
		return
	}

	updates := map[string]interface{}{"nickname": nickname, "signature": strings.TrimSpace(req.Signature)}
	if err := database.DB.Model(&models.User{}).Where("id = ?", req.UserID).Updates(updates).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "资料更新失败"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "资料已更新", "nickname": nickname, "signature": strings.TrimSpace(req.Signature)})
}

// ==========================================
// 社区接口：发布 / 列表 / 评论 / 点赞
// ==========================================

// userBrief 返回用户昵称、头像种子与自定义头像 URL，缺失时安全兜底
func userBrief(userID string) (string, string, string) {
	var u models.User
	if err := database.DB.Where("id = ?", userID).First(&u).Error; err != nil {
		return "旅行者", userID, ""
	}
	nickname := u.Nickname
	if nickname == "" {
		nickname = u.Username
	}
	avatar := u.AvatarSeed
	if avatar == "" {
		avatar = u.ID
	}
	return nickname, avatar, u.AvatarURL
}

// ListPostsHandler 社区帖子列表（含作者与评论数，按热度排序）
func ListPostsHandler(c *gin.Context) {
	var posts []models.CommunityPost
	database.DB.Order("likes desc, created_at desc").Limit(200).Find(&posts)

	result := make([]gin.H, 0, len(posts))
	for _, p := range posts {
		author, avatar, avatarURL := userBrief(p.UserID)
		var commentCount int64
		database.DB.Model(&models.Comment{}).Where("post_id = ?", p.ID).Count(&commentCount)
		result = append(result, gin.H{
			"id":           p.ID,
			"user_id":      p.UserID,
			"author":       author,
			"author_avatar": avatar,
			"author_avatar_url": avatarURL,
			"title":        p.Title,
			"content":      p.Content,
			"dest_city":    p.DestCity,
			"likes":        p.Likes,
			"comments":     commentCount,
			"created_at":   p.CreatedAt,
		})
	}
	c.JSON(http.StatusOK, gin.H{"posts": result})
}

// PublishPostHandler 发布一篇社区帖（用户分享行程/规划）
func PublishPostHandler(c *gin.Context) {
	var req struct {
		UserID   string `json:"user_id" binding:"required"`
		Title    string `json:"title"`
		Content  string `json:"content"`
		DestCity string `json:"dest_city"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	if req.Title == "" && req.Content == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "标题或内容不能为空"})
		return
	}

	post := models.CommunityPost{
		ID:       uuid.New().String(),
		UserID:   req.UserID,
		Title:    req.Title,
		Content:  req.Content,
		DestCity: req.DestCity,
	}
	if err := database.DB.Create(&post).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "发布失败"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "发布成功", "post_id": post.ID})
}

// GetCommentsHandler 获取某帖子下的评论（含回复，按时间正序）
func GetCommentsHandler(c *gin.Context) {
	postID := c.Query("post_id")
	if postID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少 post_id"})
		return
	}

	var comments []models.Comment
	database.DB.Where("post_id = ?", postID).Order("created_at asc").Find(&comments)

	result := make([]gin.H, 0, len(comments))
	for _, cm := range comments {
		author, avatar, avatarURL := userBrief(cm.UserID)
		result = append(result, gin.H{
			"id":         cm.ID,
			"post_id":    cm.PostID,
			"user_id":    cm.UserID,
			"author":     author,
			"author_avatar": avatar,
			"author_avatar_url": avatarURL,
			"content":    cm.Content,
			"parent_id":  cm.ParentID,
			"created_at": cm.CreatedAt,
		})
	}
	c.JSON(http.StatusOK, gin.H{"comments": result})
}

// CommentHandler 评论帖子或回复某条评论
func CommentHandler(c *gin.Context) {
	var req struct {
		PostID   string `json:"post_id" binding:"required"`
		UserID   string `json:"user_id" binding:"required"`
		Content  string `json:"content" binding:"required"`
		ParentID string `json:"parent_id"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}

	var post models.CommunityPost
	if err := database.DB.Where("id = ?", req.PostID).First(&post).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "帖子不存在"})
		return
	}

	comment := models.Comment{
		ID:       uuid.New().String(),
		PostID:   req.PostID,
		UserID:   req.UserID,
		Content:  req.Content,
		ParentID: req.ParentID,
	}
	if err := database.DB.Create(&comment).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "评论失败"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "评论成功", "comment_id": comment.ID})
}

// LikePostHandler 给帖子点赞
func LikePostHandler(c *gin.Context) {
	var req struct {
		PostID string `json:"post_id" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}

	var post models.CommunityPost
	if err := database.DB.Where("id = ?", req.PostID).First(&post).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "帖子不存在"})
		return
	}
	post.Likes = post.Likes + 1
	if err := database.DB.Save(&post).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "点赞失败"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "点赞成功", "likes": post.Likes})
}
