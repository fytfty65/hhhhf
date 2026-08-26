# -*- coding: utf-8 -*-
"""OmniRoute 创新功能纯函数单元测试（天气感知调度 / 拥挤度评估 / 个性化画像注入 / 预算换算）。"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 预算换算等纯函数位于 Go 服务侧；此处用等价口径验证 Python 侧纯函数算法
from core.travel_utils import analyze_weather_for_planning, estimate_crowdedness, build_personalization_hint


class TestWeatherAdvisor(unittest.TestCase):
    def test_rainy_prefers_indoor(self):
        adv = analyze_weather_for_planning("中雨 22°C")
        self.assertTrue(adv["prefer_indoor"])
        self.assertEqual(adv["level"], "rainy")

    def test_fine_weather(self):
        adv = analyze_weather_for_planning("晴 31°C")
        self.assertFalse(adv["prefer_indoor"])
        self.assertEqual(adv["level"], "hot")

    def test_mild_weather(self):
        adv = analyze_weather_for_planning("多云 26°C")
        self.assertEqual(adv["level"], "fine")


class TestCrowdedness(unittest.TestCase):
    def test_high_rating_congested(self):
        c = estimate_crowdedness("4.9", "3", True)
        self.assertEqual(c["level"], "high")

    def test_low_rating_spacious(self):
        c = estimate_crowdedness("4.0", "1", False)
        self.assertEqual(c["level"], "low")

    def test_output_structure(self):
        c = estimate_crowdedness("4.6", "2", False)
        for k in ("level", "percent", "label", "score"):
            self.assertIn(k, c)


class TestPersonalizationHint(unittest.TestCase):
    def test_control_variant_no_personalization(self):
        hint = build_personalization_hint(
            {"ab_variant": "control", "feedback_count": 5, "prompt_hint": "喜欢美食"},
            {}, [{"id": "u1"}]
        )
        self.assertIn("通用推荐策略", hint)

    def test_treatment_without_feedback_generic(self):
        hint = build_personalization_hint(
            {"ab_variant": "treatment", "feedback_count": 0}, {}, []
        )
        self.assertIn("通用推荐策略", hint)

    def test_treatment_with_feedback_injects(self):
        hint = build_personalization_hint(
            {"ab_variant": "treatment", "feedback_count": 3, "prompt_hint": "偏好美食"},
            {}, []
        )
        self.assertIn("偏好美食", hint)
        self.assertIn("treatment", hint)


if __name__ == "__main__":
    unittest.main(verbosity=2)