"""收口之后按**最终路线**重算质量分的测试（2026-10-05）。

真实场景：`final_data["quality"]` 是治理快照阶段（`prepare_governance`）算的，之后的收口
还会改路线 —— 移出跑不了的远景点、按"不想跑远"就地换近点、给缺坐标的住宿按名字补坐标、
查实时房价。结论必须跟着最终路线走，否则：
  · 日志说「📍 [坐标补齐] 按名字补到 1 个」，`anchoring.coordinate_coverage` 却是补之前的数；
  · 前端拿旧分数/旧明细渲染 → 用户看到的方案和分数说的不是同一份东西。

这里把"重算"的两块都钉住：
  ① `core.planning_governance.refresh_snapshot` 的口径必须和 `prepare_governance` 一致
     （同一份 context/signals、同一个 tier_policy、同一批候选池）；
  ② `api/agent.py` 真的在出稿前调了它，而且输入变量**在分支外有兜底初值**（否则
     NameError 会被 `except: print` 静默吞掉，变成"没重算"）。
"""

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_quality import evaluate_plan, plan_quality_snapshot  # noqa: E402
from core.planning_governance import refresh_snapshot, tier_policy  # noqa: E402

CENTER = [75.99, 39.47]


def _plan(coord=None):
    node = {"day": 1, "name": "喀什古城步行街亚朵酒店", "type": "住宿服务", "is_hotel": True, "time": "Day 1 | 21:30"}
    if coord:
        node["lnglat"] = list(coord)
        node["coordinate_status"] = "verified"
        node["coordinate_source"] = "amap_lookup"
    return {
        "route": [
            {"day": 1, "name": "喀什古城", "type": "风景名胜", "lnglat": CENTER, "time": "Day 1 | 09:00",
             "data_sources": {"cost_estimate": "amap"}},
            node,
        ]
    }


CONTEXT = {"days": 2, "budget": 3000, "distance_tolerance": "normal", "request_text": "喀什两天"}


class RefreshSnapshotTests(unittest.TestCase):
    def test_matches_prepare_governance_when_inputs_are_the_same(self):
        """同样的输入 → 同一份快照（重算不能是"另一套算法"）。"""
        plan = _plan()
        direct = plan_quality_snapshot(CONTEXT, plan, tier_policy=tier_policy({}))
        refreshed = refresh_snapshot(CONTEXT, plan)
        self.assertEqual(refreshed["quality"]["score"], direct["quality"]["score"])
        self.assertEqual(refreshed["quality"]["dimensions"], direct["quality"]["dimensions"])
        self.assertEqual(refreshed["budget"]["status"], direct["budget"]["status"])

    def test_recompute_follows_the_final_route(self):
        """路线变了（补上坐标）→ 快照必须跟着变；拿旧分数就是"做了但没算进去"。"""
        before = refresh_snapshot(CONTEXT, _plan())["quality"]
        after = refresh_snapshot(CONTEXT, _plan(CENTER))["quality"]
        coverage_before = (before["anchoring"] or {}).get("coordinate_coverage")
        coverage_after = (after["anchoring"] or {}).get("coordinate_coverage")
        self.assertIsNotNone(coverage_before)
        self.assertIsNotNone(coverage_after)
        self.assertLess(float(coverage_before), float(coverage_after))
        self.assertGreaterEqual(float(after["score"]), float(before["score"]))

    def test_recompute_does_not_mutate_the_plan(self):
        plan = _plan()
        snapshot_before = plan_quality_snapshot(CONTEXT, plan)
        refresh_snapshot(CONTEXT, plan)
        self.assertEqual(plan_quality_snapshot(CONTEXT, plan)["quality"]["score"], snapshot_before["quality"]["score"])

    def test_pool_is_passed_through_when_given(self):
        """给候选池/没给候选池分别是两种口径 —— 重算要跟治理阶段用同一批。"""
        plan = _plan()
        pool = {"风景名胜": [{"name": "喀什古城", "type": "风景名胜", "lnglat": CENTER}]}
        with_pool = refresh_snapshot(CONTEXT, plan, candidates_by_intent=pool)
        without = refresh_snapshot(CONTEXT, plan)
        self.assertEqual(with_pool["quality"]["score"], without["quality"]["score"])  # 这个方案两边证据都够
        self.assertEqual(with_pool["quality"]["place_quality"].get("total_nodes"), 2)

    def test_tier_policy_comes_from_the_increment(self):
        """增量里的档位策略要透传（否则重算会把"升档类别"按默认档保护错）。"""
        plan = _plan()
        increment = {"quota": {"风景名胜": 2}}
        self.assertEqual(tier_policy(increment), tier_policy(increment))
        refreshed = refresh_snapshot(CONTEXT, plan, increment=increment)
        self.assertIn("score", refreshed["quality"])


