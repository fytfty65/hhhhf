"""缺坐标住宿的「按名字补坐标」测试（2026-10-05）。

真实场景：供应商给的酒店候选有时**只有名字和地址、没有坐标**（喀什古城步行街亚朵酒店就是这样）。
节点被清洗成"缺坐标"之后：
  · 通勤距离算不出来 → `anchoring` 掉到 0.8（我们只能如实说"算不出来"）；
  · 地图上标不出位置。
而这个名字是**真实存在**的，用高德按名字查一次就能补上；补不到就保持缺失（绝不编坐标）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.place_quality import nodes_missing_coordinates  # noqa: E402


class MissingCoordinateTargetTests(unittest.TestCase):
    def test_zero_and_absent_coordinates_are_both_listed(self):
        plan = {
            "route": [
                {"day": 1, "name": "正常点", "type": "风景名胜", "lnglat": [75.99, 39.47]},
                {"day": 1, "name": "被清掉 0,0 的酒店", "type": "住宿服务", "is_hotel": True, "lnglat": []},
                {"day": 2, "name": "从未有坐标的酒店", "type": "住宿服务", "is_hotel": True},
                {"day": 2, "name": "从未有坐标的景点", "type": "风景名胜"},
            ]
        }
        names = [str(item.get("name")) for item in nodes_missing_coordinates(plan)]
        self.assertEqual(names, ["被清掉 0,0 的酒店", "从未有坐标的酒店", "从未有坐标的景点"])

    def test_only_lodging_filters_to_hotels(self):
        plan = {
            "route": [
                {"day": 1, "name": "没坐标的酒店", "type": "住宿服务", "is_hotel": True},
                {"day": 1, "name": "没坐标的景点", "type": "风景名胜"},
            ]
        }
        names = [str(item.get("name")) for item in nodes_missing_coordinates(plan, only_lodging=True)]
        self.assertEqual(names, ["没坐标的酒店"])

    def test_nothing_missing_returns_empty(self):
        plan = {"route": [{"day": 1, "name": "点", "type": "风景名胜", "lnglat": [75.99, 39.47]}]}
        self.assertEqual(nodes_missing_coordinates(plan), [])
        self.assertEqual(nodes_missing_coordinates({"route": []}), [])

    def test_agent_wires_the_backfill_and_discloses_it(self):
        """接线检查（这段只在真实链路执行）：补到的标来源、没补到的仍然是缺失。"""
        import io

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "api", "agent.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("nodes_missing_coordinates(final_data, only_lodging=True, copy=False)", source)
        self.assertIn('_node["coordinate_source"] = "amap_lookup"', source)
        self.assertIn('"coordinate_backfill"', source)
        self.assertIn("绝不编坐标", source)

    def test_backfill_must_ask_for_real_nodes_not_copies(self):
        """**回归**（2026-10-05 实测抓到）：`nodes_of` 默认返回浅拷贝，补坐标必须 `copy=False`。

        踩坑经过：日志明明打了「📍 [坐标补齐] 按名字补到 1 个（喀什古城步行街亚朵酒店）」，
        最终 payload 里那个住宿节点仍然是 `lnglat: []`、`anchoring.coordinate_coverage`
        还是 0.833 —— 因为改的是拷贝，压根没写回 `final_data["route"]`。
        所以这里既检查调用处传了 `copy=False`，也**直接验行为**（改拷贝 vs 改原对象）。
        """
        plan = {
            "route": [
                {"day": 1, "name": "缺坐标的酒店", "type": "住宿服务", "is_hotel": True, "lnglat": []},
            ]
        }
        copied = nodes_missing_coordinates(plan, only_lodging=True)
        self.assertEqual(len(copied), 1)
        copied[0]["lnglat"] = [75.99, 39.47]
        self.assertEqual(plan["route"][0]["lnglat"], [], "默认必须是拷贝，不能顺手改到调用方的方案上")

        real = nodes_missing_coordinates(plan, only_lodging=True, copy=False)
        self.assertEqual(len(real), 1)
        real[0]["lnglat"] = [75.99, 39.47]
        real[0]["coordinate_source"] = "amap_lookup"
        self.assertEqual(plan["route"][0]["lnglat"], [75.99, 39.47], "copy=False 必须写回原方案")
        self.assertEqual(plan["route"][0]["coordinate_source"], "amap_lookup")
        # 补上之后就不再是"缺坐标"了
        self.assertEqual(nodes_missing_coordinates(plan, only_lodging=True), [])

    def test_nested_route_structures_also_write_back(self):
        """`[[节点], [节点]]` 这种嵌套结构同样要能写回（摊平时不能又拷贝一层）。"""
        node = {"day": 1, "name": "嵌套里的酒店", "is_hotel": True}
        plan = {"nodes": [[node]]}
        real = nodes_missing_coordinates(plan, only_lodging=True, copy=False)
        self.assertEqual(len(real), 1)
        real[0]["lnglat"] = [75.99, 39.47]
        self.assertEqual(node["lnglat"], [75.99, 39.47])

    def test_backfill_query_disables_the_play_worthy_filter(self):
        """按名字查酒店必须 `play_worthy=False`。

        实测（2026-10-05）：`is_play_worthy` 会把"住宿服务"整类当非玩点丢掉 ——
        同一个酒店名 play_worthy=True 回 **0 条**、False 回 **1 条且带坐标**
        （`tools/probe_hotel_play_worthy.py`）。我一开始把"补到 0 个"误判成
        "高德没有这家/被限流"，实际是自己传错了过滤器。
        """
        import io

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "api", "agent.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("keywords=_node_name, limit=3, play_worthy=False", source)
        # 有 keywords 时不该再传 types（会把关键词相关性打没）
        self.assertNotIn('keywords=_node_name, types="100000"', source)

    def test_empty_poi_results_are_not_cached(self):
        """空结果**不缓存**：一次限流不该被固化半小时（"这里没有 POI"的假象）。"""
        import io

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "api", "agent.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("if pois:\n                        _poi_cache_set(_cache_key, pois)", source)


if __name__ == "__main__":
    unittest.main()
