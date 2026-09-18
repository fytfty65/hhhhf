"""方案复核（critic）与确定性修补（repair）：能自动修的先修掉，修不动的如实说。

为什么单独一层
--------------
`plan_quality` 只回答"这份方案多少分、哪些硬约束没过"，但**不修**。实际模型产出里，
相当一部分问题是**纯确定性就能修好的**：

- 同一天时间倒挂 / 时间超出可用时段 / 时间没落在自己的开放时间内 → 重排时间即可；
- 重复地点（同名节点出现两次）→ 删掉后出现的那个；
- 单日节点超量 → 按天限流（保留酒店）。

而另一部分**只能靠数据或用户**：缺玩点/缺餐/缺住宿夜数（要从候选池补点）、预算超支
（要做平替，得先有可比价的候选）、结构性改动（改天数/换城市）。这一层把它们分开：

- 能修的：确定性修补，**改了什么写进 actions**；
- 不能修的：**不猜、不编**，留在 issues 里并标 `needs_data`，交给候选池/价格检索或用户确认。

循环协议（propose → critique → repair → rescore）
------------------------------------------------
`review_plan()` 最多 `max_rounds` 轮（默认 3）：

1. 先 critique：没有硬失败 → `stopped_reason="clean"`，一轮都不修（**不为了修而修**）；
2. 没有任何可确定性修补的 issue → `stopped_reason="needs_data"`，如实上报；
3. 修补后重新打分：**分数不升就回滚到最好的一版**（掉分即回滚，绝不"越修越差"）；
4. 修补实际没改动任何东西 → 立刻停（`stopped_reason="no_change"`）——
   避免空转烧预算（这是从同类开源项目 FloatTrip 的 staleness 检测里学到的教训）；
5. 到达轮数上限 → `stopped_reason="max_rounds"`。

只做"可审计"的改动：每个 action 都能说清改了哪个节点、从什么改成什么。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .optimization import repair_route
from .plan_quality import (
    DAY_END_MIN,
    DAY_START_MIN,
    evaluate_plan,
    node_day,
    node_name,
    nodes_of,
    parse_clock,
    parse_open_window,
)

# 一轮修补里最小的时间步长（分钟）：给同一天相邻节点留出移动/吃饭的余量
SLOT_STEP_MIN = 90
# 默认每天第一个节点从几点开始
DEFAULT_FIRST_SLOT_MIN = 9 * 60
# 默认每天最后一个节点不晚于几点
DEFAULT_LAST_SLOT_MIN = 21 * 60
# 住宿节点最早的安排时间（避免"下午三点回酒店睡觉"）
LODGING_SLOT_MIN = 20 * 60

# 硬约束码 → 能否确定性修补
FIX_BY_CODE: Dict[str, str] = {
    "day_out_of_order": "reschedule",
    "time_out_of_window": "reschedule",
    "time_missing": "reschedule",
    "opening_hours_conflict": "reschedule",
    "duplicate_location": "dedupe",
    "daily_capacity_exceeded": "trim_day",
    "day_empty": "needs_candidates",
    "day_too_thin": "needs_candidates",
    "must_have_missing": "needs_candidates",
    "skeleton_meal_missing": "needs_candidates",
    "skeleton_meal_shortfall": "needs_candidates",
    "skeleton_play_missing": "needs_candidates",
    "skeleton_lodging_shortfall": "needs_candidates",
    "open_time_missing": "needs_data",
    "budget_exceeded": "needs_candidates",
    "budget_at_risk": "needs_data",
    "budget_unverifiable": "needs_data",
    "budget_partial": "needs_data",
    "long_trip_day_gap": "needs_candidates",
    "long_trip_day_out_of_range": "drop_out_of_range",
    "long_trip_lodging_shortfall": "needs_candidates",
}

# 这些码属于"信息性"，不参与"要不要修"的判断（缺数据永远修不了，别因此卡住）
ADVISORY_CODES = {"budget_at_risk", "budget_unverifiable", "budget_partial", "open_time_missing"}


def _issue(code: str, detail: str, severity: str) -> Dict[str, Any]:
    return {
        "code": code,
        "detail": detail,
        "severity": severity,
        "fix": FIX_BY_CODE.get(code, "manual"),
    }


def critique_plan(
    context: Mapping[str, Any],
    plan: Any,
    expectations: Optional[Mapping[str, Any]] = None,
    signals: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """给方案挑毛病：硬失败 + 未核实，并把每条标成"能修/要数据/只能人工"。"""
    report = evaluate_plan(context, plan, expectations=expectations, signals=signals)
    gate = report.get("gate") or {}
    issues: List[Dict[str, Any]] = [_issue(str(item.get("code")), str(item.get("detail") or ""), "hard")
                                   for item in gate.get("failures") or []]
    issues.extend(_issue(str(item.get("code")), str(item.get("detail") or ""), "unverifiable")
                  for item in gate.get("unverifiable") or [])

    fixable = sorted({item["fix"] for item in issues if item["severity"] == "hard" and item["fix"] in {"reschedule", "dedupe", "trim_day", "drop_out_of_range"}})
    needs_data = sorted({item["fix"] for item in issues if item["fix"] in {"needs_candidates", "needs_data"}})
    return {
        "score": report.get("score"),
        "verdict": report.get("verdict"),
        "gate_passed": bool(gate.get("passed")),
        "hard_failures": [item for item in issues if item["severity"] == "hard"],
        "unverifiable": [item for item in issues if item["severity"] == "unverifiable"],
        "issues": issues,
        "fixable": fixable,
        "needs_data": needs_data,
        "dimensions": report.get("dimensions"),
    }


def _slot_window(node: Mapping[str, Any]) -> tuple[int, int]:
    """这个节点自己允许的时间范围：优先用自己的开放时间，否则用一天的通用时段。"""
    window = parse_open_window(node.get("open_time"))
    if window is None:
        return DEFAULT_FIRST_SLOT_MIN, DEFAULT_LAST_SLOT_MIN
    open_min, close_min = window
    if close_min < open_min:  # 跨零点营业（夜市等）：压到当天时段里
        return max(DEFAULT_FIRST_SLOT_MIN, close_min), DEFAULT_LAST_SLOT_MIN
    return max(DEFAULT_FIRST_SLOT_MIN, open_min), min(DEFAULT_LAST_SLOT_MIN, close_min)


def _fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _is_lodging(node: Mapping[str, Any]) -> bool:
    text = f"{node_name(node)} {node.get('type') or ''} {' '.join(str(tag) for tag in (node.get('tags') or []))}"
    return any(key in text for key in ("住宿", "酒店", "民宿", "hotel", "客栈", "青旅")) or bool(node.get("is_hotel"))


def _reschedule_day(day: int, day_nodes: Sequence[Mapping[str, Any]], context: Mapping[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把一天的节点重排到合法时段里。

    三条规矩：
    1. **保持方案原有的节点顺序**（顺序是行程意图，时间才是出错的那一项）；
    2. **能不动就不动**：原本就合法（在时间窗内、在自己开放时间内、且不早于前一个节点）的
       时间原样保留，只是把颠倒/越界的那几个重新分配 —— 所以一份本来没问题的方案，
       过一遍不会产生任何改动（也就不会白白掉分）；
    3. 住宿节点排到最后（20:00 之后），免得出现"下午三点回酒店睡觉"。

    重排时优先复用当天已有的合法时间（相当于把时间"换位"而不是整体平移），
    实在没有可用时间才按 90 分钟步长合成一个。
    """
    ordered = [node for node in day_nodes if not _is_lodging(node)]
    ordered.extend(node for node in day_nodes if _is_lodging(node))
    windows: List[tuple[Mapping[str, Any], int, int]] = []
    for node in ordered:
        low, high = _slot_window(node)
        if _is_lodging(node):
            low = max(low, LODGING_SLOT_MIN)
            high = max(high, LODGING_SLOT_MIN)
        windows.append((node, low, high))

    # 当天"本来就合法"的时间（对各自节点而言在窗口内、也在一天可用时段内），升序备用
    pool: List[int] = []
    for node, low, high in windows:
        clock = parse_clock(node.get("time"))
        if clock is None:
            continue
        if DAY_START_MIN <= clock <= DAY_END_MIN and low <= clock <= min(high, DAY_END_MIN):
            pool.append(clock)
    pool.sort()

    actions: List[Dict[str, Any]] = []
    output: List[Dict[str, Any]] = []
    previous: Optional[int] = None
    for node, low, high in windows:
        ceiling = min(high, DAY_END_MIN)
        chosen: Optional[int] = None
        for index, candidate in enumerate(pool):
            if (previous is None or candidate >= previous) and DAY_START_MIN <= candidate <= ceiling:
                chosen = pool.pop(index)
                break
        if chosen is None:
            baseline = (previous + SLOT_STEP_MIN) if previous is not None else max(low, DAY_START_MIN)
            chosen = max(baseline, low, DAY_START_MIN)
            if chosen > ceiling:
                chosen = min(max(low, DAY_START_MIN), DAY_END_MIN)
        chosen = int(min(max(chosen, DAY_START_MIN), DAY_END_MIN))
        new_node = dict(node)
        old_time = node.get("time")
        new_node["time"] = _fmt(chosen)
        if str(old_time or "") != new_node["time"]:
            actions.append(
                {
                    "code": "reschedule",
                    "day": day,
                    "node": node_name(node),
                    "from": old_time,
                    "to": new_node["time"],
                }
            )
        output.append(new_node)
        previous = chosen
    return output, actions


