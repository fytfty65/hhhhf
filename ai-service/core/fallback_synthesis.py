"""候选池保底路线的确定性排布。

背景（2026-11-01 真实事故，四川 3 天）：大模型不可用时 `_synthesize_from_pool`
用 `random.shuffle` 后按顺序取点，并用槽位序号给任意 POI 贴标签 ——
「楠木沟风景区」拿到「美食·夜市」，下游类别判定据此把它算成餐饮。
结果是 3 天路线里 8 家餐厅、3 家酒店、只有 1 个景区，用户要的"自然景观"落空。

这里把"取哪些点"抽成纯函数：按配比目标分类别取点、按地理就近排序、
标签只描述类别本身，不再编造"夜市/老字号"这类未经核实的属性。
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .itinerary_balance import poi_category

CATEGORY_TAGS: Dict[str, List[str]] = {
    "scenic": ["自然", "风景区"],
    "city_park": ["公园", "城市休闲"],
    "cultural": ["人文", "文化场馆"],
    "food": ["美食", "本地餐饮"],
    "shopping": ["购物", "商圈"],
    "hotel": ["住宿"],
}


def tags_for(category: str, item: Mapping[str, Any] | None = None) -> List[str]:
    """标签只陈述类别事实；不再按槽位序号乱贴。

    `scenic_tier=city_park` 的城市公园单独标注，避免被当作自然景区计入。
    """
    tags = list(CATEGORY_TAGS.get(category, []))
    if category == "scenic" and isinstance(item, Mapping) and str(item.get("scenic_tier") or "") == "city_park":
        tags = ["公园", "城市休闲"]
    return tags


def _coords(item: Mapping[str, Any]) -> Optional[List[float]]:
    value = item.get("lnglat")
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            lng, lat = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
        if lng == 0 and lat == 0:
            return None
        return [lng, lat]
    return None


def _distance_km(a: Optional[Sequence[float]], b: Optional[Sequence[float]]) -> float:
    if not a or not b:
        return 0.0
    lng1, lat1, lng2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    d_lng, d_lat = lng2 - lng1, lat2 - lat1
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def _dedupe(pool: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    items: List[Dict[str, Any]] = []
    for raw in pool:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or raw.get("location") or "").strip()
        if not name:
            continue
        key = "".join(name.lower().split())
        if key in seen:
            continue
        seen.add(key)
        items.append(dict(raw) | {"name": name})
    return items


def _rating(item: Mapping[str, Any]) -> float:
    try:
        return float(item.get("rating") or 0)
    except (TypeError, ValueError):
        return 0.0


def _spread_days(total: int, days: int) -> List[int]:
    """把 total 个名额摊到 days 天，余数从前面的天开始补 —— 稀缺景区不能被首日一次用光。"""
    total = max(0, int(total))
    days = max(1, int(days))
    base, remainder = divmod(total, days)
    return [base + (1 if index < remainder else 0) for index in range(days)]


def _pick_category(item: Mapping[str, Any]) -> str:
    """白天候选的类别：城市公园单列一档，不占自然景区名额。"""
    category = poi_category(item)
    if category == "scenic" and str(item.get("scenic_tier") or "") == "city_park":
        return "city_park"
    return category


def plan_fallback_picks(
    pool: Sequence[Mapping[str, Any]],
    *,
    days: int,
    daily_nodes: int,
    targets: Optional[Mapping[str, Any]] = None,
    day_regions: Optional[Mapping[int, str]] = None,
    region_of: Optional[Callable[[Mapping[str, Any]], str]] = None,
) -> Dict[str, Any]:
    """从真实候选池选出 `days * daily_nodes` 个白天节点 + 每天 1 个酒店。

    选择顺序：先补配比缺口（景区 → 餐饮 → 人文），再用剩余槽位补最优候选；
    每一步都取"离上一个地点最近"的同类候选，避免一天里横跨半个省。

    自然景区名额只由 `scenic_tier=landmark` 的候选兑现；城市公园只在配额补满后
    作为"城市休闲"填充，既不顶替自然景观，也不在 audit 里算成自然景区。

    `day_regions` + `region_of`（省域覆盖 B）：给了就**按片区排**——
    第 N 天只从建议片区里取点；该片区这一类没有候选时这一天就少一个点
    （餐点由出稿前的"周边检索"补真实餐馆），不再退回全池抓省会景点
    （实测 2026-11-02 四川 4 天：退回全池让"计划第 4 天在阿坝"实际排成成都）。
    同一片区占多天时，景区还要**给后面那天留一半**：阿坝 2 天只有 4 个真景区，
    全部塞进第 3 天会排出 245km，且第 4 天一个景区都不剩。
    不给这两个参数时行为完全不变（非省域请求走这条）。
    """
    days = max(1, int(days))
    daily_nodes = max(1, int(daily_nodes))
    items = _dedupe(pool)
    hotels = [item for item in items if _pick_category(item) == "hotel"]
    day_pool = [item for item in items if _pick_category(item) != "hotel"]
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for item in day_pool:
        buckets.setdefault(_pick_category(item), []).append(item)
    # 真景区优先于城市公园（城市公园可以去，但不该占"自然景观"名额）
    buckets["scenic"] = sorted(
        buckets.get("scenic", []),
        key=lambda item: (0 if str(item.get("scenic_tier") or "") == "landmark" else 1, -_rating(item)),
    )

    if isinstance(targets, Mapping) and targets:
        requested_totals = {
            category: max(0, int(targets.get(category, 0) or 0))
            for category in ("scenic", "food", "cultural")
        }
    else:
        requested_totals = {category: days for category in ("scenic", "food", "cultural")}
    # 只能按候选池真实供给来分配（缺景区就如实少排，不重复、不编造）
    allocatable = {
        category: min(requested_totals[category], len(buckets.get(category, [])))
        for category in requested_totals
    }
    day_quotas = {category: _spread_days(allocatable[category], days) for category in allocatable}
    used: set[str] = set()
    picks: List[Dict[str, Any]] = []
    cursor: Optional[List[float]] = None
    planned_regions = {int(key): str(value) for key, value in dict(day_regions or {}).items() if str(value or "")}

    def _region(item: Mapping[str, Any]) -> str:
        if region_of is None:
            return ""
        try:
            return str(region_of(item) or "").strip()
        except Exception:
            return ""

    def _scoped(candidates: List[Dict[str, Any]], region: str) -> List[Dict[str, Any]]:
        """优先落在当天建议片区；该片区这一类没候选时不强求（退全池，别让一天空着）。"""
        if not region:
            return candidates
        scoped = [item for item in candidates if _region(item) == region]
        return scoped or candidates

    def _in_region(item: Mapping[str, Any], region: str) -> bool:
        return bool(region) and _region(item) == region

    def _later_same_region(day: int, region: str) -> bool:
        """这一天之后还有同片区的行程日吗（有就要把景区留给后面那天）。

        实测（2026-11-02 四川 4 天）：阿坝 2 天只有 4 个真景区，`take_any` 按
        "离上一个点最近"把 4 个全塞进第 3 天 —— 第 4 天片区里一个景区都不剩，
        只好退回全池抓省会景点，第 3 天还跑出 245km。
        """
        if not region:
            return False
        return any(
            day < other and str(planned_regions.get(other) or "") == region
            for other in planned_regions
        )

    def take(category: str, region: str = "") -> Optional[Dict[str, Any]]:
        nonlocal cursor
        candidates = [item for item in buckets.get(category, []) if _key(item) not in used]
        if not candidates:
            return None
        if region:
            scoped = [item for item in candidates if _in_region(item, region)]
            # 省域分片：给了"今天在哪片"就**不跨片区借点**。借来的点意味着当天
            # 横穿几百公里，也会让"计划今天在阿坝"实际排成成都。
            if not scoped:
                return None
            candidates = scoped
        nearest = min(candidates, key=lambda item: (_distance_km(cursor, _coords(item)), -_rating(item)))
        used.add(_key(nearest))
        cursor = _coords(nearest) or cursor
        return nearest

    def take_any(region: str = "", day: int = 1) -> Optional[Dict[str, Any]]:
        nonlocal cursor
        best: Optional[Dict[str, Any]] = None
        for category in ("scenic", "cultural", "food", "shopping", "city_park", "other"):
            pool_items = [item for item in buckets.get(category, []) if _key(item) not in used]
            if not pool_items:
                continue
            if region:
                pool_items = [item for item in pool_items if _in_region(item, region)]
                if not pool_items:
                    continue
                # 同片区后面还有行程日：景区/公园先留给后面那天（见 `_later_same_region`）
                if category in ("scenic", "city_park") and _later_same_region(day, region):
                    continue
            for item in pool_items:
                score = (_distance_km(cursor, _coords(item)), -_rating(item))
                if best is None or score < best[0]:  # type: ignore[index]
                    best = (score, item)  # type: ignore[assignment]
        if best is None:
            return None
        used.add(_key(best[1]))
        cursor = _coords(best[1]) or cursor
        return best[1]

    for day in range(1, days + 1):
        day_region = planned_regions.get(day, "")
        filled: Dict[str, int] = {}
        slots = 0
        for category in ("scenic", "food", "cultural"):
            while slots < daily_nodes and filled.get(category, 0) < day_quotas[category][day - 1]:
                picked = take(category, day_region)
                if picked is None:
                    break
                picks.append({"day": day, "category": category, "item": picked, "slot": slots})
                filled[category] = filled.get(category, 0) + 1
                slots += 1
        while slots < daily_nodes:
            picked = take_any(day_region, day)
            if picked is None:
                break
            category = _pick_category(picked)
            picks.append({"day": day, "category": category, "item": picked, "slot": slots})
            filled[category] = filled.get(category, 0) + 1
            slots += 1
        hotel = None
        remaining_hotels = [item for item in hotels if _key(item) not in used]
        if remaining_hotels:
            hotel = min(
                _scoped(remaining_hotels, day_region),
                key=lambda item: _distance_km(cursor, _coords(item)),
            )
            used.add(_key(hotel))
            cursor = _coords(hotel) or cursor
        picks.append({"day": day, "category": "hotel", "item": hotel, "slot": daily_nodes})

    counts: Dict[str, int] = {}
    for pick in picks:
        if pick["item"] is not None:
            counts[pick["category"]] = counts.get(pick["category"], 0) + 1
    return {
        "picks": picks,
        "audit": {
            "requested_daytime": days * daily_nodes,
            # 所有白天节点都算（含城市公园），用来判断"排得够不够密"
            "planned_daytime": sum(
                count for category, count in counts.items() if category != "hotel"
            ),
            "by_category": counts,
            # 自然景区只数真景区，城市公园单独记一档
            "natural_scenic": counts.get("scenic", 0),
            "city_park": counts.get("city_park", 0),
            "hotels_requested": days,
            "pool_size": len(items),
            # 省域保底排布的口径：第几天在哪个片区（没给分片建议时为空）
            "day_regions": {str(day): region for day, region in sorted(planned_regions.items())},
        },
    }


def _key(item: Mapping[str, Any]) -> str:
    return "".join(str(item.get("name") or item.get("location") or "").lower().split())
