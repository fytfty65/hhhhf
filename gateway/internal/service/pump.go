package service

import (
	"encoding/json" // 🚨 核心修复：加上这个
	"log"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/gorilla/websocket"
)

const (
	// 🚨 核心修复：定义缺失的常量
	writeWait  = 10 * time.Second
	pongWait   = 60 * time.Second
	pingPeriod = (pongWait * 9) / 10
	// Planning payloads include member profiles and the current route. 512
	// bytes silently disconnected legitimate clients before the JSON reached
	// the handler; keep a bounded 1 MiB frame limit instead.
	maxMessageSize = 1 << 20
)

// Exported mirror of the connection lifetime limits.
//
// Why exported: the read path in internal/handlers/ws_handler.go cannot reuse
// ReadPump (it has its own message routing) and previously ran a bare
// `conn.ReadMessage()` loop with no deadline at all. A peer that vanished
// without a close frame — the normal case for a dropped mobile connection —
// therefore left that goroutine blocked forever, holding its hub registration
// and send channel. These constants let the handler apply exactly the same
// liveness budget instead of inventing a second, drifting value.
const (
	// PongWait is how long a connection may go silent before it is dropped.
	PongWait = pongWait
	// PingPeriod is how often the write pump pings to elicit a pong.
	PingPeriod = pingPeriod
	// MaxMessageSize bounds an inbound frame.
	MaxMessageSize = maxMessageSize
)

// ConnectionPongWait returns the read deadline used for a WebSocket connection.
//
// WS_PONG_WAIT_SECONDS exists so the liveness behaviour can be asserted by a test
// in seconds instead of the 60s production budget. It is read per connection
// (read paths are not hot) and any unusable value falls back to PongWait, so a
// typo can only make the timeout more conservative in tests — never silently
// disable reclamation.
func ConnectionPongWait() time.Duration {
	raw := strings.TrimSpace(os.Getenv("WS_PONG_WAIT_SECONDS"))
	if raw == "" {
		return PongWait
	}
	seconds, err := strconv.Atoi(raw)
	if err != nil || seconds <= 0 {
		return PongWait
	}
	return time.Duration(seconds) * time.Second
}

// ConnectionPingPeriod returns how often to ping this connection.
//
// It is derived from ConnectionPongWait rather than taken from the PingPeriod
// constant so the two can never contradict each other. Concretely: with the
// production budget a ping every 54s precedes a 60s deadline. If a test shrinks
// the deadline to 1s while the ping period stayed at 54s, a *perfectly healthy*
// client would be disconnected before the first ping ever arrived — which is a
// false failure, and a genuinely confusing one to debug. Deriving the period
// keeps the invariant "a ping is sent well before the deadline" true at every
// budget.
func ConnectionPingPeriod() time.Duration {
	wait := ConnectionPongWait()
	if wait == PongWait {
		return PingPeriod // preserve the exact production cadence
	}
	period := (wait * 9) / 10
	if period <= 0 {
		period = wait / 2
	}
	return period
}

// ReadPump 负责从 WebSocket 连接中不断读取消息
func (c *Client) ReadPump() {
	defer func() {
		c.Hub.Unregister <- c
		c.Conn.Close()
	}()

	c.Conn.SetReadLimit(maxMessageSize)
	c.Conn.SetReadDeadline(time.Now().Add(pongWait))
	c.Conn.SetPongHandler(func(string) error {
		c.Conn.SetReadDeadline(time.Now().Add(pongWait))
		return nil
	})

	for {
		_, message, err := c.Conn.ReadMessage()
		if err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseAbnormalClosure) {
				log.Printf("WebSocket 异常关闭: %v", err)
			}
			break
		}
		// 调用同包下的 HandleIncomingMessage
		c.HandleIncomingMessage(message)
	}
}

// WritePump 负责向 WebSocket 连接写入消息
func (c *Client) WritePump() {
	// Derived from the connection's pong wait so the ping cadence can never
	// contradict the read deadline (see ConnectionPingPeriod).
	ticker := time.NewTicker(ConnectionPingPeriod())
	defer func() {
		ticker.Stop()
		c.Conn.Close()
	}()

	for {
		select {
		case message, ok := <-c.Send:
			c.Conn.SetWriteDeadline(time.Now().Add(writeWait))
			if !ok {
				c.Conn.WriteMessage(websocket.CloseMessage, []byte{})
				return
			}

			js, err := json.Marshal(message)
			if err != nil {
				log.Printf("序列化发送消息失败: %v", err)
				return
			}

			if err := c.Conn.WriteMessage(websocket.TextMessage, js); err != nil {
				return
			}
		case <-ticker.C:
			c.Conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if err := c.Conn.WriteMessage(websocket.PingMessage, nil); err != nil {
				return
			}
		}
	}
}
