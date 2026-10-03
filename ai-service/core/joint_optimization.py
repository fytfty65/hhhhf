"""联合优化（P2）：把"住宿选择"与通勤 / 费用 / 体力 / 碳排 / 预算放进**同一个模型**评估。

用户目标里的原话（本模块就是它的实现）：
    住宿总成本 = 房价 + 每日通勤时间 + 通勤费用 + 换酒店损失
    "住宿位置变化后，应同步影响：每日通勤时间、交通费用、可游玩节点数量、成员体力消耗、
      碳排放、预算余额"

为什么单独做一层：现在的流水线是"先生成候选 → 再排序 → 再补点 → 最后修复"，各领域各算各的，
住宿换一个位置不会连锁影响通勤时间、可玩节点数与预算余额。这里把耦合关系显式写成模型，
并用 Pareto 保留"更便宜但更远 / 更近但更贵"这类各有取舍的方案，而不是压成单一分数。

诚实口径（与项目一贯要求一致）
- 缺价格 → 该分项记为 `unknown`（**绝不当作 0**，否则"未知"会变成"免费"），并计入 `unknown_components`；
- 缺坐标/距离 → 该天跳过并计入 `unmeasured_days`，不拿 0 当"零通勤"；
- 通勤费用与碳排都是**方法学估算**：系数全部显式写在常量里、输出标 `estimated=True`，
  不冒充实测；时间折价系数也要显式（不允许暗中把时间当钱）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

# —— 显式系数（都可在部署时按城市/季节调整，但必须显式，不能藏在代码里）——
COMMUTE_MINUTES_VALUE_YUAN = 1.2       # 每分钟通勤时间折算成多少钱（时间成本）
LODGING_SWITCH_PENALTY_YUAN = 120.0    # 换一次酒店的损失（收拾/退房/搬运/重新熟悉）
COMMUTE_COST_PER_KM_YUAN = 2.2         # 市内每公里出行的费用估算
COMMUTE_MINUTES_PER_KM = 3.0           # 市内出行每公里耗时估算（含等车/停车）
FATIGUE_PER_COMMUTE_MINUTE = 0.6       # 每分鐘通勤的体力消耗权重
FATIGUE_PER_PLAY_NODE = 6.0            # 每个游玩节点的体力消耗权重
# 通勤挤压游玩时间：超过该分钟数后，每 30 分钟减少 1 个可玩节点
PLAYABLE_TIME_TOLERANCE_MINUTES = 45

EARTH_RADIUS_KM = 6371.0
# 回退碳排系数（仅在 api.carbon_service 不可用时使用；输出里会标明用了哪一路来源）
FALLBACK_CARBON_KG_PER_KM = 0.12


def _carbon_factor_kg_per_km() -> tuple[float, str]:
    """每公里碳排系数（优先用 api.carbon_service 的方法学，不可用时回退并**标明来源**）。"""
    try:
        from api.carbon_service import compute_footprint  # 懒导入，避免 core → api 的导入环

        result = compute_footprint([{"distance_km": 1.0, "mode": "transit"}])
        value = float(result.get("total_kg") or 0.0)
        if value > 0:
            return value, "api.carbon_service"
    except Exception:
        pass
    return FALLBACK_CARBON_KG_PER_KM, "fallback_constant"


def haversine_km(a: Any, b: Any) -> Optional[float]:
    """两点球面距离（公里）。坐标非法返回 None —— 不拿 0 当"距离为零"。"""
    first = _coords(a)
    second = _coords(b)
    if not first or not second:
        return None
    lng1, lat1 = first
    lng2, lat2 = second
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lng2 - lng1)
    h = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return round(2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h)), 3)


def _coords(value: Any) -> Optional[tuple[float, float]]:
    from .node_contract import parse_coordinates  # 复用权威节点契约的坐标解析

    parsed = parse_coordinates(value)
    return (parsed[0], parsed[1]) if parsed else None


def _price(node: Mapping[str, Any]) -> Optional[float]:
    from .node_contract import parse_cost

    return parse_cost(node.get("cost", node.get("cost_estimate", node.get("price"))))


def _day_centroid(nodes: Sequence[Mapping[str, Any]]) -> Optional[List[float]]:
    """一天游玩节点的质心；无有效坐标返回 None。"""
    points = [_coords(node.get("lnglat", node.get("coordinates"))) for node in nodes if isinstance(node, Mapping)]
    valid = [point for point in points if point]
    if not valid:
        return None
    return [sum(item[0] for item in valid) / len(valid), sum(item[1] for item in valid) / len(valid)]


def group_by_day(route: Sequence[Mapping[str, Any]]) -> Dict[int, List[Mapping[str, Any]]]:
    days: Dict[int, List[Mapping[str, Any]]] = {}
    for node in route:
        if not isinstance(node, Mapping):
            continue
        try:
            day = int(node.get("day"))
        except (TypeError, ValueError):
            continue
        days.setdefault(day, []).append(node)
    return days


def evaluate_lodging(
    lodging: Mapping[str, Any],
    route: Sequence[Mapping[str, Any]],
    *,
    nights: Optional[int] = None,
    budget_total: Optional[float] = None,
) -> Dict[str, Any]:
    """评估一个住宿选择带来的**连锁影响**（通勤 / 费用 / 体力 / 碳排 / 可玩节点 / 预算）。"""
    days = group_by_day(route)
    nights = nights if nights is not None else max(1, len(days) - 1)
    lodging_coords = _coords(lodging.get("lnglat", lodging.get("coordinates")))
    nightly = _price(lodging)

    unknown: List[str] = []
    lodging_total: Optional[float] = None
    if nightly is None:
        unknown.append("lodging_price")          # 房价未知：不计入、也绝不当作 0
    else:
        lodging_total = round(nightly * nights, 2)

    commute_minutes = 0.0
    commute_cost = 0.0
    carbon_kg = 0.0
    unmeasured_days: List[int] = []
    playable_nodes = 0
    planned_nodes = 0

    carbon_factor, carbon_source = _carbon_factor_kg_per_km()

    for day in sorted(days):
        nodes = days[day]
        centroid = _day_centroid(nodes)
        distance = haversine_km(lodging_coords, centroid) if (lodging_coords and centroid) else None
        if distance is None:
            unmeasured_days.append(day)
            planned_nodes += len(nodes)
            playable_nodes += len(nodes)  # 无法测量就不臆造惩罚
            continue
        minutes = round(distance * COMMUTE_MINUTES_PER_KM * 2, 1)  # 去+回
        commute_minutes += minutes
        commute_cost += distance * 2 * COMMUTE_COST_PER_KM_YUAN
        carbon_kg += distance * 2 * carbon_factor
        planned_nodes += len(nodes)
        squeezed = max(0, minutes - PLAYABLE_TIME_TOLERANCE_MINUTES)
        playable_nodes += max(0, len(nodes) - int(squeezed // 30))

    switch_penalty = LODGING_SWITCH_PENALTY_YUAN if lodging.get("is_switch") else 0.0
    time_value = round(commute_minutes * COMMUTE_MINUTES_VALUE_YUAN, 2)
    commute_cost = round(commute_cost, 2)
    total_known = round((lodging_total or 0.0) + commute_cost + time_value + switch_penalty, 2)
    fatigue = round(commute_minutes * FATIGUE_PER_COMMUTE_MINUTE + playable_nodes * FATIGUE_PER_PLAY_NODE, 1)

    remaining: Optional[float] = None
    if budget_total is not None:
        remaining = round(float(budget_total) - total_known, 2)

    return {
        "lodging_name": str(lodging.get("name") or ""),
        "nights": nights,
        "lodging_price_unknown": nightly is None,
        "lodging_total": lodging_total,
        "commute_minutes_total": round(commute_minutes, 1),
        "commute_cost": commute_cost,
        "commute_time_value": time_value,
        "switch_penalty": switch_penalty,
        "total_cost_known": total_known,          # 只含**已知**分项
        "unknown_components": unknown,
        "planned_nodes": planned_nodes,
        "playable_nodes": playable_nodes,
        "unmeasured_days": unmeasured_days,
        "fatigue_index": fatigue,
        "carbon_kg": round(carbon_kg, 3),
        "budget_remaining": remaining,
        "estimated": True,                        # 通勤费用/碳排/时间折价都是方法学估算
        "carbon_source": carbon_source,           # 标明碳排用的是服务方法学还是回退常量
        "methodology": {
            "commute_minutes_per_km": COMMUTE_MINUTES_PER_KM,
            "commute_cost_per_km_yuan": COMMUTE_COST_PER_KM_YUAN,
            "commute_minutes_value_yuan": COMMUTE_MINUTES_VALUE_YUAN,
            "switch_penalty_yuan": LODGING_SWITCH_PENALTY_YUAN,
        },
    }


def _dominates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """left 是否在**所有**目标上不差于 right，且至少一个更优。

    目标：总成本(已知部分)↓、通勤时间↓、体力↓、碳排↓、可玩节点↑。
    注意：成本含未知分项时不能声称"更便宜"——未知一律不参与比较（保持诚实）。
    """
    if left["unknown_components"] or right["unknown_components"]:
        # 有未知分项就只比其余维度，避免用"未知"当"便宜"
        pairs = [
            (left["commute_minutes_total"], right["commute_minutes_total"], True),
            (left["fatigue_index"], right["fatigue_index"], True),
            (left["carbon_kg"], right["carbon_kg"], True),
            (left["playable_nodes"], right["playable_nodes"], False),
        ]
    else:
        pairs = [
            (left["total_cost_known"], right["total_cost_known"], True),
            (left["commute_minutes_total"], right["commute_minutes_total"], True),
            (left["fatigue_index"], right["fatigue_index"], True),
            (left["carbon_kg"], right["carbon_kg"], True),
            (left["playable_nodes"], right["playable_nodes"], False),
        ]
    better = False
    for left_value, right_value, lower_is_better in pairs:
        if lower_is_better and left_value > right_value:
            return False
        if not lower_is_better and left_value < right_value:
            return False
        if left_value != right_value:
            better = True
    return better


def rank_lodging_options(
    options: Sequence[Mapping[str, Any]],
    route: Sequence[Mapping[str, Any]],
    *,
    nights: Optional[int] = None,
    budget_total: Optional[float] = None,
) -> Dict[str, Any]:
    """对多个住宿选择做联合评估 + Pareto 筛选（保留各有取舍的方案，不压成单一分数）。"""
    evaluated = [evaluate_lodging(item, route, nights=nights, budget_total=budget_total) for item in options if isinstance(item, Mapping)]
    pareto: List[Dict[str, Any]] = []
    dominated_by: Dict[str, List[str]] = {}
    for candidate in evaluated:
        dominators = [
            other["lodging_name"]
            for other in evaluated
            if other is not candidate and _dominates(other, candidate)
        ]
        if dominators:
            dominated_by[candidate["lodging_name"]] = dominators
        else:
            pareto.append(candidate)

    # 交易说明：Pareto 集合里最便宜 / 最省通勤 / 碳最低 各是谁（给"为什么保留"留证据）
    tradeoffs: List[str] = []
    if pareto:
        cheapest = min(pareto, key=lambda item: item["total_cost_known"])
        closest = min(pareto, key=lambda item: item["commute_minutes_total"])
        greenest = min(pareto, key=lambda item: item["carbon_kg"])
        tradeoffs.append(f"最省：{cheapest['lodging_name'] or '(未命名)'}（已知成本 ¥{cheapest['total_cost_known']}）")
        tradeoffs.append(f"最少通勤：{closest['lodging_name'] or '(未命名)'}（{closest['commute_minutes_total']} 分钟）")
        tradeoffs.append(f"碳排最低：{greenest['lodging_name'] or '(未命名)'}（{greenest['carbon_kg']} kg，方法学估算）")

    return {
        "evaluated": evaluated,
        "pareto": pareto,
        "dominated": dominated_by,
        "tradeoffs": tradeoffs,
        "note": "成本只统计已知分项；未知分项不参与'更便宜'的比较。",
    }


__all__ = [
    "COMMUTE_COST_PER_KM_YUAN",
    "COMMUTE_MINUTES_PER_KM",
    "COMMUTE_MINUTES_VALUE_YUAN",
    "LODGING_SWITCH_PENALTY_YUAN",
    "evaluate_lodging",
    "group_by_day",
    "haversine_km",
    "rank_lodging_options",
]
