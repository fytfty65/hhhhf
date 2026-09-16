"""Tests for the Monte-Carlo itinerary simulation (core/simulation.py).

This module exists to replace estimates that were either fabricated (hardcoded
satisfaction scores echoed by the LLM) or not derived from their own inputs (a
crowdedness percentage that ignored the score it had just computed). The tests
therefore focus on the properties that make the output trustworthy: determinism,
monotonic percentiles, honest handling of missing prices, and that real input
changes actually move the output.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import simulation as sim
from core.simulation import (
    CATEGORY_DWELL_SIGMA,
    CATEGORY_QUEUE_MEDIAN_MIN,
    INDOOR_CATEGORIES,
)


def sample_nodes():
    return [
        {
            "day": "1", "time": "Day 1 | 09:00", "location": "博物馆",
            "tags": ["博物馆", "文化"], "category": "museum",
            "dwell_minutes": 120, "travel_minutes": 20, "transport": "metro", "cost": 60,
        },
        {
            "day": "1", "time": "Day 1 | 13:00", "location": "老街",
            "tags": ["美食"], "category": "food",
            "dwell_minutes": 90, "travel_minutes": 15, "transport": "walking", "cost": 80,
        },
        {
            "day": "2", "time": "Day 2 | 10:00", "location": "湖滨公园",
            "tags": ["风景", "自然"], "category": "nature",
            "dwell_minutes": 150, "travel_minutes": 40, "transport": "driving", "cost": 30,
        },
    ]


class CategoryAndParsingTests(unittest.TestCase):
    def test_category_is_read_from_the_category_field(self):
        self.assertEqual(sim.category_of({"category": "museum"}), "museum")
        self.assertEqual(sim.category_of({"category": "FOOD"}), "food")

    def test_category_falls_back_to_tags(self):
        self.assertEqual(sim.category_of({"tags": ["自然", "风景"]}), "other")
        self.assertEqual(sim.category_of({"tags": ["博物馆"]}), "other")
        # Tags are matched against the known category vocabulary.
        self.assertEqual(sim.category_of({"tags": ["nature"]}), "nature")
        self.assertEqual(sim.category_of({"tags": ["food"]}), "food")

    def test_hotel_is_detected_from_the_is_hotel_flag_and_tags(self):
        self.assertEqual(sim.category_of({"is_hotel": True}), "hotel")
        self.assertEqual(sim.category_of({"tags": ["住宿"]}), "hotel")

    def test_time_of_day_bucket_boundaries(self):
        self.assertEqual(sim.time_of_day_bucket("Day 1 | 08:30"), "early")
        self.assertEqual(sim.time_of_day_bucket("Day 1 | 10:00"), "morning")
        self.assertEqual(sim.time_of_day_bucket("Day 1 | 11:30"), "midday")
        self.assertEqual(sim.time_of_day_bucket("Day 1 | 13:59"), "midday")
        self.assertEqual(sim.time_of_day_bucket("Day 1 | 14:00"), "afternoon")
        self.assertEqual(sim.time_of_day_bucket("Day 1 | 18:00"), "evening")
        self.assertEqual(sim.time_of_day_bucket("Day 1 | 22:00"), "late")
        self.assertEqual(sim.time_of_day_bucket("nonsense"), "morning")

    def test_weather_factor_penalises_bad_weather_only(self):
        self.assertEqual(sim.weather_dwell_factor("晴"), 1.0)
        self.assertEqual(sim.weather_dwell_factor("多云"), 1.0)
        self.assertLess(sim.weather_dwell_factor("暴雨"), 0.5)
        self.assertLess(sim.weather_dwell_factor("特大暴雨"), sim.weather_dwell_factor("小雨"))
        # Unknown condition must be neutral, not punitive.
        self.assertEqual(sim.weather_dwell_factor(""), 1.0)
        self.assertEqual(sim.weather_dwell_factor("某种未收录天气"), 1.0)

    def test_dwell_minutes_reads_numbers_out_of_free_text(self):
        self.assertEqual(sim.dwell_minutes_of({"dwell_minutes": 45}), 45.0)
        self.assertEqual(sim.dwell_minutes_of({"duration": "约90分钟"}), 90.0)
        self.assertEqual(sim.dwell_minutes_of({}), sim.DEFAULT_DWELL_MINUTES)


class CostParsingTests(unittest.TestCase):
    """Distinguishing "free" from "unknown" is the whole point here."""

    def test_numeric_cost_is_used(self):
        self.assertEqual(sim.cost_of({"cost": 0}), 0.0)
        self.assertEqual(sim.cost_of({"cost": 80}), 80.0)
        self.assertEqual(sim.cost_of({"cost_estimate": "¥80"}), 80.0)

    def test_explicit_free_is_zero_not_unknown(self):
        self.assertEqual(sim.cost_of({"cost": 0}), 0.0)
        self.assertEqual(sim.cost_of({"cost": "0元"}), 0.0)

    def test_missing_or_sentinel_cost_is_none(self):
        self.assertIsNone(sim.cost_of({}))
        self.assertIsNone(sim.cost_of({"cost": None}))
        self.assertIsNone(sim.cost_of({"cost": "暂无供应商数据"}))
        self.assertIsNone(sim.cost_of({"cost": True}))
        self.assertIsNone(sim.cost_of({"cost": "未知"}))


class PersistenceOfKnownEmptySampleSetTests(unittest.TestCase):
    def test_empty_itinerary_returns_a_zeroed_result_without_crashing(self):
        result = sim.simulate_plan([], samples=100)
        self.assertEqual(result.assumptions.nodes, 0)
        self.assertEqual(result.total_minutes_p50, 0.0)
        self.assertEqual(result.total_minutes_p90, 0.0)
        # No nodes means nothing is missing a price, so cost is trivially complete.
        self.assertEqual(result.cost_complete_probability, 1.0)
        self.assertIsNone(result.budget_overrun_probability)


class DeterminismTests(unittest.TestCase):
    def test_same_seed_produces_identical_output(self):
        first = sim.simulate_plan(sample_nodes(), samples=500, seed=7).to_dict()
        second = sim.simulate_plan(sample_nodes(), samples=500, seed=7).to_dict()
        self.assertEqual(first, second)

    def test_different_seeds_produce_different_output(self):
        first = sim.simulate_plan(sample_nodes(), samples=500, seed=1).to_dict()
        second = sim.simulate_plan(sample_nodes(), samples=500, seed=2).to_dict()
        self.assertNotEqual(first["total_minutes"]["p50"], second["total_minutes"]["p50"])


class PercentileTests(unittest.TestCase):
    def test_p90_is_never_below_p50(self):
        for seed in range(6):
            result = sim.simulate_plan(sample_nodes(), samples=400, seed=seed)
            self.assertGreaterEqual(
                result.total_minutes_p90, result.total_minutes_p50,
                "p90 must dominate p50 for every sample run",
            )

    def test_mean_lies_between_p50_and_the_top_of_the_distribution(self):
        result = sim.simulate_plan(sample_nodes(), samples=800, seed=11)
        self.assertGreaterEqual(result.total_minutes_mean, result.total_minutes_p50)
        self.assertLessEqual(result.total_minutes_mean, result.total_minutes_p90 * 1.5)

    def test_p50_is_near_the_deterministic_sum_of_dwell_and_travel(self):
        """The median should track the nominal plan, not drift away from it.

        Nominal total = (120+20) + (90+15) + (150+40) = 435 minutes. The sampled
        median includes queueing and multiplicative spread, so it must exceed the
        nominal figure but stay in the same order of magnitude.
        """
        result = sim.simulate_plan(sample_nodes(), samples=1500, seed=13)
        self.assertGreater(result.total_minutes_p50, 435)
        self.assertLess(result.total_minutes_p50, 435 * 3)

    def test_per_day_p90_is_reported_per_day(self):
        result = sim.simulate_plan(sample_nodes(), samples=400, seed=17)
        self.assertEqual(set(result.per_day_minutes_p90), {"1", "2"})
        # Day 2 holds one long outdoor stop; day 1 holds two shorter stops.
        self.assertGreater(result.per_day_minutes_p90["1"], 0)
        self.assertGreater(result.per_day_minutes_p90["2"], 0)


class InputSensitivityTests(unittest.TestCase):
    def test_longer_dwell_increases_the_projected_total(self):
        base = sim.simulate_plan(sample_nodes(), samples=600, seed=23)
        longer = sample_nodes()
        for node in longer:
            node["dwell_minutes"] = node["dwell_minutes"] * 3
        stretched = sim.simulate_plan(longer, samples=600, seed=23)
        self.assertGreater(stretched.total_minutes_p50, base.total_minutes_p50)

    def test_bad_weather_shrinks_outdoor_time_but_not_indoor_time(self):
        # All nodes indoors: weather must not change the outcome materially.
        indoor = [
            {"day": "1", "time": "Day 1 | 10:00", "category": "museum",
             "dwell_minutes": 100, "travel_minutes": 10, "transport": "walking", "cost": 10}
        ]
        fine = sim.simulate_plan(indoor, samples=400, seed=29, weather_condition="晴")
        storm = sim.simulate_plan(indoor, samples=400, seed=29, weather_condition="暴雨")
        self.assertAlmostEqual(fine.total_minutes_p50, storm.total_minutes_p50, delta=1.0)

        # All nodes outdoors: a storm must shorten the day noticeably. Nature is
        # not in INDOOR_CATEGORIES, which is what makes this a real signal.
        self.assertNotIn("nature", INDOOR_CATEGORIES)
        outdoor = [
            {"day": "1", "time": "Day 1 | 10:00", "category": "nature",
             "dwell_minutes": 200, "travel_minutes": 10, "transport": "walking", "cost": 10}
        ]
        fine_out = sim.simulate_plan(outdoor, samples=400, seed=31, weather_condition="晴")
        storm_out = sim.simulate_plan(outdoor, samples=400, seed=31, weather_condition="暴雨")
        self.assertLess(storm_out.total_minutes_p50, fine_out.total_minutes_p50)

    def test_wider_category_sigma_produces_a_wider_spread(self):
        tight = [{"day": "1", "time": "Day 1 | 10:00", "category": "museum",
                  "dwell_minutes": 120, "travel_minutes": 0, "cost": 0}]
        loose = [{"day": "1", "time": "Day 1 | 10:00", "category": "shopping",
                  "dwell_minutes": 120, "travel_minutes": 0, "cost": 0}]
        self.assertGreater(CATEGORY_DWELL_SIGMA["shopping"], CATEGORY_DWELL_SIGMA["museum"])

        tight_r = sim.simulate_plan(tight, samples=1500, seed=37)
        loose_r = sim.simulate_plan(loose, samples=1500, seed=37)
        tight_spread = tight_r.total_minutes_p90 - tight_r.total_minutes_p50
        loose_spread = loose_r.total_minutes_p90 - loose_r.total_minutes_p50
        self.assertGreater(loose_spread, tight_spread)


class BudgetTests(unittest.TestCase):
    def test_no_budget_means_no_overrun_probability(self):
        result = sim.simulate_plan(sample_nodes(), samples=200, seed=41)
        self.assertIsNone(result.budget_overrun_probability)

    def test_generous_budget_never_overruns(self):
        result = sim.simulate_plan(sample_nodes(), samples=300, seed=43, budget=100000)
        self.assertEqual(result.budget_overrun_probability, 0.0)

    def test_tight_budget_always_overruns(self):
        result = sim.simulate_plan(sample_nodes(), samples=300, seed=43, budget=1)
        self.assertEqual(result.budget_overrun_probability, 1.0)

    def test_cost_totals_sum_only_the_priced_nodes(self):
        result = sim.simulate_plan(sample_nodes(), samples=100, seed=47)
        # 60 + 80 + 30 = 170, and every node carried a price.
        self.assertAlmostEqual(result.cost_known_p50, 170.0, places=2)
        self.assertEqual(result.cost_complete_probability, 1.0)


class HonestGapReportingTests(unittest.TestCase):
    def test_unpriced_nodes_are_counted_and_cost_completeness_drops(self):
        nodes = sample_nodes()
        nodes[0]["cost"] = "暂无供应商数据"
        result = sim.simulate_plan(nodes, samples=100, seed=53)
        self.assertEqual(result.assumptions.unpriced_nodes, 1)
        self.assertEqual(result.cost_complete_probability, 0.0)
        # The known subtotal must not silently include the unknown node as zero
        # *and* claim completeness.
        self.assertAlmostEqual(result.cost_known_p50, 110.0, places=2)

    def test_fully_unpriced_itinerary_does_not_report_a_zero_total_as_fact(self):
        nodes = [{"day": "1", "time": "Day 1 | 10:00", "category": "museum", "dwell_minutes": 60}]
        result = sim.simulate_plan(nodes, samples=100, seed=59)
        self.assertEqual(result.assumptions.unpriced_nodes, 1)
        self.assertEqual(result.cost_known_p50, 0.0)
        self.assertEqual(result.cost_complete_probability, 0.0)

    def test_defaulted_inputs_are_reported(self):
        nodes = [{"day": "1", "location": "某地"}]
        result = sim.simulate_plan(nodes, samples=50, seed=61)
        self.assertEqual(result.assumptions.defaulted_dwell, 1)
        self.assertEqual(result.assumptions.defaulted_travel, 1)
        self.assertEqual(result.assumptions.sample_count, 50)


class MemberSatisfactionTests(unittest.TestCase):
    def _members(self):
        return [
            {"name": "美食家", "role": "寻味探索", "interests": ["美食"]},
            {"name": "摄影控", "role": "视觉体验", "interests": ["风景"]},
            {"name": "无偏好", "role": "", "interests": []},
        ]

    def test_satisfaction_is_a_distribution_not_a_constant(self):
        result = sim.simulate_plan(sample_nodes(), members=self._members(), samples=300, seed=67)
        stats = result.member_satisfaction["美食家"]
        self.assertIn("satisfaction_p50", stats)
        self.assertIn("satisfaction_floor_p10", stats)
        self.assertLessEqual(stats["satisfaction_floor_p10"], stats["satisfaction_p50"])

    def test_a_member_whose_interest_is_covered_scores_higher(self):
        result = sim.simulate_plan(sample_nodes(), members=self._members(), samples=300, seed=71)
        foodie = result.member_satisfaction["美食家"]["deterministic_utility"]
        photographer = result.member_satisfaction["摄影控"]["deterministic_utility"]
        # The route has a food node and a nature node, so both should be rewarded
        # above the neutral midpoint used for a member with no stated interests.
        self.assertGreater(foodie, 0.5)
        self.assertGreater(photographer, 0.5)

    def test_member_without_stated_interests_gets_the_neutral_midpoint(self):
        result = sim.simulate_plan(sample_nodes(), members=self._members(), samples=200, seed=73)
        self.assertAlmostEqual(
            result.member_satisfaction["无偏好"]["deterministic_utility"], 0.5, places=6
        )

    def test_no_members_means_no_satisfaction_block(self):
        result = sim.simulate_plan(sample_nodes(), samples=100, seed=79)
        self.assertEqual(result.member_satisfaction, {})

    def test_utilities_stay_within_zero_and_one(self):
        result = sim.simulate_plan(sample_nodes(), members=self._members(), samples=200, seed=83)
        for name, stats in result.member_satisfaction.items():
            self.assertGreaterEqual(stats["satisfaction_floor_p10"], 0.0, name)
            self.assertLessEqual(stats["satisfaction_p50"], 1.0, name)


class SerializationTests(unittest.TestCase):
    def test_to_dict_is_json_serializable_and_rounded(self):
        import json

        result = sim.simulate_plan(sample_nodes(), members=[{"name": "A", "interests": ["美食"]}],
                                   samples=200, seed=89, budget=200)
        payload = result.to_dict()
        text = json.dumps(payload, ensure_ascii=False)  # must not raise
        self.assertIn("total_minutes", text)
        self.assertIn("assumptions", payload)
        self.assertEqual(payload["assumptions"]["basis"], "monte_carlo")
        self.assertLessEqual(len(str(payload["total_minutes"]["p50"]).split(".")[-1]), 1)

    def test_tables_are_internally_consistent(self):
        for category in CATEGORY_DWELL_SIGMA:
            self.assertIn(category, CATEGORY_QUEUE_MEDIAN_MIN, category)
            self.assertGreaterEqual(CATEGORY_DWELL_SIGMA[category], 0.0)


if __name__ == "__main__":
    unittest.main()
