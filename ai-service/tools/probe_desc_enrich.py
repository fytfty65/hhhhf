r"""探针：POI 简介的联网富化到底拿回了什么（2026-10-05）。

背景（并排对照 plus/turbo 时抓到的**用户可见的编造**）：
turbo 跑出的喀什方案里，「胡杨书院」的 `desc` 是
`Official YouTube TV Help Center where you can find tips and tutorials ...` ——
一段跟喀什毫无关系的英文帮助页文字，被当成这个地点的简介渲染给用户。

来源怀疑：`ExpertToolbox.enrich_poi_with_web_search()`（DuckDuckGo 取第一条 `body`，不校验相关性）。
本探针用**同一个查询**复现，并打印"看起来相不相关"，为修法提供证据（不猜）。

用法：cd ai-service && python tools/probe_desc_enrich.py [--plan work/plans/turbo28-喀什-17.json]
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent
sys.path.insert(0, str(SERVICE_ROOT))

from core.console import ensure_utf8_console  # noqa: E402

ensure_utf8_console()

try:
    from duckduckgo_search import DDGS
except Exception as exc:  # pragma: no cover
    print(f"⚠️ 没有 duckduckgo_search：{exc}")
    DDGS = None  # type: ignore


def looks_relevant(text: str, city: str, poi_name: str) -> bool:
    """最小相关性判据（用于对照修法）：提到地点名/城市名，且不是一眼无关的英文帮助页。"""
    lowered = str(text or "").lower()
    if not lowered:
        return False
    has_name = poi_name and poi_name in text
    has_city = city and city in text
    chinese_ratio = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff") / max(1, len(text))
    junk_markers = ("help center", "youtube", "tutorial", "cookie", "sign in", "privacy policy")
    if any(marker in lowered for marker in junk_markers):
        return False
    return bool(has_name or has_city) and chinese_ratio > 0.3


def main() -> int:
    parser = argparse.ArgumentParser(description="POI 简介富化探针")
    parser.add_argument("--plan", default=str(REPO_ROOT / "work" / "plans" / "turbo28-喀什-17.json"))
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()

    plan: Dict[str, Any] = json.load(io.open(args.plan, encoding="utf-8"))
    if isinstance(plan.get("final_data"), dict):  # 兼容 {"final_data": {...}} 落盘形态
        plan = plan["final_data"]
    route: List[Dict[str, Any]] = [n for n in (plan.get("route") or []) if isinstance(n, dict)]
    city = str((plan.get("facts") or {}).get("request", {}).get("city", {}).get("value") or "喀什")

    print(f"方案 {Path(args.plan).name}｜城市 {city}｜节点 {len(route)}｜只测前 {args.limit} 个")
    if DDGS is None:
        return 1

    for node in route[: args.limit]:
        name = str(node.get("name") or "")
        current = str(node.get("desc") or "")
        query = f"{city} {name} 招牌特色 历史背景 游玩攻略"
        fetched = ""
        error = ""
        try:
            with DDGS(timeout=3) as ddgs:
                results = list(ddgs.text(query, max_results=1))
            if results:
                fetched = str(results[0].get("body") or "")
        except Exception as exc:
            error = str(exc)[:120]
        verdict = "相关" if looks_relevant(fetched, city, name) else "**不相关**"
        print(f"\n· {name}")
        print(f"  现在方案里的 desc: {current[:80]!r}")
        print(f"  这次取回: {fetched[:120]!r}{('  ⚠️ ' + error) if error else ''}")
        print(f"  判定: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
