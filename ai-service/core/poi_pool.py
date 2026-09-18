"""同义候选池：用**已配置的** POI 数据源（本机是 AMap）检索同类替代点。

为什么要它
----------
`budget_planner.propose_fallback` 与 `candidate_index.apply_exclusions` 都需要
`candidates_by_intent`：没有候选池时只能"删/降"，不能"换成同类"，"不想去这些点、换别的"
也无从执行。本模块负责**取真实候选**（而不是编造），并把它们整理成候选池。

设计
----
- 检索函数通过参数注入（`fetch`），因此**可以离线单测**（测试传假 fetch），生产传
  `ExpertToolbox.get_dynamic_pois`；
- 任何异常/超时都返回已有结果，**绝不阻断规划**；没有候选就是空池（上层如实告知"没换成"）；
- 候选带上价格级别与来源（`price`/`price_tier`/`price_source`），供"降档"与"未核实"口径使用。
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from .candidate_index import index_candidates, intent_of, normalize_text, tier_level

# 每个意图用于检索的关键词（AMap place/text 接受自然语言关键词）
INTENT_KEYWORDS: Dict[str, str] = {
    "cultural": "博物馆|古迹|寺庙|文化馆",
    "scenic": "风景区|公园|山水",
    "food": "小吃|老字号|本地菜",
    "hotel": "酒店|民宿",
    "shopping": "步行街|商场",
    "nightlife": "夜景|酒吧",
    "outdoor": "徒步|登山|露营地",
    "hotspring": "温泉",
    "family": "动物园|科技馆|亲子乐园",
    "photo": "观景台|日出|花海",
    "market": "菜市场|夜市",
    "landmark": "地标|广场|古城",
}
DEFAULT_TYPES = "110000|141200|060400|060100"
UNKNOWN_PRICE_MARKERS = ("暂无", "未知", "unavailable", "none", "")

FetchFn = Callable[..., Awaitable[Sequence[Mapping[str, Any]]]]


def parse_amap_location(value: Any) -> Optional[List[float]]:
    """把 AMap 的 'lng,lat' 字符串转成 [lng, lat]；解析不了返回 None。"""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return [float(value[0]), float(value[1])]
        except (TypeError, ValueError):
            return None
    if not isinstance(value, str) or "," not in value:
        return None
    parts = value.split(",")
    try:
        return [float(parts[0]), float(parts[1])]
    except (TypeError, ValueError):
        return None


def _parse_price(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    text = str(raw).strip()
    if any(marker and marker in text.lower() for marker in UNKNOWN_PRICE_MARKERS):
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def candidate_from_poi(poi: Mapping[str, Any], intent: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """AMap POI（或任何同类形状）→ 候选池条目；缺名称则丢弃。"""
    if not isinstance(poi, Mapping):
        return None
    name = str(poi.get("name") or "").strip()
    if not name:
        return None
    price = _parse_price(poi.get("cost"))
    sources = poi.get("data_sources") if isinstance(poi.get("data_sources"), Mapping) else {}
    price_source = str(sources.get("cost") or "").strip().lower() or "unavailable"
    candidate: Dict[str, Any] = {
        "name": name,
        "type": str(poi.get("type") or ""),
        "intent": intent or intent_of(poi),
        "price": price,
        "price_source": price_source,
        "rating": poi.get("rating"),
        "open_time": poi.get("open_time"),
        "estimated": bool(poi.get("estimated", price is None)),
        "lnglat": parse_amap_location(poi.get("location")),
        "source": "amap",
    }
    candidate["price_tier"] = (
        "unknown" if price is None else ("free" if price <= 0 else ("economy" if price < 150 else ("comfort" if price < 600 else "quality")))
    )
    candidate["tier_level"] = tier_level(candidate)
    return candidate


def intents_for_plan(plan: Any, extra: Iterable[str] = ()) -> List[str]:
    """方案里出现过的意图（用于决定"要为哪些类别准备替代品"）。"""
    from .plan_quality import nodes_of  # 延迟导入，避免与 plan_quality 形成模块级循环

    intents: List[str] = []
    for node in nodes_of(plan):
        label = intent_of(node)
        if label and label != "other" and label not in intents:
            intents.append(label)
    for label in extra:
        if label and label not in intents:
            intents.append(label)
    return intents


async def build_candidate_pool(
    city: str,
    intents: Sequence[str],
    fetch: Optional[FetchFn],
    limit_per_intent: int = 6,
    exclude_names: Sequence[str] = (),
    types: str = DEFAULT_TYPES,
    keywords_by_intent: Optional[Mapping[str, str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """按意图检索同类候选，返回 `{intent: [candidate, ...]}`（已按价格升序）。

    - `fetch` 为空或城市为空 → 返回空池（上层据此如实告知"没有候选"，而不是硬凑）；
    - 被 `exclude_names` 点名的候选会被剔除（用户明确说不要的地方，不能又推荐回来）。
    """
    if not fetch or not str(city or "").strip():
        return {}
    keywords_map = dict(INTENT_KEYWORDS)
    if keywords_by_intent:
        keywords_map.update({k: v for k, v in keywords_by_intent.items() if v})

    collected: List[Dict[str, Any]] = []
    for intent in intents:
        keywords = keywords_map.get(intent)
        if not keywords:
            continue
        try:
            pois = await fetch(str(city), keywords, types=types, limit=max(1, int(limit_per_intent)))
        except Exception:
            # 单个意图检索失败不影响其它意图；整体也绝不抛出（规划不能被数据源拖垮）
            continue
        for poi in pois or []:
            candidate = candidate_from_poi(poi, intent=intent)
            if candidate:
                collected.append(candidate)

    excluded = {normalize_text(name) for name in exclude_names if str(name).strip()}
    deduped: Dict[str, Dict[str, Any]] = {}
    for candidate in collected:
        key = normalize_text(candidate.get("name"))
        if not key or key in excluded:
            continue
        deduped.setdefault(key, candidate)
    return index_candidates(list(deduped.values()))
