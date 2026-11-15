"""Tests for the algorithm classes extracted from ``api/agent.py``.

These two classes moved to ``core/negotiation_algos.py`` on 2026-10-10 as the
first safe step of decomposing the 7,000-line ``api/agent.py``. The extraction was
meant to be byte-for-byte behaviour preserving, so the most valuable test is the
one that pins the re-export contract: ``api.agent`` must keep exposing both names
for existing callers, while new code imports from the new module.

The behavioural cases below are deliberately shallow — they exist so the classes
have direct coverage in their new home, which they did not have before (they were
only reachable through the agent import surface).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LLM_API_KEY", "test-key-not-used")

from core.negotiation_algos import (  # noqa: E402
    GraphRouteOptimizer,
    ParetoFrontierSelector,
)
from core.text_parsing import (  # noqa: E402
    _fairness_limit_from_env,
    _static_map_url,
    build_reasoning_step,
    extract_preference_signals,
    parse_budget_amount,
    parse_chinese_days,
    safe_repair_and_parse_json,
)


class ReExportContractTests(unittest.TestCase):
    """`api.agent` must keep both names available after the move."""

    def test_agent_still_exports_both_classes(self):
        from api import agent

        self.assertIs(agent.ParetoFrontierSelector, ParetoFrontierSelector)
        self.assertIs(agent.GraphRouteOptimizer, GraphRouteOptimizer)

    def test_new_module_is_the_single_definition_site(self):
        """The classes must not be defined twice.

        A duplicate definition would silently shadow the re-export and make the
        two copies drift, which is the failure mode this move was meant to
        prevent.
        """
        import inspect

        from api import agent

        # The class object's own module must be the new one, not api.agent.
        self.assertEqual(ParetoFrontierSelector.__module__, "core.negotiation_algos")
        self.assertEqual(GraphRouteOptimizer.__module__, "core.negotiation_algos")
        # And the source line found by inspect must live in the new file.
        self.assertIn("negotiation_algos.py", inspect.getsourcefile(ParetoFrontierSelector))
        self.assertIn("negotiation_algos.py", inspect.getsourcefile(GraphRouteOptimizer))
        self.assertFalse(hasattr(agent, "DBSCAN"), "the moved class's import should have left agent.py")


class ParetoFrontierSelectorTests(unittest.TestCase):
    def test_non_list_input_is_handled(self):
        result = ParetoFrontierSelector.resolve_conflicts(None)  # type: ignore[arg-type]
        self.assertEqual(result["frontier"], [])
        self.assertEqual(result["rejected"], [])

    def test_non_dict_candidates_are_rejected_not_crashed(self):
        result = ParetoFrontierSelector.resolve_conflicts([None, "nope", 42])  # type: ignore[list-item]
        self.assertGreaterEqual(result["rejected_count"], 1)

    def test_numeric_parsing_extracts_a_leading_number(self):
        self.assertEqual(ParetoFrontierSelector._numeric("¥120 起"), 120.0)
        self.assertEqual(ParetoFrontierSelector._numeric("no digits"), 0.0)
        self.assertEqual(ParetoFrontierSelector._numeric(None), 0.0)


class GraphRouteOptimizerTests(unittest.TestCase):
    def test_empty_and_invalid_input_returns_empty(self):
        optimizer = GraphRouteOptimizer(ParetoFrontierSelector)
        self.assertEqual(optimizer.optimize_and_sort([], 5), [])
        self.assertEqual(optimizer.optimize_and_sort(None, 5), [])  # type: ignore[arg-type]

    def test_fewer_than_three_coordinates_falls_back_to_score_order(self):
        """Below three points the graph path is pointless, so the documented
        fallback is a fitness descending sort truncated to max_nodes."""
        optimizer = GraphRouteOptimizer(ParetoFrontierSelector)
        pois = [
            {"name": "a", "fitness_score": 1, "location": [120.0, 30.0]},
            {"name": "b", "fitness_score": 9, "location": [121.0, 31.0]},
        ]
        ordered = optimizer.optimize_and_sort(pois, 5)
        self.assertEqual([p["name"] for p in ordered], ["b", "a"])

    def test_max_nodes_is_respected(self):
        optimizer = GraphRouteOptimizer(ParetoFrontierSelector)
        pois = [
            {"name": f"p{i}", "fitness_score": i, "location": [120.0 + i, 30.0]}
            for i in range(6)
        ]
        self.assertLessEqual(len(optimizer.optimize_and_sort(pois, 3)), 3)


class TextParsingReExportTests(unittest.TestCase):
    """Second extraction batch (2026-10-10): seven dependency-free helpers moved
    from ``api/agent.py`` to ``core/text_parsing.py``.

    As with batch 1, the contract that matters most is that ``api.agent`` still
    exposes every name: the tests and ``tools/`` scripts import them from there,
    and a silent disappearance would only show up as an AttributeError in a rarely
    exercised path.
    """

    MOVED = (
        "parse_chinese_days",
        "parse_budget_amount",
        "extract_preference_signals",
        "safe_repair_and_parse_json",
        "build_reasoning_step",
        "_static_map_url",
        "_fairness_limit_from_env",
    )

    def test_agent_still_exports_every_moved_name(self):
        from api import agent
        from core import text_parsing

        for name in self.MOVED:
            with self.subTest(name=name):
                self.assertTrue(hasattr(agent, name), f"api.agent lost {name}")
                self.assertIs(
                    getattr(agent, name),
                    getattr(text_parsing, name),
                    f"{name} in api.agent is not the function from core.text_parsing",
                )

    def test_single_definition_site(self):
        """Both batches must live in exactly one place, or the copies drift."""
        from core import negotiation_algos, text_parsing

        for name in ("ParetoFrontierSelector", "GraphRouteOptimizer"):
            self.assertEqual(getattr(negotiation_algos, name).__module__, "core.negotiation_algos")
        for name in self.MOVED:
            self.assertEqual(
                getattr(text_parsing, name).__module__,
                "core.text_parsing",
                f"{name} should be defined in core.text_parsing",
            )


class ParseChineseDaysTests(unittest.TestCase):
    def test_arabic_numerals(self):
        self.assertEqual(parse_chinese_days("新疆 3 天"), 3)
        self.assertEqual(parse_chinese_days("5日游"), 5)

    def test_chinese_numerals(self):
        self.assertEqual(parse_chinese_days("三天"), 3)
        self.assertEqual(parse_chinese_days("两日游"), 2)

    def test_compound_expression_uses_the_day_count(self):
        """'两天一夜' means two days, not one night."""
        self.assertEqual(parse_chinese_days("两天一夜"), 2)

    def test_clamped_to_a_sane_range(self):
        self.assertEqual(parse_chinese_days("99天"), 15)
        self.assertEqual(parse_chinese_days("0天"), 1)

    def test_default_when_nothing_parses(self):
        self.assertEqual(parse_chinese_days("随便走走"), 3)


class ParseBudgetAmountTests(unittest.TestCase):
    def test_explicit_total(self):
        self.assertEqual(parse_budget_amount("预算5000"), 5000)
        self.assertEqual(parse_budget_amount("总预算1万"), 10000)

    def test_per_person_multiplies_by_people_and_days(self):
        self.assertEqual(parse_budget_amount("人均300", trip_days=2, member_count=3), 1800)

    def test_per_day_multiplies_by_days(self):
        self.assertEqual(parse_budget_amount("每天600", trip_days=3), 1800)

    def test_bare_yuan_and_block(self):
        self.assertEqual(parse_budget_amount("8000元"), 8000)
        self.assertEqual(parse_budget_amount("1200块"), 1200)

    def test_unparseable_returns_zero_never_a_guess(self):
        """Unknown must stay unknown: 0 means 'not stated', so the planner can
        treat the budget as unconstrained rather than invent a number."""
        self.assertEqual(parse_budget_amount("随便看看"), 0)
        self.assertEqual(parse_budget_amount(""), 0)


class ExtractPreferenceSignalsTests(unittest.TestCase):
    def test_nature_focus_excludes_culture(self):
        signals = extract_preference_signals("只想看自然风景")
        self.assertTrue(signals["wants_scenic"])
        self.assertTrue(signals["exclude_cultural"])

    def test_negated_preference_is_not_a_preference(self):
        signals = extract_preference_signals("不想去博物馆")
        self.assertFalse(signals["wants_cultural"])

    def test_distance_tolerance_order_matters(self):
        """'不介意太远' must read as high, not be caught by the '太远' rule."""
        self.assertEqual(extract_preference_signals("我不介意太远")["distance_tolerance"], "high")
        self.assertEqual(extract_preference_signals("太远了")["distance_tolerance"], "low")

    def test_requirements_never_empty(self):
        self.assertTrue(extract_preference_signals("")["requirements"])


class SafeRepairJsonTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(safe_repair_and_parse_json('{"a": 1}'), {"a": 1})

    def test_fenced_json_is_unwrapped(self):
        self.assertEqual(safe_repair_and_parse_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_trailing_comma_is_repaired(self):
        self.assertEqual(safe_repair_and_parse_json('{"a": 1,}'), {"a": 1})

    def test_hopeless_input_returns_empty_dict_never_raises(self):
        self.assertEqual(safe_repair_and_parse_json("not json at all"), {})
        self.assertEqual(safe_repair_and_parse_json(""), {})


class BuildReasoningStepTests(unittest.TestCase):
    def test_shape_is_stable(self):
        step = build_reasoning_step("agent-x", "claim", "some claim", "some basis")
        self.assertEqual(set(step), {"agent", "action", "claim", "basis", "ts"})
        self.assertRegex(step["ts"], r"^\d{2}:\d{2}:\d{2}$")


class StaticMapUrlTests(unittest.TestCase):
    def test_valid_coordinates_produce_a_url(self):
        url = _static_map_url([120.5, 30.5], "KEY")
        self.assertTrue(url.startswith("https://restapi.amap.com/v3/staticmap?"))
        self.assertIn("location=120.5,30.5", url)
        self.assertIn("key=KEY", url)

    def test_degenerate_inputs_produce_empty_not_a_broken_url(self):
        """A blank fallback image is the contract: never emit a URL that would
        render as a broken request."""
        for bad in (None, [], [1], "120,30", [0.0, 0.0], [120.0, 30.0]):
            with self.subTest(bad=bad):
                key = None if bad == [120.0, 30.0] else "KEY"
                self.assertEqual(_static_map_url(bad, key), "")


class FairnessLimitTests(unittest.TestCase):
    def test_default(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"PLANNING_FAIRNESS_MAX_STD": ""}, clear=False):
            os.environ.pop("PLANNING_FAIRNESS_MAX_STD", None)
            self.assertAlmostEqual(_fairness_limit_from_env(), 0.15)

    def test_malformed_value_falls_back(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"PLANNING_FAIRNESS_MAX_STD": "abc"}, clear=False):
            self.assertAlmostEqual(_fairness_limit_from_env(), 0.15)

    def test_negative_is_clamped_to_zero(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"PLANNING_FAIRNESS_MAX_STD": "-1"}, clear=False):
            self.assertAlmostEqual(_fairness_limit_from_env(), 0.0)


if __name__ == "__main__":
    unittest.main()