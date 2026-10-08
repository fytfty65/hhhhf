"""事实可信度分级（P3：可插拔的数据新鲜度）。

为什么需要
----------
项目一直坚持「unknown ≠ 0 ≠ 估算」，但**只有价格**这条链路上真正带了可信度
（`node_contract.cost_status`）。同一个节点上的**营业时间、门票、无障碍设施、文化背景**
却没有统一的说法：有的是官网抄来的、有的是模型顺手写的、有的干脆空着，
前端只能自己猜"这条到底算不算数"。

本模块把「一条事实可不可信」变成**结构化、可渲染、可校验**的东西，三件事：

1. **来源分级** `source_type`：这条事实是从哪类地方来的
   （`official_page` / `government` / `ticketing` / `poi_provider` / `amap` /
   `official_wechat` / `knowledge_base` / `model_inference` / `none`）。
2. **新鲜度** `freshness`：`fresh` / `recent` / `stale` / `unknown`
   —— 按 `observed_at` 与"这类事实的保质期"算，不是一个死阈值。
3. **置信度** `confidence`：**由来源分级推导，不由模型自报**。

对外只给一个**用户能读懂**的标签，四态：

    verified  已核实      官方/政府/票务页，且新鲜
    estimated 估算        按公开规则推算（方法学估算），或有出处但已过期
    stale     信息较旧    有出处但超出保质期 —— **仍然展示，但明确说"这信息旧了"**
    unknown   尚未核实    没有来源 / 模型推断 / 压根没有这条事实

设计取舍（与项目一贯口径一致）
------------------------------
- **模型自报的 confidence 一律不采信**：`model_inference` 直接判 `unknown`。
  理由：模型对自己的说法打高分没有任何校准价值，反而会让"编造"看起来可信。
- **过期不等于错**：`stale` 仍然展示事实本身（用户可能就是要这个信息），
  但必须带上"信息较旧、请以官方渠道为准"。
- **没有观测时间 ≠ 新鲜**：`observed_at` 缺失一律 `freshness="unknown"`，
  不因为"看起来像新的"就给 `fresh`。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional

# ---------------------------------------------------------------------------
# 用户可见的四态
# ---------------------------------------------------------------------------
TRUST_VERIFIED = "verified"
TRUST_ESTIMATED = "estimated"
TRUST_STALE = "stale"
TRUST_UNKNOWN = "unknown"

TRUST_LABELS: Dict[str, str] = {
    TRUST_VERIFIED: "已核实",
    TRUST_ESTIMATED: "估算",
    TRUST_STALE: "信息较旧",
    TRUST_UNKNOWN: "尚未核实",
}

# 四态各自给用户的下一步动作（前端直接显示，别让用户自己想"那我该干嘛"）
TRUST_ACTIONS: Dict[str, str] = {
    TRUST_VERIFIED: "可直接参考；出行前建议再确认一次",
    TRUST_ESTIMATED: "按公开规则估算，仅供规划参考",
    TRUST_STALE: "信息较旧，请以官方渠道为准",
    TRUST_UNKNOWN: "当前无法确认，请以官方渠道为准",
}

# ---------------------------------------------------------------------------
# 来源分级
# ---------------------------------------------------------------------------
#: 来源类型 → (可信度权重, 是否算"可核实的一手来源")
SOURCE_TYPES: Dict[str, Dict[str, Any]] = {
    "official_page": {"confidence": 0.9, "first_party": True, "label": "官网页面"},
    "government": {"confidence": 0.95, "first_party": True, "label": "政府/文旅公开页"},
    "official_wechat": {"confidence": 0.8, "first_party": True, "label": "官方公众号"},
    "ticketing": {"confidence": 0.85, "first_party": True, "label": "官方票务平台"},
    "poi_provider": {"confidence": 0.7, "first_party": False, "label": "POI 供应商"},
    "amap": {"confidence": 0.7, "first_party": False, "label": "高德地图"},
    "rollinggo": {"confidence": 0.75, "first_party": False, "label": "供应商实时报价"},
    "knowledge_base": {"confidence": 0.65, "first_party": False, "label": "项目内可信知识库"},
    "platform": {"confidence": 0.5, "first_party": False, "label": "票务平台（非官方）"},
    "web": {"confidence": 0.35, "first_party": False, "label": "网摘"},
    "model_inference": {"confidence": 0.0, "first_party": False, "label": "模型推断（不可采信）"},
    "none": {"confidence": 0.0, "first_party": False, "label": "无来源"},
}

#: 后端/前端已有的来源标记 → 统一的 source_type（不改上游，只做归一）
SOURCE_ALIASES: Dict[str, str] = {
    "official": "official_page",
    "official_site": "official_page",
    "official_site_registry": "official_page",
    "web_page": "official_page",
    "gov": "government",
    "gov_page": "government",
    "mct": "government",
    "miniprogram": "ticketing",
    "vendor": "ticketing",
    "provider": "poi_provider",
    "rollinggo": "rollinggo",
    "amap": "amap",
    "kb": "knowledge_base",
    "knowledge": "knowledge_base",
    "platform": "platform",
    "qunar": "platform",
    "web": "web",
    "search": "web",
    "llm": "model_inference",
    "model": "model_inference",
    "inferred": "model_inference",
}

# ---------------------------------------------------------------------------
# 新鲜度：按"事实种类"给保质期（不是一个死阈值）
# ---------------------------------------------------------------------------
#: 事实种类 → 保质期（天）。过保 → stale，仍在展示但明确提示。
#: 依据：价格与营业时间会被调价/改点（短），文化背景与无障碍设施改得慢（长）。
FRESHNESS_WINDOWS_DAYS: Dict[str, int] = {
    "price": 30,
    "open_time": 60,
    "ticket": 30,
    "transport": 7,
    "weather": 1,
    "crowd": 1,
    "culture": 365,
    "accessibility": 180,
    "default": 90,
}

FRESH_FRESH = "fresh"
FRESH_RECENT = "recent"
FRESH_STALE = "stale"
FRESH_UNKNOWN = "unknown"


def normalize_source_type(raw: Any) -> str:
    """把各种来源写法归一到 `SOURCE_TYPES` 的键；认不出来的一律 `none`（不猜）。"""
    text = str(raw or "").strip().lower()
    if not text:
        return "none"
    if text in SOURCE_TYPES:
        return text
    if text in SOURCE_ALIASES:
        return SOURCE_ALIASES[text]
    # 形如 "official_page:xxx" 或 "amap.poi" 的前缀写法
    head = text.split(":")[0].split(".")[0]
    if head in SOURCE_TYPES:
        return head
    if head in SOURCE_ALIASES:
        return SOURCE_ALIASES[head]
    return "none"


def _coerce_datetime(value: Any) -> Optional[datetime]:
    """解析观测时间；解析不出来返回 None（**不猜**）。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def freshness_of(
    observed_at: Any,
    *,
    fact_kind: str = "default",
    now: Optional[datetime] = None,
) -> str:
    """按观测时间与事实种类的保质期算新鲜度。

    - `observed_at` 缺失/解析不了 → `unknown`（**缺观测时间不等于新鲜**）；
    - 在保质期一半以内 → `fresh`；保质期内 → `recent`；超出 → `stale`。
    """
    moment = _coerce_datetime(observed_at)
    if moment is None:
        return FRESH_UNKNOWN
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    window = FRESHNESS_WINDOWS_DAYS.get(str(fact_kind or "default"), FRESHNESS_WINDOWS_DAYS["default"])
    age = current - moment
    if age < timedelta(0):
        # 未来时间：要么时区搞错了、要么数据有问题 → 不当作新鲜
        return FRESH_UNKNOWN
    if age <= timedelta(days=window / 2):
        return FRESH_FRESH
    if age <= timedelta(days=window):
        return FRESH_RECENT
    return FRESH_STALE


