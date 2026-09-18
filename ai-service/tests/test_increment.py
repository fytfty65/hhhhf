"""二次增量（解析 / 配额执行 / 响应度度量）的确定性单测。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.increment import (  # noqa: E402
    build_node_from_candidate,
    enforce_quota,
    measure_increment,
    parse_increment,
)

PLAN = {"route": [
    {"day": 1, "name": "钟楼酒店", "type": "住宿", "time": "09:00", "cost_estimate": "¥300",
     "data_sources": {"cost_estimate": "amap"}, "lnglat": [108.94, 34.26]},
    {"day": 1, "name": "陕西历史博物馆", "type": "博物馆", "time": "10:30", "cost_estimate": "¥0",
     "data_sources": {"cost_estimate": "amap"}, "lnglat": [108.95, 34.22]},
    {"day": 1, "name": "回民街小吃", "type": "餐饮", "time": "12:30", "cost_estimate": "¥60",
     "data_sources": {"cost_estimate": "amap"}, "lnglat": [108.94, 34.26]},
    {"day": 2, "name": "城墙南门", "type": "文化", "time": "09:30", "cost_estimate": "¥54",
     "data_sources": {"cost_estimate": "amap"}, "lnglat": [108.94, 34.25]},
    {"day": 2, "name": "永兴坊美食", "type": "餐饮", "time": "12:30", "cost_estimate": "¥70",
     "data_sources": {"cost_estimate": "amap"}, "lnglat": [108.97, 34.27]},
]}

POOL = {
    "food": [
        {"name": "蒲城会馆", "price": 39, "price_tier": "economy", "lnglat": [108.94, 34.24],
         "type": "餐饮", "rating": "4.5", "open_time": "10:00-21:00", "price_source": "amap", "estimated": False},
        {"name": "陕西会客厅", "price": 88, "price_tier": "economy", "lnglat": [108.95, 34.25],
         "type": "餐饮", "rating": "4.4", "open_time": "10:00-22:00", "price_source": "amap", "estimated": False},
    ],
    "shopping": [
        {"name": "大唐不夜城步行街", "price": None, "price_tier": "unknown", "lnglat": [108.96, 34.21],
         "type": "购物", "rating": "4.6", "open_time": "全天", "price_source": "unavailable", "estimated": True},
    ],
}


class TestParseIncrement(unittest.TestCase):
    def test_exclude_uses_plan_names(self):
        delta = parse_increment("上次排的兵马俑我不想去，城墙也换掉", previous_plan=PLAN)
        self.assertIn("城墙南门", delta["exclude"])
        self.assertFalse(delta["is_noop"])

    def test_exclude_ignores_places_not_in_plan(self):
        delta = parse_increment("兵马俑不想去", previous_plan=PLAN)
        self.assertEqual(delta["exclude"], [])

    def test_quota_increase_food(self):
        delta = parse_increment("这次想多吃点地道美食", previous_plan=PLAN)
        self.assertEqual(delta["quota"].get("food"), 2)
        self.assertIn("increase:food", delta["matched"])

    def test_quota_explicit_count(self):
        delta = parse_increment("再多安排 3 个博物馆", previous_plan=PLAN)
        self.assertEqual(delta["quota"].get("cultural"), 3)

    def test_quota_reduce(self):
        delta = parse_increment("购物安排太多了，少一点", previous_plan=PLAN)
        self.assertEqual(delta["quota"].get("shopping"), -2)

    def test_upgrade_and_downgrade(self):
        self.assertEqual(parse_increment("住宿住好一点").get("upgrade", {}).get("hotel"), "quality")
        self.assertEqual(parse_increment("整体省一点").get("downgrade", {}).get("hotel"), "economy")

    def test_structural_fields(self):
        delta = parse_increment("改成 5 天，预算 3000")
        self.assertEqual(delta["days"], 5)
        self.assertEqual(delta["budget"], 3000.0)

    def test_noop(self):
        delta = parse_increment("今天天气不错", previous_plan=PLAN)
        self.assertTrue(delta["is_noop"])


class TestEnforceQuota(unittest.TestCase):
    def test_adds_food_nodes_from_pool(self):
        result = enforce_quota(PLAN, {"food": 2}, pool=POOL, context={"days": 2})
        self.assertEqual(len(result["added"]), 2)
        from core.candidate_index import intent_of
        food_after = [n for n in result["plan"]["route"] if intent_of(n) == "food"]
        self.assertEqual(len(food_after), 4)
        self.assertIn("补充了", result["disclosure"])

    def test_reports_unmet_when_pool_short(self):
        result = enforce_quota(PLAN, {"food": 5}, pool=POOL, context={"days": 2})
        self.assertEqual(len(result["added"]), 2)
        self.assertEqual(result["unmet"].get("food"), 3)
        self.assertIn("没能补上", result["disclosure"])

    def test_does_not_blast_one_day_past_limit(self):
        result = enforce_quota(PLAN, {"food": 4}, pool={"food": POOL["food"] * 4}, context={"days": 2}, max_nodes_per_day=3)
        per_day: dict = {}
        for node in result["plan"]["route"]:
            per_day[node["day"]] = per_day.get(node["day"], 0) + 1
        self.assertTrue(all(count <= 3 for count in per_day.values()), per_day)

    def test_reduce_keeps_floor(self):
        result = enforce_quota(PLAN, {"food": -5}, pool=POOL, context={"days": 2})
        remaining_food = [n for n in result["plan"]["route"] if n["name"] in {"回民街小吃", "永兴坊美食"}]
        self.assertGreaterEqual(len(remaining_food), 1)  # 一体化底线：不能把餐全删了

    def test_added_node_has_frontend_fields(self):
        result = enforce_quota(PLAN, {"food": 1}, pool=POOL, context={"days": 2})
        node = [n for n in result["plan"]["route"] if n.get("added_by_increment")][0]
        for field in ("day", "name", "location", "time", "type", "lnglat", "cost_estimate", "data_sources", "estimated"):
            self.assertIn(field, node)
        self.assertEqual(node["cost_estimate"], "¥39")

    def test_unknown_price_stays_unverified(self):
        result = enforce_quota(PLAN, {"shopping": 1}, pool=POOL, context={"days": 2})
        node = [n for n in result["plan"]["route"] if n.get("added_by_increment") == "shopping"][0]
        self.assertEqual(node["cost_estimate"], "暂无供应商数据")
        self.assertTrue(node["estimated"])

    def test_does_not_mutate_input(self):
        import copy
        snapshot = copy.deepcopy(PLAN)
        enforce_quota(PLAN, {"food": 1}, pool=POOL, context={"days": 2})
        self.assertEqual(PLAN, snapshot)


class TestMeasureIncrement(unittest.TestCase):
    def test_responsiveness_high_for_targeted_change(self):
        after = enforce_quota(PLAN, {"food": 2}, pool=POOL, context={"days": 2})["plan"]
        report = measure_increment(PLAN, after, {"quota": {"food": 2}})
        self.assertEqual(report["achieved"], 2)
        self.assertEqual(report["target_gain"], 1.0)
        self.assertLess(report["disturbance"], 0.5)
        self.assertGreater(report["responsiveness"], 0.5)

    def test_zero_when_nothing_changed(self):
        report = measure_increment(PLAN, PLAN, {"quota": {"food": 2}})
        self.assertEqual(report["achieved"], 0)
        self.assertEqual(report["responsiveness"], 0.0)

    def test_disturbance_when_plan_rewritten(self):
        other = {"route": [{"day": 1, "name": "完全不同的安排", "type": "文化", "time": "09:00"}]}
        report = measure_increment(PLAN, other, {"quota": {"food": 2}})
        self.assertGreater(report["disturbance"], 0.8)


class TestTierPolicy(unittest.TestCase):
    """升/降档要真的影响候选选择与排序偏好（用户："要接入排序权重"）。"""

    def test_policy_from_upgrade_and_downgrade(self):
        from core.increment import tier_policy

        delta = parse_increment("住宿住好一点，吃的省一点")
        policy = tier_policy(delta)
        self.assertEqual(policy.get("hotel"), 1)
        self.assertEqual(policy.get("food", 0) < 0, True)

    def test_ranking_preferences_mapping(self):
        from core.increment import delta_to_ranking_preferences

        self.assertEqual(
            delta_to_ranking_preferences({"upgrade": {"hotel": "quality"}}).get("accommodation_style"),
            "品质",
        )
        prefs = delta_to_ranking_preferences({"downgrade": {"hotel": "economy", "food": "economy"}})
        self.assertEqual(prefs.get("accommodation_style"), "经济")
        self.assertEqual(prefs.get("budget"), "low")

    def test_upgrade_quota_prefers_higher_tier_candidate(self):
        pool = {
            "hotel": [
                {"name": "青旅床位", "price": 80, "price_tier": "economy", "tier_level": 1, "rating": "4.1"},
                {"name": "精品酒店", "price": 800, "price_tier": "quality", "tier_level": 3, "rating": "4.8"},
            ]
        }
        result = enforce_quota(PLAN, {"hotel": 1}, pool=pool, context={"days": 2}, tier_policy_map={"hotel": 1})
        self.assertEqual(result["added"][0]["name"], "精品酒店")

    def test_downgrade_quota_prefers_cheapest_candidate(self):
        pool = {
            "hotel": [
                {"name": "青旅床位", "price": 80, "price_tier": "economy", "tier_level": 1, "rating": "4.1"},
                {"name": "精品酒店", "price": 800, "price_tier": "quality", "tier_level": 3, "rating": "4.8"},
            ]
        }
        result = enforce_quota(PLAN, {"hotel": 1}, pool=pool, context={"days": 2}, tier_policy_map={"hotel": -1})
        self.assertEqual(result["added"][0]["name"], "青旅床位")


if __name__ == "__main__":
    unittest.main()
