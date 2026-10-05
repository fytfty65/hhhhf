"""行程配比口径测试（用户 2026-10-04 明确的口味）。

三条口径逐个钉死：
  1. 默认：人文与自然占比相同，且**必须**穿插特色美食与住宿；
  2. 自然景观 = 景区（湖区/雪山/草原/沙漠/峡谷/国家级景点/湿地森林）；城市公园不算，
     但"景色很好的公园"（湿地公园等）算；
  3. 用户明确说了偏好就加权（想吃美食 → 美食权重上升）。

用例里的高德类别字符串都是真实探针抓到的原文（库尔勒 / 郑州），不是编的。
"""

import os
import unittest

# 城市公园限量/替换的测试要用**真实的** classify_poi_category（api.agent 导入时会构造 LLM 客户端，
# 缺 key 直接抛错，这是刻意的），所以先给个假 key 让导入跑通。
os.environ.setdefault("LLM_API_KEY", "test-key-not-used")

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


class ParkCapTests(unittest.TestCase):
    """城市公园限量（用户口径 2026-10-04：**默认 1~2 个**，喜欢可以再要）。"""

    def test_default_cap_is_one_or_two_for_the_whole_trip(self):
        from core.poi_pool import park_cap

        self.assertEqual(park_cap(1), 1)
        self.assertEqual(park_cap(2), 2)
        self.assertEqual(park_cap(4), 2, "默认是整趟 1~2 个，不随天数线性增长")
        self.assertEqual(park_cap(7), 2)

    def test_user_asking_for_parks_loosens_the_cap(self):
        """"如果用户喜欢后续再添加"：明确想逛公园/乐园时放宽到每天一个。"""
        from core.poi_pool import park_cap

        self.assertEqual(park_cap(3, wants_park=True), 3)
        self.assertEqual(park_cap(5, wants_park=True), 5)
        self.assertGreater(park_cap(4, wants_park=True), park_cap(4))

    def test_scenic_only_trip_allows_no_city_park(self):
        from core.poi_pool import park_cap

        self.assertEqual(park_cap(3, scenic_only=True), 0)
        self.assertEqual(park_cap(1, scenic_only=True), 0)
        self.assertEqual(park_cap(3, scenic_only=True, wants_park=True), 0, "只要自然时不因'想逛公园'破例")


class IntentDrivenCompositionTests(unittest.TestCase):
    """意图驱动的规划形态：用户说"不考虑距离、只想看自然" → 无人生、公园 0、跨景区。"""

    def test_only_nature_drops_cultural_entirely(self):
        targets = composition_targets(trip_days=3, daily_nodes=5, wants_scenic=True, exclude_cultural=True)
        self.assertEqual(targets["cultural"], 0)
        self.assertTrue(targets["exclude_cultural"])
        self.assertGreater(targets["scenic"], targets["food"], "景区为主、美食穿插")

    def test_forbidden_category_is_reported_even_when_target_is_zero(self):
        targets = composition_targets(trip_days=3, daily_nodes=5, wants_scenic=True, exclude_cultural=True)
        audit = composition_audit(
            {"scenic": 10, "cultural": 2, "food": 3, "hotel": 2, "landmark": 10, "city_park": 0},
            targets,
            forbidden=("cultural",),
        )
        self.assertFalse(audit["ok"])
        self.assertTrue(any(item["severity"] == "forbidden" for item in audit["deviations"]))
        self.assertTrue(any("不要人文" in note for note in audit["notes"]), audit["notes"])

    def test_no_forbidden_flag_means_no_complaint(self):
        targets = composition_targets(trip_days=3, daily_nodes=5, wants_scenic=True, exclude_cultural=True)
        audit = composition_audit({"scenic": 10, "cultural": 2, "food": 3, "hotel": 2}, targets)
        self.assertTrue(audit["ok"], audit["notes"])


