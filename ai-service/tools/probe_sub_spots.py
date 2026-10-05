"""子景点合并的**真实数据**验证（2026-10-05）。

为什么要单独一个探针：横向对照里伊宁的候选池从 43 条被"合并"到 28 条，
必须看清**合并掉的到底是同一个景区的子点，还是被误并的真景区**。
这里直接打高德（用生产同一批关键词），跑 `collapse_sub_spots`，把每个合并组
（保留了谁、并掉了谁、两点相距多少公里）打出来。

用法：
    cd ai-service
    .\\venv\\Scripts\\python.exe tools\\probe_sub_spots.py 伊宁 伊犁哈萨克自治州
"""

import asyncio
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from core.poi_pool import (  # noqa: E402
    NEIGHBOR_SCENIC_KEYWORDS,
    amap_error_note,
    collapse_sub_spots,
    haversine_km,
    poi_record_from_amap,
)

SCENIC_KEYWORDS = "自然风景|公园|天山|天池|草原|湖|峡谷|沙漠|绿洲|植物园|动物园|5A景区"


def _load_key() -> str:
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    with io.open(env_path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip().startswith("AMAP_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


async def fetch(client: httpx.AsyncClient, key: str, city: str, keywords: str, limit: int = 40) -> list:
    params = {"key": key, "keywords": keywords, "city": city, "sortrule": "weight", "offset": limit, "page": 1, "extensions": "all"}
    resp = await client.get("https://restapi.amap.com/v3/place/text", params=params)
    data = resp.json()
    if str(data.get("status")) != "1":
        print(f"  ⚠️ {city} {keywords!r}: {amap_error_note(str(data.get('info') or ''), data.get('status'))}")
        return []
    out = []
    for poi in data.get("pois") or []:
        if isinstance(poi, dict):
            out.append(poi_record_from_amap(poi, city=city, amap_key=key))
    return out


def report(items: list, label: str) -> None:
    print("=" * 78)
    print(f"{label}：原始 {len(items)} 条")
    print("=" * 78)
    for item in items:
        print(f"  · {item.get('name')}  [{item.get('type')}]  {item.get('lnglat')}")
    kept, merged = collapse_sub_spots(items)
    print(f"\n合并后 {len(kept)} 条（并掉 {merged} 条）")

    def clean(name: str) -> str:
        return "".join(ch for ch in str(name or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

    groups: dict = {}
    for item in kept:
        key = clean(str(item.get("name") or ""))[:4]
        groups.setdefault(key, []).append(item)
    dropped = [item for item in items if str(item.get("name")) not in {str(k.get("name")) for k in kept}]
    print("\n被并掉的条目 → 保留者（距离 km）：")
    for item in dropped:
        key = clean(str(item.get("name") or ""))[:4]
        candidates = groups.get(key) or []
        best = None
        for candidate in candidates:
            distance = haversine_km(candidate.get("lnglat"), item.get("lnglat"))
            if distance == distance and (best is None or distance < best[0]):
                best = (distance, candidate.get("name"))
        if best is None:
            print(f"  · {item.get('name')} → ??（找不到保留者，说明是重名/缺坐标）")
        else:
            print(f"  · {item.get('name')} → {best[1]}（{best[0]:.1f}km）")
    print()


async def main() -> None:
    cities = sys.argv[1:] or ["伊宁", "伊犁哈萨克自治州"]
    key = _load_key()
    if not key:
        print("缺少 AMAP_API_KEY")
        return
    async with httpx.AsyncClient(timeout=15.0) as client:
        for city in cities:
            keywords = SCENIC_KEYWORDS if city == "伊宁" else NEIGHBOR_SCENIC_KEYWORDS
            items = await fetch(client, key, city, keywords)
            report(items, f"{city}｜{keywords}")


if __name__ == "__main__":
    asyncio.run(main())
