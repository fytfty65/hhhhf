"""二次增量：把用户的一句话解析成结构化 delta，并**真的执行**。

解决的问题
----------
现状：追加诉求只是"重新解析文本 → 整段重跑"，偏好最终只影响 5 个类目的覆盖校验（且只写报告）。
用户说"多吃地道美食"不会真的多出餐饮节点；说"不想去兵马俑"也不会把它换掉。

本模块把增量分成三类，并给出可执行动作：
1. **排他**（`exclude`）：点名不想去的地点 → 交给 `candidate_index.apply_exclusions` 剔除+同类替换；
2. **配额**（`quota`）：多吃美食/少安排购物/住宿升级 → 转成**数量或档位约束**，由 `enforce_quota` 执行；
3. **结构**（`days` / `budget`）：天数与预算变更 → 只解析出来，是否执行由产品决定（结构性变更要确认）。

并给出 `measure_increment()`：目标特征涨了多少、其它部分被扰动多少 —— 即"是不是真的按我说的改了"。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .candidate_index import intent_of, normalize_text
from .plan_quality import PREFERENCE_TAXONOMY, node_day, node_name, nodes_of, parse_clock

# 触发词
EXCLUDE_HINTS = ("不想去", "不要去", "不去", "别安排", "不要安排", "换掉", "换成别的", "去掉", "删除", "去过", "不感兴趣", "不喜欢")
INCREASE_HINTS = ("多吃", "多安排", "多一点", "多一些", "加大", "增加", "优先", "重点", "丰富")
REDUCE_HINTS = ("少吃", "少安排", "减少", "不要太多", "太多了", "少一点", "精简")
UPGRADE_HINTS = ("住好", "升级", "品质", "高档", "奢华", "舒服")
DOWNGRADE_HINTS = ("省一点", "便宜", "省钱", "降级", "经济")

# 意图的同义说法（用户不会说"cultural"，会直接说"博物馆"）
INTENT_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "food": ("美食", "小吃", "吃", "餐厅", "地道", "苍蝇馆子", "夜市", "老字号"),
    "cultural": ("博物馆", "历史", "古迹", "文化", "人文"),
    "scenic": ("风景", "自然", "山水", "公园", "山", "湖", "海"),
    "hotel": ("住宿", "酒店", "民宿", "住"),
    "shopping": ("购物", "逛街", "商场", "买东西"),
    "nightlife": ("夜景", "夜生活", "酒吧", "演出"),
    "outdoor": ("徒步", "户外", "登山", "骑行"),
    "hotspring": ("温泉", "泡汤", "汤泉"),
    "family": ("亲子", "孩子", "带娃"),
    "photo": ("摄影", "拍照", "机位"),
}

DEFAULT_STEP = 2  # 没有明确数量时，"多安排一点"按 +2 处理
MAX_STEP = 6


def _detect_intent(text: str) -> Optional[str]:
    for label, synonyms in INTENT_SYNONYMS.items():
        if any(word in text for word in synonyms):
            return label
    return None


def _detect_explicit_count(text: str) -> Optional[int]:
    """解析明确数量：必须带量词（个/家/处/次/顿），否则"少一点/多一些"会被误读成数字。

    - '再多安排 3 个博物馆' → 3
    - '减 2 个购物点' → 2
    - '少一点' → None（走默认步长）
    """
    match = re.search(
        r"(?:多|再|增加|加)[^\d一二三四五六七八九十]{0,4}([0-9一二三四五六七八九十])\s*(?:个|家|处|次|顿)",
        text,
    )
    if not match:
        match = re.search(r"([0-9])\s*(?:个|家|处|次|顿)", text)
    if not match:
        return None
    token = match.group(1)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    return digits.get(token, int(token) if token.isdigit() else None)


def _mentions_place(name: str, normalized_text: str) -> bool:
    """判断文本里是否提到了某个方案节点。

    用户不会说全名（说"城墙"而不是"城墙南门"），所以除了整名匹配，
    还用"该名字里包含的类别关键词"（来自 PREFERENCE_TAXONOMY）做二级匹配。
    """
    key = normalize_text(name)
    if len(key) >= 2 and key in normalized_text:
        return True
    for keywords in PREFERENCE_TAXONOMY.values():
        for keyword in keywords:
            token = normalize_text(keyword)
            if len(token) >= 2 and token in key and token in normalized_text:
                return True
    return False


def parse_increment(
    text: str,
    previous_plan: Any = None,
    known_names: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """把一句话解析成结构化 delta（**只解析，不执行**）。

    返回：`{text, exclude[], quota{}, upgrade{}, downgrade{}, days?, budget?, matched[]}`
    - `exclude` 尽量用**方案里真实存在的节点名**（比正则抓词可靠得多）；
    - `quota` 是"相对变化量"（+2 表示再加两个餐饮节点）。
    """
    raw = str(text or "")
    plan_names: List[str] = []
    if previous_plan is not None:
        plan_names.extend(node_name(node) for node in nodes_of(previous_plan) if node_name(node))
    plan_names.extend(str(name) for name in (known_names or []) if str(name).strip())

    normalized = normalize_text(raw)
    matched: List[str] = []

    # ---- 排他：出现排除类说法时，收集"方案里被点到的地点" ----
    exclude: List[str] = []
    has_exclude_hint = any(hint in raw for hint in EXCLUDE_HINTS)
    if has_exclude_hint:
        matched.extend([hint for hint in EXCLUDE_HINTS if hint in raw])
        seen = set()
        for name in plan_names:
            key = normalize_text(name)
            if len(key) < 2 or key in seen:
                continue
            if _mentions_place(name, normalized):
                seen.add(key)
                exclude.append(name)

    # ---- 配额：增减类说法 + 意图 ----
    intent = _detect_intent(raw)
    quota: Dict[str, int] = {}
    if intent:
        count = _detect_explicit_count(raw) or DEFAULT_STEP
        count = max(1, min(MAX_STEP, count))
        if any(hint in raw for hint in INCREASE_HINTS):
            quota[intent] = quota.get(intent, 0) + count
            matched.append(f"increase:{intent}")
        elif any(hint in raw for hint in REDUCE_HINTS):
            quota[intent] = quota.get(intent, 0) - count
            matched.append(f"reduce:{intent}")

    # ---- 档位：住好一点 / 省一点（按分句归属意图，避免"吃的省一点"被套到住宿上） ----
    upgrade: Dict[str, str] = {}
    downgrade: Dict[str, str] = {}
    clauses = [part for part in re.split(r"[，,。;；、\s]+", raw) if part]
    for clause in clauses:
        clause_intent = _detect_intent(clause)
        if any(hint in clause for hint in UPGRADE_HINTS):
            target = clause_intent or "hotel"
            upgrade[target] = "quality"
            matched.append(f"upgrade:{target}")
        if any(hint in clause for hint in DOWNGRADE_HINTS):
            if clause_intent in {"hotel", "food"}:
                downgrade[clause_intent] = "economy"
                matched.append(f"downgrade:{clause_intent}")
            else:
                downgrade["hotel"] = "economy"
                downgrade["food"] = "economy"
                matched.append("downgrade:cost")

    # ---- 结构：天数 / 预算 ----
    days: Optional[int] = None
    day_match = re.search(r"([0-9]+)\s*天", raw)
    if day_match:
        days = int(day_match.group(1))
    budget: Optional[float] = None
    budget_match = re.search(r"预算[^\d]{0,6}([0-9]+(?:\.[0-9]+)?)", raw)
    if budget_match:
        budget = float(budget_match.group(1))

    return {
        "text": raw,
        "exclude": exclude,
        "quota": quota,
        "upgrade": upgrade,
        "downgrade": downgrade,
        "days": days,
        "budget": budget,
        "matched": matched,
        "is_noop": not (exclude or quota or upgrade or downgrade or days or budget),
    }


TIER_POLICY_SYNONYMS: Dict[str, int] = {"economy": -1, "cheap": -1, "budget": -1, "quality": 1, "premium": 1, "luxury": 2}

# 意图 → 中文类别（写进节点的 type，便于前端展示与"必去项"中文匹配）
INTENT_TYPE_LABELS: Dict[str, str] = {
    "food": "餐饮",
    "hotel": "住宿",
    "cultural": "文化",
    "scenic": "自然风光",
    "landmark": "地标",
    "outdoor": "户外",
    "family": "亲子",
    "photo": "摄影",
    "market": "市集",
    "nightlife": "夜生活",
    "shopping": "购物",
    "hotspring": "温泉",
}


def tier_policy(delta: Mapping[str, Any]) -> Dict[str, int]:
    """把 upgrade/downgrade 解析结果翻译成**档位策略**：正=升档（偏好更高档/更高分），负=降档（偏好更便宜）。

    `{"hotel": 1, "food": -1}` 表示"住宿住好一点、餐饮省一点"。
    """
    policy: Dict[str, int] = {}
    for label, target in (delta.get("upgrade") or {}).items():
        policy[label] = max(policy.get(label, 0), TIER_POLICY_SYNONYMS.get(str(target).lower(), 1))
    for label, target in (delta.get("downgrade") or {}).items():
        policy[label] = min(policy.get(label, 0), TIER_POLICY_SYNONYMS.get(str(target).lower(), -1))
    return {label: value for label, value in policy.items() if value}


def delta_to_ranking_preferences(delta: Mapping[str, Any]) -> Dict[str, Any]:
    """把增量映射成**排序器认得的偏好键**。

    网关的 `service/planning_ranker.go` 会读这些键（关键词命中 +0.05；`budget=low` 会触发
    更低价的定量 nudge），Python 侧的配额填充与兜底方向也读同一份策略，避免两边各说各话。
    """
    preferences: Dict[str, Any] = {}
    upgrades = delta.get("upgrade") or {}
    downgrades = delta.get("downgrade") or {}
    if "hotel" in upgrades:
        preferences["accommodation_style"] = "品质"
    if "hotel" in downgrades:
        preferences["accommodation_style"] = "经济"
    if downgrades:
        preferences["budget"] = "low"
    if "food" in upgrades:
        preferences["style"] = "地道品质"
    return preferences


def _count_by_intent(plan: Any) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for node in nodes_of(plan):
        label = intent_of(node)
        counts[label] = counts.get(label, 0) + 1
    return counts


def _next_slot_time(plan: Any, day: int, intent: str) -> str:
    """给新插入的节点挑一个不冲突的时间（餐饮挑饭点，其它挑白天）。"""
    existing = [parse_clock(node.get("time")) for node in nodes_of(plan) if node_day(node, 1) == day]
    taken = {value for value in existing if value is not None}
    preferences = ("12:30", "18:30") if intent == "food" else ("10:00", "14:30", "16:30", "09:00")
    for candidate in preferences:
        clock = parse_clock(candidate)
        if clock is not None and clock not in taken:
            return candidate
    return "15:00"


def build_node_from_candidate(candidate: Mapping[str, Any], day: int, time: str, intent: str) -> Dict[str, Any]:
    """候选 → 前端可用的路线节点（字段对齐 AMap POI 形状，缺什么就标注未核实）。

    `type` 落成**中文类别**（餐饮/住宿/文化/自然风光…）：意图标签（food/hotel）是内部口径，
    直接写进 type 会让前端显示英文、也会让"必须包含餐/酒店"这类中文必去项匹配不上。
    意图本身仍然通过 `tags` + `added_by_increment` 保留，`intent_of` 照旧能识别。
    """
    name = str(candidate.get("name") or "")
    price = candidate.get("price")
    source = str(candidate.get("price_source") or candidate.get("source") or "unavailable")
    raw_type = str(candidate.get("type") or "").strip()
    # 候选自带的中文类型（高德的"餐饮服务;中餐厅"）优先；只有英文意图名或空值时用我们的中文标签
    has_cjk = any("\u4e00" <= char <= "\u9fff" for char in raw_type)
    type_label = raw_type if has_cjk else INTENT_TYPE_LABELS.get(intent, intent)
    return {
        "day": day,
        "name": name,
        "location": name,
        "time": time,
        "type": type_label,
        "lnglat": candidate.get("lnglat"),
        "cost_estimate": f"¥{float(price):g}" if price is not None else "暂无供应商数据",
        "rating": candidate.get("rating") or "暂无供应商数据",
        "open_time": candidate.get("open_time") or "暂无供应商数据",
        "data_sources": {"cost_estimate": source if price is not None else "unavailable", "rating": "amap", "open_time": "amap"},
        "estimated": bool(candidate.get("estimated", price is None)),
        "tags": [intent, type_label, "按你的追加要求补充"],
        "photos": list(candidate.get("photos") or [])[:3],
        "amap_url": candidate.get("amap_url"),
        "map_image": candidate.get("map_image"),
        "added_by_increment": intent,
    }


def enforce_quota(
    plan: Any,
    quota: Mapping[str, int],
    pool: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    context: Optional[Mapping[str, Any]] = None,
    max_nodes_per_day: Optional[int] = None,
    tier_policy_map: Optional[Mapping[str, int]] = None,
) -> Dict[str, Any]:
    """按配额增/减某类节点；**保留一体化底线**（住宿/正餐的存在性不会被削减到 0）。

    `tier_policy_map`：`{"hotel": 1}` 表示这类要**升档**（补点时优先更高档/更高分），
    负数表示降档（优先更便宜）。不传则维持"便宜优先"的原有行为。
    """
    nodes = [dict(node) for node in nodes_of(plan)]
    context = context or {}
    pool = pool or {}
    policy = dict(tier_policy_map or {})
    added: List[Dict[str, Any]] = []
    removed: List[str] = []
    unmet: Dict[str, int] = {}
    used_names = {normalize_text(node_name(node)) for node in nodes}

    limit = max_nodes_per_day or int(context.get("max_nodes_per_day") or 6)
    days = max(1, int(context.get("days") or context.get("trip_days") or max([node_day(n, 1) for n in nodes] or [1])))

    for intent, delta in (quota or {}).items():
        if delta > 0:
            needed = delta
            candidates = [
                item
                for item in (pool.get(intent) or [])
                if normalize_text(item.get("name")) not in used_names
            ]
            if policy.get(intent, 0) > 0:
                # 升档：优先档位高、评分高的候选（价格只作次序）
                candidates.sort(
                    key=lambda item: (
                        -int(item.get("tier_level") or 0),
                        -float(item.get("rating") or 0),
                        float(item.get("price") or 0),
                    )
                )
            else:
                candidates.sort(key=lambda item: (item.get("price") is None, float(item.get("price") or 0)))
            for candidate in candidates:
                if needed <= 0:
                    break
                # 找当天该类节点最少、且总量未超上限的一天
                per_day: Dict[int, int] = {}
                total_per_day: Dict[int, int] = {}
                for node in nodes:
                    day = node_day(node, 1)
                    total_per_day[day] = total_per_day.get(day, 0) + 1
                    if intent_of(node) == intent:
                        per_day[day] = per_day.get(day, 0) + 1
                target_day = min(
                    range(1, days + 1),
                    key=lambda day: (per_day.get(day, 0), total_per_day.get(day, 0), day),
                )
                if total_per_day.get(target_day, 0) >= limit:
                    break  # 不为了凑配额把某天塞爆
                node = build_node_from_candidate(candidate, target_day, _next_slot_time({"route": nodes}, target_day, intent), intent)
                nodes.append(node)
                used_names.add(normalize_text(node["name"]))
                added.append({"intent": intent, "name": node["name"], "day": target_day})
                needed -= 1
            if needed > 0:
                unmet[intent] = needed
        elif delta < 0:
            # 减少：优先删"没有专属标签/价格偏高"的同类节点；餐饮/住宿至少各留 1 个
            targets = [node for node in nodes if intent_of(node) == intent]
            targets.sort(key=lambda node: float(0 if node.get("cost_estimate") in (None, "", "暂无供应商数据") else re.sub(r"[^0-9.]", "", str(node.get("cost_estimate"))) or 0), reverse=True)
            floor = 0 if intent in {"shopping", "nightlife", "photo"} else 1
            removable = max(0, len(targets) - floor)
            for node in targets[: min(removable, -delta)]:
                removed_name = node_name(node)
                nodes = [item for item in nodes if node_name(item) != removed_name]
                removed.append(removed_name)

    new_plan: Any = dict(plan) if isinstance(plan, Mapping) else {"route": nodes}
    new_plan["route"] = nodes

    parts: List[str] = []
    if added:
        parts.append("按你这次的要求补充了：" + "、".join(f"{item['name']}（第 {item['day']} 天）" for item in added))
    if removed:
        parts.append("同时减少了：" + "、".join(removed))
    if unmet:
        parts.append("这些没能补上（候选不足或当天已排满）：" + "、".join(f"{k} 还差 {v} 个" for k, v in unmet.items()))
    return {"plan": new_plan, "added": added, "removed": removed, "unmet": unmet, "disclosure": "；".join(parts)}


def measure_increment(before: Any, after: Any, delta: Mapping[str, Any]) -> Dict[str, Any]:
    """度量"是否真的按我说的改了"：目标特征达成率 + 其它部分的扰动。"""
    quota = {k: v for k, v in (delta.get("quota") or {}).items() if isinstance(v, int) and v > 0}
    before_counts = _count_by_intent(before)
    after_counts = _count_by_intent(after)

    requested = sum(quota.values())
    achieved = sum(max(0, after_counts.get(intent, 0) - before_counts.get(intent, 0)) for intent in quota)
    target_gain = achieved / requested if requested else None

    before_names = {normalize_text(node_name(node)) for node in nodes_of(before)}
    after_names = {normalize_text(node_name(node)) for node in nodes_of(after)}
    union = before_names | after_names
    disturbance = 1.0 - (len(before_names & after_names) / len(union)) if union else 0.0

    responsiveness = None
    if requested:
        responsiveness = round(min(1.0, achieved / requested) * (1.0 - disturbance), 3)
    return {
        "requested": requested,
        "achieved": achieved,
        "target_gain": round(target_gain, 3) if target_gain is not None else None,
        "disturbance": round(disturbance, 3),
        "responsiveness": responsiveness,
        "before_counts": before_counts,
        "after_counts": after_counts,
    }
