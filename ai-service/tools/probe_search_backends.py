"""搜索后端探针：哪条链路能真的搜到东西（2026-10-07）。

背景：门票价与"真简介"都卡在同一件事上 —— 联网搜索在这个环境里要么被验证码挡（百度）、
要么 0 条（搜狗 / duckduckgo_search）。用户已明确"允许采用其他数据源"，所以这里先把
**可用的搜索链路**探清楚，再决定接哪条（不猜、不硬编码一个查不到的源）。

测的四条：
  ① ddgs（duckduckgo_search 的新包名，官方说旧的已废弃）
  ② 360 搜索（www.so.com/s）
  ③ Bing（cn.bing.com/search）
  ④ 搜狗移动（m.sogou.com/web）
每条都记：HTTP 状态、能解析出几条、有没有出现 .gov.cn / 官网域名。
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from typing import Any, Dict, List

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVICE_ROOT)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

QUERIES = [
    "英吉沙国家湿地公园 门票 价格",
    "喀什博物馆 门票 价格 官网",
]


def _hosts(html: str) -> List[str]:
    found = re.findall(r"https?://([^/\"'\s<>]+)", html or "")
    hosts: List[str] = []
    for host in found:
        host = host.split(":")[0].lower()
        if host and host not in hosts:
            hosts.append(host)
    return hosts


async def probe_ddgs(query: str) -> Dict[str, Any]:
    try:
        from ddgs import DDGS  # 新包名
    except Exception as exc:
        return {"backend": "ddgs", "error": f"导入失败：{exc}"}
    try:
        rows = await asyncio.to_thread(lambda: list(DDGS(timeout=8).text(query, max_results=5)))
    except Exception as exc:
        return {"backend": "ddgs", "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    return {
        "backend": "ddgs",
        "rows": len(rows or []),
        "sample": [(str(r.get("title"))[:30], str(r.get("href") or r.get("url"))[:60]) for r in (rows or [])[:3]],
        "official": [str(r.get("href") or "") for r in (rows or []) if ".gov.cn" in str(r.get("href") or "")][:3],
    }


async def probe_html(backend: str, url: str, query: str, pattern: str) -> Dict[str, Any]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
            resp = await client.get(url, params={"q" if "bing" in url or "so.com" in url else "query": query})
    except Exception as exc:
        return {"backend": backend, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    html = resp.text or ""
    hits = re.findall(pattern, html)
    hosts = _hosts(html)
    return {
        "backend": backend,
        "status": resp.status_code,
        "chars": len(html),
        "hits": len(hits),
        "sample": [str(item)[:50] for item in hits[:3]],
        "official_hosts": [h for h in hosts if h.endswith(".gov.cn")][:3],
        "captcha": any(marker in html for marker in ("安全验证", "请输入验证码", "verify", "captcha")),
    }


async def main() -> int:
    for query in QUERIES:
        print(f"\n=== {query}")
        result = await probe_ddgs(query)
        print(" ", result)
        for backend, url, pattern in (
            ("360", "https://www.so.com/s", r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\""),
            ("bing", "https://cn.bing.com/search", r"<h2><a href=\"([^\"]+)\""),
            ("sogou-m", "https://m.sogou.com/web", r"href=\"(/web/search[^\"]+|https?://[^\"]+)\""),
        ):
            outcome = await probe_html(backend, url, query, pattern)
            print(" ", outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
