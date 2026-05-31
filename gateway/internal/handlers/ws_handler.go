package handlers

import (
	"bufio"
	"bytes"
	"encoding/json"
	"log"
	"net/http"
	"time"

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

// HandleWebSocket 处理前端的 WS 升级请求
func HandleWebSocket(c *gin.Context) {
	roomID := c.Query("room_id")
	userID := c.Query("user_id")

	// 容错处理
	if roomID == "" {
		roomID = "room_omni_001"
	}
	if userID == "" {
		userID = "user_anonymous"
	}

	conn, err := omniUpgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		log.Println("🔥 WebSocket 升级失败:", err)
		return
	}

	client := &service.Client{
		Hub:    GlobalHub,
		Conn:   conn,
		Send:   make(chan models.WSMessage, 256),
		RoomID: roomID,
		UserID: userID,
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

			// 将城市提取完全交给 Python 引擎处理，不再做硬编码猜测
			pythonPayload := map[string]interface{}{
				"type":    "negotiation_request",
				"room_id": roomID,
				"user_id": userID,
				"payload": map[string]interface{}{
					"current_request": map[string]interface{}{
						"destinations":     msg.Payload.Destinations,
						"city":             "",
						"user_preferences": msg.Payload.UserPreferences,
					},
					"evolution_memory": []interface{}{}, // 预留给 RLHF 持续学习库
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
				if err := json.Unmarshal(line, &chunk); err == nil {
					// 瞬间将 Token 广播给房间内所有用户
					outMsg := models.WSMessage{
						Type:    "stream_token", // 👈 对应前端 page.tsx 监听的事件
						RoomID:  roomID,
						UserID:  "system_omnigateway",
						Payload: chunk["token"],
					}

					GlobalHub.Broadcast <- outMsg
				}
			}
			resp.Body.Close() // 读取完毕后关闭连接
			log.Printf("🏆 拓扑路书流式传输完毕，房间: %s", roomID)
		}
	}
}
