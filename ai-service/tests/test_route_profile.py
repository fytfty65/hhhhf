"""参考路线画像的回归锁（纯逻辑，不联网）。

判据来自用户需求原话：
    "允许用户上传自己喜欢的路线（比如小红书发现的帖子），智能体对路线进行学习，
     为用户进行特征画像，当用户没有提出更多需求时，就按照他喜欢的那个路线总结的特点生成新路线。"

所以这些测试盯死四件事：
1. 抽得出特征（并且每条特征都能指回原文出处）；
2. **抽不到就是 unknown**，不用默认值冒充"学到了"；
3. 合并多条参考时口径明确（计数累加、单值多数、两条都没抽到才算 unknown）；
4. **用户当轮的明确诉求优先于画像**（否则会出现"我明明说了却被画像覆盖"）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.route_profile import (  # noqa: E402
    extract_route_profile,
    merge_profiles,
    profile_to_retrieval_hints,
    reference_profile_hint,
)

XIAOHONGSHU_TEXT = (
    "乌鲁木齐周边4天，人均¥300左右，住民宿。"
    "第一天去南山牧场西白杨沟，徒步看雪山和草原，日落机位超好拍；"
    "第二天哈熊沟森林公园，晚上去夜市吃烧烤和大盘鸡；"
    "第三天十七户湿地公园，顺路逛大巴扎买点手作文创；"
    "整体节奏轻松，不赶，地铁+打车为主。"
)


class TestExtraction(unittest.TestCase):
    def test_realistic_reference_route_yields_features_with_evidence(self):
        profile = extract_route_profile(XIAOHONGSHU_TEXT, source_label="小红书-乌鲁木齐4天")
        features = profile["features"]
        self.assertGreater(features["category_mix"].get("scenic", 0), 0, "自然类应被识别")
        self.assertGreater(features["category_mix"].get("food", 0), 0, "餐饮类应被识别")
        self.assertEqual(features["pace"], "relaxed")
        self.assertEqual(features["lodging_style"], "homestay")
        self.assertEqual(features["budget_tier"], "mid")
        self.assertIn("metro", features["transport"])
        self.assertTrue(profile["evidence"], "每条特征都要有出处片段")
        self.assertIn("pace", profile["evidence"])
        self.assertGreater(profile["confidence"], 0.5, f"特征覆盖应过半：{profile['confidence']}")
        self.assertEqual(profile["source_label"], "小红书-乌鲁木齐4天")

    def test_empty_or_garbage_text_is_all_unknown_not_guessed(self):
        for text in ("", "   ", "哈哈哈哈", "12345"):
            with self.subTest(text=text):
                profile = extract_route_profile(text)
                self.assertEqual(profile["confidence"], 0.0)
                self.assertEqual(profile["features"]["pace"], "unknown")
                self.assertEqual(profile["features"]["lodging_style"], "unknown")
                self.assertIn("pace", profile["unknown"])

    def test_cultural_landscape_does_not_count_as_scenic(self):
        """教训复用：用户抱怨原话里的"人文景观"不能被算成自然偏好。"""
        profile = extract_route_profile("这条路线人文景观很多，博物馆、文庙、古城都去了")
        mix = profile["features"]["category_mix"]
        self.assertGreater(mix.get("cultural", 0), 0)
        self.assertEqual(mix.get("scenic", 0), 0, f"人文景观不该计进自然：{mix}")

    def test_nodes_per_day_infers_pace_and_says_so(self):
        profile = extract_route_profile("每天4个地方，安排得比较满")
        self.assertEqual(profile["features"]["nodes_per_day"], 4)
        self.assertEqual(profile["features"]["pace"], "balanced")
        self.assertIn("4 个点", profile["evidence"]["pace"], "由数量推断时必须说明来源")

    def test_budget_amount_infers_tier_and_says_so(self):
        profile = extract_route_profile("人均 ¥150 玩三天")
        self.assertEqual(profile["features"]["budget_amount"], 150)
        self.assertEqual(profile["features"]["budget_tier"], "economy")
        self.assertIn("人均 ¥150", profile["evidence"]["budget_tier"])


class TestMerge(unittest.TestCase):
    def test_merge_sums_categories_and_votes_single_values(self):
        first = extract_route_profile("自然景观很棒，节奏轻松，住民宿，人均¥200")
        second = extract_route_profile("自然风景和湖泊，节奏轻松，住民宿，人均¥280")
        merged = merge_profiles([first, second])
        self.assertEqual(merged["reference_count"], 2)
        self.assertEqual(merged["features"]["pace"], "relaxed")
        self.assertEqual(merged["features"]["lodging_style"], "homestay")
        self.assertGreaterEqual(merged["features"]["category_mix"]["scenic"], 3, "同类计数应累加")

    def test_unknown_only_when_no_reference_has_it(self):
        with_food = extract_route_profile("美食很多，小吃街逛不停")
        without = extract_route_profile("去了几个公园")
        merged = merge_profiles([with_food, without])
        self.assertNotIn("category_mix", merged["unknown"])

    def test_merge_without_profiles_is_empty_not_fabricated(self):
        merged = merge_profiles([])
        self.assertEqual(merged["reference_count"], 0)
        self.assertEqual(merged["confidence"], 0.0)
        self.assertEqual(merged["features"], {})


class TestHintsAndPriority(unittest.TestCase):
    def test_nature_dominant_profile_shifts_quota(self):
        profile = extract_route_profile("自然景观、湖泊、雪山、森林公园跑了四处，只去了一个博物馆")
        hints = profile_to_retrieval_hints(profile, poi_limit=40)
        self.assertTrue(hints["prefer_scenic"])
        self.assertGreater(hints["quota"]["scenic"], hints["quota"]["cultural"])
        self.assertTrue(any("自然" in keyword for keyword in hints["extra_scenic_keywords"]))
        self.assertTrue(hints["reasons"], "必须能解释每个旋钮是怎么来的")

    def test_explicit_request_always_wins_over_profile(self):
        profile = extract_route_profile("自然景观很多，节奏轻松")
        explicit = {"requirements": ["文化历史"], "wants_cultural": True}
        self.assertIsNone(
            reference_profile_hint(profile, explicit_signals=explicit),
            "用户当轮说清楚了就不能拿画像覆盖他",
        )

    def test_no_explicit_request_uses_profile_with_disclosure(self):
        profile = extract_route_profile("自然景观很多，美食也多，节奏轻松，住民宿")
        hint = reference_profile_hint(profile, explicit_signals={"requirements": []})
        self.assertIsNotNone(hint)
        self.assertIn("参考过的", hint["disclosure"], "必须如实告诉用户这次是按参考偏好来的")
        self.assertIn("想改就直接说", hint["disclosure"], "要给用户一个覆盖它的出口")

    def test_empty_profile_yields_no_hint(self):
        self.assertIsNone(reference_profile_hint(extract_route_profile("")))
        self.assertIsNone(reference_profile_hint(None))


if __name__ == "__main__":
    unittest.main()
