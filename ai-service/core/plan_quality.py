"""规划质量评分器（阶段 0：把"方案好不好"变成可度量的数字）。

设计原则
--------
1. **纯函数、确定性、零网络**：只吃 context + plan(JSON)，可在 CI 里跑，也能给"同一输入
   两次运行"算稳定性。所有外部数据（时长/花费/分类）都复用 `core/simulation.py` 的既有口径，
   避免出现第二套语义。
2. **硬约束是门禁，不是加分项**：天数覆盖、预算、必去点、时间窗、开放时间——任一项明确
   不满足就是 `failed`；**数据缺失一律记 `unverifiable`，既不算通过也不算失败**，并单独统计，
   绝不以"没数据"冒充"已满足"。
3. **真实性只认来源**：`data_sources[field]` 属于 {unverified, unavailable, seed_template} 或
   `estimated is True` 的节点不计入"已验证"。**评分绝不奖励无法核实的内容**——宁可分低，
   也不把编造当丰富。
4. **加权维度**：偏好覆盖 30%、节奏 25%、空间效率 20%、真实性 15%、多样性 10%（可覆盖）。
   硬约束不参与加权，失败直接拉低 verdict。

用法
----
    from core.plan_quality import evaluate_plan
    report = evaluate_plan(context, plan, expectations=case["expectations"])
    report["gate"]           # {"passed": bool, "failures": [...], "unverifiable": [...]}
    report["score"]          # 0-100 加权总分（仅当门禁通过才有意义）
    report["dimensions"]     # 各维度 0-1 与解释
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .simulation import category_of, cost_of, dwell_minutes_of, travel_minutes_of

# --------------------------------------------------------------------------
# 默认参数（都可被 context / expectations 覆盖，禁止在代码里偷偷放宽）
# --------------------------------------------------------------------------
DEFAULT_WEIGHTS: Dict[str, float] = {
    "preference_coverage": 0.25,
    "pacing": 0.20,
    "space_efficiency": 0.15,
    "truthfulness": 0.15,
    "anchoring": 0.15,
    "diversity": 0.10,
    # 用户的第一优先级是"地点好不好"（2026-10-05 补的可判定部分），
    # 它必须真的占权重 —— 只加维度不给权重等于没量。
    "place_quality": 0.20,
    "completeness": 0.15,
}

# 一天的"活跃时长"上限，超过即视为过赶（分钟）
# 2026-10-05 用户口径："放宽上限吧" —— 真实方案里一天 6 个节点约 720 分钟（停留+通勤），
# 按 600 分钟卡会把正常方案全判成"过赶"。放宽到 720，并把**饭点**从扣分项降为顾问提示。
DEFAULT_DAILY_ACTIVE_MINUTES = 720.0
# 单日节点数默认区间（节奏为 relaxed 时收紧）
DEFAULT_MIN_NODES_PER_DAY = 2
DEFAULT_MAX_NODES_PER_DAY = 6
RELAXED_MAX_NODES_PER_DAY = 4
# 同城内两点间超过该距离即视为一次"长距离折返"（公里）
LONG_HOP_KM = 40.0
# 时间窗硬边界
DAY_START_MIN, DAY_END_MIN = 6 * 60, 23 * 60
# 用餐窗口
MEAL_WINDOWS = ((11 * 60, 13 * 60 + 30), (17 * 60, 20 * 60))
# 被视为"未核实"的来源标记
UNVERIFIED_SOURCES = {"unverified", "unavailable", "seed_template", "unknown", ""}
# 需要来源的字段
SOURCED_FIELDS = ("rating", "cost_estimate", "open_time")
VERIFIED_SOURCES_OK = {"provider", "vendor", "amap", "rollinggo", "seniverse", "official", "web_page"}
# 「没有数据」的如实写法：这些**不是断言**，是披露（"未知"不扣分，装懂才扣分）。
DISCLOSURE_MARKERS = ("暂无供应商数据", "暂无", "未核实", "待确认", "未知", "不详", "unavailable", "unknown")


def _is_claim(value: Any) -> bool:
    """这个字段是不是**真的做出了断言**（而不是"暂无数据"这类如实披露）。"""
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    return not any(marker in text or marker in lowered for marker in DISCLOSURE_MARKERS)


def _has_number(value: Any) -> bool:
    """字段里有没有数字（"¥50（估算）"有，"暂无供应商数据"没有）。"""
    return bool(re.search(r"\d", str(value or "")))


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_clock(value: Any) -> Optional[int]:
    """把 '09:00' / 'Day 1 | 09:00' / '9:5' 解析为分钟数；无法解析返回 None。"""
    if value is None:
        return None
    text = str(value)
    if "|" in text:
        text = text.split("|")[-1]
    text = text.strip()
    if not text or "暂无" in text or "未知" in text:
        return None
    parts = text.replace("：", ":").split(":")
    if len(parts) < 2:
        return None
    hour, minute = parts[0].strip(), parts[1].strip()[:2]
    if not hour.isdigit() or not minute.isdigit():
        return None
    total = int(hour) * 60 + int(minute)
    return total if 0 <= total < 24 * 60 else None


def parse_open_window(value: Any) -> Optional[Tuple[int, int]]:
    """从开放时间文本里取第一段 HH:MM-HH:MM（或 HH:MM 起）。解析不到返回 None。"""
    if value is None:
        return None
    text = str(value)
    if "暂无" in text or "未知" in text or not text.strip():
        return None
    times: List[int] = []
    for token in text.replace("～", "-").replace("~", "-").replace("—", "-").split("-"):
        parsed = parse_clock(token.strip())
        if parsed is not None:
            times.append(parsed)
    if len(times) >= 2:
        return times[0], times[1]
    if len(times) == 1:
        return times[0], DAY_END_MIN
    return None


def nodes_of(plan: Any) -> List[Dict[str, Any]]:
    """把 plan 里的 route 节点摊平（容忍 route/nodes/days 等结构差异）。"""
    if not isinstance(plan, Mapping):
        return []
    raw = plan.get("route") or plan.get("nodes") or []
    if isinstance(raw, Mapping):
        raw = list(raw.values())
    nodes: List[Dict[str, Any]] = []
    for item in raw if isinstance(raw, Sequence) and not isinstance(raw, str) else []:
        if isinstance(item, Mapping):
            nodes.append(dict(item))
        elif isinstance(item, Sequence) and not isinstance(item, str):
            for nested in item:
                if isinstance(nested, Mapping):
                    nodes.append(dict(nested))
    return nodes


def node_name(node: Mapping[str, Any]) -> str:
    return str(node.get("name") or node.get("location") or "").strip()


def node_day(node: Mapping[str, Any], fallback: int) -> int:
    return int(_as_float(node.get("day"), fallback))


def node_lnglat(node: Mapping[str, Any]) -> Optional[Tuple[float, float]]:
    """节点坐标；取不到（含 `(0,0)`）返回 None。

    ⚠️ `(0,0)` 是"几内亚湾海面上"，不是地点（2026-10-05 实测：数据源没给坐标时被写成 [0,0]，
    评分由此算出"最赶的一天约 9438km"）。口径与 `core/travel_feasibility.node_lnglat` **一致** ——
    这一层只做形状适配（也要认 `{"lng":…,"lat":…}`），值的判定交给同一个实现，
    避免"管线里按没有坐标算、评分里按海面上算"这种两套口径（这次就是这么被抓到的）。
    """
    from .travel_feasibility import node_lnglat as _canonical_lnglat

    value = node.get("lnglat") or node.get("location_lnglat")
    if isinstance(value, Mapping):
        value = [value.get("lng"), value.get("lat")]
    normalized = _canonical_lnglat({"lnglat": value})
    if not normalized:
        return None
    return float(normalized[0]), float(normalized[1])


def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lng1, lat1 = a
    lng2, lat2 = b
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(h)))


def annotate_lodging_options(
    plan: Any,
    options: Sequence[Any],
    *,
    threshold_km: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """给"有实价的住宿可选"清单标上**离方案里最近的玩点多远**（纯函数）。

    为什么要有它（2026-10-05 实测）：住宿候选只按价格与星级排，`anchoring` 掉到 0.8 ——
    有吃住离当天玩点 40km+，来回通勤把游玩时间吃掉，但界面上完全看不出来。
    价格可比、距离也要可比，用户才谈得上"选哪家"。

    `threshold_km` 默认用 `ANCHOR_KM`（15km，与 `score_anchoring` 同一把尺子）。
    返回每项为原字典的副本 + `nearest_play_km` / `nearest_play_name` / `far_from_plays`。
    """
    limit = float(threshold_km if threshold_km is not None else ANCHOR_KM)
    plays: List[tuple] = []
    for node in nodes_of(plan):
        if _is_anchor_side(node):
            continue  # 吃住一侧不算"玩点"
        position = node_lnglat(node)
        if position is None:
            continue
        plays.append((node_name(node), position))

    annotated: List[Dict[str, Any]] = []
    for option in options or []:
        entry = dict(option) if isinstance(option, Mapping) else {}
        position = node_lnglat(entry)
        if position is None or not plays:
            entry["nearest_play_km"] = None
            entry["nearest_play_name"] = ""
            entry["far_from_plays"] = None
            annotated.append(entry)
            continue
        name, distance = min(
            ((play_name, haversine_km(position, play_pos)) for play_name, play_pos in plays),
            key=lambda item: item[1],
        )
        entry["nearest_play_km"] = round(float(distance), 1)
        entry["nearest_play_name"] = name
        entry["far_from_plays"] = bool(float(distance) > limit)
        annotated.append(entry)
    return annotated


def _normalize(text: str) -> str:
    return "".join(ch for ch in str(text).lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _ratio(hit: int, total: int, empty: float = 1.0) -> float:
    return empty if total <= 0 else max(0.0, min(1.0, hit / total))


# --------------------------------------------------------------------------
# 硬约束（门禁）
# --------------------------------------------------------------------------
def check_hard_constraints(
    context: Mapping[str, Any],
    plan: Any,
    expectations: Optional[Mapping[str, Any]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """返回 {'failures': [...], 'unverifiable': [...]}。未核实绝不等于通过。"""
    expectations = expectations or {}
    nodes = nodes_of(plan)
    ctx = context or {}
    days = int(_as_float(ctx.get("days") or ctx.get("trip_days"), 1)) or 1
    failures: List[Dict[str, Any]] = []
    unverifiable: List[Dict[str, Any]] = []

    min_nodes = int(_as_float(expectations.get("min_nodes_per_day"), DEFAULT_MIN_NODES_PER_DAY))
    by_day: Dict[int, List[Dict[str, Any]]] = {}
    for index, node in enumerate(nodes, start=1):
        by_day.setdefault(node_day(node, index), []).append(node)

    # 1) 天数覆盖
    for day in range(1, days + 1):
        count = len(by_day.get(day, []))
        if count == 0:
            failures.append({"code": "day_empty", "detail": f"第 {day} 天没有任何行程节点"})
        elif count < min_nodes:
            failures.append(
                {"code": "day_too_thin", "detail": f"第 {day} 天仅 {count} 个节点，少于要求 {min_nodes}"}
            )

    # 2) 预算：三级价格模型（verified / estimated / unknown）——能取证就取证，不能取证就估算但必须标明
    from .budget_planner import budget_report  # 延迟导入，避免与 budget_planner 形成模块级循环

    money = budget_report(ctx, plan)
    if money["budget"] > 0:
        if money["status"] == "over":
            failures.append(
                {
                    "code": "budget_exceeded",
                    "detail": f"已验证花费 ¥{money['verified_cost']:.0f} 超出预算 ¥{money['budget']:.0f}（缺口约 ¥{money['shortfall']:.0f}）",
                }
            )
        elif money["status"] == "at_risk":
            unverifiable.append(
                {
                    "code": "budget_at_risk",
                    "detail": (
                        f"已验证 ¥{money['verified_cost']:.0f} 在预算内，但含估算后最高约 "
                        f"¥{money['verified_cost'] + money['estimated_cost']:.0f}，可能超支约 ¥{money['shortfall']:.0f}"
                        "（估算部分已标明，不作为已验证结论）"
                    ),
                }
            )
        elif money["status"] == "unverifiable":
            unverifiable.append(
                {"code": "budget_unverifiable", "detail": "所有价格都取不到来源，预算无法核实（不视为满足）"}
            )
        if money["unknown_count"]:
            unverifiable.append(
                {
                    "code": "budget_partial",
                    "detail": (
                        f"{money['unknown_count']} 个节点取不到价格，预算仅部分可核实"
                        f"（已验证 ¥{money['verified_cost']:.0f} / 预算 ¥{money['budget']:.0f}）"
                    ),
                }
            )

    # 3) 必去点（在 名称+类型+描述+标签 的整体文本里判定，避免"餐饮节点不算餐"这类误判）
    haystack: List[str] = []
    for node in nodes:
        tags = node.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        parts = [node_name(node), str(node.get("type") or ""), str(node.get("desc") or "")]
        parts.extend(str(tag) for tag in tags)
        haystack.append(_normalize(" ".join(parts)))
    for wanted in expectations.get("must_have") or []:
        target = _normalize(wanted)
        if not target:
            continue
        if not any(target in text or text in target for text in haystack if text):
            failures.append({"code": "must_have_missing", "detail": f"缺少必去项：{wanted}"})

    # 4) 时间窗与单日时序
    for day, day_nodes in sorted(by_day.items()):
        previous = None
        for node in day_nodes:
            clock = parse_clock(node.get("time"))
            if clock is None:
                unverifiable.append(
                    {"code": "time_missing", "detail": f"第 {day} 天节点「{node_name(node)}」没有可解析的时间"}
                )
                continue
            if not (DAY_START_MIN <= clock <= DAY_END_MIN):
                failures.append(
                    {"code": "time_out_of_window", "detail": f"第 {day} 天「{node_name(node)}」时间 {node.get('time')} 超出 {DAY_START_MIN//60}:00-{DAY_END_MIN//60}:00"}
                )
            if previous is not None and clock < previous:
                failures.append(
                    {"code": "day_out_of_order", "detail": f"第 {day} 天时间倒流：{node.get('time')} 早于上一个节点"}
                )
            previous = clock if previous is None else max(previous, clock)

    # 5) 开放时间（有数据才判定；没有数据记 unverifiable）
    for node in nodes:
        clock = parse_clock(node.get("time"))
        raw = node.get("open_time")
        window = parse_open_window(raw)
        if window is None:
            unverifiable.append(
                {"code": "open_time_missing", "detail": f"「{node_name(node)}」开放时间未核实（{raw or '缺失'}）"}
            )
            continue
        if clock is None:
            continue
        open_min, close_min = window
        if close_min < open_min:  # 跨零点营业
            inside = clock >= open_min or clock <= close_min
        else:
            inside = open_min <= clock <= close_min
        if not inside:
            failures.append(
                {
                    "code": "opening_hours_conflict",
                    "detail": f"「{node_name(node)}」安排在 {node.get('time')}，但开放时间为 {raw}",
                }
            )

    # 6) 一体化骨架：吃/玩/住的存在性（不允许"只有景点、没有酒店或正餐"）
    from .itinerary_skeleton import LONG_TRIP_DAYS, validate_skeleton  # 延迟导入，避免模块级循环

    skeleton = validate_skeleton(plan, ctx, expectations)
    failures.extend(skeleton["failures"])

    # 7) 长途（≥14 天）段间衔接：天数缺口/越界、分段住宿夜数不足属硬失败
    if days >= LONG_TRIP_DAYS:
        from .long_trip import continuity_report  # 延迟导入，避免模块级循环

        continuity = continuity_report(plan, {**dict(ctx), "expectations": expectations})
        failures.extend(continuity["failures"])

    # 8) 玩点必须是真景点，且不能一类刷到底 —— 阈值来自**构成策略**
    #    （目的地 + 用户偏好 + 二次增量共同推导；用户说"想多打卡自然景观"时目标会跟着变）
    #    用户实测（2026-09-19，库尔勒 7 天）：「xx博物馆-西北门地上停车场」「xx博物馆文创空间」
    #    「xx园林宾馆」被当成景点，且 7 天几乎全是博物馆。选点层同时也在挡
    #    （core/poi_pool.is_play_worthy），两道一起才既挡得住数据源、也挡得住模型自己编的。
    from .composition import composition_policy, composition_targets_met  # 延迟导入，避免模块级循环
    from .poi_pool import FACILITY_NAME_MARKERS, is_play_worthy  # 延迟导入

    hits: List[Dict[str, Any]] = []
    for node in nodes:
        label = _coarse_category(node)
        if label in {"food", "hotel"}:
            continue
        text = " ".join(
            [node_name(node), str(node.get("type") or ""), " ".join(str(tag) for tag in (node.get("tags") or []))]
        ).lower()
        if any(marker in text for marker in FACILITY_NAME_MARKERS) or not is_play_worthy(
            node_name(node), node.get("type"), strict_type=False
        ):
            failures.append(
                {
                    "code": "facility_as_attraction",
                    "detail": f"「{node_name(node)}」不是可游玩的景点（停车场/商店/住宿/交通设施一类），不该排进行程",
                }
            )
            continue
        hits.append(node)

    policy = composition_policy(ctx, increment=ctx.get("increment"), signals=None, expectations=expectations)
    targets = composition_targets_met(plan, policy)

    # 8.1 单一类别占比上限（2 天 4 个点里 3 个文化类属正常，长行程才判）
    #     "自然景观占比高"不算单调：用户要的就是自然（targets 里已按策略排除 scenic）。
    if days >= 3 and len(hits) >= 6 and targets["share_over_cap"]:
        monotony_counts = {
            name: value for name, value in targets["counts"].items()
            if not (name == "scenic" and policy["min_scenic_per_day"] > 0)
        }
        top_name, top_count = max(monotony_counts.items(), key=lambda item: item[1]) if monotony_counts else ("-", 0)
        failures.append(
            {
                "code": "play_category_monotony",
                "detail": (
                    f"玩点里 {top_count} 个都是「{top_name}」，超过上限 "
                    f"{int(float(policy['max_category_share']) * 100)}%（{policy['note']}）"
                ),
            }
        )
    elif len(hits) >= 4 and targets["share_over_cap"]:
        unverifiable.append(
            {
                "code": "play_category_monotony_hint",
                "detail": "玩点类型偏单一，建议混一些别的类型",
            }
        )

    # 8.2 文化类总量上限：自然型目的地默认设上限（用户要求少安排文化类时会压得更低）
    if targets["cultural_over_cap"]:
        failures.append(
            {
                "code": "category_cap_exceeded",
                "detail": (
                    f"文化类（博物馆/古迹）排了 {targets['cultural_total']} 个，超过上限 "
                    f"{policy['max_cultural_total']} 个（{policy['note']}）"
                ),
            }
        )

    # 8.3 每天的自然景观下限：自然型目的地每天至少 1 个（二次增量要求"多打卡自然"时目标会上调）
    if policy["min_scenic_per_day"] > 0 and targets["scenic_shortfall_days"]:
        failures.append(
            {
                "code": "scenic_shortfall",
                "detail": (
                    "第 " + "、".join(str(day) for day in targets["scenic_shortfall_days"][:6])
                    + f" 天没有自然景观（这趟要求每天至少 {policy['min_scenic_per_day']} 个自然景观：{policy['note']}）"
                ),
            }
        )

    return {"failures": failures, "unverifiable": unverifiable}


# --------------------------------------------------------------------------
# 加权维度
# --------------------------------------------------------------------------
# 偏好标签 -> 命中关键词（只做可判定的粗分类，避免"感觉上满足"）
PREFERENCE_TAXONOMY: Dict[str, Tuple[str, ...]] = {
    "food": ("餐", "食", "小吃", "夜市", "老字号", "火锅", "面", "饭", "茶", "咖啡", "甜品", "烧烤", "馍", "馆子", "饺子", "包子", "串", "粥", "米粉"),
    "scenic": ("景区", "风景", "公园", "山", "湖", "江", "河", "海", "岛", "峡", "瀑布", "草原", "森林"),
    "cultural": ("博物馆", "博物院", "展馆", "古迹", "遗址", "寺", "庙", "塔", "古城", "古镇", "文化", "美术馆", "纪念馆", "书院"),
    "shopping": ("购物", "商场", "商圈", "步行街", "市集", "市场", "超市", "奥莱"),
    "hotel": ("酒店", "民宿", "住宿", "客栈", "旅舍"),
    "nightlife": ("夜景", "酒吧", "夜市", "灯光", "夜游", "演出"),
    "outdoor": ("徒步", "登山", "露营", "骑行", "步道", "栈道", "漂流", "滑雪"),
    "family": ("亲子", "乐园", "动物园", "海洋馆", "水族", "科技馆", "游乐"),
    "photo": ("摄影", "机位", "打卡", "花海", "日出", "日落", "观景"),
    "hotspring": ("温泉", "汤泉", "泡汤"),
    "market": ("菜市场", "集市", "老街", "小吃街", "美食街"),
    "landmark": ("地标", "广场", "塔", "桥", "钟楼", "鼓楼", "城墙"),
}

# 复合意图：必须**同时**命中每一组关键词才算满足（避免"有山有水"只排了山就算过）
COMPOUND_INTENTS: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    "mountain_water": (
        ("山", "峰", "峡谷", "石林", "雪山", "丘陵", "崖"),
        ("湖", "江", "河", "海", "溪", "瀑", "水", "滩", "泉", "岛", "湿地"),
    ),
}
COMPOUND_TRIGGER_WORDS = ("山水", "有山有水", "依山傍水", "山和水", "湖光山色")


def _compound_requests(text: str) -> List[str]:
    """识别复合意图：显式说法（"有山有水"）或同时出现两组关键词。"""
    requested: List[str] = []
    for label, groups in COMPOUND_INTENTS.items():
        if any(word in text for word in COMPOUND_TRIGGER_WORDS):
            requested.append(label)
            continue
        if all(any(keyword in text for keyword in group) for group in groups):
            requested.append(label)
    return requested


def _signals_to_tags(signals: Optional[Mapping[str, Any]], context: Mapping[str, Any]) -> List[str]:
    """把偏好信号（字符串/数组/布尔）映射为 PREFERENCE_TAXONOMY 的标签集合。"""
    tags: List[str] = []
    raw_chunks: List[str] = []
    preferences = context.get("preferences") if isinstance(context.get("preferences"), Mapping) else {}

    def absorb(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            raw_chunks.append(value)
        elif isinstance(value, Sequence):
            for item in value:
                absorb(item)
        elif isinstance(value, Mapping):
            for item in value.values():
                absorb(item)

    for source in (signals or {}, preferences):
        for key in ("interest", "interests", "style", "tags", "requirements", "keywords", "preference"):
            absorb(source.get(key) if isinstance(source, Mapping) else None)
    # 布尔 flag（如 wants_food）也纳入
    combined = {**(signals or {}), **(preferences if isinstance(preferences, Mapping) else {})}
    for label, keywords in PREFERENCE_TAXONOMY.items():
        if combined.get(f"wants_{label}") is True or combined.get(label) is True:
            tags.append(label)
    text = " ".join(raw_chunks)
    for label, keywords in PREFERENCE_TAXONOMY.items():
        if label in tags:
            continue
        if any(keyword in text for keyword in keywords):
            tags.append(label)
    return sorted(set(tags))


def score_preference_coverage(
    context: Mapping[str, Any],
    plan: Any,
    signals: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    nodes = nodes_of(plan)
    tags = _signals_to_tags(signals, context)
    haystack = " ".join(
        f"{node_name(n)} {n.get('type') or ''} {n.get('desc') or ''} {' '.join(map(str, n.get('tags') or []))}"
        for n in nodes
    )
    addressed, missing = [], []
    for label in tags:
        keywords = PREFERENCE_TAXONOMY[label]
        (addressed if any(k in haystack for k in keywords) else missing).append(label)

    # 复合意图（如"有山有水"要求两类**都**出现，而不是命中任意一类就算满足）
    preferences = context.get("preferences") if isinstance(context.get("preferences"), Mapping) else {}
    request_text = " ".join(
        [
            str((signals or {}).get("interest") or ""),
            str((signals or {}).get("interests") or ""),
            str(preferences.get("interest") or ""),
            str(preferences.get("interests") or ""),
            str(context.get("request") or ""),
        ]
    )
    compound_requests = _compound_requests(request_text)
    for label in compound_requests:
        groups = COMPOUND_INTENTS[label]
        ok = all(any(keyword in haystack for keyword in group) for group in groups)
        (addressed if ok else missing).append(label)

    total = len(tags) + len(compound_requests)
    return {
        "value": _ratio(len(addressed), total),
        "requested": tags + compound_requests,
        "addressed": addressed,
        "missing": missing,
        "compound_requests": compound_requests,
    }


def score_pacing(
    context: Mapping[str, Any],
    plan: Any,
    expectations: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    expectations = expectations or {}
    nodes = nodes_of(plan)
    if not nodes:
        return {"value": 0.0, "detail": "无节点", "days": []}

    relaxed = str((context.get("preferences") or {}).get("pace") or "").lower() in {"relaxed", "slow", "慢", "轻松"}
    max_nodes = int(
        _as_float(
            expectations.get("max_nodes_per_day"),
            RELAXED_MAX_NODES_PER_DAY if relaxed else DEFAULT_MAX_NODES_PER_DAY,
        )
    )
    active_budget = _as_float(context.get("daily_active_minutes"), DEFAULT_DAILY_ACTIVE_MINUTES)

    by_day: Dict[int, List[Dict[str, Any]]] = {}
    for index, node in enumerate(nodes, start=1):
        by_day.setdefault(node_day(node, index), []).append(node)

    penalties: List[str] = []
    advisories: List[str] = []
    day_reports: List[Dict[str, Any]] = []
    for day, day_nodes in sorted(by_day.items()):
        dwell = sum(dwell_minutes_of(n) for n in day_nodes)
        travel = sum(travel_minutes_of(n) for n in day_nodes)
        total = dwell + travel
        clocks = [parse_clock(n.get("time")) for n in day_nodes]
        clocks = [c for c in clocks if c is not None]
        has_lunch = any(
            any(k in f"{node_name(n)} {n.get('type') or ''}" for k in PREFERENCE_TAXONOMY["food"])
            and MEAL_WINDOWS[0][0] <= (parse_clock(n.get("time")) or -1) <= MEAL_WINDOWS[0][1]
            for n in day_nodes
        )
        has_dinner = any(
            any(k in f"{node_name(n)} {n.get('type') or ''}" for k in PREFERENCE_TAXONOMY["food"])
            and MEAL_WINDOWS[1][0] <= (parse_clock(n.get("time")) or -1) <= MEAL_WINDOWS[1][1]
            for n in day_nodes
        )
        report = {
            "day": day,
            "nodes": len(day_nodes),
            "dwell_minutes": round(dwell, 1),
            "travel_minutes": round(travel, 1),
            "total_minutes": round(total, 1),
            "span": (max(clocks) - min(clocks)) if len(clocks) >= 2 else 0,
            "has_lunch_window": has_lunch,
            "has_dinner_window": has_dinner,
        }
        day_reports.append(report)
        last_clock = max(clocks) if clocks else None
        if len(day_nodes) > max_nodes:
            penalties.append(f"第 {day} 天 {len(day_nodes)} 个节点，超过上限 {max_nodes}")
        if total > active_budget:
            penalties.append(f"第 {day} 天预计 {total:.0f} 分钟（停留+通勤），超过 {active_budget:.0f} 分钟上限")
        # 饭点：**只作为顾问提示，不扣分**（2026-10-05 用户口径）。
        # 用户原话："放宽上限吧，要是用户不喜欢晚上不安排晚餐，在二次增量中说到就补充晚餐位置。"
        # 也就是说：没排晚餐**不是缺陷**，是等用户表态的可选偏好 ——
        # 扣分等于系统替用户认定"你必须吃晚饭"，而真正的动作是"用户提了就用增量补上"。
        if last_clock is not None and last_clock > MEAL_WINDOWS[0][1] and not has_lunch:
            advisories.append(f"第 {day} 天跨过 13:30 没有午餐节点（想要的话可以说「加一顿午饭」）")
        if last_clock is not None and last_clock > MEAL_WINDOWS[1][0] and not has_dinner:
            advisories.append(f"第 {day} 天跨过 17:00 没有晚餐节点（想要的话可以说「加一顿晚饭」）")

    value = max(0.0, 1.0 - 0.2 * len(penalties))
    return {"value": value, "penalties": penalties, "advisories": advisories, "days": day_reports}


def score_space_efficiency(context: Mapping[str, Any], plan: Any) -> Dict[str, Any]:
    """空间效率：单日移动里程相对**用户自己的距离口径**是否划算。

    ⚠️ 这把尺子以前用固定刻度（40km 以内满分、120km 以上零分），于是：
    用户明明说了"不介意跑远"，方案跑到 167km/天就被判 0 分 —— 拿一把**用户已经否掉**的
    尺子去量他主动选择的东西。正确的口径是：以 `travel_feasibility.daily_budget_km(容忍度)`
    为"当天预算"（normal 160 / high 320 / low 80），预算的 1/4 以内算高效、用满预算算 0 分。
    用户说"远一点也能接受"，不是"效率不重要"，所以**超过他自己的预算照样 0 分**。
    """
    nodes = nodes_of(plan)
    by_day: Dict[int, List[Dict[str, Any]]] = {}
    for index, node in enumerate(nodes, start=1):
        by_day.setdefault(node_day(node, index), []).append(node)

    tolerance = str((context or {}).get("distance_tolerance") or "normal").strip().lower()
    try:
        from .travel_feasibility import daily_budget_km

        budget = float(daily_budget_km(tolerance))
    except Exception:  # 口径取不到时不假装宽松，回落到 normal 预算
        budget = 160.0
    comfortable = max(10.0, budget * 0.25)
    # "一次长途跳跃"的门槛同样跟着口径走：对 normal 是 40km，对说"不介意跑远"的人就是 80km ——
    # 否则等于用市内尺度去惩罚他自己选的跨州行程（同一个错尺子，换个地方又犯一次）。
    long_hop_km = max(20.0, LONG_HOP_KM * budget / 160.0)

    total_km, long_hops = 0.0, 0
    day_km: List[float] = []
    days_with_coords = 0
    for day, day_nodes in sorted(by_day.items()):
        coords = [node_lnglat(n) for n in day_nodes]
        coords = [c for c in coords if c is not None]
        if len(coords) >= 2:
            days_with_coords += 1
        km = 0.0
        for a, b in zip(coords, coords[1:]):
            distance = haversine_km(a, b)
            km += distance
            if distance > long_hop_km:
                long_hops += 1
        day_km.append(km)
        total_km += km

    days = max(1, len(by_day))
    per_day = total_km / days
    # 参考尺度：舒适线以内满分，用满当天预算是 0 分，中间线性
    base = max(0.0, min(1.0, (budget - per_day) / max(1.0, budget - comfortable)))
    # 坐标缺失不允许被当成"高效"：按有坐标的天数占比折算
    coverage = _ratio(days_with_coords, days, empty=0.0)
    value = base * coverage
    # 来回折腾要扣，但**不能把"预算内的一天"直接扣成 0**（2026-10-05 实测）：
    # 乌鲁木齐 106km/天（预算 160km）里有 3 段 >40km 的跳跃，旧写法 0.45-0.45 → 0.0，
    # 与"远超预算"的喀什 447km 拿到同一个 0 分 —— 分不出"能接受但绕"和"根本跑不完"。
    # 现在扣分上限是当前得分的 1/2，保留"绕"与"跑不完"的区别。
    if long_hops:
        value = max(0.0, value - min(0.15 * long_hops, 0.5 * value))
    return {
        "value": value,
        "total_km": round(total_km, 1),
        "per_day_km": round(per_day, 1),
        "max_day_km": round(max(day_km) if day_km else 0.0, 1),
        "long_hops": long_hops,
        "days": days,
        "coordinate_coverage": round(coverage, 3),
        "daily_budget_km": budget,
        "long_hop_km": round(long_hop_km, 1),
        "distance_tolerance": tolerance,
    }


# 吃住到当天玩点的可接受距离（公里）
ANCHOR_KM = 15.0


def _is_anchor_side(node: Mapping[str, Any]) -> bool:
    """"吃住一侧"的节点（餐饮/住宿）——它们应当贴着当天的玩点。"""
    text = f"{node_name(node)} {node.get('type') or ''} {node.get('desc') or ''}"
    tags = node.get("tags") or []
    if isinstance(tags, list):
        text += " " + " ".join(str(tag) for tag in tags)
    return any(keyword in text for keyword in PREFERENCE_TAXONOMY["hotel"]) or any(
        keyword in text for keyword in PREFERENCE_TAXONOMY["food"]
    )


def score_anchoring(context: Mapping[str, Any], plan: Any) -> Dict[str, Any]:
    """吃住是否贴着当天玩点 —— "中间的吃住应该如何安排"的可判定版本。

    规则：当天每个餐饮/住宿节点，到当天任一玩点的**最近距离**应 ≤ `ANCHOR_KM`（默认 15km）。
    缺坐标不计入分子，但按"有坐标占比"折算：**缺数据不给满分**（与空间效率口径一致）。
    """
    nodes = nodes_of(plan)
    if not nodes:
        return {"value": 0.0, "checked": 0, "within": 0, "threshold_km": ANCHOR_KM, "far_nodes": []}

    by_day: Dict[int, List[Dict[str, Any]]] = {}
    for index, node in enumerate(nodes, start=1):
        by_day.setdefault(node_day(node, index), []).append(node)

    checked = within = coord_total = coord_ok = 0
    far_nodes: List[Dict[str, Any]] = []
    for day, day_nodes in sorted(by_day.items()):
        plays = [(node_name(n), node_lnglat(n)) for n in day_nodes if not _is_anchor_side(n)]
        plays = [(name, pos) for name, pos in plays if pos is not None]
        for node in day_nodes:
            if not _is_anchor_side(node):
                continue
            coord_total += 1
            position = node_lnglat(node)
            if position is None or not plays:
                continue
            coord_ok += 1
            nearest = min(haversine_km(position, play_pos) for _, play_pos in plays)
            checked += 1
            if nearest <= ANCHOR_KM:
                within += 1
            else:
                far_nodes.append({"day": day, "name": node_name(node), "distance_km": round(nearest, 1)})

    coverage = _ratio(coord_ok, coord_total, empty=0.0)
    base = _ratio(within, checked, empty=0.0)
    return {
        "value": base * coverage,
        "checked": checked,
        "within": within,
        "threshold_km": ANCHOR_KM,
        "far_nodes": far_nodes[:8],
        "coordinate_coverage": round(coverage, 3),
    }


def score_truthfulness(plan: Any) -> Dict[str, Any]:
    """真实度：节点上**说出来的东西有没有出处**（"未知"不算错，装懂才算错）。

    ⚠️ 这把尺子以前把两件事混成一件（2026-10-05 实测修正）：
      它只要看到 `estimated: true` 就不给分。但 `estimated` 在真实链路里是
      "**没有供应商价格**"的意思（节点上写着「暂无供应商数据」），也就是系统**如实说了不知道**。
      结果：一份把"暂无供应商数据"逐条写在脸上的方案，真实度被判 0.0 —— 尺子在惩罚诚实。
    现在的口径（与 doctrine「unknown ≠ 0 ≠ estimated」一致）：
      - 只检查**真的做出了断言**的字段（数值/文字，不含"暂无供应商数据/未核实"这类披露）；
      - 断言字段的 source 不在可核实集合里 → 记 `unsourced`（**这是"装懂"，真正要拦的**）；
      - 什么都没断言、只写了"暂无数据" → 不算撒谎，计入 `disclosed_unknown`；
      - `estimated_prices` 只做**透明性统计**（这份方案里有多少价格是"估"的），不参与扣分 ——
        判"有没有出处"的依据是 source，不是那个估计标记：高德的人均消费（source=amap）
        既真又带出处，只因为是"人均"就被判"不可信"同样是拿错尺子。
    价格到底查到了没有、可不可信，由 `completeness.price_coverage` / `unknown_disclosed`
    与这里的 `unsourced_claims` 分别回答。
    """
    nodes = nodes_of(plan)
    if not nodes:
        return {
            "value": 0.0,
            "verified_nodes": 0,
            "total_nodes": 0,
            "unsourced_claims": [],
            "unsourced_count": 0,
            "estimated_prices": 0,
            "disclosed_unknown": 0,
        }

    verified, unsourced, estimated_prices, disclosed_unknown = 0, [], 0, 0
    for node in nodes:
        sources = node.get("data_sources") if isinstance(node.get("data_sources"), Mapping) else {}
        claimed = [f for f in SOURCED_FIELDS if _is_claim(node.get(f))]
        bad = [
            f
            for f in claimed
            if str(sources.get(f, "")).strip().lower() not in VERIFIED_SOURCES_OK
        ]
        if bad:
            unsourced.append({"name": node_name(node), "fields": bad})
        # 透明性统计：价格是"估"的（不参与扣分，出处由上面的 source 判定负责）
        # ⚠️ 只看 cost_estimate：真实节点里的 `estimated` 是"**没有供应商原价**"的标记，
        #    拿它去套 rating（"4.5" 也是数字）会把有 amap 出处的评分一起判成"估算"。
        if bool(node.get("estimated")) and _has_number(node.get("cost_estimate")):
            estimated_prices += 1
        if not claimed:
            disclosed_unknown += 1
        if not bad:
            verified += 1
    return {
        "value": _ratio(verified, len(nodes)),
        "verified_nodes": verified,
        "total_nodes": len(nodes),
        "unsourced_claims": unsourced[:10],
        "unsourced_count": len(unsourced),
        "estimated_prices": estimated_prices,
        "disclosed_unknown": disclosed_unknown,
    }


def _coarse_category(node: Mapping[str, Any]) -> str:
    """粗分类：优先用 simulation 的英文口径，落到 other 时再用中文关键词兜底。

    真实规划输出的 `type` 是中文（"餐饮"/"住宿"/"博物馆"…），而 CATEGORY_DWELL_SIGMA
    的键是英文，直接用它会让所有节点都落到 other、多样性失真。
    """
    base = category_of(node)
    if base != "other":
        return base
    text = f"{node_name(node)} {node.get('type') or ''} {node.get('desc') or ''}"
    tags = node.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    text = text + " " + " ".join(str(tag) for tag in tags)
    # 明确的文化场馆词优先于“山/湖/公园”等泛自然词，避免“天山博物院”
    # 被误判为 scenic，进而错误满足自然景观配额。
    if any(keyword in text for keyword in ("博物馆", "博物院", "展馆", "展览馆", "纪念馆", "美术馆")):
        return "cultural"
    for label, keywords in PREFERENCE_TAXONOMY.items():
        if any(keyword in text for keyword in keywords):
            return label
    return "other"


def score_diversity(plan: Any) -> Dict[str, Any]:
    nodes = nodes_of(plan)
    if not nodes:
        return {"value": 0.0, "categories": {}, "duplicate_names": 0, "max_run": 0, "duplicate_name_list": []}

    categories: Dict[str, int] = {}
    names: Dict[str, int] = {}
    sequence: List[str] = []
    for node in nodes:
        category = _coarse_category(node)
        categories[category] = categories.get(category, 0) + 1
        key = _normalize(node_name(node))
        if key:
            names[key] = names.get(key, 0) + 1
        sequence.append(category)

    total = len(nodes)
    entropy = 0.0
    for count in categories.values():
        p = count / total
        entropy -= p * math.log(p + 1e-12)
    max_entropy = math.log(max(2, len(categories)))
    evenness = entropy / max_entropy if max_entropy > 0 else 0.0

    max_run, current = 1, 1
    for previous, current_item in zip(sequence, sequence[1:]):
        current = current + 1 if current_item == previous else 1
        max_run = max(max_run, current)

    duplicates = sum(1 for count in names.values() if count > 1)
    value = max(0.0, evenness - 0.1 * duplicates - 0.1 * max(0, max_run - 2))
    return {
        "value": max(0.0, min(1.0, value)),
        "categories": categories,
        "duplicate_names": duplicates,
        "duplicate_name_list": [name for name, count in names.items() if count > 1][:5],
        "max_run": max_run,
    }


def stability_score(plans: Sequence[Any]) -> Dict[str, Any]:
    """同一输入多次运行的一致性：节点名集合的两两 Jaccard 均值。"""
    sets = []
    for plan in plans:
        names = {_normalize(node_name(n)) for n in nodes_of(plan)}
        sets.append({n for n in names if n})
    if len(sets) < 2:
        return {"value": None, "runs": len(sets), "detail": "需要至少两次运行才能评估稳定性"}
    scores: List[float] = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = sets[i] | sets[j]
            inter = sets[i] & sets[j]
            scores.append(len(inter) / len(union) if union else 1.0)
    return {"value": round(sum(scores) / len(scores), 3), "runs": len(sets)}


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------
def _flatten_candidates(candidates_by_intent: Optional[Mapping[str, Any]]) -> List[Any]:
    """把"意图 → 候选列表"摊平成一串候选（供地点质量维度判断"这个节点是不是检索到的"）。"""
    flat: List[Any] = []
    if not isinstance(candidates_by_intent, Mapping):
        return flat
    for value in candidates_by_intent.values():
        if isinstance(value, Mapping):
            flat.extend(value.keys())
            continue
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            flat.extend(value)
    return flat


def plan_quality_snapshot(
    context: Mapping[str, Any],
    plan: Any,
    expectations: Optional[Mapping[str, Any]] = None,
    signals: Optional[Mapping[str, Any]] = None,
    candidates_by_intent: Optional[Mapping[str, Any]] = None,
    include_fallback: bool = True,
    tier_policy: Optional[Mapping[str, int]] = None,
) -> Dict[str, Any]:
    """给调用方（`api/agent.py`、CLI）一个"一次调用拿到全部结论"的入口。

    返回 `{quality, budget, skeleton, fallback}`：
    - `quality`：分数 / verdict / 门禁失败明细 / 未核实计数 / 各维度分值（精简，便于直接下发前端）；
    - `budget`：三级价格模型报告（verified / estimated / unknown + status + confidence）；
    - `skeleton`：一体化槽位摘要（住宿夜数 / 餐数 / 玩点数 vs 要求）；
    - `fallback`：仅当预算 `over` / `at_risk` 时给出兜底提案（含人话 `disclosure`），否则为 None。

    纯确定性、零网络；**不修改传入的 plan**。业务逻辑放在 core 里，`agent.py` 只做薄接线，
    这样接线本身不需要跑 LLM 就能单测。
    """
    report = evaluate_plan(
        context, plan, expectations=expectations, signals=signals, candidates=_flatten_candidates(candidates_by_intent)
    )
    from .itinerary_skeleton import LONG_TRIP_DAYS, horizon_advisories, segment_days  # 延迟导入（见模块头说明）

    days = max(1, int(context.get("days") or context.get("trip_days") or 1))
    advisories = list(horizon_advisories(context))
    continuity: Optional[Dict[str, Any]] = None
    if days >= 7:
        from .long_trip import continuity_report  # 延迟导入，避免模块级循环

        continuity = continuity_report(plan, {**dict(context), "expectations": expectations})
        if days >= LONG_TRIP_DAYS:
            # 长途：把衔接顾问与"下一段简报"一起给出去（前端面板已在渲染 advisories）
            advisories.extend(continuity["advisories"])
    snapshot: Dict[str, Any] = {
        "quality": {
            "score": report["score"],
            "verdict": report["verdict"],
            "gate_passed": report["gate"]["passed"],
            "gate_failures": report["gate"]["failures"],
            "unverifiable_count": report["gate"]["unverifiable_count"],
            "dimensions": {key: value["value"] for key, value in report["dimensions"].items()},
            # 「地点好不好」与「方案周全」的**明细**（给专业诊断/评测报告用）：
            # 只有分数没法行动，必须能看见"哪些名字可疑 / 哪几条周全性没过"
            "place_quality": {
                key: value
                for key, value in (report["dimensions"].get("place_quality") or {}).items()
                if key != "value"
            },
            "completeness": {
                key: value
                for key, value in (report["dimensions"].get("completeness") or {}).items()
                if key != "value"
            },
            # 吃住与玩点的距离明细：分数之外要能看见"是哪几个点离得远"
            # （`anchoring` 掉分但界面上没有任何解释时，用户只会觉得"说不清哪里怪"）
            "anchoring": {
                key: value
                for key, value in (report["dimensions"].get("anchoring") or {}).items()
                if key != "value"
            },
        },
        "budget": report["budget"],
        "skeleton": report["skeleton"],
        "horizon": {
            "days": days,
            "segments": segment_days(days),
            "advisories": advisories,
            "continuity": (
                {
                    "ok": continuity["ok"],
                    "rest_days": continuity["rest_days"],
                    "rest_days_required": continuity["rest_days_required"],
                    "budgets": continuity["budgets"],
                }
                if continuity
                else None
            ),
        },
        "fallback": None,
    }
    if include_fallback and report["budget"]["status"] in {"over", "at_risk"}:
        from .budget_planner import propose_fallback  # 延迟导入（见模块头说明）

        snapshot["fallback"] = propose_fallback(
            context,
            plan,
            candidates_by_intent=candidates_by_intent,
            tier_policy_map=tier_policy,
        )
    return snapshot


def evaluate_plan(
    context: Mapping[str, Any],
    plan: Any,
    expectations: Optional[Mapping[str, Any]] = None,
    signals: Optional[Mapping[str, Any]] = None,
    weights: Optional[Mapping[str, float]] = None,
    candidates: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """给一份规划打分：门禁 + 加权维度 + verdict。

    `candidates` 是**本次真实候选池**（可选）：地点质量维度用它判断"这个节点是不是检索到的"。
    不传也能跑，但那一项会如实标成"没有供应商证据"（而不是假装可信或假装不可信）。
    """
    hard = check_hard_constraints(context, plan, expectations)
    from .place_quality import score_completeness, score_place_quality

    dimensions = {
        "preference_coverage": score_preference_coverage(context, plan, signals),
        "pacing": score_pacing(context, plan, expectations),
        "space_efficiency": score_space_efficiency(context, plan),
        "truthfulness": score_truthfulness(plan),
        "anchoring": score_anchoring(context, plan),
        "diversity": score_diversity(plan),
        # 用户第一优先级「地点好不好」的可判定部分 + 「方案周全」
        "place_quality": score_place_quality(plan, candidates=candidates),
        "completeness": score_completeness(context, plan, expectations),
    }
    active_weights = dict(DEFAULT_WEIGHTS)
    if weights:
        active_weights.update({k: float(v) for k, v in weights.items() if k in DEFAULT_WEIGHTS})
    weight_sum = sum(active_weights.values()) or 1.0
    score = sum(dimensions[key]["value"] * active_weights[key] for key in active_weights) / weight_sum * 100.0

    passed = not hard["failures"]
    if not passed:
        verdict = "fail"
    elif hard["unverifiable"]:
        verdict = "pass_with_unverified"
    else:
        verdict = "pass"

    from .budget_planner import budget_report  # 延迟导入（见文件头说明）
    from .itinerary_skeleton import skeleton_summary

    money = budget_report(context, plan)
    skeleton = skeleton_summary(plan, context, expectations)

    return {
        "score": round(score, 1),
        "verdict": verdict,
        "gate": {
            "passed": passed,
            "failures": hard["failures"],
            "unverifiable": hard["unverifiable"],
            "unverifiable_count": len(hard["unverifiable"]),
        },
        "budget": money,
        "skeleton": {
            "lodging_nights": skeleton["lodging_nights"],
            "meals": skeleton["meals"],
            "plays": skeleton["plays"],
            "requirements": skeleton["requirements"],
        },
        "dimensions": {
            key: {"value": round(value["value"], 3), **{k: v for k, v in value.items() if k != "value"}}
            for key, value in dimensions.items()
        },
        "weights": active_weights,
        "nodes": len(nodes_of(plan)),
    }
