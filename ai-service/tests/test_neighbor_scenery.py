"""周边县市景区（"真景区"补检索）测试。

真实证据（2026-10-04，tools/probe_neighbor_scenery.py 库尔勒实测）：
  市区检索 `city=库尔勒` + 自然关键词 → 7 条，**全是市区公园，全部 ≤5km**；
  上级区域检索 `city=巴音郭楞蒙古自治州` + 景区专用关键词 →
    博斯腾湖风景名胜区 47km、博湖县南山沙漠公园 50km、相思湖旅游度假区 29km、
    博斯腾湖风景名胜区芦花港 55km、博斯腾湖风景名胜区阿洪口 32km、罗布湖 108km…
  高德 geocode 对"库尔勒"回 `city=巴音郭楞蒙古自治州 / district=库尔勒市 / level=区县`，
  所以上级区域可以自动解析，不用维护映射表。
"""

import unittest

from core.poi_pool import (
    NEIGHBOR_SCENIC_KEYWORDS,
    dedupe_sub_spots,
    filter_by_radius,
    haversine_km,
    is_play_worthy,
    parent_region,
    scenic_radius_km,
)

# 真实 geocode 返回（原文抄自探针输出）
KUERLE_GEOCODE = {
    "province": "新疆维吾尔自治区",
    "city": "巴音郭楞蒙古自治州",
    "district": "库尔勒市",
    "level": "区县",
    "adcode": "652801",
    "center": "86.174633,41.725891",
}
URUMQI_GEOCODE = {
    "province": "新疆维吾尔自治区",
    "city": "乌鲁木齐市",
    "district": "天山区",
    "level": "市",
    "adcode": "650102",
    "center": "87.617733,43.792818",
}
CENTER = [86.174633, 41.725891]


class ParentRegionTests(unittest.TestCase):
    def test_county_level_city_resolves_to_prefecture(self):
        self.assertEqual(parent_region("库尔勒", KUERLE_GEOCODE), "巴音郭楞蒙古自治州")

    def test_prefecture_level_city_has_no_parent(self):
        """乌鲁木齐本身就是地级市：没有更大的"上级区域"可补，返回空串（不做多余检索）。"""
        self.assertEqual(parent_region("乌鲁木齐", URUMQI_GEOCODE), "")

    def test_same_name_is_not_a_parent(self):
        self.assertEqual(parent_region("巴音郭楞蒙古自治州", KUERLE_GEOCODE), "")

    def test_missing_geocode_is_honest_empty(self):
        self.assertEqual(parent_region("库尔勒", {}), "")
        self.assertEqual(parent_region("库尔勒", None), "")
        self.assertEqual(parent_region("", KUERLE_GEOCODE), "")


class RadiusTests(unittest.TestCase):
    def test_radius_grows_with_trip_length(self):
        self.assertEqual(scenic_radius_km(1), 90)
        self.assertEqual(scenic_radius_km(2), 90)
        self.assertEqual(scenic_radius_km(3), 150)
        self.assertEqual(scenic_radius_km(4), 150)
        self.assertEqual(scenic_radius_km(7), 250)

    def test_real_distances_are_sane(self):
        """用真实坐标校验：博斯腾湖约 47km、罗布人村寨约 75km、罗布湖 108km。"""
        bosten = [86.7600, 41.9800]  # 博斯腾湖大河口附近
        distance = haversine_km(CENTER, bosten)
        self.assertTrue(35 <= distance <= 60, f"博斯腾湖距离应在 35-60km，实际 {distance:.0f}km")
        self.assertEqual(filter_by_radius([{"name": "博斯腾湖", "lnglat": bosten}], CENTER, scenic_radius_km(2))[1], 0)

    def test_too_far_for_short_trip_is_dropped_and_counted(self):
        far = [{"name": "罗布湖", "lnglat": [86.9, 40.8]}]  # 约 100km 以上
        kept, dropped = filter_by_radius(far, CENTER, scenic_radius_km(2))
        self.assertEqual(kept, [])
        self.assertEqual(dropped, 1, "太远必须被丢掉并计数（不能悄悄留在池子里）")

    def test_missing_coordinates_are_kept_not_dropped(self):
        """缺坐标宁可保留（当成"不知道多远"），也不能因为没坐标丢掉真景区。"""
        kept, dropped = filter_by_radius([{"name": "某景区"}], CENTER, 90)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, 0)

    def test_out_of_town_flag_and_distance_recorded(self):
        rows = [
            {"name": "市区公园", "lnglat": [86.19, 41.73]},
            {"name": "博斯腾湖", "lnglat": [86.76, 41.98]},
        ]
        kept, _ = filter_by_radius(rows, CENTER, 90)
        by_name = {item["name"]: item for item in kept}
        self.assertNotIn("out_of_town", by_name["市区公园"], "市区内的点不该标成周边")
        self.assertTrue(by_name["博斯腾湖"].get("out_of_town"))
        self.assertGreater(by_name["博斯腾湖"]["distance_km"], 15)


