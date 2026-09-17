"""规划质量评分器的确定性单测（阶段 0）。

覆盖：硬约束门禁（天数/预算/必去/时间窗/开放时间）、加权维度（偏好覆盖/节奏/空间/
真实性/多样性）、多方案稳定性，以及金标需求集的 schema 自检。
全部离线、无随机——CI 里 `python -m unittest discover -s tests` 会跑到。
"""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_quality import (  # noqa: E402
    DEFAULT_WEIGHTS,
    check_hard_constraints,
    evaluate_plan,
    haversine_km,
    parse_clock,
    parse_open_window,
    score_diversity,
    score_pacing,
    score_preference_coverage,
    score_space_efficiency,
    score_truthfulness,
    stability_score,
)

GOLDEN = Path(__file__).resolve().parent / "eval" / "golden_cases.json"


def node(name, day, time, lnglat=(108.94, 34.26), node_type="文化", cost="¥50",
         open_time="08:00-18:00", source="amap", estimated=False, **extra):
    return {
        "day": day,
        "name": name,
        "location": name,
        "time": time,
        "lnglat": list(lnglat),
        "type": node_type,
        "cost_estimate": cost,
        "open_time": open_time,
        "rating": "4.5",
        "data_sources": {"cost_estimate": source, "open_time": source, "rating": source},
        "estimated": estimated,
        **extra,
    }


def good_plan():
    return {"route": [
        node("钟楼酒店", 1, "09:00", (108.94, 34.26), "住宿", "¥300", "00:00-23:59"),
        node("陕西历史博物馆", 1, "10:30", (108.95, 34.22), "博物馆", "¥0", "09:00-17:00"),
        node("回民街小吃", 1, "12:30", (108.94, 34.26), "餐饮", "¥60", "10:00-22:00"),
        node("大雁塔", 1, "15:00", (108.96, 34.22), "文化", "¥50", "08:00-18:00"),
        node("城墙南门", 2, "09:30", (108.94, 34.25), "文化", "¥54", "08:00-20:00"),
        node("回民街午餐", 2, "12:30", (108.94, 34.26), "餐饮", "¥60", "10:00-22:00"),
        node("大唐不夜城", 2, "17:00", (108.96, 34.21), "夜景", "¥0", "00:00-23:59"),
    ]}


BASE_CONTEXT = {"city": "西安", "days": 2, "budget": 2000, "preferences": {"pace": "relaxed", "interest": ["博物馆", "美食"]}}
BASE_EXPECTATIONS = {"must_have": ["酒店", "餐"], "min_nodes_per_day": 2, "max_nodes_per_day": 4}


class TestClockParsing(unittest.TestCase):
    def test_parse_clock_variants(self):
        self.assertEqual(parse_clock("09:00"), 540)
        self.assertEqual(parse_clock("Day 1 | 09:30"), 570)
        self.assertEqual(parse_clock("9:05"), 545)
        self.assertIsNone(parse_clock("暂无供应商数据"))
        self.assertIsNone(parse_clock(""))
        self.assertIsNone(parse_clock("晚上"))

    def test_parse_open_window(self):
        self.assertEqual(parse_open_window("09:00-17:00"), (540, 1020))
        self.assertEqual(parse_open_window("09:00～17:30"), (540, 1050))
        self.assertEqual(parse_open_window("08:00"), (480, 23 * 60))
        self.assertIsNone(parse_open_window("暂无供应商数据"))
        self.assertIsNone(parse_open_window("周一闭馆"))

    def test_haversine_scale(self):
        # 西安钟楼 -> 大雁塔 约 4-5 公里
        distance = haversine_km((108.94, 34.26), (108.96, 34.22))
        self.assertTrue(3.0 < distance < 6.0, distance)


