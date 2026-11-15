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


CATEGORY_LABELS = ("scenic", "cultural", "food", "shopping", "hotel")


def poi_category(node: Mapping[str, Any]) -> str:
    """判定节点类别：声明类别 > 名称/供应商类别 > 展示标签。

    2026-11-01 真实事故：降级合成按槽位序号给地点贴标签（「楠木沟风景区」被贴成
    「美食·夜市」），而这里原来把 tags 当成和名称同等分量的证据 ——
    于是真景区被算成餐饮，类别均衡怎么补都补不出景区，方案只剩 1 个景点。
    标签只是展示文案，不能反推事实，所以放到最后。
    """
    declared = str(node.get("planning_category") or "").strip().lower()
    if declared in CATEGORY_LABELS:
        return declared

    def classify(text: str) -> str:
        if node.get("is_hotel") or any(word in text for word in ("住宿", "酒店", "hotel", "民宿")):
            return "hotel"
        if any(word in text for word in ("餐", "美食", "小吃", "咖啡", "food", "restaurant")):
            return "food"
        if any(word in text for word in ("博物馆", "文化", "古迹", "历史", "展览", "cultural")):
            return "cultural"
        if any(word in text for word in ("商场", "市集", "购物", "shopping")):
            return "shopping"
        if any(word in text for word in ("风景", "景区", "景点", "公园", "名胜", "scenic")):
            return "scenic"
        return ""

    evidence = f"{_name(node)} {node.get('type') or ''}".lower()
    matched = classify(evidence)
    if matched:
        return matched
    tags_text = " ".join(str(tag) for tag in (node.get("tags") or [])).lower()
    return classify(tags_text) or "scenic"


# 兼容模块内部既有调用点（本轮不做大面积改名）
_category = poi_category


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


def _day_pool_keys(day_pools: Optional[Mapping[int, Sequence[Mapping[str, Any]]]]) -> Dict[int, set[str]]:
    """把"每天只能从哪些候选里补"的可选配置转成按天白名单（名字 key）。

    省域行程用得上：分片建议已经确定"这趟哪几天在哪片"，补节点就必须只补当天的片区。
    实测（2026-11-01 四川 4 天）：给的是全局候选（9 个地州），补进来的都是别的片区的景区，
    紧接着又被"同一天一片区"的硬约束移出（补 9 个、删 9 个），最后景区只剩 6 个 ——
    用户看到的就是"安排的地点很少"。
    """
    if not isinstance(day_pools, Mapping):
        return {}
    result: Dict[int, set[str]] = {}
    for day, items in day_pools.items():
        try:
            key = int(day)
        except (TypeError, ValueError):
            continue
        result[key] = {_key(item) for item in items if isinstance(item, Mapping) and _name(item)}
    return result


def rebalance_daily_route(
    route: Sequence[Mapping[str, Any]],
    pool: Sequence[Mapping[str, Any]],
    *,
    days: int,
    target_daytime: int,
    day_pools: Optional[Mapping[int, Sequence[Mapping[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Fill and redistribute days without duplicating or fabricating POIs.

    `day_pools`（可选）给出"每天允许从哪些候选里补"：给了就按天过滤，没给就沿用全局池。
    """
    days = max(1, int(days))
    target_daytime = max(1, int(target_daytime))
    _allowed_by_day = _day_pool_keys(day_pools)
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

    def _daytime_candidates(day: int) -> list[Dict[str, Any]]:
        allowed = _allowed_by_day.get(day)
        return [
            item for item in available
            if _category(item) != "hotel" and (allowed is None or _key(item) in allowed)
        ]

    for day in range(1, days + 1):
        nodes = by_day[day]
        while sum(_category(node) != "hotel" for node in nodes) < target_daytime:
            current = {_category(node) for node in nodes}
            candidates = _daytime_candidates(day)
            preferred = next((item for item in candidates if _category(item) not in current), None)
            candidate = preferred or next(iter(candidates), None)
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
    def _allowed_for(day: int, node: Mapping[str, Any]) -> bool:
        allowed = _allowed_by_day.get(day)
        return allowed is None or _key(node) in allowed

    total_daytime = sum(sum(_category(node) != "hotel" for node in by_day[day]) for day in range(1, days + 1))
    achievable = min(target_daytime, total_daytime // days)
    remainder = max(0, total_daytime - achievable * days)
    desired = {day: achievable + (1 if day <= min(days, remainder) else 0) for day in range(1, days + 1)}
    moved = 0
    for target in range(1, days + 1):
        while sum(_category(node) != "hotel" for node in by_day[target]) < desired[target]:
            donor: Optional[int] = None
            index: Optional[int] = None
            for day in range(1, days + 1):
                if day == target:
                    continue
                if sum(_category(node) != "hotel" for node in by_day[day]) <= desired[day]:
                    continue
                # 👑 给了 `day_pools` 就必须按天过滤（2026-11-02 实测：第 4 天计划在阿坝，
                #    这里把成都的饭点挪了过去 —— 当天多跑 170km，计划片区也失效）。
                found = next(
                    (
                        i for i in range(len(by_day[day]) - 1, -1, -1)
                        if _category(by_day[day][i]) != "hotel" and _allowed_for(target, by_day[day][i])
                    ),
                    None,
                )
                if found is not None:
                    donor, index = day, found
                    break
            if donor is None or index is None:
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
    day_pools: Optional[Mapping[int, Sequence[Mapping[str, Any]]]] = None,
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
    _allowed_by_day = _day_pool_keys(day_pools)

    def take(category: str, day: int) -> Optional[Dict[str, Any]]:
        allowed = _allowed_by_day.get(day)
        for item in available:
            if _category(item) == category and (allowed is None or _key(item) in allowed):
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
            candidate = take(category, day)
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
        # 显式“不想去博物馆/博物院”时，文化类上限可以为 0；普通均衡模式仍保留至少 1 个文化节点。
        cap = 0 if max_cultural_share <= 0 else max(1, int(math.ceil(max_cultural_share * max(1, len(daytime)))))
        while sum(_category(node) == "cultural" for node in daytime) > cap:
            replacement = take("scenic", day) or take("food", day) or take("shopping", day)
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
