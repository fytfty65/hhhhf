package handlers

import (
	cryptoRand "crypto/rand"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math/big"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
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
	b := make([]byte, 6)
	for i := range b {
		idx, err := cryptoRand.Int(cryptoRand.Reader, big.NewInt(int64(len(charset))))
		if err != nil {
			// crypto/rand failure is exceptional; use a UUID-derived byte rather
			// than silently falling back to a timestamp-seeded PRNG.
			id := uuid.New()
			b[i] = charset[int(id[i])%len(charset)]
			continue
		}
		b[i] = charset[idx.Int64()]
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

	userID := CurrentUserID(c)
	if userID == "" {
		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "未登录", "code": "AUTH_REQUIRED"})
		return
	}
	if len([]rune(strings.TrimSpace(req.RoomName))) > 120 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "房间名称过长"})
		return
	}

	// Use the invite code as the stable room key so the existing frontend
	// contract can share one opaque room code across HTTP and WebSocket flows.
	// A short retry loop handles the (rare) random-code collision without
	// exposing a partially-created room.
	var room models.Room
	var inviteCode string
	var createErr error
	for attempt := 0; attempt < 3; attempt++ {
		inviteCode = generateInviteCode()
		room = models.Room{ID: inviteCode, InviteCode: inviteCode, Name: strings.TrimSpace(req.RoomName), CreatorID: userID}
		createErr = database.DB.Create(&room).Error
		if createErr == nil {
			break
		}
	}
	if createErr != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "房间创建失败"})
		return
	}

	// 将队长拉入房间成员表
	if req.Role == "" {
		req.Role = "主理人"
	}
	req.Role = truncateRunes(strings.TrimSpace(req.Role), 40)
	if err := database.DB.Create(&models.RoomMember{RoomID: room.ID, UserID: userID, Role: req.Role}).Error; err != nil {
		// Keep room/member creation atomic from the caller's perspective.
		database.DB.Delete(&room)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "房间成员初始化失败"})
		return
	}

	// 返回给前端，前端拿到 ID 后就可以立刻去连 WebSocket 了
	c.JSON(http.StatusOK, gin.H{
		"message":     "房间创建成功",
		"invite_code": inviteCode,
		"room_id":     room.ID,
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
	req.InviteCode = strings.ToUpper(strings.TrimSpace(req.InviteCode))
	if len(req.InviteCode) != 6 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "邀请码格式不正确"})
		return
	}

	// 1. 验证邀请码
	var room models.Room
	if err := database.DB.Where("invite_code = ?", req.InviteCode).First(&room).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "邀请码无效或房间不存在"})
		return
	}

	userID := CurrentUserID(c)
	if userID == "" {
		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "未登录", "code": "AUTH_REQUIRED"})
		return
	}

	// 2. 将当前登录用户加入房间成员表（重复加入保持幂等）
	if req.Role == "" {
		req.Role = "成员"
	}
	req.Role = truncateRunes(strings.TrimSpace(req.Role), 40)
	var member models.RoomMember
	if err := database.DB.Where("room_id = ? AND user_id = ?", room.ID, userID).First(&member).Error; err == nil {
		if member.Role != req.Role {
			database.DB.Model(&member).Update("role", req.Role)
		}
	} else if err := database.DB.Create(&models.RoomMember{RoomID: room.ID, UserID: userID, Role: req.Role}).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "加入房间失败"})
		return
	}

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
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if _, ok := requireRoomMember(c, req.RoomID); !ok {
		return
	}
	req.UserID = userID
	if req.Score != 1 && req.Score != -1 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "反馈分值仅支持 1 / -1"})
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
	userID, ok := currentUserIDOrReject(c)
	if !ok {
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

// GetTripsHandler is the stable profile-history contract used by the frontend.
func GetTripsHandler(c *gin.Context) {
	userID := CurrentUserID(c)
	if userID == "" {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "未登录", "code": "AUTH_REQUIRED"})
		return
	}
	var trips []models.TripPlan
	if err := database.DB.Where("user_id = ?", userID).Order("created_at desc").Find(&trips).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "行程加载失败", "code": "TRIPS_LOAD_FAILED"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"trips": trips})
}

