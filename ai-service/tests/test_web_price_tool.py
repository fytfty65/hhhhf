"""联网取价工具链测试。

用户提问（2026-10-04）："我们没有商业资质拿不到实时价格，那不能直接联网搜索、
或者直接打开官方页面拿吗？"

这个模块把三层答案变成可测的代码：
  1. 能搜 —— 但本机实测 DuckDuckGo 直接失败（回退 bing 也返回 None），必须**诊断并如实上报**；
  2. 能直接抓官方页面 —— 但只对公开可抓的域名（gov.cn/景区官网），平台电商页明确不做；
  3. 口径不变 —— 网页价格永远是**估算**，且必须带出处 URL 与抓取时间。
"""

import os
import tempfile
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

_USAGE_DIR = None


def setUpModule():
    """测试必须**自给自足**：不能让真实的日请求计数器把测试拖垮。

    实测事故（2026-10-05）：跑了几轮真实推演后 `web_price_usage.json` 到 126/120，
    这个文件里 4 个"搜索应该成功"的用例集体失败 —— 也就是测试依赖了**机器当天的用量状态**。
    这里把用量文件指到临时目录、并把上限调大，测试不再受真实配额影响；
    `DailyQuotaTests` 仍然用 `mock.patch.dict` 覆盖成它自己的小配额，两者互不干扰。
    """
    global _USAGE_DIR
    _USAGE_DIR = tempfile.mkdtemp(prefix="web-price-test-")
    os.environ["WEB_PRICE_USAGE_FILE"] = os.path.join(_USAGE_DIR, "usage.json")
    os.environ["WEB_PRICE_DAILY_MAX_REQUESTS"] = "1000000"


def tearDownModule():
    os.environ.pop("WEB_PRICE_USAGE_FILE", None)
    os.environ.pop("WEB_PRICE_DAILY_MAX_REQUESTS", None)
    if _USAGE_DIR:
        import shutil

        shutil.rmtree(_USAGE_DIR, ignore_errors=True)


def isolated_env(**overrides):
    """一个**清空环境**但保留"用量文件指到临时目录"的 patch。

    为什么需要它：本文件里好几个用例用 `mock.patch.dict(..., clear=True)` 来验证
    "没有代理时不污染环境"，而 `clear=True` 会把 `WEB_PRICE_USAGE_FILE` 也清掉 ——
    于是取价工具回落到**真实的** `web_price_usage.json`，机器当天用完 120 次配额后
    这些用例就集体失败（实测 2026-10-05）。测试不该依赖机器当天的用量状态。
    """
    env = {
        "WEB_PRICE_USAGE_FILE": os.path.join(_USAGE_DIR or tempfile.mkdtemp(), "usage.json"),
        "WEB_PRICE_DAILY_MAX_REQUESTS": "1000000",
    }
    env.update(overrides)
    return mock.patch.dict(os.environ, env, clear=True)


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
        with isolated_env():
            self.assertEqual(price_timeout_seconds(), 6.0)
            self.assertEqual(price_max_nodes(), 12)
            # 默认顺序把**当前真的能返回结果**的源放前面（2026-10-05 实测：百度能返回真结果，
            # 搜狗/必应被打成填充页，DuckDuckGo 时通时不通）
            self.assertEqual(enabled_providers(), ["baidu", "sogou", "search", "official_page"])
            self.assertEqual(search_proxy(), "")

    def test_env_overrides_are_honoured(self):
        with isolated_env(
            WEB_PRICE_TIMEOUT_SECONDS="12.5",
            WEB_PRICE_MAX_NODES="30",
            WEB_PRICE_PROVIDERS="search",
            WEB_SEARCH_PROXY="http://127.0.0.1:7890",
        ):
            self.assertEqual(price_timeout_seconds(), 12.5)
            self.assertEqual(price_max_nodes(), 30)
            self.assertEqual(enabled_providers(), ["search"])
            self.assertEqual(search_proxy(), "http://127.0.0.1:7890")

    def test_bad_values_fall_back_instead_of_crashing(self):
        with isolated_env(
            WEB_PRICE_TIMEOUT_SECONDS="abc",
            WEB_PRICE_MAX_NODES="-3",
            WEB_PRICE_PROVIDERS=" , ",
        ):
            self.assertEqual(price_timeout_seconds(), 6.0)
            self.assertEqual(price_max_nodes(), 12)
            self.assertEqual(enabled_providers(), ["sogou"])


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

        with isolated_env(WEB_PRICE_PROVIDERS="search"):
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

        with isolated_env(WEB_PRICE_PROVIDERS="official_page"):
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

        with isolated_env(WEB_PRICE_PROVIDERS="search,official_page"):
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

        with isolated_env(WEB_SEARCH_PROXY="http://127.0.0.1:7890", WEB_PRICE_PROVIDERS="search"):
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

        with isolated_env(WEB_PRICE_PROVIDERS="search"):
            with mock.patch("duckduckgo_search.DDGS", _Ddgs):
                count = asyncio.run(web_price_tool.search_result_count("x"))
        self.assertEqual(count, 0)
        self.assertIsNone(os.environ.get("HTTPS_PROXY"))

    def test_unreachable_proxy_gets_an_actionable_hint(self):
        import asyncio

        from core import web_price_tool

        with isolated_env(
            WEB_SEARCH_PROXY="http://127.0.0.1:7890",
            WEB_PRICE_PROVIDERS="search",
        ):
            with mock.patch(
                "core.web_price_tool.search_duckduckgo",
                side_effect=RuntimeError("ConnectError: error sending request"),
            ):
                report = asyncio.run(web_price_tool.probe_providers())
        entry = report["providers"]["search"]
        self.assertFalse(entry["ok"])
        self.assertIn("已配置代理", entry["hint"])
        self.assertIn("确认代理在运行", entry["hint"])

    def test_sogou_provider_failure_is_reported(self):
        import asyncio

        from core import web_price_tool

        with isolated_env(WEB_PRICE_PROVIDERS="sogou"):
            with mock.patch("core.web_price_tool.fetch_sogou", side_effect=RuntimeError("HTTP 503")):
                report = asyncio.run(web_price_tool.probe_providers())
        self.assertFalse(report["providers"]["sogou"]["ok"])
        self.assertIn("503", report["providers"]["sogou"]["error"])
        self.assertFalse(report["reachable"])


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


