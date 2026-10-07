"""官方域名**候选发现**的测试（2026-10-05）。

用户口径是"把 gov/景区官网页面（**白名单里的**）视作可核实来源"——
所以白名单必须人工确认。系统能做的是**提议**：从搜索结果里认出"像官网"的域名，
记进待确认清单，人看完再加进 `WEB_PRICE_HOST_SUFFIXES`。
这里钉住三件事：只挑像官网的、绝不自动信任、记录失败不影响取价。
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import web_price_tool  # noqa: E402


def rows(*items):
    return list(items)


class DomainHintTests(unittest.TestCase):
    def test_official_looking_domains_are_proposed(self):
        hints = web_price_tool.official_domain_hints(
            rows(
                {"title": "博斯腾湖景区官方网站 - 门票预订", "url": "https://www.bostenhu.com/ticket", "cite_site": "博斯腾湖景区"},
                {"title": "某旅行社报价", "url": "https://www.example-travel.com/x", "cite_site": "某旅行社"},
                {"title": "且末县文化和旅游局", "url": "https://www.qiemu-tour.cn/a", "cite_site": "且末文旅"},
            ),
            query_name="博斯腾湖",
        )
        hosts = [item["host"] for item in hints]
        self.assertIn("www.bostenhu.com", hosts)
        self.assertIn("www.qiemu-tour.cn", hosts)
        self.assertNotIn("www.example-travel.com", hosts, "没有官方身份词的普通站点不该被提议")
        # 注意「官方网站」里并不含连续的「官网」——断言用「官方」这种一定在标记里的词
        self.assertIn("官方", hints[0]["why"])

    def test_whitelisted_and_platform_hosts_are_not_proposed(self):
        hints = web_price_tool.official_domain_hints(
            rows(
                {"title": "某景区官网", "url": "https://www.kanas.gov.cn/ticket"},
                {"title": "某景区官方网站", "url": "https://www.ctrip.com/x"},
                {"title": "某景区官方网站", "url": "https://www.meituan.com/y"},
            )
        )
        self.assertEqual(hints, [], "已在白名单里的、以及平台电商页都不必提议")

    def test_only_search_result_evidence_is_used(self):
        """只说"域名像"不够：必须有标题/站点名里的官方身份词（否则就是在猜）。"""
        hints = web_price_tool.official_domain_hints(
            rows({"title": "2026 最新门票价格", "url": "https://www.xxx-scenic.com/a"})
        )
        self.assertEqual(hints, [])

    def test_real_url_field_and_limit(self):
        hints = web_price_tool.official_domain_hints(
            rows(
                {"title": "甲景区官网", "href": "https://a-scenic.cn/1"},
                {"title": "乙景区官网", "url": "https://b-scenic.cn/2"},
                {"title": "丙景区官网", "url": "https://c-scenic.cn/3"},
            ),
            max_items=2,
        )
        self.assertEqual(len(hints), 2)
        self.assertEqual(hints[0]["host"], "a-scenic.cn")

    def test_junk_rows_are_ignored(self):
        self.assertEqual(web_price_tool.official_domain_hints(rows(None, {}, {"url": "not-a-url"})), [])
        self.assertEqual(web_price_tool.official_domain_hints([]), [])


class DomainRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="domain-candidates-")
        self.path = os.path.join(self.tmp, "candidates.json")
        self.env = mock.patch.dict(os.environ, {"WEB_PRICE_DOMAIN_CANDIDATES_FILE": self.path}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_candidates_are_appended_and_deduped(self):
        first = web_price_tool.record_domain_candidates(
            [{"host": "a-scenic.cn", "url": "https://a-scenic.cn/1", "title": "甲景区官网", "why": "标题里出现「官网」"}]
        )
        self.assertEqual(first["recorded"], 1)
        second = web_price_tool.record_domain_candidates(
            [{"host": "a-scenic.cn", "url": "https://a-scenic.cn/2", "title": "甲景区官网"}]
        )
        self.assertEqual(second["recorded"], 0)
        self.assertIn("already_known", second)
        with open(self.path, encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(len(stored["candidates"]), 1)
        self.assertIn("recorded_at", stored["candidates"][0])

    def test_recording_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {"WEB_PRICE_RECORD_DOMAIN_CANDIDATES": "0"}, clear=False):
            result = web_price_tool.record_domain_candidates([{"host": "a.cn"}])
        self.assertEqual(result["recorded"], 0)
        self.assertFalse(os.path.exists(self.path))

    def test_no_hints_is_a_noop(self):
        result = web_price_tool.record_domain_candidates([])
        self.assertEqual(result["recorded"], 0)
        self.assertFalse(os.path.exists(self.path))

    def test_load_handles_missing_and_broken_files(self):
        self.assertEqual(web_price_tool.load_domain_candidates(), [])
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{不是 JSON")
        self.assertEqual(web_price_tool.load_domain_candidates(), [])


class LookupWiringTests(unittest.TestCase):
    """取价链路里的接线：白名单页面**没帮上忙**时才记候选，且记录失败不影响价格。"""

    def test_snippet_fallback_records_candidates(self):
        import asyncio

        recorded = {}

        async def fake_search(query, max_results=3, **_kwargs):
            return [
                {"title": "博斯腾湖景区官方网站", "snippet": "门票 45 元", "url": "https://www.bostenhu.com/t", "provider": "baidu"},
            ]

        def fake_record(hints, *args, **kwargs):
            recorded["hints"] = list(hints)
            return {"recorded": len(hints)}

        with mock.patch("core.web_price_tool.search_texts", fake_search):
            with mock.patch("core.web_price_tool.record_domain_candidates", fake_record):
                observation = asyncio.run(web_price_tool.lookup_price("博斯腾湖", "库尔勒"))
        self.assertEqual(observation["value"], 45.0)
        self.assertEqual(observation["source"], "web", "记录候选不改变价格口径（仍是估算）")
        self.assertEqual([item["host"] for item in recorded["hints"]], ["www.bostenhu.com"])

    def test_whitelisted_page_price_does_not_record_candidates(self):
        """能从白名单页面拿到价时不必扩白名单（没有收益的动作不做）。"""
        import asyncio

        calls = []

        async def fake_search(query, max_results=3, **_kwargs):
            return [
                {"title": "某景区官网", "url": "https://www.kanas.gov.cn/ticket", "provider": "baidu"},
                {"title": "另一景区官网", "url": "https://other-scenic.cn/x", "provider": "baidu"},
            ]

        async def fake_fetch(url, **_kwargs):
            return {"ok": True, "url": url, "text": "门票 60 元", "retrieved_at": 1.0}

        with mock.patch("core.web_price_tool.search_texts", fake_search):
            with mock.patch("core.web_price_tool.fetch_page_text", fake_fetch):
                with mock.patch("core.web_price_tool.record_domain_candidates", lambda *a, **k: calls.append(a)):
                    observation = asyncio.run(web_price_tool.lookup_price("某景区", "阿勒泰"))
        self.assertEqual(observation["source"], "web_page")
        self.assertEqual(calls, [])

    def test_recording_failure_never_breaks_the_price(self):
        import asyncio

        async def fake_search(query, max_results=3, **_kwargs):
            return [{"title": "甲景区官网", "snippet": "门票 30 元", "url": "https://a-scenic.cn/1", "provider": "baidu"}]

        def boom(*args, **kwargs):
            raise RuntimeError("磁盘满了")

        with mock.patch("core.web_price_tool.search_texts", fake_search):
            with mock.patch("core.web_price_tool.record_domain_candidates", boom):
                observation = asyncio.run(web_price_tool.lookup_price("甲景区", "某城"))
        self.assertEqual(observation["value"], 30.0, "记录候选失败绝不能影响取价结果")


if __name__ == "__main__":
    unittest.main()
