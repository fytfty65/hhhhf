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

from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

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


def top_up_missing_meals(
    context: Mapping[str, Any],
    plan: Any,
    pool: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    expectations: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """给**没有餐饮节点的那一天**补一个饭点，候选**优先取离当天玩点最近的**（2026-10-07）。

    为什么要单独做（真实缺口）：喀什/伊宁都出现过 `skeleton_meal_missing`（某天没饭点）——
    门禁判失败，而这一条是**可以从候选池补**的。之前 `fill_structural_gaps` 里的补餐跑在
    收口**之前**，收口/分天重排之后又可能把饭点挪走，于是最终方案里又缺了。

    取候选的两条硬规矩：
      · 只用候选池里真实存在的餐饮候选，**同名不重复**（绝不编造）；
      · 按"离当天玩点聚簇最近"排序 —— 否则会给远点那天塞一家市区餐馆（实测喀什
        那样会平白多出 city→far→city 两趟 200+km）。
    返回 `{plan, actions, changed}`；没池子/没候选就如实不动。
    """
    from .increment import build_node_from_candidate
    from .plan_quality import haversine_km, node_day, node_lnglat, nodes_of

    expectations = expectations or {}
    cap = expectations.get("max_nodes_per_day")
    cap = int(cap) if isinstance(cap, (int, float)) and cap > 0 else None
    # 多远之外的饭点**宁可不塞**（2026-10-07 实测）：喀什第 3 天在英吉沙/巴楚方向，
    # 最近的候选饭点在 82.6km 外的市区 —— 塞进去只是把"门禁通过"做出来，
    # 用户不可能为一顿晚饭开 82km，`anchoring` 也从 0.833 掉到 0.667。
    # 距离上限跟着用户的距离口径走（"不介意跑远"的人才放宽）。
    max_km = expectations.get("meal_max_km")
    try:
        max_km = float(max_km) if max_km is not None else 30.0
    except (TypeError, ValueError):
        max_km = 30.0

    nodes = [dict(node) for node in nodes_of(plan)]
    used = {str(node.get("name") or "").strip() for node in nodes}
    days = sorted({node_day(node, 1) for node in nodes})
    actions: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    additions: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = list(nodes)

    def _centroid(day: int, source: Sequence[Mapping[str, Any]]) -> List[float]:
        coords = []
        for node in source:
            if node_day(node, 1) != day:
                continue
            if _intent_of_node(node) in {"food", "hotel"}:
                continue
            coord = node_lnglat(node)
            if coord:
                coords.append(coord)
        if not coords:
            return []
        return [sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords)]

    def _distance_to(center: Sequence[float], item: Mapping[str, Any]) -> float:
        coord = node_lnglat(item)
        if not center or not coord:
            return float("inf")
        return haversine_km((float(center[0]), float(center[1])), coord)

    # ① 先做一次**全局分配**：把所有"缺餐的天 × 候选池餐饮"按距离排序，近的优先配
    #    （逐天各取最近会把离 A 天最近的那家配给先轮到、其实更远的 B 天 —— 实测踩到过）。
    hungry = [
        day for day in days if not any(_intent_of_node(node) == "food" for node in current if node_day(node, 1) == day)
    ]
    if pool and hungry:
        centers = {day: _centroid(day, current) for day in hungry}
        candidates = [
            dict(item)
            for item in (pool.get("food") or [])
            if str(item.get("name") or "").strip() and str(item.get("name") or "").strip() not in used
        ]
        pairs = sorted(
            (
                (_distance_to(centers[day], item), day, item)
                for day in hungry
                for item in candidates
                if str(item.get("name") or "").strip() not in used
            ),
            key=lambda row: row[0],
        )
        assigned_days: set = set()
        assigned_names: set = set()
        for distance, day, item in pairs:
            name = str(item.get("name") or "").strip()
            if day in assigned_days or name in assigned_names:
                continue
            if distance != float("inf") and distance > max_km:
                # 太远就不塞（见上面 max_km 的说明），但**如实记下来**
                assigned_days.add(day)
                skipped.append(
                    {
                        "day": day,
                        "nearest_km": round(distance, 1),
                        "reason": f"最近的候选饭点在 {round(distance, 1)}km 外（超过 {round(max_km)}km 上限），宁可不塞",
                    }
                )
                continue
            day_nodes = [node for node in current if node_day(node, 1) == day]
            room = None if cap is None else max(0, cap - len(day_nodes))
            if room is not None and room < 1:
                assigned_days.add(day)  # 这天满了，别再给它配
                continue
            assigned_days.add(day)
            assigned_names.add(name)
            used.add(name)
            additions.append(build_node_from_candidate(item, day, "13:00", "food"))
            actions.append(
                {
                    "code": "add_meal",
                    "day": day,
                    "node": name,
                    "intent": "food",
                    "distance_km": round(distance, 1) if distance != float("inf") else None,
                    "detail": "这一天没有餐饮节点，从候选池取了离当天玩点最近的一家",
                }
            )

    # ② 还缺餐的天：从**有富余饭点（≥2 个）**的那天挪一个过来（选离这天玩点最近的）
    for day in days:
        day_nodes = [node for node in current if node_day(node, 1) == day]
        if any(_intent_of_node(node) == "food" for node in day_nodes):
            continue
        if any(item.get("code") == "add_meal" and item.get("day") == day for item in actions):
            continue
        # 挪过来也要看这天还装不装得下（每天节点上限）
        room = None if cap is None else max(0, cap - len(day_nodes))
        if room is not None and room < 1:
            continue
        center = _centroid(day, current)
        donors = [
            node
            for node in current
            if _intent_of_node(node) == "food"
            and sum(
                1
                for other in current
                if node_day(other, 1) == node_day(node, 1) and _intent_of_node(other) == "food"
            )
            >= 2
        ]
        if not donors:
            continue
        move = min(donors, key=lambda node: _distance_to(center, node))
        move_km = _distance_to(center, move) if center else float("inf")
        if move_km > max_km:
            skipped.append(
                {
                    "day": day,
                    "nearest_km": round(move_km, 1) if move_km != float("inf") else None,
                    "reason": f"最近的饭点在 {round(move_km, 1)}km 外（超过 {round(max_km)}km 上限），宁可不塞",
                }
            )
            continue
        from_day = node_day(move, 1)
        updated = {**move, "day": day, "time": f"Day {day} | 13:00 用餐", "moved_from_day": from_day}
        current = [updated if node is move else node for node in current]
        actions.append(
            {
                "code": "move_meal",
                "day": day,
                "node": move.get("name"),
                "intent": "food",
                "from_day": from_day,
                "distance_km": round(move_km, 1) if move_km != float("inf") else None,
                "detail": "这一天没有餐饮节点，从富余的那天挪了一个过来（候选池里没有更近的）",
            }
        )
    if not additions and not any(item.get("code") == "move_meal" for item in actions):
        return {"plan": plan, "actions": [], "skipped": skipped, "changed": False}

    # 👑 组装时必须**按天+时刻**排好（2026-10-07 实测）：补/挪进来的节点原来是"追加到数组末尾"
    #    或"留在原位"，于是第 3 天的时刻会是 13:00 → 08:30 → 10:00，门禁
    #    `day_out_of_order`（时间倒流）直接判失败。这里按天分组、天内按时刻稳定排序。
    def _clock_of(node: Mapping[str, Any]) -> Optional[int]:
        raw = str(node.get("time") or "")
        tail = raw.split("|")[-1].strip()
        parts = tail.replace("：", ":").split(":", 1)
        if len(parts) != 2:
            return None
        hour, minute = parts[0].strip(), parts[1].strip()[:2]
        if not hour.isdigit() or not minute.isdigit():
            return None
        return int(hour) * 60 + int(minute)

    route: List[Dict[str, Any]] = list(current) + list(additions)
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for node in route:
        grouped.setdefault(node_day(node, 1), []).append(node)

    def _leg_km(nodes: Sequence[Mapping[str, Any]]) -> float:
        total = 0.0
        previous: Optional[Tuple[float, float]] = None
        for node in nodes:
            coord = node_lnglat(node)
            if coord and previous:
                km = haversine_km(previous, coord)
                if km == km:
                    total += km
            if coord:
                previous = coord
        return total

    ordered: List[Dict[str, Any]] = []
    ladder = ["08:30", "10:00", "11:30", "13:30", "15:00", "17:00", "18:30", "19:30"]
    for day in sorted(grouped):
        day_nodes = list(grouped[day])
        # 👑 新补/挪进来的饭点要插在**最省里程**的位置，而不是按时刻硬塞
        #    （2026-10-07 实测）：喀什第 3 天的玩点本身横跨 200km（英吉沙 ↔ 巴楚），
        #    按 13:00 插进去会让当天从 231km 涨到 385km；插在"顺路"的位置只多几十公里。
        for item in actions:
            if item.get("day") != day:
                continue
            name = str(item.get("node") or "")
            target = next((node for node in day_nodes if str(node.get("name") or "") == name), None)
            if target is None or len(day_nodes) < 3:
                continue
            rest = [node for node in day_nodes if node is not target]
            best_index, best_km = 1, None
            for index in range(1, len(rest) + 1):
                candidate_order = rest[:index] + [target] + rest[index:]
                km = _leg_km(candidate_order)
                if best_km is None or km < best_km - 1e-6:
                    best_km, best_index = km, index
            day_nodes = rest[:best_index] + [target] + rest[best_index:]
        # 时刻：按顺序给非住宿节点排梯队，住宿保留自己的入住时间
        slot = 0
        for node in day_nodes:
            clock = _clock_of(node)
            if _intent_of_node(node) == "hotel" and clock is not None:
                text = f"{clock // 60:02d}:{clock % 60:02d}"
            else:
                text = ladder[min(slot, len(ladder) - 1)]
                slot += 1
            suffix = ""
            raw = str(node.get("time") or "")
            if "|" in raw:
                tail = raw.split("|")[-1].strip()
                parts = tail.split(" ", 1)
                if len(parts) == 2:
                    suffix = parts[1].strip()
            node["time"] = f"Day {day} | {text}" + (f" {suffix}" if suffix else "")
        ordered.extend(day_nodes)
    return {"plan": {"route": ordered}, "actions": actions, "skipped": skipped, "changed": True}


