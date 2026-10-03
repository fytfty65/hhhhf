"""Deterministic final gate for balanced multi-day itineraries.

The planner may replace a route during fairness selection, bandit exploration
or review.  This module runs after those stages and only uses existing route
nodes or supplier-backed pool rows; it never invents a place or a price.
"""

from __future__ import annotations

from collections import defaultdict
import math
from typing import Any, Dict, Mapping, Optional, Sequence


def _name(node: Mapping[str, Any]) -> str:
    return str(node.get("name") or node.get("location") or "").strip()


def _key(node: Mapping[str, Any]) -> str:
    return "".join(_name(node).lower().split())


def _category(node: Mapping[str, Any]) -> str:
    declared = str(node.get("planning_category") or "").strip().lower()
    if declared in {"scenic", "cultural", "food", "shopping", "hotel"}:
        return declared
    text = " ".join(
        [
            _name(node),
            str(node.get("type") or ""),
            " ".join(str(tag) for tag in (node.get("tags") or [])),
        ]
    ).lower()
    if node.get("is_hotel") or any(word in text for word in ("住宿", "酒店", "hotel", "民宿")):
        return "hotel"
    if any(word in text for word in ("餐", "美食", "小吃", "咖啡", "food", "restaurant")):
        return "food"
    if any(word in text for word in ("博物馆", "文化", "古迹", "历史", "展览", "cultural")):
        return "cultural"
    if any(word in text for word in ("商场", "市集", "购物", "shopping")):
        return "shopping"
    return "scenic"


def daily_balance_audit(route: Sequence[Mapping[str, Any]], days: int, target_daytime: int) -> Dict[str, Any]:
    by_day: Dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for node in route if isinstance(route, Sequence) else []:
        if isinstance(node, Mapping):
            try:
                day = int(node.get("day") or 1)
            except (TypeError, ValueError):
                day = 1
            if 1 <= day <= max(1, days):
                by_day[day].append(node)
    per_day: Dict[str, Dict[str, Any]] = {}
    for day in range(1, max(1, days) + 1):
        daytime = sum(1 for node in by_day.get(day, []) if _category(node) != "hotel")
        hotels = sum(1 for node in by_day.get(day, []) if _category(node) == "hotel")
        per_day[str(day)] = {
            "daytime": daytime,
            "hotel": hotels,
            "target_daytime": max(1, target_daytime),
            "satisfied": daytime >= max(1, target_daytime),
        }
    deficient = [int(day) for day, row in per_day.items() if not row["satisfied"]]
    counts = [row["daytime"] for row in per_day.values()]
    return {
        "days": per_day,
        "satisfied": not deficient,
        "deficient_days": deficient,
        "daytime_spread": max(counts) - min(counts) if counts else 0,
    }


def is_daily_balance_acceptable(route: Sequence[Mapping[str, Any]], days: int, min_daytime: int) -> bool:
    return bool(daily_balance_audit(route, days, min_daytime)["satisfied"])


def _pool_node(source: Mapping[str, Any], day: int) -> Dict[str, Any]:
    node = dict(source)
    node["day"] = day
    node["name"] = _name(source)
    node["location"] = _name(source)
    node["estimated"] = bool(source.get("estimated", True))
    node["trust_reason"] = "每日密度门禁补全，来自候选数据源"
    if source.get("planning_category") in {"scenic", "cultural", "food", "shopping", "hotel"}:
        node["planning_category"] = source.get("planning_category")
    if not node.get("cost_estimate"):
        node["cost_estimate"] = "暂无供应商数据"
    if not isinstance(node.get("data_sources"), Mapping):
        node["data_sources"] = {
            "cost_estimate": "unavailable",
            "open_time": "unavailable",
            "rating": "unavailable",
        }
    return node


