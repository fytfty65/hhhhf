"""Bing CN 搜索结果探针：验证"国内直连可达的搜索源"能不能拿到价格线索。

背景（2026-10-04 实测）：
  - DuckDuckGo（含回退 bing.com）在本机**直连与代理都失败**（SSL EOF / ConnectError）；
  - 但 `cn.bing.com` **直连可用**（HTTP 200，约 100KB，2.5s）；
  - 所以联网取价要在国内环境下工作，必须有一个"国内可达"的搜索提供方。

这个探针把一个真实结果页解析成 (标题, URL, 摘要)，供：
  1. 确认解析规则对**真实 HTML** 有效（而不是对我编的样本有效）；
  2. 抽取其中的价格候选（"门票45元"这类），验证取价链路端到端可用。

用法：python tools/probe_bing_cn.py "库尔勒 博斯腾湖 门票 价格"
"""

from __future__ import annotations

import asyncio
import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.console import ensure_utf8_console  # noqa: E402

ensure_utf8_console()

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from core.web_price_tool import extract_price_candidates  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _clean(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment or "")).strip()


def parse_bing_cn(body: str, limit: int = 8) -> list[dict]:
    """从 Bing CN 结果页解析 (标题, URL, 摘要)。"""
    rows: list[dict] = []
    for block in re.findall(r'<li class="b_algo".*?</li>', body or "", flags=re.S)[:limit]:
        title_match = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S)
        snippet_match = re.search(r"<p[^>]*>(.*?)</p>", block, flags=re.S)
        if not title_match:
            continue
        rows.append({
            "title": _clean(title_match.group(2))[:120],
            "url": html.unescape(title_match.group(1)),
            "snippet": _clean(snippet_match.group(1))[:200] if snippet_match else "",
        })
    return rows


async def main() -> int:
    import httpx

    query = sys.argv[1] if len(sys.argv) > 1 else "库尔勒 博斯腾湖 门票 价格"
    url = "https://cn.bing.com/search"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers={"User-Agent": UA}) as client:
            response = await client.get(url, params={"q": query})
    except Exception as exc:
        print(f"❌ 请求失败: {type(exc).__name__}: {exc}")
        return 1
    print(f"HTTP {response.status_code} | 页面 {len(response.text)} 字节 | 查询: {query}")
    body = response.text
    title_match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.S)
    print(f"页面标题: {_clean(title_match.group(1))[:80] if title_match else '(无)'}")
    for word in [w for w in query.split() if w]:
        print(f"  页面中出现「{word}」: {body.count(word)} 次")
    blocks = re.findall(r'<li class="b_algo".*?</li>', body, flags=re.S)
    hit_blocks = [b for b in blocks if any(w in b for w in query.split() if w)]
    print(f"b_algo 块 {len(blocks)} 个，其中含查询词 {len(hit_blocks)} 个")
    print()
    rows = parse_bing_cn(body)
    print(f"解析到 {len(rows)} 条结果\n")
    price_hits = 0
    for row in rows:
        candidates = extract_price_candidates(f"{row['title']} {row['snippet']}")
        marker = ""
        if candidates:
            price_hits += 1
            marker = "  💰 " + ", ".join(f"¥{item['value']:g}（{item['excerpt'][:24]}）" for item in candidates[:2])
        print(f"- {row['title'][:60]}")
        print(f"  {row['url'][:90]}")
        if row["snippet"]:
            print(f"  {row['snippet'][:110]}{marker}")
        elif marker:
            print(f" {marker}")
    print(f"\n含价格候选的结果: {price_hits}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
