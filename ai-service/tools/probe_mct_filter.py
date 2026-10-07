"""探针第九轮：把文旅部 marker 接口的**地区过滤**参数试出来（2026-10-07）。

`province_id` 被忽略了（喀什与巴州返回同一页，共 359 条 = 全国）。marker 记录本身有
`province`/`city` 字段，所以过滤参数可能是 `province`/`city`/`area_id`/`city_id`。
"""

import asyncio
import json
import os
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT / ".env")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
TRIES = [
    {"type_id": 1, "province": 653100, "pageNum": 1, "pageSize": 20},
    {"type_id": 1, "city": 653100, "pageNum": 1, "pageSize": 20},
    {"type_id": 1, "city_id": 653100, "pageNum": 1, "pageSize": 20},
    {"type_id": 1, "area_id": 653100, "pageNum": 1, "pageSize": 20},
    {"type_id": 1, "areaId": 653100, "pageNum": 1, "pageSize": 20},
    {"type_id": 1, "region_id": 653100, "pageNum": 1, "pageSize": 20},
    {"type_id": 1, "provinceCode": 653100, "pageNum": 1, "pageSize": 20},
    {"type_id": 1, "province_id": 653100, "city_id": 653100, "pageNum": 1, "pageSize": 20},
    {"type_id": 1, "keyword": "喀什", "pageNum": 1, "pageSize": 20},
]


async def main() -> int:
    import httpx

    headers = {"User-Agent": UA, "Accept": "application/json", "Referer": "https://lyfw.mct.gov.cn/site/special/scenic"}
    async with httpx.AsyncClient(timeout=20.0, headers=headers, follow_redirects=True) as client:
        for params in TRIES:
            try:
                resp = await client.get("https://lyfw.mct.gov.cn/api/marker/list", params=params)
                payload = resp.json()
            except Exception as exc:
                print(f"  {str(params)[:76]:78s} 失败 {type(exc).__name__}")
                continue
            data = payload.get("data")
            rows = (data or {}).get("list") if isinstance(data, dict) else data
            total = (data or {}).get("total") if isinstance(data, dict) else None
            names = [str(row.get("name"))[:16] for row in (rows or [])[:4]]
            print(f"  {str(params)[:76]:78s} code={payload.get('code')} total={total} 前几条={names}")
            if rows and any("喀什" in str(row.get("name")) or "疏勒" in str(row.get("name")) or "英吉沙" in str(row.get("name")) for row in rows):
                print("     命中喀什相关，完整首条:", json.dumps(rows[0], ensure_ascii=False)[:400])
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
