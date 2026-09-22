import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.itinerary_balance import is_daily_balance_acceptable, rebalance_daily_route


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


if __name__ == "__main__":
    unittest.main()
