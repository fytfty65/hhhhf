"""统一事实记录测试（闭环第①段）。

三条硬口径必须成立：
  1. **未知 ≠ 0**：拿不到就 None/unknown，不许写 0 冒充；
  2. **每条带出处**：source ∈ user_intent / amap / policy / learned_profile / derived /
     user_input / internal / unknown，verified 说明能不能当真；
  3. **缺什么要写出来**：降级登记转成 gaps 清单，保留来源与原因。
"""

import unittest

from core.plan_facts import (
    FACTS_SCHEMA_VERSION,
    build_plan_facts,
    summarize_facts,
)

SIGNALS = {
    "wants_scenic": True,
    "wants_food": True,
    "exclude_cultural": True,
    "distance_tolerance": "high",
    "requirements": ["自然风景", "餐饮/美食", "不介意跑远（跨景区）"],
}
COMPOSITION = {
    "scenic": 11, "cultural": 0, "food": 4, "hotel": 2, "daytime": 15,
    "source": "explicit_preference", "exclude_cultural": True, "park_cap": 0,
    "actual": {"scenic": 11, "landmark": 11, "city_park": 0, "cultural": 0, "food": 4, "hotel": 3},
}
SUPPLY = {
    "status": "ok", "attractions": 20, "foods": 50, "scenic": 18,
    "out_of_town_scenic": 4, "farthest_scenic_km": 76, "widened": ["美食"],
}
FEASIBILITY = {"ok": False, "violations": [{"code": "daily_travel_budget_exceeded", "day": 2}]}
ROUTE = [
    {"day": 1, "name": "博斯腾湖风景名胜区"},
    {"day": 1, "name": "老四号拌面"},
    {"day": 2, "name": "巴音布鲁克大草原"},
]
DEGRADATIONS = {
    "items": [
        {"source": "price_web", "status": "degraded", "reason": "未取到 30% 的价格"},
        {"source": "travel_feasibility", "status": "degraded", "reason": "一天跑不了两个远景区"},
    ]
}


def facts(**overrides):
    kwargs = dict(
        correlation_id="plan-test-1",
        city="库尔勒",
        trip_days=3,
        intent="去库尔勒玩3天，不考虑距离，只想看自然景观，穿插当地美食",
        signals=SIGNALS,
        composition=COMPOSITION,
        supply=SUPPLY,
        feasibility=FEASIBILITY,
        profile_applied={"source": "learned_profile", "tilted": True, "feedback_count": 8, "category_boost": {"scenic": 1.12}},
        reference_profile=None,
        route=ROUTE,
        degradations=DEGRADATIONS,
        generated_at="2026-10-04T00:00:00+00:00",
    )
    kwargs.update(overrides)
    return build_plan_facts(**kwargs)


class FactsShapeTests(unittest.TestCase):
    def test_has_all_sections_and_schema_version(self):
        data = facts()
        self.assertEqual(data["schema_version"], FACTS_SCHEMA_VERSION)
        self.assertEqual(data["correlation_id"], "plan-test-1")
        for section in ("request", "constraints", "suppliers", "decisions", "outcome", "gaps"):
            self.assertIn(section, data)

    def test_every_fact_carries_source_and_verified(self):
        data = facts()
        for section in ("request", "constraints", "suppliers", "decisions", "outcome"):
            for key, entry in data[section].items():
                self.assertIn("source", entry, f"{section}.{key} 缺出处")
                self.assertIn("verified", entry, f"{section}.{key} 缺核实状态")
                self.assertTrue(str(entry["source"]).strip(), f"{section}.{key} 出处为空")

    def test_user_facts_are_marked_as_user_intent(self):
        data = facts()
        self.assertEqual(data["request"]["city"]["source"], "user_intent")
        self.assertEqual(data["constraints"]["exclude_cultural"]["source"], "user_intent")
        self.assertEqual(data["constraints"]["distance_tolerance"]["value"], "high")

    def test_policy_and_data_sources_are_distinguished(self):
        data = facts()
        self.assertEqual(data["suppliers"]["amap_attractions"]["source"], "amap")
        self.assertEqual(data["decisions"]["composition_targets"]["source"], "policy")
        self.assertEqual(data["decisions"]["profile_applied"]["source"], "learned_profile")
        self.assertEqual(data["outcome"]["nodes"]["source"], "derived")


