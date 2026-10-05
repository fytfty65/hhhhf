"""提案分歧显式化测试：让"多智能体协商"变成看得见的博弈。

真实背景（2026-10-04）：9 个专业智能体各自独立提案，事件流里只有 `candidate.proposed`
（"提了几个"）—— 看不到谁和谁冲突、裁决凭什么这么定，于是"协同"在审计上跟"并行各算一遍"
没有区别。这一层只基于**提案里已有的可观测信号**推分歧，推不出就不报（不编戏）。
"""

import os
import unittest

# api.agent 在导入时就会构造 LLM 客户端（缺 key 直接抛错，这是刻意的）。
# 这里要用**真实**的 classify_poi_category 来验证误报，所以先给个假 key 让导入跑通。
os.environ.setdefault("LLM_API_KEY", "test-key-not-used")

from core.proposal_conflicts import (
    arbitrate_conflicts,
    build_proposal_conflicts,
    detect_conflicts,
    summarize_stances,
)


def route_planner(*, far=2, near=3):
    candidates = [{"name": f"远景区{i}", "distance_km": 47 + i * 10} for i in range(far)]
    candidates += [{"name": f"市区点{i}"} for i in range(near)]
    return {"agent_id": "route-planner", "domain": "itinerary", "status": "ok",
            "candidates": candidates, "confidence": 0.75, "hard_constraint_results": {}}


def health_agent(limit=None, advice="常规节奏"):
    candidates = []
    if limit is not None:
        candidates = [{"daily_node_limit": limit, "advice": advice, "rest_buffer_minutes": 30}]
    return {"agent_id": "health-agent", "domain": "health",
            "status": "ok" if candidates else "degraded",
            "candidates": candidates, "confidence": 0.6 if candidates else 0.0}


def green_agent(rows=None):
    rows = rows if rows is not None else [
        {"label": "高铁", "carbon_kg": 12.0},
        {"label": "自驾", "carbon_kg": 68.0},
    ]
    return {"agent_id": "green-agent", "domain": "sustainability", "status": "ok",
            "candidates": rows, "confidence": 0.7}


def dining_agent(count=4):
    return {"agent_id": "dining-agent", "domain": "dining", "status": "ok",
            "candidates": [{"name": f"馆子{i}"} for i in range(count)], "confidence": 0.75}


def budget_agent_empty():
    return {"agent_id": "budget-agent", "domain": "budget", "status": "degraded", "candidates": [],
            "confidence": 0.0, "failure": {"code": "candidate_pool_empty"}}


class StanceTests(unittest.TestCase):
    def test_stances_describe_what_each_agent_wants(self):
        summary = summarize_stances([route_planner(far=2), health_agent(limit=4), green_agent(), dining_agent(4)])
        by_agent = {item["agent_id"]: item for item in summary["stances"]}
        self.assertEqual(by_agent["route-planner"]["signals"]["far_scenic_count"], 2)
        self.assertEqual(by_agent["route-planner"]["prefers"], "远景区")
        self.assertEqual(by_agent["health-agent"]["signals"]["daily_node_limit"], 4)
        self.assertEqual(by_agent["green-agent"]["signals"]["min_carbon_kg"], 12.0)
        self.assertEqual(by_agent["dining-agent"]["prefers"], "本地餐饮")

    def test_agents_without_candidates_are_listed_as_inactive_not_hidden(self):
        summary = summarize_stances([route_planner(), budget_agent_empty()])
        self.assertEqual([item["agent_id"] for item in summary["inactive"]], ["budget-agent"])
        self.assertEqual(summary["inactive"][0]["reason"], "candidate_pool_empty")

    def test_empty_input_is_empty_not_fabricated(self):
        summary = summarize_stances([])
        self.assertEqual(summary["stances"], [])
        self.assertEqual(summary["inactive"], [])


