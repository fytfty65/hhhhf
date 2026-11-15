"""省域范围纯函数单测：省名归一、地州锚点限额、地理分片、片区天数分配、跨城估算。"""

import unittest

from core.poi_pool import haversine_km
from core.province_scope import (
    assign_cluster_days,
    cluster_places,
    estimate_rail_hours,
    pick_anchors,
    province_of,
    redistribute_province_days,
    short_region_name,
)


def _district(name: str, center: str) -> dict:
    return {"name": name, "center": center, "adcode": "", "level": "city"}


SICHUAN = [
    _district("成都市", "104.065735,30.659462"),
    _district("阿坝藏族羌族自治州", "102.221374,31.899592"),
    _district("甘孜藏族自治州", "101.963815,30.050663"),
    _district("乐山市", "103.761263,29.582019"),
    _district("雅安市", "103.001033,29.987722"),
    _district("凉山彝族自治州", "102.264449,27.895979"),
    _district("绵阳市", "104.741722,31.464206"),
    _district("广元市", "105.829757,32.433668"),
    _district("南充市", "106.082974,30.795281"),
    _district("巴中市", "106.753669,31.858809"),
    _district("达州市", "107.502262,31.209484"),
    _district("广安市", "106.633367,30.456398"),
    _district("遂宁市", "105.571331,30.513311"),
    _district("内江市", "105.066117,29.579346"),
    _district("自贡市", "104.778442,29.339184"),
    _district("泸州市", "105.443348,28.889141"),
    _district("宜宾市", "104.630864,28.760189"),
    _district("攀枝花市", "101.716007,26.580446"),
    _district("眉山市", "103.831788,30.048317"),
    _district("资阳市", "104.641917,30.122211"),
    _district("德阳市", "104.398703,31.127991"),
]


class ProvinceOfTest(unittest.TestCase):
    def test_full_and_short_names(self):
        self.assertEqual(province_of("四川"), "四川")
        self.assertEqual(province_of("四川省"), "四川")
        self.assertEqual(province_of("广西壮族自治区"), "广西")
        self.assertEqual(province_of("新疆维吾尔自治区"), "新疆")
        self.assertEqual(province_of("内蒙古自治区"), "内蒙古")

    def test_municipalities_are_not_province_scope(self):
        for name in ("北京", "北京市", "上海", "天津", "重庆", "香港特别行政区"):
            self.assertEqual(province_of(name), "")

    def test_city_and_prefecture_are_not_province_scope(self):
        for name in ("成都", "九寨沟", "阿坝藏族羌族自治州", ""):
            self.assertEqual(province_of(name), "")


class ShortRegionNameTest(unittest.TestCase):
    def test_ethnic_autonomous_names(self):
        self.assertEqual(short_region_name("阿坝藏族羌族自治州"), "阿坝")
        self.assertEqual(short_region_name("甘孜藏族自治州"), "甘孜")
        self.assertEqual(short_region_name("巴音郭楞蒙古自治州"), "巴音郭楞")
        self.assertEqual(short_region_name("湘西土家族苗族自治州"), "湘西")
        self.assertEqual(short_region_name("延边朝鲜族自治州"), "延边")
        # 族群词在前缀：不能把「怒江」切空
        self.assertEqual(short_region_name("怒江傈僳族自治州"), "怒江")

    def test_city_region_and_league(self):
        self.assertEqual(short_region_name("乐山市"), "乐山")
        self.assertEqual(short_region_name("大兴安岭地区"), "大兴安岭")
        self.assertEqual(short_region_name("阿拉善盟"), "阿拉善")
        self.assertEqual(short_region_name("海西蒙古族藏族自治州"), "海西")


class PickAnchorsTest(unittest.TestCase):
    def test_short_trip_is_capped_but_covers_west_sichuan(self):
        anchors = pick_anchors(SICHUAN, 3, capital="成都")
        self.assertEqual(len(anchors), 6)
        names = [item["name"] for item in anchors]
        self.assertEqual(names[0], "成都市")
        # 九寨沟/四姑娘山在阿坝、稻城亚丁在甘孜：3 天预算必须把它们查进来
        self.assertIn("阿坝藏族羌族自治州", names)
        self.assertIn("甘孜藏族自治州", names)

    def test_mid_trip_and_long_trip(self):
        self.assertEqual(len(pick_anchors(SICHUAN, 5, capital="成都")), 9)
        self.assertEqual(len(pick_anchors(SICHUAN, 7, capital="成都")), len(SICHUAN))

    def test_empty_input(self):
        self.assertEqual(pick_anchors([], 3, capital="成都"), [])


