"""统一事实记录（闭环第①段）：一次规划到底"依据了什么、决定了什么、缺了什么"。

为什么要单独一份（用户总目标里的"统一事实 → 独立提案 → 硬约束过滤 → …"第一段）
------------------------------------------------------------------------------
现在这些事实散在 `final_data` 的十几个字段里（candidate_supply / composition /
travel_feasibility / profile_applied / exclusion_repair / degradations / optimization_audit…），
每个消费方各取所需、各写一套推导 —— 于是"为什么给他排了这条路线"永远说不清，
跨智能体之间也没法基于**同一份事实**讨论（提案与裁决各看各的）。

这里把它们收成**一份带出处与核实状态的事实记录**，在一次规划里只发一次。

三条硬口径
----------
1. **未知 ≠ 0**：拿不到的数字写 `None` 并标 `unknown`，绝不写 0 冒充"没有"；
2. **每条带出处**：`source` 说明这个值从哪来（user_intent / amap / policy / learned_profile /
   derived / internal），`verified` 说明能不能当真；
3. **版本化**：`FACTS_SCHEMA_VERSION`，字段变化时消费方有据可依。

纯函数：不取数、不推断，输入全是已算好的对象；缺字段就如实标 unknown。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

FACTS_SCHEMA_VERSION = 1


def _fact(value: Any, source: str, *, verified: bool = True, note: str = "") -> Dict[str, Any]:
    """一条事实：值 + 出处 + 能不能当真（拿不到就 None/unknown）。"""
    entry = {"value": value, "source": source, "verified": bool(verified)}
    if note:
        entry["note"] = note
    return entry


def _unknown(note: str = "这次没有取到该事实") -> Dict[str, Any]:
    return _fact(None, "unknown", verified=False, note=note)


def _number(value: Any) -> Optional[float]:
    """能转成数字就转，转不了返回 None（不把"不知道"变成 0）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _gaps_from_degradations(degradations: Any, limit: int = 12) -> List[Dict[str, Any]]:
    """把降级登记转成"缺了什么"的事实清单（保留来源与原因，不合并、不美化）。"""
    items = _as_mapping(degradations).get("items")
    gaps: List[Dict[str, Any]] = []
    for item in list(items or [])[:limit]:
        entry = _as_mapping(item)
        gaps.append({
            "area": str(entry.get("source") or "unknown"),
            "status": str(entry.get("status") or "degraded"),
            "reason": str(entry.get("reason") or "")[:200],
        })
    return gaps


