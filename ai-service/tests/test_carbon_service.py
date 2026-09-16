import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

"""碳足迹 / 绿色交通评估服务单元测试（api/carbon_service.py）。

全部为纯离线确定性计算，不发起任何网络请求。期望值直接由
EMISSION_FACTORS / DEFAULT_SEGMENT_KM / KG_CO2_PER_TREE_YEAR 与
compute_footprint 的聚合口径（先逐段 round(km * factor, 3)，再求和后
round(..., 2)；green_ratio = green_km / total_km）推导。
"""

from api.carbon_service import (
    DEFAULT_SEGMENT_KM,
    EMISSION_FACTORS,
    GREEN_MODES,
    KG_CO2_PER_TREE_YEAR,
    compute_footprint,
    segment_emission,
)

# 已知行程：地铁 10km + 驾车 20km + 公交 5km
KNOWN_SEGMENTS = [
    {"from": "A", "to": "B", "mode": "metro", "distance_km": 10.0},
    {"from": "B", "to": "C", "mode": "driving", "distance_km": 20.0},
    {"from": "C", "to": "D", "mode": "bus", "distance_km": 5.0},
]
ZERO_EMISSION_MODES = ("walking", "cycling")


class TestEmissionFactorTable(unittest.TestCase):
    def test_every_mode_factor_is_positive_except_zero_emission_modes(self):
        self.assertTrue(EMISSION_FACTORS, "因子表不能为空")
        for mode, factor in EMISSION_FACTORS.items():
            with self.subTest(mode=mode):
                if mode in ZERO_EMISSION_MODES:
                    self.assertEqual(factor, 0.0)
                else:
                    self.assertGreater(factor, 0.0)

    def test_walking_and_cycling_are_zero_emission(self):
        self.assertEqual(EMISSION_FACTORS["walking"], 0.0)
        self.assertEqual(EMISSION_FACTORS["cycling"], 0.0)

    def test_expected_modes_are_all_present(self):
        self.assertEqual(
            set(EMISSION_FACTORS),
            {"walking", "cycling", "metro", "bus", "transit",
             "highspeed_rail", "driving", "taxi", "flight"},
        )

    def test_documented_factor_magnitudes(self):
        # 防止因子表被静默改动导致口径漂移：量级与注释中的公开估算一致
        self.assertAlmostEqual(EMISSION_FACTORS["metro"], 0.028, places=6)
        self.assertAlmostEqual(EMISSION_FACTORS["bus"], 0.035, places=6)
        self.assertAlmostEqual(EMISSION_FACTORS["transit"], 0.030, places=6)
        self.assertAlmostEqual(EMISSION_FACTORS["highspeed_rail"], 0.040, places=6)
        self.assertAlmostEqual(EMISSION_FACTORS["driving"], 0.190, places=6)
        self.assertAlmostEqual(EMISSION_FACTORS["taxi"], 0.190, places=6)
        self.assertAlmostEqual(EMISSION_FACTORS["flight"], 0.280, places=6)
        self.assertLess(EMISSION_FACTORS["metro"], EMISSION_FACTORS["driving"])
        self.assertLess(EMISSION_FACTORS["highspeed_rail"], EMISSION_FACTORS["flight"])


