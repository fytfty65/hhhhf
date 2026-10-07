"""文旅部（MCT）全国 A 级景区通道的测试（2026-10-07）。

参数是试出来的，测试把它们钉住（以后改坏了能立刻发现）：
  · 地区过滤参数是 **`city`**（`province_id`/`city_id`/`area_id` 都会被忽略，返回全国第一页）；
  · `type_id=1` = 5A 景区；
  · 名字匹配要"全称/简称互相包含"，且**取最具体的那个** ——
    实测踩到过：`喀什古城景区` 被匹配到「喀什地区金湖杨景区」，原因是归一化时
    末尾行政区后缀先动手，把"景区"啃成"景"。
"""

import asyncio
import os
import sys
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.mct_channel import lookup_mct_price, mct_lookup, profile_of  # noqa: E402
from core.official_directory import _normalize, match_entry  # noqa: E402

MARKERS = [
    {
        "name": "喀什地区喀什古城景区",
        "longitude": "75.99",
        "latitude": "39.47",
        "address": "新疆维吾尔自治区喀什地区喀什市",
        "introduce": "喀什古城是世界上现存规模最大的生土建筑群之一。",
        "web_link_url": "https://www.kashigucheng.com/",
        "appointment_link_url": "https://www.kashigucheng.com/ticket",
    },
    {"name": "喀什地区金湖杨景区", "longitude": "76.98", "latitude": "38.03", "address": "泽普县", "introduce": "胡杨林", "web_link_url": "", "appointment_link_url": ""},
]


class NormalizeTests(unittest.TestCase):
    def test_admin_suffix_is_removed_after_generic_words(self):
        """"喀什古城景区"必须归一成"喀什"，不能变成"喀什景"（那样会错配到金湖杨）。"""
        self.assertEqual(_normalize("喀什古城景区"), _normalize("喀什地区喀什古城景区"))
        self.assertEqual(_normalize("巴音郭楞蒙古自治州博斯腾湖景区"), "博斯腾湖")

    def test_match_prefers_the_most_specific_entry(self):
        hit = match_entry("喀什古城景区", MARKERS)
        self.assertEqual(hit["name"], "喀什地区喀什古城景区")
        self.assertNotEqual(hit["name"], "喀什地区金湖杨景区")

    def test_no_match_for_an_unlisted_spot(self):
        self.assertIsNone(match_entry("图开沙漠冰雪乐园", MARKERS))

    def test_a_hall_is_not_matched_to_a_scenic_area(self):
        """实测错配：`百年喀什馆` 被安上了「喀什地区喀什古城景区」的官方简介（"喀什"是它的子串）。

        现在两条闸门：类别词分组（博物馆/纪念馆 vs 户外景区）+ "名录名是节点名子串"时
        只认"多出来的都是通用字"。这里把四个真实名字都钉住。
        """
        self.assertIsNone(match_entry("百年喀什馆", MARKERS))
        self.assertIsNone(match_entry("喀什博物馆", MARKERS))
        self.assertIsNone(match_entry("喀什玉石文化博物馆", MARKERS))
        self.assertIsNone(match_entry("伊犁河旅游风景区", MARKERS))
        # 但简称对上全称要照常命中
        self.assertEqual(match_entry("喀什古城", MARKERS)["name"], "喀什地区喀什古城景区")
        self.assertEqual(match_entry("喀什古城景区", MARKERS)["name"], "喀什地区喀什古城景区")


class MctLookupTests(unittest.TestCase):
    def _patch_markers(self):
        from core import mct_channel

        async def fake_markers(city, type_id=1):
            return MARKERS

        mct_channel.mct_markers = fake_markers  # type: ignore[assignment]

    def test_profile_carries_official_site_and_booking(self):
        self._patch_markers()
        marker = asyncio.run(mct_lookup("喀什古城", city="喀什"))
        profile = profile_of(marker)
        self.assertEqual(profile["official_name"], "喀什地区喀什古城景区")
        self.assertEqual(profile["official_site"], "https://www.kashigucheng.com/")
        self.assertEqual(profile["official_booking"], "https://www.kashigucheng.com/ticket")
        self.assertEqual(profile["lnglat"], [75.99, 39.47])
        self.assertEqual(profile["source"], "mct")

    def test_price_comes_from_the_official_page(self):
        self._patch_markers()

        async def fake_fetch(url):
            return {"ok": True, "text": "喀什古城景区 门票价格 门票45元/人 开放时间 全天"}

        observation = asyncio.run(lookup_mct_price("喀什古城", city="喀什", fetch_page=fake_fetch))
        self.assertEqual(observation["value"], 45.0)
        self.assertEqual(observation["source"], "official")
        self.assertEqual(observation["source_url"], "https://www.kashigucheng.com/ticket")

    def test_a_spot_without_registered_site_is_reported_honestly(self):
        self._patch_markers()
        observation = asyncio.run(lookup_mct_price("金湖杨景区", city="喀什", fetch_page=None))
        self.assertIsNone(observation["value"])
        self.assertIn("没有登记官网", observation["note"])

    def test_an_unlisted_spot_says_the_channel_is_5a_only(self):
        self._patch_markers()
        observation = asyncio.run(lookup_mct_price("图开沙漠冰雪乐园", city="伊宁", fetch_page=None))
        self.assertIsNone(observation["value"])
        self.assertIn("只覆盖 5A", observation["note"])


if __name__ == "__main__":
    unittest.main()
