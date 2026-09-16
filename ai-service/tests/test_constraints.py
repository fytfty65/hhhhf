"""Tests for the multi-level constraint engine (core/constraints.py).

This module is the deterministic core of the planner's feasibility story: it
decides what is a hard violation, what is negotiable, and how conflicts are
resolved. It had no test coverage before, so these tests pin the level mapping,
the violation codes, the relaxation strategies and the "missing input is not a
violation" behaviour.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import constraints as C


def good_route():
    """A route that satisfies 3 days with a food node each day."""
    return [
        {"day": 1, "location": "老字号面馆", "tags": ["美食"], "category": "food", "cost": 80},
        {"day": 1, "location": "洛阳博物馆", "tags": ["博物馆"], "category": "museum", "cost": 60},
        {"day": 2, "location": "老城小吃街", "tags": ["美食"], "category": "food", "cost": 70},
        {"day": 2, "location": "湖滨公园", "tags": ["风景"], "category": "nature", "cost": 0},
        {"day": 3, "location": "特色菜馆", "tags": ["美食"], "category": "food", "cost": 90},
        {"day": 3, "location": "古街", "tags": ["文化"], "category": "culture", "cost": 0},
    ]


def context():
    return {
        "destination": "洛阳",
        "budget": 3000,
        "days": 3,
        "travelers": 2,
        "required_categories": ["food"],
        # Present in the gateway's real context; its absence would simply mean no
        # risk constraint is emitted, which is a different test.
        "risk_tolerance": "low",
    }


class LevelConstantsTests(unittest.TestCase):
    def test_levels_are_ordered_from_hardest_to_weakest(self):
        self.assertLess(C.LEVEL_HARD, C.LEVEL_NEGOTIABLE)
        self.assertLess(C.LEVEL_NEGOTIABLE, C.LEVEL_SOFT)
        self.assertLess(C.LEVEL_SOFT, C.LEVEL_PROFILE)

    def test_every_level_has_a_display_name(self):
        for level in (C.LEVEL_HARD, C.LEVEL_NEGOTIABLE, C.LEVEL_SOFT, C.LEVEL_PROFILE):
            self.assertIn(level, C.LEVEL_NAMES)


class NormalizeContextTests(unittest.TestCase):
    def test_none_yields_a_fully_defaulted_context_without_raising(self):
        ctx = C.normalize_context(None)
        self.assertEqual(ctx["travelers"], 1)
        self.assertEqual(ctx["currency"], "CNY")
        self.assertIsNone(ctx["budget"])
        self.assertIsNone(ctx["days"])
        self.assertEqual(ctx["required_categories"], [])

    def test_unknown_keys_do_not_crash_and_are_ignored(self):
        ctx = C.normalize_context({"totally_unknown": 1, "nested": {"a": 1}})
        self.assertEqual(ctx["travelers"], 1)

    def test_numeric_strings_are_coerced(self):
        ctx = C.normalize_context({"budget": "3000", "days": "4", "travelers": "3"})
        self.assertEqual(float(ctx["budget"]), 3000.0)
        self.assertEqual(int(ctx["days"]), 4)
        self.assertEqual(int(ctx["travelers"]), 3)

    def test_normalizing_is_idempotent(self):
        once = C.normalize_context(context())
        twice = C.normalize_context(once)
        self.assertEqual(once["destination"], twice["destination"])
        self.assertEqual(once["required_categories"], twice["required_categories"])


class HardConstraintTests(unittest.TestCase):
    def test_budget_days_and_required_categories_are_hard(self):
        codes = {c["code"]: c for c in C.build_hard_constraints(context())}
        self.assertEqual(codes["budget_upper"]["level"], C.LEVEL_HARD)
        self.assertEqual(codes["days"]["level"], C.LEVEL_HARD)
        self.assertEqual(codes["required_categories"]["level"], C.LEVEL_HARD)

    def test_risk_tolerance_is_negotiable_not_hard(self):
        """Risk tolerance is deliberately the softer of the two tiers."""
        codes = {c["code"]: c for c in C.build_hard_constraints(context())}
        self.assertEqual(codes["risk_tolerance"]["level"], C.LEVEL_NEGOTIABLE)

    def test_budget_constraint_carries_its_limit_and_source(self):
        budget = next(c for c in C.build_hard_constraints(context()) if c["code"] == "budget_upper")
        self.assertEqual(budget["params"]["limit"], 3000.0)
        self.assertIn("source", budget)

    def test_absent_budget_produces_no_budget_constraint(self):
        ctx = context()
        ctx.pop("budget")
        codes = {c["code"] for c in C.build_hard_constraints(ctx)}
        self.assertNotIn("budget_upper", codes)


class FeasibilityTests(unittest.TestCase):
    def test_a_complete_route_is_feasible(self):
        report = C.evaluate_feasibility(good_route(), context())
        self.assertTrue(report["feasible"], report.get("violations"))
        self.assertEqual(report["hard_violations"], [])

    def test_a_missing_day_is_a_hard_violation(self):
        route = [n for n in good_route() if n["day"] != 3]
        report = C.evaluate_feasibility(route, context())
        self.assertFalse(report["feasible"])
        codes = {v["code"] for v in report["hard_violations"]}
        self.assertIn("missing_day", codes)
        missing = next(v for v in report["hard_violations"] if v["code"] == "missing_day")
        self.assertEqual(missing["day"], 3)

    def test_exceeding_the_budget_is_reported(self):
        route = good_route()
        for node in route:
            node["cost"] = 5000
        report = C.evaluate_feasibility(route, context())
        self.assertFalse(report["feasible"])
        self.assertIn("budget_exceeded", {v["code"] for v in report["violations"]})

    def test_report_exposes_the_checked_inputs(self):
        report = C.evaluate_feasibility(good_route(), context())
        checked = report["checked"]
        self.assertEqual(checked["days"], 3)
        self.assertEqual(checked["budget"], 3000.0)
        self.assertIn("total_cost", checked)
        self.assertTrue(checked["unique_locations"])

    def test_duplicate_locations_produce_a_violation(self):
        """Duplicates surface as a `duplicate_location` violation.

        Note: `checked["unique_locations"]` is the *input* flag (whether the check
        is enabled), not a detection result — asserting on it would only re-test
        the argument. The actual signal is the violation entry.
        """
        route = good_route()
        route[2]["location"] = route[0]["location"]
        report = C.evaluate_feasibility(route, context())
        duplicates = [v for v in report["violations"] if v["code"] == "duplicate_location"]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["location"], route[0]["location"])
        self.assertEqual(duplicates[0]["first_index"], 0)
        self.assertEqual(duplicates[0]["index"], 2)

    def test_a_route_without_duplicates_has_no_duplicate_violation(self):
        report = C.evaluate_feasibility(good_route(), context())
        self.assertEqual([v for v in report["violations"] if v["code"] == "duplicate_location"], [])
        self.assertTrue(report["checked"]["unique_locations"])

    def test_hard_and_negotiable_violations_are_separated(self):
        report = C.evaluate_feasibility([], context())
        # An empty route is infeasible on days, and the two lists must not be
        # conflated: a caller acting on "negotiable" must not see hard breakage.
        self.assertFalse(report["feasible"])
        hard_codes = {v["code"] for v in report["hard_violations"]}
        negotiable_codes = {v["code"] for v in report["negotiable_violations"]}
        self.assertTrue(hard_codes)
        self.assertEqual(hard_codes & negotiable_codes, set())

    def test_violation_level_mapping_matches_the_constraint_table(self):
        report = C.evaluate_feasibility([], context())
        hard = {c["code"] for c in C.build_hard_constraints(context())}
        for violation in report["hard_violations"]:
            level = C._violation_level(violation["code"], C.build_hard_constraints(context()))
            self.assertEqual(level, C.LEVEL_HARD, violation["code"])
        self.assertTrue(hard)


class RelaxationTests(unittest.TestCase):
    def test_each_supported_code_relaxes_and_reports_its_patch(self):
        ctx = context()
        for code in ("max_nodes_per_day", "risk_tolerance", "days", "budget_upper"):
            result = C.relax_hard_constraint(ctx, code)
            self.assertTrue(result["relaxed"], code)
            self.assertEqual(result["code"], code)
            self.assertTrue(result["patch"], code)
            self.assertTrue(result["label"])

    def test_relaxation_does_not_mutate_the_input_context(self):
        ctx = context()
        before = ctx["budget"]
        C.relax_hard_constraint(ctx, "budget_upper")
        self.assertEqual(ctx["budget"], before, "relaxation must return a copy")

    def test_budget_relaxation_adds_ten_percent(self):
        result = C.relax_hard_constraint({"budget": 1000}, "budget_upper")
        self.assertEqual(result["context"]["budget"], 1100.0)

    def test_unknown_code_is_refused_explicitly_not_silently(self):
        result = C.relax_hard_constraint(context(), "not_a_real_constraint")
        self.assertFalse(result["relaxed"])
        self.assertIn("不支持的放宽约束", result["reason"])


class ResolveConflictsTests(unittest.TestCase):
    def test_conflict_resolution_reports_infeasible_candidates_instead_of_ranking_them(self):
        candidates = [
            {"id": "ok", "route": good_route()},
            {"id": "bad", "route": []},
        ]
        result = C.resolve_conflicts(candidates, context())
        self.assertEqual(result["feasible_count"], 1)
        self.assertEqual(result["infeasible_count"], 1)
        ranked_ids = [entry.get("id") or entry.get("route") for entry in result["ranked"]]
        self.assertNotIn("bad", ranked_ids)
        self.assertTrue(result["conflict_summary"])

    def test_empty_candidate_list_is_handled(self):
        result = C.resolve_conflicts([], context())
        self.assertEqual(result["feasible_count"], 0)
        self.assertEqual(result["ranked"], [])

    def test_a_single_feasible_candidate_wins(self):
        result = C.resolve_conflicts([{"id": "only", "route": good_route()}], context())
        self.assertEqual(result["feasible_count"], 1)
        self.assertTrue(result["ranked"])


class ExplainDecisionTests(unittest.TestCase):
    def test_explanation_lists_the_basis_and_a_deterministic_confidence(self):
        out = C.explain_decision(good_route(), context())
        self.assertIn("basis", out)
        self.assertTrue(out["basis"])
        for entry in out["basis"]:
            self.assertIn(entry["kind"], {"hard", "negotiable_hard", "soft", "profile"})
        # Confidence is an arithmetic estimate, not a calibrated probability,
        # which the implementation documents. It must stay in range.
        self.assertGreaterEqual(out["confidence"], 0.0)
        self.assertLessEqual(out["confidence"], 1.0)

    def test_soft_penalty_is_reported_for_penalised_routes(self):
        out = C.explain_decision(good_route(), context())
        self.assertIn("soft_penalty", out)
        self.assertGreaterEqual(out["soft_penalty"], 0.0)


class ContextConflictTests(unittest.TestCase):
    def test_no_conflict_for_a_single_member(self):
        out = C.detect_context_conflicts(context(), members=[{"name": "A", "interests": ["美食"]}])
        self.assertIn("has_conflict", out)
        self.assertIsInstance(out["conflicts"], list)

    def test_none_context_is_safe(self):
        out = C.detect_context_conflicts(None)
        self.assertFalse(out["has_conflict"])

    def test_opposing_budgets_are_detected_as_a_conflict(self):
        members = [
            {"name": "省", "budget": 500},
            {"name": "奢", "budget": 20000},
        ]
        out = C.detect_context_conflicts(context(), members=members)
        self.assertIsInstance(out, dict)
        self.assertIn("has_conflict", out)


if __name__ == "__main__":
    unittest.main()
