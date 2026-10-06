"""评分与清洗必须与管线同一坐标口径（2026-10-05 实测：两套口径互相打架）。

事故形态：住宿候选没坐标 → 规划写成 `[0,0]`。
- 管线侧（`core/travel_feasibility`）修好之后按"没有坐标"处理，天数里程正常；
- 但**评分侧**（`core/plan_quality`）自己另写了一个 `node_lnglat`，仍把 0,0 当真实位置 →
  算出"最赶的一天约 9438km"、空间效率 0.0、周全性 0.64，方案被判差。
同一条路线两套口径 → 报告与事实互相矛盾。这一层钉住"只有一套口径"。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_quality import node_lnglat, score_space_efficiency  # noqa: E402
from core.travel_feasibility import node_lnglat as canonical_lnglat  # noqa: E402


class SingleCoordinateRuleTests(unittest.TestCase):
    def test_zero_zero_is_missing_in_both_places(self):
        for node in ({"lnglat": [0, 0]}, {"lnglat": "0,0"}, {"lnglat": {"lng": 0, "lat": 0}}):
            self.assertIsNone(node_lnglat(node), node)
            self.assertEqual(canonical_lnglat(node), [], node)

    def test_valid_coordinates_agree(self):
        node = {"lnglat": [81.5, 43.9]}
        self.assertEqual(node_lnglat(node), (81.5, 43.9))
        self.assertEqual(canonical_lnglat(node), [81.5, 43.9])

    def test_mapping_and_missing_forms(self):
        self.assertEqual(node_lnglat({"lnglat": {"lng": 81.5, "lat": 43.9}}), (81.5, 43.9))
        self.assertEqual(node_lnglat({"location_lnglat": [81.5, 43.9]}), (81.5, 43.9))
        self.assertIsNone(node_lnglat({"lnglat": [81.5]}))
        self.assertIsNone(node_lnglat({}))
        self.assertIsNone(node_lnglat({"lnglat": "81.5"}))
        # 只有一个 0 时仍是合法坐标（赤道/本初子午线）
        self.assertEqual(node_lnglat({"lnglat": [0, 43.9]}), (0.0, 43.9))

    def test_space_efficiency_ignores_a_zero_coordinate_node(self):
        plan = {"route": [
            {"day": 1, "name": "市区点", "lnglat": [81.34, 43.93]},
            {"day": 1, "name": "没坐标的酒店", "lnglat": [0, 0]},
        ]}
        report = score_space_efficiency({"days": 1, "distance_tolerance": "normal"}, plan)
        self.assertLess(report["max_day_km"], 10.0, report)
        self.assertEqual(report["coordinate_coverage"], 0.0, "缺坐标的天不该被算成有坐标")


if __name__ == "__main__":
    unittest.main()
