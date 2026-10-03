import json
import os
import sqlite3
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.run_staging_smoke import run_smoke


class StagingSmokeTests(unittest.TestCase):
    def test_read_only_smoke_returns_only_bounded_contract_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "events.db")
            connection = sqlite3.connect(db_path)
            connection.execute("CREATE TABLE planning_events (id TEXT, event_type TEXT, created_at TEXT, user_id TEXT, trip_id TEXT, plan_id TEXT, payload TEXT, value TEXT)")
            payload = {
                "model_version": "planner-v7",
                "reward": 0.9,
                "propensity": 0.5,
                "traveler_group": "pair",
                "destination": "杭州",
                "season": "summer",
                "budget_bucket": "3000_10000",
                "fairness_minimum_utility": 0.8,
                "fairness_utility_stddev": 0.04,
                "fairness_threshold": 0.1,
                "fairness_threshold_exceeded": False,
            }
            connection.execute("INSERT INTO planning_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("1", "planning_decision_audit", "2026-01-01T00:00:00Z", "private-user", "private-trip", "private-plan", json.dumps(payload), None))
            connection.commit()
            connection.close()
            result = run_smoke(db_path, "planner-v7")
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["model_version"], "planner-v7")
            self.assertNotIn("user_id", result)
            self.assertNotIn("trip_id", result)
            self.assertNotIn("reasons", result)

    def test_explicit_callback_smoke_sends_only_allowlisted_payload(self):
        received = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - stdlib handler contract
                received["token"] = self.headers.get("X-Omni-Internal-Token")
                length = int(self.headers.get("Content-Length", "0"))
                received["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"data":{"accepted":true}}')

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                db_path = str(Path(directory) / "events.db")
                connection = sqlite3.connect(db_path)
                connection.execute("CREATE TABLE planning_events (id TEXT, event_type TEXT, created_at TEXT, user_id TEXT, trip_id TEXT, plan_id TEXT, payload TEXT, value TEXT)")
                connection.execute("INSERT INTO planning_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("1", "plan_generated", "2026-01-01T00:00:00Z", "private-user", "private-trip", "private-plan", json.dumps({"model_version": "planner-v8"}), None))
                connection.commit()
                connection.close()
                result = run_smoke(db_path, "planner-v8", f"http://127.0.0.1:{server.server_port}/evaluation", "callback-secret")
                self.assertEqual(result["callback"], {"accepted": True, "status_code": 200})
                self.assertEqual(received["token"], "callback-secret")
                self.assertNotIn("dataset", received["body"])
                self.assertNotIn("user_id", received["body"])
                self.assertNotIn("trip_id", received["body"])
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