async def adjust_lodging_by_geography(
    route: Any,
    *,
    days: int,
    fetch_nearby: Any,
    threshold_km: float = 15.0,
    radius_km: float = 20.0,
    keywords: str = "酒店|民宿|客栈|宾馆",
    limit: int = 8,
    min_gain_ratio: float = 0.4,
) -> Dict[str, Any]:
    """把"住得太远"的那晚换成**当天玩点附近**的真实酒店（2026-10-07）。

    真实缺口（伊宁实测 `anchoring=0.143`）：第 1 天的玩点在英塔木镇（天鹅泉/马场），
    酒店却在 38.5km 外的市区；第 2 天玩点在西边，酒店在 46.9km 外 —— 吃住离玩点 17~38km。
    根因：`ensure_lodging_per_night` 只在**缺夜**时按"离当天最近"补，
    已经有酒店的那一晚**没人再回头看它是不是太远**。

    规则（与"按地理分天"同一套谨慎口径）：
      · 只换**明显更近**的：新距离 ≤ 老距离 × (1 - min_gain_ratio)，且新距离 ≤ 阈值；
      · 候选必须来自**真实的周边检索**（调用方注入 `fetch_nearby`），同名不重复，绝不编造；
      · 每晚仍然**一家**、入住时刻沿用原来那一刻（门禁的夜晚槽位不变）；
      · 查不到更好的 → 如实记进 `skipped`（含"现在多远"），不改动。

    返回 `{plan, actions, skipped, changed}`。
    """
    from .increment import build_node_from_candidate
    from .plan_quality import haversine_km, node_day, node_lnglat

    nodes = [dict(node) for node in (nodes_of(route) if isinstance(route, Mapping) else (route or []))]
    if not nodes:
        return {"plan": {"route": nodes}, "actions": [], "skipped": [], "changed": False}
    used = {str(node.get("name") or "").strip() for node in nodes}
    actions: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    measured: List[Dict[str, Any]] = []
    changed = False

    for day in range(1, max(1, int(days or 1)) + 1):
        day_nodes = [node for node in nodes if node_day(node, 1) == day]
        if not day_nodes:
            continue
        hotels = [node for node in day_nodes if _intent_of_node(node) == "hotel"]
        if not hotels:
            # 这一晚没有酒店节点（不需要住 / 还没排）—— 记下来，便于事后回答"为什么没查这天"
            measured.append({"day": day, "status": "no_hotel"})
            continue
        plays = [
            node
            for node in day_nodes
            if _intent_of_node(node) not in {"food", "hotel"} and node_lnglat(node)
        ]
        if not plays:
            measured.append({"day": day, "status": "no_play_with_coords", "hotel": str(hotels[0].get("name") or "")})
            continue
        coords: List[Tuple[float, float]] = []
        for node in plays:
            play_coord = node_lnglat(node)
            if play_coord:
                coords.append(play_coord)
        hotel = hotels[0]
        hotel_coord = node_lnglat(hotel)
        if not hotel_coord:
            measured.append({"day": day, "status": "no_hotel_coord", "hotel": str(hotel.get("name") or "")})
            continue
        # ⚠️ 同 `adjust_meals_by_geography`：**判定距离要与尺子一致**（到当天任一玩点的最近距离），
        #    质心只当检索中心用。否则出现"最近玩点 24.9km（尺子扣分）／质心 1.2km（这里不管）"的死角
        #    —— 伊宁 yn25 第 1 天 信泰达民宿 就是这个形态。
        def _nearest_play_km(position: Tuple[float, float]) -> float:
            return min(float(haversine_km(position, play_pos)) for play_pos in coords)

        current_km = _nearest_play_km(hotel_coord)
        measured.append(
            {"day": day, "hotel": str(hotel.get("name") or ""), "km": round(current_km, 1), "plays": len(plays)}
        )
        if current_km <= float(threshold_km):
            continue
        search_center = min(coords, key=lambda play_pos: float(haversine_km(hotel_coord, play_pos)))
        try:
            rows = await fetch_nearby(search_center, keywords, radius_km=radius_km, limit=limit) or []
        except Exception as exc:
            skipped.append({"day": day, "current_km": round(current_km, 1), "error": f"{type(exc).__name__}: {str(exc)[:80]}"})
            continue
        best = None
        best_km = None
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            name = str(row.get("name") or "").strip()
            coord = node_lnglat(row)
            if not name or not coord or name in used:
                continue
            km = _nearest_play_km(coord)
            if km > float(threshold_km):
                continue
            if best_km is None or km < best_km:
                best, best_km = dict(row), km
        if best is None or best_km is None or best_km > current_km * (1 - float(min_gain_ratio)):
            skipped.append(
                {
                    "day": day,
                    "current_km": round(current_km, 1),
                    "nearest_km": round(best_km, 1) if best_km is not None else None,
                    "reason": "周边没有明显更近的真实住宿，保持原样",
                }
            )
            continue
        replacement = build_node_from_candidate(best, day, "21:00", "hotel")
        replacement["time"] = str(hotel.get("time") or f"Day {day} | 21:00 入住")
        replacement["replaced_lodging"] = str(hotel.get("name") or "")
        nodes = [replacement if node is hotel else node for node in nodes]
        used.add(str(best.get("name") or "").strip())
        changed = True
        actions.append(
            {
                "day": day,
                "from": str(hotel.get("name") or ""),
                "to": str(best.get("name") or ""),
                "from_km": round(current_km, 1),
                "to_km": round(best_km, 1),
            }
        )
    return {"plan": {"route": nodes}, "actions": actions, "skipped": skipped, "measured": measured, "changed": changed}


