"""槽位骨架（一体化硬保证）的确定性单测。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.itinerary_skeleton import (  # noqa: E402
    build_skeleton,
    play_slots_for,
    skeleton_requirements,
    skeleton_summary,
    validate_skeleton,
)


def node(name, day, kind, time="09:00"):
    types = {"hotel": "住宿", "food": "餐饮", "play": "文化"}
    return {"day": day, "name": name, "location": name, "type": types[kind], "time": time, "lnglat": [108.94, 34.26]}


class TestRequirements(unittest.TestCase):
    def test_single_day_trip_has_no_lodging_requirement(self):
        req = skeleton_requirements({"days": 1})
        self.assertEqual(req["lodging_nights"], 0)
        self.assertEqual(req["meals_total"], 1)

    def test_three_day_trip_requires_two_nights(self):
        req = skeleton_requirements({"days": 3})
        self.assertEqual(req["lodging_nights"], 2)
        self.assertEqual(req["meals_total"], 3)
        self.assertEqual(req["meals_target"], 5)  # 软目标：每天午晚两餐

    def test_lodging_nights_override(self):
        req = skeleton_requirements({"days": 3, "lodging_nights": 0})
        self.assertEqual(req["lodging_nights"], 0)

    def test_play_slots_follow_pace(self):
        self.assertEqual(play_slots_for({"preferences": {"pace": "relaxed"}}), 1)
        self.assertEqual(play_slots_for({"preferences": {"pace": "normal"}}), 2)
        self.assertEqual(play_slots_for({"preferences": {"pace": "intense"}}), 3)


class TestBuildSkeleton(unittest.TestCase):
    def test_slots_per_day(self):
        skeleton = build_skeleton({"days": 3, "preferences": {"pace": "relaxed"}})
        self.assertEqual(len(skeleton["days"]), 3)
        first = skeleton["days"][0]["slots"]
        self.assertEqual(len([s for s in first if s["kind"] == "play"]), 1)
        self.assertEqual(len([s for s in first if s["kind"] == "meal"]), 2)
        self.assertEqual(len([s for s in first if s["kind"] == "lodging"]), 1)
        # 最后一夜之后没有住宿槽（3 天只要 2 夜）
        last = skeleton["days"][2]["slots"]
        self.assertEqual(len([s for s in last if s["kind"] == "lodging"]), 0)


class TestValidateSkeleton(unittest.TestCase):
    def test_integrated_plan_passes(self):
        plan = {"route": [
            node("酒店", 1, "hotel"), node("博物馆", 1, "play"), node("小吃街", 1, "food"),
            node("城墙", 2, "play"), node("面馆", 2, "food"),
        ]}
        report = validate_skeleton(plan, {"days": 2})
        self.assertTrue(report["ok"], report["failures"])

    def test_missing_lodging_fails(self):
        plan = {"route": [
            node("博物馆", 1, "play"), node("小吃街", 1, "food"),
            node("城墙", 2, "play"), node("面馆", 2, "food"),
        ]}
        report = validate_skeleton(plan, {"days": 2})
        self.assertFalse(report["ok"])
        self.assertIn("skeleton_lodging_shortfall", [f["code"] for f in report["failures"]])

    def test_missing_meal_on_one_day_fails(self):
        plan = {"route": [
            node("酒店", 1, "hotel"), node("博物馆", 1, "play"), node("小吃街", 1, "food"),
            node("城墙", 2, "play"),
        ]}
        report = validate_skeleton(plan, {"days": 2})
        codes = [f["code"] for f in report["failures"]]
        self.assertIn("skeleton_meal_missing", codes)

    def test_day_without_play_fails(self):
        plan = {"route": [
            node("酒店", 1, "hotel"), node("博物馆", 1, "play"), node("小吃街", 1, "food"),
            node("面馆", 2, "food"),
        ]}
        report = validate_skeleton(plan, {"days": 2})
        self.assertIn("skeleton_play_missing", [f["code"] for f in report["failures"]])

    def test_summary_reports_counts(self):
        plan = {"route": [
            node("酒店", 1, "hotel"), node("博物馆", 1, "play"), node("小吃街", 1, "food"),
            node("城墙", 2, "play"), node("面馆", 2, "food"),
        ]}
        summary = skeleton_summary(plan, {"days": 2})
        self.assertEqual(summary["lodging_nights"], 1)
        self.assertEqual(summary["meals"], 2)
        self.assertEqual(summary["plays"], 2)
        self.assertEqual(summary["days_covered"], 2)


if __name__ == "__main__":
    unittest.main()
