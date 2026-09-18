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

import inspect
import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .candidate_index import intent_of
from .itinerary_skeleton import CHUNK_DAYS, LONG_TRIP_DAYS, segment_days
from .plan_quality import cost_of, haversine_km, node_day, node_lnglat, node_name, nodes_of

# 每 7 天至少 1 个休整日
REST_EVERY_DAYS = 7
# 相邻两段之间的"跳跃"阈值：超过即提示应安排为交通日（不是硬失败——跨城本来就要坐车）
TRANSFER_HOP_KM = 150.0
# 分段花费超过该段分配预算的容忍比例
SEGMENT_BUDGET_TOLERANCE = 1.2
# 首轮分段生成的上限：最多 6 段（42 天）。更长的行程只生成前 6 段，
# 其余如实列入 skipped（交给后续修补），避免一次规划打几十个 LLM 请求。
SEGMENT_GENERATION_MAX_SEGMENTS = 6


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


def segment_repair_targets(plan: Any, context: Mapping[str, Any], chunk_days: int = CHUNK_DAYS) -> List[Dict[str, Any]]:
    """找出**需要重生成**的段（空天 / 住宿夜数不足 / 缺休整日）。

    这是"只重生成失败段"的输入 —— 不必整段重来。返回按严重度排序：
    `[{index, start_day, end_day, days, reasons[], brief}]`。
    """
    days = max(1, int(context.get("days") or context.get("trip_days") or 1))
    if days < LONG_TRIP_DAYS:
        return []
    segments = segment_days(days, chunk_days)
    structure = build_long_trip_plan(context, chunk_days)
    nodes = nodes_of(plan)
    by_day: Dict[int, List[Dict[str, Any]]] = {}
    for index, node in enumerate(nodes, start=1):
        by_day.setdefault(node_day(node, index), []).append(node)
    play_counts = _play_count_by_day(plan)

    targets: List[Dict[str, Any]] = []
    for segment in segments:
        start, end = int(segment["start"]), int(segment["end"])
        reasons: List[str] = []
        empty_days = [day for day in range(start, end + 1) if not by_day.get(day)]
        if empty_days:
            reasons.append("第 " + "、".join(str(day) for day in empty_days) + " 天没有安排")
        hotels = [
            node
            for day in range(start, end + 1)
            for node in by_day.get(day, [])
            if intent_of(node) == "hotel"
        ]
        expected_nights = max(0, int(segment["days"]) - 1)
        if len(hotels) < expected_nights:
            reasons.append(f"住宿仅 {len(hotels)} 夜（至少 {expected_nights} 夜）")
        if int(segment["days"]) >= REST_EVERY_DAYS and not any(
            play_counts.get(day, 0) <= 1 for day in range(start, end + 1)
        ):
            reasons.append("这一段没有休整日（每 7 天至少 1 天低强度）")
        if not reasons:
            continue
        state = next((item for item in structure["segments"] if item["start_day"] == start), None) or {
            "index": (start - 1) // chunk_days + 1,
            "start_day": start,
            "end_day": end,
            "days": int(segment["days"]),
            "budget": 0,
            "rest_days_required": required_rest_days(int(segment["days"])),
        }
        targets.append(
            {
                "index": int(state["index"]),
                "start_day": start,
                "end_day": end,
                "days": int(segment["days"]),
                "reasons": reasons,
                "brief": segment_brief(state, plan),
            }
        )
    targets.sort(key=lambda item: (-len(item["reasons"]), item["start_day"]))
    return targets


