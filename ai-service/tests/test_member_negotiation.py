"""成员级协商协议的单测（纯计算，无网络、无 LLM）。

覆盖：
* 单人出行的退化情况；
* "高满意度成员让位、异议清零"的完整协商；
* 无法安全让位时如实返回 no_consensus；
* `non_negotiable` 成员的底线被尊重；
* 成员字段（角色/兴趣/不可让步）进入输出，且意图能映射成兴趣标签。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.member_negotiation import MemberAgent, member_interests, negotiate_member_routes  # noqa: E402


def _route(tags):
    return [{"name": f"node-{index}", "tags": [tag], "day": 1} for index, tag in enumerate(tags)]


SCENIC_ONLY = _route(["风景", "风景", "风景", "风景"])
MIXED = _route(["风景", "美食", "风景", "美食"])
CULTURE_MIXED = _route(["风景", "美食", "购物", "风景"])


class MemberNegotiationTest(unittest.TestCase):
    def test_single_member_reaches_consensus_without_concessions(self):
        result = negotiate_member_routes(
            [SCENIC_ONLY, MIXED],
            [{"id": "u1", "name": "独自出行", "interests": ["风景"]}],
            candidate_labels=["AI 主方案", "小众探索"],
        )
        self.assertEqual(result["status"], "consensus")
        self.assertEqual(result["rounds"], 0)
        self.assertEqual(result["concessions"], [])
        self.assertEqual(len(result["members"]), 1)
        self.assertFalse(result["members"][0]["dissenting"])
        self.assertTrue(result["transcript"][-1].startswith("协商结论"))

    def test_concession_clears_dissent_and_records_who_gave_up_what(self):
        # 风景派与美食派：maximin 先选混合方案；风景派落差超过容忍度成为异议。
        # 美食派愿意让出 0.225（预算 0.30），换到纯风景方案后异议清零。
        members = [
            {"id": "m-scenic", "name": "风景派", "interests": ["风景"], "tolerance": 0.05},
            {"id": "m-food", "name": "美食派", "interests": ["美食"], "tolerance": 0.50, "concession_budget": 0.30},
        ]
        result = negotiate_member_routes(
            [SCENIC_ONLY, MIXED],
            members,
            candidate_labels=["AI 主方案", "小众探索"],
        )
        self.assertEqual(result["initial_candidate"]["index"], 1)  # maximin 先选混合方案
        self.assertEqual(result["final_candidate"]["index"], 0)  # 协商后切到纯风景
        self.assertEqual(result["status"], "consensus")
        self.assertEqual(result["dissenters_final"], [])
        self.assertEqual(len(result["concessions"]), 1)
        concession = result["concessions"][0]
        self.assertEqual(concession["member_id"], "m-food")
        self.assertAlmostEqual(concession["utility_loss"], 0.225, places=3)
        self.assertLessEqual(concession["utility_loss"], concession["budget"])
        self.assertFalse(result["min_utility_improved"])  # 诚实：最低值下降，但仍在阈值之上
        self.assertTrue(any("让位" in line for line in result["transcript"]))
        # 每位成员都有首选方案与排序，前端可以逐人展示
        for row in result["members"]:
            self.assertIn("proposal", row)
            self.assertGreaterEqual(len(row["ranked"]), 2)

    def test_no_safe_concession_reports_no_consensus_instead_of_faking_one(self):
        members = [
            {"id": "m-scenic", "name": "风景派", "interests": ["风景"]},
            {"id": "m-food", "name": "美食派", "interests": ["美食"]},
        ]
        result = negotiate_member_routes(
            [SCENIC_ONLY, MIXED],
            members,
            candidate_labels=["AI 主方案", "小众探索"],
        )
        # 默认容忍度 0.12 / 让步预算 0.08：切到任一方案都会制造新的异议成员
        self.assertEqual(result["status"], "no_consensus")
        self.assertEqual(result["concessions"], [])
        self.assertEqual(result["min_utility_before"], result["min_utility_after"])
        self.assertTrue(result["transcript"][-1].startswith("协商结论：未达成共识"))
        self.assertTrue(result["dissenters_final"])

    def test_non_negotiable_member_is_never_traded_away(self):
        members = [
            {"id": "m-fixed", "name": "不可让步成员", "interests": ["风景"], "non_negotiable": True, "acceptance_threshold": 0.8},
            {"id": "m-food", "name": "美食派", "interests": ["美食"]},
            {"id": "m-shop", "name": "购物派", "interests": ["购物"]},
        ]
        result = negotiate_member_routes(
            [SCENIC_ONLY, CULTURE_MIXED],
            members,
            candidate_labels=["AI 主方案", "小众探索"],
        )
        # 混合方案的最低效用更高，但会让不可让步成员掉到 0.675 < 0.8，因此被排除
        self.assertEqual(result["initial_candidate"]["index"], 0)
        self.assertEqual(result["final_candidate"]["index"], 0)
        self.assertEqual(result["concessions"], [])
        fixed = next(row for row in result["members"] if row["id"] == "m-fixed")
        self.assertTrue(fixed["non_negotiable"])
        self.assertEqual(fixed["role"], "")

    def test_member_fields_and_intent_mapping_are_preserved(self):
        self.assertEqual(member_interests({"intent": "想多吃本地美食，顺便看风景"}), ["风景", "美食"])
        self.assertEqual(member_interests({"interests": ["咖啡"]}), ["咖啡"])
        agent = MemberAgent.from_mapping(
            {"id": "m1", "name": "小王", "role": "摄影", "interests": ["风景"], "non_negotiable": True},
            index=0,
        )
        self.assertEqual(agent.id, "m1")
        self.assertEqual(agent.role, "摄影")
        self.assertTrue(agent.non_negotiable)

    def test_empty_inputs_are_reported_honestly(self):
        no_candidates = negotiate_member_routes([], [{"id": "u1", "name": "甲"}])
        self.assertEqual(no_candidates["status"], "no_candidates")
        self.assertIsNone(no_candidates["final_candidate"])

        no_members = negotiate_member_routes([SCENIC_ONLY], [])
        self.assertEqual(no_members["status"], "no_members")
        self.assertEqual(no_members["selected_index"], 0)


if __name__ == "__main__":
    unittest.main()
