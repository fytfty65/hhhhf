# -*- coding: utf-8 -*-
"""OmniRoute 创新功能纯函数单元测试（天气感知调度 / 拥挤度评估 / 个性化画像注入 / 预算换算）。"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 预算换算等纯函数位于 Go 服务侧；此处用等价口径验证 Python 侧纯函数算法
from core.travel_utils import analyze_weather_for_planning, estimate_crowdedness, build_personalization_hint, build_profile_category_boost, build_profile_budget_gamma, build_profile_fine_tag_boost, classify_fine_tags, normalize_mode, normalize_role


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

    def test_role_always_applied_even_in_control_group(self):
        # 手选角色显式声明，即使在 A/B 对照组且零反馈（冷启动）也必须生效
        hint = build_personalization_hint(
            {"ab_variant": "control", "feedback_count": 0},
            {}, [], role="寻味探索"
        )
        self.assertIn("寻味探索", hint)
        self.assertNotIn("通用推荐策略", hint)

    def test_role_fused_with_learned_profile(self):
        # 手选角色与算法画像融合为同一条提示词，二者并存
        hint = build_personalization_hint(
            {"ab_variant": "treatment", "feedback_count": 3, "prompt_hint": "偏好美食"},
            {}, [], role="深度探索"
        )
        self.assertIn("深度探索", hint)
        self.assertIn("偏好美食", hint)

    def test_member_role_applied_from_room_members(self):
        # 同行成员的手选角色也应被提取并生效
        hint = build_personalization_hint(
            {"ab_variant": "control", "feedback_count": 0},
            {}, [{"id": "u1", "role": "视觉体验"}]
        )
        self.assertIn("视觉体验", hint)

    def test_unknown_role_falls_back_to_generic(self):
        hint = build_personalization_hint(
            {"ab_variant": "control", "feedback_count": 0},
            {}, [], role="随机角色"
        )
        self.assertIn("通用推荐策略", hint)


class TestProfileCategoryBoost(unittest.TestCase):
    def test_control_variant_no_boost(self):
        # A/B 对照组不施加画像权重，返回空（均衡基线）
        boost = build_profile_category_boost(
            {"ab_variant": "control", "feedback_count": 5, "food_ratio": 0.9}
        )
        self.assertEqual(boost, {})

    def test_treatment_without_feedback_no_boost(self):
        # 冷启动（零反馈）treatment 组也不施加硬策略
        boost = build_profile_category_boost(
            {"ab_variant": "treatment", "feedback_count": 0, "food_ratio": 0.9}
        )
        self.assertEqual(boost, {})

    def test_treatment_food_preference_boosts_food(self):
        # 美食偏好占比高 -> food 获得正加成，scenic/cultural 负加成
        boost = build_profile_category_boost(
            {"ab_variant": "treatment", "feedback_count": 3,
             "nature_ratio": 0.1, "culture_ratio": 0.1, "food_ratio": 0.8}
        )
        self.assertIn("food", boost)
        self.assertGreater(boost["food"], 0)
        self.assertLess(boost.get("scenic", 0.0), 0)
        self.assertLess(boost.get("cultural", 0.0), 0)

    def test_balanced_profile_returns_empty(self):
        # 均衡画像（无显著偏差）应被噪声过滤为空，等价于基线
        boost = build_profile_category_boost(
            {"ab_variant": "treatment", "feedback_count": 3,
             "nature_ratio": 0.33, "culture_ratio": 0.33, "food_ratio": 0.34}
        )
        self.assertEqual(boost, {})


class TestProfileBudgetGamma(unittest.TestCase):
    def test_control_variant_no_adjust(self):
        # A/B 对照组不调整消费权重，返回基线 1.0
        self.assertEqual(build_profile_budget_gamma(
            {"ab_variant": "control", "feedback_count": 5, "budget_tendency": "low"}
        ), 1.0)

    def test_treatment_without_feedback_no_adjust(self):
        # 冷启动（零反馈）treatment 组也不调整消费权重
        self.assertEqual(build_profile_budget_gamma(
            {"ab_variant": "treatment", "feedback_count": 0, "budget_tendency": "low"}
        ), 1.0)

    def test_low_budget_amplifies_cost_penalty(self):
        # 精打细算画像 -> gamma 放大，加大高消费惩罚
        self.assertGreater(build_profile_budget_gamma(
            {"ab_variant": "treatment", "feedback_count": 3, "budget_tendency": "low"}
        ), 1.0)

    def test_high_budget_weakens_cost_penalty(self):
        # 品质高预算画像 -> gamma 缩小，弱化高消费惩罚
        self.assertLess(build_profile_budget_gamma(
            {"ab_variant": "treatment", "feedback_count": 3, "budget_tendency": "high"}
        ), 1.0)

    def test_mid_budget_returns_baseline(self):
        # 中等预算画像 -> 基线 1.0
        self.assertEqual(build_profile_budget_gamma(
            {"ab_variant": "treatment", "feedback_count": 3, "budget_tendency": "mid"}
        ), 1.0)


class TestFineTags(unittest.TestCase):
    def test_classify_single_tag(self):
        self.assertIn("山岳", classify_fine_tags("黄山风景区"))
        self.assertIn("博物馆", classify_fine_tags("故宫博物院"))
        self.assertIn("火锅串串", classify_fine_tags("成都火锅店"))

    def test_classify_multi_tag_sorted(self):
        # 命中多个细标签，应按字典序排序且去重
        tags = classify_fine_tags("苏州园林博物馆花园")
        self.assertEqual(tags, sorted(tags))
        self.assertEqual(tags, list(dict.fromkeys(tags)))

    def test_classify_empty(self):
        self.assertEqual(classify_fine_tags(""), [])
        self.assertEqual(classify_fine_tags("欢乐颂购物中心"), [])
        self.assertEqual(classify_fine_tags(None), [])

    def test_control_variant_no_boost(self):
        self.assertEqual(build_profile_fine_tag_boost(
            {"ab_variant": "control", "feedback_count": 5, "liked_tags": ["山岳"]}
        ), {})

    def test_treatment_without_feedback_no_boost(self):
        self.assertEqual(build_profile_fine_tag_boost(
            {"ab_variant": "treatment", "feedback_count": 0, "liked_tags": ["山岳"]}
        ), {})

    def test_treatment_liked_and_disliked_tags(self):
        boost = build_profile_fine_tag_boost({
            "ab_variant": "treatment", "feedback_count": 3,
            "liked_tags": ["山岳", "博物馆"], "disliked_tags": ["烧烤"],
        })
        self.assertAlmostEqual(boost["山岳"], 0.6)
        self.assertAlmostEqual(boost["博物馆"], 0.6)
        self.assertAlmostEqual(boost["烧烤"], -0.6)

    def test_replacement_keywords_still_match(self):
        # 收紧后的复合词仍能识别真实水系；单字“观”已由“道观”等替代
        self.assertIn("湖海水系", classify_fine_tags("三亚海滩"))
        self.assertIn("宗教古迹", classify_fine_tags("白云道观"))

    def test_whitelist_recall(self):
        # 收紧后遗漏的高频真实地名词经白名单精确召回（语义正确归类）
        for name in ("洱海", "黄河", "珠江", "海南岛"):
            self.assertIn("湖海水系", classify_fine_tags(name))
        self.assertIn("园林花木", classify_fine_tags("蜀南竹海"))
        self.assertIn("宗教古迹", classify_fine_tags("白云观"))

    def test_no_cross_category_false_positive(self):
        # 跨类别词不应再误命中：火锅品牌/观景台/含“岛”的城市名
        self.assertNotIn("湖海水系", classify_fine_tags("海底捞"))
        self.assertNotIn("宗教古迹", classify_fine_tags("观景台"))
        self.assertNotIn("湖海水系", classify_fine_tags("青岛啤酒博物馆"))


class TestEnumValidation(unittest.TestCase):
    def test_valid_modes(self):
        for m in ("solo", "coop", "pvp"):
            self.assertEqual(normalize_mode(m), m)
        # 大小写/空白均归一化
        self.assertEqual(normalize_mode("  SOLO "), "solo")

    def test_invalid_mode_raises(self):
        for m in ("online", "hack", "", None):
            with self.assertRaises(ValueError):
                normalize_mode(m)

    def test_valid_roles(self):
        for r in ("寻味探索", "视觉体验", "休闲漫步", "深度探索"):
            self.assertEqual(normalize_role(r), r)

    def test_invalid_role_raises(self):
        for r in ("随机角色", "", None):
            with self.assertRaises(ValueError):
                normalize_role(r)


def _go_profile_payload(**overrides):
    """构造与 Go 侧 ProfilePayload 返回结构一致的完整画像 dict（跨语言数据契约）。

    字段名严格对齐 gateway/internal/service/preference_store.go 的 ProfilePayload，
    用于端到端验证：Go 侧画像修复（高预算判定 + 消费回流）的产物能被 Python 侧正确消费。
    """
    base = {
        "ab_variant": "treatment",
        "feedback_count": 1,
        "budget_tendency": "mid",
        "nature_ratio": 0.33,
        "culture_ratio": 0.33,
        "food_ratio": 0.34,
        "liked_tags": [],
        "disliked_tags": [],
        "cuisine_prefs": [],
        "prompt_hint": "",
    }
    base.update(overrides)
    return base


class TestEndToEndProfilePipeline(unittest.TestCase):
    """端到端契约测试：Go 侧画像修复产物 -> Python 侧三大硬策略函数协同产出。

    覆盖两处修复在 Python 消费端的贯通效果：
    1) 预算死代码修复：高预算（high）画像应使消费惩罚弱化（gamma 0.3）并注入「品质高预算」文案；
    2) 消费回流画像修复：餐饮消费派生的美食画像应驱动品类加成（food>0）+ 细标签加成（地方菜 0.6）。
    """

    def test_high_budget_profile_flows_to_gamma_and_prompt(self):
        profile = _go_profile_payload(
            budget_tendency="high",
            liked_tags=["风景", "美食", "人文"],
            cuisine_prefs=["地道特色"],
            prompt_hint="该用户历史偏好画像：偏好维度（风景、美食、人文）；消费习惯为品质高预算；喜欢 风景、美食、人文；不希望出现 无。",
        )
        # 高预算画像 -> 消费惩罚弱化（gamma 0.3），不再是死代码前只能落在 mid 的 1.0
        self.assertAlmostEqual(build_profile_budget_gamma(profile), 0.3)
        # 高预算文案经个性化提示词注入链路贯通
        hint = build_personalization_hint(profile, {}, [])
        self.assertIn("品质高预算", hint)
        self.assertIn("treatment", hint)

    def test_food_expense_profile_flows_to_category_and_fine_tag(self):
        # 餐饮消费记录回流 -> Go 侧产出 food_ratio=1.0 + liked_tags 含「地方菜」
        profile = _go_profile_payload(
            feedback_count=3,
            nature_ratio=0.0, culture_ratio=0.0, food_ratio=1.0,
            liked_tags=["地方菜"],
            cuisine_prefs=["地方菜"],
        )
        boost = build_profile_category_boost(profile)
        self.assertGreater(boost["food"], 0)
        self.assertLess(boost.get("scenic", 0.0), 0)
        self.assertLess(boost.get("cultural", 0.0), 0)
        fine = build_profile_fine_tag_boost(profile)
        self.assertAlmostEqual(fine["地方菜"], 0.6)

    def test_mixed_profile_end_to_end(self):
        # 综合：餐饮消费（美食偏好）+ 五星反馈（高预算）三者协同，互不冲突
        profile = _go_profile_payload(
            feedback_count=4,
            budget_tendency="high",
            nature_ratio=0.0, culture_ratio=0.0, food_ratio=1.0,
            liked_tags=["地方菜"],
            cuisine_prefs=["地方菜"],
        )
        self.assertAlmostEqual(build_profile_budget_gamma(profile), 0.3)
        self.assertGreater(build_profile_category_boost(profile)["food"], 0)
        self.assertAlmostEqual(build_profile_fine_tag_boost(profile)["地方菜"], 0.6)

    def test_control_or_cold_start_stays_neutral(self):
        # A/B 对照组 / 冷启动（零反馈）：三大硬策略均回落基线，不施加画像差异化
        for profile in (_go_profile_payload(ab_variant="control", feedback_count=5),
                        _go_profile_payload(ab_variant="treatment", feedback_count=0)):
            self.assertEqual(build_profile_budget_gamma(profile), 1.0)
            self.assertEqual(build_profile_category_boost(profile), {})
            self.assertEqual(build_profile_fine_tag_boost(profile), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)