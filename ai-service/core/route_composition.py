"""行程配比口径（用户 2026-10-04 明确的口味，逐条落成可测规则）。

三条口径
--------
1. **默认（用户没说偏好）**：人文景观与自然景观**占比相同**，但必须穿插特色美食与酒店；
2. **自然景观 = 景区**（赛里木湖、喀纳斯这种：湖/雪山/草原/沙漠/峡谷/国家级景点/自然保护区）。
   城市公园**不算**自然景观 —— "景色很好的公园可以"，但那是"可用的公园"，
   它顶不了自然景观的名额（实测：库尔勒一次 12 节点方案里 5 个是城市公园，
   看起来"自然很多"，实际一个真景区都没有）；
3. **用户明确说了就加权**：想看美食 → 特色美食权重上升，自然/人文按比例让位。

这一层只算**目标**（纯函数，无 IO）：回答"这一趟应该长什么样"。
取数与排布由 poi_pool / agent 负责，最后再用 `composition_audit` 对账 ——
对不上就如实说，而不是悄悄交付一个"看起来挺全"的方案。
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

# —— 默认配比（白天节点份额）——
DEFAULT_SCENIC_SHARE = 0.4
DEFAULT_CULTURAL_SHARE = 0.4
DEFAULT_FOOD_SHARE = 0.2

# —— 明确偏好时的配比 ——
PREFERENCE_SHARES: Dict[str, Dict[str, float]] = {
    "scenic": {"scenic": 0.6, "cultural": 0.2, "food": 0.2},
    "cultural": {"scenic": 0.2, "cultural": 0.6, "food": 0.2},
    "food": {"scenic": 0.325, "cultural": 0.325, "food": 0.35},
    "scenic+food": {"scenic": 0.5, "cultural": 0.2, "food": 0.3},
    "cultural+food": {"scenic": 0.2, "cultural": 0.5, "food": 0.3},
    "scenic+cultural": {"scenic": 0.4, "cultural": 0.4, "food": 0.2},
    # 用户明确"只想看自然"（排除人文）：景区为主、穿插美食，住宿单独算 ——
    # 这时规划形态是"在更大范围里把几个景区串成最优路径"，不该再有人文占比。
    "no_cultural": {"scenic": 0.7, "cultural": 0.0, "food": 0.3},
}

# 自然景观（景区）判定用的词，分强弱两档 —— 这是被真实数据逼出来的：
#   「龙山公园」里有"山"、「恒友广场步行街」里有"广场"，单字/泛词一视同仁就会误判。
#   强自然词（湿地/森林/胡杨/草原/沙漠/峡谷/雪山…）出现即算自然景观，
#   哪怕它叫"XX湿地公园"（用户原话："景色很好的公园可以"）；
#   弱自然词（湖/河/山/峰…）只有在**名字里没有公园类词**时才算。
STRONG_NATURE_WORDS = (
    "湿地", "森林", "胡杨", "草原", "沙漠", "戈壁", "绿洲", "峡谷", "雪山", "冰川",
    "瀑布", "天池", "丹霞", "溶洞", "花海", "温泉", "群岛", "岛屿", "海滩", "海湾",
    "自然保护区", "地质公园", "国家公园", "保护区", "水利风景区",
)
WEAK_NATURE_WORDS = ("湖", "河", "江", "海", "山", "峰", "岭")
# 城市公园类：这些地方可以去，但不算自然景观
PARK_WORDS = ("公园", "游乐园", "动物园", "植物园", "乐园", "园林")
# 高德把真景区标成这些类别（"铁门关"这种没有自然词的景区靠这条进 landmark）
LANDMARK_TYPE_WORDS = ("风景名胜", "国家级景点", "自然保护区", "景区", "旅游景点")


def scenic_tier(name: Any, poi_type: Any = "") -> str:
    """自然候选分档：`landmark`（真景区/自然景观）| `city_park`（城市公园类）| ``（其它）。

    判定顺序（顺序很重要：强自然词 → 公园词 → 弱自然词 → 高德类别）：
      1. 强自然词（湿地/森林/胡杨/草原/沙漠/峡谷/雪山/天池/保护区…）→ `landmark`
         —— **"景色很好的公园"就是靠这条进来的**（湿地公园、森林公园、湖滨公园）；
      2. 名字是公园/游乐园/动物园/植物园/园林，或类别是"公园广场" → `city_park`
         （"孔雀公园""龙山公园"没有强自然词，落这里）；
      3. 名字里有弱自然词（湖/河/山/峰…）→ `landmark`（"博斯腾湖""孔雀河景观带"）；
      4. 类别是高德"风景名胜/国家级景点/自然保护区/景区" → `landmark`
         （"铁门关""罗布人村寨"这类没有自然词的景区靠这条）；
      5. 其余 → 空串（不是自然也不是公园，交给常规类别判断）。
    """
    name_text = str(name or "")
    type_text = str(poi_type or "")
    text = f"{name_text} {type_text}"
    if not text.strip():
        return ""
    if any(word in text for word in STRONG_NATURE_WORDS):
        return "landmark"
    park_like = any(word in name_text for word in PARK_WORDS) or "公园广场" in type_text
    if park_like:
        return "city_park"
    if any(word in name_text for word in WEAK_NATURE_WORDS):
        return "landmark"
    if any(word in type_text for word in LANDMARK_TYPE_WORDS):
        return "landmark"
    return ""


def _allocate(shares: Mapping[str, float], total: int) -> Dict[str, int]:
    """按份额分配整数名额（最大余数法），保证总和恰好等于 total。"""
    if total <= 0:
        return {key: 0 for key in shares}
    positive = {key: max(0.0, float(value)) for key, value in shares.items()}
    share_sum = sum(positive.values())
    if share_sum <= 0:
        # 没有任何份额（例如全部被排除）→ 全给第一个类别，避免出现空目标
        first = next(iter(positive), "scenic")
        return {key: (total if key == first else 0) for key in positive}
    exact = {key: value / share_sum * total for key, value in positive.items()}
    floors = {key: int(math.floor(value)) for key, value in exact.items()}
    remainder = total - sum(floors.values())
    order = sorted(exact, key=lambda key: (exact[key] - floors[key], exact[key]), reverse=True)
    for index in range(remainder):
        floors[order[index % len(order)]] += 1
    return floors


def composition_targets(
    *,
    trip_days: int,
    daily_nodes: int,
    wants_scenic: bool = False,
    wants_cultural: bool = False,
    wants_food: bool = False,
    exclude_cultural: bool = False,
    wants_hotel: bool = False,
) -> Dict[str, Any]:
    """算这一趟的目标配比（白天节点 + 住宿夜数）。

    返回 `{scenic, cultural, food, hotel, daytime, source, shares}`：
      - `source` = `explicit_preference`（用户明确说了）| `default_balanced`（默认各半）；
      - 餐饮有下限"每天至少一顿"，因为它是硬需求，不该被份额算出 0；
      - 住宿按夜数（`trip_days - 1`，至少 1）算，它是夜间节点，不占白天名额。
    """
    days = max(1, int(trip_days or 1))
    per_day = max(1, int(daily_nodes or 1))
    daytime = days * per_day

    if exclude_cultural:
        key = "no_cultural"
    else:
        wants = [name for name, flag in (("scenic", wants_scenic), ("cultural", wants_cultural), ("food", wants_food)) if flag]
        key = "+".join(sorted(wants)) if wants else "default"
    shares = PREFERENCE_SHARES.get(key)
    source = "default_balanced"
    if shares is None:
        shares = {"scenic": DEFAULT_SCENIC_SHARE, "cultural": DEFAULT_CULTURAL_SHARE, "food": DEFAULT_FOOD_SHARE}
    else:
        source = "explicit_preference"

    allocation = _allocate(shares, daytime)
    # 餐饮下限：一天至少一顿在外面吃（用户要"穿插特色美食"）
    food_floor = max(1, days) if "food" in shares else 0
    if allocation.get("food", 0) < food_floor:
        deficit = food_floor - allocation.get("food", 0)
        allocation["food"] = food_floor
        # 从份额最大的类别里扣，保持总数不变
        donors = sorted(
            (name for name in allocation if name != "food"),
            key=lambda name: allocation[name],
            reverse=True,
        )
        for name in donors:
            if deficit <= 0:
                break
            take = min(deficit, max(0, allocation[name] - 1))
            allocation[name] -= take
            deficit -= take
        if deficit > 0:
            # 实在腾不出来（天数 > 白天名额）→ 总数超一点，也要保证有饭吃
            daytime += deficit

    hotel = max(1, days - 1)
    if wants_hotel:
        hotel = max(hotel, days)

    return {
        "scenic": int(allocation.get("scenic", 0)),
        "cultural": int(allocation.get("cultural", 0)),
        "food": int(allocation.get("food", 0)),
        "hotel": int(hotel),
        "daytime": int(daytime),
        "source": source,
        "exclude_cultural": bool(exclude_cultural),
        "shares": {key: round(float(value), 3) for key, value in shares.items()},
    }


def summarize_targets(targets: Mapping[str, Any]) -> str:
    """目标配比 → 一句话（界面与日志共用）。"""
    base = (
        f"自然景区 {targets.get('scenic', 0)} · 人文 {targets.get('cultural', 0)} · "
        f"特色美食 {targets.get('food', 0)} · 住宿 {targets.get('hotel', 0)}"
    )
    if targets.get("source") == "default_balanced":
        return base + "（你没特别指定，默认自然与人文各半，并穿插美食与住宿）"
    return base + "（按你这轮明确说的偏好加权）"


def composition_hint(targets: Mapping[str, Any], *, park_cap: Optional[int] = None) -> str:
    """给模型的**目标配比提示**（用户 2026-10-04 把这件事交给我们定）。

    口径（为什么这么写）：
      - 写进去有用：实测默认那轮模型把人文排成 2 个（目标 4），只靠候选池与事后对账补不回来；
      - 但不能写成死指标：模型会为了凑数硬塞一个博物馆 —— 所以明确写"不要为凑数硬塞"，
        并给出优先级（用户明说的诉求 > 配比目标）；
      - 必须重申"自然景观 = 景区，城市公园不算"，否则它又会拿公园充自然；
      - 用户明确排除的类别一个都不要排。
    """
    scenic = int(targets.get("scenic", 0) or 0)
    cultural = int(targets.get("cultural", 0) or 0)
    food = int(targets.get("food", 0) or 0)
    hotel = int(targets.get("hotel", 0) or 0)
    lines = [
        f"白天节点大致按「自然景区 {scenic} · 人文 {cultural} · 特色美食 {food}」分配，住宿 {hotel} 处（按夜数）。",
        "自然景观只算**景区**（湖/雪山/草原/沙漠/峡谷/湿地森林/国家级景点）；城市公园**不算**自然景观，"
        + (f"全程最多 {park_cap} 个。" if park_cap is not None else "不要拿它充数。"),
        "美食要**穿插到每一天**，不要集中在一两天；住宿按夜数收尾。",
        "**不要为了凑数量硬塞**：候选里没有合适的，就少排一个并说明；宁可少，也不要排一个不合适的地点。",
        "用户在诉求里明确说过的偏好 > 这份配比；用户明确排除的类别（例如说的不要人文）一个都不要排。",
    ]
    return "\n".join(lines)


COMPOSITION_LABELS = {"scenic": "自然景区", "cultural": "人文", "food": "特色美食", "hotel": "住宿"}


def composition_audit(
    actual: Mapping[str, int],
    targets: Mapping[str, Any],
    *,
    tolerance: int = 1,
    forbidden: Sequence[str] = (),
) -> Dict[str, Any]:
    """实际配比 vs 目标配比：对不对得上，对不上差在哪。

    容差刻意给 1 个节点（模型排布有合理弹性），但两种情况必须报出来：
      - 目标 > 0 而实际 = 0（整类缺失，不是弹性）；
      - `forbidden` 里的类别**一个都不该有**（用户明确说了不要人文，就不许再出现博物馆）——
        目标为 0 的类别默认会被跳过，所以要靠这个参数把"用户明说不要"钉住。
    """
    deviations = []
    notes = []
    for intent, label in ((key, COMPOSITION_LABELS[key]) for key in ("scenic", "cultural", "food", "hotel")):
        want = int(targets.get(intent, 0) or 0)
        got = int(actual.get(intent, 0) or 0)
        if want <= 0:
            continue
        if got == 0 and want >= 1:
            deviations.append({"intent": intent, "target": want, "actual": got, "severity": "missing"})
            notes.append(f"这次一个{label}节点都没有（目标 {want} 个）")
        elif abs(got - want) > tolerance:
            severity = "over" if got > want else "under"
            deviations.append({"intent": intent, "target": want, "actual": got, "severity": severity})
            notes.append(f"{label} {got} 个，目标 {want} 个（{'偏多' if severity == 'over' else '偏少'}）")

    for intent in forbidden:
        if intent not in COMPOSITION_LABELS:
            continue
        got = int(actual.get(intent, 0) or 0)
        if got > 0:
            label = COMPOSITION_LABELS[intent]
            deviations.append({"intent": intent, "target": 0, "actual": got, "severity": "forbidden"})
            notes.append(f"你说了不要{label}，但方案里还有 {got} 个{label}节点")

    return {
        "ok": not deviations,
        "targets": dict(targets),
        "actual": {key: int(value) for key, value in actual.items()},
        "deviations": deviations,
        "notes": notes,
        "tolerance": tolerance,
        "forbidden": [str(item) for item in forbidden],
    }


def count_route_composition(route: Sequence[Mapping[str, Any]], classify, scenic_tier_of=None) -> Dict[str, int]:
    """数一遍路线里的节点构成。

    口径（用户 2026-10-04 明确）：**`scenic` 只数真自然景观（景区/湖区/湿地森林…）**；
    城市公园单独记在 `city_park`（可以去，但不顶自然景观的名额，也不该被算成"自然很多"）。
    这样配比对账才不会被"5 个城市公园"骗过去。
    """
    counts = {"scenic": 0, "cultural": 0, "food": 0, "shopping": 0, "hotel": 0, "other": 0, "landmark": 0, "city_park": 0}
    for node in route or []:
        if not isinstance(node, Mapping):
            continue
        name = str(node.get("name") or node.get("location") or "")
        poi_type = str(node.get("type") or "")
        is_hotel = bool(node.get("is_hotel")) or any(word in name for word in ("酒店", "民宿", "宾馆", "客栈", "旅馆"))
        try:
            category = str(classify(name, poi_type, is_hotel) or "other")
        except Exception:
            category = "other"
        if is_hotel:
            category = "hotel"

        if category == "scenic" and scenic_tier_of is not None:
            tier = str(scenic_tier_of(name, poi_type) or "")
            if tier == "city_park":
                # 城市公园：从 natural 里挪出去，单独计数
                counts["city_park"] += 1
                continue
            if tier == "landmark":
                counts["landmark"] += 1

        counts[category] = counts.get(category, 0) + 1
    return counts


# 禁令类别的替换优先顺序：把"不要人文"换成自然景区最合理，其次美食、逛街。
DEFAULT_REPLACEMENT_ORDER = ("scenic", "food", "shopping")


def repair_forbidden_categories(
    route: Sequence[Mapping[str, Any]],
    *,
    forbidden: Sequence[str],
    classify,
    pool: Sequence[Mapping[str, Any]] = (),
    replacement_order: Sequence[str] = DEFAULT_REPLACEMENT_ORDER,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把用户**明确排除**的类别从路线里换掉或移出（闭环"硬约束过滤"阶段）。

    为什么需要：只做"目标是 0 + 事后报警"不够 —— 模型仍可能自己塞一个博物馆进来，
    而我们只是如实告诉你，没有去修。既然用户说了不要，就该真的不出现。

    行为（保守、可解释）：
      - 命中禁令的节点优先从候选池里**同类替换**（按 `replacement_order` 找第一个非禁令类别，
        且不与路线已有节点重名）；替换会带上 `replaced_from` 便于追溯；
      - 候选池里没有可替换的 → **移出**该节点；
      - 绝不触碰非禁令节点，也不改天数与顺序（保持原位替换）。

    返回 `(新路线, 动作清单)`；动作里写明 换/移 了什么、为什么，供事件流与界面展示。
    """
    forbidden_set = {str(item) for item in (forbidden or []) if str(item)}
    if not forbidden_set:
        return [dict(node) for node in route or [] if isinstance(node, Mapping)], []

    existing_names = {
        str(node.get("name") or node.get("location") or "")
        for node in route or []
        if isinstance(node, Mapping)
    }

    def node_category(node: Mapping[str, Any]) -> str:
        name = str(node.get("name") or node.get("location") or "")
        poi_type = str(node.get("type") or "")
        is_hotel = bool(node.get("is_hotel")) or any(word in name for word in ("酒店", "民宿", "宾馆", "客栈", "旅馆"))
        if is_hotel:
            return "hotel"
        try:
            return str(classify(name, poi_type, is_hotel) or "other")
        except Exception:
            return "other"

    # 候选池按类别索引（跳过禁令类别与已出现的名字）
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for item in pool or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in existing_names:
            continue
        category = node_category(item)
        if category in forbidden_set or category == "other":
            continue
        buckets.setdefault(category, []).append(dict(item))

    kept: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []
    for node in route or []:
        if not isinstance(node, Mapping):
            continue
        category = node_category(node)
        if category not in forbidden_set:
            kept.append(dict(node))
            continue

        replacement: Optional[Dict[str, Any]] = None
        for candidate_category in replacement_order:
            if candidate_category in forbidden_set:
                continue
            bucket = buckets.get(candidate_category) or []
            while bucket:
                candidate = bucket.pop(0)
                name = str(candidate.get("name") or "")
                if not name or name in existing_names:
                    continue
                replacement = candidate
                existing_names.add(name)
                break
            if replacement is not None:
                break

        original_name = str(node.get("name") or node.get("location") or "")
        if replacement is not None:
            merged = dict(node)
            merged["name"] = replacement.get("name")
            merged["location"] = replacement.get("name")
            merged["type"] = replacement.get("type")
            if replacement.get("lnglat"):
                merged["lnglat"] = replacement.get("lnglat")
            if replacement.get("price") is not None:
                merged["cost_estimate"] = replacement.get("cost_estimate")
            merged["replaced_from"] = original_name
            kept.append(merged)
            actions.append({
                "code": "forbidden_category_replaced",
                "day": node.get("day"),
                "category": category,
                "from": original_name,
                "to": str(replacement.get("name") or ""),
                "reason": f"你说了不要{COMPOSITION_LABELS.get(category, category)}，已换成同类可去的点",
            })
        else:
            actions.append({
                "code": "forbidden_category_dropped",
                "day": node.get("day"),
                "category": category,
                "from": original_name,
                "reason": f"你说了不要{COMPOSITION_LABELS.get(category, category)}，且候选池里没有可替换的点，已移出",
            })
    return kept, actions
