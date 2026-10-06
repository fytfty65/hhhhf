"""探针：真实方案里的酒店名，供应商 MCP 到底认不认（决定"住宿价格"这条路走不走得通）。

用法：cd ai-service && .\\venv\\Scripts\\python.exe tools\\probe_hotel_names.py
"""

from __future__ import annotations

import asyncio
import os
import sys

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVICE_ROOT)

NAMES = [
    ("瑞德假日酒店(巴州博物馆市政府店)", "库尔勒"),
    ("库尔勒园林宾馆", "库尔勒"),
    ("尚客优酒店(喀什古城店)", "喀什"),
    ("喀什古城昆仑塔逸扉度假酒店", "喀什"),
    ("信泰达民宿", "伊宁"),
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
            if key.strip().startswith("ROLLINGGO_") and value.strip():
                os.environ.setdefault(key.strip(), value.strip())


async def main() -> int:
    load_env()
    from core import rollinggo_mcp

    config = rollinggo_mcp.mcp_config()
    print(f"供应商配置：enabled={config['enabled']}｜{config['reason']}")
    if not config["enabled"]:
        return 1
    print("-" * 70)
    hits = 0
    for name, city in NAMES:
        result = await rollinggo_mcp.hotel_price(name, city, check_in="2026-10-11", nights=1)
        status = str(result.get("status"))
        if status == "ok":
            hits += 1
        matched = str(result.get("matched_name") or "")
        price = result.get("price")
        error = str(result.get("error") or "")[:50]
        print(f"{status:12s}｜{name} → {matched or '—'}｜{price if price is not None else '—'}｜{error}")
    print("-" * 70)
    print(f"命中 {hits}/{len(NAMES)}：这就是「住宿价格能不能真的补上」的答案")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
