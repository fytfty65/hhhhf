"""「地点好不好 / 方案周全」的可判定部分（2026-10-05）。

背景：用户的第一优先级是**地点好不好**，而既有评测（core/plan_quality.py）量的是结构
（节奏 / 空间效率 / 偏好覆盖 / 真实性 / 吃住贴合 / 多样性），
**没有量"这些地方本身是不是真实检索到的、像不像能去的玩点、有没有明显可疑的泛称"**。
这一层把其中**能用确定性证据判定**的部分补上；判不了的一律**不猜**，标成"需要人工抽查"。

两条必须写清的边界（否则这一层就变成自欺）
------------------------------------------
1. **不评价"值不值得去"**。"草原石人有没有意思"需要外部知识，我们手里没有可靠依据；
   让模型自己评"这个地方好不好"只是把不可核实的判断包装成分数。所以这里只回答三件能核实的事：
     · 这些地点是**真实检索到的**（候选池里出现过 / 带高德 ID / 数据源标注），还是模型凭记忆写的；
     · 名字**像不像一个能去的玩点**（设施、站点、纯商业点、子景点这些已经吃过亏的模式）；
     · 有没有**明显可疑的泛称**（"漂亮的草原""草原遗迹"这种）——只列为"待人工确认"，不直接扣分。
   "值不值得去"由人工抽查表负责（tools/plan_review_sheet.py）。
2. **判不了就中性 + 如实标注**（`insufficient_evidence` / `review_required`），
   这与全项目"未知 ≠ 0 ≠ 估计"的口径一致。

`score_completeness` 是"方案周全"的可判定版本：每晚有住宿、每天有饭吃、日内不超里程、
价格有出处、**未知项有没有被如实披露**、跨城腿有没有交通方案。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .plan_quality import (
    MEAL_WINDOWS,
    _coarse_category,
    haversine_km,
    node_day,
    node_lnglat,
    node_name,
    nodes_of,
    parse_clock,
)

# 住宿节点的名字关键词（与 route_composition.is_lodging_node 同口径，避免两套判断）
_LODGING_WORDS = ("酒店", "民宿", "宾馆", "客栈", "旅馆", "度假村", "旅社", "青旅")

# 真实数据源的标记（与 plan_quality.UNVERIFIED_SOURCES 互补）
_REAL_SOURCE_MARKERS = ("amap", "provider", "vendor", "official", "rollinggo", "seniverse")

# "像玩点"的设施/站点标记（与 core.poi_pool 的 FACILITY_NAME_MARKERS 同源，这里只取关键的；
# 完整清单在 poi_pool，避免两处维护：能导入就导入，导入不到就用这份兜底）
try:  # pragma: no cover - 正常都能导入
    from .poi_pool import COMMERCE_NAME_MARKERS as _COMMERCE_MARKERS
    from .poi_pool import FACILITY_NAME_MARKERS as _FACILITY_MARKERS
except Exception:  # pragma: no cover
    _FACILITY_MARKERS = ("停车场", "出入口", "售票", "游客中心", "厕所", "卫生间", "充电站")
    _COMMERCE_MARKERS = ("商场", "购物中心", "超市", "商店", "专卖店", "文创空间", "写字楼")

# 明显"泛称/描述性"的名字：不是判它不好，而是**判不了**，交给人工看一眼。
# 实测出现过的例子：「漂亮的草原」「草原遗迹」「沙漠迷宫」「草原明珠店」。
_GENERIC_NAME_MARKERS = ("漂亮的", "美丽的", "遗迹", "迷宫", "遗址公园", "公社", "大院")


def _is_lodging(node: Mapping[str, Any]) -> bool:
    if bool(node.get("is_hotel")):
        return True
    name = node_name(node)
    return any(word in name for word in _LODGING_WORDS)


def _source_values(node: Mapping[str, Any]) -> List[str]:
    sources = node.get("data_sources")
    if isinstance(sources, Mapping):
        return [str(value).strip().lower() for value in sources.values()]
    if isinstance(sources, Sequence) and not isinstance(sources, (str, bytes)):
        return [str(value).strip().lower() for value in sources]
    return []


def is_real_retrieval(node: Mapping[str, Any], known_names: Optional[set] = None) -> bool:
    """这个节点是不是**真实检索到的**（而不是模型凭记忆写的）。

    三个独立证据，任一成立即可：带了高德 ID / 数据源标注了供应商 / 名字出现在本次候选池里。
    """
    if str(node.get("id") or "").strip():
        return True
    if str(node.get("amap_url") or "").strip():
        return True
    if any(marker in value for value in _source_values(node) for marker in _REAL_SOURCE_MARKERS):
        return True
    if known_names and node_name(node) in known_names:
        return True
    return False


def looks_like_a_visitable_place(node: Mapping[str, Any]) -> Tuple[bool, str]:
    """名字看起来是不是一个"能去的玩点"；返回 `(是否像, 不像的原因)`。"""
    name = node_name(node)
    text = name.lower()
    if not name:
        return False, "没有名字"
    for marker in _FACILITY_MARKERS:
        if str(marker).lower() in text:
            return False, f"像是设施/服务点（含「{marker}」）"
    stripped = name.strip()
    if stripped.endswith("站") and not stripped.endswith("驿站"):
        return False, "像是公交/地铁站（以「站」结尾）"
    category = _coarse_category(node)
    if category in {"scenic", "cultural"}:
        for marker in _COMMERCE_MARKERS:
            if str(marker).lower() in text:
                return False, f"是商业点却排在景点/人文位上（含「{marker}」）"
    return True, ""


def needs_human_review(node: Mapping[str, Any]) -> str:
    """需要人工看一眼的名字（**不扣分**，只列出来）——返回原因，空串表示不用看。"""
    name = node_name(node)
    for marker in _GENERIC_NAME_MARKERS:
        if marker in name:
            return f"名字偏泛称/描述性（含「{marker}」），值不值得去需要人工判断"
    return ""


def score_place_quality(
    plan: Any,
    *,
    candidates: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """地点质量（可判定部分）：真实检索比例 + 名字像不像玩点 + 待人工确认清单。"""
    nodes = nodes_of(plan)
    known_names = set()
    for item in candidates or []:
        if isinstance(item, Mapping):
            known_names.add(str(item.get("name") or ""))
        elif isinstance(item, str):
            known_names.add(item)
    known_names.discard("")

    if not nodes:
        return {
            "value": 0.0,
            "real_retrieval_ratio": 0.0,
            "visitable_ratio": 0.0,
            "review_required": [],
            "insufficient_evidence": True,
            "note": "路线是空的，无从判断地点质量",
        }

    real = 0
    visitable = 0
    review_required: List[Dict[str, str]] = []
    suspect: List[Dict[str, str]] = []
    evidence_available = bool(known_names)
    for node in nodes:
        if is_real_retrieval(node, known_names):
            real += 1
            evidence_available = True
        looks_ok, why = looks_like_a_visitable_place(node)
        if looks_ok:
            visitable += 1
        else:
            suspect.append({"name": node_name(node), "why": why})
        review_why = needs_human_review(node)
        if review_why:
            review_required.append({"name": node_name(node), "why": review_why})

    total = len(nodes)
    real_ratio = real / total
    visitable_ratio = visitable / total
    # 权重：**真实检索**（能不能核实）比"名字像玩点"更重要；后者是启发式
    value = 0.6 * real_ratio + 0.4 * visitable_ratio
    result: Dict[str, Any] = {
        "value": round(max(0.0, min(1.0, value)), 4),
        "real_retrieval_ratio": round(real_ratio, 4),
        "visitable_ratio": round(visitable_ratio, 4),
        "total_nodes": total,
        "suspect_names": suspect[:8],
        "review_required": review_required[:8],
        "note": "只判定「是不是真实检索到的 / 像不像玩点」；「值不值得去」需要人工抽查",
    }
    if not evidence_available:
        # 没有任何供应商证据（既没有 ID/来源标注，也没有候选池可比）时，
        # 真实检索比例这一项**判不了** —— 如实标注，不让它变成"看起来都不可信"
        result["insufficient_evidence"] = True
        result["value"] = round(max(0.0, min(1.0, visitable_ratio)), 4)
        result["note"] = "方案里没有任何供应商证据（也无候选池可比），只能判「名字像不像玩点」"
    return result


def _meal_count(day_nodes: Sequence[Mapping[str, Any]]) -> int:
    count = 0
    for node in day_nodes:
        if _coarse_category(node) == "food":
            count += 1
            continue
        minute = parse_clock(node.get("time"))
        if minute is None:
            continue
        if any(start <= minute <= end for start, end in MEAL_WINDOWS):
            count += 1
    return count


def score_completeness(
    context: Mapping[str, Any],
    plan: Any,
    expectations: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """方案周全（可判定部分）：住宿/吃饭/里程/价格出处/未知项披露/跨城交通。

    每一项返回 `ok: True/False/None`，`None` 表示**没有依据判断**（不参与计分，只记进 note）。
    """
    nodes = nodes_of(plan)
    days = max(1, int((context or {}).get("days") or (context or {}).get("trip_days") or 1))
    by_day: Dict[int, List[Mapping[str, Any]]] = {}
    for index, node in enumerate(nodes):
        by_day.setdefault(node_day(node, index), []).append(node)

    checks: Dict[str, Dict[str, Any]] = {}

    # ① 每晚有住宿（nights = days - 1）
    nights = max(0, days - 1)
    lodging_days = sorted({day for day, items in by_day.items() if any(_is_lodging(item) for item in items)})
    covered_nights = len([day for day in lodging_days if 1 <= day <= nights])
    checks["lodging_per_night"] = {
        "ok": (covered_nights >= nights) if nights else None,
        "detail": f"{covered_nights}/{nights} 晚有住宿" if nights else "单日行程，无需住宿",
        "weight": 1.0,
    }

    # ② 每天有饭吃
    days_with_meal = [day for day in range(1, days + 1) if _meal_count(by_day.get(day, [])) > 0]
    checks["meals_per_day"] = {
        "ok": len(days_with_meal) >= days,
        "detail": f"{len(days_with_meal)}/{days} 天有餐饮节点",
        "weight": 1.0,
    }

    # ③ 日内里程（用与硬约束同一套预算，避免两套口径）
    tolerance = str((context or {}).get("distance_tolerance") or "normal")
    try:
        from .travel_feasibility import daily_budget_km

        budget_km = float(daily_budget_km(tolerance))
    except Exception:
        budget_km = 160.0
    worst_day, worst_km = 0, 0.0
    for day in range(1, days + 1):
        points = [node_lnglat(item) for item in by_day.get(day, [])]
        points = [point for point in points if point]
        distance = 0.0
        for first, second in zip(points, points[1:]):
            step = haversine_km(first, second)
            if step == step:
                distance += step
        if distance > worst_km:
            worst_day, worst_km = day, distance
    checks["daily_travel"] = {
        "ok": None if worst_km == 0.0 else worst_km <= budget_km,
        "detail": (
            "节点没有坐标，算不出日内里程" if worst_km == 0.0
            else f"最赶的一天是第 {worst_day} 天约 {worst_km:.0f}km（预算 {budget_km:.0f}km）"
        ),
        "weight": 1.0,
    }

    # ④ 价格出处覆盖
    priced = [item for item in nodes if str(item.get("cost_estimate") or item.get("price") or "").strip()]
    checks["price_coverage"] = {
        "ok": None if not nodes else len(priced) >= max(1, int(len(nodes) * 0.6)),
        "detail": f"{len(priced)}/{len(nodes)} 个节点带价格",
        "weight": 0.5,
    }

    # ⑤ 未知项**有没有被披露**（没价格/没核实的，要在待确认清单里出现，而不是默默略过）
    audit = plan.get("price_audit") if isinstance(plan, Mapping) else {}
    audit = audit if isinstance(audit, Mapping) else {}
    pending = audit.get("pending") or []
    unknown = int(audit.get("unknown") and len(audit.get("unknown") or []) or 0)
    if not nodes:
        checks["unknown_disclosed"] = {"ok": None, "detail": "没有节点", "weight": 1.0}
    elif unknown <= 0:
        checks["unknown_disclosed"] = {"ok": True, "detail": "没有未核实的价格", "weight": 1.0}
    else:
        checks["unknown_disclosed"] = {
            "ok": bool(pending),
            "detail": f"有 {unknown} 个未核实价格，待确认清单 {len(pending)} 条",
            "weight": 1.0,
        }

    # ⑥ 跨城腿有没有交通方案（有跨城腿才判）
    transport = plan.get("transport_audit") if isinstance(plan, Mapping) else {}
    transport = transport if isinstance(transport, Mapping) else {}
    legs = transport.get("legs") or []
    if not legs:
        checks["cross_city_transport"] = {"ok": None, "detail": "没有跨城腿", "weight": 1.0}
    else:
        with_options = len([leg for leg in legs if isinstance(leg, Mapping) and (leg.get("options") or leg.get("unverified_fares"))])
        checks["cross_city_transport"] = {
            "ok": with_options >= 1,
            "detail": f"{with_options}/{len(legs)} 段跨城腿有交通方案或未核实票价说明",
            "weight": 1.0,
        }

    scored = [(key, item) for key, item in checks.items() if item.get("ok") is not None]
    weight_sum = sum(float(item["weight"]) for _, item in scored) or 1.0
    value = sum((1.0 if item["ok"] else 0.0) * float(item["weight"]) for _, item in scored) / weight_sum
    unknown_checks = [key for key, item in checks.items() if item.get("ok") is None]
    return {
        "value": round(max(0.0, min(1.0, value)), 4),
        "checks": checks,
        "unknown_checks": unknown_checks,
        "failed_checks": [key for key, item in scored if not item["ok"]],
        "note": (
            "可判定部分：" + "；".join(f"{key}={item['detail']}" for key, item in checks.items())
            + (f"（其中 {len(unknown_checks)} 项没有依据判断）" if unknown_checks else "")
        ),
    }
