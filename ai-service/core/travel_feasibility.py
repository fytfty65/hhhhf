"""行程的**物理现实性**（硬约束过滤层的日内里程约束）。

为什么需要（2026-10-04 用户口径 + 真实方案实测）：
  用户说"不考虑距离、只想看自然景观"时，我们正确地放开了半径（400km 跨州），
  但下一版方案在 **3 天里同时排了博斯腾湖（47km）与巴音布鲁克大草原（约 270km）** ——
  这不是"不畏距离"，是物理上跑不动。距离容忍解决"愿不愿意去"，不解决"一天放不放得下"。

这一层只做两件事（纯函数，可离线单测）：
  1. `evaluate_travel_feasibility()`：按天算移动里程、数出"远景区"个数，给出违反项；
  2. `repair_day_overload()`：一天塞了多个远景区时，保留最近的那个，
     其余移出该天（**至少保留 1 个节点**），并如实说明移走了什么。

口径（可调，但必须有依据）：
  - 每天总移动里程上限：normal 160km / high 320km / low 80km；
  - 每天"远景区"（离市中心 ≥ 40km）最多 1 个 —— 哪怕用户不在乎距离，
    一天跑两个 200km 级的景区在时间上就是不成立；
  - 缺坐标的节点不参与里程计算，也不被当成"远"（宁可少报，也不凭空定罪）。
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

FAR_NODE_KM = 40.0  # 离市中心多远算"远景区"（库尔勒市区景点普遍 ≤5km，博斯腾湖 47km）

DAILY_BUDGET_KM = {"low": 80.0, "normal": 160.0, "high": 320.0}
DAILY_FAR_LIMIT = {"low": 0, "normal": 1, "high": 1}

# 预算的**容差**：我们算的是**直线距离**（haversine），而真实路网通常比直线长 20–40%，
# 也就是说"超 1.8km"完全在估算误差里。2026-10-05 实测：喀什第 1 天 161.8km 被判"超当天预算
# 160km"并写进披露，用户看到的是"这个系统连 1.8 公里都要报一次"——噪声把真问题淹了。
# 10% 的容差只用来**判定**，报告的 `budget_km` 仍然如实写原值（不美化口径）。
DAILY_BUDGET_GRACE_RATIO = 0.10


def effective_daily_budget_km(distance_tolerance: str = "normal") -> float:
    """判定用的当天预算（含直线距离估算容差）。"""
    return daily_budget_km(distance_tolerance) * (1.0 + DAILY_BUDGET_GRACE_RATIO)


def node_lnglat(node: Any) -> List[float]:
    """节点坐标 → [lng, lat]；取不到就返回空列表（**不当成 0,0**）。

    ⚠️ `(0, 0)` 是"几内亚湾海面上"，不是地点：数据源没给坐标时很容易写成 0/0，
    而它一旦进了里程计算，就会算出"第 2 天移动 9440km"这种荒唐结论
    （实测 2026-10-05：供应商候选没带坐标 → 被写成 [0,0] → 整天的里程与预算判断全废）。
    与前端 `_valid_pt`、照片兜底等处保持同一口径：**0,0 = 没有坐标**。
    """
    if not isinstance(node, Mapping):
        return []
    value = node.get("lnglat") or node.get("location") or node.get("coord")
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            lng, lat = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return []
        if lng == 0.0 and lat == 0.0:
            return []
        return [lng, lat]
    if isinstance(value, str) and "," in value:
        left, _, right = value.partition(",")
        try:
            lng, lat = float(left), float(right)
        except ValueError:
            return []
        if lng == 0.0 and lat == 0.0:
            return []
        return [lng, lat]
    return []


def haversine_km(origin: Sequence[float], destination: Sequence[float]) -> float:
    try:
        lon1, lat1 = float(origin[0]), float(origin[1])
        lon2, lat2 = float(destination[0]), float(destination[1])
    except (TypeError, ValueError, IndexError):
        return float("nan")
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    h = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def daily_budget_km(distance_tolerance: str = "normal") -> float:
    return DAILY_BUDGET_KM.get(str(distance_tolerance or "normal").strip().lower(), DAILY_BUDGET_KM["normal"])


def daily_far_limit(distance_tolerance: str = "normal") -> int:
    return DAILY_FAR_LIMIT.get(str(distance_tolerance or "normal").strip().lower(), DAILY_FAR_LIMIT["normal"])


def _days_of(route: Sequence[Mapping[str, Any]]) -> Dict[int, List[Mapping[str, Any]]]:
    grouped: Dict[int, List[Mapping[str, Any]]] = {}
    for index, node in enumerate(route or []):
        if not isinstance(node, Mapping):
            continue
        try:
            day = int(node.get("day") or 1)
        except (TypeError, ValueError):
            day = 1
        grouped.setdefault(day, []).append(node)
    return grouped


def day_travel_km(nodes: Sequence[Mapping[str, Any]], city_center: Optional[Sequence[float]] = None) -> float:
    """一天内的移动里程：市中心 → 各节点 → （回市中心），按节点原有顺序累加。

    缺坐标的相邻点直接跳过（不把"不知道"当成 0 距离，也不当成很远）。
    """
    points: List[Sequence[float]] = []
    if city_center and len(list(city_center)) >= 2:
        points.append(city_center)
    for node in nodes:
        coord = node_lnglat(node)
        if coord:
            points.append(coord)
    if len(points) < 2:
        return 0.0
    total = 0.0
    for index in range(1, len(points)):
        leg = haversine_km(points[index - 1], points[index])
        if leg == leg:  # 过滤 nan
            total += leg
    # 回程：以最后一天为例，多数行程晚上回市区/住景区，这里不强行加回程，
    # 只在"当天最远的点离市中心很远"时由远景区数量来约束（见 evaluate）。
    return round(total, 1)


def evaluate_travel_feasibility(
    route: Sequence[Mapping[str, Any]],
    *,
    city_center: Optional[Sequence[float]] = None,
    distance_tolerance: str = "normal",
    days: int = 1,
) -> Dict[str, Any]:
    """按天检查：总移动里程是否超预算、远景区是否超过每天 1 个。"""
    budget = daily_budget_km(distance_tolerance)
    effective_budget = effective_daily_budget_km(distance_tolerance)
    far_limit = daily_far_limit(distance_tolerance)
    grouped = _days_of(route)
    report_days: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []

    for day in range(1, max(1, int(days or 1)) + 1):
        nodes = grouped.get(day, [])
        km = day_travel_km(nodes, city_center)
        far_nodes: List[Dict[str, Any]] = []
        for node in nodes:
            coord = node_lnglat(node)
            distance = haversine_km(city_center, coord) if (coord and city_center) else float("nan")
            if distance == distance and distance >= FAR_NODE_KM:
                far_nodes.append({
                    "name": str(node.get("name") or node.get("location") or ""),
                    "distance_km": round(float(distance), 1),
                })
        day_violations: List[str] = []
        if nodes and km > effective_budget:
            day_violations.append("daily_travel_budget_exceeded")
            violations.append({
                "code": "daily_travel_budget_exceeded",
                "day": day,
                "level": "negotiable",
                "detail": f"第 {day} 天移动约 {km:.0f}km，超过当天预算 {budget:.0f}km",
            })
        if len(far_nodes) > far_limit:
            day_violations.append("too_many_far_scenic_in_one_day")
            names = "、".join(item["name"] for item in far_nodes)
            violations.append({
                "code": "too_many_far_scenic_in_one_day",
                "day": day,
                "level": "negotiable",
                "detail": f"第 {day} 天排了 {len(far_nodes)} 个远景区（{names}），一天跑不完",
            })
        report_days.append({
            "day": day,
            "nodes": len(nodes),
            "travel_km": km,
            "budget_km": budget,
            "far_nodes": far_nodes,
            "violations": day_violations,
        })

    notes = [item["detail"] for item in violations]
    return {
        "ok": not violations,
        "distance_tolerance": str(distance_tolerance or "normal"),
        "budget_km": budget,
        # 判定用的是含容差的预算（直线距离估算误差），如实一并给出来 —— 两个数都摆着，不藏
        "effective_budget_km": round(effective_budget, 1),
        "grace_ratio": DAILY_BUDGET_GRACE_RATIO,
        "far_limit": far_limit,
        "days": report_days,
        "violations": violations,
        "notes": notes,
    }


def suggest_near_alternatives(
    route: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    *,
    city_center: Optional[Sequence[float]] = None,
    distance_tolerance: str = "normal",
    category_of: Optional[Callable[[Mapping[str, Any]], str]] = None,
    tier_of: Optional[Callable[[Mapping[str, Any]], str]] = None,
    limit: int = 2,
) -> Dict[str, Any]:
    """给"当天跑超预算"的那天，从候选池里挑**同类、离当天其它点更近**的替代点。

    **只提议，不改方案**（用户口径 2026-10-05：远的一天"可以接受，但是需要标明"；
    真要去掉得等用户说一句）。但"说一句就换"要能兑现 —— 所以这里把**换哪个**先算出来，
    免得用户说了之后系统还要现场猜（猜出来的往往还是同类远点）。

    规则（都写在返回值里，用户能看见依据）：
    - 只对**超当天预算**的天给建议（没超的不打扰）；
    - 替代点必须是**同一类**（用 `category_of` 判；不传就不分类、只看距离）；
    - 替代点要**离市中心更近**（< `FAR_NODE_KM`），并且**离当天其它点不远**（按最近距离排序）；
    - **档次不能降**（`tier_of`）：实测（2026-10-05）第一版拿"北湖公园"去替换
      "英吉沙国家湿地公园" —— 都是 scenic，但一个是城市公园、一个是真景区，
      用户明确说过"城市公园不算自然景观"。同类里没有同档次的，就如实说没有；
    - 已经在方案里出现过的点不提议（不拿"换个名字的同一个地方"糊弄）；
    - 候选池里没有合适的就是没有（`options` 为空，如实说）。
    """
    budget = daily_budget_km(distance_tolerance)
    effective_budget = effective_daily_budget_km(distance_tolerance)
    grouped = _days_of(route)
    in_plan = {str(node.get("name") or "").strip() for node in route if isinstance(node, Mapping)}

    def _category(node: Mapping[str, Any]) -> str:
        if category_of is None:
            return ""
        try:
            return str(category_of(node) or "")
        except Exception:
            return ""

    def _tier(node: Mapping[str, Any]) -> str:
        if tier_of is None:
            return ""
        try:
            return str(tier_of(node) or "")
        except Exception:
            return ""

    def _center_km(node: Mapping[str, Any]) -> float:
        coord = node_lnglat(node)
        if not coord or not city_center:
            return float("nan")
        return haversine_km(city_center, coord)

    days_out: List[Dict[str, Any]] = []
    for day in sorted(grouped):
        nodes = [node for node in grouped[day] if isinstance(node, Mapping)]
        km = day_travel_km(nodes, city_center)
        if km <= effective_budget:
            continue
        far_nodes = [
            {"name": str(node.get("name") or ""), "distance_km": round(_center_km(node), 1)}
            for node in nodes
            if _center_km(node) == _center_km(node) and _center_km(node) >= FAR_NODE_KM
        ]
        if not far_nodes:
            continue  # 超预算但不是"某个点特别远"（可能是点位太散），这一轮不给替代建议
        others = [node for node in nodes if _center_km(node) == _center_km(node) and _center_km(node) < FAR_NODE_KM]

        options: List[Dict[str, Any]] = []
        for far_node in nodes:
            far_km = _center_km(far_node)
            if not (far_km == far_km) or far_km < FAR_NODE_KM:
                continue
            wanted = _category(far_node)
            wanted_tier = _tier(far_node)
            ranked: List[Tuple[float, Mapping[str, Any]]] = []
            for candidate in candidates:
                if not isinstance(candidate, Mapping):
                    continue
                name = str(candidate.get("name") or "").strip()
                if not name or name in in_plan:
                    continue
                if any(item.get("name") == name for item in options):
                    continue
                candidate_km = _center_km(candidate)
                if not (candidate_km == candidate_km) or candidate_km >= FAR_NODE_KM:
                    continue  # 别拿另一个远点来"替代"远点
                if wanted and _category(candidate) != wanted:
                    continue
                # 档次不能降：拿"城市公园"顶替"真景区"正是用户明确反对的那件事
                if wanted_tier and _tier(candidate) != wanted_tier:
                    continue
                candidate_coord = node_lnglat(candidate)
                if others and candidate_coord:
                    nearest = min(haversine_km(candidate_coord, node_lnglat(node)) for node in others if node_lnglat(node))
                else:
                    nearest = candidate_km
                ranked.append((nearest, candidate))
            if not ranked:
                continue
            ranked.sort(key=lambda item: item[0])
            best_km, best = ranked[0]
            options.append(
                {
                    "for_node": str(far_node.get("name") or ""),
                    "for_node_km": round(far_km, 1),
                    "name": str(best.get("name") or ""),
                    "category": wanted,
                    "center_km": round(_center_km(best), 1),
                    "nearest_day_km": round(float(best_km), 1),
                    "price": best.get("price"),
                    "price_source": str(best.get("price_source") or best.get("data_source") or ""),
                    # 完整候选带一份：用户真说"换"时要能**就地替换**，不必再猜一遍
                    # （`apply_near_substitutions` 用的就是它）
                    "candidate": dict(best),
                    "why": f"替换「{far_node.get('name')}」（离市中心约 {round(far_km)}km）："
                    f"这个点在市中心 {round(_center_km(best))}km 内、离当天其它点约 {round(float(best_km))}km",
                }
            )
            if len(options) >= max(1, int(limit)):
                break
        # 超预算 + 有特别远的点 → 这一天一定要报出来（哪怕没有可换的近点，
        # "这天跑得远、池子里没有同类近点可换"本身也是用户该知道的事实）
        days_out.append(
            {
                "day": day,
                "travel_km": km,
                "budget_km": budget,
                "far_nodes": far_nodes,
                "options": options,
            }
        )
    return {
        "days": days_out,
        "distance_tolerance": str(distance_tolerance or "normal"),
        "note": "这些只是**可换的近点**（同类别、离当天其它点更近）；想换直接说一句就行，不说就不动。",
    }


def apply_near_substitutions(
    route: Sequence[Mapping[str, Any]],
    alternatives: Mapping[str, Any],
    *,
    city_center: Optional[Sequence[float]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """**用户说了"不想跑远"**时，把远点就地换成算好的近点（返回 `(新路线, 动作)`）。

    与 `suggest_near_alternatives` 的分工：那个只提议（默认口径：远的一天可以接受、但要标明），
    这个**执行** —— 只在用户明确表达"嫌远"时才调用（`distance_tolerance=low`，
    来自他亲口说"太远/换近点"或点「不喜欢」写的原因）。
    用户口径（2026-10-05）："如果用户点击不喜欢或者二次增量不想去这么远的地方，
    就按照用户的意图来"。

    做法保守：**同一天、同一时段就地换**（保留 `time`/`day`，只换地点与坐标），
    这样一天的形状不被改动，也不需要重排；没有同类同档次近点时**不动**（如实留给上层披露）。
    """
    if not isinstance(alternatives, Mapping):
        return [dict(node) for node in route], []
    options_by_day: Dict[int, List[Mapping[str, Any]]] = {}
    for day_item in alternatives.get("days") or []:
        if not isinstance(day_item, Mapping):
            continue
        try:
            day_number = int(day_item.get("day") or 0)
        except (TypeError, ValueError):
            continue
        options_by_day[day_number] = [item for item in (day_item.get("options") or []) if isinstance(item, Mapping)]

    nodes = [dict(node) for node in route if isinstance(node, Mapping)]
    actions: List[Dict[str, Any]] = []
    for day_number, options in options_by_day.items():
        for option in options:
            target = str(option.get("for_node") or "")
            candidate = option.get("candidate") if isinstance(option.get("candidate"), Mapping) else {}
            if not target or not candidate:
                continue
            for index, node in enumerate(nodes):
                if _day_of_node(node) != day_number or str(node.get("name") or "") != target:
                    continue
                if any(action.get("from") == target for action in actions):
                    break
                replaced = dict(node)
                replaced["name"] = str(candidate.get("name") or "")
                replaced["location"] = str(candidate.get("name") or "")
                if node_lnglat(candidate):
                    replaced["lnglat"] = list(node_lnglat(candidate))
                if str(candidate.get("type") or "").strip():
                    replaced["type"] = str(candidate.get("type"))
                replaced["replaced_from"] = target
                replaced["replaced_reason"] = "你说过不想跑那么远"
                if candidate.get("price") is not None:
                    replaced["cost_estimate"] = f"¥{float(candidate['price']):g}"
                    sources = dict(replaced.get("data_sources") or {})
                    sources["cost_estimate"] = str(candidate.get("price_source") or candidate.get("data_source") or "candidate_pool")
                    replaced["data_sources"] = sources
                nodes[index] = replaced
                actions.append(
                    {
                        "day": day_number,
                        "from": target,
                        "to": replaced["name"],
                        "from_km": option.get("for_node_km"),
                        "nearest_day_km": option.get("nearest_day_km"),
                        "reason": "你说过不想跑那么远，已就地换成同类的近点",
                    }
                )
                break
    return nodes, actions


def _day_of_node(node: Mapping[str, Any]) -> int:
    try:
        return int(node.get("day") or 1)
    except (TypeError, ValueError):
        return 1


def repair_day_overload(
    route: Sequence[Mapping[str, Any]],
    *,
    city_center: Optional[Sequence[float]] = None,
    distance_tolerance: str = "normal",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把"一天里的多余远景区"移出该天，返回 `(新路线, 移走的节点)`。

    规则保守：远景区按"离市中心近→远"排序，保留最近的 `far_limit` 个，
    其余移出；**每天至少留 1 个节点**（宁可留一个远的，也不把一天清空）。
    """
    far_limit = daily_far_limit(distance_tolerance)
    grouped = _days_of(route)
    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []

    for day in sorted(grouped):
        nodes = list(grouped[day])
        decorated: List[Tuple[float, int, Mapping[str, Any]]] = []
        for index, node in enumerate(nodes):
            coord = node_lnglat(node)
            distance = haversine_km(city_center, coord) if (coord and city_center) else float("nan")
            decorated.append((distance if distance == distance else -1.0, index, node))
        # ⚠️ 用户**点名选的那一家**不能被自动优化悄悄移出（2026-10-05：他挑了那家酒店，
        #    系统却因为"离市中心 40km 以上"把它移走 —— 那是把用户的选择当噪声）。
        #    保留它，超预算的部分照常披露（`evaluate_travel_feasibility` 仍然会报）。
        far_items = sorted(
            [
                item
                for item in decorated
                if item[0] >= FAR_NODE_KM
                and not (isinstance(item[2], Mapping) and item[2].get("user_choice"))
            ],
            key=lambda item: item[0],
        )

        if not city_center or len(far_items) <= far_limit:
            # 没有可比坐标，或远景区数量本来就没超 → 这一天不动
            kept.extend(dict(node) for node in nodes)
            continue

        keep_indexes = {item[1] for item in far_items[:far_limit]}
        day_kept: List[Dict[str, Any]] = []
        day_removed: List[Dict[str, Any]] = []
        for distance, index, node in decorated:
            if distance >= FAR_NODE_KM and index not in keep_indexes:
                day_removed.append({**dict(node), "removed_reason": "一天跑不了两个远景区"})
                continue
            day_kept.append(dict(node))
        if not day_kept and day_removed:
            # 兜底：一天不能清空 —— 把最近的那个远景区放回去（从 removed 里挪，不复制）
            day_kept.append({key: value for key, value in day_removed.pop(0).items() if key != "removed_reason"})
        kept.extend(day_kept)
        removed.extend(day_removed)

    return kept, removed


