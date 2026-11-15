"""多轮智能体消息总线（core/agent_bus.py）的形状与边界。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent_bus import AgentMessageBus
from core.agent_contracts import AgentInput


def make_request(agent_id: str, **context) -> AgentInput:
    return AgentInput(
        request_id="req-bus",
        agent_id=agent_id,
        domain="route",
        context=dict(context),
        timeout_ms=5000,
    )


class AgentMessageBusTest(unittest.TestCase):
    def test_inbox_respects_recipients_and_excludes_self(self):
        bus = AgentMessageBus(correlation_id="plan-1")
        bus.publish(sender="route-planner", topic="proposal", content={"candidates": 4})
        bus.start_round()
        bus.publish(
            sender="coordinator",
            topic="directive",
            recipients=("dining-agent",),
            content={"message": "晚些安排晚饭"},
        )

        self.assertEqual(bus.inbox("route-planner"), [])  # 自己的提案不回流
        self.assertEqual([row["topic"] for row in bus.inbox("lodging-agent")], ["proposal"])
        self.assertEqual([row["topic"] for row in bus.inbox("dining-agent")], ["proposal", "directive"])
        self.assertEqual(bus.inbox("dining-agent")[1]["recipients"], ["dining-agent"])

    def test_since_round_filters_history(self):
        bus = AgentMessageBus()
        bus.publish(sender="route-planner", topic="proposal", round_no=0)
        bus.start_round()
        bus.publish(sender="coordinator", topic="directive", round_no=1)
        self.assertEqual(len(bus.inbox("dining-agent")), 2)
        self.assertEqual(len(bus.inbox("dining-agent", since_round=0)), 1)
        self.assertEqual(bus.inbox("dining-agent", since_round=1), [])

    def test_inject_writes_inbox_and_round(self):
        bus = AgentMessageBus()
        bus.publish(sender="route-planner", topic="proposal", content={"candidates": 2})
        bus.start_round()
        request = make_request("dining-agent")
        request.context["messages"] = [{"stale": True}]
        written = bus.inject(request)
        self.assertEqual(written, 1)
        self.assertEqual(request.context["messages"][0]["topic"], "proposal")
        self.assertEqual(request.context["message_round"], 1)

    def test_message_cap_drops_oldest_and_counts(self):
        bus = AgentMessageBus(max_messages=2)
        bus.publish(sender="a", topic="one")
        bus.publish(sender="a", topic="two")
        bus.publish(sender="a", topic="three")
        rows = bus.transcript()
        self.assertEqual([row["message_id"] for row in rows], ["msg-2", "msg-3"])
        self.assertEqual(bus.dropped, 1)

    def test_zero_capacity_records_nothing(self):
        bus = AgentMessageBus(max_messages=0)
        self.assertIsNone(bus.publish(sender="a", topic="ignored"))
        self.assertEqual(bus.transcript(), [])
        self.assertEqual(bus.dropped, 1)

    def test_summary_is_deterministic(self):
        bus = AgentMessageBus()
        bus.publish(sender="a", topic="proposal")
        bus.start_round()
        bus.publish(sender="coordinator", topic="directive")
        summary = bus.summary()
        self.assertEqual(summary["rounds"], 1)
        self.assertEqual(summary["messages"], 2)
        self.assertEqual(summary["by_topic"], {"directive": 1, "proposal": 1})
        self.assertEqual(summary["by_round"], {"0": 1, "1": 1})
        # 同一输入重复构造应得到同一条记录（无时间戳/随机 id）
        twin = AgentMessageBus()
        twin.publish(sender="a", topic="proposal")
        twin.start_round()
        twin.publish(sender="coordinator", topic="directive")
        self.assertEqual(bus.transcript(), twin.transcript())


if __name__ == "__main__":
    unittest.main()
