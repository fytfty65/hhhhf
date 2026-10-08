"""工具权限校验测试（P2，2026-10-08）。

用户点名要的行为：
    `agent_id=transport-agent` 调 `amap.poi.search` → **拒绝**；
    调 `provider.transport.search` → **允许**。

以及用户给的理由："即使暂无商业 API 也要先把权限机制做完整"。所以这里验的是**机制**，
不是某个 API 现在通不通。
"""

import os
import sys
import unittest
from datetime import datetime, timezone

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent_contracts import AgentInput  # noqa: E402
from core.agent_runtime import SpecialistRuntime, build_pool_runtime, declared_tools  # noqa: E402
from core.tool_permissions import (  # noqa: E402
    REASON_ALLOWED,
    REASON_NOT_DECLARED,
    REASON_NOT_GRANTED,
    audit_tool_calls,
    check_tool_call,
    filter_tool_calls,
    tool_matches,
)


class ToolMatchingTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(tool_matches("amap.poi.search", "amap.poi.search"))
        self.assertFalse(tool_matches("amap.poi.search", "amap.poi.detail"))

    def test_prefix_wildcard(self):
        self.assertTrue(tool_matches("amap.*", "amap.poi.search"))
        self.assertTrue(tool_matches("*", "任意工具"))
        # 点号要保留：amap.* 不该匹配 amapx.foo
        self.assertFalse(tool_matches("amap.*", "amapx.foo"))

    def test_empty_never_matches(self):
        self.assertFalse(tool_matches("", "amap.poi.search"))
        self.assertFalse(tool_matches("amap.*", ""))


class CheckToolCallTests(unittest.TestCase):
    """用户点名的那个例子。"""

    def test_transport_agent_calling_amap_poi_search_is_denied(self):
        declared = declared_tools("transport-agent")
        verdict = check_tool_call(
            agent_id="transport-agent", tool="amap.poi.search",
            declared_tools=declared, granted_tools=declared,
        )
        self.assertFalse(verdict["allowed"], "transport-agent 不该能调 amap.poi.search")
        self.assertEqual(verdict["reason"], REASON_NOT_DECLARED)
        self.assertIn("未声明", verdict["detail"])

    def test_transport_agent_calling_provider_transport_search_is_allowed(self):
        declared = declared_tools("transport-agent")
        verdict = check_tool_call(
            agent_id="transport-agent", tool="provider.transport.search",
            declared_tools=declared, granted_tools=declared,
        )
        self.assertTrue(verdict["allowed"], f"应该放行，实际：{verdict}")
        self.assertEqual(verdict["reason"], REASON_ALLOWED)

    def test_declared_but_not_granted_is_still_denied(self):
        """声明了是能力上限；本次没授予就不能用 —— 否则"权限"只是文档。"""
        verdict = check_tool_call(
            agent_id="transport-agent", tool="provider.transport.search",
            declared_tools=["provider.transport.search"], granted_tools=[],
        )
        self.assertFalse(verdict["allowed"])
        self.assertEqual(verdict["reason"], REASON_NOT_GRANTED)
        self.assertIn("为空", verdict["detail"])

    def test_agent_with_no_declared_tools_can_call_nothing(self):
        verdict = check_tool_call(
            agent_id="route-planner", tool="amap.poi.search",
            declared_tools=[], granted_tools=[],
        )
        self.assertFalse(verdict["allowed"])

    def test_wildcard_grant_allows_family(self):
        verdict = check_tool_call(
            agent_id="x", tool="amap.poi.search",
            declared_tools=["amap.*"], granted_tools=["amap.*"],
        )
        self.assertTrue(verdict["allowed"])


