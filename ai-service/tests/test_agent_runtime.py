import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent_contracts import AgentInput
from core.agent_runtime import SpecialistRuntime, WIRED_AGENT_IDS, build_pool_runtime


class TestSpecialistRuntime(unittest.TestCase):
    def test_pool_runtime_runs_specialists_in_parallel_and_keeps_evidence(self):
        runtime, requests = build_pool_runtime(
            attractions=[{"name": "景点 A"}],
            transport=[{"mode": "train", "estimated": True}],
            lodging=[{"name": "酒店 A"}],
            dining=[{"name": "餐厅 A"}],
        )
        result = asyncio.run(runtime.execute(requests))
        self.assertTrue(result["parallel"])
        # 用接线清单推导，而不是写死数字：以后接新智能体时这里不会变成"过期断言"，
        # 但"清单与实现不符"仍会被 test_agent_coverage 里的对账测试抓住。
        self.assertEqual(result["completed"], len(WIRED_AGENT_IDS))
        self.assertGreaterEqual(result["degraded"], 2)
        transport = next(item for item in result["proposals"] if item["agent_id"] == "transport-agent")
        self.assertEqual(transport["status"], "ok")
        self.assertEqual(transport["evidence"][0]["provider"], "transport.provider")

    def test_timeout_is_a_degraded_proposal_with_fallback(self):
        async def slow(_request):
            await asyncio.sleep(0.05)
            return {"status": "ok", "candidates": [{"name": "x"}], "evidence": [{"field": "x", "provider": "test", "observed_at": "2026-01-01T00:00:00Z"}]}

        runtime = SpecialistRuntime({"risk-agent": slow})
        runtime.capabilities["risk-agent"].timeout_ms = 20
        request = AgentInput(request_id="r1", agent_id="risk-agent", domain="risk", timeout_ms=100)
        result = asyncio.run(runtime.execute([request]))
        proposal = result["proposals"][0]
        self.assertEqual(proposal["status"], "degraded")
        self.assertEqual(proposal["failure"]["code"], "agent_timeout")

    def test_malformed_success_cannot_pass_validation(self):
        async def malformed(_request):
            return {"status": "ok", "candidates": [{"name": "x"}], "evidence": []}

        runtime = SpecialistRuntime({"risk-agent": malformed})
        request = AgentInput(request_id="r2", agent_id="risk-agent", domain="risk")
        result = asyncio.run(runtime.execute([request]))
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["proposals"][0]["failure"]["code"], "agent_failed")


if __name__ == "__main__":
    unittest.main()
