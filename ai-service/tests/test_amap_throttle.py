"""高德闸门的**自适应退避**测试（2026-10-05）。

背景：闸门原来用固定间隔（默认 0.15s）。实测这把 key 的 QPS 档位会变紧 ——
`tools/probe_hotel_geocode.py` 里连"酒店"这种宽关键词都回 `CUQPS_HAS_EXCEEDED_THE_LIMIT`
并**静默返回 0 条**；候选池因此凭空变小，而方案里看不出任何异样（"这里好像没什么景点"）。

做法：被限流就把间隔翻倍（到上限为止），成功一次回落一点（不低于下限）；
所有状态都暴露出来，好让日志与方案如实说"这一轮被限流过几次"。
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# api.agent 在导入时就构造 LLM 客户端：没有 key 会直接抛 OpenAIError（其它测试同样这么处理）
os.environ.setdefault("LLM_API_KEY", "test-key-not-used")

from api.agent import (  # noqa: E402
    amap_gate_state,
    amap_max_interval_seconds,
    amap_min_interval_seconds,
    amap_throttle_interval_seconds,
    note_amap_result,
    reset_amap_throttle,
)


class AmapThrottleTests(unittest.TestCase):
    def setUp(self):
        reset_amap_throttle()
        self.env = mock.patch.dict(
            os.environ, {"AMAP_MIN_INTERVAL_SECONDS": "0.2", "AMAP_MAX_INTERVAL_SECONDS": "2.0"}, clear=False
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        reset_amap_throttle()

    def test_starts_at_the_configured_floor(self):
        self.assertAlmostEqual(amap_min_interval_seconds(), 0.2, places=3)
        self.assertAlmostEqual(amap_throttle_interval_seconds(), 0.2, places=3)
        self.assertFalse(amap_gate_state()["throttled"])

    def test_limiting_backs_off_by_doubling(self):
        first = note_amap_result(False, info="CUQPS_HAS_EXCEEDED_THE_LIMIT")
        second = note_amap_result(False, info="CUQPS_HAS_EXCEEDED_THE_LIMIT")
        self.assertAlmostEqual(first, 0.4, places=3)
        self.assertAlmostEqual(second, 0.8, places=3)
        state = amap_gate_state()
        self.assertTrue(state["throttled"])
        self.assertEqual(state["limits"], 2)
        self.assertEqual(state["requests"], 2)
        self.assertIn("CUQPS", state["last_info"])

    def test_backoff_is_capped(self):
        for _ in range(10):
            note_amap_result(False, info="CUQPS")
        self.assertAlmostEqual(amap_throttle_interval_seconds(), amap_max_interval_seconds(), places=3)

    def test_success_decays_gradually_not_instantly(self):
        note_amap_result(False, info="CUQPS")  # → 0.4
        after_success = note_amap_result(True)
        self.assertAlmostEqual(after_success, 0.32, places=3, msg="成功后回落 20%，不立刻打回 0.2")
        self.assertTrue(amap_gate_state()["throttled"], "还没回落到基准，仍算退避中")

    def test_success_never_goes_below_the_floor(self):
        for _ in range(20):
            note_amap_result(True)
        self.assertAlmostEqual(amap_throttle_interval_seconds(), amap_min_interval_seconds(), places=3)
        self.assertFalse(amap_gate_state()["throttled"])

    def test_cap_can_be_configured_below_the_floor_without_breaking(self):
        with mock.patch.dict(os.environ, {"AMAP_MAX_INTERVAL_SECONDS": "0.05"}, clear=False):
            self.assertAlmostEqual(amap_max_interval_seconds(), amap_min_interval_seconds(), places=3)

    def test_state_is_observable_for_the_plan_payload(self):
        note_amap_result(False, info="CUQPS")
        state = amap_gate_state()
        for key in ("interval_seconds", "base_interval_seconds", "throttled", "limits", "requests"):
            self.assertIn(key, state)


class GateUsesTheAdaptiveIntervalTests(unittest.TestCase):
    """接线检查：闸门本身用的是**自适应间隔**，不是写死的 0.15。"""

    def test_gate_waits_for_the_adaptive_interval(self):
        import io

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "api", "agent.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("gap = amap_throttle_interval_seconds() -", source)
        self.assertIn("note_amap_result(True)", source)
        self.assertIn("note_amap_result(False, info=info)", source)

    def test_limiting_is_disclosed_as_a_degradation(self):
        from core.degradation import collect_degradations

        items = collect_degradations(
            {
                "amap_gate": {
                    "interval_seconds": 0.8,
                    "base_interval_seconds": 0.2,
                    "limits_this_round": 2,
                    "last_info": "CUQPS_HAS_EXCEEDED_THE_LIMIT",
                }
            }
        ).get("items", [])
        entry = next((item for item in items if item.get("source") == "amap_gate"), None)
        self.assertIsNotNone(entry, items)
        self.assertIn("限流 2 次", str(entry.get("reason")))
        self.assertIn("0.8s", str(entry.get("reason")))
        self.assertIn("不是「这城市没什么景点」", str(entry.get("reason")))

    def test_no_gate_state_means_no_disclosure(self):
        from core.degradation import collect_degradations

        items = collect_degradations({}).get("items", [])
        self.assertFalse([item for item in items if item.get("source") == "amap_gate"])


if __name__ == "__main__":
    unittest.main()
