"""缓存治理与 LLM 记账的回归测试（第 11 项：依赖图/缓存继续治理）。

这些断言锁定的是"可被追问"的口径：
- 每个缓存有容量上限与 TTL，超限淘汰最久未用的，而不是整体 clear；
- 命中率、过期数、淘汰数能被 /metrics 读到；
- LLM 有次数/失败率/延迟分位；未配置单价时 cost 必须是 None，不许编账单。
"""

import os
import unittest
from unittest import mock

from core.cache_registry import BoundedTTLCache, cache_metrics, register_cache
from core.llm_metrics import LLMCallMetrics


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class BoundedTTLCacheTests(unittest.TestCase):
    def test_capacity_evicts_least_recently_used_not_everything(self):
        cache = BoundedTTLCache("unit_capacity", max_entries=3, ttl_seconds=60)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        self.assertEqual(cache.get("a"), 1, "命中 a 应把它挪到最近使用")
        cache.set("d", 4)
        self.assertEqual(len(cache), 3)
        self.assertIsNone(cache.get("b"), "最久未用的是 b，应被淘汰")
        self.assertEqual(cache.get("a"), 1)
        self.assertEqual(cache.get("d"), 4)
        self.assertEqual(cache.evictions, 1)

    def test_ttl_expiry_and_hit_rate(self):
        clock = _FakeClock()
        cache = BoundedTTLCache("unit_ttl", max_entries=8, ttl_seconds=30, clock=clock)
        cache.set("k", {"v": 1})
        self.assertEqual(cache.get("k"), {"v": 1})
        clock.advance(31)
        self.assertIsNone(cache.get("k"), "超过 TTL 必须失效")
        stats = cache.stats()
        self.assertEqual(stats["expired"], 1)
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["hit_rate"], 0.5)
        self.assertNotIn("k", cache.entries)

    def test_per_entry_ttl_overrides_default(self):
        clock = _FakeClock()
        cache = BoundedTTLCache("unit_entry_ttl", max_entries=8, ttl_seconds=3600, clock=clock)
        cache.set("short", "x", ttl=10)
        cache.set("long", "y")
        clock.advance(11)
        self.assertIsNone(cache.get("short"))
        self.assertEqual(cache.get("long"), "y")

    def test_metrics_expose_registered_caches(self):
        cache = register_cache(BoundedTTLCache("unit_registered", max_entries=4, ttl_seconds=60))
        cache.set("a", 1)
        cache.get("a")
        cache.get("missing")
        snapshot = cache_metrics()
        self.assertIn("unit_registered", snapshot)
        self.assertEqual(snapshot["unit_registered"]["entries"], 1)
        self.assertEqual(snapshot["unit_registered"]["max_entries"], 4)
        self.assertAlmostEqual(snapshot["unit_registered"]["hit_rate"], 0.5)


class LlmCallMetricsTests(unittest.TestCase):
    def test_counts_failures_latency_and_percentiles(self):
        metrics = LLMCallMetrics()
        for latency in (100.0, 200.0, 300.0):
            metrics.record(
                model="qwen-plus",
                label="main_plan",
                latency_ms=latency,
                ok=True,
                prompt_tokens=1000,
                completion_tokens=500,
            )
        metrics.record(model="qwen-plus", label="main_plan", latency_ms=900.0, ok=False, error="TimeoutError")
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["calls"], 4)
        self.assertEqual(snapshot["failures"], 1)
        self.assertAlmostEqual(snapshot["failure_rate"], 0.25)
        self.assertEqual(snapshot["latency_ms"]["p50"], 200.0)
        self.assertEqual(snapshot["latency_ms"]["p95"], 900.0)
        self.assertEqual(snapshot["prompt_tokens"], 3000)
        self.assertEqual(snapshot["completion_tokens"], 1500)
        self.assertEqual(snapshot["by_label"]["main_plan"]["failures"], 1)
        self.assertEqual(snapshot["by_label"]["main_plan"]["last_error"], "TimeoutError")

    def test_cost_is_none_unless_unit_prices_are_configured(self):
        metrics = LLMCallMetrics()
        metrics.record(
            model="qwen-plus",
            label="missing_days",
            latency_ms=10.0,
            ok=True,
            prompt_tokens=1000,
            completion_tokens=1000,
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in ("LLM_PRICE_INPUT_PER_1K", "LLM_PRICE_OUTPUT_PER_1K"):
                os.environ.pop(key, None)
            snapshot = metrics.snapshot()
        self.assertFalse(snapshot["cost_configured"])
        self.assertIsNone(snapshot["cost"], "没配单价就不能编一个像账单的数字")

        with mock.patch.dict(
            os.environ,
            {"LLM_PRICE_INPUT_PER_1K": "0.002", "LLM_PRICE_OUTPUT_PER_1K": "0.006"},
            clear=False,
        ):
            snapshot = metrics.snapshot()
        self.assertTrue(snapshot["cost_configured"])
        self.assertAlmostEqual(snapshot["cost"], 0.008, places=6)

    def test_streaming_without_usage_is_reported_not_fabricated(self):
        metrics = LLMCallMetrics()
        metrics.record(
            model="qwen-plus",
            label="main_plan",
            latency_ms=5000.0,
            ok=True,
            streamed=True,
            completion_tokens=600,
            first_token_ms=800.0,
        )
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["usage_missing_calls"], 1)
        self.assertEqual(snapshot["streams"], 1)
        self.assertEqual(snapshot["first_token_ms"]["p95"], 800.0)


if __name__ == "__main__":
    unittest.main()
