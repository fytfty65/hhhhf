"""探针：`lookup_description` 在真实网络上能拿到几段官方简介（2026-10-07）。

只做一件事：对方案里的若干地点，调**管线里同一个函数**，看能不能从白名单官方页面
取到一段"在讲这个地点"的简介。拿不到就如实报空（不编）。
用法：cd ai-service && python tools/probe_official_desc.py [--plan work/plans/final1-喀什-17.json]
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import io
import json
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent
sys.path.insert(0, str(SERVICE_ROOT))

from core.console import ensure_utf8_console  # noqa: E402

ensure_utf8_console()

from core.web_price_tool import lookup_description, provider_states  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description="官方页面简介探针")
    parser.add_argument("--plan", default="")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    path = args.plan or sorted(glob.glob(str(REPO_ROOT / "work" / "plans" / "*喀什*.json")))[-1]
    plan = json.load(io.open(path, encoding="utf-8"))
    if isinstance(plan.get("final_data"), dict):
        plan = plan["final_data"]
    route = [n for n in (plan.get("route") or []) if isinstance(n, dict) and not n.get("is_hotel")]
    city = str(((plan.get("facts") or {}).get("request") or {}).get("city", {}).get("value") or "喀什")
    print(f"方案 {Path(path).name}｜城市 {city}｜测前 {args.limit} 个非住宿节点")
    print("搜索源状态:", json.dumps(provider_states(), ensure_ascii=False)[:220])
    found = 0
    for node in route[: args.limit]:
        name = str(node.get("name") or "")
        try:
            result = await asyncio.wait_for(lookup_description(name, city), timeout=45)
        except Exception as exc:
            print(f"\n· {name}\n  ✗ 失败：{type(exc).__name__}: {str(exc)[:90]}")
            continue
        text = str(result.get("text") or "")
        if text:
            found += 1
            print(f"\n· {name}\n  ✅ {text[:120]}\n     来源 {result.get('source_url')}")
        else:
            print(f"\n· {name}\n  — 没取到（如实为空）{('：' + str(result.get('error'))[:80]) if result.get('error') else ''}")
    print(f"\n汇总：{found}/{min(args.limit, len(route))} 个地点拿到官方页面简介")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
