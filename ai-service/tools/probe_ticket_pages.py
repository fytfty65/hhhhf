"""探针：票务平台页面/官方页面能不能抓到**门票价**（2026-10-07）。

用户已允许"其他数据源"，所以先测清楚：哪些票务平台的页面**能直抓**、能抓到价。
测：去哪儿门票详情页、携程景点页、马蜂窝、以及一个 gov.cn 页面（对照）。
只报事实：HTTP 状态、正文字数、抽到的价格候选。
"""

import asyncio
import os
import re
import sys

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVICE_ROOT)

from core.web_price_tool import extract_price_candidates  # noqa: E402

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

URLS = [
    "https://piao.qunar.com/ticket/detail_195708.html",
    "https://you.ctrip.com/sight/yengisarcounty1446385/145199098.html",
    "https://www.mafengwo.cn/poi/5424525.html",
    "https://www.xjks.gov.cn/",
    "https://baike.baidu.com/item/新疆英吉沙国家湿地公园/16714806",
]


async def main() -> int:
    import httpx

    for url in URLS:
        try:
            async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
                resp = await client.get(url)
            html = resp.text or ""
        except Exception as exc:
            print(f"\n· {url}\n  抓取失败：{type(exc).__name__}: {str(exc)[:120]}")
            continue
        text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        prices = extract_price_candidates(text[:20000])
        print(f"\n· {url}")
        print(f"  状态 {resp.status_code}｜原始 {len(html)} 字｜去标签 {len(text)} 字")
        print(f"  价格候选 {len(prices)}：{[(p['value'], str(p.get('excerpt'))[:40]) for p in prices[:3]]}")
        for marker in ("门票", "价格", "预订", "安全验证", "滑动验证"):
            if marker in text:
                index = text.find(marker)
                print(f"  「{marker}」→ …{text[max(0, index-30):index+50]}…")
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
