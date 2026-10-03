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


def build_pool_runtime(*, attractions: Sequence[Mapping[str, Any]] = (), transport: Sequence[Mapping[str, Any]] = (), lodging: Sequence[Mapping[str, Any]] = (), dining: Sequence[Mapping[str, Any]] = (), risk: Sequence[Mapping[str, Any]] = (), budget: Sequence[Mapping[str, Any]] = ()) -> tuple[SpecialistRuntime, list[AgentInput]]:
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
    return SpecialistRuntime(handlers), requests
