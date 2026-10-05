"""缓存复用判据的测试（2026-10-05）。

真实事故：日志里 `✅ [语义缓存] 精确命中 伊宁/3天` 之后，整条推演**照样跑满 70s** ——
因为复用判据自成一套（"每天都必须同时有景区和餐饮"），而系统自己产出的方案经常不满足它
（收口阶段会移出多余的远景点/节点）。结果缓存被事实上关掉：同一个请求每次都重花一次大模型的钱。

这里钉住正确口径：**用"这份旧方案当初是否通过了同一套门禁"来判断**，
只有老条目（没有门禁结论）才退回严格的结构判据。
"""

import os
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")

from core.cache_policy import cached_plan_decision  # noqa: E402


def _classify(name: str, type_text: str = "", is_hotel: bool = False) -> str:
    text = f"{name}{type_text}"
    if is_hotel or "住宿" in text or "酒店" in text:
        return "hotel"
    if "景区" in text or "公园" in text:
        return "scenic"
    if "餐厅" in text or "小吃" in text:
        return "food"
    if "博物馆" in text:
        return "cultural"
    return "other"


def _route_without_food_on_last_day():
    """系统真实能产出的形态：最后一天只有景区，没有餐饮（收口会把多余节点移出）。"""
    return [
        {"day": 1, "name": "草原遗迹景区", "type": "风景名胜"},
        {"day": 1, "name": "老字号餐厅", "type": "餐饮服务"},
        {"day": 2, "name": "天鹅泉景区", "type": "风景名胜"},
        {"day": 2, "name": "米粉店小吃", "type": "餐饮服务"},
        {"day": 3, "name": "伊犁河景区", "type": "风景名胜"},
        {"day": 3, "name": "某度假酒店", "type": "住宿服务", "is_hotel": True},
    ]


