"""缺餐补位的测试（2026-10-07）。

真实缺口：喀什/伊宁都出现过 `skeleton_meal_missing`（某天没有餐饮节点）→ 门禁判失败。
这一条**可以补**，而且两条取法都不编造：
  ① 候选池里取"离当天玩点最近"的那家（全局分配：先按距离排序再配天，
     否则会把"离 A 天最近"的那家配给先轮到的 B 天）；
  ② 池子空/没候选时，从**有富余饭点（≥2 个）**的那天挪一个过来（如实记下距当天玩点多远）。
两条都不行就如实不动。
"""

import os
import sys
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")  # api.agent 导入时要建 OpenAI 客户端
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_review import top_up_missing_meals  # noqa: E402


def _plan():
    return {
        "route": [
            {"day": 1, "name": "甲景区", "type": "风景名胜", "lnglat": [81.66, 43.71], "time": "Day 1 | 09:00 游览"},
            {"day": 1, "name": "甲餐馆", "type": "餐饮服务", "lnglat": [81.66, 43.71], "time": "Day 1 | 13:00 用餐"},
            {"day": 1, "name": "乙餐馆", "type": "餐饮服务", "lnglat": [81.67, 43.72], "time": "Day 1 | 18:00 用餐"},
            {"day": 2, "name": "乙景区", "type": "风景名胜", "lnglat": [81.42, 43.85], "time": "Day 2 | 09:00 游览"},
            {"day": 3, "name": "丙景区", "type": "风景名胜", "lnglat": [80.80, 44.02], "time": "Day 3 | 09:00 游览"},
        ]
    }