def build_plan_facts(
    *,
    correlation_id: str,
    city: str,
    trip_days: int,
    intent: str,
    signals: Mapping[str, Any],
    composition: Optional[Mapping[str, Any]] = None,
    supply: Optional[Mapping[str, Any]] = None,
    feasibility: Optional[Mapping[str, Any]] = None,
    profile_applied: Optional[Mapping[str, Any]] = None,
    reference_profile: Optional[Mapping[str, Any]] = None,
    route: Optional[Sequence[Mapping[str, Any]]] = None,
    degradations: Any = None,
    budget_total: Optional[float] = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """把一次规划的事实收成一份记录（纯函数）。"""
    signals = signals if isinstance(signals, Mapping) else {}
    composition = _as_mapping(composition)
    supply = _as_mapping(supply)
    feasibility = _as_mapping(feasibility)
    profile_applied = _as_mapping(profile_applied)
    reference_profile = _as_mapping(reference_profile)
    nodes = [node for node in (route or []) if isinstance(node, Mapping)]

    # —— 请求：用户给的（唯一权威来源就是他说的话）——
    request = {
        "city": _fact(str(city or ""), "user_intent"),
        "trip_days": _fact(int(trip_days) if _number(trip_days) is not None else None, "user_intent"),
        "intent_chars": _fact(len(str(intent or "")), "user_intent"),
        # 预算是从用户原话里解析出来的（用于"住得下也要花得起"这类协商）
        "budget_total": (
            _fact(_number(budget_total), "user_intent")
            if _number(budget_total) is not None
            else _unknown("用户没给预算，或没能从原话里解析出金额")
        ),
        "requirements": _fact(list(signals.get("requirements") or []), "user_intent"),
    }

    # —— 约束：用户明说的 + 系统策略（分开写，避免把"我们自己定的"说成"用户要求的"）——
    constraints = {
        "exclude_cultural": _fact(bool(signals.get("exclude_cultural")), "user_intent"),
        "distance_tolerance": _fact(str(signals.get("distance_tolerance") or "normal"), "user_intent"),
        "wants_food": _fact(bool(signals.get("wants_food")), "user_intent"),
        "wants_scenic": _fact(bool(signals.get("wants_scenic")), "user_intent"),
        "park_cap": _fact(
            (composition.get("park_cap") if isinstance(composition.get("park_cap"), int) else None),
            "policy",
            verified=True,
            note="城市公园每天上限（只想看自然时为 0）",
        ),
    }

    # —— 供应商：真实候选有多少、有没有限流/补检索（verified 表示"来自真实数据源"）——
    suppliers = {
        "amap_attractions": _fact(_number(supply.get("attractions")), "amap"),
        "amap_foods": _fact(_number(supply.get("foods")), "amap"),
        "natural_landmarks": _fact(_number(supply.get("scenic")), "amap"),
        "supply_status": _fact(str(supply.get("status") or "unknown"), "derived", verified=bool(supply)),
        "out_of_town_scenic": _fact(_number(supply.get("out_of_town_scenic")), "amap"),
        "widened": _fact(list(supply.get("widened") or []), "policy", note="候选太薄时补检索的类别"),
    }
    if not supply:
        suppliers = {key: _unknown("本次没有候选供给记录") for key in suppliers}

    # —— 决策：目标配比、画像是否生效、参考路线是否参与 ——
    decisions = {
        "composition_targets": _fact(
            {
                "scenic": composition.get("scenic"),
                "cultural": composition.get("cultural"),
                "food": composition.get("food"),
                "hotel": composition.get("hotel"),
                "source": composition.get("source"),
            }
            if composition
            else None,
            "policy",
            verified=bool(composition),
            note="默认自然=人文各半并穿插美食住宿；用户明确偏好则加权",
        ),
        "profile_applied": _fact(
            {
                "source": profile_applied.get("source"),
                "tilted": profile_applied.get("tilted"),
                "feedback_count": profile_applied.get("feedback_count"),
                "category_boost": profile_applied.get("category_boost"),
            }
            if profile_applied
            else None,
            "learned_profile",
            verified=bool(profile_applied),
        ),
        "reference_profile_used": _fact(
            bool(reference_profile.get("applied")) if reference_profile else False,
            "user_input",
            verified=bool(reference_profile),
            note="用户给过的参考路线是否参与了这一轮",
        ),
    }

    # —— 结果：排出来是什么样（派生值，标明是算出来的）——
    actual = _as_mapping(composition.get("actual"))
    outcome = {
        "nodes": _fact(len(nodes), "derived"),
        "by_category": _fact(
            {
                "scenic_landmark": actual.get("landmark"),
                "city_park": actual.get("city_park"),
                "cultural": actual.get("cultural"),
                "food": actual.get("food"),
                "hotel": actual.get("hotel"),
            }
            if actual
            else None,
            "derived",
            verified=bool(actual),
            note="scenic 只数真景区；城市公园单列",
        ),
        "days": _fact(sorted({int(node.get("day") or 1) for node in nodes}), "derived"),
        "travel_violations": _fact(
            list(feasibility.get("violations") or [])[:5] if feasibility else None,
            "derived",
            verified=bool(feasibility),
        ),
    }

    gaps = _gaps_from_degradations(degradations)
    return {
        "schema_version": FACTS_SCHEMA_VERSION,
        "correlation_id": str(correlation_id or ""),
        "generated_at": str(generated_at or datetime.now(timezone.utc).isoformat()),
        "request": request,
        "constraints": constraints,
        "suppliers": suppliers,
        "decisions": decisions,
        "outcome": outcome,
        "gaps": gaps,
    }


def _value(facts: Mapping[str, Any], section: str, key: str) -> Any:
    entry = _as_mapping(_as_mapping(facts.get(section)).get(key))
    return entry.get("value") if isinstance(entry, Mapping) else None


def build_request_facts(
    *,
    correlation_id: str,
    city: str,
    trip_days: int,
    intent: str,
    signals: Mapping[str, Any],
    composition: Optional[Mapping[str, Any]] = None,
    budget_total: Optional[float] = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """**提案阶段**用的基础事实快照：请求 + 约束（+ 若有则目标配比）。

    为什么要有这一层：specialist 运行时此前拿到的 `hard_constraints` 是**空的** ——
    每个智能体自己看候选池，谁也不知道"用户说了不要博物馆""他不介意跑远"。
    于是"多智能体协同"实际上只是各算各的（提案与裁决各看各的），
    出了问题也无法回答"你们是基于同一份事实讨论的吗"。
    """
    facts = build_plan_facts(
        correlation_id=correlation_id,
        city=city,
        trip_days=trip_days,
        intent=intent,
        signals=signals,
        composition=composition,
        supply=None,
        feasibility=None,
        profile_applied=None,
        reference_profile=None,
        route=None,
        degradations=None,
        budget_total=budget_total,
        generated_at=generated_at,
    )
    facts["stage"] = "request"
    facts["facts_version"] = facts_version(facts)
    return facts


def facts_version(facts: Mapping[str, Any]) -> str:
    """事实版本：请求 + 约束的稳定摘要（同一版事实 → 同一个版本号）。

    做法简单但够用：把 request/constraints 规范化成 JSON 再取 sha1 前 8 位，
    前缀带 schema 版本。任何消费方都能用它回答"我们看的是同一版事实吗"。
    """
    data = facts if isinstance(facts, Mapping) else {}
    payload = {
        "schema_version": data.get("schema_version"),
        "request": {key: _as_mapping(entry).get("value") for key, entry in sorted(_as_mapping(data.get("request")).items())},
        "constraints": {key: _as_mapping(entry).get("value") for key, entry in sorted(_as_mapping(data.get("constraints")).items())},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
    return f"facts-v{data.get('schema_version') or FACTS_SCHEMA_VERSION}-{digest}"


def proposal_facts_alignment(proposals: Any, expected_version: str) -> Dict[str, Any]:
    """校验每个提案是否基于**同一版事实**（不基于就如实点名，不装作协同成功）。

    提案里的事实版本由 specialist 运行时从它的输入契约里带出来
    （`hard_constraint_results.facts_version`）；这里只做对齐检查。
    """
    expected = str(expected_version or "")
    mismatched: List[Dict[str, Any]] = []
    seen_versions: List[str] = []
    for proposal in proposals or []:
        if not isinstance(proposal, Mapping):
            continue
        constraints = _as_mapping(proposal.get("hard_constraint_results"))
        version = str(constraints.get("facts_version") or "")
        if version:
            seen_versions.append(version)
        if not version or version != expected:
            mismatched.append({
                "agent_id": str(proposal.get("agent_id") or "unknown"),
                "facts_version": version or "(未携带)",
            })
    return {
        "aligned": not mismatched,
        "expected": expected,
        "mismatched": mismatched,
        "checked": len(seen_versions),
        "versions": sorted(set(seen_versions)),
    }


def summarize_facts(facts: Mapping[str, Any]) -> Dict[str, Any]:
    """事实记录 → 事件流里那一条**摘要**（不塞整份 payload，事件流要保持轻）。"""
    data = facts if isinstance(facts, Mapping) else {}
    return {
        "schema_version": data.get("schema_version"),
        "facts_version": data.get("facts_version"),
        "city": _value(data, "request", "city"),
        "nodes": _value(data, "outcome", "nodes"),
        "supply_status": _value(data, "suppliers", "supply_status"),
        "gaps": len(list(data.get("gaps") or [])),
        "correlation_id": data.get("correlation_id"),
    }