class TestHardConstraints(unittest.TestCase):
    def test_good_plan_passes_and_has_no_unverifiable(self):
        report = check_hard_constraints(BASE_CONTEXT, good_plan(), BASE_EXPECTATIONS)
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["unverifiable"], [])

    def test_empty_day_fails(self):
        plan = {"route": [n for n in good_plan()["route"] if n["day"] == 1]}
        report = check_hard_constraints(BASE_CONTEXT, plan, BASE_EXPECTATIONS)
        codes = [item["code"] for item in report["failures"]]
        self.assertIn("day_empty", codes)

    def test_thin_day_fails(self):
        plan = {"route": [
            node("酒店", 1, "09:00", node_type="住宿", open_time="00:00-23:59"),
            node("博物馆", 1, "10:00", node_type="博物馆"),
            node("回民街", 2, "12:00", node_type="餐饮"),
        ]}
        report = check_hard_constraints(BASE_CONTEXT, plan, BASE_EXPECTATIONS)
        codes = [item["code"] for item in report["failures"]]
        self.assertIn("day_too_thin", codes)

    def test_budget_exceeded_only_when_prices_known(self):
        plan = {"route": [
            node("酒店", 1, "09:00", node_type="住宿", cost="¥2000", open_time="00:00-23:59"),
            node("博物馆", 1, "10:00", node_type="博物馆", cost="¥500"),
            node("回民街", 2, "12:00", node_type="餐饮", cost="¥100"),
            node("城墙", 2, "15:00", node_type="文化", cost="¥100"),
        ]}
        report = check_hard_constraints(BASE_CONTEXT, plan, BASE_EXPECTATIONS)
        self.assertIn("budget_exceeded", [item["code"] for item in report["failures"]])

    def test_unknown_price_is_unverifiable_not_zero(self):
        plan = {"route": [
            node("酒店", 1, "09:00", node_type="住宿", cost="暂无供应商数据", open_time="00:00-23:59"),
            node("博物馆", 1, "10:00", node_type="博物馆", cost="暂无供应商数据"),
            node("回民街", 2, "12:00", node_type="餐饮", cost="暂无供应商数据"),
            node("城墙", 2, "15:00", node_type="文化", cost="暂无供应商数据"),
        ]}
        report = check_hard_constraints(BASE_CONTEXT, plan, BASE_EXPECTATIONS)
        self.assertNotIn("budget_exceeded", [item["code"] for item in report["failures"]])
        self.assertIn("budget_partial", [item["code"] for item in report["unverifiable"]])

    def test_must_have_missing_fails(self):
        plan = {"route": [
            node("大雁塔", 1, "09:00"), node("小雁塔", 1, "11:00"),
            node("城墙", 2, "09:00"), node("碑林", 2, "11:00"),
        ]}
        report = check_hard_constraints(BASE_CONTEXT, plan, BASE_EXPECTATIONS)
        self.assertIn("must_have_missing", [item["code"] for item in report["failures"]])

    def test_time_window_and_order(self):
        plan = {"route": [
            node("酒店", 1, "05:00", node_type="住宿", open_time="00:00-23:59"),
            node("夜宵摊", 1, "23:30", node_type="餐饮", open_time="18:00-23:59"),
            node("博物馆", 2, "14:00", node_type="博物馆"),
        ]}
        report = check_hard_constraints(BASE_CONTEXT, plan, BASE_EXPECTATIONS)
        codes = [item["code"] for item in report["failures"]]
        self.assertIn("time_out_of_window", codes)

        reversed_plan = {"route": [
            node("酒店", 1, "11:00", node_type="住宿", open_time="00:00-23:59"),
            node("博物馆", 1, "09:00", node_type="博物馆"),
        ]}
        report2 = check_hard_constraints(BASE_CONTEXT, reversed_plan, BASE_EXPECTATIONS)
        self.assertIn("day_out_of_order", [item["code"] for item in report2["failures"]])

    def test_open_time_conflict_and_missing(self):
        plan = {"route": [
            node("酒店", 1, "09:00", node_type="住宿", open_time="00:00-23:59"),
            node("博物馆", 1, "07:00", node_type="博物馆", open_time="09:00-17:00"),
            node("回民街", 2, "12:00", node_type="餐饮", open_time="暂无供应商数据"),
            node("城墙", 2, "15:00", node_type="文化", open_time="08:00-20:00"),
        ]}
        report = check_hard_constraints(BASE_CONTEXT, plan, BASE_EXPECTATIONS)
        self.assertIn("opening_hours_conflict", [item["code"] for item in report["failures"]])
        self.assertIn("open_time_missing", [item["code"] for item in report["unverifiable"]])