class CompositionHintTests(unittest.TestCase):
    """目标配比写进提示词：要给出数字，但必须写清"不要为凑数硬塞"。"""

    def test_hint_carries_targets_and_the_no_padding_rule(self):
        from core.route_composition import composition_hint, composition_targets

        targets = composition_targets(trip_days=2, daily_nodes=5)
        text = composition_hint(targets, park_cap=2)
        self.assertIn(f"自然景区 {targets['scenic']}", text)
        self.assertIn(f"人文 {targets['cultural']}", text)
        self.assertIn("不要为了凑数量硬塞", text)
        self.assertIn("城市公园", text)
        self.assertIn("最多 2 个", text)
        self.assertIn("美食要**穿插到每一天**", text)

    def test_hint_states_user_preference_priority_and_exclusions(self):
        from core.route_composition import composition_hint, composition_targets

        targets = composition_targets(trip_days=3, daily_nodes=5, wants_scenic=True, exclude_cultural=True)
        text = composition_hint(targets, park_cap=0)
        self.assertIn("明确说过的偏好 > 这份配比", text)
        self.assertIn("明确排除的类别", text)

    def test_hint_without_park_cap_says_do_not_pad_with_parks(self):
        from core.route_composition import composition_hint, composition_targets

        text = composition_hint(composition_targets(trip_days=2, daily_nodes=5))
        self.assertIn("不要拿它充数", text)


