r"""直接对 ai-service 跑一次规划并落盘（用于**模型并排对照**，2026-10-05）。

为什么要它：`scripts/probe-plan-matrix.mjs` 走网关（注册/登录/WS），一次只能对着**一个**
ai-service 跑；而"qwen-plus vs qwen-turbo 并排读"需要两个模型各跑一遍。
网关只指向 8000，所以想同时保留线上那份 turbo，就要能**直接**打另一个端口的 ai-service。

用法：
    cd ai-service
    # 另一个实例：$env:AI_SERVICE_PORT='8001'; $env:LLM_MODEL_NAME='qwen-plus'; python main.py
    python tools/probe_direct_plan.py --base http://127.0.0.1:8001 --tag plus \
        --city 喀什 --intent "去喀什玩3天，想多看人文历史和古城"

落盘：`work/plans/<tag>-<city>-<诉求字数>.json`（与矩阵探针同名规则，尺子/抽查表直接读）。
只读不写业务代码；没拿到 `final_route` 就如实报错，不编方案。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent
sys.path.insert(0, str(SERVICE_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(SERVICE_ROOT / ".env")
except Exception:  # pragma: no cover - 没有 dotenv 也能跑（token 由外部提供）
    pass


def _post_stream(base: str, body: Dict[str, Any], token: str, timeout: float) -> str:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{base.rstrip('/')}/api/v1/agent/negotiate",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Omni-Internal-Token": token,
            "Accept": "application/x-ndjson",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:  # 让 4xx/5xx 的正文能看见（否则只看到"400"）
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:400]}") from exc


def run(base: str, city: str, intent: str, tag: str, timeout: float, role: str, mode: str) -> Optional[Dict[str, Any]]:
    token = os.getenv("AI_SERVICE_INTERNAL_TOKEN", "").strip()
    if not token:
        print("⚠️ 没有 AI_SERVICE_INTERNAL_TOKEN，ai-service 会拒（除非显式开了匿名）")
    body = {
        "type": "agent_negotiate",
        "room_id": f"probe-{tag}",
        "user_id": f"probe-{tag}",
        "payload": {
            "user_preferences": {
                "intent": intent,
                "mode": mode,
                "role": role,
                "history_sequence": [],
                "current_existing_route": [],
            }
        },
    }
    started = time.monotonic()
    raw = _post_stream(base, body, token, timeout)
    elapsed = time.monotonic() - started

    final: Optional[Dict[str, Any]] = None
    stages: Dict[str, Any] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "final_route" and isinstance(event.get("payload"), dict):
            final = event["payload"]
        elif kind == "budget_breakdown":
            stages["budget_breakdown"] = event.get("payload")
        elif "分段耗时" in str(event.get("token") or ""):
            stages["stage_summary"] = str(event.get("token"))[:400]
    if final is None:
        print("❌ 没拿到 final_route（不编方案）")
        print(raw[:800])
        return None

    plans_dir = REPO_ROOT / "work" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    out = plans_dir / f"{tag}-{city}-{len(intent)}.json"
    with io.open(out, "w", encoding="utf-8") as handle:
        json.dump(final, handle, ensure_ascii=False, indent=2)

    route = final.get("route") or []
    quality = final.get("quality") or {}
    print(f"✅ {out.name}｜{elapsed:.1f}s｜节点 {len(route)}｜自报分数 {quality.get('score')}")
    for token_text in (stages.get("stage_summary"),):
        if token_text:
            print("   " + token_text.replace("\n", " ")[:300])
    return final


def main() -> int:
    parser = argparse.ArgumentParser(description="直接对 ai-service 跑一次规划并落盘（模型并排对照用）")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--city", required=True)
    parser.add_argument("--intent", required=True)
    parser.add_argument("--tag", default="direct")
    parser.add_argument("--role", default="休闲漫步")
    parser.add_argument("--mode", default="coop")
    parser.add_argument("--timeout", type=float, default=420.0)
    args = parser.parse_args()
    try:
        final = run(args.base, args.city, args.intent, args.tag, args.timeout, args.role, args.mode)
    except Exception as exc:
        print(f"❌ 跑失败：{exc}")
        return 1
    return 0 if final else 1


if __name__ == "__main__":
    raise SystemExit(main())
