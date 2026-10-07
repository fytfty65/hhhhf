"""探针：城市日录当**名字解析器**，再用"平台自己的标准名"回查票价（2026-10-07）。

已知：
  · 城市级 keyword（"伊宁 景点"）能列出 15~30 个景区，宽容匹配能对上方案节点（伊宁 7/7）；
  · 但这些条目的 `price` 块是空的，而**按景点名直接查**时（"博斯腾湖"/"喀什古城"）是有的；
  · 详情接口给不了价（`detail_<id>.html` 对三个不同 id 返回**同一份** 33KB 壳页）。

所以怀疑价只在"查询词足够像平台标准名"时才有。验证：
  用日录里那个**标准名**（sightName）回查 `list.json`，看这回有没有 `priceList`。
若是，就能做成两步：城市日录 → 标准名 → 回查票价。
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

TARGETS = {
    "伊宁": ["图开沙漠冰雪乐园", "阿吾赞沟森林公园", "伊犁河旅游风景区", "草原遗迹", "沙漠迷宫", "天鹅泉湿地公园"],
    "喀什": ["喀什博物馆", "百年喀什馆"],
    "库尔勒": ["库尔勒体育公园", "铁门关景区", "龙山公园"],
}


async def list_by_keyword(keyword: str):
    from core.ticket_platforms import QUNAR_TICKET_API, _get_json

    payload = await _get_json(QUNAR_TICKET_API, {"keyword": keyword, "page": "1"})
    data = payload.get("data") or {}
    return data.get("list") or data.get("sightList") or []


def has_price(entry) -> bool:
    block = (entry or {}).get("price")
    if not isinstance(block, dict):
        return False
    sight = block.get("sightPrice")
    return bool(isinstance(sight, dict) and (sight.get("priceList")))


async def main() -> int:
    from core.official_directory import _normalize
    from core.ticket_platforms import adult_ticket_price

    for city, names in TARGETS.items():
        print(f"\n=== {city}")
        directory = {}
        for keyword in (f"{city} 景点", f"{city} 门票", city):
            for row in await list_by_keyword(keyword):
                name = str(row.get("sightName") or "").strip()
                if name and name not in directory:
                    directory[name] = row
        for name in names:
            core = _normalize(name)
            matched = None
            for entry_name, entry in directory.items():
                entry_core = _normalize(entry_name)
                if core and entry_core and (core == entry_core or core in entry_core or entry_core in core):
                    matched = entry
                    break
            if not matched:
                print(f"  {name:20s} 日录没命中")
                continue
            standard = str(matched.get("sightName"))
            # 用平台标准名回查
            rows = await list_by_keyword(standard)
            again = next((row for row in rows if str(row.get("sightName")) == standard), None)
            priced_entry = again if has_price(again) else matched
            picked = adult_ticket_price(priced_entry) if priced_entry else {}
            print(
                f"  {name:20s} → 标准名「{standard}」｜回查 {len(rows)} 条"
                f"｜{'有价' if has_price(again) else '无价'}｜票价 {picked.get('value')}"
                + (f"（{picked.get('ticket_name')}）" if picked.get("ticket_name") else "")
            )
        sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