def _retime_day(nodes: list[Dict[str, Any]], day: int) -> None:
    slots = ("08:30", "10:00", "11:30", "13:30", "15:00", "16:30", "18:00", "19:30")
    daytime = [node for node in nodes if _category(node) != "hotel"]
    hotels = [node for node in nodes if _category(node) == "hotel"]
    for index, node in enumerate(daytime):
        label = "用餐" if _category(node) == "food" else "游览"
        node["time"] = f"Day {day} | {slots[min(index, len(slots) - 1)]} {label}"
    for node in hotels:
        node["time"] = f"Day {day} | 21:30 入住"


def rebalance_daily_route(
    route: Sequence[Mapping[str, Any]],
    pool: Sequence[Mapping[str, Any]],
    *,
    days: int,
    target_daytime: int,
) -> Dict[str, Any]:
    """Fill and redistribute days without duplicating or fabricating POIs."""
    days = max(1, int(days))
    target_daytime = max(1, int(target_daytime))
    by_day: Dict[int, list[Dict[str, Any]]] = defaultdict(list)
    used: set[str] = set()
    for raw in route if isinstance(route, Sequence) else []:
        if not isinstance(raw, Mapping) or not _name(raw):
            continue
        key = _key(raw)
        if key in used:
            continue
        try:
            day = int(raw.get("day") or 1)
        except (TypeError, ValueError):
            day = 1
        if not 1 <= day <= days:
            continue
        used.add(key)
        by_day[day].append(dict(raw))

    available = [dict(item) for item in pool if isinstance(item, Mapping) and _name(item) and _key(item) not in used]
    added = 0
    for day in range(1, days + 1):
        nodes = by_day[day]
        while sum(_category(node) != "hotel" for node in nodes) < target_daytime:
            current = {_category(node) for node in nodes}
            preferred = next((item for item in available if _category(item) != "hotel" and _category(item) not in current), None)
            candidate = preferred or next((item for item in available if _category(item) != "hotel"), None)
            if candidate is None:
                break
            available.remove(candidate)
            used.add(_key(candidate))
            nodes.append(_pool_node(candidate, day))
            added += 1
        if not any(_category(node) == "hotel" for node in nodes):
            hotel = next((item for item in available if _category(item) == "hotel"), None)
            if hotel is not None:
                available.remove(hotel)
                used.add(_key(hotel))
                nodes.append(_pool_node(hotel, day))
                added += 1

    # If the pool is exhausted, redistribute surplus route nodes.  This keeps
    # every location unique and makes the last day no worse than the first.
    total_daytime = sum(sum(_category(node) != "hotel" for node in by_day[day]) for day in range(1, days + 1))
    achievable = min(target_daytime, total_daytime // days)
    remainder = max(0, total_daytime - achievable * days)
    desired = {day: achievable + (1 if day <= min(days, remainder) else 0) for day in range(1, days + 1)}
    moved = 0
    for target in range(1, days + 1):
        while sum(_category(node) != "hotel" for node in by_day[target]) < desired[target]:
            donor = next(
                (
                    day
                    for day in range(1, days + 1)
                    if day != target and sum(_category(node) != "hotel" for node in by_day[day]) > desired[day]
                ),
                None,
            )
            if donor is None:
                break
            index = next((i for i in range(len(by_day[donor]) - 1, -1, -1) if _category(by_day[donor][i]) != "hotel"), None)
            if index is None:
                break
            node = by_day[donor].pop(index)
            node["day"] = target
            by_day[target].append(node)
            moved += 1

    output: list[Dict[str, Any]] = []
    for day in range(1, days + 1):
        _retime_day(by_day[day], day)
        output.extend(by_day[day])
    audit = daily_balance_audit(output, days, target_daytime)
    audit.update({"added_from_pool": added, "moved_between_days": moved, "pool_remaining": len(available)})
    return {"route": output, "audit": audit}


def enforce_category_mix(
    route: Sequence[Mapping[str, Any]],
    pool: Sequence[Mapping[str, Any]],
    *,
    days: int,
    target_daytime: int,
    required_categories: Sequence[str] = ("scenic", "food", "cultural"),
    max_cultural_share: float = 0.4,
) -> Dict[str, Any]:
    """让普通城市游至少覆盖景区、餐饮、人文三类，不复制或编造地点。

    这是最终输出前的确定性门禁：模型可能已经给够了节点，但把所有白天节点
    都选成同一类（例如全是博物馆）。此处优先从同一供应商候选池替换重复类别，
    只有候选池没有可替换项时才保留原节点，并在 audit 中明确记录缺口。
    """
    days = max(1, int(days))
    target_daytime = max(1, int(target_daytime))
    required = tuple(dict.fromkeys(str(item) for item in required_categories if item))
    by_day: Dict[int, list[Dict[str, Any]]] = defaultdict(list)
    used: set[str] = set()
    for raw in route if isinstance(route, Sequence) else []:
        if not isinstance(raw, Mapping) or not _name(raw):
            continue
        key = _key(raw)
        if key in used:
            continue
        try:
            day = int(raw.get("day") or 1)
        except (TypeError, ValueError):
            day = 1
        if 1 <= day <= days:
            by_day[day].append(dict(raw))
            used.add(key)

    available = [
        dict(item) for item in pool
        if isinstance(item, Mapping) and _name(item) and _key(item) not in used
    ]

    def take(category: str) -> Optional[Dict[str, Any]]:
        for item in available:
            if _category(item) == category:
                available.remove(item)
                used.add(_key(item))
                return _pool_node(item, 1)
        return None

    replaced = 0
    added = 0
    unresolved: Dict[str, list[str]] = {}
    for day in range(1, days + 1):
        nodes = by_day[day]
        daytime = [node for node in nodes if _category(node) != "hotel"]
        present = {_category(node) for node in daytime}
        missing = [category for category in required if category not in present]

        # 先补齐缺失的基础体验。若已达到当天容量，替换一个重复的文化/其他节点，
        # 不额外膨胀用户请求的单日节点数。
        for category in missing:
            candidate = take(category)
            if candidate is None:
                unresolved.setdefault(str(day), []).append(category)
                continue
            if len(daytime) < target_daytime:
                candidate["day"] = day
                nodes.append(candidate)
                daytime.append(candidate)
                added += 1
            else:
                replace_index = next(
                    (
                        index for index in range(len(nodes) - 1, -1, -1)
                        if _category(nodes[index]) not in required or _category(nodes[index]) == "cultural"
                    ),
                    None,
                )
                if replace_index is None:
                    unresolved.setdefault(str(day), []).append(category)
                    available.append(candidate)
                    continue
                old = nodes[replace_index]
                old_key = _key(old)
                if old_key:
                    used.discard(old_key)
                candidate["day"] = day
                nodes[replace_index] = candidate
                daytime = [node for node in nodes if _category(node) != "hotel"]
                replaced += 1

        # 普通游不让文化场馆占满整天：在还有真实景区/餐饮候选时，替换尾部多余文化点。
        cap = max(1, int(math.ceil(max_cultural_share * max(1, len(daytime)))))
        while sum(_category(node) == "cultural" for node in daytime) > cap:
            replacement = take("scenic") or take("food") or take("shopping")
            if replacement is None:
                break
            index = next((i for i in range(len(nodes) - 1, -1, -1) if _category(nodes[i]) == "cultural"), None)
            if index is None:
                available.append(replacement)
                break
            replacement["day"] = day
            nodes[index] = replacement
            daytime = [node for node in nodes if _category(node) != "hotel"]
            replaced += 1

        by_day[day] = nodes

    output: list[Dict[str, Any]] = []
    for day in range(1, days + 1):
        _retime_day(by_day[day], day)
        output.extend(by_day[day])
    return {
        "route": output,
        "audit": {
            "required_categories": list(required),
            "added": added,
            "replaced": replaced,
            "unresolved": unresolved,
            "pool_remaining": len(available),
        },
    }
