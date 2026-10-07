"""探针：**票务平台**能不能当定向通道用（2026-10-07，用户问"既然票务平台能用为什么不用票务系统"）。

背景：通用搜索已经不行（Bing 返回随机页），但**票务平台自己**是垂直站点 —— 它们的
搜索/详情页如果能直连，那就是一条比通用搜索准得多的定向通道：
景区名 → 平台站内搜索 → 景区详情页 → 页面上的票价。

这个探针逐家测（携程/去哪儿/同程/美团），判据：
  · 能不能直连（状态码、大小）；
  · 页面里**有没有这个景区名**（说明搜索生效，不是首页/JS 壳）；
  · 有没有票价形态（`¥数字`、"起"、"门票"）；
  · 是服务端渲染（HTML 里有内容）还是 JS 壳（要接口）。
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
TARGETS = [
    ("英吉沙国家湿地公园", "喀什"),
    ("阿吾赞沟森林公园", "伊宁"),
    ("图开沙漠冰雪乐园", "伊宁"),
    ("博斯腾湖", "库尔勒"),
    ("喀什古城", "喀什"),
]
# 每家的"站内搜索"入口（先测搜索，再测详情页）
SEARCHES = [
    ("qunar-list", "https://piao.qunar.com/ticket/list.htm?keyword={q}"),
    ("qunar-search", "https://piao.qunar.com/ticket/search.htm?keyword={q}"),
    ("ctrip-search", "https://you.ctrip.com/searchsite/sight/?query={q}"),
    ("ctrip-gs", "https://gs.ctrip.com/html5/you/search/sight?keyword={q}"),
    ("ly-search", "https://www.ly.com/scenery/search?keyword={q}"),
    ("meituan-search", "https://i.meituan.com/s/{q}"),
]


def summarize(label: str, text: str, name: str, status: int) -> None:
    has_name = name in text
    prices = re.findall(r"[¥￥]\s*\d+(?:\.\d+)?", text)[:4]
    ticket_ctx = re.findall(r"[^。；<>]{0,16}(?:门票|票价)[^。；<>]{0,20}", text)[:2]
    print(
        f"    {label:16s} {status}｜{len(text):7d} 字符｜含景区名 {'是' if has_name else '否'}｜"
        f"价格样例 {prices}｜门票语境 {[item.strip()[:34] for item in ticket_ctx]}"
    )


async def main() -> int:
    import httpx

    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
        for name, city in TARGETS:
            print(f"\n=== {city} {name}")
            for label, template in SEARCHES:
                url = template.format(q=name if label != "meituan-search" else name)
                try:
                    resp = await client.get(url)
                except Exception as exc:
                    print(f"    {label:16s} 失败 {type(exc).__name__}: {str(exc)[:44]}")
                    continue
                summarize(label, resp.text or "", name, resp.status_code)
            sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
