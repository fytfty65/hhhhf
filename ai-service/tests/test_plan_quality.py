"""规划质量评分器的确定性单测（阶段 0）。

覆盖：硬约束门禁（天数/预算/必去/时间窗/开放时间）、加权维度（偏好覆盖/节奏/空间/
真实性/多样性）、多方案稳定性，以及金标需求集的 schema 自检。
全部离线、无随机——CI 里 `python -m unittest discover -s tests` 会跑到。
"""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.itinerary_skeleton import build_skeleton, horizon_advisories, segment_days  # noqa: E402
from core.plan_quality import (  # noqa: E402
    DEFAULT_WEIGHTS,
    check_hard_constraints,
    evaluate_plan,
    haversine_km,
    parse_clock,
    parse_open_window,
    plan_quality_snapshot,
    score_anchoring,
    score_diversity,
    score_pacing,
    score_preference_coverage,
    score_space_efficiency,
    score_truthfulness,
    stability_score,
)

GOLDEN = Path(__file__).resolve().parent / "eval" / "golden_cases.json"


def node(name, day, time, lnglat=(108.94, 34.26), node_type="文化", cost="¥50",
         open_time="08:00-18:00", source="amap", estimated=False, **extra):
    return {
        "day": day,
        "name": name,
        "location": name,
        "time": time,
        "lnglat": list(lnglat),
        "type": node_type,
        "cost_estimate": cost,
        "open_time": open_time,
        "rating": "4.5",
        "data_sources": {"cost_estimate": source, "open_time": source, "rating": source},
        "estimated": estimated,
        **extra,
    }


def good_plan():
    return {"route": [
        node("钟楼酒店", 1, "09:00", (108.94, 34.26), "住宿", "¥300", "00:00-23:59"),
        node("陕西历史博物馆", 1, "10:30", (108.95, 34.22), "博物馆", "¥0", "09:00-17:00"),
        node("回民街小吃", 1, "12:30", (108.94, 34.26), "餐饮", "¥60", "10:00-22:00"),
        node("大雁塔", 1, "15:00", (108.96, 34.22), "文化", "¥50", "08:00-18:00"),
        node("城墙南门", 2, "09:30", (108.94, 34.25), "文化", "¥54", "08:00-20:00"),
        node("回民街午餐", 2, "12:30", (108.94, 34.26), "餐饮", "¥60", "10:00-22:00"),
        node("大唐不夜城", 2, "17:00", (108.96, 34.21), "夜景", "¥0", "00:00-23:59"),
    ]}


BASE_CONTEXT = {"city": "西安", "days": 2, "budget": 2000, "preferences": {"pace": "relaxed", "interest": ["博物馆", "美食"]}}
BASE_EXPECTATIONS = {"must_have": ["酒店", "餐"], "min_nodes_per_day": 2, "max_nodes_per_day": 4}


class TestClockParsing(unittest.TestCase):
    def test_parse_clock_variants(self):
        self.assertEqual(parse_clock("09:00"), 540)
        self.assertEqual(parse_clock("Day 1 | 09:30"), 570)
        self.assertEqual(parse_clock("9:05"), 545)
        self.assertIsNone(parse_clock("暂无供应商数据"))
        self.assertIsNone(parse_clock(""))
        self.assertIsNone(parse_clock("晚上"))

    def test_parse_open_window(self):
        self.assertEqual(parse_open_window("09:00-17:00"), (540, 1020))
        self.assertEqual(parse_open_window("09:00～17:30"), (540, 1050))
        self.assertEqual(parse_open_window("08:00"), (480, 23 * 60))
        self.assertIsNone(parse_open_window("暂无供应商数据"))
        self.assertIsNone(parse_open_window("周一闭馆"))

    def test_haversine_scale(self):
        # 西安钟楼 -> 大雁塔 约 4-5 公里
        distance = haversine_km((108.94, 34.26), (108.96, 34.22))
        self.assertTrue(3.0 < distance < 6.0, distance)