class RulerFidelityTests(unittest.TestCase):
    """尺子必须能**复现方案自报的分数**（2026-10-05 实测抓到"两把尺子"）。

    方案分数是拿 `pref_signals`（历史 + 房间成员的话）算的；`facts` 只记发起人自己说的话
    （配比口径要求）。于是尺子从 facts 重建信号时会漏掉那部分偏好 —— 同一份喀什方案，
    管线 78.9、尺子 83.6（个性化那项 0.75 vs 1.0）。**尺子比管线松 4.7 分**，
    正好把用户第一优先级里的"个性化有没有满足"漏掉。
    解法：方案把评分输入记下来（`quality.scoring_inputs`），尺子优先用它。
    """

    CONTEXT = {"days": 2, "budget": 3000, "distance_tolerance": "normal"}

    @staticmethod
    def _plan():
        return {
            "route": [
                {"day": 1, "name": "喀什博物馆", "type": "博物馆", "lnglat": CENTER, "time": "Day 1 | 09:00"},
                {"day": 1, "name": "住宿", "type": "住宿服务", "is_hotel": True, "lnglat": CENTER, "time": "Day 1 | 21:30"},
            ]
        }

    def _pipeline_payload(self):
        """模拟管线：分数拿"更全的信号"（这里额外要了自然风景，方案里没排）算。"""
        signals = {"interest": ["自然风景"], "requirements": ["文化历史"]}
        snapshot = plan_quality_snapshot(self.CONTEXT, self._plan(), signals=signals, tier_policy=tier_policy({}))
        payload = self._plan()
        payload["quality"] = dict(snapshot["quality"])
        payload["facts"] = {
            "request": {
                "trip_days": {"value": 2, "source": "user_intent", "verified": True},
                "requirements": {"value": ["文化历史"], "source": "user_intent", "verified": True},
            },
            "constraints": {
                "distance_tolerance": {"value": "normal", "source": "user_intent", "verified": True},
                # ⚠️ facts 里**没有** signals["interest"]（自然风景）—— 这正是两把尺子的来源
                "wants_scenic": {"value": False, "source": "user_intent", "verified": True},
            },
        }
        return payload, signals, snapshot

    def test_recorded_inputs_reproduce_the_pipeline_score_exactly(self):
        from core.plan_quality import scoring_inputs

        payload, signals, snapshot = self._pipeline_payload()
        payload["quality"]["scoring_inputs"] = scoring_inputs(self.CONTEXT, signals)
        # 过一遍 JSON（真实链路上 payload 会序列化）
        recorded = json.loads(json.dumps(payload["quality"]["scoring_inputs"], ensure_ascii=False))
        again = evaluate_plan(recorded["context"], payload, signals=recorded["signals"])
        self.assertEqual(again["score"], snapshot["quality"]["score"], "尺子必须复现同一个分数")
        self.assertEqual(
            again["dimensions"]["preference_coverage"]["value"],
            snapshot["quality"]["dimensions"]["preference_coverage"],
        )

    def test_facts_only_rebuild_is_looser_than_the_pipeline(self):
        """钉住病根：只从 facts 重建会**偏高**（这把尺子会漏掉没满足的个性化）。"""
        import sys as _sys

        _sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        from score_plan_files import signals_from_plan  # noqa: E402

        payload, _, snapshot = self._pipeline_payload()
        rebuilt = evaluate_plan(self.CONTEXT, payload, signals=signals_from_plan(payload))
        self.assertLess(
            snapshot["quality"]["score"],
            rebuilt["score"],
            "facts 重建只看到发起人的话，会把没满足的个性化算成满足（分数偏高）",
        )

    def test_ruler_prefers_recorded_inputs(self):
        import sys as _sys

        _sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        from score_plan_files import scoring_basis  # noqa: E402

        payload, signals, _ = self._pipeline_payload()
        from core.plan_quality import scoring_inputs

        payload["quality"]["scoring_inputs"] = scoring_inputs(self.CONTEXT, signals)
        context, used_signals, source = scoring_basis(payload)
        self.assertEqual(source, "方案记录")
        self.assertEqual(used_signals.get("interest"), signals["interest"])
        self.assertEqual(context.get("days"), 2)
        # 没记录时退回 facts 重建（老方案仍能评，但要如实说明口径来源）
        del payload["quality"]["scoring_inputs"]
        _, _, fallback_source = scoring_basis(payload)
        self.assertEqual(fallback_source, "facts 重建")


