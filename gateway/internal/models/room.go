package models

// WSMessage 定义了 WebSocket 统一消息格式
type WSMessage struct {
	Type    string      `json:"type"`    // 消息类型: "chat", "action", "sync_state", "agent_negotiation"
	RoomID  string      `json:"room_id"` 
	UserID  string      `json:"user_id"`
	Payload interface{} `json:"payload"` // 具体的数据内容
}