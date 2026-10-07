"""探针：把官方名录页的**结构**看清楚（2026-10-07）。

上一轮：伊犁文旅名录页 40146 字符、正文里**有**"天鹅泉"、但没有 `detail?id=` 链接
（说明条目是 JS 渲染或是另一种链接格式）。这里把 HTML 的链接与"天鹅泉"附近的片段打出来，
并顺手看新疆文旅厅有没有**景区名录/A 级景区**栏目（那是全疆的，能覆盖喀什与库尔勒）。
"""

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
URLS = [
    ("伊犁文旅名录", "http://ylwl.xjyl.gov.cn/show/scenicspot/list?nodeId=2465"),
    ("新疆文旅厅", "https://wlt.xinjiang.gov.cn/"),
]


async def main() -> int:
    import httpx

    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
        for label, url in URLS:
            print(f"\n=== {label}｜{url}")
            try:
                resp = await client.get(url)
            except Exception as exc:
                print(f"  失败 {type(exc).__name__}: {str(exc)[:60]}")
                continue
            html = resp.text or ""
            hrefs = sorted(set(re.findall(r'href="([^"]+)"', html)))
            print(f"  {resp.status_code}｜{len(html)} 字符｜href {len(hrefs)} 个")
            interesting = [
                href
                for href in hrefs
                if any(word in href for word in ("scenic", "detail", "list", "jingqu", "景区", "A级", "lycp"))
            ]
            print("  相关链接：")
            for href in interesting[:14]:
                print(f"      · {href[:110]}")
            # 找"天鹅泉"附近的原始片段（看它是怎么被渲染的）
            spot = html.find("天鹅泉")
            if spot > 0:
                print("  『天鹅泉』附近原始片段：")
                print("     " + re.sub(r"\s+", " ", html[max(0, spot - 400) : spot + 200])[:560])
            # 有没有 JS 里带数据的痕迹
            for marker in ("ajax", "json", "api", "vue", "nuxt", "__NUXT__", "loadData"):
                if marker in html:
                    print(f"      含 {marker}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
