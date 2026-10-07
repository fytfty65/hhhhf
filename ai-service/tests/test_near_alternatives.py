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
            # 约 171km（经度 2 度 @39.47°N）：normal 预算 160km 会超，high 的 320km 不会
            node("171km 外的点", 1, [77.99, 39.47], "culture"),
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
            node("湿地景区", 2, [77.99, 39.47], "nature", tier="landmark"),  # 约 171km 外
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
            node("湿地景区", 2, [77.99, 39.47], "nature", tier="landmark"),
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


if __name__ == "__main__":
    unittest.main()