class SogouParserTests(unittest.TestCase):
    """解析规则必须对**真实页面**有效（下面片段是 2026-10-04 抓下来的原文，未改写结构）。"""

    REAL_BLOCK = (
        '<div class="vrwrap" id="sogou_vr_30000000_wrap_1"><h3 class="vr-title">'
        '<a name="dttl" target="_blank" href="/link?url=hedJjaC291PfuReBFiyDfBZqKaVfbyJfPUq2S9IRa1SU6uBpJIO7XLNjXxqiwmf-">'
        '2026新疆<em>博斯腾湖</em>旅游攻略-<em>博斯腾湖</em>自助游-<em>博斯腾湖门票</em>交通天气景...</a></h3>'
        '<div class="fz-mid space-txt base-ellipsis clamp2" id="cacheresult_summary_1">'
        '<em>博斯腾湖门票价格:大河口45元</em> 西海龙宫水世界198元 景区开放时间: 9:00--18:00。 '
        '信息纠错 景区电话: 0996-6621611,0996-6622638</div>  '
        '<a class="citeLinkClass" target="_blank" href="/link?url=hedJjaC291PfuReBFiyDfBZqKaVfbyJfPUq2S9IRa1SU6uBpJIO7XLNjXxqiwmf-">'
        '<img class="footerIcon" src="https://cdn-yb.icon.qq.com/zuowei_dir/www_meet99_com.ico" alt="cite">'
        '<span>相约久久旅游网</span> <span>https://www.meet99.com/j...</span> <span>2026-09-19</span></a></div>'
    )

    def test_parses_title_snippet_and_provenance_from_real_markup(self):
        from core.web_price_tool import parse_sogou_results

        rows = parse_sogou_results(self.REAL_BLOCK)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIn("博斯腾湖", row["title"])
        self.assertNotIn("<em>", row["title"], "标签要去掉")
        self.assertIn("大河口45元", row["snippet"])
        self.assertTrue(row["url"].startswith("https://www.sogou.com/link?url="))
        self.assertEqual(row["cite_site"], "相约久久旅游网")
        self.assertIn("meet99.com", row["cite_url"])
        self.assertEqual(row["cite_date"], "2026-09-19")
        self.assertEqual(row["provider"], "sogou")

    def test_price_is_extractable_from_the_real_snippet(self):
        from core.web_price_tool import extract_price_candidates, parse_sogou_results

        snippet = parse_sogou_results(self.REAL_BLOCK)[0]["snippet"]
        values = {item["value"] for item in extract_price_candidates(snippet)}
        self.assertIn(45.0, values, "真实摘要里的'大河口45元'必须能抽出来")
        self.assertIn(198.0, values)

    def test_empty_or_garbage_body_yields_nothing(self):
        from core.web_price_tool import parse_sogou_results

        self.assertEqual(parse_sogou_results(""), [])
        self.assertEqual(parse_sogou_results("<html><body>没有结果</body></html>"), [])


