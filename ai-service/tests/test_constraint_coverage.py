"""用户诉求逐条核对（约束覆盖度）的单测。

这一层的规矩：**没渲染出来的"未核实"等于没做**，所以每条诉求都必须有稳定 id 和明确状态；
尤其不许把"缺数据"算成"已落实"。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.constraint_coverage import (  # noqa: E402
    STATUS_ADVISORY,
    STATUS_APPLIED,
    STATUS_MISSING,
    STATUS_PARTIAL,
    STATUS_UNVERIFIED,
    constraint_coverage,
)


def node(day, name, kind="文化", time="10:00", cost="¥100", lnglat=(104.06, 30.67), source="amap", **extra):
    payload = {
        "day": day, "name": name, "location": name, "time": time, "type": kind,
        "cost_estimate": cost,
        "data_sources": {"cost_estimate": source}, "estimated": source != "amap",
    }
    if lnglat is not None:
        payload["lnglat"] = list(lnglat)
    payload.update(extra)
    return payload


def simple_plan():
    """一天：一个玩点 + 一餐 + 一住宿，坐标都在附近，价格来自高德。"""
    return {"route": [
        node(1, "景点A", "文化", "10:00"),
        node(1, "餐馆B", "餐饮", "12:30", cost="¥60"),
        node(1, "酒店H", "住宿", "20:00", cost="¥300"),
    ]}


def status_of(result, identifier):
    for item in result["items"]:
        if item["id"] == identifier:
            return item["status"]
    return None


class TestBasicConstraints(unittest.TestCase):
    def test_must_have_hit_and_miss(self):
        result = constraint_coverage({"days": 1}, simple_plan(), {"must_have": ["酒店", "博物馆"]})
        self.assertEqual(status_of(result, "must_have:酒店"), STATUS_APPLIED)
        self.assertEqual(status_of(result, "must_have:博物馆"), STATUS_MISSING)

    def test_interest_applied_and_missing(self):
        result = constraint_coverage(
            {"days": 1, "preferences": {"interest": ["美食", "自然风光"]}},
            simple_plan(),
            signals={"wants_food": True, "wants_scenic": True},
        )
        self.assertEqual(status_of(result, "interest:food"), STATUS_APPLIED)
        self.assertEqual(status_of(result, "interest:scenic"), STATUS_MISSING)

    def test_compound_mountain_water_missing_says_which_half(self):
        plan = {"route": [node(1, "雪山", "文化"), node(1, "餐馆B", "餐饮", "12:30")]}
        result = constraint_coverage(
            {"days": 1, "preferences": {"interest": ["有山有水"]}},
            plan,
            signals={"wants_scenic": True},
        )
        self.assertEqual(status_of(result, "interest:mountain_water"), STATUS_MISSING)
        item = next(i for i in result["items"] if i["id"] == "interest:mountain_water")
        self.assertIn("一半", item["detail"])

    def test_dietary_and_accessibility_are_unverified_never_applied(self):
        result = constraint_coverage(
            {"days": 1, "preferences": {"dietary": "不吃辣", "accessibility": "老人同行，少台阶"}},
            simple_plan(),
        )
        self.assertEqual(status_of(result, "dietary"), STATUS_UNVERIFIED)
        self.assertEqual(status_of(result, "accessibility"), STATUS_UNVERIFIED)


class TestBudgetAndPace(unittest.TestCase):
    def test_budget_within_verified_cost_is_applied(self):
        result = constraint_coverage({"days": 1, "budget": 1200}, simple_plan())
        self.assertEqual(status_of(result, "budget"), STATUS_APPLIED)

    def test_budget_with_unknown_prices_is_unverifiable_not_applied(self):
        plan = {"route": [node(1, "神秘景点", "文化", cost="暂无供应商数据", source="missing")]}
        result = constraint_coverage({"days": 1, "budget": 500}, plan)
        self.assertEqual(status_of(result, "budget"), STATUS_UNVERIFIED)

    def test_budget_exceeded_is_missing(self):
        result = constraint_coverage({"days": 1, "budget": 100}, simple_plan())
        self.assertEqual(status_of(result, "budget"), STATUS_MISSING)

    def test_pace_status_follows_pacing_score(self):
        relaxed = constraint_coverage(
            {"days": 1, "preferences": {"pace": "relaxed"}}, simple_plan(), {"max_nodes_per_day": 4}
        )
        self.assertEqual(status_of(relaxed, "pace"), STATUS_APPLIED)
        packed = {"route": [node(1, f"点{i}", "文化", f"{8 + i:02d}:00") for i in range(8)]}
        rushed = constraint_coverage(
            {"days": 1, "preferences": {"pace": "relaxed"}}, packed, {"max_nodes_per_day": 4}
        )
        self.assertIn(status_of(rushed, "pace"), {STATUS_PARTIAL, STATUS_MISSING})

    def test_rest_days_partial_when_only_one_low_intensity_day(self):
        plan = {"route": []}
        for day in range(1, 15):
            plan["route"].append(node(day, f"玩点{day}", "文化"))
            if day != 1:
                plan["route"].append(node(day, f"玩点{day}B", "文化", "14:00"))
        result = constraint_coverage(
            {"days": 14, "preferences": {"rest_days": True}}, plan, {"max_nodes_per_day": 4}
        )
        self.assertEqual(status_of(result, "rest_days"), STATUS_PARTIAL)


class TestAnchoringAndChanges(unittest.TestCase):
    def test_anchoring_far_nodes_are_reported(self):
        plan = {
            "route": [
                node(1, "景点A", "文化", lnglat=(104.06, 30.67)),
                node(1, "远处酒店", "住宿", "20:00", lnglat=(104.60, 30.67)),  # ~50km
            ]
        }
        result = constraint_coverage(
            {"days": 1, "preferences": {"anchor_near_scenic": True}}, plan, {"anchoring_km": 15}
        )
        self.assertIn(status_of(result, "anchor_near_scenic"), {STATUS_PARTIAL, STATUS_MISSING})

    def test_anchoring_without_coordinates_is_unverified(self):
        plan = {"route": [node(1, "景点A", "文化", lnglat=None), node(1, "酒店H", "住宿", "20:00", lnglat=None)]}
        result = constraint_coverage(
            {"days": 1, "preferences": {"anchor_near_scenic": True}}, plan, {"anchoring_km": 15}
        )
        self.assertEqual(status_of(result, "anchor_near_scenic"), STATUS_UNVERIFIED)

    def test_exclude_still_present_is_missing(self):
        plan = {"route": [node(1, "兵马俑", "文化"), node(1, "餐馆B", "餐饮", "12:30")]}
        result = constraint_coverage({"days": 1}, plan, increment={"exclude": ["兵马俑"], "is_noop": False})
        self.assertEqual(status_of(result, "exclude:兵马俑"), STATUS_MISSING)

    def test_exclude_removed_with_replacement_is_applied(self):
        plan = {"route": [node(1, "别的博物馆", "文化"), node(1, "餐馆B", "餐饮", "12:30")]}
        result = constraint_coverage(
            {"days": 1},
            plan,
            increment={"exclude": ["兵马俑"], "is_noop": False},
            exclusions={"removed": ["兵马俑"], "unreplaced": [], "not_found": []},
        )
        self.assertEqual(status_of(result, "exclude:兵马俑"), STATUS_APPLIED)

    def test_exclude_removed_without_replacement_is_partial(self):
        plan = {"route": [node(1, "餐馆B", "餐饮", "12:30")]}
        result = constraint_coverage(
            {"days": 1},
            plan,
            increment={"exclude": ["兵马俑"], "is_noop": False},
            exclusions={"removed": ["兵马俑"], "unreplaced": ["兵马俑"], "not_found": []},
        )
        self.assertEqual(status_of(result, "exclude:兵马俑"), STATUS_PARTIAL)

    def test_quota_full_partial_and_none(self):
        plan = simple_plan()
        full = constraint_coverage(
            {"days": 1}, plan, increment={"quota": {"food": 2}, "is_noop": False},
            increment_metrics={"requested": 2, "achieved": 2},
        )
        self.assertEqual(status_of(full, "quota"), STATUS_APPLIED)
        half = constraint_coverage(
            {"days": 1}, plan, increment={"quota": {"food": 2}, "is_noop": False},
            increment_metrics={"requested": 2, "achieved": 1},
        )
        self.assertEqual(status_of(half, "quota"), STATUS_PARTIAL)
        none = constraint_coverage(
            {"days": 1}, plan, increment={"quota": {"food": 2}, "is_noop": False},
            increment_metrics={"requested": 2, "achieved": 0},
        )
        self.assertEqual(status_of(none, "quota"), STATUS_MISSING)

    def test_tier_change_is_advisory_not_applied(self):
        result = constraint_coverage(
            {"days": 1}, simple_plan(),
            increment={"upgrade": {"hotel": "quality"}, "is_noop": False},
            tier_policy_map={"hotel": 1},
        )
        self.assertEqual(status_of(result, "tier:hotel"), STATUS_ADVISORY)

    def test_change_requested_but_nothing_applied_is_missing(self):
        """"说改但没改"必须如实写出来（staleness 机械检测）。"""
        result = constraint_coverage(
            {"days": 1}, simple_plan(), increment={"exclude": ["不存在的点"], "is_noop": False}
        )
        self.assertEqual(status_of(result, "change:applied"), STATUS_MISSING)

    def test_no_change_claimed_when_increment_metrics_exist(self):
        result = constraint_coverage(
            {"days": 1}, simple_plan(),
            increment={"exclude": ["兵马俑"], "is_noop": False},
            exclusions={"removed": ["兵马俑"], "unreplaced": [], "not_found": []},
            increment_metrics={"requested": 1, "achieved": 1},
        )
        self.assertIsNone(status_of(result, "change:applied"))


class TestSummary(unittest.TestCase):
    def test_summary_counts_every_status(self):
        result = constraint_coverage(
            {"days": 1, "budget": 100, "preferences": {"dietary": "不吃辣"}},
            simple_plan(),
            {"must_have": ["酒店", "博物馆"]},
        )
        self.assertIn("共", result["summary"])
        self.assertEqual(result["counts"].get(STATUS_MISSING, 0) >= 1, True)
        self.assertIn("dietary", result["unverified_ids"])
        self.assertIn("must_have:博物馆", result["missing_ids"])

    def test_items_are_capped_but_total_is_honest(self):
        expectations = {"must_have": [f"项目{i}" for i in range(30)]}
        result = constraint_coverage({"days": 1}, simple_plan(), expectations)
        self.assertLessEqual(len(result["items"]), 14)
        self.assertEqual(result["total"], 30)


if __name__ == "__main__":
    unittest.main()
