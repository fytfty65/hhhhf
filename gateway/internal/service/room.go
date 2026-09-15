package service

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
)

var legacyNegotiationSlots = struct {
	sync.Mutex
	active map[string]bool
}{active: make(map[string]bool)}

var legacyAgentWindows = struct {
	sync.Mutex
	entries map[string]legacyRateWindow
}{entries: make(map[string]legacyRateWindow)}

type legacyRateWindow struct {
	started time.Time
	count   int
}

func acquireLegacyNegotiation(roomID, userID string) bool {
	now := time.Now()
	legacyAgentWindows.Lock()
	entry := legacyAgentWindows.entries[userID]
	if entry.started.IsZero() || now.Sub(entry.started) >= time.Minute {
		entry = legacyRateWindow{started: now}
	}
	if entry.count >= 4 {
		legacyAgentWindows.entries[userID] = entry
		legacyAgentWindows.Unlock()
		return false
	}
	entry.count++
	legacyAgentWindows.entries[userID] = entry
	legacyAgentWindows.Unlock()

	legacyNegotiationSlots.Lock()
	defer legacyNegotiationSlots.Unlock()
	if legacyNegotiationSlots.active[roomID] {
		return false
	}
	legacyNegotiationSlots.active[roomID] = true
	return true
}

func releaseLegacyNegotiation(roomID string) {
	legacyNegotiationSlots.Lock()
	delete(legacyNegotiationSlots.active, roomID)
	legacyNegotiationSlots.Unlock()
}

// triggerAgentNegotiation 触发 AI 推演并携带群聊记忆与进化反馈
func (c *Client) triggerAgentNegotiation(wsMsg models.WSMessage) {
	if !acquireLegacyNegotiation(c.RoomID, c.UserID) {
		c.Hub.Broadcast <- models.WSMessage{Type: "error", RoomID: c.RoomID, UserID: "system", Payload: map[string]interface{}{"code": "AI_RATE_LIMITED", "message": "智能体推演请求过于频繁或房间已有任务进行中"}}
		return
	}
	defer releaseLegacyNegotiation(c.RoomID)
	var chatHistory []models.Message
	var feedbackHistory []models.FeedbackLog // 🚨 关键：引入进化反馈历史

	if database.DB != nil {
		// 1. 获取长对话记忆：最近 15 条群聊记录
		database.DB.Where("room_id = ?", c.RoomID).Order("created_at asc").Limit(15).Find(&chatHistory)

		// 2. 🚨 获取持续学习样本：最近 5 条真实交互反馈（正负样本）
		// 这是实现“系统自我进化闭环”的核心数据来源
		database.DB.Where("room_id = ?", c.RoomID).Order("created_at desc").Limit(5).Find(&feedbackHistory)
	}

	// 3. 构建高阶感知 Payload
	enrichedPayload := map[string]interface{}{
		"current_request":  wsMsg.Payload,   // 当前用户的直接指令
		"chat_history":     chatHistory,     // 群聊上下文（用于冲突仲裁）
		"evolution_memory": feedbackHistory, // 进化记忆（用于自适应模型修正）
	}
	wsMsg.Payload = enrichedPayload

	reqBody, _ := json.Marshal(wsMsg)

	// 调用 Python 后端（多智能体博弈引擎）
	baseURL := strings.TrimSpace(os.Getenv("AI_SERVICE_URL"))
	if baseURL == "" {
		baseURL = "http://127.0.0.1:8000"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Second)
	defer cancel()
	req, reqErr := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(baseURL, "/")+"/api/v1/agent/negotiate", bytes.NewReader(reqBody))
	if reqErr != nil {
		log.Printf("构造 Python 协商请求失败: %v", reqErr)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if token := strings.TrimSpace(os.Getenv("AI_SERVICE_INTERNAL_TOKEN")); token != "" {
		req.Header.Set("X-Omni-Internal-Token", token)
	}
	resp, err := (&http.Client{Timeout: 300 * time.Second}).Do(req)
	if err != nil {
		log.Printf("调用 Python 协商接口网络失败: %v", err)
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		log.Printf("Python 协商接口返回状态 %d", resp.StatusCode)
		return
	}

	bodyBytes, err := io.ReadAll(io.LimitReader(resp.Body, 16<<20+1))
	if err != nil {
		log.Printf("读取 Python 返回数据流失败: %v", err)
		return
	}

	if len(bodyBytes) > 16<<20 {
		log.Printf("AI 推演响应超过大小限制")
		return
	}
	log.Printf("🎉 成功接收到 AI 推演结果 (%d bytes)", len(bodyBytes))

	var result map[string]interface{}
	if err := json.Unmarshal(bodyBytes, &result); err != nil {
		// The current AI endpoint streams newline-delimited JSON. Keep the
		// legacy room endpoint compatible by selecting its final_route payload
		// instead of discarding an otherwise valid response.
		scanner := bufio.NewScanner(bytes.NewReader(bodyBytes))
		scanner.Buffer(make([]byte, 64*1024), 1<<20)
		for scanner.Scan() {
			var chunk map[string]interface{}
			if json.Unmarshal(bytes.TrimSpace(scanner.Bytes()), &chunk) != nil {
				continue
			}
			if payload, ok := chunk["payload"].(map[string]interface{}); ok {
				if typ, _ := chunk["type"].(string); typ == "final_route" {
					result = payload
				}
			}
		}
		if result == nil {
			log.Printf("将 AI 结果解析为 JSON 失败: %v", err)
			return
		}
	}

	// 4. 将 AI 决策结果存入数据库（作为未来的历史记忆）
	aiSummary := "AI 系统基于群聊博弈生成了优化后的精细行程"
	if summary, ok := result["negotiation_summary"].(string); ok {
		aiSummary = summary
	}

	if database.DB != nil {
		database.DB.Create(&models.Message{
			RoomID:   c.RoomID,
			UserID:   "0", // 系统/AI 统一标识
			RoleType: "ai",
			Content:  aiSummary,
		})
	}

	// 5. 将推演结果广播给房间内所有成员
	resultMsg := models.WSMessage{
		Type:    "ai_negotiation_result",
		RoomID:  c.RoomID,
		UserID:  "system_agent_coordinator",
		Payload: result,
	}
	c.Hub.Broadcast <- resultMsg
}

