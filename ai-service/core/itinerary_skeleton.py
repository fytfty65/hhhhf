"""槽位骨架：把"一体化（有吃有玩有住）"从"希望 LLM 吐出来"变成结构保证。

为什么需要它
------------
现状（`gateway/internal/handlers/planning.go`）里"景区周边住宿 / 本地风味小馆"只出现在
selected 文案，**没有任何强制注入住宿/餐饮节点的逻辑**，所以方案可能只有景点、没有酒店或正餐。
本模块先把骨架定下来（每天几个玩点槽、几餐、几夜住宿），再让候选去填空——结构上就不可能出现
"没吃/没住"。

与 plan_quality 的关系
---------------------
本模块是**生产者**（产出骨架 / 校验骨架），只依赖 `core.plan_quality` 的节点解析原语与
`PREFERENCE_TAXONOMY`。`plan_quality` 在门禁里**延迟导入**本模块，避免模块级循环导入。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .plan_quality import PREFERENCE_TAXONOMY, node_day, node_name, nodes_of

# 每天玩点槽数量：节奏越紧，槽越多（骨架先定，候选再填）
PLAY_SLOTS_BY_PACE: Dict[str, int] = {"relaxed": 1, "slow": 1, "慢": 1, "轻松": 1, "normal": 2, "intense": 3, "tight": 3}
DEFAULT_PLAY_SLOTS = 2
MEALS_PER_DAY = 2
MIN_PLAY_PER_DAY = 1


def _pace_of(context: Mapping[str, Any]) -> str:
    preferences = context.get("preferences") if isinstance(context.get("preferences"), Mapping) else {}
    return str(preferences.get("pace") or context.get("pace") or "normal").strip().lower()


def play_slots_for(context: Mapping[str, Any]) -> int:
    return PLAY_SLOTS_BY_PACE.get(_pace_of(context), DEFAULT_PLAY_SLOTS)


def _is_hotel(node: Mapping[str, Any]) -> bool:
    text = f"{node_name(node)} {node.get('type') or ''} {node.get('desc') or ''}"
    tags = node.get("tags") or []
    if isinstance(tags, list):
        text += " " + " ".join(str(tag) for tag in tags)
    if node.get("is_hotel") is True:
        return True
    return any(keyword in text for keyword in PREFERENCE_TAXONOMY["hotel"])


def _is_food(node: Mapping[str, Any]) -> bool:
    text = f"{node_name(node)} {node.get('type') or ''} {node.get('desc') or ''}"
    tags = node.get("tags") or []
    if isinstance(tags, list):
        text += " " + " ".join(str(tag) for tag in tags)
    return any(keyword in text for keyword in PREFERENCE_TAXONOMY["food"])


def skeleton_requirements(context: Mapping[str, Any], expectations: Optional[Mapping[str, Any]] = None) -> Dict[str, int]:
    """行程至少要有的槽位数。

    硬要求（门禁用）：N 天 ≥ N-1 夜住宿、每天 ≥1 个玩点、每天 ≥1 个餐饮节点、餐饮总数 ≥ 天数。
    软目标（只报告不判失败）：`meals_target = 2N-1`（每天午晚两餐），避免把"没排晚餐"直接判成不合格，
    但会在摘要里暴露差距。
    """
    expectations = expectations or {}
    days = max(1, int(context.get("days") or context.get("trip_days") or 1))
    lodging = 0 if days <= 1 else max(0, days - 1)
    if context.get("lodging_nights") is not None:
        lodging = max(0, int(context["lodging_nights"]))
    min_play = int(expectations.get("min_play_per_day") or MIN_PLAY_PER_DAY)
    return {
        "days": days,
        "lodging_nights": lodging,
        "meals_per_day": 1,
        "meals_total": max(1, days),
        "meals_target": max(1, MEALS_PER_DAY * days - 1),
        "play_per_day": max(1, min_play),
        "play_total": max(1, min_play * days),
    }


def build_skeleton(context: Mapping[str, Any], expectations: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """产出槽位骨架（供候选填空）：每天 play 槽 + meal 槽，每夜 lodging 槽。"""
    requirements = skeleton_requirements(context, expectations)
    play_slots = play_slots_for(context)
    days: List[Dict[str, Any]] = []
    for day in range(1, requirements["days"] + 1):
        days.append(
            {
                "day": day,
                "slots": (
                    [{"kind": "play", "role": "core" if index == 0 else "secondary", "index": index} for index in range(play_slots)]
                    + [{"kind": "meal", "role": "lunch"}, {"kind": "meal", "role": "dinner"}]
                    + ([{"kind": "lodging", "role": "night"}] if day <= requirements["lodging_nights"] else [])
                ),
            }
        )
    return {
        "days": days,
        "requirements": requirements,
        "pace": _pace_of(context),
        "play_slots_per_day": play_slots,
        "note": "骨架先定、候选后填：住宿/正餐/玩点的存在性由结构保证，不由 LLM 自觉保证。",
    }


def skeleton_summary(plan: Any, context: Mapping[str, Any], expectations: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """统计一份方案里的槽位覆盖情况（用于报告与门禁细节）。"""
    requirements = skeleton_requirements(context, expectations)
    nodes = nodes_of(plan)
    hotels = [node for node in nodes if _is_hotel(node)]
    foods = [node for node in nodes if _is_food(node) and not _is_hotel(node)]
    plays = [node for node in nodes if not _is_hotel(node) and not _is_food(node)]

    play_by_day: Dict[int, int] = {}
    meals_by_day: Dict[int, int] = {}
    for index, node in enumerate(nodes, start=1):
        day = node_day(node, index)
        if _is_hotel(node):
            continue
        if _is_food(node):
            meals_by_day[day] = meals_by_day.get(day, 0) + 1
            continue
        play_by_day[day] = play_by_day.get(day, 0) + 1

    return {
        "requirements": requirements,
        "lodging_nights": len(hotels),
        "meals": len(foods),
        "plays": len(plays),
        "play_by_day": play_by_day,
        "meals_by_day": meals_by_day,
        "days_covered": len({node_day(n, i) for i, n in enumerate(nodes, start=1)}),
    }


def validate_skeleton(plan: Any, context: Mapping[str, Any], expectations: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """把"一体化"当硬约束校验：住宿夜数、餐数、每天玩点数。"""
    summary = skeleton_summary(plan, context, expectations)
    requirements = summary["requirements"]
    failures: List[Dict[str, Any]] = []

    if summary["lodging_nights"] < requirements["lodging_nights"]:
        failures.append(
            {
                "code": "skeleton_lodging_shortfall",
                "detail": f"住宿仅 {summary['lodging_nights']} 夜，要求 ≥{requirements['lodging_nights']} 夜（一体化必须有住）",
            }
        )
    if summary["meals"] < requirements["meals_total"]:
        failures.append(
            {
                "code": "skeleton_meal_shortfall",
                "detail": f"餐饮节点仅 {summary['meals']} 个，要求 ≥{requirements['meals_total']} 个（一体化必须有吃）",
            }
        )
    for day in range(1, requirements["days"] + 1):
        count = summary["play_by_day"].get(day, 0)
        if count < requirements["play_per_day"]:
            failures.append(
                {
                    "code": "skeleton_play_missing",
                    "detail": f"第 {day} 天没有玩点（要求每天 ≥{requirements['play_per_day']} 个）",
                }
            )
        if summary["meals_by_day"].get(day, 0) < requirements["meals_per_day"]:
            failures.append(
                {
                    "code": "skeleton_meal_missing",
                    "detail": f"第 {day} 天没有餐饮节点（一体化要求每天至少 1 餐）",
                }
            )
    return {"ok": not failures, "failures": failures, "summary": summary}
