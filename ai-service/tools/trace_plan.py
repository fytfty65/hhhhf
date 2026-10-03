"""按 correlation_id 审计一次规划的事件链 —— 让"可追踪 / 可重放"变成能跑的东西。

背景：事件流过去是**没人读**的（前端不消费 `/events/stream`，也没有任何按规划聚合的工具），
于是"可审计"只是提交信息里的说法。这个工具把它变成可执行的动作：

    python tools/trace_plan.py --recent 5                     # 最近几次规划各自发了哪些事件
    python tools/trace_plan.py --correlation-id plan-xxxxxxxx # 打印这一次规划的完整轨迹
    python tools/trace_plan.py --correlation-id plan-xxxx --delete   # 清理（例如测试数据）

兼容性：迁移期仍有旧信封事件（没有 correlation_id），它们会被归到"（无关联键）"桶里
**如实显示**，而不是被悄悄丢掉 —— 那正是需要被发现的问题。
"""

import argparse
import asyncio
import json
import os
import sys
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import redis.asyncio as aioredis  # noqa: E402

from api.agent import OMNI_EVENTS_STREAM  # noqa: E402

UNLINKED = "（无关联键）"


def correlation_of(event: dict) -> str:
    direct = str(event.get("correlation_id") or "").strip()
    if direct:
        return direct
    payload = event.get("payload")
    if isinstance(payload, dict):
        return str(payload.get("correlation_id") or "").strip()
    return ""


def event_type_of(event: dict) -> str:
    return str(event.get("event_type") or event.get("type") or "?")


async def load_events(client, limit: int):
    entries = await client.xrange(OMNI_EVENTS_STREAM, min="-", max="+", count=limit)
    rows = []
    for entry_id, fields in entries:
        try:
            event = json.loads((fields or {}).get("data") or "{}")
        except Exception:  # noqa: BLE001
            continue
        rows.append((entry_id, event))
    return rows


def digest(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    parts = []
    for key, value in list(payload.items())[:6]:
        if isinstance(value, (str, int, float, bool)) or value is None:
            text = str(value)
            parts.append(f"{key}={text[:40]}")
    return ", ".join(parts)


async def main() -> int:
    parser = argparse.ArgumentParser(description="按 correlation_id 审计事件流")
    parser.add_argument("--correlation-id", default="")
    parser.add_argument("--recent", type=int, default=0, help="列出最近 N 次规划")
    parser.add_argument("--limit", type=int, default=5000, help="最多扫描多少条流事件")
    parser.add_argument("--delete", action="store_true", help="删除匹配到的事件（清理用）")
    args = parser.parse_args()

    url = os.getenv("REDIS_URL", "redis://localhost:6379")
    client = aioredis.from_url(url, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Redis 不可达（事件流读不出来）: {type(exc).__name__}: {exc}")
        await client.aclose()
        return 1

    rows = await load_events(client, args.limit)
    print(f"事件流 {OMNI_EVENTS_STREAM}: 共扫描 {len(rows)} 条（上限 {args.limit}）")

    if args.recent:
        groups: "OrderedDict[str, list]" = OrderedDict()
        for entry_id, event in rows:
            key = correlation_of(event) or UNLINKED
            groups.setdefault(key, []).append((entry_id, event))
        print(f"按关联键分组：{len(groups)} 组")
        for key, items in list(groups.items())[-args.recent:]:
            types = [event_type_of(event) for _, event in items]
            first = items[0][0]
            last = items[-1][0]
            print(f"  · {key}")
            print(f"      事件 {len(items)} 条 | 首 {first} | 末 {last}")
            print(f"      类型：{', '.join(types[:12])}{' …' if len(types) > 12 else ''}")

    if args.correlation_id:
        matched = [(entry_id, event) for entry_id, event in rows if correlation_of(event) == args.correlation_id]
        if not matched:
            print(f"❌ 没找到关联键为 {args.correlation_id} 的事件")
            await client.aclose()
            return 1
        print(f"\n=== 轨迹 {args.correlation_id}（{len(matched)} 条）===")
        for entry_id, event in matched:
            print(
                f"  {entry_id}  {event_type_of(event):<26} producer={event.get('producer') or '-':<16}"
                f" causation={event.get('causation_id') or '-':<20} at={event.get('occurred_at') or event.get('ts')}"
            )
            summary = digest(event.get("payload"))
            if summary:
                print(f"      payload: {summary}")
        if args.delete:
            await client.xdel(OMNI_EVENTS_STREAM, *[entry_id for entry_id, _ in matched])
            print(f"已删除 {len(matched)} 条")

    if not args.recent and not args.correlation_id:
        parser.print_help()

    await client.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
