r"""探针：门票价能不能从**搜索摘要 / 票务平台**拿到（2026-10-07，用户已允许其他数据源）。

为什么单独探这个：门票价是方案里最后一块大面积"未核实"（喀什 15 个节点里 11 个没价）。
网页直抓已实测走不通（携程/去哪儿/马蜂窝都是 JS 壳或反爬、百度百科 403、gov 站证书不匹配），
所以先看**搜索摘要**里有没有数字 —— 这是唯一还没试过、又确定可达的通道。

用法：cd ai-service && python tools/probe_ticket_prices.py [--plan work/plans/turbo31-喀什-17.json]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent
sys.path.insert(0, str(SERVICE_ROOT))

from core.web_price_tool import extract_price_candidates  # noqa: E402

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

TICKET_PLATFORM_MARKERS = ("ctrip.com", "qunar.com", "ly.com", "tuniu.com", "mafengwo.cn", "piao.")


def _is_ticket_platform(url: str) -> bool:
    host = str(url or "").split("//", 1)[-1].split("/", 1)[0].lower()
    return any(marker in host for marker in TICKET_PLATFORM_MARKERS)


async def ddgs_rows(query: str, limit: int = 6) -> List[Dict[str, Any]]:
    """走**应用同一套**搜索链（ddgs → bing → …），而不是探针自己另起一套。"""
    from core.web_price_tool import search_texts

    try:
        rows = await search_texts(query, max_results=limit)
    except Exception:
        return []
    return [row for row in (rows or []) if isinstance(row, dict)]


def _name_mentioned(blob: str, name: str) -> bool:
    """摘要必须**真的在讲这个地点**（否则会拿到别处的门票价：实测「胡杨书院」被塞了金湖杨景区的 40 元）。"""
    core = name
    for opener, closer in (("(", ")"), ("（", "）")):
        if opener in core and closer in core:
            core = core.split(opener)[0].strip()
    return bool(core) and core in blob


async def main() -> int:
    parser = argparse.ArgumentParser(description="门票价可达性探针")
    parser.add_argument("--plan", default=str(REPO_ROOT / "work" / "plans" / "turbo31-喀什-17.json"))
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args()

    plan: Dict[str, Any] = json.load(io.open(args.plan, encoding="utf-8"))
    if isinstance(plan.get("final_data"), dict):
        plan = plan["final_data"]
    route = [n for n in (plan.get("route") or []) if isinstance(n, dict)]
    city = str(((plan.get("facts") or {}).get("request") or {}).get("city", {}).get("value") or "喀什")

    got = 0
    tested = 0
    for node in route[: args.limit]:
        if node.get("is_hotel"):
            continue
        name = str(node.get("name") or "")
        current = str(node.get("cost_estimate") or "")
        tested += 1
        rows = await ddgs_rows(f"{city} {name} 门票 价格")
        prices: List[Dict[str, Any]] = []
        rejected: List[str] = []
        platform_hits = 0
        for row in rows:
            url = str(row.get("href") or row.get("url") or "")
            if _is_ticket_platform(url):
                platform_hits += 1
            blob = f"{row.get('title') or ''} {row.get('body') or ''} {url}"
            candidates = extract_price_candidates(f"{row.get('title') or ''} {row.get('body') or ''}")
            if not candidates:
                continue
            if not _name_mentioned(blob, name):
                # 摘要没提这个地点 → 价格很可能是别处的（实测：胡杨书院 ≠ 金湖杨景区 40 元）
                rejected.append(f"{candidates[0]['value']}（摘要没提这个地点，来源 {url[:50]}）")
                continue
            prices.append({**candidates[0], "url": url})
        hit = prices[0] if prices else None
        if hit:
            got += 1
        print(
            f"\n· {name}｜现有: {current}"
            f"｜摘要 {len(rows)} 条（票务平台 {platform_hits}）"
            f"｜可用价 {len(prices)} 个｜拦截 {len(rejected)} 个"
        )
        if hit:
            print(f"   → {hit['value']} 元｜来源 {str(hit.get('url'))[:70]}｜原文「{str(hit.get('excerpt'))[:50]}」")
        for item in rejected[:2]:
            print(f"   ✋ 拦掉：{item}")
        if not hit and rows and not rejected:
            print(f"   → 摘要里没数字；示例「{str(rows[0].get('body'))[:70]}」")
    print(f"\n汇总：{got}/{tested} 个非住宿节点能从搜索摘要拿到门票价")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
