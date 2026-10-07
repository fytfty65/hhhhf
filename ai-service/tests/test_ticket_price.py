"""门票价专线的测试（2026-10-07，用户放开"其他数据源"之后）。

背景与实测证据（`tools/probe_ticket_prices.py`）：
- 网页直抓走不通：携程/去哪儿/马蜂窝详情页是 JS 壳（20 字正文）、百度百科 403、
  gov 站证书不匹配 —— 所以只能从**搜索摘要/票务平台结果**里取；
- 但通用抽价在门票上**会错**，实测抓到的两个真实误采：
  ① 「森林公园」的摘要里有"40元至100元是较为常见的价格区间" → 被当成该公园门票 100 元；
  ② 「胡杨书院」的摘要讲的是**金湖杨景区**门票 40 元 → 记到了胡杨书院头上；
  ③ 「疏勒历史博物馆」拿到的是通用"免票政策详解"页 → 0 元。
所以门票走一条更严的专线：**贴门票语境 + 摘要里真的提到这个地点 + 拒绝区间/模糊表述**，
并按来源分级：官方白名单页面（可核实）> 票务平台（有出处、非官方）> 其它摘要（估算）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.web_price_tool import (  # noqa: E402
    extract_ticket_price,
    host_is_ticket_platform,
    ticket_price_from_rows,
)


class TicketExtractionTests(unittest.TestCase):
    def test_plain_ticket_price_is_extracted(self):
        found = extract_ticket_price("喀什博物馆门票价格为每人50元，学生可半价。")
        self.assertIsNotNone(found)
        self.assertEqual(found["value"], 50.0)

    def test_ticket_free_is_zero(self):
        found = extract_ticket_price("该景区门票免费，凭身份证入园。")
        self.assertIsNotNone(found)
        self.assertEqual(found["value"], 0.0)

    def test_price_range_is_rejected(self):
        """实测误采：'40元至100元是较为常见的价格区间' 不是一个确定价。"""
        self.assertIsNone(extract_ticket_price("门票价格数百元不等，其中40元至100元是较为常见的价格区间。"))

    def test_vague_words_are_rejected(self):
        self.assertIsNone(extract_ticket_price("门票大约60元左右，具体以现场为准。"))
        self.assertIsNone(extract_ticket_price("人均消费不等，门票常见价位在几十元。"))

    def test_number_without_ticket_context_is_rejected(self):
        self.assertIsNone(extract_ticket_price("停车费20元，讲解服务100元。"))

    def test_absurd_amount_is_rejected(self):
        self.assertIsNone(extract_ticket_price("门票价格99999元"))


class TicketRowSelectionTests(unittest.TestCase):
    def test_platform_price_for_the_named_place_is_taken(self):
        rows = [
            {
                "title": "喀什玉石文化博物馆门票,喀什玉石文化博物馆门票价格-去哪儿网",
                "body": "(314国道南) 地图 ¥38.5起 9.6折票面价：",
                "url": "https://piao.qunar.com/ticket/list_喀什玉石文化博物馆建筑面积.html",
                "provider": "ddgs",
            }
        ]
        found = ticket_price_from_rows(rows, "喀什玉石文化博物馆")
        self.assertIsNotNone(found)
        self.assertEqual(found["value"], 38.5)
        self.assertEqual(found["source"], "platform")
        self.assertTrue(found["platform_source"])

    def test_price_from_another_place_on_the_same_page_is_rejected(self):
        """实测误采（2026-10-07 真跑抓到）：喀什市政府的票价通知页整页提到「阳光公园」，
        但紧挨 30 元的是**香妃园**，而且是调价前的旧价。名称必须挨着价格。"""
        body = (
            "关于喀什市景区门票价格调整的通知 喀什市香妃园景区门票价格由30元/人·次调整为40元/人·次"
            "，现将有关事项通知如下：一、执行时间自发布之日起。二、各景区要明码标价、接受监督。"
            "三、附件：喀什市各公园（含阳光公园、九龙泉湿地公园）免费开放说明。"
        )
        self.assertIsNone(extract_ticket_price(body, require_name="阳光公园"))
        # 同一个页面里，真要抽香妃园就该拿到**调整后**的 40 元，而不是旧价 30
        found = extract_ticket_price(body, require_name="香妃园")
        self.assertIsNotNone(found)
        self.assertEqual(found["value"], 40.0, found)

    def test_price_from_another_place_is_rejected(self):
        """实测误采：「胡杨书院」被塞了金湖杨景区的 40 元。"""
        rows = [
            {
                "title": "金湖杨景区门票约40元，以其胡杨、碧水、绿洲著称",
                "body": "其中，金湖杨景区门票约40元。",
                "url": "https://wenku.baidu.com/view/x.html",
                "provider": "ddgs",
            }
        ]
        self.assertIsNone(ticket_price_from_rows(rows, "胡杨书院"))

    def test_generic_free_policy_page_is_rejected(self):
        """实测误采：通用"免票政策详解"页被当成疏勒历史博物馆的免票。"""
        rows = [
            {
                "title": "景区免票政策详解",
                "body": "包含学生票、老人票、免票政策详解，以及周边交通。",
                "url": "http://www.educfa.cn/readnews/14026/",
                "provider": "ddgs",
            }
        ]
        self.assertIsNone(ticket_price_from_rows(rows, "疏勒历史博物馆"))

    def test_official_page_beats_a_platform_snippet(self):
        rows = [
            {
                "title": "英吉沙国家湿地公园 门票 45元 - 去哪儿",
                "body": "门票45元",
                "url": "https://piao.qunar.com/ticket/x.html",
                "provider": "ddgs",
            },
            {
                "title": "英吉沙国家湿地公园门票价格公示",
                "body": "英吉沙国家湿地公园门票免费开放。",
                "url": "https://www.yjs.gov.cn/notice/1.html",
                "provider": "bing",
            },
        ]
        found = ticket_price_from_rows(rows, "英吉沙国家湿地公园")
        self.assertIsNotNone(found)
        self.assertEqual(found["value"], 0.0)
        self.assertEqual(found["source"], "web_page")

    def test_platform_hosts_are_recognised(self):
        self.assertTrue(host_is_ticket_platform("https://piao.qunar.com/ticket/detail_1.html"))
        self.assertTrue(host_is_ticket_platform("https://you.ctrip.com/sight/x.html"))
        self.assertFalse(host_is_ticket_platform("https://www.yjs.gov.cn/x.html"))
        self.assertFalse(host_is_ticket_platform("https://baike.baidu.com/item/x"))


class PlatformLabelTests(unittest.TestCase):
    def test_platform_price_is_labelled_non_official(self):
        """票务平台价必须**在用户看到的那一行里**说明非官方，且不计入可核实。"""
        from core.price_sources import is_verified_source, merge_observations, SOURCE_RANK

        plan = {"route": [{"day": 1, "name": "喀什玉石文化博物馆", "type": "博物馆"}]}
        merged = merge_observations(
            plan,
            [
                {
                    "name": "喀什玉石文化博物馆",
                    "value": 38.5,
                    "source": "platform",
                    "source_url": "https://piao.qunar.com/ticket/list_x.html",
                    "excerpt": "¥38.5起 票面价",
                    "retrieved_at": __import__("time").time(),
                }
            ],
        )
        node = merged["plan"]["route"][0]
        self.assertEqual(node["cost_estimate"], "¥38.5（票务平台价，非官方）")
        self.assertEqual(node["price_source_label"], "票务平台价，非官方")
        self.assertTrue(node["estimated"], "票务平台价不是可核实来源")
        self.assertFalse(is_verified_source("platform"))
        self.assertGreater(SOURCE_RANK["platform"], SOURCE_RANK["web"], "票务平台比网摘可信")
        self.assertLess(SOURCE_RANK["platform"], SOURCE_RANK["web_page"], "票务平台不如官方页面可信")


class LookupPriceTicketPathTests(unittest.TestCase):
    def test_lookup_price_uses_the_ticket_line_and_keeps_provenance(self):
        import asyncio
        from unittest import mock

        from core import web_price_tool

        async def fake_search(query, max_results=3, **_kwargs):
            return [
                {
                    "title": "喀什博物馆门票多少钱",
                    "body": "喀什博物馆门票价格为每人50元。",
                    "url": "https://piao.qunar.com/ticket/list_喀什博物馆.html",
                    "provider": "ddgs",
                    "cite_site": "去哪儿",
                }
            ]

        with mock.patch("core.web_price_tool.search_texts", fake_search):
            observation = asyncio.run(web_price_tool.lookup_price("喀什博物馆", "喀什"))
        self.assertEqual(observation["value"], 50.0)
        self.assertEqual(observation["source"], "platform")
        self.assertEqual(observation["label"], "门票")
        self.assertEqual(observation["publisher"], "去哪儿")
        self.assertFalse(observation["verified"])

    def test_generic_named_place_does_not_get_a_content_farm_number(self):
        """泛称地点（森林公园）拿不到可信门票价时**必须如实留空**。"""
        import asyncio
        from unittest import mock

        from core import web_price_tool

        async def fake_search(query, max_results=3, **_kwargs):
            return [
                {
                    "title": "森林公园门票价格",
                    "body": "门票价格数百元不等，其中40元至100元是较为常见的价格区间。",
                    "url": "https://wenku.baidu.com/view/x.html",
                    "provider": "ddgs",
                }
            ]

        with mock.patch("core.web_price_tool.search_texts", fake_search):
            observation = asyncio.run(web_price_tool.lookup_price("森林公园", "喀什"))
        self.assertIsNone(observation["value"], observation)

    def test_content_farm_free_claim_is_rejected(self):
        """实测误采：内容农场的通用"免票政策详解"页让「英吉沙国家湿地公园」显示免费。

        unknown ≠ 0：拿不到就不能替它说"不要钱"。这条走的是**通用**抽价（不是门票专线），
        所以闸门必须也设在通用路径上。
        """
        import asyncio
        from unittest import mock

        from core import web_price_tool

        async def fake_search(query, max_results=3, **_kwargs):
            return [
                {
                    "title": "英吉沙国家湿地公园 免票政策详解",
                    "body": "英吉沙国家湿地公园门票免费，学生票、老人票免票政策详解。",
                    "url": "http://www.educfa.cn/readnews/14026/",
                    "provider": "ddgs",
                }
            ]

        with mock.patch("core.web_price_tool.search_texts", fake_search):
            observation = asyncio.run(web_price_tool.lookup_price("英吉沙国家湿地公园", "喀什"))
        self.assertIsNone(observation["value"], observation)

    def test_ticket_platform_snippet_is_labelled_as_platform_not_web(self):
        """携程页面的价原来被写成 `web`（估算），其实来自票务平台 —— 标错来源等于把出处说小了。"""
        import asyncio
        from unittest import mock

        from core import web_price_tool

        async def fake_search(query, max_results=3, **_kwargs):
            return [
                {
                    "title": "九龙泉湿地公园 门票",
                    "body": "九龙泉湿地公园平日门票29元，儿童半价。",
                    "url": "https://gs.ctrip.com/html5/you/sight/kashgar2507/148457796.html",
                    "provider": "ddgs",
                }
            ]

        with mock.patch("core.web_price_tool.search_texts", fake_search):
            observation = asyncio.run(web_price_tool.lookup_price("九龙泉湿地公园", "喀什"))
        self.assertEqual(observation["value"], 29.0)
        self.assertEqual(observation["source"], "platform")


if __name__ == "__main__":
    unittest.main()
