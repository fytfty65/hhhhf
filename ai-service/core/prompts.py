r"""提示词公共层（此前是预留的空文件）+ **分段专用**裁剪（B-1：长途分段的成本治理）。

实测（2026-09-19，`work/probe_prompt_cost.py`，新疆 30 天真实推演）
----------------------------------------------------------------
- 长途分段生成会调 LLM **6 次**（每 7 天一段，上限 6 段）；
- 每次调用都原样重发同一条 **8,932 字符** 的巨型 system prompt；
- 全流程 55,291 字符里 **≈97% 是这段 prompt 的重复**。

而且这里还有第二重浪费：那段巨型 prompt 的输出契约是**一个 [FINAL_JSON] 对象**（整份路书），
而分段调用只需要**该段的节点数组**（agent 里用 `re.search(r"\[[\s\S]*\]")` 抓数组）——
系统提示与真实任务**语义打架**：既费 token，又容易让模型输出错形状。

本模块做两件事：
1. `compact_system_prompt()`：裁掉 `[FINAL_JSON]` 的对象 schema 与示例，保留规则段，
   并替换成"只输出节点数组"的明确契约；
2. `segments_prompt_note()`：分段任务的一句话上下文（纯函数，便于单测）。

裁剪是**确定性**的（纯字符串处理、无 LLM 参与）→ 可离线单测、可量化（见 tests/test_prompts.py）。
"""

from __future__ import annotations

from typing import Any, Mapping

MIN_COMPACT_CHARS = 240  # 裁得太狠就退回原文：宁可多花 token，也不能把规则丢光

# 分段任务的输出契约：只要节点数组，不要整份路书对象
SEGMENT_OUTPUT_CONTRACT = """【分段任务的输出契约（与整份路书不同，务必照做）】
1. 只输出**本段的节点数组**：[{"day": 整数, "time": "HH:MM", "name": "…", "type": "…", "cost_estimate": "…"}, …]
2. 不要输出 {"status": …, "route": …} 这类外层对象；不要输出解释、Markdown 代码块或注释；
3. 只排本段天数范围内的节点，绝不越界到别的段（系统会丢弃越界节点）；
4. 其余规则（名称与坐标照抄底座数据、每天至少 1 个玩点 + 1 餐、严禁把停车场/商店/住宿当景点、
   自然型目的地每天至少 1 个自然景观等）**照常遵守**。"""


def _strip_pool_block(text: str) -> str:
    """删掉内嵌的候选池区块（【候选…POI…】+ 紧随其后的 JSON 行）。

    这一步是**关键**：候选池 JSON 夹在规则段中间（agent.py 里 规则零…规则六 与池子块交错），
    所以"只取规则段"会把 8.4k 的池子一起带上 —— 实测正是因此分段 prompt 只降了 455 字符。
    """
    lines = str(text or "").splitlines()
    kept = []
    skip_next_json = False
    for line in lines:
        stripped = line.strip()
        if "候选" in stripped and "POI" in stripped and stripped.startswith("【"):
            skip_next_json = True
            continue
        if skip_next_json:
            # 池子块后面紧跟的就是那段 JSON（可能一行，也可能多行）
            if stripped.startswith("[") or stripped.startswith("{"):
                if stripped.endswith("]") or stripped.endswith("}"):
                    skip_next_json = False
                continue
            skip_next_json = False
        kept.append(line)
    return "\n".join(kept)


def _rules_block(system_prompt: str) -> str:
    """取出规则段：从第一个【🔴 规则 起到 [FINAL_JSON] 之前；并**剔除内嵌的候选池区块**。"""
    text = str(system_prompt or "")
    start = text.find("【🔴 规则")
    end = text.find("[FINAL_JSON]")
    if start < 0:
        return ""
    block = text[start:end].strip() if end > start else text[start:].strip()
    return _strip_pool_block(block)


