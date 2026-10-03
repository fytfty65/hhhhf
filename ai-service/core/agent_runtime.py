"""Runtime for independently executed specialist planning agents.

The planner still owns the final decision, but specialist work is now explicit:
each handler receives a validated ``AgentInput`` and returns a structured
``AgentProposal``.  Handlers run concurrently, have individual timeouts, and
cannot silently turn malformed or unsourced output into a successful proposal.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Sequence

from .agent_contracts import AgentInput, AgentProposal, capability_registry, validate_agent_proposal
from .joint_optimization import rank_lodging_options  # 联合优化（P2）：住宿 ↔ 通勤/费用/体力/碳排/预算

AgentHandler = Callable[[AgentInput], Awaitable[Mapping[str, Any] | AgentProposal]]


def _evidence(provider: str, field: str, *, estimated: bool = False) -> dict[str, Any]:
    return {
        "field": field,
        "provider": provider or "local-candidate-pool",
        "observed_at": datetime.now(timezone.utc),
        "estimated": estimated,
    }


def pool_handler(agent_id: str, domain: str, *, provider: str, field: str) -> AgentHandler:
    """Create a deterministic adapter for a supplier-backed candidate pool."""

    async def handle(request: AgentInput) -> Mapping[str, Any]:
        candidates = request.context.get("candidates")
        rows = [item for item in candidates if isinstance(item, Mapping)] if isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes)) else []
        if not rows:
            return {
                "agent_id": agent_id,
                "domain": domain,
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence(provider, field)],
                "confidence": 0.0,
                "failure": {"code": "candidate_pool_empty", "message": "供应商没有返回可用候选", "retryable": True, "fallback": "official_channel_links"},
                "explanation": "候选池为空，未编造推荐。",
            }
        return {
            "agent_id": agent_id,
            "domain": domain,
            "status": "ok",
            "degraded": False,
            "candidates": [dict(item) for item in rows[:50]],
            "hard_constraint_results": request.hard_constraints,
            "evidence": [_evidence(provider, field, estimated=any(bool(item.get("estimated")) for item in rows))],
            "confidence": 0.75 if rows else 0.0,
            "explanation": f"已从 {provider} 候选池读取 {len(rows)} 条记录。",
        }

    return handle


# ---------------------------------------------------------------------------
# 注册表覆盖度：把"注册了但没接线"显性化
# ---------------------------------------------------------------------------
# 实测（2026-10）发现：`capability_registry()` 声明了 **11 个**智能体，但 `build_pool_runtime()`
# 只装配了 6 个 —— 注册表很丰满、运行时很骨感，而且**没有任何地方会提醒这件事**。
# 于是这里把覆盖度做成可查询的数据（诊断接口/测试都能读），新增注册项如果没被分类会立刻被测出来。
GREEN_AGENT_ID = "green-agent"
HEALTH_AGENT_ID = "health-agent"
EMERGENCY_AGENT_ID = "emergency-agent"

WIRED_AGENT_IDS = (
    "route-planner",
    "transport-agent",
    "lodging-agent",
    "dining-agent",
    "risk-agent",
    "budget-agent",
    GREEN_AGENT_ID,
    HEALTH_AGENT_ID,
    EMERGENCY_AGENT_ID,
)

# 已声明但**尚未接线**：必须写清原因（测试强制：注册表里出现的新 id 必须在此分类，不能默默漏掉）
DECLARED_NOT_WIRED: Dict[str, str] = {
    "culture-agent": "缺少可核实的知识来源；按注册表 fallback=omit_unsourced_fact，宁可不出提案也不编造",
    "accessibility-agent": "缺少官方无障碍数据来源；fallback=mark_unverified",
}


def registry_coverage() -> Dict[str, Any]:
    """声明 vs 实接：覆盖度与未接原因（供诊断接口与测试使用）。"""
    declared = [item.agent_id for item in capability_registry()]
    wired = [agent_id for agent_id in declared if agent_id in WIRED_AGENT_IDS]
    pending = [agent_id for agent_id in declared if agent_id not in WIRED_AGENT_IDS]
    return {
        "declared": declared,
        "declared_count": len(declared),
        "wired": wired,
        "wired_count": len(wired),
        "not_wired": pending,
        "coverage_ratio": round(len(wired) / len(declared), 3) if declared else 0.0,
        "reasons": {agent_id: DECLARED_NOT_WIRED.get(agent_id, "未分类（需要在 DECLARED_NOT_WIRED 里说明原因）") for agent_id in pending},
    }


def green_handler() -> AgentHandler:
    """绿色出行智能体：比较候选交通方式的碳排，给出可接受的低碳替代项。

    诚实口径（与项目一贯要求一致）：
    - 碳排是**方法学估算**：证据里必须 `estimated=True`，绝不冒充实测值；
    - 没有可用交通数据 / 方法学不可用时 → **degraded + FailureInfo**（fallback=methodology_estimate），
      不编造碳排数字；
    - 只在能算出碳排的候选之间比较；缺距离的候选直接跳过，而不是拿 0 当默认值。
    """

    async def handle(request: AgentInput) -> Mapping[str, Any]:
        options = request.context.get("transport_options") or request.context.get("candidates") or []
        rows = [item for item in options if isinstance(item, Mapping)] if isinstance(options, Sequence) and not isinstance(options, (str, bytes)) else []
        if not rows:
            return {
                "agent_id": GREEN_AGENT_ID,
                "domain": "sustainability",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("carbon-methodology", "carbon_kg", estimated=True)],
                "confidence": 0.0,
                "failure": {
                    "code": "transport_options_empty",
                    "message": "没有可比较的交通选项，无法给出低碳替代项",
                    "retryable": True,
                    "fallback": "methodology_estimate",
                },
                "explanation": "缺少交通数据，未编造碳排结论。",
            }
        try:
            from api.carbon_service import compute_footprint  # 懒导入：避免 core ↔ api 的导入环
        except Exception as exc:  # 方法学不可用也要如实说
            return {
                "agent_id": GREEN_AGENT_ID,
                "domain": "sustainability",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("carbon-methodology", "carbon_kg", estimated=True)],
                "confidence": 0.0,
                "failure": {
                    "code": "methodology_unavailable",
                    "message": f"碳排方法学不可用: {type(exc).__name__}",
                    "retryable": True,
                    "fallback": "methodology_estimate",
                },
                "explanation": "无法加载碳排方法学，未给出低碳建议。",
            }

        scored: list[dict[str, Any]] = []
        for item in rows:
            raw_distance = item.get("distance_km", item.get("distance"))
            try:
                distance = float(raw_distance)
            except (TypeError, ValueError):
                continue  # 缺距离就跳过：不拿 0 当默认值（0 公里意味着"零碳排"，那是假的）
            if distance <= 0:
                continue
            mode = str(item.get("mode") or "transit")
            try:
                footprint = compute_footprint([{"distance_km": distance, "mode": mode}])
                carbon = float(footprint.get("total_kg") or 0.0)
            except Exception:
                continue
            scored.append(
                {
                    "mode": mode,
                    "distance_km": distance,
                    "carbon_kg": round(carbon, 3),
                    "duration_minutes": item.get("duration_minutes"),
                }
            )

        if not scored:
            return {
                "agent_id": GREEN_AGENT_ID,
                "domain": "sustainability",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("carbon-methodology", "carbon_kg", estimated=True)],
                "confidence": 0.0,
                "failure": {
                    "code": "no_scorable_transport_option",
                    "message": "候选交通方式缺少可计算的里程，无法比较碳排",
                    "retryable": False,
                    "fallback": "methodology_estimate",
                },
                "explanation": "候选缺少里程字段，未编造碳排比较。",
            }

        best = min(scored, key=lambda item: item["carbon_kg"])
        worst = max(scored, key=lambda item: item["carbon_kg"])
        reduction = 0.0 if worst["carbon_kg"] <= 0 else round(1 - best["carbon_kg"] / worst["carbon_kg"], 3)
        return {
            "agent_id": GREEN_AGENT_ID,
            "domain": "sustainability",
            "status": "ok",
            "degraded": False,
            # 只把最优项作为候选：低碳是"提案"，是否采纳由协调器按时间/预算权衡
            "candidates": [best],
            "hard_constraint_results": request.hard_constraints,
            "evidence": [_evidence("carbon-methodology", "carbon_kg", estimated=True)],
            "confidence": 0.6,
            "explanation": (
                f"比较 {len(scored)} 种交通方式：最低 {best['mode']} {best['carbon_kg']}kg、"
                f"最高 {worst['mode']} {worst['carbon_kg']}kg，最多可减 {int(reduction * 100)}%（方法学估算）。"
            ),
        }

    return handle


def health_handler() -> AgentHandler:
    """健康 / 体力智能体：按成员画像与天气给出**每日强度上限与休息缓冲**建议。

    注册表定义：tools=[profile.features.read, weather.snapshot]，timeout=3000，
    fallback=conservative_pace，metrics=[pace_constraint_pass_rate]。

    诚实口径：
    - 这是**保守的顾问型建议**（不是医学结论），证据标 `estimated=True`；
    - 既没有画像约束、也拿不到每日节点数 → degraded + FailureInfo（fallback=conservative_pace），
      不猜"你体力很好"；
    - 只在有依据时下调强度，且把**依据写进 explanation**，便于用户核对。
    """

    async def handle(request: AgentInput) -> Mapping[str, Any]:
        context = request.context or {}
        profile = context.get("profile") if isinstance(context.get("profile"), Mapping) else {}
        weather = context.get("weather") if isinstance(context.get("weather"), Mapping) else {}
        raw_daily = context.get("daily_nodes")
        try:
            daily_nodes = int(raw_daily) if raw_daily is not None else None
        except (TypeError, ValueError):
            daily_nodes = None
        constraints = [str(item) for item in (profile.get("health_constraints") or []) if str(item).strip()]
        condition = str(weather.get("condition") or "")

        if daily_nodes is None and not constraints:
            return {
                "agent_id": HEALTH_AGENT_ID,
                "domain": "health",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("health-methodology", "daily_intensity", estimated=True)],
                "confidence": 0.0,
                "failure": {
                    "code": "insufficient_profile_and_schedule",
                    "message": "既没有成员健康约束，也没有每日节点数，无法给出强度建议",
                    "retryable": True,
                    "fallback": "conservative_pace",
                },
                "explanation": "缺依据，未凭空判断体力。",
            }

        reasons: list[str] = []
        limit = 5
        if daily_nodes is not None:
            baseline = min(6, max(1, daily_nodes))
            limit = baseline
            if daily_nodes > 6:
                limit = 5
                reasons.append(f"每日 {daily_nodes} 个节点偏密，建议压到 {limit} 个并留出休息")
        if condition:
            for marker, extra in (("雨", 1), ("雪", 1), ("高温", 1), ("大风", 1)):
                if marker in condition:
                    limit = max(1, limit - extra)
                    reasons.append(f"天气含「{marker}」，再降 {extra} 个节点并增加室内替代")
                    break
        if constraints:
            limit = max(1, min(limit, 4))
            reasons.append("成员存在健康约束（" + "、".join(constraints[:3]) + "），按保守节奏安排")

        candidate = {
            "daily_node_limit": limit,
            "rest_buffer_minutes": 30 if limit >= 4 else 45,
            "advice": "；".join(reasons) if reasons else "维持常规节奏",
            "constraints_considered": constraints,
        }
        return {
            "agent_id": HEALTH_AGENT_ID,
            "domain": "health",
            "status": "ok",
            "degraded": False,
            "candidates": [candidate],
            "hard_constraint_results": request.hard_constraints,
            "evidence": [_evidence("health-methodology", "daily_intensity", estimated=True)],
            "confidence": 0.55,
            "explanation": "保守顾问建议（非医学结论）：" + (candidate["advice"] or "维持常规节奏"),
        }

    return handle


def replan_scope(route: Sequence[Mapping[str, Any]], risk_event: Mapping[str, Any]) -> Dict[str, Any]:
    """最小扰动重规划：算出**受影响节点 / 保留节点 / 保留率**。

    这是"旅中闭环"的核心逻辑，做成纯函数以便离线单测（风险场景很难在真实数据上复现）。

    诚实与最小扰动原则：
    - 风险事件**必须给出范围**（`affected_nodes` 或 `affected_days`）。两者都没有 → `scope_unknown`，
      绝不"整条路线重生成"（那是最大的扰动，也是最容易掩盖问题的做法）；
    - 点名了具体节点时**只影响这些节点**，同一天的其它节点照常保留；
    - 保留率 = 保留节点 / 全部节点，用于验收"未受影响节点保留率 ≥ 70%"。
    """
    nodes = [dict(item) for item in route if isinstance(item, Mapping)]
    total = len(nodes)
    if total == 0:
        return {"status": "empty_route", "affected": [], "preserved": [], "preserved_ratio": 1.0, "action": None}

    raw_nodes = risk_event.get("affected_nodes") if isinstance(risk_event, Mapping) else None
    raw_days = risk_event.get("affected_days") if isinstance(risk_event, Mapping) else None
    named = {_normalize_name(item) for item in (raw_nodes or []) if str(item).strip()}
    day_set = set()
    for item in raw_days or []:
        try:
            day_set.add(int(item))
        except (TypeError, ValueError):
            continue

    if not named and not day_set:
        return {
            "status": "scope_unknown",
            "affected": [],
            "preserved": [node.get("name") for node in nodes],
            "preserved_ratio": 1.0,
            "action": None,
            "message": "风险事件没有给出受影响范围，拒绝整条路线重生成",
        }

    affected: List[Dict[str, Any]] = []
    preserved: List[str] = []
    for node in nodes:
        name_hit = _normalize_name(node.get("name")) in named if named else False
        day_hit = False
        if day_set and not named:
            try:
                day_hit = int(node.get("day")) in day_set
            except (TypeError, ValueError):
                day_hit = False
        if name_hit or day_hit:
            affected.append(node)
        else:
            preserved.append(str(node.get("name") or ""))

    ratio = round(len(preserved) / total, 3) if total else 1.0
    action = str(risk_event.get("recommended_action") or "").strip()
    if not action:
        # 没给动作就按影响范围推断（只影响少量节点 → 局部替换；整天受影响 → 顺延）
        action = "shift_day" if (day_set and not named) else "replace_affected_nodes"
    return {
        "status": "scoped",
        "affected": affected,
        "affected_names": [str(node.get("name") or "") for node in affected],
        "preserved": preserved,
        "preserved_ratio": ratio,
        "action": action,
    }


def _normalize_name(value: Any) -> str:
    return "".join(str(value or "").split()).lower()


def emergency_handler() -> AgentHandler:
    """应急智能体：把风险事件转成**最小扰动**的重规划动作提案。

    注册表定义：tools=[risk.snapshot, planning.replan]，timeout=5000，
    fallback=manual_confirmation，metrics=[preserved_node_ratio, replan_latency]。

    诚实口径：
    - 只在风险事件给出范围时才产出动作；范围未知 → degraded + FailureInfo，**绝不整条重生成**；
    - 提案里必须带上保留率与保留节点（用户与验收标准都要看这个数）；
    - 强调"需用户确认后才写入"（fallback=manual_confirmation）。
    """

    async def handle(request: AgentInput) -> Mapping[str, Any]:
        context = request.context or {}
        route = context.get("route") or []
        event = context.get("risk_event") if isinstance(context.get("risk_event"), Mapping) else None
        if not isinstance(route, Sequence) or isinstance(route, (str, bytes)) or not route:
            return {
                "agent_id": EMERGENCY_AGENT_ID,
                "domain": "emergency",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("risk.snapshot", "affected_nodes")],
                "confidence": 0.0,
                "failure": {
                    "code": "route_missing",
                    "message": "没有当前路线，无法判断受影响节点",
                    "retryable": True,
                    "fallback": "manual_confirmation",
                },
                "explanation": "缺当前路线，未做任何重规划。",
            }
        if not event:
            return {
                "agent_id": EMERGENCY_AGENT_ID,
                "domain": "emergency",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("risk.snapshot", "risk_event")],
                "confidence": 0.0,
                "failure": {
                    "code": "risk_event_missing",
                    "message": "没有风险事件，不触发重规划",
                    "retryable": True,
                    "fallback": "manual_confirmation",
                },
                "explanation": "没有风险事件时不动作（避免无谓扰动）。",
            }

        scope = replan_scope(route, event)
        if scope["status"] != "scoped":
            return {
                "agent_id": EMERGENCY_AGENT_ID,
                "domain": "emergency",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("risk.snapshot", "affected_nodes")],
                "confidence": 0.0,
                "failure": {
                    "code": scope["status"],
                    "message": scope.get("message") or "风险事件范围不可判定",
                    "retryable": False,
                    "fallback": "manual_confirmation",
                },
                "explanation": "范围不可判定时拒绝整条路线重生成，交人工确认。",
            }

        candidate = {
            "action": scope["action"],
            "affected_nodes": scope["affected_names"],
            "preserved_nodes": scope["preserved"],
            "preserved_ratio": scope["preserved_ratio"],
            "requires_user_confirmation": True,
            "risk": {
                "event": event.get("event") or event.get("type"),
                "severity": event.get("severity"),
            },
        }
        return {
            "agent_id": EMERGENCY_AGENT_ID,
            "domain": "emergency",
            "status": "ok",
            "degraded": False,
            "candidates": [candidate],
            "hard_constraint_results": request.hard_constraints,
            "evidence": [_evidence("risk.snapshot", "affected_nodes")],
            "confidence": 0.7,
            "explanation": (
                f"最小扰动重规划：受影响 {len(scope['affected_names'])} 个节点、"
                f"保留 {len(scope['preserved'])} 个（保留率 {int(scope['preserved_ratio'] * 100)}%），"
                f"动作 {scope['action']}，需用户确认后写入。"
            ),
        }

    return handle


def lodging_handler(
    *,
    route: Sequence[Mapping[str, Any]] = (),
    nights: int | None = None,
    budget_total: float | None = None,
) -> AgentHandler:
    """住宿智能体（**联合评估版**）：不再只看房价，而是把通勤/费用/体力/碳排/可玩节点一起算。

    与通用 pool_handler 的区别：候选会经过 `core.joint_optimization.rank_lodging_options`，
    每个保留的候选带上 `joint_evaluation`（通勤分钟、通勤费用、时间折价、换酒店损失、总成本、
    未知分项、可玩节点、体力、碳排、预算余额），并在 explanation 里说明取舍。

    诚实口径：
    - **没有路线**时不做通勤联合评估，如实说明"仅按候选池给出"，不假装算过；
    - 缺坐标的天会出现在 `unmeasured_days`，不臆造零通勤；
    - 联合评估失败 → degraded + FailureInfo（fallback=area_level_recommendation），不悄悄退回假结果。
    """

    async def handle(request: AgentInput) -> Mapping[str, Any]:
        candidates = request.context.get("candidates")
        rows = [dict(item) for item in candidates if isinstance(item, Mapping)] if isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes)) else []
        if not rows:
            return {
                "agent_id": "lodging-agent",
                "domain": "lodging",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("lodging.provider", "lodging_candidates")],
                "confidence": 0.0,
                "failure": {"code": "candidate_pool_empty", "message": "供应商没有返回可用住宿候选", "retryable": True, "fallback": "area_level_recommendation"},
                "explanation": "候选池为空，未编造推荐。",
            }
        if not route:
            return {
                "agent_id": "lodging-agent",
                "domain": "lodging",
                "status": "ok",
                "degraded": False,
                "candidates": rows[:50],
                "hard_constraint_results": request.hard_constraints,
                "evidence": [_evidence("lodging.provider", "lodging_candidates")],
                "confidence": 0.6,
                "explanation": f"已读取 {len(rows)} 个住宿候选；**没有路线，未做通勤联合评估**（仅按候选池给出）。",
            }

        try:
            ranking = rank_lodging_options(rows, route, nights=nights, budget_total=budget_total)
        except Exception as exc:
            return {
                "agent_id": "lodging-agent",
                "domain": "lodging",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("joint-optimization", "commute_minutes_total", estimated=True)],
                "confidence": 0.0,
                "failure": {"code": "joint_evaluation_failed", "message": f"联合评估失败: {type(exc).__name__}", "retryable": True, "fallback": "area_level_recommendation"},
                "explanation": "联合评估不可用，未给出住宿建议。",
            }

        by_name = {str(item.get("name") or ""): item for item in rows}
        kept = ranking["pareto"] or ranking["evaluated"]
        enriched: list[dict[str, Any]] = []
        for evaluated in kept:
            base = by_name.get(evaluated["lodging_name"])
            if base is None:
                continue
            merged = dict(base)
            merged["joint_evaluation"] = {
                key: evaluated[key]
                for key in (
                    "lodging_total", "lodging_price_unknown",
                    "commute_minutes_total", "commute_cost", "commute_time_value", "switch_penalty",
                    "total_cost_known", "unknown_components", "planned_nodes", "playable_nodes",
                    "unmeasured_days", "fatigue_index", "carbon_kg", "carbon_source",
                    "budget_remaining", "estimated",
                )
            }
            enriched.append(merged)

        tradeoffs = "；".join(ranking["tradeoffs"])
        return {
            "agent_id": "lodging-agent",
            "domain": "lodging",
            "status": "ok",
            "degraded": False,
            "candidates": enriched,
            "hard_constraint_results": request.hard_constraints,
            "evidence": [
                _evidence("lodging.provider", "lodging_candidates"),
                _evidence("joint-optimization", "commute_minutes_total", estimated=True),
            ],
            "confidence": 0.7,
            "explanation": (
                f"对 {len(rows)} 个住宿候选做了通勤联合评估，保留 {len(enriched)} 个（Pareto）。{tradeoffs}"
                f"（成本只统计已知分项；{ranking['note']}）"
            ),
        }

    return handle


class SpecialistRuntime:
    def __init__(self, handlers: Mapping[str, AgentHandler]):
        self.handlers = dict(handlers)
        self.capabilities = {item.agent_id: item for item in capability_registry()}

    async def _run_one(self, request: AgentInput) -> AgentProposal:
        capability = self.capabilities.get(request.agent_id)
        handler = self.handlers.get(request.agent_id)
        started = time.perf_counter()
        if capability is None:
            return AgentProposal(
                agent_id=request.agent_id, domain=request.domain, status="failed",
                failure={"code": "agent_not_registered", "message": "智能体未注册", "retryable": False},
                degraded=False, latency_ms=0,
            )
        if handler is None:
            return AgentProposal(
                agent_id=request.agent_id, domain=request.domain, status="degraded",
                failure={"code": "handler_unavailable", "message": capability.fallback, "retryable": True, "fallback": capability.fallback},
                degraded=True, evidence=[_evidence("runtime", "handler")], latency_ms=0,
            )
        try:
            raw = await asyncio.wait_for(handler(request), timeout=capability.timeout_ms / 1000)
            payload = raw.model_dump(mode="python") if isinstance(raw, AgentProposal) else dict(raw)
            payload["latency_ms"] = max(0, round((time.perf_counter() - started) * 1000))
            return validate_agent_proposal(payload)
        except asyncio.TimeoutError:
            return AgentProposal(
                agent_id=request.agent_id, domain=request.domain, status="degraded",
                failure={"code": "agent_timeout", "message": f"超过 {capability.timeout_ms}ms 超时预算", "retryable": True, "fallback": capability.fallback},
                degraded=True, evidence=[_evidence("runtime", "timeout")], latency_ms=capability.timeout_ms,
            )
        except Exception as exc:
            return AgentProposal(
                agent_id=request.agent_id, domain=request.domain, status="failed",
                failure={"code": "agent_failed", "message": str(exc)[:300], "retryable": True, "fallback": capability.fallback},
                degraded=False, evidence=[_evidence("runtime", "failure")],
                latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
            )

    async def execute(self, requests: Sequence[AgentInput]) -> Dict[str, Any]:
        proposals = await asyncio.gather(*(self._run_one(request) for request in requests))
        rows = [proposal.model_dump(mode="json") for proposal in proposals]
        return {
            "status": "ok" if all(item.status != "failed" for item in proposals) else "degraded",
            "proposals": rows,
            "completed": len(rows),
            "failed": sum(item.status == "failed" for item in proposals),
            "degraded": sum(item.status == "degraded" for item in proposals),
            "parallel": True,
        }


def build_pool_runtime(
    *,
    attractions: Sequence[Mapping[str, Any]] = (),
    transport: Sequence[Mapping[str, Any]] = (),
    lodging: Sequence[Mapping[str, Any]] = (),
    dining: Sequence[Mapping[str, Any]] = (),
    risk: Sequence[Mapping[str, Any]] = (),
    budget: Sequence[Mapping[str, Any]] = (),
    transport_options: Sequence[Mapping[str, Any]] = (),
    profile: Mapping[str, Any] | None = None,
    weather: Mapping[str, Any] | None = None,
    daily_nodes: int | None = None,
    route: Sequence[Mapping[str, Any]] = (),
    risk_event: Mapping[str, Any] | None = None,
    nights: int | None = None,
    budget_total: float | None = None,
) -> tuple[SpecialistRuntime, list[AgentInput]]:
    sources = {
        "route-planner": ("itinerary", attractions, "amap", "attraction_candidates"),
        "transport-agent": ("transport", transport, "transport.provider", "transport_candidates"),
        "lodging-agent": ("lodging", lodging, "lodging.provider", "lodging_candidates"),
        "dining-agent": ("dining", dining, "amap", "dining_candidates"),
        "risk-agent": ("risk", risk, "risk.snapshot", "risk_signals"),
        "budget-agent": ("budget", budget, "price.provider", "budget_candidates"),
    }
    handlers: dict[str, AgentHandler] = {}
    requests: list[AgentInput] = []
    for agent_id, (domain, candidates, provider, field) in sources.items():
        if agent_id == "lodging-agent":
            # 住宿不再只看房价：走联合评估（通勤/费用/体力/碳排/可玩节点/预算一起算）
            handlers[agent_id] = lodging_handler(
                route=route, nights=nights, budget_total=budget_total
            )
        else:
            handlers[agent_id] = pool_handler(agent_id, domain, provider=provider, field=field)
        requests.append(AgentInput(
            request_id=f"runtime-{agent_id}", agent_id=agent_id, domain=domain,
            context={"candidates": list(candidates)}, hard_constraints={},
            soft_preferences={}, allowed_tools=[],
        ))
    # 绿色出行智能体：基于碳排方法学独立提案（此前只在注册表里声明，从未被调度）
    handlers[GREEN_AGENT_ID] = green_handler()
    requests.append(AgentInput(
        request_id=f"runtime-{GREEN_AGENT_ID}", agent_id=GREEN_AGENT_ID, domain="sustainability",
        context={"transport_options": list(transport_options or transport)}, hard_constraints={},
        soft_preferences={}, allowed_tools=[],
    ))
    # 健康 / 体力智能体：按画像与天气给出保守强度建议
    handlers[HEALTH_AGENT_ID] = health_handler()
    requests.append(AgentInput(
        request_id=f"runtime-{HEALTH_AGENT_ID}", agent_id=HEALTH_AGENT_ID, domain="health",
        context={
            "profile": dict(profile or {}),
            "weather": dict(weather or {}),
            "daily_nodes": daily_nodes,
        },
        hard_constraints={}, soft_preferences={}, allowed_tools=[],
    ))
    # 应急智能体：把风险事件转成最小扰动重规划动作提案（旅中闭环的入口）
    handlers[EMERGENCY_AGENT_ID] = emergency_handler()
    requests.append(AgentInput(
        request_id=f"runtime-{EMERGENCY_AGENT_ID}", agent_id=EMERGENCY_AGENT_ID, domain="emergency",
        context={"route": list(route), "risk_event": dict(risk_event or {})}, hard_constraints={},
        soft_preferences={}, allowed_tools=[],
    ))
    return SpecialistRuntime(handlers), requests
