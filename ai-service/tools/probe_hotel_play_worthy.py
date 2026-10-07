"""探针：确认"按名字查酒店返回 0 条"的真正原因是 play_worthy 过滤（2026-10-05）。

背景：坐标补齐写的是 `get_dynamic_pois(city, keywords=酒店名, types="100000")`，
但 `is_play_worthy` 默认会把**住宿服务**整类丢掉（它不属于"玩点类别"）——
于是"按名字补坐标"永远补不到（真跑时表现为"补到 0 个"）。
住宿/餐饮检索必须显式 `play_worthy=False`（代码里其它地方就是这么做的）。

用法：cd ai-service && .\\venv\\Scripts\\python.exe tools\\probe_hotel_play_worthy.py
"""

from __future__ import annotations

import asyncio
import os
import sys

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVICE_ROOT)
sys.path.insert(0, os.path.join(SERVICE_ROOT, "tools"))

from probe_hotel_geocode import load_env  # noqa: E402

CASES = [
    ("库尔勒", "酒店"),
    ("库尔勒", "瑞德假日酒店(巴州博物馆市政府店)"),
    ("库尔勒", "瑞德假日"),
    ("喀什", "喀什古城步行街亚朵酒店"),
]


async def main() -> int:
    load_env()
    from api.agent import ExpertToolbox

    toolbox = ExpertToolbox()
    for city, keyword in CASES:
        for play_worthy in (True, False):
            try:
                pois = await toolbox.get_dynamic_pois(city, limit=3, keywords=keyword, play_worthy=play_worthy)
            except Exception as exc:
                print(f"{city}｜{keyword!r}｜play_worthy={play_worthy}：异常 {type(exc).__name__}: {str(exc)[:60]}")
                continue
            hits = [(str(item.get("name") or ""), item.get("lnglat")) for item in (pois or [])][:2]
            print(f"{city}｜{keyword!r}｜play_worthy={play_worthy} → {len(pois or [])} 条 {hits}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