class TestHardConstraints(unittest.TestCase):
    def test_good_plan_passes_and_has_no_unverifiable(self):
        report = check_hard_constraints(BASE_CONTEXT, good_plan(), BASE_EXPECTATIONS)
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["unverifiable"], [])

    def test_empty_day_fails(self):
        plan = {"route": [n for n in good_plan()["route"] if n["day"] == 1]}
        report = check_hard_constraints(BASE_CONTEXT, plan, BASE_EXPECTATIONS)
        codes = [item["code"] for item in report["failures"]]
        self.assertIn("day_empty", codes)

    def test_thin_day_fails(self):
        plan = {"route": [
            node("酒店", 1, "09:00", node_type="住宿", open_time="00:00-23:59"),
            node("博物馆", 1, "10:00", node_type="博物馆"),
            node("回民街", 2, "12:00", node_type="餐饮"),
        ]}
        report = check_hard_constraints(BASE_CONTEXT, plan, BASE_EXPECTATIONS)
        codes = [item["code"] for item in report["failures"]]
        self.assertIn("day_too_thin", codes)

    def test_budget_exceeded_only_when_prices_known(self):
        plan = {"route": [
            node("酒店", 1, "09:00", node_type="住宿", cost="¥2000", open_time="00:00-23:59"),
            node("博物馆", 1, "10:00", node_type="博物馆", cost="¥500"),
            node("回民街", 2, "12:00", node_type="餐饮", cost="¥100"),
            node("城墙", 2, "15:00", node_type="文化", cost="¥100"),
        ]}
        report = check_hard_constraints(BASE_CONTEXT, plan, BASE_EXPECTATIONS)
        self.assertIn("budget_exceeded", [item["code"] for item in report["failures"]])

    def test_unknown_price_is_unverifiable_not_zero(self):
        plan = {"route": [
            node("酒店", 1, "09:00", node_type="住宿", cost="暂无供应商数据", open_time="00:00-23:59"),
            node("博物馆", 1, "10:00", node_type="博物馆", cost="暂无供应商数据"),
            node("回民街", 2, "12:00", node_type="餐饮", cost="暂无供应商数据"),
            node("城墙", 2, "15:00", node_type="文化", cost="暂无供应商数据"),
        ]}
        report = check_hard_constraints(BASE_CONTEXT, plan, BASE_EXPECTATIONS)
        self.assertNotIn("budget_exceeded", [item["code"] for item in report["failures"]])
        self.assertIn("budget_partial", [item["code"] for item in report["unverifiable"]])

    def test_must_have_missing_fails(self):
        plan = {"route": [
            node("大雁塔", 1, "09:00"), node("小雁塔", 1, "11:00"),
            node("城墙", 2, "09:00"), node("碑林", 2, "11:00"),
        ]}
        report = check_hard_constraints(BASE_CONTEXT, plan, BASE_EXPECTATIONS)
        self.assertIn("must_have_missing", [item["code"] for item in report["failures"]])

    def test_time_window_and_order(self):
        plan = {"route": [
            node("酒店", 1, "05:00", node_type="住宿", open_time="00:00-23:59"),
            node("夜宵摊", 1, "23:30", node_type="餐饮", open_time="18:00-23:59"),
            node("博物馆", 2, "14:00", node_type="博物馆"),
        ]}
        report = check_hard_constraints(BASE_CONTEXT, plan, BASE_EXPECTATIONS)
        codes = [item["code"] for item in report["failures"]]
        self.assertIn("time_out_of_window", codes)

        reversed_plan = {"route": [
            node("酒店", 1, "11:00", node_type="住宿", open_time="00:00-23:59"),
            node("博物馆", 1, "09:00", node_type="博物馆"),
        ]}
        report2 = check_hard_constraints(BASE_CONTEXT, reversed_plan, BASE_EXPECTATIONS)
        self.assertIn("day_out_of_order", [item["code"] for item in report2["failures"]])

    def test_open_time_conflict_and_missing(self):
        plan = {"route": [
            node("酒店", 1, "09:00", node_type="住宿", open_time="00:00-23:59"),
            node("博物馆", 1, "07:00", node_type="博物馆", open_time="09:00-17:00"),
            node("回民街", 2, "12:00", node_type="餐饮", open_time="暂无供应商数据"),
            node("城墙", 2, "15:00", node_type="文化", open_time="08:00-20:00"),
        ]}
        report = check_hard_constraints(BASE_CONTEXT, plan, BASE_EXPECTATIONS)
        self.assertIn("opening_hours_conflict", [item["code"] for item in report["failures"]])
        self.assertIn("open_time_missing", [item["code"] for item in report["unverifiable"]])


