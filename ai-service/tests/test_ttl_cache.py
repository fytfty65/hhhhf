"""TTL 缓存的回归锁。

背景（实测 2026-10-03）：`/api/v1/risk/realtime` 冷调用 6,882ms、命中缓存 4ms，
而前端轮询间隔（~50-60s）大于原快照 TTL（45s）→ 几乎每次轮询都在全量重打三个上游。
分层缓存要能真的省掉重复调用，所以这些测试盯死三件事：
TTL 内复用、TTL 过后重取、**失败绝不被缓存**（否则一次偶发失败会粘住整个窗口）。
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ttl_cache import AsyncTtlCache  # noqa: E402


class TestAsyncTtlCache(unittest.TestCase):
    def test_reuses_within_ttl_and_reloads_after(self):
        cache = AsyncTtlCache(60, name="test")
        calls = []

        async def loader():
            calls.append(1)
            return f"v{len(calls)}"

        async def scenario():
            first = await cache.get_or_load("k", loader)
            second = await cache.get_or_load("k", loader)
            # 用注入的"当前时间"模拟 TTL 过期，不依赖真实时钟
            expired_moment = cache._entries["k"][0] + 61
            third = await cache.get_or_load("k", loader, now=expired_moment)
            return first, second, third

        first, second, third = asyncio.run(scenario())
        self.assertEqual(first, "v1")
        self.assertEqual(second, "v1", "TTL 内必须复用，不能重复打数据源")
        self.assertEqual(third, "v2", "TTL 过后必须重新取")
        self.assertEqual(len(calls), 2)

    def test_failure_is_never_cached(self):
        cache = AsyncTtlCache(60, name="test")
        calls = []

        async def failing():
            calls.append(1)
            raise RuntimeError("upstream down")

        async def scenario():
            with self.assertRaises(RuntimeError):
                await cache.get_or_load("k", failing)
            # 下一次必须重试（而不是把失败粘住一整个 TTL 窗口）
            return await cache.get_or_load("k", lambda: asyncio.sleep(0, result="ok"))

        self.assertEqual(asyncio.run(scenario()), "ok")
        self.assertEqual(len(calls), 1)

    def test_single_flight_for_concurrent_callers(self):
        cache = AsyncTtlCache(60, name="test")
        calls = []

        async def loader():
            calls.append(1)
            await asyncio.sleep(0.05)
            return "shared"

        async def scenario():
            return await asyncio.gather(*(cache.get_or_load("k", loader) for _ in range(5)))

        results = asyncio.run(scenario())
        self.assertEqual(results, ["shared"] * 5)
        self.assertEqual(len(calls), 1, "同一 key 的并发请求只应打一次数据源（两个组件同时在轮询）")

    def test_keys_are_isolated(self):
        cache = AsyncTtlCache(60, name="test")

        async def scenario():
            a = await cache.get_or_load("city:杭州", lambda: asyncio.sleep(0, result="hz"))
            b = await cache.get_or_load("city:西安", lambda: asyncio.sleep(0, result="xa"))
            return a, b

        self.assertEqual(asyncio.run(scenario()), ("hz", "xa"))

    def test_stats_and_invalidate(self):
        cache = AsyncTtlCache(30, name="risk-weather")

        async def scenario():
            await cache.get_or_load("k", lambda: asyncio.sleep(0, result=1))
            await cache.get_or_load("k", lambda: asyncio.sleep(0, result=2))
            cache.invalidate("k")
            return await cache.get_or_load("k", lambda: asyncio.sleep(0, result=3))

        self.assertEqual(asyncio.run(scenario()), 3)
        stats = cache.stats()
        self.assertEqual(stats["name"], "risk-weather")
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 2)

    def test_rejects_non_positive_ttl(self):
        with self.assertRaises(ValueError):
            AsyncTtlCache(0)


if __name__ == "__main__":
    unittest.main()
