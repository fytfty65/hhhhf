"""`intent_of` 的类别优先口径（2026-10-05 真实推演抓到的错判）。

事故形态：喀什「**古城**昆仑塔逸扉度假酒店」（`type=住宿服务`）被判成 `cultural` ——
名字里的"古城"先撞上人文关键词，于是**住宿实时房价那一步跳过了它**，
最终方案里这家酒店永远是"暂无供应商数据"。类别（供应商给的）没有歧义，名字有。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.candidate_index import intent_of  # noqa: E402


class IntentOfLodgingTests(unittest.TestCase):
    def test_provider_category_beats_a_misleading_name(self):
        node = {"name": "喀什古城昆仑塔逸扉度假酒店", "type": "住宿服务", "tags": ["住宿"], "day": 1}
        self.assertEqual(intent_of(node), "hotel")

    def test_ambiguous_names_without_a_category_still_use_the_name(self):
        """纯文本（没有类别）时仍按名字判 —— 这条保持原行为，避免影响检索与配额。"""
        self.assertEqual(intent_of("喀什古城"), "cultural")
        self.assertEqual(intent_of("某酒店"), "hotel")

    def test_explicit_intent_still_wins(self):
        node = {"name": "喀什古城某酒店", "type": "住宿服务", "intent": "cultural"}
        self.assertEqual(intent_of(node), "cultural", "显式声明的意图优先级最高")

    def test_explicit_tags_still_win_over_category(self):
        node = {"name": "某某", "type": "风景名胜", "tags": ["hotel"]}
        self.assertEqual(intent_of(node), "hotel")

    def test_is_hotel_flag_is_honoured(self):
        self.assertEqual(intent_of({"name": "某某", "type": "", "is_hotel": True}), "hotel")

    def test_other_lodging_categories_are_recognised(self):
        for poi_type in ("住宿服务", "宾馆酒店", "民宿", "客栈"):
            self.assertEqual(intent_of({"name": "某处", "type": poi_type}), "hotel", poi_type)

    def test_non_lodging_nodes_are_unaffected(self):
        # 类别是"风景名胜"时按自然/景区判（名字里的"古城"不再单独决定结果）
        self.assertEqual(intent_of({"name": "喀什古城", "type": "风景名胜"}), "scenic")
        self.assertEqual(intent_of({"name": "巴扎美食街", "type": "餐饮服务"}), "food")
        self.assertEqual(intent_of({"name": "某停车场", "type": "交通设施服务"}), "other")


if __name__ == "__main__":
    unittest.main()
