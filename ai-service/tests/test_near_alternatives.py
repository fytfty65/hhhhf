"""「超预算的那天，可以换成哪个近点」的测试（2026-10-05）。

用户口径：远的一天"可以接受，但是需要标明"；真去掉得等用户说一句。
所以这一层**只提议、不改方案** —— 但提议必须是**可执行的**：
同类、离市中心更近、离当天其它点更近，而且不能拿"已经在方案里的点"或"另一个远点"来糊弄。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.travel_feasibility import suggest_near_alternatives  # noqa: E402

CENTER = [75.99, 39.47]  # 喀什


def node(name, day, lnglat, kind="culture", tier=""):
    return {"day": day, "name": name, "lnglat": list(lnglat), "type": kind, "category": kind, "tier": tier}


def category_of(item):
    return str(item.get("category") or "")


def tier_of(item):
    return str(item.get("tier") or "")


class SuggestNearAlternativesTests(unittest.TestCase):
    def _route(self):
        return [
            node("喀什博物馆", 2, [75.98, 39.46], "culture"),
            node("九龙泉湿地公园", 2, [75.995, 39.475], "nature"),
            # 221km 外的远点：这就是"447km 那天"的元凶
            node("胡杨书院", 2, [78.542, 39.791], "culture"),
        ]

    def _candidates(self):
        return [
            node("近的文化点A", 0, [76.00, 39.47], "culture"),
            node("近的文化点B", 0, [76.05, 39.48], "culture"),
            node("近的自然点", 0, [76.01, 39.47], "nature"),
            node("另一个远点", 0, [78.60, 39.80], "culture"),
            node("喀什博物馆", 0, [75.98, 39.46], "culture"),  # 已经在方案里
        ]

    def test_over_budget_day_gets_a_same_category_near_option(self):
        result = suggest_near_alternatives(
            self._route(), self._candidates(), city_center=CENTER, distance_tolerance="normal", category_of=category_of
        )
        self.assertEqual(len(result["days"]), 1, result)
        day = result["days"][0]
        self.assertEqual(day["day"], 2)
        self.assertGreater(day["travel_km"], day["budget_km"])
        self.assertEqual(day["far_nodes"][0]["name"], "胡杨书院")
        option = day["options"][0]
        self.assertEqual(option["for_node"], "胡杨书院")
        self.assertEqual(option["category"], "culture")
        self.assertIn("近的文化点", option["name"])
        self.assertLess(option["center_km"], 40, "替代点必须离市中心更近")
        self.assertIn("替换「胡杨书院」", option["why"])

    def test_never_offers_a_far_point_or_an_existing_node(self):
        result = suggest_near_alternatives(
            self._route(), self._candidates(), city_center=CENTER, distance_tolerance="normal", category_of=category_of
        )
        names = [item["name"] for item in result["days"][0]["options"]]
        self.assertNotIn("另一个远点", names, "不能拿另一个远点来替代远点")
        self.assertNotIn("喀什博物馆", names, "已经在方案里的点不算替代")

    def test_does_not_offer_a_different_category(self):
        result = suggest_near_alternatives(
            self._route(),
            [node("近的自然点", 0, [76.01, 39.47], "nature")],
            city_center=CENTER,
            distance_tolerance="normal",
            category_of=category_of,
        )
        self.assertEqual(result["days"][0]["options"], [], "同类没有近点就如实说没有")

    def test_days_within_budget_are_not_touched(self):
        route = [node("喀什博物馆", 1, [75.98, 39.46], "culture"), node("九龙泉", 1, [75.995, 39.475], "nature")]
        result = suggest_near_alternatives(
            route, self._candidates(), city_center=CENTER, distance_tolerance="normal", category_of=category_of
        )
        self.assertEqual(result["days"], [])

    def test_high_tolerance_raises_the_budget(self):
        """说"不介意跑远"的人，超预算的门槛跟着放宽（同一份路线不再被判超）。"""
        route = [
            node("近点", 1, [75.99, 39.47], "culture"),
            # 约 257km（经度 3 度 @39.47°N）：normal 含容差 176km 会超，high 的 352km 不会
            node("257km 外的点", 1, [78.99, 39.47], "culture"),
        ]
        normal = suggest_near_alternatives(
            route, self._candidates(), city_center=CENTER, distance_tolerance="normal", category_of=category_of
        )
        high = suggest_near_alternatives(
            route, self._candidates(), city_center=CENTER, distance_tolerance="high", category_of=category_of
        )
        self.assertEqual(len(normal["days"]), 1, normal)
        self.assertEqual(high["days"], [], "320km 预算下这一天没超")

    def test_tier_is_not_downgraded(self):
        """拿"城市公园"顶替"真景区"正是用户明确反对的那件事（实测第一版就这么建议过）。

        同类（都 scenic）但档次不同（landmark vs city_park）时**不提议** ——
        宁可如实说"没有同档次的近点"。
        """
        route = [
            node("市区点", 2, [75.99, 39.47], "nature", tier="city_park"),
            node("湿地景区", 2, [78.99, 39.47], "nature", tier="landmark"),  # 约 257km 外
        ]
        candidates = [
            {**node("北湖公园", 0, [76.00, 39.47], "nature"), "tier": "city_park"},
            {**node("某近的真景区", 0, [76.02, 39.48], "nature"), "tier": "landmark"},
        ]
        result = suggest_near_alternatives(
            route,
            candidates,
            city_center=CENTER,
            distance_tolerance="normal",
            category_of=category_of,
            tier_of=tier_of,
        )
        names = [item["name"] for item in result["days"][0]["options"]]
        self.assertEqual(names, ["某近的真景区"], names)
        self.assertNotIn("北湖公园", names, "不能拿城市公园顶替真景区")

    def test_no_same_tier_candidate_means_no_option(self):
        route = [
            node("市区点", 2, [75.99, 39.47], "nature", tier="city_park"),
            node("湿地景区", 2, [78.99, 39.47], "nature", tier="landmark"),
        ]
        result = suggest_near_alternatives(
            route,
            [{**node("北湖公园", 0, [76.00, 39.47], "nature"), "tier": "city_park"}],
            city_center=CENTER,
            distance_tolerance="normal",
            category_of=category_of,
            tier_of=tier_of,
        )
        self.assertEqual(result["days"][0]["options"], [], "没有同档次的近点就如实说没有")

    def test_options_are_capped_and_days_are_reported_one_by_one(self):
        result = suggest_near_alternatives(
            self._route(),
            self._candidates(),
            city_center=CENTER,
            distance_tolerance="normal",
            category_of=category_of,
            limit=1,
        )
        self.assertEqual(len(result["days"][0]["options"]), 1)

    def test_missing_coordinates_are_ignored_not_guessed(self):
        route = [node("没坐标的远点", 2, [0, 0], "culture"), node("市区点", 2, [75.99, 39.47], "culture")]
        result = suggest_near_alternatives(
            route, self._candidates(), city_center=CENTER, distance_tolerance="normal", category_of=category_of
        )
        self.assertEqual(result["days"], [], "0,0 不算远点、也不算可替代对象")

    def test_without_a_category_function_distance_is_the_only_rule(self):
        result = suggest_near_alternatives(
            self._route(), self._candidates(), city_center=CENTER, distance_tolerance="normal"
        )
        self.assertTrue(result["days"][0]["options"], result)
        self.assertEqual(result["days"][0]["options"][0]["category"], "")


class ApplySubstitutionTests(unittest.TestCase):
    """用户说了"不想跑远"时是**执行**：就地换成算好的近点（同一天同一时段）。"""

    def _alternatives(self):
        return {
            "days": [
                {
                    "day": 2,
                    "options": [
                        {
                            "for_node": "胡杨书院",
                            "for_node_km": 221.4,
                            "name": "疏附县博物馆",
                            "nearest_day_km": 11.1,
                            "candidate": {
                                "name": "疏附县博物馆",
                                "lnglat": [75.86, 39.38],
                                "type": "科教文化服务",
                                "price": 0,
                                "price_source": "amap",
                            },
                        }
                    ],
                }
            ]
        }

    def _route(self):
        return [
            {"day": 2, "name": "喀什博物馆", "lnglat": [75.98, 39.46], "time": "10:00"},
            {"day": 2, "name": "胡杨书院", "lnglat": [78.542, 39.791], "time": "14:00"},
        ]

    def test_far_node_is_swapped_in_place(self):
        from core.travel_feasibility import apply_near_substitutions

        route, actions = apply_near_substitutions(self._route(), self._alternatives())
        self.assertEqual([node["name"] for node in route], ["喀什博物馆", "疏附县博物馆"])
        self.assertEqual(route[1]["time"], "14:00", "就地换：同一时段不该被打乱")
        self.assertEqual(route[1]["lnglat"], [75.86, 39.38])
        self.assertEqual(route[1]["replaced_from"], "胡杨书院")
        self.assertIn("不想跑", str(route[1].get("replaced_reason")))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["from"], "胡杨书院")
        self.assertEqual(actions[0]["to"], "疏附县博物馆")
        self.assertEqual(actions[0]["from_km"], 221.4)

    def test_price_and_source_follow_the_candidate(self):
        from core.travel_feasibility import apply_near_substitutions

        route, _ = apply_near_substitutions(self._route(), self._alternatives())
        self.assertEqual(route[1]["cost_estimate"], "¥0")
        self.assertEqual(route[1]["data_sources"]["cost_estimate"], "amap")

    def test_original_route_is_not_mutated(self):
        from core.travel_feasibility import apply_near_substitutions

        original = self._route()
        apply_near_substitutions(original, self._alternatives())
        self.assertEqual(original[1]["name"], "胡杨书院")

    def test_no_candidate_means_no_change(self):
        from core.travel_feasibility import apply_near_substitutions

        route, actions = apply_near_substitutions(
            self._route(), {"days": [{"day": 2, "options": [{"for_node": "胡杨书院", "name": "某近点"}]}]}
        )
        self.assertEqual(actions, [])
        self.assertEqual([node["name"] for node in route], ["喀什博物馆", "胡杨书院"], "没有完整候选就不动")

    def test_empty_alternatives_is_a_noop(self):
        from core.travel_feasibility import apply_near_substitutions

        route, actions = apply_near_substitutions(self._route(), {})
        self.assertEqual(actions, [])
        self.assertEqual(len(route), 2)

    def test_substitution_is_disclosed(self):
        from core.degradation import collect_degradations

        payload = {
            "travel_substitution": {
                "actions": [{"day": 2, "from": "胡杨书院", "to": "疏附县博物馆", "from_km": 221.4, "nearest_day_km": 11.1}],
                "reason": "你说过不想跑那么远",
            }
        }
        items = collect_degradations(payload).get("items", [])
        entry = next((item for item in items if item.get("source") == "travel_substitution"), None)
        self.assertIsNotNone(entry, items)
        self.assertIn("胡杨书院", str(entry.get("reason")))
        self.assertIn("疏附县博物馆", str(entry.get("reason")))
        self.assertIn("换回来", str(entry.get("reason")))


class SubstituteNearPreferenceTests(unittest.TestCase):
    """`substitute(..., prefer_near_to=…)`：用户说了"不想跑远"，替代点优先选近的。

    实测（2026-10-05）：原来的替代是按**档位/价格**排的，跟距离无关 ——
    于是"换掉一个远点"很可能换来**另一个远点**（法理上照着用户的话做了，实际没照做）。
    """

    def _index(self):
        from core.candidate_index import index_candidates

        # 注意：`index_candidates` 会按**价格**重算 `tier_level`，显式写 tier_level 是没用的 ——
        # 所以这里用价格拉开档位差（便宜的近点 vs 贵的远点）。
        return index_candidates(
            [
                {"name": "近的文化点", "lnglat": [75.99, 39.47], "type": "科教文化服务", "price": 50},
                {"name": "远的文化点", "lnglat": [78.99, 39.47], "type": "科教文化服务", "price": 900},
            ]
        )

    def _node(self):
        return {"day": 2, "name": "胡杨书院", "lnglat": [78.542, 39.791], "type": "科教文化服务"}

    def test_near_candidate_wins_when_the_user_asked_for_near(self):
        from core.candidate_index import substitute

        picked = substitute(
            self._node(), self._index(), prefer_near_to=[75.99, 39.47], max_near_km=40
        )
        self.assertEqual(picked["name"], "近的文化点")

    def test_without_the_preference_the_old_rule_still_applies(self):
        """没说要近的时候维持原口径（挑档位最高的），不偷偷改行为。"""
        from core.candidate_index import substitute

        picked = substitute(self._node(), self._index())
        self.assertEqual(picked["name"], "远的文化点", "默认仍按档位最高挑")

    def test_falls_back_to_the_pool_when_nothing_is_near(self):
        """近的一个都没有时退回原池（宁可换远点，也不假装"没有替代"）。"""
        from core.candidate_index import substitute

        picked = substitute(self._node(), self._index(), prefer_near_to=[100.0, 30.0], max_near_km=5)
        self.assertIsNotNone(picked, "不该因为「没有近的」就返回 None")

    def test_exclusions_replaced_by_a_near_point_and_disclosed(self):
        from core.candidate_index import apply_exclusions

        plan = {"route": [self._node()]}
        result = apply_exclusions(
            plan, ["胡杨书院"], index=self._index(), prefer_near_to=[75.99, 39.47], max_near_km=40
        )
        self.assertEqual([item["name"] for item in result["plan"]["route"]], ["近的文化点"])
        self.assertIn("不想跑远", result["disclosure"])
        self.assertEqual(result["replaced"][0]["to"], "近的文化点")
        self.assertIsNotNone(result["replaced"][0]["center_km"])
        self.assertIn("不想跑那么远", result["replaced"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
