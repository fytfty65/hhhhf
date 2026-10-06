"""节点级负反馈（用户点「不喜欢」+ 写的 reason）→ **真的会被执行**的规划信号。

为什么单独一个模块（2026-10-05，用户口径）
----------------------------------------
用户的原话：「如果用户点击不喜欢，或者二次增量不想去这么远的地方，就按照用户的意图来」。
但当时的真实链路是：点「不喜欢」→ 网关写 `FeedbackLog` + 重算画像 → 把**地点名的细标签**
记进 `disliked_tags`，再把最近 5 条反馈原样塞进 `evolution_memory` 发给推演。

于是有两个真实错读：
1. 用户嫌「太远」→ 画像把「山岳/自然风光」记成"他不喜欢" → 下次把整类自然景观压低。
   用户的意图是"别跑这么远"，不是"我不喜欢山"。
2. 用户点名不想去某个地方 → 没有任何机制保证它不再出现：排除名单只由**这一轮文本**里的
   「不想去/换掉」解析（`core.increment.parse_increment`），用户没再打字就等于没说过。

这个模块把负反馈翻成三件可执行的事（口径：**只认负反馈**，正反馈不产生约束）：
- `exclude_names`：被点名不喜欢的具体地点 → 走 `candidate_index.apply_exclusions`（剔除 + 同类替换）；
- `distance_tolerance="low"`：原因里出现「太远/太折腾/跑得累」→ 与用户亲口说「不想跑远」同一个开关
  （`travel_feasibility.daily_far_limit=0`，远点会被日内可行性修复移出）；
- `budget_tendency="low"`：原因里出现「太贵/不值」→ 省钱倾斜。

原因归不了类时**只做排除**，不乱猜（`unknown` 只记录，不改变任何权重）——
"没读懂"和"读成别的意思"是两回事，后者更糟。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .candidate_index import normalize_text

#: 原因里出现这些说法 → 用户嫌的是**距离**，不是这个地方本身
DISTANCE_REASON_MARKERS: Sequence[str] = (
    "太远", "太远了", "远太", "太折腾", "路上太折腾", "跑得太累", "跑太累", "坐车太久",
    "不想跑", "不想走远", "别太远", "不要太远", "换近", "近一点", "近一点", "太偏", "太绕",
)
#: 原因里出现这些说法 → 用户嫌的是**花钱**
PRICE_REASON_MARKERS: Sequence[str] = (
    "太贵", "贵了", "不值", "性价比", "被坑", "宰客", "坑人", "太花钱", "花钱多",
)
#: 原因里出现这些说法 → 用户嫌的是**累/赶**，按"就近"处理（不要赶场）
PACE_REASON_MARKERS: Sequence[str] = (
    "太赶", "赶场", "太累", "太累人", "节奏太快", "没时间", "来不及", "太满", "排太满",
)

#: 这些 Target **不是地点名**：历史满意度/消费样本会把行程标题、城市名、点评词当 Target 塞进来。
#: 拿它们去排除会误伤整座城市（"库尔勒"被判不喜欢 → 名字里带"库尔勒"的点全被换掉）。
#:
#: ⚠️ 不要把「旅游」当黑名单词：真实地点名里就有它（"帕米尔旅游景区"实测被误杀过一次）。
#: 要挡的是**行程标题**（"库尔勒 3 日游"）而不是"带旅游二字的景区"。
NON_PLACE_MARKERS: Sequence[str] = (
    "满意度", "行程", "日游", "方案", "规划", "反馈", "记录", "评分", "评论",
    "消费记录", "不喜欢", "不想去", "太远", "太贵", "太赶",
)

#: 反馈条数上限（网关注入的就是最近 5 条，这里再挡一道，避免历史越攒越多）
DEFAULT_LIMIT = 5


def classify_feedback_reason(reason: Any) -> str:
    """把一句自由文本原因归成 `distance` / `price` / `pace` / `unknown`。

    顺序有讲究：**距离优先**。用户写「太远了而且太贵」时，距离是他真正的痛点
    （贵可以换便宜的同类，远只能换近的），先判距离才不会把远点留着。
    """
    text = str(reason or "")
    if not text.strip():
        return "unknown"
    for marker in DISTANCE_REASON_MARKERS:
        if marker in text:
            return "distance"
    for marker in PRICE_REASON_MARKERS:
        if marker in text:
            return "price"
    for marker in PACE_REASON_MARKERS:
        if marker in text:
            return "pace"
    return "unknown"


def looks_like_place_name(target: Any, *, city: str = "") -> bool:
    """判断反馈里的 `Target` 是不是一个**具体地点名**（只有地点名才适合进排除名单）。

    挡两类：
    1. 城市/标题/点评词（含 `NON_PLACE_MARKERS`），或与本次目的地同名 —— 排除它会误伤整个方案；
    2. 过长文本（用户把一整句话写进了 Target）。
    """
    name = str(target or "").strip()
    if len(name) < 2 or len(name) > 24:
        return False
    if any(marker in name for marker in NON_PLACE_MARKERS):
        return False
    normalized_name = normalize_text(name)
    if city and normalized_name and normalized_name == normalize_text(city):
        return False
    return True


def summarize_feedback(
    evolution_memory: Any,
    *,
    city: str = "",
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """把 `evolution_memory`（网关注入的最近若干条 FeedbackLog）翻成规划信号。

    返回：`{exclude_names, distance_complaints, price_complaints, pace_complaints,
            distance_tolerance, budget_tendency, notes, counts}`

    - `distance_tolerance` / `budget_tendency`：`None` 表示"没读到"，调用方据此决定是否覆盖；
    - `notes`：给人看的披露（"你上次标了不喜欢…这次已不排它"），**不编造**：没反馈就是空。
    """
    memory = evolution_memory if isinstance(evolution_memory, list) else []
    exclude_names: List[str] = []
    distance_complaints: List[str] = []
    price_complaints: List[str] = []
    pace_complaints: List[str] = []
    notes: List[str] = []
    seen: set = set()
    considered = 0

    for entry in memory[: max(0, int(limit))]:
        if not isinstance(entry, Mapping):
            continue
        try:
            score = int(entry.get("Score"))
        except (TypeError, ValueError):
            continue
        if score >= 0:
            continue  # 正反馈不产生任何约束
        considered += 1
        target = str(entry.get("Target") or "").strip()
        reason = str(entry.get("Reason") or "").strip()
        kind = classify_feedback_reason(reason)

        if kind == "distance":
            distance_complaints.append(reason or target)
        elif kind == "price":
            price_complaints.append(reason or target)
        elif kind == "pace":
            pace_complaints.append(reason or target)

        if looks_like_place_name(target, city=city):
            key = normalize_text(target)
            if key not in seen:
                seen.add(key)
                exclude_names.append(target)
                suffix = f"（原因：{reason}）" if reason else ""
                notes.append(f"你上次把「{target}」标为不喜欢{suffix}，本次不再排它")

    distance_tolerance = None
    if distance_complaints:
        distance_tolerance = "low"
        notes.append(
            "你在反馈里提到「" + distance_complaints[0] + "」，本次按就近安排："
            "当天不再安排明显超出距离预算的远点"
        )
    budget_tendency = None
    if price_complaints:
        budget_tendency = "low"
        notes.append("你在反馈里提到「" + price_complaints[0] + "」，本次同类优先更省的选择")

    return {
        "exclude_names": exclude_names,
        "distance_complaints": distance_complaints,
        "price_complaints": price_complaints,
        "pace_complaints": pace_complaints,
        "distance_tolerance": distance_tolerance,
        "budget_tendency": budget_tendency,
        "notes": notes,
        "counts": {
            "negative_total": considered,
            "excluded": len(exclude_names),
            "distance": len(distance_complaints),
            "price": len(price_complaints),
        },
    }


def merge_feedback_into_signals(
    signals: Optional[Mapping[str, Any]],
    feedback: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """把反馈派生的容忍度并进偏好信号，**用户这一轮亲口说的话优先**。

    - 用户这轮明确说了"不介意跑远/多远都行"（`high`）→ 保留 high，不被历史反馈翻回去；
    - 用户这轮没说（`normal`）而反馈说嫌远 → 取 `low`，让远点真的被移出；
    - 用户这轮自己就说了嫌远（`low`）→ 保持 low。
    """
    merged: Dict[str, Any] = dict(signals or {})
    if not isinstance(feedback, Mapping):
        return merged
    tolerance = str(merged.get("distance_tolerance") or "normal").strip().lower()
    if tolerance == "normal" and feedback.get("distance_tolerance") == "low":
        merged["distance_tolerance"] = "low"
    return merged
