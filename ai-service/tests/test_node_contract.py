"""权威节点契约的回归锁（P0：统一事实）。

实测的字段分叉：花费有 `cost/cost_estimate/costEstimate/price`；类别有
`type/category/planning_category`；坐标既有 `[lng,lat]` 数组，也有 AMap 的 `"lng,lat"` 字符串，
而路线节点里的 `location` 其实是**名称**。这些测试把归一化与诚实口径钉死。
"""

import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.node_contract import (  # noqa: E402
    CANONICAL_FIELDS,
    COST_ESTIMATED,
    COST_UNKNOWN,
    COST_VERIFIED,
    looks_like_coordinates,
    normalize_category,
    normalize_node,
    parse_coordinates,
    parse_cost,
    validate_node,
)


class TestCostParsing(unittest.TestCase):
    def test_parses_common_spellings(self):
        self.assertEqual(parse_cost("¥50"), 50.0)
        self.assertEqual(parse_cost("50元"), 50.0)
        self.assertEqual(parse_cost(50), 50.0)
        self.assertEqual(parse_cost("¥50-120/人"), 50.0)  # 区间取下限，不编造中间值

    def test_unknown_placeholders_never_become_zero(self):
        """这是全项目最关键的一条诚实口径：取不到价格不能变成 0 元、也不能变成"免费"。"""
        for value in ("暂无供应商数据", "暂无报价", "待确认", "N/A", "unknown", "", None):
            self.assertIsNone(parse_cost(value), f"{value!r} 必须解析为 None，不能是 0")


class TestCoordinatesAndLocationAmbiguity(unittest.TestCase):
    def test_accepts_both_shapes(self):
        self.assertEqual(parse_coordinates([86.15, 41.76]), [86.15, 41.76])
        self.assertEqual(parse_coordinates("86.15,41.76"), [86.15, 41.76])
        self.assertEqual(parse_coordinates({"lng": 86.15, "lat": 41.76}), [86.15, 41.76])

    def test_rejects_junk_and_zero_point(self):
        for bad in (None, "", "博斯腾湖", [0, 0], [999, 999], [1]):
            self.assertIsNone(parse_coordinates(bad), f"{bad!r} 不该解析成坐标")

    def test_location_is_a_name_in_route_nodes_but_coords_in_amap_pois(self):
        """同一个键名两种含义 —— 必须按**形状**判断，不能按名字猜。"""
        self.assertFalse(looks_like_coordinates("博斯腾湖"))
        self.assertTrue(looks_like_coordinates("86.15,41.76"))
        route_node = normalize_node({"day": 1, "name": "博斯腾湖", "location": "博斯腾湖", "lnglat": [86.15, 41.76]})
        self.assertEqual(route_node["name"], "博斯腾湖")
        self.assertEqual(route_node["coordinates"], [86.15, 41.76])
        amap_poi = normalize_node({"name": "博斯腾湖", "location": "86.15,41.76"})
        self.assertEqual(amap_poi["name"], "博斯腾湖")
        self.assertEqual(amap_poi["coordinates"], [86.15, 41.76])


class TestCategoryNormalization(unittest.TestCase):
    def test_aliases_converge(self):
        for raw, expected in (("餐饮", "food"), ("美食", "food"), ("住宿", "hotel"), ("博物馆", "cultural"),
                              ("自然风光", "scenic"), ("风景名胜;风景名胜;湖泊", "scenic"), ("购物商圈", "shopping")):
            self.assertEqual(normalize_category(raw), expected, f"{raw} 应归一为 {expected}")

    def test_unknown_category_is_preserved_not_guessed(self):
        """认不出来就原样保留 —— 不硬塞进某个已知类别（那等于造假）。"""
        self.assertEqual(normalize_category("某某新品", ), "某某新品")


