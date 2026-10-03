package handlers

import (
	"strings"

	"gateway/internal/database"
	"gateway/internal/models"
	"gateway/internal/service"
)

// 网关侧的用户动作发点（补齐 ai-service 事件台账里"只可能由网关发出"的那几个类型）。
//
// 背景：事件流此前只有 ai-service 的"推演开始 / 生成完成"。用户到底有没有**采纳**、
// 有没有**拒绝/确认**重规划、旅中**改没改**节点、**到访**没有 —— 全都不在流里，
// 于是"回放一次真实决策过程"根本无从谈起。
//
// 约定（写进 payload，可被审计工具看到）：
//   - `correlation_source = "plan_content"`：关联键取自 ai-service 盖在行程内容里的
//     `plan-<hex>`，与推演事件属于同一条轨迹；
//   - `correlation_source = "trip_id"`：没取到（前端还没保存该字段），退回 trip_id ——
//     **如实标注**，而不是让两条不同口径的事件看起来像同一条链。
var emitPlanEvent = service.EmitPlanEvent

// correlationForTrip 决定这次动作挂哪个关联键。
func correlationForTrip(tripID string) (string, string) {
	tripID = strings.TrimSpace(tripID)
	if database.DB != nil && tripID != "" {
		var trip models.TripPlan
		if err := database.DB.Select("content").Where("id = ?", tripID).First(&trip).Error; err == nil {
			if correlation := service.CorrelationFromTripContent(trip.Content); correlation != "" {
				return correlation, "plan_content"
			}
		}
	}
	return tripID, "trip_id"
}

// emitTripActionEvent 统一入口：补齐关联键来源、trip/user 上下文，再交给事件流。
//
// roomID 说明：本项目的 trip_id 与 room_id 常常同值（AdoptVariantHandler 里就有
// `tripID = variant.RoomID` 的回退），所以缺省用 trip_id 作为 room_id —— 取不到真实房间时
// 只影响"房间内实时广播"，不影响事件流里的审计完整性。
func emitTripActionEvent(eventType, producer, tripID, userID, roomID string, payload map[string]any) {
	correlation, source := correlationForTrip(tripID)
	if payload == nil {
		payload = map[string]any{}
	}
	payload["trip_id"] = strings.TrimSpace(tripID)
	payload["user_id"] = strings.TrimSpace(userID)
	payload["correlation_source"] = source
	if strings.TrimSpace(roomID) == "" {
		roomID = strings.TrimSpace(tripID)
	}
	emitPlanEvent(eventType, correlation, producer, "coordinator", roomID, payload)
}
