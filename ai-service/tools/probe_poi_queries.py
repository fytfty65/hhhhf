"""候选池检索探针：为什么某个城市"美食/住宿候选 = 0"？

背景（实测 2026-10-04）：用户用乌鲁木齐做真实规划，日志是
    [候选池] attractions=17 foods=0 hotels=0 类别={'scenic': 1, 'cultural': 16}
用户在诉求里明确说了"想看更多自然景观、想吃地道美食"，但美食候选为 0、景点池 16/17 是人文。

本探针把"查不到"和"被过滤掉"分开报：
  1. 按生产参数直连高德（不打过滤器）→ 原始返回几条、前几条叫什么；
  2. 逐条套用生产里的过滤器（非文旅黑名单 / is_play_worthy）→ 谁被谁丢的；
  3. 调用生产的 get_dynamic_pois → 最终剩几条（应当与 (2) 对上）。

用法：
    .\\venv\\Scripts\\python.exe tools\\probe_poi_queries.py [城市]
"""

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.console import ensure_utf8_console  # noqa: E402

ensure_utf8_console()

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import httpx  # noqa: E402

from api.agent import NON_TOURIST_BLACKLIST, ExpertToolbox  # noqa: E402
from core.poi_pool import is_play_worthy  # noqa: E402

# 与生产完全一致的查询参数（见 api/agent.py 的 foods_task / hotels_task / 景点检索）。
# 最后一列 play_worthy：景点检索要"像玩点"，美食/住宿**不能**套用这条（否则全军覆没）。
QUERIES = [
    ("景点(生产参数)", "品质景点|自然风光|风景名胜|特色景观", "110000|141200|060400|060100", 60, True),
    ("自然景观(用户诉求关键词)", "自然景观|自然保护区|地质公园|湿地公园|森林公园", "", 40, True),
    ("美食(生产参数)", "特色老字号|地方特色美食|正宗名吃|特色正餐|本地特色菜", "050000", 60, False),
    ("美食(通用兜底)", "美食|餐厅|小吃|本地菜", "", 40, False),
    ("住宿(生产参数)", "品质酒店|高端度假酒店|高分精品住宿", "100000", 30, False),
    ("住宿(通用兜底)", "酒店|民宿|宾馆", "", 30, False),
]


def drop_reason(name: str, amap_type: str, play_worthy: bool) -> str:
    lowered = name.lower()
    if any(bad.lower() in lowered for bad in NON_TOURIST_BLACKLIST):
        return "黑名单(非文旅商业)"
    # 与生产同一套判定：只有景点检索才要求"属于玩点类别"
    if not is_play_worthy(name, amap_type, strict_type=play_worthy, allow_commerce=not play_worthy):
        return "设施名（停车场/出入口…）或类别不符"
    return ""


async def raw_query(toolbox: ExpertToolbox, city: str, keywords: str, types: str, limit: int):
    """直接打高德，不经过任何过滤器。"""
    params = {
        "key": toolbox.amap_key,
        "keywords": keywords,
        "city": city,
        "sortrule": "weight",
        "offset": limit,
        "page": 1,
        "extensions": "all",
    }
    if not str(keywords or "").strip() and types:
        params["types"] = types
    async with httpx.AsyncClient(timeout=6.0) as client:
        response = await client.get(f"{toolbox.amap_url}/place/text", params=params)
        return response.json()


async def main() -> int:
    city = sys.argv[1] if len(sys.argv) > 1 else "乌鲁木齐"
    toolbox = ExpertToolbox()
    if not toolbox.amap_key:
        print("❌ 没有高德 key（AMAP_API_KEY 未配置）")
        return 1
    print(f"城市 = {city}\n")

    for label, keywords, types, limit, play_worthy in QUERIES:
        payload = await raw_query(toolbox, city, keywords, types, limit)
        status = payload.get("status")
        pois = payload.get("pois") or []
        info = payload.get("info")
        print(f"--- {label} ---")
        print(f"  keywords={keywords!r} types={types!r} play_worthy={play_worthy}")
        print(f"  高德原始返回: status={status} info={info} 条数={len(pois)}")
        dropped = []
        survivors = []
        for poi in pois[:limit]:
            if not isinstance(poi, dict):
                continue
            name = str(poi.get("name") or "")
            amap_type = str(poi.get("type") or "")
            reason = drop_reason(name, amap_type, play_worthy)
            if reason:
                dropped.append(f"{name}（{reason}）")
            else:
                survivors.append(f"{name}｜{amap_type}")
        for row in survivors[:5]:
            print(f"    ✅ {row}")
        for row in dropped[:3]:
            print(f"    ⛔ {row}")
        if len(pois) == 0 and info:
            print(f"    （高德没返回任何 POI：info={info}）")

        produced = await toolbox.get_dynamic_pois(
            city, keywords=keywords, types=types or "110000", limit=limit, play_worthy=play_worthy
        )
        print(f"  生产 get_dynamic_pois 最终产出: {len(produced)} 条")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
