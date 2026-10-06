"""探针：为什么"日内里程超预算"修复没把远点移出去（2026-10-05 真实方案里反复出现）。

用法：cd ai-service && .\\venv\\Scripts\\python.exe tools\\probe_travel_repair.py ..\\work\\plans\\turbo12-喀什-17.json
"""

from __future__ import annotations

import glob
import io
import json
import os
import sys

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVICE_ROOT)

from core.travel_feasibility import evaluate_travel_feasibility, repair_day_overload  # noqa: E402


def center_of(plan: dict) -> list:
    facts = plan.get("facts") if isinstance(plan.get("facts"), dict) else {}
    for key in ("city_center", "center"):
        value = facts.get(key)
        if isinstance(value, dict) and isinstance(value.get("value"), (list, str)):
            value = value["value"]
        if isinstance(value, list) and len(value) == 2:
            return [float(value[0]), float(value[1])]
        if isinstance(value, str) and "," in value:
            left, _, right = value.partition(",")
            try:
                return [float(left), float(right)]
            except ValueError:
                pass
    nodes = plan.get("route") or []
    coords = [n.get("lnglat") for n in nodes if isinstance(n.get("lnglat"), list) and len(n.get("lnglat")) == 2]
    if coords:
        # 用第 1 天第一个有坐标的节点当近似市中心（只为复现，不写回方案）
        return [float(coords[0][0]), float(coords[0][1])]
    return []


def main() -> int:
    patterns = sys.argv[1:] or [os.path.join("..", "work", "plans", "*.json")]
    paths = []
    for pattern in patterns:
        paths.extend(sorted(glob.glob(pattern)))
    for path in paths:
        payload = json.load(io.open(path, encoding="utf-8"))
        plan = payload.get("final_data") if isinstance(payload.get("final_data"), dict) else payload
        route = plan.get("route") or []
        facts = plan.get("facts") if isinstance(plan.get("facts"), dict) else {}
        tolerance = "normal"
        try:
            tolerance = str(facts["constraints"]["distance_tolerance"]["value"])
        except Exception:
            pass
        center = center_of(plan)
        before = evaluate_travel_feasibility(route, city_center=center, distance_tolerance=tolerance, days=plan.get("trip_days") or 0)
        print("=" * 78)
        print(os.path.basename(path))
        print(f"  口径={tolerance}｜中心={center}")
        print(f"  现状 ok={before.get('ok')}｜violations={json.dumps(before.get('violations'), ensure_ascii=False)[:200]}")
        for day in before.get("days") or []:
            print(f"    第 {day.get('day')} 天：{day.get('total_km')}km（预算 {day.get('budget_km')}）远点 {day.get('far_nodes')}")
        kept, moved = repair_day_overload(route, city_center=center, distance_tolerance=tolerance)
        print(f"  修复会移出 {len(moved)} 个：{[m.get('name') for m in moved]}")
        if moved:
            after = evaluate_travel_feasibility(kept, city_center=center, distance_tolerance=tolerance, days=plan.get("trip_days") or 0)
            print(f"  修复后 ok={after.get('ok')}｜violations={json.dumps(after.get('violations'), ensure_ascii=False)[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
