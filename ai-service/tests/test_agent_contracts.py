from datetime import datetime, timezone
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent_contracts import (
    audit_route_agent_proposal,
    capability_registry,
    validate_agent_proposal,
)


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


class AuditRouteAgentProposalTests(unittest.TestCase):
    """Coverage for the route-domain adapter that was moved here from api/agent.py
    (2026-10-10).

    It had no direct test before the move, which is precisely why a move was risky:
    the only thing standing between a silent behaviour change and production was the
    single call site in ``run_negotiate``. These cases pin the contract that adapter
    is responsible for, so the next relocation is checked rather than hoped for.
    """

    def test_route_with_sources_is_ok(self):
        result = audit_route_agent_proposal([
            {"name": "博斯腾湖", "data_sources": {"rating": {"provider": "amap"}}},
        ])
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["degraded"])
        self.assertEqual(result["evidence_count"], 1)
        self.assertEqual(result["candidate_count"], 1)

    def test_route_without_usable_sources_is_degraded(self):
        """'unavailable' and 'estimated' are not evidence, so the audit must not
        present the route as fully evidenced."""
        for provider in ("unavailable", "estimated", "", None):
            with self.subTest(provider=provider):
                result = audit_route_agent_proposal([
                    {"name": "x", "data_sources": {"rating": {"provider": provider}}},
                ])
                self.assertTrue(result["degraded"])
                self.assertEqual(result["evidence_count"], 0)

    def test_empty_route_is_never_ok(self):
        result = audit_route_agent_proposal([])
        self.assertTrue(result["degraded"])
        self.assertNotEqual(result["status"], "ok")

    def test_malformed_nodes_do_not_raise(self):
        """This runs on model output inside the request path, so garbage in must
        not throw: the audit degrades instead."""
        result = audit_route_agent_proposal([
            None, "not-a-node", 42,
            {"name": "ok-node", "data_sources": "not-a-dict"},
            {"name": "ok-node-2", "data_sources": {"rating": "amap"}},
        ])
        self.assertIn(result["status"], {"ok", "degraded", "failed"})
        self.assertIsInstance(result["evidence_count"], int)

    def test_agent_id_is_echoed_and_defaults(self):
        default = audit_route_agent_proposal([])
        self.assertEqual(default["agent_id"], "route-planner")
        custom = audit_route_agent_proposal([], agent_id="custom-agent")
        self.assertEqual(custom["agent_id"], "custom-agent")

    def test_candidates_are_bounded(self):
        """Route lengths are user-controlled; the audit must not echo unbounded
        input back into the payload."""
        route = [{"name": f"n{i}", "data_sources": {"rating": {"provider": "amap"}}} for i in range(500)]
        result = audit_route_agent_proposal(route)
        self.assertLessEqual(result["candidate_count"], 50)

    def test_api_agent_reexports_the_same_function(self):
        """The call site in run_negotiate imports it from api.agent, so the
        re-export is part of the contract.

        Imported under a patched env because ``api.agent`` builds its LLM client at
        import time and raises without a key (deliberate: fail at startup rather
        than mid-request). Same pattern as test_agent_import_guard.
        """
        import importlib

        from unittest import mock

        with mock.patch.dict(os.environ, {"LLM_API_KEY": "test-key-not-used"}, clear=False):
            agent = importlib.import_module("api.agent")
        self.assertIs(agent.audit_route_agent_proposal, audit_route_agent_proposal)


if __name__ == "__main__":
    unittest.main()
