"""预算分配与兜底（平替）引擎。

现状问题
--------
预算目前只是"事后上限校验"（`core/constraints.py` 的 `budget_upper`），而且 `api/agent.py`
只在 `budget_mode == "EXACT_AMOUNT"` 时才把预算真的传进约束；`planning_ranker.go` 对便宜只给
±0.05 微调，**全仓库没有"同义候选降档/替换"的搜索过程**，所以"预算不够"既不会自动降级，
也不会给用户一个可用的平替方案。

本模块做三件事
--------------
1) **价格三级**（用户 2026-09-17 决定：能取证就取证，不能取证就估算但要标明）：
   `verified`（可核来源）/ `estimated`（估算，必须标明）/ `unknown`（取不到）。
2) **预算报告**：`verified > 预算` → `over`（硬失败）；`verified ≤ 预算 < verified+estimated`
   → `at_risk`（可能超支，估算部分单列）；全为 unknown → `unverifiable`。
   **估算不参与"已验证结论"**，也不会把不够说成够。
3) **兜底动作序**（不动天数/城市）：① 玩点降档 → ② 餐饮降档 → ③ 减次要玩点 → ④ 换更近的点
   → ⑤ 住宿降档 → ⑥ 交通降级建议。"有吃有玩有住"的存在性最后才动；确实必须改天数/城市时
   **不自动执行**，返回 `needs_confirmation=True` 的提案 + 差异说明。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from .plan_quality import PREFERENCE_TAXONOMY, cost_of, node_lnglat, node_name, nodes_of
from .plan_quality import haversine_km
from .candidate_index import intent_of as _intent_of

VERIFIED_SOURCES = {
    "provider", "vendor", "amap", "rollinggo", "seniverse", "official", "ticket", "api", "supplier", "official_site",
}
ESTIMATED_SOURCES = {
    "estimated", "llm_estimate", "llm", "model", "inferred", "guess", "seed_template", "unverified", "unavailable", "",
}
# 兜底动作优先级（前面的先做，尽量少动结构）
FALLBACK_ORDER = ("tier_down_play", "tier_down_food", "drop_secondary_play", "swap_nearer", "lodging_downgrade", "transport_downgrade")


def _is_hotel(node: Mapping[str, Any]) -> bool:
    return _intent_of(node) == "hotel"


def _is_food(node: Mapping[str, Any]) -> bool:
    return _intent_of(node) == "food"


def price_of(node: Mapping[str, Any]) -> Dict[str, Any]:
    """单个节点的价格与**来源级别**。没有来源的价格一律降级为 estimated（不冒充已验证）。"""
    value = cost_of(node)
    sources = node.get("data_sources") if isinstance(node.get("data_sources"), Mapping) else {}
    source = ""
    for field in ("cost_estimate", "cost", "price"):
        if field in sources:
            source = str(sources.get(field) or "").strip().lower()
            break
    estimated = node.get("estimated") is True
    if value is None:
        return {"value": None, "tier": "unknown", "source": source or "missing", "name": node_name(node)}
    if source in VERIFIED_SOURCES and not estimated:
        return {"value": value, "tier": "verified", "source": source, "name": node_name(node)}
    return {
        "value": value,
        "tier": "estimated",
        "source": source or "no_source",
        "name": node_name(node),
    }


def budget_report(context: Mapping[str, Any], plan: Any) -> Dict[str, Any]:
    """预算可行性报告：分开统计"已验证"和"估算"，给出 risk 与置信度。"""
    nodes = nodes_of(plan)
    budget = 0.0
    raw_budget = context.get("budget") if isinstance(context, Mapping) else None
    try:
        budget = float(raw_budget or 0)
    except (TypeError, ValueError):
        budget = 0.0

    prices = [price_of(node) for node in nodes]
    verified = sum(p["value"] for p in prices if p["tier"] == "verified" and p["value"] is not None)
    estimated = sum(p["value"] for p in prices if p["tier"] == "estimated" and p["value"] is not None)
    unknown = [p["name"] for p in prices if p["tier"] == "unknown"]
    estimated_items = [p["name"] for p in prices if p["tier"] == "estimated"]

    if budget <= 0:
        status, shortfall = "no_budget", 0.0
    elif verified > budget:
        status, shortfall = "over", round(verified - budget, 2)
    elif verified + estimated > budget:
        status, shortfall = "at_risk", round(verified + estimated - budget, 2)
    elif verified == 0 and estimated == 0:
        # 一个价格都取不到：不能声称"预算内可行"
        status, shortfall = "unverifiable", 0.0
    else:
        status, shortfall = "ok", 0.0

    if not nodes or (verified == 0 and estimated == 0):
        confidence = "low"
    elif unknown or estimated:
        confidence = "medium"
    else:
        confidence = "high"

    return {
        "budget": budget,
        "verified_cost": round(verified, 2),
        "estimated_cost": round(estimated, 2),
        "unknown_count": len(unknown),
        "unknown_nodes": unknown[:8],
        "estimated_count": len(estimated_items),
        "estimated_nodes": estimated_items[:8],
        "status": status,
        "shortfall": shortfall,
        "confidence": confidence,
        "note": "estimated 部分为估算（已标明），不计入已验证结论；unknown 表示取不到价格。",
    }


def _requested_intents(context: Mapping[str, Any]) -> List[str]:
    preferences = context.get("preferences") if isinstance(context.get("preferences"), Mapping) else {}
    chunks: List[str] = []
    for source in (preferences, context.get("signals") or {}):
        if not isinstance(source, Mapping):
            continue
        for key in ("interest", "interests", "style", "tags", "requirements"):
            value = source.get(key)
            if isinstance(value, str):
                chunks.append(value)
            elif isinstance(value, Sequence):
                chunks.extend(str(item) for item in value)
    text = " ".join(chunks)
    intents = [label for label, keywords in PREFERENCE_TAXONOMY.items() if any(k in text for k in keywords)]
    return intents or ["scenic"]


def _intents_present(nodes: Sequence[Mapping[str, Any]]) -> List[str]:
    present = {_intent_of(node) for node in nodes}
    return sorted(present)


def _disclosure(shortfall_before: float, coverage: float, preserved_ratio: float, needs_confirmation: bool,
                actions: Sequence[Mapping[str, Any]]) -> str:
    parts: List[str] = []
    if shortfall_before > 0:
        parts.append(f"你的预算比'已验证花费'少约 ¥{shortfall_before:.0f}")
    real = [a for a in actions if a.get("saving")]
    suggestions = [a for a in actions if not a.get("saving")]
    if real:
        saved = sum(float(a.get("saving") or 0) for a in real)
        parts.append(
            f"我做了 {len(real)} 处平替（合计约省 ¥{saved:.0f}）：" + "；".join(str(a.get("reason") or "") for a in real[:4])
        )
    if suggestions:
        parts.append("另有建议（不自动改）：" + "；".join(str(a.get("reason") or "") for a in suggestions[:2]))
    parts.append(f"原始偏好保留率约 {preserved_ratio * 100:.0f}%")
    covered = min(1.0, coverage) * 100
    parts.append(f"预算覆盖度约 {covered:.0f}%")
    if needs_confirmation:
        parts.append("仍有缺口，且只能通过'改天数/换城市'才能满足——这属于结构性变更，我不会自动执行，需要你确认")
    return "；".join(parts)


def propose_fallback(
    context: Mapping[str, Any],
    plan: Any,
    shortfall: Optional[float] = None,
    candidates_by_intent: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    max_actions: int = 6,
) -> Dict[str, Any]:
    """在"不动天数/城市"的前提下给出兜底（平替）方案。

    candidates_by_intent：同义候选池（键为意图标签），每个候选形如
    {"name","price","price_tier","tier_level","lnglat"}；按价格从低到高比较。
    没有候选池时只给动作策略（不虚构替代品）——**绝不编造"某店替代某店"**。
    """
    nodes = [dict(node) for node in nodes_of(plan)]
    report = budget_report(context, plan)
    gap = float(shortfall if shortfall is not None else report["shortfall"])
    candidates_by_intent = candidates_by_intent or {}

    actions: List[Dict[str, Any]] = []
    substitutions: List[Dict[str, Any]] = []
    dropped: List[str] = []
    remaining = gap
    used_replacements: List[str] = []  # 已用过的替代不再复用（否则两个点会换成同一个地方）

    if gap > 0:
        # ① / ② / ⑤ 降档：同意图候选里找更便宜的
        for node in list(nodes):
            if remaining <= 0 or len(actions) >= max_actions:
                break
            intent = _intent_of(node)
            pool = sorted(
                [c for c in (candidates_by_intent.get(intent) or [])],
                key=lambda c: float(c.get("price") or 0),
            )
            current = price_of(node)
            if current["value"] is None:
                continue
            cheaper = next(
                (
                    c
                    for c in pool
                    if c.get("price") is not None
                    and float(c.get("price") or 0) < float(current["value"])
                    and str(c.get("name")) != node_name(node)
                    and str(c.get("name")) not in used_replacements
                ),
                None,
            )
            if not cheaper:
                continue
            used_replacements.append(str(cheaper.get("name")))
            saving = float(current["value"]) - float(cheaper["price"])
            tier = "lodging_downgrade" if intent == "hotel" else ("tier_down_food" if intent == "food" else "tier_down_play")
            action = {
                "kind": tier,
                "node": node_name(node),
                "from": node_name(node),  # 与 candidate_index.apply_exclusions 保持同一形状
                "to": str(cheaper.get("name")),
                "saving": round(saving, 2),
                "reason": f"把「{node_name(node)}」换成同类的「{cheaper.get('name')}」(约省 ¥{saving:.0f})",
                "target_tier": cheaper.get("price_tier") or cheaper.get("tier") or "cheaper",
            }
            actions.append(action)
            substitutions.append(action)
            remaining -= saving

        # ③ 减次要玩点（每天保留第一个玩点，其余按最贵优先削减）
        if remaining > 0 and len(actions) < max_actions:
            plays: List[Mapping[str, Any]] = [n for n in nodes if not _is_hotel(n) and not _is_food(n)]
            seen_days: Dict[int, int] = {}
            secondary = []
            for index, node in enumerate(plays, start=1):
                day = int(node.get("day") or 1)
                seen_days[day] = seen_days.get(day, 0) + 1
                if seen_days[day] > 1:
                    secondary.append(node)
            for node in sorted(secondary, key=lambda n: float(price_of(n)["value"] or 0), reverse=True):
                if remaining <= 0 or len(actions) >= max_actions:
                    break
                value = price_of(node)["value"]
                if value is None:
                    continue
                actions.append(
                    {
                        "kind": "drop_secondary_play",
                        "node": node_name(node),
                        "saving": round(float(value), 2),
                        "reason": f"删掉次要玩点「{node_name(node)}」(省 ¥{float(value):.0f})，保留当天核心玩点与全部正餐住宿",
                    }
                )
                dropped.append(node_name(node))
                remaining -= float(value)

        # ④ 换更近的点（只有候选池带坐标时才能算；用里程差近似节省）
        if remaining > 0 and len(actions) < max_actions:
            for node in nodes:
                if remaining <= 0 or len(actions) >= max_actions:
                    break
                here = node_lnglat(node)
                if here is None:
                    continue
                pool = candidates_by_intent.get(_intent_of(node)) or []
                nearer = None
                best = None
                for candidate in pool:
                    there = candidate.get("lnglat")
                    if not isinstance(there, (list, tuple)) or len(there) < 2:
                        continue
                    if str(candidate.get("name")) in used_replacements:
                        continue  # 已经被用作平替的地方，不再作为"更近的点"重复推荐
                    distance = haversine_km(here, (float(there[0]), float(there[1])))
                    if best is None or distance < best:
                        best, nearer = distance, candidate
                if nearer and best is not None and best > 0:
                    used_replacements.append(str(nearer.get("name")))
                    actions.append(
                        {
                            "kind": "swap_nearer",
                            "node": node_name(node),
                            "to": str(nearer.get("name")),
                            "saving": None,
                            "reason": f"把「{node_name(node)}」换成更近的「{nearer.get('name')}」(约 {best:.0f} km 内)，减少通勤花费与疲劳",
                        }
                    )

    needs_confirmation = remaining > 0 and gap > 0
    if gap > 0:
        actions.append(
            {
                "kind": "transport_downgrade",
                "node": "-",
                "saving": None,
                "reason": "把跨城段改为普速/公交或错峰出行（只给建议，不自动改）",
            }
        )

    preserved = _intents_present(nodes)
    requested = _requested_intents(context)
    kept = [intent for intent in requested if intent in preserved]
    preserved_ratio = len(kept) / len(requested) if requested else 1.0

    return {
        "trigger": "budget_shortfall" if gap > 0 else "none",
        "shortfall": round(gap, 2),
        "remaining_shortfall": round(max(0.0, remaining), 2),
        "actions": actions,
        "substitutions": substitutions,
        "dropped": dropped,
        "preserved_intents": kept,
        "dropped_intents": [intent for intent in requested if intent not in preserved],
        "preserved_ratio": round(preserved_ratio, 3),
        "needs_confirmation": needs_confirmation,
        "disclosure": _disclosure(gap, 1.0 - (max(0.0, remaining) / gap) if gap > 0 else 1.0, preserved_ratio, needs_confirmation, actions),
    }
