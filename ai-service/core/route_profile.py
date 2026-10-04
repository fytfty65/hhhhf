"""参考路线画像：把"用户喜欢的一条路线"（例如小红书帖子正文）抽成**结构化偏好特征**。

设计取向（与全仓库一致）：
- **纯函数、可离线测试**：不碰网络、不碰数据库、不调模型；
- **每条特征都要有出处**（evidence）：特征值能指回原文片段，否则用户无法信任，我们也没法解释；
- **抽不到就是 unknown**：绝不猜、不用默认值冒充"学到了"；
- **派生只做"检索提示"**：画像不直接生成行程，而是转成检索关键词 / 配额 / 节奏这些**可解释的旋钮**，
  真正怎么排还是走既有的规划链路（这样画像出问题时影响面可控、可回滚）。

与既有机制的关系：网关的 `UserPreference`（NatureRatio/CultureRatio/FoodRatio）来自**反馈学习**；
本模块处理的是**用户主动提供的一条参考路线**。两者在 ai-service 侧都作为提示注入，
但**用户当轮的明确诉求优先级最高**（见 `reference_profile_hint`）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# 类别桶：抽取"这条路线里都在干什么"。用多字短语，避免"景观"这类宽词误判
# （"人文景观"曾被误判为自然，见 tests/test_scenic_preference.py 的教训）。
CATEGORY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "scenic": (
        "自然景观", "自然风光", "自然风景", "风景", "景区", "公园", "湖泊", "湖边", "雪山", "草原",
        "森林", "湿地", "沙漠", "戈壁", "峡谷", "山川", "山水", "海岛", "海边", "日出", "日落",
        "星空", "徒步", "爬山", "观景", "绿洲", "天池", "温泉",
    ),
    "cultural": (
        "博物馆", "博物院", "文庙", "古迹", "遗址", "古城", "古镇", "寺庙", "寺", "庙", "历史",
        "人文景观", "文化", "非遗", "展览", "美术馆", "纪念馆", "石窟", "壁画", "民俗",
    ),
    "food": (
        "美食", "小吃", "餐厅", "夜市", "烧烤", "火锅", "甜品", "咖啡", "早茶", "面馆", "大盘鸡",
        "本地菜", "特色菜", "老字号", "吃的", "吃", "菜市场", "巴扎", "市场小吃",
    ),
    "shopping": ("购物", "商场", "市集", "文创", "手作", "买手", "百货", "步行街"),
    "nightlife": ("夜生活", "酒吧", "清吧", "夜景", "夜游", "夜市", "夜宵", "看夜景"),
    "photo": ("拍照", "出片", "机位", "打卡", "写真", "出片点", "日落机位"),
}

PACE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "relaxed": ("轻松", "不赶", "慢节奏", "悠闲", "躺平", "休闲", "睡到自然醒", "佛系"),
    "packed": ("暴走", "特种兵", "打卡狂", "赶场", "一天逛完", "紧凑", "高强度"),
}

TIME_STYLE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "early_bird": ("早起", "清晨", "日出", "早上出发", "看日出"),
    "night_owl": ("夜猫子", "夜市", "夜宵", "凌晨", "看夜景", "夜游", "晚上逛"),
}

BUDGET_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "economy": ("性价比", "穷游", "便宜", "省钱", "平价", "预算有限", "青旅"),
    "mid": ("舒适", "中端", "人均两三百", "品质中端"),
    "luxury": ("奢华", "五星", "高端", "豪华", "度假酒店", "精品酒店", "私汤"),
}

TRANSPORT_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "walk": ("徒步", "步行", "走路", "citywalk", "Citywalk", "步行街"),
    "metro": ("地铁", "公交", "公共交通"),
    "drive": ("自驾", "开车", "租车"),
    "charter": ("包车", "拼车", "司机"),
    "train": ("火车", "高铁", "动车"),
    "flight": ("飞机", "航班"),
}

LODGING_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "hotel": ("酒店", "度假酒店", "宾馆"),
    "homestay": ("民宿", "客栈", "小院", "院子"),
    "hostel": ("青旅", "青年旅舍", "床位"),
}

WALKING_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "high": ("徒步", "爬山", "穿越", "暴走", "登山"),
    "low": ("打车", "包车", "自驾", "缆车", "摆渡车"),
}

# 每天几个点：显式写法优先（"一天 3 个地方"／"每天2-3处"）
NODES_PER_DAY_PATTERN = re.compile(r"(?:每天|一天|一日|每日)\s*(?:安排|去|逛|玩)?\s*(\d{1,2})(?:\s*[-~至]\s*(\d{1,2}))?\s*(?:个|处|站|地|景点|地方)")
BUDGET_AMOUNT_PATTERN = re.compile(r"人均\s*[¥￥]?\s*(\d{2,5})\s*(?:元|块)?")
SNIPPET_WINDOW = 14


def _snippet(text: str, start: int, end: int) -> str:
    """取命中片段附近的原文（给用户看"我是从哪句读出来的"）。"""
    left = max(0, start - SNIPPET_WINDOW)
    right = min(len(text), end + SNIPPET_WINDOW)
    return text[left:right].strip().replace("\n", " ")


def _find_evidence(text: str, keywords: Iterable[str]) -> Tuple[str, str]:
    """返回 (命中的关键词, 出处片段)；没命中返回 ("", "")。"""
    lowered = text.lower()
    best: Tuple[str, str] = ("", "")
    for keyword in keywords:
        index = lowered.find(keyword.lower())
        if index >= 0:
            snippet = _snippet(text, index, index + len(keyword))
            # 更长的关键词更具体，优先作为证据
            if not best[0] or len(keyword) > len(best[0]):
                best = (keyword, snippet)
    return best


def _count_categories(text: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        hits = 0
        for keyword in keywords:
            hits += text.lower().count(keyword.lower())
        if hits:
            counts[category] = hits
    return counts


def extract_route_profile(text: str, *, source_label: str = "") -> Dict[str, Any]:
    """把一段参考路线文本抽成画像（纯函数）。

    返回结构：
        {
          "features": {category_mix, pace, time_style, budget_tier, budget_amount,
                       transport, lodging_style, walking_intensity},
          "evidence": {特征名: 原文片段},
          "unknown":  [抽不到的特征名],
          "confidence": 0~1（有出处的特征占比）,
          "text_chars": 原文长度,
          "source_label": 来源标签（用户自己写的备注，可为空）,
        }
    """
    raw = str(text or "")
    evidence: Dict[str, str] = {}
    features: Dict[str, Any] = {}

    mix = _count_categories(raw)
    if mix:
        features["category_mix"] = mix
        for category in mix:
            keyword, snippet = _find_evidence(raw, CATEGORY_KEYWORDS[category])
            if snippet:
                evidence[f"category_mix.{category}"] = snippet
    else:
        features["category_mix"] = {}

    nodes_per_day: Optional[int] = None
    match = NODES_PER_DAY_PATTERN.search(raw)
    if match:
        nodes_per_day = int(match.group(1))
        evidence["nodes_per_day"] = _snippet(raw, match.start(), match.end())

    pace = "unknown"
    for candidate, keywords in PACE_KEYWORDS.items():
        keyword, snippet = _find_evidence(raw, keywords)
        if keyword:
            pace = candidate
            evidence["pace"] = snippet
            break
    if pace == "unknown" and nodes_per_day is not None:
        # 由"每天几个点"推断节奏，并如实标注来源是数量而不是形容词
        pace = "packed" if nodes_per_day >= 5 else ("relaxed" if nodes_per_day <= 2 else "balanced")
        evidence["pace"] = f"由「每天 {nodes_per_day} 个点」推断"
    features["pace"] = pace
    features["nodes_per_day"] = nodes_per_day

    time_style = "unknown"
    for candidate, keywords in TIME_STYLE_KEYWORDS.items():
        keyword, snippet = _find_evidence(raw, keywords)
        if keyword:
            time_style = candidate
            evidence["time_style"] = snippet
            break
    features["time_style"] = time_style

    budget_tier = "unknown"
    for candidate, keywords in BUDGET_KEYWORDS.items():
        keyword, snippet = _find_evidence(raw, keywords)
        if keyword:
            budget_tier = candidate
            evidence["budget_tier"] = snippet
            break
    amount_match = BUDGET_AMOUNT_PATTERN.search(raw)
    budget_amount: Optional[int] = int(amount_match.group(1)) if amount_match else None
    if amount_match:
        evidence["budget_amount"] = _snippet(raw, amount_match.start(), amount_match.end())
        if budget_tier == "unknown":
            value = budget_amount or 0
            budget_tier = "economy" if value <= 200 else ("mid" if value <= 600 else "luxury")
            evidence["budget_tier"] = f"由「人均 ¥{value}」推断"
    features["budget_tier"] = budget_tier
    features["budget_amount"] = budget_amount

    transports: List[str] = []
    for candidate, keywords in TRANSPORT_KEYWORDS.items():
        keyword, snippet = _find_evidence(raw, keywords)
        if keyword:
            transports.append(candidate)
            evidence[f"transport.{candidate}"] = snippet
    features["transport"] = transports

    lodging_style = "unknown"
    for candidate, keywords in LODGING_KEYWORDS.items():
        keyword, snippet = _find_evidence(raw, keywords)
        if keyword:
            lodging_style = candidate
            evidence["lodging_style"] = snippet
            break
    features["lodging_style"] = lodging_style

    walking_intensity = "unknown"
    for candidate, keywords in WALKING_KEYWORDS.items():
        keyword, snippet = _find_evidence(raw, keywords)
        if keyword:
            walking_intensity = candidate
            evidence["walking_intensity"] = snippet
            break
    features["walking_intensity"] = walking_intensity

    tracked = ("category_mix", "pace", "time_style", "budget_tier", "transport", "lodging_style", "walking_intensity")
    unknown = [
        key for key in tracked
        if (features.get(key) in ("unknown", None, [], {}))
    ]
    known = len(tracked) - len(unknown)
    confidence = round(known / len(tracked), 2) if tracked else 0.0

    return {
        "features": features,
        "evidence": evidence,
        "unknown": unknown,
        "confidence": confidence,
        "reference_count": 1,  # 单条抽取也算"参考了 1 条"，与 merge_profiles 的口径保持一致
        "text_chars": len(raw),
        "source_label": str(source_label or ""),
    }


def merge_profiles(profiles: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """合并多条参考（同一用户学过多条路线时的口径）。

    - 类别计数**累加**（出现得越多越代表偏好）；
    - 单值特征取**出现次数最多**的值（平票时保留先出现的，稳定可预测）；
    - 出处合并保留，便于解释"这是从哪几条路线读出来的"。
    """
    valid = [profile for profile in profiles if isinstance(profile, Mapping) and profile.get("features")]
    if not valid:
        return {"features": {}, "evidence": {}, "unknown": [], "confidence": 0.0, "reference_count": 0}

    mixed: Dict[str, int] = {}
    votes: Dict[str, Dict[str, int]] = {}
    evidence: Dict[str, str] = {}
    unknown_counter: Dict[str, int] = {}
    text_chars = 0

    for profile in valid:
        features = profile.get("features") or {}
        for category, count in (features.get("category_mix") or {}).items():
            mixed[category] = mixed.get(category, 0) + int(count)
        for key in ("pace", "time_style", "budget_tier", "lodging_style", "walking_intensity"):
            value = features.get(key)
            if isinstance(value, str) and value and value != "unknown":
                votes.setdefault(key, {})[value] = votes.setdefault(key, {}).get(value, 0) + 1
        for value in features.get("transport") or []:
            votes.setdefault("transport", {})[value] = votes.setdefault("transport", {}).get(value, 0) + 1
        for key, snippet in (profile.get("evidence") or {}).items():
            evidence.setdefault(key, snippet)
        for key in profile.get("unknown") or []:
            unknown_counter[key] = unknown_counter.get(key, 0) + 1
        text_chars += int(profile.get("text_chars") or 0)

    features: Dict[str, Any] = {"category_mix": mixed}
    for key, tally in votes.items():
        winner = max(tally.items(), key=lambda item: (item[1], -list(tally).index(item[0])))[0]
        features[key] = winner if key != "transport" else [name for name, _ in sorted(tally.items(), key=lambda item: -item[1])]
    for key in ("pace", "time_style", "budget_tier", "lodging_style", "walking_intensity"):
        features.setdefault(key, "unknown")
    features.setdefault("transport", [])

    # 多条里都没抽到的特征才算 unknown（一条抽到就算已知）
    unknown = [key for key, count in unknown_counter.items() if count >= len(valid)]
    tracked = ("category_mix", "pace", "time_style", "budget_tier", "transport", "lodging_style", "walking_intensity")
    for key in tracked:
        value = features.get(key)
        if value in ("unknown", None, [], {}) and key not in unknown:
            unknown.append(key)
    known = len(tracked) - len([key for key in unknown if key in tracked])
    return {
        "features": features,
        "evidence": evidence,
        "unknown": sorted(set(unknown)),
        "confidence": round(known / len(tracked), 2),
        "reference_count": len(valid),
        "text_chars": text_chars,
    }


def profile_to_retrieval_hints(profile: Mapping[str, Any], *, poi_limit: int = 40) -> Dict[str, Any]:
    """画像 → **可解释的检索提示**（关键词倾向、配额、节奏）。

    只使用**已知**特征；未知一律不影响（不猜）。返回结构里 `reasons` 说明每个旋钮是由哪条特征推出来的，
    便于前端如实显示"这次为什么这样排"。
    """
    features = (profile or {}).get("features") or {}
    mix = features.get("category_mix") or {}
    scenic_hits = int(mix.get("scenic", 0))
    cultural_hits = int(mix.get("cultural", 0))
    food_hits = int(mix.get("food", 0))

    reasons: List[str] = []
    wants_scenic = scenic_hits > cultural_hits
    if scenic_hits or cultural_hits:
        reasons.append(f"参考路线里自然 {scenic_hits} 处 / 人文 {cultural_hits} 处")

    total = max(1, poi_limit)
    if wants_scenic:
        quota = {"scenic": total, "cultural": max(6, total // 4), "shopping": max(8, total // 3)}
    else:
        quota = {"scenic": max(10, total // 2), "cultural": max(12, total // 2), "shopping": max(8, total // 3)}

    pace = features.get("pace")
    daily_nodes = {"relaxed": 3, "balanced": 4, "packed": 6}.get(str(pace), None)
    if daily_nodes:
        reasons.append(f"参考路线节奏「{pace}」→ 每天约 {daily_nodes} 个节点")

    keywords: List[str] = []
    if wants_scenic:
        keywords.extend(["自然景观", "自然保护区", "地质公园", "湿地公园", "森林公园"])
        reasons.append("自然多于人文 → 景点检索偏向自然")
    elif cultural_hits:
        keywords.extend(["文化地标", "历史古迹", "博物馆"])
        reasons.append("人文多于自然 → 景点检索偏向人文")

    if food_hits:
        reasons.append(f"参考路线里餐饮出现 {food_hits} 次 → 提高美食优先级")

    budget_tier = features.get("budget_tier")
    if budget_tier in ("economy", "mid", "luxury"):
        reasons.append(f"消费倾向「{budget_tier}」")

    return {
        "poi_limit": total,
        "quota": quota,
        "daily_nodes": daily_nodes,
        "prefer_scenic": wants_scenic,
        "food_priority": food_hits > 0,
        "budget_tier": budget_tier if budget_tier in ("economy", "mid", "luxury") else None,
        "extra_scenic_keywords": keywords,
        "reasons": reasons,
        "confidence": float((profile or {}).get("confidence") or 0.0),
    }


def reference_profile_hint(
    profile: Optional[Mapping[str, Any]],
    *,
    explicit_signals: Optional[Mapping[str, Any]] = None,
    poi_limit: int = 40,
) -> Optional[Dict[str, Any]]:
    """决定这次规划要不要用画像 —— **用户当轮的明确诉求永远优先**。

    返回 None 表示不用（诉求已经说清楚了）；否则返回 {hints, disclosure, source}。
    `disclosure` 是给用户看的原话，必须说明"这次是按你参考过的路线偏好来的"。
    """
    if not profile or not (profile.get("features") or {}):
        return None
    signals = explicit_signals or {}
    has_explicit = bool(signals.get("requirements")) and signals.get("requirements") != ["常规观光、特色美食与舒适住宿"]
    if has_explicit:
        # 诉求明确时，画像只作为兜底不参与决策（避免"我明明说了"却被画像覆盖）
        return None
    hints = profile_to_retrieval_hints(profile, poi_limit=poi_limit)
    if not hints.get("reasons"):
        return None
    count = int(profile.get("reference_count") or 1)
    disclosure = (
        f"这次没有额外指定偏好，所以按你参考过的 {count} 条路线来的："
        + "；".join(hints["reasons"])
        + "。想改就直接说（例如「这次多看人文」），诉求会覆盖画像。"
    )
    return {"hints": hints, "disclosure": disclosure, "source": "reference_route", "reference_count": count}


__all__ = [
    "CATEGORY_KEYWORDS",
    "extract_route_profile",
    "merge_profiles",
    "profile_to_retrieval_hints",
    "reference_profile_hint",
]
