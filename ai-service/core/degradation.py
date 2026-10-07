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
    # 高德被限流：候选会**静默变少**，看起来像"这城市没什么景点"（2026-10-05）
    "amap_gate": {
        "label": "高德请求闸门",
        "impact": "被限流时候选会变少（不是这座城市没有景点），闸门已自动放慢间隔",
    },
    # 地点简介：联网富化可能抓错页面（实测把 YouTube 帮助中心当成了喀什的书院简介）
    "place_description": {
        "label": "地点简介",
        "impact": "对不上号或没出处的简介已撤下，改成「暂无简介」——不再拿搜到的网页文字顶上",
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
    # 每日密度不足：不是"少排了"，而是"一天跑不了那么多远的地方"（距离是硬约束）
    "daily_balance": {
        "label": "每日节点密度",
        "impact": "有几天节点比目标少，因为同一天跑不了那么多远的地方；优先保证不赶路",
    },
    "travel_feasibility": {
        "label": "日内里程",
        "impact": "同一天里的移动里程或远景区数量偏多，实际跑起来会赶",
    },
    # 远的一天可以接受、但要标明；并且**把"想换的话换哪个"一起给出来**（2026-10-05）
    "travel_alternatives": {
        "label": "远点的可换选项",
        "impact": "跑得远的那天有同类近点可换；说一句就换，不说就不动",
    },
    # 用户明确说了"不想跑远"时是**执行**而不是提议（口径 2026-10-05）
    "travel_substitution": {
        "label": "按你的意思换的近点",
        "impact": "你说过不想跑那么远，远点已就地换成同类的近点",
    },
    # 用户**点名**要换成哪一家（点了「换住这家」）
    "node_swap": {
        "label": "按你指定的换",
        "impact": "你点名要换的那一家已就地换好（时段不变，不会被自动优化改掉）",
    },
    # 吃住离当天的玩点偏远：不是"没排"，是"来回通勤会吃掉游玩时间"（2026-10-05 实测 anchoring 掉分）
    "anchoring": {
        "label": "吃住与玩点的距离",
        "impact": "吃住离当天的玩点偏远，来回通勤会吃掉游玩时间；换到玩点附近可以直接说一句",
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


def _unique_names(items: Any, key: str = "name", limit: int = 3) -> List[str]:
    """从动作列表里取**去重后**的名字（保序）。

    为什么需要（2026-10-05 实测）：收口分两道（门禁后 + 出稿前），同一个远点可能被移出两次
    （第二次是它被补回来之后又移出），于是披露写成"已移出 2 个（巴楚博物馆、巴楚博物馆）" ——
    用户看到的是"这里是不是写错了"。**动作次数可以是真的，但名字该去重**。
    """
    names: List[str] = []
    for item in items or []:
        value = str(_as_mapping(item).get(key) or "").strip()
        if value and value not in names:
            names.append(value)
        if len(names) >= limit:
            break
    return names


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
        # 每个源的具体原因（2026-10-05 实测需要）：只说"不可达"看不出是"被反爬挡住"还是"没查"
        summary = str(price_audit.get("provider_summary") or "").strip()
        detail = f"联网取价不可用（{reason}）"
        if summary:
            detail += f"；各源情况：{summary[:160]}"
        detail += "。可配置 WEB_SEARCH_PROXY 让搜索走代理，或把景区/酒店官网加进允许清单"
        items.append(_entry("price_web", STATUS_MISSING, detail))
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
            timed_out = int(price_audit.get("timed_out") or 0)
            if timed_out:
                # 实测（2026-10-05）：取价预算用完时有节点根本没轮到，要说清是"没时间查"
                # 而不是"网上搜不到" —— 两者的后续动作不一样。
                detail += f"，其中 {timed_out} 个是取价预算用完没轮到（不是搜不到）"
            items.append(_entry("price_web", STATUS_DEGRADED, detail))
        elif total and verified_ratio < 0.999:
            items.append(_entry("price_web", STATUS_DEGRADED, "部分价格来自网络检索，按「估算」标明，不算已验证"))

    budget = _as_mapping(data.get("budget_report"))
    if int(budget.get("unknown_count") or 0) > 0 and not any(item["source"] == "price_web" for item in items):
        items.append(
            _entry("price_web", STATUS_DEGRADED, f"{int(budget.get('unknown_count') or 0)} 个节点没有价格来源")
        )

    # 3.45) 票务平台价（2026-10-07 用户放开"其他数据源"）：它**有出处但不是官方**，
    #       必须单独说一句 —— 否则用户会把携程/去哪儿的页面价当成官方票价。
    platform_priced = [
        str(node.get("name") or node.get("location") or "").strip()
        for node in _route_of(data)
        if str((_as_mapping(node.get("data_sources")) or {}).get("cost_estimate") or "").strip().lower()
        == "platform"
    ]
    platform_priced = [name for name in platform_priced if name]
    if platform_priced:
        names = _unique_names([{"name": name} for name in platform_priced], limit=4)
        items.append(
            _entry(
                "price_web",
                STATUS_DEGRADED,
                f"{len(platform_priced)} 个价格来自票务平台（携程/去哪儿等，**非官方**，可能变动）："
                + "、".join(names)
                + "。节点上已标「票务平台价，非官方」，不计入「可核实合计」",
            )
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

    # 3.55) 配比目标受候选供给限制（2026-10-05 实测）：库尔勒目标 4 人文而候选只有 3 个人文点、
    #       喀什目标 9 而候选 7 个。这时"人文偏少"不是方案没安排好，而是**这座城市就这么点候选** ——
    #       必须按供给缺口说出来，否则用户会以为是我们没给排。
    composition = _as_mapping(data.get("composition"))
    supply_limits = _as_mapping(composition.get("supply_limited"))
    supply_counts = _as_mapping(composition.get("supply_counts"))
    _label_of = {"scenic": "自然景区", "cultural": "人文", "food": "特色美食", "hotel": "住宿"}
    for key, limit in supply_limits.items():
        info = _as_mapping(limit)
        target = int(info.get("target") or 0)
        available = int(info.get("available") or supply_counts.get(key) or 0)
        items.append(
            _entry(
                "candidate_pool",
                STATUS_DEGRADED,
                f"{_label_of.get(str(key), str(key))}候选只有 {available} 个（按你的偏好目标 {target} 个），"
                "这是候选供给的限制，不是没安排；换城市或放宽偏好可以补上",
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
        # 名字**去重**（同一个点可能被两道收口各移出一次）；次数保留真实动作数
        names = "、".join(_unique_names(moved_out))
        items.append(
            _entry(
                "travel_feasibility",
                STATUS_DEGRADED,
                f"一天跑不了两个远景区，已移出 {len(moved_out)} 个（{names}）；想保留可以说明愿意多留一天",
            )
        )

    # 3.85) 住宿兜底（2026-10-04 实测发现：伊宁 3 天方案出现过"一晚都没住宿"）：
    #       补上了要说明补在哪一晚；候选池真的没有的，如实说"需要你自己定"，绝不编一个酒店。
    lodging_repair = _as_mapping(data.get("lodging_repair"))
    lodging_actions = [item for item in (lodging_repair.get("actions") or []) if isinstance(item, Mapping)]
    if lodging_actions:
        parts = "、".join(f"第 {item.get('day')} 晚→{item.get('to')}" for item in lodging_actions[:3])
        items.append(_entry("route_composition", STATUS_DEGRADED, f"原本有晚上没安排住宿，已补上：{parts}"))
    missing_nights = list(lodging_repair.get("missing_days") or [])
    if missing_nights:
        items.append(
            _entry(
                "candidate_pool",
                STATUS_MISSING,
                "第 " + "、".join(str(day) for day in missing_nights) + " 晚没有可用住宿候选，需要你自己定",
            )
        )

    # 3.88) 住宿比夜数多（2026-10-05 横向对照实测：喀什 3 天排了 4 个住宿、乌鲁木齐 2 天 3 个）：
    #       多出来的已移出，但要说明"这趟只需要住几晚"，否则用户看到的数字对不上。
    lodging_trim = _as_mapping(data.get("lodging_trim"))
    lodging_dropped = [item for item in (lodging_trim.get("actions") or []) if isinstance(item, Mapping)]
    if lodging_dropped:
        nights = int(lodging_trim.get("nights") or 0)
        names = "、".join(str(item.get("from")) for item in lodging_dropped[:3])
        items.append(
            _entry(
                "route_composition",
                STATUS_DEGRADED,
                f"住宿比夜数多（这趟 {nights} 晚），已移出 {len(lodging_dropped)} 个：{names}",
            )
        )

    # 3.9) 城市公园限量落到实处（2026-10-04 用户口径：默认 1~2 个）：路线里超出的已被
    #      换成景区/美食或移出，动过什么要写清楚，别让用户以为方案自己"少了两个地方"。
    # 3.95) 硬约束收口（2026-10-05 实测发现的闭环漏洞）：最终均衡/类别门禁会从候选池补节点，
    #      可能把刚修好的约束又破坏掉；收口动作说明"最终门禁之后又修了一次"这件事真实发生过。
    closeout = _as_mapping(data.get("hard_constraint_closeout"))
    if closeout.get("error"):
        items.append(_entry("route_composition", STATUS_MISSING, f"出稿前硬约束收口失败：{str(closeout.get('error'))[:100]}"))
    closeout_actions = [str(item) for item in (closeout.get("actions") or []) if str(item).strip()]
    if closeout_actions:
        items.append(
            _entry(
                "route_composition",
                STATUS_DEGRADED,
                "最终均衡之后约束又被破坏过，已在出稿前重新收口：" + "；".join(closeout_actions[:3]),
            )
        )

    park_trim = _as_mapping(data.get("park_trim"))
    park_actions = [item for item in (park_trim.get("actions") or []) if isinstance(item, Mapping)]
    if park_actions:
        replaced = [item for item in park_actions if str(item.get("code")) == "park_over_cap_replaced"]
        dropped = [item for item in park_actions if str(item.get("code")) == "park_over_cap_dropped"]
        parts = []
        if replaced:
            parts.append(
                "已替换 " + str(len(replaced)) + " 处（"
                + "、".join(f"{item.get('from')}→{item.get('to')}" for item in replaced[:2]) + "）"
            )
        if dropped:
            parts.append("已移出 " + str(len(dropped)) + " 处（" + "、".join(str(item.get("from")) for item in dropped[:2]) + "）")
        items.append(
            _entry(
                "route_composition",
                STATUS_DEGRADED,
                f"城市公园超过上限 {park_trim.get('cap')} 个：" + "；".join(parts) + "（想把公园换成景区可以直接说）",
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

    # 3.6) 按地理就近重排过分天（2026-10-07 实测加的）：把远点挪到**不需要当天回市区**的那一天，
    #      最赶的一天从 449km 降到 231km。这是"我们改动了行程结构"，必须说出来 ——
    #      用户看到"第 3 天怎么和上次不一样"时有据可查；同时把前后里程摆出来。
    rebalance = _as_mapping(data.get("day_rebalance"))
    if rebalance and rebalance.get("applied"):
        items.append(
            _entry(
                "route_composition",
                STATUS_DEGRADED,
                f"按地理就近重排了分天：最赶的一天 {rebalance.get('before_max_km')}km → "
                f"{rebalance.get('after_max_km')}km（全程 {rebalance.get('before_total_km')}km → "
                f"{rebalance.get('after_total_km')}km）；换天的点："
                + "、".join(_unique_names([{"name": name} for name in (rebalance.get("moved") or [])], limit=4))
                + "。住宿、你点名的点、缺坐标的点都没动",
            )
        )

    # 3.65) 补餐的结果与"宁可不塞"（2026-10-07 实测）：某天没有饭点时，收口后会从候选池 /
    #       富余的那天补一个；但**太远的饭点不塞**（实测喀什第 3 天最近的候选在 82.6km 外的市区，
    #       塞进去只是把门禁"做"通过，用户不可能为一顿晚饭开 82km，anchoring 也会掉）。
    #       这件事必须说出来，否则用户只看到"这天没有饭点"，不知道我们试过、也不知道为什么。
    meal_topup = _as_mapping(data.get("meal_topup"))
    skipped_meals = [item for item in (meal_topup.get("skipped") or []) if isinstance(item, Mapping)]
    if skipped_meals:
        items.append(
            _entry(
                "route_composition",
                STATUS_DEGRADED,
                "；".join(
                    f"第 {item.get('day')} 天没有安排餐饮：{item.get('reason')}" for item in skipped_meals[:2]
                )
                + "。想吃的话说一声就补（也可以让它换成近一点的玩点）",
            )
        )

    # 3.9) 高德被限流（2026-10-05）：限流会**静默让候选变少** —— 看起来像"这城市没什么景点"，
    #      必须说出来，否则用户会把"供应商限流"读成"这里没得玩"。
    gate = _as_mapping(data.get("amap_gate"))
    if gate:
        limits = int(gate.get("limits_this_round") or gate.get("limits") or 0)
        items.append(
            _entry(
                "amap_gate",
                STATUS_DEGRADED,
                f"高德请求被限流 {limits} 次，闸门已自动放慢到 {gate.get('interval_seconds')}s"
                f"（基准 {gate.get('base_interval_seconds')}s）"
                + (f"：{str(gate.get('last_info'))[:60]}" if gate.get("last_info") else "")
                + "。候选可能因此变少 —— 这不是「这城市没什么景点」",
            )
        )

    # 3.10) 地点简介抓错页面 / 是无出处的套话（2026-10-05 实测）：
    #       并排读两份方案才发现，一份把 `Official YouTube TV Help Center ...` 当成喀什某书院的简介、
    #       另一份把"中医认为酸奶有助于健康"当成某个餐馆的简介。用户看到的是**假介绍**，
    #       必须进"遗留问题"，而且要说清是哪几个点。
    desc_problems = (quality.get("truthfulness") or {}).get("unrelated_descriptions") or []
    if desc_problems:
        names = _unique_names(desc_problems, limit=4)
        first = desc_problems[0] if isinstance(desc_problems[0], Mapping) else {}
        first_why = str(first.get("why") or "")
        items.append(
            _entry(
                "place_description",
                STATUS_DEGRADED,
                f"{len(desc_problems)} 个地点的简介与地点对不上或没有出处（{'、'.join(names)}）"
                + (f"：{first_why[:50]}" if first_why else "")
                + "。这些简介已在方案里改成「暂无简介」，不再拿搜到的网页文字顶上",
            )
        )

    # 4) 住宿价格：路线里的住宿节点一个报价都没有（高德对酒店普遍不返回价格）
    lodgings = [node for node in _route_of(data) if _looks_like_lodging(node)]
    if lodgings and not any(_has_price(node) for node in lodgings):
        items.append(_entry("hotel_price", STATUS_MISSING, f"{len(lodgings)} 个住宿节点都没拿到报价"))

    # 4.5) 每日密度不足（2026-10-05）：出稿前的硬约束收口会移出"一天跑不了"的远景点，
    #      于是某些天的节点数少于目标。这是**距离优先于密度**的必然结果，必须如实说出来 ——
    #      否则用户只会觉得"这天怎么这么空"，而系统的沉默会让它看起来像少排了。
    balance = _as_mapping(data.get("daily_balance"))
    if balance.get("recomputed_after_closeout") and balance.get("satisfied") is False:
        deficient = [str(day) for day in (balance.get("deficient_days") or [])]
        if deficient:
            items.append(
                _entry(
                    "daily_balance",
                    STATUS_DEGRADED,
                    "收口后第 " + "、".join(deficient) + " 天节点偏少：一天跑不了那么多远的地方，"
                    "优先保证不赶路；想更满一点可以说「愿意多留一天」或「放宽每天节点数」",
                )
            )

    # 4.6) 吃住离当天的玩点偏远（2026-10-05）：`anchoring` 一直是"只打分不解释"的维度 ——
    #      实测伊宁掉到 0.8（有吃住离玩点 40km+），但方案里一个字都没说。
    #      距离是用户最在意的事之一（"住得远不远"），必须列出来并给出可行动作。
    quality_detail = _as_mapping(_as_mapping(data.get("quality")).get("anchoring"))
    far_anchors = [item for item in (quality_detail.get("far_nodes") or []) if isinstance(item, Mapping)]
    if far_anchors:
        examples = "、".join(
            f"第 {item.get('day')} 天 {item.get('name')}（约 {item.get('distance_km')}km）"
            for item in far_anchors[:3]
        )
        items.append(
            _entry(
                "anchoring",
                STATUS_DEGRADED,
                f"{len(far_anchors)} 处吃住离当天的玩点超过 {quality_detail.get('threshold_km')}km：{examples}"
                "。想住近一点、或者把玩点换到住处附近，直接说一句就行",
            )
        )
    else:
        # 分数掉下来的另一个真实原因（2026-10-05 喀什实测 anchoring=0.8 但 far_nodes 是空的）：
        # **有吃住节点没有坐标**，通勤距离根本算不出来 —— 缺数据同样要如实说，
        # 否则用户只看到一个没解释的 0.8（这次是刚被清掉的 0,0 坐标）。
        coverage = quality_detail.get("coordinate_coverage")
        if isinstance(coverage, (int, float)) and coverage < 1.0:
            invalid = _as_mapping(data.get("invalid_coordinates"))
            names = [str(item) for item in (invalid.get("names") or []) if str(item).strip()]
            who = "（" + "、".join(names[:3]) + "）" if names else ""
            items.append(
                _entry(
                    "anchoring",
                    STATUS_DEGRADED,
                    f"有一批吃住节点没有可信坐标{who}，"
                    f"「离玩点多远」算不出来（可核实坐标覆盖 {round(float(coverage) * 100)}%）；"
                    "这些点的通勤距离请在导航里确认",
                )
            )

    # 4.7) 跑得远的那天：**可换的近点**（2026-10-05 用户口径：远的一天可以接受、但要标明；
    #      真想换得等他说一句 —— 所以这里把"换哪个"先摆出来，让他一句话就能兑现）
    alternatives = _as_mapping(data.get("travel_alternatives"))
    alt_days = [item for item in (alternatives.get("days") or []) if isinstance(item, Mapping)]
    if alt_days:
        parts: List[str] = []
        for item in alt_days[:3]:
            options = [option for option in (item.get("options") or []) if isinstance(option, Mapping)]
            if options:
                parts.append(
                    f"第 {item.get('day')} 天（约 {item.get('travel_km')}km）可换成 "
                    + "、".join(
                        f"{option.get('name')}（离当天其它点约 {option.get('nearest_day_km')}km）" for option in options[:2]
                    )
                )
            else:
                parts.append(f"第 {item.get('day')} 天（约 {item.get('travel_km')}km）候选池里没有同类近点可换")
        items.append(
            _entry(
                "travel_alternatives",
                STATUS_DEGRADED,
                "；".join(parts) + "。想换直接说一句就行，不说就不动",
            )
        )

    # 4.8) 用户说了"不想跑远"→ 真的换了（不是提议）：换成了谁、原先多远，都要写清楚
    substitution = _as_mapping(data.get("travel_substitution"))
    swap_actions = [item for item in (substitution.get("actions") or []) if isinstance(item, Mapping)]
    if swap_actions:
        items.append(
            _entry(
                "travel_substitution",
                STATUS_DEGRADED,
                "；".join(
                    f"第 {item.get('day')} 天 {item.get('from')}（离市中心约 {item.get('from_km')}km）→ {item.get('to')}"
                    for item in swap_actions[:3]
                )
                + "。这是按你「不想跑那么远」的意思换的，想反悔直接说一句换回来",
            )
        )

    # 4.9) 用户点名要换的那一家（点了「换住这家」）：换了谁、换成了谁，都要写清楚
    node_swap = _as_mapping(data.get("node_swap"))
    if node_swap.get("error"):
        items.append(_entry("node_swap", STATUS_MISSING, f"你想换的那一家没能换上：{str(node_swap.get('error'))[:120]}"))
    else:
        swap_actions = [item for item in (node_swap.get("actions") or []) if isinstance(item, Mapping)]
        if swap_actions:
            items.append(
                _entry(
                    "node_swap",
                    STATUS_DEGRADED,
                    "；".join(
                        f"第 {item.get('day')} 天 {item.get('from')} → {item.get('to')}" for item in swap_actions[:3]
                    )
                    + "。这是你点名要的那一家，已就地换好（时段不变）；想改回来说一句就行",
                )
            )

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