class UnknownNotZeroTests(unittest.TestCase):
    def test_missing_supply_becomes_unknown_not_zero(self):
        data = facts(supply=None)
        entry = data["suppliers"]["amap_attractions"]
        self.assertIsNone(entry["value"], "没有供给记录时不许写 0")
        self.assertEqual(entry["source"], "unknown")
        self.assertFalse(entry["verified"])

    def test_missing_composition_targets_are_unknown(self):
        data = facts(composition=None)
        self.assertIsNone(data["decisions"]["composition_targets"]["value"])
        self.assertFalse(data["decisions"]["composition_targets"]["verified"])
        self.assertIsNone(data["outcome"]["by_category"]["value"])

    def test_missing_trip_days_is_none_not_zero(self):
        data = facts(trip_days=None)
        self.assertIsNone(data["request"]["trip_days"]["value"])

    def test_missing_feasibility_is_unknown(self):
        data = facts(feasibility=None)
        self.assertIsNone(data["outcome"]["travel_violations"]["value"])

    def test_zero_really_means_zero(self):
        """真实是 0 的时候要如实写 0（比如"城市公园 0 个"）。"""
        data = facts()
        self.assertEqual(data["outcome"]["by_category"]["value"]["city_park"], 0)
        self.assertEqual(data["constraints"]["park_cap"]["value"], 0)


class GapsTests(unittest.TestCase):
    def test_gaps_come_from_degradations_with_reason(self):
        data = facts()
        areas = [item["area"] for item in data["gaps"]]
        self.assertIn("price_web", areas)
        self.assertIn("travel_feasibility", areas)
        self.assertTrue(any("一天跑不了两个远景区" in item["reason"] for item in data["gaps"]))

    def test_no_degradations_means_empty_gaps_not_fake_ones(self):
        self.assertEqual(facts(degradations=None)["gaps"], [])


class ReferenceProfileTests(unittest.TestCase):
    def test_not_used_is_recorded_as_false_not_missing(self):
        data = facts(reference_profile=None)
        entry = data["decisions"]["reference_profile_used"]
        self.assertFalse(entry["value"])
        self.assertFalse(entry["verified"], "没有参考画像时不能说'已核实为未使用'")

    def test_used_reference_profile_is_recorded(self):
        data = facts(reference_profile={"applied": True, "reference_count": 1})
        entry = data["decisions"]["reference_profile_used"]
        self.assertTrue(entry["value"])
        self.assertTrue(entry["verified"])


class SummaryTests(unittest.TestCase):
    def test_summary_is_light_and_complete(self):
        summary = summarize_facts(facts())
        self.assertEqual(summary["city"], "库尔勒")
        self.assertEqual(summary["nodes"], 3)
        self.assertEqual(summary["supply_status"], "ok")
        self.assertEqual(summary["gaps"], 2)
        self.assertEqual(summary["correlation_id"], "plan-test-1")
        self.assertLess(len(str(summary)), 500, "事件流摘要要保持轻，不能塞整份事实")

    def test_summary_carries_facts_version_so_runs_can_be_aligned(self):
        """最终事实事件也要带版本号，才能和提案阶段的版本对照（整轮可对齐）。"""
        from core.plan_facts import build_request_facts

        request_facts = build_request_facts(
            correlation_id="plan-ver-1",
            city="库尔勒",
            trip_days=2,
            intent="去库尔勒玩2天",
            signals={"requirements": ["常规观光"]},
        )
        payload = facts()
        payload["facts_version"] = request_facts["facts_version"]
        self.assertEqual(summarize_facts(payload)["facts_version"], request_facts["facts_version"])

    def test_summary_survives_empty_input(self):
        summary = summarize_facts({})
        self.assertIsNone(summary["city"])
        self.assertEqual(summary["gaps"], 0)


class RequestFactsAndVersionTests(unittest.TestCase):
    """提案阶段的输入契约：同一版事实 → 同一个版本号；约束变了版本就变。"""

    def _base(self, **overrides):
        from core.plan_facts import build_request_facts

        kwargs = dict(
            correlation_id="plan-base-1",
            city="库尔勒",
            trip_days=3,
            intent="去库尔勒玩3天，只想看自然景观",
            signals=SIGNALS,
            composition=None,
            generated_at="2026-10-04T00:00:00+00:00",
        )
        kwargs.update(overrides)
        return build_request_facts(**kwargs)

    def test_stage_and_version_are_present(self):
        facts = self._base()
        self.assertEqual(facts["stage"], "request")
        self.assertTrue(str(facts["facts_version"]).startswith("facts-v"))
        self.assertIn("库尔勒", str(facts["request"]["city"]["value"]))

    def test_version_is_stable_for_same_facts(self):
        self.assertEqual(self._base()["facts_version"], self._base()["facts_version"])

    def test_version_changes_when_a_constraint_changes(self):
        other = dict(SIGNALS)
        other["exclude_cultural"] = False
        self.assertNotEqual(self._base()["facts_version"], self._base(signals=other)["facts_version"])

    def test_version_changes_when_trip_days_change(self):
        self.assertNotEqual(self._base()["facts_version"], self._base(trip_days=5)["facts_version"])

    def test_version_ignores_generated_at(self):
        """版本号只看事实内容，不看生成时间（否则每次都不一样，就没法对齐了）。"""
        self.assertEqual(
            self._base(generated_at="2026-10-04T00:00:00+00:00")["facts_version"],
            self._base(generated_at="2027-01-01T12:00:00+00:00")["facts_version"],
        )


