"""探针：票务平台自己的**列表页/搜索接口**能不能直接抓到价（2026-10-07）。

背景：详情页是 JS 壳（实测 20 字正文），搜索摘要里也常常没有数字。用户已允许"其他数据源"，
所以再试一层：平台自己的列表/搜索 URL 是不是**服务端渲染**的（历史上去哪儿门票列表页是）。
测：
  · 去哪儿门票搜索：piao.qunar.com/ticket/list.htm?keyword=…
  · 去哪儿景点搜索：touch.go.qunar.com/search?keyword=…
  · 携程攻略搜索：you.ctrip.com/searchsight/…
只报事实：状态、正文字数、抽到的价格候选、有没有提到地点名。
"""

import asyncio
import os
import re
import sys
import urllib.parse

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVICE_ROOT)

from core.web_price_tool import extract_price_candidates  # noqa: E402

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

NAMES = ["喀什博物馆", "英吉沙国家湿地公园", "喀什玉石文化博物馆", "胡杨书院"]


def strip_tags(html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


async def main() -> int:
    import httpx

    async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
        for name in NAMES:
            quoted = urllib.parse.quote(name)
            urls = [
                f"https://piao.qunar.com/ticket/list.htm?keyword={quoted}",
                f"https://touch.go.qunar.com/search?keyword={quoted}",
                f"https://you.ctrip.com/searchsight/{quoted}",
            ]
            print(f"\n=== {name}")
            for url in urls:
                try:
                    resp = await client.get(url)
                except Exception as exc:
                    print(f"  · {url[:70]}\n    抓取失败 {type(exc).__name__}: {str(exc)[:90]}")
                    continue
                text = strip_tags(resp.text or "")
                prices = extract_price_candidates(text[:30000])
                named = name in text
                print(
                    f"  · {url[:70]}\n    状态 {resp.status_code}｜正文 {len(text)} 字"
                    f"｜提到地点名 {named}｜价格候选 {len(prices)}"
                    f"{'：' + str([(p['value'], str(p.get('excerpt'))[:30]) for p in prices[:3]]) if prices else ''}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