class MealTopUpTests(unittest.TestCase):
    def _meals_by_day(self, plan):
        counts = {}
        for node in plan["route"]:
            if "餐" in f"{node.get('name')} {node.get('type')}":
                counts[node["day"]] = counts.get(node["day"], 0) + 1
        return counts

    def test_pool_candidate_goes_to_the_day_it_is_closest_to(self):
        """全局分配：离第 3 天 1.4km 的那家不该被配给第 2 天。"""
        result = top_up_missing_meals(
            {"days": 3},
            _plan(),
            {"food": [{"name": "近丙餐馆", "type": "餐饮服务", "lnglat": [80.81, 44.03]}]},
            {"max_nodes_per_day": 6},
        )
        self.assertTrue(result["changed"])
        added = [item for item in result["actions"] if item["code"] == "add_meal"]
        self.assertEqual(added[0]["day"], 3, added)
        self.assertLess(added[0]["distance_km"], 5)
        meals = self._meals_by_day(result["plan"])
        self.assertTrue(all(meals.get(day) for day in (1, 2, 3)), meals)

    def test_surplus_meal_is_moved_when_the_pool_is_empty(self):
        result = top_up_missing_meals({"days": 3}, _plan(), None, {"max_nodes_per_day": 6})
        moved = [item for item in result["actions"] if item["code"] == "move_meal"]
        self.assertTrue(moved, result["actions"])
        self.assertEqual(moved[0]["from_day"], 1)
        meals = self._meals_by_day(result["plan"])
        self.assertEqual(meals.get(1), 1, "第 1 天必须还留着一个饭点")
        self.assertEqual(meals.get(2), 1, meals)

    def test_nothing_happens_when_every_day_has_a_meal(self):
        plan = _plan()
        plan["route"].append({"day": 2, "name": "丙餐馆", "type": "餐饮服务", "lnglat": [81.42, 43.85]})
        plan["route"].append({"day": 3, "name": "丁餐馆", "type": "餐饮服务", "lnglat": [80.80, 44.02]})
        result = top_up_missing_meals({"days": 3}, plan, None, {"max_nodes_per_day": 6})
        self.assertFalse(result["changed"])

    def test_no_donor_leaves_the_plan_alone(self):
        """唯一一个饭点在别的天 → 不许抢（那会变成"修一天饿一天"）。"""
        plan = {
            "route": [
                {"day": 1, "name": "甲景区", "type": "风景名胜", "lnglat": [81.66, 43.71]},
                {"day": 1, "name": "甲餐馆", "type": "餐饮服务", "lnglat": [81.66, 43.71]},
                {"day": 2, "name": "乙景区", "type": "风景名胜", "lnglat": [81.42, 43.85]},
            ]
        }
        result = top_up_missing_meals({"days": 2}, plan, None, {"max_nodes_per_day": 6})
        self.assertFalse(result["changed"], result["actions"])

    def test_result_is_ordered_by_day_and_clock(self):
        """补/挪进来的节点必须按天+时刻排好（否则门禁 `day_out_of_order` 判"时间倒流"）。

        实测：第一版把挪过来的饭点追加在数组末尾，第 3 天变成 13:00 → 08:30 → 10:00，
        门禁直接判失败。
        """
        result = top_up_missing_meals({"days": 3}, _plan(), None, {"max_nodes_per_day": 6})
        per_day = {}
        for node in result["plan"]["route"]:
            per_day.setdefault(node["day"], []).append(str(node.get("time") or ""))
        for day, times in per_day.items():
            clocks = [t.split("|")[1].strip().split(" ")[0] for t in times]
            self.assertEqual(clocks, sorted(clocks), f"第 {day} 天时刻必须递增：{times}")

    def test_a_too_far_meal_is_not_forced_in(self):
        """实测：喀什第 3 天最近的候选饭点在 82.6km 外的市区 —— 塞进去只是把门禁"做"通过，
        用户不可能为一顿晚饭开 82km（anchoring 也从 0.833 掉到 0.667）。宁可不塞，但要如实记下来。"""
        result = top_up_missing_meals(
            {"days": 3},
            _plan(),
            {"food": [{"name": "很远很远的饭庄", "type": "餐饮服务", "lnglat": [78.00, 39.00]}]},
            {"max_nodes_per_day": 6, "meal_max_km": 30.0},
        )
        added = [item for item in result["actions"] if item["code"] == "add_meal"]
        self.assertEqual(added, [], result["actions"])
        self.assertTrue(result.get("skipped"), "太远没塞必须留下原因")
        self.assertGreater(result["skipped"][0]["nearest_km"], 30.0)

    def test_a_meal_next_to_one_of_two_spread_out_stops_is_accepted(self):
        """四姑娘山 ↔ 玛嘉沟 62km 的一日游：镇上的饭点离**质心** 31km，按质心会被判太远；
        按"离当天任一个玩点最近"算只有 4.5km，应当补进来（实测 2026-11-02 四川 4 天）。"""
        route = [
            {"day": 1, "name": "四姑娘山景区", "type": "风景名胜", "lnglat": [102.826, 31.094]},
            {"day": 1, "name": "玛嘉沟生态旅游景区", "type": "风景名胜", "lnglat": [102.427, 31.534]},
        ]
        result = top_up_missing_meals(
            {"days": 1},
            {"route": route},
            {"food": [{"name": "云端山语·观景餐厅", "type": "餐饮服务", "lnglat": [102.80, 31.06]}]},
            {"max_nodes_per_day": 5, "meal_max_km": 30.0},
        )
        added = [item for item in result["actions"] if item["code"] == "add_meal"]
        self.assertTrue(added, result)
        self.assertLess(added[0]["distance_km"], 10)

    def test_high_tolerance_allows_a_farther_meal(self):
        result = top_up_missing_meals(
            {"days": 3},
            _plan(),
            {"food": [{"name": "很远很远的饭庄", "type": "餐饮服务", "lnglat": [78.00, 39.00]}]},
            {"max_nodes_per_day": 6, "meal_max_km": 2000.0},
        )
        added = [item for item in result["actions"] if item["code"] == "add_meal"]
        self.assertTrue(added, result["actions"])

    def test_daily_cap_is_respected(self):
        result = top_up_missing_meals(
            {"days": 3},
            _plan(),
            {"food": [{"name": "近丙餐馆", "type": "餐饮服务", "lnglat": [80.81, 44.03]}]},
            {"max_nodes_per_day": 1},
        )
        self.assertFalse(result["changed"], "每天上限 1 个时不能再加节点")


