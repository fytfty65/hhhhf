"""候选索引与替换（"换掉不想去的点"）的确定性单测。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.candidate_index import (  # noqa: E402
    apply_exclusions,
    index_candidates,
    intent_of,
    substitute,
    tier_level,
)

PLAN = {"route": [
    {"day": 1, "name": "兵马俑", "type": "文化", "time": "09:00", "lnglat": [109.28, 34.38], "cost_estimate": "¥120",
     "data_sources": {"cost_estimate": "amap"}},
    {"day": 1, "name": "回民街小吃", "type": "餐饮", "time": "12:30", "lnglat": [108.94, 34.26], "cost_estimate": "¥60",
     "data_sources": {"cost_estimate": "amap"}},
    {"day": 1, "name": "钟楼酒店", "type": "住宿", "time": "20:00", "lnglat": [108.94, 34.26], "cost_estimate": "¥300",
     "data_sources": {"cost_estimate": "amap"}},
    {"day": 2, "name": "华清宫", "type": "文化", "time": "09:00", "lnglat": [109.22, 34.36], "cost_estimate": "¥120",
     "data_sources": {"cost_estimate": "amap"}},
    {"day": 2, "name": "永兴坊美食", "type": "餐饮", "time": "12:00", "lnglat": [108.97, 34.27], "cost_estimate": "¥70",
     "data_sources": {"cost_estimate": "amap"}},
]}

CANDIDATES = [
    {"name": "陕西历史博物馆", "price": 0, "price_tier": "free", "lnglat": [108.95, 34.22]},
    {"name": "西安城墙", "price": 54, "price_tier": "economy", "lnglat": [108.94, 34.25]},
    {"name": "碑林博物馆", "price": 65, "price_tier": "economy", "lnglat": [108.95, 34.23]},
    {"name": "子午路张记肉夹馍", "price": 25, "price_tier": "economy", "lnglat": [108.94, 34.24]},
    {"name": "青旅床位", "price": 80, "price_tier": "economy", "lnglat": [108.94, 34.26]},
]


class TestIntentAndTier(unittest.TestCase):
    def test_intent_of_node_and_text(self):
        self.assertEqual(intent_of({"name": "陕西历史博物馆"}), "cultural")
        self.assertEqual(intent_of("回民街小吃"), "food")
        self.assertEqual(intent_of({"name": "某某酒店"}), "hotel")
        self.assertEqual(intent_of("完全无关的名字xyz"), "other")

    def test_tier_level_from_explicit_and_price(self):
        self.assertEqual(tier_level({"price_tier": "free", "price": 0}), 0)
        self.assertEqual(tier_level({"price": 80}), 1)
        self.assertEqual(tier_level({"price": 800}), 3)
        self.assertEqual(tier_level({"price": 5000}), 4)

    def test_index_buckets_sorted_by_price(self):
        index = index_candidates(CANDIDATES)
        self.assertIn("cultural", index)
        prices = [item["price"] for item in index["cultural"]]
        self.assertEqual(prices, sorted(prices))
        self.assertTrue(all("intent" in item and "tier_level" in item for item in index["cultural"]))


class TestSubstitute(unittest.TestCase):
    def test_finds_same_intent_replacement(self):
        index = index_candidates(CANDIDATES)
        replacement = substitute({"name": "兵马俑", "type": "文化"}, index)
        self.assertIsNotNone(replacement)
        self.assertNotEqual(replacement["name"], "兵马俑")

    def test_never_returns_excluded_candidate(self):
        index = index_candidates(CANDIDATES)
        replacement = substitute({"name": "兵马俑", "type": "文化"}, index, exclude_terms=["陕西历史博物馆", "西安城墙"])
        self.assertIsNotNone(replacement)
        self.assertEqual(replacement["name"], "碑林博物馆")

    def test_returns_none_when_no_candidate(self):
        self.assertIsNone(substitute({"name": "兵马俑", "type": "文化"}, {}))

    def test_prefer_cheaper(self):
        index = index_candidates(CANDIDATES)
        replacement = substitute({"name": "某某茶馆", "type": "餐饮"}, index, prefer_cheaper=True)
        self.assertEqual(replacement["name"], "子午路张记肉夹馍")


class TestApplyExclusions(unittest.TestCase):
    def test_removes_and_replaces_keep_skeleton(self):
        index = index_candidates(CANDIDATES)
        result = apply_exclusions(PLAN, ["兵马俑", "华清宫"], index=index)
        names = [node["name"] for node in result["plan"]["route"]]
        self.assertNotIn("兵马俑", names)
        self.assertNotIn("华清宫", names)
        self.assertEqual(len(result["removed"]), 2)
        self.assertEqual(len(result["replaced"]), 2)
        # 两个被移除的点必须换成**不同**的地方（不能重复替换同一个候选）
        replacements = [item["to"] for item in result["replaced"]]
        self.assertEqual(len(set(replacements)), 2)
        # 一体化没被破坏：住宿与餐饮仍在
        self.assertTrue(any("酒店" in n or "青旅" in n for n in names))
        self.assertTrue(any("小吃" in n or "肉夹馍" in n or "美食" in n for n in names))
        self.assertIn("移除", result["disclosure"])

    def test_reports_terms_not_in_plan(self):
        result = apply_exclusions(PLAN, ["根本不存在的景点"], index=index_candidates(CANDIDATES))
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["not_found"], ["根本不存在的景点"])
        self.assertIn("原本就不在方案里", result["disclosure"])

    def test_without_candidates_reports_unreplaced(self):
        result = apply_exclusions(PLAN, ["兵马俑"])
        self.assertEqual(result["removed"], ["兵马俑"])
        self.assertEqual(result["unreplaced"], ["兵马俑"])
        self.assertIn("不会硬凑", result["disclosure"])

    def test_does_not_mutate_input_plan(self):
        import copy
        snapshot = copy.deepcopy(PLAN)
        apply_exclusions(PLAN, ["兵马俑"], index=index_candidates(CANDIDATES))
        self.assertEqual(PLAN, snapshot)

    def test_records_replaced_from(self):
        result = apply_exclusions(PLAN, ["兵马俑"], index=index_candidates(CANDIDATES))
        replaced_nodes = [n for n in result["plan"]["route"] if n.get("replaced_from") == "兵马俑"]
        self.assertEqual(len(replaced_nodes), 1)
        self.assertTrue(replaced_nodes[0]["name"])

    def test_empty_terms_returns_original(self):
        result = apply_exclusions(PLAN, [])
        self.assertEqual(result["plan"], PLAN)
        self.assertEqual(result["disclosure"], "")


if __name__ == "__main__":
    unittest.main()
