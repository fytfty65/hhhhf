"""联合优化的回归锁（P2）。

用户目标里的原话就是这组测试的判据：
    "住宿总成本 = 房价 + 每日通勤时间 + 通勤费用 + 换酒店损失"
    "住宿位置变化后，应同步影响：每日通勤时间、交通费用、可游玩节点数量、成员体力消耗、
      碳排放、预算余额"
以及诚实口径：**缺价格不能当作 0**、**缺距离不能当作零通勤**。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.joint_optimization import (  # noqa: E402
    LODGING_SWITCH_PENALTY_YUAN,
    evaluate_lodging,
    haversine_km,
    lodging_joint_payload,
    rank_lodging_options,
)

# 三天行程：每天 3 个节点，坐标固定在库尔勒附近，便于用距离制造差异
CITY = [86.15, 41.76]


def route(day_offsets=(0.0, 0.01, 0.02), per_day=3):
    nodes = []
    for index, offset in enumerate(day_offsets, start=1):
        for slot in range(per_day):
            nodes.append({"day": index, "name": f"d{index}-n{slot}", "lnglat": [CITY[0] + offset, CITY[1] + offset], "type": "culture"})
    return nodes


class TestHaversine(unittest.TestCase):
    def test_same_point_is_zero_but_invalid_is_none(self):
        self.assertEqual(haversine_km(CITY, CITY), 0.0)
        # 非法坐标必须是 None —— 不能拿 0 当"距离为零"
        for bad in (None, "", "博斯腾湖", [0, 0]):
            self.assertIsNone(haversine_km(CITY, bad))

    def test_known_distance_is_plausible(self):
        # 库尔勒 → 乌鲁木齐 直线约 260-300 km
        distance = haversine_km([86.15, 41.76], [87.62, 43.83])
        self.assertTrue(200 < distance < 320, f"距离不合理: {distance}")


class TestLodgingCoupling(unittest.TestCase):
    def test_farther_lodging_worsens_everything_coupled(self):
        plan = route()
        near = evaluate_lodging({"name": "近", "lnglat": [86.151, 41.761], "cost": "¥300"}, plan, nights=2)
        far = evaluate_lodging({"name": "远", "lnglat": [86.45, 42.05], "cost": "¥300"}, plan, nights=2)
        # 位置变远 → 通勤时间 / 费用 / 体力 / 碳排 全部变差，可玩节点变少（这就是"联合"的含义）
        self.assertGreater(far["commute_minutes_total"], near["commute_minutes_total"])
        self.assertGreater(far["commute_cost"], near["commute_cost"])
        self.assertGreater(far["fatigue_index"], near["fatigue_index"])
        self.assertGreater(far["carbon_kg"], near["carbon_kg"])
        self.assertLessEqual(far["playable_nodes"], near["playable_nodes"])
        self.assertGreater(far["total_cost_known"], near["total_cost_known"])

    def test_formula_matches_documented_definition(self):
        """总成本 = 房价 + 通勤费用 + 通勤时间折价 + 换酒店损失（逐项可复算）。"""
        plan = route()
        result = evaluate_lodging({"name": "换过的酒店", "lnglat": [86.151, 41.761], "cost": "¥300", "is_switch": True}, plan, nights=2)
        expected = round(result["lodging_total"] + result["commute_cost"] + result["commute_time_value"] + result["switch_penalty"], 2)
        self.assertEqual(result["total_cost_known"], expected)
        self.assertEqual(result["switch_penalty"], LODGING_SWITCH_PENALTY_YUAN, "换酒店损失要按公式计入")
        self.assertEqual(result["lodging_total"], 600.0)

    def test_budget_remaining_is_reported(self):
        result = evaluate_lodging({"name": "近", "lnglat": [86.151, 41.761], "cost": "¥300"}, route(), nights=2, budget_total=2000)
        self.assertEqual(result["budget_remaining"], round(2000 - result["total_cost_known"], 2))


class TestHonestyRules(unittest.TestCase):
    def test_unknown_price_is_not_treated_as_free(self):
        result = evaluate_lodging({"name": "没报价的酒店", "lnglat": [86.151, 41.761], "cost": "暂无供应商数据"}, route(), nights=2)
        self.assertTrue(result["lodging_price_unknown"])
        self.assertIn("lodging_price", result["unknown_components"])
        self.assertIsNone(result["lodging_total"], "未知房价不能变成 0")
        # 总成本只统计已知分项，且不因为房价未知就宣称"很便宜"
        self.assertGreater(result["total_cost_known"], 0)

    def test_unmeasured_distance_is_not_treated_as_zero_commute(self):
        no_coords = evaluate_lodging({"name": "无坐标", "cost": "¥300"}, route(), nights=2)
        self.assertEqual(no_coords["commute_minutes_total"], 0.0)
        self.assertEqual(sorted(no_coords["unmeasured_days"]), [1, 2, 3], "无法测量的天要如实列出，而不是当成零通勤")
        self.assertEqual(no_coords["playable_nodes"], no_coords["planned_nodes"], "测不到距离就不臆造惩罚")

    def test_carbon_source_is_labelled(self):
        result = evaluate_lodging({"name": "近", "lnglat": CITY, "cost": "¥300"}, route(), nights=2)
        self.assertIn(result["carbon_source"], {"api.carbon_service", "fallback_constant"}, "碳排来源必须标明")


class TestParetoRanking(unittest.TestCase):
    def test_cheap_but_far_can_lose_once_commute_is_priced_in(self):
        """**联合优化的核心价值**：住宿便宜但远，算上通勤成本后总成本可能反而更高。

        （我最初写这条测试时断言"两者都该保留"，结果模型给出了相反且更正确的答案：
          远到 50km 时通勤费用+时间折价远超房价差额，于是"贵但近"在**所有**目标上都不差，
          属于被支配方案。测试据此改为断言这个事实。）
        """
        plan = route()
        ranked = rank_lodging_options(
            [
                {"name": "便宜但远", "lnglat": [86.5, 42.1], "cost": "¥150"},
                {"name": "贵但近", "lnglat": [86.151, 41.761], "cost": "¥800"},
            ],
            plan,
            nights=2,
        )
        evaluated = {item["lodging_name"]: item for item in ranked["evaluated"]}
        self.assertGreater(
            evaluated["便宜但远"]["total_cost_known"],
            evaluated["贵但近"]["total_cost_known"],
            "把通勤费用与时间折价算进去后，便宜的住宿总成本反而更高",
        )
        self.assertIn("便宜但远", ranked["dominated"], "它在所有目标上都不优于近的那份 → 应被支配")
        self.assertIn("贵但近", {item["lodging_name"] for item in ranked["pareto"]})

    def test_genuine_tradeoff_is_kept_and_explained(self):
        """真正的取舍要保留：更便宜但通勤略多 vs 更贵但更近。"""
        plan = route()
        ranked = rank_lodging_options(
            [
                {"name": "便宜略远", "lnglat": [86.17, 41.78], "cost": "¥200"},
                {"name": "贵一点很近", "lnglat": [86.1505, 41.7605], "cost": "¥300"},
            ],
            plan,
            nights=2,
        )
        names = {item["lodging_name"] for item in ranked["pareto"]}
        self.assertEqual(len(names), 2, f"各有取舍时不应互相支配：{names}")
        self.assertTrue(ranked["tradeoffs"], "要给出'最省/最少通勤/碳最低'的解释")
        self.assertIn("最省", " ".join(ranked["tradeoffs"]))

    def test_an_option_dominated_on_everything_is_excluded(self):
        plan = route()
        options = [
            {"name": "全面更优", "lnglat": [86.151, 41.761], "cost": "¥200"},
            {"name": "全面更差", "lnglat": [86.6, 42.2], "cost": "¥900"},
        ]
        ranked = rank_lodging_options(options, plan, nights=2)
        self.assertEqual({item["lodging_name"] for item in ranked["pareto"]}, {"全面更优"})
        self.assertIn("全面更差", ranked["dominated"])

    def test_unknown_price_does_not_win_by_being_unknown(self):
        """带未知分项的方案不能因为"未知=没成本"而在成本维度上胜出。"""
        plan = route()
        options = [
            {"name": "已知价格", "lnglat": [86.151, 41.761], "cost": "¥300"},
            {"name": "价格未知", "lnglat": [86.151, 41.761], "cost": "暂无供应商数据"},
        ]
        ranked = rank_lodging_options(options, plan, nights=2)
        # 两者地理位置相同 → 在其余维度上打平；成本维度因未知被排除比较，因此两者都不支配对方
        self.assertEqual(len(ranked["pareto"]), 2, "未知价格不能成为'更便宜'的理由")


class TestLodgingJointPayload(unittest.TestCase):
    """前端/网关拿到的联合评估载荷：算过就说算过，缺数据就说不算，绝不装作算过。"""

    def test_route_and_candidates_produce_evaluated_payload(self):
        payload = lodging_joint_payload(
            [{"name": "近", "lnglat": [86.151, 41.761], "cost": "¥300"},
             {"name": "远", "lnglat": [86.6, 42.2], "cost": "¥300"}],
            route(),
            nights=2,
            budget_total=3000,
        )
        self.assertEqual(payload["status"], "evaluated")
        self.assertTrue(payload["candidates"], "至少要留下一个住宿候选")
        self.assertIn("远", payload["dominated"], "全面更差的候选应被支配并说明")
        self.assertTrue(payload["tradeoffs"])
        self.assertIn("methodology", payload)
        first = payload["candidates"][0]
        for key in ("total_cost_known", "commute_minutes_total", "carbon_kg", "budget_remaining"):
            self.assertIn(key, first)

    def test_missing_route_is_skipped_with_reason_not_fake_zero_commute(self):
        payload = lodging_joint_payload([{"name": "近", "lnglat": [86.151, 41.761]}], [])
        self.assertEqual(payload["status"], "skipped")
        self.assertEqual(payload["reason"], "no_route")
        self.assertNotIn("candidates", payload)

    def test_missing_candidates_is_skipped_with_reason(self):
        payload = lodging_joint_payload([], route())
        self.assertEqual(payload["status"], "skipped")
        self.assertEqual(payload["reason"], "no_lodging_candidates")

    def test_non_mapping_rows_are_ignored_not_crashed(self):
        payload = lodging_joint_payload(["垃圾数据", None], route())  # type: ignore[list-item]
        self.assertEqual(payload["status"], "skipped")
        self.assertEqual(payload["reason"], "no_lodging_candidates")


if __name__ == "__main__":
    unittest.main()