def repair_plan(
    context: Mapping[str, Any],
    plan: Any,
    expectations: Optional[Mapping[str, Any]] = None,
    max_nodes_per_day: Optional[int] = None,
) -> Dict[str, Any]:
    """只做确定性修补；**绝不新增编造的节点**（缺的玩点/餐/住宿留给候选池）。"""
    days = int(context.get("days") or context.get("trip_days") or 0) or 0
    limit = max_nodes_per_day
    if limit is None and expectations:
        raw_limit = expectations.get("max_nodes_per_day")
        if isinstance(raw_limit, (int, float)) and raw_limit > 0:
            limit = int(raw_limit)

    original = [dict(node) for node in nodes_of(plan) if isinstance(node, Mapping)]
    actions: List[Dict[str, Any]] = []

    # 0) 先按天分组（保留原有顺序）
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for index, node in enumerate(original, start=1):
        grouped.setdefault(node_day(node, index), []).append(node)

    # 1) 越界天数直接丢弃并上报（改天数/长途残留时会出现）
    dropped_out_of_range: List[int] = []
    for day in sorted(grouped):
        if days and (day < 1 or day > days):
            dropped_out_of_range.extend([day] * len(grouped.pop(day)))
    if dropped_out_of_range:
        actions.append({"code": "drop_out_of_range", "days": sorted(set(dropped_out_of_range))[:5]})

    # 2) **按天**去重 + 单日限流（复用既有确定性实现）。
    #    注意必须按天调用 repair_route：它按名字去重，而"同一家酒店连住三晚"在数据里
    #    就是同名节点出现三次 —— 全局去重会把住宿夜数删掉，反而造出新的硬失败。
    kept: List[Dict[str, Any]] = []
    for day in sorted(grouped):
        day_nodes = grouped[day]
        trimmed = [dict(node) for node in repair_route(day_nodes, max_nodes_per_day=limit)]
        kept_names = {node_name(node) for node in trimmed}
        removed = [node_name(node) for node in day_nodes if node_name(node) and node_name(node) not in kept_names]
        if removed:
            actions.append({"code": "dedupe", "day": day, "dropped": removed[:5], "count": len(removed)})
        kept.extend(trimmed)

    # 3) 逐日重排时间（顺序 + 时间窗 + 自己的开放时间）
    by_day: Dict[int, List[Dict[str, Any]]] = {}
    for index, node in enumerate(kept, start=1):
        by_day.setdefault(node_day(node, index), []).append(node)
    rebuilt: List[Dict[str, Any]] = []
    for day in sorted(by_day):
        scheduled, day_actions = _reschedule_day(day, by_day[day], context)
        actions.extend(day_actions)
        rebuilt.extend(scheduled)

    new_plan: Any = dict(plan) if isinstance(plan, Mapping) else {}
    new_plan["route"] = rebuilt
    changed = [dict(node) for node in rebuilt] != original
    return {"plan": new_plan, "actions": actions, "changed": changed}


