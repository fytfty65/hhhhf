"""行程物理现实性测试（硬约束过滤层的"日内里程"）。

真实背景（2026-10-04）：用户说"不考虑距离、只想看自然景观"，我们正确放开了半径（400km），
但下一版方案在 **3 天里同时排了博斯腾湖（47km）与巴音布鲁克大草原（约 270km）** ——
距离容忍解决"愿不愿意去"，不解决"一天放不放得下"。
用例里的坐标与距离都是真实量级（库尔勒市中心 86.1746,41.7259）。
"""

import unittest

from core.travel_feasibility import (
    FAR_NODE_KM,
    daily_budget_km,
    daily_far_limit,
    day_travel_km,
    evaluate_travel_feasibility,
    node_lnglat,
    repair_day_overload,
)

CENTER = [86.174633, 41.725891]
KORLA_PARK = [86.20, 41.74]        # 市区公园 ~3km
BOSTEN = [86.76, 41.98]            # 博斯腾湖 ~47km
BAYINBULAK = [84.15, 43.03]        # 巴音布鲁克 ~270km


def node(day, name, lnglat):
    return {"day": day, "name": name, "lnglat": lnglat}


class LngLatTests(unittest.TestCase):
    def test_reads_list_string_and_alt_keys(self):
        self.assertEqual(node_lnglat({"lnglat": [86.1, 41.7]}), [86.1, 41.7])
        self.assertEqual(node_lnglat({"location": "86.1,41.7"}), [86.1, 41.7])
        self.assertEqual(node_lnglat({"coord": [86.1, 41.7]}), [86.1, 41.7])

    def test_missing_coordinates_are_empty_not_zero(self):
        self.assertEqual(node_lnglat({"name": "没有坐标"}), [])
        self.assertEqual(node_lnglat(None), [])
        self.assertEqual(node_lnglat({"lnglat": ["a", "b"]}), [])


class BudgetTests(unittest.TestCase):
    def test_budget_follows_distance_tolerance(self):
        self.assertEqual(daily_budget_km("low"), 80.0)
        self.assertEqual(daily_budget_km("normal"), 160.0)
        self.assertEqual(daily_budget_km("high"), 320.0)
        self.assertEqual(daily_budget_km("whatever"), 160.0)

    def test_far_limit_is_one_per_day_even_when_distance_does_not_matter(self):
        """哪怕用户不在乎距离，一天也只能放一个远景区（物理限制）。"""
        self.assertEqual(daily_far_limit("high"), 1)
        self.assertEqual(daily_far_limit("normal"), 1)
        self.assertEqual(daily_far_limit("low"), 0)


class DayTravelTests(unittest.TestCase):
    def test_city_day_is_short(self):
        nodes = [node(1, "孔雀公园", KORLA_PARK), node(1, "湿地公园", [86.21, 41.75])]
        km = day_travel_km(nodes, CENTER)
        self.assertLess(km, 15, f"市区一天应远小于 15km，实际 {km}")

    def test_two_far_scenic_areas_in_one_day_is_long(self):
        nodes = [node(2, "博斯腾湖", BOSTEN), node(2, "巴音布鲁克", BAYINBULAK)]
        km = day_travel_km(nodes, CENTER)
        self.assertGreater(km, 250, f"两个远景区同一天必然很长，实际 {km}")

    def test_missing_coordinates_do_not_create_fake_distance(self):
        self.assertEqual(day_travel_km([node(1, "无坐标", None), node(1, "也没有", None)], CENTER), 0.0)


