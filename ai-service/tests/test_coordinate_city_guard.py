"""目的地范围校验（2026-11-02）：坐标"合法"不等于"属于这趟行程"。

事故形态：模型给出一个经纬度都在合法区间、却明显在别的城市的坐标。
- 前端按它把整天地图缩到很低级别（与真实 POI 差着几百公里）；
- 里程里凭空多出几十公里的"通勤"（实测 DAY 1 曾出现 47.7km 的假段）。
口径与 `(0,0)` 一致：坐标不可信就当没有（`lnglat=[]`、`coordinate_status=missing`），
名称/时间/花费等字段全部保留，不参与里程与地图标点。

省域行程（目的地是"四川/新疆"这类省）要额外把各片区中心当作合法中心：
离省会很远、但在阿坝州内的九寨沟是合理的，不能被城市口径误删。
"""

import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.place_quality import (  # noqa: E402
    DEFAULT_CITY_COORDINATE_RADIUS_KM,
    DEFAULT_PROVINCE_COORDINATE_RADIUS_KM,
    clear_out_of_area_coordinates,
    poi_coordinate_radius_km,
)

CHENGDU = [104.06, 30.66]
MARKANG = [102.22, 31.90]  # 阿坝州州府（省域行程的片区中心之一）
BEIJING = [116.40, 39.90]
JIUZHAIGOU = [103.92, 33.26]


class ClearOutOfAreaTests(unittest.TestCase):
    def test_cross_city_coordinate_is_cleared_and_other_fields_kept(self):
        node = {
            "day": 1,
            "time": "Day 1 | 09:00",
            "name": "天安门",
            "lnglat": BEIJING,
            "cost_estimate": "免费",
            "address": "北京市东城区",
        }
        plan = {"route": [node]}
        cleared = clear_out_of_area_coordinates(plan, centers=[CHENGDU], radius_km=150, copy=False)
        self.assertEqual([item["name"] for item in cleared], ["天安门"])
        self.assertGreater(cleared[0]["distance_km"], 150)
        self.assertEqual(node["lnglat"], [], "坐标必须清成缺失")
        self.assertEqual(node["coordinate_status"], "missing")
        self.assertIn("超出", node["coordinate_note"])
        # 名称/时间/花费等其他字段不受影响（只清坐标）
        self.assertEqual(node["name"], "天安门")
        self.assertEqual(node["time"], "Day 1 | 09:00")
        self.assertEqual(node["cost_estimate"], "免费")
        self.assertEqual(node["address"], "北京市东城区")

    def test_near_suburb_is_kept(self):
        """都江堰离成都约 50km：在城市口径内，不能删。"""
        plan = {"route": [{"name": "都江堰", "lnglat": [103.62, 30.99]}]}
        self.assertEqual(clear_out_of_area_coordinates(plan, centers=[CHENGDU], radius_km=80), [])

    def test_multiple_centers_use_the_nearest_one(self):
        """省域行程：九寨沟离成都 300km，但在阿坝片区中心 200km 内 → 保留。"""
        plan = {"route": [{"name": "九寨沟", "lnglat": JIUZHAIGOU}]}
        self.assertEqual(
            clear_out_of_area_coordinates(plan, centers=[CHENGDU, MARKANG], radius_km=250), []
        )
        # 半径收紧到 100km 时，两个中心都够不着 → 才清掉
        cleared = clear_out_of_area_coordinates(
            {"route": [{"name": "九寨沟", "lnglat": JIUZHAIGOU}]},
            centers=[CHENGDU, MARKANG],
            radius_km=100,
        )
        self.assertEqual([item["name"] for item in cleared], ["九寨沟"])

    def test_without_valid_centers_nothing_is_cleared(self):
        """判不了就不猜：没有中心（或中心是 0,0）时一律保留。"""
        plan = {"route": [{"name": "北京", "lnglat": BEIJING}]}
        self.assertEqual(clear_out_of_area_coordinates(plan, centers=[], radius_km=80), [])
        self.assertEqual(clear_out_of_area_coordinates(plan, centers=[[0, 0]], radius_km=80), [])
        self.assertEqual(clear_out_of_area_coordinates(plan, centers=["", None], radius_km=80), [])

    def test_non_positive_radius_is_noop(self):
        plan = {"route": [{"name": "北京", "lnglat": BEIJING}]}
        self.assertEqual(clear_out_of_area_coordinates(plan, centers=[CHENGDU], radius_km=0), [])
        self.assertEqual(clear_out_of_area_coordinates(plan, centers=[CHENGDU], radius_km=-1), [])

    def test_zero_coordinate_is_not_reported_here(self):
        """`(0,0)` 由专用的 0,0 清洗负责，不在这里重复登记（否则披露里会出现两条）。"""
        plan = {"route": [{"name": "没坐标的酒店", "lnglat": [0, 0]}]}
        self.assertEqual(clear_out_of_area_coordinates(plan, centers=[CHENGDU], radius_km=80), [])

    def test_default_copy_does_not_touch_the_original_plan(self):
        plan = {"route": [{"name": "天安门", "lnglat": BEIJING}]}
        cleared = clear_out_of_area_coordinates(plan, centers=[CHENGDU], radius_km=80)
        self.assertEqual(len(cleared), 1)
        self.assertEqual(plan["route"][0]["lnglat"], BEIJING, "默认必须是拷贝，不能顺手改调用方的方案")

    def test_nested_route_structures_also_write_back(self):
        node = {"name": "天安门", "lnglat": BEIJING}
        plan = {"nodes": [[node]]}
        cleared = clear_out_of_area_coordinates(plan, centers=[CHENGDU], radius_km=80, copy=False)
        self.assertEqual(len(cleared), 1)
        self.assertEqual(node["lnglat"], [])
        self.assertEqual(node["coordinate_status"], "missing")