def fact_trust(
    *,
    value: Any = None,
    source: Any = None,
    source_type: Any = None,
    observed_at: Any = None,
    fact_kind: str = "default",
    estimated: Any = False,
    method: str = "",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """给**一条事实**打可信度分级，返回可直接透传给前端的结构。

    参数
    ----
    value        事实本身（`None`/空串 = 没有这条事实）
    source       来源标识（后端各处已有的 `official` / `web` / `amap` …）
    source_type  显式指定来源类型（给了就以它为准，否则从 `source` 归一）
    observed_at  观测时间
    fact_kind    事实种类（price/open_time/culture/accessibility/…），决定保质期
    estimated    上游是否已标"这是估算"
    method       估算方法说明（`estimated=True` 时建议给，前端会显示出来）

    返回
    ----
    `{value, source_type, source_label, observed_at, freshness, confidence, trust, label, action, message, method}`
    """
    has_value = value is not None and str(value).strip() != ""
    resolved_type = normalize_source_type(source_type if source_type is not None else source)
    meta = SOURCE_TYPES.get(resolved_type, SOURCE_TYPES["none"])
    kind = str(fact_kind or "default")
    freshness = freshness_of(observed_at, fact_kind=kind, now=now)

    # ---- 判定四态（顺序很重要：没值 → 模型推断 → 过期 → 官方 → 其余） ----
    if not has_value:
        trust = TRUST_UNKNOWN
    elif resolved_type == "model_inference":
        # 模型推断一律不可采信（哪怕它自称很确定）
        trust = TRUST_UNKNOWN
    elif freshness == FRESH_UNKNOWN:
        # 有来源但不知道什么时候观测的：不能算"已核实"（**缺时间不是新鲜**），
        # 但也不能算"未核实"—— 它确实有出处。归到 estimated 并说明原因。
        trust = TRUST_ESTIMATED
    elif freshness == FRESH_STALE:
        trust = TRUST_STALE
    elif meta["first_party"] and not estimated:
        trust = TRUST_VERIFIED
    elif estimated or not meta["first_party"]:
        trust = TRUST_ESTIMATED
    else:
        trust = TRUST_ESTIMATED

    # 估算/无出处时置信度不能顶着来源权重走
    confidence = float(meta["confidence"])
    if trust == TRUST_UNKNOWN:
        confidence = 0.0
    elif trust == TRUST_STALE:
        # 过期只是"旧"，不代表来源变差：留一半权重
        confidence = round(confidence * 0.5, 3)
    elif trust == TRUST_ESTIMATED and estimated:
        confidence = round(min(confidence, 0.5), 3)

    message = TRUST_ACTIONS[trust]
    if trust == TRUST_STALE and observed_at:
        message = f"观测于 {str(observed_at)[:10]}，{TRUST_ACTIONS[trust]}"
    if trust == TRUST_ESTIMATED and freshness == FRESH_UNKNOWN and has_value:
        message = "有出处但未记录观测时间，按估算处理"
    if resolved_type == "model_inference" and has_value:
        message = "该说法来自模型推断，不作为事实采用"

    return {
        "value": value if has_value else None,
        "source_type": resolved_type,
        "source_label": str(meta["label"]),
        "observed_at": str(observed_at) if observed_at else None,
        "freshness": freshness,
        "confidence": confidence,
        "trust": trust,
        "label": TRUST_LABELS[trust],
        "action": TRUST_ACTIONS[trust],
        "message": message,
        "method": str(method or ""),
    }


def worst_trust(items: Any) -> str:
    """一组事实里**最差**的那一档 —— 用来给区块/方案一个总体口径。

    顺序：unknown < stale < estimated < verified（unknown 最差）。
    空集合 → `unknown`（没有事实就是没核实，不是"已核实"）。
    """
    order = {TRUST_UNKNOWN: 0, TRUST_STALE: 1, TRUST_ESTIMATED: 2, TRUST_VERIFIED: 3}
    worst = None
    for item in items or []:
        trust = ""
        if isinstance(item, Mapping):
            trust = str(item.get("trust") or "")
        else:
            trust = str(item or "")
        if trust not in order:
            continue
        if worst is None or order[trust] < order[worst]:
            worst = trust
    return worst or TRUST_UNKNOWN


def trust_summary(items: Any) -> Dict[str, Any]:
    """统计一组事实的四态分布（给"信息可信度"面板用）。"""
    counts = {TRUST_VERIFIED: 0, TRUST_ESTIMATED: 0, TRUST_STALE: 0, TRUST_UNKNOWN: 0}
    for item in items or []:
        trust = ""
        if isinstance(item, Mapping):
            trust = str(item.get("trust") or "")
        else:
            trust = str(item or "")
        if trust in counts:
            counts[trust] += 1
    total = sum(counts.values())
    return {
        "counts": counts,
        "total": total,
        "worst": worst_trust(items),
        "labels": {key: TRUST_LABELS[key] for key in counts},
    }


__all__ = [
    "FRESHNESS_WINDOWS_DAYS",
    "FRESH_FRESH",
    "FRESH_RECENT",
    "FRESH_STALE",
    "FRESH_UNKNOWN",
    "SOURCE_ALIASES",
    "SOURCE_TYPES",
    "TRUST_ACTIONS",
    "TRUST_ESTIMATED",
    "TRUST_LABELS",
    "TRUST_STALE",
    "TRUST_UNKNOWN",
    "TRUST_VERIFIED",
    "fact_trust",
    "freshness_of",
    "normalize_source_type",
    "trust_summary",
    "worst_trust",
]
