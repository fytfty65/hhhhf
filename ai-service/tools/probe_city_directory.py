"""探针：**城市级景区日录**能不能补上单点查不到的票（2026-10-07）。

背景：单点查（"喀什 英吉沙国家湿地公园 门票"）走三条通道只补到 1~2/12。
但平台自己的列表接口按**城市**能一次返回十几条景区（去哪儿 `list.json` 实测 keyword="博斯腾湖" 返回 15 条），
如果按城市拉一次、再用**宽容的名字匹配**对上方案节点，就能把"名字带县名前缀/词序不同"的那些也接上。

这个探针量三件事：
  ① 城市级 keyword 拉得到多少条、里面有没有方案里的这些景点；
  ② 严格挑名（`pick_sight` 归一化相等/包含）能对上几个；
  ③ 对上之后有多少能抽出**门票口径**的价格。
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

# 方案里真实出现过、且单点查至今没拿到价的景点
TARGETS = {
    "伊宁": ["天鹅泉湿地公园", "图开沙漠冰雪乐园", "阿吾赞沟森林公园", "伊犁河旅游风景区", "草原遗迹", "沙漠迷宫", "六星街"],
    "喀什": ["英吉沙国家湿地公园", "疏勒历史博物馆", "胡杨书院", "喀什博物馆", "百年喀什馆", "阳光公园", "九龙泉湿地公园"],
    "库尔勒": ["库尔勒体育公园", "铁门关景区", "龙山公园", "博斯腾湖"],
}
KEYWORDS = ["{city} 景点", "{city} 门票", "{city} 旅游景区", "{city}"]


async def fetch_list(keyword: str):
    from core.ticket_platforms import _get_json, QUNAR_TICKET_API

    payload = await _get_json(QUNAR_TICKET_API, {"keyword": keyword, "page": "1"})
    data = payload.get("data") or {}
    return data.get("list") or data.get("sightList") or []


async def main() -> int:
    from core.official_directory import _normalize
    from core.ticket_platforms import adult_ticket_price, pick_sight

    for city, names in TARGETS.items():
        print(f"\n=== {city}")
        best_entries: dict = {}
        for template in KEYWORDS:
            keyword = template.format(city=city)
            try:
                rows = await fetch_list(keyword)
            except Exception as exc:
                print(f"  keyword={keyword!r} 失败 {type(exc).__name__}")
                continue
            for row in rows:
                name = str(row.get("sightName") or "").strip()
                if name and name not in best_entries:
                    best_entries[name] = row
            print(f"  keyword={keyword!r} → {len(rows)} 条（累计 {len(best_entries)} 个不同景区）")
        # 严格挑名 vs 城市日录
        hit_strict = hit_dir = priced = 0
        for name in names:
            strict = pick_sight(list(best_entries.values()), name)
            if strict:
                hit_strict += 1
            matched = None
            for entry_name, entry in best_entries.items():
                if _normalize(name) and (
                    _normalize(name) == _normalize(entry_name)
                    or _normalize(name) in _normalize(entry_name)
                    or _normalize(entry_name) in _normalize(name)
                ):
                    matched = entry
                    break
            if matched:
                hit_dir += 1
                picked = adult_ticket_price(matched)
                if picked:
                    priced += 1
            print(
                f"    {name:22s} 严格挑名 {'命中' if strict else '—':4s} 日录 {'命中' if matched else '—':4s}"
                + (f"｜票价 {adult_ticket_price(matched).get('value')}" if matched and adult_ticket_price(matched) else "")
            )
        print(f"  小结：{len(names)} 个里 严格 {hit_strict}｜日录 {hit_dir}｜有门票价 {priced}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
