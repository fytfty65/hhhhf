"""语义缓存"能不能复用这份旧方案"的判据（纯函数，可单测）。

为什么单独一个模块（2026-10-05 实测）：语义缓存**命中过、但几乎全被拒**。
日志里明明是 `✅ [语义缓存] 精确命中 伊宁/3天`，紧接着整条推演照样跑满 70s ——
因为这里原本有一条**自成一套**的更严判据："每一天都必须同时有景区和餐饮"。
而系统自己产出的方案未必满足它（收口阶段可能把某天多余的远景区/节点移出），
于是缓存等于被关掉：同一个请求每次都重新花一次大模型的钱。

正确口径是：**缓存复用应当用"这份旧方案当初是否通过了同一套门禁"来判断**，
而不是另发明一套更严的规则。旧方案的 payload 里本来就带着生成时的门禁结论：
  - `daily_balance.ok`（每日密度/均衡门禁）
  - `daily_balance.category_balance.ok`（类别门禁：每天要有景区/餐饮/人文）
  - `composition.audit.ok` + `supply_limited`（配比对账；供给受限导致的偏差不算方案问题）
  - `hard_constraint_closeout.error`（出稿前收口有没有失败）
只要这些没有明确失败、且结构仍然过得去，就可以秒回复用 —— 与"新方案"同一把尺子。

对**没有这些字段的旧缓存条目**（历史上写入的），退回原来的结构判据（宁可重新生成）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Sequence


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _norm(value: Any) -> str:
    """去掉空白并小写，用于比对"这个名字是不是同一个地方"（与 candidate_index 同口径的轻量版）。"""
    return "".join(str(value or "").split()).lower()


def cached_plan_decision(
    *,
    payload: Any,
    route: Any,
    days: int,
    daily_count: int,
    signals: Optional[Mapping[str, Any]] = None,
    classify: Optional[Callable[[str, str, bool], str]] = None,
    balance_ok: Optional[bool] = None,
    exclude_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """判断这份缓存方案能不能直接复用。

    返回 `{reusable, reason, checks, has_recorded_gates}`：
      - `has_recorded_gates=False` 表示这是老条目（没有生成时的门禁结论），走严格结构判据；
      - `reason` 给人看，说明"为什么拒"或"凭什么敢用"；
      - `exclude_names`：用户点过"不喜欢"的地点。缓存里只要还有它就**不能复用** ——
        否则用户刚说"别再去帕米尔"，下一次请求被含这个点的旧缓存秒回，看起来像反馈没生效。
    """
    signals = signals or {}
    final_data = _as_mapping(_as_mapping(payload).get("final_data"))
    daily_balance = _as_mapping(final_data.get("daily_balance"))
    category_balance = _as_mapping(daily_balance.get("category_balance"))
    composition = _as_mapping(final_data.get("composition"))
    composition_audit = _as_mapping(composition.get("audit"))
    closeout = _as_mapping(final_data.get("hard_constraint_closeout"))

    recorded = {
        "daily_balance": daily_balance.get("satisfied", daily_balance.get("ok")),
        "category_balance": category_balance.get("ok"),
        "composition": composition_audit.get("ok"),
    }
    has_recorded_gates = any(isinstance(value, bool) for value in recorded.values())
    explicit_culture_only = bool(signals.get("wants_cultural")) and not any(
        signals.get(key) for key in ("wants_food", "wants_scenic", "wants_hotel", "wants_shopping")
    )

    checks: Dict[str, Any] = {
        "balance": balance_ok,
        "recorded_gates": {key: value for key, value in recorded.items() if value is not None},
        "closeout_error": str(closeout.get("error") or "")[:120] or None,
        "explicit_culture_only": explicit_culture_only,
    }

    if not isinstance(route, list) or not route:
        return {"reusable": False, "reason": "缓存里没有路线", "checks": checks, "has_recorded_gates": has_recorded_gates}

    # ⓪ 用户点过"不喜欢"的地点绝不能从缓存里回来（2026-10-05 用户口径）。
    #    放在最前面：这是**用户明确表达过的意图**，不该被"这份方案当初门禁都过了"说服。
    terms = [_norm(term) for term in (exclude_names or []) if _norm(term)]
    if terms:
        cached_names = [
            str(item.get("name") or item.get("location") or "")
            for item in route
            if isinstance(item, Mapping)
        ]
        hits = [
            name for name in cached_names
            # 空名字必须排除：`"" in term` 恒为真，会把"没有名字的节点"判成命中
            if len(_norm(name)) >= 2
            and any(term in _norm(name) or _norm(name) in term for term in terms)
        ]
        checks["excluded_present"] = hits
        if hits:
            return {
                "reusable": False,
                "reason": "缓存里还有你标记不喜欢的地点：" + "、".join(hits),
                "checks": checks,
                "has_recorded_gates": has_recorded_gates,
            }

    # ① 结构底线：每一天至少得有一个白天节点。
    #    ⚠️ 这里**不拿"每日节点数达标"当否决项**（2026-10-05 实测）：出稿前的硬约束收口
    #    会移出"一天跑不了"的远景点，某些天因此少于目标节点数 —— 但**系统本来就会下发这样的方案**
    #    （距离是硬约束，密度是软目标）。拿它否决缓存，只会让用户为同一个方案再等一遍大模型，
    #    换来的还是同样密度。稀疏要在方案里**如实披露**（见 core/degradation 的 daily_density），
    #    而不是"假装重新算一遍就好了"。
    _by_day_any: Dict[int, int] = {}
    for item in route:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or item.get("location") or "")
        category = str(classify(name, str(item.get("type") or ""), bool(item.get("is_hotel")))) if classify else ""
        try:
            day = int(item.get("day") or 1)
        except (TypeError, ValueError):
            day = 1
        if category != "hotel":
            _by_day_any[day] = _by_day_any.get(day, 0) + 1
    empty_days = [day for day in range(1, max(1, int(days or 1)) + 1) if _by_day_any.get(day, 0) <= 0]
    checks["empty_days"] = empty_days
    if empty_days:
        return {
            "reusable": False,
            "reason": "第 " + "、".join(str(day) for day in empty_days) + " 天一个白天节点都没有",
            "checks": checks,
            "has_recorded_gates": has_recorded_gates,
        }

    # ② 出稿前收口失败过的方案不复用（那时约束是没兜住的）
    if checks["closeout_error"]:
        return {
            "reusable": False,
            "reason": f"出稿前收口失败过：{checks['closeout_error']}",
            "checks": checks,
            "has_recorded_gates": has_recorded_gates,
        }

    # ③ 生成时明确失败过的门禁 → 不复用（如实说明是哪一条）。
    #    ⚠️ 但**供给受限导致的配比偏差不算方案问题**：这类偏差在生成时就记在
    #    `composition.supply_limited` 里、并已如实披露给用户（"候选只有 N 个，不是没安排"）。
    #    如果因为这种偏差就把缓存否掉，等于让用户为"这座城市候选就这么多"多付一次大模型调用。
    supply_limited = _as_mapping(composition.get("supply_limited"))
    deviations = [item for item in (composition_audit.get("deviations") or []) if isinstance(item, Mapping)]
    supply_only_composition_failure = (
        recorded.get("composition") is False
        and bool(supply_limited)
        and all(str(item.get("intent") or "") in supply_limited for item in deviations)
    )
    checks["supply_only_composition_failure"] = supply_only_composition_failure
    # 只把"重算一遍能改好的问题"当否决项：配比没排够（非供给原因）、出稿收口失败。
    # 每日密度/类别门禁的缺口**不作为否决**（原因见上面 ① 的说明），只记进 checks 供诊断。
    failed = [
        key for key, value in recorded.items()
        if value is False and key == "composition" and not supply_only_composition_failure
    ]
    if failed:
        return {
            "reusable": False,
            "reason": "生成时这些门禁没过：" + "、".join(failed),
            "checks": checks,
            "has_recorded_gates": has_recorded_gates,
        }

    if has_recorded_gates:
        # 有生成时的门禁结论：配比（非供给原因）没失败、出稿收口没失败、也没有空白天 → 可以复用
        reason = "复用与生成时同一套判据：配比/收口/每日非空；每日密度与类别的门禁结论一并记录"
        if supply_only_composition_failure:
            reason = "配比偏差全部由候选供给限制解释（已在方案里如实披露），可以复用"
        return {
            "reusable": True,
            "reason": reason,
            "checks": checks,
            "has_recorded_gates": True,
        }

    # ④ 老条目（没有门禁结论）：退回结构判据 —— 默认城市游每天要有景区 + 餐饮，
    #    用户明确"只看文化"时不套这条。
    if explicit_culture_only or classify is None:
        return {
            "reusable": True,
            "reason": "老缓存条目，用户明确只看文化（不套每日景区+餐饮）" if explicit_culture_only else "老缓存条目，仅通过结构校验",
            "checks": checks,
            "has_recorded_gates": False,
        }
    by_day: Dict[int, set] = {}
    for item in route:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or item.get("location") or "")
        category = str(classify(name, str(item.get("type") or ""), bool(item.get("is_hotel"))) or "")
        try:
            day = int(item.get("day") or 1)
        except (TypeError, ValueError):
            day = 1
        by_day.setdefault(day, set()).add(category)
    missing = [
        day for day in range(1, max(1, int(days or 1)) + 1)
        if not {"scenic", "food"}.issubset(by_day.get(day, set()))
    ]
    checks["days_missing_scenic_or_food"] = missing
    if missing:
        return {
            "reusable": False,
            "reason": "老缓存条目：第 " + "、".join(str(day) for day in missing) + " 天缺景区或餐饮",
            "checks": checks,
            "has_recorded_gates": False,
        }
    return {"reusable": True, "reason": "老缓存条目：结构校验通过", "checks": checks, "has_recorded_gates": False}