class TestSegmentEmission(unittest.TestCase):
    def test_known_segment_arithmetic(self):
        seg = segment_emission("driving", 20.0)
        self.assertEqual(seg["mode"], "driving")
        self.assertEqual(seg["mode_label"], "驾车")
        self.assertEqual(seg["distance_km"], 20.0)
        self.assertAlmostEqual(seg["emission_kg"], round(20.0 * EMISSION_FACTORS["driving"], 3), places=9)
        self.assertAlmostEqual(seg["emission_kg"], 3.8, places=9)
        self.assertFalse(seg["is_estimated"])

    def test_zero_emission_mode_is_exactly_zero(self):
        for mode in ZERO_EMISSION_MODES:
            with self.subTest(mode=mode):
                seg = segment_emission(mode, 7.5)
                self.assertEqual(seg["emission_kg"], 0.0)
                self.assertEqual(seg["distance_km"], 7.5)

    def test_missing_or_non_positive_distance_falls_back_to_documented_default(self):
        self.assertEqual(DEFAULT_SEGMENT_KM, 3.0)
        for raw in (None, 0, 0.0, -5.0):
            with self.subTest(distance_km=raw):
                seg = segment_emission("driving", raw)
                self.assertEqual(seg["distance_km"], DEFAULT_SEGMENT_KM)
                self.assertTrue(seg["is_estimated"])
                self.assertAlmostEqual(
                    seg["emission_kg"],
                    round(DEFAULT_SEGMENT_KM * EMISSION_FACTORS["driving"], 3),
                    places=9,
                )
                self.assertAlmostEqual(seg["emission_kg"], 0.57, places=9)


class TestComputeFootprintArithmetic(unittest.TestCase):
    def test_total_and_per_segment_emission(self):
        result = compute_footprint(KNOWN_SEGMENTS)
        segments = result["segments"]
        self.assertEqual(len(segments), 3)

        self.assertAlmostEqual(segments[0]["emission_kg"], 10.0 * 0.028, places=9)   # 0.28
        self.assertAlmostEqual(segments[1]["emission_kg"], 20.0 * 0.190, places=9)   # 3.80
        self.assertAlmostEqual(segments[2]["emission_kg"], 5.0 * 0.035, places=9)    # 0.175
        self.assertEqual([s["index"] for s in segments], [0, 1, 2])
        self.assertEqual([(s["from"], s["to"]) for s in segments],
                         [("A", "B"), ("B", "C"), ("C", "D")])
        self.assertEqual([s["mode"] for s in segments], ["metro", "driving", "bus"])

        expected_total = round(round(10.0 * 0.028, 3) + round(20.0 * 0.190, 3) + round(5.0 * 0.035, 3), 2)
        self.assertAlmostEqual(result["total_kg"], expected_total, places=9)
        self.assertAlmostEqual(result["total_kg"], 4.25, places=9)
        self.assertAlmostEqual(result["total_km"], 35.0, places=9)
        self.assertAlmostEqual(result["green_km"], 15.0, places=9)

    def test_segment_distance_and_names_default(self):
        result = compute_footprint([
            {"mode": "bus", "distance_km": 4.0},
            {"from": "X", "mode": "metro", "distance_km": 4.0},
        ])
        self.assertEqual(result["segments"][0]["from"], "第1段起点")
        self.assertEqual(result["segments"][0]["to"], "第1段终点")
        self.assertEqual(result["segments"][1]["from"], "X")
        self.assertEqual(result["segments"][1]["to"], "第2段终点")

    def test_mode_is_normalized_case_and_whitespace(self):
        result = compute_footprint([{"mode": "  DRIVING ", "distance_km": 10.0}])
        self.assertEqual(result["segments"][0]["mode"], "driving")
        self.assertAlmostEqual(result["segments"][0]["emission_kg"], 1.9, places=9)

    def test_non_dict_segments_are_skipped(self):
        result = compute_footprint(["x", None, 3, {"mode": "bus", "distance_km": 2.0}])
        self.assertEqual(len(result["segments"]), 1)
        self.assertAlmostEqual(result["total_km"], 2.0, places=9)
        self.assertAlmostEqual(result["total_kg"], round(2.0 * 0.035, 3), places=9)


