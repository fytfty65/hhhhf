import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

"""风险计算服务单元测试（api/risk_service.py）。

全部离线运行：只测试纯函数、LocalBaselineProvider，以及注入了
stub provider 的 RiskService；绝不发起真实 HTTP 请求。
"""

import asyncio
import time

from api.risk_service import (
    CII_DIMENSION_WEIGHTS,
    LocalBaselineProvider,
    RiskReport,
    RiskService,
    compute_cii_score,
    compute_node_risk,
    compute_risk_dimensions,
    detect_risk_changes,
)

# compute_risk_dimensions 的中性基线：治安/气象/政治/交通 = 10.0，卫生 = 15.0
NEUTRAL_CRIME = 10.0
NEUTRAL_POLITICAL = 10.0
NEUTRAL_WEATHER = 10.0
NEUTRAL_TRAFFIC = 10.0
NEUTRAL_HEALTH = 15.0


class _StubProvider:
    """记录调用次数的假情报源：绝不联网。payload=None 表示「无情报可用」。"""

    name = "stub"

    def __init__(self, payload=None):
        self._payload = payload
        self.calls = []

    async def fetch(self, city):
        self.calls.append(city)
        if self._payload is None:
            return None
        return dict(self._payload, city=city)


class _BoomProvider:
    """抛异常的假情报源，用于验证降级链不会把异常冒泡出去。"""

    name = "boom"

    async def fetch(self, city):
        raise RuntimeError("no external network in tests")


def _service_with(provider):
    service = RiskService()
    service._providers = [provider]
    return service


class TestCiiComposite(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(CII_DIMENSION_WEIGHTS.values()), 1.0, places=9)
        self.assertEqual(
            set(CII_DIMENSION_WEIGHTS),
            {"crime", "political", "health", "weather", "traffic"},
        )
        self.assertGreater(CII_DIMENSION_WEIGHTS["crime"], CII_DIMENSION_WEIGHTS["political"])
        self.assertTrue(all(w > 0 for w in CII_DIMENSION_WEIGHTS.values()))

    def test_weighted_composite_for_known_input(self):
        dims = {
            "crime_score": 50,
            "political_score": 40,
            "health_score": 30,
            "weather_score": 20,
            "traffic_score": 10,
        }
        result = compute_cii_score(dims)
        # 50*.35 + 40*.20 + 30*.15 + 20*.15 + 10*.15 = 17.5 + 8 + 4.5 + 3 + 1.5
        self.assertAlmostEqual(result["cii_score"], 34.5, places=9)
        self.assertEqual(result["method"], "weighted_composite")
        self.assertAlmostEqual(result["total_weight"], 1.0, places=9)
        self.assertEqual(result["dimensions"]["crime"],
                         {"score": 50.0, "weight": 0.35, "contribution": 17.5})
        self.assertEqual(result["dimensions"]["political"]["contribution"], 8.0)
        self.assertEqual(result["dimensions"]["health"]["contribution"], 4.5)
        self.assertEqual(result["dimensions"]["weather"]["contribution"], 3.0)
        self.assertEqual(result["dimensions"]["traffic"]["contribution"], 1.5)

    def test_missing_dimensions_fall_back_to_neutral_ten(self):
        result = compute_cii_score({})
        self.assertAlmostEqual(result["cii_score"], 10.0, places=9)
        for dim in ("crime", "political", "health", "weather", "traffic"):
            self.assertEqual(result["dimensions"][dim]["score"], 10.0)
        self.assertAlmostEqual(
            result["cii_score"],
            round(10.0 * sum(CII_DIMENSION_WEIGHTS.values()), 1),
            places=9,
        )

    def test_score_is_clamped_to_100(self):
        result = compute_cii_score({"crime_score": 80}, weights={"crime": 2.0})
        self.assertEqual(result["cii_score"], 100.0)
        self.assertEqual(result["total_weight"], 2.0)


