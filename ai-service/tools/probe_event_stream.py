"""事件流真实写入探针：验证"统一信封 + 因果链"确实落进 Redis Stream（可追踪、可重放）。

背景：此前提交声称 `risk.changed → replan.requested` 是一条**带 correlation_id 的因果事件链**，
但这条说法一直没有被"真写到 Redis 里再读回来"验证过 —— 单测只覆盖了信封构造函数。
本探针走生产代码路径（`api.agent.event_bus.emit`），然后用**新的客户端**把事件读回来核对，
最后删掉探针事件（不留垃圾）。

用法：
    .\\venv\\Scripts\\python.exe tools\\probe_event_stream.py
退出码 0 = 全部断言通过；1 = 有断言失败（Redis 不可达也算失败，因为那意味着事件链是哑的）。
"""

import asyncio
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import redis.asyncio as aioredis  # noqa: E402

from api.agent import OMNI_EVENTS_STREAM, event_bus  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    print(("  ✅ " if condition else "  ❌ ") + message)
    if not condition:
        FAILURES.append(message)


async def main() -> int:
    url = os.getenv("REDIS_URL", "redis://localhost:6379")
    print(f"REDIS_URL = {url}")
    client = aioredis.from_url(url, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Redis 不可达（事件链此时是哑的）: {type(exc).__name__}: {exc}")
        return 1

    before = await client.xlen(OMNI_EVENTS_STREAM)
    correlation = f"probe-{uuid.uuid4().hex[:8]}"
    first_event_id = str(uuid.uuid4())

    # 1) 因果链的第一环：风险变化
    await event_bus.emit(
        OMNI_EVENTS_STREAM,
        "risk.changed",
        {"probe": True, "note": "事件流探针"},
        room_id="probe-room",
        correlation_id=correlation,
        producer="risk-agent",
        evidence=[{"provider": "probe", "field": "probe"}],
        confidence=0.5,
        event_id=first_event_id,
    )
    # 2) 第二环：由第一环触发（causation_id 指回第一环）
    await event_bus.emit(
        OMNI_EVENTS_STREAM,
        "replan.requested",
        {"probe": True, "action": "replace_affected_nodes"},
        room_id="probe-room",
        correlation_id=correlation,
        producer="emergency-agent",
        causation_id=first_event_id,
    )

    after = await client.xlen(OMNI_EVENTS_STREAM)
    print(f"XLEN {before} -> {after}（+{after - before}）")
    check(after >= before + 2, "两条探针事件都写进了 Stream")

    entries = await client.xrange(OMNI_EVENTS_STREAM, min="-", max="+", count=2000)
    mine: list[tuple[str, dict]] = []
    for entry_id, fields in entries:
        try:
            event = json.loads((fields or {}).get("data") or "{}")
        except Exception:  # noqa: BLE001
            continue
        if event.get("correlation_id") == correlation:
            mine.append((entry_id, event))

    check(len(mine) == 2, f"按 correlation_id 能读回两条事件（实际 {len(mine)}）")
    for entry_id, event in mine:
        print(
            "  ·",
            entry_id,
            json.dumps(
                {
                    key: event.get(key)
                    for key in ("event_type", "type", "producer", "target", "version", "causation_id", "event_id", "occurred_at", "room_id")
                },
                ensure_ascii=False,
            ),
        )

    if len(mine) == 2:
        by_type = {event.get("event_type") or event.get("type"): event for _, event in mine}
        risk_event = by_type.get("risk.changed")
        replan_event = by_type.get("replan.requested")
        check(risk_event is not None and replan_event is not None, "两种事件类型都在（risk.changed / replan.requested）")
        if risk_event and replan_event:
            check(risk_event.get("producer") == "risk-agent", "第一环 producer = risk-agent")
            check(replan_event.get("producer") == "emergency-agent", "第二环 producer = emergency-agent")
            check(replan_event.get("causation_id") == risk_event.get("event_id"), "因果链成立：replan 指回 risk 的 event_id")
            check(risk_event.get("version") == 1, "信封版本 = 1")
            check(risk_event.get("event_id") == first_event_id, "指定的 event_id 被原样写入（可重放/去重）")
            check(bool(risk_event.get("occurred_at")), "带 occurred_at 时间戳")
            check(risk_event.get("room_id") == "probe-room", "保留 room_id 镜像字段（网关 WS 桥无需同时改）")
            check((risk_event.get("payload") or {}).get("probe") is True, "payload 原样保留")

    if mine:
        await client.xdel(OMNI_EVENTS_STREAM, *[entry_id for entry_id, _ in mine])
        print(f"已清理探针事件 {len(mine)} 条")

    await client.aclose()
    print("FAILED: " + "; ".join(FAILURES) if FAILURES else "PASS - 事件链真实可追踪")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
