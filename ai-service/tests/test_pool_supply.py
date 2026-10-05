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

    def test_agent_passes_play_worthy_false_for_lodging_widening(self):
        """源码级护栏：住宿补检索同样必须关掉玩点过滤（否则"补了却一条都没加回来"）。"""
        import io
        import pathlib

        source = pathlib.Path(__file__).resolve().parents[1] / "api" / "agent.py"
        with io.open(source, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("LODGING_WIDENING_KEYWORDS", text, "住宿补检索没接线")
        lodging_block = text.split("LODGING_WIDENING_KEYWORDS")[-1][:600]
        self.assertIn("play_worthy", lodging_block)
        self.assertIn("cleaned_hotels = list(cleaned_hotels) + _added_hotels", lodging_block)


class LodgingSupplyTests(unittest.TestCase):
    """住宿候选下限（2026-10-05 横向对照实测：伊宁 3 天方案一晚住宿都没有）。

    根因不是"伊宁没酒店"，而是生产关键词「品质酒店|高端度假酒店|高分精品住宿」在高德
    对伊宁直接回 `count=0`，而同一时刻「酒店」回 count=86 / 30 条 POI。
    """

    def test_lodging_bar_is_two_choices_per_night(self):
        from core.poi_pool import required_lodging_candidates

        self.assertEqual(required_lodging_candidates(2), 4)
        self.assertEqual(required_lodging_candidates(3), 6)
        self.assertEqual(required_lodging_candidates(0), 2, "哪怕不算夜数，也要至少 2 家可比")
        self.assertTrue(
            needs_widening(0, required_lodging_candidates(2)),
            "住宿候选为 0 是硬伤（伊宁真实事故），必须触发补检索",
        )
        self.assertTrue(needs_widening(1, required_lodging_candidates(1)), "只有 1 家没法挑")
        self.assertFalse(needs_widening(6, required_lodging_candidates(3)))

    def test_lodging_widening_keywords_cover_all_kinds_of_stays(self):
        from core.poi_pool import LODGING_WIDENING_KEYWORDS

        for keyword in ("酒店", "宾馆", "民宿", "客栈", "公寓"):
            self.assertIn(keyword, LODGING_WIDENING_KEYWORDS)
        # 实测口径：宽词检索"酒店"对伊宁回 30 条，而生产窄词回 0 条
        self.assertNotIn("品质", LODGING_WIDENING_KEYWORDS, "兜底词不能再窄回去")


class SubSpotCollapseTests(unittest.TestCase):
    """同一景区的子景点合并（2026-10-05 伊宁实测：同一天排了 5 个"天山花海xx"）。

    判据必须**同时**看名字前缀与位置：只按名字前缀合并会把真景区吃掉
    （实测：伊犁州候选 43 条被误并 16 条，路线质量反而崩了）。
    """

    def test_tian_shan_hua_hai_sub_spots_collapse_to_one(self):
        from core.poi_pool import collapse_sub_spots

        # 坐标是同一个景区的几个子点（互相 1~3km）
        items = [
            {"name": "天山花海·花田柳林", "lnglat": [81.90, 43.75], "type": "风景名胜;风景名胜相关;旅游景点"},
            {"name": "天山花海天山花园", "lnglat": [81.91, 43.76], "type": "风景名胜;风景名胜相关;旅游景点"},
            {"name": "天山花海旅游景区木屋营地", "lnglat": [81.92, 43.74], "type": "风景名胜;风景名胜相关;旅游景点"},
            {"name": "天山花海天泉餐厅", "lnglat": [81.90, 43.77], "type": "餐饮服务;餐饮相关场所;餐饮相关场所"},
            {"name": "天山花海天山乐园", "lnglat": [81.93, 43.75], "type": "风景名胜;公园广场;公园"},
        ]
        kept, merged = collapse_sub_spots(items)
        self.assertEqual(len(kept), 1, f"同一个景区只该留一个，实际 {[i['name'] for i in kept]}")
        self.assertEqual(merged, 4)

    def test_same_prefix_far_apart_stays_separate(self):
        """前缀相同但相距很远 = 两个地方（这条是"误并 16 个"的教训）。"""
        from core.poi_pool import collapse_sub_spots

        items = [
            {"name": "伊犁河旅游风景区", "lnglat": [81.30, 43.90]},
            {"name": "伊犁河谷草原", "lnglat": [83.50, 43.20]},  # 同前缀，相距上百公里
        ]
        kept, merged = collapse_sub_spots(items)
        self.assertEqual(merged, 0)
        self.assertEqual(len(kept), 2)

    def test_missing_coordinates_are_never_merged(self):
        from core.poi_pool import collapse_sub_spots

        kept, merged = collapse_sub_spots([{"name": "天山花海甲"}, {"name": "天山花海乙"}])
        self.assertEqual(merged, 0, "没有坐标就无法判断是不是同一个地方，宁可不合并")
        self.assertEqual(len(kept), 2)

    def test_different_places_are_not_merged(self):
        from core.poi_pool import collapse_sub_spots

        items = [
            {"name": "孔雀公园", "lnglat": [86.15, 41.75]},
            {"name": "孔雀河景观带", "lnglat": [86.16, 41.76]},
            {"name": "库尔勒市湿地公园", "lnglat": [86.20, 41.70]},
            {"name": "博斯腾湖风景名胜区", "lnglat": [86.75, 41.98]},
        ]
        kept, merged = collapse_sub_spots(items)
        self.assertEqual(merged, 0, f"不同地方不该被并掉，实际并掉 {merged}")
        self.assertEqual(len(kept), 4)

    def test_short_names_are_never_merged_away(self):
        from core.poi_pool import collapse_sub_spots

        kept, merged = collapse_sub_spots([{"name": "西湖"}, {"name": "东湖"}, {"name": "湖"}])
        self.assertEqual(merged, 0)
        self.assertEqual(len(kept), 3)

    def test_the_shortest_name_is_the_one_kept(self):
        from core.poi_pool import collapse_sub_spots

        kept, _merged = collapse_sub_spots(
            [
                {"name": "博斯腾湖风景名胜区芦花港", "lnglat": [86.75, 41.98]},
                {"name": "博斯腾湖风景名胜区", "lnglat": [86.76, 41.97]},
            ]
        )
        self.assertEqual([item["name"] for item in kept], ["博斯腾湖风景名胜区"])

    def test_the_main_scenic_area_beats_a_shorter_sub_spot(self):
        """实测（2026-10-05 伊宁）：「天山花海天山乐园」比「天山花海旅游度假区」名字短，
        只按长度选会把真正的景区顶掉，留下一个子点。"""
        from core.poi_pool import collapse_sub_spots

        kept, merged = collapse_sub_spots(
            [
                {"name": "天山花海天山乐园", "lnglat": [81.996847, 43.732182], "type": "风景名胜;风景名胜;风景名胜"},
                {"name": "天山花海旅游度假区", "lnglat": [81.989914, 43.706884], "type": "风景名胜;风景名胜;风景名胜"},
            ]
        )
        self.assertEqual(merged, 1)
        self.assertEqual([item["name"] for item in kept], ["天山花海旅游度假区"])

    def test_a_facility_or_hotel_is_never_the_representative(self):
        """实测：只按名字长度选，「图开沙漠酒店」会当上「图开沙漠旅游度假区」这组的代表。"""
        from core.poi_pool import collapse_sub_spots

        kept, merged = collapse_sub_spots(
            [
                {"name": "图开沙漠酒店", "lnglat": [80.793468, 44.020034], "type": "住宿服务;宾馆酒店;宾馆酒店"},
                {"name": "图开沙漠冰雪乐园", "lnglat": [80.809519, 44.020116], "type": "体育休闲服务;娱乐场所;游乐场"},
            ]
        )
        self.assertEqual(merged, 1)
        self.assertEqual([item["name"] for item in kept], ["图开沙漠冰雪乐园"])

    def test_real_sub_spots_of_tian_shan_hua_hai_all_go_to_the_main_area(self):
        """用探针抓到的**真实**高德数据（tools/probe_sub_spots.py）：24 条子点归到 1 个主景区。"""
        from core.poi_pool import collapse_sub_spots

        items = [
            {"name": "天山花海旅游度假区", "lnglat": [81.989914, 43.706884]},
            {"name": "天山花海天山乐园", "lnglat": [81.996847, 43.732182]},
            {"name": "天山花海天山花园", "lnglat": [81.999285, 43.734208]},
            {"name": "伊犁天山花海旅游景区停车场", "lnglat": [81.987972, 43.707341]},
            {"name": "天山花海旅游景区房车营地", "lnglat": [82.006137, 43.740688]},
            {"name": "天山花海·域见楠舍全景主题餐厅", "lnglat": [81.9963, 43.731788]},
            {"name": "天山花海景区公共厕所", "lnglat": [81.989026, 43.706819]},
            {"name": "天山花海旅游商品超市", "lnglat": [81.989776, 43.707131]},
            {"name": "天山花海·花田柳林", "lnglat": [81.991419, 43.743657]},
            {"name": "天鹅泉湿地公园", "lnglat": [81.649858, 43.699048]},
            {"name": "天鹅泉湿地公园北停车场", "lnglat": [81.63173, 43.707739]},
        ]
        kept, merged = collapse_sub_spots(items)
        names = [str(item["name"]) for item in kept]
        self.assertIn("天山花海旅游度假区", names)
        self.assertIn("天鹅泉湿地公园", names)
        self.assertNotIn("天山花海天山乐园", names)
        self.assertNotIn("天山花海景区公共厕所", names)
        self.assertEqual(merged, 8)
        self.assertEqual(len(kept), 3, names)

    def test_punctuation_does_not_break_the_prefix(self):
        from core.poi_pool import collapse_sub_spots

        kept, merged = collapse_sub_spots(
            [
                {"name": "天山花海·花田柳林", "lnglat": [81.90, 43.75]},
                {"name": "天山花海—天山花园", "lnglat": [81.91, 43.75]},
            ]
        )
        self.assertEqual(merged, 1)
        self.assertEqual(len(kept), 1)


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
            buckets, spare, targets={"food": 2, "scenic": 1}, classify=lambda item: str(item.get("kind"))
        )
        self.assertEqual(added.get("food"), 2, f"餐饮目标 2，实际补了 {added}")
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
        result, added = top_up_category_buckets(
            buckets, [], targets={"scenic": 4, "cultural": 2, "food": 2, "shopping": 1}, classify=lambda item: "food"
        )
        self.assertEqual(added, {})
        self.assertEqual(len(result["food"]), 2)

    def test_classifier_failure_does_not_break_the_pool(self):
        from core.poi_pool import top_up_category_buckets

        def broken(item):
            raise RuntimeError("分类器炸了")

        buckets = {"food": []}
        result, added = top_up_category_buckets(buckets, [{"name": "x"}], targets={"food": 1}, classify=broken)
        self.assertEqual(added, {})
        self.assertEqual(result["food"], [])

    def test_targets_come_from_the_user_facing_composition(self):
        """补齐目标必须跟用户口径一致（默认自然=人文各半、穿插美食），不是写死的常数。"""
        from core.poi_pool import top_up_category_buckets
        from core.route_composition import composition_targets

        targets = composition_targets(trip_days=2, daily_nodes=5)
        buckets = {"scenic": [], "cultural": [], "food": [], "shopping": []}
        spare = (
            [{"name": f"natural{i}", "kind": "scenic"} for i in range(5)]
            + [{"name": f"culture{i}", "kind": "cultural"} for i in range(5)]
            + [{"name": f"food{i}", "kind": "food"} for i in range(5)]
        )
        _, added = top_up_category_buckets(buckets, spare, targets=targets, classify=lambda item: str(item.get("kind")))
        self.assertEqual(added.get("scenic"), targets["scenic"])
        self.assertEqual(added.get("cultural"), targets["cultural"])
        self.assertEqual(added.get("food"), targets["food"])


if __name__ == "__main__":
    unittest.main()
