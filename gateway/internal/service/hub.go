package service

import (
	"gateway/internal/models"
	"log"

	"github.com/gorilla/websocket"
)

// Client 是 WebSocket 连接与 Hub 之间的中间人
type Client struct {
	Hub  *Hub
	Conn *websocket.Conn
	Send chan models.WSMessage

	// 身份标识
	RoomID string
	UserID string // 或者是 uint，取决于你数据库的设计
}

// Hub 管理所有的 WebSocket 连接和按房间广播
type Hub struct {
	// 🚨 架构升级：二维哈希表 [房间ID] -> [该房间内的Client集合]
	// 这样广播时再也不用遍历全站用户，性能极大提升
	Rooms      map[string]map[*Client]bool
	Broadcast  chan models.WSMessage
	Register   chan *Client
	Unregister chan *Client
}

func NewHub() *Hub {
	return &Hub{
		Rooms:      make(map[string]map[*Client]bool),
		Broadcast:  make(chan models.WSMessage),
		Register:   make(chan *Client),
		Unregister: make(chan *Client),
	}
}

func (h *Hub) Run() {
	for {
		select {
		// 【事件一】用户连接并加入房间
		case client := <-h.Register:
			// 如果这个房间还不存在，就先建个房间
			if h.Rooms[client.RoomID] == nil {
				h.Rooms[client.RoomID] = make(map[*Client]bool)
			}
			h.Rooms[client.RoomID][client] = true
			log.Printf("📥 用户 %s 加入房间 %s", client.UserID, client.RoomID)

		// 【事件二】用户断开连接退出房间
		case client := <-h.Unregister:
			// 找到该用户所在的房间
			if roomClients, ok := h.Rooms[client.RoomID]; ok {
				if _, exists := roomClients[client]; exists {
					// 把用户踢出房间
					delete(roomClients, client)
					close(client.Send)
					log.Printf("📤 用户 %s 离开房间 %s", client.UserID, client.RoomID)

					// 🚨 内存优化：如果房间里人都走光了，销毁这个房间，释放内存
					if len(roomClients) == 0 {
						delete(h.Rooms, client.RoomID)
						log.Printf("🧹 房间 %s 已清空并销毁", client.RoomID)
					}
				}
			}

		// 【事件三】最核心的：群聊精准定向广播
		case message := <-h.Broadcast:
			// 1. 瞬间锁定消息对应的目标房间
			if roomClients, ok := h.Rooms[message.RoomID]; ok {
				// 2. 仅遍历这个房间里的用户（极度高效）
				for client := range roomClients {
					select {
					case client.Send <- message:
						// 成功将消息推入用户的发送通道
					default:
						// 如果用户的通道堵塞（网太卡或断网未及时上报），强制断开该用户
						close(client.Send)
						delete(roomClients, client)
					}
				}
			}
		}
	}
}