class TestDimensions(unittest.TestCase):
    def test_preference_coverage(self):
        context = {"preferences": {"interest": ["博物馆", "美食", "购物"]}}
        report = score_preference_coverage(context, good_plan())
        self.assertIn("cultural", report["addressed"])
        self.assertIn("food", report["addressed"])
        self.assertIn("shopping", report["missing"])
        self.assertTrue(0 < report["value"] < 1)

    def test_preference_coverage_empty_request(self):
        report = score_preference_coverage({}, good_plan())
        self.assertEqual(report["value"], 1.0)

    def test_pacing_penalises_overpacked_day(self):
        crowded = {"route": [node(f"景点{i}", 1, f"{8 + i:02d}:00") for i in range(8)]}
        report = score_pacing({"days": 1}, crowded, {"max_nodes_per_day": 4})
        self.assertLess(report["value"], 1.0)
        self.assertTrue(any("超过上限" in p for p in report["penalties"]))

    def test_pacing_detects_meal_window_and_duration(self):
        report = score_pacing({"days": 2}, good_plan(), BASE_EXPECTATIONS)
        self.assertTrue(all(day["has_lunch_window"] for day in report["days"]))
        self.assertEqual(report["value"], 1.0)

    def test_pacing_flags_overlong_day(self):
        plan = {"route": [node(f"点{i}", 1, f"{9 + i:02d}:00") for i in range(6)]}
        report = score_pacing({"daily_active_minutes": 120}, plan, {"max_nodes_per_day": 6})
        self.assertTrue(any("超过 120 分钟上限" in p for p in report["penalties"]))

    def test_space_efficiency_rewards_compact_and_flags_missing_coords(self):
        compact = {"route": [node("a", 1, "09:00", (108.94, 34.26)), node("b", 1, "10:00", (108.95, 34.26))]}
        far = {"route": [node("a", 1, "09:00", (108.94, 34.26)), node("b", 1, "10:00", (110.09, 34.49))]}
        no_coords = {"route": [dict(node("a", 1, "09:00"), lnglat=None), dict(node("b", 1, "10:00"), lnglat=None)]}
        self.assertGreater(score_space_efficiency({}, compact)["value"], score_space_efficiency({}, far)["value"])
        self.assertEqual(score_space_efficiency({}, no_coords)["value"], 0.0)
        self.assertGreaterEqual(score_space_efficiency({}, far)["long_hops"], 1)

    def test_truthfulness_requires_sources(self):
        verified = {"route": [node("a", 1, "09:00"), node("b", 1, "10:00")]}
        unsourced = {"route": [node("a", 1, "09:00", source="unverified"), node("b", 1, "10:00", source="seed_template")]}
        self.assertEqual(score_truthfulness(verified)["value"], 1.0)
        self.assertEqual(score_truthfulness(unsourced)["value"], 0.0)
        self.assertEqual(score_truthfulness(unsourced)["unsourced_count"], 2)

    def test_truthfulness_flags_claim_without_source(self):
        plan = {"route": [{"day": 1, "name": "某景点", "time": "09:00", "rating": "4.9",
                           "data_sources": {"rating": "unverified"}}]}
        report = score_truthfulness(plan)
        self.assertEqual(report["unsourced_count"], 1)
        self.assertIn("rating", report["unsourced_claims"][0]["fields"])

    def test_diversity_penalises_duplicates(self):
        diverse = {"route": [node("博物馆", 1, "09:00", node_type="博物馆"),
                             node("小吃街", 1, "12:00", node_type="餐饮"),
                             node("公园", 2, "09:00", node_type="景区")]}
        duplicated = {"route": [node("博物馆", 1, "09:00", node_type="博物馆"),
                                node("博物馆", 1, "12:00", node_type="博物馆"),
                                node("博物馆", 2, "09:00", node_type="博物馆")]}
        self.assertGreater(score_diversity(diverse)["value"], score_diversity(duplicated)["value"])
        self.assertGreaterEqual(score_diversity(duplicated)["duplicate_names"], 1)

    def test_stability(self):
        self.assertIsNone(stability_score([good_plan()])["value"])
        self.assertEqual(stability_score([good_plan(), good_plan()])["value"], 1.0)
        other = {"route": [node("完全不同的点", 1, "09:00")]}
        self.assertEqual(stability_score([good_plan(), other])["value"], 0.0)