// SaveTripHandler 保存一次行程规划（个人主页历史路书）
func SaveTripHandler(c *gin.Context) {
	var req struct {
		UserID    string `json:"user_id" binding:"required"`
		Title     string `json:"title"`
		DestCity  string `json:"dest_city"`
		Content   string `json:"content"`
		Days      int    `json:"days"`      // 行程天数（可选，供人均日消费折算）
		Travelers int    `json:"travelers"` // 同行人数含本人（可选，供人均日消费折算）
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	req.UserID = userID
	if strings.TrimSpace(req.Title) == "" && strings.TrimSpace(req.Content) == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "行程内容不能为空"})
		return
	}
	if len([]rune(req.Title)) > 200 || len([]rune(req.DestCity)) > 100 || len([]byte(req.Content)) > 4<<20 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "行程内容过长"})
		return
	}

	trip := models.TripPlan{
		ID:        uuid.New().String(),
		UserID:    req.UserID,
		Title:     req.Title,
		DestCity:  req.DestCity,
		Content:   req.Content,
		Days:      req.Days,
		Travelers: req.Travelers,
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
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	req.UserID = userID
	if len([]rune(req.AvatarSeed)) > 128 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "头像标识过长"})
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
	userID := CurrentUserID(c)
	if userID == "" {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "未登录", "code": "AUTH_REQUIRED"})
		return
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 5<<20)

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
	if err := os.MkdirAll(dir, 0o750); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "头像存储目录初始化失败"})
		return
	}

	filename := uuid.NewString() + ext
	baseDir, err := filepath.Abs(dir)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "头像存储路径初始化失败"})
		return
	}
	fullPath := filepath.Join(baseDir, filename)
	resolved, err := filepath.Abs(fullPath)
	if err != nil || filepath.Dir(resolved) != baseDir {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "头像存储路径无效"})
		return
	}

	out, err := os.OpenFile(fullPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o640)
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
		_ = os.Remove(fullPath)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "头像信息更新失败"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "头像上传成功", "avatar_url": avatarURL})
}