class FoodClassificationTests(unittest.TestCase):
    """「胡杨书院」被当成餐馆的那次错判（2026-10-07）。

    餐饮词表里有单字「面」「饭」「茶」，而 `desc` 是自由文本：
    喀什的「胡杨书院」(type=科教文化服务) 的 desc 写着"巴楚博物馆后**面**" →
    `intent_of` / `_is_food` 都判成 **food** → "第 3 天没有饭点"被掩盖（门禁假通过），
    补餐逻辑也以为那天有饭。修法：单字关键词只认名字/类别/标签，desc 只认 ≥2 字的关键词。
    """

    def test_huyang_shuyuan_is_cultural_not_food(self):
        from core.candidate_index import intent_of
        from core.itinerary_skeleton import _is_food

        node = {
            "name": "胡杨书院",
            "type": "科教文化服务",
            "desc": "高德资料：科教文化服务｜评分 4.2｜巴楚镇新村路(巴楚博物馆后面)",
        }
        self.assertEqual(intent_of(node), "cultural", intent_of(node))
        self.assertFalse(_is_food(node))

    def test_real_restaurants_are_still_food(self):
        from core.candidate_index import intent_of
        from core.itinerary_skeleton import _is_food

        for node in (
            {"name": "正宗福建馄饨王(汇城小区店)", "type": "餐饮服务"},
            {"name": "老方桌烂蹄花·川味馆子(东城店)", "type": "餐饮服务"},
            {"name": "伊宁县小波匠家常菜店", "type": "餐饮服务"},
        ):
            self.assertEqual(intent_of(node), "food", node)
            self.assertTrue(_is_food(node), node)

    def test_single_char_food_word_only_counts_in_name_or_type(self):
        from core.candidate_index import intent_of

        # 名字里有「面」才算餐饮（"拌面王"），desc 里的「后面」不算
        self.assertEqual(intent_of({"name": "老字号回民拌面王", "type": "餐饮服务"}), "food")
        self.assertEqual(
            intent_of({"name": "某某书院", "type": "科教文化服务", "desc": "位于博物馆后面，环境清幽"}), "cultural"
        )


