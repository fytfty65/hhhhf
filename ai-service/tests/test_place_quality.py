"""「地点好不好 / 方案周全」可判定部分的测试（2026-10-05）。

这一层的价值全在"**只判能核实的、判不了就如实标注**"：
  · 真实检索：带高德 ID / 数据源标注 / 出现在候选池里 —— 三条独立证据；
  · 像不像玩点：设施、站点、纯商业点排在景点位上；泛称只列"待人工确认"**不扣分**；
  · 周全：每晚住宿、每天有饭吃、日内里程、价格出处、**未知项有没有被披露**、跨城腿有方案。
"""

import os
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")

from core.place_quality import (  # noqa: E402
    is_real_retrieval,
    looks_like_a_visitable_place,
    needs_human_review,
    score_completeness,
    score_place_quality,
)
from core.plan_quality import node_day  # noqa: E402


def node(name, day=1, *, time="09:00", type_text="风景名胜;风景名胜;国家级景点", lnglat=(86.15, 41.73), **extra):
    payload = {"day": day, "name": name, "time": time, "type": type_text}
    if lnglat is not None:
        payload["lnglat"] = list(lnglat)
    payload.update(extra)
    return payload


class RealRetrievalTests(unittest.TestCase):
    def test_amap_id_counts_as_real(self):
        self.assertTrue(is_real_retrieval(node("博斯腾湖风景名胜区", id="B0FFF")))

    def test_provider_data_source_counts_as_real(self):
        self.assertTrue(is_real_retrieval(node("博斯腾湖风景名胜区", data_sources={"rating": "amap"})))

    def test_candidate_pool_membership_counts_as_real(self):
        item = node("博斯腾湖风景名胜区")
        self.assertTrue(is_real_retrieval(item, known_names={"博斯腾湖风景名胜区"}))

    def test_plain_node_without_evidence_is_not_real(self):
        self.assertFalse(is_real_retrieval(node("某编出来的地方")))


class VisitablePlaceTests(unittest.TestCase):
    def test_facility_names_are_flagged(self):
        ok, why = looks_like_a_visitable_place(node("博斯腾湖景区停车场"))
        self.assertFalse(ok)
        self.assertIn("设施", why)

    def test_station_suffix_is_flagged(self):
        ok, why = looks_like_a_visitable_place(node("天鹅河景区站"))
        self.assertFalse(ok)
        self.assertIn("站", why)

    def test_real_station_named_place_is_kept(self):
        ok, _ = looks_like_a_visitable_place(node("库尔勒驿站"))
        self.assertTrue(ok, "「驿站」是真实场所，不能因为以「站」结尾就被毙掉")

    def test_commerce_point_on_a_scenic_slot_is_flagged(self):
        ok, why = looks_like_a_visitable_place(node("恒友广场购物中心"))
        self.assertFalse(ok)
        self.assertIn("商业点", why)

    def test_normal_scenic_area_passes(self):
        ok, _ = looks_like_a_visitable_place(node("博斯腾湖风景名胜区"))
        self.assertTrue(ok)

    def test_generic_names_are_listed_for_humans_not_penalised(self):
        """「漂亮的草原」「草原遗迹」是真实高德 POI，但值不值得去**机器判不了**。"""
        reason = needs_human_review(node("漂亮的草原"))
        self.assertIn("泛称", reason)
        report = score_place_quality({"route": [node("漂亮的草原", id="B1")]})
        self.assertEqual(report["value"], 1.0, "只列出来，不扣分")
        self.assertEqual(report["review_required"][0]["name"], "漂亮的草原")


