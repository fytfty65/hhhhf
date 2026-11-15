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
# 「指定换成某一家」的说法（2026-10-05）：用户点了"有实价的住宿可选"里的某一家，
# 或者直接说"住 XX 酒店" —— 这时他要的是**那一家**，不是"随便换个同类"。
# ⚠️ 顺序要紧：`SWAP_HINTS` 必须比 `EXCLUDE_HINTS` 先判，否则"换成 X"里的"换掉/换成别的"会先命中，
# 变成"把 X 排除掉"（正好反了）。
SWAP_HINTS = ("换成", "换住", "改住", "住进", "就住", "住", "订", "选择", "改成")
# 增减的说法要覆盖用户真实口语：实测"我想多打卡自然景观"这类说法一个都没命中，
# 结果二次增量完全没被识别（策略层因此拿不到"该多加自然景观"的信号）。
INCREASE_HINTS = (
    "多吃", "多安排", "多一点", "多一些", "加大", "增加", "优先", "重点", "丰富",
    "多打卡", "多看", "多点", "多去", "想多", "更多", "想要更多", "更丰富", "主要想", "加点", "来点", "再来", "为主",
    "偏重", "倾向",
)
REDUCE_HINTS = (
    "少吃", "少安排", "减少", "不要太多", "太多了", "少一点", "精简",
    "少看", "少去", "别再看", "看腻", "不想再看", "太多",
)
# 一句话里可能同时有"少看博物馆 + 多安排自然风景"这种双向要求：按**分句**判定，
# 否则整句只认第一个命中的意图（实测会把"少看博物馆"读成"要多加博物馆"）。
CLAUSE_SPLIT = re.compile(r"[，。；;！!？?\n]+")
UPGRADE_HINTS = ("住好", "升级", "品质", "高档", "奢华", "舒服")
DOWNGRADE_HINTS = ("省一点", "便宜", "省钱", "降级", "经济")

# 意图的同义说法（用户不会说"cultural"，会直接说"博物馆"）
INTENT_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "food": ("美食", "小吃", "吃", "餐厅", "地道", "苍蝇馆子", "夜市", "老字号"),
    "cultural": ("博物馆", "博物院", "历史馆", "展馆", "纪念馆", "美术馆", "历史", "古迹", "文化", "人文"),
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


# 通用类别词：用户说"换成某家酒店"时，光有「酒店」二字**不足以定位是哪一个** ——
# 用它做匹配会让"换成火星酒店"命中池子里任意一家酒店（实测踩过）。
_GENERIC_PLACE_WORDS = (
    "酒店", "宾馆", "旅馆", "民宿", "客栈", "青旅", "公寓",
    "餐厅", "饭店", "酒楼", "食堂", "小吃", "美食",
    "公园", "景区", "风景名胜", "博物馆", "博物院", "展馆", "广场", "中心", "火车站", "机场",
)


def _mentions_specific_place(name: str, normalized_text: str, *, city: str = "") -> bool:
    """**点名**要换成哪一家时的匹配：比 `_mentions_place` 严，不允许只靠类别词命中。

    允许三种说法：① 说全名；② 说"去掉通用类别词之后的核心词"；
    ③ 说核心词的**前缀**（用户常说前半段："换成瑞德假日" 指 "瑞德假日酒店(巴州博物馆市政府店)"）。

    ⚠️ **城市名要先从两边都去掉**（2026-10-05 实测踩坑）：池子里的名字多半以城市开头
    （"库尔勒民俗文化博物馆"、"库尔勒福润德·万达锦华酒店"），而用户这句话里本来就带着城市
    （"去库尔勒玩2天…住宿换成 库尔勒福润德·万达锦华酒店"）—— 于是任何以城市名开头的前缀
    都会命中任意一句提到该城市的请求：实测把"住宿换成 X 酒店"读成了"把**库尔勒民俗文化博物馆**
    当成想换的那一家"。去掉城市之后，剩下的核心词才有区分度。

    光说"酒店/餐厅"这种类别词**不算点名**（那只是类别偏好，不是"就要这一家"）。
    """
    key = normalize_text(name)
    text = normalized_text
    city_key = normalize_text(city)
    if city_key:
        key = key.replace(city_key, "")
        text = text.replace(city_key, "")
    if len(key) < 2:
        return False
    if key in text:
        return True
    core = key
    for word in _GENERIC_PLACE_WORDS:
        core = core.replace(normalize_text(word), "")
    if len(core) < 2:
        return False
    if core in text:
        return True
    # 前缀匹配：从最长往短试，≥3 字才算"说得出是哪一家"
    for size in range(len(core), 2, -1):
        if core[:size] in text:
            return True
    return False


