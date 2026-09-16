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
    """景点拥挤度启发式评估：综合口碑热度 + 实时路况 + 是否周末，输出直观人流量分级。

    `percent` 由 `score` 单调映射得出，而不是按分级写死。原实现把 82/58/35 三个
    常数直接绑在分级上，导致 score=123 与 score=88 都对外报告 82% —— 一个用两位
    有效数字呈现、却不随自身输入变化的"精确"数字，比区间估计更容易误导用户。
    现在 percent 与 score 一一对应，并额外给出区间以体现不确定性。
    """
    rating_available = rating not in (None, "", "暂无供应商数据", "暂无评分")
    traffic_available = traffic_status not in (None, "", "unknown", "暂无供应商数据")
    try:
        r = float(rating) if rating_available else 3.0
    except Exception:
        r = 3.0
    try:
        t = int(str(traffic_status)) if traffic_available else 1
    except Exception:
        t = 1

    if not rating_available and not traffic_available:
        return {"level": "unknown", "percent": None, "label": "暂无供应商数据", "score": None, "source": "unavailable", "estimated": True}

    # Rating is on a 0-5 scale; clamp before scaling so an out-of-range supplier
    # value cannot push the score (and therefore the percentage) out of 0-100.
    r = max(0.0, min(5.0, r))
    t = max(1, min(4, t))
    score = max(0.0, min(100.0, r * 15 + (t - 1) * 12 + (12 if is_weekend else 0)))

    percent = int(round(score))
    if score >= 88:
        level, label = "high", "拥挤"
    elif score >= 70:
        level, label = "medium", "适中"
    else:
        level, label = "low", "宽松"

    # Heuristic uncertainty: the inputs are a rating and a coarse traffic code,
    # so an exact figure overstates precision. The band widens when either input
    # is missing.
    margin = 6 if (rating_available and traffic_available) else 15
    return {
        "level": level,
        "percent": percent,
        "percent_range": [max(0, percent - margin), min(100, percent + margin)],
        "label": label,
        "score": round(score, 1),
        "source": "heuristic",
        "estimated": not (rating_available and traffic_available),
    }


# 手选偏好定位 -> 偏好维度提示映射。用户在前端四选一（寻味/视觉/休闲/深度）显式声明，
# 该声明始终生效，与算法画像融合后一并注入 Prompt，覆盖冷启动与 A/B 对照组场景。
ROLE_PREFERENCE_HINTS: Dict[str, str] = {
    "寻味探索": "偏好定位「寻味探索」：优先挖掘当地特色老字号、地道小吃与夜市美食，餐饮节点应占据更高权重。",
    "视觉体验": "偏好定位「视觉体验」：优先安排出片率高、光线与景观俱佳的地标、网红打卡点与夜景机位，兼顾自然与人文。",
    "休闲漫步": "偏好定位「休闲漫步」：节奏放缓、降低节点密度，优先选择公园、街区、茶社类悠闲去处，预留充足休息时间。",
    "深度探索": "偏好定位「深度探索」：行程紧凑、最大化覆盖核心文旅地标、博物馆与历史古迹，知识点密度更高。",
}

# 出行模式与偏好定位的合法枚举（与前端「三模式 / 四选一」严格对齐）。
VALID_MODES = {"solo", "coop", "pvp"}
VALID_ROLES = {"寻味探索", "视觉体验", "休闲漫步", "深度探索"}


def normalize_mode(value: Any) -> str:
    """校验并规整出行模式枚举；非法/缺失值抛出 ValueError，杜绝静默回退到 coop。"""
    mode = str(value or "").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(f"非法出行模式 {value!r}，仅支持 {', '.join(sorted(VALID_MODES))}")
    return mode


def normalize_role(value: Any) -> str:
    """校验并规整偏好定位枚举；非法/缺失值抛出 ValueError，杜绝静默回退到默认角色。"""
    role = str(value or "").strip()
    if role not in VALID_ROLES:
        raise ValueError(f"非法偏好定位 {value!r}，仅支持 {' / '.join(sorted(VALID_ROLES))}")
    return role


