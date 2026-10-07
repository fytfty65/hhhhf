r"""探针：地点简介能不能拿到**可核实**的来源（2026-10-05）。

背景：并排对照抓到地点简介是编的（YouTube 帮助中心 / "中医认为酸奶有益健康" / 自编套话），
已改成"能核实才写，否则如实说暂无简介"。于是问题变成：**到底能不能拿到可核实的简介？**
用户口径（2026-10-05 裁决）：白名单里的 gov / 景区官网页面算可核实来源。
本探针就测三件事，全部落成证据（不猜）：
  ① 搜索引擎（配置好的那条链路 `search_texts`）还通不通；
  ② 结果里有没有**白名单域名**（gov/官网）的链接；
  ③ 白名单页面能不能真抓到正文，正文是不是在讲这个地点（用同一套相关性判据）。

用法：cd ai-service && python tools/probe_web_desc.py [--plan work/plans/turbo29-喀什-17.json] [--limit 5]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent
sys.path.insert(0, str(SERVICE_ROOT))

from core.place_quality import clean_web_knowledge, desc_relevance_problem  # noqa: E402
from core.web_price_tool import (  # noqa: E402
    fetch_page_text_stable,
    host_is_allowed,
    official_domain_hints,
    provider_states,
    search_texts,
)


async def probe_node(city: str, name: str) -> Dict[str, Any]:
    query = f"{city} {name} 简介 介绍 官网"
    report: Dict[str, Any] = {"name": name, "query": query, "rows": 0, "allowed": [], "fetched": [], "verdict": ""}
    try:
        rows = await asyncio.wait_for(search_texts(query, max_results=5), timeout=20)
    except Exception as exc:
        report["verdict"] = f"搜索失败：{type(exc).__name__}: {str(exc)[:80]}"
        return report
    report["rows"] = len(rows or [])
    hints = official_domain_hints(rows or [], query_name=name)
    report["hints"] = [str(item.get("host") or item.get("url") or "") for item in hints]
    allowed: List[str] = []
    for row in rows or []:
        url = str(row.get("url") or row.get("href") or "")
        ok, why = host_is_allowed(url)
        if url and ok and url not in allowed:
            allowed.append(url)
    report["allowed"] = allowed
    # 摘要本身（非白名单）能不能用：只做对照，按口径**不采信**
    for row in rows or []:
        body = str(row.get("body") or "")
        if body:
            report["snippet_sample"] = body[:90]
            report["snippet_relevance"] = desc_relevance_problem(body, name, city) or "相关"
            break
    for url in allowed[:2]:
        page = await fetch_page_text_stable(url)
        text = str(page.get("text") or "")
        item = {
            "url": url,
            "ok": bool(page.get("ok")),
            "chars": len(text),
            "problem": desc_relevance_problem(text[:2000], name, city) if text else "没抓到正文",
            "usable": bool(clean_web_knowledge(text[:600], name, city)),
            "head": text[:90],
        }
        report["fetched"].append(item)
    if any(item["usable"] for item in report["fetched"]):
        report["verdict"] = "✅ 白名单页面可用"
    elif allowed:
        report["verdict"] = "白名单有链接但正文不可用"
    else:
        report["verdict"] = "没有白名单链接"
    return report


async def main() -> int:
    parser = argparse.ArgumentParser(description="地点简介可核实来源探针")
    parser.add_argument("--plan", default=str(REPO_ROOT / "work" / "plans" / "turbo29-喀什-17.json"))
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    plan: Dict[str, Any] = json.load(io.open(args.plan, encoding="utf-8"))
    if isinstance(plan.get("final_data"), dict):
        plan = plan["final_data"]
    route = [n for n in (plan.get("route") or []) if isinstance(n, dict)]
    city = str(((plan.get("facts") or {}).get("request") or {}).get("city", {}).get("value") or "喀什")

    print(f"方案 {Path(args.plan).name}｜城市 {city}｜节点 {len(route)}｜只测前 {args.limit} 个")
    print("搜索提供方状态:", json.dumps(provider_states(), ensure_ascii=False)[:300])
    results = []
    for node in route[: args.limit]:
        item = await probe_node(city, str(node.get("name") or ""))
        results.append(item)
        print(f"\n· {item['name']}｜{item['verdict']}｜搜索结果 {item['rows']} 条")
        print(f"  白名单链接: {item['allowed'] or '无'}")
        if item.get("hints"):
            print(f"  像官网但未白名单: {item['hints']}")
        if item.get("snippet_sample"):
            print(f"  摘要（不采信，仅对照）: {item['snippet_sample']!r} → {item.get('snippet_relevance')}")
        for fetched in item["fetched"]:
            print(f"  抓取 {fetched['url'][:70]}｜ok={fetched['ok']}｜{fetched['chars']} 字｜{fetched['problem']}｜可用={fetched['usable']}")
    usable = sum(1 for item in results if item["verdict"].startswith("✅"))
    print(f"\n汇总：{usable}/{len(results)} 个地点拿到了可核实正文")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