class TestDimensionSourceGuard(unittest.TestCase):
    """治安/政治两维只在接入真实情报源时按等级/强度升级（risk_service.py:305、340 的 src 守卫）。"""

    def test_local_baseline_never_escalates_crime_or_political(self):
        fabricated = {
            "source": "local_baseline",
            "risk_level": "HIGH",       # 兜底源不应携带 HIGH
            "cii_score": 95.0,
            "event_intensity": 88.0,    # 也不应被当成真实事件强度
            "active_alerts": [{"type": "DATA_NOTICE", "level": "LOW", "title": "未接入实时权威情报源"}],
        }
        dims = compute_risk_dimensions(fabricated)
        self.assertEqual(dims["crime_score"], NEUTRAL_CRIME)
        self.assertEqual(dims["political_score"], NEUTRAL_POLITICAL)
        self.assertLessEqual(dims["crime_score"], NEUTRAL_CRIME)
        self.assertLessEqual(dims["political_score"], NEUTRAL_POLITICAL)

    def test_missing_source_never_escalates_crime_or_political(self):
        dims = compute_risk_dimensions({"risk_level": "HIGH", "event_intensity": 88.0})
        self.assertEqual(dims["crime_score"], NEUTRAL_CRIME)
        self.assertEqual(dims["political_score"], NEUTRAL_POLITICAL)

    def test_real_source_escalates_by_level(self):
        # 对照组：真实情报源的 HIGH 等级确实会升级，证明上面的守卫不是「恒为基线」
        dims = compute_risk_dimensions({"source": "GDELT", "risk_level": "HIGH", "active_alerts": []})
        self.assertEqual(dims["crime_score"], 75.0)
        self.assertEqual(dims["political_score"], 75.0)

        medium = compute_risk_dimensions({"source": "GDELT", "risk_level": "MEDIUM", "active_alerts": []})
        self.assertEqual(medium["crime_score"], 40.0)
        self.assertEqual(medium["political_score"], 40.0)

    def test_real_source_uses_continuous_event_intensity(self):
        dims = compute_risk_dimensions({"source": "GDELT", "event_intensity": 70.0, "active_alerts": []})
        self.assertEqual(dims["crime_score"], 70.0)
        self.assertAlmostEqual(dims["political_score"], round(70.0 * 0.8, 1), places=9)
        self.assertEqual(dims["political_score"], 56.0)

    def test_health_alert_escalates_health_dimension(self):
        dims = compute_risk_dimensions({})
        self.assertEqual(dims["health_score"], NEUTRAL_HEALTH)
        escalated = compute_risk_dimensions({"active_alerts": [{"type": "HEALTH", "level": "HIGH"}]})
        self.assertEqual(escalated["health_score"], 60.0)


class TestWeatherKeywords(unittest.TestCase):
    def _weather(self, condition):
        return compute_risk_dimensions({}, {"condition": condition})["weather_score"]

    def test_severe_condition_scores_higher_than_mild(self):
        severe = self._weather("暴雨")
        moderate = self._weather("中雨")
        mild = self._weather("晴")
        self.assertEqual(severe, 80.0)
        self.assertEqual(moderate, 40.0)
        self.assertEqual(mild, NEUTRAL_WEATHER)
        self.assertGreater(severe, moderate)
        self.assertGreater(moderate, mild)

    def test_keyword_table_specific_values(self):
        self.assertEqual(self._weather("特大暴雨"), 90.0)
        self.assertEqual(self._weather("台风"), 88.0)
        self.assertEqual(self._weather("大雨"), 55.0)
        self.assertEqual(self._weather("多云"), 25.0)

    def test_extreme_temperature_raises_score(self):
        self.assertEqual(self._weather("晴 35°C"), 70.0)
        self.assertEqual(self._weather("晴 -3°C"), 65.0)
        self.assertEqual(self._weather("晴 22°C"), 10.0)

    def test_unknown_and_missing_weather_stay_at_baseline(self):
        self.assertEqual(self._weather("未知天气"), NEUTRAL_WEATHER)
        self.assertEqual(compute_risk_dimensions({})["weather_score"], NEUTRAL_WEATHER)


class TestTrafficMapping(unittest.TestCase):
    def _traffic(self, status_code):
        return compute_risk_dimensions({}, None, {"status_code": status_code})["traffic_score"]

    def test_status_code_mapping_is_monotonic(self):
        scores = [self._traffic(str(code)) for code in (1, 2, 3, 4)]
        self.assertEqual(scores, [10.0, 40.0, 70.0, 85.0])
        for low, high in zip(scores, scores[1:]):
            self.assertLess(low, high)

    def test_int_and_str_codes_are_equivalent(self):
        for code in (1, 2, 3, 4):
            with self.subTest(code=code):
                self.assertEqual(self._traffic(code), self._traffic(str(code)))

    def test_invalid_or_missing_code_falls_back_to_smooth(self):
        self.assertEqual(self._traffic("x"), NEUTRAL_TRAFFIC)
        self.assertEqual(compute_risk_dimensions({}, None, {})["traffic_score"], NEUTRAL_TRAFFIC)
        self.assertEqual(compute_risk_dimensions({})["traffic_score"], NEUTRAL_TRAFFIC)