class ClusterAndDaysTest(unittest.TestCase):
    def test_cluster_splits_far_places(self):
        clusters = cluster_places([
            {"name": "都江堰", "lnglat": [103.62, 31.00]},
            {"name": "成都市区", "lnglat": [104.07, 30.66]},
            {"name": "九寨沟", "lnglat": [103.92, 33.26]},
        ])
        self.assertEqual(len(clusters), 2)
        sizes = sorted(len(cluster) for cluster in clusters)
        self.assertEqual(sizes, [1, 2])

    def test_cluster_keeps_coordless_places_separate(self):
        clusters = cluster_places([
            {"name": "无坐标点"},
            {"name": "成都", "lnglat": [104.07, 30.66]},
        ])
        self.assertEqual(len(clusters), 2)

    def test_assign_days_sum_and_min_one(self):
        clusters = [
            [{"lnglat": [104.07, 30.66]}, {"lnglat": [103.62, 31.00]}],
            [{"lnglat": [103.92, 33.26]}],
            [{"lnglat": [101.96, 30.05]}],
        ]
        result = assign_cluster_days(clusters, 5, 3, start_cluster=0)
        self.assertEqual(len(result["day_to_cluster"]), 5)
        self.assertEqual(result["day_to_cluster"][0], 0)
        self.assertEqual(sum(result["cluster_days"].values()), 5)
        self.assertTrue(all(count >= 1 for count in result["cluster_days"].values()))

    def test_assign_days_empty(self):
        result = assign_cluster_days([], 3)
        self.assertEqual(result["day_to_cluster"], [-1, -1, -1])
        self.assertEqual(result["used_clusters"], 0)


class ChooseRegionLineTest(unittest.TestCase):
    """一个地州跨 250km 时只走一条线（2026-11-02 实测：阿坝两天跑九寨沟+四姑娘山 = 451km）。"""

    def test_abazhou_keeps_one_direction(self):
        from core.province_scope import choose_region_line

        rows = [
            {"name": "九寨沟", "lnglat": [103.92, 33.26], "fitness_score": 9.0},
            {"name": "四姑娘山", "lnglat": [102.83, 31.09], "fitness_score": 8.5},
            {"name": "毕棚沟", "lnglat": [102.99, 31.38], "fitness_score": 8.0},
            {"name": "玛嘉沟", "lnglat": [102.43, 31.53], "fitness_score": 7.5},
            {"name": "孟屯河谷", "lnglat": [103.11, 31.71], "fitness_score": 7.0},
            {"name": "羊茸村彩林", "lnglat": [102.86, 32.11], "fitness_score": 7.2},
        ]
        result = choose_region_line(rows)
        self.assertTrue(result["applied"], "阿坝跨 250km，应该拆")
        self.assertGreater(result["span_km"], 180)
        kept_names = {row["name"] for row in result["kept"]}
        self.assertNotIn("九寨沟", kept_names, "九寨沟与四姑娘山方向不该同时进这 2 天")
        self.assertIn("四姑娘山", kept_names)
        # 黑水方向（羊茸村）离四姑娘山 113km：单日往返 226km，同样不该和四姑娘山挤在一天
        self.assertNotIn("羊茸村彩林", kept_names)
        self.assertEqual(
            {row["name"] for row in result["dropped"]}, {"九寨沟", "羊茸村彩林"}
        )
        self.assertIn("九寨沟", result["dropped_label"])
        # 留下来的这一组必须**两两间距 ≤ 80km**（单链接会把它们串成 140km 的长线；
        # 80km 量级在一组点走完通常是 100~160km，仍在当天预算的 10% 宽限内）
        kept = [row for row in rows if row["name"] in kept_names]
        for index, left in enumerate(kept):
            for right in kept[index + 1:]:
                km = haversine_km(
                    left["lnglat"], right["lnglat"]
                )
                self.assertLessEqual(km, 80.0, f"{left['name']} ↔ {right['name']} = {km:.0f}km")

    def test_compact_region_is_not_split(self):
        from core.province_scope import choose_region_line

        rows = [
            {"name": "都江堰", "lnglat": [103.62, 31.00]},
            {"name": "青城山", "lnglat": [103.57, 30.90]},
            {"name": "市区景点", "lnglat": [104.07, 30.66]},
        ]
        result = choose_region_line(rows)
        self.assertFalse(result["applied"], "成都周边跨距不到 180km，不该拆")
        self.assertEqual(len(result["kept"]), 3)
        self.assertEqual(result["dropped"], [])

    def test_coordless_rows_are_kept(self):
        from core.province_scope import choose_region_line

        rows = [
            {"name": "九寨沟", "lnglat": [103.92, 33.26]},
            {"name": "四姑娘山", "lnglat": [102.83, 31.09]},
            {"name": "毕棚沟", "lnglat": [102.99, 31.38]},
            {"name": "无坐标候选"},
        ]
        result = choose_region_line(rows)
        self.assertTrue(result["applied"])
        self.assertIn("无坐标候选", {row["name"] for row in result["kept"]})

    def test_single_candidate_never_splits(self):
        from core.province_scope import choose_region_line

        result = choose_region_line([{"name": "九寨沟", "lnglat": [103.92, 33.26]}])
        self.assertFalse(result["applied"])
        self.assertEqual(len(result["kept"]), 1)


