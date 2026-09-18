"""预算三级价格模型与兜底（平替）引擎的确定性单测。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.budget_planner import budget_report, price_of, propose_fallback  # noqa: E402
from core.plan_quality import evaluate_plan  # noqa: E402


def node(name, price, source="amap", estimated=False, day=1, kind="文化", lnglat=(108.94, 34.26)):
    return {
        "day": day, "name": name, "location": name, "type": kind, "time": "09:00",
        "lnglat": list(lnglat), "cost_estimate": price,
        "data_sources": {"cost_estimate": source}, "estimated": estimated,
    }


CONTEXT = {"days": 2, "budget": 1000, "preferences": {"interest": ["博物馆", "美食"]}}


class TestPriceTiers(unittest.TestCase):
    def test_verified(self):
        self.assertEqual(price_of(node("a", "¥100", "amap"))["tier"], "verified")

    def test_estimated_when_marked(self):
        self.assertEqual(price_of(node("a", "¥100", "llm_estimate", estimated=True))["tier"], "estimated")

    def test_estimated_when_priced_without_source(self):
        """有价无源不能冒充已验证（严格口径）。"""
        item = node("a", "¥100", "unverified")
        self.assertEqual(price_of(item)["tier"], "estimated")

    def test_unknown_when_price_missing(self):
        self.assertEqual(price_of(node("a", "暂无供应商数据"))["tier"], "unknown")

    def test_free_is_verified_zero(self):
        report = price_of(node("a", "¥0", "amap"))
        self.assertEqual(report["tier"], "verified")
        self.assertEqual(report["value"], 0.0)


class TestBudgetReport(unittest.TestCase):
    def test_over_when_verified_exceeds_budget(self):
        plan = {"route": [node("a", "¥800"), node("b", "¥400")]}
        report = budget_report(CONTEXT, plan)
        self.assertEqual(report["status"], "over")
        self.assertEqual(report["shortfall"], 200.0)
        self.assertEqual(report["confidence"], "high")

    def test_at_risk_when_estimates_push_over(self):
        plan = {"route": [node("a", "¥600"), node("b", "¥600", "llm_estimate", estimated=True)]}
        report = budget_report(CONTEXT, plan)
        self.assertEqual(report["status"], "at_risk")
        self.assertEqual(report["verified_cost"], 600.0)
        self.assertEqual(report["estimated_cost"], 600.0)
        self.assertEqual(report["confidence"], "medium")

    def test_ok_when_within_budget(self):
        plan = {"route": [node("a", "¥300"), node("b", "¥200")]}
        self.assertEqual(budget_report(CONTEXT, plan)["status"], "ok")

    def test_unverifiable_when_no_prices_at_all(self):
        plan = {"route": [node("a", "暂无供应商数据"), node("b", "暂无供应商数据")]}
        report = budget_report(CONTEXT, plan)
        self.assertEqual(report["status"], "unverifiable")
        self.assertEqual(report["unknown_count"], 2)
        self.assertEqual(report["confidence"], "low")

    def test_no_budget_in_context(self):
        plan = {"route": [node("a", "¥300")]}
        self.assertEqual(budget_report({"days": 1}, plan)["status"], "no_budget")


class TestFallback(unittest.TestCase):
    def test_tier_down_uses_cheaper_same_intent_candidate(self):
        plan = {"route": [
            node("豪华博物馆", "¥500", kind="博物馆"),
            node("普通酒店", "¥300", kind="住宿", day=1),
            node("面馆", "¥50", kind="餐饮", day=2),
            node("城墙", "¥50", kind="文化", day=2),
        ]}
        candidates = {"cultural": [{"name": "免费博物馆", "price": 0, "price_tier": "economy"}]}
        result = propose_fallback(CONTEXT, plan, shortfall=300, candidates_by_intent=candidates)
        self.assertGreaterEqual(len(result["substitutions"]), 1)
        substitution = result["substitutions"][0]
        self.assertEqual(substitution["to"], "免费博物馆")
        self.assertEqual(substitution["saving"], 500.0)
        self.assertEqual(result["remaining_shortfall"], 0.0)
        self.assertFalse(result["needs_confirmation"])
        self.assertIn("平替", result["disclosure"])

    def test_no_fabricated_substitutions_without_candidates(self):
        plan = {"route": [node("a", "¥500"), node("b", "¥500")]}
        result = propose_fallback(CONTEXT, plan, shortfall=1200)
        self.assertEqual(result["substitutions"], [])
        self.assertTrue(result["needs_confirmation"])
        self.assertIn("改天数/换城市", result["disclosure"])

    def test_drop_secondary_play_when_no_candidates(self):
        plan = {"route": [
            node("酒店", "¥200", kind="住宿", day=1),
            node("核心景点", "¥100", kind="文化", day=1),
            node("次要景点A", "¥300", kind="文化", day=1),
            node("核心景点B", "¥100", kind="文化", day=2),
            node("次要景点C", "¥400", kind="文化", day=2),
            node("面馆", "¥50", kind="餐饮", day=2),
        ]}
        result = propose_fallback(CONTEXT, plan, shortfall=250)
        kinds = [action["kind"] for action in result["actions"]]
        self.assertIn("drop_secondary_play", kinds)
        self.assertTrue(result["dropped"])  # 只删"每天第一个玩点之外"的次要玩点
        self.assertIn(result["dropped"][0], {"次要景点A", "次要景点C"})
        self.assertEqual(result["remaining_shortfall"], 0.0)

    def test_never_reuses_the_same_replacement_twice(self):
        """实测发现的真实缺陷：两个点被换成同一个地方（会重复景点）。"""
        plan = {"route": [
            node("贵餐厅A", "¥300", kind="餐饮", day=1),
            node("贵餐厅B", "¥280", kind="餐饮", day=2),
            node("酒店", "¥200", kind="住宿", day=1),
            node("景点", "¥50", kind="文化", day=1),
        ]}
        candidates = {"food": [
            {"name": "便宜小吃一号", "price": 30, "price_tier": "economy"},
            {"name": "便宜小吃二号", "price": 40, "price_tier": "economy"},
        ]}
        result = propose_fallback(CONTEXT, plan, shortfall=200, candidates_by_intent=candidates)
        targets = [item.get("to") for item in result["substitutions"]]
        self.assertGreaterEqual(len(targets), 1)
        self.assertEqual(len(targets), len(set(targets)), f"同一替代被重复使用: {targets}")
        self.assertNotIn("便宜小吃一号", result.get("dropped", []))

    def test_preserved_ratio_reports_dropped_intents(self):
        plan = {"route": [node("博物馆", "¥100", kind="博物馆"), node("面馆", "¥50", kind="餐饮")]}
        result = propose_fallback(CONTEXT, plan, shortfall=0)
        self.assertGreater(result["preserved_ratio"], 0.0)
        self.assertFalse(result["needs_confirmation"])


class TestGateIntegration(unittest.TestCase):
    def test_evaluate_plan_exposes_budget_and_skeleton(self):
        plan = {"route": [
            node("酒店", "¥200", kind="住宿", day=1), node("博物馆", "¥100", kind="博物馆", day=1),
            node("面馆", "¥50", kind="餐饮", day=1), node("城墙", "¥50", kind="文化", day=2),
            node("小吃街", "¥60", kind="餐饮", day=2),
        ]}
        report = evaluate_plan({"days": 2, "budget": 5000}, plan)
        self.assertIn("budget", report)
        self.assertIn("skeleton", report)
        self.assertEqual(report["budget"]["status"], "ok")
        self.assertEqual(report["skeleton"]["lodging_nights"], 1)

    def test_missing_hotel_fails_gate(self):
        plan = {"route": [
            node("博物馆", "¥100", kind="博物馆", day=1), node("面馆", "¥50", kind="餐饮", day=1),
            node("城墙", "¥50", kind="文化", day=2), node("小吃街", "¥60", kind="餐饮", day=2),
        ]}
        report = evaluate_plan({"days": 2, "budget": 5000}, plan)
        self.assertFalse(report["gate"]["passed"])
        self.assertIn("skeleton_lodging_shortfall", [f["code"] for f in report["gate"]["failures"]])

    def test_over_budget_fails_gate(self):
        plan = {"route": [
            node("酒店", "¥2000", kind="住宿", day=1), node("博物馆", "¥100", kind="博物馆", day=1),
            node("面馆", "¥50", kind="餐饮", day=1), node("城墙", "¥50", kind="文化", day=2),
            node("小吃街", "¥60", kind="餐饮", day=2),
        ]}
        report = evaluate_plan({"days": 2, "budget": 500}, plan)
        self.assertFalse(report["gate"]["passed"])
        self.assertIn("budget_exceeded", [f["code"] for f in report["gate"]["failures"]])


if __name__ == "__main__":
    unittest.main()
