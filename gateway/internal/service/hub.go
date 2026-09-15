package service

import (
	"gateway/internal/models"
	"log"
	"time"

	"github.com/gorilla/websocket"
)

// Client 是 WebSocket 连接与 Hub 之间的中间人
type Client struct {
	Hub  *Hub
	Conn *websocket.Conn
	Send chan models.WSMessage

	// 身份标识
	RoomID string
	UserID string

	// 成员画像（用于房间成员实时同步）
	Nickname  string
	Role      string
	Intent    string
	AvatarUrl string
}

// membersQuery 用于从 Run 协程外安全地查询房间成员列表（避免 map 并发读写）。
type membersQuery struct {
	roomID string
	resp   chan []map[string]string
}

// presenceEvent 协作光标在场态变更事件（Room 协程外发起，Run 协程内落地，保证线程安全）。
type presenceEvent struct {
	roomID   string
	presence NodePresence
	clear    bool // true 表示清除该用户在场态（离开节点/断开）
}

// Hub 管理所有的 WebSocket 连接和按房间广播
type Hub struct {
	// 🚨 架构升级：二维哈希表 [房间ID] -> [该房间内的Client集合]
	// 这样广播时再也不用遍历全站用户，性能极大提升
	Rooms        map[string]map[*Client]bool
	Broadcast    chan models.WSMessage
	Register     chan *Client
	Unregister   chan *Client
	MembersQuery chan membersQuery

	// 协作光标在场态（模块8）：Presence 通道串行化写操作，presenceByRoom 仅在 Run 协程内读写
	Presence       chan presenceEvent
	presenceByRoom map[string]map[string]NodePresence
}

func NewHub() *Hub {
	return &Hub{
		Rooms:          make(map[string]map[*Client]bool),
		Broadcast:      make(chan models.WSMessage),
		Register:       make(chan *Client),
		Unregister:     make(chan *Client),
		MembersQuery:   make(chan membersQuery),
		Presence:       make(chan presenceEvent),
		presenceByRoom: make(map[string]map[string]NodePresence),
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
			h.pushMembersUpdate(client.RoomID)

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
						delete(h.presenceByRoom, client.RoomID)
						log.Printf("🧹 房间 %s 已清空并销毁", client.RoomID)
					} else {
						// 仍有成员在线，广播最新的成员列表
						h.pushMembersUpdate(client.RoomID)
					}
				}
			}
			// 清理该用户在房间的协作光标在场态（断开即视为离开节点）
			h.removePresenceLocked(client.RoomID, client.UserID)

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

		// 【事件五】协作光标在场态变更（模块8：头像悬浮 + 光标同步）
		case ev := <-h.Presence:
			h.applyPresence(ev)

		// 【事件四】外部安全查询房间成员（用于多智能体聚合）
		case q := <-h.MembersQuery:
			members := []map[string]string{}
			if roomClients, ok := h.Rooms[q.roomID]; ok {
				members = h.buildMemberList(roomClients)
			}
			q.resp <- members
		}
	}
}

// buildMemberList 将房间内客户端集合转换为成员画像列表（含头像种子）。
// ⚠️ 仅在 Run 协程内调用，直接读取 Rooms map，保证线程安全。
func (h *Hub) buildMemberList(roomClients map[*Client]bool) []map[string]string {
	// 以 UserID 去重：同一用户可能同时持有「大厅」和「工作台」两条 WS 连接，
	// 仅保留画像更完整的一条，避免成员列表与 AI 聚合出现重复。
	byUser := make(map[string]*Client, len(roomClients))
	for client := range roomClients {
		existing, ok := byUser[client.UserID]
		if !ok {
			byUser[client.UserID] = client
			continue
		}
		// 优先保留带昵称的连接（大厅连接画像更完整）
		if client.Nickname != "" && existing.Nickname == "" {
			byUser[client.UserID] = client
		}
	}

	members := make([]map[string]string, 0, len(byUser))
	for _, client := range byUser {
		name := client.Nickname
		if name == "" {
			name = client.UserID
		}
		role := client.Role
		if role == "" {
			role = "成员"
		}
		members = append(members, map[string]string{
			"id":         client.UserID,
			"name":       name,
			"role":       role,
			"intent":     client.Intent,
			"avatarSeed": name,
			"avatar_url": client.AvatarUrl,
		})
	}
	return members
}

