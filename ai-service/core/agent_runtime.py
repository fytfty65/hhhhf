"""Runtime for independently executed specialist planning agents.

The planner still owns the final decision, but specialist work is now explicit:
each handler receives a validated ``AgentInput`` and returns a structured
``AgentProposal``.  Handlers run concurrently, have individual timeouts, and
cannot silently turn malformed or unsourced output into a successful proposal.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence

from .agent_contracts import AgentInput, AgentProposal, capability_registry, validate_agent_proposal
from .joint_optimization import rank_lodging_options  # 联合优化（P2）：住宿 ↔ 通勤/费用/体力/碳排/预算
from .tool_permissions import audit_tool_calls, filter_tool_calls  # P2：工具权限真实校验

logger = logging.getLogger(__name__)

AgentHandler = Callable[[AgentInput], Awaitable[Mapping[str, Any] | AgentProposal]]


def _evidence(provider: str, field: str, *, estimated: bool = False) -> dict[str, Any]:
    return {
        "field": field,
        "provider": provider or "local-candidate-pool",
        "observed_at": datetime.now(timezone.utc),
        "estimated": estimated,
    }


def _apply_contract_limits(rows: list[dict[str, Any]], hard_constraints: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """按输入契约里的**数量/距离上限**裁剪候选，返回 `(裁剪后, 应用记录)`。

    为什么需要：协商回环里，协调器把裁决回灌给相关智能体后，智能体必须能**真的按裁决重提** ——
    否则"再协商一轮"就是走过场（重提结果一模一样，分歧永远不消）。
    这里实现的都是可判定、可解释的硬限制：
      - `max_far_scenic`：最多保留 N 个"远景区"（`distance_km >= 40` 或 `out_of_town`），
        按距离从近到远保留（远的先让位）；
      - `candidate_cap`：候选总数上限。
    """
    applied: dict[str, Any] = {}
    result = [dict(row) for row in rows if isinstance(row, Mapping)]

    cap = hard_constraints.get("candidate_cap")
    try:
        cap_value = int(cap) if cap is not None else None
    except (TypeError, ValueError):
        cap_value = None
    if cap_value is not None and cap_value >= 0 and len(result) > cap_value:
        dropped_names = [str(row.get("name") or "") for row in result[cap_value:]][:8]
        applied["candidate_cap"] = {
            "limit": cap_value,
            "dropped_count": len(result) - cap_value,
            "dropped": dropped_names,
        }
        result = result[:cap_value]

    max_far = hard_constraints.get("max_far_scenic")
    try:
        far_value = int(max_far) if max_far is not None else None
    except (TypeError, ValueError):
        far_value = None
    if far_value is not None and far_value >= 0:
        def _is_far(row: Mapping[str, Any]) -> bool:
            if bool(row.get("out_of_town")):
                return True
            try:
                return float(row.get("distance_km")) >= 40.0
            except (TypeError, ValueError):
                return False

        far_rows = [row for row in result if _is_far(row)]
        if len(far_rows) > far_value:
            def _distance(row: Mapping[str, Any]) -> float:
                try:
                    return float(row.get("distance_km"))
                except (TypeError, ValueError):
                    return 0.0

            keep_far = {id(row) for row in sorted(far_rows, key=_distance)[:far_value]}
            dropped = [str(row.get("name") or "") for row in far_rows if id(row) not in keep_far]
            result = [row for row in result if not _is_far(row) or id(row) in keep_far]
            applied["max_far_scenic"] = {"limit": far_value, "dropped": dropped[:8], "dropped_count": len(dropped)}

    # 每晚房价上限（预算 vs 房价 的裁决）：只砍**已知超价**的，房价未知的一律保留 ——
    # "不知道多少钱"不等于"太贵"，砍掉就是拿臆测当事实。
    max_price = hard_constraints.get("max_price_per_night")
    try:
        price_cap = float(max_price) if max_price is not None else None
    except (TypeError, ValueError):
        price_cap = None
    if price_cap is not None and price_cap > 0:
        def _nightly(row: Mapping[str, Any]) -> Optional[float]:
            for key in ("nightly_price", "price_per_night", "price", "cost_estimate", "cost"):
                value = row.get(key)
                if value is None or isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    return float(value)
                digits = ""
                for char in str(value):
                    if char.isdigit() or char == ".":
                        digits += char
                    elif digits:
                        break
                try:
                    return float(digits) if digits else None
                except ValueError:
                    continue
            return None

        dropped_prices = [
            (str(row.get("name") or ""), _nightly(row))
            for row in result
            if (_nightly(row) is not None and float(_nightly(row)) > price_cap)
        ]
        unknown = sum(1 for row in result if _nightly(row) is None)
        if dropped_prices:
            result = [
                row for row in result
                if _nightly(row) is None or float(_nightly(row)) <= price_cap
            ]
            applied["max_price_per_night"] = {
                "limit": price_cap,
                "dropped": [name for name, _ in dropped_prices][:8],
                "dropped_count": len(dropped_prices),
                "kept_price_unknown": unknown,
            }
    return result, applied


def pool_handler(agent_id: str, domain: str, *, provider: str, field: str) -> AgentHandler:
    """Create a deterministic adapter for a supplier-backed candidate pool."""

    async def handle(request: AgentInput) -> Mapping[str, Any]:
        candidates = request.context.get("candidates")
        rows = [item for item in candidates if isinstance(item, Mapping)] if isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes)) else []
        rows, applied = _apply_contract_limits(rows, request.hard_constraints or {})
        if not rows:
            return {
                "agent_id": agent_id,
                "domain": domain,
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence(provider, field)],
                "confidence": 0.0,
                "failure": {"code": "candidate_pool_empty", "message": "供应商没有返回可用候选", "retryable": True, "fallback": "official_channel_links"},
                "explanation": "候选池为空，未编造推荐。",
            }
        proposal: dict[str, Any] = {
            "agent_id": agent_id,
            "domain": domain,
            "status": "ok",
            "degraded": False,
            "candidates": [dict(item) for item in rows[:50]],
            "hard_constraint_results": request.hard_constraints,
            "evidence": [_evidence(provider, field, estimated=any(bool(item.get("estimated")) for item in rows))],
            "confidence": 0.75 if rows else 0.0,
            "explanation": f"已从 {provider} 候选池读取 {len(rows)} 条记录。",
        }
        if applied:
            # 按裁决重提时要说清楚"我按要求去掉了什么"，否则用户只看到候选变少
            proposal["applied_constraints"] = applied
            dropped_total = sum(
                int(item.get("dropped_count") or len(item.get("dropped") or []))
                for item in applied.values()
                if isinstance(item, Mapping)
            )
            proposal["explanation"] = f"已按协商裁决重提：从 {provider} 候选里去掉 {dropped_total} 条超出上限的记录。"
        return proposal

    return handle


# ---------------------------------------------------------------------------
# 注册表覆盖度：把"注册了但没接线"显性化
# ---------------------------------------------------------------------------
# 实测（2026-10）发现：`capability_registry()` 声明了 **11 个**智能体，但 `build_pool_runtime()`
# 只装配了 6 个 —— 注册表很丰满、运行时很骨感，而且**没有任何地方会提醒这件事**。
# 于是这里把覆盖度做成可查询的数据（诊断接口/测试都能读），新增注册项如果没被分类会立刻被测出来。
GREEN_AGENT_ID = "green-agent"
HEALTH_AGENT_ID = "health-agent"
EMERGENCY_AGENT_ID = "emergency-agent"
CULTURE_AGENT_ID = "culture-agent"
ACCESSIBILITY_AGENT_ID = "accessibility-agent"

WIRED_AGENT_IDS = (
    "route-planner",
    "transport-agent",
    "lodging-agent",
    "dining-agent",
    "risk-agent",
    "budget-agent",
    GREEN_AGENT_ID,
    HEALTH_AGENT_ID,
    EMERGENCY_AGENT_ID,
    # 2026-10-08：文化智能体接线（数据源＝项目内可信知识库 core/culture_kb.py）
    CULTURE_AGENT_ID,
    # 2026-10-08：无障碍智能体接线（**没有数据时如实说"未核实"+ 待确认清单**，不判断无障碍）
    ACCESSIBILITY_AGENT_ID,
)

# 已声明但**尚未接线**：必须写清原因（测试强制：注册表里出现的新 id 必须在此分类，不能默默漏掉）
DECLARED_NOT_WIRED: Dict[str, str] = {}


def declared_tools(agent_id: str) -> List[str]:
    """读注册表里这个智能体声明的 `allowed_tools`。

    为什么要有它：`build_pool_runtime` 以前给**每个**请求都写死 `allowed_tools=[]`，
    于是注册表里声明的工具权限形同虚设 —— 请求里是空的，"谁能调什么"根本无从校验。
    现在从注册表取，新增智能体只要注册了就自动带上权限。
    """
    for item in capability_registry():
        if item.agent_id == agent_id:
            return list(item.allowed_tools or [])
    return []


def registry_coverage() -> Dict[str, Any]:
    """声明 vs 实接：覆盖度与未接原因（供诊断接口与测试使用）。"""
    declared = [item.agent_id for item in capability_registry()]
    wired = [agent_id for agent_id in declared if agent_id in WIRED_AGENT_IDS]
    pending = [agent_id for agent_id in declared if agent_id not in WIRED_AGENT_IDS]
    return {
        "declared": declared,
        "declared_count": len(declared),
        "wired": wired,
        "wired_count": len(wired),
        "not_wired": pending,
        "coverage_ratio": round(len(wired) / len(declared), 3) if declared else 0.0,
        "reasons": {agent_id: DECLARED_NOT_WIRED.get(agent_id, "未分类（需要在 DECLARED_NOT_WIRED 里说明原因）") for agent_id in pending},
    }


def green_handler() -> AgentHandler:
    """绿色出行智能体：比较候选交通方式的碳排，给出可接受的低碳替代项。

    诚实口径（与项目一贯要求一致）：
    - 碳排是**方法学估算**：证据里必须 `estimated=True`，绝不冒充实测值；
    - 没有可用交通数据 / 方法学不可用时 → **degraded + FailureInfo**（fallback=methodology_estimate），
      不编造碳排数字；
    - 只在能算出碳排的候选之间比较；缺距离的候选直接跳过，而不是拿 0 当默认值。
    """

    async def handle(request: AgentInput) -> Mapping[str, Any]:
        options = request.context.get("transport_options") or request.context.get("candidates") or []
        rows = [item for item in options if isinstance(item, Mapping)] if isinstance(options, Sequence) and not isinstance(options, (str, bytes)) else []
        if not rows:
            return {
                "agent_id": GREEN_AGENT_ID,
                "domain": "sustainability",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("carbon-methodology", "carbon_kg", estimated=True)],
                "confidence": 0.0,
                "failure": {
                    "code": "transport_options_empty",
                    "message": "没有可比较的交通选项，无法给出低碳替代项",
                    "retryable": True,
                    "fallback": "methodology_estimate",
                },
                "explanation": "缺少交通数据，未编造碳排结论。",
            }
        try:
            from api.carbon_service import compute_footprint  # 懒导入：避免 core ↔ api 的导入环
        except Exception as exc:  # 方法学不可用也要如实说
            return {
                "agent_id": GREEN_AGENT_ID,
                "domain": "sustainability",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("carbon-methodology", "carbon_kg", estimated=True)],
                "confidence": 0.0,
                "failure": {
                    "code": "methodology_unavailable",
                    "message": f"碳排方法学不可用: {type(exc).__name__}",
                    "retryable": True,
                    "fallback": "methodology_estimate",
                },
                "explanation": "无法加载碳排方法学，未给出低碳建议。",
            }

        scored: list[dict[str, Any]] = []
        for item in rows:
            raw_distance = item.get("distance_km", item.get("distance"))
            try:
                distance = float(raw_distance)
            except (TypeError, ValueError):
                continue  # 缺距离就跳过：不拿 0 当默认值（0 公里意味着"零碳排"，那是假的）
            if distance <= 0:
                continue
            mode = str(item.get("mode") or "transit")
            try:
                footprint = compute_footprint([{"distance_km": distance, "mode": mode}])
                carbon = float(footprint.get("total_kg") or 0.0)
            except Exception:
                continue
            scored.append(
                {
                    "mode": mode,
                    "distance_km": distance,
                    "carbon_kg": round(carbon, 3),
                    "duration_minutes": item.get("duration_minutes"),
                }
            )

        if not scored:
            return {
                "agent_id": GREEN_AGENT_ID,
                "domain": "sustainability",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("carbon-methodology", "carbon_kg", estimated=True)],
                "confidence": 0.0,
                "failure": {
                    "code": "no_scorable_transport_option",
                    "message": "候选交通方式缺少可计算的里程，无法比较碳排",
                    "retryable": False,
                    "fallback": "methodology_estimate",
                },
                "explanation": "候选缺少里程字段，未编造碳排比较。",
            }

        best = min(scored, key=lambda item: item["carbon_kg"])
        worst = max(scored, key=lambda item: item["carbon_kg"])
        reduction = 0.0 if worst["carbon_kg"] <= 0 else round(1 - best["carbon_kg"] / worst["carbon_kg"], 3)
        return {
            "agent_id": GREEN_AGENT_ID,
            "domain": "sustainability",
            "status": "ok",
            "degraded": False,
            # 只把最优项作为候选：低碳是"提案"，是否采纳由协调器按时间/预算权衡
            "candidates": [best],
            "hard_constraint_results": request.hard_constraints,
            "evidence": [_evidence("carbon-methodology", "carbon_kg", estimated=True)],
            "confidence": 0.6,
            "explanation": (
                f"比较 {len(scored)} 种交通方式：最低 {best['mode']} {best['carbon_kg']}kg、"
                f"最高 {worst['mode']} {worst['carbon_kg']}kg，最多可减 {int(reduction * 100)}%（方法学估算）。"
            ),
        }

    return handle


def culture_handler() -> AgentHandler:
    """文化智能体：为文化节点提供**有出处**的背景、礼仪与建议停留时间。

    注册表定义：tools=[knowledge.official.search]，timeout=5000，
    fallback=omit_unsourced_fact，metrics=[citation_coverage]。

    数据源：**只**用项目内的人工维护知识库 `core/culture_kb.py`
    （用户在路线图上确认过：先只用项目内知识库，不新增抓取）。
    这么选的理由：文化背景错了比没有更糟，而这类内容没有可机读的官方结构化数据。

    诚实口径：
    - 查得到条目 → ok，候选带 background/etiquette/recommended_stay 与 sources；
      来源类型是 `knowledge_base`（→ 前端显示"估算"，**不冒充已核实**）；
    - **查不到条目 → degraded + omit_unsourced_fact**，明确说"知识库里没有，未提供背景说明"，
      绝不拿模型的世界知识补一段"一般来说……"；
    - 一个文化节点都没有 → 一样 degraded（没有可说的就不说）。
    """

    async def handle(request: AgentInput) -> Mapping[str, Any]:
        from core.culture_kb import (
            KB_REVIEWED_AT,
            KB_VERSION,
            culture_entry_for,
            culture_fact,
            unsourced_note,
        )

        context = request.context or {}
        city = str(context.get("city") or "")
        raw_nodes = context.get("cultural_nodes") or context.get("nodes") or []
        nodes = (
            [item for item in raw_nodes if isinstance(item, Mapping)]
            if isinstance(raw_nodes, Sequence) and not isinstance(raw_nodes, (str, bytes))
            else []
        )
        if not nodes:
            return {
                "agent_id": CULTURE_AGENT_ID,
                "domain": "culture",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("culture-kb", "background", estimated=True)],
                "confidence": 0.0,
                "failure": {
                    "code": "no_cultural_node",
                    "message": "这份行程里没有可标注的文化节点",
                    "retryable": False,
                    "fallback": "omit_unsourced_fact",
                },
                "explanation": "没有文化节点，未编造文化背景。",
            }

        candidates: List[Dict[str, Any]] = []
        missing: List[str] = []
        for node in nodes:
            name = str(node.get("name") or node.get("location") or "").strip()
            if not name:
                continue
            entry = culture_entry_for(name, city or node.get("city") or "")
            if entry:
                candidates.append(culture_fact(entry))
            else:
                # 查不到就**如实留白**（仍然作为候选返回，让协调器知道"这个点没有背景可讲"）
                candidates.append(unsourced_note(name))
                missing.append(name)

        if not candidates:
            return {
                "agent_id": CULTURE_AGENT_ID,
                "domain": "culture",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("culture-kb", "background", estimated=True)],
                "confidence": 0.0,
                "failure": {
                    "code": "no_named_node",
                    "message": "文化节点缺少名称，无法查知识库",
                    "retryable": False,
                    "fallback": "omit_unsourced_fact",
                },
                "explanation": "节点没有名字，未编造文化背景。",
            }

        sourced = [item for item in candidates if item.get("sources")]
        coverage = round(len(sourced) / len(candidates), 3)
        explanation = f"知识库命中 {len(sourced)}/{len(candidates)} 个文化节点（{KB_VERSION}，复核于 {KB_REVIEWED_AT}）"
        if missing:
            explanation += f"；未命中：{'、'.join(missing[:3])}（未提供背景说明）"

        return {
            "agent_id": CULTURE_AGENT_ID,
            "domain": "culture",
            "status": "ok",
            "degraded": False,
            "candidates": candidates,
            "hard_constraint_results": request.hard_constraints,
            # 知识库不是一手来源：证据标 estimated=True，不冒充"可核实"
            "evidence": [_evidence("culture-kb", "background", estimated=True)],
            "confidence": round(0.6 * coverage, 3) if coverage else 0.0,
            "applied_constraints": {"kb_version": KB_VERSION, "kb_reviewed_at": KB_REVIEWED_AT},
            "explanation": explanation,
        }

    return handle


def accessibility_handler() -> AgentHandler:
    """无障碍智能体：**核验**无障碍入口、电梯、坡度与缓冲时间。

    注册表定义：tools=[accessibility.official.search, amap.route.plan]，timeout=5000，
    fallback=mark_unverified，metrics=[verified_facility_coverage]。

    ⚠️ 这个智能体的重点不是"给出无障碍结论"，而是**不给出没有依据的结论**。
    用户明确要求（2026-10-08）：
      · 没有真实数据时**不要**判断"完全无障碍"；
      · 输出「无障碍信息：未核实」+ 一份**建议确认清单**
        （入口坡度、电梯、卫生间、接驳车）；
      · 这比直接删掉无障碍能力更好：保留了架构能力，同时不虚构事实。

    诚实口径：
    - 没有任何可核实的无障碍字段 → 候选仍带 `verified: False` +
      `facilities: {坡度/电梯/卫生间/接驳车: unknown}` + `checklist`（该问什么）；
    - 行程里有行动不便成员时，**额外**给出"需提前确认"的提醒（但不假装已经确认）；
    - 将来接入官方无障碍数据时，只要把 `accessibility_facts` 传进来即可自动升级为已核实。
    """

    #: 现场/电话最该确认的四项（用户点名的）
    CHECKLIST = (
        "入口坡度与台阶（有无坡道/无障碍通道）",
        "电梯（是否有、是否可用、轿厢尺寸）",
        "无障碍卫生间（位置与开放情况）",
        "接驳车/区间车（能否放轮椅、是否需预约）",
    )

    async def handle(request: AgentInput) -> Mapping[str, Any]:
        context = request.context or {}
        raw_nodes = context.get("accessibility_nodes") or context.get("nodes") or []
        nodes = (
            [item for item in raw_nodes if isinstance(item, Mapping)]
            if isinstance(raw_nodes, Sequence) and not isinstance(raw_nodes, (str, bytes))
            else []
        )
        facts = context.get("accessibility_facts")
        facts = facts if isinstance(facts, Mapping) else {}
        mobility = bool(context.get("mobility_need"))

        if not nodes:
            return {
                "agent_id": ACCESSIBILITY_AGENT_ID,
                "domain": "accessibility",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("accessibility-checklist", "facilities", estimated=False)],
                "confidence": 0.0,
                "failure": {
                    "code": "no_accessibility_node",
                    "message": "没有需要核验无障碍信息的节点",
                    "retryable": False,
                    "fallback": "mark_unverified",
                },
                "explanation": "没有可核验的节点，未做无障碍判断。",
            }

        candidates: List[Dict[str, Any]] = []
        verified_count = 0
        for node in nodes:
            name = str(node.get("name") or node.get("location") or "").strip()
            if not name:
                continue
            known = facts.get(name) if isinstance(facts.get(name), Mapping) else {}
            facilities: Dict[str, str] = {}
            for facility in ("入口坡度", "电梯", "无障碍卫生间", "接驳车"):
                raw = known.get(facility) if known else None
                text = str(raw).strip() if raw is not None else ""
                # 只有真正拿到值才算 known，否则一律 unknown（**绝不默认"有"**）
                facilities[facility] = text if text else "unknown"
            verified = any(value != "unknown" for value in facilities.values())
            if verified:
                verified_count += 1
            candidates.append(
                {
                    "name": name,
                    "verified": verified,
                    "facilities": facilities,
                    "checklist": [] if verified else list(CHECKLIST),
                    "status_text": "无障碍信息：已核实" if verified else "无障碍信息：未核实",
                    "source_type": "official_page" if verified else "none",
                    "note": (
                        ""
                        if verified
                        else "本机没有该点的官方无障碍资料，**不判断是否无障碍**；"
                        "请按清单向景区确认"
                    ),
                }
            )

        if not candidates:
            return {
                "agent_id": ACCESSIBILITY_AGENT_ID,
                "domain": "accessibility",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("accessibility-checklist", "facilities", estimated=False)],
                "confidence": 0.0,
                "failure": {
                    "code": "no_named_node",
                    "message": "节点缺少名称，无法核验无障碍信息",
                    "retryable": False,
                    "fallback": "mark_unverified",
                },
                "explanation": "节点没有名字，未做无障碍判断。",
            }

        coverage = round(verified_count / len(candidates), 3)
        all_unverified = verified_count == 0
        explanation = (
            f"核验 {len(candidates)} 个节点：已核实 {verified_count} 个、"
            f"未核实 {len(candidates) - verified_count} 个；未核实的不做无障碍结论"
        )
        if mobility:
            explanation += "；本行程有行动不便成员，建议出发前按清单逐项确认"
        return {
            "agent_id": ACCESSIBILITY_AGENT_ID,
            "domain": "accessibility",
            # 全都没核实仍然是 ok 的提案（它**如实说了未核实**，不是失败），但降置信度
            "status": "ok",
            "degraded": bool(all_unverified),
            "candidates": candidates,
            "hard_constraint_results": request.hard_constraints,
            "evidence": [_evidence("accessibility-checklist", "facilities", estimated=False)],
            "confidence": round(coverage, 3),
            "applied_constraints": {
                "checklist": list(CHECKLIST),
                "mobility_need": mobility,
                "policy": "no_unverified_accessibility_claim",
            },
            "explanation": explanation,
        }

    return handle


def health_handler() -> AgentHandler:
    """健康 / 体力智能体：按成员画像与天气给出**每日强度上限与休息缓冲**建议。

    注册表定义：tools=[profile.features.read, weather.snapshot]，timeout=3000，
    fallback=conservative_pace，metrics=[pace_constraint_pass_rate]。

    诚实口径：
    - 这是**保守的顾问型建议**（不是医学结论），证据标 `estimated=True`；
    - 既没有画像约束、也拿不到每日节点数 → degraded + FailureInfo（fallback=conservative_pace），
      不猜"你体力很好"；
    - 只在有依据时下调强度，且把**依据写进 explanation**，便于用户核对。
    """

    async def handle(request: AgentInput) -> Mapping[str, Any]:
        context = request.context or {}
        profile = context.get("profile") if isinstance(context.get("profile"), Mapping) else {}
        weather = context.get("weather") if isinstance(context.get("weather"), Mapping) else {}
        raw_daily = context.get("daily_nodes")
        try:
            daily_nodes = int(raw_daily) if raw_daily is not None else None
        except (TypeError, ValueError):
            daily_nodes = None
        constraints = [str(item) for item in (profile.get("health_constraints") or []) if str(item).strip()]
        condition = str(weather.get("condition") or "")

        if daily_nodes is None and not constraints:
            return {
                "agent_id": HEALTH_AGENT_ID,
                "domain": "health",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("health-methodology", "daily_intensity", estimated=True)],
                "confidence": 0.0,
                "failure": {
                    "code": "insufficient_profile_and_schedule",
                    "message": "既没有成员健康约束，也没有每日节点数，无法给出强度建议",
                    "retryable": True,
                    "fallback": "conservative_pace",
                },
                "explanation": "缺依据，未凭空判断体力。",
            }

        reasons: list[str] = []
        limit = 5
        if daily_nodes is not None:
            baseline = min(6, max(1, daily_nodes))
            limit = baseline
            if daily_nodes > 6:
                limit = 5
                reasons.append(f"每日 {daily_nodes} 个节点偏密，建议压到 {limit} 个并留出休息")
        if condition:
            for marker, extra in (("雨", 1), ("雪", 1), ("高温", 1), ("大风", 1)):
                if marker in condition:
                    limit = max(1, limit - extra)
                    reasons.append(f"天气含「{marker}」，再降 {extra} 个节点并增加室内替代")
                    break
        if constraints:
            limit = max(1, min(limit, 4))
            reasons.append("成员存在健康约束（" + "、".join(constraints[:3]) + "），按保守节奏安排")

        candidate = {
            "daily_node_limit": limit,
            "rest_buffer_minutes": 30 if limit >= 4 else 45,
            "advice": "；".join(reasons) if reasons else "维持常规节奏",
            "constraints_considered": constraints,
        }
        return {
            "agent_id": HEALTH_AGENT_ID,
            "domain": "health",
            "status": "ok",
            "degraded": False,
            "candidates": [candidate],
            "hard_constraint_results": request.hard_constraints,
            "evidence": [_evidence("health-methodology", "daily_intensity", estimated=True)],
            "confidence": 0.55,
            "explanation": "保守顾问建议（非医学结论）：" + (candidate["advice"] or "维持常规节奏"),
        }

    return handle


def replan_scope(route: Sequence[Mapping[str, Any]], risk_event: Mapping[str, Any]) -> Dict[str, Any]:
    """最小扰动重规划：算出**受影响节点 / 保留节点 / 保留率**。

    这是"旅中闭环"的核心逻辑，做成纯函数以便离线单测（风险场景很难在真实数据上复现）。

    诚实与最小扰动原则：
    - 风险事件**必须给出范围**（`affected_nodes` 或 `affected_days`）。两者都没有 → `scope_unknown`，
      绝不"整条路线重生成"（那是最大的扰动，也是最容易掩盖问题的做法）；
    - 点名了具体节点时**只影响这些节点**，同一天的其它节点照常保留；
    - 保留率 = 保留节点 / 全部节点，用于验收"未受影响节点保留率 ≥ 70%"。
    """
    nodes = [dict(item) for item in route if isinstance(item, Mapping)]
    total = len(nodes)
    if total == 0:
        return {"status": "empty_route", "affected": [], "preserved": [], "preserved_ratio": 1.0, "action": None}

    raw_nodes = risk_event.get("affected_nodes") if isinstance(risk_event, Mapping) else None
    raw_days = risk_event.get("affected_days") if isinstance(risk_event, Mapping) else None
    named = {_normalize_name(item) for item in (raw_nodes or []) if str(item).strip()}
    day_set = set()
    for item in raw_days or []:
        try:
            day_set.add(int(item))
        except (TypeError, ValueError):
            continue

    if not named and not day_set:
        return {
            "status": "scope_unknown",
            "affected": [],
            "preserved": [node.get("name") for node in nodes],
            "preserved_ratio": 1.0,
            "action": None,
            "message": "风险事件没有给出受影响范围，拒绝整条路线重生成",
        }

    affected: List[Dict[str, Any]] = []
    preserved: List[str] = []
    for node in nodes:
        name_hit = _normalize_name(node.get("name")) in named if named else False
        day_hit = False
        if day_set and not named:
            try:
                day_hit = int(node.get("day")) in day_set
            except (TypeError, ValueError):
                day_hit = False
        if name_hit or day_hit:
            affected.append(node)
        else:
            preserved.append(str(node.get("name") or ""))

    ratio = round(len(preserved) / total, 3) if total else 1.0
    action = str(risk_event.get("recommended_action") or "").strip()
    if not action:
        # 没给动作就按影响范围推断（只影响少量节点 → 局部替换；整天受影响 → 顺延）
        action = "shift_day" if (day_set and not named) else "replace_affected_nodes"
    return {
        "status": "scoped",
        "affected": affected,
        "affected_names": [str(node.get("name") or "") for node in affected],
        "preserved": preserved,
        "preserved_ratio": ratio,
        "action": action,
    }


def _normalize_name(value: Any) -> str:
    return "".join(str(value or "").split()).lower()


def emergency_handler() -> AgentHandler:
    """应急智能体：把风险事件转成**最小扰动**的重规划动作提案。

    注册表定义：tools=[risk.snapshot, planning.replan]，timeout=5000，
    fallback=manual_confirmation，metrics=[preserved_node_ratio, replan_latency]。

    诚实口径：
    - 只在风险事件给出范围时才产出动作；范围未知 → degraded + FailureInfo，**绝不整条重生成**；
    - 提案里必须带上保留率与保留节点（用户与验收标准都要看这个数）；
    - 强调"需用户确认后才写入"（fallback=manual_confirmation）。
    """

    async def handle(request: AgentInput) -> Mapping[str, Any]:
        context = request.context or {}
        route = context.get("route") or []
        event = context.get("risk_event") if isinstance(context.get("risk_event"), Mapping) else None
        if not isinstance(route, Sequence) or isinstance(route, (str, bytes)) or not route:
            return {
                "agent_id": EMERGENCY_AGENT_ID,
                "domain": "emergency",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("risk.snapshot", "affected_nodes")],
                "confidence": 0.0,
                "failure": {
                    "code": "route_missing",
                    "message": "没有当前路线，无法判断受影响节点",
                    "retryable": True,
                    "fallback": "manual_confirmation",
                },
                "explanation": "缺当前路线，未做任何重规划。",
            }
        if not event:
            return {
                "agent_id": EMERGENCY_AGENT_ID,
                "domain": "emergency",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("risk.snapshot", "risk_event")],
                "confidence": 0.0,
                "failure": {
                    "code": "risk_event_missing",
                    "message": "没有风险事件，不触发重规划",
                    "retryable": True,
                    "fallback": "manual_confirmation",
                },
                "explanation": "没有风险事件时不动作（避免无谓扰动）。",
            }

        scope = replan_scope(route, event)
        if scope["status"] != "scoped":
            return {
                "agent_id": EMERGENCY_AGENT_ID,
                "domain": "emergency",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("risk.snapshot", "affected_nodes")],
                "confidence": 0.0,
                "failure": {
                    "code": scope["status"],
                    "message": scope.get("message") or "风险事件范围不可判定",
                    "retryable": False,
                    "fallback": "manual_confirmation",
                },
                "explanation": "范围不可判定时拒绝整条路线重生成，交人工确认。",
            }

        candidate = {
            "action": scope["action"],
            "affected_nodes": scope["affected_names"],
            "preserved_nodes": scope["preserved"],
            "preserved_ratio": scope["preserved_ratio"],
            "requires_user_confirmation": True,
            "risk": {
                "event": event.get("event") or event.get("type"),
                "severity": event.get("severity"),
            },
        }
        return {
            "agent_id": EMERGENCY_AGENT_ID,
            "domain": "emergency",
            "status": "ok",
            "degraded": False,
            "candidates": [candidate],
            "hard_constraint_results": request.hard_constraints,
            "evidence": [_evidence("risk.snapshot", "affected_nodes")],
            "confidence": 0.7,
            "explanation": (
                f"最小扰动重规划：受影响 {len(scope['affected_names'])} 个节点、"
                f"保留 {len(scope['preserved'])} 个（保留率 {int(scope['preserved_ratio'] * 100)}%），"
                f"动作 {scope['action']}，需用户确认后写入。"
            ),
        }

    return handle


def lodging_handler(
    *,
    route: Sequence[Mapping[str, Any]] = (),
    nights: int | None = None,
    budget_total: float | None = None,
) -> AgentHandler:
    """住宿智能体（**联合评估版**）：不再只看房价，而是把通勤/费用/体力/碳排/可玩节点一起算。

    与通用 pool_handler 的区别：候选会经过 `core.joint_optimization.rank_lodging_options`，
    每个保留的候选带上 `joint_evaluation`（通勤分钟、通勤费用、时间折价、换酒店损失、总成本、
    未知分项、可玩节点、体力、碳排、预算余额），并在 explanation 里说明取舍。

    诚实口径：
    - **没有路线**时不做通勤联合评估，如实说明"仅按候选池给出"，不假装算过；
    - 缺坐标的天会出现在 `unmeasured_days`，不臆造零通勤；
    - 联合评估失败 → degraded + FailureInfo（fallback=area_level_recommendation），不悄悄退回假结果。
    """

    async def handle(request: AgentInput) -> Mapping[str, Any]:
        candidates = request.context.get("candidates")
        rows = [dict(item) for item in candidates if isinstance(item, Mapping)] if isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes)) else []
        # 协商裁决也作用在住宿侧（例如"每晚不超过 ¥X"）：只砍已知超价的，房价未知的保留
        rows, applied = _apply_contract_limits(rows, request.hard_constraints or {})
        if not rows:
            return {
                "agent_id": "lodging-agent",
                "domain": "lodging",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("lodging.provider", "lodging_candidates")],
                "confidence": 0.0,
                "failure": {"code": "candidate_pool_empty", "message": "供应商没有返回可用住宿候选", "retryable": True, "fallback": "area_level_recommendation"},
                "explanation": "候选池为空，未编造推荐。",
            }
        if not route:
            proposal = {
                "agent_id": "lodging-agent",
                "domain": "lodging",
                "status": "ok",
                "degraded": False,
                "candidates": rows[:50],
                "hard_constraint_results": request.hard_constraints,
                "evidence": [_evidence("lodging.provider", "lodging_candidates")],
                "confidence": 0.6,
                "explanation": f"已读取 {len(rows)} 个住宿候选；**没有路线，未做通勤联合评估**（仅按候选池给出）。",
            }
            if applied:
                proposal["applied_constraints"] = applied
                proposal["explanation"] = (
                    f"已按协商裁决重提：住宿候选从 {len(applied.get('max_price_per_night', {}).get('dropped') or []) + len(rows)} "
                    f"条里去掉超价记录，剩 {len(rows)} 条；没有路线，未做通勤联合评估。"
                )
            return proposal

        try:
            ranking = rank_lodging_options(rows, route, nights=nights, budget_total=budget_total)
        except Exception as exc:
            return {
                "agent_id": "lodging-agent",
                "domain": "lodging",
                "status": "degraded",
                "degraded": True,
                "candidates": [],
                "evidence": [_evidence("joint-optimization", "commute_minutes_total", estimated=True)],
                "confidence": 0.0,
                "failure": {"code": "joint_evaluation_failed", "message": f"联合评估失败: {type(exc).__name__}", "retryable": True, "fallback": "area_level_recommendation"},
                "explanation": "联合评估不可用，未给出住宿建议。",
            }

        by_name = {str(item.get("name") or ""): item for item in rows}
        kept = ranking["pareto"] or ranking["evaluated"]
        enriched: list[dict[str, Any]] = []
        for evaluated in kept:
            base = by_name.get(evaluated["lodging_name"])
            if base is None:
                continue
            merged = dict(base)
            merged["joint_evaluation"] = {
                key: evaluated[key]
                for key in (
                    "lodging_total", "lodging_price_unknown",
                    "commute_minutes_total", "commute_cost", "commute_time_value", "switch_penalty",
                    "total_cost_known", "unknown_components", "planned_nodes", "playable_nodes",
                    "unmeasured_days", "fatigue_index", "carbon_kg", "carbon_source",
                    "budget_remaining", "estimated",
                )
            }
            enriched.append(merged)

        tradeoffs = "；".join(ranking["tradeoffs"])
        proposal = {
            "agent_id": "lodging-agent",
            "domain": "lodging",
            "status": "ok",
            "degraded": False,
            "candidates": enriched,
            "hard_constraint_results": request.hard_constraints,
            "evidence": [
                _evidence("lodging.provider", "lodging_candidates"),
                _evidence("joint-optimization", "commute_minutes_total", estimated=True),
            ],
            "confidence": 0.7,
            "explanation": (
                f"对 {len(rows)} 个住宿候选做了通勤联合评估，保留 {len(enriched)} 个（Pareto）。{tradeoffs}"
                f"（成本只统计已知分项；{ranking['note']}）"
            ),
        }
        if applied:
            proposal["applied_constraints"] = applied
        return proposal

    return handle


class SpecialistRuntime:
    def __init__(self, handlers: Mapping[str, AgentHandler]):
        self.handlers = dict(handlers)
        self.capabilities = {item.agent_id: item for item in capability_registry()}

    async def _run_one(self, request: AgentInput) -> AgentProposal:
        capability = self.capabilities.get(request.agent_id)
        handler = self.handlers.get(request.agent_id)
        started = time.perf_counter()
        if capability is None:
            return AgentProposal(
                agent_id=request.agent_id, domain=request.domain, status="failed",
                failure={"code": "agent_not_registered", "message": "智能体未注册", "retryable": False},
                degraded=False, latency_ms=0,
            )
        if handler is None:
            return AgentProposal(
                agent_id=request.agent_id, domain=request.domain, status="degraded",
                failure={"code": "handler_unavailable", "message": capability.fallback, "retryable": True, "fallback": capability.fallback},
                degraded=True, evidence=[_evidence("runtime", "handler")], latency_ms=0,
            )
        try:
            raw = await asyncio.wait_for(handler(request), timeout=capability.timeout_ms / 1000)
            payload = raw.model_dump(mode="python") if isinstance(raw, AgentProposal) else dict(raw)
            payload["latency_ms"] = max(0, round((time.perf_counter() - started) * 1000))
            # 👑 输入契约回带：每个提案都要写明"我是基于哪一版事实提的"。
            #    实测（2026-10-04）：只有候选池类 handler 会回带 `hard_constraint_results`，
            #    绿色出行/健康/应急/预算这几个提案里根本没有 → 对齐检查一上就点名了 4 个。
            #    与其逐个 handler 改（以后新增还会漏），不如在运行时出口统一盖章：
            #    盖的就是 handler 收到的 request.hard_constraints —— 这是事实，不是补写。
            if request.hard_constraints:
                existing = payload.get("hard_constraint_results")
                if not isinstance(existing, Mapping) or not existing:
                    payload["hard_constraint_results"] = dict(request.hard_constraints)
                else:
                    merged = dict(request.hard_constraints)
                    merged.update({str(key): value for key, value in existing.items()})
                    payload["hard_constraint_results"] = merged
            # 👑 P2 工具权限：在**出口**统一校验，不逐个 handler 改（新增的还会漏）。
            #    注册表声明是能力上限，请求里的 allowed_tools 是本次实际授予，两者都要满足。
            #    被拒的调用不许静默丢弃：记进 denied_tool_calls，让日志与诊断接口看得见。
            raw_calls = payload.get("tool_calls") or []
            if isinstance(raw_calls, Sequence) and not isinstance(raw_calls, (str, bytes)):
                verdict = filter_tool_calls(
                    agent_id=request.agent_id,
                    tool_calls=[item for item in raw_calls if isinstance(item, Mapping)],
                    declared_tools=declared_tools(request.agent_id),
                    granted_tools=request.allowed_tools,
                )
                payload["tool_calls"] = verdict["allowed"]
                if verdict["denied"]:
                    payload["denied_tool_calls"] = verdict["denied"]
                    logger.warning(
                        "🚫 [工具权限] %s 的 %d 次调用被拒绝：%s",
                        request.agent_id,
                        len(verdict["denied"]),
                        "; ".join(
                            f"{item.get('tool')}({item.get('reason')})" for item in verdict["denied"]
                        ),
                    )
            return validate_agent_proposal(payload)
        except asyncio.TimeoutError:
            return AgentProposal(
                agent_id=request.agent_id, domain=request.domain, status="degraded",
                failure={"code": "agent_timeout", "message": f"超过 {capability.timeout_ms}ms 超时预算", "retryable": True, "fallback": capability.fallback},
                degraded=True, evidence=[_evidence("runtime", "timeout")], latency_ms=capability.timeout_ms,
            )
        except Exception as exc:
            return AgentProposal(
                agent_id=request.agent_id, domain=request.domain, status="failed",
                failure={"code": "agent_failed", "message": str(exc)[:300], "retryable": True, "fallback": capability.fallback},
                degraded=False, evidence=[_evidence("runtime", "failure")],
                latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
            )

    async def execute(self, requests: Sequence[AgentInput]) -> Dict[str, Any]:
        proposals = await asyncio.gather(*(self._run_one(request) for request in requests))
        rows = [proposal.model_dump(mode="json") for proposal in proposals]
        # P2：把被拒的工具调用汇总出来 —— 权限控制外面看不见等于没有
        audit = audit_tool_calls(rows)
        result = {
            "status": "ok" if all(item.status != "failed" for item in proposals) else "degraded",
            "proposals": rows,
            "completed": len(rows),
            "failed": sum(item.status == "failed" for item in proposals),
            "degraded": sum(item.status == "degraded" for item in proposals),
            "parallel": True,
            "tool_permission_audit": audit,
        }
        if audit["denied_count"]:
            logger.warning(
                "🚫 [工具权限] 本次执行有 %d 次越权调用被拒绝", audit["denied_count"]
            )
        return result


def build_pool_runtime(
    *,
    attractions: Sequence[Mapping[str, Any]] = (),
    transport: Sequence[Mapping[str, Any]] = (),
    lodging: Sequence[Mapping[str, Any]] = (),
    dining: Sequence[Mapping[str, Any]] = (),
    risk: Sequence[Mapping[str, Any]] = (),
    budget: Sequence[Mapping[str, Any]] = (),
    transport_options: Sequence[Mapping[str, Any]] = (),
    profile: Mapping[str, Any] | None = None,
    weather: Mapping[str, Any] | None = None,
    daily_nodes: int | None = None,
    route: Sequence[Mapping[str, Any]] = (),
    risk_event: Mapping[str, Any] | None = None,
    nights: int | None = None,
    budget_total: float | None = None,
    facts: Mapping[str, Any] | None = None,
    cultural_nodes: Sequence[Mapping[str, Any]] = (),
    city: str = "",
    accessibility_facts: Mapping[str, Any] | None = None,
    mobility_need: bool = False,
) -> tuple[SpecialistRuntime, list[AgentInput]]:
    """装配专业智能体运行时。

    `facts` 是这一轮的**统一事实快照**（core/plan_facts.build_request_facts）：
    它把"用户说了什么、有什么约束"变成每个智能体输入契约里的硬约束 ——
    此前这里是空的，每个智能体只看得到自己的候选池，谁也不知道
    "用户不要博物馆""他不介意跑远"，所谓协同其实只是各算各的。
    现在每个提案都会带着 `facts_version`（来自输入契约），协调器据此校验
    "大家是不是基于同一版事实"。
    """
    hard_constraints: dict[str, Any] = {}
    soft_preferences: dict[str, Any] = {}
    if isinstance(facts, Mapping) and facts:
        from core.plan_facts import facts_version as _facts_version

        constraints = facts.get("constraints") if isinstance(facts.get("constraints"), Mapping) else {}
        for key, entry in constraints.items():
            value = entry.get("value") if isinstance(entry, Mapping) else entry
            if value is None:
                continue
            hard_constraints[str(key)] = value
        decisions = facts.get("decisions") if isinstance(facts.get("decisions"), Mapping) else {}
        targets_entry = decisions.get("composition_targets") if isinstance(decisions, Mapping) else None
        targets = targets_entry.get("value") if isinstance(targets_entry, Mapping) else None
        if isinstance(targets, Mapping):
            soft_preferences["composition_targets"] = dict(targets)
        hard_constraints["facts_version"] = str(facts.get("facts_version") or _facts_version(facts))

    sources = {
        "route-planner": ("itinerary", attractions, "amap", "attraction_candidates"),
        "transport-agent": ("transport", transport, "transport.provider", "transport_candidates"),
        "lodging-agent": ("lodging", lodging, "lodging.provider", "lodging_candidates"),
        "dining-agent": ("dining", dining, "amap", "dining_candidates"),
        "risk-agent": ("risk", risk, "risk.snapshot", "risk_signals"),
        "budget-agent": ("budget", budget, "price.provider", "budget_candidates"),
    }
    handlers: dict[str, AgentHandler] = {}
    requests: list[AgentInput] = []
    for agent_id, (domain, candidates, provider, field) in sources.items():
        if agent_id == "lodging-agent":
            # 住宿不再只看房价：走联合评估（通勤/费用/体力/碳排/可玩节点/预算一起算）
            handlers[agent_id] = lodging_handler(
                route=route, nights=nights, budget_total=budget_total
            )
        else:
            handlers[agent_id] = pool_handler(agent_id, domain, provider=provider, field=field)
        requests.append(AgentInput(
            request_id=f"runtime-{agent_id}", agent_id=agent_id, domain=domain,
            context={"candidates": list(candidates)},
            hard_constraints=dict(hard_constraints),
            soft_preferences=dict(soft_preferences),
            allowed_tools=declared_tools(agent_id),  # P2：按注册表授予，不再写死空列表
        ))
    # 绿色出行智能体：基于碳排方法学独立提案（此前只在注册表里声明，从未被调度）
    handlers[GREEN_AGENT_ID] = green_handler()
    requests.append(AgentInput(
        request_id=f"runtime-{GREEN_AGENT_ID}", agent_id=GREEN_AGENT_ID, domain="sustainability",
        context={"transport_options": list(transport_options or transport)},
        hard_constraints=dict(hard_constraints),
        soft_preferences=dict(soft_preferences), allowed_tools=declared_tools(GREEN_AGENT_ID),
    ))
    # 健康 / 体力智能体：按画像与天气给出保守强度建议
    handlers[HEALTH_AGENT_ID] = health_handler()
    requests.append(AgentInput(
        request_id=f"runtime-{HEALTH_AGENT_ID}", agent_id=HEALTH_AGENT_ID, domain="health",
        context={
            "profile": dict(profile or {}),
            "weather": dict(weather or {}),
            "daily_nodes": daily_nodes,
        },
        hard_constraints=dict(hard_constraints), soft_preferences=dict(soft_preferences),
        allowed_tools=declared_tools(HEALTH_AGENT_ID),
    ))
    # 应急智能体：把风险事件转成最小扰动重规划动作提案（旅中闭环的入口）
    handlers[EMERGENCY_AGENT_ID] = emergency_handler()
    requests.append(AgentInput(
        request_id=f"runtime-{EMERGENCY_AGENT_ID}", agent_id=EMERGENCY_AGENT_ID, domain="emergency",
        context={"route": list(route), "risk_event": dict(risk_event or {})},
        hard_constraints=dict(hard_constraints), soft_preferences=dict(soft_preferences),
        allowed_tools=declared_tools(EMERGENCY_AGENT_ID),
    ))
    # 文化智能体（2026-10-08 接线）：只查项目内可信知识库，查不到就如实留白
    handlers[CULTURE_AGENT_ID] = culture_handler()
    _cultural_input = list(cultural_nodes) if cultural_nodes else [
        node
        for node in (route or [])
        if isinstance(node, Mapping)
        and (
            str(node.get("category") or "").lower() == "cultural"
            or str(node.get("planning_category") or "").lower() == "cultural"
            or any(tag in ("文化", "博物馆", "古迹") for tag in (node.get("tags") or []))
        )
    ]
    requests.append(AgentInput(
        request_id=f"runtime-{CULTURE_AGENT_ID}", agent_id=CULTURE_AGENT_ID, domain="culture",
        context={"cultural_nodes": _cultural_input, "city": str(city or "")},
        hard_constraints=dict(hard_constraints), soft_preferences=dict(soft_preferences),
        allowed_tools=list(declared_tools(CULTURE_AGENT_ID)),
    ))
    # 无障碍智能体（2026-10-08 接线）：没有官方无障碍数据时**只输出"未核实 + 待确认清单"**
    handlers[ACCESSIBILITY_AGENT_ID] = accessibility_handler()
    requests.append(AgentInput(
        request_id=f"runtime-{ACCESSIBILITY_AGENT_ID}", agent_id=ACCESSIBILITY_AGENT_ID,
        domain="accessibility",
        context={
            "accessibility_nodes": _cultural_input or list(route or []),
            "accessibility_facts": dict(accessibility_facts or {}),
            "mobility_need": bool(mobility_need),
        },
        hard_constraints=dict(hard_constraints), soft_preferences=dict(soft_preferences),
        allowed_tools=list(declared_tools(ACCESSIBILITY_AGENT_ID)),
    ))
    return SpecialistRuntime(handlers), requests
