"""用大模型驱动的专业智能体处理器。

为什么要这一层（2026-11-03）
--------------------------
既有专业智能体（`core/agent_runtime.py`）是**确定性适配器**：只从候选池里
裁剪/排序，不调用大模型。要把"多智能体规划"再往前推一步，需要让专家智能体
也能用自己的判断重排/取舍候选 —— 但必须保留项目一贯的诚实口径：

  · **只允许在候选池内选点**：模型给的名字必须逐字命中池子，否则丢掉并记账；
  · **事实来自池子，不来自模型**：返回的候选行是"池子原文 + 模型的选择理由"，
    模型不能改写坐标/价格/评分；
  · **信息不足就降级**：池子为空、输出不可解析、池外候选 → 返回 `degraded`
    + `FailureInfo`，绝不编造一份"看起来很完整"的提案。

`complete` 由调用方注入（`api.agent` 用与主推演相同的模型/端点），
因此本模块无网络、可单测：测试里传一个假 `complete` 即可覆盖全部分支。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence

from .agent_contracts import AgentInput

LlmComplete = Callable[[List[Dict[str, str]]], Awaitable[str]]

_POOL_KEYS = ("candidates", "transport_options", "nodes")
_COMPACT_FIELDS = (
    "name",
    "location",
    "type",
    "distance_km",
    "out_of_town",
    "cost_estimate",
    "rating",
    "open_time",
    "tags",
    "is_hotel",
    "lnglat",
    "region_city",
)

DEFAULT_SYSTEM_PROMPT = """你是 OmniRoute 的{label}专业智能体（domain={domain}）。
职责：在给定候选池内按用户约束做出取舍，并向协调器提交结构化提案。

必须遵守：
1. 只能从 candidate_pool 里选择，name 必须逐字一致；**不得新增池外地点，也不得改写坐标/价格/评分**。
2. hard_constraints 优先于 soft_preferences；与消息中的裁决冲突时，以硬约束为准。
3. 可以参考 messages 里其它智能体上一轮的提案与协调器裁决；仍有分歧就在 explanation 里说明，不要假装共识。
4. 只输出一个 JSON 对象，不要输出 markdown 或前后缀说明：
   {{"candidates": [{{"name": "池内名字", "reason": "一句依据"}}], "explanation": "一句话总结", "confidence": 0.0}}
5. 依据不足时输出空 candidates（系统会自动降级），禁止编造。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(value: Any) -> str:
    return "".join(str(value or "").split()).lower()


def _compact_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: row.get(key) for key in _COMPACT_FIELDS if row.get(key) is not None}


def _pool_of(request: AgentInput) -> List[Mapping[str, Any]]:
    context = request.context or {}
    for key in _POOL_KEYS:
        rows = context.get(key)
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)) and rows:
            return [row for row in rows if isinstance(row, Mapping)]
    return []


def build_messages(
    request: AgentInput,
    *,
    label: str = "",
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    max_rows: int = 30,
) -> List[Dict[str, str]]:
    """把输入契约 + 收件箱消息拼成 LLM 消息。"""
    rows = _pool_of(request)
    payload = {
        "agent_id": request.agent_id,
        "domain": request.domain,
        "hard_constraints": dict(request.hard_constraints or {}),
        "soft_preferences": dict(request.soft_preferences or {}),
        "candidate_pool": [_compact_row(row) for row in rows[: max(1, int(max_rows))]],
        "candidate_pool_total": len(rows),
        # 多轮协商上下文：上一轮其它智能体的提案与协调器裁决（由 AgentMessageBus 注入）
        "messages": list(request.context.get("messages") or []),
        "revision_directive": request.context.get("revision_directive") or "",
        "revision_reason": request.context.get("revision_reason") or "",
    }
    system = system_prompt.format(label=label or request.agent_id, domain=request.domain)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]


def parse_proposal(text: Any) -> Dict[str, Any]:
    """从模型输出里取 JSON 对象（容忍 ```json 围栏与前后缀说明）。"""
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("empty_llm_output")
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("llm_output_without_json_object")
    parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("llm_output_not_object")
    return parsed


