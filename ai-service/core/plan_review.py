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
    # 构成策略相关：缺自然景观 → 从候选池补；文化类超上限/类型太单一 → 按策略换掉一个
    "scenic_shortfall": "needs_candidates",
    "category_cap_exceeded": "rebalance",
    "play_category_monotony": "rebalance",
}

# 这些码属于"信息性"，不参与"要不要修"的判断（缺数据永远修不了，别因此卡住）
ADVISORY_CODES = {"budget_at_risk", "budget_unverifiable", "budget_partial", "open_time_missing"}

# "补点"用得到的玩点意图（候选池按意图分组）
PLAY_INTENTS: tuple = (
    "cultural", "scenic", "landmark", "outdoor", "family", "photo", "market", "nightlife", "shopping",
)
# 确定性修补（不需要新数据）
DETERMINISTIC_FIXES = {"reschedule", "dedupe", "trim_day", "drop_out_of_range"}
# 候选池修补（需要真实候选，绝不编造节点）
POOL_FIXES = {"needs_candidates", "rebalance"}


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
    pool_available: bool = False,
) -> Dict[str, Any]:
    """给方案挑毛病：硬失败 + 未核实，并把每条标成"能修/要数据/只能人工"。

    `pool_available=True`（调用方已经拿到真实候选池）时，"缺玩点/缺餐/缺住宿夜数"这类
    问题才被算成**可修补** —— 没有候选池时它们只能如实上报，绝不能凭空造节点。
    """
    report = evaluate_plan(context, plan, expectations=expectations, signals=signals)
    gate = report.get("gate") or {}
    issues: List[Dict[str, Any]] = [_issue(str(item.get("code")), str(item.get("detail") or ""), "hard")
                                   for item in gate.get("failures") or []]
    issues.extend(_issue(str(item.get("code")), str(item.get("detail") or ""), "unverifiable")
                  for item in gate.get("unverifiable") or [])

    allowed = DETERMINISTIC_FIXES | (POOL_FIXES if pool_available else set())
    fixable = sorted({item["fix"] for item in issues if item["severity"] == "hard" and item["fix"] in allowed})
    needs_data = sorted(
        {item["fix"] for item in issues if item["fix"] in {"needs_candidates", "needs_data"} and item["fix"] not in allowed}
    )
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


def _intent_of_node(node: Mapping[str, Any]) -> str:
    from .candidate_index import intent_of

    return intent_of(node)


def _is_cultural_node(node: Mapping[str, Any]) -> bool:
    """是不是"文化类"玩点（博物馆/古迹等）——用于按策略把它换成自然景观。"""
    from .plan_quality import _coarse_category

    return _coarse_category(node) == "cultural"


def _pool_pick(
    pool: Optional[Mapping[str, Sequence[Mapping[str, Any]]]],
    intent: str,
    used: set,
    prefer_evening: bool = False,
) -> Optional[Dict[str, Any]]:
    """从候选池里挑一个**还没用过**的真实候选；没有就返回 None（绝不编造）。

    `prefer_evening=True`（住宿）时优先挑"开放时间缺失或覆盖到晚上"的候选 —— 否则会出现
    "酒店 20:00 入住、但数据写着 18:00 关门"这种自相矛盾的安排。
    """
    if not pool:
        return None
    candidates = [dict(item) for item in (pool.get(intent) or []) if str(item.get("name") or "").strip()]
    if prefer_evening:
        def evening_ok(item: Mapping[str, Any]) -> bool:
            window = parse_open_window(item.get("open_time"))
            if window is None:
                return True  # 没有开放时间数据：不算冲突（后面记"未核实"）
            _, close = window
            return close >= LODGING_SLOT_MIN or close < 6 * 60  # 覆盖到深夜或跨零点营业

        candidates.sort(key=lambda item: 0 if evening_ok(item) else 1)
    for candidate in candidates:
        name = str(candidate.get("name") or "").strip()
        if name in used:
            continue
        used.add(name)
        return candidate
    return None


