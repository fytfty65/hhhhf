"""探针：**查询词怎么写才搜得到票价**（2026-10-07）。

背景：按源测量之后 bing 8/8、每条 query 都带 1 条 .gov.cn，但方案层面仍然"补 0" ——
因为那些 gov 结果是**市政府首页**，不是景点页。问题在**查询词**：`喀什 英吉沙国家湿地公园 门票 价格`
这种"城市 + 全名 + 门票 价格"的写法，Bing 会优先给城市泛页。

这个探针把几种写法放在同一批景点上对照，看哪种能搜到**关于这个景点**的页面、
并且能抽出票价（用的是生产的 `extract_ticket_price(..., require_name=...)`）。

用法：cd ai-service && venv\\Scripts\\python.exe tools\\probe_query_wording.py
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

# (城市, 景点) —— 取自最近两份真实方案里"没查到票价"的玩点
TARGETS = [
    ("喀什", "英吉沙国家湿地公园"),
    ("喀什", "胡杨书院"),
    ("伊宁", "阿吾赞沟森林公园"),
    ("伊宁", "图开沙漠冰雪乐园"),
    ("喀什", "疏勒历史博物馆"),
    ("伊宁", "伊犁河旅游风景区"),
]

VARIANTS = [
    ("city_full_price", lambda city, name: f"{city} {name} 门票 价格"),
    ("quoted_name", lambda city, name: f'"{name}" 门票价格'),
    ("how_much", lambda city, name: f"{name} 门票多少钱 开放时间"),
    ("official", lambda city, name: f"{name} 门票 官网 景区"),
]


def _on_topic(rows, name: str) -> int:
    core = name.replace("国家", "").replace("风景区", "").replace("森林公园", "")[:4]
    hits = 0
    for row in rows:
        text = f"{row.get('title')} {row.get('body')}"
        if core and core in text:
            hits += 1
    return hits


async def main() -> int:
    from core import web_price_tool as wpt

    print("按写法对照（每格：条数 / 提到该景点的条数 / 抽出票价的条数）")
    print(f"{'景点':24s}" + "".join(f"{key:>22s}" for key, _ in VARIANTS))
    totals = {key: {"rows": 0, "on_topic": 0, "priced": 0} for key, _ in VARIANTS}
    for city, name in TARGETS:
        cells = []
        for key, build in VARIANTS:
            query = build(city, name)
            try:
                rows = await asyncio.wait_for(
                    wpt.search_texts(query, max_results=6, merge=True, max_providers=2), timeout=25.0
                )
            except Exception as exc:
                cells.append(f"{type(exc).__name__}")
                continue
            priced = 0
            for row in rows:
                body = str(row.get("body") or "")
                ticket = wpt.extract_ticket_price(body, require_name=name)
                if ticket:
                    priced += 1
            on_topic = _on_topic(rows, name)
            totals[key]["rows"] += len(rows)
            totals[key]["on_topic"] += on_topic
            totals[key]["priced"] += priced
            cells.append(f"{len(rows)}/{on_topic}/{priced}")
        print(f"{name:24s}" + "".join(f"{cell:>22s}" for cell in cells))
        sys.stdout.flush()
    print("\n=== 汇总 ===")
    for key, _ in VARIANTS:
        item = totals[key]
        print(f"  {key:16s} 合计 {item['rows']:3d} 条，提到景点 {item['on_topic']:2d} 条，能抽票价 {item['priced']:2d} 条")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
