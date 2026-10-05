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
            # 默认顺序把**国内直连可达**的搜狗放前面（实测 DuckDuckGo/Bing 在本机不可达）
            self.assertEqual(enabled_providers(), ["sogou", "search", "official_page"])
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

        best = pick_price_from_rows([{"title": "博物馆", "snippet": "门票价格：免费", "url": "https://x"}])
        self.assertEqual(best["value"], 0.0)

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
        self.assertTrue(observation["estimated"], "抓来的页面价仍是估算，不能标已核实")

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
        self.assertIn("asyncio.wait_for(\n                                                _lookup(_item), timeout=min(_remaining, _fair_share)", text)

    def test_each_lookup_gets_a_fair_share_of_the_budget(self):
        """只按"剩余预算"给每个点限时，第一个慢点就能吃光全部预算 —— 后面几个点全部
        "没轮到"，实测表现就是"查了 5 个、补到 0 条"（2026-10-05 矩阵 3/4 个场景）。"""
        text = self._source()
        self.assertIn("_fair_share = max(", text)
        self.assertIn("float(_price_budget) / max(1, len(_targets))", text)

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


if __name__ == "__main__":
    unittest.main()