class ConflictDetectionTests(unittest.TestCase):
    def test_far_scenic_vs_intensity_is_reported(self):
        conflicts = detect_conflicts([route_planner(far=2), health_agent(limit=4)])
        codes = [item["code"] for item in conflicts]
        self.assertIn("far_scenic_vs_daily_intensity", codes)
        self.assertEqual(conflicts[0]["between"], ["route-planner", "health-agent"])
        self.assertIn("远景区", conflicts[0]["detail"])

    def test_no_conflict_when_only_one_far_scenic(self):
        """只有一个远景区就不算冲突（一天跑一个没问题）—— 不制造分歧。"""
        conflicts = detect_conflicts([route_planner(far=1), health_agent(limit=4)])
        self.assertEqual([item["code"] for item in conflicts], [])

    def test_no_conflict_when_health_is_comfortable(self):
        conflicts = detect_conflicts([route_planner(far=3), health_agent(limit=6)])
        self.assertEqual([item["code"] for item in conflicts], [])

    def test_low_carbon_vs_driving_only_when_gap_is_meaningful(self):
        big_gap = detect_conflicts([green_agent()])
        self.assertIn("low_carbon_vs_driving", [item["code"] for item in big_gap])
        small_gap = detect_conflicts([green_agent([{"label": "自驾", "carbon_kg": 30.0}, {"label": "高铁", "carbon_kg": 28.0}])])
        self.assertEqual([item["code"] for item in small_gap], [], "差距太小不该报")

    def test_carbon_conflict_needs_a_driving_option(self):
        conflicts = detect_conflicts([green_agent([{"label": "高铁", "carbon_kg": 5.0}, {"label": "飞机", "carbon_kg": 90.0}])])
        self.assertEqual([item["code"] for item in conflicts], [], "没有自驾/包车就谈不上'低碳 vs 自驾'")

    def test_forbidden_category_in_proposals_is_a_hard_conflict(self):
        def classify(name, poi_type="", is_hotel=False):
            return "cultural" if "博物馆" in name else "scenic"

        proposals = [
            {"agent_id": "route-planner", "domain": "itinerary", "status": "ok",
             "candidates": [{"name": "巴州博物馆"}, {"name": "湿地公园"}]},
        ]
        conflicts = detect_conflicts(proposals, forbidden_categories=("cultural",), classify=classify)
        entry = next(item for item in conflicts if item["code"] == "forbidden_category_in_proposals")
        self.assertEqual(entry["severity"], "hard")
        self.assertEqual(entry["evidence"]["offenders"][0]["agent_id"], "route-planner")

    def test_hotel_named_after_a_museum_is_not_a_forbidden_cultural_node(self):
        """真实误报（2026-10-04）：分歧里报出「瑞德假日酒店(巴州博物馆市政府店)」。

        它是酒店，只是名字里带"博物馆"；`classify_poi_category` 的名字优先规则会把它判成
        cultural。检测器必须按候选自身是不是住宿来分类，否则会给用户报一条不存在的"你说了不要人文"。
        """
        from api.agent import classify_poi_category

        proposals = [
            {"agent_id": "lodging-agent", "domain": "lodging", "status": "ok",
             "candidates": [{"name": "瑞德假日酒店(巴州博物馆市政府店)", "type": "住宿服务;宾馆酒店;宾馆酒店"}]},
        ]
        conflicts = detect_conflicts(
            proposals, forbidden_categories=("cultural",), classify=classify_poi_category
        )
        self.assertEqual(
            [item["code"] for item in conflicts], [],
            "酒店名字里带'博物馆'不该被当成用户明说不要的人文节点",
        )

    def test_real_museum_in_attraction_proposals_is_still_a_hard_conflict(self):
        from api.agent import classify_poi_category

        proposals = [
            {"agent_id": "route-planner", "domain": "itinerary", "status": "ok",
             "candidates": [{"name": "巴音郭楞蒙古自治州博物馆", "type": "科教文化服务;博物馆;博物馆"}]},
        ]
        conflicts = detect_conflicts(
            proposals, forbidden_categories=("cultural",), classify=classify_poi_category
        )
        self.assertEqual([item["code"] for item in conflicts], ["forbidden_category_in_proposals"])

    def test_distance_tolerance_note_when_user_does_not_mind_distance(self):
        conflicts = detect_conflicts([route_planner(far=2), health_agent(limit=4)], distance_tolerance="high")
        far_conflict = next(item for item in conflicts if item["code"] == "far_scenic_vs_daily_intensity")
        self.assertIn("不介意跑远", far_conflict.get("note", ""))


