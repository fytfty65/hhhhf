package service

import (
	"encoding/json" // 🚨 核心修复：加上这个
	"log"
	"time"

	"github.com/gorilla/websocket"
)

const (
	// 🚨 核心修复：定义缺失的常量
	writeWait      = 10 * time.Second
	pongWait       = 60 * time.Second
	pingPeriod     = (pongWait * 9) / 10
	maxMessageSize = 512
)

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
	ticker := time.NewTicker(pingPeriod)
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

			w, err := c.Conn.NextWriter(websocket.TextMessage)
			if err != nil {
				return
			}

			// 🚨 核心修复点：将结构体 message 序列化为 JSON 字节
			js, err := json.Marshal(message)
			if err != nil {
				log.Printf("序列化发送消息失败: %v", err)
				return
			}
			
			// 现在写入的是字节流了
			w.Write(js)

			// 优化：处理队列中的其他消息（如果有）
			n := len(c.Send)
			for i := 0; i < n; i++ {
				w.Write([]byte{'\n'})
				nextMsg := <-c.Send
				nextJs, _ := json.Marshal(nextMsg)
				w.Write(nextJs)
			}

			if err := w.Close(); err != nil {
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