def fill_structural_gaps(
    context: Mapping[str, Any],
    plan: Any,
    expectations: Optional[Mapping[str, Any]] = None,
    pool: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
) -> Dict[str, Any]:
    """用**真实候选池**补结构缺口：空天/缺玩点、缺餐、住宿夜数不够、必去项缺失。

    三条硬规矩：
    - 没有候选池 → 什么都不做（返回 `changed=False`），交给上层如实上报；
    - 只从候选池取节点，经 `build_node_from_candidate` 生成（价格/评分/开放时间缺就是"未核实"）；
    - 同一个候选只用一次，且绝不与方案里已有的名字重复。
    """
    from .increment import build_node_from_candidate

    if not pool:
        return {"plan": plan, "actions": [], "changed": False}

    expectations = expectations or {}
    days = int(context.get("days") or context.get("trip_days") or 0) or 0
    cap = expectations.get("max_nodes_per_day")
    cap = int(cap) if isinstance(cap, (int, float)) and cap > 0 else None

    nodes = [dict(node) for node in nodes_of(plan)]
    used = {str(node.get("name") or "").strip() for node in nodes}
    actions: List[Dict[str, Any]] = []
    additions: List[Dict[str, Any]] = []

    def add(day: int, intent: str, code: str, time_hint: str, detail: str = "") -> bool:
        candidate = _pool_pick(pool, intent, used, prefer_evening=(intent == "hotel"))
        if not candidate:
            return False
        additions.append(build_node_from_candidate(candidate, day, time_hint, intent))
        actions.append(
            {
                "code": code,
                "day": day,
                "node": candidate.get("name"),
                "intent": intent,
                "source": candidate.get("price_source") or candidate.get("source") or "unavailable",
                "from": None,
                "to": None,
                "detail": detail,
            }
        )
        return True

    if days:
        by_day: Dict[int, List[Dict[str, Any]]] = {}
        for node in nodes:
            by_day.setdefault(node_day(node, 1), []).append(node)

        for day in range(1, days + 1):
            day_nodes = by_day.get(day, [])
            intents = {_intent_of_node(node) for node in day_nodes}
            plays = [node for node in day_nodes if _intent_of_node(node) not in {"food", "hotel"}]
            room = None if cap is None else max(0, cap - len(day_nodes))

            if not day_nodes:
                if (cap is None or room >= 1) and any(_pool_pick(pool, intent, set()) for intent in PLAY_INTENTS):
                    if add(day, _first_play_intent(pool, used), "fill_day", "10:00", "这一天原本是空的"):
                        room = None if cap is None else max(0, cap - 1)
            elif not plays:
                if (cap is None or room >= 1) and any(_pool_pick(pool, intent, set()) for intent in PLAY_INTENTS):
                    if add(day, _first_play_intent(pool, used), "add_play", "10:00", "这一天只有吃住、没有玩点"):
                        room = None if cap is None else max(0, room - 1)

            if "food" not in intents and (cap is None or (room or 0) >= 1):
                add(day, "food", "add_meal", "12:30", "这一天没有餐饮节点")

        # ---- 按**构成策略**补/换（策略来自"目的地 + 偏好 + 二次增量"）----
        # 用户要求"多打卡自然景观"时，这里就会优先补自然景观；文化类超上限时按策略换掉一个。
        from .composition import composition_policy  # 延迟导入
        from .composition import scenic_days as _scenic_days  # 延迟导入

        policy = composition_policy(context, increment=context.get("increment"), expectations=expectations)
        working_plan = {"route": nodes + additions}
        min_scenic = float(policy.get("min_scenic_per_day") or 0)
        if min_scenic > 0:
            per_day = _scenic_days(working_plan)
            for day in range(1, days + 1):
                if per_day.get(day, 0) >= min_scenic:
                    continue
                day_nodes = [node for node in (nodes + additions) if node_day(node, 1) == day]
                room = None if cap is None else max(0, cap - len(day_nodes))
                if cap is not None and room < 1:
                    continue
                add(day, "scenic", "add_scenic", "15:00", f"这一天还没有自然景观（策略：{policy['note']}）")

        max_cultural = policy.get("max_cultural_total")
        if max_cultural is not None:
            while True:
                cultural_plays = [
                    node for node in nodes
                    if _intent_of_node(node) not in {"food", "hotel"}
                    and _is_cultural_node(node)
                ]
                if len(cultural_plays) <= int(max_cultural):
                    break
                replacement = _pool_pick(pool, "scenic", used)
                if not replacement:
                    break
                victim = cultural_plays[-1]
                nodes = [node for node in nodes if node is not victim]
                day = node_day(victim, 1)
                additions.append(build_node_from_candidate(replacement, day, "15:00", "scenic"))
                actions.append(
                    {
                        "code": "rebalance_category",
                        "day": day,
                        "node": replacement.get("name"),
                        "intent": "scenic",
                        "source": replacement.get("price_source") or replacement.get("source") or "unavailable",
                        "from": node_name(victim),
                        "to": replacement.get("name"),
                        "detail": f"文化类超过上限 {max_cultural} 个 → 换成自然景观（策略：{policy['note']}）",
                    }
                )

        # 住宿夜数：第 1..days-1 天每夜至少一个住宿节点
        expected_nights = max(0, days - 1)
        current_nights = sum(1 for node in nodes if _intent_of_node(node) == "hotel")
        if current_nights < expected_nights:
            for day in range(1, days):
                if current_nights >= expected_nights:
                    break
                day_intents = {_intent_of_node(node) for node in by_day.get(day, [])}
                if "hotel" in day_intents:
                    continue
                if add(day, "hotel", "add_lodging", "20:00", f"第 {day} 晚原本没有住宿节点"):
                    current_nights += 1

    # 必去项：池子里真有对应候选才补
    haystack = " ".join(_node_text(node) for node in nodes + additions)
    for wanted in expectations.get("must_have") or []:
        target = _norm_text(wanted)
        if not target or target in haystack:
            continue
        matched_intent = None
        for intent in PLAY_INTENTS + ("food", "hotel"):
            candidate = _pool_pick(pool, intent, set())
            if candidate and target in _norm_text(f"{candidate.get('name')} {candidate.get('type')}"):
                matched_intent = intent
                break
        if not matched_intent:
            continue
        day = min(range(1, max(2, days + 1)), key=lambda value: len([n for n in nodes + additions if node_day(n, 1) == value]))
        add(day, matched_intent, "add_must_have", "10:00", f"补上你要求的「{wanted}」")

    if not additions:
        return {"plan": plan, "actions": [], "changed": False}

    merged = dict(plan) if isinstance(plan, Mapping) else {}
    merged["route"] = nodes + additions
    return {"plan": merged, "actions": actions, "changed": True}