// UpdateProfileHandler 编辑个人资料（昵称 + 个性签名）
func UpdateProfileHandler(c *gin.Context) {
	// user_id is NOT part of the request contract: it is derived from the
	// session below. It used to be declared `binding:"required"`, so every
	// client that did not echo the field back was rejected with 400 and an
	// empty body — the endpoint was unreachable for a normal caller.
	var req struct {
		UserID    string `json:"user_id"`
		Nickname  string `json:"nickname"`
		Signature string `json:"signature"`
		AvatarURL string `json:"avatar_url"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整", "code": "INVALID_PROFILE_REQUEST"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	// AuthMiddleware already rejects a mismatched body user_id; re-check here so
	// the handler is safe even if it is ever mounted without that middleware.
	if supplied := strings.TrimSpace(req.UserID); supplied != "" && supplied != userID {
		c.JSON(http.StatusForbidden, gin.H{"error": "请求身份与登录用户不一致", "code": "USER_MISMATCH"})
		return
	}
	req.UserID = userID
	if len([]rune(req.Nickname)) > 80 || len([]rune(req.Signature)) > 300 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "资料字段过长", "code": "PROFILE_FIELD_TOO_LONG"})
		return
	}
	if strings.TrimSpace(req.AvatarURL) != "" && !strings.HasPrefix(strings.TrimSpace(req.AvatarURL), "/uploads/avatars/") {
		c.JSON(http.StatusBadRequest, gin.H{"error": "头像地址格式不受支持", "code": "AVATAR_URL_UNSUPPORTED"})
		return
	}

	updates := map[string]interface{}{}
	nickname := strings.TrimSpace(req.Nickname)
	if nickname != "" {
		updates["nickname"] = nickname
	}
	if req.Signature != "" || nickname != "" {
		updates["signature"] = strings.TrimSpace(req.Signature)
	}
	if strings.TrimSpace(req.AvatarURL) != "" {
		updates["avatar_url"] = strings.TrimSpace(req.AvatarURL)
	}
	if len(updates) == 0 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "没有可更新的资料字段", "code": "NO_PROFILE_FIELDS"})
		return
	}
	if err := database.DB.Model(&models.User{}).Where("id = ?", req.UserID).Updates(updates).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "资料更新失败", "code": "PROFILE_UPDATE_FAILED"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "资料已更新", "nickname": nickname, "signature": strings.TrimSpace(req.Signature), "avatar_url": strings.TrimSpace(req.AvatarURL)})
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

// ListPostsHandler 社区帖子列表（含作者、评论数、收藏数与话题标签，按热度排序）。
// 支持 ?tag=xxx 过滤指定话题标签。
func ListPostsHandler(c *gin.Context) {
	tag := c.Query("tag")
	userID := c.Query("user_id")
	viewerID := CurrentUserID(c)

	q := database.DB.Order("created_at desc").Limit(500)
	if userID != "" {
		q = q.Where("user_id = ?", userID)
	}
	if tag != "" {
		q = q.Where("tags LIKE ?", "%\""+tag+"\"%")
	}
	var posts []models.CommunityPost
	q.Find(&posts)

	type item struct {
		post      models.CommunityPost
		heat      float64
		comments  int64
		favorites int64
		favorited bool
	}
	items := make([]item, 0, len(posts))
	now := time.Now()
	for _, p := range posts {
		var cc, fc, ownFavorite int64
		database.DB.Model(&models.Comment{}).Where("post_id = ?", p.ID).Count(&cc)
		database.DB.Model(&models.PostFavorite{}).Where("post_id = ?", p.ID).Count(&fc)
		if viewerID != "" {
			database.DB.Model(&models.PostFavorite{}).Where("post_id = ? AND user_id = ?", p.ID, viewerID).Count(&ownFavorite)
		}
		heat := service.HotScore(p.Likes, int(cc), int(fc), now.Sub(p.CreatedAt).Hours())
		items = append(items, item{post: p, heat: heat, comments: cc, favorites: fc, favorited: ownFavorite > 0})
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].heat != items[j].heat {
			return items[i].heat > items[j].heat
		}
		return items[i].post.CreatedAt.After(items[j].post.CreatedAt)
	})

	result := make([]gin.H, 0, len(items))
	for _, it := range items {
		p := it.post
		author, avatar, avatarURL := userBrief(p.UserID)
		result = append(result, gin.H{
			"id":                p.ID,
			"user_id":           p.UserID,
			"author":            author,
			"author_avatar":     avatar,
			"author_avatar_url": avatarURL,
			"title":             p.Title,
			"content":           p.Content,
			"dest_city":         p.DestCity,
			"tags":              decodeTags(p.Tags),
			"likes":             p.Likes,
			"comments":          it.comments,
			"favorites":         it.favorites,
			"favorited":         it.favorited,
			"heat":              it.heat,
			"created_at":        p.CreatedAt,
		})
	}
	c.JSON(http.StatusOK, gin.H{"posts": result})
}

// Community stats are intentionally small and cache-friendly. They power the
// portal header without forcing the client to infer totals from a paginated
// feed (and without exposing any user identifiers).
func CommunityStatsHandler(c *gin.Context) {
	var posts, routes, comments, favorites int64
	if err := database.DB.Model(&models.CommunityPost{}).Count(&posts).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "社区统计暂不可用", "code": "COMMUNITY_STATS_FAILED"})
		return
	}
	database.DB.Model(&models.CommunityPost{}).Where("content LIKE ?", "%\"routes\"%").Count(&routes)
	database.DB.Model(&models.Comment{}).Count(&comments)
	database.DB.Model(&models.PostFavorite{}).Count(&favorites)
	c.JSON(http.StatusOK, gin.H{"posts": posts, "structured_routes": routes, "comments": comments, "favorites": favorites})
}

// decodeTags 将标签 JSON 字符串反序列化为 []string（无标签或解析失败返回空切片）。
func decodeTags(s string) []string {
	var out []string
	if s == "" {
		return []string{}
	}
	_ = json.Unmarshal([]byte(s), &out)
	if out == nil {
		out = []string{}
	}
	return out
}

// PublishPostHandler 发布一篇社区帖（用户分享行程/规划，可带话题标签）
func PublishPostHandler(c *gin.Context) {
	var req struct {
		UserID   string   `json:"user_id"`
		Title    string   `json:"title"`
		Content  string   `json:"content"`
		DestCity string   `json:"dest_city"`
		Tags     []string `json:"tags"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	req.UserID = userID
	if len([]rune(req.Title)) > 200 || len([]byte(req.Content)) > 4<<20 || len(req.Tags) > 20 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "分享内容过长"})
		return
	}
	if req.Title == "" && req.Content == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "标题或内容不能为空"})
		return
	}

	tagsJSON, _ := json.Marshal(req.Tags)
	if tagsJSON == nil {
		tagsJSON = []byte("[]")
	}

	post := models.CommunityPost{
		ID:       uuid.New().String(),
		UserID:   req.UserID,
		Title:    req.Title,
		Content:  req.Content,
		DestCity: req.DestCity,
		Tags:     string(tagsJSON),
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
			"id":                cm.ID,
			"post_id":           cm.PostID,
			"user_id":           cm.UserID,
			"author":            author,
			"author_avatar":     avatar,
			"author_avatar_url": avatarURL,
			"content":           cm.Content,
			"parent_id":         cm.ParentID,
			"created_at":        cm.CreatedAt,
		})
	}
	c.JSON(http.StatusOK, gin.H{"comments": result})
}