class ParkTrimTests(unittest.TestCase):
    """公园限量落到最终路线：超出的换成真景区/美食，换不了就移出（用户口径默认 1~2 个）。"""

    def _classify(self):
        from api.agent import classify_poi_category

        return classify_poi_category

    def _route(self):
        return [
            {"day": 1, "name": "孔雀公园", "type": "风景名胜;公园广场;公园"},
            {"day": 1, "name": "库尔勒市湿地公园", "type": "风景名胜;公园广场;公园"},
            {"day": 2, "name": "劳动公园", "type": "风景名胜;公园广场;公园"},
            {"day": 2, "name": "巴音郭楞蒙古自治州博物馆", "type": "科教文化服务;博物馆;博物馆"},
        ]

    def _pool(self):
        return [
            {"name": "博斯腾湖风景名胜区", "type": "风景名胜;风景名胜;国家级景点", "lnglat": [86.76, 41.98]},
            {"name": "老四号拌面", "type": "餐饮服务;中餐厅", "lnglat": [86.2, 41.7]},
            {"name": "胡杨公园", "type": "风景名胜;公园广场;公园"},
        ]

    def test_extra_parks_are_replaced_with_real_scenery_first(self):
        from core.route_composition import trim_category_over_cap, scenic_tier

        route, actions = trim_category_over_cap(
            self._route(), cap=2, classify=self._classify(), scenic_tier_of=scenic_tier, pool=self._pool()
        )
        names = [item["name"] for item in route]
        # 湿地公园算真景区（自然词），不在公园计数里；城市公园是孔雀公园/劳动公园 → 上限 2 不用动
        self.assertEqual(len(actions), 0, f"上限 2 且城市公园只有 2 个，不该动：{actions}")

    def test_cap_one_replaces_the_extra_park_with_landmark(self):
        from core.route_composition import scenic_tier, trim_category_over_cap

        route, actions = trim_category_over_cap(
            self._route(), cap=1, classify=self._classify(), scenic_tier_of=scenic_tier, pool=self._pool()
        )
        names = [item["name"] for item in route]
        self.assertEqual(len(actions), 1, actions)
        self.assertIn(actions[0]["code"], {"park_over_cap_replaced", "park_over_cap_dropped"})
        if actions[0]["code"] == "park_over_cap_replaced":
            self.assertIn("博斯腾湖风景名胜区", names, "优先换成真景区")
            self.assertNotIn("劳动公园", names)

    def test_drops_when_no_replacement_available(self):
        from core.route_composition import scenic_tier, trim_category_over_cap

        route, actions = trim_category_over_cap(
            self._route(), cap=1, classify=self._classify(), scenic_tier_of=scenic_tier, pool=[]
        )
        names = [item["name"] for item in route]
        self.assertEqual(actions[0]["code"], "park_over_cap_dropped")
        self.assertEqual(len(names), 3, "移出一个公园后应剩 3 个节点")
        self.assertIn("巴音郭楞蒙古自治州博物馆", names, "非公园节点不能被碰")

    def test_scenic_only_cap_zero_removes_all_city_parks(self):
        from core.route_composition import scenic_tier, trim_category_over_cap

        route, actions = trim_category_over_cap(
            self._route(), cap=0, classify=self._classify(), scenic_tier_of=scenic_tier, pool=self._pool()
        )
        names = [item["name"] for item in route]
        self.assertNotIn("孔雀公园", names)
        self.assertNotIn("劳动公园", names)
        self.assertIn("库尔勒市湿地公园", names, "湿地公园是真景区，不在公园口径里")
        self.assertEqual(len(actions), 2)

    def test_never_replaces_a_park_with_another_park(self):
        from core.route_composition import scenic_tier, trim_category_over_cap

        pool = [{"name": "又一个公园", "type": "风景名胜;公园广场;公园"}]
        route, actions = trim_category_over_cap(
            self._route(), cap=1, classify=self._classify(), scenic_tier_of=scenic_tier, pool=pool
        )
        names = [item["name"] for item in route]
        self.assertNotIn("又一个公园", names, "不能拿公园换公园")
        self.assertEqual(actions[0]["code"], "park_over_cap_dropped")

    def test_shopping_street_is_not_used_as_a_park_replacement(self):
        """替换偏好是"景区 → 美食"：候选池里只有步行街时应当**移出**，而不是换成逛街（实测踩到）。"""
        from core.route_composition import scenic_tier, trim_category_over_cap

        pool = [{"name": "恒友广场步行街", "type": "购物服务;购物相关场所;购物相关场所"}]
        route, actions = trim_category_over_cap(
            self._route(), cap=1, classify=self._classify(), scenic_tier_of=scenic_tier, pool=pool
        )
        names = [item["name"] for item in route]
        self.assertNotIn("恒友广场步行街", names, "公园不该被换成购物街")
        self.assertEqual(actions[0]["code"], "park_over_cap_dropped", actions[0])

    def test_food_is_used_when_no_landmark_available(self):
        from core.route_composition import scenic_tier, trim_category_over_cap

        pool = [{"name": "老四号拌面", "type": "餐饮服务;中餐厅"}]
        route, actions = trim_category_over_cap(
            self._route(), cap=1, classify=self._classify(), scenic_tier_of=scenic_tier, pool=pool
        )
        names = [item["name"] for item in route]
        self.assertIn("老四号拌面", names, "没有景区时用美食替换（总比逛街贴近口径）")
        self.assertEqual(actions[0]["code"], "park_over_cap_replaced")

    def test_no_parks_means_no_actions(self):
        from core.route_composition import scenic_tier, trim_category_over_cap

        route = [{"day": 1, "name": "博斯腾湖风景名胜区", "type": "风景名胜;风景名胜;国家级景点"}]
        kept, actions = trim_category_over_cap(
            route, cap=0, classify=self._classify(), scenic_tier_of=scenic_tier, pool=self._pool()
        )
        self.assertEqual(actions, [])
        self.assertEqual(len(kept), 1)


