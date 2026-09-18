"""长途分段生成支撑（分段预算 / 休整日 / 段间衔接 / 下一段简报）的确定性单测。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.long_trip import (  # noqa: E402
    allocate_budget,
    build_long_trip_plan,
    continuity_report,
    merge_segment_nodes,
    required_rest_days,
    segment_brief,
    segment_prompt,
    segment_repair_targets,
    summarize_segments,
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


class TestSegmentRepair(unittest.TestCase):
    """按段修补：只重生成失败的段，而不是整段重来。"""

    def _broken_plan(self):
        plan = thirty_day_plan()
        # 让第 2 段（第 8-14 天）缺两天，且这一段没有休整日、住宿也少
        plan["route"] = [n for n in plan["route"] if n["day"] not in (9, 10)]
        plan["route"] = [n for n in plan["route"] if not (n["name"].startswith("酒店") and n["day"] in (12, 13, 14))]
        for n in plan["route"]:
            if n["day"] in range(8, 15) and n["name"].startswith("景点"):
                pass
        plan["route"] = [
            n for n in plan["route"] if not (n["day"] in range(8, 15) and n["name"].endswith("B"))
        ]
        return plan

    def test_targets_identify_the_broken_segment(self):
        targets = segment_repair_targets(self._broken_plan(), CONTEXT)
        self.assertTrue(targets)
        first = targets[0]
        self.assertEqual((first["start_day"], first["end_day"]), (8, 14))
        self.assertTrue(any("没有安排" in reason for reason in first["reasons"]))
        self.assertIn("brief", first)

    def test_targets_empty_for_complete_plan(self):
        self.assertEqual(segment_repair_targets(thirty_day_plan(), CONTEXT), [])

    def test_targets_empty_for_short_trip(self):
        plan = {"route": [node(1, "景点1"), node(1, "酒店1", "住宿")]}
        self.assertEqual(segment_repair_targets(plan, {"days": 2}), [])

    def test_prompt_mentions_range_reasons_and_continuity(self):
        target = segment_repair_targets(self._broken_plan(), CONTEXT)[0]
        prompt = segment_prompt(target)
        self.assertIn("只生成第 8-14 天", prompt)
        self.assertIn("问题", prompt)
        self.assertIn("休整日", prompt)
        self.assertIn("严禁编造", prompt)

    def test_merge_replaces_segment_and_drops_out_of_range(self):
        plan = thirty_day_plan()
        incoming = [
            {"day": 8, "name": "新景点A", "time": "10:00", "type": "文化", "cost_estimate": "¥100"},
            {"day": 9, "name": "新餐馆", "time": "12:30", "type": "餐饮", "cost_estimate": "¥50"},
            {"day": 20, "name": "越界节点", "time": "10:00", "type": "文化", "cost_estimate": "¥100"},
        ]
        result = merge_segment_nodes(plan, 8, 14, incoming)
        names = [n["name"] for n in result["plan"]["route"]]
        self.assertIn("新景点A", names)
        self.assertNotIn("越界节点", names)
        self.assertEqual(result["out_of_range"], [20])
        self.assertEqual(result["added"], 2)
        # 该段原有节点被替换（原本第 8-14 天的"景点8"应已不在）
        self.assertNotIn("景点8", names)
        # 段外节点保持不动
        self.assertIn("景点20", names)

    def test_merge_does_not_mutate_input(self):
        import copy
        plan = thirty_day_plan()
        snapshot = copy.deepcopy(plan)
        merge_segment_nodes(plan, 1, 7, [{"day": 1, "name": "X", "time": "10:00"}])
        self.assertEqual(plan, snapshot)

    def test_summary_flags_segment_needing_repair(self):
        summary = summarize_segments(self._broken_plan(), CONTEXT)
        self.assertEqual(len(summary["segments"]), 5)
        self.assertTrue(any(item["needs_repair"] for item in summary["segments"]))
        self.assertFalse(summary["ok"])

    def test_summary_ok_for_complete_plan(self):
        summary = summarize_segments(thirty_day_plan(), CONTEXT)
        self.assertTrue(summary["ok"])
        self.assertFalse(any(item["needs_repair"] for item in summary["segments"]))


if __name__ == "__main__":
    unittest.main()
