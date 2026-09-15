package handlers

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
)

// ==========================================
// 模块 7 · 省数据动态化（高德 POI + 季节 + 埋点权重）
// ==========================================

// ProvinceDynamicHandler 省数据动态化：接收候选景点集 + 可选季节，
// 聚合全站埋点热度（点击 + 正反馈），综合「季节相关度」重排后返回。
//
// 请求体示例：
//
//	{ "season": "winter", "hotspots": [{"name":"故宫","desc":"...","tags":["人文"]}] }
func ProvinceDynamicHandler(c *gin.Context) {
	var req struct {
		Season   string `json:"season"`
		Hotspots []struct {
			Name string   `json:"name"`
			Desc string   `json:"desc"`
			Tags []string `json:"tags"`
		} `json:"hotspots"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "参数不完整"})
		return
	}

	season := req.Season
	if season == "" {
		season = service.SeasonOf(int(time.Now().Month()))
	}

	names := make([]string, 0, len(req.Hotspots))
	for _, h := range req.Hotspots {
		if h.Name != "" {
			names = append(names, h.Name)
		}
	}

	clicks := aggregatePoiClicks(names)
	likes := aggregatePoiLikes(names)

	hotspots := make([]service.Hotspot, 0, len(req.Hotspots))
	for _, h := range req.Hotspots {
		hotspots = append(hotspots, service.Hotspot{Name: h.Name, Desc: h.Desc, Tags: h.Tags})
	}

	ranked := service.RankHotspots(hotspots, clicks, likes, season)

	result := make([]gin.H, 0, len(ranked))
	for _, r := range ranked {
		result = append(result, gin.H{
			"name":         r.Name,
			"season_match": r.SeasonMatch,
			"weight":       r.Weight,
			"score":        r.Score,
			"clicks":       r.Clicks,
			"likes":        r.Likes,
		})
	}
	c.JSON(http.StatusOK, gin.H{"season": season, "ranked": result})
}

// aggregatePoiClicks 聚合名称集合中各景点的埋点点击量（RecommendationEvent.Clicked）。
func aggregatePoiClicks(names []string) map[string]int64 {
	out := map[string]int64{}
	if len(names) == 0 {
		return out
	}
	var rows []models.RecommendationEvent
	database.DB.Where("target IN ? AND clicked = ?", names, true).Find(&rows)
	for _, r := range rows {
		out[r.Target]++
	}
	return out
}

// aggregatePoiLikes 聚合名称集合中各景点的正反馈数（FeedbackLog.Score >= 1）。
func aggregatePoiLikes(names []string) map[string]int64 {
	out := map[string]int64{}
	if len(names) == 0 {
		return out
	}
	var rows []models.FeedbackLog
	database.DB.Where("target IN ? AND score >= ?", names, 1).Find(&rows)
	for _, r := range rows {
		out[r.Target]++
	}
	return out
}

// ProvincePoiClickHandler 记录目的地图谱中「景点点击」埋点，供后续热度权重回填。
func ProvincePoiClickHandler(c *gin.Context) {
	var req struct {
		UserID string `json:"user_id" binding:"required"`
		Target string `json:"target" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "缺少 user_id / target"})
		return
	}
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	req.UserID = userID

	event := models.RecommendationEvent{
		UserID:  req.UserID,
		Variant: "province",
		Target:  req.Target,
		Clicked: true,
		Source:  "province",
	}
	if err := database.DB.Create(&event).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "埋点记录失败"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"message": "POI 点击已记录"})
}

// AmapPoiSearchHandler 高德 POI 搜索代理：按城市/关键词拉取真实文旅 POI，
// 将静态省数据补强为「高德实况」。无 Key 或请求失败时优雅降级，不阻断主流程。
func AmapPoiSearchHandler(c *gin.Context) {
	key := strings.TrimSpace(os.Getenv("AMAP_API_KEY"))
	city := c.Query("city")
	keywords := c.Query("keywords")
	types := c.DefaultQuery("types", "110000|141200|060400|060100")
	if len([]rune(city)) > 100 || len([]rune(keywords)) > 200 || len([]rune(types)) > 200 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "搜索参数过长", "code": "QUERY_TOO_LONG"})
		return
	}
	limit := 20
	if raw := c.Query("offset"); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil {
			limit = parsed
		}
	}
	if limit < 1 {
		limit = 1
	}
	if limit > 50 {
		limit = 50
	}

	if key == "" {
		// Provider credentials are normally owned by the AI service. Proxy the
		// normalized POI query there so local deployments do not need to copy a
		// secret into two .env files.
		body, _ := json.Marshal(gin.H{
			"city": city, "keywords": keywords, "types": types, "limit": limit,
		})
		data, status, err := callAI(c, "/api/v1/amap/poi", body)
		if err != nil {
			c.JSON(http.StatusBadGateway, gin.H{"available": false, "pois": []gin.H{}, "message": "地图数据服务不可达"})
			return
		}
		c.Data(status, "application/json; charset=utf-8", data)
		return
	}

	params := url.Values{}
	params.Set("key", key)
	params.Set("city", city)
	params.Set("keywords", keywords)
	params.Set("types", types)
	params.Set("sortrule", "weight")
	params.Set("offset", strconv.Itoa(limit))
	params.Set("page", "1")
	params.Set("extensions", "base")

	ctx, cancel := context.WithTimeout(c.Request.Context(), 8*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, "https://restapi.amap.com/v3/place/text?"+params.Encode(), nil)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"available": false, "pois": []gin.H{}, "message": "高德请求初始化失败"})
		return
	}
	resp, err := (&http.Client{Timeout: 8 * time.Second}).Do(request)
	if err != nil {
		c.JSON(http.StatusOK, gin.H{"available": false, "pois": []gin.H{}, "message": "高德服务不可达"})
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(io.LimitReader(resp.Body, 2<<20+1))
	if err != nil {
		c.JSON(http.StatusOK, gin.H{"available": false, "pois": []gin.H{}, "message": "读取高德响应失败"})
		return
	}
	if len(body) > 2<<20 {
		c.JSON(http.StatusBadGateway, gin.H{"available": false, "pois": []gin.H{}, "message": "高德响应过大"})
		return
	}

	var amapResp struct {
		Status string `json:"status"`
		Info   string `json:"info"`
		Pois   []struct {
			Name     string `json:"name"`
			Type     string `json:"type"`
			Address  string `json:"address"`
			Location string `json:"location"`
			Adname   string `json:"adname"`
		} `json:"pois"`
	}
	if err := json.Unmarshal(body, &amapResp); err != nil || amapResp.Status != "1" {
		c.JSON(http.StatusOK, gin.H{"available": false, "pois": []gin.H{}, "message": amapResp.Info})
		return
	}

	pois := make([]gin.H, 0, len(amapResp.Pois))
	for _, p := range amapResp.Pois {
		lnglat := strings.Split(p.Location, ",")
		lng, lat := "", ""
		if len(lnglat) == 2 {
			lng, lat = lnglat[0], lnglat[1]
		}
		pois = append(pois, gin.H{
			"name":    p.Name,
			"type":    p.Type,
			"address": p.Address,
			"adname":  p.Adname,
			"lng":     lng,
			"lat":     lat,
		})
	}
	c.JSON(http.StatusOK, gin.H{"available": true, "count": len(pois), "pois": pois})
}