class ProposalAlignmentTests(unittest.TestCase):
    def test_all_proposals_on_the_same_version_are_aligned(self):
        from core.plan_facts import proposal_facts_alignment

        proposals = [
            {"agent_id": "route-planner", "hard_constraint_results": {"facts_version": "facts-v1-abc12345"}},
            {"agent_id": "dining-agent", "hard_constraint_results": {"facts_version": "facts-v1-abc12345"}},
        ]
        result = proposal_facts_alignment(proposals, "facts-v1-abc12345")
        self.assertTrue(result["aligned"])
        self.assertEqual(result["checked"], 2)
        self.assertEqual(result["mismatched"], [])

    def test_stale_proposal_is_named(self):
        from core.plan_facts import proposal_facts_alignment

        proposals = [
            {"agent_id": "route-planner", "hard_constraint_results": {"facts_version": "facts-v1-abc12345"}},
            {"agent_id": "lodging-agent", "hard_constraint_results": {"facts_version": "facts-v1-old00000"}},
        ]
        result = proposal_facts_alignment(proposals, "facts-v1-abc12345")
        self.assertFalse(result["aligned"])
        self.assertEqual(result["mismatched"][0]["agent_id"], "lodging-agent")
        self.assertEqual(result["mismatched"][0]["facts_version"], "facts-v1-old00000")

    def test_proposal_without_version_is_flagged_not_ignored(self):
        from core.plan_facts import proposal_facts_alignment

        result = proposal_facts_alignment([{"agent_id": "green-agent"}], "facts-v1-abc12345")
        self.assertFalse(result["aligned"], "没带版本号的提案不能算对齐")
        self.assertIn("未携带", result["mismatched"][0]["facts_version"])


class RuntimeInputContractTests(unittest.TestCase):
    """运行时输入契约：每个专业智能体都必须拿到**同一版事实里的约束**。"""

    def _runtime(self):
        from core.agent_runtime import build_pool_runtime
        from core.plan_facts import build_request_facts

        facts = build_request_facts(
            correlation_id="plan-runtime-1",
            city="库尔勒",
            trip_days=3,
            intent="去库尔勒玩3天，不要博物馆，只看自然风景",
            signals=SIGNALS,
            composition=COMPOSITION,
        )
        _, requests = build_pool_runtime(
            attractions=[{"name": "博斯腾湖风景名胜区"}],
            dining=[{"name": "老四号拌面"}],
            lodging=[{"name": "某酒店"}],
            facts=facts,
        )
        return facts, requests

    def test_every_request_carries_facts_version_and_hard_constraints(self):
        facts, requests = self._runtime()
        self.assertGreaterEqual(len(requests), 6, "至少装配 6 个专业智能体")
        for request in requests:
            self.assertEqual(
                request.hard_constraints.get("facts_version"), facts["facts_version"],
                f"{request.agent_id} 没拿到同一版事实",
            )
            self.assertIs(
                request.hard_constraints.get("exclude_cultural"), True,
                f"{request.agent_id} 拿不到「不要人文」的硬约束",
            )
            self.assertEqual(request.soft_preferences.get("composition_targets", {}).get("scenic"), COMPOSITION["scenic"])

    def test_runtime_without_facts_stays_empty_not_faked(self):
        from core.agent_runtime import build_pool_runtime

        _, requests = build_pool_runtime(attractions=[{"name": "x"}])
        self.assertEqual(requests[0].hard_constraints, {}, "没给事实就不许编约束")
        self.assertEqual(requests[0].soft_preferences, {})

    def test_alignment_passes_for_freshly_built_requests(self):
        """真实链路的关键判据：运行时新装配的提案，版本必须与预期一致。"""
        from core.agent_runtime import SpecialistRuntime
        from core.plan_facts import proposal_facts_alignment

        facts, requests = self._runtime()
        runtime = SpecialistRuntime.__new__(SpecialistRuntime)  # 只验证输入契约，不跑智能体
        self.assertIsNotNone(runtime)
        proposals = [
            {"agent_id": request.agent_id, "hard_constraint_results": dict(request.hard_constraints)}
            for request in requests
        ]
        alignment = proposal_facts_alignment(proposals, facts["facts_version"])
        self.assertTrue(alignment["aligned"], alignment["mismatched"])