class EvaluateTests(unittest.TestCase):
    def test_two_far_scenic_in_one_day_is_reported(self):
        route = [
            node(1, "博斯腾湖", BOSTEN),
            node(1, "巴音布鲁克", BAYINBULAK),
            node(2, "孔雀公园", KORLA_PARK),
        ]
        report = evaluate_travel_feasibility(route, city_center=CENTER, distance_tolerance="high", days=2)
        self.assertFalse(report["ok"])
        codes = [item["code"] for item in report["violations"]]
        self.assertIn("too_many_far_scenic_in_one_day", codes)
        self.assertTrue(any("一天跑不完" in note for note in report["notes"]), report["notes"])

    def test_one_far_scenic_per_day_is_acceptable(self):
        route = [
            node(1, "博斯腾湖", BOSTEN),
            node(2, "巴音布鲁克", BAYINBULAK),
            node(3, "孔雀公园", KORLA_PARK),
        ]
        report = evaluate_travel_feasibility(route, city_center=CENTER, distance_tolerance="high", days=3)
        self.assertTrue(report["ok"], report["notes"])

    def test_short_tolerance_flags_any_far_node(self):
        route = [node(1, "博斯腾湖", BOSTEN)]
        report = evaluate_travel_feasibility(route, city_center=CENTER, distance_tolerance="low", days=1)
        self.assertFalse(report["ok"], "说了不想跑远，就不该出现 47km 的景区")

    def test_city_only_plan_passes(self):
        route = [node(1, "孔雀公园", KORLA_PARK), node(1, "龙山公园", [86.21, 41.75])]
        report = evaluate_travel_feasibility(route, city_center=CENTER, distance_tolerance="normal", days=1)
        self.assertTrue(report["ok"], report["notes"])

    def test_report_always_has_day_rows(self):
        report = evaluate_travel_feasibility([], city_center=CENTER, distance_tolerance="normal", days=3)
        self.assertEqual(len(report["days"]), 3)
        self.assertTrue(all(row["nodes"] == 0 for row in report["days"]))

    def test_missing_center_does_not_invent_far_node_violations(self):
        """没有市中心坐标时不许凭空判"这个点太远"（远景区判定要靠中心坐标）。

        注意：日内**里程**仍然照算（那是节点之间的距离，不需要中心）——
        两个相距 244km 的景区放同一天，本来就是跑不动的一天。
        """
        route = [node(1, "博斯腾湖", BOSTEN), node(1, "巴音布鲁克", BAYINBULAK)]
        report = evaluate_travel_feasibility(route, city_center=[], distance_tolerance="normal", days=1)
        codes = [item["code"] for item in report["violations"]]
        self.assertNotIn("too_many_far_scenic_in_one_day", codes, report["notes"])
        self.assertIn("daily_travel_budget_exceeded", codes, "244km 的一天本来就超预算，这是真实里程")


class RepairTests(unittest.TestCase):
    def test_keeps_nearest_far_node_and_moves_the_rest_out(self):
        route = [
            node(1, "巴音布鲁克", BAYINBULAK),
            node(1, "博斯腾湖", BOSTEN),
            node(1, "孔雀公园", KORLA_PARK),
        ]
        kept, removed = repair_day_overload(route, city_center=CENTER, distance_tolerance="normal")
        kept_names = [item["name"] for item in kept]
        removed_names = [item["name"] for item in removed]
        self.assertIn("博斯腾湖", kept_names, "保留更近的那个远景区")
        self.assertNotIn("巴音布鲁克", kept_names)
        self.assertEqual(removed_names, ["巴音布鲁克"])
        self.assertIn("一天跑不了两个远景区", str(removed[0].get("removed_reason")))
        self.assertIn("孔雀公园", kept_names, "市区节点不受影响")

    def test_never_empties_a_day(self):
        route = [node(1, "巴音布鲁克", BAYINBULAK), node(1, "博斯腾湖", BOSTEN)]
        kept, removed = repair_day_overload(route, city_center=CENTER, distance_tolerance="low")
        self.assertTrue(kept, "哪怕只允许 0 个远景区，也不能把一天清空")
        self.assertEqual(len(kept) + len(removed), 2)

    def test_no_change_when_each_day_has_one_far_node(self):
        route = [node(1, "博斯腾湖", BOSTEN), node(2, "巴音布鲁克", BAYINBULAK)]
        kept, removed = repair_day_overload(route, city_center=CENTER, distance_tolerance="high")
        self.assertEqual(removed, [])
        self.assertEqual(len(kept), 2)

    def test_repaired_route_passes_the_check(self):
        route = [
            node(1, "巴音布鲁克", BAYINBULAK),
            node(1, "博斯腾湖", BOSTEN),
            node(2, "孔雀公园", KORLA_PARK),
        ]
        kept, _ = repair_day_overload(route, city_center=CENTER, distance_tolerance="normal")
        report = evaluate_travel_feasibility(kept, city_center=CENTER, distance_tolerance="normal", days=2)
        codes = [item["code"] for item in report["violations"]]
        self.assertNotIn("too_many_far_scenic_in_one_day", codes, report["notes"])


