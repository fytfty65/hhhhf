"""文化知识库与无障碍智能体的测试（P1，2026-10-08）。

这两个智能体的重点都不是"能输出多少内容"，而是**没有依据时不输出**：
- 文化：查不到条目就如实留白，绝不拿模型世界知识补一段"一般来说……"；
- 无障碍：没有官方资料时**不判断**"是否无障碍"，只给"未核实 + 待确认清单"。
"""

import asyncio
import os
import sys
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent_contracts import AgentInput  # noqa: E402
from core.agent_runtime import (  # noqa: E402
    ACCESSIBILITY_AGENT_ID,
    CULTURE_AGENT_ID,
    DECLARED_NOT_WIRED,
    WIRED_AGENT_IDS,
    accessibility_handler,
    culture_handler,
    declared_tools,
    registry_coverage,
)
from core.culture_kb import (  # noqa: E402
    CULTURE_ENTRIES,
    KB_REVIEWED_AT,
    culture_entry_for,
    culture_fact,
    has_volatile_numbers,
)


def run(coro):
    return asyncio.run(coro)


class RegistryWiringTests(unittest.TestCase):
    """两个智能体从"声明了没接线"变成"真的接线"。"""

    def test_both_agents_are_now_wired(self):
        self.assertIn(CULTURE_AGENT_ID, WIRED_AGENT_IDS)
        self.assertIn(ACCESSIBILITY_AGENT_ID, WIRED_AGENT_IDS)

    def test_no_agent_is_left_unclassified(self):
        """每个声明过的智能体要么接线、要么在 DECLARED_NOT_WIRED 里写明原因。"""
        coverage = registry_coverage()
        self.assertEqual(coverage["not_wired"], [])
        self.assertEqual(DECLARED_NOT_WIRED, {})
        self.assertEqual(coverage["coverage_ratio"], 1.0)
        self.assertEqual(coverage["declared_count"], coverage["wired_count"])

    def test_allowed_tools_come_from_the_registry(self):
        """请求里的 allowed_tools 不再写死空列表（P2 的基础）。"""
        self.assertEqual(declared_tools(CULTURE_AGENT_ID), ["knowledge.official.search"])
        self.assertEqual(
            declared_tools(ACCESSIBILITY_AGENT_ID),
            ["accessibility.official.search", "amap.route.plan"],
        )
        self.assertEqual(declared_tools("不存在的智能体"), [])


class CultureKnowledgeBaseTests(unittest.TestCase):
    def test_every_entry_has_sources_and_review_date(self):
        """每条都要有**出处**与人工复核日期 —— 没有出处的条目不许进库。"""
        for entry in CULTURE_ENTRIES:
            with self.subTest(entry=entry.get("name")):
                self.assertTrue(entry.get("name"))
                self.assertTrue(entry.get("background"))
                sources = entry.get("sources") or []
                self.assertTrue(sources, "必须有出处")
                for source in sources:
                    self.assertTrue(str(source.get("url") or "").startswith("http"), source)
                    self.assertTrue(source.get("label"))
                self.assertEqual(entry.get("reviewed_at"), KB_REVIEWED_AT)

    def test_entries_do_not_contain_volatile_numbers(self):
        """条目里不许写票价/时刻/日期 —— 那些会过期，而且会有第二个真相源。"""
        for entry in CULTURE_ENTRIES:
            for field in ("background", "etiquette", "recommended_stay"):
                text = str(entry.get(field) or "")
                with self.subTest(entry=entry.get("name"), field=field):
                    self.assertFalse(
                        has_volatile_numbers(text),
                        f"{entry.get('name')} 的 {field} 里出现了易变数字：{text}",
                    )

    def test_lookup_by_name_and_alias(self):
        self.assertIsNotNone(culture_entry_for("喀什古城", "喀什"))
        self.assertIsNotNone(culture_entry_for("香妃墓", "喀什"), "别名要能命中")
        self.assertIsNotNone(culture_entry_for("巴音布鲁克", "库尔勒"))

    def test_unknown_place_returns_none_not_a_guess(self):
        """查不到就是 None —— 不返回一条"大概是这样的"糊弄过去。"""
        self.assertIsNone(culture_entry_for("某个不存在的地方", "喀什"))
        self.assertIsNone(culture_entry_for("", ""))

    def test_culture_fact_is_marked_as_knowledge_base(self):
        entry = culture_entry_for("喀什古城", "喀什")
        fact = culture_fact(entry)
        self.assertEqual(fact["source_type"], "knowledge_base")
        self.assertEqual(fact["reviewed_at"], KB_REVIEWED_AT)
        self.assertTrue(fact["sources"])
        self.assertTrue(fact["etiquette"], "礼仪说明要有")


