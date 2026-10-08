"""景区**官方站点/官方购票渠道**登记表的测试（2026-10-07，用户给了三个域名）。

把三件事钉住：
  ① 名字匹配（含别名与城市线索，别的城市的同名景区不许错配）；
  ② **客户端渲染**的票价页要如实说明，并把官方小程序/官网交出去（不猜价）；
  ③ 用户提供的参考价只作 `reference_prices` 原样登记，**不冒充**我们抓到的一手价。
"""

import asyncio
import os
import sys
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.official_sites import (  # noqa: E402
    OFFICIAL_SITES,
    channel_summary,
    lookup_official_site_price,
    official_site_for,
    registered_hosts,
)

FIXTURE_HTML = "巴楚红海景区 门票价格 门票45元/人 优惠政策：半票 22元"


class RegistryTests(unittest.TestCase):
    def test_hosts_are_registered_for_fetching(self):
        hosts = registered_hosts()
        for host in ("www.bachu.gov.cn", "www.xjlbr.com", "www.byblk.cn"):
            self.assertIn(host, hosts)

    def test_name_matching_covers_aliases_and_city(self):
        self.assertEqual((official_site_for("罗布人村寨", "库尔勒") or {}).get("key"), "loburen_village")
        self.assertEqual((official_site_for("罗布人村寨景区", "库尔勒") or {}).get("key"), "loburen_village")
        self.assertEqual((official_site_for("巴音布鲁克", "库尔勒") or {}).get("key"), "bayinbuluke")
        self.assertEqual((official_site_for("红海景区", "喀什") or {}).get("key"), "bachu_honghai")
        self.assertIsNone(official_site_for("博斯腾湖", "库尔勒"), "没登记的景区不许硬凑")

    def test_city_hint_prevents_cross_city_mismatch(self):
        """同一个名字挂到不相干的城市时不该命中（城市线索都在时用于过滤）。"""
        self.assertIsNone(official_site_for("罗布人村寨", "三亚"))

    def test_reference_prices_are_clearly_attributed(self):
        entry = official_site_for("罗布人村寨", "库尔勒") or {}
        self.assertEqual([item["value"] for item in entry.get("reference_prices") or []], [35, 15])
        self.assertIn("用户提供", str(entry.get("reference_source")))


class LookupTests(unittest.TestCase):
    def test_js_rendered_price_page_says_so_and_gives_the_channel(self):
        """实测：罗布人村寨的票价页是客户端渲染（所有子页都返回同一份 2490 字符壳）。"""
        observation = asyncio.run(lookup_official_site_price("罗布人村寨", city="库尔勒"))
        self.assertIsNone(observation["value"], "抓不到就不许编价")
        channel = observation.get("official_channel") or {}
        self.assertEqual(channel.get("official_miniprogram"), "罗布人村寨")
        self.assertIn("客户端渲染", observation["note"])
        self.assertIn("35", observation["note"], "用户给的参考价要原样带出并标明出处")
        self.assertEqual(observation["source"], "official")
        self.assertIn("未授权", str(channel.get("official_channel_note")))

    def test_readable_official_page_still_yields_a_verifiable_price(self):
        """页面读得到就照常抽价（这条路径给"将来页面改成服务端渲染"留好）。"""

        async def fake_fetch(url, **kwargs):
            return {"ok": True, "text": FIXTURE_HTML}

        # 临时把巴楚红海标成"可读页面"，验证抽取路径
        entry = next(item for item in OFFICIAL_SITES if item["key"] == "bachu_honghai")
        original = entry["price_page_rendered"]
        entry["price_page_rendered"] = False
        try:
            observation = asyncio.run(
                lookup_official_site_price("巴楚红海景区", city="喀什", fetch_text=fake_fetch)
            )
        finally:
            entry["price_page_rendered"] = original
        self.assertEqual(observation["value"], 45.0)
        self.assertEqual(observation["source"], "official")
        self.assertIn("门票45元", observation["excerpt"])

    def test_readable_page_without_a_price_is_reported_honestly(self):
        async def fake_fetch(url, **kwargs):
            return {"ok": True, "text": "巴楚红海景区 优惠政策：半票群体条件见附表"}

        entry = next(item for item in OFFICIAL_SITES if item["key"] == "bachu_honghai")
        original = entry["price_page_rendered"]
        entry["price_page_rendered"] = False
        try:
            observation = asyncio.run(
                lookup_official_site_price("巴楚红海景区", city="喀什", fetch_text=fake_fetch)
            )
        finally:
            entry["price_page_rendered"] = original
        self.assertIsNone(observation["value"])
        self.assertIn("没有写出", observation["note"])

    def test_unregistered_spot_is_reported(self):
        observation = asyncio.run(lookup_official_site_price("博斯腾湖", city="库尔勒"))
        self.assertIsNone(observation["value"])
        self.assertIn("没有", observation["note"])

    def test_channel_summary_shape(self):
        summary = channel_summary(official_site_for("巴音布鲁克", "库尔勒") or {})
        self.assertEqual(summary["official_miniprogram"], "巴音布鲁克旅游")
        self.assertEqual(summary["official_site"], "https://www.byblk.cn/")
        self.assertEqual(summary["source"], "official_site_registry")


if __name__ == "__main__":
    unittest.main()