class CoordinateRadiusPolicyTests(unittest.TestCase):
    def test_city_radius_follows_scenic_policy_with_a_floor_of_80(self):
        self.assertEqual(poi_coordinate_radius_km(3, "normal"), 150.0)
        self.assertEqual(poi_coordinate_radius_km(1, "low"), DEFAULT_CITY_COORDINATE_RADIUS_KM)
        self.assertGreater(poi_coordinate_radius_km(5, "high"), 250.0)

    def test_province_scope_widens_the_radius(self):
        self.assertEqual(
            poi_coordinate_radius_km(3, "normal", province_scope=True),
            DEFAULT_PROVINCE_COORDINATE_RADIUS_KM,
        )

    def test_env_override_wins_and_must_be_positive(self):
        with mock.patch.dict(os.environ, {"CITY_COORDINATE_RADIUS_KM": "35"}):
            self.assertEqual(poi_coordinate_radius_km(5, "high", province_scope=True), 35.0)
        with mock.patch.dict(os.environ, {"CITY_COORDINATE_RADIUS_KM": "0"}):
            self.assertEqual(poi_coordinate_radius_km(3, "normal"), 150.0)
        with mock.patch.dict(os.environ, {"CITY_COORDINATE_RADIUS_KM": "abc"}):
            self.assertEqual(poi_coordinate_radius_km(3, "normal"), 150.0)


class AgentWiringTests(unittest.TestCase):
    def test_both_main_and_fallback_paths_are_guarded(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "api", "agent.py"), encoding="utf-8") as handle:
            source = handle.read()
        # 主路径（LLM 出稿）与候选池兜底路径都要接线，否则降级模式下坐标不受约束
        self.assertGreaterEqual(source.count("clear_out_of_area_coordinates("), 2)
        self.assertGreaterEqual(source.count("poi_coordinate_radius_km("), 2)
        self.assertIn('final_data["out_of_area_coordinates"]', source)
        self.assertIn("copy=False", source)
        # 省域行程必须把片区中心也算作合法中心
        self.assertIn('_anchor.get("center")', source)


if __name__ == "__main__":
    unittest.main()
