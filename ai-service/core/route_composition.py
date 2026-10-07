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


LODGING_NAME_WORDS = ("酒店", "民宿", "宾馆", "客栈", "旅馆", "度假村", "旅社", "青旅")


def is_lodging_node(node: Mapping[str, Any], classify=None) -> bool:
    """这个节点是不是住宿（名称/标记/类别三条路，缺一不可地覆盖真实数据）。

    实测：酒店名可能完全不含"酒店"二字（「康城建国国际」的类别是"住宿服务;宾馆酒店"），
    所以名称之外还要看 `is_hotel` 标记与分类器。
    """
    name = str(node.get("name") or node.get("location") or "")
    if bool(node.get("is_hotel")):
        return True
    if any(word in name for word in LODGING_NAME_WORDS):
        return True
    if classify is None:
        return False
    try:
        return str(classify(name, str(node.get("type") or ""), False) or "") == "hotel"
    except Exception:
        return False


def numeric_targets(
    targets: Mapping[str, Any],
    *,
    keys: Sequence[str] = ("scenic", "cultural", "food", "hotel"),
) -> Dict[str, int]:
    """只取目标里的**数字键**，忽略 `source` 这类说明字段。

    真实事故（2026-10-05）：agent 里直接写
    `{k: int(v or 0) for k, v in composition.items()}`，而 `composition` 里有
    `source="default_balanced"` → `int('default_balanced')` 抛 ValueError，
    整个推演在出稿前崩掉（而且被外层 `except` 报成"真实路网拼装失败"，误导排查）。
    配比映射是"几个数字 + 一个说明"的结构，取数必须显式点名键。
    """
    out: Dict[str, int] = {}
    for key in keys:
        try:
            out[str(key)] = int((targets or {}).get(key) or 0)
        except (TypeError, ValueError):
            continue
    return out