# ——————————————————————————————————————————————————————————————
# 省域口径（B）：目的地是"一个省"时，日内现实性不能按"离省会多远"来判。
#
# 为什么必须换一套判据（2026-11-01）：
#   省级请求会把九寨沟（离成都约 300km）、峨眉山（约 140km）放进候选池。
#   旧判据 `evaluate_travel_feasibility` 以 `city_center`（省会）为起点算里程与"远景区"，
#   于是把这些**本来就在候选里、而且真值得去**的景区判成"一天跑不完"，
#   `repair_day_overload` 直接把川西节点移出 → 用户看到的还是"成都周边"，省域覆盖落空。
#
# 省域的真实约束不是"离省会多远"，而是：
#   1. 同一天只在一个片区（片区 = 地州/州级市，由调用方用 `region_of` 判定）；
#   2. 跨片区那一天是"整段换乘"（高铁/动车），换乘本身要放得进一天（默认 ≤5 小时）；
#   3. 片区内部的移动仍受当天里程预算约束（daily_budget_km，与旧口径同一张表）。
#
# 所以这里**不传 city_center、也不数 far_nodes**：离省会 300km 是"要坐一趟高铁"，
# 不是"这一天跑不完"。
# ——————————————————————————————————————————————————————————————

MAX_TRANSFER_HOURS = 5.0