class ArbitrationTests(unittest.TestCase):
    def test_every_conflict_gets_a_decision_pointing_at_a_real_mechanism(self):
        proposals = [route_planner(far=2), health_agent(limit=4), green_agent()]
        conflicts = detect_conflicts(proposals, distance_tolerance="high")
        decisions = arbitrate_conflicts(conflicts, distance_tolerance="high")
        self.assertEqual(len(decisions), len(conflicts))
        for decision in decisions:
            self.assertTrue(str(decision.get("decision")).strip())
            self.assertTrue(str(decision.get("mechanism")).strip())
            self.assertTrue(str(decision.get("why")).strip())

    def test_far_scenic_decision_names_the_enforcing_module(self):
        decisions = arbitrate_conflicts(
            [{"code": "far_scenic_vs_daily_intensity"}], distance_tolerance="high"
        )
        self.assertIn("travel_feasibility", decisions[0]["mechanism"])
        self.assertIn("一天最多一个", decisions[0]["decision"])

    def test_forbidden_decision_names_the_exclusion_repair(self):
        decisions = arbitrate_conflicts(
            [{"code": "forbidden_category_in_proposals"}], forbidden_categories=("cultural",)
        )
        self.assertIn("repair_forbidden_categories", decisions[0]["mechanism"])

    def test_unknown_conflict_is_left_to_the_human_not_faked(self):
        decisions = arbitrate_conflicts([{"code": "some_new_conflict"}])
        self.assertEqual(decisions[0]["mechanism"], "未自动处置")
        self.assertIn("留给你判断", decisions[0]["why"])


class BundleTests(unittest.TestCase):
    def test_bundle_has_stances_conflicts_arbitration_and_notes(self):
        bundle = build_proposal_conflicts(
            [route_planner(far=2), health_agent(limit=4), budget_agent_empty()],
            distance_tolerance="high",
        )
        self.assertEqual(bundle["schema"], 1)
        self.assertTrue(bundle["stances"])
        self.assertTrue(bundle["conflicts"])
        self.assertTrue(bundle["arbitration"])
        self.assertEqual([item["agent_id"] for item in bundle["inactive"]], ["budget-agent"])
        self.assertTrue(any("没有参与博弈" in note for note in bundle["notes"]))

    def test_no_conflict_is_stated_as_checked_and_consistent(self):
        bundle = build_proposal_conflicts([route_planner(far=1), health_agent(limit=5)])
        self.assertEqual(bundle["conflicts"], [])
        self.assertTrue(any("没有显式分歧" in note for note in bundle["notes"]))
        self.assertTrue(any("不是没检查" in note for note in bundle["notes"]))


class ClassifierTypeFirstTests(unittest.TestCase):
    """真实分类事故（2026-10-04）：类型说了算，名字不该把餐厅判成人文。"""

    def _classify(self, name, poi_type, is_hotel=False):
        from api.agent import classify_poi_category

        return classify_poi_category(name, poi_type, is_hotel)

    def test_restaurant_named_with_lou_is_still_food(self):
        """「西域楼兰焰一店」名字里有"楼"，但类别是餐饮 —— 它不该被算成人文。"""
        self.assertEqual(
            self._classify("西域楼兰焰一店(延安路店)", "餐饮服务;中餐厅;中餐厅"), "food"
        )
        self.assertEqual(self._classify("老伙夫·印象餐厅(华夏名门店)", "餐饮服务;中餐厅;中餐厅"), "food")

    def test_hotel_named_after_a_museum_is_still_a_hotel(self):
        self.assertEqual(
            self._classify("瑞德假日酒店(巴州博物馆市政府店)", "住宿服务;宾馆酒店;宾馆酒店"), "hotel"
        )

    def test_shopping_place_with_cultural_word_stays_shopping(self):
        self.assertEqual(self._classify("文创商业街", "购物服务;购物相关场所;购物相关场所"), "shopping")

    def test_real_museum_is_still_cultural(self):
        self.assertEqual(self._classify("库尔勒民俗文化博物馆", "科教文化服务;博物馆;博物馆"), "cultural")
        self.assertEqual(self._classify("巴州博物馆", "科教文化服务;博物馆;博物馆"), "cultural")

    def test_name_culture_rule_still_beats_generic_nature_words(self):
        """既有修正必须保留：天山博物院/湖畔展馆不能被泛自然词抢走。"""
        self.assertEqual(self._classify("天山博物院", "风景名胜;风景名胜;旅游景点"), "cultural")
        self.assertEqual(self._classify("湖畔展馆", "风景名胜;风景名胜;旅游景点"), "cultural")

    def test_scenic_without_cultural_words_is_scenic(self):
        self.assertEqual(self._classify("博斯腾湖风景名胜区", "风景名胜;风景名胜;国家级景点"), "scenic")


