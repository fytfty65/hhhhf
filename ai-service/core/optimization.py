"""可验证的路线优化原语。

这些函数只处理结构化候选和路线，不调用模型或外部服务，便于离线评估、
属性测试和在线执行前的最后一道安全校验。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, (int, float)) and value == value:
            return float(value)
        text = str(value or "").replace(",", "")
        digits = "".join(ch if ch.isdigit() or ch in ".-" else " " for ch in text).split()
        return float(digits[0]) if digits else default
    except (TypeError, ValueError):
        return default


def _name(item: Mapping[str, Any]) -> str:
    return str(item.get("name") or item.get("location") or "").strip()


def _categories(item: Mapping[str, Any]) -> set[str]:
    values: List[str] = []
    for key in ("category", "type"):
        value = item.get(key)
        if isinstance(value, str):
            values.extend(value.lower().replace("/", "|").split("|"))
    tags = item.get("tags")
    if isinstance(tags, (list, tuple, set)):
        values.extend(str(tag).lower() for tag in tags)
    elif tags:
        values.append(str(tags).lower())
    return {value.strip() for value in values if value.strip()}


def validate_candidate(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate one candidate without inventing missing supplier fields."""
    violations: List[str] = []
    if not isinstance(candidate, Mapping):
        return {"valid": False, "violations": ["candidate_not_object"]}
    if not _name(candidate):
        violations.append("name_required")
    for field in ("price", "cost", "cost_estimate"):
        if field in candidate and candidate[field] not in (None, "", "未知", "暂无供应商数据"):
            if _number(candidate[field], -1) < 0:
                violations.append("negative_cost")
            break
    risk = candidate.get("risk_score", candidate.get("riskScore"))
    if risk not in (None, "") and not 0 <= _number(risk, -1) <= 100:
        violations.append("risk_out_of_range")
    return {"valid": not violations, "violations": violations}


def validate_route(
    route: Sequence[Mapping[str, Any]],
    *,
    budget: float | None = None,
    days: int | None = None,
    max_nodes_per_day: int | None = None,
    risk_tolerance: float | None = None,
    required_categories: Mapping[int, Iterable[str]] | None = None,
    unique_locations: bool = True,
) -> Dict[str, Any]:
    """Return a complete, auditable hard-constraint report for a route."""
    violations: List[Dict[str, Any]] = []
    if not isinstance(route, Sequence) or isinstance(route, (str, bytes)):
        return {"feasible": False, "violations": [{"code": "route_not_array"}], "checked": {}}
    seen: Dict[str, int] = {}
    by_day: Dict[int, List[Mapping[str, Any]]] = defaultdict(list)
    total_cost = 0.0
    for index, node in enumerate(route):
        report = validate_candidate(node)
        for code in report["violations"]:
            violations.append({"code": code, "index": index})
        if not isinstance(node, Mapping):
            continue
        name = _name(node)
        if unique_locations and name:
            if name in seen:
                violations.append({"code": "duplicate_location", "index": index, "first_index": seen[name], "location": name})
            else:
                seen[name] = index
        day = int(_number(node.get("day"), 1))
        by_day[day].append(node)
        total_cost += _number(node.get("cost", node.get("cost_estimate")), 0.0)
        if risk_tolerance is not None:
            risk = node.get("risk_score", node.get("riskScore"))
            if risk not in (None, "") and _number(risk, 0) > risk_tolerance:
                violations.append({"code": "risk_tolerance_exceeded", "index": index, "value": _number(risk), "limit": risk_tolerance})
    if days is not None:
        missing = [day for day in range(1, max(1, int(days)) + 1) if not by_day.get(day)]
        for day in missing:
            violations.append({"code": "missing_day", "day": day})
    if max_nodes_per_day is not None:
        for day, nodes in by_day.items():
            if len(nodes) > max_nodes_per_day:
                violations.append({"code": "daily_capacity_exceeded", "day": day, "count": len(nodes), "limit": max_nodes_per_day})
    if budget is not None and budget > 0 and total_cost > budget:
        violations.append({"code": "budget_exceeded", "value": round(total_cost, 2), "limit": budget})
    if required_categories:
        for day, categories in required_categories.items():
            actual = set().union(*(_categories(node) for node in by_day.get(int(day), []))) if by_day.get(int(day)) else set()
            missing = [category for category in categories if not any(str(category).lower() in value for value in actual)]
            if missing:
                violations.append({"code": "required_category_missing", "day": int(day), "categories": missing})
    return {
        "feasible": not violations,
        "violations": violations,
        "checked": {"days": days, "max_nodes_per_day": max_nodes_per_day, "budget": budget, "risk_tolerance": risk_tolerance, "total_cost": round(total_cost, 2), "unique_locations": unique_locations},
    }