class NearbyFoodCandidatesTests(unittest.TestCase):
    """按天做周边检索拿餐饮候选（2026-10-07）。

    实测：喀什第 3 天（岳普湖方向）全城候选里最近的餐馆在 82.6km 外的市区；
    按天做一次 `place/around`，同一片区域 9.1km 就有几家 —— 缺餐因此能**就地**补上，
    而不是把市区餐馆硬塞到 200km 外的那一天。
    """

    def _route(self):
        return [
            {"day": 1, "name": "甲景区", "type": "风景名胜", "lnglat": [76.0, 39.4]},
            {"day": 1, "name": "甲餐馆", "type": "餐饮服务", "lnglat": [76.0, 39.4]},
            {"day": 2, "name": "乙景区", "type": "风景名胜", "lnglat": [77.0, 39.2]},
            {"day": 2, "name": "乙民宿", "type": "住宿服务", "is_hotel": True, "lnglat": [77.0, 39.2]},
        ]

    def test_only_hungry_days_are_searched(self):
        import asyncio

        from api.agent import collect_nearby_food_candidates

        calls = []

        async def fake_nearby(center, keywords, **kwargs):
            calls.append((list(center), keywords, kwargs))
            return [{"name": "附近饭馆", "type": "餐饮服务", "lnglat": [77.01, 39.21]}]

        pool, report = asyncio.run(
            collect_nearby_food_candidates(self._route(), days=2, fetch_nearby=fake_nearby, radius_km=15.0)
        )
        self.assertEqual(len(calls), 1, "只有第 2 天缺餐，应该只查它")
        self.assertEqual(calls[0][0], [77.0, 39.2])
        self.assertEqual([item["name"] for item in pool["food"]], ["附近饭馆"])
        self.assertEqual(report[0]["day"], 2)
        self.assertEqual(report[0]["found"], 1)

    def test_duplicate_names_are_not_added(self):
        import asyncio

        from api.agent import collect_nearby_food_candidates

        async def fake_nearby(center, keywords, **kwargs):
            return [{"name": "乙景区", "type": "风景名胜", "lnglat": [77.0, 39.2]}]

        pool, _ = asyncio.run(
            collect_nearby_food_candidates(self._route(), days=2, fetch_nearby=fake_nearby)
        )
        self.assertEqual(pool["food"], [], "已经在方案里的名字不许再作为候选")

    def test_a_failing_day_does_not_break_the_others(self):
        import asyncio

        from api.agent import collect_nearby_food_candidates

        async def fake_nearby(center, keywords, **kwargs):
            raise RuntimeError("限流")

        pool, report = asyncio.run(
            collect_nearby_food_candidates(self._route(), days=2, fetch_nearby=fake_nearby)
        )
        self.assertEqual(pool["food"], [])
        self.assertTrue(report[0].get("error"), report)

    def test_nearby_candidate_is_accepted_by_the_top_up(self):
        """周边候选 + 补餐：9km 的饭点会被采纳（而不是留空或被判太远）。"""
        import asyncio

        from api.agent import collect_nearby_food_candidates

        async def fake_nearby(center, keywords, **kwargs):
            return [{"name": "岳普湖县双福美食", "type": "餐饮服务", "lnglat": [77.05, 39.24]}]

        route = [
            {"day": 1, "name": "甲景区", "type": "风景名胜", "lnglat": [76.0, 39.4]},
            {"day": 2, "name": "乙景区", "type": "风景名胜", "lnglat": [77.0, 39.2]},
            {"day": 2, "name": "乙公园", "type": "风景名胜", "lnglat": [77.0, 39.2]},
        ]
        pool, _ = asyncio.run(
            collect_nearby_food_candidates(route, days=2, fetch_nearby=fake_nearby, radius_km=15.0)
        )
        result = top_up_missing_meals({"days": 2}, {"route": route}, pool, {"max_nodes_per_day": 6, "meal_max_km": 30.0})
        added = [item for item in result["actions"] if item["code"] == "add_meal"]
        self.assertTrue(added, result)
        self.assertLess(added[0]["distance_km"], 30.0)


