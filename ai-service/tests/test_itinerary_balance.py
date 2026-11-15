import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.itinerary_balance import enforce_category_mix, is_daily_balance_acceptable, rebalance_daily_route


def node(day, name, kind="景点"):
    return {"day": day, "name": name, "type": kind, "cost_estimate": "暂无供应商数据"}


class TestItineraryBalance(unittest.TestCase):
    def test_five_day_route_moves_surplus_to_last_day(self):
        route = [node(1, f"景点{i}") for i in range(1, 17)] + [node(day, f"已有{day}") for day in range(2, 6)]
        result = rebalance_daily_route(route, [], days=5, target_daytime=4)
        self.assertTrue(result["audit"]["satisfied"])
        self.assertEqual(result["audit"]["daytime_spread"], 0)
        self.assertEqual(len({item["name"] for item in result["route"]}), len(result["route"]))

    def test_pool_fills_every_day_without_inventing_price(self):
        route = [node(day, f"首站{day}") for day in range(1, 6)]
        pool = [node(1, f"候选{i}", "文化") for i in range(1, 21)]
        result = rebalance_daily_route(route, pool, days=5, target_daytime=4)
        self.assertTrue(is_daily_balance_acceptable(result["route"], 5, 4))
        self.assertEqual(result["audit"]["added_from_pool"], 15)
        added = [item for item in result["route"] if item["name"].startswith("候选")]
        self.assertTrue(all(item["cost_estimate"] == "暂无供应商数据" for item in added))

    def test_reports_shortage_when_real_candidates_are_insufficient(self):
        result = rebalance_daily_route([node(1, "唯一地点")], [], days=5, target_daytime=4)
        self.assertFalse(result["audit"]["satisfied"])
        self.assertEqual(result["audit"]["deficient_days"], [1, 2, 3, 4, 5])

    def test_category_mix_replaces_museum_only_day_with_real_pool_rows(self):
        route = [
            node(1, "博物馆甲", "博物馆"),
            node(1, "博物馆乙", "博物馆"),
            node(1, "酒店甲", "酒店") | {"is_hotel": True},
        ]
        pool = [
            node(1, "天山公园", "风景名胜"),
            node(1, "抓饭餐厅", "餐饮"),
            node(1, "老街", "商业街"),
        ]
        result = enforce_category_mix(route, pool, days=1, target_daytime=2)
        names = {item["name"] for item in result["route"]}
        self.assertIn("天山公园", names)
        self.assertIn("抓饭餐厅", names)
        self.assertNotIn("博物馆乙", names)
        self.assertTrue(result["audit"]["replaced"] >= 2)

    def test_surplus_moves_respect_day_pools(self):
        """省域分片：富余节点只能在"那天允许的候选"里挪。

        实测（2026-11-02 四川 4 天）：第 4 天计划阿坝，这里把成都的饭点挪过去，
        当天多跑 170km，分片计划也失效。
        """
        route = [node(1, f"成都{i}") for i in range(1, 6)] + [node(2, "阿坝首站")]
        pool = [node(1, "阿坝一号", "风景名胜")]
        day_pools = {1: [], 2: [{"day": 1, "name": "阿坝一号", "type": "风景名胜"}]}
        result = rebalance_daily_route(route, pool, days=2, target_daytime=3, day_pools=day_pools)
        day2_names = [item["name"] for item in result["route"] if item["day"] == 2]
        self.assertIn("阿坝一号", day2_names)
        self.assertNotIn("成都5", day2_names, day2_names)
        self.assertFalse(any(name.startswith("成都") for name in day2_names), day2_names)


if __name__ == "__main__":
    unittest.main()
