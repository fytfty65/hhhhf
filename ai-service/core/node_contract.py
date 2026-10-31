"""权威路线节点契约（P0：统一事实）。

为什么需要
----------
同一件事在各层有好几种写法，实测 grep 出来的分叉包括：

- 花费：`cost` / `cost_estimate` / `costEstimate` / `price`
- 类别：`type` / `category` / `planning_category`
- 坐标：`lnglat`（[lng,lat] 数组）/ `location`（AMap POI 里是 "lng,lat" 字符串，
  但在路线节点里 `location` 其实是**名称**！）/ `coordinates`
- 来源与时间：`data_sources` / `source` / `observed_at`

网关侧已有一份 `contracts.Candidate`（含 `ObservedAt`）并在供应商质量门禁里卡
`observed_at_missing`；前端也在各处自行归一化。缺的是**一份权威节点契约**，
让"事实"只有一种形状。本模块给出它，并坚持项目一贯的诚实口径：

**取不到的价格一律 `cost_status="unknown"`，绝不当作 0 元、绝不冒充已核实。**
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional

from .fact_trust import (
    TRUST_ESTIMATED,
    TRUST_STALE,
    TRUST_UNKNOWN,
    TRUST_VERIFIED,
    fact_trust,
)

# 可信度四态（与 fact_trust 一致，这里再导出一次方便校验）
TRUST_STATES = {TRUST_VERIFIED, TRUST_ESTIMATED, TRUST_STALE, TRUST_UNKNOWN}

# 权威节点字段（与目标架构里列出的 12 个字段一一对应）
CANONICAL_FIELDS = (
    "day",
    "name",
    "category",
    "coordinates",
    "cost",
    "cost_status",
    "source",
    "observed_at",
    "confidence",
    "open_time",
    "official_url",
    "estimated",
    # P3：可信度分级（四态 + 来源类型 + 新鲜度）
    "trust",
    "source_type",
    "freshness",
)

# cost_status 三态：只有拿到可核实来源才算 verified
COST_VERIFIED = "verified"
COST_ESTIMATED = "estimated"
COST_UNKNOWN = "unknown"

CASH_SOURCE_MARKERS = ("amap", "official", "provider", "vendor", "ticketing", "12306", "rollinggo")
# ⚠️ `web_page` 必须算"可核实现金来源"（2026-10-08 实测修）：
#    `web_page` 是取价管线对**白名单官方/政府页面正文**的写法（用户 2026-10-05 拍板：
#    "官方自己写的票价、带 URL 可点开" → 可核实）。它字面上不含 "official"，
#    于是掉进下面的 else 分支被**强制** estimated=True，把节点自己写的 `estimated=False` 覆盖掉。
#    实测症状：21 个官方页节点 fresh + 有观测时间，却全部停在"估算"，
#    文案还写着"按公开规则估算，仅供规划参考" —— 明明是官方页读到的价，却被说成估的。
#    这里按 P3 的分级口径对齐：official_page 是 first_party，属于可核实来源。
VERIFIED_CASH_SOURCES = ("web_page", "official_page", "government")
UNKNOWN_PRICE_MARKERS = ("暂无供应商数据", "暂无报价", "未知", "待确认", "n/a", "na", "unknown", "无")

# 类别别名 → 粗粒度类别（与 core/plan_quality 的 _coarse_category 口径保持一致，便于对账）
CATEGORY_ALIASES: Dict[str, str] = {
    "餐": "food", "餐饮": "food", "美食": "food", "food": "food", "dining": "food",
    "住宿": "hotel", "酒店": "hotel", "民宿": "hotel", "hotel": "hotel", "lodging": "hotel",
    "文化": "cultural", "博物馆": "cultural", "古迹": "cultural", "cultural": "cultural",
    "自然": "scenic", "自然风光": "scenic", "风景": "scenic", "景区": "scenic", "公园": "scenic", "scenic": "scenic",
    "购物": "shopping", "商圈": "shopping", "shopping": "shopping",
    "夜景": "nightlife", "夜市": "nightlife", "nightlife": "nightlife",
    "交通": "transport", "transport": "transport",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def looks_like_coordinates(value: Any) -> bool:
    """"lng,lat" 形态的字符串（AMap POI 的 location 字段就是这个形状）。

    注意：路线节点里的 `location` 是**名称**，所以不能无条件把它当坐标 —— 必须先看形状。
    """
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return True
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", _text(value))
    return bool(match)


def parse_coordinates(value: Any) -> Optional[List[float]]:
    """归一化成 `[lng, lat]`；非法/零点返回 None（不编造坐标）。"""
    if isinstance(value, Mapping):
        lng, lat = value.get("lng"), value.get("lat")
        if lng is None or lat is None:
            return None
        try:
            coords = [float(lng), float(lat)]
        except (TypeError, ValueError):
            return None
    elif isinstance(value, str):
        match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", value)
        if not match:
            return None
        coords = [float(match.group(1)), float(match.group(2))]
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            coords = [float(value[0]), float(value[1])]
        except (TypeError, ValueError):
            return None
    else:
        return None
    lng, lat = coords
    if lng == 0.0 and lat == 0.0:
        return None
    if not (-180.0 <= lng <= 180.0 and -90.0 <= lat <= 90.0):
        return None
    return coords


def parse_cost(value: Any) -> Optional[float]:
    """解析金额：`¥50` / `50元` / `50-120`（取区间下限）/ 数字；取不到返回 None。

    "暂无供应商数据"这类占位词必须返回 None —— **不能变成 0**，否则预算会把"未知"算成"免费"。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _text(value)
    if not text:
        return None
    lowered = text.lower()
    for marker in UNKNOWN_PRICE_MARKERS:
        if marker and marker in lowered:
            return None
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if not numbers:
        return None
    return float(numbers[0])


