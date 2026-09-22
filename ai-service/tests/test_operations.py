# -*- coding: utf-8 -*-
"""Tests for small operational API contracts."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LLM_API_KEY", "unit-test-key")

from api.operations import amap_poi, risk_global


class FakeToolbox:
    amap_key = "configured-for-test"

    async def get_dynamic_pois(self, city, keywords, types, limit):
        self.call = (city, keywords, types, limit)
        return [{
            "name": "灵隐寺",
            "type": "风景名胜",
            "address": "灵隐路",
            "location": "120.1012,30.2401",
            "map_image": "https://provider.invalid/map?key=must-not-leak",
        }]


class TestAmapPoiEndpoint(unittest.IsolatedAsyncioTestCase):
    async def test_returns_normalized_public_shape(self):
        with patch("api.operations.ExpertToolbox", FakeToolbox):
            result = await amap_poi({"city": "杭州", "keywords": "西湖特色景点", "limit": 6})

        self.assertTrue(result["available"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["pois"][0]["lng"], "120.1012")
        self.assertEqual(result["pois"][0]["lat"], "30.2401")
        self.assertNotIn("map_image", result["pois"][0])

    async def test_rejects_incomplete_query_without_provider_call(self):
        result = await amap_poi({"city": "杭州"})
        self.assertFalse(result["available"])
        self.assertEqual(result["pois"], [])


class TestGlobalRiskEndpoint(unittest.IsolatedAsyncioTestCase):
    async def test_limits_queries_and_preserves_snapshot_contract(self):
        async def fake_snapshot(city, coordinate, baseline):
            return ({
                "city": city,
                "source": "test-provider",
                "risk_ts": 1770000000000,
                "is_estimated": False,
                "signal_sources": {"safety": {"provider": "test-provider", "available": True}},
            }, [{"type": "WEATHER_CHANGE"}] if baseline else [])

        payload = {"cities": [
            {"city": "北京", "coordinate": [116.4, 39.9]},
            {"city": "北京"},
            {"city": "东京", "coordinate": [139.7, 35.6]},
            {"city": "巴黎"},
            {"city": "纽约"},
            {"city": "悉尼"},
            {"city": "伦敦"},
        ]}
        with patch("api.operations._risk_snapshot", new=AsyncMock(side_effect=fake_snapshot)) as mocked:
            result = await risk_global(payload)

        self.assertEqual([item["city"] for item in result["results"]], ["北京", "东京", "巴黎", "纽约", "悉尼", "伦敦"])
        self.assertEqual(mocked.await_count, 6)
        self.assertTrue(result["results"][0]["available"])
        self.assertEqual(result["results"][0]["snapshot"]["signal_sources"]["safety"]["provider"], "test-provider")
        self.assertTrue(any(error["code"] == "CITY_LIMIT" for error in result["errors"]))

    async def test_rejects_missing_cities_without_provider_call(self):
        with patch("api.operations._risk_snapshot", new=AsyncMock()) as mocked:
            result = await risk_global({})
        self.assertEqual(result["results"], [])
        self.assertEqual(result["errors"][0]["code"], "CITIES_REQUIRED")
        mocked.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
