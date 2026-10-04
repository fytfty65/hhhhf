"""行程配比口径测试（用户 2026-10-04 明确的口味）。

三条口径逐个钉死：
  1. 默认：人文与自然占比相同，且**必须**穿插特色美食与住宿；
  2. 自然景观 = 景区（湖区/雪山/草原/沙漠/峡谷/国家级景点/湿地森林）；城市公园不算，
     但"景色很好的公园"（湿地公园等）算；
  3. 用户明确说了偏好就加权（想吃美食 → 美食权重上升）。

用例里的高德类别字符串都是真实探针抓到的原文（库尔勒 / 郑州），不是编的。
"""

import unittest

from core.route_composition import (
    composition_audit,
    composition_targets,
    count_route_composition,
    scenic_tier,
    summarize_targets,
)

# 真实 AMap 类别（tools/probe_poi_queries.py 与真实推演的候选池里抄下来的）
REAL_AMAP = [
    ("孔雀公园", "风景名胜;公园广场;公园"),
    ("库尔勒市湿地公园", "风景名胜;公园广场;公园"),
    ("龙山公园", "风景名胜;公园广场;公园"),
    ("龙山动物园", "风景名胜;公园广场;动物园"),
    ("龙山公园玻璃吊桥", "风景名胜;风景名胜相关;旅游景点"),
    ("天鹅河景区", "风景名胜;风景名胜;国家级景点"),
    ("孔雀河景观带", "风景名胜;风景名胜相关;旅游景点"),
    ("普惠农场胡杨林景区", "风景名胜;风景名胜;风景名胜"),
    ("博斯腾湖", "风景名胜;风景名胜;国家级景点"),
    ("巴音郭楞蒙古自治州博物馆", "科教文化服务;博物馆;博物馆"),
    ("恒友广场步行街", "购物服务;购物相关场所;购物相关场所"),
]


class ScenicTierTests(unittest.TestCase):
    def test_city_parks_are_not_natural_landmarks(self):
        """这次最要紧的一条：城市公园不算自然景观（实测一次方案 5 个都是城市公园）。"""
        for name, poi_type in REAL_AMAP:
            if name in ("孔雀公园", "龙山公园", "龙山动物园", "龙山公园玻璃吊桥"):
                self.assertEqual(scenic_tier(name, poi_type), "city_park", f"{name} 应当是城市公园档")

    def test_good_looking_parks_count_as_landmarks(self):
        """用户原话："景色很好的公园可以" —— 湿地公园靠自然词进 landmark。"""
        self.assertEqual(scenic_tier("库尔勒市湿地公园", "风景名胜;公园广场;公园"), "landmark")

    def test_real_scenic_areas_are_landmarks(self):
        for name, poi_type in REAL_AMAP:
            if name in ("天鹅河景区", "孔雀河景观带", "普惠农场胡杨林景区", "博斯腾湖"):
                self.assertEqual(scenic_tier(name, poi_type), "landmark", f"{name} 应当算自然景观")

    def test_scenic_area_without_natural_word_still_counts(self):
        """铁门关这类没有自然词的景区：靠"风景名胜/国家级景点"进 landmark。"""
        self.assertEqual(scenic_tier("铁门关", "风景名胜;风景名胜;国家级景点"), "landmark")
        self.assertEqual(scenic_tier("罗布人村寨", "风景名胜;风景名胜;旅游景点"), "landmark")

    def test_museums_and_streets_are_neither(self):
        self.assertEqual(scenic_tier("巴音郭楞蒙古自治州博物馆", "科教文化服务;博物馆;博物馆"), "")
        self.assertEqual(scenic_tier("恒友广场步行街", "购物服务;购物相关场所;购物相关场所"), "")

    def test_empty_input_is_empty_not_a_guess(self):
        self.assertEqual(scenic_tier("", ""), "")
        self.assertEqual(scenic_tier(None, None), "")


