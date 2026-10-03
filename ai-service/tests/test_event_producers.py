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
    EVENT_TYPES,
    EXTERNAL_EMITTERS,
    INFRA_PRODUCERS,
    PENDING_WIRING,
    PRODUCERS,
    EventValidationError,
    known_producers,
    new_event,
)

PRODUCER_LITERAL = re.compile(r'producer\s*=\s*"([A-Za-z0-9_.:-]+)"')
# 事件类型字面量：兼容单行与多行 emit（operations.py 里的 emit 就是多行写的）
EMIT_TYPE_LITERAL = re.compile(r'emit\(\s*[^,()]*,\s*"([a-z][a-z_.]*)"', re.S)
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


class TestEventTypeCoverage(unittest.TestCase):
    """事件**类型**的覆盖护栏：登记了就要有人发，没人发就必须在缺口台账里写明归属。"""

    def _emitted_types(self) -> set:
        found = set()
        for path in ROOT.rglob("*.py"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            found.update(EMIT_TYPE_LITERAL.findall(text))
        return found

    def test_emitted_types_are_all_registered(self):
        """事故回归：reasoning_step / negotiation_started / specialist_proposals_ready /
        final_route_ready 曾经在发但没登记 → 走旧信封、没有 correlation_id。"""
        unregistered = sorted(self._emitted_types() - set(EVENT_TYPES))
        self.assertEqual(unregistered, [], f"这些类型在发但没登记（会丢 correlation_id）：{unregistered}")

    def test_registered_types_are_either_emitted_or_explicitly_pending(self):
        registered = set(EVENT_TYPES)
        emitted = self._emitted_types()
        pending = set(PENDING_WIRING)
        external = set(EXTERNAL_EMITTERS)
        self.assertEqual(
            sorted(registered - emitted - pending - external), [],
            "登记了、没有发点、又没进台账（缺口台账或外部发点台账）",
        )
        self.assertEqual(sorted(pending - registered), [], "缺口台账里有没登记过的类型（拼错了？）")
        self.assertEqual(sorted(external - registered), [], "外部发点台账里有没登记过的类型（拼错了？）")
        self.assertEqual(sorted(pending & emitted), [], "已经有发点了，请从 PENDING_WIRING 里删掉")
        self.assertEqual(sorted(external & emitted), [], "同样的类型不该既在 Python 发又在外部发")
        self.assertEqual(sorted(pending & external), [], "同一条不该同时挂在两份台账里")

    def test_external_emitters_point_at_real_files(self):
        """外部发点台账必须指向真实存在的文件，否则只是把"没人发"改个说法。"""
        repo_root = ROOT.parent
        for event_type, reference in EXTERNAL_EMITTERS.items():
            path_part = reference.split("｜")[0].strip()
            self.assertTrue(path_part, f"{event_type} 的外部发点没写文件路径")
            target = repo_root / path_part
            self.assertTrue(target.exists(), f"{event_type} 指向的文件不存在：{path_part}")

    def test_pipeline_milestone_types_are_registered(self):
        for name in ("negotiation_started", "reasoning_step", "specialist_proposals_ready", "final_route_ready"):
            self.assertIn(name, EVENT_TYPES)


if __name__ == "__main__":
    unittest.main()