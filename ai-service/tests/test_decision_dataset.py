import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.build_decision_dataset import RawEvent, build_dataset, _raw_events


def event(index, event_type="planning_decision_audit", version="v1", reward=None, propensity=None):
    payload = {
        "model_version": version,
        "destination": "乌鲁木齐",
        "season": "summer",
        "budget_bucket": "3000_10000",
        "traveler_group": "pair",
        "candidate_count": 8,
        "pareto_frontier_count": 2,
        "pareto_rejected_count": 6,
        "fairness_minimum_utility": 0.72,
        "fairness_utility_stddev": 0.06,
        "fairness_threshold": 0.15,
        "fairness_threshold_exceeded": False,
    }
    if reward is not None:
        payload["reward"] = reward
    if propensity is not None:
        payload["propensity"] = propensity
    return RawEvent(
        event_id=str(index), event_type=event_type,
        created_at=f"2026-01-{index:02d}T00:00:00Z",
        trip_id=f"trip-{index}", plan_id=f"plan-{index}", payload=payload,
    )


class DecisionDatasetTests(unittest.TestCase):
    def test_only_decision_events_are_time_split_and_fields_are_preserved(self):
        events = [event(i, version="v1" if i < 4 else "v2", reward=1.0, propensity=0.5) for i in range(1, 6)]
        events.insert(2, event(99, "impression"))
        report = build_dataset(events)
        self.assertEqual(report["sample_count"], 5)
        self.assertEqual(report["train_count"], 4)
        self.assertEqual(report["validation_count"], 1)
        self.assertEqual(report["samples"][-1]["split"], "validation")
        self.assertEqual(report["samples"][0]["pareto_rejected_count"], 6)
        self.assertEqual(report["samples"][0]["fairness_threshold"], 0.15)
        self.assertEqual(report["counterfactual_samples"], 5)

    def test_insufficient_evidence_is_explicit(self):
        report = build_dataset([event(1, reward=1.0, propensity=0.5)])
        self.assertEqual(report["status"], "insufficient_evidence")
        self.assertFalse(report["ready"])
        self.assertIn("样本不足", report["reason"])
        self.assertNotIn("best_arm", report)

    def test_missing_reward_never_becomes_a_zero_label(self):
        events = [event(i, version="v1" if i < 16 else "v2", propensity=0.5) for i in range(1, 32)]
        report = build_dataset(events)
        self.assertEqual(report["status"], "insufficient_evidence")
        self.assertGreater(report["missing_fields"]["reward"], 0)
        self.assertEqual(report["counterfactual_status"], "insufficient_data")
        self.assertIsNone(report["samples"][0]["reward"])

    def test_gateway_shape_and_outcome_join(self):
        raw = _raw_events([
            {"event_id": "audit", "event_type": "planning_decision_audit", "created_at": "2026-01-01T00:00:00Z", "trip_id": "t", "plan_id": "p", "payload": {"model_version": "v1"}},
            {"event_id": "adopt", "event_type": "plan_adopted", "created_at": "2026-01-02T00:00:00Z", "trip_id": "t", "plan_id": "p", "payload": {"reward": 1, "propensity": 0.4}},
        ])
        report = build_dataset(raw)
        self.assertEqual(report["sample_count"], 1)
        self.assertEqual(report["samples"][0]["reward"], 1.0)
        self.assertEqual(report["samples"][0]["propensity"], 0.4)

    def test_provider_quality_is_aggregated_separately_from_decision_samples(self):
        events = _raw_events([
            {
                "event_id": "quality-1", "event_type": "provider_quality",
                "created_at": "2026-01-01T00:00:00Z", "payload": {
                    "kind": "transport", "provider": "sandbox", "candidate_count": 4,
                    "decision_eligible_count": 3, "source_conflict_count": 1,
                    "enum_invalid_count": 1, "raw_response": "must-not-export",
                },
            },
            {"event_id": "adopt", "event_type": "plan_adopted", "created_at": "2026-01-02T00:00:00Z", "trip_id": "t", "payload": {"reward": 1}},
        ])
        report = build_dataset(events)
        self.assertEqual(report["sample_count"], 1)
        self.assertEqual(report["provider_quality"]["status"], "available")
        provider = report["provider_quality"]["providers"]["transport:sandbox"]
        self.assertEqual(provider["enum_invalid_count"], 1)
        self.assertEqual(provider["candidate_count"], 4)
        self.assertAlmostEqual(provider["enum_invalid_ratio"], 0.25)
        self.assertNotIn("raw_response", provider)


if __name__ == "__main__":
    unittest.main()
