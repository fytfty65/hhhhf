"""出行方式的比较与推荐：把候选交通排序成"既合适又便宜"，并说清为什么。

架构位置（重要）
----------------
- **检索**由网关负责：MCP 工具 `searchLowestPriceFlight` / `searchLowestPriceTrain`（最便宜机票/火车）
  与 AMap 路径规划；网关已有 `/api/v1/planning/transport-options` 与 `RankCandidates`（价格/时长 nudge，
  并标注来源）。
- **本模块负责**：把两边的候选归一化成同一形状 → 按"契合度 × 价格"排序 → 给出人话推荐与"票价未核实"
  清单。纯函数、零网络，可离线测；AI 服务侧用它做解释与建议，避免把"最便宜"讲成"最合适"。

严格口径
--------
- **票价未知 ≠ 免费**：`price=None` 的候选不参与"更便宜"的比较，且必须标注"票价未核实"；
- 只有可核实来源（provider/amap/official）才说"价格来自供应商"，网络报价一律标"未核实"；
- 排"最便宜"时优先在同模式下比较（高铁 vs 高铁），跨模式比较必须同时给出时长差。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

MODE_LABELS: Dict[str, str] = {
    "train": "高铁/火车",
    "flight": "飞机",
    "coach": "大巴",
    "drive": "自驾",
    "transit": "公共交通",
    "walk": "步行",
    "ride": "打车",
    "highspeed": "高铁",
    "unknown": "未注明",
}

MODE_SYNONYMS: Dict[str, str] = {
    "高铁": "train",
    "火车": "train",
    "动车": "train",
    "train": "train",
    "飞机": "flight",
    "航班": "flight",
    "机票": "flight",
    "flight": "flight",
    "大巴": "coach",
    "客车": "coach",
    "coach": "coach",
    "自驾": "drive",
    "开车": "drive",
    "drive": "drive",
    "公交": "transit",
    "地铁": "transit",
    "公共交通": "transit",
    "transit": "transit",
    "步行": "walk",
    "walk": "walk",
    "打车": "ride",
    "出租": "ride",
    "ride": "ride",
    "taxi": "ride",
}

VERIFIED_FARE_SOURCES = {"provider", "vendor", "official", "amap", "rollinggo", "12306"}


def normalize_mode(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "unknown"
    lowered = raw.lower()
    if lowered in MODE_LABELS:
        return lowered
    for token, mode in MODE_SYNONYMS.items():
        if token in raw or token in lowered:
            return mode
    return "unknown"


def mode_label(mode: str) -> str:
    return MODE_LABELS.get(str(mode or "unknown"), "未注明")


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or any(marker in text for marker in ("暂无", "未知", "unavailable", "none")):
        return None
    digits = "".join(ch if ch.isdigit() or ch == "." else "" for ch in text)
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def normalize_option(raw: Mapping[str, Any], default_source: str = "unavailable", retrieved_at: Optional[float] = None) -> Dict[str, Any]:
    """把网关/AMap/MCP 的候选归一化成统一形状（容忍字段命名差异）。"""
    if not isinstance(raw, Mapping):
        return {}
    mode = normalize_mode(raw.get("mode") or raw.get("type") or raw.get("transport") or raw.get("vehicle"))
    if mode == "unknown":
        # 供应商返回里常常只有 trainid / flightid 这类编号，据此判模式
        if raw.get("trainid") is not None or raw.get("train_no") is not None or raw.get("trainnumber") is not None:
            mode = "train"
        elif raw.get("flightid") is not None or raw.get("flight_no") is not None or raw.get("flightnumber") is not None:
            mode = "flight"
    price = _number(
        raw.get("price")
        if raw.get("price") is not None
        else raw.get("lowestPrice") or raw.get("basePrice") or raw.get("fare") or raw.get("price_from")
    )
    source = str(raw.get("price_source") or raw.get("source") or raw.get("provider") or default_source).strip().lower()
    duration = _number(
        raw.get("duration_minutes") or raw.get("duration") or raw.get("minutes") or raw.get("duration_min")
    )
    if duration is None:
        seconds = _number(raw.get("duration_seconds"))
        duration = seconds / 60.0 if seconds else None
    transfers = _number(raw.get("transfers") or raw.get("transfer_count"))
    return {
        "mode": mode,
        "mode_label": mode_label(mode),
        "from": str(raw.get("from") or raw.get("origin") or ""),
        "to": str(raw.get("to") or raw.get("destination") or ""),
        "code": str(raw.get("train_no") or raw.get("trainid") or raw.get("flight_no") or raw.get("flightid") or ""),
        "duration_minutes": duration,
        "price": price,
        "price_source": source or "unavailable",
        "fare_verified": bool(price is not None and source in VERIFIED_FARE_SOURCES),
        "transfers": int(transfers) if transfers is not None else None,
        "retrieved_at": retrieved_at if retrieved_at is not None else raw.get("retrieved_at"),
        "provider": str(raw.get("provider") or ""),
    }


def normalize_amap_options(payload: Mapping[str, Any], default_source: str = "amap") -> List[Dict[str, Any]]:
    """AMap 路径规划结果 → 候选（**只有时长，没有票价**，因此票价一律标未核实）。"""
    options: List[Dict[str, Any]] = []
    if not isinstance(payload, Mapping):
        return options
    transits = payload.get("transits")
    if isinstance(transits, list):
        for item in transits:
            if not isinstance(item, Mapping):
                continue
            options.append(
                normalize_option(
                    {
                        "mode": "transit",
                        "duration_minutes": item.get("duration") and float(item["duration"]) / 60.0,
                        "transfers": len(item.get("segments") or []),
                        "price": None,  # AMap 不给票价 → 明确未知
                        "source": default_source,
                    },
                    default_source=default_source,
                )
            )
    for path_key, mode in (("paths", "drive"), ("path", "walk")):
        path = payload.get(path_key)
        if isinstance(path, list) and path and isinstance(path[0], Mapping):
            options.append(
                normalize_option(
                    {
                        "mode": mode,
                        "duration_minutes": path[0].get("duration") and float(path[0]["duration"]) / 60.0,
                        "price": None,
                        "source": default_source,
                    },
                    default_source=default_source,
                )
            )
    return [item for item in options if item]


def _preference_modes(preferences: Optional[Mapping[str, Any]]) -> List[str]:
    if not preferences:
        return []
    raw = preferences.get("transport_preference") or preferences.get("transport") or preferences.get("mode")
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    return [normalize_mode(item) for item in values if item]


def score_option(option: Mapping[str, Any], preferences: Optional[Mapping[str, Any]] = None) -> Tuple[float, List[str]]:
    """契合度打分（越大越好）+ 理由。价格未知不参与价格比较，只标注。"""
    preferences = preferences or {}
    why: List[str] = []
    score = 0.0

    preferred = _preference_modes(preferences)
    mode = str(option.get("mode") or "unknown")
    if preferred and mode in preferred:
        score += 3.0
        why.append(f"符合你偏好的{option.get('mode_label')}")

    pacing = str(preferences.get("pace") or "").lower()
    duration = option.get("duration_minutes")
    if isinstance(duration, (int, float)):
        # 省时加分：100 分钟内不罚，超过按小时轻微递减
        score += max(0.0, 2.0 - max(0.0, float(duration) - 100) / 120.0)
        if pacing in {"intense", "tight"} and duration <= 180:
            score += 1.0
            why.append("时长短，符合紧凑节奏")

    transfers = option.get("transfers")
    if isinstance(transfers, int):
        if transfers <= 1:
            score += 0.5
        else:
            score -= 0.5 * (transfers - 1)
            why.append(f"需换乘 {transfers} 次")

    budget_low = str(preferences.get("budget") or "").lower() in {"low", "true", "经济"} or preferences.get("budget") is True
    price = option.get("price")
    if isinstance(price, (int, float)):
        # 价格越低越好（对数感受），低预算用户加倍看重
        weight = 2.5 if budget_low else 1.0
        score += weight * max(0.0, 3.0 - float(price) / 300.0)
        if budget_low:
            why.append(f"票价 ¥{float(price):.0f}，符合省钱优先")
    else:
        why.append("票价未核实（不计入更便宜的比较）")

    return round(score, 3), why


def rank_options(
    options: Sequence[Mapping[str, Any]],
    preferences: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """按契合度排序；同分时"票价可核实"优先、"更便宜"优先。"""
    ranked: List[Dict[str, Any]] = []
    for option in options or []:
        if not option:
            continue
        score, why = score_option(option, preferences)
        enriched = dict(option)
        enriched["fit_score"] = score
        enriched["why"] = why
        ranked.append(enriched)
    ranked.sort(
        key=lambda item: (
            -float(item.get("fit_score") or 0),
            not bool(item.get("fare_verified")),
            float(item.get("price") if isinstance(item.get("price"), (int, float)) else 10**9),
        )
    )
    return ranked


def advice(
    options: Sequence[Mapping[str, Any]],
    preferences: Optional[Mapping[str, Any]] = None,
    budget_left: Optional[float] = None,
) -> Dict[str, Any]:
    """给出人话建议 + 未核实票价清单 + 备选（说清"便宜多少 / 慢多少"，不把未知票价说成免费）。"""
    ranked = rank_options(options, preferences)
    if not ranked:
        return {
            "recommendation": "暂时没有取到可用的出行方式（数据源未返回），已按未核实处理",
            "ranked": [],
            "unverified_fares": [],
            "alternatives": [],
        }

    best = ranked[0]
    parts = [f"推荐 {best.get('mode_label')}"]
    if isinstance(best.get("price"), (int, float)):
        tag = "供应商报价" if best.get("fare_verified") else "网络报价（未核实）"
        parts.append(f"约 ¥{float(best['price']):.0f}（{tag}）")
    else:
        parts.append("票价未核实")
    if isinstance(best.get("duration_minutes"), (int, float)):
        parts.append(f"约 {float(best['duration_minutes']) / 60:.1f} 小时")
    if best.get("why"):
        parts.append("理由：" + "、".join(best["why"][:2]))

    alternatives: List[str] = []
    best_price = best.get("price") if isinstance(best.get("price"), (int, float)) else None
    best_duration = best.get("duration_minutes") if isinstance(best.get("duration_minutes"), (int, float)) else None
    for other in ranked[1:4]:
        if other.get("mode") == best.get("mode") and other.get("code") == best.get("code"):
            continue
        line = f"{other.get('mode_label')}"
        other_price = other.get("price") if isinstance(other.get("price"), (int, float)) else None
        other_duration = other.get("duration_minutes") if isinstance(other.get("duration_minutes"), (int, float)) else None
        if other_price is not None and best_price is not None:
            delta = other_price - best_price
            line += f"：{'贵' if delta > 0 else '便宜'} ¥{abs(delta):.0f}"
        elif other_price is not None:
            line += f"：约 ¥{other_price:.0f}（未核实）"
        else:
            line += "：票价未核实"
        if other_duration is not None and best_duration is not None:
            diff = other_duration - best_duration
            if abs(diff) >= 15:
                line += f"，{'慢' if diff > 0 else '快'} {abs(diff) / 60:.1f} 小时"
        alternatives.append(line)

    unverified = [
        {"mode": item.get("mode_label"), "code": item.get("code"), "reason": "票价未核实"}
        for item in ranked
        if item.get("price") is None or not item.get("fare_verified")
    ]

    if budget_left is not None and best_price is not None and best_price > budget_left:
        parts.append(f"注意：该方案票价 ¥{best_price:.0f} 已超过你剩余预算 ¥{budget_left:.0f}")
    if any(item.get("price") is None for item in ranked) and best_price is not None:
        parts.append("另有方案未取到票价，未参与比价")

    return {
        "recommendation": "；".join(parts),
        "ranked": ranked,
        "alternatives": alternatives,
        "unverified_fares": unverified[:6],
    }


def cheapest_verified(options: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """取"票价可核实"里最便宜的一个；全都未核实则返回 None（不猜）。"""
    verified = [item for item in options or [] if item.get("fare_verified") and isinstance(item.get("price"), (int, float))]
    if not verified:
        return None
    return min(verified, key=lambda item: float(item["price"]))
