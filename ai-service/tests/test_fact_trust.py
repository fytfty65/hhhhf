"""事实可信度分级（P3）的测试（2026-10-08）。

这个模块的**每一条规则都是"不许骗人"的具体化**，所以测试重点全在边界上：
模型自报的置信度必须被丢掉、缺观测时间不许算新鲜、过期要展示但明说旧了、
没有值就是 unknown（不是 0、不是估算）。
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.fact_trust import (  # noqa: E402
    TRUST_ESTIMATED,
    TRUST_LABELS,
    TRUST_STALE,
    TRUST_UNKNOWN,
    TRUST_VERIFIED,
    fact_trust,
    freshness_of,
    normalize_source_type,
    trust_summary,
    worst_trust,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def days_ago(n):
    return (NOW - timedelta(days=n)).isoformat()


class SourceTypeTests(unittest.TestCase):
    def test_existing_backend_markers_are_normalized(self):
        """上游已有的写法要能归一，不能要求上游改字段。"""
        cases = {
            "official": "official_page",
            "web_page": "official_page",
            "official_site_registry": "official_page",
            "mct": "government",
            "amap": "amap",
            "rollinggo": "rollinggo",
            "platform": "platform",
            "web": "web",
            "llm": "model_inference",
            "provider": "poi_provider",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_source_type(raw), expected, raw)

    def test_prefix_and_case_forms_are_tolerated(self):
        self.assertEqual(normalize_source_type("AMAP.poi.search"), "amap")
        self.assertEqual(normalize_source_type("official_page:byblk"), "official_page")

    def test_unknown_marker_is_none_not_guessed(self):
        """认不出来的来源一律 none —— 不猜成 official，也不猜成 web。"""
        for raw in ("", None, "某个没见过的来源", "mystery"):
            self.assertEqual(normalize_source_type(raw), "none", str(raw))


class FreshnessTests(unittest.TestCase):
    def test_missing_observed_at_is_unknown_not_fresh(self):
        """**缺观测时间不等于新鲜**（这条最容易被"看起来是新的"糊过去）。"""
        for raw in (None, "", "不是时间"):
            self.assertEqual(freshness_of(raw, fact_kind="price", now=NOW), "unknown", str(raw))

    def test_price_window_is_shorter_than_culture_window(self):
        """同一天的数据，价格算旧了、文化背景还算新 —— 保质期按事实种类走。"""
        moment = days_ago(100)
        self.assertEqual(freshness_of(moment, fact_kind="price", now=NOW), "stale")
        self.assertEqual(freshness_of(moment, fact_kind="culture", now=NOW), "fresh")

    def test_fresh_recent_stale_boundaries(self):
        # price 窗口 30 天：≤15 天 fresh，≤30 天 recent，>30 天 stale
        self.assertEqual(freshness_of(days_ago(1), fact_kind="price", now=NOW), "fresh")
        self.assertEqual(freshness_of(days_ago(20), fact_kind="price", now=NOW), "recent")
        self.assertEqual(freshness_of(days_ago(31), fact_kind="price", now=NOW), "stale")

    def test_future_timestamp_is_not_treated_as_fresh(self):
        """未来时间说明时区或数据有问题，不能趁机算成 fresh。"""
        future = (NOW + timedelta(days=3)).isoformat()
        self.assertEqual(freshness_of(future, fact_kind="price", now=NOW), "unknown")


class FactTrustTests(unittest.TestCase):
    def test_official_and_fresh_is_verified(self):
        item = fact_trust(
            value="¥140",
            source="official",
            observed_at=days_ago(2),
            fact_kind="price",
            now=NOW,
        )
        self.assertEqual(item["trust"], TRUST_VERIFIED)
        self.assertEqual(item["label"], "已核实")
        self.assertEqual(item["source_type"], "official_page")
        self.assertGreater(item["confidence"], 0.5)

    def test_missing_value_is_unknown_not_zero(self):
        """**unknown ≠ 0 ≠ 估算**：没有值就是 unknown，绝不当 0 元。"""
        item = fact_trust(value=None, source="official", observed_at=days_ago(1), now=NOW)
        self.assertEqual(item["trust"], TRUST_UNKNOWN)
        self.assertIsNone(item["value"], "不许把 None 变成 0")
        self.assertEqual(item["confidence"], 0.0)
        self.assertIn("官方渠道", item["message"])

    def test_empty_string_value_is_also_unknown(self):
        item = fact_trust(value="   ", source="amap", observed_at=days_ago(1), now=NOW)
        self.assertEqual(item["trust"], TRUST_UNKNOWN)
        self.assertIsNone(item["value"])

    def test_model_inference_is_never_trusted(self):
        """模型推断一律不可采信 —— 哪怕它有值、有时间。"""
        item = fact_trust(
            value="每天 9:00 开门",
            source="llm",
            observed_at=days_ago(1),
            fact_kind="open_time",
            now=NOW,
        )
        self.assertEqual(item["trust"], TRUST_UNKNOWN)
        self.assertEqual(item["confidence"], 0.0)
        self.assertIn("模型推断", item["message"])

    def test_sourced_but_undated_is_estimated_not_verified(self):
        """有出处但没观测时间 → 不能算已核实（缺时间不是新鲜），归估算并说明。"""
        item = fact_trust(value="¥30", source="official", observed_at=None, fact_kind="price", now=NOW)
        self.assertEqual(item["trust"], TRUST_ESTIMATED)
        self.assertIn("未记录观测时间", item["message"])

    def test_stale_keeps_the_value_but_says_it_is_old(self):
        """过期**仍展示**（用户可能就要这条信息），但必须说清"旧了"。"""
        item = fact_trust(
            value="¥30",
            source="official",
            observed_at=days_ago(400),
            fact_kind="price",
            now=NOW,
        )
        self.assertEqual(item["trust"], TRUST_STALE)
        self.assertEqual(item["label"], "信息较旧")
        self.assertEqual(item["value"], "¥30", "过期不等于删掉")
        self.assertIn("官方渠道", item["message"])
        self.assertLess(item["confidence"], 0.9, "过期要降权")

    def test_platform_price_is_estimated_not_verified(self):
        """票务平台价不是官方价 —— 归估算。"""
        item = fact_trust(
            value="¥29",
            source="platform",
            observed_at=days_ago(1),
            fact_kind="price",
            now=NOW,
        )
        self.assertEqual(item["trust"], TRUST_ESTIMATED)

    def test_methodology_estimate_is_labelled_estimated(self):
        """显式标了 estimated 的（例如碳排方法学估算）→ 估算，并把方法带出来。"""
        item = fact_trust(
            value="12.3 kgCO2e",
            source="amap",
            observed_at=days_ago(1),
            fact_kind="transport",
            estimated=True,
            method="按里程 × 排放因子（公开方法学）",
            now=NOW,
        )
        self.assertEqual(item["trust"], TRUST_ESTIMATED)
        self.assertIn("排放因子", item["method"])
        self.assertLessEqual(item["confidence"], 0.5, "估算不该给高置信度")

    def test_knowledge_base_entry_is_estimated_not_verified(self):
        """项目内知识库**不是**一手来源：归估算，不能冒充"已核实"。"""
        item = fact_trust(
            value="当地待客讲究双手递茶",
            source="knowledge_base",
            observed_at=days_ago(10),
            fact_kind="culture",
            now=NOW,
        )
        self.assertEqual(item["trust"], TRUST_ESTIMATED)
        self.assertEqual(item["source_label"], "项目内可信知识库")

    def test_every_trust_state_has_a_user_facing_label(self):
        for state in (TRUST_VERIFIED, TRUST_ESTIMATED, TRUST_STALE, TRUST_UNKNOWN):
            self.assertIn(state, TRUST_LABELS)
            self.assertTrue(TRUST_LABELS[state])


class AggregateTests(unittest.TestCase):
    def test_worst_trust_picks_the_weakest(self):
        items = [{"trust": TRUST_VERIFIED}, {"trust": TRUST_UNKNOWN}, {"trust": TRUST_STALE}]
        self.assertEqual(worst_trust(items), TRUST_UNKNOWN)
        self.assertEqual(worst_trust([{"trust": TRUST_VERIFIED}]), TRUST_VERIFIED)

    def test_empty_set_is_unknown_not_verified(self):
        """没有事实 → unknown（"没核实过"不能被读成"已核实"）。"""
        self.assertEqual(worst_trust([]), TRUST_UNKNOWN)
        self.assertEqual(worst_trust(None), TRUST_UNKNOWN)

    def test_trust_summary_counts(self):
        items = [
            {"trust": TRUST_VERIFIED},
            {"trust": TRUST_VERIFIED},
            {"trust": TRUST_STALE},
        ]
        summary = trust_summary(items)
        self.assertEqual(summary["counts"][TRUST_VERIFIED], 2)
        self.assertEqual(summary["counts"][TRUST_STALE], 1)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["worst"], TRUST_STALE)


if __name__ == "__main__":
    unittest.main()