class TestGreenRatio(unittest.TestCase):
    def test_mixed_trip_ratio(self):
        result = compute_footprint(KNOWN_SEGMENTS)
        # green_km = 地铁 10 + 公交 5 = 15；total_km = 35
        self.assertAlmostEqual(result["green_ratio"], round(15.0 / 35.0, 3), places=9)
        self.assertAlmostEqual(result["green_ratio"], 0.429, places=9)

    def test_all_green_ratio_is_one(self):
        result = compute_footprint([{"mode": m, "distance_km": 10.0} for m in sorted(GREEN_MODES)])
        self.assertEqual(result["green_ratio"], 1.0)
        self.assertAlmostEqual(result["green_km"], 50.0, places=9)
        self.assertEqual(result["total_kg"], 0.0)

    def test_non_green_trip_ratio_is_zero(self):
        result = compute_footprint([{"mode": "driving", "distance_km": 10.0}])
        self.assertEqual(result["green_ratio"], 0.0)
        self.assertEqual(result["green_km"], 0.0)

    def test_zero_distance_edge_case_returns_one(self):
        # 无有效里程（空列表 / 全部非 dict）时约定 green_ratio = 1.0，而不是 0 或 ZeroDivisionError
        for segments in ([], ["x", None, 7]):
            with self.subTest(segments=segments):
                result = compute_footprint(segments)
                self.assertEqual(result["total_km"], 0.0)
                self.assertEqual(result["total_kg"], 0.0)
                self.assertEqual(result["green_ratio"], 1.0)
                self.assertEqual(result["tree_equivalent"], 0.0)

    def test_zero_distance_segment_is_estimated_not_zero_emission(self):
        # 距离为 0 时按缺省距离估算，不能制造「零排放」假象
        result = compute_footprint([{"mode": "driving", "distance_km": 0}])
        self.assertEqual(result["segments"][0]["distance_km"], DEFAULT_SEGMENT_KM)
        self.assertTrue(result["segments"][0]["is_estimated"])
        self.assertGreater(result["total_kg"], 0.0)
        self.assertEqual(result["green_ratio"], 0.0)
        self.assertTrue(result["is_estimated"])


class TestAlternatives(unittest.TestCase):
    def test_green_modes_get_no_alternative(self):
        for mode in sorted(GREEN_MODES):
            with self.subTest(mode=mode):
                result = compute_footprint([{"mode": mode, "distance_km": 10.0}])
                self.assertEqual(result["alternatives"], [])
                self.assertEqual(result["saved_if_green_kg"], 0)

    def test_transit_is_not_green_and_gets_metro_alternative(self):
        self.assertNotIn("transit", GREEN_MODES)
        result = compute_footprint([{"mode": "transit", "distance_km": 10.0}])
        alt = result["alternatives"][0]
        self.assertEqual(alt["alternative_mode"], "地铁")
        self.assertAlmostEqual(alt["saved_kg"], round(10.0 * (0.030 - 0.028), 3), places=9)
        self.assertAlmostEqual(alt["saved_kg"], 0.02, places=9)

    def test_driving_saved_kg_matches_factor_difference(self):
        result = compute_footprint([{"from": "B", "to": "C", "mode": "driving", "distance_km": 20.0}])
        self.assertEqual(len(result["alternatives"]), 1)
        alt = result["alternatives"][0]
        emission = result["segments"][0]["emission_kg"]
        expected = round(emission - 20.0 * EMISSION_FACTORS["metro"], 3)
        self.assertEqual(alt["saved_kg"], expected)
        self.assertAlmostEqual(alt["saved_kg"], round(20.0 * (0.190 - 0.028), 3), places=9)
        self.assertAlmostEqual(alt["saved_kg"], 3.24, places=9)
        self.assertEqual(alt["current_mode"], "驾车")
        self.assertEqual(alt["alternative_mode"], "地铁")
        self.assertEqual(result["saved_if_green_kg"], alt["saved_kg"])

    def test_taxi_and_flight_alternatives(self):
        taxi = compute_footprint([{"mode": "taxi", "distance_km": 10.0}])["alternatives"][0]
        self.assertEqual(taxi["alternative_mode"], "地铁")
        self.assertAlmostEqual(taxi["saved_kg"], round(10.0 * (0.190 - 0.028), 3), places=9)

        flight = compute_footprint([{"mode": "flight", "distance_km": 100.0}])["alternatives"][0]
        self.assertEqual(flight["alternative_mode"], "高铁")
        self.assertAlmostEqual(flight["saved_kg"], round(100.0 * (0.280 - 0.040), 3), places=9)
        self.assertAlmostEqual(flight["saved_kg"], 24.0, places=9)

    def test_saved_if_green_is_sum_of_alternatives(self):
        result = compute_footprint([
            {"mode": "driving", "distance_km": 20.0},
            {"mode": "flight", "distance_km": 100.0},
        ])
        self.assertEqual(len(result["alternatives"]), 2)
        self.assertAlmostEqual(
            result["saved_if_green_kg"],
            round(sum(a["saved_kg"] for a in result["alternatives"]), 3),
            places=9,
        )


