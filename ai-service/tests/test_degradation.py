"""数据降级登记处（core/degradation）的单测。

规矩：**不撒谎、不夸大**——拿到了就不进列表；不完整必须说清"不完整在哪"；
文案与"用户损失"只在后端注册表里定义一处（前端照抄，不自己编）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.degradation import (  # noqa: E402
    DATA_SOURCES,
    STATUS_DEGRADED,
    STATUS_MISSING,
    collect_degradations,
    degradation_for,
)


def lodging(name, cost="暂无供应商数据"):
    return {"day": 1, "name": name, "type": "住宿", "cost_estimate": cost, "tags": ["住宿"]}


def node(name, cost="¥100", kind="文化"):
    return {"day": 1, "name": name, "type": kind, "cost_estimate": cost}


class TestCleanPayload(unittest.TestCase):
    def test_clean_payload_has_no_degradations(self):
        payload = {
            "quality": {"gate_passed": True},
            "review": {"stopped_reason": "clean", "actions": [], "needs_data": []},
            "price_audit": {"total": 5, "verified_ratio": 1.0, "unknown_ratio": 0.0},
            "transport_audit": {"legs": [{"from": "西安", "to": "成都", "options": [{"mode": "高铁"}], "unverified_fares": []}]},
            "route": [node("景点A"), lodging("酒店H", "¥300")],
        }
        result = collect_degradations(payload)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["summary"], "数据源这次都取到了，没有降级项。")

    def test_empty_payload_is_not_reported_as_broken(self):
        self.assertEqual(collect_degradations({})["total"], 0)

    def test_every_registered_source_has_a_label_and_impact(self):
        for source, meta in DATA_SOURCES.items():
            self.assertTrue(meta.get("label"), source)
            self.assertTrue(meta.get("impact"), source)


class TestMissingSources(unittest.TestCase):
    def test_quality_failure_is_missing_not_ok(self):
        result = collect_degradations({"quality": {"error": "boom"}})
        entry = degradation_for({"quality": {"error": "boom"}}, "plan_quality")
        self.assertEqual(entry["status"], STATUS_MISSING)
        self.assertIn("boom", entry["reason"])
        self.assertEqual(result["total"], 1)

    def test_review_error_and_needs_data(self):
        payload = {"review": {"error": "复核炸了"}}
        self.assertEqual(degradation_for(payload, "plan_review")["status"], STATUS_MISSING)

        no_pool = {"review": {"needs_data": ["needs_candidates"], "used_pool": False}}
        entry = degradation_for(no_pool, "candidate_pool")
        self.assertEqual(entry["status"], STATUS_MISSING)
        self.assertIn("没有取到候选地点", entry["reason"])

        exhausted = {"review": {"needs_data": ["needs_candidates"], "used_pool": True}}
        entry = degradation_for(exhausted, "candidate_pool")
        self.assertEqual(entry["status"], STATUS_DEGRADED)
        self.assertIn("没有更多可用", entry["reason"])

    def test_constraint_check_failure(self):
        payload = {"constraints": {"error": "核对失败"}}
        self.assertEqual(degradation_for(payload, "constraint_check")["status"], STATUS_MISSING)


class TestLongTripAndPrice(unittest.TestCase):
    def test_long_trip_failed_and_skipped_segments(self):
        payload = {"long_trip": {"first_round": {"failed": [3], "skipped": [5, 6]}}}
        entry = degradation_for(payload, "long_trip_segmentation")
        self.assertEqual(entry["status"], STATUS_DEGRADED)
        self.assertIn("第 3 段没生成出来", entry["reason"])
        self.assertIn("第 5、6 段超出本次上限", entry["reason"])

    def test_long_trip_repair_failure_is_also_reported(self):
        payload = {"long_trip": {"repair_failed_segments": [22]}}
        self.assertIn("第 22 天起", degradation_for(payload, "long_trip_segmentation")["reason"])

    def test_all_prices_unknown_is_missing(self):
        payload = {"price_audit": {"total": 4, "verified_ratio": 0.0, "unknown_ratio": 1.0}}
        entry = degradation_for(payload, "price_web")
        self.assertEqual(entry["status"], STATUS_MISSING)
        self.assertIn("一个价格都没取到", entry["reason"])

    def test_partial_prices_is_degraded_with_names(self):
        payload = {"price_audit": {"total": 6, "verified_ratio": 0.5, "unknown_ratio": 0.33, "still_unknown": ["某酒店"]}}
        entry = degradation_for(payload, "price_web")
        self.assertEqual(entry["status"], STATUS_DEGRADED)
        self.assertIn("某酒店", entry["reason"])

    def test_estimated_prices_are_degraded_not_ok(self):
        payload = {"price_audit": {"total": 6, "verified_ratio": 0.5, "unknown_ratio": 0.0}}
        entry = degradation_for(payload, "price_web")
        self.assertEqual(entry["status"], STATUS_DEGRADED)
        self.assertIn("估算", entry["reason"])

    def test_lodging_without_any_price_is_reported(self):
        payload = {"route": [node("景点A"), lodging("酒店H"), lodging("民宿X")]}
        entry = degradation_for(payload, "hotel_price")
        self.assertEqual(entry["status"], STATUS_MISSING)
        self.assertIn("2 个住宿节点", entry["reason"])

    def test_lodging_with_price_is_not_reported(self):
        payload = {"route": [lodging("酒店H", "¥300")]}
        self.assertIsNone(degradation_for(payload, "hotel_price"))


class TestTransportAndSummary(unittest.TestCase):
    def test_transport_without_options_or_fares(self):
        payload = {
            "transport_audit": {
                "legs": [
                    {"from": "西安", "to": "成都", "options": [], "unverified_fares": [{"mode": "高铁"}]},
                    {"from": "成都", "to": "昆明", "options": [{"mode": "飞机"}], "unverified_fares": []},
                ]
            }
        }
        entry = degradation_for(payload, "transport_options")
        self.assertEqual(entry["status"], STATUS_DEGRADED)
        self.assertIn("1 段没取到可用班次", entry["reason"])
        self.assertIn("1 段的票价没有可核实来源", entry["reason"])

    def test_summary_names_every_source_once(self):
        payload = {
            "review": {"needs_data": ["needs_candidates"], "used_pool": True},
            "route": [lodging("酒店H")],
            "price_audit": {"total": 3, "verified_ratio": 0.0, "unknown_ratio": 1.0},
        }
        result = collect_degradations(payload)
        self.assertEqual(result["counts"].get(STATUS_MISSING, 0) >= 2, True)
        for source in result["sources"]:
            self.assertIn(DATA_SOURCES[source]["label"], result["summary"])

    def test_items_always_carry_label_and_impact(self):
        payload = {"review": {"error": "x"}, "price_audit": {"total": 1, "unknown_ratio": 1.0}}
        for item in collect_degradations(payload)["items"]:
            self.assertTrue(item["label"])
            self.assertTrue(item["impact"])
            self.assertIn(item["status"], {STATUS_DEGRADED, STATUS_MISSING})


if __name__ == "__main__":
    unittest.main()
