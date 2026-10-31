"""探针：去哪儿 `list.json` 与携程详情页的**票价字段**（2026-10-07）。

上一轮打通了两条真实接口：
  · 携程建议词：`m.ctrip.com/restapi/h5api/globalsearch/search?action=gsonline&...&keyword=` →
    返回景区 id + 详情 URL（`you.ctrip.com/sight/<拼音><城市id>/<sightId>.html`）；
  · 去哪儿门票列表：`piao.qunar.com/ticket/list.json?keyword=` → 20KB JSON（含 1898 条）。

这一轮把它们的**字段结构**看清楚：有没有景区名、有没有票价、票价怎么标（起价/票面价）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT / ".env")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
QUERIES = ["博斯腾湖", "喀什古城", "阿吾赞沟森林公园", "天鹅泉湿地公园"]
CTRIP_SUGGEST = "https://m.ctrip.com/restapi/h5api/globalsearch/search"
QUNAR_LIST = "https://piao.qunar.com/ticket/list.json"


async def main() -> int:
    import httpx

    headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "zh-CN,zh;q=0.9"}
    async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
        for query in QUERIES:
            print(f"\n=== {query}")
            # ① 携程建议词 → 详情 URL
            try:
                resp = await client.get(
                    CTRIP_SUGGEST,
                    params={"action": "gsonline", "source": "globalonline", "keyword": query},
                )
                payload = resp.json()
            except Exception as exc:
                print(f"  携程建议词失败 {type(exc).__name__}: {str(exc)[:50]}")
                payload = {}
            sights = [
                item
                for item in (payload.get("data") or [])
                if isinstance(item, dict) and str(item.get("code") or "").startswith("sight")
            ]
            print(f"  携程建议词：{len(payload.get('data') or [])} 条，其中景区 {len(sights)} 条")
            for item in sights[:3]:
                print(f"    · {item.get('word')}｜id={item.get('id')}｜{str(item.get('url'))[:70]}")
            # ② 去哪儿门票列表 JSON
            try:
                resp = await client.get(QUNAR_LIST, params={"keyword": query, "page": "1"})
                data = resp.json()
            except Exception as exc:
                print(f"  去哪儿失败 {type(exc).__name__}: {str(exc)[:50]}")
                data = {}
            payload = data.get("data") or {}
            items = payload.get("list") or payload.get("sightList") or []
            print(f"  去哪儿：ret={data.get('ret')} totalCount={payload.get('totalCount')} 列表 {len(items)} 条")
            if items:
                first = items[0]
                print("    条目字段:", sorted(first.keys())[:24])
                for key in ("sightName", "name", "qunarPrice", "price", "marketPrice", "salePrice", "fromPrice", "detailUrl", "sightId"):
                    if key in first:
                        print(f"      {key} = {str(first.get(key))[:60]}")
                # 打印前 3 条的 名称 + 任何含 price 的字段
                for item in items[:3]:
                    prices = {key: value for key, value in item.items() if "price" in str(key).lower()}
                    print(f"    · {str(item.get('sightName') or item.get('name'))[:24]:26s} {prices}")
            sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