class LodgingGeographyTests(unittest.TestCase):
    """住得太远就换成当天玩点附近的（2026-10-07：伊宁 anchoring=0.143）。

    真实数据：第 1 天玩点在英塔木镇（天鹅泉/马场），酒店却在 38.5km 外的市区；
    `ensure_lodging_per_night` 只在**缺夜**时按"离当天最近"补，已经有酒店的那一晚没人回头看。
    """

    def _route(self):
        return [
            {"day": 1, "name": "天鹅泉湿地公园", "type": "风景名胜", "lnglat": [81.65, 43.70]},
            {"day": 1, "name": "甲餐馆", "type": "餐饮服务", "lnglat": [81.65, 43.70]},
            {
                "day": 1,
                "name": "市区大酒店",
                "type": "住宿服务",
                "is_hotel": True,
                "lnglat": [81.30, 43.94],
                "time": "Day 1 | 21:30 入住",
            },
            {"day": 2, "name": "伊犁河景区", "type": "风景名胜", "lnglat": [81.29, 43.89]},
            {
                "day": 2,
                "name": "河边民宿",
                "type": "住宿服务",
                "is_hotel": True,
                "lnglat": [81.29, 43.89],
                "time": "Day 2 | 21:30 入住",
            },
        ]

    def test_a_far_hotel_is_replaced_by_a_nearby_real_one(self):
        import asyncio

        from core.plan_review import adjust_lodging_by_geography

        async def fake_nearby(center, keywords, **kwargs):
            return [{"name": "英塔木镇民宿", "type": "住宿服务", "lnglat": [81.66, 43.71]}]

        result = asyncio.run(
            adjust_lodging_by_geography(self._route(), days=2, fetch_nearby=fake_nearby, threshold_km=15.0)
        )
        self.assertTrue(result["changed"])
        action = result["actions"][0]
        self.assertEqual(action["day"], 1)
        self.assertEqual(action["to"], "英塔木镇民宿")
        self.assertGreater(action["from_km"], 30)
        self.assertLess(action["to_km"], 5)
        names = [node["name"] for node in result["plan"]["route"]]
        self.assertNotIn("市区大酒店", names)
        self.assertIn("河边民宿", names, "本来就近的那晚不许动")
        hotel = next(node for node in result["plan"]["route"] if node["name"] == "英塔木镇民宿")
        self.assertEqual(hotel["time"], "Day 1 | 21:30 入住", "入住时刻要沿用原来那一刻")

    def test_a_slightly_closer_hotel_is_not_worth_swapping(self):
        """明显更近才换（跟按地理分天同一个谨慎口径）：只近一点点不值得让用户看到换店。

        这里酒店离玩点约 18km（刚超过 15km 阈值），周边最近的约 12km —— 近了但不到 40%，不换。
        """
        import asyncio

        from core.plan_review import adjust_lodging_by_geography

        route = [
            {"day": 1, "name": "天鹅泉湿地公园", "type": "风景名胜", "lnglat": [81.65, 43.70]},
            {
                "day": 1,
                "name": "稍远酒店",
                "type": "住宿服务",
                "is_hotel": True,
                "lnglat": [81.44, 43.78],
                "time": "Day 1 | 21:30 入住",
            },
            {"day": 2, "name": "伊犁河景区", "type": "风景名胜", "lnglat": [81.29, 43.89]},
        ]

        async def fake_nearby(center, keywords, **kwargs):
            return [{"name": "半路客栈", "type": "住宿服务", "lnglat": [81.50, 43.75]}]

        result = asyncio.run(
            adjust_lodging_by_geography(route, days=1, fetch_nearby=fake_nearby, threshold_km=15.0)
        )
        self.assertFalse(result["changed"], result["actions"])
        self.assertEqual(result["skipped"][0]["day"], 1)
        self.assertGreater(result["skipped"][0]["current_km"], 15.0)
        self.assertIsNotNone(result["skipped"][0]["nearest_km"])

    def test_nothing_happens_when_the_search_finds_nothing(self):
        import asyncio

        from core.plan_review import adjust_lodging_by_geography

        async def fake_nearby(center, keywords, **kwargs):
            return []

        result = asyncio.run(
            adjust_lodging_by_geography(self._route(), days=2, fetch_nearby=fake_nearby, threshold_km=15.0)
        )
        self.assertFalse(result["changed"])
        self.assertTrue(result["skipped"])
        self.assertEqual([node["name"] for node in result["plan"]["route"]][2], "市区大酒店")

    def test_a_failing_search_leaves_the_plan_alone(self):
        import asyncio

        from core.plan_review import adjust_lodging_by_geography

        async def fake_nearby(center, keywords, **kwargs):
            raise RuntimeError("高德限流")

        result = asyncio.run(
            adjust_lodging_by_geography(self._route(), days=2, fetch_nearby=fake_nearby, threshold_km=15.0)
        )
        self.assertFalse(result["changed"])
        self.assertTrue(result["skipped"][0].get("error"))