# 细粒度偏好标签关键词（与 Go 网关 preferences.go 的 fineTags 严格对齐，跨语言保持同一口径）。
FINE_TAG_KEYWORDS: List[tuple] = [
    ("山岳", ["山", "峰", "岭", "雪山", "冰川", "天池", "峡", "谷", "瀑布"]),
    ("湖海水系", ["湖", "洱海", "北海", "什刹海", "黄河", "秦淮河", "珠江", "漓江", "钱塘江", "海滨", "海滩", "海湾", "海域", "海洋", "河流", "河畔", "河边", "河谷", "运河", "长江", "江河", "江畔", "海南岛", "涠洲岛", "海岛", "岛屿", "小岛", "群岛", "滩", "湾", "湿地", "泉"]),
    ("草原沙漠", ["草原", "沙漠", "戈壁", "绿洲"]),
    ("园林花木", ["公园", "植物园", "园林", "花园", "花海", "竹林", "竹海"]),
    ("博物馆", ["博物", "科技馆", "美术馆", "陈列", "展览"]),
    ("古迹遗址", ["古城", "遗址", "陵", "城墙", "故城", "古都"]),
    ("宗教古迹", ["寺", "庙", "石窟", "清真寺", "祠", "道观", "宫观", "寺观", "白云观", "青羊观", "教堂", "塔"]),
    ("名人故居", ["故居", "书院", "文庙", "纪念馆", "名人"]),
    ("老街坊", ["老街", "古镇", "街区", "胡同", "里弄"]),
    ("火锅串串", ["火锅", "串串", "关东煮"]),
    ("烧烤", ["烧烤", "串烧", "烤"]),
    ("面食", ["面", "拌面", "拉面", "馕", "饼", "包子", "饺子", "饭"]),
    ("地方菜", ["餐厅", "菜馆", "饭馆", "川菜", "粤菜", "湘菜", "本帮菜"]),
    ("小吃夜市", ["小吃", "夜市", "小吃街", "美食街", "摊"]),
    ("甜饮咖啡", ["甜品", "奶茶", "咖啡", "茶饮", "冰淇淋"]),
]


def classify_fine_tags(name: str) -> List[str]:
    """返回地点名命中的细粒度偏好标签（去重、排序），与 Go 网关 fineTagsOf 对齐。"""
    n = str(name or "")
    hits: List[str] = []
    seen: set = set()
    for tag, kws in FINE_TAG_KEYWORDS:
        for kw in kws:
            if kw in n:
                if tag not in seen:
                    seen.add(tag)
                    hits.append(tag)
                break
    return sorted(hits)


def build_profile_fine_tag_boost(profile: Dict[str, Any]) -> Dict[str, float]:
    """A/B treatment 组硬策略：把用户喜欢/不喜欢的细标签映射为 POI 细标签匹配置信加成。

    - liked_tags 命中 +w、disliked_tags 命中 -w；
    - 仅 treatment 组且已有历史反馈时生效，control 组/冷启动返回空 dict（均衡基线）；
    - 与 build_profile_category_boost（粗维度）正交叠加，构成「粗+细」二级粒度的差异化排序。
    """
    if not isinstance(profile, dict):
        return {}
    if str(profile.get("ab_variant") or "control") != "treatment":
        return {}
    if int(profile.get("feedback_count") or 0) <= 0:
        return {}
    boost: Dict[str, float] = {}
    w = 0.6
    for t in (profile.get("liked_tags") or []):
        boost[str(t)] = w
    for t in (profile.get("disliked_tags") or []):
        boost[str(t)] = -w
    return boost


