"""语义缓存真实写入探针：走生产代码路径（api.agent._semantic_cache），并且直接查 Redis 验证。

零推演成本：不触发任何规划，只调用 store/lookup 与（fire-and-forget 的）embedding 索引。
"""

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))  # api.agent 在导入时就要 LLM_API_KEY

import redis.asyncio as aioredis  # noqa: E402

from api.agent import _semantic_cache  # noqa: E402

CITY = "探针城市"
DAYS = 2
PROFILE = "语义缓存探针画像"


async def main() -> None:
    url = _semantic_cache.redis_url
    key = _semantic_cache._key(CITY, DAYS, PROFILE)
    print(f"REDIS_URL = {url}")
    print(f"cache key = {key}")

    client = aioredis.from_url(url, decode_responses=True)
    try:
        await client.delete(key)
    except Exception as exc:  # noqa: BLE001
        print(f"预清理失败（不影响结论）: {type(exc).__name__}: {exc}")

    payload = {"probe": True, "route": [{"day": 1, "name": "探针节点"}]}
    await _semantic_cache.store(CITY, DAYS, PROFILE, payload)
    print("store() 已返回（内部失败会被吞掉并打印 ⚠️ 日志）")

    raw = await client.get(key)
    print(f"Redis 直查: {'命中 ' + str(len(raw)) + ' 字节' if raw else '未命中（写入没落地）'}")

    ttl = await client.ttl(key)
    print(f"Redis TTL: {ttl}")

    _semantic_cache._mem.clear()  # 排掉内存缓存，逼它走 Redis
    hit = await _semantic_cache.lookup(CITY, DAYS, PROFILE)
    print(f"lookup()（清空内存后）: {'命中' if hit else '未命中'}")

    await client.delete(key)
    await client.aclose()


asyncio.run(main())