class LodgingEnsureTests(unittest.TestCase):
    """每晚都要有住宿（2026-10-05 横向对照实测：伊宁 3 天方案一晚住宿都没有）。"""

    def test_missing_night_gets_the_nearest_candidate(self):
        from core.route_composition import ensure_lodging_per_night

        route = [
            {"day": 1, "name": "赛里木湖", "lnglat": [81.18, 44.60]},
            {"day": 1, "name": "伊宁希尔顿酒店", "lnglat": [81.34, 43.90]},
            {"day": 2, "name": "喀赞其民俗旅游区", "lnglat": [81.33, 43.91]},
            {"day": 2, "name": "伊犁河大桥", "lnglat": [81.35, 43.89]},
        ]
        pool = [
            {"name": "伊宁温州国际大酒店", "lnglat": [81.32, 43.92]},
            {"name": "乌鲁木齐环球酒店", "lnglat": [87.62, 43.83]},
        ]
        kept, actions, missing = ensure_lodging_per_night(route, nights=2, pool=pool)
        self.assertEqual(missing, [])
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["code"], "lodging_added")
        self.assertEqual(actions[0]["day"], 2)
        self.assertEqual(actions[0]["to"], "伊宁温州国际大酒店", "要挑离当天行程最近的，而不是池子里第一个")
        added = [node for node in kept if node.get("added_reason") == "missing_lodging_for_night"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["day"], 2)
        self.assertTrue(added[0]["is_hotel"])

    def test_days_that_already_have_lodging_are_left_alone(self):
        from core.route_composition import ensure_lodging_per_night

        route = [
            {"day": 1, "name": "博斯腾湖", "lnglat": [86.75, 41.98]},
            {"day": 1, "name": "库尔勒康城建国酒店", "lnglat": [86.15, 41.73]},
            {"day": 2, "name": "天鹅河景区", "lnglat": [86.14, 41.72]},
            {"day": 2, "name": "库尔勒民宿小院", "lnglat": [86.16, 41.74]},
        ]
        kept, actions, missing = ensure_lodging_per_night(route, nights=2, pool=[{"name": "备用酒店"}])
        self.assertEqual(actions, [])
        self.assertEqual(missing, [])
        self.assertEqual(len(kept), 4)

    def test_empty_pool_reports_the_gap_instead_of_inventing_a_hotel(self):
        from core.route_composition import ensure_lodging_per_night

        route = [{"day": 1, "name": "赛里木湖", "lnglat": [81.18, 44.60]}]
        kept, actions, missing = ensure_lodging_per_night(route, nights=1, pool=[])
        self.assertEqual(actions, [])
        self.assertEqual(missing, [1], "候选池为空就必须如实报缺，绝不编一个酒店")
        self.assertEqual(len(kept), 1)
        self.assertFalse(any(node.get("is_hotel") for node in kept))

    def test_the_same_hotel_is_never_reused_for_two_nights(self):
        from core.route_composition import ensure_lodging_per_night

        route = [
            {"day": 1, "name": "博斯腾湖", "lnglat": [86.75, 41.98]},
            {"day": 2, "name": "天鹅河景区", "lnglat": [86.14, 41.72]},
        ]
        pool = [
            {"name": "库尔勒康城建国酒店", "lnglat": [86.15, 41.73]},
            {"name": "库尔勒东归宾馆", "lnglat": [86.16, 41.75]},
        ]
        kept, actions, missing = ensure_lodging_per_night(route, nights=2, pool=pool)
        self.assertEqual(missing, [])
        added = [str(node.get("name")) for node in kept if node.get("added_reason") == "missing_lodging_for_night"]
        self.assertEqual(len(added), 2)
        self.assertEqual(len(set(added)), 2, "两晚不能住同一家，池子里有第二个就必须用第二个")

    def test_a_hotel_already_in_the_route_is_not_re_added_from_the_pool(self):
        from core.route_composition import ensure_lodging_per_night

        route = [
            {"day": 1, "name": "赛里木湖", "lnglat": [81.18, 44.60]},
            {"day": 1, "name": "伊宁希尔顿酒店", "lnglat": [81.34, 43.90]},
            {"day": 2, "name": "喀赞其民俗旅游区", "lnglat": [81.33, 43.91]},
            {"day": 2, "name": "伊犁河大桥", "lnglat": [81.35, 43.89]},
        ]
        pool = [
            {"name": "伊宁希尔顿酒店", "lnglat": [81.34, 43.90]},
            {"name": "伊宁温州国际大酒店", "lnglat": [81.32, 43.92]},
        ]
        kept, actions, missing = ensure_lodging_per_night(route, nights=2, pool=pool)
        self.assertEqual(missing, [])
        self.assertEqual([action["to"] for action in actions], ["伊宁温州国际大酒店"])

    def test_zero_nights_means_no_work(self):
        from core.route_composition import ensure_lodging_per_night

        route = [{"day": 1, "name": "博斯腾湖", "lnglat": [86.75, 41.98]}]
        kept, actions, missing = ensure_lodging_per_night(route, nights=0, pool=[{"name": "某酒店"}])
        self.assertEqual((actions, missing), ([], []))
        self.assertEqual(len(kept), 1)

    def test_classify_is_used_when_the_name_gives_nothing_away(self):
        from core.route_composition import ensure_lodging_per_night

        # 真实情况：酒店名可能完全不含"酒店"二字，只能靠高德类别认（住宿服务;宾馆酒店）
        route = [
            {"day": 1, "name": "博斯腾湖", "lnglat": [86.75, 41.98]},
            {"day": 1, "name": "康城建国国际", "lnglat": [86.15, 41.73], "type": "住宿服务;宾馆酒店;宾馆酒店"},
        ]
        pool = [{"name": "库尔勒东归宾馆", "lnglat": [86.16, 41.75]}]

        def classify(name, type_text, food_hint=False):
            return "hotel" if "住宿" in str(type_text) else "scenic"

        kept, actions, _missing = ensure_lodging_per_night(route, nights=1, pool=pool, classify=classify)
        self.assertEqual(actions, [], "类别已经说明这天有住宿，就不该再加一个")
        self.assertEqual(len(kept), 2)

    def test_without_classify_an_unnamed_hotel_room_is_not_assumed(self):
        from core.route_composition import ensure_lodging_per_night

        route = [
            {"day": 1, "name": "博斯腾湖", "lnglat": [86.75, 41.98]},
            {"day": 1, "name": "康城建国国际", "lnglat": [86.15, 41.73], "type": "住宿服务;宾馆酒店;宾馆酒店"},
        ]
        pool = [{"name": "库尔勒东归宾馆", "lnglat": [86.16, 41.75]}]
        _kept, actions, _missing = ensure_lodging_per_night(route, nights=1, pool=pool)
        self.assertEqual(len(actions), 1, "没有类别信息时按「看不出是住宿」处理，宁可补一个也不留空")


