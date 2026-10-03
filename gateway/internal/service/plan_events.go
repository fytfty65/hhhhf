package service

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// newEventID 生成事件 id（与 ai-service 一样用 UUID 字符串，便于跨服务去重与追踪）。
func newEventID() string { return uuid.NewString() }

// 网关侧写事件流（与 ai-service `core/events.py` 的统一信封对齐）。
//
// 为什么要有这个文件：ai-service 的 `PENDING_WIRING` 台账里有一批类型（user.accepted /
// user.rejected / node.replaced / replan.confirmed / trip.visited）**只可能由网关发出** ——
// 用户在前端点"采纳/拒绝/确认"，网关执行落库。没有发点，事件流里就永远只有"规划开始/结束"，
// "用户有没有采纳、旅中到底改没改、有没有到访"全都查不到，回放与学习都没有料。
//
// 协议口径（与 Python 侧一致）：
//   - `correlation_id` 必填：没有它这条事件无法归属到任何一次规划；
//   - `version` 固定为 1；
//   - `occurred_at` 必须是带时区的 RFC3339；
//   - 同时保留旧字段镜像（type / room_id / payload / ts），
//     这样网关自己的 WS 桥（service/eventstream.go）与前端无需同时改。
//
// 降级策略：事件是审计旁路，**写不进去也只能记日志**，绝不允许影响主流程
// （与 ai-service `RedisEventStream.emit` 的"静默跳过"一致）。

// EventEnvelopeVersion 与 ai-service core/events.py 的 ENVELOPE_VERSION 对齐。
const EventEnvelopeVersion = 1

var (
	planEventClient     *redis.Client
	planEventClientOnce sync.Once
)

// PlanEventRedis 复用同一个客户端（Redis 未配置时返回 nil，表示"这次没有事件流"）。
func PlanEventRedis() *redis.Client {
	planEventClientOnce.Do(func() {
		url := strings.TrimSpace(os.Getenv("REDIS_URL"))
		if url == "" {
			return
		}
		options, err := redis.ParseURL(url)
		if err != nil {
			log.Printf("⚠️ [事件流] REDIS_URL 解析失败，网关侧事件停发: %v", err)
			return
		}
		planEventClient = redis.NewClient(options)
	})
	return planEventClient
}

// BuildPlanEvent 组装一条符合协议的信封（纯函数：时间与 event_id 由调用方传入，便于测试）。
func BuildPlanEvent(
	eventType, correlationID, producer, target, causationID, roomID string,
	payload map[string]any,
	occurredAt time.Time,
	eventID string,
) ([]byte, error) {
	eventType = strings.TrimSpace(eventType)
	correlationID = strings.TrimSpace(correlationID)
	producer = strings.TrimSpace(producer)
	if eventType == "" {
		return nil, fmt.Errorf("event_type_required")
	}
	if correlationID == "" {
		return nil, fmt.Errorf("correlation_id_required")
	}
	if producer == "" {
		return nil, fmt.Errorf("producer_required")
	}
	if occurredAt.IsZero() {
		return nil, fmt.Errorf("occurred_at_required")
	}
	if payload == nil {
		payload = map[string]any{}
	}
	envelope := map[string]any{
		"event_id":       eventID,
		"event_type":     eventType,
		"correlation_id": correlationID,
		"producer":       producer,
		"target":         target,
		"version":        EventEnvelopeVersion,
		"occurred_at":    occurredAt.UTC().Format(time.RFC3339Nano),
		"room_id":        roomID,
		"payload":        payload,
		// 旧字段镜像：网关 WS 桥与前端读的是这些
		"type": eventType,
		"ts":   occurredAt.UTC().UnixMilli(),
	}
	return json.Marshal(envelope)
}

// EmitPlanEvent 把一条网关侧事件写进全局事件流（best effort）。
func EmitPlanEvent(eventType, correlationID, producer, target, roomID string, payload map[string]any) {
	body, err := BuildPlanEvent(eventType, correlationID, producer, target, "", roomID, payload, time.Now().UTC(), newEventID())
	if err != nil {
		log.Printf("⚠️ [事件流] %s 未发出（协议不完整）: %v", eventType, err)
		return
	}
	client := PlanEventRedis()
	if client == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := client.XAdd(ctx, &redis.XAddArgs{
		Stream: eventStreamKey,
		MaxLen: 1000,
		Approx: true,
		Values: map[string]any{"data": string(body)},
	}).Err(); err != nil {
		log.Printf("⚠️ [事件流] %s 写入失败（不影响主流程）: %v", eventType, err)
	}
}

// CorrelationFromTripContent 从落库的行程内容里取 ai-service 盖的关联键。
//
// ai-service 在 final_route 里回填了 `correlation_id`（`plan-<hex>`），前端保存行程时
// 可能把它一起存进 TripPlan.Content（顶层或 final_data 里）。取到就用它 —— 这样网关侧
// 的"采纳/确认/到访"事件能和 ai-service 的"推演开始/结束"事件归到同一条轨迹上。
// 取不到就返回空串，由调用方退回 trip_id 并**如实标注** correlation_source。
func CorrelationFromTripContent(content string) string {
	trimmed := strings.TrimSpace(content)
	if trimmed == "" {
		return ""
	}
	var document map[string]any
	if err := json.Unmarshal([]byte(trimmed), &document); err != nil {
		return ""
	}
	if value, ok := document["correlation_id"].(string); ok && strings.TrimSpace(value) != "" {
		return strings.TrimSpace(value)
	}
	if finalData, ok := document["final_data"].(map[string]any); ok {
		if value, ok := finalData["correlation_id"].(string); ok && strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}
