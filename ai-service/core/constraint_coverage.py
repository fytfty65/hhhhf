"""用户诉求的逐条核对（约束覆盖度）：把"你到底要我做什么、我做到哪一步"变成后端真值字段。

为什么单独一层
--------------
同类开源项目（FloatTrip）踩过的坑值得记下来：它的后端**算好了** `constraint_coverage`
（applied / advisory / unverified），但前端与移动端的适配层都没有映射这两个字段，
结果界面上 `unverified` 一次都没出现 —— **没渲染出来的"未核实"，等于没做**。

所以这一层做两件事：
1. 把用户诉求（必去项、兴趣、有山有水这类复合意图、预算、节奏、休整、吃住就近、
   饮食/无障碍、二次规划的排他/配额/档位）逐条算成**稳定 id + 状态**的真值字段；
2. 明确区分五种状态，**不把"没数据"混进"已落实"**：

   - `applied`：落实了（有证据）；
   - `partially_applied`：部分落实（比例/缺口写在 detail）；
   - `unverified`：说不上（缺数据：价格、菜品、台阶、坐标……）——**不算通过**；
   - `advisory`：只写进了偏好/建议，本轮没有可核实的改变（例如升档没有可比价）；
   - `missing`：明确没做到（例如点名的地点还在、要求改的地方一字没改）。

其中最后一条 `missing` 里的 `change:applied` 是**"说改但没改"的机械检测**：
用户明确提了修改意图，而方案一字未动（没有任何增量指标）时，必须如实写出来，
而不是回一句"已按你的要求调整"。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from .plan_quality import (
    node_day,
    node_name,
    nodes_of,
    score_anchoring,
    score_pacing,
    score_preference_coverage,
)

# 状态词表（前端逐条渲染，评测也能断言）
STATUS_APPLIED = "applied"
STATUS_PARTIAL = "partially_applied"
STATUS_UNVERIFIED = "unverified"
STATUS_ADVISORY = "advisory"
STATUS_MISSING = "missing"

CATEGORY_LABELS: Dict[str, str] = {
    "food": "美食",
    "scenic": "自然风光",
    "cultural": "文化/历史",
    "mountain_water": "有山有水（山水都要有）",
    "nightlife": "夜生活",
    "shopping": "购物",
    "family": "亲子",
    "hotel": "住宿",
    "food_cat": "餐饮",
}

TIER_LABELS: Dict[str, str] = {"hotel": "住宿", "food": "餐饮"}

MAX_ITEMS = 14


def _norm(text: Any) -> str:
    return str(text or "").strip().lower().replace(" ", "")


def _item(
    identifier: str,
    category: str,
    text: str,
    status: str,
    polarity: str = "require",
    source: str = "user_explicit",
    detail: str = "",
) -> Dict[str, Any]:
    return {
        "id": identifier,
        "category": category,
        "text": text,
        "polarity": polarity,
        "source": source,
        "status": status,
        "detail": detail,
    }


def _haystack(plan: Any) -> str:
    parts: List[str] = []
    for node in nodes_of(plan):
        tags = node.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        joined = " ".join(
            [node_name(node), str(node.get("type") or ""), str(node.get("desc") or "")] + [str(tag) for tag in tags]
        )
        parts.append(_norm(joined))
    return " ".join(parts)


def _rest_days(plan: Any) -> int:
    """低强度日（当天 ≤1 个玩点）的天数：休整诉求的判定依据。"""
    from .long_trip import _play_count_by_day  # 复用同一口径，避免两套"休整日"定义

    counts = _play_count_by_day(plan)
    return sum(1 for count in counts.values() if count <= 1)


def constraint_coverage(
    context: Mapping[str, Any],
    plan: Any,
    expectations: Optional[Mapping[str, Any]] = None,
    signals: Optional[Mapping[str, Any]] = None,
    increment: Optional[Mapping[str, Any]] = None,
    increment_metrics: Optional[Mapping[str, Any]] = None,
    tier_policy_map: Optional[Mapping[str, int]] = None,
    exclusions: Optional[Mapping[str, Any]] = None,
    request_text: str = "",
) -> Dict[str, Any]:
    """把用户诉求逐条核对成 `{items, counts, summary, unverified_ids, missing_ids}`。"""
    context = context or {}
    expectations = expectations or {}
    increment = increment or {}
    preferences = context.get("preferences") if isinstance(context.get("preferences"), Mapping) else {}
    haystack = _haystack(plan)
    names = [_norm(node_name(node)) for node in nodes_of(plan)]
    items: List[Dict[str, Any]] = []

    # 1) 必去项
    for wanted in expectations.get("must_have") or []:
        target = _norm(wanted)
        if not target:
            continue
        hit = any(target in text or text in target for text in haystack.split(" ") if text)
        items.append(
            _item(
                f"must_have:{wanted}",
                "must_have",
                f"必须包含「{wanted}」",
                STATUS_APPLIED if hit else STATUS_MISSING,
                detail="" if hit else "方案里没有找到这个内容",
            )
        )

    # 2) 兴趣/偏好类别（含"有山有水"这类复合意图：山、水两组必须同时命中）
    coverage = score_preference_coverage(context, plan, signals)
    compound = set(coverage.get("compound_requests") or [])
    for name in coverage.get("addressed") or []:
        items.append(
            _item(
                f"interest:{name}",
                "interest",
                CATEGORY_LABELS.get(name, str(name)),
                STATUS_APPLIED,
                polarity="prefer",
                source="signal",
            )
        )
    for name in coverage.get("missing") or []:
        if name in compound:
            detail = "只命中了一半（山或水缺一项），复合诉求要两组都有"
        else:
            detail = "方案里没有这类内容"
        items.append(
            _item(
                f"interest:{name}",
                "interest",
                CATEGORY_LABELS.get(name, str(name)),
                STATUS_MISSING,
                polarity="prefer",
                source="signal",
                detail=detail,
            )
        )

    # 3) 预算（三级价格模型：只有可核实价格才算"落实"）
    from .budget_planner import budget_report  # 延迟导入，避免与 budget_planner 形成模块级循环

    money = budget_report(context, plan)
    if money.get("budget", 0) > 0:
        status = {
            "ok": STATUS_APPLIED,
            "over": STATUS_MISSING,
            "at_risk": STATUS_UNVERIFIED,
            "unverifiable": STATUS_UNVERIFIED,
        }.get(str(money.get("status")), STATUS_UNVERIFIED)
        detail = {
            "ok": f"可核实花费 ¥{money.get('verified_cost', 0):.0f} 在预算内",
            "over": f"可核实花费超出预算约 ¥{money.get('shortfall', 0):.0f}",
            "at_risk": f"含估算后可能超支约 ¥{money.get('shortfall', 0):.0f}（估算部分已标明，不算已验证）",
            "unverifiable": "所有价格都取不到来源，预算无法核实（不视为满足）",
        }.get(str(money.get("status")), "")
        items.append(_item("budget", "budget", f"预算 ¥{money.get('budget', 0):.0f} 以内", status, detail=detail))

    # 4) 节奏
    if preferences.get("pace"):
        pacing = score_pacing(context, plan, expectations)
        value = float(pacing.get("value") or 0.0)
        if value >= 0.8:
            status, detail = STATUS_APPLIED, ""
        elif value >= 0.5:
            status, detail = STATUS_PARTIAL, f"节奏匹配度 {value:.0%}，有个别天偏紧"
        else:
            status, detail = STATUS_MISSING, f"节奏匹配度 {value:.0%}，与你要的节奏不符"
        items.append(_item("pace", "pace", f"节奏：{preferences.get('pace')}", status, polarity="prefer", detail=detail))

    # 5) 休整日
    if preferences.get("rest_days"):
        rest = _rest_days(plan)
        days = int(context.get("days") or 0)
        wanted_rest = 1 if days and days < 7 else max(1, days // 7)
        if rest >= wanted_rest:
            status, detail = STATUS_APPLIED, f"低强度日 {rest} 天"
        elif rest > 0:
            status, detail = STATUS_PARTIAL, f"只有 {rest} 天低强度，建议至少 {wanted_rest} 天"
        else:
            status, detail = STATUS_MISSING, "一天都没留出低强度日"
        items.append(_item("rest_days", "pace", "中间要有休息的日子", status, polarity="prefer", detail=detail))

    # 6) 吃住就近（"别每天在路上折腾"）
    if preferences.get("anchor_near_scenic") or expectations.get("anchoring_km"):
        anchor = score_anchoring(context, plan)
        value = float(anchor.get("value") or 0.0)
        far = anchor.get("far_nodes") or []
        coverage_ratio = float(anchor.get("coordinate_coverage") or 0.0)
        if coverage_ratio < 0.999:
            status = STATUS_UNVERIFIED
            detail = f"有节点缺坐标（坐标覆盖 {coverage_ratio:.0%}），吃住距离无法完全核实"
        elif not far and value >= 0.999:
            status, detail = STATUS_APPLIED, f"吃住都在当天玩点 {anchor.get('threshold_km')} km 内"
        elif value >= 0.6:
            status = STATUS_PARTIAL
            detail = "；".join(f"第 {item['day']} 天「{item['name']}」约 {item['distance_km']} km" for item in far[:3])
        else:
            status = STATUS_MISSING
            detail = "；".join(f"第 {item['day']} 天「{item['name']}」约 {item['distance_km']} km" for item in far[:3])
        items.append(
            _item("anchor_near_scenic", "anchoring", "住得/吃得离玩点近一点", status, polarity="prefer", detail=detail)
        )

    # 7) 饮食与无障碍：**我们确实没有这些数据**，如实记"未核实"，绝不假装满足
    if preferences.get("dietary"):
        items.append(
            _item(
                "dietary",
                "accessibility",
                f"饮食要求：{preferences.get('dietary')}",
                STATUS_UNVERIFIED,
                detail="没有菜品级数据，是否满足无法核实（已按你的要求排了餐饮节点，但没核实菜品）",
            )
        )
    if preferences.get("accessibility"):
        items.append(
            _item(
                "accessibility",
                "accessibility",
                f"体力/无障碍：{preferences.get('accessibility')}",
                STATUS_UNVERIFIED,
                detail="没有台阶/坡道/步行距离数据，是否满足无法核实",
            )
        )

    # 8) 二次规划：排他（点名的地点必须真的不在方案里）
    removed = {_norm(item) for item in ((exclusions or {}).get("removed") or [])}
    not_found = {_norm(item) for item in ((exclusions or {}).get("not_found") or [])}
    unreplaced = {_norm(item) for item in ((exclusions or {}).get("unreplaced") or [])}
    for name in increment.get("exclude") or []:
        key = _norm(name)
        if key in not_found:
            status, detail = STATUS_UNVERIFIED, "方案里本来就没有这个名字，无法确认你指的是哪个地点"
        elif any(key and key in candidate for candidate in names):
            status, detail = STATUS_MISSING, "这个地点还在方案里"
        elif key in unreplaced:
            status, detail = STATUS_PARTIAL, "已经移出方案，但没有找到同类替代（候选池里没有合适的）"
        elif removed or any(key and key in candidate for candidate in removed):
            status, detail = STATUS_APPLIED, "已移出方案并换成同类"
        else:
            status, detail = STATUS_PARTIAL, "已从方案里去掉"
        items.append(_item(f"exclude:{name}", "change", f"不要「{name}」", status, detail=detail))

    # 9) 二次规划：配额（"多加两顿"必须真的落到节点上）
    metrics = increment_metrics or {}
    quota = {k: v for k, v in (increment.get("quota") or {}).items() if isinstance(v, int) and v}
    if quota:
        requested = int(metrics.get("requested") or sum(quota.values()))
        achieved = int(metrics.get("achieved") or 0)
        if achieved >= requested:
            status, detail = STATUS_APPLIED, f"要 {requested} 个，已加上 {achieved} 个"
        elif achieved > 0:
            status, detail = STATUS_PARTIAL, f"要 {requested} 个，只加上 {achieved} 个"
        else:
            status, detail = STATUS_MISSING, "一个都没加上（候选池里没有可用地点）"
        label = "、".join(f"{CATEGORY_LABELS.get(key, key)} +{value}" for key, value in quota.items())
        items.append(_item("quota", "change", f"增加：{label}", status, detail=detail))

    # 10) 档位（升/降档）：写进了偏好，但没有可比价就**不能说已经变好**
    for label, value in (tier_policy_map or {}).items():
        if not value:
            continue
        direction = "升一档" if value > 0 else "降一档"
        items.append(
            _item(
                f"tier:{label}",
                "change",
                f"{TIER_LABELS.get(label, label)}{direction}",
                STATUS_ADVISORY,
                polarity="prefer",
                detail="已写进下一轮排序偏好；本轮是否真的换到对应档位，需要可核实的价格才能确认",
            )
        )

    # 11) "说改但没改"的机械检测（沿用同类项目 staleness 检测的思路）
    if increment and not increment.get("is_noop"):
        asked = bool(increment.get("exclude") or quota or increment.get("upgrade") or increment.get("downgrade"))
        if asked and not (removed or unreplaced or not_found or metrics):
            items.append(
                _item(
                    "change:applied",
                    "change",
                    "按你这次说的做调整",
                    STATUS_MISSING,
                    detail="这一次没能落实到行程里（没匹配到可替换的候选），不能说「已经调整好了」",
                )
            )
        elif asked:
            achieved = int(metrics.get("achieved") or len(removed) or 0)
            if not achieved and not removed:
                items.append(
                    _item(
                        "change:applied",
                        "change",
                        "按你这次说的做调整",
                        STATUS_UNVERIFIED,
                        detail="已经按你说的重排了，但没有可核实的结果可以确认",
                    )
                )

    counts: Dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    summary = "共 {total} 条诉求：落实 {applied} · 部分落实 {partial} · 未核实 {unverified} · 仅建议 {advisory} · 没做到 {missing}".format(
        total=len(items),
        applied=counts.get(STATUS_APPLIED, 0),
        partial=counts.get(STATUS_PARTIAL, 0),
        unverified=counts.get(STATUS_UNVERIFIED, 0),
        advisory=counts.get(STATUS_ADVISORY, 0),
        missing=counts.get(STATUS_MISSING, 0),
    )
    return {
        "items": items[:MAX_ITEMS],
        "total": len(items),
        "counts": counts,
        "summary": summary,
        "unverified_ids": [item["id"] for item in items if item["status"] == STATUS_UNVERIFIED][:8],
        "missing_ids": [item["id"] for item in items if item["status"] == STATUS_MISSING][:8],
    }
