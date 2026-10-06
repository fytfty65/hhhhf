"""一次性小探针：验证 RollingGo MCP 是否**无状态**（不 initialize 直接 tools/call）。

用法：cd ai-service && .\\venv\\Scripts\\python.exe tools\\probe_mcp_stateless.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_mcp_provider import PROTOCOL_VERSION, decode_body, load_env  # noqa: E402


async def main() -> int:
    import httpx

    env = load_env()
    endpoint = env.get("ROLLINGGO_API_URL", "")
    token = env.get("ROLLINGGO_API_KEY", "")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "Authorization": f"Bearer {token}",
    }
    args = {
        "originQuery": "库尔勒住宿 1 晚",
        "place": "库尔勒 中国",
        "placeType": "城市",
        "checkInParam": {"checkInDate": "2026-10-10", "stayNights": 1, "adultCount": 2},
        "size": 1,
    }
    async with httpx.AsyncClient(timeout=40.0) as client:
        started = time.monotonic()
        response = await client.post(
            endpoint,
            json={"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "searchHotels", "arguments": args}},
            headers=headers,
        )
        elapsed = time.monotonic() - started
        body = decode_body(response.content, response.headers.get("Content-Type", "")) or {}
        result = body.get("result") or {}
        print(
            f"不 initialize 直呼 tools/call：HTTP {response.status_code}｜{elapsed:.2f}s｜"
            f"isError={result.get('isError')}｜error={body.get('error')}"
        )
        content = result.get("content") or []
        if content:
            print("  片段:", str(content[0].get("text"))[:180].replace("\n", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
