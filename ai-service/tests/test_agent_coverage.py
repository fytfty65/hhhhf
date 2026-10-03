"""注册表覆盖度 + 绿色出行智能体的回归锁。

背景（实测 2026-10）：`capability_registry()` 声明 11 个智能体，而 `build_pool_runtime()`
只装配 6 个 —— "注册表很丰满、运行时很骨感"，且此前**没有任何机制会提醒这件事**。
这批测试做两件事：
1. 把覆盖度变成**可断言的数据**（新增注册项必须被分类：要么接线，要么在 DECLARED_NOT_WIRED 里说明原因）；
2. 锁住绿色出行智能体的诚实口径（碳排是方法学估算、缺数据必须降级、绝不编造）。
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent_contracts import AgentInput, capability_registry, validate_agent_proposal  # noqa: E402
from core.agent_runtime import (  # noqa: E402
    DECLARED_NOT_WIRED,
    GREEN_AGENT_ID,
    WIRED_AGENT_IDS,
    build_pool_runtime,
    green_handler,
    registry_coverage,
)


class TestRegistryCoverage(unittest.TestCase):
    def test_every_declared_agent_is_classified(self):
        """注册表里出现的每个 agent_id 都必须被分类：已接线，或在不接线清单里写明原因。

        这条是防止"注册表越写越长、运行时永远只有 6 个"的关键锁。
        """
        declared = {item.agent_id for item in capability_registry()}
        wired = set(WIRED_AGENT_IDS)
        pending = set(DECLARED_NOT_WIRED)
        self.assertEqual(
            declared - wired - pending,
            set(),
            "有注册但既未接线、也没在 DECLARED_NOT_WIRED 里说明原因的智能体",
        )
        self.assertEqual(wired & pending, set(), "同一个智能体不能既接线又列在未接线清单里")
        self.assertTrue(wired.issubset(declared), "接线清单里出现了注册表没有的 id")

    def test_coverage_is_reported_honestly(self):
        coverage = registry_coverage()
        self.assertEqual(coverage["declared_count"], len(capability_registry()))
        self.assertEqual(coverage["wired_count"], len(WIRED_AGENT_IDS))
        self.assertAlmostEqual(
            coverage["coverage_ratio"],
            round(coverage["wired_count"] / coverage["declared_count"], 3),
        )
        self.assertEqual(set(coverage["not_wired"]), set(DECLARED_NOT_WIRED))
        # 未接线的每一个都必须给得出原因（不能是空话）
        for agent_id, reason in coverage["reasons"].items():
            self.assertTrue(reason and len(reason) > 4, f"{agent_id} 的未接原因太含糊: {reason!r}")

    def test_runtime_actually_installs_every_wired_agent(self):
        """接线清单必须与运行时真正装上的 handler 一致（避免清单与实现脱节）。"""
        runtime, requests = build_pool_runtime()
        self.assertEqual(set(runtime.handlers), set(WIRED_AGENT_IDS))
        self.assertEqual({item.agent_id for item in requests}, set(WIRED_AGENT_IDS))


class TestGreenAgent(unittest.TestCase):
    def _run(self, context):
        handler = green_handler()
        request = AgentInput(request_id="r1", agent_id=GREEN_AGENT_ID, domain="sustainability", context=context)
        return asyncio.run(handler(request))

    def test_proposes_lowest_carbon_option_with_methodology_evidence(self):
        proposal = self._run(
            {
                "transport_options": [
                    {"mode": "driving", "distance_km": 120, "duration_minutes": 100},
                    {"mode": "transit", "distance_km": 120, "duration_minutes": 160},
                ]
            }
        )
        validated = validate_agent_proposal(proposal)  # 必须能过协调器那道闸
        self.assertEqual(validated.status, "ok")
        self.assertTrue(validated.candidates, "ok 提案必须有候选")
        best = validated.candidates[0]
        self.assertIn("carbon_kg", best)
        # 公共交通的碳排应低于自驾（若这条不成立，说明方法学接错了）
        self.assertEqual(best["mode"], "transit")
        # 碳排是方法学估算：证据必须标 estimated=True，不能冒充实测
        self.assertTrue(all(item.estimated for item in validated.evidence))
        self.assertIn("方法学估算", validated.explanation or "")

    def test_degrades_instead_of_fabricating_without_data(self):
        proposal = self._run({})
        validated = validate_agent_proposal(proposal)
        self.assertEqual(validated.status, "degraded")
        self.assertEqual(validated.candidates, [])
        self.assertIsNotNone(validated.failure)
        self.assertEqual(validated.failure.fallback, "methodology_estimate")

    def test_skips_options_without_distance_instead_of_treating_them_as_zero(self):
        """缺里程的候选必须被跳过 —— 拿 0 当默认值等于宣称"零碳排"，那是编造。"""
        proposal = self._run(
            {
                "transport_options": [
                    {"mode": "transit"},  # 没有里程 → 跳过
                    {"mode": "driving", "distance_km": 50},
                ]
            }
        )
        validated = validate_agent_proposal(proposal)
        self.assertEqual(validated.status, "ok")
        self.assertEqual(len(validated.candidates), 1)
        self.assertGreater(validated.candidates[0]["carbon_kg"], 0, "真实里程必须算出正的碳排")

    def test_zero_or_negative_distance_is_not_scored(self):
        proposal = self._run({"transport_options": [{"mode": "transit", "distance_km": 0}]})
        validated = validate_agent_proposal(proposal)
        self.assertEqual(validated.status, "degraded")
        self.assertEqual(validated.failure.code, "no_scorable_transport_option")


if __name__ == "__main__":
    unittest.main()
