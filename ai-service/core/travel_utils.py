# -*- coding: utf-8 -*-
"""旅行规划纯函数工具（天气感知调度 / 拥挤度评估 / 个性化画像提示词注入）。

将不依赖外部 SDK 的纯算法集中在此处，便于独立单元测试与复用。
"""

from typing import Any, Dict, List
from collections import OrderedDict

RAIN_KEYWORDS = ["雨", "雪", "雷", "暴雨", "阵雨", "小雨", "中雨", "大雨", "冻雨", "冰雹", "雪"]


def analyze_weather_for_planning(condition: str) -> Dict[str, Any]:
    """根据实时天气推导行程规划建议：雨天/雪天优先推荐室内景点作为备选方案。"""
    cond = str(condition or "")
    is_rainy = any(k in cond for k in RAIN_KEYWORDS)
    is_hot = any(k in cond for k in ["晴", "高温"]) and not is_rainy

    if is_rainy:
        return {
            "prefer_indoor": True,
            "level": "rainy",
            "label": "雨天/降雪",
            "advice": "检测到降水，建议将户外景点调整为博物馆、科技馆、商场、展馆等室内备选方案，并将交通缓冲时间增加 30%。"
        }
    if is_hot:
        return {
            "prefer_indoor": False,
            "level": "hot",
            "label": "高温晴热",
            "advice": "天气炎热，建议将室外活动集中在清晨与傍晚，午后安排室内或树荫遮蔽景点。"
        }
    return {
        "prefer_indoor": False,
        "level": "fine",
        "label": "天气适宜",
        "advice": "天气适宜，可按原计划进行户外与室内均衡游览。"
    }


def estimate_crowdedness(rating: float, traffic_status: str, is_weekend: bool = False) -> Dict[str, Any]:
    """景点拥挤度启发式评估：综合口碑热度 + 实时路况 + 是否周末，输出直观人流量分级。"""
    try:
        r = float(rating or 4.5)
    except Exception:
        r = 4.5
    try:
        t = int(str(traffic_status or "1"))
    except Exception:
        t = 1

    score = r * 15 + (t - 1) * 12 + (12 if is_weekend else 0)
    if score >= 88:
        level, percent, label = "high", 82, "拥挤"
    elif score >= 70:
        level, percent, label = "medium", 58, "适中"
    else:
        level, percent, label = "low", 35, "宽松"
    return {"level": level, "percent": percent, "label": label, "score": round(score, 1)}


def build_personalization_hint(profile: Dict[str, Any], member_profiles: Dict[str, Any], room_members: List[Any]) -> str:
    """根据网关注入的用户画像生成个性化提示词，仅在 A/B treatment 组且有历史反馈时生效。

    这是一个持续的 A/B 测试框架：控制组保持通用推荐，实验组注入画像，
    通过前端埋点点击率与反馈准确率对比，持续优化推荐效果。
    """
    variant = str(profile.get("ab_variant") or "control")
    feedback_count = int(profile.get("feedback_count") or 0)

    if variant != "treatment" or feedback_count <= 0:
        return "【个性化画像状态】本轮采用通用推荐策略（用户画像尚在积累或处于 A/B 对照组）。"

    hits = OrderedDict()
    personal_hint = str(profile.get("prompt_hint") or "").strip()
    if personal_hint:
        hits[personal_hint] = True

    for m in room_members:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        mp = member_profiles.get(mid) if mid else None
        if not isinstance(mp, dict):
            continue
        if int(mp.get("feedback_count") or 0) <= 0:
            continue
        if str(mp.get("ab_variant") or "control") != "treatment":
            continue
        member_hint = str(mp.get("prompt_hint") or "").strip()
        if member_hint:
            hits.setdefault(member_hint, True)

    hint_text = "\n".join(list(hits.keys()))
    return (
        "【个性化旅行画像（高优先级软约束，A/B treatment 组）】\n"
        f"{hint_text}\n"
        "请优先匹配上述偏好维度、消费习惯与兴趣标签，同时兼顾所有成员的诉求平衡。"
    )