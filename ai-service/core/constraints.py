# -*- coding: utf-8 -*-
"""确定性约束引擎与冲突消解。

把网关流入的「旅行数字孪生」上下文（hard_constraints / soft_preferences / profile /
realtime_state）规整为带优先级的约束集合，并提供：

  - build_hard_constraints / build_soft_preferences  约束注册表
  - evaluate_feasibility                             硬约束可行性审计（复用 optimization.validate_route）
  - score_soft_penalty                               软偏好加权惩罚
  - resolve_conflicts                                候选方案的确定性冲突消解
  - relax_hard_constraint                            可协商硬约束的确定性放宽
  - explain_decision                                 结构化可解释输出（依据/置信度/可调整范围）
  - detect_context_conflicts                         多成员诉求的确定性冲突检测

设计原则（与 optimization.py / travel_utils.py 保持一致）：
  * 纯函数、不调用模型或外部服务，便于离线回放与单元测试；
  * 只做确定性判定与消解，绝不伪造供应商缺失字段；
  * 约束按优先级分层：L0 硬约束 > L1 可协商硬约束 > L2 软偏好 > L3 画像倾向。

约束优先级数值越小越「硬」，冲突消解时先满足低数值层级的约束。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.optimization import fairness_report, pareto_frontier, validate_candidate, validate_route

# ---------------------------------------------------------------------------
# 约束优先级分层
# ---------------------------------------------------------------------------
LEVEL_HARD = 0            # L0 不可违反（安全 / 可行性 / 必选品类）
LEVEL_NEGOTIABLE = 1      # L1 可协商硬约束（预算软上限 / 天数 / 单日容量）
LEVEL_SOFT = 2            # L2 软偏好（成员兴趣 / 节奏 / 消费倾向）
LEVEL_PROFILE = 3         # L3 画像倾向（最弱，仅排序微调）

LEVEL_NAMES = {
    LEVEL_HARD: "hard",
    LEVEL_NEGOTIABLE: "negotiable_hard",
    LEVEL_SOFT: "soft",
    LEVEL_PROFILE: "profile",
}


# ---------------------------------------------------------------------------
# 防御性解析辅助
# ---------------------------------------------------------------------------
def _number(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)) and value == value:
            return float(value)
        text = str(value or "").replace(",", "")
        digits = "".join(ch if ch.isdigit() or ch in ".-" else " " for ch in text).split()
        return float(digits[0]) if digits else default
    except (TypeError, ValueError):
        return default


def _non_negative(value: Any, default: float = 0.0) -> float:
    return max(0.0, _number(value, default))


def _as_map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, (list, tuple, set)):
        return [item for item in value]
    return []


def _name(node: Mapping[str, Any]) -> str:
    return str(node.get("name") or node.get("location") or "").strip() if isinstance(node, Mapping) else ""


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# 上下文规整：把 contracts.go 的 PlanningContext 折叠为带默认值的字典
# ---------------------------------------------------------------------------
def normalize_context(context: Any) -> Dict[str, Any]:
    """把数字孪生上下文规整为标准字段（缺失字段用安全默认值）。

    认可两类键名：契约字段（snake_case）与前端历史字段（camelCase）。
    """
    ctx = _as_map(context)

    def pick(*keys: str) -> Any:
        for key in keys:
            if key in ctx and ctx[key] not in (None, ""):
                return ctx[key]
        return None

    budget = pick("budget", "total_budget")
    days = pick("days", "trip_days")
    hard = _as_map(pick("hard_constraints", "hardConstraints"))
    soft = _as_map(pick("soft_preferences", "softPreferences"))
    profile = _as_map(pick("profile", "personalized_profile", "personalizedProfile"))
    realtime = _as_map(pick("realtime_state", "realtimeState"))
    members = _as_list(pick("members", "room_members", "roomMembers"))

    return {
        "destination": str(pick("destination", "city") or "").strip(),
        "origin": str(pick("origin") or "").strip(),
        "budget": _non_negative(budget) if budget not in (None, "") else None,
        "days": int(_non_negative(days, 1)) if days not in (None, "") else None,
        "travelers": max(1, int(_non_negative(pick("travelers"), 1))),
        "currency": str(pick("currency") or "CNY").strip(),
        "start_date": str(pick("start_date", "startDate") or "").strip(),
        "end_date": str(pick("end_date", "endDate") or "").strip(),
        "max_nodes_per_day": pick("max_nodes_per_day", "maxNodesPerDay"),
        "risk_tolerance": pick("risk_tolerance", "riskTolerance"),
        "required_categories": _as_list(pick("required_categories", "requiredCategories")),
        "hard_constraints": hard,
        "soft_preferences": soft,
        "profile": profile,
        "realtime_state": realtime,
        "members": members,
    }


# ---------------------------------------------------------------------------
# 约束注册表：从上下文构建带优先级的约束集合
# ---------------------------------------------------------------------------
def build_hard_constraints(context: Any) -> List[Dict[str, Any]]:
    """返回 L0/L1 硬约束列表（含来源与判定参数）。"""
    ctx = normalize_context(context)
    constraints: List[Dict[str, Any]] = []

    def add(code: str, level: int, label: str, params: Dict[str, Any], source: str) -> None:
        constraints.append({
            "code": code,
            "level": level,
            "kind": LEVEL_NAMES[level],
            "label": label,
            "weight": None,
            "params": params,
            "source": source,
        })

    # —— L0 硬约束（不可违反，由确定性校验直接判罚）——
    if ctx["budget"] is not None:
        add("budget_upper", LEVEL_HARD, "总预算上限", {"limit": ctx["budget"]}, "context.budget")
    if ctx["days"] is not None:
        add("days", LEVEL_HARD, "行程天数", {"days": ctx["days"]}, "context.days")
    if ctx["required_categories"]:
        add("required_categories", LEVEL_HARD, "必选品类", {"categories": ctx["required_categories"]}, "context.required_categories")

    # —— L1 可协商硬约束（有默认上限，可被显式声明覆盖）——
    max_nodes = _number(ctx["max_nodes_per_day"], 0.0) if ctx["max_nodes_per_day"] not in (None, "") else None
    if max_nodes and max_nodes > 0:
        add("max_nodes_per_day", LEVEL_NEGOTIABLE, "单日容量上限", {"limit": int(max_nodes)}, "context.max_nodes_per_day")
    risk = _number(ctx["risk_tolerance"], 100.0) if ctx["risk_tolerance"] not in (None, "") else None
    if risk is not None:
        add("risk_tolerance", LEVEL_NEGOTIABLE, "风险容忍度", {"limit": risk}, "context.risk_tolerance")

    # —— 显式声明的额外硬约束（契约扩展字段透传）——
    for key, value in ctx["hard_constraints"].items():
        if key in {"budget_upper", "days", "required_categories", "max_nodes_per_day", "risk_tolerance"}:
            continue
        params = value if isinstance(value, Mapping) else {"value": value}
        add(str(key), LEVEL_HARD if key.startswith(("hard_", "must_", "safety_")) else LEVEL_NEGOTIABLE,
            str(key), params, "context.hard_constraints")
    return constraints


def build_soft_preferences(context: Any) -> List[Dict[str, Any]]:
    """返回 L2/L3 软偏好列表（带权重），冲突消解时按权重惩罚。"""
    ctx = normalize_context(context)
    prefs: List[Dict[str, Any]] = []

    def add(code: str, level: int, label: str, weight: float, params: Dict[str, Any], source: str) -> None:
        if weight <= 0:
            return
        prefs.append({
            "code": code,
            "level": level,
            "kind": LEVEL_NAMES[level],
            "label": label,
            "weight": round(float(weight), 4),
            "params": params,
            "source": source,
        })

    # —— L2 软偏好 ——
    soft = ctx["soft_preferences"]
    for member in ctx["members"]:
        if not isinstance(member, Mapping):
            continue
        tags = _as_list(member.get("interestTags") or member.get("interest_tags"))
        if tags:
            add(f"interest.{member.get('id') or member.get('name') or 'member'}", LEVEL_SOFT,
                "兴趣偏好", 1.0, {"tags": [str(t) for t in tags]}, "context.members.interests")
    if "pace" in soft or "pace_weight" in soft:
        add("pace", LEVEL_SOFT, "游玩节奏", _non_negative(soft.get("pace") if "pace" in soft else soft.get("pace_weight"), 1.0),
            {}, "context.soft_preferences")
    if "interest_tags" in soft or "interestTags" in soft:
        tags = _as_list(soft.get("interest_tags") if "interest_tags" in soft else soft.get("interestTags"))
        add("interest_tags", LEVEL_SOFT, "兴趣偏好", 1.0, {"tags": [str(t) for t in tags]}, "context.soft_preferences")

    # —— L3 画像倾向（最弱，仅微调）——
    profile = ctx["profile"]
    if profile:
        bt = str(profile.get("budget_tendency") or "").strip().lower()
        if bt in {"low", "high"}:
            add("budget_tendency", LEVEL_PROFILE, "消费倾向", 0.5, {"tendency": bt}, "context.profile")
    return prefs


# ---------------------------------------------------------------------------
# 硬约束可行性审计
# ---------------------------------------------------------------------------
def evaluate_feasibility(route: Sequence[Mapping[str, Any]], context: Any) -> Dict[str, Any]:
    """返回 route 的硬约束可行性报告（L0 不可违反 / L1 可协商分列）。"""
    ctx = normalize_context(context)
    hard = build_hard_constraints(ctx)

    required: Dict[int, Iterable[str]] = {}
    for constraint in hard:
        if constraint["code"] == "required_categories":
            required = {day: constraint["params"]["categories"] for day in range(1, max(1, int(_non_negative(ctx["days"], 1))))}

    report = validate_route(
        route,
        budget=ctx["budget"],
        days=ctx["days"],
        max_nodes_per_day=_number(ctx["max_nodes_per_day"]).__int__() if ctx["max_nodes_per_day"] not in (None, "") else None,
        risk_tolerance=_number(ctx["risk_tolerance"]) if ctx["risk_tolerance"] not in (None, "") else None,
        required_categories=required or None,
    )
    violations = report.get("violations", [])
    hard_violations = [v for v in violations if _violation_level(v["code"], hard) == LEVEL_HARD]
    negotiable_violations = [v for v in violations if _violation_level(v["code"], hard) == LEVEL_NEGOTIABLE]
    return {
        "feasible": report.get("feasible", False),
        "hard_violations": hard_violations,
        "negotiable_violations": negotiable_violations,
        "violations": violations,
        "checked": report.get("checked", {}),
        "constraints": hard,
    }


def _violation_level(code: str, hard_constraints: Sequence[Mapping[str, Any]]) -> int:
    """把 validate_route 的 violation code 映射回约束层级。"""
    code_to_level = {c["code"]: c["level"] for c in hard_constraints}
    known = {
        "budget_exceeded": "budget_upper",
        "missing_day": "days",
        "daily_capacity_exceeded": "max_nodes_per_day",
        "risk_tolerance_exceeded": "risk_tolerance",
        "required_category_missing": "required_categories",
    }
    return int(code_to_level.get(known.get(code, code), LEVEL_HARD))


# ---------------------------------------------------------------------------
# 软偏好加权惩罚
# ---------------------------------------------------------------------------
def score_soft_penalty(route: Sequence[Mapping[str, Any]], context: Any, members: Optional[Sequence[Mapping[str, Any]]] = None) -> Dict[str, Any]:
    """量化软偏好的满足程度，返回 (penalty, mean_utility, max_regret)。

    penalty 越低越好：由成员效用缺口（1 - utility）加权合成，再叠加软偏好权重。
    """
    ctx = normalize_context(context)
    member_list = [m for m in (members or ctx["members"]) if isinstance(m, Mapping)]
    prefs = build_soft_preferences(ctx)

    fairness = fairness_report(route, member_list)
    mean_utility = fairness.get("mean_utility", 1.0)
    max_regret = fairness.get("max_regret", 0.0)

    soft_weight = sum(pref["weight"] for pref in prefs if pref["level"] == LEVEL_SOFT)
    profile_weight = sum(pref["weight"] for pref in prefs if pref["level"] == LEVEL_PROFILE)
    unsatisfied = _clamp(1.0 - mean_utility)
    penalty = round(unsatisfied * (1.0 + soft_weight) + 0.25 * max_regret * (1.0 + profile_weight), 6)
    return {
        "penalty": penalty,
        "mean_utility": round(mean_utility, 6),
        "max_regret": round(max_regret, 6),
        "soft_preferences": prefs,
    }


# ---------------------------------------------------------------------------
# 冲突消解：硬过滤 → 软权衡 + 公平性 → Pareto
# ---------------------------------------------------------------------------
def resolve_conflicts(
    candidates: Sequence[Any],
    context: Any,
    members: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """确定性冲突消解主入口。

    candidates 支持两种形态：
      1. [route, ...]            —— 每个元素是一段 route 节点列表；
      2. [{"route": [...], "name": ...}, ...]。

    流程：L0 硬约束过滤 → 可行候选按「软惩罚 + 公平性」做 Pareto 前沿 →
    输出可解释的排序与淘汰依据（服务于第 4 / 6 项的说明与重规划）。
    """
    ctx = normalize_context(context)
    member_list = [m for m in (members or ctx["members"]) if isinstance(m, Mapping)]

    routes: List[Dict[str, Any]] = []
    for index, item in enumerate(candidates):
        if isinstance(item, Mapping) and "route" in item:
            routes.append({"index": index, "name": str(item.get("name") or f"route-{index}"), "nodes": _as_list(item["route"])})
        elif isinstance(item, (list, tuple)):
            routes.append({"index": index, "name": f"route-{index}", "nodes": [n for n in item if isinstance(n, Mapping)]})

    feasible: List[Dict[str, Any]] = []
    infeasible: List[Dict[str, Any]] = []
    for spec in routes:
        name, route, index = spec["name"], spec["nodes"], spec["index"]
        audit = evaluate_feasibility(route, ctx)
        score = score_soft_penalty(route, ctx, member_list)
        entry = {
            "name": name,
            "index": index,
            "route": route,
            "feasible": not bool(audit["hard_violations"]),
            "hard_violations": audit["hard_violations"],
            "negotiable_violations": audit["negotiable_violations"],
            "penalty": score["penalty"],
            "mean_utility": score["mean_utility"],
            "max_regret": score["max_regret"],
        }
        if audit["hard_violations"]:
            entry["_hard_violations"] = audit["hard_violations"]
            infeasible.append(entry)
        else:
            feasible.append(entry)

    # 对可行候选做多目标 Pareto：最小化惩罚、最小化后悔、最大化均值效用。
    frontier_result: Dict[str, Any] = {"frontier": [], "rejected": []}
    if feasible:
        frontier_result = pareto_frontier(
            feasible,
            {
                "penalty": lambda row: float(row["penalty"]),
                "max_regret": lambda row: float(row["max_regret"]),
                "mean_utility": lambda row: float(row["mean_utility"]),
            },
            {"penalty": False, "max_regret": False, "mean_utility": True},
        )

    ranked = sorted(
        feasible,
        key=lambda row: (-float(row["mean_utility"]), float(row["penalty"]), float(row["max_regret"])),
    )

    # 冲突摘要：可行里牺牲了谁的诉求、不可行里违反了哪些硬约束。
    conflict_summary: List[Dict[str, Any]] = []
    for row in infeasible:
        conflict_summary.append({
            "route": row["name"],
            "kind": "infeasible",
            "detail": [v.get("code") for v in row["hard_violations"]],
        })
    if ranked:
        best, worst = ranked[0], ranked[-1]
        if best["max_regret"] - worst["max_regret"] > 1e-6 and member_list:
            conflict_summary.append({
                "route": best["name"],
                "kind": "preference_tradeoff",
                "detail": {"max_regret": best["max_regret"], "suggested": "公平性妥协，需向低效用成员说明或协商"},
            })

    return {
        "feasible_count": len(feasible),
        "infeasible_count": len(infeasible),
        "ranked": [{k: v for k, v in row.items() if not k.startswith("_")} for row in ranked],
        "frontier": [r for r in frontier_result.get("frontier", []) if isinstance(r, Mapping)],
        "rejected": frontier_result.get("rejected", []),
        "conflict_summary": conflict_summary,
    }


# ---------------------------------------------------------------------------
# 可协商硬约束的确定性放宽
# ---------------------------------------------------------------------------
def relax_hard_constraint(context: Any, code: str) -> Dict[str, Any]:
    """对单个可协商硬约束给出确定性放宽建议（不改变原上下文，返回新上下文副本）。"""
    ctx = normalize_context(context)
    relaxed = dict(ctx)
    strategy = {
        "max_nodes_per_day": ("单日容量上限 +1", {"max_nodes_per_day": int(_number(ctx["max_nodes_per_day"], 0)) + 1}),
        "risk_tolerance": ("风险容忍度 +10", {"risk_tolerance": min(100.0, _number(ctx["risk_tolerance"], 100.0) + 10.0)}),
        "days": ("行程天数 +1", {"days": (ctx["days"] or 1) + 1}),
        "budget_upper": ("预算软上限 +10%", {"budget": round((ctx["budget"] or 0) * 1.1, 2)}),
    }
    if code not in strategy:
        return {"relaxed": False, "reason": f"不支持的放宽约束 {code!r}", "context": relaxed}
    label, patch = strategy[code]
    relaxed.update(patch)
    return {"relaxed": True, "code": code, "label": label, "patch": patch, "context": relaxed}


# ---------------------------------------------------------------------------
# 结构化可解释输出
# ---------------------------------------------------------------------------
def explain_decision(route: Sequence[Mapping[str, Any]], context: Any, members: Optional[Sequence[Mapping[str, Any]]] = None) -> Dict[str, Any]:
    """生成结构化解释：依据、置信度、可调整范围、备选。

    供前端「共识解释 / 可调整滑块」使用；置信度为确定性估算（非模型概率）。
    """
    ctx = normalize_context(context)
    member_list = [m for m in (members or ctx["members"]) if isinstance(m, Mapping)]
    audit = evaluate_feasibility(route, ctx)
    score = score_soft_penalty(route, ctx, member_list)

    basis: List[Dict[str, Any]] = []
    for constraint in audit["constraints"]:
        basis.append({"code": constraint["code"], "label": constraint["label"], "level": constraint["level"], "kind": constraint["kind"]})
    for pref in score["soft_preferences"]:
        basis.append({"code": pref["code"], "label": pref["label"], "level": pref["level"], "kind": pref["kind"], "weight": pref["weight"]})

    adjustable: List[Dict[str, Any]] = [
        {"param": "budget", "current": ctx["budget"], "range": None, "impact": "L0 硬约束，放宽需重新协商"},
        {"param": "days", "current": ctx["days"], "range": None, "impact": "L0 硬约束，放宽需重新协商"},
    ]
    for code in ("max_nodes_per_day", "risk_tolerance"):
        if any(c["code"] == code for c in audit["constraints"]):
            relaxed = relax_hard_constraint(ctx, code)
            adjustable.append({"param": code, "current": ctx.get(code), "range": relaxed.get("patch"), "impact": "L1 可协商，可自动放宽"})

    blocked = bool(audit["hard_violations"])
    confidence = 0.0 if blocked else 1.0
    if not blocked:
        if not member_list:
            confidence -= 0.1
        if not ctx["realtime_state"]:
            confidence -= 0.1
        confidence -= 0.05 * len(audit["negotiable_violations"])
    confidence = round(_clamp(confidence), 3)

    return {
        "route": _name(route[0]) if isinstance(route, (list, tuple)) and route and isinstance(route[0], Mapping) else "",
        "feasible": audit["feasible"],
        "basis": basis,
        "confidence": confidence,
        "soft_penalty": score["penalty"],
        "mean_utility": score["mean_utility"],
        "max_regret": score["max_regret"],
        "adjustable": adjustable,
        "alternatives": [],
    }


# ---------------------------------------------------------------------------
# 多成员诉求的确定性冲突检测（规划前）
# ---------------------------------------------------------------------------
def detect_context_conflicts(context: Any, members: Optional[Sequence[Mapping[str, Any]]] = None) -> Dict[str, Any]:
    """在进入 LLM 前检测成员间的确定性冲突（预算/风险/节奏口径不一致）。"""
    ctx = normalize_context(context)
    member_list = [m for m in (members or ctx["members"]) if isinstance(m, Mapping)]
    conflicts: List[Dict[str, Any]] = []
    budgets = [_non_negative(m.get("budgetWeight") or m.get("budget_weight")) for m in member_list]
    risks = [_non_negative(m.get("riskWeight") or m.get("risk_weight")) for m in member_list]
    if budgets and max(budgets) - min(budgets) >= 1.5:
        conflicts.append({"kind": "budget", "spread": round(max(budgets) - min(budgets), 4), "suggestion": "预算诉求跨度大，按均值预算 + 分层可选方案处理"})
    if risks and max(risks) - min(risks) >= 1.5:
        conflicts.append({"kind": "risk", "spread": round(max(risks) - min(risks), 4), "suggestion": "风险偏好分歧，高风险项目设为可选而非必选"})
    return {"has_conflict": bool(conflicts), "conflicts": conflicts}