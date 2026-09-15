package service

import (
	"context"
	"encoding/json"
	"log"
	"os"
	"time"

	"gateway/internal/models"

	"github.com/redis/go-redis/v9"
)

// eventStreamKey 与 AI 服务 RedisEventStream 写入的全局事件流一致。
const eventStreamKey = "omni:events:global"

// streamEvent 是 Redis Stream 内单条事件的信封结构（与 AI 服务 emit 对齐）。
type streamEvent struct {
	Type    string          `json:"type"`
	RoomID  string          `json:"room_id"`
	Payload json.RawMessage `json:"payload"`
	TS      int64           `json:"ts"`
}

// StartEventStreamBridge 启动 Redis Stream 事件桥接 goroutine：
// 从全局事件流消费事件，并按事件信封内的 room_id 定向广播到房间 WebSocket。
// Redis 不可用或连接中断时自动重连自愈，绝不阻塞/拖垮网关主流程。
func StartEventStreamBridge(hub *Hub) {
	go func() {
		url := os.Getenv("REDIS_URL")
		if url == "" {
			// Redis is optional for a local single-process development run. Do
			// not start a reconnect loop (or emit noisy warnings) when the
			// operator intentionally left it unset; production startup validation
			// still requires REDIS_URL for shared sessions.
			return
		}
		opt, err := redis.ParseURL(url)
		if err != nil {
			log.Printf("⚠️ [事件流桥接] REDIS_URL 解析失败，桥接停用: %v", err)
			return
		}
		rdb := redis.NewClient(opt)
		defer rdb.Close()

		lastID := "$" // 仅接收启动之后的新事件，不重放历史
		ctx := context.Background()
		for {
			streams, err := rdb.XRead(ctx, &redis.XReadArgs{
				Streams: []string{eventStreamKey, lastID},
				Count:   50,
				Block:   5 * time.Second,
			}).Result()
			if err != nil {
				if err == redis.Nil {
					continue // 5s 超时无新事件，正常空转
				}
				log.Printf("⚠️ [事件流桥接] XRead 失败，3s 后重连: %v", err)
				time.Sleep(3 * time.Second)
				continue
			}

			for _, s := range streams {
				for _, msg := range s.Messages {
					lastID = msg.ID
					dataRaw, ok := msg.Values["data"]
					if !ok {
						continue
					}
					dataStr, ok := dataRaw.(string)
					if !ok {
						continue
					}
					var ev streamEvent
					if err := json.Unmarshal([]byte(dataStr), &ev); err != nil {
						continue
					}
					var payload interface{}
					_ = json.Unmarshal(ev.Payload, &payload)
					hub.Broadcast <- models.WSMessage{
						Type:    "stream_event",
						RoomID:  ev.RoomID,
						UserID:  "system_redis_stream",
						Payload: map[string]interface{}{
							"type":    ev.Type,
							"room_id": ev.RoomID,
							"payload": payload,
							"ts":      ev.TS,
						},
					}
				}
			}
		}
	}()
}
