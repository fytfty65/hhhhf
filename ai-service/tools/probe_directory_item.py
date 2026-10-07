"""探针：名录条目的完整标记（要拿到 detail id 才能建索引）——2026-10-07。"""

import asyncio
import os
import re
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT / ".env")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
URL = "http://ylwl.xjyl.gov.cn/show/scenicspot/list?nodeId=2465"


async def main() -> int:
    import httpx

    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
        resp = await client.get(URL)
    html = resp.text or ""
    print(f"{URL} → {resp.status_code}｜{len(html)} 字符")
    spot = html.find("天鹅泉")
    print("\n--- 『天鹅泉』前后 1200 字符（原样）")
    print(html[max(0, spot - 900) : spot + 300])
    print("\n--- 页面里所有 onclick / data-* / detail 痕迹")
    for pattern in (r"onclick=\"[^\"]{0,120}\"", r"data-[a-z]+=\"[^\"]{0,60}\"", r"detail[^\"'<> ]{0,60}"):
        hits = sorted(set(re.findall(pattern, html, flags=re.I)))
        print(f"  {pattern}: {len(hits)} 个")
        for hit in hits[:8]:
            print(f"     · {hit[:110]}")
    # 条目数量线索
    for marker in ("scenicspot", "totalPage", "pageCount", "共", "条"):
        count = html.count(marker)
        if count:
            print(f"  含 {marker}: {count} 次")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
