"""把方案按天铺开、算相邻两点直线距离（人工判断"一天是不是在来回折腾"用）。

用法：cd ai-service && .\\venv\\Scripts\\python.exe tools\\dump_plan_legs.py ..\\work\\plans\\turbo-*.json

为什么要有它：`space_efficiency` 只看"日均里程 / 长途跳跃次数"这类聚合数字，
而"这个 0.0 到底是路线真的在往返折腾，还是尺子太严"必须肉眼看一下逐段里程才知道。
"""

from __future__ import annotations

import glob
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.travel_feasibility import haversine_km, node_lnglat  # noqa: E402


def main() -> int:
    patterns = sys.argv[1:] or [os.path.join("work", "plans", "*.json")]
    paths = []
    for pattern in patterns:
        paths.extend(sorted(glob.glob(pattern)))
    if not paths:
        print("没找到方案 JSON")
        return 1
    for path in paths:
        payload = json.load(io.open(path, encoding="utf-8"))
        plan = payload.get("final_data") if isinstance(payload.get("final_data"), dict) else payload
        print("=" * 78)
        print(os.path.basename(path))
        day = None
        previous = None
        total = 0.0
        for node in plan.get("route") or []:
            current_day = node.get("day")
            if current_day != day:
                day = current_day
                previous = None
                print(f"  第 {day} 天")
            position = node_lnglat(node)
            leg = ""
            if previous is not None and position:
                km = haversine_km(previous, position)
                total += km
                marker = "  ⚠️ 长途" if km > 40 else ""
                leg = f"   ← {km:.0f}km{marker}"
            print(f"    {node.get('name')}{leg}")
            previous = position
        print(f"  合计直线移动 {total:.1f}km")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
