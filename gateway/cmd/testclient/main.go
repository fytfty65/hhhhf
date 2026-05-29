package main

import (
	"encoding/json"
	"log"
	"net/url"
	"time"

	"github.com/gorilla/websocket"
)

func main() {
	// 1. 定义要连接的 WebSocket 房间地址
	u := url.URL{Scheme: "ws", Host: "localhost:8080", Path: "/api/v1/room/test-room-001/ws"}
	log.Printf("虚拟客户端正在连接网关: %s", u.String())

	// 2. 发起连接
	c, _, err := websocket.DefaultDialer.Dial(u.String(), nil)
	if err != nil {
		log.Fatalf("连接网关失败 (网关启动了吗?): %v", err)
	}
	defer c.Close()
	log.Println("✅ 成功连接到房间!")

	// 3. 启动一个后台协程监听广播
	go func() {
		for {
			_, message, err := c.ReadMessage()
			if err != nil {
				log.Println("读取消息断开:", err)
				return
			}
			log.Printf("\n🔥🔥🔥 [收到网关广播] 🔥🔥🔥\n%s\n", string(message))
		}
	}()

	// ==========================================
	// 🚨 测试用例切换区 (取消注释对应的 payload)
	// ==========================================

	// 🧪 测试用例 A: 吐鲁番极热与用户偏好冲突
	
	// payload := map[string]interface{}{
	// 	"room_id": "test-room-001",
	// 	"destinations": []string{"新疆吐鲁番火焰山景区", "吐鲁番葡萄沟风景区"},
	// 	"constraints": map[string]interface{}{
	// 		"total_budget": "1000元",
	// 		"time_window":  "10:00 - 20:00",
	// 	},
	// 	"user_preferences": map[string]interface{}{
	// 		"user_A (王柄淞)": "我就喜欢中午去火焰山感受极热，这才是旅游的意义！体力极好。",
	// 		"user_B (吴汶珂)": "非常怕热，一点太阳都不能晒，强烈要求多在葡萄沟吃西瓜避暑。",
	// 	},
	// }
	

	// // 🧪 测试用例 B: 广州暴雨与室外执念冲突 (如需测试，取消下方注释，并注释掉上方的 payload)
	// /*
	 payload := map[string]interface{}{
	 	"room_id": "test-room-001",
	 	"destinations": []string{"广州长隆野生动物世界", "广东省博物馆"},
	 	"constraints": map[string]interface{}{
	 		"total_budget": "2000元",
			"time_window":  "09:00 - 18:00",
	 	},
	 	"user_preferences": map[string]interface{}{
	 		"user_A (王柄淞)": "无论如何都要去长隆看野生动物，就算下暴雨我也要去！",
	 		"user_B (吴汶珂)": "讨厌下雨天弄脏鞋子，如果是雨天必须全部安排在室内（如博物馆）。",
		},
	 }
	

	// ==========================================

	wsMsg := map[string]interface{}{
		"type":    "agent_negotiate",
		"room_id": "test-room-001",
		"user_id": "test_user_wbs",
		"payload": payload,
	}

	msgBytes, _ := json.Marshal(wsMsg)

	// 5. 发送消息
	log.Println("📤 正在发送极端环境测试请求，等待 AI 推演...")
	err = c.WriteMessage(websocket.TextMessage, msgBytes)
	if err != nil {
		log.Fatalf("发送消息失败: %v", err)
	}

	// 6. 阻塞主程序，留出时间让大模型思考并返回
	// 由于涉及多项 API 并发查询和 AI 长文本生成，建议稍微延长时间到 25 秒
	time.Sleep(25 * time.Second)
	log.Println("测试结束，断开连接。")
}