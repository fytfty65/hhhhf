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


def candidate(name, price=None, intent="cultural", open_time="09:00-18:00", source="amap"):
    return {
        "name": name, "type": intent, "intent": intent, "price": price,
        "price_source": source if price is not None else "unavailable",
        "rating": "4.6", "open_time": open_time, "estimated": price is None,
        "lnglat": [104.06, 30.67], "source": "amap",
    }


def full_pool():
    return {
        "cultural": [candidate("博物馆候选", 50), candidate("古迹候选", 40)],
        "scenic": [candidate("山水候选", 0, "scenic")],
        "food": [candidate("小吃候选", 30, "food"), candidate("老字号候选", 60, "food")],
        "hotel": [candidate("酒店候选", 300, "hotel", "00:00-23:59")],
    }


class TestPoolRepair(unittest.TestCase):
    """候选池修补：只能从**真实候选**补点，补不动就如实说（绝不编造节点）。"""

    CONTEXT = {"days": 2, "budget": 2000}
    EXPECTATIONS = {"min_nodes_per_day": 2, "max_nodes_per_day": 4, "must_have": ["酒店", "餐"]}

    def _gap_plan(self):
        """第 1 天只有一个玩点、没有餐和住宿；第 2 天完全是空的。"""
        return {"route": [node(1, "某某景点", "文化", "10:00")]}

    def test_gaps_are_filled_from_the_pool_only(self):
        result = review_plan(self.CONTEXT, self._gap_plan(), self.EXPECTATIONS, pool=full_pool())
        names = [n["name"] for n in result["plan"]["route"]]
        pool_names = {c["name"] for group in full_pool().values() for c in group}
        added = [name for name in names if name != "某某景点"]
        self.assertTrue(added)
        self.assertTrue(set(added).issubset(pool_names), added)
        self.assertEqual(result["remaining_hard"], 0, result["stopped_reason"])
        self.assertEqual(result["stopped_reason"], "clean")

    def test_action_codes_are_explicit(self):
        result = review_plan(self.CONTEXT, self._gap_plan(), self.EXPECTATIONS, pool=full_pool())
        codes = [action["code"] for action in result["actions"]]
        self.assertIn("add_meal", codes)
        self.assertIn("fill_day", codes)
        self.assertIn("add_lodging", codes)

    def test_without_pool_nothing_is_invented(self):
        result = review_plan(self.CONTEXT, self._gap_plan(), self.EXPECTATIONS)
        self.assertEqual([n["name"] for n in result["plan"]["route"]], ["某某景点"])
        self.assertEqual(result["stopped_reason"], "needs_data")
        self.assertIn("needs_candidates", result["needs_data"])

    def test_candidates_are_never_reused(self):
        pool = full_pool()
        result = review_plan(self.CONTEXT, self._gap_plan(), self.EXPECTATIONS, pool=pool)
        names = [n["name"] for n in result["plan"]["route"]]
        self.assertEqual(len(names), len(set(names)))

    def test_added_nodes_keep_honest_sources(self):
        result = review_plan(self.CONTEXT, self._gap_plan(), self.EXPECTATIONS, pool=full_pool())
        meal = next(n for n in result["plan"]["route"] if n["name"] == "小吃候选")
        self.assertEqual(meal["cost_estimate"], "¥30")
        self.assertFalse(meal["estimated"])
        self.assertEqual(meal["data_sources"]["cost_estimate"], "amap")
        self.assertEqual(meal["type"], "餐饮")  # 中文类别，便于前端展示与"必去项"匹配

    def test_candidate_without_price_is_marked_unverified(self):
        pool = {"food": [candidate("没有报价的小吃", None, "food")], "hotel": [candidate("酒店候选", 300, "hotel", "00:00-23:59")]}
        result = review_plan({"days": 1, "budget": 500}, {"route": [node(1, "景点A", "文化")]}, {"min_nodes_per_day": 2}, pool=pool)
        added = next(n for n in result["plan"]["route"] if n["name"] == "没有报价的小吃")
        self.assertEqual(added["cost_estimate"], "暂无供应商数据")
        self.assertTrue(added["estimated"])
        self.assertEqual(added["data_sources"]["cost_estimate"], "unavailable")

    def test_exhausted_pool_is_reported_as_such(self):
        """两个白天要两顿饭，但池子里只有一顿 → 如实说"候选池里没有更多可用的"。"""
        pool = {"food": [candidate("唯一小吃", 30, "food")], "cultural": [candidate("博物馆候选", 50)],
                "hotel": [candidate("酒店候选", 300, "hotel", "00:00-23:59")]}
        result = review_plan(self.CONTEXT, self._gap_plan(), self.EXPECTATIONS, pool=pool)
        self.assertEqual(result["stopped_reason"], "candidates_exhausted")
        self.assertGreater(result["remaining_hard"], 0)

    def test_must_have_is_matched_from_the_pool(self):
        pool = full_pool()
        expectations = {"min_nodes_per_day": 2, "must_have": ["博物馆"]}
        result = review_plan({"days": 1, "budget": 1000}, {"route": [node(1, "某某景点", "文化")]}, expectations, pool=pool)
        codes = [action["code"] for action in result["actions"]]
        self.assertIn("add_must_have", codes)
        self.assertIn("博物馆候选", [n["name"] for n in result["plan"]["route"]])

    def test_hotel_candidate_prefers_evening_open_window(self):
        """20:00 入住，就不要挑一个写着 18:00 关门的候选。"""
        pool = {
            "hotel": [candidate("早关门酒店", 200, "hotel", "08:00-18:00"), candidate("通宵酒店", 300, "hotel", "00:00-23:59")],
            "food": [candidate("小吃候选", 30, "food")],
            "cultural": [candidate("博物馆候选", 50)],
        }
        plan = {"route": [node(1, "景点A", "文化", "10:00"), node(2, "景点B", "文化", "10:00")]}
        result = review_plan({"days": 2, "budget": 1000}, plan, {"min_nodes_per_day": 1}, pool=pool)
        names = [n["name"] for n in result["plan"]["route"]]
        self.assertIn("通宵酒店", names)
        self.assertNotIn("早关门酒店", names)

    def test_input_plan_is_not_mutated_by_pool_repair(self):
        plan = self._gap_plan()
        snapshot = copy.deepcopy(plan)
        review_plan(self.CONTEXT, plan, self.EXPECTATIONS, pool=full_pool())
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


