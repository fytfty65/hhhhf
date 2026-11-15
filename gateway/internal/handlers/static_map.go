package handlers

import (
	"context"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
)

var (
	staticMapLocationPattern = regexp.MustCompile(`^-?\d{1,3}(\.\d+)?,-?\d{1,2}(\.\d+)?$`)
	staticMapSizePattern     = regexp.MustCompile(`^\d{2,4}\*\d{2,4}$`)
)

// StaticMapHandler 代理高德静态地图，给 App 提供真实底图。
//
// 为什么走网关而不是让 App 直接请求高德：AMAP_API_KEY 是 Web 服务密钥，
// 打进 APK 等于公开；同时静态地图属于付费配额，统一在服务端出口便于限流与缓存。
//
// 这里只出「底图」——折线、按天配色与编号由客户端绘制，这样样式可以随时调整，
// 也不会被静态地图接口的参数长度和 markers 能力限制。
func StaticMapHandler(c *gin.Context) {
	key := strings.TrimSpace(os.Getenv("AMAP_API_KEY"))
	if key == "" {
		c.JSON(http.StatusServiceUnavailable, gin.H{
			"error": "地图底图服务未配置",
			"code":  "MAP_KEY_MISSING",
		})
		return
	}

	location := strings.TrimSpace(c.Query("location"))
	if !staticMapLocationPattern.MatchString(location) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "location 需要 lng,lat", "code": "INVALID_LOCATION"})
		return
	}

	zoom := strings.TrimSpace(c.DefaultQuery("zoom", "12"))
	if parsed, err := strconv.Atoi(zoom); err != nil || parsed < 3 || parsed > 18 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "zoom 需要在 3~18", "code": "INVALID_ZOOM"})
		return
	}

	size := strings.TrimSpace(c.DefaultQuery("size", "640*420"))
	if !staticMapSizePattern.MatchString(size) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "size 需要 宽*高", "code": "INVALID_SIZE"})
		return
	}
	parts := strings.SplitN(size, "*", 2)
	width, errW := strconv.Atoi(parts[0])
	height, errH := strconv.Atoi(parts[1])
	if errW != nil || errH != nil || width < 100 || width > 1024 || height < 100 || height > 1024 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "size 单边需要在 100~1024", "code": "INVALID_SIZE"})
		return
	}

	params := url.Values{}
	params.Set("key", key)
	params.Set("location", location)
	params.Set("zoom", zoom)
	params.Set("size", size)

	ctx, cancel := context.WithTimeout(c.Request.Context(), 8*time.Second)
	defer cancel()

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, "https://restapi.amap.com/v3/staticmap?"+params.Encode(), nil)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "底图请求初始化失败", "code": "MAP_REQUEST_FAILED"})
		return
	}

	response, err := (&http.Client{Timeout: 8 * time.Second}).Do(request)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "底图服务不可达", "code": "MAP_UNREACHABLE"})
		return
	}
	defer response.Body.Close()

	body, err := io.ReadAll(io.LimitReader(response.Body, 4<<20+1))
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "读取底图失败", "code": "MAP_READ_FAILED"})
		return
	}
	if len(body) > 4<<20 {
		c.JSON(http.StatusBadGateway, gin.H{"error": "底图响应过大", "code": "MAP_TOO_LARGE"})
		return
	}

	contentType := response.Header.Get("Content-Type")
	if !strings.HasPrefix(contentType, "image/") {
		// 高德在参数错误或配额不足时返回 JSON，这里只透出可读结论，不回传原始报文。
		c.JSON(http.StatusBadGateway, gin.H{"error": "底图服务暂不可用", "code": "MAP_UNAVAILABLE"})
		return
	}

	c.Header("Cache-Control", "private, max-age=600")
	c.Data(http.StatusOK, "image/png", body)
}
