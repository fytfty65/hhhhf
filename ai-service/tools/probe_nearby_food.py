"""探针：按天做**周边检索**拿吃住候选（高德 place/around）——远点那天的"吃"能不能就地解决。

背景：喀什第 3 天在英吉沙/巴楚方向，候选池是全城取的，餐饮候选最近也在 82.6km 外；
伊宁吃住离当天玩点 17~38km。这里直接对"当天玩点聚簇"做一次周边检索，看能不能拿到近的。
用法：cd ai-service && python tools/probe_nearby_food.py [--plan work/plans/ks9-喀什-17.json]
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import io
import json
import os
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT / ".env")

from core.plan_quality import haversine_km, node_day, node_lnglat  # noqa: E402


def _day_gaps(plan, days):
    """返回 {day: (center, 有没有饭点)}：只算非吃住的玩点聚簇。"""
    out = {}
    route = [n for n in (plan.get("route") or []) if isinstance(n, dict)]
    for day in range(1, days + 1):
        nodes = [n for n in route if node_day(n, 1) == day]
        plays = [
            n
            for n in nodes
            if not any(w in f"{n.get('name')} {n.get('type')}" for w in ("餐", "美食", "酒店", "民宿", "客栈", "宾馆"))
        ]
        meals = [n for n in nodes if any(w in f"{n.get('name')} {n.get('type')}" for w in ("餐", "美食", "米粉", "拌面", "馕"))]
        coords = [node_lnglat(n) for n in plays if node_lnglat(n)]
        center = (
            [sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords)] if coords else []
        )
        out[day] = (center, bool(meals))
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description="周边餐饮检索探针")
    parser.add_argument("--plan", default="")
    parser.add_argument("--radius", type=float, default=15.0)
    args = parser.parse_args()
    path = args.plan or sorted(glob.glob(str(REPO_ROOT / "work" / "plans" / "ks9-*.json")))[0]
    plan = json.load(io.open(path, encoding="utf-8"))
    days = int((((plan.get("facts") or {}).get("request") or {}).get("trip_days") or {}).get("value") or 3)
    print(f"方案 {Path(path).name}｜{days} 天")

    from api.agent import ExpertToolbox

    toolbox = ExpertToolbox()
    for day, (center, has_meal) in _day_gaps(plan, days).items():
        if not center:
            continue
        rows = await toolbox.get_nearby_pois(center, "餐厅|美食|小吃", radius_km=args.radius, limit=8)
        print(f"\n第 {day} 天｜聚簇 {center[0]:.3f},{center[1]:.3f}｜当天有饭点={has_meal}｜周边检索 {len(rows)} 条")
        for row in rows[:5]:
            km = haversine_km(center, node_lnglat(row) or [])
            print(f"    {str(row.get('name'))[:22]:24s} {km:6.1f}km  {str(row.get('address'))[:26]}")
    print("\ndone", flush=True)
    return 0


if __name__ == "__main__":
    code = asyncio.run(main())
    os._exit(code)