// CommentHandler 评论帖子或回复某条评论
func CommentHandler(c *gin.Context) {
	var req struct {
		PostID   string `json:"post_id" binding:"required"`
		UserID   string `json:"user_id"`
		Content  string `json:"content" binding:"required"`
		ParentID string `json:"parent_id"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	req.UserID = userID
	if len([]rune(req.Content)) > 2000 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "评论内容过长"})
		return
	}

	var post models.CommunityPost
	if err := database.DB.Where("id = ?", req.PostID).First(&post).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "帖子不存在"})
		return
	}
	if req.ParentID != "" {
		var parent models.Comment
		if err := database.DB.Where("id = ? AND post_id = ?", req.ParentID, req.PostID).First(&parent).Error; err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "回复目标不存在或不属于该帖子"})
			return
		}
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

// FavoritePostHandler 收藏/取消收藏帖子（一人一藏，重复点击切换）。
func FavoritePostHandler(c *gin.Context) {
	var req struct {
		PostID string `json:"post_id" binding:"required"`
		UserID string `json:"user_id"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	req.UserID = userID

	var post models.CommunityPost
	if err := database.DB.Where("id = ?", req.PostID).First(&post).Error; err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "帖子不存在"})
		return
	}

	var existing models.PostFavorite
	if err := database.DB.Where("post_id = ? AND user_id = ?", req.PostID, req.UserID).First(&existing).Error; err == nil {
		database.DB.Delete(&existing)
		var count int64
		database.DB.Model(&models.PostFavorite{}).Where("post_id = ?", req.PostID).Count(&count)
		c.JSON(http.StatusOK, gin.H{"message": "已取消收藏", "favorited": false, "favorites": count})
		return
	}

	database.DB.Create(&models.PostFavorite{PostID: req.PostID, UserID: req.UserID})
	var count int64
	database.DB.Model(&models.PostFavorite{}).Where("post_id = ?", req.PostID).Count(&count)
	c.JSON(http.StatusOK, gin.H{"message": "已收藏", "favorited": true, "favorites": count})
}