class CachePolicyTests(unittest.TestCase):
    def test_plan_that_passed_the_same_gates_is_reusable(self):
        payload = {
            "final_data": {
                "route": _route_without_food_on_last_day(),
                "daily_balance": {"ok": True, "category_balance": {"ok": True}},
                "composition": {"audit": {"ok": True}},
            }
        }
        decision = cached_plan_decision(
            payload=payload,
            route=payload["final_data"]["route"],
            days=3,
            daily_count=4,
            signals={"wants_scenic": True},
            classify=_classify,
            balance_ok=True,
        )
        self.assertTrue(decision["reusable"], decision)
        self.assertTrue(decision["has_recorded_gates"])
        self.assertIn("同一套判据", decision["reason"])

    def test_plan_that_failed_composition_is_not_reused(self):
        payload = {
            "final_data": {
                "route": _route_without_food_on_last_day(),
                "daily_balance": {"ok": True},
                "composition": {"audit": {"ok": False, "notes": ["人文 1 个，目标 3 个（偏少）"]}},
            }
        }
        decision = cached_plan_decision(
            payload=payload,
            route=payload["final_data"]["route"],
            days=3,
            daily_count=4,
            signals={},
            classify=_classify,
            balance_ok=True,
        )
        self.assertFalse(decision["reusable"])
        self.assertIn("composition", decision["reason"])

    def test_closeout_failure_blocks_reuse(self):
        payload = {
            "final_data": {
                "route": _route_without_food_on_last_day(),
                "daily_balance": {"ok": True},
                "hard_constraint_closeout": {"error": "NameError: x"},
            }
        }
        decision = cached_plan_decision(
            payload=payload,
            route=payload["final_data"]["route"],
            days=3,
            daily_count=4,
            signals={},
            classify=_classify,
            balance_ok=True,
        )
        self.assertFalse(decision["reusable"])
        self.assertIn("收口失败", decision["reason"])

    def test_supply_limited_composition_deviation_still_allows_reuse(self):
        """供给受限导致的配比偏差**不算方案问题**（已在方案里如实披露），不该因此重跑一遍模型。

        实测（2026-10-05）：库尔勒"人文候选 3 个 < 目标 4 个"被判"composition 没过" → 缓存被否，
        用户为"这座城市候选就这么点"多付一次 60s 的大模型调用。
        """
        payload = {
            "final_data": {
                "route": _route_without_food_on_last_day(),
                "daily_balance": {"satisfied": True, "category_balance": {"ok": True}},
                "composition": {
                    "audit": {
                        "ok": False,
                        "deviations": [{"intent": "cultural", "target": 4, "actual": 1, "severity": "under"}],
                    },
                    "supply_limited": {"cultural": {"target": 4, "available": 3}},
                },
            }
        }
        decision = cached_plan_decision(
            payload=payload,
            route=payload["final_data"]["route"],
            days=3,
            daily_count=4,
            signals={"wants_scenic": True},
            classify=_classify,
            balance_ok=True,
        )
        self.assertTrue(decision["reusable"], decision)
        self.assertIn("供给限制", decision["reason"])

    def test_non_supply_composition_deviation_still_blocks_reuse(self):
        payload = {
            "final_data": {
                "route": _route_without_food_on_last_day(),
                "daily_balance": {"satisfied": True},
                "composition": {
                    "audit": {
                        "ok": False,
                        "deviations": [{"intent": "food", "target": 3, "actual": 0, "severity": "under"}],
                    },
                    "supply_limited": {"cultural": {"target": 4, "available": 3}},
                },
            }
        }
        decision = cached_plan_decision(
            payload=payload,
            route=payload["final_data"]["route"],
            days=3,
            daily_count=4,
            signals={},
            classify=_classify,
            balance_ok=True,
        )
        self.assertFalse(decision["reusable"], decision)
        self.assertIn("composition", decision["reason"])

    def test_thin_day_is_disclosed_not_a_veto(self):
        """每日密度不足**不是**否决项（距离是硬约束、密度是软目标），但要记进 checks。

        实测（2026-10-05 喀什）：出稿前收口移出"一天跑不了"的远景点后某天少一个节点，
        缓存这边拿"密度达标"去卡 → 拒用 → 用户为同一个方案再等 84~188s，结果还是同样密度。
        """
        payload = {
            "final_data": {
                "route": _route_without_food_on_last_day(),
                "daily_balance": {"satisfied": False, "deficient_days": [3]},
            }
        }
        decision = cached_plan_decision(
            payload=payload,
            route=payload["final_data"]["route"],
            days=3,
            daily_count=4,
            signals={},
            classify=_classify,
            balance_ok=False,
        )
        self.assertTrue(decision["reusable"], decision)
        self.assertIs(decision["checks"]["recorded_gates"].get("daily_balance"), False)
        self.assertIs(decision["checks"]["balance"], False)

    def test_legacy_entry_without_gates_falls_back_to_strict_rule(self):
        payload = {"final_data": {"route": _route_without_food_on_last_day()}}
        decision = cached_plan_decision(
            payload=payload,
            route=payload["final_data"]["route"],
            days=3,
            daily_count=4,
            signals={"wants_scenic": True},
            classify=_classify,
            balance_ok=True,
        )
        self.assertFalse(decision["reusable"], "老条目没有门禁结论时，第 3 天缺餐饮要拒")
        self.assertFalse(decision["has_recorded_gates"])
        self.assertEqual(decision["checks"]["days_missing_scenic_or_food"], [3])

    def test_legacy_entry_is_accepted_when_it_is_structurally_complete(self):
        route = _route_without_food_on_last_day()
        route.append({"day": 3, "name": "拌面王餐厅", "type": "餐饮服务"})
        payload = {"final_data": {"route": route}}
        decision = cached_plan_decision(
            payload=payload,
            route=route,
            days=3,
            daily_count=4,
            signals={},
            classify=_classify,
            balance_ok=True,
        )
        self.assertTrue(decision["reusable"], decision)

    def test_culture_only_request_skips_the_daily_scenic_food_rule(self):
        route = [{"day": 1, "name": "喀什博物馆", "type": "博物馆"}, {"day": 2, "name": "百年喀什馆", "type": "博物馆"}]
        payload = {"final_data": {"route": route}}
        decision = cached_plan_decision(
            payload=payload,
            route=route,
            days=2,
            daily_count=2,
            signals={"wants_cultural": True},
            classify=_classify,
            balance_ok=True,
        )
        self.assertTrue(decision["reusable"], decision)

    def test_empty_route_is_never_reused(self):
        decision = cached_plan_decision(
            payload={"final_data": {"route": []}},
            route=[],
            days=2,
            daily_count=4,
            signals={},
            classify=_classify,
            balance_ok=True,
        )
        self.assertFalse(decision["reusable"])


class DailyDensityDisclosureTests(unittest.TestCase):
    """每日密度不足要如实披露（2026-10-05）：不是"少排了"，而是"一天跑不了那么多远的地方"。"""

    def test_recomputed_thin_days_are_disclosed(self):
        from core.degradation import collect_degradations

        payload = {
            "route": [{"day": 1, "name": "某景区", "type": "风景名胜"}],
            "daily_balance": {
                "recomputed_after_closeout": True,
                "satisfied": False,
                "deficient_days": [2, 3],
            },
        }
        items = collect_degradations(payload).get("items", [])
        entry = next((item for item in items if item.get("source") == "daily_balance"), None)
        self.assertIsNotNone(entry, items)
        self.assertIn("第 2、3 天", str(entry.get("reason")))
        self.assertIn("一天跑不了那么多远的地方", str(entry.get("reason")))

    def test_density_is_quiet_when_it_was_not_recomputed(self):
        from core.degradation import collect_degradations

        payload = {"daily_balance": {"satisfied": False, "deficient_days": [2]}}
        items = collect_degradations(payload).get("items", [])
        self.assertFalse([item for item in items if item.get("source") == "daily_balance"])


if __name__ == "__main__":
    unittest.main()