class ProviderOrderTests(unittest.TestCase):
    """按配置顺序试提供方，第一个有结果的就用；全失败要留下尝试记录（供披露原因）。"""

    def test_first_provider_with_results_wins(self):
        import asyncio

        from core import web_price_tool

        async def fake_sogou(query, max_results=3):
            return [{"title": "门票45元", "url": "https://www.sogou.com/link?url=x", "provider": "sogou"}]

        async def fake_ddg(query, max_results=3):
            raise AssertionError("搜狗有结果时不该再去问 DuckDuckGo")

        with isolated_env(WEB_PRICE_PROVIDERS="sogou,search"):
            with mock.patch("core.web_price_tool.fetch_sogou", fake_sogou):
                with mock.patch("core.web_price_tool.search_duckduckgo", fake_ddg):
                    rows = asyncio.run(web_price_tool.search_texts("库尔勒 门票"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(web_price_tool.last_search_attempts[0]["provider"], "sogou")
        self.assertTrue(web_price_tool.last_search_attempts[0]["ok"])

    def test_all_providers_failing_records_attempts_and_returns_empty(self):
        import asyncio

        from core import web_price_tool

        async def broken(*_args, **_kwargs):
            raise RuntimeError("boom")

        with isolated_env(WEB_PRICE_PROVIDERS="sogou,search"):
            with mock.patch("core.web_price_tool.fetch_sogou", broken):
                with mock.patch("core.web_price_tool.search_duckduckgo", broken):
                    rows = asyncio.run(web_price_tool.search_texts("库尔勒 门票"))
        self.assertEqual(rows, [])
        providers = [item["provider"] for item in web_price_tool.last_search_attempts]
        self.assertEqual(providers, ["sogou", "search"], "两个源都要试过并留痕")
        self.assertTrue(all(item["ok"] is False for item in web_price_tool.last_search_attempts))


class PickPriceTests(unittest.TestCase):
    """挑结果时**优先有出处的**：搜狗的聚合块有价格但没 URL，直接用会得到"有价无源"。"""

    def test_row_with_url_wins_over_row_without(self):
        from core.web_price_tool import pick_price_from_rows

        rows = [
            {"title": "", "snippet": "门票价格 45元/人 区间车 无区间车", "provider": "sogou"},
            {
                "title": "2026博斯腾湖旅游攻略",
                "snippet": "博斯腾湖门票价格:大河口45元",
                "url": "https://www.sogou.com/link?url=abc",
                "cite_site": "相约久久旅游网",
                "cite_date": "2026-09-19",
                "provider": "sogou",
            },
        ]
        best = pick_price_from_rows(rows)
        self.assertEqual(best["value"], 45.0)
        self.assertEqual(best["url"], "https://www.sogou.com/link?url=abc")
        self.assertEqual(best["publisher"], "相约久久旅游网")
        self.assertEqual(best["source_date"], "2026-09-19")

    def test_free_still_counts_as_a_price(self):
        from core.web_price_tool import pick_price_from_rows

        best = pick_price_from_rows([{"title": "博物馆", "snippet": "门票免费，无需预约", "url": "https://x"}])
        self.assertEqual(best["value"], 0.0)

    def test_free_outside_a_ticket_context_is_not_a_price(self):
        """实测错价（2026-10-05）：这两条都不是价格，却都被抽成了 ¥0。"""
        from core.web_price_tool import extract_price_candidates, pick_price_from_rows

        # 马拉松参赛者的免费福利 → 不是景区门票
        marathon = extract_price_candidates("凭号码布免费游,2025库尔勒半马选手福利")
        self.assertEqual(marathon, [], marathon)
        # 酒店取消条款 → 不是房价
        cancel = extract_price_candidates("餐食 今天18:00前可免费取消 · 可住时段 14:00 后")
        self.assertEqual(cancel, [], cancel)
        self.assertIsNone(
            pick_price_from_rows([
                {"title": "2025库尔勒半马", "snippet": "凭号码布免费游", "url": "https://www.xjkel.gov.cn/x"},
            ])
        )

    def test_official_source_wins_over_self_media(self):
        """同一个景区：政府页面 vs 自媒体，出处更可信的那个要被采信。"""
        from core.web_price_tool import pick_price_from_rows

        rows = [
            {"title": "博斯腾湖门票优惠", "snippet": "门票 30元 起（以现场为准）", "url": "https://www.163.com/dy/article/x.html", "cite_site": "www.163.com"},
            {"title": "博斯腾湖景区门票价格公示", "snippet": "门票价格 45元/人", "url": "https://www.xjkel.gov.cn/notice/1.html", "cite_site": "www.xjkel.gov.cn"},
        ]
        best = pick_price_from_rows(rows)
        self.assertEqual(best["value"], 45.0, best)
        self.assertTrue(best["official_source"], best)
        self.assertEqual(best["publisher"], "www.xjkel.gov.cn")

    def test_no_prices_returns_none(self):
        from core.web_price_tool import pick_price_from_rows

        self.assertIsNone(pick_price_from_rows([{"title": "无关内容", "snippet": "今天天气不错"}]))
        self.assertIsNone(pick_price_from_rows([]))


class PacingAndCacheTests(unittest.TestCase):
    """节流与缓存：实测连续快速请求会被限流（同一地点一会儿有一会儿没有）。"""

    def test_min_interval_and_cache_ttl_are_configurable(self):
        from core.web_price_tool import search_cache_ttl_seconds, search_min_interval_seconds

        with isolated_env():
            self.assertEqual(search_min_interval_seconds(), 1.2)
            self.assertEqual(search_cache_ttl_seconds(), 6 * 3600.0)
        with isolated_env(
            WEB_PRICE_MIN_INTERVAL_SECONDS="0.3",
            WEB_SEARCH_CACHE_TTL_SECONDS="60",
        ):
            self.assertEqual(search_min_interval_seconds(), 0.3)
            self.assertEqual(search_cache_ttl_seconds(), 60.0)

    def test_same_query_is_served_from_cache_without_second_request(self):
        import asyncio

        from core import web_price_tool

        calls = {"n": 0}

        async def fake_sogou(query, max_results=3):
            calls["n"] += 1
            return [{"title": "门票45元", "url": "https://x", "provider": "sogou"}]

        web_price_tool._search_cache.clear()
        web_price_tool._last_search_at = 0.0
        with isolated_env(
            WEB_PRICE_PROVIDERS="sogou",
            WEB_PRICE_MIN_INTERVAL_SECONDS="0",
        ):
            with mock.patch("core.web_price_tool.fetch_sogou", fake_sogou):
                first = asyncio.run(web_price_tool.search_texts("库尔勒 门票"))
                second = asyncio.run(web_price_tool.search_texts("库尔勒 门票"))
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(calls["n"], 1, "第二次应命中缓存，不再打扰对方站点")
        self.assertEqual(web_price_tool.last_search_attempts[0]["provider"], "cache")


class DailyQuotaTests(unittest.TestCase):
    """日请求上限开关（用户口径：避免无意中超量）。"""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        os.unlink(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {
            "WEB_PRICE_USAGE_FILE": self.tmp.name,
            "WEB_PRICE_DAILY_MAX_REQUESTS": "2",
        }, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_usage_counts_and_quota_left(self):
        from core.web_price_tool import daily_quota_left, read_usage, record_request

        self.assertEqual(read_usage()["requests"], 0)
        self.assertEqual(daily_quota_left(), 2)
        record_request()
        self.assertEqual(daily_quota_left(), 1)
        record_request(2)
        self.assertEqual(daily_quota_left(), 0)

    def test_exhausted_quota_blocks_the_search_and_says_why(self):
        import asyncio

        from core import web_price_tool

        web_price_tool._search_cache.clear()
        web_price_tool.record_request(5)
        calls = {"n": 0}

        async def fake_sogou(query, max_results=3):
            calls["n"] += 1
            return [{"title": "x", "url": "https://x"}]

        with mock.patch("core.web_price_tool.fetch_sogou", fake_sogou):
            rows = asyncio.run(web_price_tool.search_texts("库尔勒 门票"))
        self.assertEqual(rows, [])
        self.assertEqual(calls["n"], 0, "配额用完后不该再发请求")
        self.assertEqual(web_price_tool.last_search_attempts[0]["provider"], "quota")
        self.assertIn("配额已用完", web_price_tool.last_search_attempts[0]["error"])

    def test_requests_are_recorded_on_success(self):
        import asyncio

        from core import web_price_tool

        web_price_tool._search_cache.clear()
        web_price_tool._last_search_at = 0.0

        async def fake_sogou(query, max_results=3):
            return [{"title": "门票45元", "url": "https://x", "provider": "sogou"}]

        with mock.patch.dict(os.environ, {"WEB_PRICE_PROVIDERS": "sogou", "WEB_PRICE_MIN_INTERVAL_SECONDS": "0"}, clear=False):
            with mock.patch("core.web_price_tool.fetch_sogou", fake_sogou):
                asyncio.run(web_price_tool.search_texts("库尔勒 门票"))
        self.assertEqual(web_price_tool.read_usage()["requests"], 1)


class HotelAllowlistTests(unittest.TestCase):
    """酒店集团官网纳入默认白名单（用户口径）；民宿平台靠用户自己配置。"""

    def test_hotel_group_official_pages_are_allowed(self):
        for url in (
            "https://www.ihg.com/holidayinn/hotels/cn/zh/korla/rate",
            "https://www.marriott.com/hotels/travel/rate",
            "https://www.huazhu.com/hotel/123",
        ):
            allowed, reason = host_is_allowed(url)
            self.assertTrue(allowed, f"{url} 应被允许：{reason}")

    def test_booking_platforms_stay_blocked(self):
        allowed, reason = host_is_allowed("https://hotels.ctrip.com/hotel/1.html")
        self.assertFalse(allowed)
        self.assertIn("平台电商页", reason)

    def test_user_configured_domain_still_works(self):
        with mock.patch.dict(os.environ, {"WEB_PRICE_HOST_SUFFIXES": "mybnb.example.com"}, clear=False):
            allowed, reason = host_is_allowed("https://mybnb.example.com/room/1")
        self.assertTrue(allowed, reason)


class PageFetchIntegrationTests(unittest.TestCase):
    """命中白名单页面时**直抓正文**取价（比摘要可靠）。"""

    def test_official_page_price_wins_over_snippet(self):
        import asyncio

        from core import web_price_tool

        async def fake_search(query, max_results=3):
            return [
                {"title": "某酒店", "snippet": "每晚 999 元", "url": "https://hotels.ctrip.com/x", "provider": "sogou"},
                {"title": "官方房价", "url": "https://www.huazhu.com/hotel/123", "provider": "sogou"},
            ]

        async def fake_fetch(url, **_kwargs):
            return {"ok": True, "url": url, "text": "标准间 每晚 288 元 含早", "retrieved_at": 1.0}

        with mock.patch("core.web_price_tool.search_texts", fake_search):
            with mock.patch("core.web_price_tool.fetch_page_text", fake_fetch):
                observation = asyncio.run(web_price_tool.lookup_price("某酒店", "库尔勒"))
        self.assertEqual(observation["value"], 288.0, "应优先用官方页面正文里的价格")
        self.assertEqual(observation["source"], "web_page")
        self.assertEqual(observation["provider"], "official_page")
        self.assertEqual(observation["source_url"], "https://www.huazhu.com/hotel/123")
        self.assertFalse(observation["estimated"], "白名单官方页面（华住官网）的价格按「可核实」计")
        self.assertTrue(observation["verified"], "2026-10-05 用户口径：白名单官方页面 = 可核实来源")

    def test_falls_back_to_snippet_when_page_unavailable(self):
        import asyncio

        from core import web_price_tool

        async def fake_search(query, max_results=3):
            return [{"title": "博斯腾湖", "snippet": "门票45元", "url": "https://www.sogou.com/link?url=x", "provider": "sogou", "cite_site": "相约久久"}]

        with mock.patch("core.web_price_tool.search_texts", fake_search):
            observation = asyncio.run(web_price_tool.lookup_price("博斯腾湖", "库尔勒"))
        self.assertEqual(observation["value"], 45.0)
        self.assertEqual(observation["source"], "web")
        self.assertEqual(observation["publisher"], "相约久久")

    def test_nothing_found_is_honest_none(self):
        import asyncio

        from core import web_price_tool

        async def fake_search(query, max_results=3):
            return [{"title": "无关", "snippet": "今天天气不错", "url": "https://www.sogou.com/link?url=y"}]

        with mock.patch("core.web_price_tool.search_texts", fake_search):
            observation = asyncio.run(web_price_tool.lookup_price("某酒店", "库尔勒"))
        self.assertIsNone(observation["value"])
        self.assertTrue(observation["estimated"])


class PreferOfficialUpgradeTests(unittest.TestCase):
    """把"有数字没出处"的网摘价升级成白名单官方页面价（2026-10-05）。

    升级请求和"填空白"不一样：目标是**可核实来源**，所以查询词补「官网」、
    并且遍历多个白名单候选页面（填空白时只试第一个，省时间）。
    """

    def test_upgrade_query_asks_for_the_official_site(self):
        import asyncio

        from core import web_price_tool

        seen = {}

        async def fake_search(query, max_results=3):
            seen["query"] = query
            return []

        with mock.patch("core.web_price_tool.search_texts", fake_search):
            asyncio.run(web_price_tool.lookup_price("博斯腾湖", "库尔勒", prefer_official=True))
        self.assertIn("官网", seen["query"])
        self.assertEqual(seen["query"].split()[0], "库尔勒")

    def test_upgrade_walks_past_a_dead_official_page(self):
        """第一个白名单页面抓不到价格时，继续试下一个 —— 升级这类请求，官方价才是目标。"""
        import asyncio

        from core import web_price_tool

        tried = []

        async def fake_search(query, max_results=3):
            return [
                {"title": "门票", "snippet": "门票 45 元", "url": "https://www.sogou.com/link?url=x", "provider": "sogou"},
                {"title": "某景区管理局", "url": "https://www.example.gov.cn/a", "provider": "sogou"},
                {"title": "另一官方页", "url": "https://www.example.gov.cn/b", "provider": "sogou"},
            ]

        async def fake_fetch(url, **_kwargs):
            tried.append(url)
            if url.endswith("/a"):
                return {"ok": True, "url": url, "text": "本页只有简介，没有票价信息", "retrieved_at": 1.0}
            return {"ok": True, "url": url, "text": "景区门票 60 元", "retrieved_at": 1.0}

        with mock.patch("core.web_price_tool.search_texts", fake_search):
            with mock.patch("core.web_price_tool.fetch_page_text", fake_fetch):
                observation = asyncio.run(
                    web_price_tool.lookup_price("博斯腾湖", "库尔勒", prefer_official=True)
                )
        self.assertEqual(observation["value"], 60.0, observation)
        self.assertEqual(observation["source"], "web_page")
        self.assertEqual(observation["source_url"], "https://www.example.gov.cn/b")
        self.assertEqual(len(tried), 2, "应当接着试第二个白名单页面")

    def test_plain_lookup_still_falls_back_to_the_snippet(self):
        """不做升级时行为不变：官方页拿不到就退回摘要价（标 web＝估算）。"""
        import asyncio

        from core import web_price_tool

        async def fake_search(query, max_results=3):
            return [
                {"title": "门票", "snippet": "门票 45 元", "url": "https://www.sogou.com/link?url=x", "provider": "sogou"},
                {"title": "某景区管理局", "url": "https://www.example.gov.cn/a", "provider": "sogou"},
            ]

        async def fake_fetch(url, **_kwargs):
            return {"ok": True, "url": url, "text": "本页只有简介，没有票价信息", "retrieved_at": 1.0}

        with mock.patch("core.web_price_tool.search_texts", fake_search):
            with mock.patch("core.web_price_tool.fetch_page_text", fake_fetch):
                observation = asyncio.run(web_price_tool.lookup_price("博斯腾湖", "库尔勒"))
        self.assertEqual(observation["value"], 45.0)
        self.assertEqual(observation["source"], "web")
        self.assertTrue(observation["estimated"])


class FetchStableTests(unittest.TestCase):
    """偶发抓取失败要重试一次（2026-10-05 实测：真实方案里白名单 gov.cn 页面抓失败过）。"""

    def test_transient_failure_is_retried_and_then_succeeds(self):
        import asyncio

        from core import web_price_tool

        calls = []

        async def flaky_fetch(url, **_kwargs):
            calls.append(url)
            if len(calls) == 1:
                return {"ok": False, "url": url, "error": "ReadTimeout: 读取超时"}
            return {"ok": True, "url": url, "text": "景区门票 45 元", "retrieved_at": 1.0}

        with mock.patch("core.web_price_tool.fetch_page_text", flaky_fetch):
            result = asyncio.run(web_price_tool.fetch_page_text_stable("https://www.xjyn.gov.cn/a.shtml"))
        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 2, "偶发失败应重试一次")

    def test_persistent_failure_stops_at_the_attempt_limit(self):
        """持续失败不做无界重试：预算有限，失败原因多半是持续性的。"""
        import asyncio

        from core import web_price_tool

        calls = []

        async def dead_fetch(url, **_kwargs):
            calls.append(url)
            return {"ok": False, "url": url, "error": "HTTP 403"}

        with mock.patch("core.web_price_tool.fetch_page_text", dead_fetch):
            result = asyncio.run(web_price_tool.fetch_page_text_stable("https://www.xjyn.gov.cn/a.shtml"))
        self.assertFalse(result["ok"])
        self.assertEqual(len(calls), 2, "默认最多两次")

    def test_lookup_uses_the_retrying_fetch(self):
        """"偶发失败 → 退回网摘估算"这件事要真的被挡住：取价里必须用重试版抓取。"""
        import asyncio

        from core import web_price_tool

        calls = []

        async def fake_search(query, max_results=3):
            return [
                {"title": "文化馆免费开放", "snippet": "免费开放", "url": "https://www.xjyn.gov.cn/a.shtml", "provider": "baidu"},
            ]

        async def flaky_fetch(url, **_kwargs):
            calls.append(url)
            if len(calls) == 1:
                return {"ok": False, "url": url, "error": "ReadTimeout: 读取超时"}
            return {"ok": True, "url": url, "text": "伊宁县文化馆免费开放服务公示", "retrieved_at": 1.0}

        with mock.patch("core.web_price_tool.search_texts", fake_search):
            with mock.patch("core.web_price_tool.fetch_page_text", flaky_fetch):
                observation = asyncio.run(web_price_tool.lookup_price("伊宁县文化艺术中心", "伊宁"))
        self.assertEqual(observation["source"], "web_page", observation)
        self.assertFalse(observation["estimated"], "官方页面正文来的价格按可核实计")
        self.assertEqual(len(calls), 2)


class PriceStepBudgetTests(unittest.TestCase):
    """取价这一步不能"一超时就整段作废"（2026-10-05 矩阵实测：4 个场景里 3 个只剩一个 error）。

    事故形态：整体 `wait_for(gather(...), 25s)` 一旦超时抛 TimeoutError，**已经查到的价格全丢**，
    price_audit 只剩 `{"error": "TimeoutError:"}` —— 界面上表现成"取价压根没跑"
    （查了几个/补到几条/来源可达全是空）。
    """

    def _source(self):
        import io
        import pathlib

        path = pathlib.Path(__file__).resolve().parents[1] / "api" / "agent.py"
        with io.open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_budget_and_per_node_estimate_match_measured_cost(self):
        """预算与"能查几个"必须按**实测单点耗时**给（探针实测 ≈6.5s/节点）。

        上一版预算 25s + 每点按 4.2s 估算 → 号称能查 5 个，实际 4 个没轮到、补到 0 条。
        """
        from core.web_price_tool import (
            affordable_target_count,
            price_budget_seconds,
            price_timeout_seconds,
            search_min_interval_seconds,
        )

        with isolated_env():
            budget = price_budget_seconds()
            per_node = search_min_interval_seconds() + price_timeout_seconds()
            self.assertGreaterEqual(budget, 40.0, "实测单点 6.5s，25s 的预算不够 5 个点")
            self.assertGreaterEqual(per_node, 6.0, "单点估算不许比实测乐观")
            count = affordable_target_count(12, budget_seconds=budget)
            self.assertGreaterEqual(count, 4)
            self.assertLessEqual(count, 12)
            # 目标数 × 单点成本不能超出预算太多（否则必然有节点"没轮到"）
            self.assertLessEqual(count * per_node, budget + per_node)

    def test_each_lookup_carries_its_own_deadline(self):
        text = self._source()
        self.assertIn("_lookup_bounded", text, "取价必须逐点限时，而不是整体一刀切")
        self.assertIn("_deadline = _time.monotonic()", text)
        self.assertIn("timeout=min(_remaining, _per_point_cap)", text)
        # 每点的上限还要能带"是否走官方页升级"这个开关（第二轮用）
        self.assertIn("_lookup(_item, prefer_official=_want_official)", text)

    def test_official_page_upgrade_runs_after_filling_and_only_when_sources_are_alive(self):
        """第二轮（官方页升级）的触发条件必须有护栏，否则每次取价都多花 8~10s。

        真实推演教训（2026-10-05）：喀什跑完 `unsourced_before=0` —— 那批"有数字没出处"
        正是这一轮自己补出来的（空白 → 网摘价）。所以升级只能放在**补价之后**，
        并且：补到过价（源活着）+ 预算还剩 + 没被反爬挡住，三条都满足才做。
        """
        text = self._source()
        self.assertIn("第二轮", text)
        self.assertIn("and _budget_left > 5.0", text)
        self.assertIn("and not _blocked", text)
        self.assertIn("and _live_provider", text, "第一轮靠搜索缓存补到价时，源可能已经在冷却里")
        self.assertIn('"验证" in str(item.get("error") or "")', text)

    def test_price_step_locals_are_assigned_before_they_are_used(self):
        """取价段里的局部变量必须"先算后用"（AST 静态检查）。

        真实事故（2026-10-05 真实推演）：把"跳过官方页升级"的判断写在了 `_blocked` 赋值之前，
        抛 `UnboundLocalError`，被取价段外层 except 接住 —— `price_audit` 整块丢失、只剩一句 error，
        界面上表现成"取价压根没跑"。这类错单测跑不到（那段代码只在真实链路里执行），
        所以这里做一次"赋值必须早于使用"的静态检查，防止同样的顺序错再犯。
        """
        import ast
        import io

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        source = io.open(os.path.join(root, "api", "agent.py"), encoding="utf-8").read()
        tree = ast.parse(source)
        target = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_negotiate"
        )
        names = ("_blocked", "_upgraded", "_newly_unsourced", "_filled_names", "_unsourced_after_names", "_live_provider")
        first_store: dict = {}
        first_load: dict = {}
        for node in ast.walk(target):
            if not isinstance(node, ast.Name) or node.id not in names:
                continue
            if isinstance(node.ctx, ast.Store):
                first_store.setdefault(node.id, node.lineno)
            else:
                first_load.setdefault(node.id, node.lineno)
        for name in names:
            self.assertIn(name, first_store, f"{name} 在 run_negotiate 里没有被赋值？")
            if name in first_load:
                self.assertLess(
                    first_store[name],
                    first_load[name],
                    f"{name} 在第 {first_load[name]} 行就被用了，但第 {first_store[name]} 行才赋值",
                )

    def test_lookups_are_sequential_with_a_shared_deadline(self):
        """取价底层是**串行**的（全局锁 + 节流），所以并发只会排队。

        实测（2026-10-05）：6 个点并发 + 每点 7.5s 份额时，排在第 6 位的点要等前面跑完，
        早就超过自己的份额 → "6 个点全部超预算、来源可达=None"，而实际一个搜索都没发出去。
        做法：顺序执行 + 共享 deadline（每点上限 = min(剩余时间, 单点上限)）。
        """
        text = self._source()
        self.assertIn("_per_point_cap = max(", text)
        self.assertIn("for _index, _item in enumerate(_targets):", text)
        self.assertIn("_observations.append(await _lookup_bounded(_item))", text)
        self.assertNotIn("asyncio.gather(\n                                        *[_lookup_bounded(item) for item in _targets]", text)

    def test_the_whole_price_step_is_not_wrapped_in_a_global_timeout(self):
        text = self._source()
        self.assertNotIn(
            "asyncio.wait_for(\n                                        asyncio.gather(*[_lookup(item) for item in _targets])",
            text,
            "整体 wait_for 会把已查到结果一起丢掉",
        )
        self.assertIn('"timed_out": len(_timed_out)', text, "超预算的条数要如实记账")

    def test_partial_results_survive_a_timeout(self):
        """行为级验证：一个点超时，另一个点查到的价格**必须仍然被采纳**。"""
        import asyncio

        from core.price_sources import merge_observations

        async def one_fast_one_slow():
            async def _lookup_bounded(name, delay, value):
                if delay == 0:
                    return {"name": name, "value": value, "source": "web", "provider": "sogou", "estimated": True}
                await asyncio.sleep(delay)
                return {"name": name, "value": None, "source": "web", "provider": "search",
                        "estimated": True, "timeout": True, "error": "单点取价超预算（来源慢或不可达）"}

            return await asyncio.gather(
                _lookup_bounded("博斯腾湖", 0, 45.0),
                _lookup_bounded("某酒店", 5, None),
            )

        observations = asyncio.run(one_fast_one_slow())
        plan = {"route": [{"day": 1, "name": "博斯腾湖", "price": None, "price_source": "unknown"},
                          {"day": 1, "name": "某酒店", "price": None, "price_source": "unknown"}]}
        merged = merge_observations(plan, observations)
        self.assertEqual(len(merged["updated"]), 1, "超时的点不该拖累查到的点")
        self.assertEqual(merged["updated"][0]["value"], 45.0)
        self.assertEqual(merged["updated"][0]["name"], "博斯腾湖")
        self.assertEqual(sum(1 for item in observations if item.get("timeout")), 1)


class ProviderCooldownTests(unittest.TestCase):
    """提供方熔断（2026-10-05 实测：搜狗已经查不动了，每个取价点还先去问它、等它失败）。

    现象是"查了 6 个、补到 0 条、超预算 5 个"：单点预算被**死源**吃光，
    能用的源根本没轮到。规则：连续失败 2 次 → 冷却 180s → 后面的点直接跳过它。
    """

    def setUp(self):
        from core import web_price_tool

        web_price_tool.reset_provider_state()
        web_price_tool._search_cache.clear()
        web_price_tool._last_search_at = 0.0

    def tearDown(self):
        from core import web_price_tool

        web_price_tool.reset_provider_state()

    def test_two_failures_put_a_provider_into_cooldown(self):
        import asyncio

        from core import web_price_tool

        calls = {"n": 0}

        async def broken(*_args, **_kwargs):
            calls["n"] += 1
            raise RuntimeError("HTTP 503")

        with isolated_env(WEB_PRICE_PROVIDERS="sogou", WEB_PRICE_MIN_INTERVAL_SECONDS="0"):
            with mock.patch("core.web_price_tool.fetch_sogou", broken):
                asyncio.run(web_price_tool.search_texts("库尔勒 门票 A"))
                asyncio.run(web_price_tool.search_texts("库尔勒 门票 B"))
                self.assertEqual(calls["n"], 2)
                # 第三次：已在冷却期 → 不再打扰死源（但仍然会强制放行一次，见下面那条用例）
                asyncio.run(web_price_tool.search_texts("库尔勒 门票 C"))
        state = web_price_tool.provider_states()["sogou"]
        self.assertGreaterEqual(state["consecutive_failures"], 2)

    def test_a_dead_first_provider_no_longer_blocks_the_working_one(self):
        """这是那条实测事故的核心断言：死源冷却后，第二个源必须真的被问到。"""
        import asyncio

        from core import web_price_tool

        asked = []

        async def broken(*_args, **_kwargs):
            asked.append("sogou")
            raise RuntimeError("HTTP 503")

        async def working(*_args, **_kwargs):
            asked.append("search")
            return [{"title": "门票45元", "url": "https://x", "provider": "search"}]

        with isolated_env(WEB_PRICE_PROVIDERS="sogou,search", WEB_PRICE_MIN_INTERVAL_SECONDS="0"):
            with mock.patch("core.web_price_tool.fetch_sogou", broken):
                with mock.patch("core.web_price_tool.search_duckduckgo", working):
                    asyncio.run(web_price_tool.search_texts("库尔勒 门票 A"))
                    asked.clear()
                    asyncio.run(web_price_tool.search_texts("库尔勒 门票 B"))
                    asked.clear()
                    rows = asyncio.run(web_price_tool.search_texts("库尔勒 门票 C"))
        self.assertEqual(len(rows), 1, "第二个源的结果必须真的拿到")
        self.assertEqual(asked, ["search"], f"死源进冷却后不该再逐个点去问它，实际问了 {asked}")

    def test_success_resets_the_failure_counter(self):
        import asyncio

        from core import web_price_tool

        async def broken(*_args, **_kwargs):
            raise RuntimeError("boom")

        async def working(*_args, **_kwargs):
            return [{"title": "门票45元", "url": "https://x"}]

        with isolated_env(WEB_PRICE_PROVIDERS="sogou", WEB_PRICE_MIN_INTERVAL_SECONDS="0"):
            with mock.patch("core.web_price_tool.fetch_sogou", broken):
                asyncio.run(web_price_tool.search_texts("库尔勒 A"))
            with mock.patch("core.web_price_tool.fetch_sogou", working):
                asyncio.run(web_price_tool.search_texts("库尔勒 B"))
        self.assertEqual(web_price_tool.provider_states()["sogou"]["consecutive_failures"], 0)
        self.assertTrue(web_price_tool.provider_states()["sogou"]["available"])

    def test_cooldown_expires(self):
        from core import web_price_tool

        with isolated_env(WEB_PRICE_PROVIDER_COOLDOWN_SECONDS="60"):
            web_price_tool._note_provider_result("sogou", False, now=1000.0)
            web_price_tool._note_provider_result("sogou", False, now=1000.0)
            self.assertFalse(web_price_tool.provider_is_available("sogou", now=1030.0))
            self.assertTrue(web_price_tool.provider_is_available("sogou", now=1061.0))

    def test_all_providers_cooling_down_still_tries_one(self):
        """兜底：全都冷却时也要强制试一次 —— 熔断不能把功能直接关掉。"""
        import asyncio

        from core import web_price_tool

        asked = []

        async def working(*_args, **_kwargs):
            asked.append("sogou")
            return [{"title": "门票45元", "url": "https://x"}]

        with isolated_env(WEB_PRICE_PROVIDERS="sogou", WEB_PRICE_MIN_INTERVAL_SECONDS="0"):
            web_price_tool._note_provider_result("sogou", False)
            web_price_tool._note_provider_result("sogou", False)
            with mock.patch("core.web_price_tool.fetch_sogou", working):
                rows = asyncio.run(web_price_tool.search_texts("库尔勒 门票"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(asked, ["sogou"])

    def test_cooldown_is_explained_in_the_attempts(self):
        import asyncio

        from core import web_price_tool

        async def broken(*_args, **_kwargs):
            raise RuntimeError("HTTP 503")

        with isolated_env(WEB_PRICE_PROVIDERS="sogou", WEB_PRICE_MIN_INTERVAL_SECONDS="0"):
            web_price_tool._note_provider_result("sogou", False)
            web_price_tool._note_provider_result("sogou", False)
            with mock.patch("core.web_price_tool.fetch_sogou", broken):
                asyncio.run(web_price_tool.search_texts("库尔勒 门票"))
        override = [item for item in web_price_tool.last_search_attempts if item.get("cooldown_overridden")]
        self.assertTrue(override, web_price_tool.last_search_attempts)
        self.assertIn("冷却", str(override[0].get("error")))


class BaiduProviderTests(unittest.TestCase):
    """百度作为国内可用搜索源（2026-10-05 实测：搜狗/必应被打成填充页、DuckDuckGo 时通时不通）。

    样本结构照抄**真实**百度结果页（tools/dump_baidu_sample.py 抓到的那一份），
    标题/摘要/落地页都是真实内容，只是把巨大的 JSON 内联数据裁掉了。
    """

    SAMPLE = """
    <div class="result-op c-container new-pmd" srcid="62744" id="1" tpl="right_citation_aggregated" mu="null">
      <div class="c-container"><h3><a href="http://www.baidu.com/link?url=xxx">热搜榜</a></h3></div>
    </div>
    <div class="result-op c-container new-pmd" srcid="51527" id="3" tpl="sg_kg_entity_san"
         mu="https://baike.baidu.com/item/%E5%8D%9A%E6%96%AF%E8%85%BE%E6%B9%96">
      <div class="c-container"><h3><a href="http://www.baidu.com/link?url=yyy">
        <em>巴音郭楞蒙古自治州博斯腾湖景区</em>(新疆维吾尔自治区巴音...</a></h3>
        <div class="summary-text_15QGa">新疆维吾尔自治区巴音郭楞蒙古自治州的国家5A级旅游景区</div>
      </div>
    </div>
    <div class="result c-container xpath-log new-pmd" srcid="1599" id="4" tpl="www_index"
         mu="https://baijiahao.baidu.com/s?id=1877664545352972512&amp;wfr=spider&amp;for=pc">
      <div class="cosc-card"><div><h3 class="cosc-title">
        <a href="http://www.baidu.com/link?url=zzz"><span><!--s-text--><em>博斯腾湖门票45元</em>被说“踩坑”环湖免费与景区收费,游客该分开看<!--/s-text--></span></a>
        </h3><div class="summary-text_15QGa">博斯腾湖门票45元 被说“踩坑”环湖免费与景区收费 ,游客该分开看 一场有关博斯腾湖旅游体验的争论</div>
      </div></div>
    </div>
    <div class="result c-container xpath-log new-pmd" srcid="1701" id="5" tpl="www_index"
         mu="https://www.cncn.com/jingdian/12345">
      <div class="c-container"><h3><a href="http://www.baidu.com/link?url=www">
        <em>博斯腾湖门票_博斯腾门票价格_博斯腾门票多少钱</em></a></h3>
        <div class="summary-text_15QGa">单票 -\\-南岸景区成人票50元</div>
      </div>
    </div>
    """

    def test_real_baidu_markup_is_parsed(self):
        from core.web_price_tool import parse_baidu_results

        rows = parse_baidu_results(self.SAMPLE, limit=5)
        titles = [row["title"] for row in rows]
        self.assertEqual(len(rows), 3, titles)
        self.assertTrue(any("博斯腾湖门票45元" in title for title in titles), titles)
        self.assertTrue(any("博斯腾湖门票_" in title for title in titles), titles)
        # 百度内部 widget（热搜榜）不是搜索结果，要跳过
        self.assertFalse(any("热搜" in title for title in titles), titles)
        # 百科实体卡是真结果（保留），但它的正文里混着渲染用的内联 JSON —— 不能漏进摘要
        for row in rows:
            self.assertNotIn('"size"', row["snippet"], row)
            self.assertNotIn('":', row["snippet"], row)

    def test_real_destination_url_comes_from_mu_not_the_redirect(self):
        from core.web_price_tool import parse_baidu_results

        rows = parse_baidu_results(self.SAMPLE, limit=5)
        entry = next(row for row in rows if "45元" in row["title"])
        self.assertTrue(entry["url"].startswith("https://baijiahao.baidu.com/s?"), entry["url"])
        self.assertIn("&wfr=spider", entry["url"], "HTML 实体要还原")
        self.assertEqual(entry["cite_site"], "baijiahao.baidu.com")
        self.assertEqual(entry["provider"], "baidu")

    def test_price_is_extractable_from_the_real_row(self):
        from core.web_price_tool import parse_baidu_results, pick_price_from_rows

        rows = parse_baidu_results(self.SAMPLE, limit=5)
        best = pick_price_from_rows(rows)
        self.assertIsNotNone(best)
        self.assertEqual(best["value"], 45.0, best)
        self.assertEqual(best["publisher"], "baijiahao.baidu.com")

    def test_empty_or_garbage_html_yields_nothing(self):
        from core.web_price_tool import parse_baidu_results

        self.assertEqual(parse_baidu_results("", limit=3), [])
        self.assertEqual(parse_baidu_results("<html><body>nothing</body></html>", limit=3), [])

    def test_baidu_is_first_in_the_default_order(self):
        from core.web_price_tool import enabled_providers

        with isolated_env():
            self.assertEqual(enabled_providers()[0], "baidu", enabled_providers())


class PriceContextRankingTests(unittest.TestCase):
    """同一条摘要里"门票45元"和"环湖免费"同时出现时，不能挑 免费（2026-10-05 实测挑错了）。"""

    def test_ticket_context_beats_free(self):
        from core.web_price_tool import extract_price_candidates, pick_price_from_rows

        rows = [{
            "title": "博斯腾湖门票45元被说“踩坑”环湖免费与景区收费",
            "snippet": "焦点不是45元门票，而是游客为什么付费。环湖免费。",
            "url": "https://baijiahao.baidu.com/s?id=1",
            "provider": "baidu",
            "cite_site": "baijiahao.baidu.com",
        }]
        best = pick_price_from_rows(rows)
        self.assertEqual(best["value"], 45.0, best)
        # 直接抽取时，语境强的也要排在前面
        candidates = extract_price_candidates(rows[0]["title"] + " " + rows[0]["snippet"])
        self.assertEqual(candidates[0]["value"], 45.0, candidates)

    def test_free_still_wins_when_it_is_the_ticket_price(self):
        from core.web_price_tool import pick_price_from_rows

        rows = [{
            "title": "孔雀河景观带门票免费开放",
            "snippet": "门票：免费",
            "url": "https://www.xinjiang.gov.cn/x",
            "provider": "baidu",
        }]
        self.assertEqual(pick_price_from_rows(rows)["value"], 0.0)


class PriceStepEarlyStopTests(unittest.TestCase):
    """取价这一步的"所有源都死了就别硬撑"与"每个源的原因都要说出来"。"""

    def _source(self):
        import io
        import pathlib

        path = pathlib.Path(__file__).resolve().parents[1] / "api" / "agent.py"
        with io.open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_price_step_stops_early_when_every_source_is_dead(self):
        """所有源都不可用时逐点重试是白花时间：实测 27.4s、补到 0 条。

        前两个点都没换来价格就停下，剩下的节点留给「需要你确认」，并如实说明原因。
        """
        text = self._source()
        self.assertIn("_early_stop_reason", text)
        self.assertIn("所有取价源连续失败，已提前停止", text)
        self.assertIn('"early_stop_reason": _early_stop_reason', text)

    def test_provider_summary_is_reported_per_source(self):
        """只说"来源可达=False"看不出是"被反爬挡住"还是"根本没查"。"""
        text = self._source()
        self.assertIn("_provider_summary", text)
        self.assertIn('"provider_summary": _provider_summary', text)


class PriceFailureDisclosureTests(unittest.TestCase):
    def test_unreachable_price_source_names_each_provider(self):
        from core.degradation import collect_degradations

        payload = {
            "price_audit": {
                "provider_reachable": False,
                "provider_status": {"error": "baidu 返回安全验证页（需要会话/被限流）"},
                "provider_summary": "baidu：baidu 返回安全验证页；sogou：0 条结果；search：0 条结果",
            }
        }
        items = collect_degradations(payload).get("items", [])
        entry = next((item for item in items if item.get("source") == "price_web"), None)
        self.assertIsNotNone(entry, items)
        self.assertIn("各源情况", str(entry.get("reason")))
        self.assertIn("baidu", str(entry.get("reason")))
        self.assertIn("WEB_SEARCH_PROXY", str(entry.get("reason")))


if __name__ == "__main__":
    unittest.main()