class TestNodeRisk(unittest.TestCase):
    def test_hotel_is_discounted(self):
        self.assertEqual(compute_node_risk({"is_hotel": False}, 40.0), 20.0)
        self.assertEqual(compute_node_risk({"is_hotel": True}, 40.0), 12.0)
        self.assertAlmostEqual(
            compute_node_risk({"is_hotel": True}, 40.0),
            compute_node_risk({}, 40.0) - 8.0,
            places=9,
        )

    def test_night_hours_add_penalty(self):
        self.assertEqual(compute_node_risk({"time": "23:30"}, 40.0), 26.0)
        self.assertEqual(compute_node_risk({"time": "02:15"}, 40.0), 26.0)
        self.assertEqual(compute_node_risk({"time": "09:00"}, 40.0), 20.0)
        self.assertGreater(
            compute_node_risk({"time": "23:30"}, 40.0),
            compute_node_risk({"time": "09:00"}, 40.0),
        )

    def test_daytime_hours_do_not_add_night_penalty(self):
        # 12:30 / 16:45 均非夜间（设计意图为 20:00-06:59），不应触发夜间加成。
        #
        # 此用例最初以 @unittest.expectedFailure 记录了一个真实缺陷：旧正则
        # r"(2[0-3]|0?[0-6]):\d{2}" 未锚定小时起点，"0?[0-6]" 分支会在 "12:30"
        # 的第 2 个字符处匹配到 "2:30"、在 "16:45" 匹配到 "6:45"，把白天误判为
        # 夜间并错误加 6 分（26.0 而非 20.0）。缺陷已修复（risk_service.py 改用
        # 锚定的 _NIGHT_HOUR_PATTERN），因此该标记已移除。
        self.assertEqual(compute_node_risk({"time": "12:30"}, 40.0), 20.0)
        self.assertEqual(compute_node_risk({"time": "16:45"}, 40.0), 20.0)

    def test_night_boundary_hours_are_exact(self):
        # 边界：20:00 起算夜间，07:00 不再算。
        self.assertEqual(compute_node_risk({"time": "19:59"}, 40.0), 20.0)
        self.assertEqual(compute_node_risk({"time": "20:00"}, 40.0), 26.0)
        self.assertEqual(compute_node_risk({"time": "06:59"}, 40.0), 26.0)
        self.assertEqual(compute_node_risk({"time": "07:00"}, 40.0), 20.0)

    def test_night_detection_survives_a_day_prefix(self):
        # 规划器产出的时间形如 "Day 1 | 21:30"，前缀不得破坏夜间判定。
        self.assertEqual(compute_node_risk({"time": "Day 1 | 12:30"}, 40.0), 20.0)
        self.assertEqual(compute_node_risk({"time": "Day 1 | 21:30"}, 40.0), 26.0)

    def test_crowd_hint_adds_penalty(self):
        self.assertEqual(compute_node_risk({}, 40.0, "景区非常拥挤"), 40.0)
        self.assertEqual(compute_node_risk({}, 40.0, "高峰时段"), 40.0)
        self.assertEqual(compute_node_risk({}, 40.0, "人流适中"), 20.0)

    def test_score_is_clamped_to_0_100(self):
        self.assertEqual(compute_node_risk({}, 300.0), 100.0)
        self.assertEqual(compute_node_risk({"is_hotel": True}, 0.0), 0.0)

    def test_missing_city_crime_uses_neutral_default(self):
        self.assertEqual(compute_node_risk({}, None), 5.0)


