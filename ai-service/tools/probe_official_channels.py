"""探针：**定向通道**能不能拿到官方/票务数据（2026-10-07）。

背景：通用搜索对这些小众 POI 基本没收录（实测「图开沙漠冰雪乐园」返回"百度图片"、
「伊宁 图开沙漠冰雪乐园 门票 价格」返回伊宁政务网首页）。既然通用搜索不行，
就换成**定向**：用 `site:` 限定到官方文旅站 / 票务平台，或直接打这些站点的站内检索。

这个探针回答：
  ① `site:gov.cn <景点> 门票` 这类限定查询，能不能搜出**关于这个景点**的官方页？
  ② 已知的官方文旅站（伊犁文旅 `ylwl.xjyl.gov.cn`、喀什政府 `xjks.gov.cn`）里，
     有没有**景区名录/详情页**可以直接定位（例如 `show/scenicspot/detail?id=581`）？
  ③ 票务平台（携程/去哪儿/同程）用 `site:` 限定后能不能拿到票价？

用法：cd ai-service && venv\\Scripts\\python.exe tools\\probe_official_channels.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT / ".env")

# (城市, 景点, 已知的官方/白名单线索)
TARGETS = [
    ("伊宁", "天鹅泉湿地公园", "已知官方页 ylwl.xjyl.gov.cn/show/scenicspot/detail?id=581（¥30）"),
    ("喀什", "英吉沙国家湿地公园", "通用搜索拿不到"),
    ("伊宁", "图开沙漠冰雪乐园", "通用搜索返回百度图片"),
    ("伊宁", "阿吾赞沟森林公园", "通用搜索拿不到"),
    ("喀什", "胡杨书院", "通用搜索拿不到"),
    ("喀什", "疏勒历史博物馆", "通用搜索拿不到"),
]

QUERY_PATTERNS = [
    ("gov_site", lambda name: f'"{name}" 门票 site:gov.cn'),
    ("gov_site_city", lambda name, city: f"{city} {name} 门票 site:gov.cn"),
    ("gov_any", lambda name, city: f"{name} 门票 价格 官网 site:gov.cn"),
    ("ctrip", lambda name, city: f"{name} 门票 site:ctrip.com"),
    ("qunar", lambda name, city: f"{name} 门票 site:qunar.com"),
]


async def probe_query(label: str, query: str) -> dict:
    from core import web_price_tool as wpt

    try:
        rows = await asyncio.wait_for(
            wpt.search_texts(query, max_results=6, merge=True, max_providers=2), timeout=25.0
        )
    except Exception as exc:
        return {"label": label, "query": query, "error": f"{type(exc).__name__}: {str(exc)[:60]}"}
    gov = [row for row in rows if ".gov.cn" in str(row.get("url") or "")]
    platforms = [row for row in rows if wpt.host_is_ticket_platform(str(row.get("url") or ""))]
    return {
        "label": label,
        "query": query,
        "rows": rows,
        "count": len(rows),
        "gov": len(gov),
        "platform": len(platforms),
        "urls": [str(row.get("url"))[:80] for row in rows[:3]],
    }


async def main() -> int:
    from core import web_price_tool as wpt

    print("=== ① 定向查询（site: 限定）")
    for city, name, note in TARGETS:
        print(f"\n▶ {city} {name}｜{note}")
        for label, build in QUERY_PATTERNS:
            try:
                query = build(name) if label == "gov_site" else build(name, city)
            except TypeError:
                query = build(name, city)
            item = await probe_query(label, query)
            if item.get("error"):
                print(f"    {label:12s} {query[:52]:54s} 失败 {item['error']}")
                continue
            priced = 0
            for row in item["rows"]:
                if wpt.extract_ticket_price(str(row.get("body") or ""), require_name=name):
                    priced += 1
            print(
                f"    {label:12s} {query[:52]:54s} 条 {item['count']}｜gov {item['gov']}｜"
                f"平台 {item['platform']}｜抽到票价 {priced}"
            )
            for url in item["urls"][:2]:
                print(f"        · {url}")
        sys.stdout.flush()

    print("\n=== ② 官方文旅站有没有景区名录/站内检索")
    import httpx

    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    candidates = [
        "http://ylwl.xjyl.gov.cn/",
        "http://ylwl.xjyl.gov.cn/show/scenicspot",
        "http://www.xjks.gov.cn/",
        "https://www.xjks.gov.cn/",
    ]
    async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
        for url in candidates:
            try:
                resp = await client.get(url)
                html = resp.text or ""
                links = re.findall(r'href="([^"]*(?:scenicspot|jingqu|spot|search)[^"]*)"', html, flags=re.I)
                print(f"  {url:48s} {resp.status_code}｜{len(html)} 字符｜相关链接 {len(set(links))} 个")
                for link in list(dict.fromkeys(links))[:5]:
                    print(f"      · {link[:90]}")
            except Exception as exc:
                print(f"  {url:48s} 失败 {type(exc).__name__}: {str(exc)[:50]}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
