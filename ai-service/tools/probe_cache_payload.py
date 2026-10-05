"""检查语义缓存里的方案 payload 是否带着该带的披露（2026-10-05）。

为什么要看一眼：`daily_balance` 的"收口后重算"结论进了 payload，但**用户看到的是 degradations**。
这里直接读 Redis 里的缓存条目，确认"密度不足"确实进了遗留问题清单，
而不是只活在服务端日志里。
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis.asyncio as redis  # noqa: E402


async def main() -> None:
    client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
    keys = await client.keys("*")
    plan_keys = [key for key in keys if len(key) == 32 and all(ch in "0123456789abcdef" for ch in key)]
    print(f"缓存条目 {len(plan_keys)} 个：")
    for key in plan_keys:
        raw = await client.get(key)
        try:
            data = json.loads(raw)
        except Exception:
            continue
        final_data = data.get("final_data") or {}
        city = str(final_data.get("city") or (data.get("target_city") or {}).get("name") or "?")
        route = final_data.get("route") or []
        balance = final_data.get("daily_balance") or {}
        degradations = (final_data.get("degradations") or {}).get("items") or []
        density = [item for item in degradations if item.get("source") == "daily_balance"]
        print(
            f"  · {city}: 节点={len(route)} 密度达标={balance.get('satisfied')} "
            f"收口后重算={balance.get('recomputed_after_closeout')} 密度披露={len(density)} 条"
        )
        for item in density:
            print(f"      - {str(item.get('reason'))[:110]}")
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
