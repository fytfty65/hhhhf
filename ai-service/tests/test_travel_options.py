"""出稿后路网拼装的性能与正确性护栏（2026-10-05）。

为什么单独测这一块：横向对照实测「路网与推送」这一段要 **48.4s**，而服务端整个请求
要等它跑完才结束（`latency_ms` 与"含收尾"总计完全吻合），也就是**它直接决定用户等多久**。
优化做了三件事，这里逐条钉住：
  1. 有界并发（信号量）：一次打 45 个请求会被高德 QPS 限流，反而更慢；
  2. 只问有意义的出行方式：3km 以上不问步行、40km 以上不问市内公交；
  3. 复用同一个坐标对的查询结果（进程内缓存），空结果不缓存（避免把一次限流固化）。
另外钉住一个**静默算错**的坑：`haversine_km` 按下标取经纬度，
直接喂 "81.3,43.9" 字符串会把第 0/1 个字符当经纬度。
"""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")

from api.agent import (  # noqa: E402
    _TRAVEL_OPTIONS_CACHE,
    _straight_line_km,
)


class StraightLineTests(unittest.TestCase):
    def test_same_point_is_zero(self):
        self.assertEqual(_straight_line_km("81.3,43.9", "81.3,43.9"), 0.0)

    def test_strings_are_parsed_not_indexed_by_character(self):
        """回归：把 "100,10" 当字符串按下标取，会算出**天差地别**的距离。"""
        self.assertEqual(_straight_line_km("100,10", "100,10"), 0.0)
        # 纬度差 1 度 ≈ 111km；按字符下标取会得到 0（'1','0' → 1.0,0.0 vs 1.0,0.0），
        # 也就是"同一个点" —— 这正是静默算错的样子，所以这里必须断言真实量级。
        distance = _straight_line_km("100,10", "100,11")
        self.assertIsNotNone(distance)
        self.assertTrue(110 < float(distance) < 112, distance)

    def test_garbage_returns_none_not_zero(self):
        self.assertIsNone(_straight_line_km("", "81.3,43.9"))
        self.assertIsNone(_straight_line_km("abc", "81.3,43.9"))


class TravelOptionsTests(unittest.TestCase):
    """用假 httpx 客户端观察"到底发了哪些请求"，不碰真实高德。"""

    def setUp(self):
        _TRAVEL_OPTIONS_CACHE.clear()
        self.requested = []

        class _Response:
            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

        requested = self.requested

        async def _fake_get(url, params=None):
            # 记录"问了哪种出行方式"（真实 URL 是 /direction/driving|walking|transit/integrated）
            kind = url.split("/direction/")[-1].split("/")[0]
            requested.append(kind)
            if kind == "walking":
                return _Response({"status": "1", "route": {"paths": [{"distance": "800", "duration": "600", "steps": []}]}})
            if kind == "transit":
                return _Response({"status": "1", "route": {"transits": [{"distance": "3000", "duration": "1800", "segments": []}]}})
            return _Response({"status": "1", "route": {"paths": [{"distance": "12000", "duration": "1500", "steps": []}]}})

        class _Client:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def get(self, url, params=None):
                # 真实 httpx 的 get 是**协程**：假客户端必须也返回协程，
                # 否则 asyncio.gather 直接 TypeError（这一版测试就踩过）
                return await _fake_get(url, params)

        self.client_patch = mock.patch("api.agent.httpx.AsyncClient", _Client)

    def _toolbox(self):
        from api.agent import ExpertToolbox

        box = ExpertToolbox()
        box.amap_key = "test-key"
        return box

    def test_far_leg_skips_walking_and_transit(self):
        with self.client_patch:
            box = self._toolbox()
            result = asyncio.run(box.get_travel_options("87.6,43.8", "81.3,43.9", "伊宁"))
        self.assertEqual(self.requested, ["driving"], "跨城腿不该问步行/市内公交")
        self.assertIn("driving", result)

    def test_short_leg_asks_all_three(self):
        with self.client_patch:
            box = self._toolbox()
            result = asyncio.run(box.get_travel_options("81.30,43.90", "81.31,43.91", "伊宁"))
        self.assertEqual(sorted(self.requested), ["driving", "transit", "walking"])
        self.assertIn("walking", result)
        self.assertIn("transit", result)

    def test_medium_leg_asks_driving_and_transit_only(self):
        with self.client_patch:
            box = self._toolbox()
            asyncio.run(box.get_travel_options("81.3,43.9", "81.4,43.95", "伊宁"))
        self.assertEqual(sorted(self.requested), ["driving", "transit"])

    def test_same_pair_is_served_from_cache(self):
        with self.client_patch:
            box = self._toolbox()
            asyncio.run(box.get_travel_options("81.3,43.9", "81.4,43.95", "伊宁"))
            first_round = list(self.requested)
            asyncio.run(box.get_travel_options("81.3,43.9", "81.4,43.95", "伊宁"))
        self.assertEqual(self.requested, first_round, "同一对坐标不该第二次去问高德")

    def test_failed_lookup_is_not_cached(self):
        class _Broken:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def get(self, *_args, **_kwargs):
                raise RuntimeError("QPS 限流")

        with mock.patch("api.agent.httpx.AsyncClient", _Broken):
            box = self._toolbox()
            result = asyncio.run(box.get_travel_options("81.3,43.9", "81.4,43.95", "伊宁"))
        self.assertEqual(result, {})
        self.assertEqual(len(_TRAVEL_OPTIONS_CACHE), 0, "空结果不能进缓存（否则一次限流被固化）")

    def test_missing_key_returns_empty_without_any_request(self):
        box = self._toolbox()
        box.amap_key = ""
        with self.client_patch:
            self.assertEqual(asyncio.run(box.get_travel_options("81.3,43.9", "81.4,43.95", "伊宁")), {})
        self.assertEqual(self.requested, [])


class ConcurrencyGuardTests(unittest.TestCase):
    def test_semaphore_bounds_the_burst(self):
        from api import agent as agent_module

        self.assertEqual(agent_module._TRAVEL_OPTIONS_SEMAPHORE._value, 8)
        self.assertEqual(len(agent_module._TRAVEL_OPTIONS_CACHE), 0)

    def test_cache_has_bound_and_ttl_instead_of_clearing_everything(self):
        """超限要淘汰最久未用的一条，而不是整体 clear（整体清空会把热点一起丢）。"""
        from api import agent as agent_module
        from core.cache_registry import BoundedTTLCache

        live = agent_module._TRAVEL_OPTIONS_CACHE
        self.assertEqual(live.max_entries, 512)
        self.assertGreater(live.ttl_seconds, 0)

        cache = BoundedTTLCache("test_travel_options", max_entries=512, ttl_seconds=3600)
        for index in range(513):
            cache.set(f"pair-{index}", {"duration_min": index})
        self.assertEqual(len(cache), 512)
        self.assertIsNone(cache.get("pair-0"), "最久未用的一条应被淘汰")
        self.assertEqual(cache.get("pair-512"), {"duration_min": 512})
        self.assertEqual(cache.evictions, 1)


if __name__ == "__main__":
    unittest.main()
