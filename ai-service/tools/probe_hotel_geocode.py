"""探针：高德到底有没有这些"供应商给了名字但没给坐标"的酒店（2026-10-05）。

背景：供应商的酒店候选有时只有名字和地址、没有坐标；节点被清成"缺坐标"之后
通勤距离算不出来。补坐标要按名字查高德 —— 先问清楚"高德到底有没有这家、用什么查法能查到"，
再决定要不要在代码里加重试（而不是凭感觉加一层）。

用法：cd ai-service && .\\venv\\Scripts\\python.exe tools\\probe_hotel_geocode.py
"""

from __future__ import annotations

import asyncio
import os
import sys

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVICE_ROOT)

CASES = [
    ("喀什", "喀什古城步行街亚朵酒店"),
    ("喀什", "喀什古城昆仑塔逸扉度假酒店"),
    ("伊宁", "伊宁上海城六星街丽呈酒店"),
    ("库尔勒", "瑞德假日酒店(巴州博物馆市政府店)"),
]


def load_env() -> None:
    path = os.path.join(SERVICE_ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, _, value = text.partition("=")
            if key.strip() in {"AMAP_API_KEY", "LLM_API_KEY"} and value.strip():
                os.environ.setdefault(key.strip(), value.strip())


async def main() -> int:
    load_env()
    from api.agent import ExpertToolbox

    toolbox = ExpertToolbox()
    print(f"高德 key：{'已配置' if getattr(toolbox, 'amap_key', None) else '未配置'}")
    print("-" * 78)
    for city, name in CASES:
        for label, kwargs in (("按名字+住宿类", {"keywords": name, "types": "100000"}), ("按名字（不限类别）", {"keywords": name})):
            try:
                pois = await toolbox.get_dynamic_pois(city, limit=3, **kwargs)
            except Exception as exc:
                print(f"{city}｜{name}｜{label}：查询异常 {type(exc).__name__}: {str(exc)[:60]}")
                continue
            hits = [
                (str(item.get("name") or ""), item.get("lnglat"))
                for item in (pois or [])
                if isinstance(item, dict)
            ]
            print(f"{city}｜{name}｜{label}：{len(hits)} 条 {hits[:3]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
