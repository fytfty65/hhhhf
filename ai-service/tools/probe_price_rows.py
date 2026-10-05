"""取价查询与抽取的**原始对照**探针（2026-10-05）。

为什么要它：方案里常常"查了 6 个、补到 0 条"，而日志只告诉你"没抽到价格"。
要判断是"搜索没给到有用结果"还是"给了但抽取漏了"，必须把**原始行**打出来：
提供方、标题、摘要、链接。这样"改查询词"还是"改抽取规则"才有依据，而不是猜。

用法：
    cd ai-service
    .\\venv\\Scripts\\python.exe tools\\probe_price_rows.py 库尔勒 "博斯腾湖风景名胜区" "金润假日酒店"
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from core.web_price_tool import (  # noqa: E402
    enabled_providers,
    extract_price_candidates,
    last_search_attempts,
    pick_price_from_rows,
    search_texts,
)

# 不同机构/类别的查询词模板：门票、房价、人均 是三类完全不同的问法。
QUERY_TEMPLATES = [
    "{city} {name} 门票 价格",
    "{city} {name} 门票多少钱",
    "{name} 门票价格 官网",
    "{city} {name} 房价 每晚",
    "{city} {name} 人均 多少钱",
]


async def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    city = sys.argv[1]
    names = sys.argv[2:]
    print(f"提供方顺序: {enabled_providers()}\n")
    for name in names:
        print("=" * 78)
        print(f"地点：{name}")
        print("=" * 78)
        for template in QUERY_TEMPLATES:
            query = template.format(city=city, name=name)
            try:
                rows = await search_texts(query, max_results=3)
            except Exception as exc:
                print(f"  · {query} → 异常 {type(exc).__name__}: {exc}")
                continue
            best = pick_price_from_rows(rows)
            mark = f"✅ 抽到 ¥{best['value']:g}" if best else "⬜ 没抽到"
            print(f"  · {query} → {len(rows)} 行｜{mark}")
            for row in rows:
                title = str(row.get("title") or "")[:60]
                snippet = str(row.get("snippet") or "")[:90]
                direct = extract_price_candidates(f"{title} {snippet}")
                print(f"      - [{row.get('provider')}] {title}")
                print(f"        摘要: {snippet}")
                print(f"        直接抽价: {[c['value'] for c in direct]}")
            if best:
                print(f"        选中出处: {best.get('publisher') or best.get('provider')}｜{str(best.get('url'))[:70]}")
                print(f"        原文: {str(best.get('excerpt'))[:90]}")
        if last_search_attempts:
            print(f"  最后一次搜索尝试: {last_search_attempts}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