def clamp_targets_by_supply(
    targets: Mapping[str, int],
    supply: Mapping[str, int],
    *,
    keys: Sequence[str] = ("scenic", "cultural", "food", "hotel"),
) -> Tuple[Dict[str, int], Dict[str, Dict[str, int]]]:
    """把"用户口径算出的目标"按**候选供给**压到做得到的范围内。

    为什么需要（2026-10-05 横向对照实测）：库尔勒默认口径目标 4 人文，而候选池里
    一共只有 3 个人文点（`类别={'scenic': 5, 'cultural': 3, ...}`）；喀什目标 9 人文、
    候选 7 个。这时对账报"人文偏少"其实是在**怪方案没做到供给本身就不够的事** ——
    用户看到的是"这趟人文安排得不好"，真实原因是"这个城市的候选里人文就这么点"。

    所以分两层说清楚：
      - 做得到的部分（clamped）对账，达不到才算方案问题；
      - 做不到的部分（limited）作为**供给缺口**单独披露。

    返回 `(clamped_targets, limited)`；`limited[类别] = {"target": 原目标, "available": 候选数}`。
    """
    # ⚠️ 只搬**数字**键。`composition` 里还有 `source`（"default_balanced" 这种字符串）、
    #    `shares`（字典）、`exclude_cultural`（布尔）—— 无脑 `int(value)` 会直接
    #    `ValueError: invalid literal for int(): 'default_balanced'`，把整段配比对账带走
    #    （2026-10-05 实测：4 个场景全崩，界面上只看到"配比对账失败"）。布尔也要跳过，
    #    否则 `int(False)=0` 会凭空造出一个目标 0 的类别。
    clamped: Dict[str, int] = {}
    for key, value in (targets or {}).items():
        if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set)):
            continue
        try:
            clamped[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    limited: Dict[str, Dict[str, int]] = {}
    for key in keys:
        if key not in clamped:
            continue
        target = int(clamped.get(key) or 0)
        if target <= 0:
            continue
        available = int((supply or {}).get(key) or 0)
        if target > available:
            clamped[key] = available
            limited[str(key)] = {"target": target, "available": available}
    return clamped, limited


def summarize_supply_limits(limited: Mapping[str, Mapping[str, int]], label_of=None) -> List[str]:
    """把供给缺口说成人话（用于"遗留问题"与配比说明）。"""
    labels = {"scenic": "自然景区", "cultural": "人文", "food": "特色美食", "hotel": "住宿"}
    notes: List[str] = []
    for key, item in (limited or {}).items():
        name = (label_of or labels.get)(str(key)) if label_of else labels.get(str(key), str(key))
        notes.append(
            f"{name}候选只有 {int(item.get('available') or 0)} 个（目标 {int(item.get('target') or 0)} 个），"
            "这是这座城市的候选供给缺口，不是没安排"
        )
    return notes


def trim_lodging_over_cap(
    route: Sequence[Mapping[str, Any]],
    *,
    nights: int,
    classify=None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """住宿节点**不能多于夜数**：多出来的移出该路线。

    实测（2026-10-05 横向对照）：喀什 3 天排了 4 个住宿（应 2 晚）、乌鲁木齐 2 天排了 3 个（应 1 晚）。
    原因是两个机制叠加 —— 候选池把**全部**酒店都塞进 top（`hotel: 6/8`，而目标是 1~2 个），
    最终均衡又会"哪天没有酒店就补一个"（最后一天本来就不需要住）。
    结果用户看到"这一趟住 3 个酒店"，既不合口径也解释不通。

    规则：按天排序，保留**最前面的 `nights` 晚**的住宿；同一天有多个时优先留有价格的。
    返回 `(新路线, 移出的住宿)`，动作码 `lodging_over_cap_dropped`（住宿不做同类替换，
    直接移出 —— 多一个住宿本来就没有"换成别的"的道理）。
    """
    route_list = [dict(node) for node in route or [] if isinstance(node, Mapping)]
    total_nights = max(0, int(nights or 0))
    if total_nights <= 0:
        return route_list, []

    lodging = [node for node in route_list if is_lodging_node(node, classify)]
    if len(lodging) <= total_nights:
        return route_list, []

    def _has_price(node: Mapping[str, Any]) -> int:
        value = node.get("cost_estimate") or node.get("price")
        return 1 if value not in (None, "", "未知") else 0

    def _day_of(node: Mapping[str, Any]) -> int:
        try:
            return int(node.get("day") or 1)
        except (TypeError, ValueError):
            return 1

    # 需要住宿的是"前 nights 天"（最后一天通常是回程日，不留宿）：
    # **先**在 1..nights 里选，只有那里没有住宿时才退而取最靠前的几天 ——
    # 否则"所有住宿都排在最后一天"会让前几晚空着（实测伊宁 3 天只剩 1 个住宿）。
    all_days = sorted({_day_of(node) for node in lodging})
    day_order = [day for day in all_days if day <= total_nights] + [
        day for day in all_days if day > total_nights
    ]
    keep_ids = set()
    # ⚠️ 用户**点名选的那一家**优先保留（2026-10-05 实测）：他挑了一家酒店，限量逻辑却按
    #    第 1..nights 天顺序保留、把用户选的那家当"多余的"移出 —— 那是把明确选择当噪声。
    for node in lodging:
        if bool(node.get("user_choice")):
            keep_ids.add(id(node))
    for day in day_order[:total_nights]:
        same_day = [node for node in lodging if _day_of(node) == day]
        same_day.sort(key=lambda node: (-_has_price(node), str(node.get("name") or "")))
        keep_ids.add(id(same_day[0]))
    # 天数不足 nights 时（例如所有住宿都挤在同一天），按"有价优先"把剩下的名额补满
    if len(keep_ids) < total_nights:
        for node in sorted(lodging, key=lambda item: (-_has_price(item), _day_of(item))):
            if len(keep_ids) >= total_nights:
                break
            keep_ids.add(id(node))

    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    for node in route_list:
        if is_lodging_node(node, classify) and id(node) not in keep_ids:
            removed.append({
                **node,
                "removed_reason": f"住宿比夜数多（{len(lodging)} 个 > {total_nights} 晚）",
            })
            continue
        kept.append(node)
    return kept, removed


def order_route_by_day(route: Any) -> List[Dict[str, Any]]:
    """把路线按天做**稳定**排序（同一天内部保持原顺序）。

    为什么是方案自己的责任（2026-10-05 实测，伊宁 3 天方案）：
    住宿兜底/补点是**追加**节点的（`ensure_lodging_per_night`、候选池补点），
    于是第 1 晚补出来的「信泰达民宿」被放在路线数组的**末尾** —— 排在第 3 天的节点之后。
    前端"按天分组"的视图看不出问题，但任何按**数组顺序**连线/导出的消费方都会画出
    "第 3 天最后一个点 → 第 1 天的民宿"这种跨天回头线。

    路线数组的顺序**就是行程顺序**，这是方案本身该保证的事，不该指望每个消费方各排一遍。
    只按天排、不按时间排：同一天里"上午/午饭/下午/酒店"的顺序是规划结果，不能被重排。
    """
    items = [dict(node) for node in route or [] if isinstance(node, Mapping)]
    return sorted(items, key=lambda node: _node_day(node))


def _node_day(node: Mapping[str, Any]) -> int:
    try:
        return int(node.get("day") or 1)
    except (TypeError, ValueError):
        return 1


# "玩点"类别（判"某天还有没有得看"、算当天玩点聚簇都用它）：
# 分类器给的是 scenic/cultural/landmark/city_park，缺一档就会把"只有公园/地标的一天"误判成没有玩点。
PLAY_CATEGORIES = {"scenic", "cultural", "landmark", "city_park", "park", "nature"}


def _coords_of(node: Mapping[str, Any]) -> List[float]:
    value = node.get("lnglat")
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            lon, lat = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return []
        if lon == 0.0 and lat == 0.0:
            return []
        return [lon, lat]
    return []


def _km(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return float("inf")
    lon1, lat1, lon2, lat2 = float(a[0]), float(a[1]), float(b[0]), float(b[1])
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    h = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _day_leg_km(nodes: Sequence[Mapping[str, Any]]) -> float:
    total = 0.0
    previous: List[float] = []
    for node in nodes:
        coord = _coords_of(node)
        if coord and previous:
            km = _km(previous, coord)
            if km == km and km != float("inf"):
                total += km
        if coord:
            previous = coord
    return total


def _default_category(node: Mapping[str, Any]) -> str:
    """粗分类的**兜底实现**（与 `api.agent.classify_poi_category` 同口径；不可用时用关键词）。

    为什么要兜底：本模块是纯函数层，不能反向依赖 `api.agent`；而"每天类别构成不变"
    这条约束必须有分类才能算。关键词判据与 `poi_pool` 的那套一致（自然/人文/美食/住宿）。
    """
    name = str(node.get("name") or node.get("location") or "")
    poi_type = str(node.get("type") or "")
    text = f"{name} {poi_type}"
    if bool(node.get("is_hotel")) or is_lodging_node(node):
        return "hotel"
    if any(word in text for word in ("餐", "美食", "小吃", "馄饨", "火锅", "烧烤", "面馆", "饭店", "蹄花", "馆子")):
        return "food"
    if any(word in text for word in ("博物馆", "博物院", "纪念馆", "展馆", "文化馆", "书院", "故居", "寺", "古城", "遗址")):
        return "cultural"
    if any(word in text for word in ("景区", "风景", "公园", "湿地", "草原", "湖", "山", "沙漠", "峡谷", "森林公园")):
        return "scenic"
    return "other"


def rebalance_days_by_geography(
    route: Any,
    *,
    min_gain_ratio: float = 0.15,
    classify=None,
    protect_kinds: Sequence[str] = (),
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """按地理就近**重排天与天之间的点**，专治"远点被塞进市区那一天"（2026-10-07 实测）。

    为什么要做（真实方案的诊断，`work/diag_day_distance.py`）：
      喀什 3 天，第 2 天 = 九龙泉(市区) → 喀什博物馆(市区) → 川味馆子(市区) → **胡杨书院(巴楚, 216km)**
      → 阳光公园(市区) → 亚朵酒店(市区)，当天 449km。而按地理就近重排后：
      第 1 天 17km、第 2 天 12km、第 3 天 233km（英吉沙 66km + 巴楚 216km 放同一天）。
      也就是说：**不是候选池只有远点，是"天"这一层没看地理。**
    约束（一条都不能破）：
      · **住宿不动**：它绑在"第几晚"，换天等于换行程结构；
      · **用户点名的点不动**（`user_choice`）；
      · **每天的类别构成不变**（自然/人文/美食/其它 各几个照旧）—— 只换"哪个点去哪天"；
      · **每天节点数不变**（每日密度门禁不动）；
      · 没坐标的点按原地不动处理（判不了就别乱搬）；
      · **只有在最赶的一天真的明显变短时才动**（默认要短 15% 以上），否则原样返回；
      · **不制造新的门禁失败**：某天原本有玩点、重排后一个都没有 → 不改；
        `protect_kinds`（由调用方按用户诉求给，例如"想看自然风景"→ `("scenic",)`）
        里的类别，某天原本有、重排后没有 → 也不改。
        为什么要按诉求给（2026-10-07 实测）：门禁里有一条"以自然为主的行程每天至少 1 个自然景观"，
        原来一刀切保护"自然+人文"，把伊宁一次很有价值的重排（167km → 130km）给否了，
        而那个 `play_category_monotony` 失败在**重排前后都存在**（是 LLM 原始路线的问题）。
        保护的范围应该跟着诉求走，而不是一律最严。
    返回 `(新路线, 报告)`；`报告["applied"]=False` 时路线与传入的等价。
    """
    items = [dict(node) for node in (route or []) if isinstance(node, Mapping)]
    report: Dict[str, Any] = {
        "applied": False,
        "before_max_km": 0.0,
        "after_max_km": 0.0,
        "before_total_km": 0.0,
        "after_total_km": 0.0,
        "moved": [],
        "note": "",
    }
    if len(items) < 4:
        report["note"] = "节点太少，不做地理重排"
        return items, report

    days = sorted({_node_day(node) for node in items})
    if len(days) < 2:
        report["note"] = "只有一天，不做地理重排"
        return items, report

    by_day: Dict[int, List[Dict[str, Any]]] = {day: [n for n in items if _node_day(n) == day] for day in days}
    report["before_max_km"] = round(max(_day_leg_km(v) for v in by_day.values()), 1)
    report["before_total_km"] = round(sum(_day_leg_km(v) for v in by_day.values()), 1)

    # 不可动的节点：住宿（绑夜）、用户点名的、以及没有坐标的
    fixed: Dict[int, List[Dict[str, Any]]] = {day: [] for day in days}
    movable: Dict[int, List[Dict[str, Any]]] = {day: [] for day in days}
    for day in days:
        for node in by_day[day]:
            reasons = bool(node.get("user_choice")) or is_lodging_node(node, classify) or not _coords_of(node)
            (fixed[day] if reasons else movable[day]).append(node)

    def category(node: Mapping[str, Any]) -> str:
        if classify is None:
            return _default_category(node)
        name = str(node.get("name") or node.get("location") or "")
        try:
            return str(classify(name, str(node.get("type") or ""), bool(node.get("is_hotel"))) or "other")
        except Exception:
            return _default_category(node)

    def _play_centroid(day: int) -> List[float]:
        plays = [_coords_of(node) for node in by_day[day] if category(node) in PLAY_CATEGORIES]
        plays = [coord for coord in plays if coord]
        if not plays:
            plays = [_coords_of(node) for node in by_day[day] if _coords_of(node)]
        if not plays:
            return []
        return [sum(c[0] for c in plays) / len(plays), sum(c[1] for c in plays) / len(plays)]

    # 👑 **每天的餐饮下限**先记下来，但**不钉死节点**（钉死会把地理重排的收益吃掉：实测喀什
    #    449km → 461km，因为钉住的两个饭点占住了本该腾给远点的位置）。
    #    做法改成"先自由重排、再修餐饮覆盖"：下面算完之后，某天原本有饭点却没了，
    #    就用"有富余饭点的那天"跟它换一个（换法取最不伤里程的那种）；换不动就整单不改。
    days_with_meal_before = {
        day: any(category(node) == "food" for node in fixed[day] + movable[day]) for day in days
    }
    floating_meals = sum(1 for day in days for node in movable[day] if category(node) == "food")

    # 每天要**至少**保留哪些类别（不是"数量完全照旧"—— 那会把远点死死锁在原天：
    # 实测喀什第 2 天有 2 个人文点，「胡杨书院」(巴楚 216km) 就被这条硬约束留在了市区那天）。
    # 保留"每天至少各一个"是因为每日密度/节奏门禁关心的是"这天有没有得看、有没有得吃"。
    need: Dict[int, Dict[str, int]] = {}
    for day in days:
        counts: Dict[str, int] = {}
        for node in movable[day]:
            key = category(node)
            if key != "other":
                counts[key] = 1  # 下限 1
        need[day] = counts
    hungry_days = [day for day in days if not days_with_meal_before[day]]
    # ⚠️ 这里**不**给"原本没饭点的那天"加下限（2026-10-07 实测）：餐饮候选都在市区时，
    #    硬把它塞给"远点那天"会凭空加出 city→far→city 两趟（喀什 449km → 460km），
    #    于是整个重排被收益门槛否掉。缺餐该由**收口后的补餐**解决（就近取候选池的餐饮），
    #    不是靠把别的天的饭点搬过去。这里只保证"原本有饭点的那天不许丢"（见下面的修复段）。

    unused = [node for day in days for node in movable[day]]
    target_counts = {day: len(movable[day]) for day in days}
    assigned: Dict[int, List[Dict[str, Any]]] = {day: [] for day in days}

    # 起点：全体质心（无坐标点已被排除在 movable 之外）
    coords = [_coords_of(node) for node in items if _coords_of(node)]
    if not coords:
        report["note"] = "没有任何可信坐标，不做地理重排"
        return items, report
    centroid = [sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords)]
    position = list(centroid)

    def _take(day: int, pool: List[Dict[str, Any]], *, farthest: bool = False) -> None:
        nonlocal position
        key = (lambda node: -_km(centroid, _coords_of(node))) if farthest else (
            lambda node: _km(position, _coords_of(node))
        )
        chosen = min(pool, key=key)
        unused.remove(chosen)
        assigned[day].append(chosen)
        position = _coords_of(chosen) or position

    # 👑 关键一步：**不用回市区的那一天**（最后一天通常没酒店）先拿"最偏的点"。
    #    为什么要这样（真实诊断）：喀什第 2 天有个 216km 的巴楚书院，而当晚还要回市区住 ——
    #    一个远点花掉"出去 + 回来"两趟。把它挪到不需要返程的那天，一个远点只花一趟。
    hotel_free_days = [
        day for day in days if not any(is_lodging_node(node, classify) for node in fixed[day])
    ]
    outlier_days = hotel_free_days or [days[-1]]
    other_days = [day for day in days if day not in outlier_days]

    for day in outlier_days:
        for key, minimum in need[day].items():
            pool = [node for node in unused if category(node) == key]
            if pool:
                _take(day, pool, farthest=True)
        while len(assigned[day]) < target_counts[day] and unused:
            _take(day, unused, farthest=True)

    # 其余天：先满足"每天每类至少一个"（就近取），再按最近填满
    for day in other_days:
        for key, minimum in need[day].items():
            already = sum(1 for node in assigned[day] if category(node) == key)
            for _ in range(max(0, minimum - already)):
                pool = [node for node in unused if category(node) == key]
                if not pool:
                    break
                _take(day, pool)
    for day in other_days:
        while len(assigned[day]) < target_counts[day] and unused:
            _take(day, unused)

    # 第三轮：**同类互换**爬山（2026-10-07 实测需要）：贪心可能把"那一天唯一的人文点"
    # 先分给了近的一天，于是远的人文点（巴楚书院）被迫留在要回市区的天里、最赶的一天没降下来。
    # 只换**同类**节点 → 每天的类别构成与数量都不变，纯粹用地理换。
    def _max_km_of(candidate: Dict[int, List[Dict[str, Any]]]) -> float:
        return max(_day_leg_km(fixed[day] + candidate[day]) for day in days)

    for _ in range(3):
        improved = False
        current_max = _max_km_of(assigned)
        for left_day in days:
            for right_day in days:
                if left_day >= right_day:
                    continue
                for left_node in list(assigned[left_day]):
                    for right_node in list(assigned[right_day]):
                        if category(left_node) != category(right_node):
                            continue
                        assigned[left_day].remove(left_node)
                        assigned[left_day].append(right_node)
                        assigned[right_day].remove(right_node)
                        assigned[right_day].append(left_node)
                        new_max = _max_km_of(assigned)
                        if new_max < current_max - 1e-6:
                            current_max = new_max
                            improved = True
                            break
                        # 换回来
                        assigned[left_day].remove(right_node)
                        assigned[left_day].append(left_node)
                        assigned[right_day].remove(left_node)
                        assigned[right_day].append(right_node)
                    if improved:
                        break
                if improved:
                    break
            if improved:
                break
        if not improved:
            break

    # 👑 餐饮覆盖修复（2026-10-07 实测回归：重排把伊宁第 3 天的饭点搬走 → 门禁判失败）：
    #    某天原本有饭点、重排后没有 → 从"有富余饭点（≥2）"的那天换一个过来，
    #    换法取**最不伤里程**的那种；换不动就整单不改（宁可不动，也不能让用户没饭吃）。
    def _day_has_meal(nodes: Sequence[Mapping[str, Any]]) -> bool:
        return any(category(node) == "food" for node in nodes)

    for _ in range(len(days) * 2):
        starving = [
            day for day in days
            if days_with_meal_before[day] and not _day_has_meal(fixed[day] + assigned[day])
        ]
        if not starving:
            break
        fixed_this_round = False
        for hungry in starving:
            donors = [
                day for day in days
                if day != hungry and sum(1 for node in assigned[day] if category(node) == "food") >= 2
            ]
            best_swap = None
            best_max = _max_km_of(assigned)
            for donor in donors:
                for meal in [node for node in assigned[donor] if category(node) == "food"]:
                    for other in list(assigned[hungry]):
                        assigned[donor].remove(meal)
                        assigned[donor].append(other)
                        assigned[hungry].remove(other)
                        assigned[hungry].append(meal)
                        candidate_max = _max_km_of(assigned)
                        if candidate_max < best_max - 1e-6:
                            best_max = candidate_max
                            best_swap = (donor, hungry, meal, other)
                        # 撤销
                        assigned[hungry].remove(meal)
                        assigned[hungry].append(other)
                        assigned[donor].remove(other)
                        assigned[donor].append(meal)
            if best_swap:
                donor, hungry_day, meal, other = best_swap
                assigned[donor].remove(meal)
                assigned[donor].append(other)
                assigned[hungry_day].remove(other)
                assigned[hungry_day].append(meal)
                fixed_this_round = True
        if not fixed_this_round:
            report["after_max_km"] = round(_max_km_of(assigned), 1)
            report["after_total_km"] = round(sum(_day_leg_km(fixed[day] + assigned[day]) for day in days), 1)
            report["note"] = "重排会让某一天没有餐饮节点、又换不动，因此不改动分天"
            return items, report

    # 组装：每天 = 固定节点 + 新分配的可动节点，按**时刻**排好（时间语义不变）
    def _clock_and_suffix(node: Mapping[str, Any]) -> Tuple[int, str]:
        raw = str(node.get("time") or "")
        tail = raw.split("|")[-1].strip()
        parts = tail.replace("：", ":").split(":", 1)
        if len(parts) == 2:
            hour = parts[0].strip()
            minute = parts[1].strip()[:2]
            suffix = parts[1].strip()[2:].strip()
            if hour.isdigit() and minute.isdigit():
                return int(hour) * 60 + int(minute), suffix
        return -1, tail

    # 时刻用**标准梯队**重排（沿用规划自己那套 08:30/10:00/11:30/13:30/15:00/17:00），
    # 只保留原来的语义后缀（游览/用餐/入住）—— 按原时刻排序能让"上午看的点仍在上午"。
    # 为什么不用原时刻直接写：重排后同一天可能有两个点都是 10:00（实测），时间就成了摆设。
    ladder = ["08:30", "10:00", "11:30", "13:30", "15:00", "17:00", "18:30", "19:30"]
    new_by_day: Dict[int, List[Dict[str, Any]]] = {}
    moved_names: List[str] = []
    for day in days:
        original_index = {id(node): index for index, node in enumerate(by_day[day])}
        merged = fixed[day] + assigned[day]
        merged.sort(
            key=lambda node: (
                _clock_and_suffix(node)[0] if _clock_and_suffix(node)[0] >= 0 else 10**6,
                original_index.get(id(node), 10_000),
            )
        )
        slot_index = 0
        updated: List[Dict[str, Any]] = []
        for node in merged:
            clock, suffix = _clock_and_suffix(node)
            if is_lodging_node(node, classify) and clock >= 0:
                clock_text = f"{clock // 60:02d}:{clock % 60:02d}"  # 入住时间保持方案原样
            else:
                clock_text = ladder[min(slot_index, len(ladder) - 1)]
                slot_index += 1
            # ⚠️ 必须产出**新字典**：不能就地改传进来的节点 —— 否则"判定不值得重排"
            #    返回原路线时，原路线的 day/time 已经被改过（实测 bug：applied=False 但时间被打乱）
            updated.append(
                {
                    **node,
                    "day": day,
                    "time": f"Day {day} | {clock_text}" + (f" {suffix}" if suffix else ""),
                }
            )
            if id(node) not in original_index:
                moved_names.append(str(node.get("name") or ""))
        new_by_day[day] = updated

    new_items: List[Dict[str, Any]] = []
    for day in days:
        new_items.extend(new_by_day[day])

    report["after_max_km"] = round(max(_day_leg_km(v) for v in new_by_day.values()), 1)
    report["after_total_km"] = round(sum(_day_leg_km(v) for v in new_by_day.values()), 1)
    report["moved"] = moved_names
    report["before_days"] = {str(node.get("name") or ""): _node_day(node) for node in items}
    # 👑 每天的"玩点性质"不能丢（2026-10-07 自测抓到）：门禁里有一条"以自然为主的行程
    #    每天至少 1 个自然景观、文化类设上限"。重排如果让某天**原本有自然点、重排后一个都没了**，
    #    就会凭空调出一个新的门禁失败 —— 宁可这一天的远点不挪。
    play_kinds = ("scenic", "cultural")
    # 判"某天还有没有得看"用更全的玩点集合（公园/地标也算玩点）
    PLAYS_FOR_GUARD = PLAY_CATEGORIES

    def _play_kinds(nodes: Sequence[Mapping[str, Any]]) -> set:
        return {category(node) for node in nodes if category(node) in PLAYS_FOR_GUARD}

    for day in days:
        before_kinds = _play_kinds(fixed[day] + movable[day])
        after_kinds = _play_kinds(fixed[day] + assigned[day])
        if before_kinds and not after_kinds:
            report["note"] = f"重排会让第 {day} 天一个玩点都没有，因此不改动分天"
            return items, report
        lost_required = before_kinds & set(protect_kinds) - after_kinds
        if lost_required:
            report["note"] = (
                f"重排会让第 {day} 天丢掉「{'、'.join(sorted(lost_required))}」"
                "（这趟的诉求要求每天都有这一类），因此不改动分天"
            )
            return items, report
        # 👑 餐饮覆盖也不能破（2026-10-07 实测抓到的回归）：某天原本有饭点、重排后没有 → 不改。
        #    伊宁真跑就是这样丢掉第 3 天的餐饮，门禁 `skeleton_meal_missing` 直接判失败。
        before_meal = any(category(node) == "food" for node in fixed[day] + movable[day])
        after_meal = any(category(node) == "food" for node in fixed[day] + assigned[day])
        if before_meal and not after_meal:
            report["note"] = f"重排会让第 {day} 天没有餐饮节点，因此不改动分天"
            return items, report
    if not moved_names:
        report["note"] = "按地理算不出更好的分天（没有点需要换天）"
        return items, report
    if report["after_max_km"] >= report["before_max_km"] * (1.0 - max(0.0, min_gain_ratio)):
        report["note"] = (
            f"重排后最赶的一天没有明显变短（{report['before_max_km']:.0f}km → {report['after_max_km']:.0f}km），"
            "保持原样"
        )
        return items, report
    report["applied"] = True
    report["note"] = (
        f"按地理就近重排：最赶的一天 {report['before_max_km']:.0f}km → {report['after_max_km']:.0f}km"
        f"（全程 {report['before_total_km']:.0f}km → {report['after_total_km']:.0f}km），"
        f"换天的点：{'、'.join(moved_names[:6])}"
    )
    return new_items, report


def settle_lodging(
    route: Sequence[Mapping[str, Any]],
    *,
    nights: int,
    pool: Sequence[Mapping[str, Any]] = (),
    classify=None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[int]]:
    """把住宿一次收口到"**每晚一家、不多不少**"，返回 `(路线, 补上的, 移出的, 仍缺的晚)`。

    为什么要把两步封在一起（2026-10-05 实测教训）：这两步的顺序**会决定结果**，
    分开写在几千行的推演函数里时，谁先谁后根本没人记得住，结果就是伊宁 3 天
    最后只剩 1 个住宿（目标 2 晚）。正确顺序是**先补后减**：
      1. `ensure_lodging_per_night`：先把缺的晚补上（从候选池挑离当天最近的）；
      2. `trim_lodging_over_cap`：再按夜数限量 —— 它偏好保留"该住的第 1..nights 天"，
         所以第 1 步补进来的那几晚不会被当成多余的删掉，而排在最后一天的酒店会被清掉。
    反过来的顺序（先减后补）会留下一个排在不需要留宿那天的酒店，因为它在前一步
    就已经占满了夜数名额。
    """
    filled, added, missing = ensure_lodging_per_night(
        route, nights=nights, pool=pool, classify=classify
    )
    trimmed, dropped = trim_lodging_over_cap(filled, nights=nights, classify=classify)
    return trimmed, added, dropped, missing


def count_lodging(route: Sequence[Mapping[str, Any]], classify=None) -> int:
    """路线里住宿节点的个数（与 `is_lodging_node` 同一口径，供对账/断言用）。"""
    return len([node for node in route or [] if isinstance(node, Mapping) and is_lodging_node(node, classify)])


def ensure_lodging_per_night(
    route: Sequence[Mapping[str, Any]],
    *,
    nights: int,
    pool: Sequence[Mapping[str, Any]] = (),
    classify=None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[int]]:
    """每晚都必须有住宿：缺的那一晚从候选里**挑离当天行程最近的**补上。

    为什么要有它（2026-10-05 横向对照实测）：伊宁 3 天方案里**一个住宿节点都没有**
    （住宿候选池为空 → lodging-agent 未参与 → 模型也没排住宿），用户拿到的是
    "住哪儿没着落"的行程。这是硬伤，不是风格问题。

    规则：
      - 逐天检查是否有住宿节点（按名称/类别判定）；
      - 缺的那天从候选池挑**未使用过**且**离当天行程质心最近**的住宿；
      - 候选池里没有可用住宿 → 记进 `missing_days`（**如实报缺，不编一个酒店**）。
    """
    import math

    route_list = [dict(node) for node in route or [] if isinstance(node, Mapping)]
    total_nights = max(0, int(nights or 0))
    if total_nights <= 0:
        return route_list, [], []

    def is_hotel(node: Mapping[str, Any]) -> bool:
        return is_lodging_node(node, classify)

    def coord_of(node: Mapping[str, Any]) -> List[float]:
        value = node.get("lnglat") or node.get("location")
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                return [float(value[0]), float(value[1])]
            except (TypeError, ValueError):
                return []
        if isinstance(value, str) and "," in value:
            left, _, right = value.partition(",")
            try:
                return [float(left), float(right)]
            except ValueError:
                return []
        return []

    def haversine(a: Sequence[float], b: Sequence[float]) -> float:
        if not a or not b or len(a) < 2 or len(b) < 2:
            return float("inf")
        lon1, lat1, lon2, lat2 = float(a[0]), float(a[1]), float(b[0]), float(b[1])
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        h = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
        return 2 * 6371.0 * math.asin(math.sqrt(h))

    used_names = {str(node.get("name") or node.get("location") or "") for node in route_list}
    available = [
        dict(item) for item in (pool or [])
        if isinstance(item, Mapping) and str(item.get("name") or "").strip()
        and str(item.get("name")) not in used_names
    ]

    # ⚠️ 要看的是**第 1..nights 天**，不是"路线里出现的前 nights 个日子"。
    #    实测事故（2026-10-05 伊宁）：路线恰好没有第 2 天的节点时，
    #    `sorted({day})[:nights]` 会去检查第 1、3 天，第 2 晚**永远补不上**。
    days = list(range(1, total_nights + 1))
    actions: List[Dict[str, Any]] = []
    missing_days: List[int] = []

    for day in days:
        day_nodes = [node for node in route_list if int(node.get("day") or 1) == day]
        if not day_nodes or any(is_hotel(node) for node in day_nodes):
            continue
        points = [coord_of(node) for node in day_nodes]
        points = [point for point in points if point]
        centroid = (
            [sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points)]
            if points
            else []
        )
        if not available:
            missing_days.append(day)
            continue
        best_index, best_distance = 0, float("inf")
        for index, candidate in enumerate(available):
            distance = haversine(centroid, coord_of(candidate))
            if distance < best_distance:
                best_index, best_distance = index, distance
        chosen = available.pop(best_index)
        used_names.add(str(chosen.get("name") or ""))
        inserted = dict(chosen)
        inserted["day"] = day
        inserted["is_hotel"] = True
        inserted.setdefault("type", "住宿服务;宾馆酒店;宾馆酒店")
        inserted["added_reason"] = "missing_lodging_for_night"
        route_list.append(inserted)
        actions.append({
            "code": "lodging_added",
            "day": day,
            "to": str(chosen.get("name") or ""),
            "distance_km": None if best_distance == float("inf") else round(best_distance, 1),
            "reason": "这一晚原本没有住宿，已按离当天行程最近补上",
        })
    return route_list, actions, missing_days


def trim_category_over_cap(
    route: Sequence[Mapping[str, Any]],
    *,
    cap: int,
    classify,
    scenic_tier_of=None,
    category: str = "city_park",
    pool: Sequence[Mapping[str, Any]] = (),
    replacement_preference: Sequence[str] = ("scenic", "food"),
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把**超过上限的城市公园**从路线里换掉或移出（用户口径：默认 1~2 个）。

    为什么要有这一步：候选池限量只管住了"模型能看到什么"，模型照样能多排
    （实测出现过路线里 3 个公园 > 上限 2）。口径要落到**最终路线**上，而不是只写在日志里。

    规则（保守、可解释）：
      - 保留路线里**前 `cap` 个**公园（顺序即优先级），其余逐个处理；
      - 优先从候选池**同类替换**：按 `replacement_preference`（默认自然景区 → 美食）找候选，
        且不与路线重名、本身不是城市公园（不能拿公园换公园）；
      - 没有可替换的 → 移出该节点，并如实记动作；
      - 绝不触碰非公园节点。
    `category="city_park"` 时用 `scenic_tier_of` 判定"这是不是城市公园"（与配比对账同一口径）。
    """
    route_list = [dict(node) for node in route or [] if isinstance(node, Mapping)]
    limit = max(0, int(cap or 0))

    def category_of(node: Mapping[str, Any]) -> str:
        name = str(node.get("name") or node.get("location") or "")
        poi_type = str(node.get("type") or "")
        is_hotel = bool(node.get("is_hotel")) or any(
            word in name for word in ("酒店", "民宿", "宾馆", "客栈", "旅馆")
        )
        if is_hotel:
            return "hotel"
        try:
            base = str(classify(name, poi_type, is_hotel) or "other")
        except Exception:
            return "other"
        if category == "city_park" and base == "scenic" and scenic_tier_of is not None:
            tier = str(scenic_tier_of(name, poi_type) or "")
            return "city_park" if tier == "city_park" else base
        return base

    park_indexes = [index for index, node in enumerate(route_list) if category_of(node) == category]
    if len(park_indexes) <= limit:
        return route_list, []

    existing_names = {str(node.get("name") or node.get("location") or "") for node in route_list}
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for item in pool or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in existing_names:
            continue
        item_category = category_of(item)
        if item_category in {category, "other", "hotel"}:
            continue
        if category == "city_park" and item_category == "scenic" and scenic_tier_of is not None:
            # 只接受**真景区**来替换公园（不能再换一个公园进来）
            if str(scenic_tier_of(str(item.get("name") or ""), str(item.get("type") or "")) or "") != "landmark":
                continue
        buckets.setdefault(item_category, []).append(dict(item))

    actions: List[Dict[str, Any]] = []
    for index in park_indexes[limit:]:
        node = route_list[index]
        original = str(node.get("name") or node.get("location") or "")
        replacement: Optional[Dict[str, Any]] = None
        for wanted in replacement_preference:
            bucket = buckets.get(str(wanted)) or []
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
        if replacement is not None:
            merged = dict(node)
            merged["name"] = replacement.get("name")
            merged["location"] = replacement.get("name")
            merged["type"] = replacement.get("type")
            if replacement.get("lnglat"):
                merged["lnglat"] = replacement.get("lnglat")
            merged["replaced_from"] = original
            merged["replacement_reason"] = "city_park_over_cap"
            route_list[index] = merged
            actions.append({
                "code": "park_over_cap_replaced",
                "day": node.get("day"),
                "from": original,
                "to": str(replacement.get("name") or ""),
                "reason": f"城市公园超过上限 {limit} 个，已换成同类可去的点",
            })
        else:
            route_list[index] = {**dict(node), "_drop": True}
            actions.append({
                "code": "park_over_cap_dropped",
                "day": node.get("day"),
                "from": original,
                "reason": f"城市公园超过上限 {limit} 个，且候选池里没有可替换的点，已移出",
            })
    kept = [node for node in route_list if not node.get("_drop")]
    for node in kept:
        node.pop("_drop", None)
    return kept, actions


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
