package handlers

import (
	"bufio"
	"bytes"
	"encoding/json"
	"log"
	"net/http"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
)

// 定义前端传来的 Payload 结构体
type FrontendMessage struct {
	Type    string `json:"type"`
	Payload struct {
		Destinations    []string `json:"destinations"`
		UserPreferences struct {
			Mode                 string        `json:"mode"`
			Role                 string        `json:"role"`
			Intent               string        `json:"intent"`
			HistorySequence      []string      `json:"history_sequence"`
			CurrentExistingRoute []interface{} `json:"current_existing_route"`
		} `json:"user_preferences"`
	} `json:"payload"`
}

var omniUpgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

// collectCommunityReference 拉取社区热门行程作为软引导参考，注入给 Python 推理时借用（不改动 AI 核心逻辑）。
func collectCommunityReference() []map[string]string {
	var posts []models.CommunityPost
	if err := database.DB.Order("likes desc, created_at desc").Limit(3).Find(&posts).Error; err != nil {
		return []map[string]string{}
	}
	ref := make([]map[string]string, 0, len(posts))
	for _, p := range posts {
		// 截断内容，避免把超长 JSON 灌入 Prompt
		content := p.Content
		runes := []rune(content)
		if len(runes) > 400 {
			content = string(runes[:400]) + "..."
		}
		ref = append(ref, map[string]string{
			"title":     p.Title,
			"dest_city": p.DestCity,
			"content":   content,
		})
	}
	return ref
}

// collectEvolutionMemory 拉取房间内最近的真实交互反馈（正/负样本），实现系统自我进化闭环。
func collectEvolutionMemory(roomID string) []models.FeedbackLog {
	feedback := make([]models.FeedbackLog, 0)
	if database.DB == nil {
		return feedback
	}
	database.DB.Where("room_id = ?", roomID).Order("created_at desc").Limit(5).Find(&feedback)
	return feedback
}

// collectMemberProfiles 聚合房间内各在线成员的用户旅行画像，供推荐引擎做多画像融合。
func collectMemberProfiles(roomMembers []map[string]string) map[string]interface{} {
	profiles := map[string]interface{}{}
	for _, m := range roomMembers {
		id, ok := m["id"]
		if !ok || id == "" {
			continue
		}
		profiles[id] = service.ProfilePayload(id)
	}
	return profiles
}