class CultureAgentTests(unittest.TestCase):
    def test_hit_returns_sourced_background(self):
        request = AgentInput(
            request_id="t1", agent_id=CULTURE_AGENT_ID, domain="culture",
            context={"city": "喀什", "cultural_nodes": [{"name": "喀什古城"}]},
        )
        result = run(culture_handler()(request))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertTrue(candidate["background"])
        self.assertTrue(candidate["sources"])
        self.assertEqual(candidate["source_type"], "knowledge_base")
        # 知识库不是一手来源：证据必须是"估算"，不许冒充可核实
        self.assertTrue(result["evidence"][0].get("estimated"), "知识库证据要标 estimated")
        # 而且不能说成"已核实"
        self.assertIn("知识库", result["explanation"])

    def test_miss_is_honest_and_does_not_invent(self):
        """**查不到条目时不许编**：这条是这个智能体存在的意义。"""
        request = AgentInput(
            request_id="t2", agent_id=CULTURE_AGENT_ID, domain="culture",
            context={"city": "喀什", "cultural_nodes": [{"name": "某个冷门遗址"}]},
        )
        result = run(culture_handler()(request))
        candidate = result["candidates"][0]
        self.assertEqual(candidate["background"], "", "不许编背景")
        self.assertEqual(candidate["etiquette"], "")
        self.assertEqual(candidate["sources"], [])
        self.assertIn("不编造", candidate["note"])

    def test_empty_nodes_is_degraded_with_fallback(self):
        request = AgentInput(
            request_id="t3", agent_id=CULTURE_AGENT_ID, domain="culture", context={},
        )
        result = run(culture_handler()(request))
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["failure"]["fallback"], "omit_unsourced_fact")

    def test_coverage_is_reported(self):
        request = AgentInput(
            request_id="t4", agent_id=CULTURE_AGENT_ID, domain="culture",
            context={
                "city": "喀什",
                "cultural_nodes": [{"name": "喀什古城"}, {"name": "不存在的点"}],
            },
        )
        result = run(culture_handler()(request))
        self.assertEqual(result["applied_constraints"]["kb_version"], "culture-kb-1")
        self.assertIn("1/2", result["explanation"])


class AccessibilityAgentTests(unittest.TestCase):
    def _request(self, nodes, facts=None, mobility=False):
        return AgentInput(
            request_id="a1", agent_id=ACCESSIBILITY_AGENT_ID, domain="accessibility",
            context={
                "accessibility_nodes": nodes,
                "accessibility_facts": facts or {},
                "mobility_need": mobility,
            },
        )

    def test_without_data_it_says_unverified_and_lists_what_to_check(self):
        """**没有数据时不许判断"完全无障碍"** —— 用户点名要求的行为。"""
        result = run(accessibility_handler()(self._request([{"name": "喀什古城"}])))
        candidate = result["candidates"][0]
        self.assertFalse(candidate["verified"])
        self.assertEqual(candidate["status_text"], "无障碍信息：未核实")
        # 四项待确认清单
        self.assertEqual(len(candidate["checklist"]), 4)
        joined = "".join(candidate["checklist"])
        for expected in ("入口坡度", "电梯", "卫生间", "接驳车"):
            self.assertIn(expected, joined)
        # 设施一律 unknown，绝不当成"有"
        for value in candidate["facilities"].values():
            self.assertEqual(value, "unknown")
        self.assertIn("不判断", candidate["note"])

    def test_never_claims_accessible_without_facts(self):
        """整份结果里不许出现"完全无障碍/无障碍设施齐全"这种断言。"""
        result = run(accessibility_handler()(self._request([{"name": "A"}, {"name": "B"}])))
        text = str(result)
        for forbidden in ("完全无障碍", "无障碍设施齐全", "无需担心"):
            self.assertNotIn(forbidden, text)

    def test_with_official_facts_it_upgrades_to_verified(self):
        """将来接入官方无障碍数据时能自动升级为已核实（架构能力保留）。"""
        facts = {"喀什古城": {"电梯": "有（游客中心）", "无障碍卫生间": "有"}}
        result = run(accessibility_handler()(self._request([{"name": "喀什古城"}], facts)))
        candidate = result["candidates"][0]
        self.assertTrue(candidate["verified"])
        self.assertEqual(candidate["status_text"], "无障碍信息：已核实")
        self.assertEqual(candidate["facilities"]["电梯"], "有（游客中心）")
        self.assertEqual(candidate["facilities"]["入口坡度"], "unknown", "没数据的那项仍是 unknown")
        self.assertEqual(candidate["checklist"], [])

    def test_mobility_need_is_flagged_but_not_faked(self):
        result = run(accessibility_handler()(self._request([{"name": "A"}], mobility=True)))
        self.assertTrue(result["applied_constraints"]["mobility_need"])
        self.assertIn("行动不便", result["explanation"])
        # 仍然不许因此就说"已确认可用"
        self.assertFalse(result["candidates"][0]["verified"])

    def test_empty_nodes_is_degraded(self):
        result = run(accessibility_handler()(self._request([])))
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["failure"]["fallback"], "mark_unverified")


if __name__ == "__main__":
    unittest.main()