class TestNodeNormalization(unittest.TestCase):
    def test_all_legacy_spellings_land_on_one_shape(self):
        legacy = {
            "day": 2,
            "location": "博斯腾湖",          # 节点里 location = 名称
            "lnglat": [86.15, 41.76],
            "type": "自然风光",              # 类别别名
            "costEstimate": "¥45",           # 花费别名
            "data_sources": {"cost_estimate": "amap"},
            "open_time": "09:00-19:00",
            "amap_url": "https://uri.amap.com/marker?position=86.15,41.76",
        }
        node = normalize_node(legacy)
        self.assertEqual(node["name"], "博斯腾湖")
        self.assertEqual(node["category"], "scenic")
        self.assertEqual(node["cost"], 45.0)
        self.assertEqual(node["cost_status"], COST_VERIFIED, "有 amap 来源即视为已核实")
        self.assertFalse(node["estimated"])
        self.assertEqual(node["official_url"], "https://uri.amap.com/marker?position=86.15,41.76")
        for field in CANONICAL_FIELDS:
            self.assertIn(field, node, f"归一化结果必须包含 {field}")
        self.assertEqual(validate_node(node), [], "有来源的已核实节点应通过自检")

    def test_missing_price_is_unknown_never_zero_and_never_estimated(self):
        node = normalize_node({"day": 1, "name": "某酒店", "type": "住宿", "cost_estimate": "暂无供应商数据"})
        self.assertIsNone(node["cost"])
        self.assertEqual(node["cost_status"], COST_UNKNOWN)
        self.assertFalse(node["estimated"], "未知不是估算，两者不能混")
        self.assertIn("cost", node["facts_missing"])
        self.assertEqual(validate_node(node), [], "缺价格本身不算违规，只要如实标成 unknown")

    def test_estimated_when_no_sourced_price(self):
        node = normalize_node({"name": "某点", "cost": "80"})
        self.assertEqual(node["cost_status"], COST_ESTIMATED)
        self.assertTrue(node["estimated"])

    def test_validate_rejects_dishonest_nodes(self):
        # 取不到价格却标成已核实 → 违规
        bad = normalize_node({"name": "x", "location": "86.15,41.76"})
        tampered = dict(bad, cost=None, cost_status=COST_VERIFIED)
        self.assertIn("missing_cost_must_be_unknown", validate_node(tampered))
        # 没有来源却声称已核实 → 违规
        no_source = dict(bad, cost=50.0, cost_status=COST_VERIFIED, source=None)
        self.assertIn("verified_cost_requires_source", validate_node(no_source))
        # 缺字段 → 违规
        self.assertTrue(any(item.startswith("missing_field:") for item in validate_node({"name": "x"})))

    def test_facts_missing_reports_what_to_degrade(self):
        node = normalize_node({"name": "只有名字的点"})
        self.assertIn("coordinates", node["facts_missing"])
        self.assertIn("source", node["facts_missing"])


