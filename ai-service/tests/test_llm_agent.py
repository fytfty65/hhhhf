"""LLM 专业智能体处理器（core/llm_agent.py）的诚实边界。

覆盖四条必须成立的口径：
  1. 只采纳池内名字（池外候选一律丢弃并记账，不采纳）；
  2. 事实来自候选池，模型只能给选择理由（不能改价格/坐标/评分）；
  3. 池子为空 / 输出不可解析 / 调用失败 / 全是池外候选 → `degraded` + FailureInfo；
  4. 多轮消息总线的收件箱与本轮裁决会进入提示词。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent_contracts import AgentInput
from core.llm_agent import build_messages, llm_handler, parse_proposal

POOL = [
    {
        "name": "莫高窟",
        "lnglat": "94.809,40.042",
        "cost_estimate": "¥238",
        "rating": "4.8",
        "type": "文化古迹",
    },
    {
        "name": "鸣沙山月牙泉",
        "lnglat": "94.667,40.089",
        "cost_estimate": "¥110",
        "rating": "4.7",
        "type": "自然风景",
    },
]


def make_request(pool=None, *, messages=None, revision="") -> AgentInput:
    context = {"candidates": POOL if pool is None else pool}
    if messages is not None:
        context["messages"] = messages
    if revision:
        context["revision_directive"] = revision
    return AgentInput(
        request_id="req-llm",
        agent_id="route-planner",
        domain="route",
        context=context,
        hard_constraints={"max_far_scenic": 2},
        timeout_ms=15000,
    )


class FakeComplete:
    def __init__(self, output: str = "", *, raises: Exception | None = None):
        self.output = output
        self.raises = raises
        self.calls: list[list[dict]] = []

    async def __call__(self, messages):
        self.calls.append(messages)
        if self.raises is not None:
            raise self.raises
        return self.output


def run(handler, request):
    return asyncio.run(handler(request))


class ParseProposalTest(unittest.TestCase):
    def test_tolerates_fences_and_prose(self):
        parsed = parse_proposal(
            '下面是我的提案：\n```json\n{"candidates": [{"name": "莫高窟"}], "confidence": 0.8}\n```'
        )
        self.assertEqual(parsed["candidates"][0]["name"], "莫高窟")

    def test_rejects_non_json(self):
        with self.assertRaises(ValueError):
            parse_proposal("我觉得莫高窟不错。")
        with self.assertRaises(ValueError):
            parse_proposal("")


class LlmHandlerTest(unittest.TestCase):
    def test_accepts_pool_names_and_keeps_pool_facts(self):
        output = json.dumps(
            {
                "candidates": [
                    {"name": "莫高窟", "reason": "人文核心，上午光线好"},
                    {"name": "敦煌夜市", "reason": "编造的池外地点"},
                ],
                "explanation": "优先人文核心，夜市不在池内已丢弃",
                "confidence": 1.7,
            },
            ensure_ascii=False,
        )
        complete = FakeComplete(output)
        handler = llm_handler("route-planner", "route", complete=complete)
        proposal = run(handler, make_request())

        self.assertEqual(proposal["status"], "ok")
        self.assertEqual(len(proposal["candidates"]), 1)
        kept = proposal["candidates"][0]
        self.assertEqual(kept["name"], "莫高窟")
        # 事实来自池子：模型没机会改价格/坐标
        self.assertEqual(kept["cost_estimate"], "¥238")
        self.assertEqual(kept["lnglat"], "94.809,40.042")
        self.assertEqual(kept["selection_reason"], "人文核心，上午光线好")
        self.assertEqual(proposal["confidence"], 1.0)  # 夹到 [0,1]
        self.assertEqual(
            proposal["applied_constraints"]["llm_dropped_not_in_pool"], ["敦煌夜市"]
        )
        self.assertEqual(len(complete.calls), 1)

    def test_all_invented_candidates_degrade(self):
        complete = FakeComplete(
            json.dumps({"candidates": [{"name": "外滩"}, {"name": "迪士尼"}]}, ensure_ascii=False)
        )
        proposal = run(llm_handler("route-planner", "route", complete=complete), make_request())
        self.assertEqual(proposal["status"], "degraded")
        self.assertEqual(proposal["failure"]["code"], "llm_candidates_not_in_pool")
        self.assertFalse(proposal["failure"]["retryable"])
        self.assertEqual(proposal["candidates"], [])

    def test_empty_pool_never_calls_llm(self):
        complete = FakeComplete('{"candidates": [{"name": "莫高窟"}]}')
        proposal = run(
            llm_handler("route-planner", "route", complete=complete), make_request(pool=[])
        )
        self.assertEqual(proposal["status"], "degraded")
        self.assertEqual(proposal["failure"]["code"], "candidate_pool_empty")
        self.assertEqual(complete.calls, [])

    def test_unparsable_output_degrades_without_adoption(self):
        proposal = run(
            llm_handler("route-planner", "route", complete=FakeComplete("随便说说")),
            make_request(),
        )
        self.assertEqual(proposal["status"], "degraded")
        self.assertEqual(proposal["failure"]["code"], "llm_output_unparsable")

    def test_call_failure_degrades(self):
        proposal = run(
            llm_handler(
                "route-planner", "route", complete=FakeComplete(raises=RuntimeError("boom"))
            ),
            make_request(),
        )
        self.assertEqual(proposal["status"], "degraded")
        self.assertEqual(proposal["failure"]["code"], "llm_call_failed")
        self.assertTrue(proposal["failure"]["retryable"])

    def test_messages_and_directive_reach_the_prompt(self):
        request = make_request(
            messages=[{"round_no": 0, "sender": "lodging-agent", "topic": "proposal"}],
            revision="减少远景区，控制在 2 个以内",
        )
        messages = build_messages(request)
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["messages"][0]["sender"], "lodging-agent")
        self.assertIn("远景区", payload["revision_directive"])
        self.assertEqual(payload["candidate_pool_total"], 2)
        # 池内事实被裁剪成紧凑行，但名字/价格仍在
        self.assertEqual(payload["candidate_pool"][0]["name"], "莫高窟")
        self.assertEqual(payload["candidate_pool"][0]["cost_estimate"], "¥238")


class RuntimeWiringTest(unittest.TestCase):
    def test_route_planner_uses_llm_only_when_enabled(self):
        from core.agent_runtime import build_pool_runtime

        complete = FakeComplete(
            json.dumps(
                {"candidates": [{"name": "鸣沙山月牙泉", "reason": "顺路"}], "confidence": 0.6},
                ensure_ascii=False,
            )
        )

        async def scenario(with_llm: bool):
            runtime, requests = build_pool_runtime(
                attractions=POOL,
                llm_complete=complete if with_llm else None,
                llm_agent_ids=("route-planner",) if with_llm else (),
            )
            result = await runtime.execute(requests)
            route = next(
                row for row in result["proposals"] if row["agent_id"] == "route-planner"
            )
            return route, result

        route, result = asyncio.run(scenario(True))
        self.assertEqual(len(route["candidates"]), 1)
        self.assertEqual(route["candidates"][0]["name"], "鸣沙山月牙泉")
        self.assertEqual(len(complete.calls), 1)
        self.assertIn("message_round", result)

        offline, _ = asyncio.run(scenario(False))
        self.assertEqual(len(offline["candidates"]), len(POOL))  # 确定性候选池：全部保留

    def test_bus_round_trip_between_agents(self):
        from core.agent_bus import AgentMessageBus
        from core.agent_runtime import build_pool_runtime

        async def scenario():
            runtime, requests = build_pool_runtime(attractions=POOL)
            bus = AgentMessageBus(correlation_id="plan-bus")
            result = await runtime.execute(requests, bus=bus, round_no=0)
            return result, bus

        result, bus = asyncio.run(scenario())
        self.assertEqual(result["message_round"], 0)
        self.assertEqual(result["message_bus"]["messages"], result["completed"])
        self.assertEqual(len(bus.transcript()), result["completed"])
        self.assertEqual({row["topic"] for row in bus.transcript()}, {"proposal"})


if __name__ == "__main__":
    unittest.main()