class TestDimensions(unittest.TestCase):
    def test_preference_coverage(self):
        context = {"preferences": {"interest": ["博物馆", "美食", "购物"]}}
        report = score_preference_coverage(context, good_plan())
        self.assertIn("cultural", report["addressed"])
        self.assertIn("food", report["addressed"])
        self.assertIn("shopping", report["missing"])
        self.assertTrue(0 < report["value"] < 1)

    def test_preference_coverage_empty_request(self):
        report = score_preference_coverage({}, good_plan())
        self.assertEqual(report["value"], 1.0)

    def test_pacing_penalises_overpacked_day(self):
        crowded = {"route": [node(f"景点{i}", 1, f"{8 + i:02d}:00") for i in range(8)]}
        report = score_pacing({"days": 1}, crowded, {"max_nodes_per_day": 4})
        self.assertLess(report["value"], 1.0)
        self.assertTrue(any("超过上限" in p for p in report["penalties"]))

    def test_pacing_detects_meal_window_and_duration(self):
        report = score_pacing({"days": 2}, good_plan(), BASE_EXPECTATIONS)
        self.assertTrue(all(day["has_lunch_window"] for day in report["days"]))
        self.assertEqual(report["value"], 1.0)

    def test_pacing_flags_overlong_day(self):
        plan = {"route": [node(f"点{i}", 1, f"{9 + i:02d}:00") for i in range(6)]}
        report = score_pacing({"daily_active_minutes": 120}, plan, {"max_nodes_per_day": 6})
        self.assertTrue(any("超过 120 分钟上限" in p for p in report["penalties"]))

    def test_missing_dinner_is_an_advisory_not_a_penalty(self):
        """用户口径（2026-10-05）：「放宽上限吧，要是用户不喜欢晚上不安排晚餐，
        在二次增量中说到就补充晚餐位置」。

        没排晚餐**不是缺陷**，是等用户表态的可选偏好；扣分等于系统替用户认定
        "你必须吃晚饭"。真正的动作是"用户提了就用增量补上"——所以只进 advisories。
        """
        plan = {"route": [
            node("景区A", 1, "09:00"),
            node("午餐B", 1, "12:00", node_type="餐饮"),
            node("景区C", 1, "18:00"),
        ]}
        report = score_pacing({"days": 1}, plan, {"max_nodes_per_day": 6})
        self.assertEqual(report["value"], 1.0, report)
        self.assertEqual([p for p in report["penalties"] if "晚餐" in p], [])
        self.assertTrue(any("晚餐" in text for text in report["advisories"]), report["advisories"])
        self.assertTrue(any("加一顿晚饭" in text for text in report["advisories"]), report["advisories"])

    def test_daily_active_budget_is_720_minutes_by_default(self):
        """6 个节点 ≈720 分钟：放宽后的上限之内（旧上限 600 分钟会把正常的一天判超）。"""
        plan = {"route": [
            node(f"点{i}", 1, f"{9 + i:02d}:00", node_type="文化", cost="¥50")
            for i in range(6)
        ]}
        report = score_pacing({"days": 1}, plan, {"max_nodes_per_day": 6})
        self.assertEqual(report["value"], 1.0, report["penalties"])
        # 上限是"超过才罚"（total > budget）：正好用满 720 分钟不罚
        self.assertEqual(report["days"][0]["total_minutes"], 720.0)
        self.assertFalse(any("上限" in p for p in report["penalties"]))

    def test_pacing_uses_the_configured_active_budget_when_given(self):
        plan = {"route": [node(f"点{i}", 1, f"{9 + i:02d}:00") for i in range(6)]}
        tight = score_pacing({"days": 1, "daily_active_minutes": 200}, plan, {"max_nodes_per_day": 6})
        self.assertTrue(any("200 分钟上限" in p for p in tight["penalties"]))

    def test_space_efficiency_rewards_compact_and_flags_missing_coords(self):
        compact = {"route": [node("a", 1, "09:00", (108.94, 34.26)), node("b", 1, "10:00", (108.95, 34.26))]}
        far = {"route": [node("a", 1, "09:00", (108.94, 34.26)), node("b", 1, "10:00", (110.09, 34.49))]}
        no_coords = {"route": [dict(node("a", 1, "09:00"), lnglat=None), dict(node("b", 1, "10:00"), lnglat=None)]}
        self.assertGreater(score_space_efficiency({}, compact)["value"], score_space_efficiency({}, far)["value"])
        self.assertEqual(score_space_efficiency({}, no_coords)["value"], 0.0)
        self.assertGreaterEqual(score_space_efficiency({}, far)["long_hops"], 1)

    def test_truthfulness_requires_sources(self):
        verified = {"route": [node("a", 1, "09:00"), node("b", 1, "10:00")]}
        unsourced = {"route": [node("a", 1, "09:00", source="unverified"), node("b", 1, "10:00", source="seed_template")]}
        self.assertEqual(score_truthfulness(verified)["value"], 1.0)
        self.assertEqual(score_truthfulness(unsourced)["value"], 0.0)
        self.assertEqual(score_truthfulness(unsourced)["unsourced_count"], 2)

    def test_truthfulness_flags_claim_without_source(self):
        plan = {"route": [{"day": 1, "name": "某景点", "time": "09:00", "rating": "4.9",
                           "data_sources": {"rating": "unverified"}}]}
        report = score_truthfulness(plan)
        self.assertEqual(report["unsourced_count"], 1)
        self.assertIn("rating", report["unsourced_claims"][0]["fields"])

    def test_saying_unknown_is_not_a_lie(self):
        """写明"暂无供应商数据"的节点不是撒谎（2026-10-05 修正）。

        真实方案的形态：11 个节点里 10 个价格写着「暂无供应商数据」、评分/开放时间有高德出处。
        旧尺子一看到 `estimated: true` 就判 0 分 → 一份把"不知道"逐条写在脸上的方案，
        真实度是 0.0 —— 尺子在惩罚诚实。口径应是：**只有断言了却给不出出处**才算不可信。
        """
        plan = {"route": [
            node("天山大峡谷", 1, "08:30", cost="暂无供应商数据", open_time="暂无供应商数据", estimated=True),
            node("水磨沟风景区", 1, "15:00", cost="暂无供应商数据", open_time="暂无供应商数据", estimated=True),
        ]}
        report = score_truthfulness(plan)
        self.assertEqual(report["value"], 1.0, report)
        self.assertEqual(report["unsourced_count"], 0)
        self.assertEqual(report["disclosed_unknown"], 0, "评分字段仍有出处，不算整条未知")

    def test_truthfulness_reports_unknown_only_nodes(self):
        plan = {"route": [
            {"day": 1, "name": "纯未知点", "time": "09:00", "cost_estimate": "暂无供应商数据",
             "open_time": "暂无供应商数据", "rating": "暂无供应商数据", "estimated": True, "data_sources": {}},
        ]}
        report = score_truthfulness(plan)
        self.assertEqual(report["value"], 1.0, "什么都不声称 → 不构成无出处断言")
        self.assertEqual(report["disclosed_unknown"], 1)

    def test_numeric_price_without_verifiable_source_is_flagged(self):
        """有价格数字但出处是搜索摘要（web）→ 如实标成"没有可核实出处"（这正是要拦的）。"""
        plan = {"route": [
            {"day": 1, "name": "英吉沙国家湿地公园", "time": "09:00", "cost_estimate": "¥206",
             "estimated": True, "data_sources": {"cost_estimate": "web", "rating": "amap"}},
        ]}
        report = score_truthfulness(plan)
        self.assertEqual(report["unsourced_count"], 1)
        self.assertIn("cost_estimate", report["unsourced_claims"][0]["fields"])

    def test_estimated_price_with_verified_source_still_counts(self):
        """高德人均消费（source=amap）是"真的且有出处"的：只因为是"人均"就判不可信同样是错尺子。"""
        plan = {"route": [
            node("正规餐厅", 1, "12:30", node_type="餐饮", cost="人均餐饮 ¥52.00",
                 open_time="暂无供应商数据", estimated=True),
        ]}
        report = score_truthfulness(plan)
        self.assertEqual(report["value"], 1.0, report)
        self.assertEqual(report["estimated_prices"], 1, "仍要如实统计『有多少价格是估的』")

    def test_whitelisted_official_page_counts_as_verifiable(self):
        """用户口径（2026-10-05）：白名单里的景区/政府官网页面算可核实来源。"""
        plan = {"route": [node("某景区", 1, "09:00", source="web_page")]}
        report = score_truthfulness(plan)
        self.assertEqual(report["value"], 1.0, report)
        self.assertEqual(report["unsourced_count"], 0)

    def test_space_efficiency_scales_with_the_users_own_tolerance(self):
        """距离效率按**用户自己的口径**量：说"不介意跑远"的人不该被按市内刻度判 0 分。

        同一条 ~110km 的一天：low（预算 80km）跑不动 → 0；normal（160km）能跑但要扣；
        high（320km）在他自己的口径里很宽裕 → 满分。
        """
        plan = {"route": [
            node("a", 1, "09:00", (108.94, 34.26)),
            node("b", 1, "15:00", (109.96, 34.30)),
        ]}
        low = score_space_efficiency({"distance_tolerance": "low"}, plan)
        normal = score_space_efficiency({"distance_tolerance": "normal"}, plan)
        high = score_space_efficiency({"distance_tolerance": "high"}, plan)
        self.assertEqual(low["value"], 0.0, low)
        self.assertGreater(normal["value"], low["value"])
        self.assertGreater(high["value"], normal["value"])
        self.assertGreater(high["value"], 0.7, high)
        self.assertEqual(high["daily_budget_km"], 320.0)
        self.assertEqual(high["distance_tolerance"], "high")

    def test_space_efficiency_is_full_marks_when_inside_the_comfortable_line(self):
        """high 口径下 ~60km 的一天：舒适线（80km）以内、且不算长途跳跃 → 满分。"""
        plan = {"route": [
            node("a", 1, "09:00", (108.94, 34.26)),
            node("b", 1, "15:00", (109.53, 34.34)),
        ]}
        report = score_space_efficiency({"distance_tolerance": "high"}, plan)
        self.assertEqual(report["value"], 1.0, report)
        self.assertEqual(report["long_hops"], 0)
        self.assertEqual(report["long_hop_km"], 80.0)

    def test_space_efficiency_still_zero_when_over_the_users_budget(self):
        """"远一点也能接受"不等于"效率不重要"：超过他自己那档预算照样 0 分。"""
        plan = {"route": [
            node("a", 1, "09:00", (108.94, 34.26)),
            node("b", 1, "20:00", (113.30, 34.40)),
        ]}
        report = score_space_efficiency({"distance_tolerance": "high"}, plan)
        self.assertEqual(report["value"], 0.0, report)
        self.assertGreater(report["per_day_km"], 320.0)

    def test_diversity_penalises_duplicates(self):
        diverse = {"route": [node("博物馆", 1, "09:00", node_type="博物馆"),
                             node("小吃街", 1, "12:00", node_type="餐饮"),
                             node("公园", 2, "09:00", node_type="景区")]}
        duplicated = {"route": [node("博物馆", 1, "09:00", node_type="博物馆"),
                                node("博物馆", 1, "12:00", node_type="博物馆"),
                                node("博物馆", 2, "09:00", node_type="博物馆")]}
        self.assertGreater(score_diversity(diverse)["value"], score_diversity(duplicated)["value"])
        self.assertGreaterEqual(score_diversity(duplicated)["duplicate_names"], 1)

    def test_anchoring_details_are_kept_in_the_snapshot(self):
        """吃住离玩点多远要能看见明细（只给一个 0.8 的分数，用户只会觉得"说不清哪里怪"）。"""
        from core.plan_quality import plan_quality_snapshot

        plan = {"route": [
            node("某景区", 1, "09:00", (108.94, 34.26), "文化"),
            node("钟楼酒店", 1, "20:00", (110.20, 34.30), "住宿", "¥300"),
        ]}
        snapshot = plan_quality_snapshot({"days": 1}, plan)
        anchoring = snapshot["quality"]["anchoring"]
        self.assertIn("far_nodes", anchoring)
        self.assertEqual([item["name"] for item in anchoring["far_nodes"]], ["钟楼酒店"])
        self.assertEqual(anchoring["threshold_km"], 15.0)

    def test_lodging_options_get_a_commute_distance(self):
        """"有实价的住宿可选"要标出离方案里的玩点多远（价格可比、距离也要可比）。"""
        from core.plan_quality import annotate_lodging_options

        plan = {"route": [node("某景区", 1, "09:00", (108.94, 34.26), "文化")]}
        options = [
            {"name": "近的酒店", "price": 300, "lnglat": [108.95, 34.26]},
            {"name": "远的酒店", "price": 240, "lnglat": [109.30, 34.26]},
            {"name": "没坐标的酒店", "price": 200},
        ]
        annotated = annotate_lodging_options(plan, options)
        self.assertLess(annotated[0]["nearest_play_km"], 5)
        self.assertFalse(annotated[0]["far_from_plays"])
        self.assertEqual(annotated[0]["nearest_play_name"], "某景区")
        self.assertGreater(annotated[1]["nearest_play_km"], 30)
        self.assertTrue(annotated[1]["far_from_plays"], "远的那家要标出来")
        self.assertIsNone(annotated[2]["nearest_play_km"], "没坐标就是判不了，不是 0")
        self.assertIsNone(annotated[2]["far_from_plays"])

    def test_lodging_option_annotation_without_play_nodes(self):
        from core.plan_quality import annotate_lodging_options

        plan = {"route": [node("某酒店", 1, "20:00", (108.95, 34.26), "住宿", "¥300")]}
        annotated = annotate_lodging_options(plan, [{"name": "甲", "lnglat": [108.95, 34.26]}])
        self.assertIsNone(annotated[0]["nearest_play_km"])
        self.assertEqual(annotate_lodging_options(plan, []), [])

    def test_anchoring_far_nodes_become_a_disclosure(self):
        """`anchoring` 掉分时方案里必须写明"是哪几个点离得远"（原来只打分、不解释）。"""
        from core.degradation import collect_degradations

        payload = {
            "quality": {
                "anchoring": {
                    "value": 0.8,
                    "threshold_km": 15.0,
                    "far_nodes": [{"day": 2, "name": "信泰达民宿", "distance_km": 42.5}],
                }
            },
            "route": [{"day": 2, "name": "信泰达民宿", "type": "住宿服务"}],
        }
        items = collect_degradations(payload).get("items", [])
        entry = next((item for item in items if item.get("source") == "anchoring"), None)
        self.assertIsNotNone(entry, items)
        self.assertIn("信泰达民宿", str(entry.get("reason")))
        self.assertIn("42.5km", str(entry.get("reason")))
        self.assertIn("直接说一句", str(entry.get("reason")))

    def test_no_far_anchor_means_no_disclosure(self):
        from core.degradation import collect_degradations

        payload = {"quality": {"anchoring": {"value": 1.0, "threshold_km": 15.0, "far_nodes": []}}}
        items = collect_degradations(payload).get("items", [])
        self.assertFalse([item for item in items if item.get("source") == "anchoring"])

    def test_missing_coordinates_are_disclosed_as_the_reason_for_a_lower_score(self):
        """`anchoring` 掉分的另一个真实原因：吃住节点没坐标、通勤距离算不出来。

        实测（2026-10-05 喀什）：`anchoring=0.8` 但 `far_nodes` 是空的 ——
        分数掉在"可核实坐标覆盖 83%"上，而方案里一个字都没说（用户只看到一个没解释的 0.8）。
        """
        from core.degradation import collect_degradations

        payload = {
            "quality": {
                "anchoring": {
                    "value": 0.8,
                    "threshold_km": 15.0,
                    "far_nodes": [],
                    "coordinate_coverage": 0.833,
                }
            },
            "invalid_coordinates": {"names": ["喀什古城步行街亚朵酒店"], "note": "…"},
        }
        items = collect_degradations(payload).get("items", [])
        entry = next((item for item in items if item.get("source") == "anchoring"), None)
        self.assertIsNotNone(entry, items)
        self.assertIn("喀什古城步行街亚朵酒店", str(entry.get("reason")))
        self.assertIn("83%", str(entry.get("reason")))
        self.assertIn("请在导航里确认", str(entry.get("reason")))

    def test_stability(self):
        self.assertIsNone(stability_score([good_plan()])["value"])
        self.assertEqual(stability_score([good_plan(), good_plan()])["value"], 1.0)
        other = {"route": [node("完全不同的点", 1, "09:00")]}
        self.assertEqual(stability_score([good_plan(), other])["value"], 0.0)


