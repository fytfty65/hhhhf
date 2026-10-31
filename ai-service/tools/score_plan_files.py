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

from core.console import ensure_utf8_console  # noqa: E402

ensure_utf8_console()

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


def scoring_basis(plan: Dict[str, Any]) -> Any:
    """尺子该拿哪份输入去评：**优先方案自己记的评分输入**，记不到才从 facts 重建。

    为什么必须优先（2026-10-05 实测抓到"两把尺子"）：方案里的分数是拿 `pref_signals`
    （历史 + 房间成员的话都算）算出来的，而 `facts` 只记**发起人自己这一轮**说的话
    （这是配比口径的要求，不是 bug）。于是从 facts 重建信号时看不到那部分偏好：
    同一份喀什方案，管线里 `preference_coverage=0.75` → 总分 78.9，尺子重建出 1.0 → 83.6。
    尺子比管线**松 4.7 分**，正好把用户最在意的"个性化有没有满足"漏掉。

    返回 `(context, signals, source)`，`source` 会打印出来 —— 尺子也要如实交代自己的口径。
    """
    recorded = (plan.get("quality") or {}).get("scoring_inputs") if isinstance(plan.get("quality"), dict) else None
    if isinstance(recorded, dict) and isinstance(recorded.get("context"), dict) and recorded["context"]:
        signals = recorded.get("signals")
        return dict(recorded["context"]), (dict(signals) if isinstance(signals, dict) else {}), "方案记录"
    return context_from_plan(plan), signals_from_plan(plan), "facts 重建"


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
        context, signals, basis = scoring_basis(plan)
        days = context.get("days") or 2
        report = evaluate_plan(context, plan, signals=signals)
        dims = ", ".join(f"{key}={value['value']}" for key, value in report["dimensions"].items())
        print("=" * 78)
        print(
            f"{os.path.basename(path)}｜天数 {days}｜距离口径 {context.get('distance_tolerance')}"
            f"｜节点 {report['nodes']}｜总分 {report['score']}｜{report['verdict']}｜口径来源 {basis}"
        )
        payload_score = ((plan.get("quality") or {}).get("score")) if isinstance(plan.get("quality"), dict) else None
        if isinstance(payload_score, (int, float)) and abs(float(payload_score) - float(report["score"])) > 0.05:
            # 尺子与方案自报的分数不一致 → 必须说出来，别让两个数字并存
            print(f"  ⚠️ 尺子 {report['score']} ≠ 方案自报 {payload_score}（口径没对齐，分数不可比）")
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
