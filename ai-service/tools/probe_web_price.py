"""联网取价探针：对给定的地点逐个搜价，验证"能不能拿到价格线索 + 出处"。

为什么单独做：价格补全在推演里是**有界且按条件触发**的一步，跑整条推演去验证它既慢又贵。
这个探针直接把取价链路（提供方 → 解析 → 抽价 → 出处）跑一遍，结果一目了然：

  python tools/probe_web_price.py 库尔勒 "博斯腾湖风景名胜区" "库尔勒民俗文化博物馆" "金润假日酒店"

输出每个地点的：命中提供方、抽到的价格、价格原文片段、来源页 URL / 站名 / 日期。
诚实口径：搜到的价格一律标"估算（不是官方报价）"；搜不到就写"未取到"，绝不填数字。
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from core.web_price_tool import (  # noqa: E402
    build_observation,
    enabled_providers,
    last_search_attempts,
    pick_price_from_rows,
    probe_providers,
    search_texts,
)


async def lookup(city: str, name: str) -> dict:
    query = f"{city} {name} 门票 价格 人均 每晚"
    try:
        rows = await search_texts(query, max_results=3)
    except Exception as exc:
        return {"name": name, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    best = pick_price_from_rows(rows)
    if best is None:
        observation = build_observation(name=name, value=None, source="web", provider=",".join(enabled_providers()))
        observation["rows"] = len(rows)
        return observation
    return build_observation(
        name=name,
        value=float(best["value"]),
        source="web",
        source_url=str(best.get("url") or ""),
        excerpt=str(best.get("excerpt") or ""),
        provider=str(best.get("provider") or ""),
        publisher=str(best.get("publisher") or ""),
        source_date=str(best.get("source_date") or ""),
    )


async def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    city = sys.argv[1]
    names = sys.argv[2:]
    print(f"提供方顺序: {enabled_providers()}")
    report = await probe_providers()
    for provider, status in report["providers"].items():
        print(f"  · {provider}: {status}")
    print(f"可达: {report['reachable']}\n")

    hits = 0
    for name in names:
        result = await lookup(city, name)
        if result.get("value") is not None:
            hits += 1
            print(f"✅ {name}")
            print(f"   价格 ¥{result['value']:g}（估算，非官方报价）｜出处: {result.get('publisher') or result.get('provider')}")
            print(f"   原文: {result.get('excerpt')}")
            if result.get("source_date"):
                print(f"   信息日期: {result['source_date']}（价格会过期，请按这个日期判断新鲜度）")
            print(f"   来源: {result.get('source_url') or '(没抓到链接)'}")
        else:
            print(f"⬜ {name} —— 未取到（{result.get('error') or '搜索结果里没有可抽的价格'}）")
    print(f"\n命中 {hits}/{len(names)}")
    if last_search_attempts:
        print("搜索尝试记录:", last_search_attempts)
    return 0 if hits else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