def build_personalization_hint(profile: Dict[str, Any], member_profiles: Dict[str, Any], room_members: List[Any], role: str = "") -> str:
    """融合「用户手选角色 + 算法旅行画像」生成个性化提示词。

    - 手选角色（role / 各成员 role）是用户显式声明，始终生效，覆盖冷启动与 A/B 对照组；
    - 算法画像（profile / member_profiles）来自历史反馈学习，仅在 A/B treatment 组且有反馈时注入；
    - 二者合并为同一条高优先级软约束，实现「声明偏好 + 学习偏好」的画像融合。
    """
    # —— 1. 手选角色偏好（显式声明，始终生效）——
    declared: List[str] = []
    main_role = str(role or "").strip()
    if main_role in ROLE_PREFERENCE_HINTS:
        declared.append(ROLE_PREFERENCE_HINTS[main_role])
    for m in room_members:
        if not isinstance(m, dict):
            continue
        r = str(m.get("role") or "").strip()
        if r in ROLE_PREFERENCE_HINTS:
            declared.append(ROLE_PREFERENCE_HINTS[r])
    declared = list(OrderedDict.fromkeys(declared))

    # —— 2. 算法画像（A/B treatment 组且已有历史反馈时注入）——
    variant = str(profile.get("ab_variant") or "control")
    feedback_count = int(profile.get("feedback_count") or 0)

    learned: List[str] = []
    if variant == "treatment" and feedback_count > 0:
        personal_hint = str(profile.get("prompt_hint") or "").strip()
        if personal_hint:
            learned.append(personal_hint)
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
                learned.append(member_hint)
    learned = list(OrderedDict.fromkeys(learned))

    # —— 3. 融合输出 ——
    if not declared and not learned:
        return "【个性化画像状态】本轮采用通用推荐策略（用户画像尚在积累或处于 A/B 对照组）。"

    parts: List[str] = []
    if declared:
        parts.append("【显式偏好定位（用户手选，始终生效）】\n" + "\n".join(declared))
    if learned:
        parts.append("【算法旅行画像（A/B treatment 组，来自历史反馈）】\n" + "\n".join(learned))
    return "\n".join(parts) + "\n请优先匹配上述偏好维度、消费习惯与兴趣标签，同时兼顾同行成员的诉求平衡。"


def build_profile_category_boost(profile: Dict[str, Any]) -> Dict[str, float]:
    """A/B treatment 组差异化推荐策略：把算法画像的维度比例映射为候选品类加成权重。

    - 仅当 ab_variant=treatment 且已有历史反馈（feedback_count>0）时返回非空加成，
      使 treatment 组候选排序真正偏向其画像偏好（硬策略）；
    - control 组返回空 dict（均衡基线），不施加画像权重；
    - 由此 A/B 实验可通过 CTR/采纳率验证「画像驱动个性化排序」相对通用均衡的增量。

    返回 key 与 classify_poi_category 对齐：scenic / cultural / food。
    """
    if not isinstance(profile, dict):
        return {}
    variant = str(profile.get("ab_variant") or "control")
    feedback_count = int(profile.get("feedback_count") or 0)
    if variant != "treatment" or feedback_count <= 0:
        return {}

    def _ratio(key: str) -> float:
        try:
            return float(profile.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    nature = _ratio("nature_ratio")
    culture = _ratio("culture_ratio")
    food = _ratio("food_ratio")

    total = nature + culture + food
    if total <= 0:
        return {}
    nature /= total
    culture /= total
    food /= total

    # 与均衡基线 1/3 的偏差放大为品类加成（K 控制个性化强度；2.4 与显式诉求信号 +1.0 同量级）
    k = 2.4
    boost = {
        "scenic": round((nature - 1.0 / 3.0) * k, 4),
        "cultural": round((culture - 1.0 / 3.0) * k, 4),
        "food": round((food - 1.0 / 3.0) * k, 4),
    }
    # 仅保留显著偏差，过滤噪声（<0.05 归零）
    return {cat: val for cat, val in boost.items() if abs(val) >= 0.05}


def build_profile_budget_gamma(profile: Dict[str, Any]) -> float:
    """A/B treatment 组差异化硬策略：把历史消费倾向 budget_tendency 映射为 gamma 缩放因子。

    - low（精打细算/中低预算）放大消费惩罚，high（品质高预算）弱化消费惩罚，mid 保持基线；
    - 仅 treatment 组且已有历史反馈（feedback_count>0）时生效；
    - control 组 / 冷启动返回 1.0，不改变消费权重（均衡基线）。
    """
    if not isinstance(profile, dict):
        return 1.0
    variant = str(profile.get("ab_variant") or "control")
    feedback_count = int(profile.get("feedback_count") or 0)
    if variant != "treatment" or feedback_count <= 0:
        return 1.0
    bt = str(profile.get("budget_tendency") or "mid").strip().lower()
    # low→更省钱（惩罚放大），high→更愿花钱（惩罚弱化），mid→基线
    return {"low": 2.0, "mid": 1.0, "high": 0.3}.get(bt, 1.0)
