"""联网取价工具链测试。

用户提问（2026-10-04）："我们没有商业资质拿不到实时价格，那不能直接联网搜索、
或者直接打开官方页面拿吗？"

这个模块把三层答案变成可测的代码：
  1. 能搜 —— 但本机实测 DuckDuckGo 直接失败（回退 bing 也返回 None），必须**诊断并如实上报**；
  2. 能直接抓官方页面 —— 但只对公开可抓的域名（gov.cn/景区官网），平台电商页明确不做；
  3. 口径不变 —— 网页价格永远是**估算**，且必须带出处 URL 与抓取时间。
"""

import os
import unittest
from unittest import mock

from core.web_price_tool import (
    BLOCKED_HOST_MARKERS,
    build_observation,
    enabled_providers,
    extract_price_candidates,
    host_is_allowed,
    price_max_nodes,
    price_timeout_seconds,
    search_proxy,
)


class PriceParsingTests(unittest.TestCase):
    def test_yuan_and_symbol_forms(self):
        values = {item["value"] for item in extract_price_candidates("门票45元，观光车￥30，讲解 20 元")}
        self.assertEqual(values, {45.0, 30.0, 20.0})

    def test_free_is_a_real_price_not_a_missing_one(self):
        rows = extract_price_candidates("孔雀河景观带 免费开放")
        self.assertEqual(rows[0]["value"], 0.0)
        self.assertIn("免费", rows[0]["excerpt"])

    def test_bare_numbers_are_not_prices(self):
        """"2026 年""2 天""3 号"不能被当成价格。"""
        self.assertEqual(extract_price_candidates("2026年 2 天 3 号出发 8 个人"), [])

    def test_label_without_unit_is_still_a_price(self):
        rows = extract_price_candidates("门票 45 起，人均 60 左右")
        values = {item["value"] for item in rows}
        self.assertIn(45.0, values)
        self.assertIn(60.0, values)

    def test_context_is_ranked_first(self):
        rows = extract_price_candidates("某酒店 680 元，门票 45 元")
        self.assertTrue(rows[0]["has_context"] or rows[0]["value"] == 45.0)

    def test_absurd_numbers_are_filtered(self):
        values = {item["value"] for item in extract_price_candidates("价格 999999 元")}
        self.assertEqual(values, set())

    def test_empty_text_is_empty(self):
        self.assertEqual(extract_price_candidates(""), [])
        self.assertEqual(extract_price_candidates(None), [])


class HostAllowlistTests(unittest.TestCase):
    def test_government_pages_are_allowed(self):
        allowed, reason = host_is_allowed("https://www.xinjiang.gov.cn/lyyou/2026/price.html")
        self.assertTrue(allowed, reason)

    def test_scenic_site_is_allowed_when_explicitly_configured(self):
        """景区官网域名各异：默认不猜，靠 WEB_PRICE_HOST_SUFFIXES 显式配置。"""
        with mock.patch.dict(os.environ, {"WEB_PRICE_HOST_SUFFIXES": "bostenlake.com.cn"}, clear=False):
            allowed, reason = host_is_allowed("https://www.bostenlake.com.cn/ticket")
        self.assertTrue(allowed, reason)

    def test_unknown_commercial_domain_is_refused_by_default(self):
        allowed, reason = host_is_allowed("https://www.bostenlake.com.cn/ticket")
        self.assertFalse(allowed, reason)
        self.assertIn("WEB_PRICE_HOST_SUFFIXES", reason)

    def test_platform_pages_are_refused_with_a_reason(self):
        """携程/美团这类页面：价格在登录态接口里，抓页面拿不到，而且违反其服务条款。"""
        for url in (
            "https://hotels.ctrip.com/hotel/123.html",
            "https://www.meituan.com/zhoubianyou/456",
            "https://www.dianping.com/shop/789",
        ):
            allowed, reason = host_is_allowed(url)
            self.assertFalse(allowed, url)
            self.assertTrue(any(marker in url for marker in BLOCKED_HOST_MARKERS))
            self.assertTrue(reason)

    def test_non_http_is_refused(self):
        allowed, reason = host_is_allowed("ftp://example.com")
        self.assertFalse(allowed)
        self.assertIn("http", reason)

    def test_allow_any_host_switch_is_explicit(self):
        with mock.patch.dict(os.environ, {"WEB_PRICE_ALLOW_ANY_HOST": "1"}, clear=False):
            allowed, reason = host_is_allowed("https://some-random-site.com/price")
            self.assertTrue(allowed)
            self.assertIn("ANY_HOST", reason)
        with mock.patch.dict(os.environ, {"WEB_PRICE_ALLOW_ANY_HOST": ""}, clear=False):
            allowed, _ = host_is_allowed("https://some-random-site.com/price")
            self.assertFalse(allowed)