class MealGeographyTests(unittest.TestCase):
    """排得太远的饭点换成附近的（2026-10-07：伊宁第 1 天三家餐馆都在 30km 外的县城）。

    与"补餐"分工：补餐治"某天没有饭点"，这里治"有饭点但太远"。一对一替换 → 每天至少一餐不变。
    """

    def _route(self):
        return [
            {"day": 1, "name": "天鹅泉湿地公园", "type": "风景名胜", "lnglat": [81.66, 43.71]},
            {
                "day": 1,
                "name": "县城大餐馆",
                "type": "餐饮服务",
                "lnglat": [81.49, 43.97],
                "time": "Day 1 | 13:30 用餐",
            },
            {"day": 2, "name": "伊犁河景区", "type": "风景名胜", "lnglat": [81.29, 43.89]},
            {
                "day": 2,
                "name": "河边拌面王",
                "type": "餐饮服务",
                "lnglat": [81.30, 43.88],
                "time": "Day 2 | 13:30 用餐",
            },
        ]

    def test_lodging_mover_uses_the_same_distance_as_the_ruler(self):
        """住宿侧同一把尺子问题（2026-10-08）：判定要用"到当天任一玩点的最近距离"。

        实测形态（伊宁 yn25 第 1 天 信泰达民宿）：最近玩点 24.9km（尺子扣分）／质心 1.2km（旧口径不管）。
        """
        import asyncio

        from core.plan_quality import ANCHOR_KM, score_anchoring
        from core.plan_review import adjust_lodging_by_geography

        route = [
            {"day": 1, "name": "玩点甲", "type": "风景名胜", "lnglat": [81.90, 43.71]},
            {"day": 1, "name": "玩点乙", "type": "风景名胜", "lnglat": [81.30, 43.71]},
            {
                "day": 1,
                "name": "中间民宿",
                "type": "住宿服务",
                "is_hotel": True,
                "lnglat": [81.60, 43.71],
                "time": "Day 1 | 21:00 入住",
            },
        ]
        before = score_anchoring({}, {"route": route})
        self.assertTrue(before["far_nodes"], "构造要让尺子扣分")
        self.assertGreater(before["far_nodes"][0]["distance_km"], ANCHOR_KM)

        async def fake_nearby(center, keywords, **kwargs):
            return [
                {
                    "name": "玩点甲旁边客栈",
                    "type": "住宿服务",
                    "lnglat": [81.901, 43.711],
                    "is_hotel": True,
                }
            ]

        result = asyncio.run(
            adjust_lodging_by_geography(route, days=1, fetch_nearby=fake_nearby, threshold_km=15.0)
        )
        self.assertTrue(result["changed"], "离质心很近但离玩点很远 → 必须换掉（旧口径漏掉）")
        after = score_anchoring({}, result["plan"])
        self.assertEqual(after["far_nodes"], [], "换完尺子不该再扣分")

    def test_a_far_meal_is_replaced_by_a_nearby_one(self):
        import asyncio

        from core.plan_review import adjust_meals_by_geography

        async def fake_nearby(center, keywords, **kwargs):
            return [{"name": "英塔木镇饭馆", "type": "餐饮服务", "lnglat": [81.67, 43.72]}]

        result = asyncio.run(adjust_meals_by_geography(self._route(), days=2, fetch_nearby=fake_nearby))
        self.assertTrue(result["changed"])
        self.assertEqual(result["actions"][0]["to"], "英塔木镇饭馆")
        names = [node["name"] for node in result["plan"]["route"]]
        self.assertNotIn("县城大餐馆", names)
        self.assertIn("河边拌面王", names, "本来就近的那天不许动")
        # 每天仍然有饭点（一对一替换）
        for day in (1, 2):
            meals = [
                node
                for node in result["plan"]["route"]
                if node["day"] == day and "餐" in str(node.get("type"))
            ]
            self.assertTrue(meals, f"第 {day} 天不能没有饭点")

    def test_swaps_are_capped_per_day_and_in_total(self):
        """一天最多换 3 个、总共最多 6 个：限额是按**实测耗时**定的（一次检索 0.32~0.40s），
        不是为了"完美"无限检索，但也不能因为假设的耗时把明明能换的都留下。"""
        import asyncio

        from core.plan_review import adjust_meals_by_geography

        route = [
            {"day": 1, "name": "天鹅泉湿地公园", "type": "风景名胜", "lnglat": [81.66, 43.71]},
            {"day": 1, "name": "远餐馆甲", "type": "餐饮服务", "lnglat": [81.49, 43.97]},
            {"day": 1, "name": "远餐馆乙", "type": "餐饮服务", "lnglat": [81.49, 43.97]},
            {"day": 1, "name": "远餐馆丙", "type": "餐饮服务", "lnglat": [81.49, 43.97]},
            {"day": 1, "name": "远餐馆丁", "type": "餐饮服务", "lnglat": [81.49, 43.97]},
        ]

        async def fake_nearby(center, keywords, **kwargs):
            return [
                {"name": "近饭馆一", "type": "餐饮服务", "lnglat": [81.67, 43.72]},
                {"name": "近饭馆二", "type": "餐饮服务", "lnglat": [81.67, 43.72]},
                {"name": "近饭馆三", "type": "餐饮服务", "lnglat": [81.67, 43.72]},
                {"name": "近饭馆四", "type": "餐饮服务", "lnglat": [81.67, 43.72]},
            ]

        result = asyncio.run(adjust_meals_by_geography(route, days=1, fetch_nearby=fake_nearby))
        self.assertEqual(len(result["actions"]), 3, result["actions"])
        one = asyncio.run(
            adjust_meals_by_geography(route, days=1, fetch_nearby=fake_nearby, max_per_day=1)
        )
        self.assertEqual(len(one["actions"]), 1, one["actions"])
        total = asyncio.run(
            adjust_meals_by_geography(route, days=1, fetch_nearby=fake_nearby, max_total=2)
        )
        self.assertEqual(len(total["actions"]), 2, total["actions"])

    def test_a_failing_search_leaves_meals_alone(self):
        import asyncio

        from core.plan_review import adjust_meals_by_geography

        async def fake_nearby(center, keywords, **kwargs):
            raise RuntimeError("高德限流")

        result = asyncio.run(adjust_meals_by_geography(self._route(), days=2, fetch_nearby=fake_nearby))
        self.assertFalse(result["changed"])
        self.assertTrue(result["skipped"][0].get("error"))
        self.assertEqual([node["name"] for node in result["plan"]["route"]][1], "县城大餐馆")

    def test_one_unimprovable_meal_does_not_block_the_others(self):
        """一个饭点"周边没有更近的"不能把**同一天剩下的饭点**一起跳过（2026-10-08 实测）。

        症状：伊宁第 1 天有三个 30km 外的饭点，检索第 1 个时周边没有更近的餐馆，
        代码直接 `break` 出整天的循环 —— 后面两个饭点**根本没被问过**，
        结果"还剩一个 30km 的饭点"看起来像"每天最多换 2 个"的限制造成的，其实是这个 break。
        正确行为：这个饭点记 `skipped`，继续看下一个。
        """
        import asyncio

        from core.plan_review import adjust_meals_by_geography

        route = [
            {"day": 1, "name": "天鹅泉湿地公园", "type": "风景名胜", "lnglat": [81.66, 43.71]},
            {"day": 1, "name": "远餐馆甲", "type": "餐饮服务", "lnglat": [81.49, 43.97]},
            {"day": 1, "name": "远餐馆乙", "type": "餐饮服务", "lnglat": [81.49, 43.97]},
        ]
        calls = []

        async def fake_nearby(center, keywords, **kwargs):
            calls.append(len(calls))
            # 第一次查"没有更近的"，第二次查给一个很近的 —— 两个饭点都要被问到
            if len(calls) == 1:
                return []
            return [{"name": "镇口小馆", "type": "餐饮服务", "lnglat": [81.67, 43.72]}]

        result = asyncio.run(adjust_meals_by_geography(route, days=1, fetch_nearby=fake_nearby))
        self.assertEqual(len(calls), 2, f"两个饭点都要被问到，实际只问了 {len(calls)} 次")
        self.assertEqual(len(result["skipped"]), 1, "第一个记 skipped")
        self.assertEqual(result["actions"][0]["to"], "镇口小馆", "第二个照样换掉")
        names = [node["name"] for node in result["plan"]["route"]]
        self.assertIn("镇口小馆", names)
        self.assertIn("远餐馆甲", names, "没找到替代的那个保持原样")

    def test_mover_uses_the_same_distance_as_the_ruler(self):
        """**判定距离必须与尺子一致**（2026-10-08 实测修，伊宁 anchoring 卡在 0.714 的真因）。

        尺子 `score_anchoring` 看"到当天**任一玩点的最近距离**"；
        改的人原来用"到当天玩点**质心**的距离"。一日多点时两者差很远，于是出现死角：
          · 饭点离最近的玩点 17.8km（尺子扣分），离质心只有 9.8km（改的人不管）→ 永远不修；
          · 反过来，最近玩点 3.8km（尺子通过）却被质心 13.7km 判成"太远"要换。
        这个用例构造的就是第一种：质心很近、但离每个玩点都远。
        """
        import asyncio

        from core.plan_quality import ANCHOR_KM, score_anchoring
        from core.plan_review import adjust_meals_by_geography

        # 两个玩点拉得很开（48km），饭点正好在它们中间的质心上：
        # 离质心 0.0km（老口径认为"就在旁边"），离每个玩点却都 24km（尺子扣分）。
        # 这正是伊宁真实的死角形态。
        route = [
            {"day": 1, "name": "玩点甲", "type": "风景名胜", "lnglat": [81.90, 43.71]},
            {"day": 1, "name": "玩点乙", "type": "风景名胜", "lnglat": [81.30, 43.71]},
            {"day": 1, "name": "中间饭馆", "type": "餐饮服务", "lnglat": [81.60, 43.71]},
        ]
        before = score_anchoring({}, {"route": route})
        self.assertTrue(
            before["far_nodes"], f"这个构造要让尺子扣分，实际 far_nodes={before['far_nodes']}"
        )
        self.assertGreater(before["far_nodes"][0]["distance_km"], ANCHOR_KM)

        async def fake_nearby(center, keywords, **kwargs):
            # 挨着"玩点甲"给一家真实餐馆（离玩点 ≤15km ⇒ 尺子也会满意）
            return [{"name": "玩点甲旁边的小馆", "type": "餐饮服务", "lnglat": [81.901, 43.711]}]

        result = asyncio.run(adjust_meals_by_geography(route, days=1, fetch_nearby=fake_nearby))
        self.assertTrue(result["changed"], "质心很近但离玩点很远 → 必须换掉（原来这里被漏掉）")
        self.assertIn("玩点甲旁边的小馆", [node["name"] for node in result["plan"]["route"]])
        after = score_anchoring({}, result["plan"])
        self.assertEqual(after["far_nodes"], [], "换完之后尺子不该再扣分")
        self.assertGreater(after["value"], before["value"])

    def test_a_meal_near_a_play_node_is_left_alone(self):
        """反向：离某个玩点很近就该放过，不能因为质心远就换掉（库尔勒实测的误判）。"""
        import asyncio

        from core.plan_review import adjust_meals_by_geography

        route = [
            {"day": 1, "name": "玩点甲", "type": "风景名胜", "lnglat": [81.90, 43.71]},
            {"day": 1, "name": "玩点乙", "type": "风景名胜", "lnglat": [81.30, 43.71]},
            {"day": 1, "name": "贴甲饭馆", "type": "餐饮服务", "lnglat": [81.901, 43.711]},
        ]

        async def fake_nearby(center, keywords, **kwargs):
            return [{"name": "别处小馆", "type": "餐饮服务", "lnglat": [81.60, 43.71]}]

        result = asyncio.run(adjust_meals_by_geography(route, days=1, fetch_nearby=fake_nearby))
        self.assertFalse(result["changed"], "贴着一个玩点就是合格的，不许换")
        self.assertEqual(result["measured"], [])

    def test_a_failing_search_still_stops_that_day(self):
        """检索**报错**时当天不再继续问（限流时逐点重试只是白等），与"没找到更近的"区分开。"""
        import asyncio

        from core.plan_review import adjust_meals_by_geography

        route = [
            {"day": 1, "name": "天鹅泉湿地公园", "type": "风景名胜", "lnglat": [81.66, 43.71]},
            {"day": 1, "name": "远餐馆甲", "type": "餐饮服务", "lnglat": [81.49, 43.97]},
            {"day": 1, "name": "远餐馆乙", "type": "餐饮服务", "lnglat": [81.49, 43.97]},
        ]
        calls = []

        async def fake_nearby(center, keywords, **kwargs):
            calls.append(len(calls))
            raise RuntimeError("高德限流")

        result = asyncio.run(adjust_meals_by_geography(route, days=1, fetch_nearby=fake_nearby))
        self.assertEqual(len(calls), 1, "报错就停，不要对着同一个失败源重试")
        self.assertFalse(result["changed"])
        self.assertTrue(result["skipped"][0].get("error"))


if __name__ == "__main__":
    unittest.main()