def parse_increment(
    text: str,
    previous_plan: Any = None,
    known_names: Optional[Iterable[str]] = None,
    city: str = "",
) -> Dict[str, Any]:
    """把一句话解析成结构化 delta（**只解析，不执行**）。

    返回：`{text, exclude[], quota{}, upgrade{}, downgrade{}, days?, budget?, swap_to?, matched[]}`
    - `exclude` 尽量用**方案里真实存在的节点名**（比正则抓词可靠得多）；
    - `quota` 是"相对变化量"（+2 表示再加两个餐饮节点）；
    - `swap_to` 是用户**点名要换成的那一家**（候选池/方案里的名字），空表示没点名；
    - `city` 只用于"点名匹配"时去掉城市名（否则任何以城市开头的地点名都会误命中）。
    """
    raw = str(text or "")
    plan_names: List[str] = []
    if previous_plan is not None:
        plan_names.extend(node_name(node) for node in nodes_of(previous_plan) if node_name(node))
    plan_names.extend(str(name) for name in (known_names or []) if str(name).strip())

    normalized = normalize_text(raw)
    matched: List[str] = []

    # ---- 指定换成哪一家（**必须先于排他判定**）----
    # 用户点了「换住这家」/ 说"住 XX 酒店"时，要的是**那一家**（候选池里的），
    # 而"换成 X"里的"换成"同时也在 EXCLUDE_HINTS 的语感里（"换成别的"）——
    # 先判排他就会把用户**想要**的那家当成"不想要的"排掉，正好反了。
    swap_to: Optional[str] = None
    swap_kind: str = ""
    if any(hint in raw for hint in SWAP_HINTS):
        for name in plan_names:
            label = str(name or "").strip()
            if not label:
                continue
            if _mentions_specific_place(label, normalized, city=city):
                swap_to = label
                swap_kind = str(intent_of(label) or "other")
                matched.append(f"swap_to:{label}")
                break

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
            # 已经被当成"想换成的那一家"的名字，不能再当成"要排除的"
            if swap_to and key == normalize_text(swap_to):
                continue
            if _mentions_place(name, normalized):
                seen.add(key)
                exclude.append(name)

    # ---- 配额：增减类说法 + 意图（**按分句**判定：一句话里可能同时有"少看博物馆 + 多排自然"） ----
    quota: Dict[str, int] = {}
    clauses = [part for part in CLAUSE_SPLIT.split(raw) if part.strip()] or [raw]
    for clause in clauses:
        clause_intent = _detect_intent(clause)
        if not clause_intent:
            continue
        count = _detect_explicit_count(clause) or DEFAULT_STEP
        count = max(1, min(MAX_STEP, count))
        if any(hint in clause for hint in INCREASE_HINTS):
            quota[clause_intent] = quota.get(clause_intent, 0) + count
            matched.append(f"increase:{clause_intent}")
        elif any(hint in clause for hint in REDUCE_HINTS):
            quota[clause_intent] = quota.get(clause_intent, 0) - count
            matched.append(f"reduce:{clause_intent}")

        # “不想去博物馆/博物院”不是一个地点偏好，而是类别级硬排除。
        # 不能只依赖 known_names：模型可能已经生成了多个同类场馆，
        # 也不能在最终类别门禁中再次把文化节点补回来。
        if any(hint in clause for hint in EXCLUDE_HINTS) and clause_intent:
            quota[clause_intent] = min(quota.get(clause_intent, 0), -MAX_STEP)
            matched.append(f"exclude_category:{clause_intent}")
    if not quota:
        # 兜底：整句判定（老行为，覆盖"分句切不出来但整句能看出意图"的情况）
        intent = _detect_intent(raw)
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
        # 用户**点名要换成哪一家**（候选池里的名字）；空表示没点名
        "swap_to": swap_to,
        "swap_kind": swap_kind,
        "matched": matched,
        "is_noop": not (exclude or quota or upgrade or downgrade or days or budget or swap_to),
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
    node = {
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
    # 🤝 补住宿时必须显式打 is_hotel：高德会把"度假营地/漫谷"这类归到
    #    `体育休闲服务`，名字里也没有"酒店/民宿"，下游 `_is_lodging` 只看名字和
    #    is_hotel —— 实测（2026-11-02 四川 4 天）：第 1 天补的「成都·云溪漫谷」
    #    因此没被算作住宿，周全判定成"2/3 晚有住宿"、整个方案 verdict=fail。
    if intent == "hotel":
        node["is_hotel"] = True
    return node


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
            # 普通“少一点”仍保留一体化底线；类别级“不要博物馆/博物院”
            # 会被编码为 -MAX_STEP，明确允许该类别清零。
            floor = 0 if intent in {"shopping", "nightlife", "photo"} or abs(delta) >= MAX_STEP else 1
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


def apply_swap_to(
    plan: Any,
    swap_to: str,
    *,
    pool: Optional[Sequence[Mapping[str, Any]]] = None,
    kind: str = "",
) -> Dict[str, Any]:
    """把方案里**同一天的那一类节点**就地换成用户点名的那一家（`swap_to`）。

    为什么放在 core（2026-10-05）：用户在「有实价的住宿可选」里挑了一家，
    如果只是把清单摆出来、点不动，等于"给了选择权但兑现不了"。
    规则保守：
    - 只在**候选池/方案里能找到同名条目**时才换（找不到如实说没换成，不编一个酒店）；
    - 优先换**住宿**（`kind == "hotel"` 时只动住宿节点），且**保留原来的 day/time**
      （一天的形状不变，不需要重排）；
    - 已经是同一家就什么都不做（避免"换成它自己"的无意义动作）。

    返回 `{plan, action}`（`action` 为空表示没换）。
    """
    target_name = str(swap_to or "").strip()
    if not target_name:
        return {"plan": plan, "action": None}

    nodes = [dict(node) for node in nodes_of(plan)]
    desired: Optional[Mapping[str, Any]] = None
    for item in pool or []:
        if isinstance(item, Mapping) and normalize_text(item.get("name")) == normalize_text(target_name):
            desired = item
            break
    if desired is None:
        # 不在传入的池子里，但方案里可能已经有这个名字（那也算"找到了"）
        for node in nodes:
            if normalize_text(node_name(node)) == normalize_text(target_name):
                desired = node
                break
    if desired is None:
        return {"plan": plan, "action": None, "reason": f"候选池里没有「{target_name}」，没有可换的那一家"}

    wanted_kind = str(kind or "").strip().lower()
    replacement_index: Optional[int] = None
    for index, node in enumerate(nodes):
        if normalize_text(node_name(node)) == normalize_text(target_name):
            return {"plan": plan, "action": None, "reason": "方案里已经是这一家了"}
        if wanted_kind and intent_of(node) != wanted_kind:
            continue
        if not wanted_kind and intent_of(node) not in {"hotel", "food", "scenic", "cultural"}:
            continue
        replacement_index = index
        break
    if replacement_index is None:
        return {"plan": plan, "action": None, "reason": f"方案里没有可替换的{kind or '节点'}"}

    old = nodes[replacement_index]
    new_node = dict(old)
    new_node["name"] = str(desired.get("name"))
    new_node["location"] = str(desired.get("name"))
    if isinstance(desired.get("lnglat"), (list, tuple)) and len(desired["lnglat"]) >= 2:
        new_node["lnglat"] = [float(desired["lnglat"][0]), float(desired["lnglat"][1])]
    if str(desired.get("type") or "").strip():
        new_node["type"] = str(desired["type"])
    if desired.get("price") is not None:
        new_node["cost_estimate"] = f"¥{float(desired['price']):g}"
        sources = dict(new_node.get("data_sources") or {})
        sources["cost_estimate"] = str(desired.get("price_source") or desired.get("data_source") or "candidate_pool")
        new_node["data_sources"] = sources
        new_node["estimated"] = False if sources["cost_estimate"] in {"rollinggo", "amap", "provider", "vendor", "official"} else new_node.get("estimated", True)
    new_node["replaced_from"] = node_name(old)
    new_node["replaced_reason"] = "你点名要换成这一家"
    # 用户点名的选择不能被后面的自动优化悄悄改掉（例如"一天跑不了两个远景区"把它移出）
    new_node["user_choice"] = True
    nodes[replacement_index] = new_node
    new_plan: Any = dict(plan) if isinstance(plan, Mapping) else {"route": nodes}
    new_plan["route"] = nodes
    return {
        "plan": new_plan,
        "action": {
            "day": old.get("day"),
            "from": node_name(old),
            "to": str(desired.get("name")),
            "kind": intent_of(old),
            "reason": "你点名要换成这一家",
        },
        "reason": "",
    }


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