def llm_handler(
    agent_id: str,
    domain: str,
    *,
    complete: LlmComplete,
    label: str = "",
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    provider: str = "llm.specialist",
    max_candidates: int = 20,
    fallback: str = "verified_candidate_pool",
) -> Callable[[AgentInput], Awaitable[Dict[str, Any]]]:
    """构造一个 LLM 驱动的专业智能体处理器。

    `complete` 只负责"发消息拿文本"；超时/异常/解析/对账都在这里兜住，
    失败一律返回 `degraded`（带 `FailureInfo`），由运行时统一记账。
    """

    def _degraded(code: str, message: str, *, retryable: bool = True) -> Dict[str, Any]:
        return {
            "agent_id": agent_id,
            "domain": domain,
            "status": "degraded",
            "degraded": True,
            "candidates": [],
            "evidence": [{"field": "candidates", "provider": provider, "observed_at": _now()}],
            "confidence": 0.0,
            "failure": {
                "code": code,
                "message": str(message)[:500],
                "retryable": retryable,
                "fallback": fallback,
            },
            "explanation": str(message)[:1000],
        }

    async def handle(request: AgentInput) -> Dict[str, Any]:
        pool_rows = _pool_of(request)
        if not pool_rows:
            return _degraded(
                "candidate_pool_empty",
                f"{agent_id} 没有候选池数据，未调用大模型，也未编造候选。",
            )

        messages = build_messages(request, label=label, system_prompt=system_prompt)
        try:
            text = await complete(messages)
        except Exception as exc:  # 调用失败要如实降级，不能把异常抛给运行时当"崩溃"
            print(f"⚠️ [{agent_id}] LLM 提案调用失败: {type(exc).__name__}: {exc}", flush=True)
            return _degraded(
                "llm_call_failed",
                f"大模型调用失败（{type(exc).__name__}），已保留候选池原始提案供协调器使用。",
            )

        try:
            parsed = parse_proposal(text)
        except Exception:
            print(f"⚠️ [{agent_id}] LLM 输出无法解析为 JSON，已降级", flush=True)
            return _degraded(
                "llm_output_unparsable",
                "大模型输出不是可解析的 JSON，未采纳其结论。",
            )

        pool_by_name = {_norm(row.get("name") or row.get("location")): dict(row) for row in pool_rows}
        kept: List[Dict[str, Any]] = []
        dropped: List[str] = []
        raw_candidates = parsed.get("candidates")
        picked = raw_candidates if isinstance(raw_candidates, list) else []
        for item in picked:
            if not isinstance(item, Mapping):
                continue
            key = _norm(item.get("name"))
            base = pool_by_name.get(key)
            if base is None:
                dropped.append(str(item.get("name") or "(未命名)"))
                continue
            merged = dict(base)
            reason = str(item.get("reason") or "").strip()
            if reason:
                merged["selection_reason"] = reason[:200]
            if merged not in kept:
                kept.append(merged)

        if not kept:
            if dropped:
                return _degraded(
                    "llm_candidates_not_in_pool",
                    f"大模型给出的 {len(dropped)} 个候选都不在候选池内（已全部丢弃，未采纳）。",
                    retryable=False,
                )
            return _degraded(
                "llm_returned_no_candidate",
                "大模型未给出任何候选，未采纳其结论。",
                retryable=False,
            )

        try:
            confidence = float(parsed.get("confidence") if parsed.get("confidence") is not None else 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        explanation = str(parsed.get("explanation") or "").strip()[:1000] or (
            f"从 {len(pool_rows)} 个候选中选出 {len(kept)} 个（大模型提案，事实取自候选池）。"
        )
        payload: Dict[str, Any] = {
            "agent_id": agent_id,
            "domain": domain,
            "status": "ok",
            "degraded": False,
            "candidates": kept[: max(1, int(max_candidates))],
            "hard_constraint_results": dict(request.hard_constraints or {}),
            "evidence": [{"field": "candidates", "provider": provider, "observed_at": _now()}],
            "confidence": confidence,
            "explanation": explanation,
        }
        if dropped:
            # 池外候选被丢弃要留痕：否则"模型编了 3 个点、全被拦下"外面看不见
            payload["applied_constraints"] = {
                "llm_dropped_not_in_pool": dropped[:8],
                "llm_dropped_count": len(dropped),
            }
        return payload

    return handle
