"""供应商事实占位清洗（2026-11-03）：查不到的评分/开放时间/价格就留空。

事故形态：高德 `place/text` 没给 `biz_ext` 时，底座把评分/营业时间/价格写成同一句
"暂无供应商数据"；下游 `or "暂无供应商数据"` 继续复制，最终 payload 每个节点三行同款文案。
界面把它当事实展示（信息卡"暂无供应商数据 分"、时间轴每站重复一行），
用户读不到任何信息，也容易把占位误读成事实。

口径：**只清占位，不动真值**；清空后由 `supplier_data_gaps` 统一披露一次缺什么。
"""

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.place_quality import (  # noqa: E402
    SUPPLIER_PLACEHOLDERS,
    clear_placeholder_supplier_facts,
    is_supplier_placeholder,
    supplier_facts_disclosure,
)

PLACEHOLDER = "暂无供应商数据"


class PlaceholderDetectionTests(unittest.TestCase):
    def test_known_placeholders_are_detected(self):
        for text in SUPPLIER_PLACEHOLDERS:
            self.assertTrue(is_supplier_placeholder(text), text)
        self.assertTrue(is_supplier_placeholder("  " + PLACEHOLDER + "  "))

    def test_real_values_are_not_placeholders(self):
        for value in ("4.6", "4.6分", "¥60", "60元/人", "08:00-18:00", "08:30-17:30（周一闭馆）", 4.6):
            self.assertFalse(is_supplier_placeholder(value), value)
        # 空值不是"占位"（本来就没有数据），清洗时也不该登记
        self.assertFalse(is_supplier_placeholder(""))
        self.assertFalse(is_supplier_placeholder(None))


class ClearPlaceholderTests(unittest.TestCase):
    def test_placeholders_are_cleared_and_real_values_kept(self):
        node = {
            "day": 1,
            "name": "宽窄巷子",
            "rating": PLACEHOLDER,
            "open_time": "08:00-22:00",
            "cost_estimate": "暂无",
            "desc": "成都老街区",
        }
        plan = {"route": [node]}
        cleared = clear_placeholder_supplier_facts(plan, copy=False)
        self.assertEqual([item["name"] for item in cleared], ["宽窄巷子"])
        self.assertEqual(cleared[0]["fields"], ["rating", "cost_estimate"])
        self.assertEqual(node["rating"], "", "占位必须清成空，不能继续当事实展示")
        self.assertEqual(node["cost_estimate"], "")
        # 真值原样保留：开放时间是高德真给的，不能一起清
        self.assertEqual(node["open_time"], "08:00-22:00")
        # 其他字段不动
        self.assertEqual(node["desc"], "成都老街区")
        self.assertEqual(node["day"], 1)

    def test_only_placeholder_fields_are_touched(self):
        clean_node = {"name": "都江堰", "rating": "4.7", "open_time": "08:00-18:00", "cost_estimate": "¥80"}
        self.assertEqual(
            clear_placeholder_supplier_facts({"route": [clean_node]}, copy=False), []
        )
        self.assertEqual(clean_node["rating"], "4.7")

    def test_empty_values_are_not_reported(self):
        node = {"name": "某酒店", "rating": "", "open_time": None, "cost_estimate": ""}
        self.assertEqual(clear_placeholder_supplier_facts({"route": [node]}, copy=False), [])

    def test_default_copy_does_not_touch_the_original(self):
        plan = {"route": [{"name": "天安门", "rating": PLACEHOLDER}]}
        cleared = clear_placeholder_supplier_facts(plan)
        self.assertEqual(len(cleared), 1)
        self.assertEqual(plan["route"][0]["rating"], PLACEHOLDER, "默认必须拷贝，不能顺手改调用方数据")

    def test_nested_route_structures_are_covered(self):
        node = {"name": "锦里", "rating": PLACEHOLDER}
        plan = {"nodes": [[node]]}
        self.assertEqual(len(clear_placeholder_supplier_facts(plan, copy=False)), 1)
        self.assertEqual(node["rating"], "")


class DisclosureTests(unittest.TestCase):
    def test_disclosure_is_one_aggregated_entry(self):
        cleared = [
            {"name": "宽窄巷子", "fields": ["rating", "open_time"]},
            {"name": "某酒店", "fields": ["cost_estimate"]},
        ]
        gap = supplier_facts_disclosure(cleared)
        self.assertIsNotNone(gap)
        self.assertEqual(gap["node_count"], 2)
        self.assertEqual(gap["fields"], ["cost_estimate", "open_time", "rating"])
        self.assertEqual([item["name"] for item in gap["items"]], ["宽窄巷子", "某酒店"])
        self.assertIn("未能从供应商核实", gap["note"])
        self.assertNotIn(PLACEHOLDER, gap["note"], "披露文案不应该再复制占位本身")

    def test_no_cleared_nodes_means_no_disclosure(self):
        self.assertIsNone(supplier_facts_disclosure([]))


class AgentWiringTests(unittest.TestCase):
    def test_all_output_paths_clean_placeholders(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "api", "agent.py"), encoding="utf-8") as handle:
            source = handle.read()
        # 主路径 / 候选池兜底 / 种子模板+缓存命中：三条出口都必须洗掉占位
        self.assertGreaterEqual(source.count("clear_placeholder_supplier_facts("), 3)
        self.assertGreaterEqual(source.count("supplier_facts_disclosure("), 3)
        self.assertGreaterEqual(source.count('"supplier_data_gaps"'), 3)
        # 清洗必须就地改最终 payload（否则日志说清了、payload 还在）
        self.assertIn("copy=False", source)


if __name__ == "__main__":
    unittest.main()