class ClassifierImpactTests(unittest.TestCase):
    def test_composition_count_is_not_polluted_by_restaurant_names(self):
        from core.route_composition import count_route_composition

        def classify(name, poi_type="", is_hotel=False):
            from api.agent import classify_poi_category

            return classify_poi_category(name, poi_type, is_hotel)

        route = [
            {"day": 1, "name": "西域楼兰焰一店(延安路店)", "type": "餐饮服务;中餐厅;中餐厅"},
            {"day": 1, "name": "博斯腾湖风景名胜区", "type": "风景名胜;风景名胜;国家级景点"},
        ]
        counts = count_route_composition(route, classify)
        self.assertEqual(counts["cultural"], 0, "餐厅不该被算成人文")
        self.assertEqual(counts["food"], 1)
        self.assertEqual(counts["scenic"], 1)


class RevisionDirectiveTests(unittest.TestCase):
    """协商回环第一步：把裁决变成**可执行的指令**（数量/类别上限），而不是一句漂亮话。"""

    def test_far_scenic_conflict_produces_a_numeric_directive(self):
        from core.proposal_conflicts import build_revision_directives

        directives = build_revision_directives(
            [{"code": "far_scenic_vs_daily_intensity", "between": ["route-planner", "health-agent"]}],
            trip_days=3,
        )
        self.assertIn("route-planner", directives)
        self.assertEqual(directives["route-planner"]["hard_constraints"]["max_far_scenic"], 3)
        self.assertIn("一天最多一个远景区", directives["route-planner"]["message"])

    def test_forbidden_conflict_targets_the_offending_agents(self):
        from core.proposal_conflicts import build_revision_directives

        directives = build_revision_directives(
            [{"code": "forbidden_category_in_proposals", "between": ["dining-agent"]}],
            forbidden_categories=("cultural",),
        )
        self.assertIn("dining-agent", directives)
        self.assertIn("排除", directives["dining-agent"]["message"])

    def test_no_conflicts_means_no_directives(self):
        from core.proposal_conflicts import build_revision_directives

        self.assertEqual(build_revision_directives([]), {})


class ContractLimitTests(unittest.TestCase):
    """智能体必须能**真的按裁决重提**，否则再协商一轮就是走过场。"""

    def _handle(self, rows, constraints):
        import asyncio

        from core.agent_runtime import pool_handler
        from core.agent_contracts import AgentInput

        handler = pool_handler("route-planner", "itinerary", provider="amap", field="attraction_candidates")
        request = AgentInput(
            request_id="r1", agent_id="route-planner", domain="itinerary",
            context={"candidates": rows}, hard_constraints=constraints, soft_preferences={}, allowed_tools=[],
        )
        return asyncio.run(handler(request))

    def test_max_far_scenic_keeps_the_nearest_ones(self):
        rows = [
            {"name": "近景区", "distance_km": 45},
            {"name": "中景区", "distance_km": 80},
            {"name": "远景区", "distance_km": 270},
            {"name": "市区点"},
        ]
        proposal = self._handle(rows, {"max_far_scenic": 1})
        names = [item["name"] for item in proposal["candidates"]]
        self.assertIn("近景区", names)
        self.assertNotIn("远景区", names, "超限的远景区必须被去掉")
        self.assertEqual(proposal["applied_constraints"]["max_far_scenic"]["dropped_count"], 2)
        self.assertIn("按协商裁决重提", proposal["explanation"])

    def test_candidate_cap_trims_total(self):
        rows = [{"name": f"点{i}"} for i in range(10)]
        proposal = self._handle(rows, {"candidate_cap": 4})
        self.assertEqual(len(proposal["candidates"]), 4)
        self.assertEqual(proposal["applied_constraints"]["candidate_cap"]["dropped_count"], 6)
        self.assertEqual(len(proposal["applied_constraints"]["candidate_cap"]["dropped"]), 6)

    def test_no_constraints_means_untouched(self):
        rows = [{"name": "远景区", "distance_km": 270}, {"name": "点"}]
        proposal = self._handle(rows, {})
        self.assertEqual(len(proposal["candidates"]), 2)
        self.assertNotIn("applied_constraints", proposal)

    def test_out_of_town_flag_counts_as_far(self):
        rows = [{"name": "周边景区", "out_of_town": True}, {"name": "近处", "distance_km": 3}]
        proposal = self._handle(rows, {"max_far_scenic": 0})
        self.assertEqual([item["name"] for item in proposal["candidates"]], ["近处"])


