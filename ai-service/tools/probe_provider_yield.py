"""探针：**生产路径**上每个搜索源的产出与耗时（2026-10-07）。

为什么要它：取价/简介的产出一直"0~4/12"地波动，而我们只看到"补到 0 条"这种结果，
看不到**每一家源各自成没成、花了多久、给了几条**。这个探针跑的就是生产用的
`core.web_price_tool.search_texts(..., merge=True)`，把 `last_search_attempts`
（每家的 ok/error/条数）与耗时逐条打出来，最后给一张按源的汇总表。

它回答的是这类问题（用于决定默认源顺序 / 要不要继续带着 baidu、sogou）：
  · 哪家在**真实 query** 上稳定出结果？哪家是"每次都失败、只吃时间"的死源？
  · 取价用 merge 问 2 家 vs 3 家，产出差多少、时间差多少？
  · 拿到的东西里有多少是 .gov.cn / 票务平台（能算"可核实"或"有出处"的）？

用法：cd ai-service && venv\\Scripts\\python.exe tools\\probe_provider_yield.py [--providers 3]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT / ".env")

QUERIES = [
    "伊宁 天鹅泉湿地公园 门票 价格",
    "喀什 英吉沙国家湿地公园 门票 价格",
    "伊宁 伊犁河旅游风景区 门票 价格",
    "喀什 胡杨书院 门票 价格",
    "伊宁 阿吾赞沟森林公园 门票 价格",
    "喀什 喀什博物馆 门票 价格",
    "伊宁 图开沙漠冰雪乐园 门票 价格",
    "喀什 疏勒历史博物馆 门票 价格",
]


async def main() -> int:
    parser = argparse.ArgumentParser(description="生产路径搜索源产出探针")
    parser.add_argument("--providers", type=int, default=3, help="merge 时最多问几家")
    parser.add_argument("--json", default="", help="把结果写到这个文件")
    parser.add_argument("--limit", type=int, default=len(QUERIES))
    args = parser.parse_args()

    from core import web_price_tool as wpt

    print(f"启用的源: {wpt.enabled_providers()}｜merge max_providers={args.providers}")
    stats: dict = defaultdict(lambda: {"attempts": 0, "ok": 0, "rows": 0, "seconds": 0.0, "errors": []})
    results = []
    for query in QUERIES[: args.limit]:
        started = time.monotonic()
        rows = await wpt.search_texts(query, max_results=6, merge=True, max_providers=args.providers)
        elapsed = time.monotonic() - started
        attempts = list(wpt.last_search_attempts or [])
        official = [row for row in rows if ".gov.cn" in str(row.get("url") or "")]
        platform = [row for row in rows if wpt.host_is_ticket_platform(str(row.get("url") or ""))]
        prices = [
            {"name": row.get("name"), "value": (wpt.extract_ticket_price(str(row.get("body") or "")) or {}).get("value")}
            for row in rows
        ]
        priced = [item for item in prices if item["value"]]
        print(f"\n▶ {query}")
        print(f"  合计 {len(rows)} 条｜gov {len(official)}｜票务平台 {len(platform)}｜能抽出票价 {len(priced)}｜{elapsed:.1f}s")
        for item in attempts:
            provider = str(item.get("provider"))
            stats[provider]["attempts"] += 1
            if item.get("ok"):
                stats[provider]["ok"] += 1
                stats[provider]["rows"] += int(item.get("results") or 0)
            else:
                stats[provider]["errors"].append(str(item.get("error") or "")[:60])
            print(
                f"    · {provider:12s} ok={item.get('ok')} results={item.get('results')} "
                f"{('err=' + str(item.get('error'))[:50]) if not item.get('ok') else ''}"
            )
        results.append(
            {
                "query": query,
                "rows": len(rows),
                "gov": len(official),
                "platform": len(platform),
                "with_price": len(priced),
                "seconds": round(elapsed, 1),
                "attempts": attempts,
            }
        )

    print("\n=== 按源汇总 ===")
    for provider, item in sorted(stats.items(), key=lambda kv: -kv[1]["ok"]):
        errors = [err for err in item["errors"] if err]
        print(
            f"  {provider:12s} 问 {item['attempts']} 次，成功 {item['ok']} 次，共 {item['rows']} 条"
            + (f"，失败样例：{errors[0]}" if errors else "")
        )
    print(f"\n总计：{sum(item['rows'] for item in results)} 条，"
          f"gov {sum(item['gov'] for item in results)} 条，"
          f"票务平台 {sum(item['platform'] for item in results)} 条，"
          f"能抽出票价 {sum(item['with_price'] for item in results)} 个")
    if args.json:
        io.open(args.json, "w", encoding="utf-8").write(json.dumps(results, ensure_ascii=False, indent=2))
        print("明细已写入", args.json)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
