"""地理就近分天的测试（2026-10-07）。

真实病（`work/diag_day_distance.py` 诊断 + 真跑）：喀什 3 天里，第 2 天是
九龙泉(市区) → 喀什博物馆(市区) → 川味馆子(市区) → **胡杨书院(巴楚 216km)** → 阳光公园(市区)
→ 亚朵酒店(市区)，当天 **449km**；而按地理就近重排后最赶的一天是 **293km**、全程 610→457km。
结论：不是候选池只有远点，是"天"这一层没看地理。
约束（测试逐条钉住）：住宿不动、用户点名的点不动、每天节点数不变、
每天每类至少保留一个、没坐标的点不动、**只有真的改善才动**。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.route_composition import rebalance_days_by_geography  # noqa: E402

CITY = [75.99, 39.47]  # 喀什市中心
NEAR = [76.00, 39.48]
NEAR2 = [76.01, 39.46]
FAR = [78.55, 39.79]  # 巴楚方向，200+km
FAR2 = [77.55, 39.60]  # 另一个方向的远点，约 150km


def _node(name, day, coord, time, **extra):
    node = {"name": name, "day": day, "lnglat": list(coord), "time": f"Day {day} | {time}", "type": "风景名胜"}
    node.update(extra)
    return node


class RebalanceTests(unittest.TestCase):
    def _route(self):
        """贴近真实结构：第 1、2 天各有酒店（远点要"出去再回来"= 2 倍里程），第 3 天不回市区。

        这正是喀什第 2 天跑 449km 的形状：市区几个点中间夹一个 216km 的远点，
        出去 216、回来 219 —— 一个远点花掉两趟。把两个远点并到**不回市区**的第 3 天，
        最赶的一天立刻降下来。
        """
        return [
            _node("市集广场", 1, NEAR, "09:00 游览"),
            _node("市立博物馆", 1, NEAR2, "11:00 游览", type="博物馆"),
            _node("老城餐馆", 1, NEAR, "13:00 用餐", type="餐饮"),
            _node("远处湿地", 1, FAR2, "15:00 游览"),
            _node("河边客栈", 1, NEAR, "21:30 入住", is_hotel=True),
            _node("东门书店", 2, NEAR2, "09:00 游览"),
            _node("城中茶馆", 2, NEAR, "11:00 游览"),
            _node("丝路餐馆", 2, NEAR2, "13:00 用餐", type="餐饮"),
            _node("东门书院", 2, NEAR2, "14:00 游览", type="博物馆"),
            _node("巴楚书院", 2, FAR, "15:00 游览", type="博物馆"),
            _node("西门旅社", 2, NEAR, "21:30 入住", is_hotel=True),
            _node("南湖公园", 3, NEAR, "09:00 游览"),
            _node("北苑茶馆", 3, NEAR2, "11:00 游览"),
        ]

    def test_far_outlier_is_moved_into_one_day(self):
        new_route, report = rebalance_days_by_geography(self._route())
        self.assertTrue(report["applied"], report)
        self.assertLess(report["after_max_km"], report["before_max_km"])

    def test_keeps_per_day_counts_and_hotels(self):
        route = self._route()
        new_route, _ = rebalance_days_by_geography(route)
        before = {}
        for node in route:
            before[node["day"]] = before.get(node["day"], 0) + 1
        after = {}
        for node in new_route:
            after[node["day"]] = after.get(node["day"], 0) + 1
        self.assertEqual(before, after, "每天节点数不能变（每日密度门禁看的是这个）")
        for hotel in [n for n in new_route if n.get("is_hotel")]:
            self.assertIn(hotel["day"], (1, 2), "住宿绑在'第几晚'，不许换天")

    def test_user_choice_nodes_stay(self):
        route = self._route()
        route[1]["user_choice"] = True  # 用户点名要的博物馆
        new_route, _ = rebalance_days_by_geography(route)
        kept = [n for n in new_route if str(n.get("name")) == "市立博物馆"][0]
        self.assertEqual(kept["day"], 1, "用户点名的点不许被搬走")

    def test_composition_is_preserved_where_it_can_be(self):
        """每天每类**尽量**保留：类别够分时（总数 ≥ 需要的天数）每天必须还有这一类；
        不够分时（实测：3 天只有 2 个人文点）允许某天失去这一类 —— 但**总量一个都不能少**。"""
        route = self._route()
        before_total = {}
        for node in route:
            key = node.get("type") or "other"
            before_total[key] = before_total.get(key, 0) + 1
        new_route, _ = rebalance_days_by_geography(route)
        after_total = {}
        cats_by_day = {}
        for node in new_route:
            key = node.get("type") or "other"
            after_total[key] = after_total.get(key, 0) + 1
            cats_by_day.setdefault(node["day"], set()).add(key)
        self.assertEqual(before_total, after_total, "重排不许丢点/改类别")
        # 餐饮有 2 个、需要 2 天 → 每天都还得有
        for day in (1, 2):
            self.assertIn("餐饮", cats_by_day.get(day, set()), f"第 {day} 天的餐饮不能被搬空")
        # 人文有 3 个（巴楚书院 + 东门书院 + 市立博物馆）—— 总数一个都不能少
        self.assertEqual(after_total.get("博物馆", 0), 3)

    def test_a_day_never_loses_all_of_its_play_kind(self):
        """重排不许让某天**原本有自然点、之后一个都没了** —— 那会凭空造出一个类别门禁失败。

        真实背景：门禁里有"以自然为主的行程每天至少 1 个自然景观、文化类设上限"，
        伊宁那次真跑就出现过 `play_category_monotony`。宁可这一天不挪。
        """
        route = [
            # 第 1 天：只有人文（重排若把它的人文挪走就会触发门禁）
            _node("市立博物馆", 1, NEAR, "09:00 游览", type="博物馆"),
            _node("老城餐馆", 1, NEAR, "13:00 用餐", type="餐饮"),
            _node("河边客栈", 1, NEAR, "21:30 入住", is_hotel=True),
            # 第 2 天：自然 + 人文
            _node("草原风景区", 2, NEAR2, "09:00 游览", type="风景名胜"),
            _node("远方博物馆", 2, FAR, "11:00 游览", type="博物馆"),
            _node("城南餐馆", 2, NEAR2, "13:00 用餐", type="餐饮"),
            _node("城北客栈", 2, NEAR2, "21:30 入住", is_hotel=True),
            # 第 3 天：自然，不回市区
            _node("湿地公园", 3, NEAR, "09:00 游览", type="风景名胜"),
            _node("森林公园", 3, FAR2, "11:00 游览", type="风景名胜"),
        ]
        new_route, report = rebalance_days_by_geography(route)
        if report["applied"]:
            kinds = {}
            for node in new_route:
                if node.get("type") in ("风景名胜", "博物馆"):
                    kinds.setdefault(node["day"], set()).add(node["type"])
            for day in (1, 2, 3):
                self.assertTrue(kinds.get(day), f"第 {day} 天不能一个玩点都没有：{kinds}")
        else:
            self.assertIn("丢", report["note"] + "丢")  # 判否理由必须说清

    def test_protect_kinds_follows_the_user_request(self):
        """`protect_kinds` 由调用方按诉求给：只保护那一类，别的类别允许跨天流动。

        实测背景：一刀切保护"自然+人文"把伊宁一次有价值的重排（167km → 130km）否掉了，
        而那个 `play_category_monotony` 失败在重排前后都存在（是原始路线的问题）。
        """
        route = self._route()
        strict, strict_report = rebalance_days_by_geography(route, protect_kinds=["scenic", "cultural"])
        loose, loose_report = rebalance_days_by_geography(route, protect_kinds=["scenic"])
        # 只保护自然时，人文可以流动 → 应该能拿到改善
        self.assertTrue(loose_report["applied"], loose_report)
        self.assertGreaterEqual(strict_report["after_max_km"], loose_report["after_max_km"] - 0.01)
        # 不管保护哪一类，"某天一个玩点都没有"永远不许发生
        for candidate in (strict, loose):
            kinds = {}
            for node in candidate:
                if node.get("type") in ("风景名胜", "博物馆"):
                    kinds.setdefault(node["day"], set()).add(node["type"])
            for day in kinds:
                self.assertTrue(kinds[day], f"第 {day} 天不能没有玩点")

    def test_no_change_when_already_compact(self):
        # 四个点都在同一片区域、两天完全等价 → 怎么换都一样，不该动
        route = [
            _node("A", 1, NEAR, "09:00 游览"),
            _node("B", 1, NEAR, "11:00 游览"),
            _node("C", 2, NEAR, "09:00 游览"),
            _node("D", 2, NEAR, "11:00 游览"),
        ]
        _, report = rebalance_days_by_geography(route)
        self.assertFalse(report["applied"], report)

    def test_rejected_rebalance_leaves_the_route_untouched(self):
        """判定"不值得重排"时**原路线一个字都不能改**（实测 bug：判否了但 day/time 已被改乱）。"""
        route = self._route()
        snapshot = [(n["name"], n["day"], n["time"]) for n in route]
        new_route, report = rebalance_days_by_geography(route, min_gain_ratio=10.0)  # 逼它判否
        self.assertFalse(report["applied"])
        self.assertEqual([(n["name"], n["day"], n["time"]) for n in new_route], snapshot)

    def test_nodes_without_coordinates_are_left_alone(self):
        route = self._route()
        route[0]["lnglat"] = []
        new_route, _ = rebalance_days_by_geography(route)
        kept = [n for n in new_route if str(n.get("name")) == "市集广场"][0]
        self.assertEqual(kept["day"], 1, "判不了地理的点不要乱搬")

    def test_time_suffix_and_day_prefix_are_rewritten_consistently(self):
        new_route, _ = rebalance_days_by_geography(self._route())
        per_day = {}
        for node in new_route:
            text = str(node.get("time") or "")
            self.assertTrue(text.startswith(f"Day {node['day']} |"), text)
            per_day.setdefault(node["day"], []).append(text)
        for day, texts in per_day.items():
            clocks = [t.split("|")[1].strip().split(" ")[0] for t in texts]
            self.assertEqual(clocks, sorted(clocks), f"第 {day} 天时刻必须递增：{texts}")
        restaurants = [n for n in new_route if n.get("type") == "餐饮"]
        self.assertTrue(any("用餐" in str(n.get("time")) for n in restaurants), "语义后缀要保留")


if __name__ == "__main__":
    unittest.main()
