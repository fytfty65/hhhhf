import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.proposal_governance import apply_candidate_gates, gate_candidate_pool


class TestProposalGovernance(unittest.TestCase):
    def test_only_evidenced_proposed_candidates_enter_pool(self):
        pool = [{"name": "A", "risk_score": 10}, {"name": "B", "risk_score": 10}]
        proposal = {
            "status": "ok",
            "candidates": [{"name": "A"}],
            "evidence": [{"field": "poi", "provider": "amap"}],
        }
        result = gate_candidate_pool(pool, proposal)
        self.assertEqual([item["name"] for item in result["accepted"]], ["A"])
        self.assertEqual(result["rejected"][0]["reason"], "not_in_specialist_proposal")

    def test_degraded_proposal_cannot_silently_pass(self):
        result = gate_candidate_pool([{"name": "A"}], {"status": "degraded", "failure": {"code": "timeout"}})
        self.assertEqual(result["accepted"], [])
        self.assertEqual(result["status"], "degraded")

    def test_apply_gates_reports_each_domain(self):
        result = apply_candidate_gates(
            {"dining": [{"name": "餐厅"}], "lodging": [{"name": "酒店"}]},
            {"proposals": [
                {"agent_id": "dining-agent", "status": "ok", "candidates": [{"name": "餐厅"}], "evidence": [{"field": "poi", "provider": "amap"}]},
                {"agent_id": "lodging-agent", "status": "degraded", "failure": {"code": "empty"}},
            ]},
        )
        self.assertEqual(len(result["pools"]["dining"]), 1)
        self.assertEqual(result["pools"]["lodging"], [])
        self.assertEqual(result["audits"]["lodging"]["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