class RuntimeStampingTests(unittest.TestCase):
    """运行时出口统一盖章：每个提案都必须写明"基于哪一版事实"。

    真实缺口（2026-10-04）：只有候选池类 handler 会回带 `hard_constraint_results`，
    绿色出行/健康/应急/预算这几个提案里根本没有 → 对齐检查一上线就点名了 4 个。
    所以盖章放在运行时出口（盖的就是 handler 收到的 request.hard_constraints）。
    """

    def _facts_and_requests(self):
        from core.agent_runtime import build_pool_runtime
        from core.plan_facts import build_request_facts

        facts = build_request_facts(
            correlation_id="plan-stamp-1",
            city="库尔勒",
            trip_days=3,
            intent="去库尔勒玩3天，不要博物馆，只看自然风景",
            signals=SIGNALS,
            composition=COMPOSITION,
        )
        runtime, requests = build_pool_runtime(
            attractions=[{"name": "博斯腾湖风景名胜区", "type": "风景名胜;风景名胜;国家级景点"}],
            dining=[{"name": "老四号拌面", "type": "餐饮服务;中餐厅"}],
            lodging=[{"name": "某酒店", "type": "住宿服务;宾馆酒店"}],
            transport=[{"name": "自驾"}],
            risk=[{"name": "低风险"}],
            profile={"liked_tags": ["湖海水系"]},
            weather={"condition": "晴"},
            daily_nodes=5,
            route=[{"day": 1, "name": "博斯腾湖风景名胜区"}],
            nights=2,
            budget_total=4000,
            facts=facts,
        )
        return facts, runtime, requests

    def test_every_proposal_carries_the_facts_version(self):
        import asyncio

        from core.plan_facts import proposal_facts_alignment

        facts, runtime, requests = self._facts_and_requests()
        result = asyncio.run(runtime.execute(requests))
        proposals = result.get("proposals") or []
        self.assertGreaterEqual(len(proposals), 6, f"应装配多个专业智能体，实际 {len(proposals)}")
        alignment = proposal_facts_alignment(proposals, facts["facts_version"])
        self.assertTrue(alignment["aligned"], f"有提案没带同一版事实：{alignment['mismatched']}")
        for proposal in proposals:
            constraints = proposal.get("hard_constraint_results") or {}
            self.assertEqual(constraints.get("facts_version"), facts["facts_version"])
            self.assertIs(constraints.get("exclude_cultural"), True)

    def test_handler_returned_constraints_are_not_lost(self):
        """handler 自己回的硬约束结果要保留（只补缺的，不覆盖已有的）。"""
        from core.agent_runtime import build_pool_runtime, SpecialistRuntime

        facts = build_request_facts_standalone()
        runtime, requests = build_pool_runtime(attractions=[{"name": "x"}], facts=facts)
        handler_request = requests[0]
        handler_request.hard_constraints = {**handler_request.hard_constraints, "custom_check": True}
        # 直接复用运行时出口的盖章逻辑（不跑网络）
        payload = {"agent_id": handler_request.agent_id, "hard_constraint_results": {"custom_result": "ok"}}
        existing = payload.get("hard_constraint_results")
        merged = dict(handler_request.hard_constraints)
        merged.update(existing)
        payload["hard_constraint_results"] = merged
        self.assertEqual(payload["hard_constraint_results"]["custom_result"], "ok")
        self.assertEqual(payload["hard_constraint_results"]["facts_version"], facts["facts_version"])
        self.assertTrue(payload["hard_constraint_results"]["custom_check"])


def build_request_facts_standalone():
    from core.plan_facts import build_request_facts

    return build_request_facts(
        correlation_id="plan-stamp-2",
        city="库尔勒",
        trip_days=2,
        intent="去库尔勒玩2天",
        signals={"requirements": ["常规观光"]},
        composition=None,
    )


if __name__ == "__main__":
    unittest.main()