def review_plan(
    context: Mapping[str, Any],
    plan: Any,
    expectations: Optional[Mapping[str, Any]] = None,
    signals: Optional[Mapping[str, Any]] = None,
    max_rounds: int = 3,
    repair: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """propose → critique → repair → rescore，最多 `max_rounds` 轮；掉分/掉门禁即回滚。

    接受标准是"**硬失败更少**，或者硬失败一样多而分数更高" —— 只看分数会犯这种错：
    重排时间修掉了"时间倒流"，但节奏维度分略降，于是"修好了却回滚"，用户拿到的
    还是那份时间倒流的方案。硬约束永远优先于加权分。
    """
    repair_fn = repair or (lambda ctx, current, exp=None: repair_plan(ctx, current, exp))
    current = plan
    initial_critique = critique_plan(context, plan, expectations, signals)
    best = plan
    best_critique = initial_critique
    best_score = float(initial_critique.get("score") or 0.0)
    best_hard = len(initial_critique.get("hard_failures") or [])
    rounds: List[Dict[str, Any]] = []
    stopped_reason = "clean"

    for index in range(max(0, int(max_rounds))):
        critique = critique_plan(context, current, expectations, signals)
        fixable = list(critique["fixable"])
        if not critique["hard_failures"]:
            stopped_reason = "clean"
            break
        if not fixable:
            stopped_reason = "needs_data"
            break
        outcome = repair_fn(context, current, expectations)
        actions = outcome.get("actions") or []
        if not outcome.get("changed"):
            rounds.append(
                {
                    "round": index + 1,
                    "score_before": critique.get("score"),
                    "score_after": critique.get("score"),
                    "hard_before": len(critique["hard_failures"]),
                    "hard_after": len(critique["hard_failures"]),
                    "actions": actions,
                    "accepted": False,
                }
            )
            stopped_reason = "no_change"
            break
        rescored = critique_plan(context, outcome["plan"], expectations, signals)
        new_score = float(rescored.get("score") or 0.0)
        new_hard = len(rescored.get("hard_failures") or [])
        accepted = new_hard < best_hard or (new_hard == best_hard and new_score > best_score)
        rounds.append(
            {
                "round": index + 1,
                "score_before": critique.get("score"),
                "score_after": rescored.get("score"),
                "hard_before": len(critique["hard_failures"]),
                "hard_after": new_hard,
                "actions": actions,
                "accepted": accepted,
            }
        )
        if not accepted:
            stopped_reason = "no_improvement"
            break
        best, best_score, best_hard, best_critique, current = outcome["plan"], new_score, new_hard, rescored, outcome["plan"]
    else:
        if rounds:
            stopped_reason = "max_rounds"

    return {
        "plan": best,
        "rounds": rounds,
        "stopped_reason": stopped_reason,
        "initial_score": float(initial_critique.get("score") or 0.0),
        "final_score": best_score,
        "initial_hard_failures": len(initial_critique.get("hard_failures") or []),
        "remaining_hard": best_hard,
        "improved": [dict(node) for node in nodes_of(best)] != [dict(node) for node in nodes_of(plan)],
        "needs_data": best_critique.get("needs_data") or [],
        "actions": [action for item in rounds for action in (item.get("actions") or [])],
    }