// HandleIncomingMessage 处理来自客户端的 WebSocket 消息
func (c *Client) HandleIncomingMessage(message []byte) {
	var wsMsg models.WSMessage
	if err := json.Unmarshal(message, &wsMsg); err != nil {
		log.Printf("消息解析失败: %v", err)
		return
	}

	// 自动注入房间和用户身份
	wsMsg.RoomID = c.RoomID
	wsMsg.UserID = c.UserID

	// 1. 如果是普通群聊消息，持久化到数据库以供 AI 学习上下文
	if database.DB != nil && wsMsg.Type == "chat" {
		// 提取消息内容，防止存储原始二进制
		var chatContent string
		if content, ok := wsMsg.Payload.(string); ok {
			chatContent = content
		} else {
			chatContent = string(message)
		}

		database.DB.Create(&models.Message{
			RoomID:   c.RoomID,
			UserID:   c.UserID,
			RoleType: "human",
			Content:  chatContent,
		})
	}

	// 行程节点级协作批注：走独立实时同步链路（持久化 + 规范广播），避免重复广播原始报文
	if wsMsg.Type == "annotation_add" || wsMsg.Type == "annotation_vote" || wsMsg.Type == "annotation_resolve" {
		c.handleAnnotation(wsMsg.Type, wsMsg.Payload)
		return
	}

	// 协作光标/编辑节点在场态（模块8）：经 Hub 串行化落地 + 广播，避免直接读写 Rooms 造成数据竞争
	if wsMsg.Type == "cursor_move" || wsMsg.Type == "editing_node" || wsMsg.Type == "cursor_leave" {
		c.handlePresence(wsMsg)
		return
	}

	// 2. 实时广播给房间内的其他小伙伴
	c.Hub.Broadcast <- wsMsg

	// 3. 根据指令类型分发逻辑
	switch wsMsg.Type {
	case "agent_negotiate":
		log.Printf("🚀 启动多智能体深度博弈推演: Room %s", wsMsg.RoomID)
		go c.triggerAgentNegotiation(wsMsg)

	case "action_add_poi":
		log.Printf("📍 用户 %s 尝试向沙盘添加兴趣点", wsMsg.UserID)
		// TODO: 未来可在此接入实时冲突检测逻辑

	case "feedback_submit":
		log.Printf("💡 收到用户反馈，系统正在进化...")
		// 反馈逻辑已通过 HTTP 接口处理，此处可做实时广播提醒

	default:
		log.Printf("📩 房间 %s 收到常规消息", wsMsg.RoomID)
	}
}

// handlePresence 处理协作光标/编辑节点在场态消息（模块8）。
// 融合「客户端上报身份」与「连接注册身份」后经 Hub 串行化落地并广播，线程安全。
func (c *Client) handlePresence(wsMsg models.WSMessage) {
	if wsMsg.Type == "cursor_leave" {
		c.Hub.ClearPresence(c.RoomID, c.UserID)
		return
	}
	payload, _ := wsMsg.Payload.(map[string]interface{})
	p := ParsePresence(c.UserID, c.Nickname, c.Role, c.AvatarUrl, payload)
	if p.NodeKey == "" {
		c.Hub.ClearPresence(c.RoomID, c.UserID)
		return
	}
	c.Hub.TrackPresence(c.RoomID, p)
}
