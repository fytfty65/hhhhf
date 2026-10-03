"""住宿智能体（联合评估版）的诚实分支锁。

判据来自用户口径："住哪"不能只看房价，要看它对通勤/费用/体力/碳排/可玩节点/预算的连锁影响；
以及**没有依据时不许假装算过**：
    - 没有路线 → 如实说"未做通勤联合评估"，不臆造零通勤；
    - 候选池为空 → degraded + candidate_pool_empty，不编造推荐；
    - 联合评估抛错 → degraded + joint_evaluation_failed，绝不悄悄退回假结果。
"""

import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent_contracts import AgentInput  # noqa: E402
from core.agent_runtime import lodging_handler  # noqa: E402

CITY = [86.15, 41.76]


def plan() -> list[dict]:
    nodes = []
    for day in (1, 2, 3):
        for slot in range(3):
            nodes.append({"day": day, "name": f"d{day}-n{slot}", "lnglat": [CITY[0] + 0.01, CITY[1] + 0.01]})
    return nodes


def request(candidates: list) -> AgentInput:
    return AgentInput(
        request_id="runtime-lodging-agent",
        agent_id="lodging-agent",
        domain="lodging",
        context={"candidates": list(candidates)},
        hard_constraints={},
        soft_preferences={},
        allowed_tools=[],
    )


class TestLodgingAgentHonesty(unittest.TestCase):
    def test_no_route_says_it_did_not_evaluate_commute(self):
        handler = lodging_handler(route=())
        result = asyncio.run(handler(request([{"name": "酒店 A", "cost": "¥300"}])))
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["degraded"])
        self.assertEqual(len(result["candidates"]), 1)
        self.assertNotIn("joint_evaluation", result["candidates"][0], "没算就不能挂联合评估字段")
        self.assertIn("没有路线", result["explanation"])

    def test_empty_pool_degrades_instead_of_inventing(self):
        handler = lodging_handler(route=plan())
        result = asyncio.run(handler(request([])))
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(result["degraded"])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["failure"]["code"], "candidate_pool_empty")

    def test_with_route_candidates_carry_joint_evaluation(self):
        handler = lodging_handler(route=plan(), nights=2, budget_total=3000)
        result = asyncio.run(handler(request([
            {"name": "近", "lnglat": [86.151, 41.761], "cost": "¥300"},
            {"name": "远", "lnglat": [86.7, 42.3], "cost": "¥300"},
        ])))
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["candidates"], "有路线时必须给出保留的候选")
        for item in result["candidates"]:
            joint = item["joint_evaluation"]
            for key in ("commute_minutes_total", "commute_cost", "carbon_kg", "playable_nodes", "estimated"):
                self.assertIn(key, joint)
            self.assertIsNotNone(joint["budget_remaining"], "给了预算就要给出余额")
        self.assertIn("Pareto", result["explanation"])

    def test_unknown_price_is_not_treated_as_zero(self):
        handler = lodging_handler(route=plan(), nights=2)
        result = asyncio.run(handler(request([
            {"name": "价格未知", "lnglat": [86.151, 41.761], "cost": "暂无供应商数据"},
        ])))
        joint = result["candidates"][0]["joint_evaluation"]
        self.assertIsNone(joint["lodging_total"])
        self.assertIn("lodging_price", joint["unknown_components"])

    def test_evaluation_failure_degrades_loudly(self):
        handler = lodging_handler(route=plan())
        with mock.patch("core.agent_runtime.rank_lodging_options", side_effect=RuntimeError("boom")):
            result = asyncio.run(handler(request([{"name": "酒店 A", "lnglat": [86.151, 41.761]}])))
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(result["degraded"])
        self.assertEqual(result["failure"]["code"], "joint_evaluation_failed")
        self.assertEqual(result["failure"]["fallback"], "area_level_recommendation")


if __name__ == "__main__":
    unittest.main()