def _leg_km(
    nodes: Sequence[Mapping[str, Any]],
    region_of: Optional[Callable[[Mapping[str, Any]], str]] = None,
) -> float:
    """当天节点之间的移动里程（不含"从市中心出发/回市中心"那段）。

    ⚠️ 跨片区的那一段**不计入**：那是一趟高铁/动车，由 `transfer_too_long` 判定。
    把它按 300km 的路网里程算进当天预算，得到的结论会是"换乘日超预算"——
    而换乘日本来就该把时间花在车上，这不是跑不动的景点日。
    """
    total = 0.0
    previous_coord: Optional[List[float]] = None
    previous_region = ""
    for node in nodes:
        coord = node_lnglat(node)
        if not coord:
            continue
        region = ""
        if region_of is not None:
            try:
                region = str(region_of(node) or "").strip()
            except Exception:
                region = ""
        if previous_coord is not None and not (previous_region and region and previous_region != region):
            leg = haversine_km(previous_coord, coord)
            if leg == leg:
                total += leg
        previous_coord = coord
        previous_region = region or previous_region
    return round(total, 1)


def _region_sequence(nodes: Sequence[Mapping[str, Any]], region_of: Callable[[Mapping[str, Any]], str]) -> List[str]:
    """当天出现过的片区（按首次出现顺序去重）；取不到片区的节点不算数。"""
    sequence: List[str] = []
    for node in nodes:
        try:
            region = str(region_of(node) or "").strip()
        except Exception:
            region = ""
        if region and region not in sequence:
            sequence.append(region)
    return sequence