class ThresholdTests(unittest.TestCase):
    def test_far_threshold_matches_real_scale(self):
        """市区景点普遍 ≤5km、博斯腾湖 47km —— 阈值 40km 能把两者分开。"""
        self.assertTrue(40 <= FAR_NODE_KM <= 60)
        from core.travel_feasibility import haversine_km

        self.assertLess(haversine_km(CENTER, KORLA_PARK), FAR_NODE_KM)
        self.assertGreater(haversine_km(CENTER, BOSTEN), FAR_NODE_KM)


class TravelDegradationTests(unittest.TestCase):
    """日内跑不完必须出现在「遗留问题」里（界面直接读它）。"""

    def test_violation_is_registered(self):
        from core.degradation import collect_degradations

        payload = {
            "travel_feasibility": evaluate_travel_feasibility(
                [node(1, "博斯腾湖", BOSTEN), node(1, "巴音布鲁克", BAYINBULAK)],
                city_center=CENTER,
                distance_tolerance="normal",
                days=1,
            )
        }
        items = collect_degradations(payload).get("items", [])
        entry = next((item for item in items if item["source"] == "travel_feasibility"), None)
        self.assertIsNotNone(entry, "跑不完的一天却没登记")
        self.assertIn("一天跑不完", str(entry.get("reason")))

    def test_auto_repair_is_disclosed_with_names(self):
        from core.degradation import collect_degradations

        payload = {
            "travel_repair": {
                "moved_out": [{"name": "巴音布鲁克大草原", "day": 1, "reason": "一天跑不了两个远景区"}],
                "reason": "一天跑不了两个远景区",
            }
        }
        items = collect_degradations(payload).get("items", [])
        entry = next((item for item in items if item["source"] == "travel_feasibility"), None)
        self.assertIsNotNone(entry)
        self.assertIn("巴音布鲁克大草原", str(entry.get("reason")))
        self.assertIn("移出", str(entry.get("reason")))

    def test_ok_plan_adds_nothing(self):
        from core.degradation import collect_degradations

        payload = {
            "travel_feasibility": evaluate_travel_feasibility(
                [node(1, "孔雀公园", KORLA_PARK)], city_center=CENTER, distance_tolerance="normal", days=1
            )
        }
        items = collect_degradations(payload).get("items", [])
        self.assertFalse([item for item in items if item["source"] == "travel_feasibility"])


class BudgetGraceTests(unittest.TestCase):
    """预算的**容差**：我们算的是直线距离，真实路网比直线长 20–40%。

    实测（2026-10-05 喀什）：第 1 天 161.8km 被判"超当天预算 160km"并写进披露 ——
    用户看到的是"这个系统连 1.8 公里都要报一次"，噪声把真问题（445km 那天）淹了。
    """

    def test_small_overage_within_the_grace_is_not_a_violation(self):
        from core.travel_feasibility import effective_daily_budget_km

        route = [node(1, "市区点", CENTER), node(1, "刚过线", (88.25, 41.73))]
        report = evaluate_travel_feasibility(route, city_center=CENTER, distance_tolerance="normal", days=1)
        self.assertGreater(report["days"][0]["travel_km"], report["budget_km"])
        self.assertLess(report["days"][0]["travel_km"], report["effective_budget_km"])
        self.assertTrue(report["ok"], report["violations"])
        self.assertEqual(report["budget_km"], 160.0, "报告里仍如实写原预算，不美化口径")
        self.assertEqual(report["effective_budget_km"], effective_daily_budget_km("normal"))
        self.assertEqual(report["grace_ratio"], 0.10)

    def test_real_overage_above_the_grace_still_fails(self):
        route = [node(1, "市区点", CENTER), node(1, "真超了", BAYINBULAK)]
        report = evaluate_travel_feasibility(route, city_center=CENTER, distance_tolerance="normal", days=1)
        self.assertFalse(report["ok"], report)
        self.assertIn("daily_travel_budget_exceeded", report["days"][0]["violations"])

    def test_grace_follows_the_tolerance_band(self):
        """low 档也要有同样的容差（80km → 88km），否则"差一点点"在小预算下更容易误报。"""
        from core.travel_feasibility import effective_daily_budget_km

        self.assertAlmostEqual(effective_daily_budget_km("low"), 88.0, places=1)
        self.assertAlmostEqual(effective_daily_budget_km("high"), 352.0, places=1)
        self.assertAlmostEqual(effective_daily_budget_km("不存在"), 176.0, places=1)


if __name__ == "__main__":
    unittest.main()
