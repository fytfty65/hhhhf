"""探针第二轮：带上重定向与 https 回退，按用户给的栏目路径找名录（2026-10-07）。

用户给的线索：
  · 喀什：`www.kashi.gov.cn` 的「走进喀什 > 喀什风韵 > 喀什旅游」栏目；
  · 库尔勒：`www.xjkel.gov.cn` 的「政务公开」等栏目里有 A 级景区列表；
  · 巴州：`www.xjbz.gov.cn`。
上一轮 `kashi.gov.cn` / `xjkel.gov.cn` 都返回 301（没有跟随重定向），这一轮跟随并用 https 回退。
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
SITES = ["www.kashi.gov.cn", "www.xjkel.gov.cn"]
# 用户点名的栏目词，以及"名录"特征词
SECTION_HINTS = ("走进喀什", "喀什风韵", "喀什旅游", "景区", "A级", "A 级", "名录", "旅游", "政务公开", "文旅")


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


async def open_site(client, host: str):
    for url in (f"http://{host}/", f"https://{host}/"):
        try:
            resp = await client.get(url)
        except Exception as exc:
            print(f"  {url:34s} 失败 {type(exc).__name__}: {str(exc)[:44]}")
            continue
        print(f"  {url:34s} {resp.status_code}｜{len(resp.text or '')} 字符｜最终 {str(resp.url)[:52]}")
        if resp.status_code < 400 and len(resp.text or "") > 1000:
            return resp
    return None


async def main() -> int:
    import httpx

    async with httpx.AsyncClient(
        timeout=15.0, headers={"User-Agent": UA, "Accept-Language": "zh-CN"}, follow_redirects=True
    ) as client:
        for host in SITES:
            print(f"\n=== {host}")
            home = await open_site(client, host)
            if home is None:
                continue
            base = str(home.url)
            html = home.text or ""
            sections = []
            for url, text in links(html, base):
                if any(hint in text for hint in SECTION_HINTS) and url not in [item[0] for item in sections]:
                    sections.append((url, text))
            print(f"  疑似栏目 {len(sections)} 个")
            for url, text in sections[:10]:
                print(f"    · {text[:14]:16s} {url[:76]}")
            # 跟进去，找"景区名 + 链接"的条目
            for url, text in sections[:6]:
                try:
                    page = await client.get(url)
                except Exception as exc:
                    print(f"    ↳ {text[:12]} 失败 {type(exc).__name__}")
                    continue
                body = page.text or ""
                spots = [
                    (href, name)
                    for href, name in links(body, str(page.url))
                    if 2 <= len(name) <= 22
                    and re.search(r"(景区|公园|古城|博物馆|寺|湖|山|湿地|沙漠|胡杨|风情|乐园|草原|峡谷|泉|老街|大巴扎)", name)
                ]
                detail_like = [href for href, _ in spots if re.search(r"(content|art|info|detail|\.shtml|id=)", href)]
                print(
                    f"    ↳ {text[:12]:14s} {page.status_code}｜{len(body):6d} 字符｜"
                    f"景区样式条目 {len(spots)}｜其中像详情页 {len(detail_like)}｜含『门票』{'门票' in body}｜含『A级』{'A级' in body}"
                )
                for href, name in spots[:5]:
                    print(f"         · {name[:18]:20s} {href[:74]}")
                if "门票" in body:
                    spot = body.find("门票")
                    print("         『门票』上下文:", re.sub(r"\s+", " ", body[max(0, spot - 70): spot + 70])[:150])
                sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