class NegotiationLoopTests(unittest.TestCase):
    """整条回环：远景区超天数 → 检测 → 指令 → 重提 → 再检测（分歧应消失）。"""

    def _first_round(self, far_count=6, trip_days=3):
        import asyncio

        from core.agent_runtime import build_pool_runtime
        from core.plan_facts import build_request_facts

        facts = build_request_facts(
            correlation_id="plan-loop-1",
            city="库尔勒",
            trip_days=trip_days,
            intent="去库尔勒玩3天，不考虑距离，只想看自然景观",
            signals={"wants_scenic": True, "requirements": ["自然风景"]},
            composition=None,
        )
        attractions = [
            {"name": f"远景区{i}", "distance_km": 45 + i * 20, "type": "风景名胜;风景名胜;国家级景点"}
            for i in range(far_count)
        ] + [{"name": "市区公园", "type": "风景名胜;公园广场;公园"}]
        runtime, requests = build_pool_runtime(attractions=attractions, facts=facts)
        result = asyncio.run(runtime.execute(requests))
        return facts, attractions, result

    def test_loop_resolves_the_far_scenic_conflict(self):
        import asyncio

        from core.agent_runtime import build_pool_runtime
        from core.proposal_conflicts import (
            build_proposal_conflicts,
            build_revision_directives,
            evaluate_revisions,
        )

        facts, attractions, first = self._first_round(far_count=6, trip_days=3)
        before = build_proposal_conflicts(first["proposals"], trip_days=3)
        codes_before = [item["code"] for item in before["conflicts"]]
        self.assertIn("far_scenic_vs_daily_intensity", codes_before, before["notes"])

        directives = build_revision_directives(before["conflicts"], trip_days=3)
        runtime, requests = build_pool_runtime(attractions=attractions, facts=facts)
        target = [req for req in requests if req.agent_id == "route-planner"]
        for req in target:
            req.hard_constraints.update(directives["route-planner"]["hard_constraints"])
            req.context["revision_directive"] = directives["route-planner"]["message"]
        revised_result = asyncio.run(runtime.execute(target))
        revised = {str(item["agent_id"]): item for item in revised_result["proposals"]}

        merged = [revised.get(str(item["agent_id"]), item) for item in first["proposals"]]
        after = build_proposal_conflicts(merged, trip_days=3)
        record = evaluate_revisions(before["conflicts"], after["conflicts"], revised)

        self.assertEqual(record["revised_agents"], ["route-planner"])
        self.assertTrue(record["resolved_all"], record)
        self.assertEqual([item["code"] for item in after["conflicts"]], [])
        self.assertEqual(record["resolved"], ["far_scenic_vs_daily_intensity"])
        self.assertTrue(record["changed"], "重提要说明按裁决去掉了什么")
        self.assertEqual(record["changed"][0]["applied"]["max_far_scenic"]["limit"], 3)

    def test_loop_reports_remaining_conflicts_honestly(self):
        from core.proposal_conflicts import evaluate_revisions

        record = evaluate_revisions(
            [{"code": "a"}, {"code": "b"}],
            [{"code": "b"}],
            {"route-planner": {"agent_id": "route-planner", "candidates": []}},
        )
        self.assertEqual(record["resolved"], ["a"])
        self.assertEqual(record["remaining"], ["b"])
        self.assertFalse(record["resolved_all"])
        self.assertTrue(any("仍有分歧" in note for note in record["notes"]))


if __name__ == "__main__":
    unittest.main()
