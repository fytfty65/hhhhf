"""提案分歧显式化：把"多智能体协商"从并行各算变成**看得见的博弈**。

为什么要这一层（2026-10-04）
--------------------------
专业智能体已经各自独立提案（9 个），事件流里也有 `candidate.proposed`，但那条流只记
"提了几个候选" —— **看不到谁和谁冲突、裁决凭什么这么定**。于是"协同"在审计上跟
"并行各算一遍"没有区别，出了问题也无法回答"你们当时分歧在哪"。

这一层只做三件事，全部基于**提案里已有的可观测信号**（绝不编造分歧）：
  1. `summarize_stances()`：每个智能体"站在什么立场"（远景区数量 / 每日强度上限 /
     低碳选项 / 候选是否为空…）、以及**谁没数据没参与**（如实列出，不假装协同过）；
  2. `detect_conflicts()`：只报能由这些信号直接推出的冲突（跑远 vs 体力、低碳 vs 自驾、
     有人还提了用户明说不要的类别…）；没有任何分歧就返回空列表；
  3. `arbitrate_conflicts()`：逐条给出裁决**决定 + 依据 + 由哪一层真正执行** ——
     决定必须指向真实存在的机制（例如"每天最多 1 个远景区"由 travel_feasibility 执行），
     不许只写一句漂亮话。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

SCHEMA = 1
FAR_KM = 40.0
CARBON_GAP_KG = 20.0  # 碳排差距多大才算"值得提一句"（避免把噪声当分歧）


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _candidates_of(proposal: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    rows = proposal.get("candidates")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _far_candidates(candidates: Sequence[Mapping[str, Any]]) -> List[str]:
    names: List[str] = []
    for row in candidates:
        distance: Any = row.get("distance_km")
        try:
            value = float(distance)
        except (TypeError, ValueError):
            value = None
        if (value is not None and value >= FAR_KM) or bool(row.get("out_of_town")):
            names.append(str(row.get("name") or ""))
    return [name for name in names if name]


def _health_limit(proposal: Mapping[str, Any]) -> Optional[int]:
    for row in _candidates_of(proposal):
        raw: Any = row.get("daily_node_limit")
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def _carbon_options(proposal: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    rows: List[Mapping[str, Any]] = []
    for row in _candidates_of(proposal):
        if row.get("carbon_kg") is not None:
            rows.append(row)
    return rows


def _nightly_price(row: Mapping[str, Any]) -> Optional[float]:
    """住宿一晚价格：高德给的是 `cost`/`cost_estimate`，我们自己的候选里也可能是 `price`。

    取不到就返回 None —— 不允许把"不知道房价"当成 0（那会让便宜酒店凭空胜出）。
    """
    for key in ("nightly_price", "price_per_night", "price", "cost_estimate", "cost"):
        value = row.get(key)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value)
        digits = ""
        for char in text:
            if char.isdigit() or char == ".":
                digits += char
            elif digits:
                break
        try:
            return float(digits) if digits else None
        except ValueError:
            continue
    return None


def _lodging_prices(proposal: Mapping[str, Any]) -> List[tuple[str, float]]:
    pairs: List[tuple[str, float]] = []
    for row in _candidates_of(proposal):
        price = _nightly_price(row)
        if price is None:
            continue
        pairs.append((str(row.get("name") or ""), price))
    return pairs


def summarize_stances(proposals: Any) -> Dict[str, Any]:
    """每个提案的立场 + 谁没参与（没有数据或无需处置）。"""
    stances: List[Dict[str, Any]] = []
    inactive: List[Dict[str, Any]] = []
    for proposal in proposals or []:
        if not isinstance(proposal, Mapping):
            continue
        agent_id = str(proposal.get("agent_id") or "unknown")
        domain = str(proposal.get("domain") or "")
        status = str(proposal.get("status") or "")
        candidates = _candidates_of(proposal)
        stance: Dict[str, Any] = {
            "agent_id": agent_id,
            "domain": domain,
            "status": status,
            "candidates": len(candidates),
            "confidence": proposal.get("confidence"),
        }
        signals: Dict[str, Any] = {}
        if agent_id == "route-planner":
            far = _far_candidates(candidates)
            signals["far_scenic_count"] = len(far)
            signals["far_scenic"] = far[:5]
            stance["prefers"] = "远景区" if far else ("市区候选" if candidates else "无候选")
        elif agent_id == "health-agent":
            limit = _health_limit(proposal)
            signals["daily_node_limit"] = limit
            advice = ""
            for row in candidates:
                advice = str(row.get("advice") or advice)
            signals["advice"] = advice[:120]
            stance["prefers"] = "保守强度" if (limit is not None and limit <= 4) else "常规强度"
        elif agent_id == "green-agent":
            carbon_rows = _carbon_options(proposal)
            values: List[float] = []
            for row in carbon_rows:
                carbon: Any = row.get("carbon_kg")
                try:
                    values.append(float(carbon))
                except (TypeError, ValueError):
                    continue
            signals["options"] = len(carbon_rows)
            signals["min_carbon_kg"] = round(min(values), 1) if values else None
            signals["max_carbon_kg"] = round(max(values), 1) if values else None
            signals["best_mode"] = str(_as_mapping(carbon_rows[0]).get("label") or "") if carbon_rows else ""
            stance["prefers"] = "低碳" if values else "无交通数据"
        elif agent_id == "dining-agent":
            stance["prefers"] = "本地餐饮" if candidates else "无候选"
        elif agent_id == "lodging-agent":
            stance["prefers"] = "住宿联合评估" if candidates else "无候选"
        elif agent_id == "emergency-agent":
            stance["prefers"] = "无待处置风险"
        stance["signals"] = signals
        if candidates:
            stances.append(stance)
        else:
            failure = _as_mapping(proposal.get("failure"))
            inactive.append({
                "agent_id": agent_id,
                "domain": domain,
                "status": status,
                "reason": str(failure.get("code") or proposal.get("explanation") or "没有提出任何候选")[:160],
            })
    return {"schema": SCHEMA, "stances": stances, "inactive": inactive}


def _looks_like_hotel(row: Mapping[str, Any]) -> bool:
    """这一条候选是不是住宿。

    真实误报（2026-10-04）：某次「禁止人文」的分歧里报出「瑞德假日酒店(巴州博物馆市政府店)」——
    它是**酒店**，但 `classify_poi_category` 的名字优先规则会先看到"博物馆"就判成 cultural。
    检测器必须按候选自身的住宿属性来分类，否则给用户报一条根本不存在的"你说了不要人文"。
    """
    if bool(row.get("is_hotel")):
        return True
    text = f"{row.get('name') or ''} {row.get('type') or ''}"
    return any(word in text for word in ("酒店", "民宿", "宾馆", "客栈", "旅馆", "住宿"))


def detect_conflicts(
    proposals: Any,
    *,
    forbidden_categories: Sequence[str] = (),
    distance_tolerance: str = "normal",
    classify=None,
    trip_days: Optional[int] = None,
    budget_total: Optional[float] = None,
    nights: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """只报能由提案信号直接推出的分歧；推不出就返回空列表（不编戏）。

    "跑远 vs 体力"的判据（2026-10-04 修正）：一天最多只能安排一个远景区，
    所以只要**候选里的远景区数量超过行程天数**，就必然有远景区排不进去 ——
    这才是可观测、可协商的分歧。旧的"体力上限 ≤4"判据几乎永不触发（默认每天 5-6 个节点），
    留着它只会让这一层看起来在工作。
    """
    rows = [item for item in (proposals or []) if isinstance(item, Mapping)]
    by_agent = {str(item.get("agent_id") or ""): item for item in rows}
    conflicts: List[Dict[str, Any]] = []

    # ① 跑远 vs 体力/天数：候选里的远景区比天数还多 → 必然有排不进去的
    route = by_agent.get("route-planner")
    health = by_agent.get("health-agent")
    if route:
        far = _far_candidates(_candidates_of(route))
        limit = _health_limit(health) if health else None
        days = None
        try:
            days = int(trip_days) if trip_days is not None else None
        except (TypeError, ValueError):
            days = None
        exceeds_days = days is not None and len(far) > max(1, days)
        exceeds_intensity = limit is not None and limit <= 4 and len(far) >= 2
        if len(far) >= 2 and (exceeds_days or exceeds_intensity):
            between = ["route-planner"] + (["health-agent"] if health else [])
            if exceeds_days:
                detail = (f"候选里有 {len(far)} 个远景区（{'、'.join(far[:3])}…），"
                          f"而行程只有 {days} 天、一天最多去一个 → 必然有排不进去的")
            else:
                detail = (f"路线侧有 {len(far)} 个远景区（{'、'.join(far[:3])}），"
                          f"体力侧建议每天最多 {limit} 个节点")
            conflicts.append({
                "code": "far_scenic_vs_daily_intensity",
                "between": between,
                "severity": "negotiable",
                "detail": detail,
                "evidence": {"far_scenic": far[:5], "trip_days": days, "daily_node_limit": limit},
            })

    # ② 低碳 vs 自驾：绿色出行给出了明显更低碳的选项，而候选里有自驾/包车
    green = by_agent.get("green-agent")
    if green:
        carbon_rows = _carbon_options(green)
        values: List[float] = []
        for row in carbon_rows:
            try:
                raw_carbon: Any = row.get("carbon_kg")
                values.append(float(raw_carbon))
            except (TypeError, ValueError):
                continue
        labels = " ".join(str(row.get("label") or "") for row in carbon_rows)
        if values and (max(values) - min(values)) >= CARBON_GAP_KG and any(word in labels for word in ("自驾", "包车", "开车")):
            conflicts.append({
                "code": "low_carbon_vs_driving",
                "between": ["green-agent", "transport-agent"],
                "severity": "negotiable",
                "detail": f"低碳选项 {min(values):.0f}kg 与自驾/包车 {max(values):.0f}kg 相差 "
                          f"{max(values) - min(values):.0f}kg",
                "evidence": {"min_carbon_kg": round(min(values), 1), "max_carbon_kg": round(max(values), 1)},
            })

    # ③ 预算 vs 房价：住宿总额占预算比重过高 → 住得下与花得起冲突（可协商：压晚价上限）
    lodging = by_agent.get("lodging-agent")
    if lodging and budget_total and nights:
        try:
            budget = float(budget_total)
            night_count = max(1, int(nights))
        except (TypeError, ValueError):
            budget, night_count = 0.0, 0
        prices = _lodging_prices(lodging)
        if budget > 0 and night_count > 0 and prices:
            cheapest_name, cheapest = min(prices, key=lambda item: item[1])
            heaviest_name, heaviest = max(prices, key=lambda item: item[1])
            share = (heaviest * night_count) / budget
            if share > 0.5:
                conflicts.append({
                    "code": "lodging_price_vs_budget",
                    "between": ["lodging-agent", "budget-agent"],
                    "severity": "negotiable",
                    "detail": (f"住 {night_count} 晚最贵要 ¥{heaviest * night_count:.0f}"
                               f"（{heaviest_name or '未命名'}，占预算 {share * 100:.0f}%），"
                               f"最便宜的 {cheapest_name or '未命名'} 是 ¥{cheapest:.0f}/晚"),
                    "evidence": {
                        "budget_total": budget,
                        "nights": night_count,
                        "cheapest_nightly": cheapest,
                        "heaviest_nightly": heaviest,
                        "share": round(share, 3),
                    },
                })

    # ④ 有人还提了用户明说不要的类别：这是**硬冲突**，必须由过滤器处理
    if forbidden_categories and callable(classify):
        offenders: List[Dict[str, Any]] = []
        for proposal in rows:
            agent_id = str(proposal.get("agent_id") or "")
            hit = []
            for row in _candidates_of(proposal):
                name = str(row.get("name") or "")
                poi_type = str(row.get("type") or "")
                try:
                    category = str(classify(name, poi_type, _looks_like_hotel(row)) or "")
                except Exception:
                    continue
                if category in set(forbidden_categories):
                    hit.append(name)
            if hit:
                offenders.append({"agent_id": agent_id, "names": hit[:5], "count": len(hit)})
        if offenders:
            conflicts.append({
                "code": "forbidden_category_in_proposals",
                "between": [item["agent_id"] for item in offenders],
                "severity": "hard",
                "detail": "有提案仍包含你明确排除的类别：" + "、".join(
                    f"{item['agent_id']}（{item['count']} 个）" for item in offenders
                ),
                "evidence": {"offenders": offenders},
            })

    # ④ 距离口径与"远景区很多"并存时，如实说明这是用户自己选的路（不是分歧，是口径）
    if distance_tolerance == "high":
        for conflict in conflicts:
            if conflict["code"] == "far_scenic_vs_daily_intensity":
                conflict["note"] = "你已经说了不介意跑远，所以这条按'一天最多一个远景区'处理，而不是取消远景区"
    return conflicts


def arbitrate_conflicts(
    conflicts: Sequence[Mapping[str, Any]],
    *,
    distance_tolerance: str = "normal",
    forbidden_categories: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """逐条给出裁决：决定 + 依据 + **由哪一层真正执行**（必须指向真实机制）。"""
    decisions: List[Dict[str, Any]] = []
    for conflict in conflicts or []:
        code = str(_as_mapping(conflict).get("code") or "")
        if code == "far_scenic_vs_daily_intensity":
            decisions.append({
                "code": code,
                "decision": "保留远景区，但**一天最多一个**，其余移出当天",
                "mechanism": "core/travel_feasibility.repair_day_overload（硬约束过滤阶段执行）",
                "why": "你明确表示不介意跑远" if distance_tolerance == "high" else "默认口径下优先保证跑得完",
            })
        elif code == "low_carbon_vs_driving":
            decisions.append({
                "code": code,
                "decision": "跨景区行程保留用车，同时在方案里标明碳排差异",
                "mechanism": "core/carbon_service（碳排标注）+ 交通审计",
                "why": "看自然景区通常必须用车，碳排让位于你的出行诉求，但如实标注",
            })
        elif code == "lodging_price_vs_budget":
            decisions.append({
                "code": code,
                "decision": "按预算给每晚房价设上限，超出的住宿候选换掉",
                "mechanism": "core/agent_runtime._apply_contract_limits(max_price_per_night) + 住宿联合评估",
                "why": "住得下也要花得起：先按你说的总预算把住宿压在合理区间",
            })
        elif code == "forbidden_category_in_proposals":
            for category in forbidden_categories:
                decisions.append({
                    "code": code,
                    "decision": f"把「{category}」类节点从路线里换掉或移出",
                    "mechanism": "core/route_composition.repair_forbidden_categories（硬约束过滤阶段执行）",
                    "why": "你明确说了不要这一类",
                })
                break
        else:
            decisions.append({
                "code": code or "unknown",
                "decision": "记录为待观察分歧",
                "mechanism": "未自动处置",
                "why": "这一条没有对应的自动机制，如实留给你判断",
            })
    return decisions


def build_revision_directives(
    conflicts: Sequence[Mapping[str, Any]],
    *,
    trip_days: Optional[int] = None,
    forbidden_categories: Sequence[str] = (),
) -> Dict[str, Dict[str, Any]]:
    """把裁决回灌成**可执行的指令**：谁要重提、按什么约束重提。

    这是"协商"与"协调器裁断"的分界线：只有真的把裁决变成相关智能体的新输入契约，
    并允许它们重提一轮，才叫协商；否则只是记了一条决定。
    指令必须是**可判定**的（数量/类别上限），不能是"请你更环保一些"这种没法执行的话。
    """
    directives: Dict[str, Dict[str, Any]] = {}
    for conflict in conflicts or []:
        entry = _as_mapping(conflict)
        code = str(entry.get("code") or "")
        if code == "far_scenic_vs_daily_intensity":
            try:
                limit = max(1, int(trip_days)) if trip_days is not None else 1
            except (TypeError, ValueError):
                limit = 1
            directives["route-planner"] = {
                "reason": "一天最多安排一个远景区",
                "hard_constraints": {"max_far_scenic": limit},
                "message": f"请按「一天最多一个远景区」重提：最多保留 {limit} 个远景区（优先留近的）",
            }
        elif code == "forbidden_category_in_proposals":
            for agent_id in entry.get("between") or []:
                directives[str(agent_id)] = {
                    "reason": "用户明确排除了该类别",
                    "hard_constraints": {},
                    "message": "请重提：不要包含用户明确排除的类别",
                }
        elif code == "lodging_price_vs_budget":
            evidence = _as_mapping(entry.get("evidence"))
            try:
                budget = float(evidence.get("budget_total") or 0.0)
                night_count = max(1, int(evidence.get("nights") or 1))
            except (TypeError, ValueError):
                budget, night_count = 0.0, 1
            cap = int(budget * 0.5 / night_count) if budget > 0 else 0
            directives["lodging-agent"] = {
                "reason": "住宿占预算比重过高",
                "hard_constraints": {"max_price_per_night": cap},
                "message": f"请按「每晚不超过 ¥{cap}」重提（住 {night_count} 晚、预算 ¥{budget:.0f} 的一半留给住宿）",
            }
        elif code == "low_carbon_vs_driving":
            directives.setdefault("transport-agent", {
                "reason": "低碳与自驾存在差异",
                "hard_constraints": {},
                "message": "请重提：标明各交通方式的碳排差异，供裁决参考",
            })
    return directives


def evaluate_revisions(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
    revised: Mapping[str, Mapping[str, Any]],
    *,
    round_index: int = 1,
) -> Dict[str, Any]:
    """协商一轮后的结果：分歧解掉了几条、还剩几条、谁真的重提了（含去掉了什么）。"""
    before_codes = {str(_as_mapping(item).get("code") or "") for item in before or []}
    after_codes = {str(_as_mapping(item).get("code") or "") for item in after or []}
    changed: List[Dict[str, Any]] = []
    for agent_id, proposal in (revised or {}).items():
        entry = _as_mapping(proposal)
        applied = _as_mapping(entry.get("applied_constraints"))
        if applied:
            changed.append({
                "agent_id": str(agent_id),
                "applied": applied,
                "candidates_after": len(_candidates_of(entry)),
            })
    return {
        "round": int(round_index),
        "revised_agents": sorted(str(key) for key in (revised or {})),
        "changed": changed,
        "conflicts_before": sorted(before_codes),
        "conflicts_after": sorted(after_codes),
        "resolved": sorted(before_codes - after_codes),
        "remaining": sorted(after_codes),
        "resolved_all": not after_codes,
        "notes": (
            ["本轮分歧全部解决"] if not after_codes and before_codes
            else ([f"本轮后仍有分歧：{'、'.join(sorted(after_codes))}"] if after_codes else [])
        ),
    }


def should_continue_negotiation(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
    *,
    round_index: int = 1,
    max_rounds: int = 2,
) -> Dict[str, Any]:
    """要不要再来一轮协商？三个停下来的理由都要说清楚（不许默默停）。

    为什么必须做收敛判断（2026-10-04）：
      固定只跑一轮的话，"重提后分歧没变"和"分歧解决了"在记录上长得一样；
      而无限重提又会把推演拖长。所以按**分歧是否真的减少**决定，并给硬上限：
        - 没有分歧了 → 停（已收敛）；
        - 达到轮次上限 → 停（交硬约束过滤兜底，并如实说明还有几条没消解）；
        - 重提后分歧**一条都没少** → 停（再提也没用，别空转）。
    """
    before_codes = {str(_as_mapping(item).get("code") or "") for item in before or []}
    after_codes = {str(_as_mapping(item).get("code") or "") for item in after or []}
    if not after_codes:
        return {"continue": False, "reason": "分歧已全部解决，协商结束", "code": "converged"}
    if int(round_index) >= max(1, int(max_rounds)):
        return {
            "continue": False,
            "reason": f"达到 {max_rounds} 轮上限，剩余分歧交硬约束过滤兜底",
            "code": "max_rounds",
        }
    if after_codes == before_codes:
        return {
            "continue": False,
            "reason": "重提后分歧一条都没减少，继续协商没有意义",
            "code": "no_progress",
        }
    return {
        "continue": True,
        "reason": f"分歧从 {len(before_codes)} 条减少到 {len(after_codes)} 条，再协商一轮",
        "code": "progress",
    }


def build_proposal_conflicts(
    proposals: Any,
    *,
    forbidden_categories: Sequence[str] = (),
    distance_tolerance: str = "normal",
    classify=None,
    trip_days: Optional[int] = None,
    budget_total: Optional[float] = None,
    nights: Optional[int] = None,
) -> Dict[str, Any]:
    """把三件事合成一条事实（立场 + 分歧 + 裁决），随方案下发并发事件。"""
    summary = summarize_stances(proposals)
    conflicts = detect_conflicts(
        proposals,
        forbidden_categories=forbidden_categories,
        distance_tolerance=distance_tolerance,
        classify=classify,
        trip_days=trip_days,
        budget_total=budget_total,
        nights=nights,
    )
    arbitration = arbitrate_conflicts(
        conflicts,
        distance_tolerance=distance_tolerance,
        forbidden_categories=forbidden_categories,
    )
    notes: List[str] = []
    if not conflicts:
        notes.append("这次各智能体没有显式分歧（不是没检查，是确实一致）")
    if summary["inactive"]:
        notes.append(
            "没有参与博弈的智能体：" + "、".join(item["agent_id"] for item in summary["inactive"])
        )
    return {
        "schema": SCHEMA,
        "stances": summary["stances"],
        "inactive": summary["inactive"],
        "conflicts": conflicts,
        "arbitration": arbitration,
        "notes": notes,
    }
