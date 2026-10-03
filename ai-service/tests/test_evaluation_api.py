import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.evaluation import evaluation_health, read_evaluation_audit


class EvaluationApiTests(unittest.TestCase):
    def test_missing_audit_file_is_insufficient_data(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"MODEL_EVALUATION_AUDIT_FILE": str(Path(directory) / "missing.jsonl")}):
                report = read_evaluation_audit()
        self.assertEqual(report["status"], "insufficient_data")
        self.assertEqual(report["entries"], [])

    def test_reader_returns_only_allowlisted_aggregates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            path.write_text(json.dumps({
                "recorded_at": 10,
                "status": "insufficient_evidence",
                "promotion_ready": False,
                "model_version": "planner-v4",
                "sample_count": 3,
                "validation_samples": 1,
                "callback": {"accepted": False, "status_code": 409, "token": "secret"},
                "raw_events": [{"user_id": "private"}],
            }) + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"MODEL_EVALUATION_AUDIT_FILE": str(path)}):
                report = read_evaluation_audit()
        entry = report["latest"]
        self.assertEqual(report["status"], "available")
        self.assertEqual(entry["model_version"], "planner-v4")
        self.assertEqual(entry["callback"], {"accepted": False, "status_code": 409})
        self.assertNotIn("raw_events", entry)
        self.assertNotIn("token", entry["callback"])

    def test_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            path.write_text("\n".join(json.dumps({"recorded_at": i, "status": "ready"}) for i in range(5)) + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"MODEL_EVALUATION_AUDIT_FILE": str(path)}):
                report = read_evaluation_audit(2)
        self.assertEqual(report["count"], 2)
        self.assertEqual(report["entries"][0]["recorded_at"], 3)

    def test_reader_exposes_bounded_policy_segment_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            path.write_text(json.dumps({
                "recorded_at": 10, "status": "insufficient_evidence",
                "policy_segment_coverage": {
                    "traveler_group": {"available_segments": 1, "segment_count": 2, "status": "available", "samples": ["private"]},
                },
            }) + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"MODEL_EVALUATION_AUDIT_FILE": str(path)}):
                entry = read_evaluation_audit()["latest"]
        self.assertEqual(entry["policy_segment_coverage"]["traveler_group"], {"available_segments": 1, "segment_count": 2, "status": "available"})

    def test_reader_exposes_provider_quality_aggregates_without_raw_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            path.write_text(json.dumps({
                "recorded_at": 10,
                "status": "insufficient_evidence",
                "provider_quality_status": "blocked",
                "provider_quality_providers": 3,
                "provider_quality_ready_providers": 1,
                "provider_quality_insufficient_providers": 1,
                "provider_quality_blocked_providers": 1,
                "provider_quality_entries": [{"url": "https://secret.invalid", "user_id": "private"}],
            }) + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"MODEL_EVALUATION_AUDIT_FILE": str(path)}):
                entry = read_evaluation_audit()["latest"]
        self.assertEqual(entry["provider_quality_status"], "blocked")
        self.assertEqual(entry["provider_quality_providers"], 3)
        self.assertEqual(entry["provider_quality_blocked_providers"], 1)
        self.assertNotIn("provider_quality_entries", entry)

    def test_health_marks_stale_task_critical(self):
        health = evaluation_health([{"recorded_at": 100, "status": "ready"}], now=100 + 7201)
        self.assertEqual(health["status"], "critical")
        self.assertIn("超过规定时间", health["reason"])

    def test_insufficient_evidence_is_warning_not_execution_failure(self):
        health = evaluation_health([{"recorded_at": 100, "status": "insufficient_evidence"}], now=101)
        self.assertEqual(health["status"], "warning")
        self.assertEqual(health["consecutive_failures"], 0)

    def test_three_failed_callbacks_are_critical(self):
        entries = [
            {"recorded_at": 100 + index, "status": "ready", "callback": {"accepted": False}}
            for index in range(3)
        ]
        health = evaluation_health(entries, now=103)
        self.assertEqual(health["status"], "critical")
        self.assertEqual(health["consecutive_failures"], 3)


if __name__ == "__main__":
    unittest.main()