def _node_text(node: Mapping[str, Any]) -> str:
    tags = node.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return _norm_text(" ".join([node_name(node), str(node.get("type") or ""), " ".join(str(tag) for tag in tags)]))


def _norm_text(text: Any) -> str:
    return str(text or "").strip().lower().replace(" ", "")


def _first_play_intent(pool: Mapping[str, Sequence[Mapping[str, Any]]], used: set) -> str:
    """挑一个有可用候选的玩点意图（按固定顺序，保证确定性）。"""
    for intent in PLAY_INTENTS:
        if any(str(c.get("name") or "").strip() and str(c.get("name") or "").strip() not in used for c in pool.get(intent) or []):
            return intent
    return PLAY_INTENTS[0]


def repair_plan(
    context: Mapping[str, Any],
    plan: Any,
    expectations: Optional[Mapping[str, Any]] = None,
    max_nodes_per_day: Optional[int] = None,
    pool: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
) -> Dict[str, Any]:
    """修补方案：先用**真实候选池**补结构缺口，再做确定性整理；**绝不新增编造节点**。"""
    filled = fill_structural_gaps(context, plan, expectations, pool)
    working: Any = filled["plan"]
    actions: List[Dict[str, Any]] = list(filled["actions"])

    days = int(context.get("days") or context.get("trip_days") or 0) or 0
    limit = max_nodes_per_day
    if limit is None and expectations:
        raw_limit = expectations.get("max_nodes_per_day")
        if isinstance(raw_limit, (int, float)) and raw_limit > 0:
            limit = int(raw_limit)

    original = [dict(node) for node in nodes_of(plan) if isinstance(node, Mapping)]

    # 0) 先按天分组（保留原有顺序；补进来的新节点也一起进）
    working_nodes = [dict(node) for node in nodes_of(working) if isinstance(node, Mapping)]
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for index, node in enumerate(working_nodes, start=1):
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

    new_plan: Any = dict(working) if isinstance(working, Mapping) else {}
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
    pool: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
) -> Dict[str, Any]:
    """propose → critique → repair → rescore，最多 `max_rounds` 轮；掉分/掉门禁即回滚。

    接受标准是"**硬失败更少**，或者硬失败一样多而分数更高" —— 只看分数会犯这种错：
    重排时间修掉了"时间倒流"，但节奏维度分略降，于是"修好了却回滚"，用户拿到的
    还是那份时间倒流的方案。硬约束永远优先于加权分。

    给了 `pool`（真实候选池）时，"缺玩点/缺餐/缺住宿夜数/必去项"才算可修补；
    没给就只能如实上报（`needs_data`），绝不凭空补节点。
    """
    repair_fn = repair or (lambda ctx, current, exp=None: repair_plan(ctx, current, exp, pool=pool))
    has_pool = bool(pool)
    # 构成策略（目的地+偏好+二次增量）：用于判断"这轮修补到底有没有变好"。
    # 只看硬失败条数会误杀：一次修补可能把"缺自然景观的天数 4 → 1"却没减少硬失败条数，
    # 旧规则会把它整个回滚掉 —— 用户就白修了。
    from .composition import composition_policy, composition_targets_met  # 延迟导入

    policy = composition_policy(context, increment=context.get("increment"), expectations=expectations)

    def progress_of(target_plan: Any, critique: Mapping[str, Any]) -> tuple:
        targets = composition_targets_met(target_plan, policy)
        return (
            len(critique.get("hard_failures") or []),
            len(targets.get("scenic_shortfall_days") or []),
            1 if targets.get("cultural_over_cap") else 0,
            1 if targets.get("share_over_cap") else 0,
            -float(critique.get("score") or 0.0),
        )

    current = plan
    initial_critique = critique_plan(context, plan, expectations, signals, pool_available=has_pool)
    best = plan
    best_critique = initial_critique
    best_score = float(initial_critique.get("score") or 0.0)
    best_hard = len(initial_critique.get("hard_failures") or [])
    best_progress = progress_of(plan, initial_critique)
    rounds: List[Dict[str, Any]] = []
    stopped_reason = "clean"

    for index in range(max(0, int(max_rounds))):
        critique = critique_plan(context, current, expectations, signals, pool_available=has_pool)
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
            # 有候选池、也确实有"该补"的问题，但补不动了 → 说清是"候选池里没有更多可用的"
            exhausted = has_pool and "needs_candidates" in (critique.get("fixable") or [])
            stopped_reason = "candidates_exhausted" if exhausted else "no_change"
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
            break
        rescored = critique_plan(context, outcome["plan"], expectations, signals, pool_available=has_pool)
        new_score = float(rescored.get("score") or 0.0)
        new_hard = len(rescored.get("hard_failures") or [])
        # 接受标准：**进步了就收**（硬失败更少 → 缺自然景观的天数更少 → 文化类回到上限内 →
        # 类型更均衡 → 分数更高）。任何一项变差都会让 progress 变大，从而被回滚。
        new_progress = progress_of(outcome["plan"], rescored)
        accepted = new_progress < best_progress
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
        best_progress = new_progress
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
