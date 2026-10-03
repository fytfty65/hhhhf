"""旅中闭环入口的回归锁：`POST /risk/replan-scope`。

它把三件事串起来（此前都是散的）：
1. emergency-agent 产出最小扰动提案（受影响/保留节点、保留率、动作）；
2. 发出**带 correlation_id 的关联事件链**：risk.changed → replan.requested（后者 causation_id 指向前者）；
3. **范围不可判定时不发 replan.requested** —— 拒绝整条路线重生成；
并且**只产出提案与事件、不写入新版本**（确认前不覆盖原方案）。

测试通过替换 `event_bus.emit` 捕获事件，不需要 Redis（离线可跑）。
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ⚠️ 必须先加载 .env 再导入 api.operations：`api/agent.py` 在模块级构造 AsyncOpenAI，
# 缺 LLM_API_KEY 时导入即抛错（fail-fast，但对测试进程来说必须先备好环境）。
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from api import operations  # noqa: E402


def _route():
    return [
        {"day": 1, "name": "day1-spot1", "type": "culture"},
        {"day": 1, "name": "day1-spot2", "type": "culture"},
        {"day": 2, "name": "day2-spot1", "type": "scenic"},
        {"day": 2, "name": "day2-spot2", "type": "food"},
    ]


class _Recorder:
    """替换 event_bus.emit：记录事件参数，不碰 Redis。"""

    def __init__(self):
        self.calls = []

    async def __call__(self, stream, event_type, payload, room_id="", max_len=1000, **kwargs):
        self.calls.append({"stream": stream, "event_type": event_type, "payload": payload,
                           "room_id": room_id, **kwargs})
        return "1-0"


class TestReplanScopeEndpoint(unittest.TestCase):
    def setUp(self):
        self.recorder = _Recorder()
        self._original = operations.event_bus.emit
        operations.event_bus.emit = self.recorder

    def tearDown(self):
        operations.event_bus.emit = self._original

    def _post(self, payload):
        return asyncio.run(operations.risk_replan_scope(payload))

    def test_emits_correlated_event_chain_and_returns_proposal(self):
        result = self._post({
            "correlation_id": "trip-abc",
            "room_id": "ROOM1",
            "route": _route(),
            "risk_event": {
                "event": "weather_changed", "severity": "high",
                "affected_days": [2], "affected_nodes": ["day2-spot1"],
                "recommended_action": "replace_outdoor_activity",
            },
        })
        self.assertEqual(result["correlation_id"], "trip-abc")
        self.assertEqual(result["status"], "ok")
        candidate = result["proposal"]["candidates"][0]
        self.assertEqual(candidate["affected_nodes"], ["day2-spot1"])
        self.assertEqual(candidate["preserved_ratio"], 0.75)
        self.assertTrue(candidate["requires_user_confirmation"], "确认前不得写入新版本")

        types = [call["event_type"] for call in self.recorder.calls]
        self.assertEqual(types, ["risk.changed", "replan.requested"], "应发出风险→重规划的事件链")
        risk_call, replan_call = self.recorder.calls
        # 关联键一致（可追踪/可重放的前提）
        self.assertEqual(risk_call["correlation_id"], "trip-abc")
        self.assertEqual(replan_call["correlation_id"], "trip-abc")
        # 因果链：replan.requested 由这次 risk.changed 触发
        self.assertEqual(replan_call["causation_id"], risk_call["event_id"])
        self.assertNotEqual(risk_call["event_id"], replan_call["event_id"], "两个事件要有各自 id")
        # 生产者与房间要带上（供网关按房间广播）
        self.assertEqual(risk_call["producer"], "risk-agent")
        self.assertEqual(replan_call["producer"], "emergency-agent")
        self.assertEqual(replan_call["room_id"], "ROOM1")
        # 事件载荷要能支撑前端展示"哪些保留、哪些替换"
        self.assertEqual(replan_call["payload"]["affected_nodes"], ["day2-spot1"])
        self.assertEqual(replan_call["payload"]["preserved_ratio"], 0.75)

    def test_unknown_scope_emits_only_risk_change(self):
        result = self._post({"correlation_id": "trip-x", "route": _route(), "risk_event": {"event": "traffic_jam"}})
        self.assertEqual(result["status"], "degraded")
        types = [call["event_type"] for call in self.recorder.calls]
        self.assertEqual(types, ["risk.changed"], "范围不可判定时不得发出 replan.requested（拒绝整条重生成）")
        self.assertEqual(result["proposal"]["candidates"], [])
        self.assertIn("不写入新版本", result["note"])

    def test_missing_route_degrades_but_still_records_the_risk(self):
        result = self._post({"route": [], "risk_event": {"event": "weather_changed", "affected_days": [1]}})
        self.assertEqual(result["status"], "degraded")
        self.assertEqual([call["event_type"] for call in self.recorder.calls], ["risk.changed"])
        # 没给 correlation_id 时也要自动生成一个（否则事件无法追踪）
        self.assertTrue(result["correlation_id"].startswith("trip-"))

    def test_trip_id_is_accepted_as_correlation_id(self):
        result = self._post({
            "trip_id": "trip-from-gateway",
            "route": _route(),
            "risk_event": {"event": "scenic_closed", "affected_days": [1]},
        })
        self.assertEqual(result["correlation_id"], "trip-from-gateway")
        self.assertEqual(self.recorder.calls[1]["correlation_id"], "trip-from-gateway")


if __name__ == "__main__":
    unittest.main()
