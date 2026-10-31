"""探针：票务平台的**接口/详情页**（比站内搜索页靠谱）—— 2026-10-07。

上一轮结论：平台站内搜索页全是 JS 壳或直接不给内容
（qunar-list 0 字符、ctrip-search 7387 字符无景区名、ly-search 每次都返回同一个 41KB 页面）。
但**详情页**是能用的 —— 实测那条「阿吾赞沟森林公园 ¥110」就来自
`gs.ctrip.com/html5/you/sight/1446400/144508327.htm`（只不过那个 URL 是搜索引擎给的）。

这一轮换两条路：
  ① **建议词/搜索接口**（JSON，通常不需要登录）：拿到详情页 URL；
  ② **已确认存在的详情页**：直连 + 看能不能解析出票价（结构化字段/JSON-LD/文本）。
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

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
QUERY = "阿吾赞沟森林公园"
KNOWN_DETAIL = "https://gs.ctrip.com/html5/you/sight/1446400/144508327.htm"

APIS = [
    ("ctrip-suggest", "https://m.ctrip.com/restapi/h5api/globalsearch/search", {"action": "gsonline", "source": "globalonline", "keyword": "{q}"}),
    ("ctrip-h5-sight", "https://m.ctrip.com/restapi/h5api/searchapp/search", {"action": "sight", "keyword": "{q}"}),
    ("qunar-list-json", "https://piao.qunar.com/ticket/list.json", {"keyword": "{q}", "page": "1"}),
    ("qunar-suggest", "https://piao.qunar.com/ticket/searchSuggest.json", {"keyword": "{q}"}),
    ("lvmama-search", "https://s.lvmama.com/ticket/", {"keyword": "{q}"}),
    ("ly-search-api", "https://www.ly.com/scenery/ajax/SearchScenery", {"keyword": "{q}"}),
    ("tuniu-search", "https://s.tuniu.com/search_complex/whole-sh-{q}/", {}),
]


async def main() -> int:
    import httpx

    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
        print("=== ① 建议词/搜索接口")
        for label, url, params in APIS:
            target = url.format(q=QUERY) if "{q}" in url else url
            query_params = {key: str(value).format(q=QUERY) for key, value in (params or {}).items()}
            try:
                resp = await client.get(target, params=query_params or None)
            except Exception as exc:
                print(f"  {label:16s} 失败 {type(exc).__name__}: {str(exc)[:44]}")
                continue
            text = (resp.text or "").strip()
            hit = QUERY in text or "阿吾赞" in text
            print(f"  {label:16s} {resp.status_code}｜{len(text):7d} 字符｜含景区名 {'是' if hit else '否'}｜{text[:90]}")
            if hit:
                urls = sorted(set(re.findall(r'https?://[^"\'\s<>]{10,90}', text)))
                print("      链接样例:", urls[:4])

        print("\n=== ② 已知详情页能不能解析出票价")
        try:
            resp = await client.get(KNOWN_DETAIL)
        except Exception as exc:
            print(f"  失败 {type(exc).__name__}: {str(exc)[:60]}")
            os._exit(0)
        body = resp.text or ""
        print(f"  {resp.status_code}｜{len(body)} 字符｜最终 {str(resp.url)[:70]}")
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
        print("  含景区名:", QUERY in text, "｜含『门票』:", "门票" in text)
        for pattern in (r"[¥￥]\s*\d+(?:\.\d+)?", r"(?:门票|票价)[^。；<>]{0,24}", r'"price"\s*:\s*"?[\d.]+'):
            hits = re.findall(pattern, text)[:5]
            print(f"  {pattern} → {hits}")
        ids = re.findall(r'"(?:sightId|poiId|productId|price)"\s*:\s*"?([\w.]+)', body)[:6]
        print("  结构化字段线索:", ids)
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
