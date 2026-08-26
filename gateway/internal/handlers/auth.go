package handlers

import (
	"net/http"
	"strings"
	"sync"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"golang.org/x/crypto/bcrypt"
)

// sessionStore 以内存形式维护 token -> userID 的会话映射。
// 生产环境可替换为 Redis，这里为保持零外部依赖而采用内存实现。
var (
	sessionMu    sync.RWMutex
	sessionStore = make(map[string]string)
)

func newSessionToken(userID string) string {
	token := uuid.New().String()
	sessionMu.Lock()
	sessionStore[token] = userID
	sessionMu.Unlock()
	return token
}

// buildAuthUser 将数据库用户转换为前端期望的 user 对象。
func buildAuthUser(u models.User) gin.H {
	nickname := u.Nickname
	if nickname == "" {
		nickname = u.Username
	}
	avatarSeed := u.AvatarSeed
	if avatarSeed == "" {
		avatarSeed = u.ID
	}
	return gin.H{
		"id":         u.ID,
		"username":   u.Username,
		"nickname":   nickname,
		"avatarSeed": avatarSeed,
		"avatarUrl":  u.AvatarURL,
		"signature":  u.Signature,
	}
}

// RegisterHandler 用户注册：用户名 + 密码（可选昵称）
func RegisterHandler(c *gin.Context) {
	var req struct {
		Username string `json:"username" binding:"required"`
		Password string `json:"password" binding:"required,min=6"`
		Nickname string `json:"nickname"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "用户名或密码格式不正确（密码至少 6 位）"})
		return
	}

	username := strings.TrimSpace(req.Username)
	if username == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "用户名不能为空"})
		return
	}

	var existing models.User
	if err := database.DB.Where("username = ?", username).First(&existing).Error; err == nil {
		c.JSON(http.StatusConflict, gin.H{"error": "用户名已被注册"})
		return
	}

	hash, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcrypt.DefaultCost)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "密码加密失败"})
		return
	}

	user := models.User{
		ID:           uuid.New().String(),
		Username:     username,
		PasswordHash: string(hash),
		Nickname:     strings.TrimSpace(req.Nickname),
		AvatarSeed:   username,
	}
	if err := database.DB.Create(&user).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "注册失败，请稍后再试"})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"message": "注册成功",
		"token":   newSessionToken(user.ID),
		"user":    buildAuthUser(user),
	})
}

// LoginHandler 用户登录：校验用户名与密码
func LoginHandler(c *gin.Context) {
	var req struct {
		Username string `json:"username" binding:"required"`
		Password string `json:"password" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "请填写用户名与密码"})
		return
	}

	username := strings.TrimSpace(req.Username)

	var user models.User
	if err := database.DB.Where("username = ?", username).First(&user).Error; err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "用户名或密码错误"})
		return
	}

	if err := bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(req.Password)); err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "用户名或密码错误"})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"message": "登录成功",
		"token":   newSessionToken(user.ID),
		"user":    buildAuthUser(user),
	})
}