class PlaceQualityScoreTests(unittest.TestCase):
    def test_all_retrieved_and_visitable_scores_one(self):
        plan = {"route": [node("博斯腾湖风景名胜区", id="B1"), node("天鹅河景区", id="B2")]}
        report = score_place_quality(plan)
        self.assertEqual(report["value"], 1.0)
        self.assertEqual(report["real_retrieval_ratio"], 1.0)
        self.assertNotIn("insufficient_evidence", report)

    def test_mixed_sources_are_proportional(self):
        plan = {"route": [node("博斯腾湖风景名胜区", id="B1"), node("某编出来的地方")]}
        report = score_place_quality(plan)
        self.assertEqual(report["real_retrieval_ratio"], 0.5)
        self.assertLess(report["value"], 1.0)

    def test_no_supplier_evidence_is_declared_not_guessed(self):
        plan = {"route": [node("某编出来的地方")]}
        report = score_place_quality(plan)
        self.assertTrue(report["insufficient_evidence"], report)
        self.assertIn("没有任何供应商证据", report["note"])

    def test_empty_route_is_zero_and_says_why(self):
        report = score_place_quality({"route": []})
        self.assertEqual(report["value"], 0.0)
        self.assertTrue(report["insufficient_evidence"])


class CompletenessTests(unittest.TestCase):
    def _plan(self, **overrides):
        """一份**真的周全**的 3 天方案：2 晚住宿、每天 3 顿/3 个节点、八成节点带价格。"""

        def priced(name, day, time, **extra):
            return node(name, day, time=time, cost_estimate="¥45", data_sources={"cost_estimate": "amap"}, **extra)

        plan = {
            "route": [
                priced("博斯腾湖风景名胜区", 1, "09:00", id="B1"),
                priced("老字号拌面", 1, "12:30", type_text="餐饮服务;中餐厅", lnglat=(86.16, 41.73)),
                priced("瑞德假日酒店", 1, "21:30", type_text="住宿服务;宾馆酒店", lnglat=(86.15, 41.74), is_hotel=True),
                priced("天鹅河景区", 2, "09:00", lnglat=(86.14, 41.72), id="B2"),
                priced("库尔勒民俗餐厅", 2, "12:30", type_text="餐饮服务;中餐厅", lnglat=(86.15, 41.75)),
                node("库尔勒民宿", 2, time="21:30", type_text="住宿服务;旅馆招待所", lnglat=(86.15, 41.76), is_hotel=True),
                node("铁门关景区", 3, time="09:00", lnglat=(85.68, 41.83), id="B3"),
                node("库尔勒香梨园餐厅", 3, time="12:30", type_text="餐饮服务;中餐厅", lnglat=(85.70, 41.84)),
            ],
            "price_audit": {"unknown": [], "pending": []},
        }
        plan.update(overrides)
        return plan

    def test_complete_plan_passes_every_check(self):
        report = score_completeness({"days": 3}, self._plan())
        self.assertEqual(report["failed_checks"], [], report)
        self.assertEqual(report["value"], 1.0, report)

    def test_missing_lodging_night_fails_that_check(self):
        plan = self._plan()
        plan["route"] = [item for item in plan["route"] if node_day(item, 1) != 2 or not item.get("is_hotel")]
        report = score_completeness({"days": 3}, plan)
        self.assertIn("lodging_per_night", report["failed_checks"])
        self.assertIn("1/2 晚有住宿", report["checks"]["lodging_per_night"]["detail"])

    def test_day_without_meal_fails(self):
        plan = self._plan()
        plan["route"] = [
            item for item in plan["route"]
            if not (node_day(item, 3) == 3 and "餐饮" in str(item.get("type") or ""))
        ]
        report = score_completeness({"days": 3}, plan)
        self.assertIn("meals_per_day", report["failed_checks"])

    def test_unknown_prices_must_be_disclosed(self):
        """有未核实价格、却没有待确认清单 → 周全性不过（「没说的未知」等于没说）。"""
        plan = self._plan(price_audit={"unknown": ["某酒店"], "pending": []})
        report = score_completeness({"days": 3}, plan)
        self.assertIn("unknown_disclosed", report["failed_checks"])

    def test_unknown_prices_with_pending_list_pass(self):
        plan = self._plan(price_audit={"unknown": ["某酒店"], "pending": [{"name": "某酒店"}]})
        report = score_completeness({"days": 3}, plan)
        self.assertNotIn("unknown_disclosed", report["failed_checks"])

    def test_far_day_fails_travel_budget(self):
        plan = self._plan()
        plan["route"][3] = node("很远的地方", 2, time="09:00", lnglat=(88.5, 43.0), id="B9")
        report = score_completeness({"days": 3, "distance_tolerance": "low"}, plan)
        self.assertIn("daily_travel", report["failed_checks"])

    def test_missing_coordinates_are_not_counted_as_failure(self):
        plan = {"route": [node("无坐标的点", 1, lnglat=None)], "price_audit": {}}
        report = score_completeness({"days": 1}, plan)
        self.assertEqual(report["checks"]["daily_travel"]["ok"], None, "没有坐标就是判不了，不算失败")
        self.assertIn("daily_travel", report["unknown_checks"])

    def test_cross_city_leg_without_options_fails(self):
        plan = self._plan(transport_audit={"legs": [{"from": "库尔勒", "to": "乌鲁木齐"}]})
        report = score_completeness({"days": 3}, plan)
        self.assertIn("cross_city_transport", report["failed_checks"])

    def test_cross_city_leg_with_only_a_prose_disclosure_counts(self):
        """说明只写在 `advice` 里也算"如实说明"（2026-10-05 实测：判据只看结构化字段，
        明明是"如实说明未核实"，却被算成 0/2 段，周全性白扣分）。"""
        plan = self._plan(
            transport_audit={
                "legs": [
                    {"from": "库尔勒", "to": "乌鲁木齐", "advice": "这一段没有取到可用的出行方式（数据源未返回），票价与班次未核实"},
                ]
            }
        )
        report = score_completeness({"days": 3}, plan)
        self.assertTrue(report["checks"]["cross_city_transport"]["ok"], report["checks"]["cross_city_transport"])

    def test_cross_city_leg_with_a_structured_fare_status_counts(self):
        plan = self._plan(transport_audit={"legs": [{"from": "甲", "to": "乙", "fare_status": "unverified"}]})
        report = score_completeness({"days": 3}, plan)
        self.assertTrue(report["checks"]["cross_city_transport"]["ok"], report["checks"]["cross_city_transport"])

    def test_no_cross_city_leg_is_unknown_not_failure(self):
        report = score_completeness({"days": 3}, self._plan())
        self.assertEqual(report["checks"]["cross_city_transport"]["ok"], None)
        self.assertIn("cross_city_transport", report["unknown_checks"])


