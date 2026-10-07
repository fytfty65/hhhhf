"""「用户点名换成哪一家」的测试（2026-10-05）。

场景：后端把"有实价的住宿可选"摆出来了，用户点了其中一家的「换住这家」——
必须真的换成**那一家**（而不是"随便换个同类"），并且不能被后面的自动优化悄悄改掉。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.increment import apply_swap_to, parse_increment  # noqa: E402


class ParseSwapToTests(unittest.TestCase):
    def test_named_swap_is_parsed(self):
        delta = parse_increment("住宿换成 库尔勒福润德·万达锦华酒店", known_names=["库尔勒福润德·万达锦华酒店"])
        self.assertEqual(delta["swap_to"], "库尔勒福润德·万达锦华酒店")
        self.assertEqual(delta["swap_kind"], "hotel")
        self.assertFalse(delta["is_noop"])

    def test_named_swap_is_not_read_as_an_exclusion(self):
        """「换成 X」里的"换成"跟"换成别的"语感相同 —— 先判排他就会把用户**想要**的那家排掉。"""
        delta = parse_increment("把住宿换成 库尔勒福润德·万达锦华酒店", known_names=["库尔勒福润德·万达锦华酒店"])
        self.assertEqual(delta["swap_to"], "库尔勒福润德·万达锦华酒店")
        self.assertEqual(delta["exclude"], [], delta)

    def test_plain_exclusion_still_works(self):
        delta = parse_increment("不想去喀什博物馆了", previous_plan={"route": [{"name": "喀什博物馆"}]})
        self.assertEqual(delta["exclude"], ["喀什博物馆"])
        self.assertIsNone(delta["swap_to"])

    def test_no_name_mentioned_means_no_swap(self):
        delta = parse_increment("住宿换一家", known_names=["库尔勒福润德·万达锦华酒店"])
        self.assertIsNone(delta["swap_to"])

    def test_unknown_name_is_not_a_swap(self):
        """候选池里没有的名字不能被当成"要换的那一家"（否则会去换一个不存在的地方）。"""
        delta = parse_increment("换成火星酒店", known_names=["库尔勒福润德·万达锦华酒店"])
        self.assertIsNone(delta["swap_to"])

    def test_category_word_alone_is_not_a_named_swap(self):
        """只说"换成一家酒店"不算点名（类别词不足以定位是哪一家）。"""
        delta = parse_increment("住宿换成一家酒店", known_names=["库尔勒福润德·万达锦华酒店", "某民宿"])
        self.assertIsNone(delta["swap_to"])

    def test_core_name_without_the_category_suffix_still_matches(self):
        """用户说"换成瑞德假日"（去掉「酒店」二字）也算点名。"""
        delta = parse_increment("换成瑞德假日", known_names=["瑞德假日酒店(巴州博物馆市政府店)"])
        self.assertEqual(delta["swap_to"], "瑞德假日酒店(巴州博物馆市政府店)")

    def test_city_prefix_does_not_match_every_pool_name(self):
        """城市名不能当成"点名"——池子里的名字多半以城市开头，实测把"住宿换成 X 酒店"
        读成了"把**库尔勒民俗文化博物馆**当成想换的那一家"。"""
        delta = parse_increment(
            "去库尔勒玩2天，帮我安排一下就行（住宿换成 库尔勒福润德·万达锦华酒店）",
            known_names=["库尔勒民俗文化博物馆", "库尔勒福润德·万达锦华酒店", "库尔勒园林宾馆"],
            city="库尔勒",
        )
        self.assertEqual(delta["swap_to"], "库尔勒福润德·万达锦华酒店")

    def test_city_prefixed_hotel_still_matches_by_full_name(self):
        delta = parse_increment(
            "住宿换成维也纳国际酒店(库尔勒天鹅河店)",
            known_names=["维也纳国际酒店(库尔勒天鹅河店)"],
            city="库尔勒",
        )
        self.assertEqual(delta["swap_to"], "维也纳国际酒店(库尔勒天鹅河店)")


class ApplySwapToTests(unittest.TestCase):
    def _plan(self):
        return {
            "route": [
                {"day": 1, "name": "某景区", "lnglat": [75.99, 39.47], "type": "风景名胜", "time": "09:00"},
                {"day": 1, "name": "老酒店", "lnglat": [75.98, 39.46], "type": "住宿服务", "time": "20:00", "is_hotel": True, "cost_estimate": "暂无供应商数据"},
            ]
        }

    def _pool(self):
        return [
            {
                "name": "新酒店",
                "lnglat": [75.90, 39.50],
                "type": "住宿服务",
                "price": 265,
                "price_source": "rollinggo",
                "is_hotel": True,
            }
        ]

    def test_named_hotel_is_swapped_in_place(self):
        result = apply_swap_to(self._plan(), "新酒店", pool=self._pool(), kind="hotel")
        action = result["action"]
        self.assertIsNotNone(action)
        self.assertEqual(action["from"], "老酒店")
        self.assertEqual(action["to"], "新酒店")
        self.assertEqual(action["day"], 1)
        route = result["plan"]["route"]
        self.assertEqual([node["name"] for node in route], ["某景区", "新酒店"])
        self.assertEqual(route[1]["time"], "20:00", "原地换：时段不变")
        self.assertEqual(route[1]["lnglat"], [75.90, 39.50])
        self.assertEqual(route[1]["cost_estimate"], "¥265")
        self.assertEqual(route[1]["data_sources"]["cost_estimate"], "rollinggo")
        self.assertFalse(route[1]["estimated"], "可核实来源不该再标估算")
        self.assertTrue(route[1]["user_choice"], "用户点名的选择要打标：后面的自动优化不许改掉它")

    def test_unknown_name_is_reported_not_faked(self):
        result = apply_swap_to(self._plan(), "不存在酒店", pool=self._pool(), kind="hotel")
        self.assertIsNone(result["action"])
        self.assertIn("候选池里没有", str(result.get("reason")))

    def test_swapping_to_the_same_hotel_is_a_noop(self):
        result = apply_swap_to(self._plan(), "老酒店", pool=self._pool(), kind="hotel")
        self.assertIsNone(result["action"])
        self.assertIn("已经是这一家", str(result.get("reason")))

    def test_original_plan_is_not_mutated(self):
        plan = self._plan()
        apply_swap_to(plan, "新酒店", pool=self._pool(), kind="hotel")
        self.assertEqual(plan["route"][1]["name"], "老酒店")

    def test_user_choice_is_not_removed_by_the_travel_repair(self):
        """他点名选的酒店，不能因为"离市中心 40km 以上"被自动移出。"""
        from core.travel_feasibility import repair_day_overload

        route = [
            {"day": 1, "name": "市区点", "lnglat": [86.17, 41.72]},
            {"day": 1, "name": "他选的远酒店", "lnglat": [84.15, 43.03], "user_choice": True, "is_hotel": True},
            {"day": 1, "name": "另一个远景区", "lnglat": [85.00, 42.50]},
        ]
        kept, removed = repair_day_overload(route, city_center=[86.174633, 41.725891], distance_tolerance="high")
        kept_names = [node["name"] for node in kept]
        self.assertIn("他选的远酒店", kept_names, "用户点名的不能被自动移出")
        self.assertNotIn("他选的远酒店", [node["name"] for node in removed])

    def test_node_swap_is_disclosed(self):
        from core.degradation import collect_degradations

        items = collect_degradations(
            {"node_swap": {"actions": [{"day": 1, "from": "老酒店", "to": "新酒店", "kind": "hotel"}]}}
        ).get("items", [])
        entry = next((item for item in items if item.get("source") == "node_swap"), None)
        self.assertIsNotNone(entry, items)
        self.assertIn("老酒店 → 新酒店", str(entry.get("reason")))
        self.assertIn("你点名要的那一家", str(entry.get("reason")))

    def test_failed_swap_is_disclosed_too(self):
        from core.degradation import collect_degradations

        items = collect_degradations({"node_swap": {"actions": [], "error": "候选池里没有「火星酒店」"}}).get("items", [])
        entry = next((item for item in items if item.get("source") == "node_swap"), None)
        self.assertIsNotNone(entry, items)
        self.assertIn("没能换上", str(entry.get("reason")))


if __name__ == "__main__":
    unittest.main()