class LodgingDisclosureTests(unittest.TestCase):
    def test_repaired_nights_and_remaining_gaps_are_disclosed(self):
        from core.degradation import collect_degradations

        payload = {
            "lodging_repair": {
                "actions": [{"code": "lodging_added", "day": 2, "to": "伊宁希尔顿酒店"}],
                "missing_days": [3],
            }
        }
        items = collect_degradations(payload).get("items", [])
        repaired = next((item for item in items if "原本有晚上没安排住宿" in str(item.get("reason"))), None)
        self.assertIsNotNone(repaired, items)
        self.assertIn("第 2 晚→伊宁希尔顿酒店", str(repaired.get("reason")))
        gap = next((item for item in items if "没有可用住宿候选" in str(item.get("reason"))), None)
        self.assertIsNotNone(gap, items)
        self.assertIn("第 3 晚", str(gap.get("reason")))

    def test_nothing_repaired_and_nothing_missing_stays_quiet(self):
        from core.degradation import collect_degradations

        items = collect_degradations({"lodging_repair": {"actions": [], "missing_days": []}}).get("items", [])
        self.assertFalse([item for item in items if "住宿" in str(item.get("reason"))])


class ParkTrimDisclosureTests(unittest.TestCase):
    def test_trim_is_disclosed_with_names(self):
        from core.degradation import collect_degradations

        payload = {
            "park_trim": {
                "cap": 1,
                "actions": [
                    {"code": "park_over_cap_replaced", "from": "劳动公园", "to": "博斯腾湖风景名胜区"},
                    {"code": "park_over_cap_dropped", "from": "孔雀公园"},
                ],
            }
        }
        items = collect_degradations(payload).get("items", [])
        entry = next((item for item in items if "公园超过上限" in str(item.get("reason"))), None)
        self.assertIsNotNone(entry, items)
        self.assertIn("劳动公园→博斯腾湖风景名胜区", str(entry.get("reason")))
        self.assertIn("孔雀公园", str(entry.get("reason")))


if __name__ == "__main__":
    unittest.main()