class RailEstimateTest(unittest.TestCase):
    def test_chengdu_to_jiuzhaigou_is_a_real_transfer(self):
        hours = estimate_rail_hours([104.07, 30.66], [103.92, 33.26])
        self.assertIsNotNone(hours)
        # 直线约 290km：200km/h + 0.5h 进出站 ≈ 2 小时量级
        self.assertGreaterEqual(hours, 1.5)
        self.assertLessEqual(hours, 3.5)

    def test_missing_coords(self):
        self.assertIsNone(estimate_rail_hours([], [103.92, 33.26]))
        self.assertIsNone(estimate_rail_hours("", "103.92,33.26"))


REGION_CENTERS = {
    "成都": [104.07, 30.66],
    "阿坝": [102.22, 31.90],
    "甘孜": [101.96, 30.05],
}


def _node(name: str, lnglat: list, region: str, day: int) -> dict:
    return {"name": name, "lnglat": lnglat, "region_city": region, "day": day}


def _region_of(node: dict) -> str:
    return str(node.get("region_city") or "")


def _region_center(region: str) -> list:
    return REGION_CENTERS.get(region, [])


class ProvinceFeasibilityTest(unittest.TestCase):
    """省域口径：离省会 300km 不等于"一天跑不完"，但同一天跨太多片不行。"""

    def test_jiuzhaigou_300km_from_capital_is_not_a_violation(self):
        from core.travel_feasibility import evaluate_province_feasibility

        route = [
            _node("成都大熊猫基地", [104.14, 30.74], "成都", 1),
            _node("武侯祠", [104.05, 30.64], "成都", 1),
        ]
        report = evaluate_province_feasibility(
            route, region_of=_region_of, region_center_of=_region_center, days=1
        )
        self.assertTrue(report["ok"], report["notes"])
        self.assertEqual(report["scope"], "province")
        # 旧口径里的"远景区过多"在省域口径下不再出现
        codes = {item["code"] for item in report["violations"]}
        self.assertNotIn("too_many_far_scenic_in_one_day", codes)

    def test_transfer_day_with_two_regions_is_allowed(self):
        from core.travel_feasibility import evaluate_province_feasibility

        route = [
            _node("成都东站", [104.14, 30.63], "成都", 2),
            _node("九寨沟", [103.92, 33.26], "阿坝", 2),
        ]
        report = evaluate_province_feasibility(
            route, region_of=_region_of, region_center_of=_region_center, days=2
        )
        self.assertTrue(report["ok"], report["notes"])
        day_two = report["days"][1]
        self.assertEqual(day_two["regions"], ["成都", "阿坝"])
        self.assertIsNotNone(day_two["transfer"])
        self.assertLessEqual(day_two["transfer"]["hours"], 5.0)

    def test_three_regions_or_long_transfer_are_flagged(self):
        from core.travel_feasibility import evaluate_province_feasibility

        three = [
            _node("成都大熊猫基地", [104.14, 30.74], "成都", 1),
            _node("九寨沟", [103.92, 33.26], "阿坝", 1),
            _node("稻城亚丁", [100.30, 28.43], "甘孜", 1),
        ]
        report = evaluate_province_feasibility(
            three, region_of=_region_of, region_center_of=_region_center, days=1
        )
        self.assertFalse(report["ok"])
        self.assertIn("too_many_regions_in_one_day", {item["code"] for item in report["violations"]})

        two = [
            _node("成都大熊猫基地", [104.14, 30.74], "成都", 1),
            _node("稻城亚丁", [100.30, 28.43], "甘孜", 1),
        ]
        report = evaluate_province_feasibility(
            two, region_of=_region_of, region_center_of=_region_center, days=1, max_transfer_hours=1.0
        )
        self.assertIn("transfer_too_long", {item["code"] for item in report["violations"]})

    def test_intra_region_mileage_still_budgeted(self):
        from core.travel_feasibility import evaluate_province_feasibility

        route = [
            _node("黄龙", [103.83, 32.75], "阿坝", 1),
            _node("四姑娘山", [102.93, 31.08], "阿坝", 1),
        ]
        report = evaluate_province_feasibility(
            route, region_of=_region_of, region_center_of=_region_center, days=1
        )
        self.assertFalse(report["ok"])
        self.assertIn("daily_travel_budget_exceeded", {item["code"] for item in report["violations"]})

    def test_repair_moves_out_other_regions_only(self):
        from core.travel_feasibility import repair_province_overload

        route = [
            _node("成都大熊猫基地", [104.14, 30.74], "成都", 1),
            _node("武侯祠", [104.05, 30.64], "成都", 1),
            _node("九寨沟", [103.92, 33.26], "阿坝", 1),
            _node("稻城亚丁", [100.30, 28.43], "甘孜", 1),
        ]
        kept, removed = repair_province_overload(
            route, region_of=_region_of, region_center_of=_region_center
        )
        self.assertEqual([item["name"] for item in kept], ["成都大熊猫基地", "武侯祠"])
        self.assertEqual([item["name"] for item in removed], ["九寨沟", "稻城亚丁"])
        self.assertTrue(all("片区" in item["removed_reason"] for item in removed))

    def test_ping_pong_between_two_regions_is_flagged(self):
        """成都↔阿坝来回两趟只出现过两个片区，但一天跑 600km+，必须判不可行。

        实测（2026-11-02 四川 4 天兜底路线）：第 4 天 成都→阿坝→成都→阿坝，
        质量闸门算出 622km，省域口径却只报 62.8km（跨片区那几段按设计不计入），
        两边结论互相矛盾 —— 这里补上"来回次数"这一维。
        """
        from core.travel_feasibility import evaluate_province_feasibility

        route = [
            _node("丹景台", [104.26, 30.35], "成都", 1),
            _node("红桦林观景点", [102.84, 32.28], "阿坝", 1),
            _node("新繁东湖", [104.02, 30.87], "成都", 1),
            _node("羊茸村彩林", [102.86, 32.11], "阿坝", 1),
        ]
        report = evaluate_province_feasibility(
            route, region_of=_region_of, region_center_of=_region_center, days=1
        )
        self.assertFalse(report["ok"])
        self.assertIn("too_many_regions_in_one_day", {item["code"] for item in report["violations"]})
        self.assertEqual(report["days"][0]["regions"], ["成都", "阿坝"])

    def test_repair_keeps_at_least_one_node(self):
        from core.travel_feasibility import repair_province_overload

        route = [
            _node("九寨沟", [103.92, 33.26], "阿坝", 1),
            _node("稻城亚丁", [100.30, 28.43], "甘孜", 1),
            _node("成都大熊猫基地", [104.14, 30.74], "成都", 1),
        ]
        kept, removed = repair_province_overload(
            route, region_of=_region_of, region_center_of=_region_center
        )
        self.assertGreaterEqual(len(kept), 1)
        self.assertEqual(len(kept) + len(removed), len(route))


