"""回归：候选池保底方案必须按配比排，标签不能反过来污染类别。"""

import unittest

from core.fallback_synthesis import plan_fallback_picks, tags_for
from core.itinerary_balance import poi_category
from core.route_composition import composition_targets


def scenic(name, lng, lat, tier="landmark"):
    return {"name": name, "lnglat": [lng, lat], "type": "风景名胜", "planning_category": "scenic", "scenic_tier": tier}


def food(name, lng, lat):
    return {"name": name, "lnglat": [lng, lat], "type": "餐饮服务", "planning_category": "food"}


def cultural(name, lng, lat):
    return {"name": name, "lnglat": [lng, lat], "type": "科教文化服务", "planning_category": "cultural"}


def hotel(name, lng, lat):
    return {"name": name, "lnglat": [lng, lat], "type": "住宿服务", "planning_category": "hotel", "is_hotel": True}


class FallbackSynthesisTest(unittest.TestCase):
    def test_scenic_and_food_request_gets_scenic_every_day(self):
        """用户说「看自然景观 + 吃地道美食」时，每天都得有真景区，而不是一串餐厅。"""
        pool = [
            scenic("青城山", 103.57, 30.90), scenic("都江堰景区", 103.62, 31.00),
            scenic("西岭雪山", 103.20, 30.72), scenic("人民公园", 104.06, 30.66, tier="city_park"),
            food("陈麻婆豆腐", 104.06, 30.67), food("钟水饺", 104.07, 30.66),
            food("明婷饭店", 104.08, 30.68), food("蜀九香火锅", 104.05, 30.65),
            food("小谭豆花", 104.07, 30.67), food("龙抄手", 104.06, 30.65),
            cultural("武侯祠", 104.05, 30.64), cultural("四川博物院", 104.03, 30.66),
            hotel("春熙路某酒店", 104.08, 30.65), hotel("太古里某酒店", 104.09, 30.65),
            hotel("宽窄巷子某酒店", 104.05, 30.67),
        ]
        targets = composition_targets(trip_days=3, daily_nodes=4, wants_scenic=True, wants_food=True)
        result = plan_fallback_picks(pool, days=3, daily_nodes=4, targets=targets)
        picks = result["picks"]
        by_day = {}
        for pick in picks:
            by_day.setdefault(pick["day"], []).append(pick)

        for day, rows in by_day.items():
            categories = [row["category"] for row in rows]
            self.assertIn("scenic", categories, f"Day {day} 没有景区：{categories}")
            self.assertIn("food", categories, f"Day {day} 没有餐饮：{categories}")
            self.assertEqual(categories.count("hotel"), 1, f"Day {day} 住宿节点应恰好 1 个：{categories}")

        names = [row["item"]["name"] for row in picks if row["item"]]
        self.assertEqual(len(names), len(set(names)), "同一个地点不能重复出现")
        self.assertTrue(all(row["item"] for row in picks if row["category"] != "hotel"))
        self.assertEqual(result["audit"]["planned_daytime"], 12)
        # 自然景区名额只由 landmark 兑现；城市公园单独计数，不许顶替
        self.assertEqual(result["audit"]["natural_scenic"], 3)
        chosen_scenic = [row["item"]["name"] for row in picks if row["category"] == "scenic"]
        self.assertNotIn("人民公园", chosen_scenic)
        self.assertEqual(result["audit"]["city_park"], 1)

    def test_pool_shortage_is_reported_honestly(self):
        """候选池只有 1 个景区时，不许假装排满，audit 要如实说只有 1 个。"""
        pool = [scenic("楠木沟风景区", 104.0, 31.4)] + [
            food(f"餐厅{i}", 104.0 + i * 0.01, 30.6) for i in range(6)
        ]
        result = plan_fallback_picks(pool, days=3, daily_nodes=3, targets=None)
        self.assertEqual(result["audit"]["by_category"].get("scenic"), 1)
        self.assertEqual(result["audit"]["planned_daytime"], 7)

    def test_tags_describe_category_only(self):
        self.assertEqual(tags_for("scenic"), ["自然", "风景区"])
        self.assertEqual(tags_for("scenic", {"scenic_tier": "city_park"}), ["公园", "城市休闲"])
        self.assertEqual(tags_for("hotel"), ["住宿"])


