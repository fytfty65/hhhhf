"""探针第十轮：文旅部 marker 的 type_id 含义与三地覆盖（2026-10-07）。

`city` 才是地区过滤参数（653100 → 喀什 3 条）。`type_id=1` 看起来是 **5A**
（全国 359 条 ≈ 5A 总数）。这一轮把 type_id 1..5 在三地的条数摸清，
决定通道要抓哪些 type —— 覆盖率直接决定这条通道值不值。
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
CITIES = {"喀什地区": 653100, "巴音郭楞蒙古自治州": 652800, "伊犁哈萨克自治州": 654000}


async def main() -> int:
    import httpx

    headers = {"User-Agent": UA, "Accept": "application/json", "Referer": "https://lyfw.mct.gov.cn/site/special/scenic"}
    async with httpx.AsyncClient(timeout=20.0, headers=headers, follow_redirects=True) as client:
        for label, code in CITIES.items():
            print(f"\n=== {label}（city={code}）")
            for type_id in range(1, 7):
                try:
                    payload = (
                        await client.get(
                            "https://lyfw.mct.gov.cn/api/marker/list",
                            params={"type_id": type_id, "city": code, "pageNum": 1, "pageSize": 50},
                        )
                    ).json()
                except Exception as exc:
                    print(f"  type_id={type_id} 失败 {type(exc).__name__}")
                    continue
                data = payload.get("data") or {}
                rows = data.get("list") if isinstance(data, dict) else data
                total = data.get("total") if isinstance(data, dict) else None
                if not rows:
                    print(f"  type_id={type_id} → total={total}（无数据）")
                    continue
                names = [str(row.get("name"))[:18] for row in rows[:6]]
                with_site = sum(1 for row in rows if str(row.get("web_link_url") or "").strip())
                with_booking = sum(1 for row in rows if str(row.get("appointment_link_url") or "").strip())
                print(
                    f"  type_id={type_id} → total={total}｜官网 {with_site}/{len(rows)}｜预约 {with_booking}/{len(rows)}"
                    f"｜{names}"
                )
            sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
