"""价格来源与归总：把"检索到的价格"结构化、标来源、合并回方案节点。

现状（为什么要这一层）
--------------------
`api/agent.py` 已有 `get_real_time_web_price()`：用 DuckDuckGo 抓 2 条搜索摘要，返回**一段文本**
（失败就是"未抓取到外网实时价格"）。它只作为给 LLM 的上下文，**没有**：
① 结构化（无法参与预算计算）② 逐节点（一次调用只给"城市+大类"）③ 来源标注（用户看不出可信度）
④ 合并回方案（节点上的 `cost_estimate` 仍是"暂无供应商数据"）。

本模块补齐"归总"这一段（纯函数、可离线测）：
- `pending_price_targets()`：只挑**缺价格**的节点去查（避免每次全量检索）；
- `merge_observations()`：把检索结果按**来源优先级**合并回节点，**弱来源不覆盖强来源**；
- `price_coverage()`：给出已验证/估算/未取到的比例，供报告与前端展示；
- 来源分级与 `budget_planner` 的三级价格模型一致：`provider/amap/official` = 可核实；
  `web/estimate` = **估算（必须标明）**；没有 = 未取到。

红线：**绝不为了让预算好看而把估算当已验证**；弱来源不覆盖强来源；过期观测不采用。
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .candidate_index import intent_of, normalize_text
from .plan_quality import cost_of, node_name, nodes_of

# 来源可信度：数字越大越可信（强来源不被打败）
SOURCE_RANK: Dict[str, int] = {
    "provider": 4,
    "vendor": 4,
    "official": 4,
    "amap": 3,
    "web": 2,
    "estimate": 1,
    "llm_estimate": 1,
    "": 0,
    "unavailable": 0,
}
VERIFIED_SOURCES = {"provider", "vendor", "official", "amap"}


def source_rank(source: Any) -> int:
    return SOURCE_RANK.get(str(source or "").strip().lower(), 0)


def is_verified_source(source: Any) -> bool:
    return str(source or "").strip().lower() in VERIFIED_SOURCES


def make_observation(name: str, value: Optional[float], source: str, retrieved_at: Optional[float] = None) -> Dict[str, Any]:
    """一条价格观测。`value=None` 表示"查了但没查到"，同样要如实记录。"""
    return {
        "name": str(name or "").strip(),
        "value": float(value) if isinstance(value, (int, float)) else None,
        "source": str(source or "").strip().lower(),
        "retrieved_at": float(retrieved_at if retrieved_at is not None else time.time()),
    }


def _current_price_source(node: Mapping[str, Any]) -> str:
    sources = node.get("data_sources") if isinstance(node.get("data_sources"), Mapping) else {}
    for field in ("cost_estimate", "cost", "price"):
        if field in sources:
            return str(sources.get(field) or "").strip().lower()
    return ""


def price_coverage(plan: Any) -> Dict[str, Any]:
    """方案的价格覆盖情况：已验证 / 估算 / 未取到（比例 + 节点名单，便于"还要查哪几个"）。"""
    verified: List[str] = []
    estimated: List[str] = []
    unknown: List[str] = []
    for node in nodes_of(plan):
        name = node_name(node)
        value = cost_of(node)
        source = _current_price_source(node)
        if value is None:
            unknown.append(name)
        elif is_verified_source(source):
            verified.append(name)
        else:
            estimated.append(name)
    total = len(verified) + len(estimated) + len(unknown)
    return {
        "total": total,
        "verified": verified,
        "estimated": estimated,
        "unknown": unknown,
        "verified_ratio": round(len(verified) / total, 3) if total else 0.0,
        "unknown_ratio": round(len(unknown) / total, 3) if total else 0.0,
    }


def pending_price_targets(plan: Any, limit: int = 20, include_estimated: bool = False) -> List[Dict[str, Any]]:
    """挑出**需要去查价格**的节点（默认只挑完全没有价格的）。

    返回按意图分组用的清单：`[{name, intent, current_source, current_value}]`，
    调用方据此逐节点检索（而不是像现在这样一次只查"城市+大类"）。
    """
    targets: List[Dict[str, Any]] = []
    for node in nodes_of(plan):
        value = cost_of(node)
        source = _current_price_source(node)
        if value is not None and not include_estimated:
            continue
        if value is not None and is_verified_source(source):
            continue
        targets.append(
            {
                "name": node_name(node),
                "intent": intent_of(node),
                "current_value": value,
                "current_source": source or "missing",
            }
        )
        if len(targets) >= max(1, int(limit)):
            break
    return targets


def merge_observations(
    plan: Any,
    observations: Sequence[Mapping[str, Any]],
    max_age_seconds: Optional[float] = 6 * 3600,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """把价格观测合并回方案节点（不修改传入 plan）。

    规则（严格口径）：
    - 名称匹配（规范化后包含或相等）才合并；匹配不上如实进 `unmatched`，不瞎猜；
    - **弱来源不覆盖强来源**：已有 `amap` 价格时，`web` 观测只记录不覆盖；
    - 观测过期（超过 `max_age_seconds`）不采用，进 `stale`；
    - `value=None`（查了没查到）不改动节点，只进 `missed`，供"还需补价"清单使用。
    """
    now = float(now if now is not None else time.time())
    nodes = [dict(node) for node in nodes_of(plan)]
    updated: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    unmatched: List[str] = []
    stale: List[str] = []
    missed: List[str] = []

    def match_index(name: str) -> Optional[int]:
        key = normalize_text(name)
        if not key:
            return None
        for index, node in enumerate(nodes):
            target = normalize_text(node_name(node))
            if not target:
                continue
            if key == target or key in target or target in key:
                return index
        return None

    for observation in observations:
        name = str(observation.get("name") or "").strip()
        index = match_index(name)
        if index is None:
            unmatched.append(name)
            continue
        retrieved_at = observation.get("retrieved_at")
        if max_age_seconds is not None and isinstance(retrieved_at, (int, float)):
            if now - float(retrieved_at) > float(max_age_seconds):
                stale.append(name)
                continue
        value = observation.get("value")
        source = str(observation.get("source") or "").strip().lower()
        node = nodes[index]
        if value is None:
            missed.append(name)
            continue

        current_value = cost_of(node)
        current_source = _current_price_source(node)
        if current_value is not None and source_rank(current_source) >= source_rank(source):
            skipped.append({"name": name, "kept": current_source or "missing", "dropped": source or "missing"})
            continue

        new_node = dict(node)
        new_node["cost_estimate"] = f"¥{float(value):g}"
        sources = dict(new_node.get("data_sources") or {})
        sources["cost_estimate"] = source or "unavailable"
        new_node["data_sources"] = sources
        # 只有"可核实来源"才取消 estimated 标记
        new_node["estimated"] = not is_verified_source(source)
        nodes[index] = new_node
        updated.append({"name": name, "value": float(value), "source": source or "unavailable"})

    new_plan: Any = dict(plan) if isinstance(plan, Mapping) else {"route": nodes}
    new_plan["route"] = nodes
    return {
        "plan": new_plan,
        "updated": updated,
        "skipped": skipped,
        "unmatched": unmatched,
        "stale": stale,
        "missed": missed,
        "coverage": price_coverage(new_plan),
    }


_PRICE_PATTERN = re.compile(r"(?:¥|￥|价格|票价|均价|每晚|人均|约)?\s*([0-9]{2,6}(?:\.[0-9]{1,2})?)\s*(?:元|块|RMB)?", re.I)


def parse_price_from_text(text: Any) -> Optional[float]:
    """从检索文本里抠出第一个像价格的数量（2-6 位数字）。

    只做"取数"，不判断真假：来源标记由调用方给定（网络报价 → source='web' → 仍属**估算**，
    必须标明，绝不当作已验证）。
    """
    raw = str(text or "")
    if not raw or "未抓取" in raw or "未获取" in raw:
        return None
    match = _PRICE_PATTERN.search(raw)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def summarize_merge(result: Mapping[str, Any]) -> str:
    """给人看的合并结果说明（前端与日志共用，避免各写一套文案）。"""
    parts: List[str] = []
    updated = result.get("updated") or []
    if updated:
        verified = [item for item in updated if is_verified_source(item.get("source"))]
        part = f"补上 {len(updated)} 个价格"
        if verified:
            part += f"（其中 {len(verified)} 个来自可核实来源）"
        parts.append(part)
    if result.get("skipped"):
        parts.append(f"{len(result['skipped'])} 个已有更可信来源，未覆盖")
    if result.get("missed"):
        parts.append(f"{len(result['missed'])} 个查了没查到（保持未核实）")
    if result.get("stale"):
        parts.append(f"{len(result['stale'])} 个观测已过期，未采用")
    if result.get("unmatched"):
        parts.append(f"{len(result['unmatched'])} 个没匹配到方案节点")
    coverage = result.get("coverage") or {}
    if coverage:
        parts.append(
            f"价格覆盖：可核实 {coverage.get('verified_ratio', 0) * 100:.0f}% / "
            f"未取到 {coverage.get('unknown_ratio', 0) * 100:.0f}%"
        )
    return "；".join(parts) if parts else "没有可合并的价格观测"
