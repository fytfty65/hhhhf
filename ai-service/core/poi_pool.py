"""同义候选池：用**已配置的** POI 数据源（本机是 AMap）检索同类替代点。

为什么要它
----------
`budget_planner.propose_fallback` 与 `candidate_index.apply_exclusions` 都需要
`candidates_by_intent`：没有候选池时只能"删/降"，不能"换成同类"，"不想去这些点、换别的"
也无从执行。本模块负责**取真实候选**（而不是编造），并把它们整理成候选池。

设计
----
- 检索函数通过参数注入（`fetch`），因此**可以离线单测**（测试传假 fetch），生产传
  `ExpertToolbox.get_dynamic_pois`；
- 任何异常/超时都返回已有结果，**绝不阻断规划**；没有候选就是空池（上层如实告知"没换成"）；
- 候选带上价格级别与来源（`price`/`price_tier`/`price_source`），供"降档"与"未核实"口径使用。
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .candidate_index import index_candidates, intent_of, normalize_text, tier_level

# —— 供应商错误口径（真实缺口，2026-10-04 实测）——
# 高德对「景点」查询会直接返回 status=0 / info=CUQPS_HAS_EXCEEDED_THE_LIMIT（QPS 限流）：
#
#   --- 景点(生产参数) ---
#   高德原始返回: status=0 info=CUQPS_HAS_EXCEEDED_THE_LIMIT 条数=0
#   生产 get_dynamic_pois 最终产出: 7 条
#
# 而 `get_dynamic_pois` 原来是 `if status == "1" and pois:` 之外一律返回空 —— 限流被当成
# "这个地方没有景点"。后果不是报错，而是**候选池凭空变小**：模型改用自己的记忆编排地点，
# 用户看到的是"地点不靠谱/方案不周全"，而日志里只有一行"attractions=1"。
#
# 所以这里把"限流可重试"与"其它错误必须说出来"变成可单测的纯逻辑。
AMAP_RETRYABLE_INFOS = ("CUQPS_HAS_EXCEEDED_THE_LIMIT",)


def amap_error_is_retryable(info: str) -> bool:
    """QPS 限流是"等一下就好"，值得重试；密钥/参数类错误重试无意义。"""
    value = str(info or "").strip().upper()
    return any(marker in value for marker in AMAP_RETRYABLE_INFOS)


def amap_error_note(info: str, status: Any = "") -> str:
    """把供应商错误翻译成"对用户意味着什么"（写日志与降级说明用）。"""
    value = str(info or "").strip()
    if amap_error_is_retryable(value):
        return "POI 数据源限流（QPS），本轮候选可能不完整"
    if value.upper().startswith("INVALID_USER_KEY") or "KEY" in value.upper():
        return "POI 数据源密钥无效或未授权，本轮无真实候选"
    if value:
        return f"POI 数据源返回错误 {value}（status={status}），本轮无真实候选"
    return "POI 数据源返回空结果"


# —— 候选供给：太薄就补一次更宽的检索，并把"供给不足"如实写进方案 ——
#
# 实测（2026-10-04）两个真实缺口：
#   1. 库尔勒景点查询被高德限流 → status=0 → 生产候选只剩 1 个；补一次更宽的
#      「景点|景区|公园|…」检索能拿回真实景区（天鹅河/孔雀河/胡杨林…）。
#   2. 库尔勒美食用生产关键词只回 3 条，而通用兜底「美食|餐厅|小吃|本地菜」回 40 条 ——
#      关键词太窄，模型看到的美食候选就只有 3 个。
# 所以：候选不足时**补一次**（只补一次，避免把 QPS 打爆），并且把结论告诉用户，
# 而不是让方案看起来"就是这么点地方"。
SCENIC_WIDENING_KEYWORDS = "景点|景区|公园|游玩|山|湖|河|草原|沙漠|峡谷|湿地|林|寺|古城"
FOOD_WIDENING_KEYWORDS = "美食|餐厅|小吃|本地菜"


def required_daytime_slots(trip_days: int, daily_nodes: int) -> int:
    """排满行程需要的白天节点数（每天 daily_nodes 个）。"""
    days = max(1, int(trip_days or 1))
    per_day = max(1, int(daily_nodes or 1))
    return days * per_day


def required_scenic_candidates(trip_days: int) -> int:
    """自然景观的下限：每天至少能有一个自然点可选。"""
    return max(2, int(trip_days or 1))


def required_food_candidates(trip_days: int) -> int:
    """餐饮候选的下限：出门玩一天两顿在外面吃 → 至少 2×天数，且不少于 4。

    实测教训（2026-10-04）：库尔勒餐饮一直只回 3 条（而通用兜底「美食|餐厅|小吃|本地菜」
    能回 40 条）。上一版阈值写成"少于天数才补"，3 ≥ 2 于是从来不触发 ——
    结果是模型只能自己编馆子，有两次真实路线里**一个餐饮节点都没有**。
    餐饮不是配菜：一天两顿是硬需求，所以阈值按"一天两顿"给。
    """
    return max(4, int(trip_days or 1) * 2)


def category_floor(intent: str, trip_days: int) -> int:
    """交给模型的候选里，每个类别**至少**要有几个（否则模型想排也排不进去）。

    真实缺口（2026-10-04，库尔勒实测）：餐饮候选补到 50 个之后，最终 top 候选里
    依旧是 `{'scenic': 7, 'cultural': 3, 'hotel': 6}` —— 一个餐饮都没有。
    原因是候选桶建在**帕累托前沿**上，前沿按多目标支配关系把餐饮整类挤掉了，
    于是"按类别轮流取"的均衡逻辑手里根本没有餐饮可轮。方案因此长期没有正经吃饭安排。
    """
    days = max(1, int(trip_days or 1))
    if intent == "scenic":
        return max(2, days)
    if intent == "food":
        return max(2, days)  # 一天至少有一顿可选
    if intent == "cultural":
        return max(1, days - 1)  # 人文是点缀，不该占满
    if intent == "shopping":
        return 1
    return 0


def top_up_category_buckets(
    buckets: Mapping[str, Sequence[Mapping[str, Any]]],
    spare: Sequence[Mapping[str, Any]],
    *,
    targets: Mapping[str, int],
    classify: Callable[[Mapping[str, Any]], str],
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, int]]:
    """把被前沿挤掉的类别从**完整候选池**补到目标下限。

    目标下限由 `core/route_composition.composition_targets` 给出（默认自然=人文各半、
    穿插美食住宿；用户明确偏好则加权），所以"补多少"跟着用户口径走，而不是写死。

    返回 `(新 buckets, {类别: 补回数量})`。纯函数：不修改入参、按名字去重、
    只在同类别的 spare 里取（绝不把餐饮塞进观光桶）。
    """
    result: Dict[str, List[Dict[str, Any]]] = {key: [dict(item) for item in items] for key, items in buckets.items()}
    known = {str(item.get("name") or "") for items in result.values() for item in items}
    added: Dict[str, int] = {}

    for intent in ("scenic", "cultural", "food", "shopping", "park"):
        floor = int(targets.get(intent, 0) or 0)
        bucket = result.setdefault(intent, [])
        need = floor - len(bucket)
        if need <= 0:
            continue
        count = 0
        for item in spare:
            if count >= need:
                break
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in known:
                continue
            try:
                actual = str(classify(item) or "")
            except Exception:
                continue
            if actual != intent:
                continue
            known.add(name)
            bucket.append(dict(item))
            count += 1
        if count:
            added[intent] = count
    return result, added


def needs_widening(available: int, required: int) -> bool:
    """候选是否低于下限（低于就该补一次更宽的检索）。"""
    return int(available or 0) < max(1, int(required or 1))


async def widen_candidates(
    fetch: Callable[..., Awaitable[List[Dict[str, Any]]]],
    *,
    city: str,
    keywords: str,
    types: str = "",
    limit: int = 30,
    existing_names: Optional[Iterable[str]] = None,
    fetch_kwargs: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """候选太薄时**补一次**更宽的检索（只补一次，避免把数据源 QPS 打爆）。

    为什么单独抽出来：这段逻辑嵌在几千行的推演生成器里时，"补检索真的跑过吗"
    只能靠碰运气触发（两次真实探针都因为候选够多而没走到）。抽成注入 fetch 的助手后，
    去重/失败不抛/计数都能用假 fetch 做确定性单测。

    `fetch_kwargs` 必须把调用方的取数参数原样带下去 —— 实测教训（2026-10-04）：
    餐饮补检索漏传 `play_worthy=False`，于是补检索用了默认的"必须是玩点"过滤，
    餐饮类别被整类丢掉、返回空列表，表现成"补了却一条都没加回来"，而且日志里什么都不报。
    所以这里把参数透传做成显式契约，并用单测钉死。

    返回 `{added, added_count, error}`：
      - `added` 只含**新增**（按名字去重）且结构合法的记录；
      - 任何异常都不抛，只填 `error`，由调用方如实记录并继续用原候选（绝不阻断出方案）。
    """
    seen = {str(name or "") for name in (existing_names or [])}
    try:
        extra = await fetch(city, keywords=keywords, types=types, limit=limit, **dict(fetch_kwargs or {}))
    except Exception as exc:  # 补充检索失败不能影响主流程
        return {"added": [], "added_count": 0, "error": str(exc)[:120]}

    added: List[Dict[str, Any]] = []
    for item in extra or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        added.append(dict(item))
    return {"added": added, "added_count": len(added), "error": ""}


def pool_supply_report(
    *,
    attractions: int,
    foods: int,
    trip_days: int,
    daily_nodes: int,
    scenic: Optional[int] = None,
    widened: Sequence[str] = (),
    provider_notes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """把"这次真实候选够不够"变成结构化事实（随方案下发，并进降级清单）。

    为什么必须做：候选池太小时，模型会用自己的记忆补地点 —— 用户看到的方案"看起来挺全"，
    实际有些地点不是数据源里的。不写清楚就是拿用户的信任换好看。
    """
    needed = required_daytime_slots(trip_days, daily_nodes)
    known = max(0, int(attractions or 0)) + max(0, int(foods or 0))
    notes = [str(item) for item in (provider_notes or []) if str(item).strip()]

    if int(attractions or 0) <= 0:
        status = "missing"
        notes.append("这次没有取到任何真实景点候选（数据源限流或覆盖不足），方案里的地点以模型知识为主，请逐个核对")
    elif known < needed:
        status = "thin"
        notes.append(
            f"真实白天候选 {known} 个，少于排满 {needed} 个白天节点所需的量；部分地点靠模型知识补，请核对"
        )
    else:
        status = "ok"

    if scenic is not None and status == "ok" and int(scenic) <= 0:
        # 有候选但一个自然点都没有：对"想看自然风景"的诉求等于没满足
        status = "thin"
        notes.append("候选里没有自然景观类地点，想看自然的话这一版会不满足")

    widened_list = [str(item) for item in widened if str(item).strip()]
    if widened_list:
        notes.append("候选偏少，已额外补了一次更宽的检索：" + "、".join(widened_list))

    return {
        "status": status,
        "needed_daytime": needed,
        "known_daytime": known,
        "attractions": max(0, int(attractions or 0)),
        "foods": max(0, int(foods or 0)),
        "scenic": None if scenic is None else max(0, int(scenic)),
        "widened": widened_list,
        "notes": notes,
    }

# 每个意图用于检索的关键词（AMap place/text 接受自然语言关键词）
INTENT_KEYWORDS: Dict[str, str] = {
    "cultural": "博物馆|古迹|寺庙|文化馆",
    # 自然风景：只写"风景区|公园|山水"在新疆这类目的地会漏掉湖/沙漠/胡杨/草原/峡谷
    "scenic": "风景区|公园|山水|湖|湿地|草原|沙漠|峡谷|胡杨|森林公园",
    "food": "小吃|老字号|本地菜",
    "hotel": "酒店|民宿",
    "shopping": "步行街|商场",
    "nightlife": "夜景|酒吧",
    "outdoor": "徒步|登山|露营地",
    "hotspring": "温泉",
    "family": "动物园|科技馆|亲子乐园",
    "photo": "观景台|日出|花海",
    "market": "菜市场|夜市",
    "landmark": "地标|广场|古城",
}
DEFAULT_TYPES = "110000|141200|060400|060100"
# 这些意图属于"玩点"：只有它们才需要过 is_play_worthy（住宿/餐饮有自己的意图）
PLAY_INTENTS: tuple = (
    "cultural", "scenic", "landmark", "outdoor", "family", "photo", "market", "nightlife", "shopping",
)
UNKNOWN_PRICE_MARKERS = ("暂无", "未知", "unavailable", "none", "")

FetchFn = Callable[..., Awaitable[Sequence[Mapping[str, Any]]]]


def parse_amap_location(value: Any) -> Optional[List[float]]:
    """把 AMap 的 'lng,lat' 字符串转成 [lng, lat]；解析不了返回 None。"""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return [float(value[0]), float(value[1])]
        except (TypeError, ValueError):
            return None
    if not isinstance(value, str) or "," not in value:
        return None
    parts = value.split(",")
    try:
        return [float(parts[0]), float(parts[1])]
    except (TypeError, ValueError):
        return None


def _parse_price(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    text = str(raw).strip()
    if any(marker and marker in text.lower() for marker in UNKNOWN_PRICE_MARKERS):
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# "这个地点值不值得当景点" —— 出现在**规划里**的必须是真景点
# ---------------------------------------------------------------------------
# 高德的高层类别（type 的第一段）。只有这些才算"玩点"。
PLAY_TYPE_PREFIXES = (
    "风景名胜",
    "公园广场",
    "科教文化服务",   # 博物馆/展览馆/科技馆/美术馆…
    "体育休闲服务",   # 滑雪场/垂钓园/度假村…
)
# 高德**完整的**一级服务类别表。用来区分"这是高德分类"还是"我们自己的短标签"：
# 行程节点里的 type 常是我们自己的短标签（文化/餐饮/住宿），不能拿高德规则去卡它，
# 否则「type=文化」会被误判成"不是景点"。
AMAP_SERVICE_CATEGORIES = {
    "汽车服务", "汽车销售", "汽车维修", "摩托车服务", "餐饮服务", "购物服务", "生活服务",
    "体育休闲服务", "医疗保健服务", "住宿服务", "风景名胜", "商务住宅", "政府机构及社会团体",
    "科教文化服务", "交通设施服务", "金融保险服务", "公司企业", "道路附属设施", "地名地址信息",
    "公共设施", "事件活动", "室内设施", "通行设施", "公园广场",
}
# 名字里带这些词的一律**不能当景点**：设施、出入口、服务点、以及"借了关键词的"商店/住宿。
# 血的教训（2026-09-19 用户实测 7 天库尔勒方案）：
#   「库尔勒民俗文化博物馆-西北门地上停车场」「巴州博物馆文创空间」「库尔勒园林宾馆」
#   都被当成了景点排在行程里 —— 关键词检索只看名字，不看它到底是什么。
FACILITY_NAME_MARKERS = (
    # 停车与出入口
    "停车场", "停车楼", "停车区", "地上停车", "地下停车", "出入口", "门口", "大门", "侧门",
    # 服务/管理设施
    "售票处", "售票点", "检票口", "服务中心", "服务点", "管理处", "管理站", "指挥部", "派出所",
    "公共厕所", "洗手间", "卫生间", "充电站", "加油站", "加气站", "变电", "配电",
    # 交通枢纽
    "机场", "火车站", "汽车站", "客运站", "地铁站", "公交站", "收费站", "服务区",
)
# 商业/办公/住宿类标记：对"文化/自然风景"这类玩点是硬伤，但对"逛街/夜市"意图是正常场所，
# 所以按意图放行（allow_commerce）。
COMMERCE_NAME_MARKERS = (
    "文创空间", "文创店", "旗舰店", "专卖店", "便利店", "超市", "商场", "购物中心",
    "营业厅", "银行", "atm", "药店", "诊所", "公司", "办公", "写字楼", "商务中心",
    "宾馆", "酒店", "旅馆", "招待所", "民宿", "客栈", "公寓", "小区", "住宅",
)
# 走"逛街/夜市"路线的意图：商业场所是正常的（但设施类标记仍然挡）
COMMERCE_OK_INTENTS = ("shopping", "market", "nightlife")


def is_play_worthy(
    name: Any,
    amap_type: Any = None,
    strict_type: bool = True,
    allow_commerce: bool = False,
) -> bool:
    """这个名字/类别能不能当"玩点"排进行程。

    规则（保守，只挡明显不合适的）：
    1. 名字里带**设施**标记（停车场/出入口/售票处/火车站…）→ 不行；
    2. `allow_commerce=False` 时，名字里带商业/办公/住宿标记（商场/文创空间/宾馆…）→ 也不行；
    3. `strict_type=True` 且类别是**高德一级服务类别**时，必须属于玩点类别
       （风景名胜/公园广场/科教文化服务/体育休闲服务）。我们自己的短标签（文化/餐饮/住宿…）
       不适用这条，避免误杀。
    """
    text = str(name or "").strip().lower()
    if not text:
        return False
    if any(marker in text for marker in FACILITY_NAME_MARKERS):
        return False
    # 站点后缀：高德会把「湿地公园站」这类**公交/地铁站**标成"风景名胜;旅游景点"，
    # 关键词检索就会把它当景点排进行程（实测 2026-10-04 库尔勒真实方案）。
    # 只按后缀判，且放行"驿站"这类真实场所，避免误杀。
    stripped = str(name or "").strip()
    if stripped.endswith("站") and not stripped.endswith("驿站"):
        return False
    if not allow_commerce and any(marker in text for marker in COMMERCE_NAME_MARKERS):
        return False
    raw_type = str(amap_type or "").strip()
    if strict_type and raw_type:
        head = raw_type.split(";")[0].strip()
        if head in AMAP_SERVICE_CATEGORIES:
            return any(head.startswith(prefix) for prefix in PLAY_TYPE_PREFIXES)
    return True


# 需要"严格类别"的玩点意图：逛街/夜市/夜生活本来就发生在商业场所，不能按景点类别卡
STRICT_TYPE_PLAY_INTENTS = ("cultural", "scenic", "landmark", "outdoor", "family", "photo")


def attraction_query_limits(
    poi_limit: int,
    *,
    wants_scenic: bool = False,
    exclude_cultural: bool = False,
) -> Dict[str, int]:
    """按用户诉求分配三类景点检索的配额（自然 / 人文 / 逛街）。

    实测背景（2026-10-04）：用户明确说"想看更多自然景观、想吃地道美食"，
    但配额与诉求无关（自然/人文/逛街固定比例），最终池子是 **16/17 人文** ——
    需求被"检索配额"这一层吃掉了，模型再努力也只能在人文里排序。
    所以这里让诉求直接决定配比：想看自然就把自然放大、人文收成点缀；
    明确排除人文时干脆不发这次检索（省一次调用，也避免"取了又全丢"）。
    """
    base = max(1, int(poi_limit or 1))
    if exclude_cultural:
        cultural = 0
    elif wants_scenic:
        cultural = max(6, base // 4)
    else:
        cultural = max(12, base // 2)
    scenic = base if wants_scenic else max(10, base // 2)
    shopping = max(8, base // 3)
    return {"scenic": scenic, "cultural": cultural, "shopping": shopping}


def candidate_from_poi(poi: Mapping[str, Any], intent: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """AMap POI（或任何同类形状）→ 候选池条目；缺名称或**不适合当景点**则丢弃。"""
    if not isinstance(poi, Mapping):
        return None
    name = str(poi.get("name") or "").strip()
    if not name:
        return None
    resolved_intent = intent or intent_of(poi)
    if resolved_intent in PLAY_INTENTS and not is_play_worthy(
        name,
        poi.get("type"),
        strict_type=resolved_intent in STRICT_TYPE_PLAY_INTENTS,
        allow_commerce=resolved_intent in COMMERCE_OK_INTENTS,
    ):
        # 实测事故：关键词搜"博物馆"会带出「xx博物馆-西北门地上停车场」「xx博物馆文创空间」，
        # 搜"园林"会带出「xx园林宾馆」——它们都只是名字里含关键词，实际是设施/商店/住宿。
        return None
    price = _parse_price(poi.get("cost"))
    sources = poi.get("data_sources") if isinstance(poi.get("data_sources"), Mapping) else {}
    price_source = str(sources.get("cost") or "").strip().lower() or "unavailable"
    candidate: Dict[str, Any] = {
        "name": name,
        "type": str(poi.get("type") or ""),
        "intent": intent or intent_of(poi),
        "price": price,
        "price_source": price_source,
        "rating": poi.get("rating"),
        "open_time": poi.get("open_time"),
        "estimated": bool(poi.get("estimated", price is None)),
        "lnglat": parse_amap_location(poi.get("location")),
        "source": "amap",
    }
    candidate["price_tier"] = (
        "unknown" if price is None else ("free" if price <= 0 else ("economy" if price < 150 else ("comfort" if price < 600 else "quality")))
    )
    candidate["tier_level"] = tier_level(candidate)
    return candidate


def poi_record_from_amap(poi: Mapping[str, Any], city: str = "", amap_key: str = "") -> Dict[str, Any]:
    """高德 place/text 的单条 POI → 我们的**底座记录**（纯函数，可离线单测）。

    契约（血的教训 2026-09-19）：高德只给 `location: "lng,lat"` 字符串，而下游三层匹配
    （`_fill_from_match` / `_find_match_by_coord`）和候选池都只认 **`lnglat` 数组**。
    之前这里只回 `location`，结果整池 0 条有坐标 → 每个方案节点都拿不到坐标 → 地图上
    一个点都画不出来（用户看到的就是"地图空白 + 没有坐标"）。所以这个函数**必须**同时给出
    `location`（原始字符串，用于静态地图/跳转）和 `lnglat`（数组，用于匹配与绘制）。
    """
    import urllib.parse

    name = str(poi.get("name") or "")
    biz_ext = poi.get("biz_ext") if isinstance(poi.get("biz_ext"), Mapping) else {}
    rating = biz_ext.get("rating")
    if isinstance(rating, list):
        rating = rating[0] if rating else None
    cost = biz_ext.get("cost")
    if isinstance(cost, list):
        cost = cost[0] if cost else None
    open_time = biz_ext.get("open_time")

    loc_str = str(poi.get("location") or "")
    lnglat = parse_amap_location(loc_str)

    # 图片统一归一化为 HTTPS（兼容 http:// 与 // 协议相对），避免混合内容拦截导致图片空白
    photos: List[str] = []
    raw_photos = poi.get("photos", [])
    if isinstance(raw_photos, list):
        for item in raw_photos:
            if not isinstance(item, Mapping) or not item.get("url"):
                continue
            url = str(item["url"]).strip()
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("http://"):
                url = "https://" + url[len("http://"):]
            if url.startswith("https://"):
                photos.append(url)
    photos = photos[:3]

    clean_search_name = re.sub(r"[\(（].*?[\)）]", "", name).strip()
    if lnglat:
        # 有坐标就给高德 marker 深链（点开就是准确位置），否则退回关键词搜索
        amap_url = "https://uri.amap.com/marker?position={},{}&name={}".format(
            loc_str.split(",")[0], loc_str.split(",")[1], urllib.parse.quote(clean_search_name)
        )
    else:
        amap_url = "https://www.amap.com/search?query=" + urllib.parse.quote(f"{city} {clean_search_name}")

    map_image = ""
    if loc_str and "," in loc_str and amap_key:
        map_image = (
            "https://restapi.amap.com/v3/staticmap"
            f"?location={loc_str}&zoom=15&size=480*360"
            f"&markers=mid,0xFF0000,A:{loc_str}"
            f"&key={amap_key}"
        )

    rating_source = "amap" if rating not in (None, "") else "unavailable"
    cost_source = "amap" if cost not in (None, "") else "unavailable"
    open_source = "amap" if open_time else "unavailable"
    return {
        "id": str(poi.get("id") or ""),
        "name": name,
        "type": str(poi.get("type", "")).split(";")[0],
        "business_area": str(poi.get("business_area") or ""),
        "address": str(poi.get("address") or ""),
        "location": loc_str,
        "lnglat": lnglat,
        "rating": str(rating) if rating not in (None, "") else "暂无供应商数据",
        "cost": str(cost) if cost not in (None, "") else "暂无供应商数据",
        "open_time": str(open_time) if open_time else "暂无供应商数据",
        "data_sources": {"rating": rating_source, "cost": cost_source, "open_time": open_source},
        "estimated": rating_source == "unavailable" or cost_source == "unavailable" or open_source == "unavailable",
        "photos": photos,
        "amap_url": amap_url,
        "map_image": map_image,
    }


def intents_for_plan(plan: Any, extra: Iterable[str] = ()) -> List[str]:
    """方案里出现过的意图（用于决定"要为哪些类别准备替代品"）。"""
    from .plan_quality import nodes_of  # 延迟导入，避免与 plan_quality 形成模块级循环

    intents: List[str] = []
    for node in nodes_of(plan):
        label = intent_of(node)
        if label and label != "other" and label not in intents:
            intents.append(label)
    for label in extra:
        if label and label not in intents:
            intents.append(label)
    return intents


async def build_candidate_pool(
    city: str,
    intents: Sequence[str],
    fetch: Optional[FetchFn],
    limit_per_intent: int = 6,
    exclude_names: Sequence[str] = (),
    types: str = DEFAULT_TYPES,
    keywords_by_intent: Optional[Mapping[str, str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """按意图检索同类候选，返回 `{intent: [candidate, ...]}`（已按价格升序）。

    - `fetch` 为空或城市为空 → 返回空池（上层据此如实告知"没有候选"，而不是硬凑）；
    - 被 `exclude_names` 点名的候选会被剔除（用户明确说不要的地方，不能又推荐回来）。
    """
    if not fetch or not str(city or "").strip():
        return {}
    keywords_map = dict(INTENT_KEYWORDS)
    if keywords_by_intent:
        keywords_map.update({k: v for k, v in keywords_by_intent.items() if v})

    collected: List[Dict[str, Any]] = []
    for intent in intents:
        keywords = keywords_map.get(intent)
        if not keywords:
            continue
        try:
            pois = await fetch(str(city), keywords, types=types, limit=max(1, int(limit_per_intent)))
        except Exception:
            # 单个意图检索失败不影响其它意图；整体也绝不抛出（规划不能被数据源拖垮）
            continue
        for poi in pois or []:
            candidate = candidate_from_poi(poi, intent=intent)
            if candidate:
                collected.append(candidate)

    excluded = {normalize_text(name) for name in exclude_names if str(name).strip()}
    deduped: Dict[str, Dict[str, Any]] = {}
    for candidate in collected:
        key = normalize_text(candidate.get("name"))
        if not key or key in excluded:
            continue
        deduped.setdefault(key, candidate)
    return index_candidates(list(deduped.values()))
