"""国内搜索源可用性探针：逐个试，用**真实页面**判断哪个能拿到本查询的结果。

实测背景（2026-10-04）：
  - DuckDuckGo / bing.com：本机直连与经代理都失败（SSL EOF / ConnectError）；
  - cn.bing.com 直连 HTTP 200，但**不带会话 cookie 时结果是填充推荐**（查询词只出现在标题里）；
  - 所以必须逐个验证：带 cookie 的 bing、搜狗、百度 到底能不能给出本查询的真实结果。

判据（不猜）：把结果块里**含查询词**的比例打出来 —— 全是 0 就说明这个源不可用。

用法：python tools/probe_search_sources.py "库尔勒 博斯腾湖 门票 价格"
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

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _clean(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment or "")).strip()


def _blocks_bing(body: str) -> list[str]:
    return re.findall(r'<li class="b_algo".*?</li>', body or "", flags=re.S)


def _blocks_generic(body: str) -> list[str]:
    """搜狗/百度这类：用 <div class="vrwrap"> / <div class="result"> 做粗粒度分块。"""
    parts = re.split(r'(?=<div[^>]+class="(?:vrwrap|result|rb|c-container)[^"]*")', body or "")
    return [part for part in parts if len(part) > 200]


async def describe(label: str, response, body: str, query: str, blocker) -> dict:
    words = [word for word in query.split() if word]
    blocks = blocker(body)
    hit = [block for block in blocks if any(word in block for word in words)]
    title_match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.S)
    print(f"--- {label} ---")
    print(f"  HTTP {getattr(response, 'status_code', '?')} | {len(body)} 字节 | 标题 {_clean(title_match.group(1))[:50] if title_match else '(无)'}")
    print(f"  结果块 {len(blocks)} 个，含查询词 {len(hit)} 个")
    for block in hit[:3]:
        text = _clean(block)
        print(f"    · {text[:120]}")
    return {"blocks": len(blocks), "relevant": len(hit)}


async def main() -> int:
    import httpx

    query = sys.argv[1] if len(sys.argv) > 1 else "库尔勒 博斯腾湖 门票 价格"
    print(f"查询: {query}\n")
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers={"User-Agent": UA}) as client:
        # ① 带会话的 Bing CN：先访问首页拿 cookie，再搜（并显式指定市场）
        try:
            await client.get("https://cn.bing.com/")
            response = await client.get(
                "https://cn.bing.com/search", params={"q": query, "mkt": "zh-CN", "ensearch": "0"}
            )
            await describe("cn.bing.com（带会话）", response, response.text, query, _blocks_bing)
        except Exception as exc:
            print(f"--- cn.bing.com（带会话）---\n  ❌ {type(exc).__name__}: {str(exc)[:90]}")

        # ② 搜狗
        try:
            await client.get("https://www.sogou.com/")
            response = await client.get("https://www.sogou.com/web", params={"query": query})
            await describe("sogou.com", response, response.text, query, _blocks_generic)
        except Exception as exc:
            print(f"--- sogou.com ---\n  ❌ {type(exc).__name__}: {str(exc)[:90]}")

        # ③ 百度
        try:
            await client.get("https://www.baidu.com/")
            response = await client.get("https://www.baidu.com/s", params={"wd": query})
            await describe("baidu.com", response, response.text, query, _blocks_generic)
        except Exception as exc:
            print(f"--- baidu.com ---\n  ❌ {type(exc).__name__}: {str(exc)[:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
