"""分层缓存到底省没省掉上游调用？（计数桩验证，不打真实数据源）

实测背景（2026-10-03）：冷调用 6,882ms、命中 45s 快照缓存 4ms，而前端轮询间隔 ~50-60s
大于 45s → 几乎每次轮询都在重打 weather + traffic + safety。
这个测试把"第二次轮询不再打上游"钉死：**故意先清掉外层快照缓存**，
逼 `_risk_snapshot` 重建快照 —— 如果分层缓存没生效，上游计数就会涨。
"""

import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from api import operations  # noqa: E402
from api.risk_service import RiskService  # noqa: E402


class TestRiskSnapshotLayeredCache(unittest.TestCase):
    def setUp(self):
        operations._risk_cache.clear()
        operations._risk_weather_cache.invalidate()
        operations._risk_traffic_cache.invalidate()
        operations._risk_safety_cache.invalidate()
        self.calls = {"weather": 0, "traffic": 0, "safety": 0}

    def _patch_providers(self):
        # 注意：给**类**打补丁时，桩函数会收到实例作为第一个参数
        async def weather(_self, city):
            self.calls["weather"] += 1
            return {"source": "stub-weather", "city": city, "retrieved_at": 1790000000000}

        async def traffic(_self, coord):
            self.calls["traffic"] += 1
            return {"source": "stub-traffic", "coord": coord}

        async def safety(_self, city):
            self.calls["safety"] += 1
            return {"city": city, "source": "stub-safety", "cii_score": 12.0, "retrieved_at": 1790000000000}

        async def aggregate(_self, intel, city, weather_value=None, traffic_value=None):
            return intel

        def build(_self, safety_intel, weather=None, traffic=None):
            return {"city": safety_intel.get("city"), "source": "stub", "weather": weather, "traffic": traffic}

        patches = [
            mock.patch.object(operations.ExpertToolbox, "get_real_weather", weather),
            mock.patch.object(operations.ExpertToolbox, "get_traffic_status", traffic),
            mock.patch.object(RiskService, "get_city_safety_intel", safety),
            mock.patch.object(RiskService, "aggregate_city_risk", aggregate),
            mock.patch.object(RiskService, "build_snapshot", build),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_repeated_poll_does_not_hit_providers_again(self):
        self._patch_providers()

        async def scenario():
            first = await operations._risk_snapshot("测试城", "120.1,30.2", None)
            # 清掉**外层**快照缓存：模拟"下一次轮询已经超出 45s 快照 TTL"
            operations._risk_cache.clear()
            second = await operations._risk_snapshot("测试城", "120.1,30.2", None)
            return first, second

        first, second = asyncio.run(scenario())
        self.assertTrue(first[0] and second[0])
        self.assertEqual(
            self.calls, {"weather": 1, "traffic": 1, "safety": 1},
            f"分层缓存没生效：重复轮询又打了上游 {self.calls}",
        )

    def test_sources_without_timestamp_get_fetch_time_stamp(self):
        """来源不报 retrieved_at 时，必须在**取数时刻**补戳 —— 否则缓存之后
        risk_ts 会退回组装时间，界面就会显得比数据本身更新。"""
        self._patch_providers()

        async def scenario():
            await operations._risk_snapshot("测试城", "120.1,30.2", None)
            operations._risk_cache.clear()
            return await operations._risk_snapshot("测试城", "120.1,30.2", None)

        snapshot, _changes = asyncio.run(scenario())
        stamped = snapshot.get("traffic", {}).get("retrieved_at")
        self.assertIsInstance(stamped, int, f"未报时间的来源应被补上取数时刻：{snapshot.get('traffic')}")
        self.assertGreater(stamped, 1_700_000_000_000, "戳必须是毫秒时间戳，不能是别的东西")

    def test_different_city_still_fetches(self):
        self._patch_providers()

        async def scenario():
            await operations._risk_snapshot("测试城", "120.1,30.2", None)
            await operations._risk_snapshot("另一城", "108.9,34.2", None)

        asyncio.run(scenario())
        self.assertEqual(self.calls["weather"], 2, "换城市必须重新取，缓存不能跨城市串味")
        self.assertEqual(self.calls["safety"], 2)

    def test_provider_failure_is_not_cached(self):
        attempts = {"weather": 0}

        async def flaky_weather(_self, city):
            attempts["weather"] += 1
            if attempts["weather"] == 1:
                raise RuntimeError("weather provider down")
            return {"source": "stub-weather", "city": city}

        async def safety(_self, city):
            return {"city": city, "source": "stub-safety"}

        async def aggregate(_self, intel, city, weather_value=None, traffic_value=None):
            return intel

        def build(_self, safety_intel, weather=None, traffic=None):
            return {"city": safety_intel.get("city")}

        with mock.patch.object(operations.ExpertToolbox, "get_real_weather", flaky_weather), \
             mock.patch.object(RiskService, "get_city_safety_intel", safety), \
             mock.patch.object(RiskService, "aggregate_city_risk", aggregate), \
             mock.patch.object(RiskService, "build_snapshot", build):
            async def scenario():
                with self.assertRaises(RuntimeError):
                    await operations._risk_snapshot("测试城", "120.1,30.2", None)
                operations._risk_cache.clear()
                return await operations._risk_snapshot("测试城", "120.1,30.2", None)

            snapshot, _changes = asyncio.run(scenario())

        self.assertTrue(snapshot)
        self.assertEqual(attempts["weather"], 2, "一次偶发失败不能被缓存住，下一次必须重试")


if __name__ == "__main__":
    unittest.main()
