"""候选索引与替换（步 2）：让"换同类 / 换掉不想去的点"有真实候选可用。

为什么需要
----------
兜底引擎（`core/budget_planner.py`）在**没有候选池**时只能"删/降"，不能"换同类"，
所以"换一家更便宜的同款餐厅"做不到；用户说"不想去这几个点、换别的"更是无从下手。
本模块把 provider 返回的候选按**意图标签**归档，并提供：

- `index_candidates()`：候选打标（意图 / 价格档 / 价格级别 / 坐标），按意图分桶；
- `intent_of()`：节点或文本 → 意图标签（全项目唯一的分类入口，避免多套语义）；
- `apply_exclusions()`：把用户点名"不想去"的地点从方案里剔除，并**从候选池里补同类**（不是简单删掉，
  否则一体化会被破坏）；
- `substitute()`：给单个节点找同类替代（排除被点名的地点，可选预算上限）。

绝不编造：候选池里没有合适替代时返回 `None`，调用方必须如实告知"没能换"。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .plan_quality import PREFERENCE_TAXONOMY, node_name, nodes_of

# 价格档（越贵越高），用于"降档/升级"判断
TIER_LEVELS = {"free": 0, "economy": 1, "comfort": 2, "quality": 3, "luxury": 4}


def intent_of(value: Any) -> str:
    """节点或纯文本 → 意图标签（命中 PREFERENCE_TAXONOMY 的第一个标签，兜底 other）。"""
    if isinstance(value, Mapping):
        text = f"{node_name(value)} {value.get('type') or ''} {value.get('desc') or ''}"
        tags = value.get("tags") or []
        if isinstance(tags, list):
            text += " " + " ".join(str(tag) for tag in tags)
    else:
        text = str(value or "")
    for label, keywords in PREFERENCE_TAXONOMY.items():
        if any(keyword in text for keyword in keywords):
            return label
    return "other"


def normalize_text(text: Any) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def tier_level(candidate: Mapping[str, Any]) -> int:
    raw = str(candidate.get("price_tier") or candidate.get("tier") or "").strip().lower()
    if raw in TIER_LEVELS:
        return TIER_LEVELS[raw]
    price = candidate.get("price")
    if price is None:
        return 2
    try:
        value = float(price)
    except (TypeError, ValueError):
        return 2
    if value <= 0:
        return 0
    if value < 150:
        return 1
    if value < 600:
        return 2
    if value < 2000:
        return 3
    return 4


def annotate_candidate(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    """补上 `intent` 与 `tier_level`，不改动原对象。"""
    annotated = dict(candidate)
    annotated["intent"] = candidate.get("intent") or intent_of(candidate)
    annotated["tier_level"] = tier_level(candidate)
    return annotated


def index_candidates(candidates: Iterable[Mapping[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """按意图分桶；同一意图内按价格从低到高（便于"先降档"）。"""
    index: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in candidates or []:
        if not isinstance(candidate, Mapping):
            continue
        annotated = annotate_candidate(candidate)
        index.setdefault(str(annotated["intent"]), []).append(annotated)
    for bucket in index.values():
        bucket.sort(key=lambda item: (item.get("price") is None, float(item.get("price") or 0)))
    return index


def _is_excluded(candidate: Mapping[str, Any], exclude_terms: Sequence[str]) -> bool:
    name = normalize_text(candidate.get("name"))
    if not name:
        return False
    for term in exclude_terms:
        target = normalize_text(term)
        if target and (target in name or name in target):
            return True
    return False


def substitute(
    node: Mapping[str, Any],
    index: Mapping[str, Sequence[Mapping[str, Any]]],
    exclude_terms: Sequence[str] = (),
    max_price: Optional[float] = None,
    prefer_cheaper: bool = False,
) -> Optional[Dict[str, Any]]:
    """给一个节点找同类替代：默认挑"价格可接受里档位最高"的；`prefer_cheaper` 时挑最便宜的。

    - 被 `exclude_terms` 点名的候选**永不返回**；
    - 候选池里没有合适的就返回 None（不编造）。
    """
    intent = intent_of(node)
    current_name = normalize_text(node_name(node))
    pool = list(index.get(intent) or [])
    if not pool:
        return None
    filtered = [
        item
        for item in pool
        if normalize_text(item.get("name")) != current_name and not _is_excluded(item, exclude_terms)
    ]
    if max_price is not None:
        filtered = [item for item in filtered if float(item.get("price") or 0) <= max_price]
    if not filtered:
        return None
    if prefer_cheaper:
        return min(filtered, key=lambda item: float(item.get("price") or 0))
    return max(filtered, key=lambda item: (int(item.get("tier_level") or 0), -float(item.get("price") or 0)))


def apply_exclusions(
    plan: Any,
    exclude_terms: Sequence[str],
    index: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
) -> Dict[str, Any]:
    """把用户点名"不想去"的地点剔除，并尽量从候选池补同类（保持一体化骨架）。

    返回：`{plan, removed, replaced, not_found, unreplaced, disclosure}`
    - `plan`：剔除/替换后的**新**方案（不修改传入 plan）；
    - `removed`：被剔除的节点名；
    - `replaced`：[{from, to, intent}] 完成的替换；
    - `not_found`：方案里根本没出现的点名地点（如实告知，不假装换了）；
    - `unreplaced`：剔除了但候选池里没有同类的（如实告知，缺口留给上层补槽）。
    """
    terms = [str(term) for term in (exclude_terms or []) if str(term).strip()]
    if not terms:
        return {"plan": plan, "removed": [], "replaced": [], "not_found": [], "unreplaced": [], "disclosure": ""}

    nodes = [dict(node) for node in nodes_of(plan)]
    kept: List[Dict[str, Any]] = []
    removed: List[str] = []
    replaced: List[Dict[str, Any]] = []
    unreplaced: List[str] = []
    seen_terms: List[str] = []
    used_replacements: List[str] = []
    pool = index or {}

    for node in nodes:
        name = node_name(node)
        hit = next((term for term in terms if normalize_text(term) and (normalize_text(term) in normalize_text(name) or normalize_text(name) in normalize_text(term))), None)
        if not hit:
            kept.append(node)
            continue
        removed.append(name)
        seen_terms.append(hit)
        # 已用过的替代不再复用（否则两个点会被换成同一个地方）
        replacement = substitute(node, pool, exclude_terms=list(terms) + used_replacements)
        if replacement is None:
            unreplaced.append(name)
            continue
        used_replacements.append(str(replacement.get("name")))
        new_node = dict(node)
        new_node["name"] = str(replacement.get("name"))
        new_node["location"] = str(replacement.get("name"))
        if replacement.get("lnglat") is not None:
            new_node["lnglat"] = list(replacement["lnglat"])
        if replacement.get("price") is not None:
            new_node["cost_estimate"] = f"¥{float(replacement['price']):g}"
        new_node["data_sources"] = dict(new_node.get("data_sources") or {})
        if replacement.get("price_tier"):
            new_node["data_sources"]["cost_estimate"] = str(replacement.get("source") or replacement["price_tier"])
        new_node["replaced_from"] = name
        kept.append(new_node)
        replaced.append({"from": name, "to": new_node["name"], "intent": intent_of(node)})

    not_found = [term for term in terms if normalize_text(term) not in {normalize_text(t) for t in seen_terms}]

    new_plan: Any
    if isinstance(plan, Mapping):
        new_plan = dict(plan)
        new_plan["route"] = kept
    else:
        new_plan = {"route": kept}

    parts: List[str] = []
    if removed:
        parts.append("已按你的要求移除：" + "、".join(removed))
    if replaced:
        parts.append("并换成同类替代：" + "、".join(f"{item['from']} → {item['to']}" for item in replaced))
    if unreplaced:
        parts.append("这些点没有找到合适的同类替代（不会硬凑）：" + "、".join(unreplaced))
    if not_found:
        parts.append("你提到的这些地点原本就不在方案里：" + "、".join(not_found))
    return {
        "plan": new_plan,
        "removed": removed,
        "replaced": replaced,
        "not_found": not_found,
        "unreplaced": unreplaced,
        "disclosure": "；".join(parts),
    }
