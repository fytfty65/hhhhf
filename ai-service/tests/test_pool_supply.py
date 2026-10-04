"""候选供给测试：太薄要补检索，而且要如实告诉用户"哪些地点不是数据源来的"。

真实缺口（2026-10-04 实测）：
  1. 库尔勒景点查询被高德 QPS 限流 → status=0 → 生产候选只剩 1 个，
     用户看到的方案却"看起来挺全"（其余地点来自模型知识）；
  2. 库尔勒美食用生产关键词只回 3 条，通用兜底「美食|餐厅|小吃|本地菜」回 40 条 ——
     关键词太窄，候选池白白变小。
所以：候选太薄就补一次更宽的检索；补不齐也要在方案里说清楚。
"""

import unittest

from core.degradation import collect_degradations
from core.poi_pool import (
    FOOD_WIDENING_KEYWORDS,
    SCENIC_WIDENING_KEYWORDS,
    needs_widening,
    pool_supply_report,
    required_daytime_slots,
    required_food_candidates,
    required_scenic_candidates,
)


class RequiredSlotsTests(unittest.TestCase):
    def test_required_slots_is_days_times_nodes(self):
        self.assertEqual(required_daytime_slots(2, 4), 8)
        self.assertEqual(required_daytime_slots(0, 0), 1, "空输入按最少 1 天 1 个处理，不返回 0")

    def test_needs_widening_when_not_even_one_per_day(self):
        self.assertTrue(needs_widening(1, required_scenic_candidates(2)), "2 天只有 1 个自然候选 → 该补")
        self.assertTrue(needs_widening(0, required_scenic_candidates(2)))
        self.assertFalse(needs_widening(2, required_scenic_candidates(2)))
        self.assertFalse(needs_widening(9, required_scenic_candidates(3)))
        self.assertTrue(needs_widening(1, required_scenic_candidates(1)), "至少要有 2 个才能比较，1 个就补")

    def test_food_bar_is_two_meals_a_day(self):
        """真正的事故口径：库尔勒餐饮只有 3 条时**必须**补检索。

        上一版阈值用"少于天数"（3 ≥ 2 → 不补），结果模型只能自己编馆子，
        有两次真实路线里一个餐饮节点都没有。
        """
        self.assertEqual(required_food_candidates(2), 4)
        self.assertEqual(required_food_candidates(4), 8)
        self.assertEqual(required_food_candidates(1), 4, "一天也要至少 4 个候选才有得挑")
        self.assertTrue(needs_widening(3, required_food_candidates(2)), "3 条餐饮候选不足以支撑 2 天行程")
        self.assertTrue(needs_widening(3, required_food_candidates(1)))
        self.assertFalse(needs_widening(4, required_food_candidates(2)))
        self.assertFalse(needs_widening(40, required_food_candidates(2)))


class SupplyReportTests(unittest.TestCase):
    def test_ok_when_enough_real_candidates(self):
        report = pool_supply_report(attractions=12, foods=8, trip_days=2, daily_nodes=4, scenic=5)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["known_daytime"], 20)
        self.assertEqual(report["needed_daytime"], 8)
        self.assertEqual(report["notes"], [])

    def test_thin_when_fewer_than_needed(self):
        report = pool_supply_report(attractions=2, foods=3, trip_days=2, daily_nodes=4, scenic=1)
        self.assertEqual(report["status"], "thin")
        self.assertTrue(any("少于排满" in note for note in report["notes"]), report["notes"])

    def test_missing_when_no_attraction_at_all(self):
        """一个真实景点都没有：必须说"以模型知识为主"，不能只说"候选偏少"。"""
        report = pool_supply_report(attractions=0, foods=3, trip_days=2, daily_nodes=4)
        self.assertEqual(report["status"], "missing")
        self.assertTrue(any("模型知识" in note for note in report["notes"]), report["notes"])

    def test_thin_when_no_scenic_even_if_pool_is_large(self):
        """候选够多但一个自然点都没有：对"想看自然风景"的诉求等于没满足。"""
        report = pool_supply_report(attractions=20, foods=10, trip_days=2, daily_nodes=4, scenic=0)
        self.assertEqual(report["status"], "thin")
        self.assertTrue(any("自然" in note for note in report["notes"]), report["notes"])

    def test_widening_is_disclosed(self):
        report = pool_supply_report(
            attractions=6, foods=9, trip_days=2, daily_nodes=4, widened=["自然景观", "美食"]
        )
        self.assertIn("自然景观", report["widened"])
        self.assertTrue(any("补了一次更宽的检索" in note for note in report["notes"]), report["notes"])

    def test_provider_error_note_is_carried_through(self):
        report = pool_supply_report(
            attractions=3, foods=9, trip_days=1, daily_nodes=3, provider_notes=["高德限流，本轮候选可能不完整"]
        )
        self.assertIn("高德限流，本轮候选可能不完整", report["notes"])

    def test_widening_keywords_are_broad_enough_to_find_real_scenery(self):
        for keyword in ("景点", "景区", "公园", "湖", "草原", "沙漠", "峡谷"):
            self.assertIn(keyword, SCENIC_WIDENING_KEYWORDS)
        self.assertIn("美食", FOOD_WIDENING_KEYWORDS)
        self.assertIn("小吃", FOOD_WIDENING_KEYWORDS)