def _region_moves(
    nodes: Sequence[Mapping[str, Any]], region_of: Callable[[Mapping[str, Any]], str]
) -> int:
    """当天相邻节点之间**片区变化**的次数（取不到片区的节点跳过）。

    为什么不能只看"当天出现过几个片区"：成都→阿坝→成都→阿坝 只出现过两个片区，
    按去重口径会被当成"一次换乘日"直接放过；实际是来回横跳两趟，单日里程 600km+。
    实测（2026-11-02 四川 4 天，候选池兜底路线）：第 4 天这样排，里程重算 622km，
    而省域口径只报 62.8km（跨片区那几段按设计不计入），于是质量闸门与省域口径互相矛盾。
    """
    moves = 0
    previous = ""
    for node in nodes:
        try:
            region = str(region_of(node) or "").strip()
        except Exception:
            region = ""
        if not region:
            continue
        if previous and region != previous:
            moves += 1
        previous = region
    return moves


def evaluate_province_feasibility(
    route: Sequence[Mapping[str, Any]],
    *,
    region_of: Callable[[Mapping[str, Any]], str],
    region_center_of: Optional[Callable[[str], Sequence[float]]] = None,
    day_regions: Optional[Mapping[int, str]] = None,
    distance_tolerance: str = "normal",
    days: int = 1,
    max_transfer_hours: float = MAX_TRANSFER_HOURS,
) -> Dict[str, Any]:
    """省域口径的日内现实性：同一天一个片区、跨片区一次换乘、片区内受里程预算约束。

    与 `evaluate_travel_feasibility` 返回同构（`ok / days / violations / notes`），
    额外给出 `scope="province"`、每天的 `regions` 与 `planned_region`（分片建议），
    便于上游在披露里说清"这一天在哪个片区、换乘多久"。
    """
    budget = daily_budget_km(distance_tolerance)
    effective_budget = effective_daily_budget_km(distance_tolerance)
    grouped = _days_of(route)
    planned = dict(day_regions or {})
    report_days: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []

    def _transfer_hours(a: str, b: str) -> Optional[float]:
        if region_center_of is None:
            return None
        try:
            start = list(region_center_of(a) or [])
            end = list(region_center_of(b) or [])
        except Exception:
            return None
        if len(start) < 2 or len(end) < 2:
            return None
        km = haversine_km(start, end)
        if km != km:
            return None
        return round(float(km) / 200.0 + 0.5, 1)

    for day in range(1, max(1, int(days or 1)) + 1):
        nodes = grouped.get(day, [])
        regions = _region_sequence(nodes, region_of)
        moves = _region_moves(nodes, region_of)
        km = _leg_km(nodes, region_of)
        day_violations: List[str] = []
        transfer: Optional[Dict[str, Any]] = None

        if len(regions) >= 3 or moves >= 2:
            day_violations.append("too_many_regions_in_one_day")
            if len(regions) >= 3:
                detail = f"第 {day} 天跨了 {len(regions)} 个片区（{'、'.join(regions)}），一天只够玩一个片区"
            else:
                detail = (
                    f"第 {day} 天在{'、'.join(regions)}之间来回跑了 {moves} 次，"
                    "一天只够一个片区（跨片区应整段移动，而不是当天折返）"
                )
            violations.append({
                "code": "too_many_regions_in_one_day",
                "day": day,
                "level": "negotiable",
                "detail": detail,
            })
        elif len(regions) == 2:
            hours = _transfer_hours(regions[0], regions[1])
            transfer = {"from": regions[0], "to": regions[1], "hours": hours}
            if hours is not None and hours > max_transfer_hours:
                day_violations.append("transfer_too_long")
                violations.append({
                    "code": "transfer_too_long",
                    "day": day,
                    "level": "negotiable",
                    "detail": (
                        f"第 {day} 天从{regions[0]}换到{regions[1]}约 {hours:g} 小时（估算），"
                        f"超出一天可接受换乘 {max_transfer_hours:g} 小时"
                    ),
                })

        if nodes and km > effective_budget:
            day_violations.append("daily_travel_budget_exceeded")
            violations.append({
                "code": "daily_travel_budget_exceeded",
                "day": day,
                "level": "negotiable",
                "detail": f"第 {day} 天在片区内移动约 {km:.0f}km，超过当天预算 {budget:.0f}km",
            })

        report_days.append({
            "day": day,
            "nodes": len(nodes),
            "travel_km": km,
            "budget_km": budget,
            "regions": regions,
            "planned_region": str(planned.get(day) or ""),
            "transfer": transfer,
            "violations": day_violations,
        })

    return {
        "ok": not violations,
        "scope": "province",
        "distance_tolerance": str(distance_tolerance or "normal"),
        "budget_km": budget,
        "effective_budget_km": round(effective_budget, 1),
        "grace_ratio": DAILY_BUDGET_GRACE_RATIO,
        "max_transfer_hours": float(max_transfer_hours),
        "days": report_days,
        "violations": violations,
        "notes": [item["detail"] for item in violations],
        # 省域口径下"远景区"这个概念不再适用：离省会远只说明要换乘，不说明跑不完。
        "note": "省域口径：同一天只安排一个片区；跨片区那天要能当天到（换乘按高铁估算）。",
    }


