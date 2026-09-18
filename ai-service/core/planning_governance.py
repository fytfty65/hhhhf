"""规划治理编排：把"候选池 → 质量门禁 → 兜底/排他"串成一次调用。

为什么单独一层
--------------
`api/agent.py` 是 3000+ 行的协程函数，直接在里头拼装这些逻辑既难测也难读。
这里把**编排**抽出来（可以用假 `fetch` 离线测试），`agent.py` 只负责 await 一次并
把结果挂到 `final_route.payload` 上。

流程
----
1. `plan_quality_snapshot()` 先给出质量/预算/骨架（不需要候选池）；
2. 只有"需要平替或需要换点"时才去取候选池（预算 over/at_risk，或调用方给了排他词），
   **避免每次推演都多打一次数据源**；
3. 有候选池就用它重算一次快照 → `fallback` 里会出现真实的同类替换；
4. 给了 `exclude_terms` 时，额外产出 `exclusions`（剔除+同类替换，缺口如实上报）。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence

from .budget_planner import propose_fallback
from .candidate_index import apply_exclusions
from .increment import enforce_quota, measure_increment
from .plan_quality import plan_quality_snapshot
from .poi_pool import build_candidate_pool, intents_for_plan

FetchFn = Callable[..., Awaitable[Sequence[Mapping[str, Any]]]]

# 用户说"不想去/换掉/别安排"这类话时的触发词（真正解析由 core/increment.parse_increment 完成）
EXCLUSION_HINTS = ("不想去", "不要去", "别安排", "换掉", "换成", "去掉", "删除")


def mentions_exclusion(text: Any) -> bool:
    raw = str(text or "")
    return any(hint in raw for hint in EXCLUSION_HINTS)


async def prepare_governance(
    context: Mapping[str, Any],
    plan: Any,
    signals: Optional[Mapping[str, Any]] = None,
    expectations: Optional[Mapping[str, Any]] = None,
    city: str = "",
    fetch: Optional[FetchFn] = None,
    exclude_terms: Sequence[str] = (),
    limit_per_intent: int = 6,
    request_text: str = "",
    increment: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """返回 `{snapshot, pool_sizes, exclusions, quota, increment, plan, used_pool}`；任何数据源失败都不抛异常。

    `increment` 是 `core/increment.parse_increment()` 的结果：
    - `exclude` → 剔除+同类替换；
    - `quota` → 从候选池补/减节点（真的改变选点）；
    - 结果方案放在返回值的 `plan` 里，调用方据此替换路线（`measure_increment` 给出"改了多少/扰动多少"）。
    """
    increment = increment or {}
    exclude_terms = list(exclude_terms) + list(increment.get("exclude") or [])
    snapshot = plan_quality_snapshot(context, plan, expectations=expectations, signals=signals)

    quota = {k: v for k, v in (increment.get("quota") or {}).items() if isinstance(v, int) and v}
    needs_pool = (
        snapshot["budget"]["status"] in {"over", "at_risk"}
        or bool(exclude_terms)
        or bool(quota)
        or mentions_exclusion(request_text)
    )
    pool: Dict[str, List[Dict[str, Any]]] = {}
    if needs_pool and fetch and city:
        intents = intents_for_plan(plan, extra=[str(item) for item in exclude_terms])
        for intent in quota:
            if intent not in intents:
                intents.append(intent)
        pool = await build_candidate_pool(
            city,
            intents,
            fetch,
            limit_per_intent=limit_per_intent,
            exclude_names=exclude_terms,
        )

    working_plan: Any = plan
    exclusions: Optional[Dict[str, Any]] = None
    if exclude_terms:
        exclusions = apply_exclusions(working_plan, list(exclude_terms), index=pool)
        working_plan = exclusions["plan"]

    quota_result: Optional[Dict[str, Any]] = None
    if quota:
        quota_result = enforce_quota(
            working_plan,
            quota,
            pool=pool,
            context=context,
            max_nodes_per_day=int(context.get("max_nodes_per_day") or 0) or None,
        )
        working_plan = quota_result["plan"]

    if pool:
        # 有候选池 → 用**最终方案**重算，让 fallback 基于真实候选做同类替换
        snapshot = plan_quality_snapshot(
            context,
            working_plan,
            expectations=expectations,
            signals=signals,
            candidates_by_intent=pool,
        )

    increment_metrics = None
    if exclusions or quota_result:
        increment_metrics = measure_increment(plan, working_plan, increment)
        parts = [item for item in ((exclusions or {}).get("disclosure"), (quota_result or {}).get("disclosure")) if item]
        increment_metrics["disclosure"] = "；".join(parts)

    return {
        "snapshot": snapshot,
        "pool_sizes": {intent: len(items) for intent, items in pool.items()},
        "used_pool": bool(pool),
        "exclusions": exclusions,
        "quota": quota_result,
        "increment": increment_metrics,
        "plan": working_plan,
    }


def fallback_with_pool(
    context: Mapping[str, Any],
    plan: Any,
    pool: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """只用已有候选池生成兜底方案（同步、无数据源调用），便于单独复用与测试。"""
    return propose_fallback(context, plan, candidates_by_intent=pool)
