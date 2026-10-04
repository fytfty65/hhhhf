"""周边县市自然景区探针：目的地的"真景区"往往不在市辖区里。

真实问题（2026-10-04，库尔勒实测）：
  用户说"想看自然风景"，但候选池里的自然点全是市区公园（孔雀公园/龙山公园/湿地公园），
  而库尔勒真正拿得出手的自然景观 —— 博斯腾湖（博湖县）、罗布人村寨（尉犁县）、
  塔克拉玛干沙漠边缘 —— 都在**周边县市**，不在库尔勒市辖区内。
  如果检索被限制在市辖区，方案就永远只能拿公园凑数。

这个探针回答三件事（只用真实高德接口，不编数据）：
  1. 同一个自然关键词，`city=库尔勒` 与 `city=巴音郭楞蒙古自治州`（上级州）返回什么；
  2. 明确搜"博斯腾湖/罗布人村寨"时，哪个 city 参数能搜到；
  3. 这些景区离市中心多远（决定"值不值得排进 2 天行程"）。

用法：
  python tools/probe_neighbor_scenery.py 库尔勒 巴音郭楞蒙古自治州
"""

from __future__ import annotations

import asyncio
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from api.agent import ExpertToolbox  # noqa: E402

SCENIC_KEYWORDS = "自然风景|公园|天山|天池|草原|湖|峡谷|沙漠|绿洲|植物园|动物园|5A景区"
LANDMARK_KEYWORDS = "博斯腾湖|罗布人村寨|塔克拉玛干|胡杨林|大峡谷|草原"


def haversine_km(a, b) -> float:
    if not a or not b or len(a) != 2 or len(b) != 2:
        return float("nan")
    lon1, lat1 = float(a[0]), float(a[1])
    lon2, lat2 = float(b[0]), float(b[1])
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    h = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def parse_lnglat(value) -> list:
    text = str(value or "")
    if "," not in text:
        return []
    left, _, right = text.partition(",")
    try:
        return [float(left), float(right)]
    except ValueError:
        return []


async def query(toolbox: ExpertToolbox, city: str, keywords: str, limit: int = 20) -> list:
    return await toolbox.get_dynamic_pois(city, keywords=keywords, types="", limit=limit, play_worthy=True)


async def main() -> int:
    city = sys.argv[1] if len(sys.argv) > 1 else "库尔勒"
    parent = sys.argv[2] if len(sys.argv) > 2 else "巴音郭楞蒙古自治州"

    toolbox = ExpertToolbox()
    if not getattr(toolbox, "amap_key", ""):
        print("❌ 没有高德 key（AMAP_API_KEY 未配置）")
        return 1

    center = parse_lnglat(await toolbox.get_coordinates(city))
    print(f"城市 = {city}（中心 {center}）/ 上级区域 = {parent}\n")

    # 1) geocode 能不能直接告诉我们"上级区域"（州/地区/盟）？能的话就不用维护映射表
    import httpx

    try:
        async with httpx.AsyncClient(timeout=6.0) as http_client:
            resp = await http_client.get(
                f"{toolbox.amap_url}/geocode/geo",
                params={"key": toolbox.amap_key, "address": city, "extensions": "base"},
            )
            geo = resp.json()
        if str(geo.get("status")) == "1" and geo.get("geocodes"):
            first = geo["geocodes"][0]
            print("--- geocode（用于解析上级区域）---")
            for key in ("formatted_address", "province", "city", "district", "adcode", "level"):
                print(f"    {key} = {first.get(key)}")
            print()
        else:
            print(f"--- geocode 未返回结果: {geo.get('info')} ---\n")
    except Exception as exc:
        print(f"--- geocode 调用失败: {exc} ---\n")

    # 2) "景区专用关键词"在上级区域下能搜到什么（真实自然景观）
    for label, target_city, keywords in (
        ("上级区域·景区专用关键词", parent, "风景名胜区|自然保护区|湖|沙漠|草原|峡谷|胡杨林|地质公园|国家森林公园|湿地"),
        ("市区·景区专用关键词", city, "风景名胜区|自然保护区|湖|沙漠|草原|峡谷|胡杨林|地质公园|国家森林公园|湿地"),
    ):
        print(f"--- {label}（city={target_city}）---")
        print(f"  keywords={keywords!r}")
        try:
            rows = await query(toolbox, target_city, keywords, limit=25)
        except Exception as exc:
            print(f"  ⛔ 调用失败: {exc}\n")
            continue
        print(f"  条数={len(rows)}")
        for row in rows[:14]:
            distance = haversine_km(center, row.get("lnglat"))
            distance_text = "?" if math.isnan(distance) else f"{distance:.0f}km"
            print(f"    {row.get('name')}｜{row.get('type')}｜距市中心 {distance_text}")
        print()

    for label, target_city, keywords in (
        ("市区自然关键词", city, SCENIC_KEYWORDS),
        ("上级区域自然关键词", parent, SCENIC_KEYWORDS),
        ("市区·点名景区", city, LANDMARK_KEYWORDS),
        ("上级区域·点名景区", parent, LANDMARK_KEYWORDS),
    ):
        print(f"--- {label}（city={target_city}）---")
        print(f"  keywords={keywords!r}")
        try:
            rows = await query(toolbox, target_city, keywords)
        except Exception as exc:
            print(f"  ⛔ 调用失败: {exc}\n")
            continue
        if not rows:
            print("  （无结果）\n")
            continue
        print(f"  条数={len(rows)}")
        for row in rows[:12]:
            distance = haversine_km(center, row.get("lnglat"))
            distance_text = "?" if math.isnan(distance) else f"{distance:.0f}km"
            print(f"    {row.get('name')}｜{row.get('type')}｜距市中心 {distance_text}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
