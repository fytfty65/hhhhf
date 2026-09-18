"""方案复核/修补（critic + repair + review loop）的确定性单测。

这一层的价值在于"能自动修的修掉、修不动的如实说"，所以测试重点不是文案，而是：
- 哪些问题被判成"可确定性修补"，哪些判成"要数据/只能人工"；
- 修补**绝不新增编造节点**（缺玩点/餐/住宿必须留给候选池）；
- 掉门禁/掉分**必须回滚**；修补没改动就立刻停（别空转）。
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_review import (  # noqa: E402
    critique_plan,
    repair_plan,
    review_plan,
)

CONTEXT = {"days": 2, "budget": 2000}
EXPECTATIONS = {"min_nodes_per_day": 1, "max_nodes_per_day": 4}


def node(day, name, kind="文化", time="10:00", cost="¥100", open_time=None, **extra):
    payload = {
        "day": day, "name": name, "location": name, "time": time, "type": kind,
        "cost_estimate": cost, "data_sources": {"cost_estimate": "amap"}, "estimated": False,
    }
    if open_time:
        payload["open_time"] = open_time
    payload.update(extra)
    return payload


def broken_plan():
    """两天：第 1 天时间倒流 + 重复地点（住宿被排到了上午），第 2 天时间越界。"""
    return {"route": [
        node(1, "博物馆A", "文化", "14:00", open_time="09:00-17:00"),
        node(1, "餐馆B", "餐饮", "12:30", open_time="10:00-22:00"),
        node(1, "博物馆A", "文化", "16:00", open_time="09:00-17:00"),
        node(1, "酒店H", "住宿", "09:00"),
        node(2, "景点C", "文化", "10:00"),
        node(2, "餐馆D", "餐饮", "05:00"),
    ]}


def clean_plan():
    return {"route": [
        node(1, "博物馆A", "文化", "09:00", open_time="09:00-17:00"),
        node(1, "餐馆B", "餐饮", "12:00", open_time="10:00-22:00"),
        node(1, "酒店H", "住宿", "20:00"),
        node(2, "景点C", "文化", "09:00"),
        node(2, "餐馆D", "餐饮", "12:30"),
    ]}


class TestCritique(unittest.TestCase):
    def test_classifies_fixable_and_needs_data(self):
        critique = critique_plan(CONTEXT, broken_plan(), EXPECTATIONS)
        self.assertIn("reschedule", critique["fixable"])
        self.assertFalse(critique["gate_passed"])
        codes = {item["code"] for item in critique["hard_failures"]}
        self.assertIn("day_out_of_order", codes)
        self.assertIn("time_out_of_window", codes)
        self.assertIn("needs_data", critique["needs_data"])  # 没开放时间的节点要补数据，不是硬失败

    def test_missing_meals_or_lodging_are_marked_needs_candidates(self):
        plan = {"route": [node(1, "景点A", "文化", "10:00"), node(2, "景点B", "文化", "10:00")]}
        critique = critique_plan(CONTEXT, plan, EXPECTATIONS)
        self.assertIn("needs_candidates", critique["needs_data"])
        self.assertIn("skeleton_lodging_shortfall", {item["code"] for item in critique["hard_failures"]})

    def test_clean_plan_has_no_hard_failures(self):
        critique = critique_plan(CONTEXT, clean_plan(), EXPECTATIONS)
        self.assertEqual(critique["hard_failures"], [])


class TestRepairPlan(unittest.TestCase):
    def test_reschedules_in_place_and_puts_lodging_last(self):
        result = repair_plan(CONTEXT, broken_plan(), EXPECTATIONS)
        route = result["plan"]["route"]
        times = [n["time"] for n in route if n["day"] == 1]
        self.assertEqual(times, sorted(times))                 # 时间不再倒流
        day1 = [n["name"] for n in route if n["day"] == 1]
        self.assertEqual(day1, ["博物馆A", "餐馆B", "酒店H"])   # 去重 + 住宿排最后
        self.assertEqual(route[2]["time"], "20:00")             # 住宿不早于 20:00
        self.assertTrue(result["changed"])

    def test_reuses_existing_valid_times_instead_of_shifting_everything(self):
        """时间颠倒时优先"换位"（复用当天已有的合法时间），而不是整体往后平移。"""
        result = repair_plan(CONTEXT, broken_plan(), EXPECTATIONS)
        day1 = {n["name"]: n["time"] for n in result["plan"]["route"] if n["day"] == 1}
        self.assertEqual(day1["博物馆A"], "12:30")
        self.assertEqual(day1["餐馆B"], "14:00")

    def test_respects_open_window_when_rescheduling(self):
        plan = {"route": [node(1, "夜market", "餐饮", "10:00", open_time="18:00-23:00")]}
        result = repair_plan({"days": 1, "budget": 1000}, plan, {"min_nodes_per_day": 1})
        self.assertEqual(result["plan"]["route"][0]["time"], "18:00")

    def test_drops_out_of_range_days(self):
        plan = {"route": [node(1, "A"), node(5, "越界")]}
        result = repair_plan({"days": 2, "budget": 1000}, plan, EXPECTATIONS)
        names = [n["name"] for n in result["plan"]["route"]]
        self.assertNotIn("越界", names)
        self.assertEqual([a["code"] for a in result["actions"] if a["code"] == "drop_out_of_range"], ["drop_out_of_range"])
        self.assertEqual(result["actions"][0]["days"], [5])

    def test_same_hotel_on_consecutive_nights_is_not_deduped(self):
        """同一家酒店连住三晚 = 三个同名节点，绝不能被去重删掉（否则住宿夜数不达标）。"""
        plan = {"route": [
            node(1, "同一家酒店", "住宿", "20:00"),
            node(2, "同一家酒店", "住宿", "20:00"),
            node(3, "同一家酒店", "住宿", "20:00"),
        ]}
        result = repair_plan({"days": 3, "budget": 3000}, plan, {"min_nodes_per_day": 1})
        self.assertEqual(len(result["plan"]["route"]), 3)
        self.assertEqual([a["code"] for a in result["actions"]], [])

    def test_never_invents_nodes(self):
        before = broken_plan()
        result = repair_plan(CONTEXT, copy.deepcopy(before), EXPECTATIONS)
        self.assertLessEqual(len(result["plan"]["route"]), len(before["route"]))

    def test_clean_plan_needs_no_change(self):
        result = repair_plan(CONTEXT, clean_plan(), EXPECTATIONS)
        self.assertFalse(result["changed"])
        self.assertEqual(result["actions"], [])

    def test_input_plan_is_not_mutated(self):
        plan = broken_plan()
        snapshot = copy.deepcopy(plan)
        repair_plan(CONTEXT, plan, EXPECTATIONS)
        self.assertEqual(plan, snapshot)


class TestReviewLoop(unittest.TestCase):
    def test_fixes_hard_failures_and_reports_actions(self):
        result = review_plan(CONTEXT, broken_plan(), EXPECTATIONS)
        self.assertTrue(result["improved"])
        self.assertLess(result["remaining_hard"], result["initial_hard_failures"])
        self.assertTrue(any(action["code"] == "reschedule" for action in result["actions"]))
        day1_times = [n["time"] for n in result["plan"]["route"] if n["day"] == 1]
        self.assertEqual(day1_times, sorted(day1_times))
        self.assertNotIn("博物馆A", [n["name"] for n in result["plan"]["route"]][1:])  # 重复的已删

    def test_clean_plan_stops_without_repairing(self):
        result = review_plan(CONTEXT, clean_plan(), EXPECTATIONS)
        self.assertEqual(result["stopped_reason"], "clean")
        self.assertEqual(result["rounds"], [])
        self.assertFalse(result["improved"])

    def test_needs_data_stops_instead_of_guessing(self):
        """只有"缺餐/缺住宿"这类要靠候选池的问题时，不许瞎补，直接停并如实上报。"""
        plan = {"route": [node(1, "景点A", "文化", "10:00")] }
        result = review_plan({"days": 1, "budget": 1000}, plan, EXPECTATIONS)
        self.assertEqual(result["stopped_reason"], "needs_data")
        self.assertIn("needs_candidates", result["needs_data"])
        self.assertEqual([n["name"] for n in result["plan"]["route"]], ["景点A"])

    def test_regression_is_rejected_and_rolled_back(self):
        def bad_repair(context, plan, expectations=None):
            worsened = {"route": [dict(node) for node in plan["route"][:1]]}  # 删掉一天 → 硬失败更多
            return {"plan": worsened, "actions": [{"code": "reschedule"}], "changed": True}

        result = review_plan(CONTEXT, broken_plan(), EXPECTATIONS, repair=bad_repair)
        self.assertEqual(result["stopped_reason"], "no_improvement")
        self.assertIn("博物馆A", [n["name"] for n in result["plan"]["route"]])

    def test_no_change_stops_immediately(self):
        def noop_repair(context, plan, expectations=None):
            return {"plan": plan, "actions": [], "changed": False}

        result = review_plan(CONTEXT, broken_plan(), EXPECTATIONS, repair=noop_repair)
        self.assertEqual(result["stopped_reason"], "no_change")
        self.assertEqual(len(result["rounds"]), 1)
        self.assertFalse(result["rounds"][0]["accepted"])

    def test_max_rounds_is_respected(self):
        """每轮只修一天：5 天全倒流 + 最多 2 轮 → 必须停在 max_rounds，而不是无限修。"""
        plan = {"route": []}
        for day in range(1, 6):
            plan["route"].append(node(day, f"玩点{day}", "文化", "14:00"))
            plan["route"].append(node(day, f"餐{day}", "餐饮", "12:00"))

        def one_day_per_round(context, current, expectations=None):
            route = [dict(item) for item in current["route"]]
            for day in range(1, 6):
                day_nodes = [item for item in route if item["day"] == day]
                if [item["time"] for item in day_nodes] != sorted(item["time"] for item in day_nodes):
                    for index, item in enumerate(day_nodes):
                        item["time"] = ["09:00", "12:00"][index]
                    return {"plan": {"route": route}, "actions": [{"code": "reschedule", "day": day}], "changed": True}
            return {"plan": current, "actions": [], "changed": False}

        result = review_plan({"days": 5, "budget": 5000}, plan, {"min_nodes_per_day": 1}, max_rounds=2, repair=one_day_per_round)
        self.assertEqual(result["stopped_reason"], "max_rounds")
        self.assertEqual(len(result["rounds"]), 2)
        self.assertLess(result["remaining_hard"], result["initial_hard_failures"])


if __name__ == "__main__":
    unittest.main()