class TestDetectRiskChanges(unittest.TestCase):
    def test_none_or_non_dict_baseline_returns_empty(self):
        for baseline in (None, [], "x", 3):
            with self.subTest(baseline=baseline):
                self.assertEqual(detect_risk_changes(baseline, {"risk_level": "HIGH", "cii_score": 90}), [])

    def test_detects_risk_level_jump(self):
        changes = detect_risk_changes(
            {"risk_level": "LOW", "cii_score": 10.0},
            {"risk_level": "HIGH", "cii_score": 70.0},
        )
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["type"], "RISK_LEVEL_CHANGE")
        self.assertEqual(changes[0]["severity"], "HIGH")
        self.assertIn("LOW", changes[0]["detail"])
        self.assertIn("HIGH", changes[0]["detail"])

    def test_detects_large_cii_delta_without_level_change(self):
        changes = detect_risk_changes(
            {"risk_level": "LOW", "cii_score": 10.0},
            {"risk_level": "LOW", "cii_score": 30.0},
        )
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["type"], "CII_CHANGE")
        self.assertEqual(changes[0]["severity"], "MEDIUM")
        self.assertIn("20.0", changes[0]["detail"])

    def test_small_cii_delta_is_ignored(self):
        # 阈值 15：14.9 不触发，15.0 触发
        self.assertEqual(
            detect_risk_changes({"risk_level": "LOW", "cii_score": 10.0},
                                {"risk_level": "LOW", "cii_score": 24.9}),
            [],
        )
        triggered = detect_risk_changes({"risk_level": "LOW", "cii_score": 10.0},
                                        {"risk_level": "LOW", "cii_score": 25.0})
        self.assertEqual([c["type"] for c in triggered], ["CII_CHANGE"])

    def test_detects_weather_break_and_traffic_worsening(self):
        weather = detect_risk_changes(
            {"risk_level": "LOW", "weather": {"condition": "晴"}},
            {"risk_level": "LOW", "weather": {"condition": "暴雨"}},
        )
        self.assertEqual(weather[0]["type"], "WEATHER_CHANGE")
        self.assertEqual(weather[0]["severity"], "HIGH")

        traffic = detect_risk_changes(
            {"risk_level": "LOW", "traffic": {"status_code": "1", "description": "畅通"}},
            {"risk_level": "LOW", "traffic": {"status_code": "4", "description": "严重拥堵"}},
        )
        self.assertEqual(traffic[0]["type"], "TRAFFIC_CHANGE")
        self.assertEqual(traffic[0]["severity"], "HIGH")

    def test_traffic_code_below_three_is_not_reported(self):
        self.assertEqual(
            detect_risk_changes(
                {"risk_level": "LOW", "traffic": {"status_code": "1"}},
                {"risk_level": "LOW", "traffic": {"status_code": "2"}},
            ),
            [],
        )

    def test_detects_new_alert(self):
        changes = detect_risk_changes(
            {"risk_level": "LOW", "active_alerts": [{"title": "旧告警"}]},
            {"risk_level": "LOW", "active_alerts": [{"title": "旧告警"},
                                                    {"title": "新告警", "level": "HIGH", "detail": "d"}]},
        )
        self.assertEqual([c["type"] for c in changes], ["NEW_ALERT"])
        self.assertEqual(changes[0]["severity"], "HIGH")
        self.assertIn("新告警", changes[0]["title"])


class TestLocalBaselineProvider(unittest.TestCase):
    def test_returns_low_and_estimated(self):
        data = asyncio.run(LocalBaselineProvider().fetch("北京"))
        self.assertEqual(data["city"], "北京")
        self.assertEqual(data["risk_level"], "LOW")
        self.assertIs(data["is_estimated"], True)
        self.assertEqual(data["source"], "local_baseline")
        self.assertEqual(data["cii_score"], 10.0)

    def test_never_fabricates_medium_or_high(self):
        provider = LocalBaselineProvider()
        for city in ("北京", "Beijing", "A", "", "城市-123", "🚀"):
            with self.subTest(city=city):
                data = asyncio.run(provider.fetch(city))
                self.assertEqual(data["risk_level"], "LOW")
                self.assertNotIn(data["risk_level"], ("MEDIUM", "HIGH"))
                self.assertTrue(data["is_estimated"])
                self.assertLessEqual(data["cii_score"], 10.0)

    def test_alert_is_a_low_data_notice(self):
        data = asyncio.run(LocalBaselineProvider().fetch("上海"))
        self.assertEqual(len(data["active_alerts"]), 1)
        alert = data["active_alerts"][0]
        self.assertEqual(alert["type"], "DATA_NOTICE")
        self.assertEqual(alert["level"], "LOW")
        self.assertIn("上海", data["safety_advice"])