class ConfigTests(unittest.TestCase):
    def test_defaults_are_sane(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(price_timeout_seconds(), 6.0)
            self.assertEqual(price_max_nodes(), 12)
            self.assertEqual(enabled_providers(), ["search", "official_page"])
            self.assertEqual(search_proxy(), "")

    def test_env_overrides_are_honoured(self):
        with mock.patch.dict(os.environ, {
            "WEB_PRICE_TIMEOUT_SECONDS": "12.5",
            "WEB_PRICE_MAX_NODES": "30",
            "WEB_PRICE_PROVIDERS": "search",
            "WEB_SEARCH_PROXY": "http://127.0.0.1:7890",
        }, clear=True):
            self.assertEqual(price_timeout_seconds(), 12.5)
            self.assertEqual(price_max_nodes(), 30)
            self.assertEqual(enabled_providers(), ["search"])
            self.assertEqual(search_proxy(), "http://127.0.0.1:7890")

    def test_bad_values_fall_back_instead_of_crashing(self):
        with mock.patch.dict(os.environ, {
            "WEB_PRICE_TIMEOUT_SECONDS": "abc",
            "WEB_PRICE_MAX_NODES": "-3",
            "WEB_PRICE_PROVIDERS": " , ",
        }, clear=True):
            self.assertEqual(price_timeout_seconds(), 6.0)
            self.assertEqual(price_max_nodes(), 12)
            self.assertEqual(enabled_providers(), ["search"])


class ObservationTests(unittest.TestCase):
    def test_web_prices_are_never_verified(self):
        entry = build_observation(
            name="博斯腾湖", value=45.0, source="web_snippet",
            source_url="https://example.gov.cn/x", excerpt="门票45元",
        )
        self.assertFalse(entry["verified"], "网页/搜索来的价格不许标成已核实")
        self.assertTrue(entry["estimated"])

    def test_provider_prices_are_verified(self):
        entry = build_observation(name="某酒店", value=380.0, source="provider")
        self.assertTrue(entry["verified"])
        self.assertFalse(entry["estimated"])

    def test_every_observation_carries_provenance_fields(self):
        entry = build_observation(name="x", value=1.0, source="web_snippet")
        for key in ("source_url", "excerpt", "provider", "retrieved_at", "currency"):
            self.assertIn(key, entry)


class ProviderProbeTests(unittest.TestCase):
    """诊断能力：搜索源不可达时必须**说出来**，而不是静默返回空。"""

    def test_search_failure_is_reported_with_a_hint(self):
        import asyncio

        from core import web_price_tool

        with mock.patch.dict(os.environ, {"WEB_PRICE_PROVIDERS": "search"}, clear=True):
            with mock.patch("duckduckgo_search.DDGS", side_effect=RuntimeError("blocked")):
                report = asyncio.run(web_price_tool.probe_providers(query="测试"))
        entry = report["providers"]["search"]
        self.assertFalse(entry["ok"])
        self.assertIn("blocked", entry["error"])
        self.assertIn("WEB_SEARCH_PROXY", entry["hint"])
        self.assertFalse(report["reachable"])

    def test_official_page_provider_reports_capability_not_reachability(self):
        """能力 ≠ 可达：官方页面只是"配置了允许清单"，不能因此说"取价源可达"。"""
        import asyncio

        from core import web_price_tool

        with mock.patch.dict(os.environ, {"WEB_PRICE_PROVIDERS": "official_page"}, clear=True):
            report = asyncio.run(web_price_tool.probe_providers())
        entry = report["providers"]["official_page"]
        self.assertTrue(entry["configured"])
        self.assertNotIn("ok", entry)
        self.assertFalse(report["reachable"], "没有真正取数的提供方时不许说可达")

    def test_reachability_comes_only_from_real_fetchers(self):
        import asyncio

        from core import web_price_tool

        class _Ddgs:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def text(self, *_args, **_kwargs):
                return [{"title": "门票45元"}]

        with mock.patch.dict(os.environ, {"WEB_PRICE_PROVIDERS": "search,official_page"}, clear=True):
            with mock.patch("duckduckgo_search.DDGS", _Ddgs):
                report = asyncio.run(web_price_tool.probe_providers())
        self.assertTrue(report["reachable"], report)
        self.assertTrue(report["providers"]["search"]["ok"])


class FetchPageTests(unittest.TestCase):
    def test_refuses_platform_page_without_any_request(self):
        import asyncio

        from core.web_price_tool import fetch_page_text

        result = asyncio.run(fetch_page_text("https://hotels.ctrip.com/hotel/1.html"))
        self.assertFalse(result["ok"])
        self.assertIn("平台电商页", result["error"])

    def test_fetches_allowed_page_and_strips_tags(self):
        import asyncio

        from core import web_price_tool

        class _Response:
            status_code = 200
            text = "<html><body><h1>门票</h1><script>x=1</script><p>门票45元</p></body></html>"

        class _Client:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def get(self, _url):
                return _Response()

        import httpx

        with mock.patch.object(httpx, "AsyncClient", _Client):
            result = asyncio.run(web_price_tool.fetch_page_text("https://www.xinjiang.gov.cn/x"))
        self.assertTrue(result["ok"])
        self.assertIn("门票45元", result["text"])
        self.assertNotIn("x=1", result["text"])

    def test_http_error_is_reported_not_faked(self):
        import asyncio

        from core import web_price_tool

        class _Response:
            status_code = 403
            text = ""

        class _Client:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def get(self, _url):
                return _Response()

        import httpx

        with mock.patch.object(httpx, "AsyncClient", _Client):
            result = asyncio.run(web_price_tool.fetch_page_text("https://www.xinjiang.gov.cn/x"))
        self.assertFalse(result["ok"])
        self.assertIn("403", result["error"])


class ProxyWiringTests(unittest.TestCase):
    """真实缺陷（2026-10-04）：`WEB_SEARCH_PROXY` 最初只给了 httpx，搜索根本没走代理 ——
    界面上写着"可配置代理让搜索走代理"，配了也照样连不通。文档承诺必须与代码一致。"""

    def test_proxy_is_passed_to_the_search_engine_and_env_restored(self):
        import asyncio

        from core import web_price_tool

        captured: dict = {}

        class _Ddgs:
            def __init__(self, **kwargs):
                captured["kwargs"] = kwargs
                captured["http_proxy"] = os.environ.get("HTTPS_PROXY")

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def text(self, *_args, **_kwargs):
                return [{"title": "ok"}]

        with mock.patch.dict(os.environ, {
            "WEB_SEARCH_PROXY": "http://127.0.0.1:7890",
            "WEB_PRICE_PROVIDERS": "search",
        }, clear=True):
            with mock.patch("duckduckgo_search.DDGS", _Ddgs):
                count = asyncio.run(web_price_tool.search_result_count("库尔勒 门票"))

        self.assertEqual(count, 1)
        self.assertEqual(captured["kwargs"].get("proxy"), "http://127.0.0.1:7890")
        self.assertEqual(captured["http_proxy"], "http://127.0.0.1:7890", "调用期间环境变量也要覆盖")
        # 现场必须恢复，绝不能把代理留在进程环境里（那会污染高德等其它请求）
        self.assertIsNone(os.environ.get("HTTPS_PROXY"))
        self.assertIsNone(os.environ.get("HTTP_PROXY"))

    def test_no_proxy_configured_means_no_env_pollution(self):
        import asyncio

        from core import web_price_tool

        class _Ddgs:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def text(self, *_args, **_kwargs):
                return []

        with mock.patch.dict(os.environ, {"WEB_PRICE_PROVIDERS": "search"}, clear=True):
            with mock.patch("duckduckgo_search.DDGS", _Ddgs):
                count = asyncio.run(web_price_tool.search_result_count("x"))
        self.assertEqual(count, 0)
        self.assertIsNone(os.environ.get("HTTPS_PROXY"))

    def test_unreachable_proxy_gets_an_actionable_hint(self):
        import asyncio

        from core import web_price_tool

        with mock.patch.dict(os.environ, {
            "WEB_SEARCH_PROXY": "http://127.0.0.1:7890",
            "WEB_PRICE_PROVIDERS": "search",
        }, clear=True):
            with mock.patch(
                "core.web_price_tool.search_result_count",
                side_effect=RuntimeError("ConnectError: error sending request"),
            ):
                report = asyncio.run(web_price_tool.probe_providers())
        entry = report["providers"]["search"]
        self.assertFalse(entry["ok"])
        self.assertIn("已配置代理", entry["hint"])
        self.assertIn("确认代理在运行", entry["hint"])


class SourceLinkTests(unittest.TestCase):
    """出处链接：估算价必须能"点开核对"（用户原话："不能直接打开页面看看吗"）。"""

    def _plan(self):
        return {"route": [{"day": 1, "name": "博斯腾湖", "type": "风景名胜", "is_hotel": False}]}

    def test_observation_url_lands_on_the_node_and_in_the_pending_list(self):
        from core.price_sources import merge_observations, pending_price_report

        observation = {
            "name": "博斯腾湖",
            "value": 45.0,
            "source": "web",
            "source_url": "https://www.xinjiang.gov.cn/ticket/45",
            "excerpt": "门票45元",
            "retrieved_at": 1_700_000_000.0,
        }
        merged = merge_observations(self._plan(), [observation], now=1_700_000_001.0)
        node = merged["plan"]["route"][0]
        self.assertEqual(node["price_source_url"], "https://www.xinjiang.gov.cn/ticket/45")
        self.assertEqual(node["price_excerpt"], "门票45元")
        self.assertTrue(node["estimated"], "web 来的价格仍是估算")
        self.assertEqual(merged["updated"][0]["source_url"], "https://www.xinjiang.gov.cn/ticket/45")

        report = pending_price_report(merged["plan"])
        entry = report["pending"][0]
        self.assertEqual(entry["url"], "https://www.xinjiang.gov.cn/ticket/45", "待确认清单要给出处链接")
        self.assertIn("联网估算价", entry["reason"])
        self.assertTrue(entry["estimated"])

    def test_without_url_it_falls_back_to_a_search_entry(self):
        from core.price_sources import merge_observations, pending_price_report

        merged = merge_observations(
            self._plan(),
            [{"name": "博斯腾湖", "value": 45.0, "source": "web", "retrieved_at": 1_700_000_000.0}],
            now=1_700_000_001.0,
        )
        report = pending_price_report(merged["plan"])
        self.assertIn("amap.com/search", report["pending"][0]["url"])
        self.assertIn("没有可核实报价", report["pending"][0]["reason"])

    def test_verified_price_disappears_from_the_pending_list(self):
        from core.price_sources import merge_observations, pending_price_report

        merged = merge_observations(
            self._plan(),
            [{"name": "博斯腾湖", "value": 45.0, "source": "amap", "retrieved_at": 1_700_000_000.0}],
            now=1_700_000_001.0,
        )
        self.assertEqual(pending_price_report(merged["plan"])["pending"], [])


if __name__ == "__main__":
    unittest.main()