class TestEvaluatePlan(unittest.TestCase):
    def test_verdict_transitions(self):
        passed = evaluate_plan(BASE_CONTEXT, good_plan(), BASE_EXPECTATIONS)
        self.assertTrue(passed["gate"]["passed"])
        self.assertEqual(passed["verdict"], "pass")
        self.assertGreater(passed["score"], 60)

        unverified = {"route": [
            node("钟楼酒店", 1, "09:00", node_type="住宿", open_time="暂无供应商数据"),
            node("陕西历史博物馆", 1, "10:30", node_type="博物馆", open_time="暂无供应商数据"),
            node("回民街小吃", 1, "12:30", node_type="餐饮", open_time="暂无供应商数据"),
            node("城墙南门", 2, "09:30", node_type="文化", open_time="暂无供应商数据"),
            node("永兴坊面馆", 2, "12:30", node_type="餐饮", open_time="暂无供应商数据"),
        ]}
        report = evaluate_plan(BASE_CONTEXT, unverified, BASE_EXPECTATIONS)
        self.assertTrue(report["gate"]["passed"])
        self.assertEqual(report["verdict"], "pass_with_unverified")
        self.assertGreater(report["gate"]["unverifiable_count"], 0)

        bad = evaluate_plan(BASE_CONTEXT, {"route": [node("兵马俑", 1, "07:00")]}, BASE_EXPECTATIONS)
        self.assertFalse(bad["gate"]["passed"])
        self.assertEqual(bad["verdict"], "fail")

    def test_weights_are_configurable(self):
        report = evaluate_plan(BASE_CONTEXT, good_plan(), BASE_EXPECTATIONS,
                               weights={"preference_coverage": 1.0, "pacing": 0.0, "space_efficiency": 0.0,
                                        "truthfulness": 0.0, "diversity": 0.0})
        self.assertEqual(report["weights"]["preference_coverage"], 1.0)
        self.assertEqual(report["weights"]["pacing"], 0.0)

    def test_scores_are_bounded(self):
        report = evaluate_plan(BASE_CONTEXT, good_plan(), BASE_EXPECTATIONS)
        self.assertGreaterEqual(report["score"], 0.0)
        self.assertLessEqual(report["score"], 100.0)
        for key, value in report["dimensions"].items():
            self.assertGreaterEqual(value["value"], 0.0, key)
            self.assertLessEqual(value["value"], 1.0, key)


