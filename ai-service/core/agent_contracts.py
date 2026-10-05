"""Structured contracts for specialist planning agents.

The coordinator may display natural-language explanations, but it must make
decisions from these validated structures. The models are deliberately small
and domain-neutral so transport, lodging, dining, budget, risk, and emergency
agents can share one protocol without coupling their internal implementations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=120)
    observed_at: datetime
    expires_at: Optional[datetime] = None
    estimated: bool = False
    uri: Optional[str] = Field(default=None, max_length=500)

    @field_validator("observed_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            raise ValueError("evidence_timestamp_requires_timezone")
        return value


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=120)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    source: str = Field(min_length=1, max_length=120)
    latency_ms: int = Field(default=0, ge=0)


class FailureInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False
    fallback: Optional[str] = Field(default=None, max_length=120)


class AgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=120)
    agent_id: str = Field(min_length=1, max_length=120)
    domain: str = Field(min_length=1, max_length=80)
    context: Dict[str, Any] = Field(default_factory=dict)
    hard_constraints: Dict[str, Any] = Field(default_factory=dict)
    soft_preferences: Dict[str, Any] = Field(default_factory=dict)
    allowed_tools: List[str] = Field(default_factory=list)
    timeout_ms: int = Field(default=3000, ge=100, le=120000)


class AgentCapability(BaseModel):
    """Public, secret-free description of one specialist responsibility."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=80)
    domain: str = Field(min_length=1, max_length=80)
    responsibility: str = Field(min_length=1, max_length=300)
    input_contract: str = Field(default="AgentInput", max_length=80)
    output_contract: str = Field(default="AgentProposal", max_length=80)
    allowed_tools: List[str] = Field(default_factory=list)
    timeout_ms: int = Field(ge=100, le=120000)
    fallback: str = Field(min_length=1, max_length=160)
    evidence_required: bool = True
    metrics: List[str] = Field(default_factory=list)


class AgentProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=120)
    domain: str = Field(min_length=1, max_length=80)
    status: Literal["ok", "degraded", "failed"]
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
    hard_constraint_results: Dict[str, Any] = Field(default_factory=dict)
    # 协商回环用：这一版提案**按裁决去掉了什么**（例如 max_far_scenic 砍掉 3 个远景区）。
    # 必须有这个字段：模型是 extra="forbid"，没有它"重提"的痕迹会被契约丢掉，
    # 协商在审计上看起来就像什么都没发生。
    applied_constraints: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[EvidenceRef] = Field(default_factory=list)
    tool_calls: List[ToolCall] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    latency_ms: int = Field(default=0, ge=0)
    degraded: bool = False
    failure: Optional[FailureInfo] = None
    explanation: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("degraded")
    @classmethod
    def degraded_matches_status(cls, value: bool, info):
        status = info.data.get("status")
        if status == "degraded" and not value:
            raise ValueError("degraded_status_requires_degraded_flag")
        if status == "failed" and value:
            raise ValueError("failed_status_cannot_be_degraded")
        return value


def validate_agent_proposal(payload: Mapping[str, Any]) -> AgentProposal:
    """Parse and enforce coordinator-facing proposal invariants.

    A successful proposal needs at least one candidate and one evidence item;
    a degraded proposal must explain its fallback. This function is the single
    gate that coordinator code should call before Pareto/fairness selection.
    """

    proposal = AgentProposal.model_validate(payload)
    if proposal.status == "ok" and not proposal.candidates:
        raise ValueError("successful_proposal_requires_candidate")
    if proposal.status in {"ok", "degraded"} and not proposal.evidence:
        raise ValueError("proposal_requires_evidence")
    if proposal.status == "degraded" and proposal.failure is None:
        raise ValueError("degraded_proposal_requires_failure_info")
    if proposal.status == "failed" and proposal.failure is None:
        raise ValueError("failed_proposal_requires_failure_info")
    return proposal


def proposal_timestamp() -> datetime:
    """UTC timestamp helper for adapters creating EvidenceRef values."""

    return datetime.now(timezone.utc)


def capability_registry() -> List[AgentCapability]:
    """Canonical specialist matrix consumed by the coordinator and diagnostics."""

    raw = [
        ("route-planner", "路线规划", "itinerary", "生成城市、每日和景区三级路线", ["amap.poi.search", "provider.candidate.search"], 12000, "verified_candidate_pool", ["constraint_pass_rate", "daily_coverage"]),
        ("transport-agent", "交通", "transport", "组合跨城与市内交通并核验换乘约束", ["provider.transport.search", "amap.route.plan"], 8000, "official_channel_links", ["transfer_feasibility", "source_coverage"]),
        ("lodging-agent", "住宿", "lodging", "按每日路线、通勤和成员需求匹配住宿", ["provider.lodging.search", "amap.poi.search"], 8000, "area_level_recommendation", ["commute_minutes", "source_coverage"]),
        ("dining-agent", "餐饮", "dining", "按时间窗、位置、饮食限制和排队风险推荐餐饮", ["amap.poi.search", "provider.dining.search"], 6000, "sourced_poi_only", ["diet_constraint_pass_rate", "route_detour"]),
        ("budget-agent", "预算", "budget", "统一规划、预订和实际消费分类并预警", ["budget.ledger.read", "provider.price.search"], 3000, "unknown_price_excluded", ["priced_coverage", "overrun_probability"]),
        ("risk-agent", "风险", "risk", "聚合天气、交通、客流和安全变化", ["weather.snapshot", "crowd.snapshot", "risk.snapshot"], 5000, "last_verified_snapshot", ["freshness_seconds", "alert_precision"]),
        ("emergency-agent", "应急", "emergency", "将风险变化转为最小扰动重规划动作", ["risk.snapshot", "planning.replan"], 5000, "manual_confirmation", ["preserved_node_ratio", "replan_latency"]),
        ("culture-agent", "文化", "culture", "为文化节点提供有来源的背景与礼仪说明", ["knowledge.official.search"], 5000, "omit_unsourced_fact", ["citation_coverage"]),
        ("accessibility-agent", "无障碍", "accessibility", "核验无障碍入口、电梯、坡度和缓冲时间", ["accessibility.official.search", "amap.route.plan"], 5000, "mark_unverified", ["verified_facility_coverage"]),
        ("health-agent", "健康", "health", "根据体力、年龄和健康约束调整强度", ["profile.features.read", "weather.snapshot"], 3000, "conservative_pace", ["pace_constraint_pass_rate"]),
        ("green-agent", "绿色出行", "sustainability", "比较交通碳排并在可接受时间内给出替代项", ["carbon.calculate", "provider.transport.search"], 4000, "methodology_estimate", ["carbon_reduction", "method_coverage"]),
    ]
    return [
        AgentCapability(
            agent_id=agent_id,
            label=label,
            domain=domain,
            responsibility=responsibility,
            allowed_tools=tools,
            timeout_ms=timeout_ms,
            fallback=fallback,
            metrics=metrics,
        )
        for agent_id, label, domain, responsibility, tools, timeout_ms, fallback, metrics in raw
    ]