def segment_prompt(target: Mapping[str, Any]) -> str:
    """把"段目标"变成给 LLM 的指令（要求只输出该段的节点数组）。

    同一段指令同时服务两种场景：**首轮分段生成**（`reasons` 为空）与**事后修补**
    （`reasons` 说明这一段当前哪里不合格）。两者要求的输出格式完全一致。
    """
    brief = target.get("brief") or {}
    carry = brief.get("carry_in") or {}
    reasons = [str(item) for item in (target.get("reasons") or [])]
    lines = [
        f"只生成第 {target['start_day']}-{target['end_day']} 天（共 {target['days']} 天）的行程节点，不要输出其它天数。"
    ]
    if reasons:
        lines.append("这一段当前的问题：" + "；".join(reasons))
    else:
        lines.append("这是首轮分段生成：请把这几天排满，不要留空天，也不要输出其它段。")
    if carry.get("last_node"):
        lines.append(f"上一段最后停在第 {carry.get('after_day')} 天的「{carry['last_node']}」，请从这里自然衔接。")
    if brief.get("rest_days_required"):
        lines.append(f"这一段至少安排 {brief['rest_days_required']} 个低强度休整日（当天只 1 个玩点）。")
    lines.extend(
        [
            "每天必须包含：至少 1 个玩点、至少 1 个餐饮节点；跨夜需住宿节点；时间按先后递增，不要同一时间两个节点。",
            "每个节点必须含字段：day（全局天数）、name、location、time、type、cost_estimate、lnglat；"
            "价格与开放时间没有可核实来源就写「暂无供应商数据」并标 estimated=true，严禁编造。",
            "直接输出 JSON 数组（元素为节点对象），不要附加解释文字。",
        ]
    )
    return "\n".join(lines)