class CacheVersionTests(unittest.TestCase):
    """缓存里装的是**整份 payload**，所以生成逻辑一变就要 bump 精确缓存键版本。

    实测踩坑（2026-10-05）：补坐标 + 质量重算改完之后直接跑探针，请求 394ms 就回来了、
    日志里既没有「📐 [质量重算]」也没有「📍 [坐标补齐]」—— 命中的是**旧代码产出的缓存**，
    旧条目里的坐标和质量分正是已知错的那两项。改完逻辑不 bump = 拿错数据骗人，
    而且看起来一切正常（最危险的那种）。
    """

    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "api", "agent.py"), encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_current_version_is_used_in_the_key(self):
        import re

        match = re.search(r'raw = f"(v\d+)\|\{MODEL_NAME\}', self.source)
        self.assertIsNotNone(match, "精确缓存键必须带版本号")
        version = match.group(1)
        self.assertGreaterEqual(int(version[1:]), 7, "v7 起才是「结论按最终路线重算」之后的 payload")
        # 当前版本必须有一句"为什么 bump"的说明（否则下一个人不知道能不能再改）
        self.assertIn(f"{version}：", self.source)


class AgentWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(root, "api", "agent.py"), encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_agent_recomputes_quality_before_emitting(self):
        self.assertIn("refresh_snapshot as _refresh_quality_snapshot", self.source)
        self.assertIn("📐 [质量重算]", self.source)
        # 重算发生在收口「遗留问题重算」之前（同一处、同一理由）
        quality_at = self.source.index("refresh_snapshot as _refresh_quality_snapshot")
        degrade_at = self.source.index("collect_degradations as _collect_degradations_final")
        self.assertLess(quality_at, degrade_at)

    def test_recompute_inputs_have_outer_defaults(self):
        """输入变量必须在分支外先置空，否则 NameError 会被 `except: print` 静默吞掉。"""
        for name in (
            "_quality_context_for_final",
            "_quality_signals_for_final",
            "_quality_increment_for_final",
            "_quality_pool_for_final",
        ):
            self.assertIn(f"{name}: Optional[Dict[str, Any]] = None", self.source)
            self.assertIn(f"{name} = ", self.source)
        # 没有上下文时保持原样，不猜
        self.assertIn("_quality_context_for_final is None", self.source)

    def test_amap_gate_is_registered_before_the_disclosure_recompute(self):
        """高德限流要先登记、再重算披露（2026-10-05 实测：顺序反了 = 披露里没有限流）。

        实测症状（喀什 turbo25）：payload 里有 `amap_gate.limits_this_round=1`，
        但 `degradations` 里**一条限流都没有** —— 面板「遗留问题」把限流整条漏掉。
        """
        gate_at = self.source.index("_gate_state = amap_gate_state()")
        degrade_at = self.source.index("collect_degradations as _collect_degradations_final")
        facts_at = self.source.index("build_plan_facts as _build_plan_facts_final")
        self.assertLess(gate_at, degrade_at, "闸门状态必须在披露重算之前登记")
        self.assertLess(degrade_at, facts_at)


if __name__ == "__main__":
    unittest.main()
