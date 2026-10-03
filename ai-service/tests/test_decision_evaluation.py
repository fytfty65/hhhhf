import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.build_decision_dataset import RawEvent
from tools.evaluate_decision_dataset import evaluate


def audit(index, version="v1", reward=1.0, propensity=0.5, destination="乌鲁木齐", traveler_group="pair", behavior_enabled=None):
    behavior = {}
    if behavior_enabled is not None:
        behavior = {
            "behavior_enabled": behavior_enabled,
            "behavior_sample_count": 5,
            "behavior_verified": 2 if behavior_enabled else 0,
            "behavior_adopted": 2 if behavior_enabled else 0,
            "baseline_changed_positions": 1 if behavior_enabled else 0,
        }
    return RawEvent(
        event_id=str(index),
        event_type="planning_decision_audit",
        created_at=f"2026-02-{index:02d}T00:00:00Z",
        trip_id=f"trip-{index}",
        plan_id=f"plan-{index}",
        payload={
            "model_version": version,
            "reward": reward,
            "propensity": propensity,
            "destination": destination,
            "season": "summer",
            "budget_bucket": "3000_10000",
            "traveler_group": traveler_group,
            "fairness_minimum_utility": 0.8,
            "fairness_utility_stddev": 0.04,
            "fairness_threshold": 0.1,
            "fairness_threshold_exceeded": False,
            **behavior,
        },
    )


class DecisionEvaluationTests(unittest.TestCase):
    def provider_quality(self, index, *, stale=0, conflict=0, enum_invalid=0, eligible=5):
        return RawEvent(
            event_id=f"quality-{index}", event_type="provider_quality",
            created_at=f"2026-02-{index:02d}T00:00:00Z", trip_id="", plan_id="",
            payload={
                "kind": "transport", "provider": "fixture-provider",
                "candidate_count": 5, "decision_eligible_count": eligible,
                "decision_blocked_count": 5 - eligible,
                "stale_count": stale, "source_conflict_count": conflict,
                "enum_invalid_count": enum_invalid,
            },
        )

    def test_insufficient_data_blocks_promotion(self):
        report = evaluate([audit(1)])
        self.assertFalse(report["promotion_ready"])
        self.assertEqual(report["status"], "insufficient_evidence")
        self.assertTrue(report["reasons"])

    def test_ready_report_requires_all_evidence(self):
        events = [audit(i, version="v1" if i <= 15 else "v2") for i in range(1, 31)]
        report = evaluate(events)
        self.assertTrue(report["promotion_ready"])
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["counterfactual"]["status"], "available")
        self.assertEqual(report["fairness"]["status"], "available")
        self.assertEqual(report["drift"]["status"], "stable")
        self.assertGreaterEqual(report["offline_score"], 0.8)

    def test_missing_propensity_is_not_treated_as_zero(self):
        events = [audit(i, version="v1" if i <= 15 else "v2", propensity=None) for i in range(1, 31)]
        report = evaluate(events)
        self.assertFalse(report["promotion_ready"])
        self.assertEqual(report["counterfactual"]["status"], "insufficient_data")
        self.assertTrue(any("反事实" in reason for reason in report["reasons"]))

    def test_drift_is_a_blocking_signal(self):
        events = [audit(i, version="v1" if i <= 15 else "v2", destination="A" if i <= 24 else "B") for i in range(1, 31)]
        report = evaluate(events)
        self.assertTrue(report["drift"]["score"] >= 0)

    def test_policy_comparison_is_explicitly_gated(self):
        events = []
        for i in range(1, 11):
            events.append(audit(i, reward=0.8, behavior_enabled=False))
        for i in range(11, 21):
            events.append(audit(i, reward=1.0, behavior_enabled=True))
        report = evaluate(events)
        comparison = report["policy_comparison"]
        self.assertEqual(comparison["status"], "available")
        self.assertEqual(comparison["baseline"]["samples"], 10)
        self.assertEqual(comparison["behavior_aware"]["samples"], 10)
        self.assertAlmostEqual(comparison["reward_lift"], 0.2)

    def test_policy_comparison_does_not_impute_missing_group(self):
        report = evaluate([audit(i, behavior_enabled=True) for i in range(1, 31)])
        comparison = report["policy_comparison"]
        self.assertEqual(comparison["status"], "insufficient_data")
        self.assertIsNone(comparison["reward_lift"])
        self.assertTrue(comparison["reasons"])

    def test_policy_comparison_is_stratified_without_merging_sparse_segments(self):
        events = []
        for i in range(1, 11):
            events.append(audit(i, reward=0.8, behavior_enabled=False, traveler_group="pair"))
        for i in range(11, 21):
            events.append(audit(i, reward=1.0, behavior_enabled=True, traveler_group="pair"))
        # A second traveler group has only one observation and must remain
        # explicitly insufficient rather than borrowing pair-group evidence.
        events.append(audit(21, reward=1.0, behavior_enabled=True, traveler_group="family"))
        report = evaluate(events)
        coverage = report["policy_segment_coverage"]["traveler_group"]
        self.assertEqual(coverage["segment_count"], 2)
        self.assertEqual(coverage["available_segments"], 1)
        self.assertEqual(coverage["status"], "available")
        self.assertEqual(report["policy_comparison_by_segment"]["traveler_group"]["segments"]["family"]["status"], "insufficient_data")

    def test_fairness_summary_is_stratified_and_keeps_sparse_dimensions_explicit(self):
        events = [audit(i, version="v1" if i <= 15 else "v2", traveler_group="pair") for i in range(1, 31)]
        report = evaluate(events)
        summary = report["fairness_segment_summary"]
        self.assertEqual(summary["status"], "available")
        self.assertGreaterEqual(summary["max_stddev"], 0)
        self.assertEqual(summary["dimensions"]["traveler_group"]["available_segments"], 1)

    def test_provider_quality_is_a_separate_release_gate(self):
        events = [audit(i, version="v1" if i <= 15 else "v2") for i in range(1, 31)]
        events.extend(self.provider_quality(i) for i in range(1, 6))
        report = evaluate(events)
        self.assertTrue(report["promotion_ready"])
        self.assertEqual(report["provider_quality_gate"]["status"], "available")
        self.assertEqual(report["provider_quality_gate"]["ready_providers"], 1)

    def test_provider_quality_breach_blocks_without_polluting_decision_samples(self):
        events = [audit(i, version="v1" if i <= 15 else "v2") for i in range(1, 31)]
        events.extend(self.provider_quality(i, enum_invalid=1) for i in range(1, 6))
        report = evaluate(events)
        self.assertFalse(report["promotion_ready"])
        self.assertEqual(report["evaluation_samples"], 30)
        self.assertEqual(report["provider_quality_gate"]["status"], "blocked")
        self.assertIn("enum_invalid_ratio", report["provider_quality_gate"]["reasons"][0])

    def test_provider_quality_sparse_evidence_is_not_called_healthy(self):
        events = [audit(i, version="v1" if i <= 15 else "v2") for i in range(1, 31)]
        events.append(self.provider_quality(1))
        report = evaluate(events)
        self.assertFalse(report["promotion_ready"])
        self.assertEqual(report["provider_quality_gate"]["status"], "insufficient_data")


if __name__ == "__main__":
    unittest.main()