async def adjust_meals_by_geography(
    route: Any,
    *,
    days: int,
    fetch_nearby: Any,
    threshold_km: float = 15.0,
    radius_km: float = 25.0,
    keywords: str = "餐厅|美食|小吃",
    limit: int = 8,
    min_gain_ratio: float = 0.4,
    max_per_day: int = 3,
    max_total: int = 6,
) -> Dict[str, Any]:
    """把**排得太远**的饭点换成当天玩点附近的真实餐馆（2026-10-07）。

    与"补餐"（`top_up_missing_meals`）的分工：那边治"某天没有饭点"，这边治"有饭点但排得太远"。
    真实数据（伊宁 `anchoring` 卡在 0.429）：第 1 天玩点在英塔木镇，三家餐馆都在 **30km** 外的
    县城；第 2 天那家 17.8km。它们不是缺口，但用户要为一顿饭来回开 60km。

    规则（与住宿就近同一套谨慎口径）：
      · 只换**明显更近**的（新 ≤ 老 × (1 - min_gain_ratio) 且 ≤ 阈值）；
      · **一对一替换**，所以"每天至少一餐"不会被破坏；
      · 同名不重复、只从真实周边检索里取、绝不编造；查不到更好的 → 记 `skipped`（含现在多远）。

    返回 `{plan, actions, skipped, measured, changed}`。
    """
    from .increment import build_node_from_candidate
    from .plan_quality import haversine_km, node_day, node_lnglat

    nodes = [dict(node) for node in (nodes_of(route) if isinstance(route, Mapping) else (route or []))]
    if not nodes:
        return {"plan": {"route": nodes}, "actions": [], "skipped": [], "measured": [], "changed": False}
    used = {str(node.get("name") or "").strip() for node in nodes}
    actions: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    measured: List[Dict[str, Any]] = []
    changed = False

    for day in range(1, max(1, int(days or 1)) + 1):
        day_nodes = [node for node in nodes if node_day(node, 1) == day]
        if not day_nodes:
            continue
        plays = [
            node
            for node in day_nodes
            if _intent_of_node(node) not in {"food", "hotel"} and node_lnglat(node)
        ]
        if not plays:
            continue
        coords: List[Tuple[float, float]] = []
        for node in plays:
            play_coord = node_lnglat(node)
            if play_coord:
                coords.append(play_coord)
        # ⚠️ **判定用的距离必须和尺子一致**（2026-10-08 实测修，这是伊宁 anchoring 卡在 0.714 的真因）：
        #    尺子 `score_anchoring` 看的是"到当天**任一玩点的最近距离**"，
        #    而这里原来用"到当天玩点**质心**的距离" —— 两个数在一日多点时差得很远，
        #    于是出现"改的人觉得没问题、尺子仍在扣分"的死角：
        #      · 伊宁 yn24 第 2 天 老字号回民拌面王：最近玩点 17.8km（尺子扣分）／质心 9.8km（这里不管）；
        #      · 伊宁 yn25 第 1 天 信泰达民宿：最近玩点 24.9km／质心 1.2km —— 完全看不见。
        #    反向也错：库尔勒 馍夹肉 最近玩点 3.8km（尺子通过）却被质心 13.7km 判为"需要换"。
        #    质心仍留作**检索中心**（问周边餐馆时用），但**判定**改用最近玩点距离。
        def _nearest_play_km(position: Tuple[float, float]) -> float:
            return min(float(haversine_km(position, play_pos)) for play_pos in coords)

        meals = [node for node in day_nodes if _intent_of_node(node) == "food" and node_lnglat(node)]
        # 一天最多换 `max_per_day` 个、总数封顶 `max_total`。
        # ⚠️ 限额是按**实测耗时**定的，不是拍的（2026-10-08 复测）：
        #    一次周边检索 `0.32~0.40s`（同中心点第二次 0.00s，走缓存），
        #    原来定 2/4 是按"每次要好几秒"的**假设**来的 —— 实测差了约 50 倍，
        #    结果伊宁第 1 天三个 30km 外的饭点永远只换得掉两个（anchoring 卡在 0.714 八次运行）。
        #    实测同中心点有 8 个 ≤4.7km 且没用过的真实餐馆，所以放宽到 3/6。
        swapped_today = 0
        for meal in meals:
            if swapped_today >= max(1, int(max_per_day)) or len(actions) >= max(1, int(max_total)):
                break
            if not any(node is meal for node in nodes):
                continue  # 这个饭点刚被换掉了（旧对象已不在路线里），别再重复处理
            meal_coord = node_lnglat(meal)
            if meal_coord is None:
                continue
            current_km = _nearest_play_km(meal_coord)
            if current_km <= float(threshold_km):
                continue
            measured.append({"day": day, "meal": str(meal.get("name") or ""), "km": round(current_km, 1)})
            # 检索中心用**离这个饭点最近的玩点**（比质心更贴：质心可能落在几个玩点中间的空地上，
            # 周边餐馆既不属于任何一个玩点）。
            nearest_play_pos = min(coords, key=lambda play_pos: float(haversine_km(meal_coord, play_pos)))
            search_center = nearest_play_pos
            try:
                rows = await fetch_nearby(search_center, keywords, radius_km=radius_km, limit=limit) or []
            except Exception as exc:
                skipped.append(
                    {
                        "day": day,
                        "meal": str(meal.get("name") or ""),
                        "current_km": round(current_km, 1),
                        "error": f"{type(exc).__name__}: {str(exc)[:80]}",
                    }
                )
                break
            best = None
            best_km = None
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                name = str(row.get("name") or "").strip()
                coord = node_lnglat(row)
                if not name or not coord or name in used:
                    continue
                km = _nearest_play_km(coord)
                if km > float(threshold_km):
                    continue
                if best_km is None or km < best_km:
                    best, best_km = dict(row), km
            if best is None or best_km is None or best_km > current_km * (1 - float(min_gain_ratio)):
                skipped.append(
                    {
                        "day": day,
                        "meal": str(meal.get("name") or ""),
                        "current_km": round(current_km, 1),
                        "nearest_km": round(best_km, 1) if best_km is not None else None,
                        "reason": "周边没有明显更近的真实餐馆，保持原样",
                    }
                )
                # ⚠️ 这里必须是 `continue` 而不是 `break`（2026-10-08 实测修）：
                #    `break` 会**跳过同一天剩下的饭点**，让结果取决于饭点的排列顺序 ——
                #    伊宁第 1 天三个 30km 外的饭点，检索第一个时周边没有更近的，
                #    后面两个**根本没被问过**，看上去却像"每天最多换 2 个"的限额造成的。
                #    检索**报错**（限流）才停整天，那种情况在 except 分支里 `break`。
                continue
            replacement = build_node_from_candidate(best, day, "13:00", "food")
            replacement["time"] = str(meal.get("time") or f"Day {day} | 13:00 用餐")
            replacement["replaced_meal"] = str(meal.get("name") or "")
            nodes = [replacement if node is meal else node for node in nodes]
            used.add(str(best.get("name") or "").strip())
            used.discard(str(meal.get("name") or "").strip())
            changed = True
            swapped_today += 1
            actions.append(
                {
                    "day": day,
                    "from": str(meal.get("name") or ""),
                    "to": str(best.get("name") or ""),
                    "from_km": round(current_km, 1),
                    "to_km": round(best_km, 1),
                }
            )
            continue  # 继续看这一天下一个太远的饭点（受 max_per_day 限制）
    return {
        "plan": {"route": nodes},
        "actions": actions,
        "skipped": skipped,
        "measured": measured,
        "changed": changed,
    }


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
                if (cap is None or (room is not None and room >= 1)) and any(_pool_pick(pool, intent, set()) for intent in PLAY_INTENTS):
                    if add(day, _first_play_intent(pool, used), "fill_day", "10:00", "这一天原本是空的"):
                        room = None if cap is None else max(0, cap - 1)
            elif not plays:
                if (cap is None or (room is not None and room >= 1)) and any(_pool_pick(pool, intent, set()) for intent in PLAY_INTENTS):
                    if add(day, _first_play_intent(pool, used), "add_play", "10:00", "这一天只有吃住、没有玩点"):
                        room = None if cap is None else max(0, (room or 0) - 1)

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
                if cap is not None and (room or 0) < 1:
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
