"""票务平台通道的测试（2026-10-07，用户："既然票务平台能用为什么不用票务系统"）。

用**真实返回结构**做夹具（字段名照抄去哪儿/携程接口），把三个易错点钉住：
  ① `list.json` 里的 `price.sightPrice.priceList` 才是票种，**成人票优先**；
  ② 平台上挂着**跟团游/演艺票**，不过滤就会把「赛里木湖+库尔德宁三日游 ¥2999」当成
     「天鹅泉湿地公园门票」（实测就这么错过一次）；
  ③ 票种名与景区名不一致时（「买买提的老房子门票成人票」挂在喀什古城下），
     照抄票种名并标 `ticket_scope="related"`，不假装它就是景区门票。
"""

import asyncio
import os
import sys
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ticket_platforms import (  # noqa: E402
    adult_ticket_price,
    lookup_ctrip_price,
    lookup_platform_price,
    lookup_qunar_price,
    pick_sight,
)

BOSTEN = {
    "sightName": "博斯腾湖",
    "sightId": 95106489,
    "qunarPrice": "16",
    "price": {
        "sightPrice": {
            "totalCount": 33,
            "priceList": [
                {"typeName": "博斯腾湖大河口景区门票成人票", "qunarPrice": "42"},
                {"typeName": "博斯腾湖大河口景区门票学生票", "qunarPrice": "21"},
                {"typeName": "博斯腾湖大河口景区门票家庭票2大1小", "qunarPrice": "105"},
            ],
        }
    },
}
TIANEQUE = {
    "sightName": "天鹅泉湿地公园",
    "sightId": 180076650,
    "qunarPrice": "2999",
    "price": {
        "sightPrice": {
            "priceList": [
                {"typeName": "赛里木湖+库尔德宁景区三日游【双生之境】", "qunarPrice": "2999"},
                {"typeName": "伊宁起止拼小团4日游", "qunarPrice": "1880"},
            ]
        }
    },
}
KASHI = {
    "sightName": "喀什古城",
    "sightId": 3653110945,
    "qunarPrice": "20",
    "price": {
        "sightPrice": {
            "priceList": [
                {"typeName": "买买提的老房子门票成人票", "qunarPrice": "29"},
                {"typeName": "遇见喀什国际演艺中心沉浸式观演票成人票", "qunarPrice": "289"},
            ]
        }
    },
}


class PickSightTests(unittest.TestCase):
    def test_exact_name_wins(self):
        entries = [{"sightName": "博斯腾湖大河口景区"}, {"sightName": "博斯腾湖"}, {"sightName": "博斯腾湖-白鹭洲"}]
        self.assertEqual(pick_sight(entries, "博斯腾湖")["sightName"], "博斯腾湖")

    def test_no_match_returns_none(self):
        self.assertIsNone(pick_sight([{"sightName": "头屯河谷森林公园"}], "阿吾赞沟森林公园"))


class TicketProductTests(unittest.TestCase):
    def test_adult_ticket_preferred(self):
        picked = adult_ticket_price(BOSTEN)
        self.assertEqual(picked["value"], 42.0)
        self.assertEqual(picked["kind"], "adult")
        self.assertIn("成人票", picked["ticket_name"])

    def test_tour_packages_are_not_ticket_prices(self):
        """实测错：天鹅泉被安上 ¥2999 —— 那是"赛里木湖+库尔德宁三日游"，不是门票。"""
        self.assertEqual(adult_ticket_price(TIANEQUE), {})

    def test_show_tickets_are_filtered_out(self):
        picked = adult_ticket_price(KASHI)
        self.assertEqual(picked["value"], 29.0)
        self.assertNotIn("演艺", picked["ticket_name"])

    def test_a_ticket_for_another_spot_is_flagged(self):
        picked = adult_ticket_price(KASHI)
        self.assertEqual(picked["ticket_scope"], "related", "票种名与景区名不同要标出来")

    def test_guided_tour_products_are_not_ticket_prices(self):
        """实测（喀什博物馆）：平台上「门票预约或讲解1.5h」名字里带"门票"，但价不是门票价。"""
        entry = {
            "sightName": "喀什博物馆",
            "price": {
                "sightPrice": {
                    "priceList": [
                        {"typeName": "新疆·喀什博物馆门票预约或讲解1.5h·北朝三耳陶罐", "qunarPrice": "55"},
                    ]
                }
            },
        }
        self.assertEqual(adult_ticket_price(entry), {})


class LookupTests(unittest.TestCase):
    def test_qunar_price_is_platform_sourced(self):
        async def fake_json(url, params):
            return {"ret": True, "data": {"list": [BOSTEN]}}

        observation = asyncio.run(lookup_qunar_price("博斯腾湖", fetch_json=fake_json))
        self.assertEqual(observation["value"], 42.0)
        self.assertEqual(observation["source"], "platform")
        self.assertEqual(observation["provider"], "qunar")
        self.assertIn("门票成人票", observation["excerpt"])
        self.assertIn("detail_95106489", observation["source_url"])

    def test_a_sight_without_ticket_products_says_so(self):
        async def fake_json(url, params):
            return {"ret": True, "data": {"list": [TIANEQUE]}}

        observation = asyncio.run(lookup_qunar_price("天鹅泉湿地公园", fetch_json=fake_json))
        self.assertIsNone(observation["value"])
        self.assertIn("没有**门票**口径的价", observation["note"])

    def test_ctrip_uses_the_suggest_api_then_the_detail_page(self):
        async def fake_json(url, params):
            return {
                "data": [
                    {"id": 144508327, "code": "sight_144508327", "word": "阿吾赞沟森林公园",
                     "url": "http://you.ctrip.com/sight/awuzangou1446400/144508327.html"}
                ]
            }

        async def fake_page(url, **kwargs):
            return {"ok": True, "text": "阿吾赞沟森林公园 门票110元起 预订"}

        observation = asyncio.run(
            lookup_ctrip_price("阿吾赞沟森林公园", fetch_json=fake_json, fetch_page=fake_page)
        )
        self.assertEqual(observation["value"], 110.0)
        self.assertEqual(observation["source"], "platform")
        self.assertIn("you.ctrip.com", observation["source_url"])

    def test_platform_entry_point_falls_back_to_ctrip(self):
        async def fake_json(url, params):
            if "qunar" in url:
                return {"ret": True, "data": {"list": [TIANEQUE]}}
            return {
                "data": [
                    {"id": 1, "code": "sight_1", "word": "天鹅泉湿地公园",
                     "url": "http://you.ctrip.com/sight/tianeque1446400/1.html"}
                ]
            }

        async def fake_page(url, **kwargs):
            return {"ok": True, "text": "天鹅泉湿地公园 门票30元/人"}

        observation = asyncio.run(
            lookup_platform_price("天鹅泉湿地公园", fetch_json=fake_json, fetch_page=fake_page)
        )
        self.assertEqual(observation["value"], 30.0)
        self.assertEqual(observation["provider"], "ctrip")


if __name__ == "__main__":
    unittest.main()
