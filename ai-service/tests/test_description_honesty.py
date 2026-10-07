"""地点简介（desc）的诚实性测试（2026-10-05）。

真实事故（并排读 qwen-plus / qwen-turbo 两份方案时抓到的**用户可见的编造**）：
  · `胡杨书院` 的简介是 `Official YouTube TV Help Center where you can find tips and tutorials ...`
    （搜索引擎给这条 query 回了一条毫不相干的英文帮助页，富化入口**照单全收**）；
  · 历史方案里还有 `中医认为，适量饮用酸奶对健康有益…` 当某个餐馆的简介、`足球公园` 的简介带 "sign in"；
  · 其余十来个节点的简介是我们自己编的套话「本地特色文旅目的地。」「本地高人气文旅目的地。」
    —— 在**断言**（高人气/独特文化特色/交通便利）却查不到任何出处，与 (0,0) 坐标同类。

口径（doctrine：unknown ≠ 0 ≠ estimated，绝不编）：
  能核实的（校验过的联网简介 / 供应商原文）才写；否则如实写「暂无简介（未检索到可核实的来源）」，
  并且随节点记录 `desc_source`（web / provider / missing），让界面和审计能回答"这句话哪来的"。
"""

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.place_quality import (  # noqa: E402
    NO_DESC,
    clean_web_knowledge,
    desc_relevance_problem,
    description_of,
    is_boilerplate_desc,
    unrelated_descriptions,
)
from core.plan_quality import score_truthfulness  # noqa: E402

# 实测抓到的那段（原样保留，别改成"类似物"—— 回归要钉住真实事故）
YOUTUBE_JUNK = "Official YouTube TV Help Center where you can find tips and tutorials"
YOGURT_JUNK = "中医认为，适量饮用酸奶对健康有益，但需结合体质和季节调整。酸奶味甘酸、性微寒，归脾胃经。"


class RelevanceTests(unittest.TestCase):
    def test_the_actual_junk_is_rejected(self):
        self.assertIn("抓错了页面", desc_relevance_problem(YOUTUBE_JUNK, "胡杨书院", "喀什"))
        self.assertTrue(desc_relevance_problem(YOGURT_JUNK, "正宗福建馄饨王(汇城小区店)", "喀什"))

    def test_a_real_description_is_accepted(self):
        text = "胡杨书院位于喀什老城，院内以地方志和民俗藏书为主。"
        self.assertEqual(desc_relevance_problem(text, "胡杨书院", "喀什"), "")
        self.assertEqual(clean_web_knowledge(text, "胡杨书院", "喀什"), text)

    def test_descriptions_without_the_place_are_rejected(self):
        self.assertTrue(desc_relevance_problem("这是一段和目的地无关的文字。", "胡杨书院", "喀什"))

    def test_city_only_counts_as_relevant(self):
        """提到城市算勉强相关（网页确实在讲这座城市），但正文里必须真的出现。"""
        text = "喀什的秋天很短暂，九月底就开始转凉。"
        self.assertEqual(desc_relevance_problem(text, "胡杨书院", "喀什"), "")

    def test_boilerplate_is_recognised(self):
        for text in (
            "本地特色文旅目的地。",
            "本地高人气文旅目的地。",
            "本地高人气热门目的地，融汇了独特的地域人文与招牌风味体验。",
            "喀什精选住宿，交通便利。",
        ):
            self.assertTrue(is_boilerplate_desc(text), text)

    def test_clean_web_knowledge_returns_empty_not_praise(self):
        """校验不过就回**空串** —— 绝不返回"编一句好听的话"当兜底（那正是要修的东西）。"""
        self.assertEqual(clean_web_knowledge(YOUTUBE_JUNK, "胡杨书院", "喀什"), "")
        self.assertEqual(clean_web_knowledge("本地特色文旅目的地。", "胡杨书院", "喀什"), "")
        self.assertEqual(clean_web_knowledge("", "胡杨书院", "喀什"), "")


