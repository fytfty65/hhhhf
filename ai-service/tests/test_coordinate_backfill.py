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
        self.assertIn("nodes_missing_coordinates(final_data, only_lodging=True)", source)
        self.assertIn('_node["coordinate_source"] = "amap_lookup"', source)
        self.assertIn('"coordinate_backfill"', source)
        self.assertIn("绝不编坐标", source)


if __name__ == "__main__":
    unittest.main()
