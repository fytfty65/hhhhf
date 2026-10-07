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


if __name__ == "__main__":
    unittest.main()
