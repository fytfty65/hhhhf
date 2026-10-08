"""探针第二轮：在三个官方站里**找到票价所在的那个页面**（2026-10-07）。

首页都是门户页（新闻/公告流），票价在子页。这一轮：
  ① 抓首页所有链接（href + 文字）；
  ② 挑"门票/购票/票价/预定/预订/票务/须知/公告/游览/景区介绍"这类字样的链接进去；
  ③ 在每个页面里找 `门票|票价|联票|观光车|区间车` 附近的数字，并打印**我们的抽取器**给出的结果。
每站最多跟 6 个链接，避免打扰对方站点。
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
SITES = [
    ("巴楚红海景区", "https://www.bachu.gov.cn/", "红海"),
    ("罗布人村寨", "https://www.xjlbr.com/", "罗布人村寨"),
    ("巴音布鲁克", "https://www.byblk.cn/", "巴音布鲁克"),
]
WANT = ("门票", "购票", "票价", "预定", "预订", "票务", "须知", "公告", "游览", "景区介绍", "参观", "服务")
PRICE_WORDS = ("门票", "票价", "联票", "观光车", "区间车")


def plain(html: str) -> str:
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


def links_of(html: str, base: str):
    out = []
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html or "", flags=re.S | re.I):
        clean = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", text))
        if not clean or len(clean) > 30:
            continue
        if href.lower().startswith(("javascript:", "#", "mailto:")):
            continue
        out.append((urljoin(base, href), clean))
    return out


async def main() -> int:
    import httpx

    from core.web_price_tool import extract_ticket_price

    async with httpx.AsyncClient(
        timeout=15.0, headers={"User-Agent": UA, "Accept-Language": "zh-CN"}, follow_redirects=True
    ) as client:
        for label, base, name in SITES:
            print(f"\n=== {label}｜{base}")
            try:
                home = await client.get(base)
            except Exception as exc:
                print(f"  首页失败 {type(exc).__name__}")
                continue
            candidates = []
            for url, text in links_of(home.text or "", str(home.url)):
                if any(word in text for word in WANT) and url not in [item[0] for item in candidates]:
                    candidates.append((url, text))
            print(f"  疑似票务/公告链接 {len(candidates)} 个")
            for url, text in candidates[:6]:
                print(f"    · {text[:20]:22s} {url[:78]}")
                try:
                    page = await client.get(url)
                except Exception as exc:
                    print(f"        抓取失败 {type(exc).__name__}")
                    continue
                text_page = plain(page.text or "")
                hits = re.findall(r"[^。；<>]{0,20}(?:门票|票价|联票|观光车|区间车)[^。；<>]{0,30}", text_page)
                picked = extract_ticket_price(text_page, require_name=name, name_window=120)
                if hits or picked:
                    print(f"        {page.status_code}｜{len(text_page)} 字符｜抽取 {picked}")
                    for hit in hits[:5]:
                        print("         片段:", hit.strip()[:110])
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
