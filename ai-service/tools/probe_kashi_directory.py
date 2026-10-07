"""探针：按用户给的域名/栏目，把喀什与库尔勒的**景区名录**找出来（2026-10-07）。

用户提供：
  · 喀什地区文旅局 `www.kashi.gov.cn` —— 名录在「走进喀什 > 喀什风韵 > 喀什旅游」；
  · 喀什市文旅局 `www.xjks.gov.cn`；
  · 库尔勒市文旅局 `www.xjkel.gov.cn` —— A 级景区列表在「政务公开」等栏目；
  · 巴州文旅局 `www.xjbz.gov.cn`。

目标：找到"**景区名 → 页面**"的入口（最好是名录页 + 每条的链接），并确认页面上有没有票价。
做法：抓首页 → 挑疑似栏目链接 → 抓那些页 → 看条目结构（名字 + 链接 + 是否写"门票"）。
"""

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
    ("喀什地区政府/文旅", "http://www.kashi.gov.cn/"),
    ("喀什市政府", "http://www.xjks.gov.cn/"),
    ("库尔勒市政府", "http://www.xjkel.gov.cn/"),
    ("巴州政府", "http://www.xjbz.gov.cn/"),
]

# 栏目线索：含这些词的内链值得跟进去
HINTS = ("走进喀什", "喀什风韵", "喀什旅游", "旅游景区", "A级", "A 级", "景区", "旅游", "政务公开", "文旅")


def links(html: str, base: str):
    out = []
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html or "", flags=re.S | re.I):
        clean = re.sub(r"<[^>]+>", "", text).strip()
        clean = re.sub(r"\s+", "", clean)
        if not clean or len(clean) > 30:
            continue
        if not href or href.lower().startswith(("javascript:", "#", "mailto:")):
            continue
        out.append((urljoin(base, href), clean))
    return out


async def fetch(client, url: str):
    try:
        resp = await client.get(url)
    except Exception as exc:
        print(f"    {url[:70]:72s} 失败 {type(exc).__name__}: {str(exc)[:40]}")
        return None
    return resp


async def main() -> int:
    import httpx

    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": UA, "Accept-Language": "zh-CN"}) as client:
        for label, base in SITES:
            print(f"\n=== {label}｜{base}")
            resp = await fetch(client, base)
            if resp is None:
                continue
            html = resp.text or ""
            print(f"  {resp.status_code}｜{len(html)} 字符")
            candidates = []
            for url, text in links(html, base):
                if any(hint in text for hint in HINTS) and url not in [item[0] for item in candidates]:
                    candidates.append((url, text))
            print(f"  疑似栏目链接 {len(candidates)} 个")
            for url, text in candidates[:8]:
                print(f"    · {text[:16]:18s} {url[:78]}")
            # 跟进去前 4 个，看条目结构
            for url, text in candidates[:4]:
                page = await fetch(client, url)
                if page is None:
                    continue
                body = page.text or ""
                items = links(body, url)
                priced = "门票" in body
                # 条目：链接文字像景区名（2~20 字、含景区/公园/古城/博物馆/寺/湖/山 等）
                spots = [
                    (href, name)
                    for href, name in items
                    if 2 <= len(name) <= 20
                    and re.search(r"(景区|公园|古城|博物馆|寺|湖|山|湿地|沙漠|胡杨|风情|乐园|草原|峡谷|泉)", name)
                ]
                print(f"    ↳ {text[:14]}｜{page.status_code}｜{len(body)} 字符｜景区名样式的链接 {len(spots)} 个｜含『门票』{priced}")
                for href, name in spots[:6]:
                    print(f"         · {name[:20]:22s} {href[:74]}")
            sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
