"""探针第八轮：文旅部 marker 接口在新疆的实际数据（2026-10-07）。

上一轮打通了参数（`type_id` + `province_id`）。这一轮看：
  · 喀什地区 / 巴州 的 id；
  · 这两个地区的景区条目里有哪些字段（官网、官方预约平台、坐标）；
  · 分页上限（一次能取多少），以及**有没有票价字段**。
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
API = "https://lyfw.mct.gov.cn/api"


async def main() -> int:
    import httpx

    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
        "Referer": "https://lyfw.mct.gov.cn/site/special/scenic",
    }
    async with httpx.AsyncClient(timeout=20.0, headers=headers, follow_redirects=True) as client:
        prov = (await client.get(f"{API}/marker/province/list")).json()
        children = []
        for item in prov.get("data") or []:
            for child in item.get("children") or []:
                children.append((child.get("id"), child.get("name"), child.get("count")))
        for target in ("喀什", "巴音郭楞", "伊犁"):
            hits = [row for row in children if target in str(row[1])]
            print(f"  {target}: {hits}")

        for name, pid in (("喀什地区", next((row[0] for row in children if "喀什" in str(row[1])), 653100)),
                          ("巴音郭楞", next((row[0] for row in children if "巴音郭楞" in str(row[1])), 652800))):
            print(f"\n=== {name}（province_id={pid}）")
            data = (await client.get(f"{API}/marker/list", params={"type_id": 1, "province_id": pid, "pageNum": 1, "pageSize": 50})).json()
            payload = data.get("data") or {}
            rows = payload.get("list") if isinstance(payload, dict) else payload
            total = payload.get("total") if isinstance(payload, dict) else None
            print(f"  共 {total} 条，本次 {len(rows or [])} 条")
            for row in (rows or [])[:8]:
                print(
                    f"    · {str(row.get('name'))[:20]:22s} 等级={row.get('level') or row.get('type_name') or '-'} "
                    f"官网={str(row.get('web_link_url'))[:34]:36s} 预约={str(row.get('appointment_link_url'))[:30]}"
                )
            if rows:
                print("  一条完整字段：", json.dumps(rows[0], ensure_ascii=False)[:600])
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
