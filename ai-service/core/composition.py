"""行程构成策略：**按目的地 + 用户偏好 + 二次增量**推导"这趟该看什么"。

为什么不写死
------------
"自然型目的地每天至少 1 个自然景观、博物馆全行程 ≤2" 这种规则如果写死，就会犯两个错：
- 用户明明说"我主要想逛博物馆"，却被硬塞自然景观；
- 用户二次规划说"**想多打卡自然景观**"，规则不会跟着变。
所以这里做成一层**策略**：给出目标值（不是常量），由三处信息共同决定 ——

1. **目的地/原话**：自然型（山湖草原沙漠、或原话提到自然/风景/山水）→ 默认每天 ≥1 个自然景观、
   文化类总量设上限；城市型则放宽；
2. **用户偏好**（signals/interest）：提到自然风光 → 抬高自然目标；偏历史/博物馆 → 放宽文化上限；
3. **二次增量**（`core/increment.parse_increment` 的结果）：`quota={"scenic": +2}` 说明用户
   明确要**多加自然景观** → 每天自然景观目标上调、文化类上限下调；`quota={"cultural": -1}`
   （例如"少看博物馆"）→ 文化类上限下调甚至归零，并把它记进"别再排这么多"的类别。

输出的都是**目标与上限**，判定层（plan_quality）拿它去检查，补点修复拿 `required_categories`
去决定补什么类型的点 —— 三处共用一份策略，行为才不会各说各话。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

# 自然型目的地的判定线索：地名/原话/兴趣里出现这些，就认为这趟是奔着自然去的
NATURE_DESTINATION_HINTS = (
    "自然", "风景", "山水", "草原", "沙漠", "湖", "河", "峡谷", "雪山", "冰川", "胡杨",
    "森林", "湿地", "海岛", "海滩", "天池", "花海", "梯田", "丹霞", "溶洞", "国家公园",
)
NATURE_CITY_HINTS = (
    "新疆", "西藏", "青海", "内蒙", "甘肃", "云南", "贵州", "四川", "桂林", "张家界", "九寨",
    "香格里拉", "稻城", "伊犁", "喀纳斯", "库尔勒", "乌鲁木齐", "拉萨", "西宁", "敦煌", "张掖",
    "呼伦贝尔", "阿尔山", "丽江", "大理", "泸沽湖", "三亚", "北海", "武夷", "黄山", "长白山",
)
# 文化类（博物馆/古迹…）：自然型目的地默认给它设上限，避免"7 天全是博物馆"
CULTURE_CATEGORIES = ("cultural",)

# 目标上限的边界（避免策略被极端输入推飞）
MAX_DAILY_SCENIC = 3
DEFAULT_MAX_CATEGORY_SHARE = 0.8


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "")


def _texts(context: Mapping[str, Any], signals: Optional[Mapping[str, Any]]) -> str:
    parts: List[str] = [
        str(context.get("request_text") or ""),
        str(context.get("city") or ""),
        " ".join(str(item) for item in (context.get("destinations") or [])),
    ]
    preferences = context.get("preferences") if isinstance(context.get("preferences"), Mapping) else {}
    interests = preferences.get("interest") or []
    if isinstance(interests, str):
        interests = [interests]
    parts.extend(str(item) for item in interests)
    if isinstance(signals, Mapping):
        parts.extend(str(item) for item in (signals.get("interest") or []))
    return _normalize(" ".join(parts))


def composition_policy(
    context: Mapping[str, Any],
    increment: Optional[Mapping[str, Any]] = None,
    signals: Optional[Mapping[str, Any]] = None,
    expectations: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """给出这趟行程的构成目标：每天自然景观数、单一类别占比上限、文化类总量上限、必须补的类别。"""
    context = context or {}
    expectations = expectations or {}
    increment = increment or {}
    days = max(1, int(context.get("days") or context.get("trip_days") or 1))
    haystack = _texts(context, signals)

    nature_destination = any(hint in haystack for hint in NATURE_DESTINATION_HINTS) or any(
        hint in haystack for hint in (_normalize(item) for item in NATURE_CITY_HINTS)
    )
    # 用户明确说想多看自然（偏好或二次增量）也要算进去
    quota: Dict[str, int] = {
        str(key): int(value)
        for key, value in (increment.get("quota") or {}).items()
        if isinstance(value, int) and value
    }
    wants_more_scenic = quota.get("scenic", 0) > 0
    wants_less_culture = quota.get("cultural", 0) < 0

    min_scenic_per_day = 0.0
    max_cultural_total: Optional[int] = None
    max_category_share = DEFAULT_MAX_CATEGORY_SHARE
    reasons: List[str] = []

    if nature_destination:
        min_scenic_per_day = 1.0
        max_cultural_total = max(1, days // 3)
        max_category_share = 0.6
        reasons.append("这趟以自然风景为主：每天至少 1 个自然景观，文化类设上限")
    if expectations.get("min_scenic_per_day") is not None:
        min_scenic_per_day = max(min_scenic_per_day, float(expectations["min_scenic_per_day"]))
    if expectations.get("max_cultural_total") is not None:
        max_cultural_total = int(expectations["max_cultural_total"])

    # ---- 二次增量：用户这次说的话，直接改目标 ----
    if wants_more_scenic:
        gain = max(1, int(round(quota["scenic"] / max(1, days / 7))))  # 7 天的 +2 ≈ 每天 +0.3 → 至少 +1
        min_scenic_per_day = min(MAX_DAILY_SCENIC, min_scenic_per_day + gain)
        reasons.append(f"你这次要求多加自然景观（+{quota['scenic']}）→ 自然景观目标上调")
        if max_cultural_total is not None:
            max_cultural_total = max(0, max_cultural_total - 1)
        else:
            max_cultural_total = max(0, days // 3)
        max_category_share = min(max_category_share, 0.6)
    if wants_less_culture:
        reduce_by = max(1, abs(quota["cultural"]))
        max_cultural_total = max(0, (max_cultural_total if max_cultural_total is not None else days // 3) - reduce_by)
        reasons.append(f"你这次要求少安排文化类（{quota['cultural']}）→ 文化类上限下调")
    for category, delta in quota.items():
        if delta > 0 and category != "scenic":
            reasons.append(f"你这次要求多加「{category}」{delta} 个 → 补点时优先这一类")

    required_categories = sorted({category for category, delta in quota.items() if delta > 0})
    return {
        "days": days,
        "nature_destination": bool(nature_destination),
        "min_scenic_per_day": round(min_scenic_per_day, 2),
        "max_cultural_total": max_cultural_total,
        "max_category_share": max_category_share,
        "required_categories": required_categories,
        "quota": quota,
        "reasons": reasons,
        "note": "；".join(reasons) if reasons else "没有特别的构成要求，按常规均衡安排",
    }


def count_categories(plan: Any) -> Dict[str, int]:
    """按"玩点类别"统计方案（餐饮/住宿不计入，它们不是玩点）。"""
    from .plan_quality import _coarse_category, nodes_of  # 延迟导入，避免模块级循环

    counts: Dict[str, int] = {}
    for node in nodes_of(plan):
        label = _coarse_category(node)
        if label in {"food", "hotel"}:
            continue
        counts[label] = counts.get(label, 0) + 1
    return counts


def scenic_days(plan: Any) -> Dict[int, int]:
    """每天有几个自然景观（自然型目的地的"每天 ≥1"就是看这个）。"""
    from .plan_quality import _coarse_category, node_day, nodes_of  # 延迟导入

    per_day: Dict[int, int] = {}
    for index, node in enumerate(nodes_of(plan), start=1):
        if _coarse_category(node) != "scenic":
            continue
        day = node_day(node, index)
        per_day[day] = per_day.get(day, 0) + 1
    return per_day


def composition_targets_met(plan: Any, policy: Mapping[str, Any]) -> Dict[str, Any]:
    """策略 vs 实际：达标了吗？没达标缺什么（补点修复据此决定补哪一类）。"""
    counts = count_categories(plan)
    total_plays = sum(counts.values())
    per_day = scenic_days(plan)
    days = max(1, int(policy.get("days") or 1))
    min_scenic = float(policy.get("min_scenic_per_day") or 0)
    max_cultural = policy.get("max_cultural_total")

    shortfall_days = [
        day for day in range(1, days + 1) if per_day.get(day, 0) < min_scenic
    ] if min_scenic > 0 else []
    cultural_total = sum(counts.get(name, 0) for name in CULTURE_CATEGORIES)
    share = max(counts.values()) / total_plays if total_plays else 0.0

    return {
        "counts": counts,
        "total_plays": total_plays,
        "scenic_days": per_day,
        "scenic_shortfall_days": shortfall_days,
        "cultural_total": cultural_total,
        "cultural_over_cap": bool(max_cultural is not None and cultural_total > int(max_cultural)),
        "category_share": round(share, 3),
        "share_over_cap": share > float(policy.get("max_category_share") or 1.0),
        "needs": (["scenic"] if shortfall_days else []) + (["scenic"] if policy.get("nature_destination") and not counts.get("scenic") else []),
    }
