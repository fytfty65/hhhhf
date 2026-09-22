from datetime import datetime, timezone
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent_contracts import capability_registry, validate_agent_proposal


def evidence():
    return [{"field": "name", "provider": "amap", "observed_at": datetime.now(timezone.utc)}]


class AgentContractTests(unittest.TestCase):
    def test_valid_proposal_is_structured_before_selection(self):
        proposal = validate_agent_proposal({
            "agent_id": "transport-agent",
            "domain": "transport",
            "status": "ok",
            "candidates": [{"name": "铁路"}],
            "hard_constraint_results": {"satisfied": True},
            "evidence": evidence(),
            "confidence": 0.8,
        })
        self.assertEqual(proposal.domain, "transport")
        self.assertEqual(proposal.evidence[0].provider, "amap")


    def test_success_requires_candidate_and_evidence(self):
        with self.assertRaisesRegex(ValueError, "successful_proposal_requires_candidate"):
            validate_agent_proposal({
                "agent_id": "lodging-agent",
                "domain": "lodging",
                "status": "ok",
                "evidence": evidence(),
            })
        with self.assertRaisesRegex(ValueError, "proposal_requires_evidence"):
            validate_agent_proposal({
                "agent_id": "lodging-agent",
                "domain": "lodging",
                "status": "ok",
                "candidates": [{"name": "酒店"}],
            })


    def test_degraded_proposal_must_explain_fallback(self):
        with self.assertRaisesRegex(ValueError, "degraded_proposal_requires_failure_info"):
            validate_agent_proposal({
                "agent_id": "risk-agent",
                "domain": "risk",
                "status": "degraded",
                "degraded": True,
                "candidates": [{"name": "人工复核"}],
                "evidence": evidence(),
            })

    def test_capability_registry_has_unique_domains_and_safe_timeouts(self):
        capabilities = capability_registry()
        self.assertGreaterEqual(len(capabilities), 8)
        self.assertEqual(len({item.agent_id for item in capabilities}), len(capabilities))
        self.assertTrue(all(100 <= item.timeout_ms <= 120000 for item in capabilities))
        self.assertTrue(all(item.evidence_required and item.allowed_tools and item.fallback for item in capabilities))


if __name__ == "__main__":
    unittest.main()
