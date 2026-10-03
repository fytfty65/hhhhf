import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.run_model_evaluation import EvaluationLock, append_audit, gateway_payload, post_report, run_once


class RunModelEvaluationTests(unittest.TestCase):
    def test_gateway_payload_contains_only_aggregate_fields(self):
        payload = gateway_payload({
            "evaluation_version": "decision-evaluation-v1",
            "status": "insufficient_evidence",
            "promotion_ready": False,
            "reasons": ["样本不足"],
            "evaluation_samples": 4,
            "validation_samples": 1,
            "offline_score": None,
            "dataset": {"samples": [{"trip_hash": "private"}]},
        }, "planner-v4")
        self.assertEqual(payload["model_version"], "planner-v4")
        self.assertNotIn("dataset", payload)
        self.assertNotIn("offline_score", payload)
        self.assertEqual(payload["promotion_ready"], False)

    def test_gateway_payload_flattens_policy_comparison_without_raw_samples(self):
        payload = gateway_payload({
            "status": "ready",
            "promotion_ready": True,
            "policy_comparison": {
                "status": "available",
                "baseline": {"samples": 7, "mean_reward": 0.5},
                "behavior_aware": {"samples": 8, "mean_reward": 0.7},
                "reward_lift": 0.2,
                "adoption_signal_lift": 0.1,
                "samples": [{"trip_hash": "private"}],
            },
        }, "planner-v5")
        self.assertEqual(payload["policy_comparison_status"], "available")
        self.assertEqual(payload["policy_baseline_samples"], 7)
        self.assertEqual(payload["policy_behavior_samples"], 8)
        self.assertEqual(payload["policy_reward_lift"], 0.2)
        self.assertTrue(payload["policy_comparison_ready"])
        self.assertNotIn("samples", payload)

    def test_gateway_payload_keeps_only_bounded_segment_coverage(self):
        payload = gateway_payload({
            "policy_comparison": {"status": "insufficient_data", "baseline": {}, "behavior_aware": {}},
            "policy_segment_coverage": {
                "traveler_group": {"available_segments": 1, "segment_count": 2, "status": "available", "samples": ["private"]},
            },
        }, "planner-v5")
        self.assertEqual(payload["policy_segment_coverage"]["traveler_group"], {"available_segments": 1, "segment_count": 2, "status": "available"})
        self.assertNotIn("samples", payload["policy_segment_coverage"]["traveler_group"])

    def test_gateway_payload_exports_segmented_fairness_aggregates(self):
        payload = gateway_payload({
            "fairness_segment_summary": {
                "status": "available", "max_stddev": 0.08,
                "threshold_exceeded": 0, "dimensions": {"traveler_group": {"segments": ["private"]}},
            },
        }, "planner-v5")
        self.assertTrue(payload["fairness_segment_required"])
        self.assertEqual(payload["fairness_segment_max_stddev"], 0.08)
        self.assertEqual(payload["fairness_segment_threshold_exceeded"], 0)
        self.assertNotIn("dimensions", payload)

    def test_dry_run_audit_keeps_only_bounded_fairness_dimension_summaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "audit.jsonl")
            append_audit(path, {
                "status": "ready",
                "model_version": "planner-v6",
                "fairness_segment_dimensions": {
                    "traveler_group": {"status": "available", "available_segments": 2, "segment_count": 3, "max_stddev": 0.08, "threshold_exceeded": 0, "segments": {"private": {"samples": ["secret"]}}},
                    "destination": {"status": "insufficient_data", "available_segments": 0, "segment_count": 1, "max_stddev": None, "threshold_exceeded": 0},
                },
            })
            record = json.loads(Path(path).read_text(encoding="utf-8").strip())
            dimensions = record["fairness_segment_dimensions"]
            self.assertEqual(dimensions["traveler_group"]["available_segments"], 2)
            self.assertNotIn("segments", dimensions["traveler_group"])

    def test_callback_requires_gateway_accepted_envelope_even_on_http_200(self):
        class EmptySuccessResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b'{"message":"proxy success"}'

        with patch("tools.run_model_evaluation.urllib.request.urlopen", return_value=EmptySuccessResponse()):
            result = post_report("http://gateway.test/evaluation", "protected-token", {"status": "ready"})
        self.assertEqual(result, {"status_code": 200, "accepted": False})

    def test_lock_excludes_overlapping_jobs_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "evaluation.lock")
            first = EvaluationLock(path)
            second = EvaluationLock(path)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()
            self.assertFalse(Path(path).exists())

    def test_stale_lock_can_be_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluation.lock"
            path.write_text(json.dumps({"pid": 1}), encoding="utf-8")
            old = path.stat().st_mtime - 7200
            os.utime(path, (old, old))
            lock = EvaluationLock(str(path), max_age_seconds=60)
            self.assertTrue(lock.acquire())
            lock.release()

    def test_audit_record_is_credential_free_and_append_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "audit.jsonl")
            append_audit(path, {
                "status": "insufficient_evidence",
                "model_version": "planner-v4",
                "sample_count": 3,
                "callback": {"accepted": False},
                "token": "must-not-be-written",
            })
            record = json.loads(Path(path).read_text(encoding="utf-8").strip())
            self.assertEqual(record["model_version"], "planner-v4")
            self.assertNotIn("token", record)

    def test_audit_record_keeps_only_provider_quality_aggregates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "audit.jsonl")
            append_audit(path, {
                "status": "insufficient_evidence",
                "model_version": "planner-v7",
                "provider_quality_status": "blocked",
                "provider_quality_providers": 3,
                "provider_quality_ready_providers": 1,
                "provider_quality_insufficient_providers": 1,
                "provider_quality_blocked_providers": 1,
                "provider_quality_entries": [{"url": "https://secret.invalid", "token": "secret"}],
            })
            record = json.loads(Path(path).read_text(encoding="utf-8").strip())
            self.assertEqual(record["provider_quality_status"], "blocked")
            self.assertEqual(record["provider_quality_providers"], 3)
            self.assertEqual(record["provider_quality_blocked_providers"], 1)
            self.assertNotIn("provider_quality_entries", record)

    def test_dry_run_executes_sqlite_to_evaluation_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "events.db")
            connection = sqlite3.connect(db_path)
            connection.execute("CREATE TABLE planning_events (id TEXT, event_type TEXT, created_at TEXT, user_id TEXT, trip_id TEXT, plan_id TEXT, payload TEXT, value TEXT)")
            for index in range(1, 31):
                enabled = index > 15
                payload = {
                    "model_version": "planner-v1" if index <= 15 else "planner-v2",
                    "reward": 0.8 if not enabled else 0.95,
                    "propensity": 0.5,
                    "behavior_enabled": enabled,
                    "behavior_verified": 1 if enabled else 0,
                    "behavior_adopted": 1 if enabled else 0,
                    "destination": "杭州",
                    "season": "summer",
                    "budget_bucket": "3000_10000",
                    "traveler_group": "pair",
                    "fairness_minimum_utility": 0.8,
                    "fairness_utility_stddev": 0.04,
                    "fairness_threshold": 0.1,
                    "fairness_threshold_exceeded": False,
                }
                connection.execute("INSERT INTO planning_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                    str(index), "planning_decision_audit", f"2026-02-{index:02d}T00:00:00Z", "u", f"t-{index}", f"p-{index}", json.dumps(payload), None,
                ))
            connection.commit()
            connection.close()
            result = run_once(db_path, "planner-v2", "2", dry_run=True)
            self.assertEqual(result["status"], "ready")
            self.assertTrue(result["promotion_ready"])
            self.assertEqual(result["fairness_segment_status"], "available")
            self.assertEqual(result["fairness_segment_threshold_exceeded"], 0)


if __name__ == "__main__":
    unittest.main()
