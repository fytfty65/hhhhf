"""事件流消费的回归锁：**Redis 异常时必须退避**。

背景（实测事故 2026-10-03）
--------------------------
`api/operations.py` 的 `/events/stream` 是 `while True` + 依赖 `consume()` 的 `block_ms=5000`
来"睡 5 秒"。而 `consume()` 原来在异常时**立即 return []**，于是只要 Redis 侧一出错，
这个循环就退化成毫秒级空转：ai-service 空转 **87.6% 单核**、开机 25 小时累计 **22 小时 CPU**，
而 8000 端口一个客户端都没有。这条测试把"异常必须等待"钉死。
"""

import asyncio
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import api.agent as agent_module  # noqa: E402
from api.agent import RedisEventStream  # noqa: E402


class TestConsumeBackoff(unittest.TestCase):
    def setUp(self):
        self._original_from_url = agent_module.redis.from_url

    def tearDown(self):
        agent_module.redis.from_url = self._original_from_url

    def test_consume_backs_off_when_redis_fails(self):
        def boom(*args, **kwargs):
            raise RuntimeError("redis unreachable")

        agent_module.redis.from_url = boom
        bus = RedisEventStream()
        started = time.monotonic()
        result = asyncio.run(bus.consume("omni:events", block_ms=1000))
        elapsed = time.monotonic() - started

        self.assertEqual(result, [], "异常时应返回空列表（调用方据此发 keepalive）")
        self.assertGreaterEqual(
            elapsed,
            0.5,
            "Redis 异常时必须退避等待：立即返回会让订阅端的 while True 空转烧满一个核心",
        )

    def test_consume_reuses_client_between_calls(self):
        """连接要复用：原来每轮循环都 redis.from_url 新建一次，失败时还会狂刷连接。"""
        calls = {"count": 0}

        class FakeClient:
            async def xread(self, *args, **kwargs):
                return []

        def counting_from_url(*args, **kwargs):
            calls["count"] += 1
            return FakeClient()

        agent_module.redis.from_url = counting_from_url
        bus = RedisEventStream()
        asyncio.run(bus.consume("omni:events", block_ms=10))
        asyncio.run(bus.consume("omni:events", block_ms=10))
        self.assertEqual(calls["count"], 1, "同一个实例应复用连接，而不是每次重建")

    def test_client_is_dropped_after_failure(self):
        """失败后要丢弃坏连接，下一轮重建 —— 否则会一直用坏客户端。"""
        class BrokenClient:
            async def xread(self, *args, **kwargs):
                raise RuntimeError("broken")

        agent_module.redis.from_url = lambda *a, **k: BrokenClient()
        bus = RedisEventStream()
        asyncio.run(bus.consume("omni:events", block_ms=10))
        self.assertIsNone(bus._client, "失败后应清空缓存的客户端")


if __name__ == "__main__":
    unittest.main()
