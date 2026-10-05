"""统一事实记录测试（闭环第①段）。

三条硬口径必须成立：
  1. **未知 ≠ 0**：拿不到就 None/unknown，不许写 0 冒充；
  2. **每条带出处**：source ∈ user_intent / amap / policy / learned_profile / derived /
     user_input / internal / unknown，verified 说明能不能当真；
  3. **缺什么要写出来**：降级登记转成 gaps 清单，保留来源与原因。
"""

import unittest

from core.plan_facts import (
    FACTS_SCHEMA_VERSION,
    build_plan_facts,
    summarize_facts,
)

SIGNALS = {
    "wants_scenic": True,
    "wants_food": True,
    "exclude_cultural": True,
    "distance_tolerance": "high",
    "requirements": ["自然风景", "餐饮/美食", "不介意跑远（跨景区）"],
}
COMPOSITION = {
    "scenic": 11, "cultural": 0, "food": 4, "hotel": 2, "daytime": 15,
    "source": "explicit_preference", "exclude_cultural": True, "park_cap": 0,
    "actual": {"scenic": 11, "landmark": 11, "city_park": 0, "cultural": 0, "food": 4, "hotel": 3},
}
SUPPLY = {
    "status": "ok", "attractions": 20, "foods": 50, "scenic": 18,
    "out_of_town_scenic": 4, "farthest_scenic_km": 76, "widened": ["美食"],
}
FEASIBILITY = {"ok": False, "violations": [{"code": "daily_travel_budget_exceeded", "day": 2}]}
ROUTE = [
    {"day": 1, "name": "博斯腾湖风景名胜区"},
    {"day": 1, "name": "老四号拌面"},
    {"day": 2, "name": "巴音布鲁克大草原"},
]
DEGRADATIONS = {
    "items": [
        {"source": "price_web", "status": "degraded", "reason": "未取到 30% 的价格"},
        {"source": "travel_feasibility", "status": "degraded", "reason": "一天跑不了两个远景区"},
    ]
}


def facts(**overrides):
    kwargs = dict(
        correlation_id="plan-test-1",
        city="库尔勒",
        trip_days=3,
        intent="去库尔勒玩3天，不考虑距离，只想看自然景观，穿插当地美食",
        signals=SIGNALS,
        composition=COMPOSITION,
        supply=SUPPLY,
        feasibility=FEASIBILITY,
        profile_applied={"source": "learned_profile", "tilted": True, "feedback_count": 8, "category_boost": {"scenic": 1.12}},
        reference_profile=None,
        route=ROUTE,
        degradations=DEGRADATIONS,
        generated_at="2026-10-04T00:00:00+00:00",
    )
    kwargs.update(overrides)
    return build_plan_facts(**kwargs)


class FactsShapeTests(unittest.TestCase):
    def test_has_all_sections_and_schema_version(self):
        data = facts()
        self.assertEqual(data["schema_version"], FACTS_SCHEMA_VERSION)
        self.assertEqual(data["correlation_id"], "plan-test-1")
        for section in ("request", "constraints", "suppliers", "decisions", "outcome", "gaps"):
            self.assertIn(section, data)

    def test_every_fact_carries_source_and_verified(self):
        data = facts()
        for section in ("request", "constraints", "suppliers", "decisions", "outcome"):
            for key, entry in data[section].items():
                self.assertIn("source", entry, f"{section}.{key} 缺出处")
                self.assertIn("verified", entry, f"{section}.{key} 缺核实状态")
                self.assertTrue(str(entry["source"]).strip(), f"{section}.{key} 出处为空")

    def test_user_facts_are_marked_as_user_intent(self):
        data = facts()
        self.assertEqual(data["request"]["city"]["source"], "user_intent")
        self.assertEqual(data["constraints"]["exclude_cultural"]["source"], "user_intent")
        self.assertEqual(data["constraints"]["distance_tolerance"]["value"], "high")

    def test_policy_and_data_sources_are_distinguished(self):
        data = facts()
        self.assertEqual(data["suppliers"]["amap_attractions"]["source"], "amap")
        self.assertEqual(data["decisions"]["composition_targets"]["source"], "policy")
        self.assertEqual(data["decisions"]["profile_applied"]["source"], "learned_profile")
        self.assertEqual(data["outcome"]["nodes"]["source"], "derived")


class UnknownNotZeroTests(unittest.TestCase):
    def test_missing_supply_becomes_unknown_not_zero(self):
        data = facts(supply=None)
        entry = data["suppliers"]["amap_attractions"]
        self.assertIsNone(entry["value"], "没有供给记录时不许写 0")
        self.assertEqual(entry["source"], "unknown")
        self.assertFalse(entry["verified"])

    def test_missing_composition_targets_are_unknown(self):
        data = facts(composition=None)
        self.assertIsNone(data["decisions"]["composition_targets"]["value"])
        self.assertFalse(data["decisions"]["composition_targets"]["verified"])
        self.assertIsNone(data["outcome"]["by_category"]["value"])

    def test_missing_trip_days_is_none_not_zero(self):
        data = facts(trip_days=None)
        self.assertIsNone(data["request"]["trip_days"]["value"])

    def test_missing_feasibility_is_unknown(self):
        data = facts(feasibility=None)
        self.assertIsNone(data["outcome"]["travel_violations"]["value"])

    def test_zero_really_means_zero(self):
        """真实是 0 的时候要如实写 0（比如"城市公园 0 个"）。"""
        data = facts()
        self.assertEqual(data["outcome"]["by_category"]["value"]["city_park"], 0)
        self.assertEqual(data["constraints"]["park_cap"]["value"], 0)


class GapsTests(unittest.TestCase):
    def test_gaps_come_from_degradations_with_reason(self):
        data = facts()
        areas = [item["area"] for item in data["gaps"]]
        self.assertIn("price_web", areas)
        self.assertIn("travel_feasibility", areas)
        self.assertTrue(any("一天跑不了两个远景区" in item["reason"] for item in data["gaps"]))

    def test_no_degradations_means_empty_gaps_not_fake_ones(self):
        self.assertEqual(facts(degradations=None)["gaps"], [])


class ReferenceProfileTests(unittest.TestCase):
    def test_not_used_is_recorded_as_false_not_missing(self):
        data = facts(reference_profile=None)
        entry = data["decisions"]["reference_profile_used"]
        self.assertFalse(entry["value"])
        self.assertFalse(entry["verified"], "没有参考画像时不能说'已核实为未使用'")

    def test_used_reference_profile_is_recorded(self):
        data = facts(reference_profile={"applied": True, "reference_count": 1})
        entry = data["decisions"]["reference_profile_used"]
        self.assertTrue(entry["value"])
        self.assertTrue(entry["verified"])


class SummaryTests(unittest.TestCase):
    def test_summary_is_light_and_complete(self):
        summary = summarize_facts(facts())
        self.assertEqual(summary["city"], "库尔勒")
        self.assertEqual(summary["nodes"], 3)
        self.assertEqual(summary["supply_status"], "ok")
        self.assertEqual(summary["gaps"], 2)
        self.assertEqual(summary["correlation_id"], "plan-test-1")
        self.assertLess(len(str(summary)), 500, "事件流摘要要保持轻，不能塞整份事实")

    def test_summary_survives_empty_input(self):
        summary = summarize_facts({})
        self.assertIsNone(summary["city"])
        self.assertEqual(summary["gaps"], 0)


if __name__ == "__main__":
    unittest.main()
