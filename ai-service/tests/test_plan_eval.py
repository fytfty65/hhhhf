"""评测框架的**机器判定**部分：长途 horizon（分段连续性 + 首轮分段生成）不能只是人工评审。

这里测的是 tools/eval_plan_quality.py 的 `long_trip_check` / `build_report`：
每条用例都对应"我们对外承诺过的东西"，所以判定必须可复现、可当门禁用。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from tests.test_long_trip import node, thirty_day_plan  # noqa: E402
from tools.eval_plan_quality import (  # noqa: E402
    build_report,
    find_plan_files,
    load_cases,
    long_trip_check,
    render_markdown,
)

FIXTURE_DIR = SERVICE_ROOT / "tests" / "eval" / "fixtures"


def long_case(**horizon):
    base = {
        "segment_days": 7,
        "min_rest_days": 3,
    }
    base.update(horizon)
    return {
        "id": "case-long",
        "title": "超长行程",
        "context": {"city": "成都", "days": 30, "budget": 30000, "preferences": {"pace": "relaxed"}},
        "expectations": {"must_have": ["酒店"], "min_nodes_per_day": 1, "max_nodes_per_day": 4},
        "signals": {"wants_scenic": True},
        "horizon": base,
    }


class TestLongTripCheck(unittest.TestCase):
    def test_no_horizon_means_no_long_trip_check(self):
        case = {"id": "short", "context": {"days": 3}, "expectations": {}}
        self.assertIsNone(long_trip_check(case, {"route": []}))

    def test_complete_long_plan_passes(self):
        check = long_trip_check(long_case(), thirty_day_plan())
        self.assertIsNotNone(check)
        self.assertTrue(check["ok"], check["failures"])
        self.assertEqual(len(check["segments"]), 5)
        self.assertFalse(check["first_round_required"])

    def test_day_gap_is_a_failure(self):
        plan = thirty_day_plan()
        plan["route"] = [n for n in plan["route"] if n["day"] != 12 and n["day"] != 13]
        check = long_trip_check(long_case(), plan)
        codes = {item["code"] for item in check["failures"]}
        self.assertIn("long_trip_day_gap", codes)
        self.assertFalse(check["ok"])

    def test_lodging_shortfall_is_a_failure(self):
        plan = thirty_day_plan()
        plan["route"] = [n for n in plan["route"] if not (n["type"] == "住宿" and n["day"] in range(8, 15))]
        codes = {item["code"] for item in long_trip_check(long_case(), plan)["failures"]}
        self.assertIn("long_trip_lodging_shortfall", codes)

    def test_first_round_required_but_missing_is_a_failure(self):
        check = long_trip_check(long_case(first_round_required=True), thirty_day_plan())
        codes = {item["code"] for item in check["failures"]}
        self.assertIn("long_trip_first_round_missing", codes)
        self.assertIsNone(check["first_round"])

    def test_first_round_with_failed_segments_is_a_failure(self):
        plan = thirty_day_plan()
        plan["long_trip"] = {"first_round": {"chunk_days": 7, "generated": [1, 2], "failed": [3], "skipped": []}}
        check = long_trip_check(long_case(first_round_required=True), plan)
        codes = {item["code"] for item in check["failures"]}
        self.assertIn("long_trip_first_round_failed", codes)
        self.assertEqual(check["first_round"]["generated"], [1, 2])

    def test_first_round_all_generated_passes(self):
        plan = thirty_day_plan()
        plan["long_trip"] = {"first_round": {"chunk_days": 7, "generated": [1, 2, 3, 4, 5], "failed": [], "skipped": []}}
        check = long_trip_check(long_case(first_round_required=True), plan)
        self.assertTrue(check["ok"], check["failures"])

    def test_rest_day_shortfall_is_only_an_advisory(self):
        plan = thirty_day_plan()
        # 把每天第二个玩点补上 → 没有低强度日；这不是硬失败（跨城/长线本来可能排满）
        for day in range(1, 31):
            plan["route"].append(node(day, f"加排{day}", "文化", "16:00"))
        check = long_trip_check(long_case(min_rest_days=4), plan)
        self.assertTrue(check["ok"], check["failures"])
        self.assertTrue(any("休整" in text for text in check["advisories"]))


class TestReportAggregation(unittest.TestCase):
    def _write(self, directory: Path, case_id: str, plan) -> None:
        import json
        (directory / f"{case_id}.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    def test_report_counts_long_trip_failures_and_renders_section(self):
        case = long_case(first_round_required=True)
        plan = thirty_day_plan()
        plan["long_trip"] = {"first_round": {"chunk_days": 7, "generated": [1, 2], "failed": [3], "skipped": []}}
        with tempfile.TemporaryDirectory() as tmp:
            plans_dir = Path(tmp)
            self._write(plans_dir, "case-long", plan)
            report = build_report([case], plans_dir)
        self.assertEqual(report["long_trip_cases"], 1)
        self.assertEqual(report["long_trip_failures"], 1)
        self.assertEqual(report["long_trip_failure_cases"], ["case-long"])
        markdown = render_markdown(report)
        self.assertIn("## 长途（horizon 判定）", markdown)
        self.assertIn("long_trip_first_round_failed", markdown)

    def test_report_marks_missing_first_round_in_table(self):
        case = long_case(first_round_required=True)
        with tempfile.TemporaryDirectory() as tmp:
            plans_dir = Path(tmp)
            self._write(plans_dir, "case-long", thirty_day_plan())
            report = build_report([case], plans_dir)
        markdown = render_markdown(report)
        self.assertIn("未下发（用例要求了首轮分段生成）", markdown)


class TestCommittedFixtures(unittest.TestCase):
    """评测夹具必须进仓库：全新 clone、没有 Key、不联网也能跑出报告。

    同类开源项目把 fixtures/ 写进了 .gitignore，导致它的"可复现评测"开箱不可复现 —— 这条测试
    就是我们的反例保险：夹具文件在仓库里，且评测器确实能把"编造"和"干净"区分开。
    """

    def setUp(self):
        self.cases = load_cases(FIXTURE_DIR / "cases.json")
        self.report = build_report(self.cases, FIXTURE_DIR / "plans")
        self.by_id = {row["id"]: row for row in self.report["cases"]}

    def test_every_fixture_case_has_a_committed_plan(self):
        self.assertGreaterEqual(len(self.cases), 2)
        for case in self.cases:
            files = find_plan_files(FIXTURE_DIR / "plans", str(case["id"]))
            self.assertTrue(files, f"夹具用例 {case['id']} 没有对应的 plan 文件")

    def test_clean_fixture_has_no_unsourced_claims(self):
        clean = self.by_id["fixture-clean"]
        self.assertEqual(clean["no_invention"]["unsourced_count"], 0)
        self.assertEqual(clean["no_invention"]["value"], 1.0)
        self.assertTrue(clean["gate_passed"], clean["gate_failures"])

    def test_fabricated_fixture_is_caught(self):
        fabricated = self.by_id["fixture-fabricated"]
        self.assertGreater(fabricated["no_invention"]["unsourced_count"], 0)
        self.assertLess(fabricated["no_invention"]["value"], 1.0)
        fields = {field for claim in fabricated["no_invention"]["unsourced_claims"] for field in claim["fields"]}
        self.assertIn("cost_estimate", fields)
        self.assertIn("rating", fields)

    def test_report_names_the_offenders(self):
        markdown = render_markdown(self.report)
        self.assertIn("## 不凭空编造（g_no_invention）", markdown)
        self.assertIn("没有可核实来源", markdown)
        self.assertIn("fixture-fabricated", markdown)
        self.assertEqual(self.report["unsourced_cases"], ["fixture-fabricated"])
        self.assertGreater(self.report["unsourced_claims_total"], 0)


if __name__ == "__main__":
    unittest.main()
