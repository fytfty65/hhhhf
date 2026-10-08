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
        """罗布人村寨：渲染后**仍然**没有票价（只在微信小程序里公布）→ 如实说，不猜价。"""

        async def fake_render(url):
            return {"ok": True, "url": url, "text": "罗布人村寨 在线预订 游客须知 交通指南 每日运营时段：10:00至21:00", "seconds": 5.4}

        observation = asyncio.run(
            lookup_official_site_price("罗布人村寨", city="库尔勒", render_text=fake_render)
        )
        self.assertIsNone(observation["value"], "抓不到就不许编价")
        channel = observation.get("official_channel") or {}
        self.assertEqual(channel.get("official_miniprogram"), "罗布人村寨")
        self.assertIn("微信小程序", observation["note"])
        self.assertIn("35", observation["note"], "用户给的参考价要原样带出并标明出处")
        self.assertEqual(observation["source"], "official")
        self.assertFalse(observation.get("rendered"))
        self.assertIn("未授权", str(channel.get("official_channel_note")))

    def test_rendered_official_page_yields_a_verifiable_price(self):
        """巴音布鲁克：渲染后正文写着「票价：140元（包含门票+区间车票）」→ 采为官方可核实价。"""

        async def fake_render(url):
            return {
                "ok": True,
                "url": url,
                "text": "门票预定 景点门票： 巴音布鲁克景区联票 游玩时段： 全年 08:30-19:00 票价： 140元（包含门票+区间车票）",
                "seconds": 3.8,
            }

        observation = asyncio.run(
            lookup_official_site_price("巴音布鲁克", city="库尔勒", render_text=fake_render)
        )
        self.assertEqual(observation["value"], 140.0)
        self.assertEqual(observation["source"], "official")
        self.assertTrue(observation.get("rendered"))
        self.assertEqual(observation.get("matched_via"), "rendered_official_page")
        self.assertEqual(observation["excerpt"], "票价： 140元（包含门票+区间车票）", "摘要要是带票价的那句，不是整页开头")
        self.assertEqual((observation.get("official_channel") or {}).get("official_miniprogram"), "巴音布鲁克旅游")

    def test_rendered_page_with_price_on_its_own_line_gets_a_clean_excerpt(self):
        """实测渲染后的官方票务页是**分行**的：「票价：」一行、金额一行 —— 摘要要把两句接起来。"""

        async def fake_render(url):
            return {
                "ok": True,
                "url": url,
                "text": "门票预定\n景点门票：\n巴音布鲁克景区联票\n游玩时段：\n全年 08:30-19:00\n票价：\n140元（包含门票+区间车票）\n预定须知：",
                "seconds": 3.8,
            }

        observation = asyncio.run(
            lookup_official_site_price("巴音布鲁克", city="库尔勒", render_text=fake_render)
        )
        self.assertEqual(observation["value"], 140.0)
        self.assertEqual(observation["excerpt"], "票价： 140元（包含门票+区间车票）")

    def test_alias_matched_node_still_gets_the_official_price(self):
        """节点名与官方名不同（别名命中的）也要拿到价。

        实测：和静方案里节点是「天鹅湖公园」（巴音布鲁克景区里的天鹅湖），
        页面写的是「巴音布鲁克景区联票 票价：140元」。只认节点名就会把这条真价否掉。
        """

        async def fake_render(url):
            return {
                "ok": True,
                "url": url,
                "text": "景点门票： 巴音布鲁克景区联票 票价： 140元（包含门票+区间车票）",
                "seconds": 3.0,
            }

        observation = asyncio.run(
            lookup_official_site_price("天鹅湖公园", city="和静", render_text=fake_render)
        )
        self.assertEqual(observation["value"], 140.0)
        self.assertEqual(observation["excerpt"], "票价： 140元（包含门票+区间车票）", "摘要要截到带票价的那一句")

    def test_render_failure_is_reported_honestly(self):
        async def fake_render(url):
            return {"ok": False, "url": url, "error": "TimeoutError: 渲染超时（25s）"}

        observation = asyncio.run(
            lookup_official_site_price("巴音布鲁克", city="库尔勒", render_text=fake_render)
        )
        self.assertIsNone(observation["value"])
        self.assertIn("客户端渲染", observation["note"])
        self.assertIn("微信小程序", observation["note"])

    def test_readable_official_page_still_yields_a_verifiable_price(self):
        """页面服务端读得到就照常抽价（巴楚红海这条路径留着）。"""

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