class TestRiskReport(unittest.TestCase):
    def test_to_dict_includes_risk_ts_timestamp(self):
        before = int(time.time() * 1000)
        data = RiskReport(city="上海").to_dict()
        after = int(time.time() * 1000)
        self.assertIn("risk_ts", data)
        self.assertIsInstance(data["risk_ts"], int)
        self.assertGreaterEqual(data["risk_ts"], before)
        self.assertLessEqual(data["risk_ts"], after)

    def test_to_dict_contract_fields(self):
        data = RiskReport(city="广州").to_dict()
        for key in ("city", "cii_score", "risk_level", "crime_score", "weather_score",
                    "political_score", "health_score", "traffic_score", "source",
                    "is_estimated", "active_alerts", "safety_advice", "weather",
                    "traffic", "cii_decomposition", "risk_ts"):
            self.assertIn(key, data)
        self.assertEqual(data["city"], "广州")
        self.assertEqual(data["risk_level"], "LOW")
        self.assertEqual(data["cii_score"], 10.0)
        self.assertFalse(data["is_estimated"])


class TestBuildSnapshot(unittest.TestCase):
    def setUp(self):
        self.service = RiskService()

    def test_snapshot_carries_values_and_timestamp(self):
        before = int(time.time() * 1000)
        snapshot = self.service.build_snapshot(
            {
                "city": "杭州",
                "cii_score": 34.5,
                "risk_level": "MEDIUM",
                "crime_score": 50.0,
                "source": "local_baseline",
                "is_estimated": True,
                "active_alerts": [{"title": "t"}],
                "safety_advice": "a",
                "cii_decomposition": {"method": "weighted_composite"},
            },
            weather={"condition": "晴"},
            traffic={"status_code": "1"},
        )
        after = int(time.time() * 1000)
        self.assertIn("risk_ts", snapshot)
        self.assertGreaterEqual(snapshot["risk_ts"], before)
        self.assertLessEqual(snapshot["risk_ts"], after)
        self.assertEqual(snapshot["city"], "杭州")
        self.assertEqual(snapshot["cii_score"], 34.5)
        self.assertEqual(snapshot["risk_level"], "MEDIUM")
        self.assertEqual(snapshot["crime_score"], 50.0)
        self.assertEqual(snapshot["source"], "local_baseline")
        self.assertTrue(snapshot["is_estimated"])
        self.assertEqual(snapshot["weather"], {"condition": "晴"})
        self.assertEqual(snapshot["traffic"], {"status_code": "1"})
        self.assertEqual(snapshot["cii_decomposition"], {"method": "weighted_composite"})
        self.assertEqual(snapshot["signal_sources"]["safety"]["provider"], "local_baseline")
        self.assertTrue(snapshot["signal_sources"]["safety"]["available"])
        self.assertTrue(snapshot["signal_sources"]["safety"]["estimated"])
        self.assertTrue(snapshot["signal_sources"]["weather"]["available"])
        self.assertTrue(snapshot["signal_sources"]["traffic"]["available"])

    def test_snapshot_preserves_provider_freshness_metadata(self):
        snapshot = self.service.build_snapshot(
            {"city": "成都", "source": "risk-feed", "retrieved_at": "2026-09-22T08:00:00Z"},
            weather={"provider": "weather-feed", "updated_at": 1770000000000},
            traffic={"source": "traffic-feed", "timestamp": "2026-09-22T08:01:00Z", "estimated": True},
        )

        self.assertEqual(snapshot["signal_sources"]["safety"]["retrieved_at"], "2026-09-22T08:00:00Z")
        self.assertEqual(snapshot["signal_sources"]["weather"]["provider"], "weather-feed")
        self.assertEqual(snapshot["signal_sources"]["weather"]["retrieved_at"], 1770000000000)
        self.assertEqual(snapshot["signal_sources"]["traffic"]["provider"], "traffic-feed")
        self.assertTrue(snapshot["signal_sources"]["traffic"]["estimated"])

    def test_snapshot_defaults_are_neutral(self):
        snapshot = self.service.build_snapshot({})
        self.assertEqual(snapshot["cii_score"], 10.0)
        self.assertEqual(snapshot["risk_level"], "LOW")
        self.assertFalse(snapshot["is_estimated"])
        self.assertEqual(snapshot["active_alerts"], [])
        self.assertEqual(snapshot["source"], "")
        for source in snapshot["signal_sources"].values():
            self.assertFalse(source["available"])
            self.assertFalse(source["estimated"])
            self.assertIsNone(source["retrieved_at"])


