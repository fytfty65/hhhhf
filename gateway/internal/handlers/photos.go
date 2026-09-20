package handlers

import (
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/gin-gonic/gin"

	"gateway/internal/photos"
)

// 用户实拍照片（⑥ 的网络版）：上传 / 我的列表 / 取图 / 删除。
// 路由挂在已带 AuthMiddleware 的 /api/v1 组下，所以这里拿到的 user_id 是可信的。
//
// 路径特意**避开** `/photos/mine` 与 `/photos/:id` 的静态/通配同级冲突：
// gin 的路由树在这类组合上可能直接 panic，那样整个 gateway 起不来（代价太大），
// 所以列表走 `/my/photos`，:id 只用于单张取图与删除。

var (
	photoServiceOnce sync.Once
	photoServiceInst *photos.Service
	photoServiceErr  error
)

func photosDir() string {
	if dir := strings.TrimSpace(os.Getenv("PHOTOS_DIR")); dir != "" {
		return dir
	}
	return filepath.Join("data", "photos")
}

// photosEnabled：默认开启；显式设成 0/false/off 就整条链路关掉（前端会退回"仅本机保存"）。
func photosEnabled() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("PHOTO_UPLOAD_ENABLED"))) {
	case "0", "false", "off", "no":
		return false
	default:
		return true
	}
}

func getPhotoService() (*photos.Service, error) {
	photoServiceOnce.Do(func() {
		photoServiceInst, photoServiceErr = photos.NewService(photosDir(), photosEnabled())
	})
	return photoServiceInst, photoServiceErr
}

func photoError(c *gin.Context, err error) {
	switch {
	case errors.Is(err, photos.ErrDisabled):
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": err.Error(), "code": "PHOTO_UPLOAD_DISABLED"})
	case errors.Is(err, photos.ErrForbidden):
		c.JSON(http.StatusForbidden, gin.H{"error": err.Error(), "code": "PHOTO_FORBIDDEN"})
	case errors.Is(err, photos.ErrNotFound):
		c.JSON(http.StatusNotFound, gin.H{"error": err.Error(), "code": "PHOTO_NOT_FOUND"})
	case errors.Is(err, photos.ErrTooLarge), errors.Is(err, photos.ErrUnsupportedType), errors.Is(err, photos.ErrNotAnImage),
		errors.Is(err, photos.ErrDailyQuota), errors.Is(err, photos.ErrTotalQuota):
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error(), "code": "PHOTO_REJECTED"})
	default:
		c.JSON(http.StatusInternalServerError, gin.H{"error": "照片处理失败", "code": "PHOTO_ERROR"})
	}
}

// UploadPhotoHandler：multipart/form-data，字段 file(必填) / node_name / trip_id。
// 上传成功后状态固定 pending —— 只有上传者自己看得见，审核通过才公开。
func UploadPhotoHandler(c *gin.Context) {
	service, err := getPhotoService()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "照片服务不可用", "code": "PHOTO_ERROR"})
		return
	}
	fileHeader, err := c.FormFile("file")
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少 file 字段", "code": "PHOTO_REJECTED"})
		return
	}
	if fileHeader.Size > int64(photos.MaxUploadBytes)+1024 {
		c.JSON(http.StatusBadRequest, gin.H{"error": photos.ErrTooLarge.Error(), "code": "PHOTO_REJECTED"})
		return
	}
	opened, err := fileHeader.Open()
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "文件读取失败", "code": "PHOTO_REJECTED"})
		return
	}
	defer opened.Close()
	// 多读一点以便识别"其实超限"，但绝不无限读进内存
	data := make([]byte, 0, fileHeader.Size)
	buffer := make([]byte, 64*1024)
	for {
		read, readErr := opened.Read(buffer)
		if read > 0 {
			data = append(data, buffer[:read]...)
			if len(data) > photos.MaxUploadBytes {
				c.JSON(http.StatusBadRequest, gin.H{"error": photos.ErrTooLarge.Error(), "code": "PHOTO_REJECTED"})
				return
			}
		}
		if readErr != nil {
			break
		}
	}

	userID := c.GetString("user_id")
	photo, err := service.Upload(userID, c.PostForm("node_name"), c.PostForm("trip_id"), data)
	if err != nil {
		photoError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{
		"photo":   photo,
		"status":  photo.Status,
		"message": "已收到，正在审核；通过后其他人也能看到这张实拍",
	})
}

// ListMyPhotosHandler：只返回自己的照片（含待审）。别人的照片不会出现在这里。
func ListMyPhotosHandler(c *gin.Context) {
	service, err := getPhotoService()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "照片服务不可用", "code": "PHOTO_ERROR"})
		return
	}
	userID := c.GetString("user_id")
	items := service.ListMine(userID)
	if items == nil {
		items = []photos.Photo{}
	}
	c.JSON(http.StatusOK, gin.H{"photos": items, "enabled": service.Enabled()})
}

// GetPhotoHandler：取图。未通过审核的照片只有上传者本人能取到（服务层判可见性）。
func GetPhotoHandler(c *gin.Context) {
	service, err := getPhotoService()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "照片服务不可用", "code": "PHOTO_ERROR"})
		return
	}
	userID := c.GetString("user_id")
	photo, path, err := service.Open(c.Param("id"), userID)
	if err != nil {
		photoError(c, err)
		return
	}
	// 待审照片是私有的：不允许中间层/共享缓存留存
	if photo.Status != photos.StatusApproved {
		c.Header("Cache-Control", "private, no-store")
	} else {
		c.Header("Cache-Control", "public, max-age=86400")
	}
	c.Header("Content-Type", photo.Mime)
	c.File(path)
}

// DeletePhotoHandler：上传者本人（或管理员）可删；记录与文件一起清掉。
func DeletePhotoHandler(c *gin.Context) {
	service, err := getPhotoService()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "照片服务不可用", "code": "PHOTO_ERROR"})
		return
	}
	userID := c.GetString("user_id")
	isAdmin := strings.EqualFold(strings.TrimSpace(c.GetString("role")), "admin")
	if err := service.Delete(c.Param("id"), userID, isAdmin); err != nil {
		photoError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"deleted": true})
}