// CollectMembers 供 Run 协程外调用，安全获取某房间当前在线成员画像。
func (h *Hub) CollectMembers(roomID string) []map[string]string {
	q := membersQuery{
		roomID: roomID,
		resp:   make(chan []map[string]string, 1),
	}
	h.MembersQuery <- q
	return <-q.resp
}

// pushMembersUpdate 同步将某房间当前在线成员列表广播给房间内所有客户端。
// ⚠️ 必须在 Run 协程内调用（与 Rooms map 同线程，避免数据竞争与通道死锁）。
func (h *Hub) pushMembersUpdate(roomID string) {
	roomClients, ok := h.Rooms[roomID]
	if !ok || len(roomClients) == 0 {
		return
	}

	members := h.buildMemberList(roomClients)

	update := models.WSMessage{
		Type:    "room_members_update",
		RoomID:  roomID,
		UserID:  "system_member_sync",
		Payload: members,
	}

	for client := range roomClients {
		select {
		case client.Send <- update:
		default:
			// 通道堵塞则跳过，避免阻塞成员广播
		}
	}
}

// TrackPresence 供 Run 协程外调用：上报某成员的协作光标在场态（正在聚焦哪个节点）。
func (h *Hub) TrackPresence(roomID string, p NodePresence) {
	h.Presence <- presenceEvent{roomID: roomID, presence: p}
}

// ClearPresence 供 Run 协程外调用：清除某成员在场态（离开节点 / 断开）。
func (h *Hub) ClearPresence(roomID, userID string) {
	h.Presence <- presenceEvent{roomID: roomID, presence: NodePresence{UserID: userID}, clear: true}
}

// applyPresence 在 Run 协程内落地一场在场态变更：先清理过期，再合并/移除，最后广播快照。
func (h *Hub) applyPresence(ev presenceEvent) {
	now := time.Now()
	room, ok := h.presenceByRoom[ev.roomID]
	if ev.clear {
		if ok {
			delete(room, ev.presence.UserID)
			if len(room) == 0 {
				delete(h.presenceByRoom, ev.roomID)
			}
		}
	} else {
		if !ok {
			room = map[string]NodePresence{}
			h.presenceByRoom[ev.roomID] = room
		}
		UpsertPresence(room, ev.presence, now)
		PruneExpiredPresence(room, now, PresenceTTL)
	}
	h.pushPresenceUpdate(ev.roomID)
}

// removePresenceLocked 移除某用户在房间的在场态，并在仍有成员时广播。
// ⚠️ 仅在 Run 协程内调用。
func (h *Hub) removePresenceLocked(roomID, userID string) {
	if room, ok := h.presenceByRoom[roomID]; ok {
		if _, exists := room[userID]; exists {
			delete(room, userID)
			h.pushPresenceUpdate(roomID)
		}
	}
}

// pushPresenceUpdate 将当前房间的在场态快照广播给房间内所有客户端。
// ⚠️ 仅在 Run 协程内调用。
func (h *Hub) pushPresenceUpdate(roomID string) {
	roomClients, ok := h.Rooms[roomID]
	if !ok || len(roomClients) == 0 {
		return
	}

	update := models.WSMessage{
		Type:    "editing_nodes_update",
		RoomID:  roomID,
		UserID:  "system_presence_sync",
		Payload: PresenceSnapshot(h.presenceByRoom[roomID]),
	}

	for client := range roomClients {
		select {
		case client.Send <- update:
		default:
			// 通道堵塞则跳过，避免阻塞在场态广播
		}
	}
}
