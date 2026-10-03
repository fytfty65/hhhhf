package service

import (
	"encoding/json"
	"testing"
	"time"
)

func TestBuildPlanEventEnvelope(t *testing.T) {
	occurred := time.Date(2026, 10, 3, 12, 0, 0, 0, time.UTC)
	body, err := BuildPlanEvent(
		"user.accepted", "plan-abc", "web-client", "coordinator", "", "room-1",
		map[string]any{"variant": "慢游方案"}, occurred, "evt-1",
	)
	if err != nil {
		t.Fatal(err)
	}
	var envelope map[string]any
	if err := json.Unmarshal(body, &envelope); err != nil {
		t.Fatal(err)
	}
	// 统一协议字段（与 ai-service core/events.py 对齐）
	for key, want := range map[string]any{
		"event_id":       "evt-1",
		"event_type":     "user.accepted",
		"correlation_id": "plan-abc",
		"producer":       "web-client",
		"target":         "coordinator",
		"version":        float64(EventEnvelopeVersion),
		"occurred_at":    "2026-10-03T12:00:00Z",
		"room_id":        "room-1",
	} {
		if envelope[key] != want {
			t.Fatalf("字段 %s 不对：got %#v want %#v", key, envelope[key], want)
		}
	}
	// 旧字段镜像：网关自己的 WS 桥（service/eventstream.go）读的是这些，不能丢
	if envelope["type"] != "user.accepted" {
		t.Fatalf("旧字段 type 镜像丢了：%#v", envelope["type"])
	}
	if _, ok := envelope["ts"].(float64); !ok {
		t.Fatalf("旧字段 ts 镜像丢了：%#v", envelope["ts"])
	}
}

func TestBuildPlanEventRejectsIncompleteProtocol(t *testing.T) {
	now := time.Now().UTC()
	cases := []struct {
		name                             string
		eventType, correlation, producer string
		occurred                         time.Time
	}{
		{"缺事件类型", "", "plan-1", "web-client", now},
		{"缺关联键", "user.accepted", "", "web-client", now},
		{"缺生产者", "user.accepted", "plan-1", "", now},
		{"缺时间", "user.accepted", "plan-1", "web-client", time.Time{}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := BuildPlanEvent(tc.eventType, tc.correlation, tc.producer, "coordinator", "", "", nil, tc.occurred, "evt"); err == nil {
				t.Fatal("协议不完整时必须报错，而不是发一条没人能归属的事件")
			}
		})
	}
}

func TestCorrelationFromTripContent(t *testing.T) {
	if got := CorrelationFromTripContent(`{"correlation_id":"plan-top"}`); got != "plan-top" {
		t.Fatalf("顶层关联键没取到：%q", got)
	}
	if got := CorrelationFromTripContent(`{"final_data":{"correlation_id":"plan-nested"}}`); got != "plan-nested" {
		t.Fatalf("final_data 里的关联键没取到：%q", got)
	}
	for _, content := range []string{"", "   ", "不是 JSON", `{"routes":[]}`, `{"correlation_id":"  "}`} {
		if got := CorrelationFromTripContent(content); got != "" {
			t.Fatalf("内容为 %q 时不该编出关联键，得到 %q", content, got)
		}
	}
}

// Redis 不可达时只记日志，绝不 panic 或阻塞主流程（事件是审计旁路）。
func TestEmitPlanEventDegradesWhenRedisIsUnreachable(t *testing.T) {
	t.Setenv("REDIS_URL", "redis://127.0.0.1:6399")
	EmitPlanEvent("trip.visited", "plan-1", "web-client", "coordinator", "room-1", map[string]any{"node_key": "1:西湖"})
}