class TestTreeEquivalent(unittest.TestCase):
    def test_uses_documented_kg_per_tree_constant(self):
        self.assertEqual(KG_CO2_PER_TREE_YEAR, 21.0)
        result = compute_footprint(KNOWN_SEGMENTS)
        self.assertAlmostEqual(
            result["tree_equivalent"],
            round(result["total_kg"] / KG_CO2_PER_TREE_YEAR, 2),
            places=9,
        )
        self.assertAlmostEqual(result["tree_equivalent"], round(4.25 / 21.0, 2), places=9)
        self.assertAlmostEqual(result["tree_equivalent"], 0.2, places=9)

    def test_scale_is_proportional_to_total(self):
        one = compute_footprint([{"mode": "driving", "distance_km": 10.0}])["tree_equivalent"]
        ten = compute_footprint([{"mode": "driving", "distance_km": 100.0}])["tree_equivalent"]
        self.assertGreater(ten, one)
        self.assertAlmostEqual(ten, round(100.0 * 0.190 / KG_CO2_PER_TREE_YEAR, 2), places=9)


class TestProvenance(unittest.TestCase):
    def test_response_carries_source_is_estimated_and_method(self):
        result = compute_footprint(KNOWN_SEGMENTS)
        self.assertIn("source", result)
        self.assertIn("is_estimated", result)
        self.assertIn("method", result)
        self.assertEqual(result["source"], "carbon_emission_factors_estimation")
        self.assertEqual(result["method"], "distance_x_emission_factor")
        self.assertIsInstance(result["is_estimated"], bool)
        self.assertFalse(result["is_estimated"])

    def test_is_estimated_true_when_any_segment_distance_missing(self):
        result = compute_footprint([
            {"mode": "metro", "distance_km": 10.0},
            {"mode": "bus"},  # 距离缺失 -> 估算
        ])
        self.assertTrue(result["is_estimated"])
        self.assertTrue(result["segments"][1]["is_estimated"])
        self.assertFalse(result["segments"][0]["is_estimated"])


class TestUnknownModeFallback(unittest.TestCase):
    def test_unknown_mode_falls_back_to_transit_factor(self):
        seg = segment_emission("hoverboard", 12.0)
        self.assertEqual(seg["mode"], "transit")
        self.assertEqual(seg["mode_label"], "公交/地铁")
        self.assertEqual(seg["is_estimated"], False)
        self.assertAlmostEqual(seg["emission_kg"], round(12.0 * EMISSION_FACTORS["transit"], 3), places=9)
        self.assertAlmostEqual(seg["emission_kg"], 0.36, places=9)

    def test_footprint_with_unknown_mode_equals_transit(self):
        unknown = compute_footprint([{"mode": "hoverboard", "distance_km": 12.0}])
        transit = compute_footprint([{"mode": "transit", "distance_km": 12.0}])
        self.assertEqual(unknown["total_kg"], transit["total_kg"])
        self.assertEqual(unknown["segments"][0]["mode"], "transit")

    def test_empty_mode_falls_back_to_transit(self):
        # compute_footprint 对空 mode 用 "transit" 兜底（不崩溃、不产生 0 因子假象）
        result = compute_footprint([{"mode": "", "distance_km": 10.0}])
        self.assertEqual(result["segments"][0]["mode"], "transit")
        self.assertAlmostEqual(result["total_kg"], round(10.0 * EMISSION_FACTORS["transit"], 3), places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