class IntegrationTests(unittest.TestCase):
    def test_evaluate_plan_includes_the_two_new_dimensions(self):
        from core.plan_quality import DEFAULT_WEIGHTS, evaluate_plan

        plan = {"route": [node("博斯腾湖风景名胜区", id="B1"), node("老字号拌面", time="12:30", type_text="餐饮服务;中餐厅")]}
        report = evaluate_plan({"days": 1}, plan)
        self.assertIn("place_quality", report["dimensions"])
        self.assertIn("completeness", report["dimensions"])
        self.assertIn("place_quality", DEFAULT_WEIGHTS)
        self.assertIn("completeness", DEFAULT_WEIGHTS)
        self.assertGreater(DEFAULT_WEIGHTS["place_quality"], 0)
        self.assertLessEqual(report["score"], 100.0)

    def test_snapshot_exposes_the_details_for_diagnostics(self):
        from core.plan_quality import plan_quality_snapshot

        plan = {"route": [node("漂亮的草原", id="B1")]}
        snapshot = plan_quality_snapshot({"days": 1}, plan)
        quality = snapshot["quality"]
        self.assertIn("place_quality", quality)
        self.assertIn("completeness", quality)
        self.assertTrue(quality["place_quality"]["review_required"], quality["place_quality"])


if __name__ == "__main__":
    unittest.main()