def merge_segment_nodes(plan: Any, start_day: int, end_day: int, nodes: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """用新生成的节点替换某一段（不修改传入 plan）。

    - 只接受 `day` 落在该段范围内的节点，范围外的**丢弃并上报**（防止 LLM 顺手改了别的天）；
    - 该段原有节点被整体替换（避免残留空天）；
    - 结果按 (day, time) 排序，保证前端按天渲染正常。
    """
    kept: List[Dict[str, Any]] = []
    for index, node in enumerate(nodes_of(plan), start=1):
        day = node_day(node, index)
        if start_day <= day <= end_day:
            continue
        kept.append(dict(node))

    incoming: List[Dict[str, Any]] = []
    out_of_range: List[int] = []
    for node in nodes or []:
        if not isinstance(node, Mapping):
            continue
        candidate = dict(node)
        try:
            day_int = int(candidate.get("day"))
        except (TypeError, ValueError):
            day_int = start_day
        if day_int < start_day or day_int > end_day:
            out_of_range.append(day_int)
            continue
        candidate["day"] = day_int
        incoming.append(candidate)

    merged = kept + incoming
    merged.sort(key=lambda item: (int(item.get("day") or 1), str(item.get("time") or "")))
    new_plan: Any = dict(plan) if isinstance(plan, Mapping) else {}
    new_plan["route"] = merged
    return {"plan": new_plan, "added": len(incoming), "out_of_range": out_of_range}


async def _maybe_await(value: Any) -> Any:
    """生成器既可以是协程也可以是普通函数（离线单测就用普通函数）。"""
    if inspect.isawaitable(value):
        return await value
    return value


async def build_segmented_plan(
    plan: Any,
    context: Mapping[str, Any],
    generate: Callable[[Mapping[str, Any]], Any],
    chunk_days: int = CHUNK_DAYS,
    max_segments: int = SEGMENT_GENERATION_MAX_SEGMENTS,
) -> Dict[str, Any]:
    """**首轮**分段生成：按 chunk 天逐段生成、边生成边合并，段间简报基于已落地的真实计划。

    与 `segment_repair_targets()`（事后修补失败段）的分工：
    - 这里是"先分好、再生成"：每一段的 `brief.carry_in` 都取自**上一段真实产出的最后节点**，
      所以 30 天这种量级不会因为一次生成太长而漏天、断链、编造；
    - 生成完之后仍然跑一次 `segment_repair_targets()`，把**仍不合格**的段如实列出来，
      交给调用方后续修补 —— 首轮生成与修补是两件事，不混成一件。

    约定（都是硬规矩）：
    - `generate(target) -> nodes | None` 由调用方注入（api 层接 LLM），可以返回 awaitable；
      返回 None / 空数组 = 这一段生成失败；
    - **单段失败不影响其它段**，也不抛异常；已生成的段全部保留；
    - 越界天数的节点由 `merge_segment_nodes` 丢弃并上报（防"顺手改了别的天"）；
    - 超过 `max_segments` 的段不生成，如实放进 `skipped_segments`（不打几十个 LLM 请求）。
    """
    days = max(1, int(context.get("days") or context.get("trip_days") or 1))
    structure = build_long_trip_plan(context, chunk_days)
    segments = structure["segments"]
    working: Any = plan
    generated: List[int] = []
    failed: List[int] = []
    skipped: List[int] = []
    records: List[Dict[str, Any]] = []

    budget_for = {item["index"]: item for item in segments}

    for index, segment in enumerate(segments):
        if index >= max(1, int(max_segments)):
            skipped.append(int(segment["index"]))
            continue
        state = budget_for[int(segment["index"])]
        target: Dict[str, Any] = {
            "index": int(segment["index"]),
            "start_day": int(segment["start_day"]),
            "end_day": int(segment["end_day"]),
            "days": int(segment["days"]),
            "reasons": [],
            "brief": segment_brief(state, working),
        }
        record: Dict[str, Any] = {
            "index": int(segment["index"]),
            "start_day": int(segment["start_day"]),
            "end_day": int(segment["end_day"]),
            "days": int(segment["days"]),
            "ok": False,
            "added": 0,
            "out_of_range": [],
        }
        try:
            nodes = await _maybe_await(generate(target))
        except Exception as exc:  # 单段失败不影响其它段，也不阻断出方案
            record["error"] = str(exc)[:200]
            nodes = None
        if not nodes:
            record["error"] = record.get("error") or "这一段没有产出节点"
            failed.append(int(segment["index"]))
            records.append(record)
            continue
        merged = merge_segment_nodes(working, int(segment["start_day"]), int(segment["end_day"]), nodes)
        working = merged["plan"]
        record["added"] = int(merged["added"])
        record["out_of_range"] = list(merged["out_of_range"])[:5]
        record["ok"] = int(merged["added"]) > 0
        if record["ok"]:
            generated.append(int(segment["index"]))
        else:
            failed.append(int(segment["index"]))
            record["error"] = record.get("error") or "这一段产出的节点全部越界，已丢弃"
        records.append(record)

    remaining = segment_repair_targets(working, context, chunk_days)
    return {
        "plan": working,
        "applied": bool(generated),
        "is_long_trip": days >= LONG_TRIP_DAYS,
        "days": days,
        "chunk_days": chunk_days,
        "max_segments": max(1, int(max_segments)),
        "truncated": bool(skipped),
        "segments": records,
        "generated_segments": generated,
        "failed_segments": failed,
        "skipped_segments": skipped,
        "needs_repair_segments": [int(item["start_day"]) for item in remaining],
        "continuity": continuity_report(working, context, chunk_days),
    }


def summarize_segments(plan: Any, context: Mapping[str, Any], chunk_days: int = CHUNK_DAYS) -> Dict[str, Any]:
    """给前端/报告的段级摘要：每段天数、节点数、休整日、是否需要修补。"""
    days = max(1, int(context.get("days") or context.get("trip_days") or 1))
    segments = segment_days(days, chunk_days)
    by_day: Dict[int, int] = {}
    for index, node in enumerate(nodes_of(plan), start=1):
        by_day[node_day(node, index)] = by_day.get(node_day(node, index), 0) + 1
    play_counts = _play_count_by_day(plan)
    report = continuity_report(plan, context, chunk_days)
    failing = {item["start_day"] for item in segment_repair_targets(plan, context, chunk_days)}
    return {
        "segments": [
            {
                "index": index + 1,
                "start_day": int(segment["start"]),
                "end_day": int(segment["end"]),
                "days": int(segment["days"]),
                "nodes": sum(by_day.get(day, 0) for day in range(int(segment["start"]), int(segment["end"]) + 1)),
                "rest_days": sum(
                    1 for day in range(int(segment["start"]), int(segment["end"]) + 1) if play_counts.get(day, 0) <= 1
                ),
                "needs_repair": int(segment["start"]) in failing,
            }
            for index, segment in enumerate(segments)
        ],
        "ok": report["ok"],
        "failures": report["failures"],
        "advisories": report["advisories"],
    }
