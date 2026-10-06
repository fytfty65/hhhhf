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
        days = plan.get("trip_days") or 2
        report = evaluate_plan({"days": days}, plan, signals={})
        dims = ", ".join(f"{key}={value['value']}" for key, value in report["dimensions"].items())
        print("=" * 78)
        print(f"{os.path.basename(path)}｜天数 {days}｜节点 {report['nodes']}｜总分 {report['score']}｜{report['verdict']}")
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
