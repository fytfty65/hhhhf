r"""用**真实方案 JSON** 跑一遍新维度，看看分数与"待人工确认"清单长什么样（2026-10-05）。

用法：cd ai-service && .\venv\Scripts\python.exe tools\score_plan_files.py ..\work\plans\*.json
"""

from __future__ import annotations

import glob
import io
import json
import os
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_quality import evaluate_plan  # noqa: E402


def load(path: str) -> Dict[str, Any]:
    payload = json.load(io.open(path, encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("final_data"), dict):
        return payload["final_data"]
    return payload if isinstance(payload, dict) else {}


def _fact_value(facts: Dict[str, Any], *path: str) -> Any:
    """读 `facts.<…>.value`（方案自己记录的事实），读不到就返回 None。

    ⚠️ 为什么不写死默认值：这两个口径**方案自己记着**，用错的默认值去评它是"用错尺子"。
    实测（2026-10-05）：3 天的喀什方案被 `days=2` 去评（缺省值），"日内距离/天数"怎么算都不对。
    """
    node: Any = facts
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, dict):
        return node.get("value")
    return None


def context_from_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """按方案**自己记录的口径**构造评分上下文（天数 / 距离容忍度 / 预算）。"""
    facts = plan.get("facts") if isinstance(plan.get("facts"), dict) else {}
    days = _fact_value(facts, "request", "trip_days") or plan.get("trip_days") or 2
    tolerance = _fact_value(facts, "constraints", "distance_tolerance") or "normal"
    budget = _fact_value(facts, "request", "budget_total")
    context: Dict[str, Any] = {"days": days, "distance_tolerance": str(tolerance)}
    if isinstance(budget, (int, float)):
        context["budget"] = budget
    return context


def signals_from_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """从方案记录的事实还原偏好信号（用户明说的诉求），供评分维度判"有没有满足个性化"。"""
    facts = plan.get("facts") if isinstance(plan.get("facts"), dict) else {}
    constraints = facts.get("constraints") if isinstance(facts.get("constraints"), dict) else {}
    signals: Dict[str, Any] = {}
    for key in ("wants_scenic", "wants_food", "wants_cultural", "wants_shopping", "wants_hotel", "exclude_cultural"):
        value = _fact_value(facts, "constraints", key)
        if isinstance(value, bool):
            signals[key] = value
    tolerance = _fact_value(facts, "constraints", "distance_tolerance")
    if tolerance:
        signals["distance_tolerance"] = str(tolerance)
    requirements = _fact_value(facts, "request", "requirements")
    if isinstance(requirements, list) and requirements:
        signals["requirements"] = requirements
    # `constraints` 里可能还有本函数没枚举到的键，尽量带上（不强求）
    for key, item in constraints.items():
        if key not in signals and isinstance(item, dict) and isinstance(item.get("value"), bool):
            signals[key] = item["value"]
    return signals


def main() -> int:
    patterns = sys.argv[1:] or [os.path.join("work", "plans", "*.json")]
    paths = []
    for pattern in patterns:
        paths.extend(sorted(glob.glob(pattern)))
    if not paths:
        print("没找到方案 JSON（先跑 scripts/probe-plan-matrix.mjs 落盘）")
        return 1
    for path in paths:
        plan = load(path)
        context = context_from_plan(plan)
        signals = signals_from_plan(plan)
        days = context.get("days") or 2
        report = evaluate_plan(context, plan, signals=signals)
        dims = ", ".join(f"{key}={value['value']}" for key, value in report["dimensions"].items())
        print("=" * 78)
        print(f"{os.path.basename(path)}｜天数 {days}｜距离口径 {context.get('distance_tolerance')}｜节点 {report['nodes']}｜总分 {report['score']}｜{report['verdict']}")
        print(f"  维度：{dims}")
        place = report["dimensions"]["place_quality"]
        print(
            f"  地点质量：真实检索 {place.get('real_retrieval_ratio', 0):.0%}｜像玩点 {place.get('visitable_ratio', 0):.0%}"
            f"｜可疑 {len(place.get('suspect_names') or [])}｜待人工确认 {len(place.get('review_required') or [])}"
        )
        for item in (place.get("suspect_names") or [])[:5]:
            print(f"    ⚠️ {item['name']} —— {item['why']}")
        for item in (place.get("review_required") or [])[:5]:
            print(f"    👀 {item['name']} —— {item['why']}")
        complete = report["dimensions"]["completeness"]
        print(f"  周全：{complete['value']}｜未过项 {complete.get('failed_checks') or '无'}｜判不了 {complete.get('unknown_checks') or '无'}")
        for key, item in (complete.get("checks") or {}).items():
            mark = {True: "✓", False: "✗", None: "—"}[item.get("ok")]
            print(f"    {mark} {key}：{item.get('detail')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
