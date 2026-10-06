"""RollingGo MCP（**只读**）酒店取价：把"高德不提供房价"这个最大的价格缺口补上。

为什么这一层存在（2026-10-05 实测，`tools/probe_mcp_provider.py`）
--------------------------------------------------------------
用户的约束是"没有商业资质、拿不到官方价格 API"，但**网关 `.env` 里早就配了一个**：
`ROLLINGGO_API_URL=https://mcp.rollinggo.cn/mcp` + `ROLLINGGO_API_KEY=mcp_…`。
配了不等于通了，于是先做只读探针，结果：

- MCP 握手 HTTP 200，服务端自称 `MCP Hotel Server 1.0.0`，声明 3 个只读工具：
  `searchHotels` / `getHotelDetail` / `getHotelSearchTags`；
- `searchHotels` 返回**真实房价**：库尔勒「亚朵酒店 4★ 1晚总价：418CNY」、
  喀什「尚客优酒店(喀什古城店) 2★ 1晚总价：131CNY」，带 `currency` / `lowestPrice` /
  `bookingUrl`（可点开核对）—— 正是 `pending_price_report` 里一直写着"需要你确认"的那一项；
- 服务端**无状态**：不 `initialize` 直接 `tools/call` 也能拿到结果（实测 0.68s/次），
  所以客户端不做会话管理（少两次往返、少一类失败模式）。

口径（与 `docs/provider-availability.md` 一致，逐条都落到代码里）
--------------------------------------------------------------
1. **只读**：只调 `searchHotels` / `getHotelDetail`，绝不下单/取消；
2. 价格标 `rollinggo`：它本来就在 `price_sources.VERIFIED_SOURCES` 与
   `plan_quality.VERIFIED_SOURCES_OK` 里 —— 即"可核实来源"，`estimated` 标记随之取消；
3. **按酒店名匹配**：查不到就是查不到，**绝不拿同城别家的价冒充这家的价**（那是编造）；
4. 报价带入住日期与可点开的 URL；没配置就如实 `not_configured`，不假装查过；
5. 不打印/不落盘 token 与原始响应，只留字段名与计数。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .candidate_index import normalize_text

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_TOOL = "searchHotels"
TOOL_DETAIL = "getHotelDetail"
# 只读白名单：这两个是查询工具；下单/取消类工具（若有）**永远不在这里**
READ_ONLY_TOOLS = (DEFAULT_TOOL, TOOL_DETAIL)

DEFAULT_TIMEOUT_SECONDS = 12.0
DEFAULT_MAX_HOTELS = 5


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or "").strip() or default


def mcp_endpoint() -> str:
    return _env("ROLLINGGO_API_URL")


def mcp_token() -> str:
    return _env("ROLLINGGO_API_KEY") or _env("ROLLINGGO_API_TOKEN")


def mcp_timeout_seconds() -> float:
    try:
        value = float(_env("ROLLINGGO_MCP_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def mcp_max_hotels() -> int:
    try:
        value = int(_env("ROLLINGGO_MCP_MAX_HOTELS", str(DEFAULT_MAX_HOTELS)))
    except ValueError:
        return DEFAULT_MAX_HOTELS
    return max(1, min(20, value))


def mcp_config() -> Dict[str, Any]:
    """供应商配置状态。**没配置也是一种如实状态**（`not_configured`），不是故障。"""
    disabled = _env("ROLLINGGO_MCP_ENABLED", "1").lower() in {"0", "false", "off", "no"}
    endpoint = mcp_endpoint()
    token = mcp_token()
    if disabled:
        return {"enabled": False, "endpoint": endpoint, "token_present": bool(token), "reason": "ROLLINGGO_MCP_ENABLED=0（显式关闭）"}
    if not endpoint:
        return {"enabled": False, "endpoint": "", "token_present": bool(token), "reason": "没配 ROLLINGGO_API_URL"}
    if not token:
        return {"enabled": False, "endpoint": endpoint, "token_present": False, "reason": "没配 ROLLINGGO_API_KEY"}
    return {"enabled": True, "endpoint": endpoint, "token_present": True, "reason": "已配置只读酒店查询"}


# --------------------------------------------------------------------------
# 解析（纯函数：拿真实响应文本做单测，不碰网络）
# --------------------------------------------------------------------------
def hotel_rows(payload: Any) -> List[Dict[str, Any]]:
    """从 `searchHotels` 的结果里取出酒店列表（真实键名是 `hotelInformationList`）。

    兼容 `hotels` / `list` / `items` / `data`：供应商改名时不至于"悄悄一个都不返回"。
    """
    if isinstance(payload, str):
        import json

        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in ("hotelInformationList", "hotels", "list", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
        if isinstance(value, Mapping):
            nested = hotel_rows(value)
            if nested:
                return nested
    return []


def parse_price_message(message: Any) -> Optional[float]:
    """"查价成功。1晚总价：418CNY（约418CNY/晚）" → 418.0。

    只在 `lowestPrice` 缺失时兜底用；拿不到就 None（不猜）。
    ⚠️ 不能"取第一个数字"：真实文本开头就是「1晚总价」——第一个数字是**晚数 1**，
    取它会把 418 元的房价记成 1 元（实测踩过）。所以**先找带币种/单位的金额**。
    """
    text = str(message or "")
    if not text:
        return None
    patterns = (
        r"(\d[\d,]*(?:\.\d+)?)\s*(?:CNY|RMB|元|人民币)",
        r"[¥￥]\s*(\d[\d,]*(?:\.\d+)?)",
        r"(?:总价|价格|房价|每晚|均价)\D{0,4}(\d[\d,]*(?:\.\d+)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            continue
    return None


def normalize_hotel(row: Mapping[str, Any]) -> Dict[str, Any]:
    """供应商行 → 我们内部的酒店记录（字段名固定，缺就是缺）。"""
    price = row.get("price") if isinstance(row.get("price"), Mapping) else {}
    lowest = price.get("lowestPrice")
    value: Optional[float] = None
    if isinstance(lowest, (int, float)) and not isinstance(lowest, bool):
        value = float(lowest)
    elif isinstance(lowest, str):
        try:
            value = float(lowest)
        except ValueError:
            value = None
    message = str(price.get("message") or "")
    has_price = bool(price.get("hasPrice"))
    if value is None and has_price:
        value = parse_price_message(message)
    lnglat: List[float] = []
    try:
        if row.get("longitude") is not None and row.get("latitude") is not None:
            lnglat = [float(row["longitude"]), float(row["latitude"])]
    except (TypeError, ValueError):
        lnglat = []
    return {
        "name": str(row.get("name") or "").strip(),
        "price": value,
        "currency": str(price.get("currency") or "CNY"),
        "has_price": has_price,
        "message": message,
        "booking_url": str(row.get("bookingUrl") or "").strip(),
        "star": row.get("starRating"),
        "address": str(row.get("address") or "").strip(),
        "lnglat": lnglat,
        "hotel_id": row.get("hotelId"),
        "brand": str(row.get("brand") or "").strip(),
    }


def match_hotel(rows: Sequence[Mapping[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    """按酒店名匹配**这一家**（规范化后互相包含）。

    匹配不上就返回 None —— 绝不拿"同城另一家"的价格冒充这家（那是编造）。
    """
    key = normalize_text(name)
    if not key:
        return None
    for row in rows:
        candidate = normalize_text(row.get("name"))
        if not candidate:
            continue
        if key == candidate or key in candidate or candidate in key:
            return dict(row)
    return None


# --------------------------------------------------------------------------
# 网络（只读调用；测试通过替换 `_call_tool` 来避免真实请求）
# --------------------------------------------------------------------------
async def _call_tool(tool: str, arguments: Mapping[str, Any], *, timeout: Optional[float] = None) -> Dict[str, Any]:
    """一次只读 `tools/call`。返回 `{ok, payload, error, status_code, elapsed_ms}`。"""
    tool_name = str(tool or "")
    if tool_name not in READ_ONLY_TOOLS:
        # 安全边界写死在代码里：任何 mutation 工具都进不来
        return {"ok": False, "payload": None, "error": f"{tool_name} 不在只读白名单 {READ_ONLY_TOOLS}"}
    config = mcp_config()
    if not config["enabled"]:
        return {"ok": False, "payload": None, "error": config["reason"], "not_configured": True}

    import httpx

    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool_name, "arguments": dict(arguments)}}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "Authorization": f"Bearer {mcp_token()}",
    }
    limit = float(timeout or mcp_timeout_seconds())
    try:
        async with httpx.AsyncClient(timeout=limit, follow_redirects=True) as client:
            response = await client.post(config["endpoint"], json=body, headers=headers)
    except Exception as exc:  # 网络层失败要如实报，不能假装"没查到价格"
        return {"ok": False, "payload": None, "error": f"{type(exc).__name__}: {str(exc)[:140]}"}
    if response.status_code < 200 or response.status_code >= 300:
        return {"ok": False, "payload": None, "error": f"HTTP {response.status_code}", "status_code": response.status_code}

    envelope = _decode(response.content, response.headers.get("Content-Type", ""))
    if not isinstance(envelope, Mapping):
        return {"ok": False, "payload": None, "error": "返回体不是合法 JSON/SSE"}
    if envelope.get("error"):
        message = envelope["error"].get("message") if isinstance(envelope["error"], Mapping) else envelope["error"]
        return {"ok": False, "payload": None, "error": f"MCP error: {str(message)[:140]}"}
    result = envelope.get("result") if isinstance(envelope.get("result"), Mapping) else {}
    if result.get("isError"):
        return {"ok": False, "payload": result, "error": "工具返回 isError=True"}
    content = result.get("content") if isinstance(result.get("content"), list) else []
    text = ""
    for item in content:
        if isinstance(item, Mapping) and item.get("text"):
            text = str(item["text"])
            break
    if not text:
        return {"ok": False, "payload": result, "error": "工具没有返回文本内容"}
    import json

    try:
        return {"ok": True, "payload": json.loads(text)}
    except json.JSONDecodeError:
        return {"ok": False, "payload": None, "error": "返回文本不是 JSON"}


def _decode(raw: bytes, content_type: str) -> Optional[Any]:
    """MCP Streamable HTTP 可能回 JSON，也可能回 SSE（`data: {...}`）。两种都认。"""
    import json

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


def _arguments(
    *,
    name: str,
    city: str,
    check_in: str = "",
    nights: int = 1,
    adults: int = 2,
    size: int = 3,
    place_is_hotel: bool = True,
) -> Dict[str, Any]:
    """构造 `searchHotels` 参数（字段名与枚举照抄服务端 schema）。"""
    check_in_param: Dict[str, Any] = {
        "stayNights": max(1, min(28, int(nights or 1))),
        "adultCount": max(1, int(adults or 2)),
    }
    if check_in:
        check_in_param["checkInDate"] = str(check_in)
    # 服务端 schema：具体酒店直接填酒店名（placeType=酒店）；查城市要补国家（城市 国家）
    if place_is_hotel:
        place, place_type = f"{name} {city}".strip(), "酒店"
    else:
        place, place_type = f"{city} 中国".strip(), "城市"
    return {
        "originQuery": f"{city}住宿，{max(1, int(adults or 2))} 位成人，{max(1, int(nights or 1))} 晚",
        "place": place,
        "placeType": place_type,
        "checkInParam": check_in_param,
        "size": max(1, min(20, int(size or 3))),
    }


async def hotel_price(
    name: str,
    city: str,
    *,
    check_in: str = "",
    nights: int = 1,
    adults: int = 2,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """查**这一家**酒店的实时房价（只读）。

    返回 `{status, name, price, currency, message, booking_url, star, matched_name, check_in, error}`：
    - `status="ok"`：匹配到这家且拿到价格（`price` 可能为 None 表示"有价但没给出数字"）；
    - `status="not_found"`：供应商返回了结果但**没有这一家** —— 如实说，不拿别家的价顶替；
    - `status="not_configured"` / `"error"`：没配置 / 调用失败（都带原因）。
    """
    config = mcp_config()
    if not config["enabled"]:
        return {"status": "not_configured", "name": name, "error": config["reason"], "check_in": check_in}

    size = mcp_max_hotels()
    attempts: List[Dict[str, Any]] = [
        _arguments(name=name, city=city, check_in=check_in, nights=nights, adults=adults, size=size, place_is_hotel=True)
    ]
    # 同名酒店有时只在"城市"检索里返回（酒店名检索命中率不稳定），因此补一次城市检索再匹配。
    attempts.append(
        _arguments(name=name, city=city, check_in=check_in, nights=nights, adults=adults, size=size, place_is_hotel=False)
    )

    last_error = ""
    saw_rows = False
    for arguments in attempts:
        called = await _call_tool(DEFAULT_TOOL, arguments, timeout=timeout)
        if not called.get("ok"):
            last_error = str(called.get("error") or "调用失败")
            continue
        rows = [normalize_hotel(item) for item in hotel_rows(called.get("payload"))]
        if rows:
            saw_rows = True
        matched = match_hotel(rows, name)
        if matched:
            return {
                "status": "ok",
                "name": name,
                "matched_name": matched.get("name"),
                "price": matched.get("price"),
                "currency": matched.get("currency"),
                "message": matched.get("message"),
                "booking_url": matched.get("booking_url"),
                "star": matched.get("star"),
                "address": matched.get("address"),
                "lnglat": matched.get("lnglat"),
                "has_price": matched.get("has_price"),
                "check_in": check_in,
                "nights": max(1, int(nights or 1)),
                "adults": max(1, int(adults or 2)),
                "source": "rollinggo",
                "tool": DEFAULT_TOOL,
            }
    if saw_rows:
        return {
            "status": "not_found",
            "name": name,
            "error": "供应商返回了同城酒店，但没有这一家（不拿别家的价顶替）",
            "check_in": check_in,
        }
    return {"status": "error", "name": name, "error": last_error or "没有拿到任何结果", "check_in": check_in}


async def price_lodging_nodes(
    plan: Any,
    *,
    city: str,
    nights: int = 1,
    start_date: str = "",
    adults: int = 2,
    limit: int = 3,
) -> Dict[str, Any]:
    """给最终路线里**还没有可核实价格**的住宿节点查实时房价（只读），并合并回方案。

    为什么必须放在**住宿定型之后**（2026-10-05 真实推演踩到的坑）：
    取价步骤跑在"复核与多方案"，而硬约束收口（`settle_lodging`）在这之后还会补/换酒店。
    实测那一轮：取价时查到的是「库尔勒园林宾馆 ¥165」，收口后最终节点换成了
    「瑞德假日酒店(巴州博物馆市政府店)」—— 早查的那笔价随着换店一起丢了，
    最终方案里住宿仍是"暂无供应商数据"。**住宿价格必须在住宿定下来之后再查。**
    （同一个教训之前在"住宿收口必须是最后一道"上已经吃过一次。）

    返回 `{plan, report}`：`report` 含 `{enabled, reason, queried, matched, not_found, prices, check_in}`，
    供 `price_audit` 原样下发（用户能看到"查了几家、拿到几家、报价对应哪个日期"）。
    """
    from .candidate_index import intent_of  # 局部导入：避免模块级循环依赖
    from .plan_quality import node_name, nodes_of
    from .price_sources import is_verified_source, merge_observations

    config = mcp_config()
    report: Dict[str, Any] = {
        "enabled": bool(config.get("enabled")),
        "reason": config.get("reason"),
        "queried": 0,
        "matched": 0,
        "not_found": [],
        "without_price": [],
        "prices": {},
        "check_in": str(start_date or "").strip() or "（未给出发日期，供应商按明日报价）",
        "nights": max(1, int(nights or 1)),
    }
    if not config.get("enabled"):
        return {"plan": plan, "report": report, "observations": []}

    targets: List[str] = []
    for node in nodes_of(plan):
        if intent_of(node) != "hotel":
            continue
        sources = node.get("data_sources") if isinstance(node.get("data_sources"), Mapping) else {}
        current = str(sources.get("cost_estimate") or sources.get("cost") or "").strip().lower()
        if is_verified_source(current):
            continue  # 已经有可核实价格（例如供应商/高德给的），不必再查
        name = node_name(node)
        if name and name not in targets:
            targets.append(name)
    targets = targets[: max(1, int(limit))]

    observations: List[Dict[str, Any]] = []
    for name in targets:
        result = await hotel_price(
            name, city, check_in=str(start_date or "").strip(), nights=max(1, int(nights or 1)), adults=adults
        )
        report["queried"] += 1
        if result.get("status") == "ok":
            report["matched"] += 1
            price = result.get("price")
            if isinstance(price, (int, float)) and price > 0:
                report["prices"][name] = float(price)
            else:
                # 匹配到了这家酒店但供应商这次没给出数字（例如"暂无可售房型"）——
                # 如实记下来，不要拿 0 或别的数字顶上。
                report["without_price"].append(name)
        elif result.get("status") == "not_found":
            report["not_found"].append(name)
        observations.append(build_observation(name, result))

    merged_plan = plan
    updated: List[str] = []
    if observations:
        merged = merge_observations(plan, observations)
        merged_plan = merged["plan"]
        updated = [str(item.get("name") or "") for item in merged.get("updated") or []]
    report["updated"] = updated
    # 「匹配到这家」≠「拿到了数字」：供应商可能返回这家但当天没有可售报价。
    # 分开报，用户才知道"是没这家"还是"有这家但没价"（unknown ≠ 0）。
    report["with_price"] = len(report["prices"])
    return {"plan": merged_plan, "report": report, "observations": observations}


def build_observation(name: str, result: Mapping[str, Any]) -> Dict[str, Any]:
    """查价结果 → 取价观测（交给 `price_sources.merge_observations` 合并回方案节点）。"""
    price = result.get("price")
    message = str(result.get("message") or "")
    if result.get("status") != "ok":
        return {
            "name": name,
            "value": None,
            "source": "rollinggo",
            "provider": "rollinggo",
            "error": str(result.get("error") or result.get("status") or ""),
        }
    return {
        "name": str(result.get("matched_name") or name),
        "value": float(price) if isinstance(price, (int, float)) else None,
        "source": "rollinggo",
        "provider": "rollinggo",
        "source_url": str(result.get("booking_url") or ""),
        "excerpt": message[:160],
        "check_in": str(result.get("check_in") or ""),
        "nights": result.get("nights"),
        "star": result.get("star"),
    }