class TestCompositionDrivenRepair(unittest.TestCase):
    """自动补点要按**构成策略**走：自然型目的地缺自然景观就补自然景观，文化类超上限就换掉。"""

    CONTEXT = {"days": 4, "request_text": "去库尔勒看自然风景", "city": "库尔勒"}
    EXPECTATIONS = {"min_nodes_per_day": 2, "max_nodes_per_day": 4}

    def _pool(self):
        def scenic(name):
            return {
                "name": name, "type": "scenic", "intent": "scenic", "price": 50, "price_source": "amap",
                "rating": "4.6", "open_time": "00:00-23:59", "estimated": False,
                "lnglat": [86.1, 41.7], "source": "amap",
            }

        return {
            "scenic": [scenic(name) for name in ("博斯腾湖", "龙山公园", "天鹅湖", "孔雀公园", "罗布人村寨", "胡杨林")],
            "food": [scenic("小吃")],
            "hotel": [{**scenic("酒店"), "type": "hotel", "intent": "hotel", "price": 300}],
        }

    def _museum_only_plan(self):
        route = [node(day, f"博物馆{day}", "文化") for day in range(1, 5)]
        route += [node(day, f"餐馆{day}", "餐饮", "12:30") for day in range(1, 5)]
        return {"route": route}

    def test_nature_destination_gets_scenic_nodes_added(self):
        result = review_plan(self.CONTEXT, self._museum_only_plan(), self.EXPECTATIONS, pool=self._pool())
        names = [item["name"] for item in result["plan"]["route"]]
        self.assertTrue(any(name in names for name in ("博斯腾湖", "龙山公园", "天鹅湖")), names)
        codes = [action["code"] for action in result["actions"]]
        self.assertIn("add_scenic", codes)

    def test_culture_over_cap_triggers_rebalance(self):
        result = review_plan(self.CONTEXT, self._museum_only_plan(), self.EXPECTATIONS, pool=self._pool())
        rebalances = [action for action in result["actions"] if action["code"] == "rebalance_category"]
        self.assertTrue(rebalances, "文化类超上限时应换掉一个（换成策略指定的自然景观）")
        self.assertTrue(rebalances[0]["from"].startswith("博物馆"))
        # 换进来的必须是自然景观
        self.assertEqual(rebalances[0]["intent"], "scenic")

    def test_without_pool_nothing_is_added(self):
        result = review_plan(self.CONTEXT, self._museum_only_plan(), self.EXPECTATIONS)
        self.assertEqual([action for action in result["actions"] if action["code"] == "add_scenic"], [])


if __name__ == "__main__":
    unittest.main()