// HandleWebSocket 处理前端的 WS 升级请求
func HandleWebSocket(c *gin.Context) {
	roomID := c.Query("room_id")
	userID := c.Query("user_id")
	nickname := c.Query("nickname")
	role := c.Query("role")
	intent := c.Query("intent")

	// 容错处理
	if roomID == "" {
		roomID = "room_omni_001"
	}
	if userID == "" {
		userID = "user_anonymous"
	}
	if nickname == "" {
		nickname = userID
	}
	if role == "" {
		role = "成员"
	}

	conn, err := omniUpgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		log.Println("🔥 WebSocket 升级失败:", err)
		return
	}

	client := &service.Client{
		Hub:      GlobalHub,
		Conn:     conn,
		Send:     make(chan models.WSMessage, 256),
		RoomID:   roomID,
		UserID:   userID,
		Nickname: nickname,
		Role:     role,
		Intent:   intent,
	}
	client.Hub.Register <- client
	go client.WritePump()

	defer func() {
		client.Hub.Unregister <- client
		conn.Close()
	}()

	log.Printf("🟢 [Neural Link] 节点接入! Room: %s | User: %s", roomID, userID)

	for {
		_, message, err := conn.ReadMessage()
		if err != nil {
			log.Printf("🔴 节点断开 (%s): %v", userID, err)
			break
		}

		var msg FrontendMessage
		if err := json.Unmarshal(message, &msg); err != nil {
			log.Println("⚠️ JSON 解析错误:", err)
			continue
		}

		if msg.Type == "agent_negotiate" {
			intent := msg.Payload.UserPreferences.Intent
			log.Printf("🚀 接收到前端意图: 模式[%s], 诉求[%s]", msg.Payload.UserPreferences.Mode, intent)

			// 👑 多人协同：聚合当前房间内所有在线成员的画像，传递给 Python 做多智能体博弈
			roomMembers := GlobalHub.CollectMembers(roomID)
			if len(roomMembers) == 0 {
				// 兜底：至少带上当前发起人自己
				roomMembers = []map[string]string{{
					"id":     userID,
					"name":   nickname,
					"role":   role,
					"intent": intent,
				}}
			}

			userPrefsMap := map[string]interface{}{
				"mode":                   msg.Payload.UserPreferences.Mode,
				"role":                   msg.Payload.UserPreferences.Role,
				"intent":                 msg.Payload.UserPreferences.Intent,
				"history_sequence":       msg.Payload.UserPreferences.HistorySequence,
				"current_existing_route": msg.Payload.UserPreferences.CurrentExistingRoute,
				"room_members":           roomMembers,
				"community_reference":    collectCommunityReference(),
				// 🎯 个性化旅行画像注入：发起人与房间成员的画像一并下发给推荐引擎
				"personalized_profile": service.ProfilePayload(userID),
				"member_profiles":      collectMemberProfiles(roomMembers),
			}

			// 将城市提取完全交给 Python 引擎处理，不再做硬编码猜测
			pythonPayload := map[string]interface{}{
				"type":    "negotiation_request",
				"room_id": roomID,
				"user_id": userID,
				"payload": map[string]interface{}{
					"current_request": map[string]interface{}{
						"destinations":     msg.Payload.Destinations,
						"city":             "",
						"user_preferences": userPrefsMap,
					},
					"evolution_memory": collectEvolutionMemory(roomID),
				},
			}

			// 向 Python 算法引擎发起 HTTP POST 请求
			pythonURL := "http://localhost:8000/api/v1/agent/negotiate"
			reqBody, _ := json.Marshal(pythonPayload)

			log.Println("🧠 正在呼叫 Python 多智能体推演矩阵 (流式模式)...")
			httpClient := &http.Client{
				Timeout: 300 * time.Second,
			}
			resp, err := httpClient.Post(pythonURL, "application/json", bytes.NewBuffer(reqBody))

			if err != nil {
				log.Println("🔥 呼叫 Python 引擎失败:", err)
				GlobalHub.Broadcast <- models.WSMessage{
					Type:    "error",
					RoomID:  roomID,
					Payload: "AI 引擎未响应，请检查 Python 服务是否在 8000 端口启动",
				}
				continue
			}

			// ==========================================
			// 🚨 核心改造 2：流式读取 Python 响应，并实时广播！
			// ==========================================
			reader := bufio.NewReader(resp.Body)
			for {
				line, err := reader.ReadBytes('\n')
				if err != nil {
					break // 流结束或断开连接
				}

				line = bytes.TrimSpace(line)
				if len(line) == 0 {
					continue
				}

				var chunk map[string]interface{}
				if err := json.Unmarshal(line, &chunk); err != nil {
					continue
				}

				// 1. 👑 流式推理 token（正文逐字下发）
				if token, ok := chunk["token"]; ok {
					GlobalHub.Broadcast <- models.WSMessage{
						Type:    "stream_token",
						RoomID:  roomID,
						UserID:  "system_omnigateway",
						Payload: token,
					}
					continue
				}

				// 2. � 真实导航路网坐标（高德 polyline），驱动前端绘制真实路线
				if path, ok := chunk["actual_path"]; ok {
					GlobalHub.Broadcast <- models.WSMessage{
						Type:    "actual_path",
						RoomID:  roomID,
						UserID:  "system_omnigateway",
						Payload: path,
					}
					continue
				}

				// 3. 👑 结构化事件透传：target_city / weather_info / traffic_info /
				//    travel_details / budget_breakdown / safety_info / final_route 等
				if msgType, ok := chunk["type"].(string); ok {
					GlobalHub.Broadcast <- models.WSMessage{
						Type:    msgType,
						RoomID:  roomID,
						UserID:  "system_omnigateway",
						Payload: chunk["payload"],
					}
				}
			}
			resp.Body.Close() // 读取完毕后关闭连接
			log.Printf("🏆 拓扑路书流式传输完毕，房间: %s", roomID)
		}
	}
}
