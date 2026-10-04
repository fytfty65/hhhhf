package service

import (
	"testing"
	"time"

	"gateway/internal/metrics"
	"gateway/internal/models"
)

// 广播背压回归锁。
//
// 真实事故（2026-10-04）：一次真实推演的逐字 token 流把客户端发送缓冲打满，
// Hub 静默 close(Send) 并把客户端踢出房间，**没有任何日志和指标**。
// 表现是"方案永远不来"，排查方向却全在模型和候选池上。
//
// 现在的口径：
//   - stream_token：只丢这条 + 计数，连接保留（token 只是逐字显示的装饰）；
//   - 结构性事件：如实断开并留痕（日志 + 指标），让"没收到方案"可查。

func newTestHub(t *testing.T) *Hub {
	t.Helper()
	hub := NewHub()
	go hub.Run()
	return hub
}

// roomMembers 通过对 Run 协程发查询来同步：返回时保证此前的广播已被处理。
func roomMembers(hub *Hub, roomID string) []map[string]string {
	resp := make(chan []map[string]string, 1)
	hub.MembersQuery <- membersQuery{roomID: roomID, resp: resp}
	select {
	case members := <-resp:
		return members
	case <-time.After(2 * time.Second):
		return nil
	}
}

func registerFullClient(t *testing.T, hub *Hub, roomID, userID string) *Client {
	t.Helper()
	client := &Client{Hub: hub, RoomID: roomID, UserID: userID, Send: make(chan models.WSMessage, 1)}
	client.Send <- models.WSMessage{Type: "filler"} // 先把缓冲占满，模拟消费不过来的客户端
	hub.Register <- client
	if members := roomMembers(hub, roomID); len(members) != 1 {
		t.Fatalf("注册后房间应有 1 名成员，实际 %d", len(members))
	}
	return client
}

func TestHubDropsStreamTokensWithoutKillingTheClient(t *testing.T) {
	hub := newTestHub(t)
	registerFullClient(t, hub, "room-token", "user-token")

	before := metrics.WSDropCount(metrics.WSDropStreamToken)
	hub.Broadcast <- models.WSMessage{Type: "stream_token", RoomID: "room-token", UserID: "system", Payload: "逐"}

	if members := roomMembers(hub, "room-token"); len(members) != 1 {
		t.Fatal("token 背压不该把客户端踢出房间 —— 否则最终方案永远送不到")
	}
	if after := metrics.WSDropCount(metrics.WSDropStreamToken); after != before+1 {
		t.Fatalf("token 丢弃必须计数（否则生产里看不见）: %d → %d", before, after)
	}
}

func TestHubDisconnectsSlowClientOnStructuralMessageAndCountsIt(t *testing.T) {
	hub := newTestHub(t)
	registerFullClient(t, hub, "room-struct", "user-struct")

	before := metrics.WSDropCount(metrics.WSDropSlowClient)
	hub.Broadcast <- models.WSMessage{Type: "final_route", RoomID: "room-struct", UserID: "system", Payload: map[string]any{"route": []any{}}}

	if members := roomMembers(hub, "room-struct"); len(members) != 0 {
		t.Fatal("结构性事件送不出去时应断开该客户端（保留一个已死的连接只会一直丢方案）")
	}
	if after := metrics.WSDropCount(metrics.WSDropSlowClient); after != before+1 {
		t.Fatalf("断开慢客户端必须计数: %d → %d", before, after)
	}
}

func TestHubBroadcastsToHealthyClientAndKeepsTokenFlow(t *testing.T) {
	hub := newTestHub(t)
	client := &Client{Hub: hub, RoomID: "room-ok", UserID: "user-ok", Send: make(chan models.WSMessage, 8)}
	hub.Register <- client
	if members := roomMembers(hub, "room-ok"); len(members) != 1 {
		t.Fatal("注册失败")
	}

	hub.Broadcast <- models.WSMessage{Type: "stream_token", RoomID: "room-ok", Payload: "你好"}
	hub.Broadcast <- models.WSMessage{Type: "final_route", RoomID: "room-ok", Payload: map[string]any{"status": "ok"}}

	// 注册本身会推一条成员列表更新（room_members_update），这里如实跳过，
	// 只关心 token 流与最终方案都真的送到了健康客户端。
	deadline := time.After(2 * time.Second)
	got := make([]string, 0, 3)
	sawFinal := false
	for !sawFinal {
		select {
		case msg := <-client.Send:
			got = append(got, msg.Type)
			if msg.Type == "final_route" {
				sawFinal = true
			}
		case <-deadline:
			t.Fatalf("健康客户端应收满 token 与 final_route，只收到 %v", got)
		}
	}
	if len(got) < 2 || got[len(got)-2] != "stream_token" || got[len(got)-1] != "final_route" {
		t.Fatalf("token 应先于 final_route 到达（顺序/类型不对）: %v", got)
	}
	if members := roomMembers(hub, "room-ok"); len(members) != 1 {
		t.Fatal("健康客户端不该被断开")
	}
}

func TestHubIgnoresUnknownRoom(t *testing.T) {
	hub := newTestHub(t)
	before := metrics.WSDropCount(metrics.WSDropSlowClient)
	hub.Broadcast <- models.WSMessage{Type: "final_route", RoomID: "room-missing", Payload: map[string]any{}}
	if after := metrics.WSDropCount(metrics.WSDropSlowClient); after != before {
		t.Fatal("没有成员的房间不该产生背压计数（否则指标会误导排查）")
	}
}
