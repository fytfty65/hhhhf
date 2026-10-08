"""探针第四轮：票价到底藏在哪个接口/字符串里（2026-10-07）。

已知：两个站的页面是客户端渲染，JS 包里出现了 35/15/140 这些数字，但不一定是票价。
这一轮把 `/api/...` 与 JS 里 **门票/票价/price 附近**的片段打出来，判定：
  · 有结构化接口 → 可以当通道接（可靠）；
  · 只有前端模板串 → 需要浏览器/小程序，本机取不到，只能登记域名 + 提示官方购票入口。
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT / ".env")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
SITES = ["https://www.xjlbr.com/", "https://www.byblk.cn/"]
API_TRIES = [
    "/api/cms/article/list",
    "/api/cms/article/detail",
    "/api/article/list",
    "/api/ticket/list",
    "/api/scenic/ticket",
    "/api/price/list",
]


async def main() -> int:
    import httpx

    async with httpx.AsyncClient(
        timeout=15.0, headers={"User-Agent": UA, "Accept-Language": "zh-CN"}, follow_redirects=True
    ) as client:
        for base in SITES:
            print(f"\n=== {base}")
            home = await client.get(base)
            html = home.text or ""
            # 页面 + JS 里所有 /api/... 线索
            assets = [urljoin(str(home.url), item) for item in re.findall(r'(?:src|href)="([^"]+\.js[^"]*)"', html, flags=re.I)]
            corpus = html
            for asset in assets[:5]:
                if "xjlbr" not in asset and "byblk" not in asset:
                    continue
                try:
                    corpus += (await client.get(asset)).text or ""
                except Exception:
                    continue
            apis = sorted(set(re.findall(r'["\'`](/api/[A-Za-z0-9_/\-\.{}]{2,70})["\'`]', corpus)))
            print(f"  /api/ 线索 {len(apis)} 个：{apis[:10]}")
            # JS 里"门票/票价/price"附近
            for pattern in (r".{0,40}(?:门票|票价|联票).{0,50}", r'["\']?price["\']?\s*[:=]\s*["\']?\d+.{0,20}'):
                hits = re.findall(pattern, corpus)
                if hits:
                    print(f"  片段（{pattern[:18]}…）{len(hits)} 条：")
                    for hit in hits[:4]:
                        print("    ", re.sub(r"\s+", " ", hit)[:140])
            # 试接口
            for path in API_TRIES[:4]:
                try:
                    resp = await client.get(urljoin(str(home.url), path))
                except Exception:
                    continue
                text = (resp.text or "").strip()
                if resp.status_code < 400 and ("price" in text or "门票" in text or '"data"' in text):
                    print(f"  接口 {path} → {resp.status_code}｜{len(text)} 字符｜{text[:120]}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
