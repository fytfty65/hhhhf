"""价格来源与归总（合并回方案 / 覆盖报告 / 待查清单）的确定性单测。"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.price_sources import (  # noqa: E402
    is_verified_source,
    make_observation,
    merge_observations,
    pending_price_report,
    pending_price_targets,
    price_coverage,
    source_rank,
    summarize_merge,
)


def node(name, cost=None, source="unavailable", kind="文化", day=1):
    payload = {
        "day": day, "name": name, "location": name, "time": "09:00", "type": kind,
        "lnglat": [108.94, 34.26], "cost_estimate": "暂无供应商数据" if cost is None else f"¥{cost}",
        "data_sources": {"cost_estimate": source},
    }
    return payload


def plan():
    return {"route": [
        node("钟楼酒店", 300, "amap", "住宿"),
        node("陕西历史博物馆", None, "unavailable", "博物馆"),
        node("回民街小吃", "60", "web", "餐饮"),
        node("城墙南门", None, "unavailable", "文化"),
    ]}


class TestSourceRanking(unittest.TestCase):
    def test_rank_and_verified(self):
        self.assertGreater(source_rank("amap"), source_rank("web"))
        self.assertGreater(source_rank("provider"), source_rank("amap"))
        self.assertTrue(is_verified_source("amap"))
        self.assertFalse(is_verified_source("web"))
        self.assertFalse(is_verified_source("estimate"))


class TestCoverageAndTargets(unittest.TestCase):
    def test_coverage_splits_verified_estimated_unknown(self):
        report = price_coverage(plan())
        self.assertEqual(report["total"], 4)
        self.assertEqual(report["verified"], ["钟楼酒店"])
        self.assertEqual(report["estimated"], ["回民街小吃"])
        self.assertEqual(sorted(report["unknown"]), ["城墙南门", "陕西历史博物馆"])

    def test_pending_targets_only_missing_by_default(self):
        targets = pending_price_targets(plan())
        names = [item["name"] for item in targets]
        self.assertIn("陕西历史博物馆", names)
        self.assertNotIn("钟楼酒店", names)  # 已有可核实价格
        self.assertNotIn("回民街小吃", names)  # 已有价格（虽为估算），默认不重复查
        self.assertEqual(targets[0]["intent"], "cultural")

    def test_pending_targets_can_include_estimated(self):
        names = [item["name"] for item in pending_price_targets(plan(), include_estimated=True)]
        self.assertIn("回民街小吃", names)

    def test_pending_targets_respects_limit(self):
        self.assertEqual(len(pending_price_targets(plan(), limit=1)), 1)


class TestMerge(unittest.TestCase):
    def test_fills_missing_price_and_marks_source(self):
        result = merge_observations(plan(), [make_observation("陕西历史博物馆", 0, "amap")])
        merged = {item["name"]: item for item in result["plan"]["route"]}
        self.assertEqual(merged["陕西历史博物馆"]["cost_estimate"], "¥0")
        self.assertEqual(merged["陕西历史博物馆"]["data_sources"]["cost_estimate"], "amap")
        self.assertFalse(merged["陕西历史博物馆"]["estimated"])  # 可核实来源 → 不再是估算
        self.assertEqual(len(result["updated"]), 1)
        self.assertIn("补上 1 个价格", summarize_merge(result))

    def test_weak_source_does_not_override_strong(self):
        result = merge_observations(plan(), [make_observation("钟楼酒店", 99, "web")])
        merged = {item["name"]: item for item in result["plan"]["route"]}
        self.assertEqual(merged["钟楼酒店"]["cost_estimate"], "¥300")
        self.assertEqual(merged["钟楼酒店"]["data_sources"]["cost_estimate"], "amap")
        self.assertEqual(len(result["skipped"]), 1)

    def test_strong_source_overrides_weak(self):
        result = merge_observations(plan(), [make_observation("回民街小吃", 45, "provider")])
        merged = {item["name"]: item for item in result["plan"]["route"]}
        self.assertEqual(merged["回民街小吃"]["cost_estimate"], "¥45")
        self.assertEqual(merged["回民街小吃"]["data_sources"]["cost_estimate"], "provider")
        self.assertFalse(merged["回民街小吃"]["estimated"])

    def test_web_price_stays_estimated(self):
        result = merge_observations(plan(), [make_observation("城墙南门", 54, "web")])
        merged = {item["name"]: item for item in result["plan"]["route"]}
        self.assertEqual(merged["城墙南门"]["cost_estimate"], "¥54")
        self.assertTrue(merged["城墙南门"]["estimated"])  # 网络报价仍是估算，必须标明
        self.assertFalse(is_verified_source(merged["城墙南门"]["data_sources"]["cost_estimate"]))

    def test_none_value_is_recorded_as_missed_not_written(self):
        result = merge_observations(plan(), [make_observation("城墙南门", None, "web")])
        merged = {item["name"]: item for item in result["plan"]["route"]}
        self.assertEqual(merged["城墙南门"]["cost_estimate"], "暂无供应商数据")
        self.assertEqual(result["missed"], ["城墙南门"])
        self.assertIn("查了没查到", summarize_merge(result))

    def test_unmatched_is_reported(self):
        result = merge_observations(plan(), [make_observation("完全不存在的景点", 100, "amap")])
        self.assertEqual(result["unmatched"], ["完全不存在的景点"])
        self.assertIn("没匹配到方案节点", summarize_merge(result))

    def test_stale_observation_rejected(self):
        old = make_observation("城墙南门", 54, "amap", retrieved_at=time.time() - 10 * 3600)
        result = merge_observations(plan(), [old], max_age_seconds=3600)
        merged = {item["name"]: item for item in result["plan"]["route"]}
        self.assertEqual(merged["城墙南门"]["cost_estimate"], "暂无供应商数据")
        self.assertEqual(result["stale"], ["城墙南门"])

    def test_fresh_observation_within_ttl_accepted(self):
        fresh = make_observation("城墙南门", 54, "amap", retrieved_at=time.time() - 60)
        result = merge_observations(plan(), [fresh], max_age_seconds=3600)
        merged = {item["name"]: item for item in result["plan"]["route"]}
        self.assertEqual(merged["城墙南门"]["cost_estimate"], "¥54")
        self.assertEqual(result["stale"], [])

    def test_does_not_mutate_input(self):
        import copy
        original = plan()
        snapshot = copy.deepcopy(original)
        merge_observations(original, [make_observation("城墙南门", 54, "amap")])
        self.assertEqual(original, snapshot)

    def test_fuzzy_name_matching(self):
        result = merge_observations(plan(), [make_observation("城墙南门(永宁门)", 54, "amap")])
        self.assertEqual(len(result["updated"]), 1)

    def test_coverage_updates_after_merge(self):
        result = merge_observations(
            plan(),
            [make_observation("陕西历史博物馆", 0, "amap"), make_observation("城墙南门", 54, "amap")],
        )
        self.assertEqual(result["coverage"]["verified_ratio"], 0.75)


if __name__ == "__main__":
    unittest.main()

class TestPendingPriceReport(unittest.TestCase):
    """给用户的"需要你确认"清单：为什么没价格 + 去哪核（用户看不懂"未取到 73%"）。"""

    def test_lodging_says_amap_has_no_room_rates(self):
        sample = {"route": [
            {"day": 1, "name": "某酒店", "type": "住宿", "cost_estimate": "暂无供应商数据"},
            {"day": 1, "name": "某博物馆", "type": "文化", "cost_estimate": "暂无供应商数据",
             "amap_url": "https://uri.amap.com/marker?position=1,2&name=x"},
        ]}
        report = pending_price_report(sample)
        by_name = {item["name"]: item for item in report["pending"]}
        self.assertIn("高德不提供房价", by_name["某酒店"]["reason"])
        self.assertIn("没有可核实报价", by_name["某博物馆"]["reason"])
        self.assertEqual(by_name["某酒店"]["kind"], "hotel")
        for item in report["pending"]:
            self.assertTrue(item["url"].startswith("https://"))
        self.assertIn("需要你确认", report["summary"])

    def test_verified_prices_are_not_asked_again(self):
        sample = {"route": [
            {"day": 1, "name": "有报价的景点", "type": "文化", "cost_estimate": "¥50",
             "data_sources": {"cost_estimate": "amap"}},
        ]}
        report = pending_price_report(sample)
        self.assertEqual(report["pending"], [])
        self.assertIn("没有需要你确认的", report["summary"])

    def test_missing_url_falls_back_to_search_link(self):
        sample = {"route": [{"day": 1, "name": "没链接的酒店", "type": "住宿", "cost_estimate": "暂无供应商数据"}]}
        item = pending_price_report(sample)["pending"][0]
        self.assertIn("amap.com/search", item["url"])
