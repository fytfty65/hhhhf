import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.optimization import fairness_report, pareto_frontier, select_fair_route, validate_route


class TestOptimization(unittest.TestCase):
    def test_route_validator_reports_hard_violations(self):
        report = validate_route(
            [
                {"day": 1, "name": "A", "cost_estimate": "¥80", "risk_score": 15},
                {"day": 1, "name": "A", "cost_estimate": "¥80", "risk_score": 15},
            ],
            budget=100,
            days=2,
            max_nodes_per_day=1,
            risk_tolerance=10,
        )
        codes = {item["code"] for item in report["violations"]}
        self.assertFalse(report["feasible"])
        self.assertTrue({"duplicate_location", "budget_exceeded", "missing_day", "daily_capacity_exceeded", "risk_tolerance_exceeded"} <= codes)

    def test_pareto_frontier_explains_dominated_candidates(self):
        result = pareto_frontier(
            [{"name": "fast" , "time": 1, "cost": 100}, {"name": "cheap", "time": 3, "cost": 50}, {"name": "dominated", "time": 4, "cost": 120}],
            {"time": lambda row: row["time"], "cost": lambda row: row["cost"]},
            {"time": False, "cost": False},
        )
        self.assertEqual({item["name"] for item in result["frontier"]}, {"fast", "cheap"})
        self.assertEqual(result["rejected"][0]["reason"], "dominated")
        self.assertIn("fast", result["rejected"][0]["dominated_by"])

    def test_fairness_selects_maximin_then_low_regret(self):
        members = [{"id": "food", "interestTags": ["food"]}, {"id": "scenic", "interestTags": ["scenic"]}]
        routes = [[{"name": "food", "tags": ["food"], "cost": 20}], [{"name": "balanced", "tags": ["food", "scenic"], "cost": 20}]]
        selected = select_fair_route(routes, members)
        self.assertEqual(selected["index"], 1)
        report = fairness_report(routes[selected["index"]], members)
        self.assertLessEqual(report["max_regret"], 0.01)


if __name__ == "__main__":
    unittest.main()
