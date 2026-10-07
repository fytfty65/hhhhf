"""探针：拿到 `sightId` 后，**哪个接口能给票价**（2026-10-07）。

上一轮：城市级日录能把方案节点对上（伊宁 7/7、库尔勒 4/4），但列表条目里**没有** price 块
（只有直接按景点名查时才有）。所以要顺 `sightId` 去取详情。候选：
  · `piao.qunar.com/ticket/detail_<id>.html`（HTML，可能是 JS 壳）
  · `piao.qunar.com/ticket/detail.json?sightId=`
  · `piao.qunar.com/ticket/detailAjax.json?...`
  · `piao.qunar.com/ticket/detail/price.json?...`
判据：返回体里有没有 `qunarPrice`/`priceList`/`门票`。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT / ".env")

# (景点名, sightId) —— sightId 取自上一轮城市日录的命中结果
SIGHTS = [
    ("库尔勒体育公园", 1735077393),
    ("博斯腾湖", 95106489),
    ("天鹅泉湿地公园", 180076650),
]
CANDIDATES = [
    ("html", "https://piao.qunar.com/ticket/detail_{sid}.html", {}),
    ("detail.json", "https://piao.qunar.com/ticket/detail.json", {"sightId": "{sid}"}),
    ("detailAjax", "https://piao.qunar.com/ticket/detailAjax.json", {"sightId": "{sid}"}),
    ("price.json", "https://piao.qunar.com/ticket/detail/price.json", {"sightId": "{sid}"}),
    ("sight.json", "https://piao.qunar.com/ticket/sight.json", {"sightId": "{sid}"}),
]


async def main() -> int:
    import httpx

    from core.ticket_platforms import platform_headers

    async with httpx.AsyncClient(timeout=15.0, headers=platform_headers(), follow_redirects=True) as client:
        for name, sid in SIGHTS:
            print(f"\n=== {name}（sightId={sid}）")
            for label, template, params in CANDIDATES:
                url = template.format(sid=sid)
                query = {key: str(value).format(sid=sid) for key, value in params.items()}
                try:
                    resp = await client.get(url, params=query or None)
                except Exception as exc:
                    print(f"  {label:12s} 失败 {type(exc).__name__}: {str(exc)[:40]}")
                    continue
                text = (resp.text or "").strip()
                markers = {
                    "qunarPrice": text.count("qunarPrice"),
                    "priceList": text.count("priceList"),
                    "门票": text.count("门票"),
                    "sightName": text.count("sightName"),
                }
                print(f"  {label:12s} {resp.status_code}｜{len(text):7d} 字符｜{markers}")
                if markers["qunarPrice"] or markers["priceList"]:
                    print("     片段:", text[:180].replace("\n", " "))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
