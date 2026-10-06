"""节点级负反馈 → 规划信号的测试（2026-10-05 用户口径）。

用户原话：「如果用户点击不喜欢，或者二次增量不想去这么远的地方，就按照用户的意图来」。
所以这里钉三件事：
1. 点了"不喜欢"的具体地点**真的进排除名单**（不是只写进画像标签）；
2. 原因说"太远"→ 距离容忍度压成 low（与亲口说"不想跑远"同一个开关）；
3. 城市名/标题/点评词**绝不能**被当成地点排除 —— 否则会误伤整座城市。
"""

import os
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")

from core.feedback_signals import (  # noqa: E402
    classify_feedback_reason,
    looks_like_place_name,
    merge_feedback_into_signals,
    summarize_feedback,
)


def _fb(target, score=-1, reason=""):
    return {"Target": target, "Score": score, "Reason": reason}


class ClassifyReasonTests(unittest.TestCase):
    def test_distance_reasons(self):
        for reason in ("太远了", "路上太折腾", "不想跑那么远", "换近一点的", "太偏了"):
            self.assertEqual(classify_feedback_reason(reason), "distance", reason)

    def test_price_and_pace_reasons(self):
        self.assertEqual(classify_feedback_reason("太贵了，不值"), "price")
        self.assertEqual(classify_feedback_reason("太赶了，来不及"), "pace")

    def test_distance_wins_over_price(self):
        """「太远了而且太贵」：贵可以换同类便宜点，远只能换近的 —— 必须按距离处理。"""
        self.assertEqual(classify_feedback_reason("太远了，还那么贵"), "distance")

    def test_blank_and_unknown(self):
        self.assertEqual(classify_feedback_reason(""), "unknown")
        self.assertEqual(classify_feedback_reason(None), "unknown")
        self.assertEqual(classify_feedback_reason("人太多了"), "unknown")


class PlaceNameGuardTests(unittest.TestCase):
    def test_plain_place_name_is_usable(self):
        self.assertTrue(looks_like_place_name("帕米尔旅游景区"))
        self.assertTrue(looks_like_place_name("香妃园", city="喀什"))

    def test_city_name_is_never_a_place_to_exclude(self):
        """历史满意度样本会拿目的地城市当 Target —— 排除它等于把整座城市清空。"""
        self.assertFalse(looks_like_place_name("库尔勒", city="库尔勒"))
        self.assertFalse(looks_like_place_name("喀什", city="喀什"))

    def test_trip_titles_and_review_words_are_not_places(self):
        for target in ("库尔勒 3 日游", "行程满意度低", "消费记录: 餐饮 120CNY", "不喜欢", "太远了"):
            self.assertFalse(looks_like_place_name(target), target)

    def test_overlong_target_is_not_a_place(self):
        self.assertFalse(looks_like_place_name("这个地方真的太远了我实在不想再去了下次别安排"))


