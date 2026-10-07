"""「只看自然就别排人文」的测试（2026-10-07 用户裁决）。

用户原话：「如果用户说只看自然的时候，我觉得就没必要安排人文了，按照用户的需求来制定我们的方案。」
这条裁决落在三处，逐条钉住：
  ① **信号**：'想看自然风景' 这类说法要真的被认成"以自然为主"（原来只认"只想/只看"，
     真实诉求漏判 → `exclude_cultural=False` → 配比仍留 20% 人文：伊宁真跑 9 景区 + 2 人文）；
  ② **配比**：`exclude_cultural` → `no_cultural`（人文 0、自然 0.7、餐饮 0.3）；
  ③ **评分**：限定类别时，多样性不再因为"连排的都是自然点"扣分 —— 否则尺子在惩罚"照用户说的做"。
附带修掉一个**假失败**：12 个玩点里 1 个人文点被算成"占比 100% 超上限 60%"。
"""

import os
import sys
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.composition import composition_policy, composition_targets_met  # noqa: E402
from core.plan_quality import requested_palette, score_diversity  # noqa: E402
from core.route_composition import composition_targets  # noqa: E402


class SignalTests(unittest.TestCase):
    def test_nature_only_phrasing_excludes_cultural(self):
        from api.agent import extract_preference_signals

        for text in (
            "去伊宁玩3天，想看自然风景，不介意跑远",
            "只想看自然景观",
            "专门来爬山的",
            "这趟以自然为主就行",
        ):
            signals = extract_preference_signals(text)
            self.assertTrue(signals.get("wants_scenic"), text)
            self.assertTrue(signals.get("exclude_cultural"), text)

    def test_mentioning_culture_keeps_it(self):
        from api.agent import extract_preference_signals

        for text in ("想看自然风景，也想去博物馆", "去喀什玩3天，想多看人文历史和古城"):
            self.assertFalse(extract_preference_signals(text).get("exclude_cultural"), text)


class CompositionTests(unittest.TestCase):
    def test_no_cultural_targets_are_zero(self):
        targets = composition_targets(
            trip_days=3, daily_nodes=5, wants_scenic=True, exclude_cultural=True
        )
        self.assertEqual(targets["cultural"], 0, targets)
        self.assertGreater(targets["scenic"], targets["food"])


class PaletteTests(unittest.TestCase):
    def test_requested_palette_follows_the_signals(self):
        self.assertEqual(requested_palette({"wants_scenic": True, "exclude_cultural": True}), {"scenic"})
        self.assertEqual(requested_palette({"wants_scenic": True, "wants_cultural": False}), {"scenic"})
        self.assertEqual(requested_palette({"wants_cultural": True, "wants_scenic": False}), {"cultural"})
        self.assertEqual(requested_palette({"wants_scenic": True, "wants_cultural": True}), set())
        self.assertEqual(requested_palette(None), set())

    def _plan(self, pattern):
        names = {
            "scenic": ("风景区", "风景名胜"),
            "food": ("小吃店", "餐饮服务"),
            "hotel": ("客栈", "住宿服务"),
            "cultural": ("博物馆", "博物馆"),
        }
        route = []
        for index, key in enumerate(pattern):
            name, kind = names[key]
            route.append({"day": 1, "name": f"{name}{index}", "type": kind, "lnglat": [76.0, 39.4]})
        return {"route": route}

    def test_scenic_runs_are_not_punished_when_scenic_is_the_palette(self):
        plan = self._plan(["scenic"] * 6 + ["food", "hotel"])
        raw = score_diversity(plan)
        scoped = score_diversity(plan, palette={"scenic"})
        self.assertGreater(raw["max_run"], 2)
        self.assertGreater(scoped["value"], raw["value"], "用户只要自然，连排自然点不该扣分")
        self.assertEqual(scoped["palette"], ["scenic"])
        self.assertEqual(scoped["counted_run"], 1)

    def test_food_runs_are_still_punished(self):
        plan = self._plan(["scenic", "food", "food", "food", "food", "hotel"])
        scoped = score_diversity(plan, palette={"scenic"})
        self.assertGreaterEqual(scoped["counted_run"], 4, "连排餐厅仍然要扣")

    def test_no_palette_keeps_the_old_behaviour(self):
        plan = self._plan(["scenic"] * 5 + ["food"])
        self.assertEqual(score_diversity(plan)["value"], score_diversity(plan, palette=set())["value"])


class ShareBasisTests(unittest.TestCase):
    """12 个玩点里 1 个人文点被算成"占比 100% 超上限 60%" —— 样本太小不许判占比。"""

    def _plan(self, cultural_count, scenic_count):
        route = []
        for index in range(scenic_count):
            route.append({"day": index // 3 + 1, "name": f"风景区{index}", "type": "风景名胜", "lnglat": [76.0, 39.4]})
        for index in range(cultural_count):
            route.append({"day": index // 3 + 1, "name": f"博物馆{index}", "type": "博物馆", "lnglat": [76.01, 39.41]})
        for index in range(3):
            route.append({"day": index + 1, "name": f"餐馆{index}", "type": "餐饮服务", "lnglat": [76.0, 39.4]})
        return {"route": route}

    def test_single_cultural_is_not_a_monotony_failure(self):
        plan = self._plan(cultural_count=1, scenic_count=11)
        policy = composition_policy({"days": 3, "city": "伊宁", "request_text": "想看自然风景"})
        met = composition_targets_met(plan, policy)
        self.assertLess(met["category_share_basis"], met["category_share_min_basis"])
        self.assertFalse(met["share_over_cap"], met)

    def test_real_monotony_is_still_caught(self):
        plan = self._plan(cultural_count=4, scenic_count=2)
        policy = composition_policy({"days": 3, "city": "伊宁", "request_text": "想看自然风景"})
        met = composition_targets_met(plan, policy)
        self.assertGreaterEqual(met["category_share_basis"], met["category_share_min_basis"])
        self.assertTrue(met["share_over_cap"], met)


if __name__ == "__main__":
    unittest.main()