def repair_route(route: Sequence[Mapping[str, Any]], *, max_nodes_per_day: int | None = None) -> List[Mapping[str, Any]]:
    """Apply only deterministic, auditable structural repairs.

    The function never fabricates a node: duplicate locations are dropped and
    daily overflow is trimmed while retaining a hotel node when present.
    Missing days or semantic violations remain visible to ``validate_route``.
    """
    output: List[Mapping[str, Any]] = []
    seen: set[str] = set()
    by_day: Dict[int, List[Mapping[str, Any]]] = defaultdict(list)
    for node in route if isinstance(route, Sequence) else []:
        if not isinstance(node, Mapping):
            continue
        name = _name(node)
        if name and name in seen:
            continue
        if name:
            seen.add(name)
        by_day[int(_number(node.get("day"), 1))].append(node)
    for day in sorted(by_day):
        nodes = by_day[day]
        if max_nodes_per_day is not None and len(nodes) > max_nodes_per_day:
            hotels = [node for node in nodes if "hotel" in _categories(node) or bool(node.get("is_hotel"))]
            kept = nodes[:max_nodes_per_day]
            if hotels and not any(node in hotels for node in kept):
                kept[-1] = hotels[0]
            nodes = kept
        output.extend(nodes)
    return output


def _dominates(left: Sequence[float], right: Sequence[float], maximize: Sequence[bool]) -> bool:
    better = False
    for lval, rval, is_max in zip(left, right, maximize):
        if is_max and lval < rval or not is_max and lval > rval:
            return False
        if lval != rval:
            better = True
    return better


def pareto_frontier(
    candidates: Sequence[Mapping[str, Any]],
    objectives: Mapping[str, Callable[[Mapping[str, Any]], float]],
    maximize: Mapping[str, bool] | None = None,
) -> Dict[str, Any]:
    """Compute a deterministic Pareto frontier and explain dominated rows."""
    names = list(objectives)
    directions = [bool((maximize or {}).get(name, True)) for name in names]
    rows = [item for item in candidates if isinstance(item, Mapping)]
    vectors = [[_number(objectives[name](item)) for name in names] for item in rows]
    frontier: List[Mapping[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        dominators = [other for other, vector in enumerate(vectors) if other != index and _dominates(vector, vectors[index], directions)]
        if dominators:
            rejected.append({"candidate": _name(row), "index": index, "reason": "dominated", "dominated_by": [_name(rows[item]) for item in dominators]})
        else:
            frontier.append(row)
    return {"frontier": frontier, "rejected": rejected, "objectives": names, "frontier_count": len(frontier), "rejected_count": len(rejected)}


def member_utilities(route: Sequence[Mapping[str, Any]], members: Sequence[Mapping[str, Any]]) -> Dict[str, float]:
    """Estimate transparent per-member utility from declared preferences."""
    nodes = [node for node in route if isinstance(node, Mapping)]
    if not members:
        return {"anonymous": 0.0}
    result: Dict[str, float] = {}
    all_tags = [str(tag).lower() for node in nodes for tag in (node.get("tags") or [])] if nodes else []
    total_cost = sum(_number(node.get("cost", node.get("cost_estimate"))) for node in nodes)
    average_risk = sum(_number(node.get("risk_score", node.get("riskScore")), 0) for node in nodes) / max(1, len(nodes))
    for index, member in enumerate(members):
        tags = [str(tag).lower() for tag in (member.get("interestTags") or member.get("interest_tags") or [])]
        interest = sum(1 for tag in tags if tag in all_tags) / max(1, len(tags)) if tags else 0.5
        budget_weight = max(0.0, _number(member.get("budgetWeight", member.get("budget_weight")), 1.0))
        pace_weight = max(0.0, _number(member.get("paceWeight", member.get("pace_weight")), 1.0))
        risk_weight = max(0.0, _number(member.get("riskWeight", member.get("risk_weight")), 1.0))
        budget_utility = 1.0 / (1.0 + total_cost / max(1.0, 180.0 * budget_weight))
        pace_utility = 1.0 / (1.0 + len(nodes) / max(1.0, 4.0 * pace_weight))
        risk_utility = max(0.0, 1.0 - average_risk / max(1.0, 100.0 * risk_weight))
        result[str(member.get("id") or member.get("name") or f"member-{index}")] = round(0.45 * interest + 0.25 * budget_utility + 0.15 * pace_utility + 0.15 * risk_utility, 6)
    return result


def fairness_report(route: Sequence[Mapping[str, Any]], members: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    utilities = member_utilities(route, members)
    values = list(utilities.values())
    mean = sum(values) / max(1, len(values))
    variance = sum((value - mean) ** 2 for value in values) / max(1, len(values))
    regret = max(values) - min(values) if values else 0.0
    return {"utilities": utilities, "minimum_utility": round(min(values), 6) if values else 0.0, "mean_utility": round(mean, 6), "utility_variance": round(variance, 6), "max_regret": round(regret, 6), "nash_welfare": round(sum(__import__("math").log(max(value, 1e-6)) for value in values), 6) if values else 0.0}


def select_fair_route(routes: Sequence[Sequence[Mapping[str, Any]]], members: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    reports = [fairness_report(route, members) for route in routes]
    if not reports:
        return {"index": -1, "report": fairness_report([], members), "reports": []}
    index = max(range(len(reports)), key=lambda item: (reports[item]["minimum_utility"], -reports[item]["utility_variance"], -reports[item]["max_regret"], reports[item]["mean_utility"]))
    return {"index": index, "report": reports[index], "reports": reports}
