"""探针：用户给的三个**独立官方域名**能不能直连、页面里到底写了什么价（2026-10-07）。

用户提供的源：
  · 巴楚红海景区 — `www.bachu.gov.cn`（巴楚县政府网）→ 公示的是**优惠政策**（半票群体），全价票待确认
  · 罗布人村寨   — `www.xjlbr.com`（独立官网）→ 门票 35 元/人、观光车票 15 元/人
  · 巴音布鲁克   — `www.byblk.cn`（官网票务页）→ 联票 140 元（含门票+区间车）

这一轮只做三件事：可达性（http/https/状态码）、页面里能否找到票价、以及**我们现成的门票抽取器**
（`extract_ticket_price`）能不能把用户说的数字抽出来 —— 抽不出来就说明抽取口径要调。
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT / ".env")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
SITES = [
    ("巴楚红海景区", "www.bachu.gov.cn", ["/", "/hhy/", "/ly/"]),
    ("罗布人村寨", "www.xjlbr.com", ["/", "/ticket", "/menpiao"]),
    ("巴音布鲁克", "www.byblk.cn", ["/", "/ticket", "/menpiao"]),
]


async def main() -> int:
    import httpx

    from core.web_price_tool import extract_ticket_price

    async with httpx.AsyncClient(
        timeout=15.0, headers={"User-Agent": UA, "Accept-Language": "zh-CN"}, follow_redirects=True
    ) as client:
        for label, host, paths in SITES:
            print(f"\n=== {label}｜{host}")
            home = None
            for scheme in ("https", "http"):
                try:
                    resp = await client.get(f"{scheme}://{host}/")
                except Exception as exc:
                    print(f"  {scheme}://{host}/ 失败 {type(exc).__name__}: {str(exc)[:50]}")
                    continue
                print(f"  {scheme}://{host}/ → {resp.status_code}｜{len(resp.text or ''):7d} 字符｜最终 {str(resp.url)[:60]}")
                home = resp
                break
            if home is None:
                continue
            body = home.text or ""
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", body)))
            # 页面里所有"门票/票价/联票/观光车 + 数字"的片段，人工核对
            hits = re.findall(r"[^。；<>]{0,24}(?:门票|票价|联票|观光车|区间车)[^。；<>]{0,34}", text)
            for hit in hits[:8]:
                print("   片段:", hit.strip()[:110])
            for name in (label, label.replace("景区", ""), "红海", "罗布人", "巴音布鲁克"):
                picked = extract_ticket_price(text, require_name=name, name_window=120)
                if picked:
                    print(f"   抽取({name}) → {picked}")
                    break
            # 找"购票/票务/门票"相关的链接，看看是否有独立票务子页
            links = sorted(
                set(
                    re.findall(r'href="([^"]*(?:ticket|piao|menpiao|goupiao|booking|buy)[^"]*)"', body, flags=re.I)
                )
            )
            if links:
                print("   疑似票务链接:", links[:6])
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