def repair_province_overload(
    route: Sequence[Mapping[str, Any]],
    *,
    region_of: Callable[[Mapping[str, Any]], str],
    region_center_of: Optional[Callable[[str], Sequence[float]]] = None,
    day_regions: Optional[Mapping[int, str]] = None,
    distance_tolerance: str = "normal",
    max_transfer_hours: float = MAX_TRANSFER_HOURS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把"一天跨了太多片区 / 换乘放不进一天"的节点移出该天（返回 `(新路线, 移走的节点)`）。

    以当天**应当去的片区**为准：给了分片建议（`day_regions`）且这一天确实有该片区的点，
    就保留该片区、移出串进来的别的片区；没有分片建议时按"出现最多的片区"（并列取先出现的）。
    **每天至少留 1 个节点**（宁可留一个跨片的，也不把一天清空）。
    换乘日（两个片区且换乘 ≤ 上限）不动 —— 那正是"跨片区那天整段移动"的正常形态。
    """
    grouped = _days_of(route)
    planned = dict(day_regions or {})
    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []

    def _transfer_hours(a: str, b: str) -> Optional[float]:
        if region_center_of is None:
            return None
        try:
            start = list(region_center_of(a) or [])
            end = list(region_center_of(b) or [])
        except Exception:
            return None
        if len(start) < 2 or len(end) < 2:
            return None
        km = haversine_km(start, end)
        if km != km:
            return None
        return round(float(km) / 200.0 + 0.5, 1)

    for day in sorted(grouped):
        nodes = list(grouped[day])
        regions_of_nodes: List[str] = []
        for node in nodes:
            try:
                regions_of_nodes.append(str(region_of(node) or "").strip())
            except Exception:
                regions_of_nodes.append("")
        present = [region for region in regions_of_nodes if region]
        if len(present) < 3:
            long_transfer = False
            if len(present) == 2:
                hours = _transfer_hours(present[0], present[1])
                long_transfer = hours is not None and hours > max_transfer_hours
            if not long_transfer:
                kept.extend(dict(node) for node in nodes)
                continue

        counts: Dict[str, int] = {}
        for region in present:
            counts[region] = counts.get(region, 0) + 1
        # 优先按分片建议保留：这一天的点本来就该是那个片区的，串进来的才是要移走的。
        # （实测：没有这条时按"出现最多"裁决，某天串进 2 个外片区、只剩 1 个计划内点位，
        #   就会把计划内的点移走、留下外片区的点，覆盖反而更乱。）
        planned_region = str(planned.get(day) or "").strip()
        if planned_region and planned_region in present:
            dominant = planned_region
        else:
            # 出现最多的片区为准；并列时取"先出现的那个"（-index 让更小的下标得分更高）
            dominant = max(present, key=lambda region: (counts[region], -present.index(region)))
        day_kept: List[Dict[str, Any]] = []
        day_removed: List[Dict[str, Any]] = []
        for node, region in zip(nodes, regions_of_nodes):
            if region and region != dominant:
                day_removed.append({**dict(node), "removed_reason": "同一天跨了多个片区（一天只够玩一个片区）"})
                continue
            day_kept.append(dict(node))
        if not day_kept and day_removed:
            day_kept.append({key: value for key, value in day_removed.pop(0).items() if key != "removed_reason"})
        kept.extend(day_kept)
        removed.extend(day_removed)

    return kept, removed