class CompositionTargetTests(unittest.TestCase):
    def test_default_is_half_scenic_half_cultural_plus_food_and_hotel(self):
        targets = composition_targets(trip_days=2, daily_nodes=5)
        self.assertEqual(targets["source"], "default_balanced")
        self.assertEqual(targets["scenic"], targets["cultural"], "默认自然与人文占比相同")
        self.assertEqual(targets["scenic"] + targets["cultural"] + targets["food"], targets["daytime"])
        self.assertGreaterEqual(targets["food"], 2, "2 天行程至少穿插 2 顿特色美食")
        self.assertEqual(targets["hotel"], 1, "2 天住 1 晚")

    def test_food_preference_raises_food_weight(self):
        base = composition_targets(trip_days=2, daily_nodes=5)
        food = composition_targets(trip_days=2, daily_nodes=5, wants_food=True)
        self.assertEqual(food["source"], "explicit_preference")
        self.assertGreater(food["food"], base["food"], "想吃更多美食 → 美食名额必须增加")
        self.assertGreater(food["food"] / food["daytime"], 0.3)

    def test_scenic_preference_raises_scenic_and_lowers_cultural(self):
        targets = composition_targets(trip_days=2, daily_nodes=5, wants_scenic=True)
        self.assertGreater(targets["scenic"], targets["cultural"])
        self.assertGreaterEqual(targets["food"], 2, "想看自然也不该把吃饭挤掉")

    def test_cultural_preference_is_the_mirror(self):
        targets = composition_targets(trip_days=2, daily_nodes=5, wants_cultural=True)
        self.assertGreater(targets["cultural"], targets["scenic"])

    def test_excluding_cultural_removes_it_entirely(self):
        targets = composition_targets(trip_days=3, daily_nodes=5, exclude_cultural=True)
        self.assertEqual(targets["cultural"], 0)
        self.assertGreater(targets["scenic"], 0)
        self.assertGreater(targets["food"], 0, "排除人文之后吃饭仍然要在")

    def test_longer_trip_scales_food_and_hotel(self):
        targets = composition_targets(trip_days=5, daily_nodes=4)
        self.assertGreaterEqual(targets["food"], 5, "5 天至少 5 顿在外面吃")
        self.assertEqual(targets["hotel"], 4, "5 天住 4 晚")
        self.assertEqual(targets["daytime"], 20)

    def test_short_trip_still_has_everything(self):
        targets = composition_targets(trip_days=1, daily_nodes=3)
        self.assertGreaterEqual(targets["hotel"], 1, "1 天也至少给 1 个住宿候选")
        self.assertGreaterEqual(targets["food"], 1)
        self.assertEqual(targets["scenic"] + targets["cultural"] + targets["food"], targets["daytime"])

    def test_summary_says_why_the_mix_is_what_it_is(self):
        self.assertIn("默认自然与人文各半", summarize_targets(composition_targets(trip_days=2, daily_nodes=5)))
        self.assertIn("明确说的偏好", summarize_targets(composition_targets(trip_days=2, daily_nodes=5, wants_food=True)))


class CompositionAuditTests(unittest.TestCase):
    def test_missing_whole_category_is_reported(self):
        targets = composition_targets(trip_days=2, daily_nodes=5)
        audit = composition_audit({"scenic": 0, "cultural": 5, "food": 2, "hotel": 1}, targets)
        self.assertFalse(audit["ok"])
        self.assertTrue(any(item["intent"] == "scenic" and item["severity"] == "missing" for item in audit["deviations"]))
        self.assertTrue(any("自然景区" in note for note in audit["notes"]))

    def test_small_deviation_within_tolerance_is_ok(self):
        targets = composition_targets(trip_days=2, daily_nodes=5)
        audit = composition_audit({"scenic": 3, "cultural": 4, "food": 2, "hotel": 1}, targets)
        self.assertTrue(audit["ok"], audit["notes"])

    def test_over_weighted_category_is_reported(self):
        targets = composition_targets(trip_days=2, daily_nodes=5)
        audit = composition_audit({"scenic": 3, "cultural": 6, "food": 1, "hotel": 1}, targets)
        self.assertFalse(audit["ok"])
        self.assertTrue(any(item["severity"] == "over" for item in audit["deviations"]))

    def test_zero_target_category_is_ignored(self):
        targets = composition_targets(trip_days=2, daily_nodes=5, exclude_cultural=True)
        audit = composition_audit({"scenic": 6, "cultural": 0, "food": 4, "hotel": 1}, targets)
        self.assertNotIn("cultural", [item["intent"] for item in audit["deviations"]])