class TestTrustGrading(unittest.TestCase):
    """P3：每条事实带可信度四态（已核实/估算/信息较旧/尚未核实）。"""

    def test_official_and_fresh_price_is_verified(self):
        node = normalize_node(
            {
                "day": 1,
                "name": "天鹅泉湿地公园",
                "location": "81.65,43.70",
                "cost": "30",
                "source": "official",
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.assertEqual(node["trust"], "verified")
        self.assertEqual(node["trust_label"], "已核实")
        self.assertEqual(node["source_type"], "official_page")
        self.assertEqual(node["freshness"], "fresh")
        self.assertEqual(validate_node(node), [])

    def test_missing_price_is_trust_unknown(self):
        node = normalize_node({"day": 1, "name": "某酒店", "type": "住宿", "cost_estimate": "暂无供应商数据"})
        self.assertEqual(node["trust"], "unknown")
        self.assertEqual(node["trust_label"], "尚未核实")
        self.assertEqual(node["derived_confidence"], 0.0)
        self.assertEqual(validate_node(node), [])

    def test_model_confidence_is_not_trusted(self):
        """上游/模型自报的 confidence 不采信 —— 由来源分级推导。"""
        node = normalize_node(
            {
                "day": 1,
                "name": "某点",
                "location": "86.15,41.76",
                "cost": "88",
                "source": "llm",
                "confidence": 0.99,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.assertEqual(node["trust"], "unknown", "模型推断不能因为自报 0.99 就变可信")
        self.assertEqual(node["derived_confidence"], 0.0)

    def test_stale_fact_is_shown_but_flagged(self):
        old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        node = normalize_node(
            {
                "day": 1,
                "name": "某景区",
                "location": "86.15,41.76",
                "cost": "140",
                "source": "official",
                "observed_at": old,
            }
        )
        self.assertEqual(node["trust"], "stale")
        self.assertEqual(node["trust_label"], "信息较旧")
        self.assertEqual(node["cost"], 140.0, "过期的事实仍然展示")

    def test_price_retrieved_at_is_read_as_the_observation_time(self):
        """取价管线写的 `price_retrieved_at` 必须被认成观测时间（2026-10-08 实测修）。

        实测缺口：120 份方案、483 个有价节点，`observed_at` **全是空**，于是有价节点
        只能落到"估算"，而其中 144 个其实带着真实抓取时间 `price_retrieved_at`
        （来自 `build_observation` 的 `retrieved_at`）—— 契约不认这个字段名，
        有观测时间的事实被判成"没观测时间"。这是**字段名没对齐**，不是真的没数据。
        """
        fresh = time.time() - 3600  # 1 小时前抓的
        node = normalize_node(
            {
                "day": 1,
                "name": "某景区",
                "location": "86.15,41.76",
                "cost": "140",
                "source": "official",
                "price_retrieved_at": fresh,
            }
        )
        self.assertTrue(node["observed_at"], "应当读到 price_retrieved_at")
        self.assertEqual(node["freshness"], "fresh", "1 小时前抓的价是新鲜的")
        self.assertEqual(node["trust"], "verified", "官方价 + 新鲜 → 已核实")

    def test_missing_observation_time_still_not_verified(self):
        """反过来：**确实没有**观测时间的，不许因此变成已核实（缺时间≠新鲜）。"""
        node = normalize_node(
            {
                "day": 1,
                "name": "某景区",
                "location": "86.15,41.76",
                "cost": "140",
                "source": "official",
            }
        )
        self.assertFalse(node["observed_at"], "没有时间就是没有，不许补默认值")
        self.assertEqual(node["trust"], "estimated", "有出处但没观测时间 → 估算")

    def test_web_page_price_is_not_downgraded_to_estimate(self):
        """`web_page`（白名单官方页）不能因为字段名不含 "official" 被强制降级（2026-10-08 实测修）。

        实测症状：21 个官方页节点 fresh + 有观测时间，却全部停在"估算"，
        文案还写着"按公开规则估算，仅供规划参考" —— 明明是官方页读到的票价却被说成估的。
        原因：`web_page` 不在 `CASH_SOURCE_MARKERS`（那里只认字面 "official"），
        于是掉进 else 分支被**强制** `estimated=True`，把节点自己写的 `estimated=False` 覆盖掉。

        `web_page` 是用户 2026-10-05 拍板的**可核实**来源（官方自己写的票价、带 URL 可点开）。
        """
        node = normalize_node(
            {
                "day": 1,
                "name": "某景区",
                "location": "86.15,41.76",
                "cost": "42",
                "estimated": False,
                "data_sources": {"cost_estimate": "web_page"},
                "price_retrieved_at": time.time() - 3600,
            }
        )
        self.assertEqual(node["cost_status"], "verified", "官方页读到的价是可核实的")
        self.assertFalse(node["estimated"], "不能被强制标成估算")
        self.assertEqual(node["trust"], "verified")

    def test_platform_price_is_still_an_estimate(self):
        """反过来：票务平台价**仍然**是估算（用户口径：平台价非官方，不计入可核实）。"""
        node = normalize_node(
            {
                "day": 1,
                "name": "某景区",
                "location": "86.15,41.76",
                "cost": "42",
                "data_sources": {"cost_estimate": "platform"},
                "price_retrieved_at": time.time() - 3600,
            }
        )
        self.assertNotEqual(node["cost_status"], "verified", "平台价不是官方价")
        self.assertEqual(node["trust"], "estimated")

    def test_validate_rejects_inconsistent_trust(self):
        """四态与花费状态必须自洽，否则前端标签会骗人。"""
        base = normalize_node({"name": "x", "location": "86.15,41.76"})
        # 花费 unknown 却声称已核实
        tampered = dict(base, cost=None, cost_status=COST_UNKNOWN, trust="verified")
        self.assertIn("trust_verified_requires_verified_cost", validate_node(tampered))
        # 花费 unknown 却标成估算
        tampered = dict(base, cost=None, cost_status=COST_UNKNOWN, trust="estimated")
        self.assertTrue(
            any(item.startswith("unknown_cost_must_be_trust_unknown") for item in validate_node(tampered))
        )
        # 四态写了个不存在的值
        tampered = dict(base, trust="totally-fine")
        self.assertTrue(any(item.startswith("trust_invalid:") for item in validate_node(tampered)))


if __name__ == "__main__":
    unittest.main()