// ListTagsHandler 返回全站话题标签及出现次数（供热门标签选择与筛选）。
func ListTagsHandler(c *gin.Context) {
	var posts []models.CommunityPost
	database.DB.Find(&posts)

	counter := map[string]int{}
	for _, p := range posts {
		for _, t := range decodeTags(p.Tags) {
			counter[t]++
		}
	}

	type tagCount struct {
		Tag   string
		Count int
	}
	tags := make([]tagCount, 0, len(counter))
	for k, v := range counter {
		tags = append(tags, tagCount{k, v})
	}
	sort.Slice(tags, func(i, j int) bool { return tags[i].Count > tags[j].Count })
	if len(tags) > 30 {
		tags = tags[:30]
	}

	result := make([]gin.H, 0, len(tags))
	for _, t := range tags {
		result = append(result, gin.H{"tag": t.Tag, "count": t.Count})
	}
	c.JSON(http.StatusOK, gin.H{"tags": result})
}

// SafetyBriefingHandler 生成AI安全简报
func SafetyBriefingHandler(c *gin.Context) {
	var req struct {
		City         string               `json:"city"`
		CiiScore     float64              `json:"cii_score"`
		RiskLevel    string               `json:"risk_level"`
		ActiveAlerts []models.SafetyAlert `json:"active_alerts"`
		Weather      interface{}          `json:"weather"`
		Traffic      interface{}          `json:"traffic"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}

	// Build briefing text from available data
	var parts []string
	parts = append(parts, fmt.Sprintf("【%s安全简报】", req.City))

	if req.CiiScore > 0 {
		if req.CiiScore >= 40 {
			parts = append(parts, fmt.Sprintf("综合风险指数CII为%.1f，处于高危区间，建议谨慎出行。", req.CiiScore))
		} else if req.CiiScore >= 20 {
			parts = append(parts, fmt.Sprintf("综合风险指数CII为%.1f，处于中度预警区间，请注意安全。", req.CiiScore))
		} else {
			parts = append(parts, fmt.Sprintf("综合风险指数CII为%.1f，处于安全区间，适合出行。", req.CiiScore))
		}
	}

	if req.RiskLevel == "HIGH" {
		parts = append(parts, "当前风险等级为高危，请密切关注当地安全形势。")
	}

	if len(req.ActiveAlerts) > 0 {
		for _, alert := range req.ActiveAlerts {
			parts = append(parts, fmt.Sprintf("⚠️ %s：%s", alert.Title, alert.Detail))
		}
	} else {
		parts = append(parts, "当前无活跃安全告警。")
	}

	if weather, ok := req.Weather.(map[string]interface{}); ok {
		if cond, ok := weather["condition"]; ok {
			parts = append(parts, fmt.Sprintf("天气状况：%v。", cond))
		}
	}

	briefing := strings.Join(parts, "\n\n")
	c.JSON(http.StatusOK, gin.H{"briefing": briefing})
}

// BudgetPredictHandler 预算预测
func BudgetPredictHandler(c *gin.Context) {
	city := c.Query("city")
	days := c.Query("days")

	// Simple estimation based on city tier
	baseCost := 500.0 // default daily
	highCostCities := map[string]bool{"北京": true, "上海": true, "深圳": true, "香港": true, "东京": true, "纽约": true, "伦敦": true, "巴黎": true}
	lowCostCities := map[string]bool{"成都": true, "重庆": true, "西安": true, "长沙": true, "昆明": true}

	if highCostCities[city] {
		baseCost = 800
	}
	if lowCostCities[city] {
		baseCost = 350
	}

	daysNum := 3
	if d, err := strconv.Atoi(days); err == nil {
		daysNum = d
	}

	estimated := baseCost * float64(daysNum)
	c.JSON(http.StatusOK, gin.H{"estimated_cost": estimated, "city": city, "days": daysNum})
}

// RiskRealtimeHandler 旅中风险推送中心：转发到 AI 服务实时重取风险快照，检测变更事件
func RiskRealtimeHandler(c *gin.Context) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 1<<20)
	var req map[string]interface{}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}

	reqBody, _ := json.Marshal(req)
	data, status, err := callAI(c, "/api/v1/risk/realtime", reqBody)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "AI 服务不可用", "changes": []interface{}{}, "snapshot": nil})
		return
	}
	c.Data(status, "application/json; charset=utf-8", data)
}
