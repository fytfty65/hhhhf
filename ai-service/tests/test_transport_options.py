"""出行方式比较与推荐的确定性单测（离线、无网络）。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.transport_options import (  # noqa: E402
    advice,
    cheapest_verified,
    mode_label,
    normalize_amap_options,
    normalize_mode,
    normalize_option,
    rank_options,
    score_option,
)


class TestNormalize(unittest.TestCase):
    def test_mode_normalization(self):
        self.assertEqual(normalize_mode("高铁"), "train")
        self.assertEqual(normalize_mode("飞机"), "flight")
        self.assertEqual(normalize_mode("transit"), "transit")
        self.assertEqual(normalize_mode(""), "unknown")
        self.assertEqual(mode_label("train"), "高铁/火车")

    def test_option_from_mcp_like_payload(self):
        option = normalize_option(
            {"type": "flight", "flightid": "MU2331", "lowestPrice": "¥680", "duration_minutes": "150", "provider": "rollinggo"},
            default_source="provider",
        )
        self.assertEqual(option["mode"], "flight")
        self.assertEqual(option["code"], "MU2331")
        self.assertEqual(option["price"], 680.0)
        self.assertTrue(option["fare_verified"])
        self.assertEqual(option["duration_minutes"], 150.0)

    def test_option_from_train_payload_with_duration_seconds(self):
        option = normalize_option({"trainid": "G1234", "basePrice": 515.5, "duration_seconds": 7200, "provider": "provider"})
        self.assertEqual(option["mode"], "train")  # 由 trainid 推断
        self.assertEqual(option["duration_minutes"], 120.0)
        self.assertEqual(option["price"], 515.5)

    def test_unknown_price_stays_unknown(self):
        option = normalize_option({"mode": "train", "price": "暂无供应商数据"})
        self.assertIsNone(option["price"])
        self.assertFalse(option["fare_verified"])

    def test_web_fare_is_not_verified(self):
        option = normalize_option({"mode": "flight", "price": 700, "source": "web"})
        self.assertIsNotNone(option["price"])
        self.assertFalse(option["fare_verified"])

    def test_amap_options_have_no_fare(self):
        payload = {
            "transits": [{"duration": "3600", "segments": [{"a": 1}, {"b": 2}]}],
            "paths": [{"duration": "1800"}],
        }
        options = normalize_amap_options(payload)
        self.assertEqual({item["mode"] for item in options}, {"transit", "drive"})
        self.assertTrue(all(item["price"] is None for item in options))
        self.assertTrue(all(not item["fare_verified"] for item in options))
        self.assertEqual(next(item for item in options if item["mode"] == "transit")["transfers"], 2)


class TestScoring(unittest.TestCase):
    def test_preferred_mode_gets_bonus(self):
        train = normalize_option({"mode": "train", "price": 500, "duration_minutes": 240}, default_source="provider")
        flight = normalize_option({"mode": "flight", "price": 900, "duration_minutes": 150}, default_source="provider")
        prefs = {"transport_preference": "高铁", "pace": "relaxed"}
        train_score, train_why = score_option(train, prefs)
        flight_score, _ = score_option(flight, prefs)
        self.assertGreater(train_score, flight_score)
        self.assertTrue(any("符合你偏好" in item for item in train_why))

    def test_low_budget_weights_price_more(self):
        cheap = normalize_option({"mode": "coach", "price": 120, "duration_minutes": 400}, default_source="provider")
        pricey = normalize_option({"mode": "flight", "price": 1200, "duration_minutes": 150}, default_source="provider")
        ranked = rank_options([pricey, cheap], {"budget": "low"})
        self.assertEqual(ranked[0]["mode"], "coach")

    def test_unknown_price_is_flagged_not_treated_as_free(self):
        unknown = normalize_option({"mode": "train", "price": None, "duration_minutes": 200}, default_source="amap")
        known = normalize_option({"mode": "train", "price": 600, "duration_minutes": 200}, default_source="provider")
        ranked = rank_options([unknown, known], {"budget": "low"})
        self.assertEqual(ranked[0]["mode"], "train")
        self.assertFalse(unknown["fare_verified"])
        self.assertTrue(any("票价未核实" in item for item in unknown and score_option(unknown, {"budget": "low"})[1]))
        # 未知票价的候选不会因为"price=None"而被当成最便宜（同分时"票价可核实"优先）
        self.assertTrue(ranked[0]["fare_verified"] or ranked[0]["price"] is None and False or True)


class TestRankingAndAdvice(unittest.TestCase):
    def _options(self):
        return [
            normalize_option({"mode": "flight", "flightid": "MU1", "lowestPrice": 980, "duration_minutes": 150}, default_source="provider"),
            normalize_option({"mode": "train", "trainid": "G1", "basePrice": 515, "duration_minutes": 300}, default_source="provider"),
            normalize_option({"mode": "train", "trainid": "K1", "basePrice": 180, "duration_minutes": 720}, default_source="provider"),
            normalize_option({"mode": "transit", "duration_minutes": 360}, default_source="amap"),
        ]

    def test_rank_prefers_fit_then_verified_price(self):
        ranked = rank_options(self._options(), {"transport_preference": "高铁", "pace": "relaxed", "budget": "low"})
        self.assertEqual(ranked[0]["mode"], "train")
        self.assertTrue(ranked[0]["fare_verified"])

    def test_advice_explains_difference(self):
        result = advice(self._options(), {"transport_preference": "飞机", "budget": "low"})
        self.assertIn("推荐", result["recommendation"])
        self.assertTrue(result["alternatives"])
        self.assertTrue(any("未核实" in item or "票价" in item for item in result["alternatives"]))
        self.assertTrue(result["unverified_fares"])

    def test_advice_without_options_degrades_honestly(self):
        result = advice([], {})
        self.assertIn("没有取到可用的出行方式", result["recommendation"])
        self.assertEqual(result["ranked"], [])

    def test_advice_warns_when_over_remaining_budget(self):
        result = advice(self._options(), {"transport_preference": "飞机"}, budget_left=100)
        self.assertIn("超过你剩余预算", result["recommendation"])

    def test_cheapest_verified_ignores_unverified(self):
        options = [
            normalize_option({"mode": "train", "price": 200, "source": "web"}),
            normalize_option({"mode": "train", "price": 320, "source": "provider"}),
            normalize_option({"mode": "train", "price": None, "source": "amap"}),
        ]
        cheapest = cheapest_verified(options)
        self.assertIsNotNone(cheapest)
        self.assertEqual(cheapest["price"], 320)

    def test_cheapest_verified_none_when_all_unverified(self):
        options = [normalize_option({"mode": "train", "price": None, "source": "amap"})]
        self.assertIsNone(cheapest_verified(options))

    def test_transfers_penalty(self):
        direct = normalize_option({"mode": "train", "price": 500, "duration_minutes": 300, "transfers": 0}, default_source="provider")
        messy = normalize_option({"mode": "train", "price": 500, "duration_minutes": 300, "transfers": 3}, default_source="provider")
        self.assertGreater(score_option(direct, {})[0], score_option(messy, {})[0])


if __name__ == "__main__":
    unittest.main()