class SubSpotDedupeTests(unittest.TestCase):
    def test_sub_spots_of_the_same_scenic_area_are_merged(self):
        raw = [
            {"name": "博斯腾湖风景名胜区芦花港"},
            {"name": "博斯腾湖风景名胜区"},
            {"name": "博斯腾湖风景名胜区阿洪口"},
            {"name": "博斯腾湖大河口景区"},
            {"name": "相思湖旅游度假区"},
        ]
        kept, merged = dedupe_sub_spots(raw)
        names = [item["name"] for item in kept]
        self.assertIn("博斯腾湖风景名胜区", names, "主景区名要留下（它最短）")
        self.assertNotIn("博斯腾湖风景名胜区芦花港", names, "子景点应被合并")
        self.assertNotIn("博斯腾湖风景名胜区阿洪口", names)
        self.assertIn("相思湖旅游度假区", names, "不同景区不能被误合并")
        self.assertEqual(merged, 2)

    def test_distinct_places_are_all_kept(self):
        raw = [{"name": "天鹅湖广场"}, {"name": "塔什店矿山湖"}, {"name": "博湖县南山沙漠公园"}]
        kept, merged = dedupe_sub_spots(raw)
        self.assertEqual(len(kept), 3)
        self.assertEqual(merged, 0)

    def test_nameless_items_are_ignored(self):
        kept, _ = dedupe_sub_spots([{"name": ""}, {"name": "博斯腾湖风景名胜区"}])
        self.assertEqual([item["name"] for item in kept], ["博斯腾湖风景名胜区"])


class SceneryKeywordAndFacilityTests(unittest.TestCase):
    def test_neighbor_keywords_target_real_scenery(self):
        for keyword in ("风景名胜区", "自然保护区", "沙漠", "草原", "峡谷", "胡杨林", "地质公园"):
            self.assertIn(keyword, NEIGHBOR_SCENIC_KEYWORDS)

    def test_scenic_area_parts_are_not_attractions(self):
        """探针里搜出来的一堆"零件"不该当景点：码头/扬水站/假山/户外舞台。"""
        for name in ("梨香湖码头", "博斯腾湖风景名胜区-扬水站景区", "博斯腾湖零公里景区-假山", "露营BBQ营地-户外舞台"):
            self.assertFalse(is_play_worthy(name, "风景名胜;风景名胜;旅游景点"), f"{name} 不该算景点")

    def test_real_scenic_areas_survive_the_filter(self):
        for name in ("博斯腾湖风景名胜区", "新疆罗布人村寨景区", "博湖县南山沙漠公园", "相思湖旅游度假区"):
            self.assertTrue(is_play_worthy(name, "风景名胜;风景名胜;旅游景点"), f"{name} 被误杀了")


if __name__ == "__main__":
    unittest.main()