class TestRiskServiceCache(unittest.TestCase):
    PAYLOAD = {
        "cii_score": 42.0,
        "risk_level": "MEDIUM",
        "source": "stub",
        "active_alerts": [],
    }

    def test_same_city_is_served_from_cache(self):
        stub = _StubProvider(self.PAYLOAD)
        service = _service_with(stub)
        self.assertGreater(service._intel_ttl, 0.0)

        first = asyncio.run(service.get_city_safety_intel("北京"))
        second = asyncio.run(service.get_city_safety_intel("北京"))

        self.assertEqual(stub.calls, ["北京"])
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(first, second)
        self.assertIs(first, second)
        self.assertEqual(service.cached_city_count(), 1)
        self.assertEqual(first["cii_score"], 42.0)

    def test_cache_key_ignores_case_and_surrounding_spaces(self):
        stub = _StubProvider(self.PAYLOAD)
        service = _service_with(stub)
        asyncio.run(service.get_city_safety_intel("Beijing"))
        asyncio.run(service.get_city_safety_intel("  beijing  "))
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(service.cached_city_count(), 1)

    def test_different_cities_are_cached_separately(self):
        stub = _StubProvider(self.PAYLOAD)
        service = _service_with(stub)
        asyncio.run(service.get_city_safety_intel("北京"))
        asyncio.run(service.get_city_safety_intel("上海"))
        self.assertEqual(stub.calls, ["北京", "上海"])
        self.assertEqual(service.cached_city_count(), 2)

    def test_provider_returning_none_falls_back_to_local_baseline(self):
        stub = _StubProvider(None)
        service = _service_with(stub)
        intel = asyncio.run(service.get_city_safety_intel("广州"))
        self.assertEqual(stub.calls, ["广州"])
        self.assertEqual(intel["source"], "local_baseline")
        self.assertEqual(intel["risk_level"], "LOW")
        self.assertTrue(intel["is_estimated"])
        self.assertEqual(service.cached_city_count(), 1)

    def test_provider_exception_falls_back_instead_of_raising(self):
        service = _service_with(_BoomProvider())
        intel = asyncio.run(service.get_city_safety_intel("深圳"))
        self.assertEqual(intel["source"], "local_baseline")
        self.assertEqual(intel["risk_level"], "LOW")

    def test_aggregate_city_risk_fuses_local_signals_offline(self):
        service = RiskService()

        async def _no_public_alert(city):
            return None

        service._fetch_public_weather_alert = _no_public_alert
        intel = asyncio.run(service.aggregate_city_risk(
            {"source": "local_baseline", "is_estimated": True, "city": "北京", "active_alerts": []},
            "北京",
            weather={"condition": "暴雨"},
            traffic={"status_code": "4"},
            is_weekend=True,
        ))
        # 维度：crime 10 / political 10 / health 15 / weather 80 / traffic 85
        # CII = 3.5 + 2.0 + 2.25 + 12.0 + 12.75 = 32.5 -> MEDIUM (>= 25)
        self.assertEqual(intel["crime_score"], 10.0)
        self.assertEqual(intel["political_score"], 10.0)
        self.assertEqual(intel["health_score"], 15.0)
        self.assertEqual(intel["weather_score"], 80.0)
        self.assertEqual(intel["traffic_score"], 85.0)
        self.assertEqual(intel["cii_score"], 32.5)
        self.assertEqual(intel["risk_level"], "MEDIUM")
        self.assertTrue(intel["is_estimated"])
        self.assertEqual(intel["cii_decomposition"]["method"], "weighted_composite")
        self.assertEqual(intel["fusion"]["source"], "local_multi_source_fusion")
        titles = {a["title"] for a in intel["active_alerts"]}
        self.assertIn("暴雨风险", titles)
        self.assertIn("严重拥堵", titles)


if __name__ == "__main__":
    unittest.main(verbosity=2)
