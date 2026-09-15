# 智能时间管理：景点开放时间感知（周一闭馆规避）+ 行程节奏判断。
# 纯函数实现，便于单元测试；供推演引擎与前端共用节奏结论。

from typing import Any, Dict, List, Optional

# 周一普遍闭馆的场馆类型关键词（用于开放时间感知）
CLOSED_MONDAY_KEYWORDS = ["博物馆", "美术馆", "科技馆", "纪念馆", "展览馆", "图书馆", "陈列馆"]


def detect_monday_closure_risk(
    name: str = "",
    po_type: str = "",
    tags: Optional[List[str]] = None,
) -> bool:
    """判断某个景点是否属于周一常闭馆的场馆类型。"""
    text = f"{name} {po_type or ''} {' '.join(tags or [])}"
    return any(k in text for k in CLOSED_MONDAY_KEYWORDS)


def analyze_pace(trip_days: int, node_count: int, intent: str = "") -> Dict[str, Any]:
    """行程节奏判断：rush(赶场) / balanced(适中) / deep(深度漫游)。

    综合用户意图关键词与「日均节点密度」两路信号，
    返回节奏标签、日均节点数与对应的出行建议。
    """
    trip_days = max(1, int(trip_days or 1))
    node_count = max(0, int(node_count or 0))
    avg = node_count / trip_days
    s = str(intent or "")

    rush_hints = ["特种兵", "暴走", "赶场", "打卡", "紧凑", "高效", "多玩", "多逛"]
    deep_hints = ["悠闲", "度假", "慢", "深度", "放松", "慵懒", "休闲", "慢节奏"]

    rush = any(k in s for k in rush_hints)
    deep = any(k in s for k in deep_hints)

    if rush or avg >= 6:
        pace = "rush"
    elif deep or avg <= 3:
        pace = "deep"
    else:
        pace = "balanced"

    meta = {
        "rush": {
            "label": "特种兵赶场型",
            "suggestion": "节点密度较高，建议早出发、精简单点停留，优先地铁/打车接驳，并预留弹性时间以防赶场疲惫。",
        },
        "balanced": {
            "label": "张弛有度型",
            "suggestion": "节奏均衡，建议上午户外、下午文化、晚间美食，每个节点保留 2-3 小时游览，出行从容。",
        },
        "deep": {
            "label": "深度漫游型",
            "suggestion": "节奏偏慢，可在核心景点停留更久、深度体验，减少无效赶路，享受在地氛围。",
        },
    }[pace]

    return {
        "pace": pace,
        "label": meta["label"],
        "suggestion": meta["suggestion"],
        "avg_nodes_per_day": round(avg, 1),
    }


def monday_closure_notes(nodes: List[Dict[str, Any]]) -> List[str]:
    """对路线中「周一闭馆型」节点生成规避提示（若行程覆盖周一，需调整其排期）。"""
    notes: List[str] = []
    for n in nodes or []:
        if detect_monday_closure_risk(
            str(n.get("name", "")), str(n.get("type", "")), n.get("tags")
        ):
            day = n.get("day", "?")
            notes.append(
                f"第 {day} 天【{n.get('name')}】属周一闭馆型场馆，建议避开周一安排行程。"
            )
    return notes