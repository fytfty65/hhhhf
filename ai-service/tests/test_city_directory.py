"""城市日录当名字解析器的测试（2026-10-07）。

实测背景：方案节点的名字常常对不上平台标准名 —— 伊宁 7 个景点里"严格挑名"只命中 1 个，
用宽容匹配（`_normalize` 后互相包含）能对上 7 个。所以加一步：查不到就用城市日录换名再查。
但**换名不等于有价**：日录条目本身常常没有 price 块，拿不到就如实说。这里把两步都钉住。
"""

import asyncio
import os
import sys
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import ticket_platforms  # noqa: E402
from core.ticket_platforms import lookup_platform_price, lookup_qunar_price, reset_city_directory  # noqa: E402

CITY_LIST = {
    "ret": True,
    "data": {
        "list": [
            {"sightName": "伊宁县天鹅泉湿地公园", "sightId": 180076650},
            {"sightName": "图开沙漠景区", "sightId": 4129757019},
        ]
    },
}
# 用"平台标准名"回查时，才带出 price 块（这正是两段式的意义）
PRICED_BY_STANDARD_NAME = {
    "伊宁县天鹅泉湿地公园": {
        "sightName": "伊宁县天鹅泉湿地公园",
        "sightId": 180076650,
        "price": {"sightPrice": {"priceList": [{"typeName": "天鹅泉湿地公园门票成人票", "qunarPrice": "30"}]}},
    }
}


class CityDirectoryTests(unittest.TestCase):
    def setUp(self):
        reset_city_directory()

    def test_renamed_query_finds_the_price(self):
        """方案名是"天鹅泉湿地公园"，平台标准名是"伊宁县天鹅泉湿地公园" → 换名后才拿到价。"""

        async def fake_json(url, params):
            keyword = str((params or {}).get("keyword") or "")
            if keyword.startswith("伊宁 "):
                return CITY_LIST
            if keyword == "伊宁县天鹅泉湿地公园":
                return {"ret": True, "data": {"list": [PRICED_BY_STANDARD_NAME["伊宁县天鹅泉湿地公园"]]}}
            if keyword == "天鹅泉湿地公园":
                return {"ret": True, "data": {"list": [{"sightName": "伊宁县天鹅泉湿地公园", "sightId": 180076650}]}}
            return {"ret": True, "data": {"list": []}}

        observation = asyncio.run(
            lookup_qunar_price("天鹅泉湿地公园", city="伊宁", fetch_json=fake_json)
        )
        self.assertEqual(observation["value"], 30.0)
        self.assertEqual(observation["provider"], "qunar")
        self.assertIn("city_directory", str(observation.get("matched_via")), observation)
        self.assertIn("门票成人票", observation["excerpt"])

    def test_directory_is_fetched_once_per_city(self):
        calls = []

        async def fake_json(url, params):
            calls.append(str((params or {}).get("keyword") or ""))
            return CITY_LIST

        async def run():
            await ticket_platforms.qunar_city_directory("伊宁", fetch_json=fake_json)
            await ticket_platforms.qunar_city_directory("伊宁", fetch_json=fake_json)

        asyncio.run(run())
        self.assertEqual(len(calls), 3, f"同一个城市只该拉一轮（3 个关键词）：{calls}")

    def test_no_price_after_renaming_is_reported_honestly(self):
        async def fake_json(url, params):
            keyword = str((params or {}).get("keyword") or "")
            if keyword.startswith("伊宁 "):
                return {"ret": True, "data": {"list": [{"sightName": "图开沙漠景区", "sightId": 1}]}}
            return {"ret": True, "data": {"list": [{"sightName": "图开沙漠景区", "sightId": 1}]}}

        observation = asyncio.run(lookup_qunar_price("图开沙漠冰雪乐园", city="伊宁", fetch_json=fake_json))
        self.assertIsNone(observation["value"])
        self.assertTrue(observation.get("platform_checked"))
        self.assertIn("门票", observation["note"])

    def test_platform_entry_point_still_falls_back_to_ctrip(self):
        async def fake_json(url, params):
            keyword = str((params or {}).get("keyword") or "")
            if "qunar" in url:
                return {"ret": True, "data": {"list": []}}
            return {
                "data": [
                    {"id": 1, "code": "sight_1", "word": "天鹅泉湿地公园",
                     "url": "http://you.ctrip.com/sight/tianeque1446400/1.html"}
                ]
            }

        async def fake_page(url, **kwargs):
            return {"ok": True, "text": "天鹅泉湿地公园 门票30元/人"}

        observation = asyncio.run(
            lookup_platform_price("天鹅泉湿地公园", city="伊宁", fetch_json=fake_json, fetch_page=fake_page)
        )
        self.assertEqual(observation["value"], 30.0)
        self.assertEqual(observation["provider"], "ctrip")


class CityAwareMatchingTests(unittest.TestCase):
    """名字归一化会退化成都市名，必须用**城市名以外的内容**比对（2026-10-07 实跑抓到的错配）。

    实测：库尔勒一轮里「库尔勒市湿地公园」拿到了「库尔勒体育公园门票成人票 ¥28」，
    两个节点的 qunar sightId 与来源 URL **完全相同** —— 根因是
    `_normalize("库尔勒市湿地公园")` = "库尔勒"（通词"湿地公园"被去掉后只剩城市名），
    而它正好是 "库尔勒体育" 的子串。
    """

    ENTRIES = [
        {"sightName": "库尔勒体育公园", "sightId": 1},
        {"sightName": "伊宁县天鹅泉湿地公园", "sightId": 2},
        {"sightName": "博斯腾湖", "sightId": 3},
        {"sightName": "喀什地区喀什古城景区", "sightId": 4},
    ]

    def test_a_name_that_degrades_to_the_city_name_does_not_match(self):
        from core.ticket_platforms import pick_sight

        self.assertIsNone(
            pick_sight(self.ENTRIES, "库尔勒市湿地公园", city="库尔勒"),
            "去掉城市名后什么都不剩，就不该匹配（否则会把体育公园的票安给湿地公园）",
        )

    def test_real_names_still_match(self):
        from core.ticket_platforms import pick_sight

        self.assertEqual(pick_sight(self.ENTRIES, "库尔勒体育公园", city="库尔勒")["sightName"], "库尔勒体育公园")
        self.assertEqual(
            pick_sight(self.ENTRIES, "天鹅泉湿地公园", city="伊宁")["sightName"], "伊宁县天鹅泉湿地公园"
        )
        self.assertEqual(pick_sight(self.ENTRIES, "喀什古城", city="喀什")["sightName"], "喀什地区喀什古城景区")
        self.assertIsNone(pick_sight(self.ENTRIES, "图开沙漠冰雪乐园", city="伊宁"))

    def test_two_different_spots_never_share_one_ticket(self):
        """同一批结果里，两个不同节点不许落到同一个 sightId（实跑里就是这么发现错配的）。"""

        def fake_price_for(name, city):
            from core.ticket_platforms import pick_sight

            hit = pick_sight(self.ENTRIES, name, city=city)
            return (hit or {}).get("sightId")

        self.assertNotEqual(
            fake_price_for("库尔勒体育公园", "库尔勒"),
            fake_price_for("库尔勒市湿地公园", "库尔勒"),
        )


if __name__ == "__main__":
    unittest.main()