class DescriptionOfTests(unittest.TestCase):
    def test_validated_web_text_is_used_and_labelled(self):
        text, source = description_of("胡杨书院", web_knowledge="胡杨书院在喀什，藏书以地方志为主。", city="喀什")
        self.assertEqual(source, "web")
        self.assertIn("胡杨书院", text)

    def test_provider_text_is_used_when_web_fails(self):
        text, source = description_of(
            "喀什博物馆",
            web_knowledge=YOUTUBE_JUNK,
            provider_desc="喀什博物馆，位于喀什市，馆藏文物以本地出土为主。",
            city="喀什",
        )
        self.assertEqual(source, "provider")
        self.assertIn("喀什博物馆", text)

    def test_missing_is_honest_not_flattering(self):
        text, source = description_of("胡杨书院", web_knowledge=YOUTUBE_JUNK, city="喀什")
        self.assertEqual(source, "missing")
        self.assertEqual(text, NO_DESC)
        self.assertNotIn("高人气", text)

    def test_legacy_boilerplate_from_provider_is_also_dropped(self):
        """老的套话（可能来自上游/缓存）认作"没有简介"，不能让它在方案里复活。"""
        text, source = description_of("胡杨书院", provider_desc="本地特色文旅目的地。", city="喀什")
        self.assertEqual(source, "missing")
        self.assertEqual(text, NO_DESC)


class UnrelatedDescriptionTests(unittest.TestCase):
    def _plan(self, desc, **extra):
        node = {"day": 1, "name": "胡杨书院", "type": "风景名胜", "lnglat": [75.9, 39.4], "desc": desc}
        node.update(extra)
        return {"route": [node]}

    def test_junk_is_listed_with_a_reason(self):
        found = unrelated_descriptions(self._plan(YOUTUBE_JUNK))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["name"], "胡杨书院")
        self.assertIn("抓错了页面", found[0]["why"])

    def test_provider_descriptions_are_trusted(self):
        self.assertEqual(unrelated_descriptions(self._plan("本地小馆，招牌是馄饨。", desc_source="provider")), [])

    def test_missing_description_is_not_a_problem(self):
        self.assertEqual(unrelated_descriptions(self._plan(NO_DESC)), [])
        self.assertEqual(unrelated_descriptions(self._plan("")), [])

    def test_boilerplate_is_flagged_as_an_unsourced_claim(self):
        found = unrelated_descriptions(self._plan("本地特色文旅目的地。"))
        self.assertEqual(len(found), 1)
        self.assertIn("套话", found[0]["why"])


class TruthfulnessCatchesItTests(unittest.TestCase):
    def test_score_truthfulness_flags_the_junk_description(self):
        """尺子必须自己抓住这类错 —— 这次是"人并排读两份方案"才发现的，不能只靠人。"""
        plan = {
            "route": [
                {"day": 1, "name": "胡杨书院", "type": "风景名胜", "lnglat": [75.9, 39.4], "desc": YOUTUBE_JUNK},
                {"day": 1, "name": "喀什博物馆", "type": "博物馆", "lnglat": [75.9, 39.5], "desc": NO_DESC},
            ]
        }
        report = score_truthfulness(plan)
        self.assertEqual(report["unrelated_description_count"], 1)
        self.assertEqual(report["unrelated_descriptions"][0]["name"], "胡杨书院")
        # 有编造简介的节点不算"可核实"，诚实写「暂无简介」的算
        self.assertEqual(report["verified_nodes"], 1)


class PipelineWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "api", "agent.py"), encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_no_fabricated_description_fallbacks_left(self):
        """那些"没有出处的赞美"不许再出现在出方案的代码里（只在注释里留作说明）。"""
        for text in ("本地高人气文旅目的地。", "本地特色文旅目的地。", "精选住宿，交通便利。", "融入独特文化特色"):
            # 出现即违规 —— 除了 `enrich_poi_with_web_search` 的注释里那句"原来这里返回的是…"
            occurrences = [
                line
                for line in self.source.splitlines()
                if text in line and not line.strip().startswith(("#", '"""', "*")) and '原来这里返回的是' not in line
            ]
            self.assertEqual(occurrences, [], f"还在编简介：{text} → {occurrences}")

    def test_enrichment_is_validated(self):
        self.assertIn("clean_web_knowledge(info, poi_name, city)", self.source)
        self.assertIn("web_knowledge_list = [\"\"] * len(search_candidates)", self.source)
        self.assertIn("desc_source", self.source)
        # 富化失败不再回一句"高人气"
        self.assertNotIn("本地高人气热门目的地，融汇了独特的地域人文与招牌风味体验。\"", self.source.split("原来这里返回的是")[0])


if __name__ == "__main__":
    unittest.main()