class SupplyDegradationTests(unittest.TestCase):
    def test_thin_supply_shows_up_in_degradations(self):
        payload = {"candidate_supply": pool_supply_report(attractions=1, foods=2, trip_days=2, daily_nodes=4)}
        degradations = collect_degradations(payload)
        sources = [item["source"] for item in degradations.get("items", [])]
        self.assertIn("candidate_pool", sources)
        detail = " ".join(str(item.get("reason") or "") for item in degradations.get("items", []))
        self.assertTrue("模型知识" in detail or "少于排满" in detail, detail)

    def test_missing_supply_is_marked_missing_not_degraded(self):
        payload = {"candidate_supply": pool_supply_report(attractions=0, foods=0, trip_days=1, daily_nodes=3)}
        items = collect_degradations(payload).get("items", [])
        entry = next((item for item in items if item["source"] == "candidate_pool"), None)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["status"], "missing")

    def test_ok_supply_adds_nothing(self):
        payload = {"candidate_supply": pool_supply_report(attractions=12, foods=9, trip_days=2, daily_nodes=4, scenic=3)}
        items = collect_degradations(payload).get("items", [])
        self.assertFalse([item for item in items if item["source"] == "candidate_pool"])


class WidenCandidatesTests(unittest.TestCase):
    """补检索助手：去重 / 失败不抛 / 只补一次。用假 fetch 做确定性覆盖。"""

    def _run(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_adds_only_new_names(self):
        from core.poi_pool import widen_candidates

        async def fake_fetch(city, *, keywords, types, limit):
            self.assertEqual(city, "库尔勒")
            self.assertIn("景点", keywords)
            return [
                {"name": "天鹅河景区"},
                {"name": "孔雀公园"},  # 已有同名 → 不该重复加入
                {"name": "  "},  # 空名字 → 丢弃
                "不是字典",  # 结构不合法 → 丢弃
            ]

        result = self._run(
            widen_candidates(
                fake_fetch, city="库尔勒", keywords=SCENIC_WIDENING_KEYWORDS, types="110000", limit=30,
                existing_names=["孔雀公园"],
            )
        )
        self.assertEqual(result["added_count"], 1)
        self.assertEqual(result["added"][0]["name"], "天鹅河景区")
        self.assertEqual(result["error"], "")
        self.assertIsInstance(result["added"][0], dict)

    def test_failure_is_reported_not_raised(self):
        from core.poi_pool import widen_candidates

        async def broken_fetch(city, *, keywords, types, limit):
            raise RuntimeError("amap 500")

        result = self._run(
            widen_candidates(broken_fetch, city="库尔勒", keywords="景点", existing_names=[])
        )
        self.assertEqual(result["added"], [])
        self.assertEqual(result["added_count"], 0)
        self.assertIn("amap 500", result["error"])

    def test_empty_result_is_not_an_error(self):
        from core.poi_pool import widen_candidates

        async def empty_fetch(city, *, keywords, types, limit):
            return []

        result = self._run(widen_candidates(empty_fetch, city="库尔勒", keywords="美食", existing_names=["老店"]))
        self.assertEqual(result["added_count"], 0)
        self.assertEqual(result["error"], "")

    def test_fetch_kwargs_are_forwarded(self):
        """真实事故：餐饮补检索漏传 play_worthy=False → 餐饮被"必须是玩点"整类丢掉，
        返回空列表还一声不响。参数透传必须有测试钉死。"""
        from core.poi_pool import widen_candidates

        captured = {}

        async def recording_fetch(city, *, keywords, types, limit, **kwargs):
            captured.update({"city": city, "keywords": keywords, "types": types, "limit": limit, **kwargs})
            return [{"name": "新店"}]

        result = self._run(
            widen_candidates(
                recording_fetch,
                city="库尔勒",
                keywords=FOOD_WIDENING_KEYWORDS,
                types="050000",
                limit=40,
                existing_names=[],
                fetch_kwargs={"play_worthy": False},
            )
        )
        self.assertEqual(captured.get("play_worthy"), False, f"取数参数必须透传，实际 {captured}")
        self.assertEqual(captured.get("keywords"), FOOD_WIDENING_KEYWORDS)
        self.assertEqual(result["added_count"], 1)

    def test_agent_passes_play_worthy_false_for_food_widening(self):
        """源码级护栏：餐饮补检索那处必须带 play_worthy=False。"""
        import io
        import pathlib

        source = pathlib.Path(__file__).resolve().parents[1] / "api" / "agent.py"
        with io.open(source, encoding="utf-8") as handle:
            text = handle.read()
        food_block = text.split("FOOD_WIDENING_KEYWORDS")[-1][:400]
        self.assertIn("play_worthy", food_block, "餐饮补检索必须显式关掉玩点过滤")


class CategoryFloorTests(unittest.TestCase):
    """类别下限兜底：真实缺口是"餐饮 50 个候选，top 里 0 个"。"""

    def test_floors_grow_with_trip_length(self):
        from core.poi_pool import category_floor

        self.assertEqual(category_floor("scenic", 1), 2)
        self.assertEqual(category_floor("scenic", 4), 4)
        self.assertEqual(category_floor("food", 1), 2)
        self.assertEqual(category_floor("food", 3), 3)
        self.assertEqual(category_floor("cultural", 3), 2, "人文是点缀，不该要求太多")
        self.assertEqual(category_floor("shopping", 5), 1)
        self.assertEqual(category_floor("other", 5), 0)

    def test_food_is_topped_up_from_spare_pool(self):
        from core.poi_pool import top_up_category_buckets

        buckets = {"scenic": [{"name": "孔雀公园"}], "cultural": [{"name": "州博物馆"}], "food": [], "shopping": []}
        spare = [
            {"name": "孔雀公园", "kind": "scenic"},  # 已在桶里 → 不重复
            {"name": "民杨抓饭", "kind": "food"},
            {"name": "疆土印象新疆菜", "kind": "food"},
            {"name": "醉美博湖鱼宴", "kind": "food"},
        ]
        result, added = top_up_category_buckets(
            buckets, spare, trip_days=2, classify=lambda item: str(item.get("kind"))
        )
        self.assertEqual(added.get("food"), 2, f"2 天行程餐饮下限 2，实际补了 {added}")
        self.assertEqual([item["name"] for item in result["food"]], ["民杨抓饭", "疆土印象新疆菜"])
        self.assertEqual(len(result["scenic"]), 1, "已满足下限的类别不该被加东西")

    def test_no_topup_when_buckets_already_meet_floor(self):
        from core.poi_pool import top_up_category_buckets

        buckets = {
            "scenic": [{"name": "a"} for _ in range(4)],
            "cultural": [{"name": "b"}, {"name": "c"}],
            "food": [{"name": "d"}, {"name": "e"}],
            "shopping": [{"name": "f"}],
        }
        result, added = top_up_category_buckets(buckets, [], trip_days=2, classify=lambda item: "food")
        self.assertEqual(added, {})
        self.assertEqual(len(result["food"]), 2)

    def test_classifier_failure_does_not_break_the_pool(self):
        from core.poi_pool import top_up_category_buckets

        def broken(item):
            raise RuntimeError("分类器炸了")

        buckets = {"food": []}
        result, added = top_up_category_buckets(buckets, [{"name": "x"}], trip_days=1, classify=broken)
        self.assertEqual(added, {})
        self.assertEqual(result["food"], [])


if __name__ == "__main__":
    unittest.main()
