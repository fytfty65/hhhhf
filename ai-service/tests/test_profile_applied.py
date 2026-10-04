"""反馈学习闭环可观测性测试：`profile_applied` 必须如实区分四种来源。

对应真实缺口（2026-09-19 排查）：画像加成只在标准输出里，脚本/界面断言不到，
于是"反馈学习有没有改变下一次规划"变成只能人肉看日志的问题；更糟的是
`control` 组与"treatment 但还没反馈"都表现为空加成，两者会被混为一谈。
这里把口径钉死：没有加成就不许说成个性化生效。
"""

import unittest

from core.travel_utils import build_profile_applied


def _profile(**overrides):
    base = {
        "ab_variant": "treatment",
        "feedback_count": 4,
        "nature_ratio": 0.6,
        "culture_ratio": 0.2,
        "food_ratio": 0.2,
        "liked_tags": ["湖海水系", "山岳"],
        "disliked_tags": ["博物馆"],
        "budget_tendency": "mid",
    }
    base.update(overrides)
    return base


class ProfileAppliedTests(unittest.TestCase):
    def test_no_profile_is_not_reported_as_personalized(self):
        applied = build_profile_applied({})
        self.assertEqual(applied["source"], "no_profile")
        self.assertEqual(applied["category_boost"], {})
        self.assertEqual(applied["feedback_count"], 0)
        self.assertFalse(applied["tilted"])

    def test_control_group_is_labelled_even_though_it_has_feedback(self):
        applied = build_profile_applied(_profile(ab_variant="control"))
        self.assertEqual(applied["source"], "control_group")
        self.assertEqual(applied["feedback_count"], 4)
        self.assertEqual(applied["category_boost"], {})
        self.assertEqual(applied["budget_gamma_scale"], 1.0)
        self.assertFalse(applied["tilted"])

    def test_treatment_without_feedback_is_cold_start_not_learned(self):
        applied = build_profile_applied(_profile(feedback_count=0))
        self.assertEqual(applied["source"], "cold_start")
        self.assertEqual(applied["category_boost"], {})

    def test_treatment_with_feedback_reports_learned_profile_and_tilts_scenic(self):
        applied = build_profile_applied(_profile())
        self.assertEqual(applied["source"], "learned_profile")
        self.assertTrue(applied["tilted"])
        self.assertGreater(applied["category_boost"].get("scenic", 0), 0)
        self.assertLess(applied["category_boost"].get("cultural", 0), 0)
        self.assertEqual(applied["fine_tag_boost"].get("湖海水系"), 0.6)
        self.assertEqual(applied["fine_tag_boost"].get("博物馆"), -0.6)

    def test_balanced_ratios_do_not_fake_a_boost(self):
        """三大比例都接近 1/3 时不应产出噪声加成 —— 否则"学习生效"会被误报。

        同时：source 仍是 learned_profile（确实读了反馈），但 tilted 必须为 False，
        这样调用方不会把"读了画像"当成"改了结果"。
        """
        applied = build_profile_applied(
            _profile(nature_ratio=0.34, culture_ratio=0.33, food_ratio=0.33, liked_tags=[], disliked_tags=[])
        )
        self.assertEqual(applied["category_boost"], {})
        self.assertEqual(applied["source"], "learned_profile")
        self.assertFalse(applied["tilted"])

    def test_budget_tendency_low_scales_gamma_and_marks_learned(self):
        applied = build_profile_applied(
            _profile(liked_tags=[], disliked_tags=[], nature_ratio=0.34, culture_ratio=0.33, food_ratio=0.33, budget_tendency="low")
        )
        self.assertEqual(applied["budget_gamma_scale"], 2.0)
        self.assertEqual(applied["source"], "learned_profile")
        self.assertTrue(applied["tilted"])

    def test_ratios_are_echoed_for_auditing(self):
        applied = build_profile_applied(_profile())
        self.assertEqual(applied["nature_ratio"], 0.6)
        self.assertEqual(applied["culture_ratio"], 0.2)
        self.assertEqual(applied["food_ratio"], 0.2)


if __name__ == "__main__":
    unittest.main()
