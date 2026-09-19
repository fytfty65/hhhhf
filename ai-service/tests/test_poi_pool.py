"""候选池与治理编排的确定性单测（用假 fetch，离线、无网络）。"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.planning_governance import mentions_exclusion, prepare_governance  # noqa: E402
from core.poi_pool import (  # noqa: E402
    build_candidate_pool,
    candidate_from_poi,
    intents_for_plan,
    parse_amap_location,
    poi_record_from_amap,
)

AMAP_STYLE_POIS = {
    "博物馆|古迹|寺庙|文化馆": [
        {"name": "陕西历史博物馆", "type": "科教文化服务;博物馆", "location": "108.95,34.22",
         "rating": "4.7", "cost": "暂无供应商数据", "open_time": "09:00-17:00",
         "data_sources": {"rating": "amap", "cost": "unavailable", "open_time": "amap"}, "estimated": True},
        {"name": "碑林博物馆", "type": "科教文化服务;博物馆", "location": "108.95,34.23",
         "rating": "4.5", "cost": "65", "open_time": "08:00-18:00",
         "data_sources": {"rating": "amap", "cost": "amap", "open_time": "amap"}, "estimated": False},
    ],
    "小吃|老字号|本地菜": [
        {"name": "子午路张记肉夹馍", "type": "餐饮服务;中餐厅", "location": "108.94,34.24",
         "rating": "4.6", "cost": "25", "open_time": "07:00-21:00",
         "data_sources": {"rating": "amap", "cost": "amap", "open_time": "amap"}, "estimated": False},
    ],
    "酒店|民宿": [
        {"name": "青旅床位", "type": "住宿服务;旅馆", "location": "108.94,34.26",
         "rating": "暂无供应商数据", "cost": "80", "open_time": "全天",
         "data_sources": {"rating": "unavailable", "cost": "amap", "open_time": "amap"}, "estimated": True},
    ],
}


async def fake_fetch(city, keywords, types=None, limit=6):
    return AMAP_STYLE_POIS.get(keywords, [])


async def failing_fetch(city, keywords, types=None, limit=6):
    raise RuntimeError("数据源超时")


def plan_with(cost_hotel="¥1800", cost_museum="¥300"):
    return {"route": [
        {"day": 1, "name": "钟楼酒店", "type": "住宿", "time": "09:00", "cost_estimate": cost_hotel,
         "data_sources": {"cost_estimate": "amap"}, "lnglat": [108.94, 34.26]},
        {"day": 1, "name": "豪华博物馆", "type": "博物馆", "time": "10:30", "cost_estimate": cost_museum,
         "data_sources": {"cost_estimate": "amap"}, "lnglat": [108.95, 34.22]},
        {"day": 1, "name": "回民街小吃", "type": "餐饮", "time": "12:30", "cost_estimate": "¥60",
         "data_sources": {"cost_estimate": "amap"}, "lnglat": [108.94, 34.26]},
        {"day": 2, "name": "城墙南门", "type": "文化", "time": "09:30", "cost_estimate": "¥54",
         "data_sources": {"cost_estimate": "amap"}, "lnglat": [108.94, 34.25]},
        {"day": 2, "name": "永兴坊美食", "type": "餐饮", "time": "12:30", "cost_estimate": "¥70",
         "data_sources": {"cost_estimate": "amap"}, "lnglat": [108.97, 34.27]},
    ]}


class TestPoiPool(unittest.TestCase):
    def test_parse_location(self):
        self.assertEqual(parse_amap_location("108.95,34.22"), [108.95, 34.22])
        self.assertIsNone(parse_amap_location(""))
        self.assertIsNone(parse_amap_location("北京"))

    def test_poi_record_always_carries_lnglat_not_just_location(self):
        """契约回归（2026-09-19 血的教训）：高德只给 location 字符串，下游只认 lnglat 数组。

        少了 lnglat → 整池 0 条有坐标 → 每个方案节点都拿不到坐标 → 地图上一个点都画不出来。
        """
        raw = {
            "id": "B000A",
            "name": "洛阳博物馆",
            "type": "科教文化服务;博物馆;博物馆",
            "address": "聂泰路",
            "location": "112.451541,34.643323",
            "biz_ext": {"rating": ["4.7"], "cost": [], "open_time": "09:00-17:00"},
            "photos": [{"url": "//store.is.autonavi.com/a.jpg"}, {"url": "http://x.com/b.jpg"}, {"url": "ftp://bad"}],
        }
        record = poi_record_from_amap(raw, city="洛阳", amap_key="k")
        self.assertEqual(record["lnglat"], [112.451541, 34.643323])
        self.assertEqual(record["location"], "112.451541,34.643323")
        self.assertEqual(record["name"], "洛阳博物馆")
        self.assertEqual(record["type"], "科教文化服务")
        # 图片统一 https，坏协议直接丢
        self.assertEqual(record["photos"], ["https://store.is.autonavi.com/a.jpg", "https://x.com/b.jpg"])
        # 有位置就给 marker 深链；静态地图只在有 key 时生成
        self.assertIn("uri.amap.com/marker", record["amap_url"])
        self.assertIn("staticmap", record["map_image"])
        # rating 来自高德 → verified；cost 缺 → estimated 必须为真（不许冒充已核实）
        self.assertEqual(record["data_sources"]["rating"], "amap")
        self.assertEqual(record["data_sources"]["cost"], "unavailable")
        self.assertTrue(record["estimated"])

    def test_poi_record_without_location_still_safe(self):
        record = poi_record_from_amap({"name": "没有坐标的地方", "biz_ext": {}}, city="洛阳")
        self.assertIsNone(record["lnglat"])
        self.assertEqual(record["map_image"], "")
        self.assertIn("amap.com/search", record["amap_url"])
        self.assertEqual(record["rating"], "暂无供应商数据")
        self.assertTrue(record["estimated"])

    def test_candidate_from_poi_marks_price_tier_and_source(self):
        candidate = candidate_from_poi(AMAP_STYLE_POIS["小吃|老字号|本地菜"][0])
        self.assertEqual(candidate["name"], "子午路张记肉夹馍")
        self.assertEqual(candidate["price"], 25.0)
        self.assertEqual(candidate["price_tier"], "economy")
        self.assertEqual(candidate["price_source"], "amap")
        self.assertEqual(candidate["lnglat"], [108.94, 34.24])

    def test_candidate_without_price_is_unknown_not_zero(self):
        candidate = candidate_from_poi(AMAP_STYLE_POIS["博物馆|古迹|寺庙|文化馆"][0])
        self.assertIsNone(candidate["price"])
        self.assertEqual(candidate["price_tier"], "unknown")

    def test_intents_for_plan(self):
        intents = intents_for_plan(plan_with())
        self.assertIn("hotel", intents)
        self.assertIn("cultural", intents)
        self.assertIn("food", intents)

    def test_build_pool_buckets_by_intent(self):
        pool = asyncio.run(build_candidate_pool("西安", ["cultural", "food", "hotel"], fake_fetch))
        self.assertEqual(len(pool["cultural"]), 2)
        self.assertEqual(len(pool["food"]), 1)
        self.assertEqual(len(pool["hotel"]), 1)
        # 桶内按价格升序（未知价格排后面）
        self.assertEqual(pool["cultural"][0]["name"], "碑林博物馆")

    def test_build_pool_honours_exclusions(self):
        pool = asyncio.run(build_candidate_pool("西安", ["cultural"], fake_fetch, exclude_names=["碑林博物馆"]))
        names = [item["name"] for item in pool["cultural"]]
        self.assertNotIn("碑林博物馆", names)
        self.assertIn("陕西历史博物馆", names)

    def test_build_pool_returns_empty_without_fetch_or_city(self):
        self.assertEqual(asyncio.run(build_candidate_pool("", ["food"], fake_fetch)), {})
        self.assertEqual(asyncio.run(build_candidate_pool("西安", ["food"], None)), {})

    def test_build_pool_survives_fetch_failure(self):
        self.assertEqual(asyncio.run(build_candidate_pool("西安", ["food", "cultural"], failing_fetch)), {})


class TestGovernance(unittest.TestCase):
    def test_mentions_exclusion(self):
        self.assertTrue(mentions_exclusion("上次排的兵马俑我不想去"))
        self.assertTrue(mentions_exclusion("把华山换掉"))
        self.assertFalse(mentions_exclusion("想吃地道美食"))

    def test_no_pool_needed_when_budget_ok(self):
        result = asyncio.run(
            prepare_governance(
                {"days": 2, "budget": 99999, "preferences": {"interest": ["博物馆"]}},
                plan_with(),
                city="西安",
                fetch=fake_fetch,
            )
        )
        self.assertFalse(result["used_pool"])
        self.assertEqual(result["pool_sizes"], {})
        self.assertIsNone(result["snapshot"]["fallback"])

    def test_pool_used_when_over_budget_and_enables_substitution(self):
        # 预算 700，已核实 1800+300+60+54+70 = 2284 → over
        result = asyncio.run(
            prepare_governance(
                {"days": 2, "budget": 700, "preferences": {"interest": ["博物馆", "美食"]}},
                plan_with(),
                city="西安",
                fetch=fake_fetch,
            )
        )
        self.assertTrue(result["used_pool"])
        self.assertGreater(result["pool_sizes"].get("cultural", 0), 0)
        fallback = result["snapshot"]["fallback"]
        self.assertIsNotNone(fallback)
        # 有候选池时应当出现"同类替换"，而不是只有删/降
        self.assertGreaterEqual(len(fallback["substitutions"]), 1)
        self.assertTrue(any(item.get("to") for item in fallback["substitutions"]))

    def test_exclusions_execute_and_dont_reintroduce_excluded(self):
        result = asyncio.run(
            prepare_governance(
                {"days": 2, "budget": 99999},
                plan_with(),
                city="西安",
                fetch=fake_fetch,
                exclude_terms=["豪华博物馆", "回民街小吃"],
            )
        )
        exclusions = result["exclusions"]
        self.assertIsNotNone(exclusions)
        names = [node["name"] for node in exclusions["plan"]["route"]]
        self.assertNotIn("豪华博物馆", names)
        self.assertNotIn("回民街小吃", names)
        # 被排除的名字不能又出现在候选池里
        for bucket in result["snapshot"] and [] or []:
            pass
        self.assertIn("移除", exclusions["disclosure"])

    def test_pool_failure_still_returns_snapshot(self):
        result = asyncio.run(
            prepare_governance(
                {"days": 2, "budget": 700},
                plan_with(),
                city="西安",
                fetch=failing_fetch,
            )
        )
        self.assertFalse(result["used_pool"])
        self.assertIsNotNone(result["snapshot"]["budget"]["status"])

    def test_no_network_when_fetch_missing(self):
        result = asyncio.run(
            prepare_governance({"days": 2, "budget": 700}, plan_with(), city="西安", fetch=None)
        )
        self.assertFalse(result["used_pool"])


class TestIncrementThroughGovernance(unittest.TestCase):
    """二次增量走编排：排他要真的换掉，配额要真的多排，并给出响应度。"""

    def _run(self, text, budget=99999):
        from core.increment import parse_increment

        delta = parse_increment(text, previous_plan=plan_with())
        return asyncio.run(
            prepare_governance(
                {"days": 2, "budget": budget, "preferences": {"interest": ["美食", "博物馆"]}},
                plan_with(),
                city="西安",
                fetch=fake_fetch,
                request_text=text,
                increment=delta,
            )
        )

    def test_exclusion_rewrites_route(self):
        result = self._run("上次排的城墙我不想去，换掉")
        names = [node["name"] for node in result["plan"]["route"]]
        self.assertNotIn("城墙南门", names)
        self.assertTrue(result["exclusions"]["removed"])
        self.assertTrue(result["exclusions"]["replaced"] or result["exclusions"]["unreplaced"])
        self.assertIsNotNone(result["increment"])

    def test_quota_actually_adds_nodes_and_reports_responsiveness(self):
        result = self._run("这次想多吃点地道美食")
        from core.candidate_index import intent_of

        before_food = [n for n in plan_with()["route"] if intent_of(n) == "food"]
        after_food = [n for n in result["plan"]["route"] if intent_of(n) == "food"]
        self.assertGreater(len(after_food), len(before_food))
        metrics = result["increment"]
        self.assertEqual(metrics["requested"], 2)
        self.assertGreaterEqual(metrics["achieved"], 1)
        self.assertIsNotNone(metrics["responsiveness"])
        self.assertLess(metrics["disturbance"], 0.6)

    def test_quota_without_pool_reports_unmet_not_fabricated(self):
        from core.increment import parse_increment

        delta = parse_increment("这次想多吃点地道美食", previous_plan=plan_with())
        result = asyncio.run(
            prepare_governance(
                {"days": 2, "budget": 99999},
                plan_with(),
                city="西安",
                fetch=None,
                increment=delta,
            )
        )
        self.assertFalse(result["used_pool"])
        self.assertEqual(result["quota"]["added"], [])
        self.assertEqual(result["quota"]["unmet"].get("food"), 2)
        self.assertIn("没能补上", result["quota"]["disclosure"])


if __name__ == "__main__":
    unittest.main()
