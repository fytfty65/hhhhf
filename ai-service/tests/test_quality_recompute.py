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
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.planning_governance import refresh_snapshot, tier_policy  # noqa: E402
from core.plan_quality import plan_quality_snapshot  # noqa: E402

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