class FilterToolCallsTests(unittest.TestCase):
    def test_splits_allowed_and_denied(self):
        result = filter_tool_calls(
            agent_id="transport-agent",
            tool_calls=[
                {"tool": "provider.transport.search", "source": "provider"},
                {"tool": "amap.poi.search", "source": "amap"},
            ],
            declared_tools=declared_tools("transport-agent"),
            granted_tools=declared_tools("transport-agent"),
        )
        self.assertEqual([item["tool"] for item in result["allowed"]], ["provider.transport.search"])
        self.assertEqual([item["tool"] for item in result["denied"]], ["amap.poi.search"])
        self.assertEqual(result["denied"][0]["reason"], REASON_NOT_DECLARED)

    def test_audit_summarises_denials(self):
        audit = audit_tool_calls([
            {"tool_calls": [{"tool": "a"}], "denied_tool_calls": [{"tool": "b", "reason": "x"}]},
        ])
        self.assertEqual(audit["total_tool_calls"], 1)
        self.assertEqual(audit["denied_count"], 1)

    def test_allowed_entries_keep_their_original_shape(self):
        """回归：放行的条目不能被塞进裁决字段。

        `AgentProposal.tool_calls` 的元素是 `ToolCall`（`extra="forbid"`），
        多一个 `allowed`/`reason` 字段会让**整个提案**校验失败、被判成 agent_failed
        —— 即"放行的调用反而把提案搞挂"。2026-10-08 实测踩到过。
        """
        result = filter_tool_calls(
            agent_id="transport-agent",
            tool_calls=[{"tool": "provider.transport.search", "source": "provider"}],
            declared_tools=declared_tools("transport-agent"),
            granted_tools=declared_tools("transport-agent"),
        )
        self.assertEqual(
            result["allowed"], [{"tool": "provider.transport.search", "source": "provider"}],
            "放行的条目必须原样保留，不许带上裁决字段",
        )
        for key in ("allowed", "reason", "detail"):
            self.assertNotIn(key, result["allowed"][0])


class RuntimeEnforcementTests(unittest.TestCase):
    """端到端：越权调用要在运行时被拦下，并且**记进提案**里。"""

    def _runtime_with(self, agent_id, tool_calls):
        from core.agent_contracts import AgentProposal

        async def handler(request: AgentInput) -> AgentProposal:
            # 契约要求：status="ok" 的提案必须至少有一个候选与一条证据
            #（`successful_proposal_requires_candidate`）—— 所以这里给一个真候选，
            # 否则提案会被契约判非法，权限记录也就看不到（实测踩过）。
            return AgentProposal(
                agent_id=request.agent_id, domain=request.domain, status="ok",
                candidates=[{"mode": "transit"}],
                evidence=[
                    {
                        "field": "distance_km",
                        "provider": "provider.transport",
                        "observed_at": datetime.now(timezone.utc),
                    }
                ],
                confidence=0.5, tool_calls=tool_calls,
            )

        runtime = SpecialistRuntime({agent_id: handler})
        request = AgentInput(
            request_id="t", agent_id=agent_id, domain="transport",
            allowed_tools=declared_tools(agent_id),
        )
        import asyncio

        return asyncio.run(runtime.execute([request]))

    def test_denied_call_is_recorded_not_silently_dropped(self):
        result = self._runtime_with(
            "transport-agent",
            [{"tool": "amap.poi.search", "source": "amap", "arguments": {}}],
        )
        proposal = result["proposals"][0]
        self.assertEqual(proposal["tool_calls"], [], "越权调用不该留在 tool_calls 里")
        denied = proposal["denied_tool_calls"]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["tool"], "amap.poi.search")
        self.assertEqual(denied[0]["reason"], REASON_NOT_DECLARED)

    def test_allowed_call_passes_through(self):
        result = self._runtime_with(
            "transport-agent",
            [{"tool": "provider.transport.search", "source": "provider", "arguments": {}}],
        )
        proposal = result["proposals"][0]
        self.assertEqual(len(proposal["tool_calls"]), 1)
        self.assertEqual(proposal["denied_tool_calls"], [])

    def test_execute_exposes_the_audit(self):
        result = self._runtime_with(
            "transport-agent",
            [{"tool": "amap.poi.search", "source": "amap", "arguments": {}}],
        )
        audit = result["tool_permission_audit"]
        self.assertEqual(audit["denied_count"], 1)
        self.assertEqual(audit["denied"][0]["agent_id"], "transport-agent")


class AssemblyGrantsTests(unittest.TestCase):
    """装配出来的请求必须真的带上注册表声明的权限（以前一律是空列表）。"""

    def test_every_request_carries_its_declared_tools(self):
        _, requests = build_pool_runtime(route=[{"name": "X", "category": "cultural"}])
        self.assertTrue(requests)
        for request in requests:
            with self.subTest(agent=request.agent_id):
                self.assertEqual(
                    request.allowed_tools, declared_tools(request.agent_id),
                    f"{request.agent_id} 的 allowed_tools 与注册表不一致",
                )

    def test_at_least_one_agent_actually_has_tools(self):
        """防止"全都声明成空列表"这种假绿：必须有人真的有权限。"""
        _, requests = build_pool_runtime(route=[{"name": "X", "category": "cultural"}])
        self.assertTrue(any(request.allowed_tools for request in requests))


if __name__ == "__main__":
    unittest.main()