def _day_node(name: str, lnglat: list, region: str, day: int, category: str) -> dict:
    return {
        "name": name,
        "lnglat": lnglat,
        "region_city": region,
        "day": day,
        "planning_category": category,
        "is_hotel": category == "hotel",
    }


class RedistributeProvinceDaysTest(unittest.TestCase):
    """方案 1（按子片区拆天）：同一片区占多天时，把点按地理线摊到每天都合规。"""

    def _run_t_route(self) -> list:
        """复刻 2026-11-02 四川 4 天 LLM 路线：阿坝 4 个景区全在第 3 天，第 4 天退回成都餐饮。"""
        return [
            _day_node("都江堰景区", [103.61, 31.00], "成都", 1, "scenic"),
            _day_node("成都新繁某酒店", [104.01, 30.87], "成都", 1, "hotel"),
            _day_node("龙泉山风景区", [104.31, 30.54], "成都", 2, "scenic"),
            _day_node("成都大慈寺旁民宿", [104.18, 30.65], "成都", 2, "hotel"),
            _day_node("毕棚沟景区", [102.994, 31.381], "阿坝", 3, "scenic"),
            _day_node("山水餐厅", [102.976, 31.413], "阿坝", 3, "food"),
            _day_node("四姑娘山景区", [102.826, 31.094], "阿坝", 3, "scenic"),
            _day_node("玛嘉沟生态旅游景区", [102.427, 31.534], "阿坝", 3, "scenic"),
            _day_node("孟屯河谷高桥沟景区", [103.109, 31.712], "阿坝", 3, "scenic"),
            _day_node("四姑娘山向往的民宿", [102.789, 31.054], "阿坝", 3, "hotel"),
            _day_node("建设路夜市", [104.102, 30.669], "成都", 4, "scenic"),
            _day_node("重庆特色面庄", [104.103, 30.644], "成都", 4, "food"),
            _day_node("谢烤鸭特色家常菜", [104.136, 30.778], "成都", 4, "food"),
        ]

    def test_four_scenic_spots_are_split_over_two_region_days(self):
        route = self._run_t_route()
        result = redistribute_province_days(
            route,
            day_regions={1: "成都", 2: "成都", 3: "阿坝", 4: "阿坝"},
            region_of=_region_of,
            days=4,
            budget_km=176.0,
        )
        self.assertTrue(result["applied"], result["notes"])
        by_day: dict = {}
        for node in result["route"]:
            by_day.setdefault(int(node["day"]), []).append(node)

        for day in (3, 4):
            categories = [item["planning_category"] for item in by_day[day]]
            self.assertIn("scenic", categories, f"第 {day} 天没有玩点：{categories}")
            self.assertTrue(
                all(_region_of(item) == "阿坝" for item in by_day[day]),
                f"第 {day} 天不该跨到别的片区：{[_region_of(item) for item in by_day[day]]}",
            )
            report = next(item for item in result["days"] if item["day"] == day)
            self.assertLessEqual(report["travel_km"], 176.0, report)

        # 阿坝 4 个景区分两天，不能再出现"第 3 天 4 个、第 4 天 0 个"
        day3_scenic = [item["name"] for item in by_day[3] if item["planning_category"] == "scenic"]
        day4_scenic = [item["name"] for item in by_day[4] if item["planning_category"] == "scenic"]
        self.assertGreaterEqual(len(day3_scenic), 1, day3_scenic)
        self.assertGreaterEqual(len(day4_scenic), 1, day4_scenic)
        self.assertLessEqual(len(day3_scenic + day4_scenic), 4)

        dropped_names = [item["name"] for item in result["dropped"]]
        self.assertIn("建设路夜市", dropped_names, "整天在成都的第 4 天节点应被移出")

    def test_compliant_block_is_left_alone(self):
        route = [
            _day_node("毕棚沟景区", [102.994, 31.381], "阿坝", 1, "scenic"),
            _day_node("玛嘉沟生态旅游景区", [102.427, 31.534], "阿坝", 2, "scenic"),
            _day_node("山水餐厅", [102.976, 31.413], "阿坝", 1, "food"),
        ]
        result = redistribute_province_days(
            route,
            day_regions={1: "阿坝", 2: "阿坝"},
            region_of=_region_of,
            days=2,
            budget_km=176.0,
        )
        self.assertFalse(result["applied"])
        self.assertEqual([item["name"] for item in result["route"]], [item["name"] for item in route])

    def test_pool_fills_days_emptied_by_cross_region_nodes(self):
        route = self._run_t_route()
        pool = [
            _day_node("九鼎山风景区", [103.65, 31.55], "阿坝", 4, "scenic"),
            _day_node("老马坪森林公园", [102.17, 31.36], "阿坝", 4, "scenic"),
            _day_node("理县藏餐", [102.99, 31.40], "阿坝", 4, "food"),
            _day_node("成都某火锅", [104.06, 30.66], "成都", 4, "food"),
        ]
        result = redistribute_province_days(
            route,
            day_regions={1: "成都", 2: "成都", 3: "阿坝", 4: "阿坝"},
            region_of=_region_of,
            days=4,
            budget_km=176.0,
            pool=pool,
            # 子片区收敛已向用户声明"另一条线这次不排"，补点不能把它塞回来
            region_allow={"阿坝": {"九鼎山风景区"}},
        )
        added_names = [item["name"] for item in result["added"]]
        self.assertIn("九鼎山风景区", added_names, "移出的名额应优先用同片区玩点补")
        self.assertNotIn("老马坪森林公园", added_names, "已声明不排的子片区不能补回来")
        self.assertNotIn("成都某火锅", added_names, "不许拿别的片区补")
        day4 = [item for item in result["route"] if int(item["day"]) == 4]
        self.assertTrue(all(_region_of(item) == "阿坝" for item in day4), day4)

    def test_hotel_anchor_is_not_left_stranded_by_the_split(self):
        """复刻 run y（2026-11-02 四川）：门禁补点后 Day 3 = 四姑娘山+玛嘉沟+孟屯+饭点，
        Day 4 只剩毕棚沟+饭点。按数组顺序算 Day 3 已经 212km（超预算），
        Day 4 又只剩一个玩点。按片区拆天后两天都要在预算内、且每天都得还有玩点。"""
        route = [
            _day_node("都江堰景区", [103.610529, 31.003363], "成都", 1, "scenic"),
            _day_node("成都三岔湖度假酒店", [104.267494, 30.327746], "成都", 1, "hotel"),
            _day_node("麓湖水城景区", [104.064974, 30.458829], "成都", 2, "scenic"),
            _day_node("新繁东湖酒店", [104.01436, 30.874423], "成都", 2, "hotel"),
            _day_node("四姑娘山景区", [102.826018, 31.094381], "阿坝", 3, "scenic"),
            _day_node("玛嘉沟生态旅游景区", [102.426794, 31.533639], "阿坝", 3, "scenic"),
            _day_node("孟屯河谷高桥沟景区", [103.109373, 31.712019], "阿坝", 3, "scenic"),
            _day_node("八壤藏家楼", [103.113063, 31.709576], "阿坝", 3, "food"),
            _day_node("四姑娘山向往的民宿", [102.789259, 31.053722], "阿坝", 3, "hotel"),
            _day_node("毕棚沟景区", [102.994424, 31.38062], "阿坝", 4, "scenic"),
            _day_node("诺布咖啡藏餐厅", [102.981118, 31.412006], "阿坝", 4, "food"),
        ]
        result = redistribute_province_days(
            route,
            day_regions={1: "成都", 2: "成都", 3: "阿坝", 4: "阿坝"},
            region_of=_region_of,
            days=4,
            budget_km=176.0,
        )
        self.assertTrue(result["applied"], result["notes"])
        by_day: dict = {}
        for node in result["route"]:
            by_day.setdefault(int(node["day"]), []).append(node)
        for day in (3, 4):
            report = next(item for item in result["days"] if item["day"] == day)
            self.assertLessEqual(report["travel_km"], 176.0, report)
            self.assertTrue(
                any(item["planning_category"] == "scenic" for item in by_day[day]),
                f"第 {day} 天又被拆成没有玩点：{by_day[day]}",
            )
        hotels = [item["name"] for item in by_day[3] if item["planning_category"] == "hotel"]
        self.assertIn("四姑娘山向往的民宿", hotels, "住宿不归这里搬，必须留在原来的天")


if __name__ == "__main__":
    unittest.main()
