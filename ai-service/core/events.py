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
    # 规划主链路的里程碑事件：这 4 条**此前已经在发**，只是没登记 —— 于是走旧信封、没有
    # correlation_id，事件流成了"没法按一次规划串起来"的日志（审计/回放无从谈起）。
    # 由 tools/trace_plan.py 的审计需求倒逼登记。
    "negotiation_started": "推演开始",
    "reasoning_step": "推演推理步骤",
    "specialist_proposals_ready": "专业提案就绪",
    "final_route_ready": "行程生成完成",
}

# 缺口台账：**已登记但代码里还没有发点**的事件类型。
#
# 为什么要写出来而不是删掉：这些类型对应的是产品上真实需要的环节（谁提出的候选、谁被淘汰、
# 路线何时通过验证、用户何时采纳…），只是发点还没接。把它们显式登记成"待接线 + 归属 + 原因"，
# 比留在 EVENT_TYPES 里假装体系完整、或者直接删掉假装不需要，都要诚实。
# `tests/test_event_producers.py` 会强制：EVENT_TYPES == 代码里真有发点的类型 ∪ 本台账。
PENDING_WIRING: Dict[str, str] = {
    "trip.created": "coordinator｜推演入口已发 negotiation_started，语义重叠，需先定口径再发",
    "intent.parsed": "intent-agent｜需求解析目前内联在 api/agent.py，未独立成组件",
    "candidate.proposed": "各专业智能体｜specialist runtime 目前只汇总提案，未逐条发事件",
    "candidate.rejected": "validator｜淘汰原因目前只在 gate_audits 里，未进事件流",
    "constraint.failed": "validator｜硬约束失败目前只在约束核对面板里",
    "route.drafted": "route-planner｜草案生成阶段未发事件",
    "route.validated": "validator｜validate_route 结果只回填到 final_data.constraints",
    "route.repaired": "coordinator｜自动复核/长行程修补已实现，但未发事件",
    "route.selected": "coordinator｜裁决选出主方案时未发事件（多方案对比是真实存在的）",
    "user.accepted": "gateway｜用户在前端的采纳动作，属网关侧发点",
    "user.rejected": "gateway｜用户拒绝动作，属网关侧发点",
    "node.replaced": "gateway｜应用重规划替换节点时（ConfirmReplanHandler）未发事件",
    "replan.confirmed": "gateway｜确认重规划时未发事件（同一处可一并补）",
    "trip.visited": "gateway｜执行状态标记为已到访时未发事件",
}

# 事件生产者（producer）白名单。
#
# ⚠️ 事故记录（2026-10-03，由 tools/probe_event_stream.py 在真实 Redis 上抓出）：
# 这份名单原来是**手写**的，结果 `emergency-agent` 与 `route-planner` 没被登记 ——
# 它们发出的事件每次都触发 `unknown_producer`，`RedisEventStream.emit` 于是默默（只打一行
# 告警）退回旧信封，`correlation_id / causation_id / event_id` 全部丢失：**因果链在真实链路里
# 是断的，而单元测试全绿**（单测只测信封构造函数，不测"真实链路用了哪个 producer"）。
# 现在：智能体生产者**从能力注册表派生**，只有非智能体生产者需要手写 —— 以后新接智能体不会再漏。
INFRA_PRODUCERS = (
    "coordinator",
    "validator",
    "gateway",
    "web-client",
)

# 历史上登记过、当前没有对应智能体 id 的名字（旧日志/测试仍会引用，保留兼容）
LEGACY_PRODUCERS = ("intent-agent", "scenic-agent")

# 导入能力注册表失败时的兜底清单：宁可名单偏旧，也不能因为导入问题把事件链打断
_FALLBACK_AGENT_PRODUCERS = (
    "accessibility-agent",
    "budget-agent",
    "culture-agent",
    "dining-agent",
    "emergency-agent",
    "green-agent",
    "health-agent",
    "lodging-agent",
    "risk-agent",
    "route-planner",
    "transport-agent",
)


def _agent_producers() -> tuple[str, ...]:
    """智能体生产者 = 能力注册表里的 agent_id（单一事实来源，避免手写名单再次漏登记）。"""
    try:
        from .agent_runtime import capability_registry

        return tuple(sorted({item.agent_id for item in capability_registry()}))
    except Exception:
        return _FALLBACK_AGENT_PRODUCERS


def known_producers() -> tuple[str, ...]:
    return tuple(sorted(set(INFRA_PRODUCERS) | set(LEGACY_PRODUCERS) | set(_agent_producers())))


PRODUCERS = known_producers()

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
    if producer not in known_producers():
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
    if producer not in known_producers():
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
    "PENDING_WIRING",
    "PRODUCERS",
    "EventValidationError",
    "dedupe_key",
    "event_types",
    "known_producers",
    "new_event",
    "validate_event",
]
