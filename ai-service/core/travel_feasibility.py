"""行程的**物理现实性**（硬约束过滤层的日内里程约束）。

为什么需要（2026-10-04 用户口径 + 真实方案实测）：
  用户说"不考虑距离、只想看自然景观"时，我们正确地放开了半径（400km 跨州），
  但下一版方案在 **3 天里同时排了博斯腾湖（47km）与巴音布鲁克大草原（约 270km）** ——
  这不是"不畏距离"，是物理上跑不动。距离容忍解决"愿不愿意去"，不解决"一天放不放得下"。

这一层只做两件事（纯函数，可离线单测）：
  1. `evaluate_travel_feasibility()`：按天算移动里程、数出"远景区"个数，给出违反项；
  2. `repair_day_overload()`：一天塞了多个远景区时，保留最近的那个，
     其余移出该天（**至少保留 1 个节点**），并如实说明移走了什么。

口径（可调，但必须有依据）：
  - 每天总移动里程上限：normal 160km / high 320km / low 80km；
  - 每天"远景区"（离市中心 ≥ 40km）最多 1 个 —— 哪怕用户不在乎距离，
    一天跑两个 200km 级的景区在时间上就是不成立；
  - 缺坐标的节点不参与里程计算，也不被当成"远"（宁可少报，也不凭空定罪）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

FAR_NODE_KM = 40.0  # 离市中心多远算"远景区"（库尔勒市区景点普遍 ≤5km，博斯腾湖 47km）

DAILY_BUDGET_KM = {"low": 80.0, "normal": 160.0, "high": 320.0}
DAILY_FAR_LIMIT = {"low": 0, "normal": 1, "high": 1}


def node_lnglat(node: Any) -> List[float]:
    """节点坐标 → [lng, lat]；取不到就返回空列表（**不当成 0,0**）。

    ⚠️ `(0, 0)` 是"几内亚湾海面上"，不是地点：数据源没给坐标时很容易写成 0/0，
    而它一旦进了里程计算，就会算出"第 2 天移动 9440km"这种荒唐结论
    （实测 2026-10-05：供应商候选没带坐标 → 被写成 [0,0] → 整天的里程与预算判断全废）。
    与前端 `_valid_pt`、照片兜底等处保持同一口径：**0,0 = 没有坐标**。
    """
    if not isinstance(node, Mapping):
        return []
    value = node.get("lnglat") or node.get("location") or node.get("coord")
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            lng, lat = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return []
        if lng == 0.0 and lat == 0.0:
            return []
        return [lng, lat]
    if isinstance(value, str) and "," in value:
        left, _, right = value.partition(",")
        try:
            lng, lat = float(left), float(right)
        except ValueError:
            return []
        if lng == 0.0 and lat == 0.0:
            return []
        return [lng, lat]
    return []


def haversine_km(origin: Sequence[float], destination: Sequence[float]) -> float:
    try:
        lon1, lat1 = float(origin[0]), float(origin[1])
        lon2, lat2 = float(destination[0]), float(destination[1])
    except (TypeError, ValueError, IndexError):
        return float("nan")
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    h = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def daily_budget_km(distance_tolerance: str = "normal") -> float:
    return DAILY_BUDGET_KM.get(str(distance_tolerance or "normal").strip().lower(), DAILY_BUDGET_KM["normal"])


def daily_far_limit(distance_tolerance: str = "normal") -> int:
    return DAILY_FAR_LIMIT.get(str(distance_tolerance or "normal").strip().lower(), DAILY_FAR_LIMIT["normal"])


def _days_of(route: Sequence[Mapping[str, Any]]) -> Dict[int, List[Mapping[str, Any]]]:
    grouped: Dict[int, List[Mapping[str, Any]]] = {}
    for index, node in enumerate(route or []):
        if not isinstance(node, Mapping):
            continue
        try:
            day = int(node.get("day") or 1)
        except (TypeError, ValueError):
            day = 1
        grouped.setdefault(day, []).append(node)
    return grouped


def day_travel_km(nodes: Sequence[Mapping[str, Any]], city_center: Optional[Sequence[float]] = None) -> float:
    """一天内的移动里程：市中心 → 各节点 → （回市中心），按节点原有顺序累加。

    缺坐标的相邻点直接跳过（不把"不知道"当成 0 距离，也不当成很远）。
    """
    points: List[Sequence[float]] = []
    if city_center and len(list(city_center)) >= 2:
        points.append(city_center)
    for node in nodes:
        coord = node_lnglat(node)
        if coord:
            points.append(coord)
    if len(points) < 2:
        return 0.0
    total = 0.0
    for index in range(1, len(points)):
        leg = haversine_km(points[index - 1], points[index])
        if leg == leg:  # 过滤 nan
            total += leg
    # 回程：以最后一天为例，多数行程晚上回市区/住景区，这里不强行加回程，
    # 只在"当天最远的点离市中心很远"时由远景区数量来约束（见 evaluate）。
    return round(total, 1)


def evaluate_travel_feasibility(
    route: Sequence[Mapping[str, Any]],
    *,
    city_center: Optional[Sequence[float]] = None,
    distance_tolerance: str = "normal",
    days: int = 1,
) -> Dict[str, Any]:
    """按天检查：总移动里程是否超预算、远景区是否超过每天 1 个。"""
    budget = daily_budget_km(distance_tolerance)
    far_limit = daily_far_limit(distance_tolerance)
    grouped = _days_of(route)
    report_days: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []

    for day in range(1, max(1, int(days or 1)) + 1):
        nodes = grouped.get(day, [])
        km = day_travel_km(nodes, city_center)
        far_nodes: List[Dict[str, Any]] = []
        for node in nodes:
            coord = node_lnglat(node)
            distance = haversine_km(city_center, coord) if (coord and city_center) else float("nan")
            if distance == distance and distance >= FAR_NODE_KM:
                far_nodes.append({
                    "name": str(node.get("name") or node.get("location") or ""),
                    "distance_km": round(float(distance), 1),
                })
        day_violations: List[str] = []
        if nodes and km > budget:
            day_violations.append("daily_travel_budget_exceeded")
            violations.append({
                "code": "daily_travel_budget_exceeded",
                "day": day,
                "level": "negotiable",
                "detail": f"第 {day} 天移动约 {km:.0f}km，超过当天预算 {budget:.0f}km",
            })
        if len(far_nodes) > far_limit:
            day_violations.append("too_many_far_scenic_in_one_day")
            names = "、".join(item["name"] for item in far_nodes)
            violations.append({
                "code": "too_many_far_scenic_in_one_day",
                "day": day,
                "level": "negotiable",
                "detail": f"第 {day} 天排了 {len(far_nodes)} 个远景区（{names}），一天跑不完",
            })
        report_days.append({
            "day": day,
            "nodes": len(nodes),
            "travel_km": km,
            "budget_km": budget,
            "far_nodes": far_nodes,
            "violations": day_violations,
        })

    notes = [item["detail"] for item in violations]
    return {
        "ok": not violations,
        "distance_tolerance": str(distance_tolerance or "normal"),
        "budget_km": budget,
        "far_limit": far_limit,
        "days": report_days,
        "violations": violations,
        "notes": notes,
    }


def repair_day_overload(
    route: Sequence[Mapping[str, Any]],
    *,
    city_center: Optional[Sequence[float]] = None,
    distance_tolerance: str = "normal",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把"一天里的多余远景区"移出该天，返回 `(新路线, 移走的节点)`。

    规则保守：远景区按"离市中心近→远"排序，保留最近的 `far_limit` 个，
    其余移出；**每天至少留 1 个节点**（宁可留一个远的，也不把一天清空）。
    """
    far_limit = daily_far_limit(distance_tolerance)
    grouped = _days_of(route)
    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []

    for day in sorted(grouped):
        nodes = list(grouped[day])
        decorated: List[Tuple[float, int, Mapping[str, Any]]] = []
        for index, node in enumerate(nodes):
            coord = node_lnglat(node)
            distance = haversine_km(city_center, coord) if (coord and city_center) else float("nan")
            decorated.append((distance if distance == distance else -1.0, index, node))
        far_items = sorted([item for item in decorated if item[0] >= FAR_NODE_KM], key=lambda item: item[0])

        if not city_center or len(far_items) <= far_limit:
            # 没有可比坐标，或远景区数量本来就没超 → 这一天不动
            kept.extend(dict(node) for node in nodes)
            continue

        keep_indexes = {item[1] for item in far_items[:far_limit]}
        day_kept: List[Dict[str, Any]] = []
        day_removed: List[Dict[str, Any]] = []
        for distance, index, node in decorated:
            if distance >= FAR_NODE_KM and index not in keep_indexes:
                day_removed.append({**dict(node), "removed_reason": "一天跑不了两个远景区"})
                continue
            day_kept.append(dict(node))
        if not day_kept and day_removed:
            # 兜底：一天不能清空 —— 把最近的那个远景区放回去（从 removed 里挪，不复制）
            day_kept.append({key: value for key, value in day_removed.pop(0).items() if key != "removed_reason"})
        kept.extend(day_kept)
        removed.extend(day_removed)

    return kept, removed