def normalize_category(raw: Any) -> str:
    """归一化类别；认不出来就原样保留（不硬塞进某个已知类别，避免造假）。"""
    text = _text(raw)
    if not text:
        return ""
    head = text.split(";")[0].strip()
    for candidate in (text, head):
        if candidate in CATEGORY_ALIASES:
            return CATEGORY_ALIASES[candidate]
    for key, value in CATEGORY_ALIASES.items():
        if key in text:
            return value
    return text


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, "", [], {}):
            return mapping[key]
    return None


def normalize_node(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """把任意来源的节点/候选归一化成权威契约。

    返回的字典**一定包含 `CANONICAL_FIELDS` 全部键**（缺的就是 None/空），
    并附带 `facts_missing`（缺哪些关键事实），供上层如实降级而不是假装齐全。
    """
    if not isinstance(raw, Mapping):
        raise TypeError("node_must_be_mapping")

    raw_sources = raw.get("data_sources")
    sources: Mapping[str, Any] = raw_sources if isinstance(raw_sources, Mapping) else {}
    name = _text(_first(raw, "name", "title"))
    location = _first(raw, "location", "address")
    if not name and isinstance(location, str) and not looks_like_coordinates(location):
        name = _text(location)  # 路线节点里 location 常常就是名称

    coordinates = None
    for key in ("coordinates", "lnglat", "location", "coord", "point"):
        candidate = raw.get(key)
        if candidate is None:
            continue
        parsed = parse_coordinates(candidate)
        if parsed:
            coordinates = parsed
            break

    cost = parse_cost(_first(raw, "cost", "cost_estimate", "costEstimate", "price", "estimated_price"))
    source = _text(
        _first(raw, "source")
        or sources.get("cost_estimate")
        or sources.get("price")
        or sources.get("rating")
        or ""
    )
    explicit_status = _text(_first(raw, "cost_status", "price_status")).lower()
    estimated_flag = bool(raw.get("estimated")) or explicit_status == COST_ESTIMATED

    if cost is None:
        cost_status = COST_UNKNOWN
        estimated_flag = False  # 未知不是"估算"，两者不能混
    elif explicit_status in {COST_VERIFIED, COST_ESTIMATED}:
        cost_status = explicit_status
    elif any(marker in source.lower() for marker in CASH_SOURCE_MARKERS) or any(
        marker in source.lower() for marker in VERIFIED_CASH_SOURCES
    ):
        cost_status = COST_VERIFIED
        estimated_flag = False
    else:
        cost_status = COST_ESTIMATED
        estimated_flag = True

    confidence = _first(raw, "confidence")
    try:
        confidence = None if confidence is None else max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = None

    # ---- P3：可插拔的数据新鲜度（source_type / freshness / trust / confidence）----
    # 为什么放在这里而不是让上游各自算：**同一个节点上的每条事实都该带同一套口径**，
    # 而且 `confidence` **由来源分级推导、不采信上游/模型自报的值**
    # （模型对自己的说法打高分没有校准价值，反而让"编造"看起来可信）。
    # ⚠️ `price_retrieved_at` 必须在列表里（2026-10-08 实测补）：
    #    `core/price_sources.py` 取到价时写的就是这个字段（来自 `build_observation` 的
    #    `retrieved_at`，是**真实抓取时间**），但契约以前不认它 ——
    #    结果 120 份方案、483 个有价节点里 `observed_at` 全是空，
    #    有价节点因此只能落到"估算"，而它其实有观测时间、本该判定新鲜度。
    #    实测这 483 个里有 144 个带 `price_retrieved_at`，改完能如实升到"已核实"；
    #    剩下 339 个**确实没有**观测时间，继续留在"估算"（缺时间不等于新鲜，不许补默认值）。
    observed_at = _first(
        raw,
        "observed_at",
        "observedAt",
        "price_retrieved_at",
        "data_time",
        "updated_at",
        "collected_at",
    )
    trust_item = fact_trust(
        value=cost,
        source=source,
        source_type=_first(raw, "source_type", "sourceType"),
        observed_at=observed_at,
        fact_kind=str(_first(raw, "fact_kind") or "price"),
        estimated=estimated_flag,
        method=_first(raw, "method", "estimate_method") or "",
    )

    node: Dict[str, Any] = {
        "day": raw.get("day"),
        "name": name,
        "category": normalize_category(_first(raw, "category", "planning_category", "type")),
        "coordinates": coordinates,
        "cost": cost,
        "cost_status": cost_status,
        "source": source or None,
        "observed_at": observed_at,
        "confidence": confidence,
        "open_time": _text(_first(raw, "open_time", "opening_hours", "openTime")) or None,
        "official_url": _text(_first(raw, "official_url", "amap_url", "booking_url", "url")) or None,
        "estimated": estimated_flag,
        # P3：一条事实的"可不可信"（前端四态：已核实/估算/信息较旧/尚未核实）
        "trust": trust_item["trust"],
        "trust_label": trust_item["label"],
        "trust_action": trust_item["action"],
        "trust_message": trust_item["message"],
        "source_type": trust_item["source_type"],
        "source_label": trust_item["source_label"],
        "freshness": trust_item["freshness"],
        # 推导出来的置信度**覆盖**上游自报值（见上面的说明）
        "derived_confidence": trust_item["confidence"],
    }

    missing: List[str] = []
    if not node["name"]:
        missing.append("name")
    if not node["coordinates"]:
        missing.append("coordinates")
    if cost is None:
        missing.append("cost")
    if not node["source"]:
        missing.append("source")
    if not node["observed_at"]:
        missing.append("observed_at")
    node["facts_missing"] = missing
    return node


def validate_node(node: Mapping[str, Any]) -> List[str]:
    """契约自检：返回违规项列表（空列表 = 合规）。用于 CI/单测与上游产出前的把关。"""
    problems: List[str] = []
    for field in CANONICAL_FIELDS:
        if field not in node:
            problems.append(f"missing_field:{field}")
    if _text(node.get("name")) == "":
        problems.append("name_required")
    if node.get("coordinates") is not None and not parse_coordinates(node.get("coordinates")):
        problems.append("coordinates_invalid")
    status = _text(node.get("cost_status")).lower()
    if status not in {COST_VERIFIED, COST_ESTIMATED, COST_UNKNOWN}:
        problems.append(f"cost_status_invalid:{status or 'empty'}")
    # 【诚实口径】取不到价格就必须是 unknown，且不能被标成估算
    if node.get("cost") is None and status != COST_UNKNOWN:
        problems.append("missing_cost_must_be_unknown")
    if node.get("cost") is None and node.get("estimated"):
        problems.append("unknown_cost_must_not_be_estimated")
    # 【诚实口径】标成已核实就必须有来源
    if status == COST_VERIFIED and not _text(node.get("source")):
        problems.append("verified_cost_requires_source")
    # 【P3 诚实口径】可信度四态必须与花费状态自洽，否则前端显示的标签会骗人
    trust = _text(node.get("trust")).lower()
    if trust not in TRUST_STATES:
        problems.append(f"trust_invalid:{trust or 'empty'}")
    if trust == TRUST_VERIFIED and status != COST_VERIFIED:
        problems.append("trust_verified_requires_verified_cost")
    if status == COST_UNKNOWN and trust != TRUST_UNKNOWN:
        problems.append(f"unknown_cost_must_be_trust_unknown:{trust}")
    if status == COST_VERIFIED and trust == TRUST_STALE:
        problems.append("trust_stale_must_not_claim_verified_cost")
    return problems


__all__ = [
    "CANONICAL_FIELDS",
    "CATEGORY_ALIASES",
    "COST_ESTIMATED",
    "COST_UNKNOWN",
    "COST_VERIFIED",
    "looks_like_coordinates",
    "normalize_category",
    "normalize_node",
    "parse_coordinates",
    "parse_cost",
    "validate_node",
]
