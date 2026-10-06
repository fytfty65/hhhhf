"""探针：为什么有些住宿节点没被供应商查价（intent 判定 / 已有可核实价格）。

用法：cd ai-service && .\\venv\\Scripts\\python.exe tools\\probe_lodging_targets.py ..\\work\\plans\\turbo10-喀什-17.json
"""

from __future__ import annotations

import io
import json
import os
import sys

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVICE_ROOT)

from core.candidate_index import intent_of  # noqa: E402
from core.plan_quality import node_name, nodes_of  # noqa: E402
from core.price_sources import is_verified_source  # noqa: E402


def main() -> int:
    paths = sys.argv[1:] or [os.path.join("..", "work", "plans", "*.json")]
    import glob

    resolved = []
    for pattern in paths:
        resolved.extend(sorted(glob.glob(pattern)))
    for path in resolved:
        payload = json.load(io.open(path, encoding="utf-8"))
        plan = payload.get("final_data") if isinstance(payload.get("final_data"), dict) else payload
        print("=" * 78)
        print(os.path.basename(path))
        for node in nodes_of(plan):
            kind = intent_of(node)
            sources = node.get("data_sources") if isinstance(node.get("data_sources"), dict) else {}
            current = str(sources.get("cost_estimate") or sources.get("cost") or "")
            looks_lodging = kind == "hotel" or any(
                marker in f"{node_name(node)} {node.get('type') or ''}" for marker in ("住宿", "酒店", "民宿", "客栈", "宾馆")
            )
            if not looks_lodging:
                continue
            verified = is_verified_source(current)
            print(
                f"  intent={kind:6s}｜{node_name(node)}｜type={node.get('type')}｜tags={node.get('tags')}"
                f"｜cost={node.get('cost_estimate')}｜source={current or '—'}｜可核实={verified}"
                f"｜{'会查' if not verified else '跳过（已有可核实价格）'}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
