package service

import (
	"bytes"
	"encoding/json"
	"io"
	"log"
	"net/http"

	"gateway/internal/database"
	"gateway/internal/models"
)

// triggerAgentNegotiation 触发 AI 推演并携带群聊记忆与进化反馈
func (c *Client) triggerAgentNegotiation(wsMsg models.WSMessage) {
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
	resp, err := http.Post("http://localhost:8000/api/v1/agent_negotiate", "application/json", bytes.NewBuffer(reqBody))
	if err != nil {
		log.Printf("调用 Python 协商接口网络失败: %v", err)
		return
	}
	defer resp.Body.Close()

	bodyBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Printf("读取 Python 返回数据流失败: %v", err)
		return
	}

	log.Printf("🎉 成功接收到 AI 推演原始结果: %s", string(bodyBytes))

	var result map[string]interface{}
	if err := json.Unmarshal(bodyBytes, &result); err != nil {
		log.Printf("将 AI 结果解析为 JSON 失败: %v", err)
		return
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
