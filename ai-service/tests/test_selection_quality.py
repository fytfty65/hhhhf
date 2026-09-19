"""选点质量：出现在行程里的必须是**真景点**，而且不能一类刷到底。

用户实测事故（2026-09-19，库尔勒 7 天）把这条钉得很死：
  · 「库尔勒民俗文化博物馆-西北门地上停车场」「巴州博物馆文创空间」「库尔勒园林宾馆」
    都被当成景点排进了行程 —— 关键词检索只看名字，不看它到底是什么；
  · 7 天几乎全是博物馆，一个自然风景都没有（去新疆却看不到山水草原）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_quality import check_hard_constraints  # noqa: E402
from core.poi_pool import candidate_from_poi, is_play_worthy  # noqa: E402


class TestPlayWorthy(unittest.TestCase):
    def test_facilities_and_shops_are_not_playworthy(self):
        for name, amap_type in [
            ("库尔勒民俗文化博物馆-西北门地上停车场", "交通设施服务;停车场;停车场"),
            ("巴州博物馆文创空间", "购物服务;专卖店;专卖店"),
            ("库尔勒园林宾馆", "住宿服务;宾馆酒店;宾馆酒店"),
            ("库尔勒火车站", "交通设施服务;火车站;火车站"),
            ("孔雀河景区售票处", "风景名胜;风景名胜相关;旅游景点"),
        ]:
            self.assertFalse(is_play_worthy(name, amap_type), f"{name} 不该被当成景点")

    def test_real_attractions_stay(self):
        for name, amap_type in [
            ("九号仓巴州博物馆", "科教文化服务;博物馆;博物馆"),
            ("博斯腾湖", "风景名胜;风景名胜;湖泊"),
            ("罗布人村寨", "风景名胜;风景名胜相关;旅游景点"),
            ("孔雀河景观带", "风景名胜;公园广场;公园"),
        ]:
            self.assertTrue(is_play_worthy(name, amap_type), f"{name} 是正经景点，不该被丢")

    def test_our_own_short_labels_are_never_killed(self):
        # 行程节点里的 type 常是我们自己的短标签，不能拿高德分类规则去卡
        self.assertTrue(is_play_worthy("景点A", "文化"))
        self.assertTrue(is_play_worthy("回民街小吃", "餐饮"))
        self.assertTrue(is_play_worthy("某个没说类别的地方", None))

    def test_commercial_intents_keep_their_venues(self):
        # 夜市/步行街本来就发生在商业场所：只按名字挡设施，不按高德类别挡
        self.assertTrue(is_play_worthy("洛阳十字街小吃一条街", "购物服务;商场;购物中心", strict_type=False))
        self.assertFalse(is_play_worthy("某某夜市停车场", "交通设施服务;停车场;停车场", strict_type=False))

    def test_candidate_from_poi_drops_facility_for_play_intent(self):
        poi = {"name": "博物馆-西北门地上停车场", "type": "交通设施服务;停车场;停车场", "location": "86.1,41.7"}
        self.assertIsNone(candidate_from_poi(poi, intent="cultural"))
        # 但作为"购物"类候选、且名字本身不是设施时仍然保留（strict_type=False 那条通道）
        mall = {"name": "某某商场", "type": "购物服务;商场;购物中心", "location": "86.1,41.7"}
        self.assertIsNotNone(candidate_from_poi(mall, intent="shopping"))


def play(day, name, kind="文化"):
    return {"day": day, "name": name, "location": name, "time": "10:00", "type": kind, "cost_estimate": "¥50"}


def stay(day, name="酒店H"):
    return {"day": day, "name": name, "location": name, "time": "20:00", "type": "住宿", "cost_estimate": "¥300"}


def meal(day, name="餐馆B"):
    return {"day": day, "name": name, "location": name, "time": "12:30", "type": "餐饮", "cost_estimate": "¥60"}


class TestSelectionGates(unittest.TestCase):
    def _codes(self, context, route, expectations=None):
        result = check_hard_constraints(context, {"route": route}, expectations or {})
        return [item["code"] for item in result["failures"]]

    def test_parking_lot_as_attraction_is_a_hard_failure(self):
        route = [play(1, "库尔勒民俗文化博物馆-西北门地上停车场"), meal(1), stay(1)]
        self.assertIn("facility_as_attraction", self._codes({"days": 1}, route))

    def test_monotony_fires_on_long_museum_only_trips(self):
        route = []
        for day in range(1, 8):
            route.extend([play(day, f"博物馆{day}"), play(day, f"博物馆{day}B"), meal(day), stay(day)])
        codes = self._codes({"days": 7}, route)
        self.assertIn("play_category_monotony", codes)

    def test_monotony_does_not_fire_on_short_trips(self):
        route = [play(1, "博物馆1"), play(1, "博物馆1B"), meal(1), stay(1), play(2, "博物馆2"), meal(2)]
        self.assertNotIn("play_category_monotony", self._codes({"days": 2}, route))

    def test_nature_request_without_any_scenic_is_a_hard_failure(self):
        route = []
        for day in range(1, 4):
            route.extend([play(day, f"博物馆{day}"), meal(day), stay(day)])
        context = {"days": 3, "request_text": "去库尔勒玩3天，想看自然风景和草原"}
        self.assertIn("scenic_missing", self._codes(context, route))
        # 排了自然风景就不报
        route[0] = play(1, "博斯腾湖", "自然风光")
        self.assertNotIn("scenic_missing", self._codes(context, route))


if __name__ == "__main__":
    unittest.main()
