"""统一事件协议（P0：可追踪 / 可重放 / 可审计的底座）。

为什么需要
----------
实测（2026-10）全仓库 grep `correlation_id` / `causation_id` **零命中**：事件信封只有
`{type, room_id, payload, ts}`。这意味着：
- 无法回答"这条事件属于哪一次规划请求"（缺 correlation_id）；
- 无法回答"是谁触发了谁"（缺 causation_id）；
- 无法安全演进（缺 version）；
- 因此**重放、审计、回滚、持续学习**都没有底座。

设计约束（很重要，避免把现有链路打断）
--------------------------------------
1. **向后兼容**：现有消费者（网关 WS 桥、前端）读的是 `type` / `room_id` / `payload` / `ts`。
   新信封**同时保留这四个镜像字段**，所以升级不需要同时改消费端。
2. **纯函数**：`new_event()` / `validate_event()` 不碰 IO、不读环境，可离线单测。
3. **事件类型白名单**：写错类型立刻报错，而不是悄悄写进流里（否则审计时会发现一堆野生类型）。
4. **幂等**：提供稳定的 `dedupe_key()`，供消费端做去重（至少一次投递 + 去重 = 效果上的恰好一次）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

# 事件类型白名单：与目标架构里列出的类型对齐。
# 新增类型必须在这里登记（并且要有对应的生产者在用），避免流里出现无法审计的野生事件。
EVENT_TYPES: Dict[str, str] = {
    "trip.created": "行程创建",
    "intent.parsed": "需求解析完成",
    "candidate.proposed": "候选被提出",
    "candidate.rejected": "候选被淘汰",
    "constraint.failed": "硬约束被违反",
    "route.drafted": "路线草案生成",
    "route.validated": "路线通过验证",
    "route.repaired": "路线被修复",
    "route.selected": "路线被选中",
    "user.accepted": "用户采纳",
    "user.rejected": "用户拒绝",
    "node.replaced": "节点被替换",
    "risk.changed": "风险变化",
    "replan.requested": "请求重规划",
    "replan.confirmed": "重规划已确认",
    "trip.visited": "实际到访",
}

# 事件生产者（producer）白名单：明确"谁发的"，避免审计时只能看到"ai-service"这种粗粒度
PRODUCERS = (
    "intent-agent",
    "scenic-agent",
    "transport-agent",
    "lodging-agent",
    "dining-agent",
    "budget-agent",
    "risk-agent",
    "culture-agent",
    "accessibility-agent",
    "health-agent",
    "green-agent",
    "coordinator",
    "validator",
    "gateway",
    "web-client",
)

ENVELOPE_VERSION = 1


class EventValidationError(ValueError):
    """事件不符合协议时抛出（宁可发不出去，也不要写进流里污染审计链）。"""


def new_event(
    event_type: str,
    payload: Optional[Mapping[str, Any]] = None,
    *,
    correlation_id: str,
    producer: str,
    target: str = "coordinator",
    causation_id: Optional[str] = None,
    room_id: str = "",
    evidence: Optional[Iterable[Mapping[str, Any]]] = None,
    confidence: Optional[float] = None,
    event_id: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    version: int = ENVELOPE_VERSION,
) -> Dict[str, Any]:
    """构造一条符合协议的事件信封（纯函数）。

    `correlation_id` 必填：它就是"这次规划/这段旅程"的关联键，没有它重放和审计都无从谈起。
    """
    if not str(correlation_id or "").strip():
        raise EventValidationError("correlation_id_required")
    if event_type not in EVENT_TYPES:
        raise EventValidationError(f"unknown_event_type:{event_type}")
    if producer not in PRODUCERS:
        raise EventValidationError(f"unknown_producer:{producer}")
    moment = occurred_at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise EventValidationError("occurred_at_requires_timezone")
    if confidence is not None and not (0.0 <= float(confidence) <= 1.0):
        raise EventValidationError("confidence_out_of_range")

    evidence_list: List[Dict[str, Any]] = [dict(item) for item in (evidence or [])]
    envelope: Dict[str, Any] = {
        "event_id": str(event_id or uuid.uuid4()),
        "correlation_id": str(correlation_id).strip(),
        "causation_id": str(causation_id) if causation_id else None,
        "event_type": event_type,
        "producer": producer,
        "target": str(target or "coordinator"),
        "version": int(version),
        "occurred_at": moment.astimezone(timezone.utc).isoformat(),
        "payload": dict(payload or {}),
        "evidence": evidence_list,
        "confidence": None if confidence is None else float(confidence),
    }
    # —— 向后兼容镜像：现有消费者（网关 WS 桥 / 前端）读的是这四个字段 ——
    envelope["type"] = event_type
    envelope["room_id"] = str(room_id or "")
    envelope["ts"] = int(moment.timestamp() * 1000)
    return envelope


def validate_event(envelope: Mapping[str, Any]) -> Dict[str, Any]:
    """校验一条事件是否满足协议；返回规范化后的副本。"""
    if not isinstance(envelope, Mapping):
        raise EventValidationError("envelope_must_be_mapping")
    for required in ("event_id", "correlation_id", "event_type", "producer", "occurred_at", "version"):
        if not str(envelope.get(required) or "").strip():
            raise EventValidationError(f"missing_required_field:{required}")
    event_type = str(envelope["event_type"])
    if event_type not in EVENT_TYPES:
        raise EventValidationError(f"unknown_event_type:{event_type}")
    producer = str(envelope["producer"])
    if producer not in PRODUCERS:
        raise EventValidationError(f"unknown_producer:{producer}")
    try:
        parsed = datetime.fromisoformat(str(envelope["occurred_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise EventValidationError("occurred_at_not_iso8601") from exc
    if parsed.tzinfo is None:
        raise EventValidationError("occurred_at_requires_timezone")
    confidence = envelope.get("confidence")
    if confidence is not None and not (0.0 <= float(confidence) <= 1.0):
        raise EventValidationError("confidence_out_of_range")
    return dict(envelope)


def dedupe_key(envelope: Mapping[str, Any]) -> str:
    """幂等去重键。

    用 `correlation_id + event_type + causation_id` 而不是 `event_id`：同一次请求里
    "同一因果分支上的同类事件"重复投递应当被去重（至少一次投递 + 去重 ≈ 恰好一次）。
    """
    return "|".join(
        [
            str(envelope.get("correlation_id") or ""),
            str(envelope.get("event_type") or ""),
            str(envelope.get("causation_id") or "-"),
        ]
    )


def event_types() -> List[Dict[str, str]]:
    """给文档/诊断接口用的事件类型清单。"""
    return [{"event_type": name, "label": label} for name, label in EVENT_TYPES.items()]


__all__ = [
    "ENVELOPE_VERSION",
    "EVENT_TYPES",
    "PRODUCERS",
    "EventValidationError",
    "dedupe_key",
    "event_types",
    "new_event",
    "validate_event",
]