class TestGoldenCases(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.cases = self.payload["cases"]

    def test_case_count_and_unique_ids(self):
        self.assertGreaterEqual(len(self.cases), 20)
        ids = [case["id"] for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_case_is_well_formed(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertIn("request", case)
                self.assertTrue(case["request"].strip())
                context = case["context"]
                self.assertGreaterEqual(int(context["days"]), 1)
                self.assertGreater(int(context["budget"]), 0)
                self.assertIn("title", case)
                expectations = case["expectations"]
                self.assertLessEqual(expectations["min_nodes_per_day"], expectations["max_nodes_per_day"])
                self.assertIsInstance(expectations["must_have"], list)
                self.assertIsInstance(case.get("notes", ""), str)

    def test_case_expectations_are_soft_by_default(self):
        """must_have 必须少而稳（<=2），避免把主观偏好写成硬门禁。"""
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertLessEqual(len(case["expectations"]["must_have"]), 2)

    def test_scorer_runs_on_every_case_without_crashing(self):
        """用空方案跑一遍，确保评分器对所有用例都不炸（缺数据只会记未核实/失败）。"""
        for case in self.cases:
            with self.subTest(case=case["id"]):
                report = evaluate_plan(case["context"], {"route": []},
                                       expectations=case["expectations"], signals=case.get("signals"))
                self.assertIn("score", report)
                self.assertFalse(report["gate"]["passed"])  # 空方案必然不过门禁


class TestQualitySnapshot(unittest.TestCase):
    """`plan_quality_snapshot` 是 agent.py 薄接线的入口，必须稳定、不改动传入 plan。"""

    def _context(self):
        return {"days": 2, "budget": 2000, "preferences": {"pace": "relaxed", "interest": ["博物馆", "美食"]}}

    def test_snapshot_shape(self):
        snapshot = plan_quality_snapshot(self._context(), good_plan())
        for key in ("quality", "budget", "skeleton", "fallback"):
            self.assertIn(key, snapshot)
        self.assertTrue(snapshot["quality"]["gate_passed"])
        self.assertEqual(snapshot["quality"]["verdict"], "pass")
        self.assertEqual(snapshot["skeleton"]["lodging_nights"], 1)
        self.assertIsNone(snapshot["fallback"])

    def test_snapshot_gives_fallback_when_over_budget(self):
        plan = {"route": [
            node("钟楼酒店", 1, "09:00", node_type="住宿", cost="¥1800", open_time="00:00-23:59"),
            node("博物馆", 1, "10:30", node_type="博物馆", cost="¥400"),
            node("回民街小吃", 1, "12:30", node_type="餐饮", cost="¥200"),
            node("城墙南门", 2, "09:30", node_type="文化", cost="¥200"),
            node("面馆", 2, "12:30", node_type="餐饮", cost="¥200"),
        ]}
        snapshot = plan_quality_snapshot(self._context(), plan)
        self.assertEqual(snapshot["budget"]["status"], "over")
        self.assertIsNotNone(snapshot["fallback"])
        self.assertIn("disclosure", snapshot["fallback"])
        self.assertFalse(snapshot["quality"]["gate_passed"])  # 预算硬门禁不过

    def test_snapshot_no_fallback_when_ok(self):
        snapshot = plan_quality_snapshot(self._context(), good_plan())
        self.assertIsNone(snapshot["fallback"])

    def test_snapshot_does_not_mutate_plan(self):
        plan = good_plan()
        before = json.dumps(plan, ensure_ascii=False, sort_keys=True)
        plan_quality_snapshot(self._context(), plan)
        self.assertEqual(before, json.dumps(plan, ensure_ascii=False, sort_keys=True))

    def test_snapshot_marks_unverified_budget(self):
        plan = {"route": [
            node("钟楼酒店", 1, "09:00", node_type="住宿", cost="暂无供应商数据", open_time="00:00-23:59"),
            node("博物馆", 1, "10:30", node_type="博物馆", cost="暂无供应商数据"),
            node("回民街小吃", 1, "12:30", node_type="餐饮", cost="暂无供应商数据"),
            node("城墙南门", 2, "09:30", node_type="文化", cost="暂无供应商数据"),
            node("面馆", 2, "12:30", node_type="餐饮", cost="暂无供应商数据"),
        ]}
        snapshot = plan_quality_snapshot(self._context(), plan)
        self.assertEqual(snapshot["budget"]["status"], "unverifiable")
        self.assertEqual(snapshot["budget"]["confidence"], "low")
        self.assertIsNone(snapshot["fallback"])  # 未核实不该触发"降级"，而是提示补数据


class TestCompoundIntentAndAnchoring(unittest.TestCase):
    """用户场景：'想去看有山有水的地方' + '中间的吃住怎么安排'。"""

    def test_mountain_water_requires_both(self):
        context = {"preferences": {"interest": ["有山有水"]}}
        only_mountain = {"route": [
            node("华山", 1, "09:00", node_type="山景"),
            node("酒店", 1, "20:00", node_type="住宿", open_time="00:00-23:59"),
        ]}
        with_water = {"route": [
            node("华山", 1, "09:00", node_type="山景"),
            node("洱海", 1, "15:00", node_type="湖泊"),
            node("酒店", 1, "20:00", node_type="住宿", open_time="00:00-23:59"),
        ]}
        report_only_land = score_preference_coverage(context, only_mountain)
        report_both = score_preference_coverage(context, with_water)
        self.assertIn("mountain_water", report_only_land["missing"])
        self.assertIn("mountain_water", report_both["addressed"])
        self.assertGreater(report_both["value"], report_only_land["value"])

    def test_explicit_phrase_triggers_compound(self):
        report = score_preference_coverage({"request": "我想去有山有水的地方"}, good_plan())
        self.assertIn("mountain_water", report["compound_requests"])

    def test_anchoring_rewards_food_and_hotel_near_play(self):
        near = {"route": [
            node("雪山景区", 1, "09:00", (100.10, 25.10), node_type="山景"),
            node("山下农家菜", 1, "12:30", (100.11, 25.11), node_type="餐饮"),
            node("山脚民宿", 1, "20:00", (100.12, 25.12), node_type="住宿", open_time="00:00-23:59"),
        ]}
        far = {"route": [
            node("雪山景区", 1, "09:00", (100.10, 25.10), node_type="山景"),
            node("城里餐厅", 1, "12:30", (102.00, 27.00), node_type="餐饮"),
            node("机场酒店", 1, "20:00", (102.20, 27.20), node_type="住宿", open_time="00:00-23:59"),
        ]}
        near_report = score_anchoring({}, near)
        far_report = score_anchoring({}, far)
        self.assertEqual(near_report["value"], 1.0)
        self.assertLess(far_report["value"], 1.0)
        self.assertGreaterEqual(len(far_report["far_nodes"]), 2)

    def test_anchoring_does_not_reward_missing_coords(self):
        no_coords = {"route": [
            {"day": 1, "name": "雪山景区", "type": "山景", "time": "09:00"},
            {"day": 1, "name": "山下农家菜", "type": "餐饮", "time": "12:30"},
        ]}
        self.assertEqual(score_anchoring({}, no_coords)["value"], 0.0)

    def test_a_scenic_node_is_not_mistaken_for_a_meal_by_its_address(self):
        """**地址里的字不能把风景名胜判成饭点**（2026-10-08 实测修）。

        真实数据：库尔勒「博斯腾湖风景名胜区」的 desc 是
        「高德资料：风景名胜｜评分 4.6｜巴音郭楞焉耆盆地东南**面**博湖县境内」，
        food 关键词里的单字「面」命中"东南面" → 它被当成饭点，
        尺子接着按"饭点离玩点 45.2km"扣分（anchoring 白掉 0.25）。
        实测 562 个玩点节点里有 24 个被这样误判。
        """
        plan = {"route": [
            {
                "day": 1,
                "name": "博斯腾湖风景名胜区",
                "type": "风景名胜",
                "tags": ["风景"],
                "desc": "高德资料：风景名胜｜评分 4.6｜巴音郭楞焉耆盆地东南面博湖县境内",
                "lnglat": [86.70, 41.90],
                "time": "Day 1 | 10:00 游览",
            },
            {
                "day": 1,
                "name": "莲花湖宾馆",
                "type": "住宿服务",
                "is_hotel": True,
                "lnglat": [86.71, 41.91],
                "time": "Day 1 | 20:00 入住",
            },
        ]}
        report = score_anchoring({}, plan)
        far_names = [item["name"] for item in report["far_nodes"]]
        self.assertNotIn("博斯腾湖风景名胜区", far_names, "玩点不该被当成'饭点太远'")
        self.assertEqual(report["checked"], 1, "只有宾馆算吃住侧节点")

    def test_a_real_meal_with_a_single_char_keyword_is_still_an_anchor(self):
        """反过来：真正的饭点（类型/标签明确）不能被漏掉。"""
        plan = {"route": [
            {"day": 1, "name": "雪山景区", "type": "山景", "lnglat": [100.10, 25.10]},
            {"day": 1, "name": "兰州牛肉面", "type": "餐饮服务", "lnglat": [102.00, 27.00]},
        ]}
        report = score_anchoring({}, plan)
        self.assertEqual(report["checked"], 1)
        self.assertEqual([item["name"] for item in report["far_nodes"]], ["兰州牛肉面"])


class TestHorizon(unittest.TestCase):
    """用户场景：一个月（30 天）的行程。"""

    def test_segments_split_long_trip(self):
        segments = segment_days(30)
        self.assertEqual(len(segments), 5)
        self.assertEqual(segments[0], {"start": 1, "end": 7, "days": 7})
        self.assertEqual(segments[-1], {"start": 29, "end": 30, "days": 2})

    def test_short_trip_has_single_segment(self):
        self.assertEqual(segment_days(3), [{"start": 1, "end": 3, "days": 3}])

    def test_advisories_for_long_trip(self):
        advisories = horizon_advisories({"days": 30})
        self.assertTrue(any("分段" in text for text in advisories))
        self.assertTrue(any("休整" in text for text in advisories))
        self.assertEqual(horizon_advisories({"days": 5}), [])

    def test_skeleton_supports_thirty_days(self):
        skeleton = build_skeleton({"days": 30, "preferences": {"pace": "normal"}})
        self.assertEqual(len(skeleton["days"]), 30)
        self.assertEqual(skeleton["segments"][0]["days"], 7)
        self.assertTrue(skeleton["advisories"])

    def test_snapshot_exposes_horizon(self):
        plan = {"route": [
            {"day": 1, "name": "酒店", "type": "住宿", "time": "09:00", "cost_estimate": "¥100",
             "data_sources": {"cost_estimate": "amap"}},
            {"day": 1, "name": "博物馆", "type": "博物馆", "time": "10:00", "cost_estimate": "¥0",
             "data_sources": {"cost_estimate": "amap"}},
            {"day": 1, "name": "面馆", "type": "餐饮", "time": "12:00", "cost_estimate": "¥30",
             "data_sources": {"cost_estimate": "amap"}},
        ]}
        snapshot = plan_quality_snapshot({"days": 30, "budget": 20000}, plan)
        self.assertEqual(snapshot["horizon"]["days"], 30)
        self.assertEqual(len(snapshot["horizon"]["segments"]), 5)
        self.assertTrue(snapshot["horizon"]["advisories"])


class TestCityPreferenceRegression(unittest.TestCase):
    """城市 × 偏好回归矩阵：乌鲁木齐 / 成都 / 三亚 × 普通游 / 深度探索 / 美食偏好。

    这三座城市的气候、可达半径和 POI 结构差异明显（西北内陆 / 西南平原 / 海岛），
    三种偏好又分别对应不同的密度与节点构成。历史上出现过"三种偏好套用同一套排法"
    的退化，所以这里把口径钉死在金标用例上，并由评分器验证偏好真的改变了节点构成。
    """

    MATRIX = (
        # (case_id, city, mode)   mode ∈ {"normal", "intense", "food"}
        ("urumqi-normal-3d", "乌鲁木齐", "normal"),
        ("urumqi-deep-4d", "乌鲁木齐", "intense"),
        ("urumqi-food-3d", "乌鲁木齐", "food"),
        ("chengdu-normal-3d", "成都", "normal"),
        ("chengdu-deep-4d", "成都", "intense"),
        ("chengdu-food-3d", "成都", "food"),
        ("sanya-normal-4d", "三亚", "normal"),
        ("sanya-deep-3d", "三亚", "intense"),
        ("sanya-food-3d", "三亚", "food"),
    )

    @classmethod
    def setUpClass(cls):
        payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cls.by_id = {case["id"]: case for case in payload["cases"]}

    def _case(self, case_id):
        self.assertIn(case_id, self.by_id, f"金标集缺少城市×偏好用例 {case_id}")
        return self.by_id[case_id]

    def test_matrix_cases_exist_and_are_well_formed(self):
        for case_id, city, _mode in self.MATRIX:
            with self.subTest(case=case_id):
                case = self._case(case_id)
                context = case["context"]
                self.assertEqual(context["city"], city)
                self.assertGreaterEqual(int(context["days"]), 2)
                self.assertGreater(int(context["budget"]), 0)
                self.assertIn("preferences", context)
                expectations = case["expectations"]
                self.assertLessEqual(expectations["min_nodes_per_day"], expectations["max_nodes_per_day"])
                self.assertLessEqual(len(expectations["must_have"]), 2)
                self.assertTrue(case["request"].strip())

    def test_mode_is_encoded_in_preferences_and_signals(self):
        for case_id, _city, mode in self.MATRIX:
            with self.subTest(case=case_id):
                case = self._case(case_id)
                preferences = case["context"]["preferences"]
                signals = case.get("signals") or {}
                if mode == "normal":
                    self.assertEqual(preferences.get("pace"), "normal")
                elif mode == "intense":
                    self.assertEqual(preferences.get("pace"), "intense")
                else:  # food
                    self.assertIn("美食", preferences.get("interest") or [])
                    self.assertIs(signals.get("wants_food"), True)

    def test_density_expectations_differ_by_mode(self):
        """普通游/美食偏好必须比深度探索稀疏——这是最容易退化成同一套排法的地方。"""
        for case_id, _city, mode in self.MATRIX:
            with self.subTest(case=case_id):
                expectations = self._case(case_id)["expectations"]
                lo = int(expectations["min_nodes_per_day"])
                hi = int(expectations["max_nodes_per_day"])
                if mode == "intense":
                    self.assertGreaterEqual(lo, 3)
                    self.assertGreaterEqual(hi, 5)
                else:
                    self.assertLessEqual(hi, 4)
                    self.assertGreaterEqual(lo, 2)

    def test_food_preference_must_land_on_dining_nodes(self):
        """美食偏好：行程里有餐饮节点才算满足，纯景点方案必须被判缺失。"""
        dining = {"route": [node("第一市场海鲜", 1, "12:30", node_type="餐饮")]}
        sights_only = {"route": [node("天涯海角", 1, "09:00", node_type="景区")]}
        for case_id, _city, mode in self.MATRIX:
            if mode != "food":
                continue
            with self.subTest(case=case_id):
                context = self._case(case_id)["context"]
                signals = self._case(case_id).get("signals")
                hit = score_preference_coverage(context, dining, signals)
                miss = score_preference_coverage(context, sights_only, signals)
                self.assertIn("food", hit["requested"])
                self.assertIn("food", hit["addressed"])
                self.assertIn("food", miss["missing"])
                self.assertGreater(hit["value"], miss["value"])

    def test_intense_preference_needs_culture_or_outdoor(self):
        """深度探索：博物馆/公园这类节点必须被识别为已覆盖，空方案必须计入缺失。"""
        deep_plan = {"route": [
            node("新疆维吾尔自治区博物馆", 1, "09:00", node_type="博物馆"),
            node("红山公园", 1, "15:00", node_type="公园"),
            node("天山步道", 1, "16:30", node_type="徒步"),
        ]}
        for case_id, _city, mode in self.MATRIX:
            if mode != "intense":
                continue
            with self.subTest(case=case_id):
                case = self._case(case_id)
                report = score_preference_coverage(case["context"], deep_plan, case.get("signals"))
                self.assertTrue(report["requested"], "深度探索用例必须能映射到偏好标签")
                for label in ("cultural", "scenic", "outdoor"):
                    self.assertIn(label, report["requested"])
                    self.assertIn(label, report["addressed"])
                empty = score_preference_coverage(case["context"], {"route": []}, case.get("signals"))
                self.assertTrue(empty["missing"], "空方案必须把偏好全部计入缺失")

    def test_mode_pairs_are_distinguishable(self):
        """同一城市内，三种偏好的期望节点密度不得完全一致（防止偏好被忽略）。"""
        for city in ("乌鲁木齐", "成都", "三亚"):
            shapes = {}
            for case_id, case_city, mode in self.MATRIX:
                if case_city != city:
                    continue
                expectations = self._case(case_id)["expectations"]
                shapes[mode] = (expectations["min_nodes_per_day"], expectations["max_nodes_per_day"])
            self.assertEqual(len(shapes), 3, city)
            self.assertNotEqual(shapes["normal"], shapes["intense"], city)
            self.assertNotEqual(shapes["food"], shapes["intense"], city)

    def test_scoring_is_deterministic_per_case(self):
        plan = {"route": [
            node("酒店", 1, "09:00", node_type="住宿", open_time="00:00-23:59"),
            node("博物馆", 1, "10:30", node_type="博物馆"),
            node("本地餐馆", 1, "12:30", node_type="餐饮"),
        ]}
        for case_id, _city, _mode in self.MATRIX:
            with self.subTest(case=case_id):
                case = self._case(case_id)
                first = evaluate_plan(case["context"], plan, case["expectations"], signals=case.get("signals"))
                second = evaluate_plan(case["context"], plan, case["expectations"], signals=case.get("signals"))
                self.assertEqual(first["score"], second["score"])
                self.assertEqual(first["verdict"], second["verdict"])
                self.assertEqual(
                    first["dimensions"]["preference_coverage"],
                    second["dimensions"]["preference_coverage"],
                )


if __name__ == "__main__":
    unittest.main()
