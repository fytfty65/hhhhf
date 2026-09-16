"""Tests for the Prometheus exposition on the AI service.

The JSON /metrics endpoint only exposes a lifetime average latency, which cannot
back an SLO. These tests pin what a scraper needs: text exposition format,
cumulative buckets ending in +Inf, and sum/count series so quantiles are
computable from the scrape.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.observability import LATENCY_BUCKETS_MS, MetricsRegistry, _escape_label


def parse_metric(text, needle):
    """Return the numeric value of the first line containing `needle`."""
    for line in text.splitlines():
        if needle in line:
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"metric containing {needle!r} not found in:\n{text}")


class PrometheusExpositionTests(unittest.TestCase):
    def setUp(self):
        self.metrics = MetricsRegistry()

    def test_required_series_are_present(self):
        self.metrics.observe("GET", "/health", 200, 3.0)
        text = self.metrics.prometheus()
        for expected in (
            "# TYPE omniroute_ai_up gauge",
            "omniroute_ai_up 1",
            "# TYPE omniroute_ai_requests_total counter",
            "# TYPE omniroute_ai_responses_total counter",
            'omniroute_ai_responses_total{status="200"} 1',
            "# TYPE omniroute_ai_request_duration_milliseconds histogram",
            'method="GET",path="/health"',
            'le="+Inf"',
            "_duration_milliseconds_sum{",
            "_duration_milliseconds_count{",
        ):
            self.assertIn(expected, text)

    def test_buckets_are_cumulative_and_inf_matches_count(self):
        for latency in (3.0, 20.0, 20.0, 9200.0):
            self.metrics.observe("POST", "/x", 200, latency)
        text = self.metrics.prometheus()

        cumulative = [
            parse_metric(text, f'path="/x",le="{bound:g}"}}') for bound in LATENCY_BUCKETS_MS
        ]
        for i in range(1, len(cumulative)):
            self.assertGreaterEqual(
                cumulative[i], cumulative[i - 1], "buckets must be cumulative"
            )
        self.assertEqual(parse_metric(text, 'path="/x",le="+Inf"}'), 4)
        # 5ms and 20ms samples fall in the first two buckets.
        self.assertEqual(cumulative[0], 1)
        self.assertEqual(cumulative[2], 3)

    def test_sum_and_count_match_observations(self):
        for latency in (10.0, 20.0, 30.0):
            self.metrics.observe("GET", "/y", 200, latency)
        text = self.metrics.prometheus()
        self.assertEqual(parse_metric(text, '_count{method="GET",path="/y"}'), 3)
        self.assertEqual(parse_metric(text, '_sum{method="GET",path="/y"}'), 60)

    def test_quantiles_are_computable_from_the_scrape(self):
        """A p95 computed from the buckets must show the tail, not the average.

        Four fast samples and one very slow one: the mean is ~1.8s (misleadingly
        healthy) while the le=5000 bucket only covers 80%, which is exactly the
        signal the average hides.
        """
        for latency in (10.0, 12.0, 11.0, 13.0, 9000.0):
            self.metrics.observe("POST", "/slow", 200, latency)
        text = self.metrics.prometheus()
        total = parse_metric(text, 'path="/slow",le="+Inf"}')
        le5000 = parse_metric(text, 'path="/slow",le="5000"}')
        self.assertEqual(total, 5)
        self.assertEqual(le5000, 4)
        self.assertLess(le5000 / total, 0.95, "p95 must not be satisfiable below 5s")

    def test_slowest_bucket_exceeds_observed_maximum(self):
        # The service proxies provider calls observed at ~9s; a histogram topping
        # out below that would push every slow request into +Inf.
        self.assertGreaterEqual(max(LATENCY_BUCKETS_MS), 30000)
        for i in range(1, len(LATENCY_BUCKETS_MS)):
            self.assertGreater(LATENCY_BUCKETS_MS[i], LATENCY_BUCKETS_MS[i - 1])

    def test_extra_gauges_are_rendered_and_non_numeric_skipped(self):
        text = self.metrics.prometheus(
            extra={"bandit_rewards_recorded": 3, "flag": True, "label": "nope", "rate": 0.25}
        )
        self.assertIn('omniroute_ai_gauge{name="bandit_rewards_recorded"} 3', text)
        self.assertIn('omniroute_ai_gauge{name="rate"} 0.25', text)
        self.assertNotIn('name="flag"', text)
        self.assertNotIn('name="label"', text)

    def test_label_values_are_escaped(self):
        self.assertEqual(_escape_label('a"b'), 'a\\"b')
        self.assertEqual(_escape_label("a\\b"), "a\\\\b")
        self.assertEqual(_escape_label("a\nb"), "a\\nb")

        self.metrics.observe("GET", '/weird"path', 200, 1.0)
        text = self.metrics.prometheus()
        self.assertIn('path="/weird\\"path"', text)

    def test_metric_lines_are_well_formed(self):
        self.metrics.observe("GET", "/z", 200, 1.0)
        for line in self.metrics.prometheus().splitlines():
            if not line or line.startswith("#"):
                continue
            self.assertFalse(line.startswith(" "), f"line starts with space: {line!r}")
            self.assertTrue(
                re.match(r"^[a-zA-Z_:][a-zA-Z0-9_:]*(\{.*\})? -?[\d.eE+]+$", line),
                f"malformed metric line: {line!r}",
            )

    def test_json_snapshot_contract_is_unchanged(self):
        """The JSON endpoint keeps its shape for existing consumers."""
        self.metrics.observe("GET", "/health", 200, 4.0)
        snapshot = self.metrics.snapshot()
        self.assertEqual(snapshot["service"], "ai-service")
        self.assertEqual(snapshot["requests_total"], 1)
        self.assertEqual(snapshot["requests_by_status"]["200"], 1)
        self.assertEqual(snapshot["requests_by_path"]["GET /health"]["requests"], 1)
        self.assertEqual(snapshot["requests_by_path"]["GET /health"]["avg_latency_ms"], 4.0)


if __name__ == "__main__":
    unittest.main()
