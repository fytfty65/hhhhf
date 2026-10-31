"""RollingGo MCP 只读探针：把"供应商到底能不能用"从猜变成事实（2026-10-05）。

背景：`gateway/.env` 里配了 `ROLLINGGO_API_URL=https://mcp.rollinggo.cn/mcp` +
`ROLLINGGO_API_KEY=mcp_…`，网关侧也有一整套 MCP 客户端（initialize → notifications/initialized
→ tools/list → 调用只读工具）。但"配了"不等于"通了"：本探针只做**只读**调用，
回答三个问题：
  1. 握手与 tools/list 通不通（HTTP 状态、协议版本、会话头）；
  2. 这台服务器声明了哪些工具（名字 + 必填参数），有没有**价格类只读工具**；
  3. 真调一次价格类工具能不能拿到结果（字段名 + 条数 + 是否含价格字段）。

安全边界（与 docs/provider-availability.md 一致）：
  - **只读**：只调 `tools/list` 与用户在本脚本里显式指定的只读工具；
  - 绝不打印 token（只打印前 6 位用于确认"用的是哪把钥匙"）；
  - 不落盘原始响应，只打印字段名与计数。

用法：
    cd ai-service && .\\venv\\Scripts\\python.exe tools\\probe_mcp_provider.py
    $env:MCP_CALL_TOOL='searchScenic'; $env:MCP_CALL_ARGS='{"city":"库尔勒"}'; python tools/probe_mcp_provider.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.console import ensure_utf8_console  # noqa: E402

ensure_utf8_console()

ENV_PATHS = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "gateway", ".env"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
]
PROTOCOL_VERSION = "2025-06-18"


def load_env() -> Dict[str, str]:
    """从 gateway/.env 与 ai-service/.env 里读供应商配置（不覆盖已有环境变量）。"""
    values: Dict[str, str] = {}
    for path in ENV_PATHS:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                text = line.strip()
                if not text or text.startswith("#") or "=" not in text:
                    continue
                key, _, value = text.partition("=")
                key, value = key.strip(), value.strip()
                if key and value:
                    values.setdefault(key, value)
    return values


def decode_body(raw: bytes, content_type: str) -> Optional[Dict[str, Any]]:
    """MCP Streamable HTTP 可能回 JSON，也可能回 SSE（`data: {...}`）。两种都要认。"""
    text = raw.decode("utf-8", errors="ignore").strip()
    if not text:
        return None
    if "text/event-stream" in (content_type or "").lower() or text.startswith("event:") or text.startswith("data:"):
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = line[len("data:") :].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    continue
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def field_names(value: Any, prefix: str = "", depth: int = 0, out: Optional[List[str]] = None) -> List[str]:
    """收集一个 JSON 结果里的字段名（不收集值）：用来判断"有没有价格字段"。"""
    out = out if out is not None else []
    if depth > 3:
        return out
    if isinstance(value, dict):
        for key, item in value.items():
            out.append(f"{prefix}{key}")
            field_names(item, f"{prefix}{key}.", depth + 1, out)
    elif isinstance(value, list) and value:
        field_names(value[0], f"{prefix}[].", depth + 1, out)
    return out


async def main() -> int:
    import httpx

    env = load_env()
    endpoint = os.getenv("ROLLINGGO_API_URL") or env.get("ROLLINGGO_API_URL") or ""
    token = os.getenv("ROLLINGGO_API_KEY") or env.get("ROLLINGGO_API_KEY") or ""
    auth_header = os.getenv("ROLLINGGO_AUTH_HEADER") or env.get("ROLLINGGO_AUTH_HEADER") or "Authorization"
    token_url = os.getenv("ROLLINGGO_API_TOKEN") or env.get("ROLLINGGO_API_TOKEN") or ""
    protocol = os.getenv("ROLLINGGO_PROVIDER_PROTOCOL") or env.get("ROLLINGGO_PROVIDER_PROTOCOL") or ""

    print("=" * 78)
    print("RollingGo MCP 只读探针")
    print("=" * 78)
    print(f"endpoint : {endpoint or '（未配置）'}")
    print(f"token    : {(token[:6] + '…') if token else '（未配置）'}（长度 {len(token)}）")
    print(f"token_url: {'已配置' if token_url else '未配置'}")
    print(f"protocol : {protocol or '（未声明，网关按 mcp 处理）'}")
    if not endpoint or not token:
        print("❌ 没配 endpoint/token，供应商保持 not_configured（这是如实状态，不是故障）")
        return 1

    session = ""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
    }
    if auth_header.lower() == "authorization" and not token.lower().startswith("bearer "):
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers[auth_header] = token

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        async def call(method: str, params: Any, *, notify: bool = False, request_id: int = 1) -> Dict[str, Any]:
            nonlocal session
            body: Dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
            if not notify:
                body["id"] = request_id
            send_headers = dict(headers)
            if session:
                send_headers["Mcp-Session-Id"] = session
            response = await client.post(endpoint, json=body, headers=send_headers)
            if response.headers.get("Mcp-Session-Id"):
                session = response.headers["Mcp-Session-Id"]
            decoded = decode_body(response.content, response.headers.get("Content-Type", ""))
            return {
                "status": response.status_code,
                "content_type": response.headers.get("Content-Type", ""),
                "session": bool(session),
                "bytes": len(response.content),
                "body": decoded,
            }

        print("\n--- ① initialize ---")
        init = await call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "omniroute-probe", "version": "2026-10-05"},
            },
        )
        print(f"HTTP {init['status']}｜{init['content_type'][:40]}｜{init['bytes']}B｜拿到会话头={init['session']}")
        result = (init.get("body") or {}).get("result") or {}
        server_info = result.get("serverInfo") or {}
        if server_info:
            print(f"  服务端：{server_info.get('name')} {server_info.get('version')}")
        if init["status"] != 200 or not result:
            print("❌ 握手失败，后续不再尝试（如实报告，不重试轰炸对方）")
            print("   body:", json.dumps(init.get("body"), ensure_ascii=False)[:300])
            return 1

        print("\n--- ② notifications/initialized ---")
        note = await call("notifications/initialized", {}, notify=True)
        print(f"HTTP {note['status']}（通知无返回体属正常）")

        print("\n--- ③ tools/list ---")
        listed = await call("tools/list", {}, request_id=2)
        tools = ((listed.get("body") or {}).get("result") or {}).get("tools") or []
        print(f"HTTP {listed['status']}｜声明工具 {len(tools)} 个")
        price_tools: List[str] = []
        for tool in tools:
            name = str(tool.get("name") or "")
            description = str(tool.get("description") or "").replace("\n", " ")[:80]
            schema = tool.get("inputSchema") or {}
            required = list(schema.get("required") or [])
            properties = list((schema.get("properties") or {}).keys())
            mark = ""
            text = f"{name} {description}".lower()
            if any(key in text for key in ("price", "价格", "票价", "房价", "门票", "hotel", "ticket")):
                mark = "  💰"
                price_tools.append(name)
            print(f"  · {name}{mark}｜必填 {required or '无'}｜字段 {properties[:6]}")
            if description:
                print(f"      {description}")
        print(f"\n价格相关只读工具：{price_tools or '（未发现）'}")

        call_tool = os.getenv("MCP_CALL_TOOL") or ""
        if not call_tool:
            print("\n（只做只读盘点。要真调一次：设 MCP_CALL_TOOL / MCP_CALL_ARGS 后再跑一次）")
            return 0

        print(f"\n--- ④ 调用只读工具 {call_tool} ---")
        try:
            args = json.loads(os.getenv("MCP_CALL_ARGS") or "{}")
        except json.JSONDecodeError as exc:
            print(f"❌ MCP_CALL_ARGS 不是合法 JSON：{exc}")
            return 1
        called = await call("tools/call", {"name": call_tool, "arguments": args}, request_id=3)
        payload = (called.get("body") or {}).get("result") or {}
        content = payload.get("content") or []
        print(f"HTTP {called['status']}｜isError={payload.get('isError')}｜content 段 {len(content)} 个")
        texts = [str(item.get("text") or "") for item in content if isinstance(item, dict)]
        for text in texts[:2]:
            print("  文本片段:", text[:200].replace("\n", " "))
        parsed: Any = None
        for text in texts:
            try:
                parsed = json.loads(text)
                break
            except json.JSONDecodeError:
                continue
        if parsed is None:
            print("  （返回不是 JSON，或为空 —— 如实记录，不做推断）")
            return 0
        names = sorted(set(field_names(parsed)))
        print(f"  字段名（{len(names)} 个）: {names[:30]}")
        rows = parsed if isinstance(parsed, list) else (parsed.get("data") or parsed.get("items") or parsed.get("list") or [])
        if isinstance(rows, list):
            print(f"  条目数: {len(rows)}")
        price_fields = [name for name in names if any(k in name.lower() for k in ("price", "amount", "fee", "cost", "价格", "票价"))]
        print(f"  价格字段: {price_fields or '（没有价格字段）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
