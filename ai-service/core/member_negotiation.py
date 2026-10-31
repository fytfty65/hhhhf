"""成员级多智能体协商（纯 Python、确定性、无 LLM、无网络）。

为什么需要这个模块
------------------
协调器此前只有"群体公平"这一步：按 maximin 从若干候选方案里挑一个，然后报告一个
效用标准差。**没有成员层面的协商** —— 谁提出了哪个方案、谁为谁让了多少、最终没人
满意时是否如实说，全都不可见。前端因此只能写"每位成员专属智能体"，而这条链路里
根本没有成员智能体。

本模块补上这一段：每位成员是一个独立的协商方（MemberAgent），各自对候选方案排序并
提出首选；协调者选定方案后，逐轮尝试"由高满意度成员向低满意度成员让步"的候选切换，
每次让位都受该成员自己声明的让步预算约束。整个过程可复现、可单测。

协议（与 `fairness_gate` 的口径一致）
------------------------------------
1. 每位成员用 `core.simulation.member_utilities_for` 计算自己在各候选路线上的效用，
   按效用降序给出排序与首选方案（proposal）。
2. 协调者按 maximin（先抬高最低效用，其次平均效用）选定初始方案。
3. 与各自最优效用的差距超过 `tolerance`，或效用低于 `acceptance_threshold` 的成员，
   被列为异议成员。
4. 按差距从大到小，寻找可切换的候选：切换必须同时满足
   - 每位成员切换后的效用都不低于其接受阈值（守住底线）；
   - 每位成员的损失不超过其让步预算（知情同意；`non_negotiable` 成员预算视为 0）；
   - 成员效用总和上升（群体福利不被净损失）。
5. 每轮最多接受一次切换，最多 `max_rounds` 轮。异议人数必须下降（或同人数下最低
   效用上升）才接受，保证协商单调收敛、不会来回震荡。

诚实性说明（重要）
------------------
maximin **本身**就是"抬高最低效用"的最优解，因此"再找一个最低效用更高"的候选在
数学上通常不存在。真正可做的成员级协商是：在守住每位成员接受阈值与让步预算的前提
下，让满意的人让出一点，换取总体福利上升、异议人数下降。因此本模块同时报告
`min_utility_before/after` 与 `min_utility_improved`；最低值下降但所有人仍高于阈值时，
transcript 会明说是"让位所致"，不会把它伪装成提升。无可行切换时状态为
`no_consensus`，不编造共识。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .simulation import member_utilities_for
from .text_parsing import extract_preference_signals

# 用户诉求信号 -> 路线节点的粗粒度标签。与 `_CAT_TAG` / fairness 口径保持一致。
SIGNAL_INTEREST_TAGS = (
    ("wants_scenic", "风景"),
    ("wants_cultural", "文化"),
    ("wants_food", "美食"),
    ("wants_shopping", "购物"),
    ("wants_hotel", "住宿"),
)

DEFAULT_ACCEPTANCE_THRESHOLD = 0.45
DEFAULT_TOLERANCE = 0.12
DEFAULT_CONCESSION_BUDGET = 0.08
EPSILON = 1e-9


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _as_float(value: Any, default: float) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def _interests_from_raw(raw: Any) -> List[str]:
    if isinstance(raw, str):
        raw = [raw]
    result: List[str] = []
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
    return result


def member_interests(member: Mapping[str, Any]) -> List[str]:
    """把成员声明转成可与路线标签匹配的兴趣标签。

    优先使用显式声明的 `interests` / `interestTags`；没有显式声明时，才从成员的
    `intent` 文本里抽信号并映射成粗标签（风景/文化/美食/购物/住宿）。这样：
    * fairness 与协商用的是**同一组标签**，不会各报一套满意度；
    * 成员只写了一句自然语言时，仍然能参与协商，而不是被当成"未知偏好"永远 0.5。
    """
    explicit = _interests_from_raw(
        member.get("interests") or member.get("interestTags") or member.get("interest_tags")
    )
    if explicit:
        return explicit
    intent = str(member.get("intent") or "").strip()
    if not intent:
        return []
    try:
        signals = extract_preference_signals(intent)
    except Exception:  # 解析失败只意味着"偏好未知"，不影响协商继续
        return []
    result: List[str] = []
    for key, tag in SIGNAL_INTEREST_TAGS:
        if signals.get(key) and tag not in result:
            result.append(tag)
    return result


@dataclass
class MemberAgent:
    """一位参与协商的真实成员（不是按领域划分的专家）。"""

    id: str
    name: str
    role: str = ""
    interests: List[str] = field(default_factory=list)
    non_negotiable: bool = False
    acceptance_threshold: float = DEFAULT_ACCEPTANCE_THRESHOLD
    tolerance: float = DEFAULT_TOLERANCE
    concession_budget: float = DEFAULT_CONCESSION_BUDGET

    @classmethod
    def from_mapping(cls, member: Mapping[str, Any], index: int = 0) -> "MemberAgent":
        member_id = str(member.get("id") or member.get("name") or f"member-{index + 1}")
        name = str(member.get("name") or member.get("nickname") or f"成员 {index + 1}")
        return cls(
            id=member_id,
            name=name,
            role=str(member.get("role") or ""),
            interests=member_interests(member),
            non_negotiable=bool(member.get("non_negotiable") or member.get("nonNegotiable")),
            acceptance_threshold=_clamp(
                _as_float(member.get("acceptance_threshold"), DEFAULT_ACCEPTANCE_THRESHOLD), 0.0, 1.0
            ),
            tolerance=_clamp(_as_float(member.get("tolerance"), DEFAULT_TOLERANCE), 0.0, 1.0),
            concession_budget=_clamp(
                _as_float(member.get("concession_budget"), DEFAULT_CONCESSION_BUDGET), 0.0, 1.0
            ),
        )

    def utility(self, nodes: Sequence[Mapping[str, Any]]) -> float:
        """在一条路线上的效用，口径与 fairness/仿真完全一致。"""
        scores = member_utilities_for(
            nodes,
            [{"name": self.id, "interests": self.interests, "role": self.role}],
        )
        return _clamp(_as_float(scores.get(self.id), 0.5), 0.0, 1.0)


@dataclass
class _Candidate:
    index: int
    id: str
    name: str
    nodes: List[Mapping[str, Any]]


def _normalize_candidates(
    candidates: Sequence[Any],
    candidate_labels: Optional[Sequence[str]] = None,
) -> List[_Candidate]:
    rows: List[_Candidate] = []
    for index, raw in enumerate(candidates or []):
        nodes: Sequence[Any]
        cand_id = f"candidate-{index + 1}"
        cand_name = (
            str(candidate_labels[index])
            if candidate_labels and index < len(candidate_labels) and candidate_labels[index]
            else f"候选方案 {index + 1}"
        )
        if isinstance(raw, Mapping):
            nodes = raw.get("route") or raw.get("nodes") or []
            cand_id = str(raw.get("id") or cand_id)
            cand_name = str(raw.get("name") or cand_name)
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            nodes = raw
        else:
            nodes = []
        rows.append(
            _Candidate(
                index=index,
                id=cand_id,
                name=cand_name,
                nodes=[node for node in nodes if isinstance(node, Mapping)],
            )
        )
    return rows


def _rank_key(matrix: Sequence[Sequence[float]], member_index: int):
    def key(candidate_index: int) -> tuple:
        return (-matrix[member_index][candidate_index], candidate_index)

    return key


def negotiate_member_routes(
    candidates: Sequence[Any],
    members: Sequence[Mapping[str, Any]],
    *,
    candidate_labels: Optional[Sequence[str]] = None,
    max_rounds: int = 2,
) -> Dict[str, Any]:
    """执行成员级协商，返回可审计的完整记录。

    `candidates` 可以是路线列表，也可以是 `{"id","name","route"}` 字典；`members`
    是房间里的真实成员（至少含 id/name，可选 role/intent/interests/non_negotiable）。
    返回值只包含计算结果，不调用模型，也不修改传入对象。
    """
    cands = _normalize_candidates(candidates, candidate_labels)
    agents = [MemberAgent.from_mapping(member, index) for index, member in enumerate(members or []) if isinstance(member, Mapping)]

    if not cands:
        return {
            "status": "no_candidates",
            "method": "maximin_plus_member_concession",
            "transcript": ["没有可协商的候选方案，未进行成员协商。"],
            "selected_index": 0,
            "final_candidate": None,
            "members": [],
            "candidates": [],
            "concessions": [],
            "utility_trajectory": {},
            "rounds": 0,
        }
    if not agents:
        return {
            "status": "no_members",
            "method": "maximin_plus_member_concession",
            "transcript": ["房间内没有可识别的成员，按单一发起人直接采用首选方案。"],
            "selected_index": 0,
            "final_candidate": {
                "index": 0,
                "id": cands[0].id,
                "name": cands[0].name,
            },
            "members": [],
            "candidates": [
                {"index": cand.index, "id": cand.id, "name": cand.name, "selected": cand.index == 0}
                for cand in cands
            ],
            "concessions": [],
            "utility_trajectory": {},
            "rounds": 0,
        }

    # 效用矩阵：members × candidates（确定性，同一输入永远同一结果）
    matrix: List[List[float]] = [[round(agent.utility(cand.nodes), 6) for cand in cands] for agent in agents]
    count = len(cands)

    def utilities_at(candidate_index: int) -> List[float]:
        return [matrix[m][candidate_index] for m in range(len(agents))]

    def min_of(candidate_index: int) -> float:
        return min(utilities_at(candidate_index)) if agents else 0.0

    def mean_of(candidate_index: int) -> float:
        values = utilities_at(candidate_index)
        return sum(values) / len(values) if values else 0.0

    # ① 每位成员独立排序并提出首选
    rankings: List[List[int]] = [
        sorted(range(count), key=_rank_key(matrix, member_index)) for member_index in range(len(agents))
    ]

    # `non_negotiable` 成员的底线：效用低于其接受阈值的候选不进入协调者的初始选择
    # （除非所有候选都触犯，此时如实保留并标注）。
    def violates_non_negotiable(candidate_index: int) -> bool:
        for member_index, agent in enumerate(agents):
            if agent.non_negotiable and matrix[member_index][candidate_index] + EPSILON < agent.acceptance_threshold:
                return True
        return False

    eligible = [cand.index for cand in cands if not violates_non_negotiable(cand.index)]
    pool = eligible or [cand.index for cand in cands]

    # ② 协调者按 maximin 选初始方案（并列时看平均效用，再按原始顺序）
    selected = sorted(pool, key=lambda c: (-min_of(c), -mean_of(c), c))[0]
    min_before = min_of(selected)

    transcript: List[str] = [
        f"第 0 轮：协调者按 maximin 从 {count} 个候选方案中选定《{cands[selected].name}》，"
        f"组内最低成员效用 {min_before:.2f}。"
    ]
    if not eligible:
        transcript.append("注意：所有候选方案都触及非让步成员的接受阈值，本轮只能保留相对最优者。")

    # ③ 异议判定：低于自身接受阈值，或与**自己**在全部候选里的最优效用差距超过容忍度。
    #    注意 gap 的参照必须是成员自己的最优，而不是"当前候选上最高的那位成员" ——
    #    后者会让高满意度成员永远 gap=0，异议判定形同虚设。
    own_best = [max(matrix[m]) if matrix[m] else 0.0 for m in range(len(agents))]

    def dissenters_of(candidate_index: int) -> List[int]:
        dissenters: List[int] = []
        for member_index, agent in enumerate(agents):
            utility = matrix[member_index][candidate_index]
            gap = own_best[member_index] - utility
            if utility + EPSILON < agent.acceptance_threshold or gap > agent.tolerance + EPSILON:
                dissenters.append(member_index)
        return sorted(dissenters, key=lambda m: (-(own_best[m] - matrix[m][candidate_index]), m))

    initial_dissenters = dissenters_of(selected)
    for member_index in initial_dissenters:
        agent = agents[member_index]
        best_index = rankings[member_index][0] if rankings[member_index] else selected
        transcript.append(
            f"第 0 轮：成员「{agent.name}」在《{cands[selected].name}》上的效用为 "
            f"{matrix[member_index][selected]:.2f}（其首选为《{cands[best_index].name}》"
            f"{matrix[member_index][best_index]:.2f}），列为异议成员。"
        )

    trajectory: Dict[str, List[float]] = {agent.id: [round(matrix[m][selected], 4)] for m, agent in enumerate(agents)}
    concessions: List[Dict[str, Any]] = []
    rounds: List[Dict[str, Any]] = []
    accepted_moves = 0

    def dissent_count(candidate_index: int) -> int:
        return len(dissenters_of(candidate_index))

    def acceptable_switch(target: int) -> Optional[Dict[str, Any]]:
        """判断 current -> target 是否是「知情让步 + 群体不亏」的安全切换。"""
        losses: List[Dict[str, Any]] = []
        gains: List[Dict[str, Any]] = []
        # 基线是**当前**方案（可能已因上一轮让步而改变），不是最初的 maximin 方案。
        total_before = sum(utilities_at(current))
        total_after = sum(utilities_at(target))
        if total_after + EPSILON < total_before:
            return None
        for member_index, agent in enumerate(agents):
            before = matrix[member_index][current]
            after = matrix[member_index][target]
            if after + EPSILON < agent.acceptance_threshold:
                # 不能把任何成员推到自己的接受阈值以下
                return None
            loss = before - after
            if loss > EPSILON:
                budget = 0.0 if agent.non_negotiable else agent.concession_budget
                if loss > budget + EPSILON:
                    return None
                losses.append(
                    {
                        "member_id": agent.id,
                        "member_name": agent.name,
                        "utility_before": round(before, 4),
                        "utility_after": round(after, 4),
                        "utility_loss": round(loss, 4),
                        "budget": round(budget, 4),
                    }
                )
            elif after > before + EPSILON:
                gains.append(
                    {
                        "member_id": agent.id,
                        "member_name": agent.name,
                        "utility_before": round(before, 4),
                        "utility_after": round(after, 4),
                        "utility_gain": round(after - before, 4),
                    }
                )
        return {"losses": losses, "gains": gains, "total_before": total_before, "total_after": total_after}

    current = selected
    for round_index in range(1, max(0, int(max_rounds)) + 1):
        dissenters = dissenters_of(current)
        if not dissenters:
            break
        candidates_order = sorted(
            [cand.index for cand in cands if cand.index != current],
            key=lambda c: (dissent_count(c), -min_of(c), -mean_of(c), c),
        )
        chosen: Optional[int] = None
        chosen_detail: Optional[Dict[str, Any]] = None
        for target in candidates_order:
            if dissent_count(target) >= len(dissenters):
                # 切换必须让异议人数下降；否则谈不上"协商有进展"
                continue
            detail = acceptable_switch(target)
            if detail is None:
                continue
            chosen = target
            chosen_detail = detail
            break

        if chosen is None:
            transcript.append(
                f"第 {round_index} 轮：没有既守住每位成员接受阈值（{DEFAULT_ACCEPTANCE_THRESHOLD:.2f}）"
                f"、又不超出让步预算、还能减少异议的切换方案，保持《{cands[current].name}》。"
            )
            rounds.append(
                {
                    "round": round_index,
                    "from": cands[current].id,
                    "to": None,
                    "accepted": False,
                    "dissenters_before": len(dissenters),
                    "dissenters_after": len(dissenters),
                }
            )
            break

        assert chosen_detail is not None
        before_index = current
        current = chosen
        accepted_moves += 1
        for loss in chosen_detail["losses"]:
            concessions.append(
                {
                    "round": round_index,
                    "member_id": loss["member_id"],
                    "member_name": loss["member_name"],
                    "from_candidate": cands[before_index].id,
                    "from_candidate_name": cands[before_index].name,
                    "to_candidate": cands[chosen].id,
                    "to_candidate_name": cands[chosen].name,
                    "utility_loss": loss["utility_loss"],
                    "budget": loss["budget"],
                }
            )
        gain_text = "、".join(
            f"「{gain['member_name']}」+{gain['utility_gain']:.2f}" for gain in chosen_detail["gains"]
        )
        loss_text = "、".join(
            f"「{loss['member_name']}」-{loss['utility_loss']:.2f}（预算 {loss['budget']:.2f}）"
            for loss in chosen_detail["losses"]
        )
        transcript.append(
            f"第 {round_index} 轮：由《{cands[before_index].name}》切换到《{cands[chosen].name}》。"
            f"让位：{loss_text or '无人让位'}；获益：{gain_text or '无明确获益'}。"
            f"异议人数 {len(dissenters)} → {dissent_count(chosen)}。"
        )
        rounds.append(
            {
                "round": round_index,
                "from": cands[before_index].id,
                "to": cands[chosen].id,
                "accepted": True,
                "dissenters_before": len(dissenters),
                "dissenters_after": dissent_count(chosen),
                "concessions": chosen_detail["losses"],
                "gains": chosen_detail["gains"],
            }
        )
        for member_index, agent in enumerate(agents):
            trajectory[agent.id].append(round(matrix[member_index][current], 4))

    final_dissenters = dissenters_of(current)
    min_after = min_of(current)
    min_improved = min_after > min_before + EPSILON
    if len(agents) <= 1 or not final_dissenters:
        status = "consensus"
    elif accepted_moves > 0:
        status = "partial"
    else:
        status = "no_consensus"

    if status == "consensus" and accepted_moves > 0:
        transcript.append(
            f"协商结论：通过 {accepted_moves} 次成员让位达成共识。最低成员效用 {min_before:.2f} → {min_after:.2f}"
            + ("（提升）。" if min_improved else "（下降，但仍高于每位成员的接受阈值，属让位代价）。")
        )
    elif status == "consensus":
        if len(agents) > 1:
            transcript.append(
                f"协商结论：首轮即达成共识，最低成员效用 {min_after:.2f}，无人需要让步。"
            )
            # 首选与最终方案不一致、但差距在容忍度内的成员也要点名，否则"共识"
            # 看起来像所有人首选相同 —— 那会掩盖真实的妥协。
            compromises = [
                f"「{agents[m].name}」首选《{cands[rankings[m][0]].name}》，"
                f"接受《{cands[current].name}》（差 {own_best[m] - matrix[m][current]:.2f} ≤ 容忍度 {agents[m].tolerance:.2f}）"
                for m in range(len(agents))
                if rankings[m][0] != current
            ]
            if compromises:
                transcript.append("成员接受情况：" + "；".join(compromises) + "。")
        else:
            transcript.append("协商结论：单人出行，无需多人协商。")
    elif status == "partial":
        names = "、".join(f"「{agents[m].name}」" for m in final_dissenters)
        transcript.append(
            f"协商结论：最低成员效用 {min_before:.2f} → {min_after:.2f}，异议人数下降但未清零；"
            f"仍未满足 {names} 的诉求，请调整偏好或约束。"
        )
    else:
        names = "、".join(f"「{agents[m].name}」" for m in final_dissenters)
        transcript.append(
            f"协商结论：未达成共识。{names} 的诉求在当前候选与让步预算内无法满足，保留原方案。"
        )

    member_rows: List[Dict[str, Any]] = []
    for member_index, agent in enumerate(agents):
        best_index = rankings[member_index][0]
        final_utility = matrix[member_index][current]
        member_rows.append(
            {
                "id": agent.id,
                "name": agent.name,
                "role": agent.role,
                "interests": list(agent.interests),
                "non_negotiable": agent.non_negotiable,
                "acceptance_threshold": round(agent.acceptance_threshold, 4),
                "tolerance": round(agent.tolerance, 4),
                "concession_budget": round(agent.concession_budget, 4),
                "proposal": {
                    "candidate_index": best_index,
                    "candidate_id": cands[best_index].id,
                    "candidate_name": cands[best_index].name,
                    "utility": round(matrix[member_index][best_index], 4),
                },
                "ranked": [
                    {
                        "candidate_index": c,
                        "candidate_id": cands[c].id,
                        "candidate_name": cands[c].name,
                        "utility": round(matrix[member_index][c], 4),
                    }
                    for c in rankings[member_index]
                ],
                "final_utility": round(final_utility, 4),
                "utility_gap_to_best": round(matrix[member_index][best_index] - final_utility, 4),
                "dissenting": member_index in final_dissenters,
                "accepted_compromise": final_utility + EPSILON < matrix[member_index][best_index],
                "concession_count": sum(1 for item in concessions if item["member_id"] == agent.id),
            }
        )

    return {
        "status": status,
        "method": "maximin_plus_member_concession",
        "rounds": len(rounds),
        "max_rounds": max(0, int(max_rounds)),
        "selected_index": current,
        "final_candidate": {
            "index": current,
            "id": cands[current].id,
            "name": cands[current].name,
        },
        "initial_candidate": {"index": selected, "id": cands[selected].id, "name": cands[selected].name},
        "min_utility_before": round(min_before, 4),
        "min_utility_after": round(min_after, 4),
        "min_utility_improved": min_improved,
        "dissenters_initial": [agents[m].id for m in initial_dissenters],
        "dissenters_final": [agents[m].id for m in final_dissenters],
        "concessions": concessions,
        "utility_trajectory": trajectory,
        "round_log": rounds,
        "members": member_rows,
        "candidates": [
            {
                "index": cand.index,
                "id": cand.id,
                "name": cand.name,
                "min_utility": round(min_of(cand.index), 4),
                "mean_utility": round(mean_of(cand.index), 4),
                "selected": cand.index == current,
            }
            for cand in cands
        ],
        "transcript": transcript,
    }
