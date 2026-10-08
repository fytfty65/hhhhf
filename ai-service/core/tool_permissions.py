"""工具权限校验（P2）。

用户点名的问题（2026-10-08）：
    注册表里声明了 `allowed_tools`，但**实际请求里很多智能体是空工具列表**，
    于是"谁能调什么"根本无从校验。举例：`transport-agent` 调 `amap.poi.search` 应当**拒绝**，
    调 `provider.transport.search` 应当**允许**。

这里的做法
----------
把权限判断收敛到**一个函数** `check_tool_call()`，由运行时在提案出口统一校验：
    · 注册表里这个智能体没声明过这个工具 → **拒绝**（`tool_not_allowed`）；
    · 声明过但没带上（请求 `allowed_tools` 为空）→ **拒绝**（`tool_not_granted`，
      因为"没授权"和"没这个权限"是两回事，分开报才查得出问题）；
    · 通配声明（`amap.*`）→ 允许同前缀工具；
    · 都通过 → 允许。

为什么放在运行时出口而不是每个 handler 里
------------------------------------------
和 `hard_constraint_results` 盖章同一个理由：**逐个 handler 改，新增的还会漏**。
出口统一校验，新智能体天然受管。

诚实口径
--------
拒绝就是拒绝，`allowed=False` + 机器可读的 `reason`，不静默放行、也不假装"调用成功"。
`audit_tool_calls()` 把一次执行里所有被拒调用汇总出来，方便在日志/诊断接口里看见 ——
**被拒绝却不出现在任何地方的权限控制等于没有**。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence

#: 拒绝原因（机器可读）
REASON_ALLOWED = "allowed"
REASON_NOT_DECLARED = "tool_not_allowed"
REASON_NOT_GRANTED = "tool_not_granted"
REASON_AGENT_UNKNOWN = "agent_not_registered"
REASON_EMPTY_TOOL = "tool_name_empty"


def _as_list(value: Any) -> List[str]:
    if isinstance(value, (str, bytes)):
        return [str(value)]
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    return []


def tool_matches(pattern: str, tool: str) -> bool:
    """工具名匹配，支持 `amap.*` 这类前缀通配。"""
    pattern = str(pattern or "").strip()
    tool = str(tool or "").strip()
    if not pattern or not tool:
        return False
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return tool.startswith(pattern[:-1])  # 保留点号：amap.* 不匹配 amapx.foo
    return pattern == tool


def check_tool_call(
    *,
    agent_id: str,
    tool: str,
    declared_tools: Iterable[str],
    granted_tools: Iterable[str],
) -> Dict[str, Any]:
    """判断一次工具调用是否放行。

    参数
    ----
    declared_tools: 注册表里这个智能体**声明**拥有的工具（能力上限）
    granted_tools:  本次请求**实际授予**的工具（`AgentInput.allowed_tools`）

    两者都要满足：声明了但没授予 → 仍然拒绝（否则"权限"只是文档）。
    """
    declared = _as_list(declared_tools)
    granted = _as_list(granted_tools)
    tool_name = str(tool or "").strip()

    if not tool_name:
        return {"allowed": False, "reason": REASON_EMPTY_TOOL, "tool": "", "agent_id": agent_id}
    if not declared:
        return {
            "allowed": False, "reason": REASON_NOT_DECLARED, "tool": tool_name, "agent_id": agent_id,
            "detail": "该智能体在注册表里没有声明任何工具",
        }
    if not any(tool_matches(item, tool_name) for item in declared):
        return {
            "allowed": False, "reason": REASON_NOT_DECLARED, "tool": tool_name, "agent_id": agent_id,
            "detail": f"注册表未声明该工具（已声明：{', '.join(declared)}）",
        }
    if not granted or not any(tool_matches(item, tool_name) for item in granted):
        return {
            "allowed": False, "reason": REASON_NOT_GRANTED, "tool": tool_name, "agent_id": agent_id,
            "detail": (
                "本次请求未授予该工具"
                + (f"（已授予：{', '.join(granted)}）" if granted else "（请求的 allowed_tools 为空）")
            ),
        }
    return {"allowed": True, "reason": REASON_ALLOWED, "tool": tool_name, "agent_id": agent_id}


def filter_tool_calls(
    *,
    agent_id: str,
    tool_calls: Sequence[Mapping[str, Any]],
    declared_tools: Iterable[str],
    granted_tools: Iterable[str],
) -> Dict[str, Any]:
    """把一次提案里的工具调用分成"放行"与"拒绝"两堆。

    ⚠️ 放行的那一堆**保持原样**，不能把 `allowed`/`reason`/`detail` 混进去：
    `AgentProposal.tool_calls` 的元素是 `ToolCall` 且 `extra="forbid"`，
    多一个字段整个提案就会校验失败、被运行时判成 `agent_failed`
    （2026-10-08 实测踩到：放行的调用反而把提案搞挂了）。
    裁决信息只加在 `denied` 那一堆上（它的类型是自由的 dict）。

    返回 `{"allowed": [...], "denied": [...]}`；`denied` 里每条都带 `reason`/`detail`，
    便于直接写进日志或诊断接口 —— 用户要能看见"谁试图越权"。"""
    allowed: List[Dict[str, Any]] = []
    denied: List[Dict[str, Any]] = []
    for call in tool_calls or []:
        if not isinstance(call, Mapping):
            continue
        name = str(call.get("tool") or "")
        verdict = check_tool_call(
            agent_id=agent_id, tool=name,
            declared_tools=declared_tools, granted_tools=granted_tools,
        )
        if verdict["allowed"]:
            allowed.append(dict(call))
        else:
            entry = dict(call)
            entry.update(verdict)
            denied.append(entry)
    return {"allowed": allowed, "denied": denied}


def audit_tool_calls(proposals: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """汇总一次执行里所有被拒的工具调用（供日志/诊断接口展示）。

    为什么要这个：权限控制如果只"内部拒绝"而外面看不见，出问题时没人知道
    —— 用户明确要求"即使暂无商业 API 也要先把权限机制做完整"。"""
    denied: List[Dict[str, Any]] = []
    total = 0
    for proposal in proposals or []:
        if not isinstance(proposal, Mapping):
            continue
        for entry in proposal.get("denied_tool_calls") or []:
            if isinstance(entry, Mapping):
                denied.append(dict(entry))
        for entry in proposal.get("tool_calls") or []:
            if isinstance(entry, Mapping):
                total += 1
    return {
        "total_tool_calls": total,
        "denied_count": len(denied),
        "denied": denied,
    }


__all__ = [
    "REASON_ALLOWED",
    "REASON_AGENT_UNKNOWN",
    "REASON_EMPTY_TOOL",
    "REASON_NOT_DECLARED",
    "REASON_NOT_GRANTED",
    "audit_tool_calls",
    "check_tool_call",
    "filter_tool_calls",
    "tool_matches",
]