class CategoryPrecedenceTest(unittest.TestCase):
    def test_bogus_tags_cannot_flip_a_scenic_spot_into_food(self):
        """真实事故：降级路线按槽位贴标签，把真景区算成餐饮，类别均衡因此补不出景区。"""
        node = {
            "name": "楠木沟风景区",
            "type": "风景名胜",
            "tags": ["美食", "夜市"],
        }
        self.assertEqual(poi_category(node), "scenic")

    def test_supplier_type_wins_over_decorative_tags(self):
        node = {"name": "陶德砂锅(世豪广场店)", "type": "餐饮服务", "tags": ["地标", "文化"]}
        self.assertEqual(poi_category(node), "food")

    def test_planning_category_is_authoritative(self):
        node = {"name": "某景区", "planning_category": "scenic", "tags": ["美食"]}
        self.assertEqual(poi_category(node), "scenic")


class ProvinceRegionPlanTest(unittest.TestCase):
    """省域保底排布：给了分片建议就按片区排，不再让一天横跨半个省。"""

    def test_picks_follow_day_region_plan(self):
        pool = [
            scenic("成都大熊猫基地", 104.14, 30.74) | {"region_short": "成都"},
            scenic("都江堰景区", 103.62, 31.00) | {"region_short": "成都"},
            scenic("九寨沟", 103.92, 33.26) | {"region_short": "阿坝"},
            food("陈麻婆豆腐", 104.06, 30.67) | {"region_short": "成都"},
            food("九寨沟藏餐", 103.92, 33.25) | {"region_short": "阿坝"},
            hotel("成都某酒店", 104.08, 30.65) | {"region_short": "成都"},
            hotel("九寨沟某民宿", 103.92, 33.26) | {"region_short": "阿坝"},
        ]
        result = plan_fallback_picks(
            pool,
            days=2,
            daily_nodes=2,
            targets=None,
            day_regions={1: "成都", 2: "阿坝"},
            region_of=lambda item: str(item.get("region_short") or ""),
        )
        day1 = [pick["item"]["name"] for pick in result["picks"] if pick["day"] == 1 and pick["item"]]
        day2 = [pick["item"]["name"] for pick in result["picks"] if pick["day"] == 2 and pick["item"]]
        self.assertEqual(day1, ["成都大熊猫基地", "陈麻婆豆腐", "成都某酒店"])
        self.assertEqual(day2, ["九寨沟", "九寨沟藏餐", "九寨沟某民宿"])
        self.assertEqual(result["audit"]["day_regions"], {"1": "成都", "2": "阿坝"})

    def test_scenic_is_reserved_for_later_days_in_same_region(self):
        """同一片区占 2 天时，景区要分两天，不能把第 4 天要用的点全塞进第 3 天。

        实测（2026-11-02 四川 4 天）：阿坝 4 个真景区全进 Day 3（245km），
        Day 4 一个不剩 → 退回全池抓成都景点，计划与实际不一致。
        """
        pool = [
            scenic("四姑娘山", 102.83, 31.09) | {"region_short": "阿坝"},
            scenic("毕棚沟", 102.99, 31.38) | {"region_short": "阿坝"},
            scenic("玛嘉沟", 102.43, 31.53) | {"region_short": "阿坝"},
            scenic("孟屯河谷", 103.11, 31.71) | {"region_short": "阿坝"},
            scenic("成都大熊猫基地", 104.14, 30.74) | {"region_short": "成都"},
            hotel("成都某酒店", 104.08, 30.65) | {"region_short": "成都"},
            hotel("理县某酒店", 102.99, 31.40) | {"region_short": "阿坝"},
        ]
        result = plan_fallback_picks(
            pool,
            days=2,
            daily_nodes=5,
            targets={"scenic": 4, "food": 0, "cultural": 0},
            day_regions={1: "阿坝", 2: "阿坝"},
            region_of=lambda item: str(item.get("region_short") or ""),
        )
        day1 = [
            pick["item"]["name"] for pick in result["picks"]
            if pick["day"] == 1 and pick["item"] and not pick["item"].get("is_hotel")
        ]
        day2 = [
            pick["item"]["name"] for pick in result["picks"]
            if pick["day"] == 2 and pick["item"] and not pick["item"].get("is_hotel")
        ]
        self.assertEqual(len(day1), 2, day1)
        self.assertEqual(len(day2), 2, day2)
        self.assertNotIn("成都大熊猫基地", day1 + day2, "省域分片日不该跨片区借成都景点")

    def test_without_plan_behaviour_is_unchanged(self):
        pool = [
            scenic("成都大熊猫基地", 104.14, 30.74),
            scenic("九寨沟", 103.92, 33.26),
            hotel("成都某酒店", 104.08, 30.65),
        ]
        result = plan_fallback_picks(pool, days=2, daily_nodes=1, targets=None)
        self.assertEqual(result["audit"]["day_regions"], {})
        self.assertEqual(result["audit"]["planned_daytime"], 2)


if __name__ == "__main__":
    unittest.main()