def compact_system_prompt(system_prompt: str, extra_rules: str = "") -> str:
    """把整份路书的巨型 prompt 裁成"分段生成"用的紧凑 prompt。

    - 保留：规则段（含构成规则）；可选追加 `extra_rules`；
    - 丢弃：`[FINAL_JSON]` 里的对象 schema、示例及其后的全部内容；
    - 追加：分段任务的输出契约（只输出节点数组）。
    裁完若短于 `MIN_COMPACT_CHARS`，说明这条 prompt 结构出乎意料 → **退回原文**（不省这份 token，
    也不能让分段拿到一份没有规则的提示）。
    """
    rules = _rules_block(system_prompt)
    if extra_rules:
        rules = (rules + "\n\n" + str(extra_rules).strip()).strip()
    compact = (rules + "\n\n" + SEGMENT_OUTPUT_CONTRACT).strip()
    if not rules or len(compact) < MIN_COMPACT_CHARS:
        return str(system_prompt or "")
    return compact


def segments_prompt_note(target: Mapping[str, Any]) -> str:
    """分段任务的一句话上下文（纯函数，便于单测）。"""
    if not isinstance(target, Mapping):
        return ""
    days = target.get("days") or []
    parts = []
    try:
        first, last = int(days[0]), int(days[-1])
        if first and last:
            parts.append(f"本段覆盖第 {first}-{last} 天")
    except (TypeError, ValueError, IndexError):
        pass
    if target.get("budget"):
        parts.append(f"本段预算约 ¥{target['budget']}")
    if target.get("city"):
        parts.append(f"目的地：{target['city']}")
    return "；".join(parts)


def prompt_stats(system_prompt: str) -> Mapping[str, Any]:
    """给测试与可观测性用：原始长度、裁剪后长度、节省比例。"""
    compact = compact_system_prompt(system_prompt)
    original_len = len(str(system_prompt or ""))
    compact_len = len(compact)
    saved = 0.0 if original_len == 0 else round(1 - compact_len / original_len, 3)
    return {"original": original_len, "compact": compact_len, "saved_ratio": saved}


def compact_pool_index(pool: Any) -> list:
    """把候选池投影成**最小索引**：只留 name / lnglat / type。

    为什么（实测，30 天新疆行程 6 次分段调用）：8,932 字符的 system prompt 里 **8.4k 是候选池 JSON**，
    而它每次分段都被原样重发 —— 那才是 97% 重复的真身（不是规则段、也不是 [FINAL_JSON] schema）。
    分段真正需要底座数据的部分是"逐字照抄 name 与坐标"，因此 cost/rating/open_time/photos/amap_url
    这些字段对分段没用，全部投影掉（主推演那一轮仍然拿完整字段，用于估价与核对）。
    """
    items = pool
    if isinstance(pool, Mapping):
        items = pool.get("route") or pool.get("items") or pool.get("candidates") or []
    result = []
    for entry in items or []:
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        item = {"name": name}
        for key in ("lnglat", "location"):
            value = entry.get(key)
            if value:
                item[key] = value
        kind = entry.get("type")
        if kind:
            item["type"] = str(kind)
        result.append(item)
    return result


def segment_system_prompt(system_prompt: str, pool: Any = None) -> str:
    """分段生成用的 system prompt：**规则 + 输出契约 + 候选池最小索引**。"""
    base = compact_system_prompt(system_prompt)
    index = compact_pool_index(pool)
    if not index:
        return base
    import json

    return (
        base
        + "\n\n【本段可用候选（仅名称/坐标/类型；location 与 lnglat 必须逐字照抄）】\n"
        + json.dumps(index, ensure_ascii=False)
    )


__all__ = [
    "MIN_COMPACT_CHARS",
    "SEGMENT_OUTPUT_CONTRACT",
    "compact_pool_index",
    "compact_system_prompt",
    "prompt_stats",
    "segment_system_prompt",
    "segments_prompt_note",
]
