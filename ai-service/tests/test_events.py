"""统一事件协议的回归锁（P0：可追踪 / 可重放 / 可审计）。

背景：全仓库 grep `correlation_id` / `causation_id` 零命中 —— 事件信封只有
`{type, room_id, payload, ts}`，无法回答"哪次请求""谁触发谁"，因此重放/审计/学习都没有底座。
这些测试把新协议的关键约束钉死，尤其是**向后兼容**（现有网关 WS 桥与前端读的是旧字段）。
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.events import (  # noqa: E402
    ENVELOPE_VERSION,
    EVENT_TYPES,
    PRODUCERS,
    EventValidationError,
    dedupe_key,
    event_types,
    new_event,
    validate_event,
)


class TestEventEnvelope(unittest.TestCase):
    def _event(self, **overrides):
        kwargs = {
            "correlation_id": "trip-456",
            "producer": "scenic-agent",
            "causation_id": "evt-122",
            "room_id": "ROOM1",
            "evidence": [{"provider": "amap", "observed_at": "2026-10-03T10:00:00Z"}],
            "confidence": 0.82,
        }
        kwargs.update(overrides)
        return new_event("candidate.proposed", {"name": "博斯腾湖"}, **kwargs)

    def test_envelope_carries_traceability_fields(self):
        event = self._event()
        for field in (
            "event_id",
            "correlation_id",
            "causation_id",
            "event_type",
            "producer",
            "target",
            "version",
            "occurred_at",
            "payload",
            "evidence",
            "confidence",
        ):
            self.assertIn(field, event, f"信封缺字段 {field}")
        self.assertEqual(event["version"], ENVELOPE_VERSION)
        self.assertEqual(event["correlation_id"], "trip-456")
        self.assertEqual(event["causation_id"], "evt-122")
        self.assertEqual(event["confidence"], 0.82)
        self.assertEqual(event["evidence"][0]["provider"], "amap")

    def test_backward_compatible_mirror_fields(self):
        """现有消费端（网关 WS 桥/前端）读 type/room_id/payload/ts —— 升级不能把它们弄没。"""
        event = self._event()
        self.assertEqual(event["type"], "candidate.proposed")
        self.assertEqual(event["room_id"], "ROOM1")
        self.assertEqual(event["payload"], {"name": "博斯腾湖"})
        self.assertIsInstance(event["ts"], int)
        # ts 必须与 occurred_at 一致（毫秒），否则前端排序会错
        parsed = datetime.fromisoformat(event["occurred_at"])
        self.assertEqual(event["ts"], int(parsed.timestamp() * 1000))

    def test_correlation_id_is_mandatory(self):
        """没有关联键就无法重放/审计，因此必填 —— 宁可发不出去。"""
        with self.assertRaises(EventValidationError):
            new_event("candidate.proposed", {}, correlation_id="", producer="scenic-agent")
        with self.assertRaises(EventValidationError):
            new_event("candidate.proposed", {}, correlation_id="   ", producer="scenic-agent")

    def test_unknown_type_or_producer_is_rejected(self):
        with self.assertRaises(EventValidationError):
            new_event("candidate.teleported", {}, correlation_id="trip-1", producer="scenic-agent")
        with self.assertRaises(EventValidationError):
            new_event("candidate.proposed", {}, correlation_id="trip-1", producer="mystery-agent")

    def test_confidence_range_and_timezone_enforced(self):
        with self.assertRaises(EventValidationError):
            self._event(confidence=1.5)
        with self.assertRaises(EventValidationError):
            self._event(occurred_at=datetime(2026, 10, 3, 10, 0, 0))  # naive datetime

    def test_event_type_whitelist_covers_target_architecture(self):
        """目标架构里点名的事件类型必须都在白名单里（缺了就没法审计这类动作）。"""
        for name in (
            "trip.created",
            "intent.parsed",
            "candidate.proposed",
            "candidate.rejected",
            "constraint.failed",
            "route.drafted",
            "route.validated",
            "route.repaired",
            "route.selected",
            "user.accepted",
            "user.rejected",
            "node.replaced",
            "risk.changed",
            "replan.requested",
            "replan.confirmed",
            "trip.visited",
        ):
            self.assertIn(name, EVENT_TYPES)
        self.assertGreaterEqual(len(PRODUCERS), 11, "至少覆盖注册表里的 11 个智能体")
        self.assertEqual(len(event_types()), len(EVENT_TYPES))


class TestValidateAndDedupe(unittest.TestCase):
    def test_validate_accepts_fresh_event(self):
        event = new_event("risk.changed", {"severity": "high"}, correlation_id="trip-9", producer="risk-agent")
        self.assertEqual(validate_event(event)["event_type"], "risk.changed")

    def test_validate_rejects_tampered_event(self):
        event = new_event("risk.changed", {}, correlation_id="trip-9", producer="risk-agent")
        broken = dict(event, correlation_id="")
        with self.assertRaises(EventValidationError):
            validate_event(broken)
        with self.assertRaises(EventValidationError):
            validate_event(dict(event, event_type="nope.nope"))
        with self.assertRaises(EventValidationError):
            validate_event(dict(event, occurred_at="2026-10-03 10:00"))

    def test_dedupe_key_is_stable_and_causally_aware(self):
        first = new_event("route.repaired", {}, correlation_id="trip-9", producer="validator", causation_id="evt-1")
        repeat = new_event("route.repaired", {}, correlation_id="trip-9", producer="validator", causation_id="evt-1")
        other_cause = new_event("route.repaired", {}, correlation_id="trip-9", producer="validator", causation_id="evt-2")
        # 同一因果分支上的同类事件 → 同一个去重键（重复投递应被丢弃）
        self.assertEqual(dedupe_key(first), dedupe_key(repeat))
        # 不同因果 → 不同键（不能误杀）
        self.assertNotEqual(dedupe_key(first), dedupe_key(other_cause))
        # 不同关联（不同行程）→ 不同键
        other_trip = new_event("route.repaired", {}, correlation_id="trip-10", producer="validator", causation_id="evt-1")
        self.assertNotEqual(dedupe_key(first), dedupe_key(other_trip))


if __name__ == "__main__":
    unittest.main()