class CountRouteTests(unittest.TestCase):
    def test_counts_route_and_separates_landmarks_from_city_parks(self):
        route = [
            {"day": 1, "name": "孔雀公园", "type": "风景名胜;公园广场;公园"},
            {"day": 1, "name": "库尔勒市湿地公园", "type": "风景名胜;公园广场;公园"},
            {"day": 1, "name": "巴音郭楞蒙古自治州博物馆", "type": "科教文化服务;博物馆;博物馆"},
            {"day": 1, "name": "托克逊老四号拌面千城旗舰店", "type": "餐饮服务;中餐厅;中餐厅"},
            {"day": 1, "name": "金润假日酒店(库尔勒新汇嘉店)", "type": "住宿服务;宾馆酒店;宾馆酒店"},
        ]

        def classify(name, poi_type, is_hotel=False):
            if is_hotel:
                return "hotel"
            if "公园" in poi_type:
                return "scenic"
            if "博物馆" in poi_type:
                return "cultural"
            if "餐饮" in poi_type:
                return "food"
            return "other"

        counts = count_route_composition(route, classify, scenic_tier_of=scenic_tier)
        self.assertEqual(counts["scenic"], 1, "scenic 只数真自然景观（湿地公园）")
        self.assertEqual(counts["landmark"], 1, "只有湿地公园算自然景观")
        self.assertEqual(counts["city_park"], 1, "孔雀公园算城市公园，且不该混进 scenic")
        self.assertEqual(counts["cultural"], 1)
        self.assertEqual(counts["food"], 1)
        self.assertEqual(counts["hotel"], 1)

    def test_city_parks_do_not_inflate_the_natural_count(self):
        """真实事故：一次 12 节点方案里有 5 个城市公园，看起来"自然很多"，实际 0 个景区。"""
        route = [
            {"day": 1, "name": "孔雀公园", "type": "风景名胜;公园广场;公园"},
            {"day": 1, "name": "龙山公园", "type": "风景名胜;公园广场;公园"},
            {"day": 2, "name": "劳动公园", "type": "风景名胜;公园广场;公园"},
        ]
        counts = count_route_composition(route, lambda name, poi_type, is_hotel=False: "scenic", scenic_tier_of=scenic_tier)
        self.assertEqual(counts["scenic"], 0, "城市公园不能算自然景观")
        self.assertEqual(counts["city_park"], 3)


class CompositionDegradationTests(unittest.TestCase):
    """配比不一致必须出现在「遗留问题」里（界面直接读它）。"""

    def test_off_target_composition_is_registered(self):
        from core.degradation import collect_degradations

        targets = composition_targets(trip_days=2, daily_nodes=5)
        actual_counts = {"scenic": 0, "cultural": 5, "food": 2, "hotel": 1, "landmark": 0, "city_park": 0}
        payload = {
            "composition": {
                **targets,
                "actual": actual_counts,
                "audit": composition_audit(actual_counts, targets),
            }
        }
        items = collect_degradations(payload).get("items", [])
        entry = next((item for item in items if item["source"] == "route_composition"), None)
        self.assertIsNotNone(entry, "配比偏了却没登记")
        self.assertIn("自然景区", str(entry.get("reason")))

    def test_on_target_composition_adds_nothing(self):
        from core.degradation import collect_degradations

        targets = composition_targets(trip_days=2, daily_nodes=5)
        actual = {"scenic": targets["scenic"], "cultural": targets["cultural"], "food": targets["food"], "hotel": targets["hotel"]}
        payload = {"composition": {**targets, "actual": actual, "audit": composition_audit(actual, targets)}}
        items = collect_degradations(payload).get("items", [])
        self.assertFalse([item for item in items if item["source"] == "route_composition"])


class StationNameTests(unittest.TestCase):
    """真实瑕疵：高德把「湿地公园站」标成"风景名胜;旅游景点"，于是被当景点排进行程。"""

    def test_bus_or_metro_station_suffix_is_not_play_worthy(self):
        from core.poi_pool import is_play_worthy

        self.assertFalse(is_play_worthy("湿地公园站", "风景名胜;风景名胜相关;旅游景点"), "公交站不是景点")
        self.assertFalse(is_play_worthy("孔雀公园站", "风景名胜;风景名胜相关;旅游景点"))
        self.assertFalse(is_play_worthy("市民中心地铁站", "风景名胜;旅游景点"))

    def test_real_places_ending_with_zhan_are_kept(self):
        from core.poi_pool import is_play_worthy

        self.assertTrue(is_play_worthy("龙门驿站", "风景名胜;风景名胜;旅游景点"), "驿站是真实场所，别误杀")
        self.assertTrue(is_play_worthy("库尔勒市湿地公园", "风景名胜;公园广场;公园"))


if __name__ == "__main__":
    unittest.main()
