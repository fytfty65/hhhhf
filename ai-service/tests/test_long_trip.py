"""长途分段生成支撑（分段预算 / 休整日 / 段间衔接 / 下一段简报）的确定性单测。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.long_trip import (  # noqa: E402
    allocate_budget,
    build_long_trip_plan,
    continuity_report,
    required_rest_days,
    segment_brief,
)


def node(day, name, kind="文化", time="09:00", lnglat=(104.06, 30.67), cost="¥100", source="amap"):
    return {
        "day": day, "name": name, "location": name, "time": time, "type": kind,
        "lnglat": list(lnglat), "cost_estimate": cost,
        "data_sources": {"cost_estimate": source}, "estimated": False,
    }


def thirty_day_plan(far_city_from_day=8):
    """30 天：每天玩点 + 餐饮，每 7 天一个休整日（只 1 个玩点），第 8 天起换到远处城市。"""
    route = []
    for day in range(1, 31):
        rest = day % 7 == 0
        lnglat = (104.06, 30.67) if day < far_city_from_day else (102.71, 25.05)
        route.append(node(day, f"景点{day}", "文化", "10:00", lnglat))
        if not rest:
            route.append(node(day, f"景点{day}B", "文化", "14:00", lnglat))
        route.append(node(day, f"餐馆{day}", "餐饮", "12:30", lnglat, "¥50"))
        route.append(node(day, f"酒店{day}", "住宿", "20:00", lnglat, "¥300"))
    return {"route": route}


CONTEXT = {"days": 30, "budget": 30000}


class TestSegmentation(unittest.TestCase):
    def test_required_rest_days(self):
        self.assertEqual(required_rest_days(30), 4)
        self.assertEqual(required_rest_days(13), 1)
        self.assertEqual(required_rest_days(6), 0)

    def test_budget_allocation_sums_to_total(self):
        segments = [{"days": 7}, {"days": 7}, {"days": 7}, {"days": 7}, {"days": 2}]
        budgets = allocate_budget(30000, segments)
        self.assertEqual(len(budgets), 5)
        self.assertAlmostEqual(sum(budgets), 30000, places=2)
        self.assertGreater(budgets[0], budgets[-1])  # 2 天的段拿得少

    def test_no_budget_gives_zeros(self):
        self.assertEqual(allocate_budget(0, [{"days": 7}]), [0.0])

    def test_build_plan_segments_and_states(self):
        plan = build_long_trip_plan(CONTEXT)
        self.assertTrue(plan["is_long_trip"])
        self.assertEqual(len(plan["segments"]), 5)
        first = plan["segments"][0]
        self.assertEqual((first["start_day"], first["end_day"]), (1, 7))
        self.assertIsNone(first["carry_in"])
        self.assertIsNotNone(plan["segments"][1]["carry_in"])
        self.assertEqual(first["lodging_nights_expected"], 6)
        self.assertIn("分段生成", " ".join(plan["advisories"]))

    def test_short_trip_not_flagged_long(self):
        plan = build_long_trip_plan({"days": 5})
        self.assertFalse(plan["is_long_trip"])
        self.assertEqual(len(plan["segments"]), 1)


class TestSegmentBrief(unittest.TestCase):
    def test_brief_carries_previous_state_and_budget(self):
        plan = thirty_day_plan()
        structure = build_long_trip_plan(CONTEXT)
        second = structure["segments"][1]
        brief = segment_brief(second, plan)
        self.assertEqual(brief["generate_days"], "8-14")
        self.assertEqual(brief["carry_in"]["after_day"], 7)
        self.assertEqual(brief["carry_in"]["last_node"], "酒店7")
        self.assertGreater(brief["budget"]["already_spent_before"], 0)
        self.assertGreater(brief["rest_days_required"], 0)
        self.assertTrue(any("不要重复" in item for item in brief["constraints"]))

    def test_first_segment_has_no_carry_in_node(self):
        plan = thirty_day_plan()
        structure = build_long_trip_plan(CONTEXT)
        brief = segment_brief(structure["segments"][0], plan)
        self.assertIsNone(brief["carry_in"]["last_node"])


class TestContinuity(unittest.TestCase):
    def test_complete_plan_passes_with_transfer_advisory(self):
        report = continuity_report(thirty_day_plan(), CONTEXT)
        self.assertTrue(report["ok"], report["failures"])
        # 第 7 天在成都、第 8 天到昆明 → 应给出交通日建议
        self.assertTrue(any("交通日" in text for text in report["advisories"]), report["advisories"])

    def test_day_gap_fails(self):
        plan = thirty_day_plan()
        plan["route"] = [n for n in plan["route"] if n["day"] != 12]
        report = continuity_report(plan, CONTEXT)
        self.assertFalse(report["ok"])
        self.assertIn("long_trip_day_gap", [item["code"] for item in report["failures"]])

    def test_day_out_of_range_fails(self):
        plan = thirty_day_plan()
        plan["route"].append(node(31, "超范围景点"))
        report = continuity_report(plan, CONTEXT)
        self.assertIn("long_trip_day_out_of_range", [item["code"] for item in report["failures"]])

    def test_lodging_shortfall_in_segment_fails(self):
        plan = thirty_day_plan()
        plan["route"] = [n for n in plan["route"] if not (n["name"].startswith("酒店") and n["day"] in (2, 3, 4))]
        report = continuity_report(plan, CONTEXT)
        self.assertIn("long_trip_lodging_shortfall", [item["code"] for item in report["failures"]])

    def test_rest_day_advisory(self):
        plan = thirty_day_plan()
        # 把休整日也塞满 → 低强度日变少
        for day in range(1, 31):
            plan["route"].append(node(day, f"加班景点{day}", "文化", "16:00", (104.06, 30.67)))
        report = continuity_report(plan, CONTEXT)
        self.assertTrue(any("休整" in text for text in report["advisories"]), report["advisories"])

    def test_segment_budget_advisory(self):
        plan = thirty_day_plan()
        report = continuity_report(plan, {"days": 30, "budget": 3000})
        self.assertTrue(any("超过该段分配" in text for text in report["advisories"]), report["advisories"])

    def test_explicit_min_rest_days_from_expectations(self):
        plan = thirty_day_plan()
        report = continuity_report(plan, {"days": 30, "budget": 30000, "expectations": {"min_rest_days": 10}})
        self.assertTrue(any("用例要求至少 10 个休整日" in text for text in report["advisories"]))

    def test_short_trip_has_no_rest_requirement(self):
        plan = {"route": [node(1, "景点1"), node(1, "酒店1", "住宿"), node(2, "景点2"), node(2, "酒店2", "住宿")]}
        report = continuity_report(plan, {"days": 2, "budget": 2000})
        self.assertTrue(report["ok"])
        self.assertEqual(report["rest_days_required"], 0)


if __name__ == "__main__":
    unittest.main()