class SummarizeFeedbackTests(unittest.TestCase):
    def test_disliked_place_enters_exclusion_list(self):
        result = summarize_feedback(
            [_fb("帕米尔旅游景区", reason="太远了"), _fb("石头城", reason="人太多")],
            city="喀什",
        )
        self.assertEqual(result["exclude_names"], ["帕米尔旅游景区", "石头城"])
        self.assertEqual(result["counts"]["excluded"], 2)
        self.assertTrue(any("帕米尔旅游景区" in note for note in result["notes"]))

    def test_distance_complaint_lowers_tolerance(self):
        result = summarize_feedback([_fb("帕米尔旅游景区", reason="太远了，来回一天")], city="喀什")
        self.assertEqual(result["distance_tolerance"], "low")
        self.assertTrue(any("就近安排" in note for note in result["notes"]))

    def test_price_complaint_sets_budget_low_without_touching_distance(self):
        result = summarize_feedback([_fb("某高档餐厅", reason="太贵了，不值")])
        self.assertEqual(result["budget_tendency"], "low")
        self.assertIsNone(result["distance_tolerance"])

    def test_unknown_reason_only_excludes_and_does_not_guess(self):
        """读不懂原因时宁可只做排除：**"没读懂"和"读成别的意思"是两回事**。"""
        result = summarize_feedback([_fb("某网红点", reason="人太多了")])
        self.assertEqual(result["exclude_names"], ["某网红点"])
        self.assertIsNone(result["distance_tolerance"])
        self.assertIsNone(result["budget_tendency"])

    def test_positive_feedback_produces_nothing(self):
        result = summarize_feedback([_fb("卡拉库里湖", score=1, reason="太美了")])
        self.assertEqual(result["exclude_names"], [])
        self.assertIsNone(result["distance_tolerance"])
        self.assertEqual(result["notes"], [])

    def test_city_level_low_satisfaction_does_not_wipe_the_city(self):
        """真实事故面：历史满意度把"库尔勒 3 日游"标成 Target=-1，不能因此清空库尔勒。"""
        result = summarize_feedback(
            [_fb("库尔勒", reason="行程满意度低"), _fb("库尔勒 3 日游", reason="行程满意度低")],
            city="库尔勒",
        )
        self.assertEqual(result["exclude_names"], [])
        self.assertEqual(result["distance_tolerance"], None)
        self.assertEqual(result["counts"]["negative_total"], 2)

    def test_duplicate_dislikes_are_deduped(self):
        result = summarize_feedback([_fb("香妃园"), _fb("香妃园"), _fb("香妃园", reason="太远")])
        self.assertEqual(result["exclude_names"], ["香妃园"])
        self.assertEqual(len([note for note in result["notes"] if "香妃园" in note]), 1, result["notes"])

    def test_place_name_containing_the_word_tourism_is_still_a_place(self):
        """「帕米尔旅游景区」实测被"旅游"黑名单误杀过一次 —— 要挡的是行程标题，不是景区名。"""
        self.assertTrue(looks_like_place_name("帕米尔旅游景区"))
        self.assertTrue(looks_like_place_name("喀纳斯旅游景区", city="布尔津"))

    def test_memory_limit_bounds_how_much_history_applies(self):
        memory = [_fb(f"地点{i}") for i in range(10)]
        result = summarize_feedback(memory, limit=3)
        self.assertEqual(result["exclude_names"], ["地点0", "地点1", "地点2"])

    def test_junk_entries_are_ignored(self):
        result = summarize_feedback([None, "x", {"Target": "某园", "Score": "abc"}, {"Target": "真·园", "Score": -1}])
        self.assertEqual(result["exclude_names"], ["真·园"])

    def test_empty_memory_is_honest(self):
        result = summarize_feedback([])
        self.assertEqual(result, {
            "exclude_names": [],
            "distance_complaints": [],
            "price_complaints": [],
            "pace_complaints": [],
            "distance_tolerance": None,
            "budget_tendency": None,
            "notes": [],
            "counts": {"negative_total": 0, "excluded": 0, "distance": 0, "price": 0},
        })
        self.assertEqual(summarize_feedback(None)["notes"], [])


class MergeFeedbackTests(unittest.TestCase):
    def test_feedback_lowers_normal_tolerance(self):
        merged = merge_feedback_into_signals({"distance_tolerance": "normal"}, {"distance_tolerance": "low"})
        self.assertEqual(merged["distance_tolerance"], "low")

    def test_user_speaking_now_wins_over_history(self):
        """用户这轮亲口说"不介意跑远"→ 不能被上次反馈的"太远"翻回去。"""
        merged = merge_feedback_into_signals({"distance_tolerance": "high"}, {"distance_tolerance": "low"})
        self.assertEqual(merged["distance_tolerance"], "high")

    def test_no_feedback_keeps_signals_untouched(self):
        signals = {"distance_tolerance": "normal", "wants_scenic": True}
        merged = merge_feedback_into_signals(signals, {"distance_tolerance": None})
        self.assertEqual(merged, signals)
        self.assertIsNot(merged, signals, "不要就地改调用方的 dict")
        self.assertEqual(merge_feedback_into_signals(None, {"distance_tolerance": "low"}).get("distance_tolerance"), "low")


if __name__ == "__main__":
    unittest.main()
