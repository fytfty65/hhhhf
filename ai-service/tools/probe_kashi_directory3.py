"""探针第三轮：把喀什/库尔勒的栏目树走到底，并看国家文旅部的 5A 景区服务页（2026-10-07）。

线索：
  · 喀什：`https://www.kashi.gov.cn/ksdqxzgs/c106685/zjks.shtml`（走进喀什）
    与 `…/c106705/list.shtml`（喀什风韵）—— 用户说的「喀什旅游」应该在这下面；
  · 库尔勒：`https://www.xjkel.gov.cn/xjkrls/c110128/jump_column.shtml`（政务公开，是跳转页）；
  · 国家文旅部：`https://lyfw.mct.gov.cn/site/special/scenic`（5A 级旅游景区服务）。

只问两件事：① 名录/列表页在哪个 URL；② 页面里到底有没有票价。
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

ENTRY_PAGES = [
    ("喀什·走进喀什", "https://www.kashi.gov.cn/ksdqxzgs/c106685/zjks.shtml"),
    ("喀什·喀什风韵", "https://www.kashi.gov.cn/ksdqxzgs/c106705/list.shtml"),
    ("库尔勒·政务公开跳转", "https://www.xjkel.gov.cn/xjkrls/c110128/jump_column.shtml"),
    ("文旅部·5A景区服务", "https://lyfw.mct.gov.cn/site/special/scenic"),
]


def links(html: str, base: str):
    out = []
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html or "", flags=re.S | re.I):
        clean = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", text))
        if not clean or len(clean) > 30:
            continue
        if not href or href.lower().startswith(("javascript:", "#", "mailto:")):
            continue
        out.append((urljoin(base, href), clean))
    return out


async def main() -> int:
    import httpx

    async with httpx.AsyncClient(
        timeout=15.0, headers={"User-Agent": UA, "Accept-Language": "zh-CN"}, follow_redirects=True
    ) as client:
        for label, url in ENTRY_PAGES:
            print(f"\n=== {label}｜{url}")
            try:
                resp = await client.get(url)
            except Exception as exc:
                print(f"  失败 {type(exc).__name__}: {str(exc)[:60]}")
                continue
            body = resp.text or ""
            print(f"  {resp.status_code}｜{len(body)} 字符｜最终 {str(resp.url)[:70]}")
            if "门票" in body:
                spot = body.find("门票")
                print("  含『门票』：", re.sub(r"\s+", " ", body[max(0, spot - 70): spot + 70])[:150])
            # JS 跳转页：把里面的目标 URL 抠出来
            scripted = re.findall(r'(?:location\.(?:href|replace)\s*=\s*|url\s*=\s*)["\']([^"\']+)["\']', body)
            if scripted:
                print("  脚本跳转目标:", scripted[:3])
            items = links(body, str(resp.url))
            # 更深的栏目（同一站点、路径含 c1xxxxx 的）
            deeper = [
                (href, name)
                for href, name in items
                if re.search(r"(c1\d{5}|column|list|index)", href) and urljoin(str(resp.url), href) != url
            ]
            uniq = []
            for href, name in deeper:
                if href not in [item[0] for item in uniq]:
                    uniq.append((href, name))
            print(f"  更深的栏目链接 {len(uniq)} 个")
            for href, name in uniq[:14]:
                print(f"    · {name[:16]:18s} {href[:82]}")
            # 景区样式条目
            spots = [
                (href, name)
                for href, name in items
                if 2 <= len(name) <= 22
                and re.search(r"(景区|公园|古城|博物馆|湿地|沙漠|胡杨|风情|乐园|草原|峡谷|泉|山|湖)", name)
            ]
            print(f"  景区样式条目 {len(spots)} 个")
            for href, name in spots[:8]:
                print(f"    ★ {name[:18]:20s} {href[:80]}")
            sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
