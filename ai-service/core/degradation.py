"""数据降级登记处：哪个外部依赖没取到、用户因此损失了什么，只在这里说一次。

为什么需要这一层
----------------
现在"哪个数据没取到"散落在各处：`transport_audit.note`、`price_audit.summary`、
`long_trip.error`、`review.error`、`constraints.error`、以及满屏的"暂无供应商数据"。
前端要分别认识每个字段，漏一个就是**"未核实没渲染出来"**（同类开源项目的真实失败模式：
后端算好了未核实字段，前端适配层没映射，界面上一次都没出现）。

这一层做两件事：
1. `DATA_SOURCES`：一张**注册表**——每个数据源/能力的人话名称与"用户损失了什么"，
   文案只在后端一处定义，前端从注册表渲染（不在前端硬编码名字）；
2. `collect_degradations(payload)`：从**既有 payload 推导**降级项（不新增任何取数逻辑），
   统一成 `{source, label, status, reason, impact}`。

状态只有三种，够用且不撒谎：
- `ok`：拿到了（一般不进列表）；
- `degraded`：拿到了但不完整 / 是估算 / 用了备用方案（必须说明）；
- `missing`：完全没拿到。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_MISSING = "missing"

# 数据源/能力注册表：文案与"用户损失"都在这里，前端不再自己编
DATA_SOURCES: Dict[str, Dict[str, str]] = {
    "amap_poi": {
        "label": "高德地点数据",
        "impact": "地点本身与坐标可能缺，评分/营业时间按「未核实」呈现",
    },
    "price_web": {
        "label": "价格检索",
        "impact": "没查到价格的花费只能标「估算/未核实」，不计入已核实合计",
    },
    "hotel_price": {
        "label": "住宿价格",
        "impact": "住宿花费无法核实（高德对酒店普遍不返回价格），预算结论只能按缺价处理",
    },
    "candidate_pool": {
        "label": "候选地点池",
        "impact": "缺玩点/缺餐/住宿夜数不够时无法自动补，只能如实告诉你",
    },
    "route_composition": {
        "label": "行程配比",
        "impact": "这一趟的自然/人文/美食/住宿数量与你说的偏好有偏差，按实际排出来的算",
    },
    "travel_feasibility": {
        "label": "日内里程",
        "impact": "同一天里的移动里程或远景区数量偏多，实际跑起来会赶",
    },
    "transport_options": {
        "label": "跨城出行方式",
        "impact": "只能比时长，班次与票价按「未核实」标注",
    },
    "long_trip_segmentation": {
        "label": "长途分段生成",
        "impact": "没生成的段落是空的，需要再跑一次或你手动调整",
    },
    "plan_review": {
        "label": "自动复核",
        "impact": "时间顺序/重复/缺口这一类问题这次没有被自动检查",
    },
    "plan_quality": {
        "label": "规划质量核对",
        "impact": "预算、门禁与诉求核对这次没有结果",
    },
    "constraint_check": {
        "label": "诉求逐条核对",
        "impact": "你提的每条要求有没有落实，这次没能逐条核对",
    },
    "llm_plan": {
        "label": "多智能体推演",
        "impact": "本次方案是候选池保底合成（不是模型推演结果），可稍后重新推演一次",
    },
}


def _entry(source: str, status: str, reason: str) -> Dict[str, Any]:
    meta = DATA_SOURCES.get(source, {})
    return {
        "source": source,
        "label": meta.get("label", source),
        "impact": meta.get("impact", ""),
        "status": status,
        "reason": reason,
    }


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _route_of(data: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    route = data.get("route")
    if not isinstance(route, (list, tuple)):
        return []
    return [node for node in route if isinstance(node, Mapping)]


def _looks_like_lodging(node: Mapping[str, Any]) -> bool:
    tags = node.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    text = " ".join(
        [str(node.get("type") or ""), str(node.get("name") or ""), " ".join(str(tag) for tag in tags)]
    )
    return any(key in text for key in ("住宿", "酒店", "民宿", "客栈", "青旅", "hotel")) or bool(node.get("is_hotel"))


def _has_price(node: Mapping[str, Any]) -> bool:
    for field in ("cost_estimate", "cost", "price"):
        value = node.get(field)
        if value in (None, "", "暂无供应商数据", "未知"):
            continue
        text = str(value).strip()
        if text and any(char.isdigit() for char in text):
            return True
    return False


def collect_degradations(payload: Any) -> Dict[str, Any]:
    """从既有 payload 推导"哪些数据没拿到/不完整"，不新增任何取数逻辑。"""
    data = _as_mapping(payload)
    items: List[Dict[str, Any]] = []

    # 0) 整份方案是不是"保底路线"（大模型不可用时由候选池合成）：
    #    这是最深的一层降级，必须让用户一眼看出来，而不是悄悄当成正常推演结果。
    if str(data.get("status") or "").strip() == "degraded_fallback":
        items.append(_entry("llm_plan", STATUS_DEGRADED, "大模型不可用，本次方案改由高德候选池合成"))

    # 1) 主流程：质量核对 / 复核 / 诉求核对 直接报错
    quality = _as_mapping(data.get("quality"))
    if quality.get("error"):
        items.append(_entry("plan_quality", STATUS_MISSING, str(quality.get("error"))[:120]))

    review = _as_mapping(data.get("review"))
    if review.get("error"):
        items.append(_entry("plan_review", STATUS_MISSING, str(review.get("error"))[:120]))
    else:
        needs = [str(item) for item in (review.get("needs_data") or [])]
        if "needs_candidates" in needs:
            items.append(
                _entry(
                    "candidate_pool",
                    STATUS_DEGRADED if review.get("used_pool") else STATUS_MISSING,
                    "候选池里没有更多可用的同类地点" if review.get("used_pool") else "这次没有取到候选地点",
                )
            )
        if "needs_data" in needs:
            items.append(_entry("price_web", STATUS_MISSING, "还有节点缺价格/开放时间，暂时补不上"))

    constraints = _as_mapping(data.get("constraints"))
    if constraints.get("error"):
        items.append(_entry("constraint_check", STATUS_MISSING, str(constraints.get("error"))[:120]))

    # 2) 长途分段：整块失败 / 有段落没生成出来
    long_trip = _as_mapping(data.get("long_trip"))
    if long_trip.get("error"):
        items.append(_entry("long_trip_segmentation", STATUS_MISSING, str(long_trip.get("error"))[:120]))
    else:
        first_round = _as_mapping(long_trip.get("first_round"))
        failed = list(first_round.get("failed") or [])
        skipped = list(first_round.get("skipped") or [])
        if failed or skipped:
            parts = []
            if failed:
                parts.append("第 " + "、".join(str(item) for item in failed) + " 段没生成出来")
            if skipped:
                parts.append("第 " + "、".join(str(item) for item in skipped) + " 段超出本次上限还没排")
            items.append(_entry("long_trip_segmentation", STATUS_DEGRADED, "；".join(parts)))
        repair_failed = list(long_trip.get("repair_failed_segments") or [])
        if repair_failed:
            items.append(
                _entry(
                    "long_trip_segmentation",
                    STATUS_DEGRADED,
                    "补了一次仍没补上（从第 " + "、".join(str(item) for item in repair_failed) + " 天起）",
                )
            )

    # 3) 价格：完全不可核实 / 部分估算是两种不同的说法
    price_audit = _as_mapping(data.get("price_audit"))
    if price_audit.get("error"):
        items.append(_entry("price_web", STATUS_MISSING, str(price_audit.get("error"))[:120]))
    elif price_audit.get("provider_reachable") is False:
        # 取价源不可达要说清楚"不是没查，是查不了"，并把可配置的出口指出来
        status = _as_mapping(price_audit.get("provider_status"))
        reason = str(status.get("error") or "搜索源不可达")[:120]
        items.append(
            _entry(
                "price_web",
                STATUS_MISSING,
                f"联网取价不可用（{reason}）；可配置 WEB_SEARCH_PROXY 让搜索走代理，或继续用「需要你确认」核对",
            )
        )
    else:
        unknown_ratio = float(price_audit.get("unknown_ratio") or 0.0)
        verified_ratio = float(price_audit.get("verified_ratio") or 0.0)
        total = int(price_audit.get("total") or 0)
        if total and unknown_ratio >= 0.999:
            items.append(_entry("price_web", STATUS_MISSING, f"{total} 个节点一个价格都没取到"))
        elif unknown_ratio > 0:
            missing_names = list(price_audit.get("still_unknown") or price_audit.get("unknown") or [])[:3]
            detail = f"未取到 {round(unknown_ratio * 100)}% 的价格"
            if missing_names:
                detail += "（" + "、".join(str(name) for name in missing_names) + "）"
            items.append(_entry("price_web", STATUS_DEGRADED, detail))
        elif total and verified_ratio < 0.999:
            items.append(_entry("price_web", STATUS_DEGRADED, "部分价格来自网络检索，按「估算」标明，不算已验证"))

    budget = _as_mapping(data.get("budget_report"))
    if int(budget.get("unknown_count") or 0) > 0 and not any(item["source"] == "price_web" for item in items):
        items.append(
            _entry("price_web", STATUS_DEGRADED, f"{int(budget.get('unknown_count') or 0)} 个节点没有价格来源")
        )

    # 3.5) 候选供给不足（真实缺口，2026-10-04）：数据源限流/覆盖不足时真实候选会很少，
    #      方案里的地点会有一部分来自模型知识 —— 这件事必须在"遗留问题"里说出来，
    #      否则"看起来挺全"的方案会被当成全部来自真实数据源。
    supply = _as_mapping(data.get("candidate_supply"))
    supply_status = str(supply.get("status") or "").strip()
    if supply_status in {"thin", "missing"}:
        supply_notes = [str(item) for item in (supply.get("notes") or []) if str(item).strip()]
        items.append(
            _entry(
                "candidate_pool",
                STATUS_MISSING if supply_status == "missing" else STATUS_DEGRADED,
                supply_notes[0] if supply_notes else "本次真实候选偏少，部分地点靠模型知识补",
            )
        )

    # 3.7) 日内物理现实性（硬约束过滤层，2026-10-04）：一天跑不完的行程要如实说，
    #      包括"已自动移出多余远景区"这种情况（移出了什么也要写清楚）。
    travel = _as_mapping(data.get("travel_feasibility"))
    travel_notes = [str(item) for item in (travel.get("notes") or []) if str(item).strip()]
    if travel.get("error"):
        items.append(_entry("travel_feasibility", STATUS_MISSING, str(travel.get("error"))[:120]))
    elif travel_notes:
        items.append(_entry("travel_feasibility", STATUS_DEGRADED, "；".join(travel_notes[:2])))
    repaired = _as_mapping(data.get("travel_repair"))
    moved_out = list(repaired.get("moved_out") or [])
    if moved_out:
        names = "、".join(str(_as_mapping(item).get("name") or "") for item in moved_out[:3])
        items.append(
            _entry(
                "travel_feasibility",
                STATUS_DEGRADED,
                f"一天跑不了两个远景区，已移出 {len(moved_out)} 个（{names}）；想保留可以说明愿意多留一天",
            )
        )

    # 3.8) 明确排除项的实际处置（2026-10-04）：用户说"不要博物馆"，方案里真出现过又被换掉/移出的，
    #      也要写清楚 —— 让用户知道系统按他的禁令动过什么，而不是悄悄改了。
    exclusion = _as_mapping(data.get("exclusion_repair"))
    if exclusion.get("error"):
        items.append(_entry("route_composition", STATUS_MISSING, f"按禁令修正失败：{str(exclusion.get('error'))[:100]}"))
    else:
        exclusion_actions = [item for item in (exclusion.get("actions") or []) if isinstance(item, Mapping)]
        if exclusion_actions:
            replaced = [item for item in exclusion_actions if str(item.get("code")) == "forbidden_category_replaced"]
            dropped = [item for item in exclusion_actions if str(item.get("code")) == "forbidden_category_dropped"]
            parts = []
            if replaced:
                parts.append(
                    "已替换 " + str(len(replaced)) + " 处（"
                    + "、".join(f"{item.get('from')}→{item.get('to')}" for item in replaced[:2]) + "）"
                )
            if dropped:
                parts.append("已移出 " + str(len(dropped)) + " 处（" + "、".join(str(item.get("from")) for item in dropped[:2]) + "）")
            items.append(_entry("route_composition", STATUS_DEGRADED, "你说了不要的类别：" + "；".join(parts)))

    # 3.6) 配比对账（用户口径，2026-10-04）：这一趟排出来的自然/人文/美食/住宿数量，
    #      与"默认自然=人文各半 + 穿插美食住宿"或"用户明确偏好加权"的目标不一致时如实说。
    composition = _as_mapping(data.get("composition"))
    composition_audit_data = _as_mapping(composition.get("audit"))
    if composition_audit_data and composition_audit_data.get("ok") is False:
        composition_notes = [str(item) for item in (composition_audit_data.get("notes") or []) if str(item).strip()]
        items.append(
            _entry(
                "route_composition",
                STATUS_DEGRADED,
                "；".join(composition_notes[:2]) if composition_notes else "实际配比与目标不符",
            )
        )

    # 4) 住宿价格：路线里的住宿节点一个报价都没有（高德对酒店普遍不返回价格）
    lodgings = [node for node in _route_of(data) if _looks_like_lodging(node)]
    if lodgings and not any(_has_price(node) for node in lodgings):
        items.append(_entry("hotel_price", STATUS_MISSING, f"{len(lodgings)} 个住宿节点都没拿到报价"))

    # 5) 跨城出行方式
    transport = _as_mapping(data.get("transport_audit"))
    if transport.get("error"):
        items.append(_entry("transport_options", STATUS_MISSING, str(transport.get("error"))[:120]))
    elif transport.get("legs"):
        unverified = sum(1 for leg in transport.get("legs") or [] if _as_mapping(leg).get("unverified_fares"))
        no_options = sum(1 for leg in transport.get("legs") or [] if not _as_mapping(leg).get("options"))
        if unverified or no_options:
            items.append(
                _entry(
                    "transport_options",
                    STATUS_DEGRADED,
                    f"{no_options} 段没取到可用班次、{unverified} 段的票价没有可核实来源",
                )
            )

    counts: Dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    sources = sorted({item["source"] for item in items})
    if not items:
        summary = "数据源这次都取到了，没有降级项。"
    else:
        summary = "{n} 项数据不完整或没拿到：{names}".format(
            n=len(items),
            names="、".join(next((item["label"] for item in items if item["source"] == source), source) for source in sources),
        )
    return {
        "items": items,
        "total": len(items),
        "counts": counts,
        "sources": sources,
        "summary": summary,
    }


def degradation_for(payload: Any, source: str) -> Optional[Dict[str, Any]]:
    """取某一个数据源的降级项（前端/测试都常用）。"""
    for item in collect_degradations(payload)["items"]:
        if item["source"] == source:
            return item
    return None
