"""长途（≥14 天）分段生成支撑：分段预算、休整日、段间衔接校验、下一段生成简报。

问题
----
30 天 × 每天 4-5 个节点 ≈ 150+ 节点，靠**单次 LLM 生成不可靠**（会截断、漏天数、编造）。
确定性层能做的、也必须先做的是：把长途拆成可生成、可校验的段，并给每段明确的"进入状态"与约束。

本模块提供
----------
- `build_long_trip_plan()`：按 chunk 天分段，给出每段预算、需要几个休整日、进出状态位；
- `segment_brief()`：给"生成第 i 段"用的**结构化简报**（上一段结束在哪、还剩多少钱、这周至少几个休整日、
  需要避开的城市/地点），让分段生成有衔接依据，而不是各自为政；
- `continuity_report()`：段间衔接校验（天数是否连续完整、跨段跳跃是否合理、住宿夜数是否对得上、
  休整日够不够、分段花费是否超分配），可直接作为长行程的门禁/顾问输出。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .candidate_index import intent_of
from .itinerary_skeleton import CHUNK_DAYS, LONG_TRIP_DAYS, segment_days
from .plan_quality import cost_of, haversine_km, node_day, node_lnglat, node_name, nodes_of

# 每 7 天至少 1 个休整日
REST_EVERY_DAYS = 7
# 相邻两段之间的"跳跃"阈值：超过即提示应安排为交通日（不是硬失败——跨城本来就要坐车）
TRANSFER_HOP_KM = 150.0
# 分段花费超过该段分配预算的容忍比例
SEGMENT_BUDGET_TOLERANCE = 1.2


def _play_count_by_day(plan: Any) -> Dict[int, int]:
    counts: Dict[int, int] = {}
    for index, node in enumerate(nodes_of(plan), start=1):
        label = intent_of(node)
        if label in {"hotel", "food"}:
            continue
        counts[node_day(node, index)] = counts.get(node_day(node, index), 0) + 1
    return counts


def required_rest_days(days: int) -> int:
    """每 7 天至少 1 个休整日（不足 7 天不强制）。"""
    return max(0, int(days) // REST_EVERY_DAYS)


def allocate_budget(total_budget: float, segments: Sequence[Mapping[str, Any]]) -> List[float]:
    """把总预算按段天数比例分配；最后一段吃掉余数（避免四舍五入后对不上总额）。"""
    total = max(0.0, float(total_budget or 0))
    if total <= 0 or not segments:
        return [0.0 for _ in segments]
    total_days = sum(int(item.get("days") or 0) for item in segments) or 1
    allocated: List[float] = []
    remaining = total
    for index, item in enumerate(segments):
        if index == len(segments) - 1:
            allocated.append(round(remaining, 2))
            break
        share = round(total * (int(item.get("days") or 0) / total_days), 2)
        allocated.append(share)
        remaining -= share
    return allocated


def build_long_trip_plan(context: Mapping[str, Any], chunk_days: int = CHUNK_DAYS) -> Dict[str, Any]:
    """把长途拆成段，并给出每段的预算 / 休整日要求 / 进出状态位。"""
    days = max(1, int(context.get("days") or context.get("trip_days") or 1))
    segments = segment_days(days, chunk_days)
    budgets = allocate_budget(float(context.get("budget") or 0), segments)

    plans: List[Dict[str, Any]] = []
    carry_in: Optional[Dict[str, Any]] = None
    for index, segment in enumerate(segments):
        segment_days_count = int(segment["days"])
        rest_needed = required_rest_days(segment_days_count)
        plans.append(
            {
                "index": index + 1,
                "start_day": segment["start"],
                "end_day": segment["end"],
                "days": segment_days_count,
                "budget": budgets[index],
                "rest_days_required": rest_needed,
                "lodging_nights_expected": max(0, segment_days_count - 1),
                "carry_in": carry_in,
                "carry_out": None,  # 生成该段后回填（城市/最后节点/剩余预算）
            }
        )
        # 简化状态位：假定同一段内城市连续（真正的城市由生成阶段决定，这里给出结构）
        carry_in = {"after_day": segment["end"], "city": None, "last_node": None, "budget_left": None}
    return {
        "days": days,
        "chunk_days": chunk_days,
        "is_long_trip": days >= LONG_TRIP_DAYS,
        "segments": plans,
        "advisories": [
            f"按 {chunk_days} 天分段生成与校验（共 {len(plans)} 段）",
            f"每 7 天至少 1 个休整日（全程至少 {required_rest_days(days)} 天）",
            "住宿按『同一城市连续住』聚合，减少反复换酒店",
        ],
    }


def segment_brief(state: Mapping[str, Any], plan: Any) -> Dict[str, Any]:
    """生成"第 N 段"用的简报：接着哪一天、从哪出发、还剩多少钱、这段必须几个休整日。"""
    day = int(state.get("start_day") or 1)
    end_day = int(state.get("end_day") or day)
    previous = [
        node
        for index, node in enumerate(nodes_of(plan), start=1)
        if node_day(node, index) < day
    ]
    last_node = previous[-1] if previous else None
    spent = sum((cost_of(node) or 0.0) for node in previous)
    budget = float(state.get("budget") or 0)
    return {
        "segment_index": state.get("index"),
        "generate_days": f"{day}-{end_day}",
        "carry_in": {
            "after_day": (day - 1) if day > 1 else None,
            "from_city": (last_node or {}).get("city") if isinstance(last_node, Mapping) else None,
            "last_node": node_name(last_node) if isinstance(last_node, Mapping) else None,
            "last_lnglat": node_lnglat(last_node) if isinstance(last_node, Mapping) else None,
        },
        "budget": {"segment": budget, "already_spent_before": round(spent, 2), "left_overall": round(max(0.0, budget - spent), 2)},
        "rest_days_required": int(state.get("rest_days_required") or 0),
        "constraints": [
            "不要重复前面已经排过的地点",
            f"第 {day}-{end_day} 天每天至少 1 个玩点、1 个餐饮节点",
            "跨城请安排在交通日，不要同一天既跨城又排 3 个以上景点",
        ],
    }


def continuity_report(plan: Any, context: Mapping[str, Any], chunk_days: int = CHUNK_DAYS) -> Dict[str, Any]:
    """段间衔接校验：天数连续性、跨段跳跃、住宿夜数、休整日、分段花费。

    硬失败（failures）：天数缺口/越界、某段住宿夜数明显不够。
    顾问（advisories）：跨段跳跃过大、休整日不足、分段花费超分配 —— 这些要靠生成阶段解决，
    不适合直接判死（跨城本来就要坐车）。
    """
    days = max(1, int(context.get("days") or context.get("trip_days") or 1))
    expectations = context.get("expectations") if isinstance(context.get("expectations"), Mapping) else {}
    nodes = nodes_of(plan)
    failures: List[Dict[str, Any]] = []
    advisories: List[str] = []

    by_day: Dict[int, List[Dict[str, Any]]] = {}
    for index, node in enumerate(nodes, start=1):
        by_day.setdefault(node_day(node, index), []).append(node)

    # 1) 天数连续完整
    for day in range(1, days + 1):
        if not by_day.get(day):
            failures.append({"code": "long_trip_day_gap", "detail": f"第 {day} 天没有安排（长途行程不允许空天）"})
    for day in sorted(by_day):
        if day < 1 or day > days:
            failures.append({"code": "long_trip_day_out_of_range", "detail": f"存在第 {day} 天，超出 {days} 天范围"})

    # 2) 分段衔接（跨段跳跃）
    segments = segment_days(days, chunk_days)
    for index in range(len(segments) - 1):
        current = segments[index]
        following = segments[index + 1]
        tail = by_day.get(int(current["end"])) or []
        head = by_day.get(int(following["start"])) or []
        tail_pos = next((node_lnglat(node) for node in reversed(tail) if node_lnglat(node)), None)
        head_pos = next((node_lnglat(node) for node in head if node_lnglat(node)), None)
        if tail_pos and head_pos:
            hop = haversine_km(tail_pos, head_pos)
            if hop > TRANSFER_HOP_KM:
                advisories.append(
                    f"第 {current['end']} 天到第 {following['start']} 天之间跨约 {hop:.0f} km，"
                    "建议把第 %d 天设为交通日并确认高铁/航班衔接" % int(following["start"])
                )

    # 3) 住宿夜数（每段至少 段天数-1 夜）
    for index, segment in enumerate(segments, start=1):
        start, end = int(segment["start"]), int(segment["end"])
        hotels = [
            node
            for day in range(start, end + 1)
            for node in by_day.get(day, [])
            if intent_of(node) == "hotel"
        ]
        expected = max(0, int(segment["days"]) - 1)
        if len(hotels) < expected:
            failures.append(
                {
                    "code": "long_trip_lodging_shortfall",
                    "detail": f"第 {index} 段（第 {start}-{end} 天）住宿仅 {len(hotels)} 夜，至少需要 {expected} 夜",
                }
            )

    # 4) 休整日
    play_counts = _play_count_by_day(plan)
    rest_days = [day for day, count in play_counts.items() if count <= 1]
    need_rest = required_rest_days(days)
    if need_rest and len(rest_days) < need_rest:
        advisories.append(
            f"全程只有 {len(rest_days)} 天属低强度（≤1 个玩点），建议至少 {need_rest} 天休整"
        )

    # 5) 分段花费 vs 分配预算
    budgets = allocate_budget(float(context.get("budget") or 0), segments)
    for index, segment in enumerate(segments, start=1):
        start, end = int(segment["start"]), int(segment["end"])
        spent = sum(
            (cost_of(node) or 0.0)
            for day in range(start, end + 1)
            for node in by_day.get(day, [])
        )
        allowance = budgets[index - 1]
        if allowance > 0 and spent > allowance * SEGMENT_BUDGET_TOLERANCE:
            advisories.append(
                f"第 {index} 段花费 ¥{spent:.0f} 超过该段分配 ¥{allowance:.0f} 的 {int((SEGMENT_BUDGET_TOLERANCE - 1) * 100)}% 以上"
            )

    # 6) 显式期望的休整日下限
    explicit_rest = expectations.get("min_rest_days")
    if isinstance(explicit_rest, int) and explicit_rest > len(rest_days):
        advisories.append(f"用例要求至少 {explicit_rest} 个休整日，当前 {len(rest_days)} 天")

    return {
        "ok": not failures,
        "failures": failures,
        "advisories": advisories,
        "segments": len(segments),
        "rest_days": len(rest_days),
        "rest_days_required": need_rest,
        "is_long_trip": days >= LONG_TRIP_DAYS,
        "budgets": budgets,
    }