class TestEvaluatePlan(unittest.TestCase):
    def test_verdict_transitions(self):
        passed = evaluate_plan(BASE_CONTEXT, good_plan(), BASE_EXPECTATIONS)
        self.assertTrue(passed["gate"]["passed"])
        self.assertEqual(passed["verdict"], "pass")
        self.assertGreater(passed["score"], 60)

        unverified = {"route": [
            node("酒店", 1, "09:00", node_type="住宿", open_time="暂无供应商数据"),
            node("博物馆", 1, "10:30", node_type="博物馆", open_time="暂无供应商数据"),
            node("回民街", 2, "12:00", node_type="餐饮", open_time="暂无供应商数据"),
            node("城墙", 2, "15:00", node_type="文化", open_time="暂无供应商数据"),
        ]}
        report = evaluate_plan(BASE_CONTEXT, unverified, BASE_EXPECTATIONS)
        self.assertTrue(report["gate"]["passed"])
        self.assertEqual(report["verdict"], "pass_with_unverified")
        self.assertGreater(report["gate"]["unverifiable_count"], 0)

        bad = evaluate_plan(BASE_CONTEXT, {"route": [node("兵马俑", 1, "07:00")]}, BASE_EXPECTATIONS)
        self.assertFalse(bad["gate"]["passed"])
        self.assertEqual(bad["verdict"], "fail")

    def test_weights_are_configurable(self):
        report = evaluate_plan(BASE_CONTEXT, good_plan(), BASE_EXPECTATIONS,
                               weights={"preference_coverage": 1.0, "pacing": 0.0, "space_efficiency": 0.0,
                                        "truthfulness": 0.0, "diversity": 0.0})
        self.assertEqual(report["weights"]["preference_coverage"], 1.0)
        self.assertEqual(report["weights"]["pacing"], 0.0)

    def test_scores_are_bounded(self):
        report = evaluate_plan(BASE_CONTEXT, good_plan(), BASE_EXPECTATIONS)
        self.assertGreaterEqual(report["score"], 0.0)
        self.assertLessEqual(report["score"], 100.0)
        for key, value in report["dimensions"].items():
            self.assertGreaterEqual(value["value"], 0.0, key)
            self.assertLessEqual(value["value"], 1.0, key)


class TestGoldenCases(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.cases = self.payload["cases"]

    def test_case_count_and_unique_ids(self):
        self.assertGreaterEqual(len(self.cases), 20)
        ids = [case["id"] for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_case_is_well_formed(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertIn("request", case)
                self.assertTrue(case["request"].strip())
                context = case["context"]
                self.assertGreaterEqual(int(context["days"]), 1)
                self.assertGreater(int(context["budget"]), 0)
                self.assertIn("title", case)
                expectations = case["expectations"]
                self.assertLessEqual(expectations["min_nodes_per_day"], expectations["max_nodes_per_day"])
                self.assertIsInstance(expectations["must_have"], list)
                self.assertIsInstance(case.get("notes", ""), str)

    def test_case_expectations_are_soft_by_default(self):
        """must_have 必须少而稳（<=2），避免把主观偏好写成硬门禁。"""
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertLessEqual(len(case["expectations"]["must_have"]), 2)

    def test_scorer_runs_on_every_case_without_crashing(self):
        """用空方案跑一遍，确保评分器对所有用例都不炸（缺数据只会记未核实/失败）。"""
        for case in self.cases:
            with self.subTest(case=case["id"]):
                report = evaluate_plan(case["context"], {"route": []},
                                       expectations=case["expectations"], signals=case.get("signals"))
                self.assertIn("score", report)
                self.assertFalse(report["gate"]["passed"])  # 空方案必然不过门禁


if __name__ == "__main__":
    unittest.main()
