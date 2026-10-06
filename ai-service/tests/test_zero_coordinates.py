"""节点坐标口径：`(0, 0)` 是"海面上"，不是地点（2026-10-05 实测）。

事故形态：住宿候选由供应商注入、但那一行**没有经纬度**，规划里被写成 `[0,0]`，
而里程计算把它当成一个真实位置 → 算出"第 2 天移动 **9440km**"，
当天预算判断、空间效率、周全性全废（伊宁同题两次推演总分 88.3 → 75.1）。
与前端 `_valid_pt`、照片兜底等处保持同一口径：**0,0 = 没有坐标**（不参与距离/预算计算）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.travel_feasibility import evaluate_travel_feasibility, node_lnglat  # noqa: E402

CENTER = [81.32, 43.92]  # 伊宁


class ZeroCoordinateTests(unittest.TestCase):
    def test_zero_zero_is_treated_as_missing(self):
        self.assertEqual(node_lnglat({"lnglat": [0, 0]}), [])
        self.assertEqual(node_lnglat({"lnglat": [0.0, 0.0]}), [])
        self.assertEqual(node_lnglat({"lnglat": "0,0"}), [])
        self.assertEqual(node_lnglat({"lnglat": [0, 43.9]}), [0.0, 43.9], "只有一个 0 不算（赤道/本初子午线是合法坐标）")

    def test_real_coordinates_still_work(self):
        self.assertEqual(node_lnglat({"lnglat": [81.5, 43.9]}), [81.5, 43.9])
        self.assertEqual(node_lnglat({"location": "81.5,43.9"}), [81.5, 43.9])
        self.assertEqual(node_lnglat({"lnglat": [81.5]}), [])
        self.assertEqual(node_lnglat({"lnglat": None}), [])
        self.assertEqual(node_lnglat("不是节点"), [])

    def test_travel_feasibility_ignores_zero_coordinates(self):
        """带 [0,0] 的节点不能把当天里程撑到几千公里。

        （`travel_km` 里含"市中心 → 当天第一个点"的那一小段，所以断言"很小"而不是"恰好 0"。）
        """
        route = [
            {"day": 1, "name": "市区点", "lnglat": [81.34, 43.93]},
            {"day": 1, "name": "没坐标的酒店", "lnglat": [0, 0]},
        ]
        report = evaluate_travel_feasibility(route, city_center=CENTER, distance_tolerance="normal", days=1)
        self.assertTrue(report["ok"], report)
        self.assertLess(report["days"][0]["travel_km"], 10.0, "0,0 不该参与里程计算（否则会算出几千公里）")
        self.assertEqual(report["days"][0]["nodes"], 2)
        self.assertEqual(
            [item["name"] for item in report["days"][0]["far_nodes"]],
            [],
            "0,0 也不能被当成远点",
        )


if __name__ == "__main__":
    unittest.main()
