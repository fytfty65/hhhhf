"""事件生产者白名单的护栏（事故回归：2026-10-03）。

事故经过：`core/events.py` 的 producer 白名单是**手写**的，`emergency-agent` 与
`route-planner` 没被登记。它们发出的 `replan.requested` / 风险事件每次都触发
`unknown_producer`，`RedisEventStream.emit` 于是退回旧信封，`correlation_id / causation_id`
全部丢失 —— **因果链在真实链路里是断的，而单测全绿**。
（真凶由 `tools/probe_event_stream.py` 在真实 Redis 上跑出来，不是推理出来的。）

这些测试保证两件事：
1. 白名单与能力注册表**不会漂移**（派生 + 逐个断言）；
2. 代码里任何 `producer="..."` 字面量都必须是已知生产者（静态扫描，写入即报错）。
"""

import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.agent_runtime import WIRED_AGENT_IDS, capability_registry  # noqa: E402
from core.events import (  # noqa: E402
    INFRA_PRODUCERS,
    PRODUCERS,
    EventValidationError,
    known_producers,
    new_event,
)

PRODUCER_LITERAL = re.compile(r'producer\s*=\s*"([A-Za-z0-9_.:-]+)"')
# tests/ 里会**故意**写非法 producer（就是在测"非法要被拒"），不参与静态扫描
SKIP_DIRS = {"venv", ".venv", "site-packages", "__pycache__", ".git", "node_modules", "work", "tests"}


class TestProducerRegistry(unittest.TestCase):
    def test_every_registered_agent_is_an_accepted_producer(self):
        missing = [item.agent_id for item in capability_registry() if item.agent_id not in known_producers()]
        self.assertEqual(missing, [], f"这些智能体没被登记为 producer，它们的事件会退回旧信封：{missing}")

    def test_wired_agents_are_accepted_producers(self):
        missing = [agent_id for agent_id in WIRED_AGENT_IDS if agent_id not in known_producers()]
        self.assertEqual(missing, [])

    def test_infra_producers_are_accepted(self):
        for name in INFRA_PRODUCERS:
            self.assertIn(name, known_producers())

    def test_emergency_agent_chain_keeps_causation(self):
        """事故回归：应急智能体必须能发出**带因果链**的 replan.requested。"""
        self.assertIn("emergency-agent", PRODUCERS)
        event = new_event(
            "replan.requested",
            {"action": "replace_affected_nodes"},
            correlation_id="trip-1",
            producer="emergency-agent",
            causation_id="evt-risk-1",
        )
        self.assertEqual(event["causation_id"], "evt-risk-1")
        self.assertEqual(event["correlation_id"], "trip-1")
        self.assertEqual(event["version"], 1)

    def test_route_planner_is_an_accepted_producer(self):
        self.assertIn("route-planner", known_producers())
        event = new_event("route.drafted", {"days": 2}, correlation_id="trip-2", producer="route-planner")
        self.assertEqual(event["producer"], "route-planner")

    def test_unknown_producer_is_still_rejected(self):
        with self.assertRaises(EventValidationError):
            new_event("candidate.proposed", {}, correlation_id="trip-3", producer="mystery-agent")

    def test_every_producer_literal_in_the_codebase_is_registered(self):
        """静态护栏：代码里的 producer="..." 必须在白名单里，否则事件会静默退回旧信封。"""
        known = set(known_producers())
        offenders = []
        for path in ROOT.rglob("*.py"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            for match in PRODUCER_LITERAL.finditer(text):
                name = match.group(1)
                if name.strip(".") == "":  # 文档/正则里的占位写法，不是真实生产者
                    continue
                if name not in known:
                    offenders.append(f"{path.relative_to(ROOT)}: producer=\"{name}\"")
        self.assertEqual(offenders, [], "发现了未登记的事件生产者：" + "; ".join(offenders))


if __name__ == "__main__":
    unittest.main()
