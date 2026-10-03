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
from typing import Any, Awaitable, Callable, Dict, Mapping, Sequence

from .agent_contracts import AgentInput, AgentProposal, capability_registry, validate_agent_proposal

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

WIRED_AGENT_IDS = (
    "route-planner",
    "transport-agent",
    "lodging-agent",
    "dining-agent",
    "risk-agent",
    "budget-agent",
    GREEN_AGENT_ID,
)

# 已声明但**尚未接线**：必须写清原因（测试强制：注册表里出现的新 id 必须在此分类，不能默默漏掉）
DECLARED_NOT_WIRED: Dict[str, str] = {
    "emergency-agent": "属旅中执行闭环：replan 端点已存在，但推演阶段尚未调度（P4 一起做）",
    "culture-agent": "缺少可核实的知识来源；按注册表 fallback=omit_unsourced_fact，宁可不出提案也不编造",
    "accessibility-agent": "缺少官方无障碍数据来源；fallback=mark_unverified",
    "health-agent": "待接 profile.features.read + weather.snapshot 后再调度",
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
    return SpecialistRuntime(handlers), requests
