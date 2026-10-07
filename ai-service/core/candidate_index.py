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


# 供应商类别里的**住宿**标记：高德把住宿统一标成「住宿服务」，比名字里的关键词可靠得多。
LODGING_TYPE_MARKERS = ("住宿", "酒店", "宾馆", "旅馆", "民宿", "客栈", "青旅", "公寓")


def intent_of(value: Any) -> str:
    """节点或纯文本 → 意图标签（显式意图优先，其次**供应商类别**，最后才按名字关键词）。

    显式优先很重要：候选池条目与我们插入的节点都带 `intent`/`tags`，
    若只按中文关键词猜，"蒲城会馆"这类名字会被判成 other，响应度与配额核对都会算错。

    ⚠️ 类别优先于名字（2026-10-05 真实推演抓到的错判）：
    喀什的「**古城**昆仑塔逸扉度假酒店」名字里有"古城"，关键词扫描先撞上 `cultural`，
    于是这家住宿被判成人文 —— 后果是住宿实时房价那一步**跳过了它**（只在查 hotel 意图的节点），
    最终方案里它一直是"暂无供应商数据"。而它的 `type` 明明是 `住宿服务`。
    所以：先看供应商给的类别，再退回名字关键词（名字本身有歧义，类别没有）。
    """
    if isinstance(value, Mapping):
        explicit = str(value.get("intent") or "").strip()
        if explicit in PREFERENCE_TAXONOMY:
            return explicit
        tags = value.get("tags") or []
        if isinstance(tags, list):
            for tag in tags:
                label = str(tag).strip()
                if label in PREFERENCE_TAXONOMY:
                    return label
        poi_type = str(value.get("type") or "")
        if value.get("is_hotel") is True or any(marker in poi_type for marker in LODGING_TYPE_MARKERS):
            return "hotel"
        text = f"{node_name(value)} {poi_type} {value.get('desc') or ''}"
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


def _distance_km(a: Any, b: Any) -> float:
    """两点直线距离（km）；取不到坐标返回 `inf`（"不知道多远"不能当成"很近"）。"""
    from .travel_feasibility import haversine_km, node_lnglat

    first, second = node_lnglat({"lnglat": a}), node_lnglat({"lnglat": b})
    if not first or not second:
        return float("inf")
    value = haversine_km(first, second)
    return float(value) if value == value else float("inf")


def substitute(
    node: Mapping[str, Any],
    index: Mapping[str, Sequence[Mapping[str, Any]]],
    exclude_terms: Sequence[str] = (),
    max_price: Optional[float] = None,
    prefer_cheaper: bool = False,
    prefer_near_to: Optional[Sequence[float]] = None,
    max_near_km: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """给一个节点找同类替代：默认挑"价格可接受里档位最高"的；`prefer_cheaper` 时挑最便宜的。

    - 被 `exclude_terms` 点名的候选**永不返回**；
    - `prefer_near_to`（用户说了"不想跑远"）时：先剔掉离该点超过 `max_near_km` 的候选，
      再按**离它最近**排序 —— 否则"换掉一个远点"很可能换来**另一个远点**
      （2026-10-05 实测：原来是按档位/价格排的，跟距离无关）；
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

    if prefer_near_to:
        near = [item for item in filtered if _distance_km(item.get("lnglat"), prefer_near_to) <= float(max_near_km or 40.0)]
        # 有近的就只在近的里挑；一个都没有则退回原池（宁可换远点，也不假装"没有替代"）
        if near:
            filtered = near
    near_only = False
    if prefer_near_to:
        near = [item for item in filtered if _distance_km(item.get("lnglat"), prefer_near_to) <= float(max_near_km or 40.0)]
        # 有近的就只在近的里挑；一个都没有则退回原池（宁可换远点，也不假装"没有替代"）
        if near:
            filtered = near
            near_only = True
    if not filtered:
        return None
    if near_only:
        # 用户说了"不想跑远"：**先看离得近**，同样近再看档位/价格
        return min(
            filtered,
            key=lambda item: (
                _distance_km(item.get("lnglat"), prefer_near_to),
                -int(item.get("tier_level") or 0),
                float(item.get("price") or 0),
            ),
        )
    if prefer_cheaper:
        return min(filtered, key=lambda item: float(item.get("price") or 0))
    return max(filtered, key=lambda item: (int(item.get("tier_level") or 0), -float(item.get("price") or 0)))


def apply_exclusions(
    plan: Any,
    exclude_terms: Sequence[str],
    index: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    *,
    prefer_near_to: Optional[Sequence[float]] = None,
    max_near_km: Optional[float] = None,
) -> Dict[str, Any]:
    """把用户点名"不想去"的地点剔除，并尽量从候选池补同类（保持一体化骨架）。

    `prefer_near_to`：用户说过"不想跑远"时传市中心坐标 —— 替代点**优先选近的**
    （否则"换掉一个远点"很可能换来另一个远点，2026-10-05 实测）。

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
        replacement = substitute(
            node,
            pool,
            exclude_terms=list(terms) + used_replacements,
            prefer_near_to=prefer_near_to,
            max_near_km=max_near_km,
        )
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
        replaced.append(
            {
                "from": name,
                "to": new_node["name"],
                "intent": intent_of(node),
                # 用户说过"不想跑远"时，把"换来的这个离市中心多远"一并记下来 —— 换点必须可核对
                "center_km": (
                    round(_distance_km(new_node.get("lnglat"), prefer_near_to), 1) if prefer_near_to else None
                ),
                "reason": "你说过不想跑那么远，换成同类的近点" if prefer_near_to else "",
            }
        )

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
        _suffix = "（按你说的「不想跑远」，选的是同类里离得最近的）" if prefer_near_to else ""
        parts.append("并换成同类替代：" + "、".join(f"{item['from']} → {item['to']}" for item in replaced) + _suffix)
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
