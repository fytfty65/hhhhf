import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import httpx
import math
import re
import difflib
import hashlib
import asyncio
import datetime
import urllib.parse
import traceback
import uuid
import numpy as np
import networkx as nx
from sklearn.cluster import DBSCAN
from typing import List, Dict, Any, Optional, Tuple, Mapping
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI, BadRequestError
from models.schemas import GatewayMessage, NegotiateResponse
from core.travel_utils import analyze_weather_for_planning, estimate_crowdedness, build_personalization_hint, build_profile_category_boost, build_profile_budget_gamma, build_profile_fine_tag_boost, build_profile_applied, classify_fine_tags, normalize_mode, normalize_role
from core.time_intelligence import analyze_pace, monday_closure_notes
from core.optimization import fairness_gate, fairness_report, pareto_frontier, repair_route, select_fair_route, validate_candidate, validate_route
from core.constraints import resolve_conflicts
from core.contextual_bandit import ContextualThompsonBandit, derive_reward, stable_arm_id
from core.agent_contracts import capability_registry, validate_agent_proposal, proposal_timestamp
from core.simulation import member_utilities_for, simulate_plan
from core import llm_config as llm_cfg  # LLM 调用参数单一来源（模型/端点/temperature/max_tokens）
from core.llm_config import model_name  # LLM 默认值单一来源（曾与 conflict.py 各写一套）
from core.events import new_event  # 统一事件协议信封（correlation_id / causation_id / version）
# 候选池相关工具统一在模块级导入：实测教训（2026-10-04）—— 把它们各自写在函数内部局部导入，
# 结果外层代码用了 `needs_widening` 却只在内层函数里导过 → NameError 让整条推演流中断，
# 而单测全绿（因为没有任何单测覆盖这条长生成器路径）。放在顶层，缺名字在导入时就暴露。
from core.poi_pool import (
    FOOD_WIDENING_KEYWORDS,
    NEIGHBOR_SCENIC_KEYWORDS,
    SCENIC_WIDENING_KEYWORDS,
    amap_error_is_retryable,
    amap_error_note,
    attraction_query_limits,
    dedupe_sub_spots,
    filter_by_radius,
    needs_widening,
    parent_region,
    park_cap,
    pool_supply_report,
    required_food_candidates,
    required_scenic_candidates,
    scenic_radius_km,
    top_up_category_buckets,
    widen_candidates,
)
# 行程配比口径（用户明确的口味）：默认自然=人文各半并穿插美食/住宿；自然景观只算景区
# （城市公园另算一档）；用户明确说了就加权。纯函数在 core/route_composition，便于单测。
from core.route_composition import (
    composition_audit,
    composition_hint as composition_hint_text,
    composition_targets,
    count_route_composition,
    repair_forbidden_categories,
    scenic_tier,
    summarize_targets,
    trim_category_over_cap,
)


def _fairness_limit_from_env() -> float:
    """Read the group fairness standard deviation limit defensively."""
    try:
        return max(0.0, float(os.getenv("PLANNING_FAIRNESS_MAX_STD", "0.15")))
    except (TypeError, ValueError):
        return 0.15


def _simulate_final_plan(
    final_data: Dict[str, Any],
    *,
    room_members: Any = None,
    budget: Any = None,
    weather: Any = None,
) -> Dict[str, Any]:
    """Run the Monte-Carlo digital twin over the finished route.

    Returns an empty dict on any failure: the plan is still valid without its
    uncertainty report, and a simulation problem must never break planning.
    """
    try:
        route = final_data.get("route")
        if not isinstance(route, list) or not route:
            return {}
        condition = ""
        if isinstance(weather, dict):
            condition = str(weather.get("condition") or "")
        budget_value = None
        try:
            if budget is not None and str(budget).strip() != "":
                budget_value = float(budget)
        except (TypeError, ValueError):
            budget_value = None
        result = simulate_plan(
            route,
            members=room_members if isinstance(room_members, list) else None,
            weather_condition=condition,
            budget=budget_value,
        )
        return result.to_dict()
    except Exception as exc:  # 仿真失败不得影响行程下发
        print(f"⚠️ [仿真] 生成不确定度报告失败，已跳过: {exc}", flush=True)
        return {}


def _satisfaction_from_simulation(simulation: Any) -> Dict[str, int]:
    """Convert simulated member satisfaction into the legacy 0-100 display shape.

    The previous value was a hardcoded constant echoed from the prompt template.
    This derives it from each member's modelled utility on the actual route, so
    the number now has a traceable basis. Returns {} when there is nothing to
    compute, letting the caller leave the field absent rather than inventing one.
    """
    if not isinstance(simulation, dict):
        return {}
    members = simulation.get("member_satisfaction")
    if not isinstance(members, dict) or not members:
        return {}
    scores: Dict[str, int] = {}
    for name, stats in members.items():
        if not isinstance(stats, dict):
            continue
        value = stats.get("satisfaction_p50")
        if isinstance(value, (int, float)):
            scores[str(name)] = int(round(max(0.0, min(1.0, float(value))) * 100))
    return scores
from duckduckgo_search import DDGS
import redis.asyncio as redis
from api.risk_service import RiskService, compute_node_risk, detect_risk_changes

router = APIRouter()

client = AsyncOpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
    timeout=httpx.Timeout(180.0, connect=15.0, read=180.0, write=30.0)
)
MODEL_NAME = model_name()  # 单一来源：core/llm_config（曾与 conflict.py 各写一套兜底值）

# LLM 调用重试次数（网络抖动/限流时自动重试）
LLM_MAX_RETRIES = 2
# 心跳间隔：流式请求过程中如果长时间未收到 token，则给前端推送心跳日志
HEARTBEAT_INTERVAL_SEC = 8

# 长途（≥14 天）首轮分段生成的**总时间预算**（秒）：超预算的段不再调用 LLM，
# 如实记为"这一段没生成出来"，由前端核对面板提示，而不是让用户无限等。
LONG_TRIP_GENERATION_SECONDS = 240.0
# 候选池取数超时（秒）：复核/治理共用一份池子，取不到就如实报"要数据"，不拖住出方案
POOL_FETCH_TIMEOUT_SECONDS = 12.0

# Production bandit is opt-in. Keeping the singleton at module scope preserves
# posterior state across requests while the disabled path is a no-op.
def _bandit_enabled() -> bool:
    return os.getenv("AI_BANDIT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


planning_bandit = ContextualThompsonBandit(
    enabled=_bandit_enabled(),
    exploration_budget=float(os.getenv("AI_BANDIT_EXPLORATION_BUDGET", "0.10")),
)
# Keep two stable policy arms available by default. The flag remains opt-in;
# registering arms does not change behavior while AI_BANDIT_ENABLED is false.
for _arm in filter(None, (item.strip() for item in os.getenv("AI_BANDIT_ARMS", "builtin-joint-v1:balanced,builtin-joint-v1:relaxed").split(","))):
    planning_bandit.register(_arm)
_bandit_state_file = os.getenv("AI_BANDIT_STATE_FILE", "").strip()
if _bandit_state_file:
    if planning_bandit.load(_bandit_state_file):
        print(f"♻️ [Bandit] 已从 {_bandit_state_file} 恢复学习状态", flush=True)
    else:
        # The state file does not exist yet. Arm the path and write an initial
        # snapshot, otherwise `record()` has no path to save to and every learned
        # posterior is lost on restart — which is what made the learning loop
        # look like it worked while accumulating nothing durable.
        try:
            planning_bandit.save(_bandit_state_file)
            planning_bandit.arm_state_file(_bandit_state_file)
            print(f"🆕 [Bandit] 初始化学习状态文件: {_bandit_state_file}", flush=True)
        except OSError as _bandit_state_err:
            print(f"⚠️ [Bandit] 学习状态文件不可写，学习结果将不会持久化: {_bandit_state_err}", flush=True)


# ==========================================
# 实时数据缓存层：降低 API 调用成本、提高响应速度（TTL=1h，满足更新频率>=每小时1次）
# ==========================================
import time as _time

_api_cache: Dict[str, tuple] = {}

def _cache_get(key: str):
    item = _api_cache.get(key)
    if not item:
        return None
    expire_ts, val = item
    if _time.time() > expire_ts:
        _api_cache.pop(key, None)
        return None
    return val

def _cache_set(key: str, val, ttl: int = 3600):
    _api_cache[key] = (_time.time() + ttl, val)


# ==========================================
# 👑 辅助工具：中文与多格式游玩天数解析器
# ==========================================
def parse_chinese_days(text: str) -> int:
    """
    智能解析用户输入中的游玩天数，支持 '3天', '三天', '五日游', '两天一夜', '七天大环线' 等表述
    """
    if not text:
        return 3
        
    cn_num_map = {
        '一': 1, '二': 2, '两': 2, '仨': 3, '三': 3, '四': 4, '五': 5, 
        '六': 6, '七': 7, '八': 8, '九': 9, '十': 10, '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15
    }
    
    # 优先匹配阿拉伯数字：如 "3天", "5日", "7天6晚"
    match_digit = re.search(r'(\d+)\s*[天日]', text)
    if match_digit:
        val = int(match_digit.group(1))
        return max(1, min(15, val))
        
    # 匹配中文连词复合表达：如 "两天一夜" -> 2天, "三天两晚" -> 3天
    match_compound = re.search(r'([一二两仨三四五六七八九十\d]+)\s*天\s*[一二两仨三四五六七八九十\d]+\s*[晚夜]', text)
    if match_compound:
        val_str = match_compound.group(1)
        if val_str.isdigit():
            return max(1, min(15, int(val_str)))
        return cn_num_map.get(val_str, 3)
        
    # 匹配中文单个数字：如 "三天", "五天", "两日游"
    match_cn = re.search(r'([一二两仨三四五六七八九十]+)\s*[天日]', text)
    if match_cn:
        cn_char = match_cn.group(1)
        return cn_num_map.get(cn_char, 3)
        
    return 3


def parse_budget_amount(text: str, trip_days: int = 1, member_count: int = 1) -> int:
    """
    从用户诉求文本中解析预算金额，返回「总预算」数值（元），无法解析时返回 0。
    支持："预算5000"、"总预算1万"、"人均300"、"每天600"、"8000元"、"1.5万" 等表述。
    """
    if not text:
        return 0
    days = max(trip_days, 1)
    members = max(member_count, 1)

    def _to_number(num_str: str, unit: str = "") -> float:
        try:
            val = float(num_str)
        except (TypeError, ValueError):
            return 0.0
        if "万" in (unit or ""):
            val *= 10000
        elif "千" in (unit or ""):
            val *= 1000
        return val

    # 1) 总预算/预算 明确表述（优先级最高）
    m = re.search(r'(?:总预算|整体预算|预算|花销)\D{0,4}(\d+(?:\.\d+)?)\s*(万|千|元|块)?', text)
    if m:
        val = _to_number(m.group(1), m.group(2))
        if val >= 100:
            return int(val)

    # 2) 人均表述 -> 人均 × 人数 × 天数
    m = re.search(r'人均\D{0,4}(\d+(?:\.\d+)?)\s*(万|千|元|块)?', text)
    if m:
        per = _to_number(m.group(1), m.group(2))
        if per >= 10:
            return int(per * members * days)

    # 3) 每天/每日/一天 表述 -> 日均 × 天数
    m = re.search(r'(?:每天|每日|一天|日均)\D{0,4}(\d+(?:\.\d+)?)\s*(万|千|元|块)?', text)
    if m:
        per = _to_number(m.group(1), m.group(2))
        if per >= 10:
            return int(per * days)

    # 4) 裸金额带「万」：如 "1万"、"1.5万"
    m = re.search(r'(\d+(?:\.\d+)?)\s*万', text)
    if m:
        return int(_to_number(m.group(1), "万"))

    # 5) 裸金额带「元/块」：如 "5000元"、"800块"
    m = re.search(r'(\d{3,})\s*(?:元|块)', text)
    if m:
        return int(float(m.group(1)))

    return 0


# ==========================================
# 👑 文旅偏好信号与 POI 分类：驱动偏好权重、每日均衡排布与覆盖校验
# ==========================================
_HOTEL_NAME_KEYWORDS = ["酒店", "度假村", "民宿", "客栈", "宾馆", "公寓", "青旅", "驿站", "大酒店", "国宾馆"]
_SCENIC_KW = ["山", "湖", "公园", "园林", "风景", "峡谷", "瀑布", "温泉", "海滩", "海岛", "草原", "森林", "湿地", "花海", "溶洞", "雪山", "冰川", "丹霞", "沙漠", "绿洲", "植物园", "动物园", "海洋馆", "栈道", "索道"]
_CULTURAL_KW = ["博物馆", "博物院", "展馆", "展览馆", "纪念馆", "故居", "古城", "古镇", "古村", "遗址", "文庙", "书院", "寺庙", "道观", "清真寺", "教堂", "佛塔", "城墙", "宫殿", "陵墓", "碑林", "老街", "胡同", "美术馆", "科技馆", "天文馆", "图书馆", "大剧院", "音乐厅", "艺术中心", "非遗", "民俗", "文化园", "创意园"]
_FOOD_KW = ["美食", "餐厅", "酒楼", "食府", "饭庄", "菜馆", "小吃", "夜市", "老字号", "私房菜", "土菜", "农家菜", "海鲜", "早茶", "茶餐厅", "火锅", "烧烤", "面馆", "包子", "烤鸭", "米线", "羊肉", "牛肉", "拉面", "抓饭", "拌面", "大盘鸡", "馕", "三套车"]
_SHOPPING_KW = ["步行街", "商业街", "商场", "购物中心", "百货", "商圈", "市场", "太古里", "IFS", "万象城", "大悦城", "万达", "银泰", "SKP", "奥莱", "outlets", "集市", "古玩城", "文创市集"]


def classify_poi_category(name: str, poi_type: str = "", is_hotel: bool = False) -> str:
    """将 POI 归一为 scenic/cultural/food/shopping/hotel/other，供权重与均衡排布复用。"""
    if is_hotel:
        return "hotel"
    n = str(name or "").lower()
    t = str(poi_type or "").lower()
    # 👑 餐饮 / 住宿 / 购物：**类别说了算**，名字里的"楼/馆/城"等字样不改变类别。
    # 实测事故（2026-10-04 真实推演）：餐厅「西域楼兰焰一店」因为名字带"楼"被判成人文 →
    # ① 在"排除人文"时被当成博物馆候选剔除；② 配比对账里被算成人文节点；
    # ③ 提案分歧里报出一条根本不存在的"有人还提了你不要的类别"。
    # 而"名字里的文化词优先于泛自然词"这条既有修正（针对天山博物院/湖畔展馆）必须保留 ——
    # 它们的类别是科教文化/风景名胜，不落在这三个前缀里，所以互不影响。
    _type_text = t
    if _type_text.startswith("10") or any(k in _type_text for k in ["住宿", "酒店", "宾馆", "民宿"]):
        return "hotel"
    if _type_text.startswith("05") or any(k in _type_text for k in ["餐饮", "美食", "餐厅", "小吃"]):
        return "food"
    if _type_text.startswith("06") or any(k in _type_text for k in ["购物", "商场", "商业街", "商圈"]):
        return "shopping"
    # 名称中的明确文化场馆词优先于“山/湖/公园”等泛自然词，
    # 避免“天山博物院”“湖畔展馆”被误分到 scenic，导致类别门禁误判。
    if any(k in n for k in _CULTURAL_KW) or any(k in t for k in ["博物馆", "博物院", "展馆", "展览馆", "科教", "文化"]):
        return "cultural"
    # 高德 type 码直接映射（优先于名称）：用大类前缀覆盖全部子类（05=餐饮，10=住宿，11=风景，14=科教文化，06=购物）
    if t.startswith("10") or any(k in t for k in ["住宿", "酒店", "宾馆", "民宿"]):
        return "hotel"
    if t.startswith("11") or any(k in t for k in ["风景名胜", "风景", "公园", "景点", "景区"]):
        return "scenic"
    if t.startswith("14") or any(k in t for k in ["博物馆", "博物院", "展馆", "展览馆", "科教", "文化"]):
        return "cultural"
    if t.startswith("05") or any(k in t for k in ["餐饮", "美食", "餐厅", "小吃"]):
        return "food"
    if t.startswith("06") or any(k in t for k in ["购物", "商场", "商业街", "商圈"]):
        return "shopping"
    # 名称关键词：「酒店」优先于风景/文化，避免「湖畔酒店」被误判为自然风景
    for kw in _HOTEL_NAME_KEYWORDS:
        if kw in n:
            return "hotel"
    for kw in _SCENIC_KW:
        if kw in n:
            return "scenic"
    for kw in _CULTURAL_KW:
        if kw in n:
            return "cultural"
    for kw in _FOOD_KW:
        if kw in n:
            return "food"
    for kw in _SHOPPING_KW:
        if kw in n:
            return "shopping"
    return "other"


def extract_preference_signals(text: str) -> Dict[str, Any]:
    """从用户诉求文本中抽取住宿/餐饮/风景/文化/购物偏好信号，供权重与校验使用。"""
    t = str(text or "").lower()

    def positive_mention(words: List[str]) -> bool:
        """忽略“不想去/少安排”等否定上下文，避免把排除项当成偏好项。"""
        for word in words:
            for match in re.finditer(re.escape(word), t):
                prefix = t[max(0, match.start() - 10):match.start()]
                if re.search(r"(?:不想|不要|不去|别|少|减少|去掉|换掉|排除|不需要)", prefix):
                    continue
                return True
        return False

    cultural_words = ["博物馆", "博物院", "展馆", "展览馆", "纪念馆", "美术馆", "历史", "古迹", "文化", "人文", "人文景观"]
    # 排除人文的三种说法（用户口径 2026-10-04：说"只想看自然"时人文就该让位）：
    #   ① 明确否定："不要博物馆/少安排人文"；
    #   ② 只谈自然："只想看自然景观""专门来爬山的"——且整句里根本没提人文/文化；
    #   ③ 老口径：否定词 + 文化类词。
    excludes_cultural = bool(
        re.search(r"(?:不想|不要|不去|别|少|减少|去掉|换掉|排除|不需要)[^。；，,!?！?]{0,10}(?:博物馆|博物院|展馆|展览馆|纪念馆|美术馆|文化|人文)", t)
    ) or bool(
        re.search(r"(?:不想|不要|不去|少安排|不用安排|别安排|别去|不看)[^。；，,!?！?]{0,6}(?:人文|博物馆|文化|古迹)", t)
    ) or bool(
        # 语序反过来："人文少安排""博物馆就不用去了"。这里必须列具体词，
        # 不能写成"文化+不"，否则"人文不少"会被误判成排除。
        re.search(r"(?:人文|文化|博物馆|古迹|历史)[^。；，,!?！?]{0,3}(?:少安排|少一点|少去|少逛|不用|不要|不去|免了|砍掉|去掉|别安排|别去)", t)
    )
    mentions_culture = any(word in t for word in ["人文", "文化", "博物馆", "博物院", "古迹", "历史"])
    nature_focus = bool(
        re.search(r"(?:只想|只看|只要|专门|就想|就是为了|全程)[^。；，,!?！?]{0,6}(?:自然|风景|景区|户外|雪山|湖|草原|沙漠|峡谷|胡杨)", t)
    )
    if nature_focus and not mentions_culture:
        excludes_cultural = True

    # 距离容忍度：用户说"不考虑距离"时，规划形态应该变成"在更大范围里找景区最优路径"，
    # 而不是继续在市区打转（实测：市区检索只有 ≤5km 的公园，真景区在 47-76km 外）。
    if any(k in t for k in ["不考虑距离", "不介意距离", "不嫌远", "多远都行", "多远都可以", "可以跑远", "跑远一点", "距离不是问题", "不介意远", "愿意跑远", "远一点也没关系", "不在乎距离"]):
        distance_tolerance = "high"
    elif any(k in t for k in ["不想跑远", "别太远", "不要太远", "不想走远", "就近", "附近玩", "近距离"]):
        distance_tolerance = "low"
    else:
        distance_tolerance = "normal"

    signals: Dict[str, Any] = {
        "wants_hotel": False, "wants_food": False, "wants_scenic": False,
        "wants_cultural": False, "wants_shopping": False, "luxury_hotel": False,
        "exclude_cultural": excludes_cultural,
        "distance_tolerance": distance_tolerance,
        "requirements": [],
    }
    if any(k in t for k in ["酒店", "住宿", "民宿", "度假村", "客栈", "宾馆", "入住", "五星", "豪华"]):
        signals["wants_hotel"] = True
        signals["requirements"].append("住宿/酒店")
    if any(k in t for k in ["高档", "豪华", "五星", "奢华", "品质", "度假", "高端"]):
        signals["luxury_hotel"] = True
    if any(k in t for k in ["美食", "吃", "餐厅", "特色菜", "小吃", "老字号", "夜市", "火锅", "口味", "本地菜"]) or "餐" in t:
        signals["wants_food"] = True
        signals["requirements"].append("餐饮/美食")
    # 自然景观：只认**多字短语**，不收"景观""自然"这种宽词 ——
    # 否则"人文景观"会被判成想看自然（用户的抱怨原话里就有这个词）。
    scenic_phrases = [
        "风景", "自然风光", "自然风景", "自然景观", "自然保护区", "地质公园", "湿地公园",
        "森林公园", "山水", "公园", "景点", "景区", "户外", "峡谷", "草原", "温泉", "园林",
        "爬山", "徒步", "雪山", "海岛", "湖边", "看海", "湖泊", "森林", "湿地", "沙漠",
        "戈壁", "观景", "绿洲",
    ]
    if any(k in t for k in scenic_phrases):
        # 实测事故（2026-10-04）：用户原话是"我想看更多的自然景观"，而词表里只有
        # "自然风光/风景"，于是 wants_scenic=False —— 诉求被丢掉，行程排成了人文景点。
        signals["wants_scenic"] = True
        signals["requirements"].append("自然风景")
    if positive_mention(cultural_words) and not excludes_cultural:
        signals["wants_cultural"] = True
        signals["requirements"].append("文化历史")
    if any(k in t for k in ["购物", "商场", "商圈", "商业街", "逛街"]):
        signals["wants_shopping"] = True
        signals["requirements"].append("购物商圈")
    # 城市公园：默认限量（1~2 个），用户**明确想逛公园/乐园**时放宽 ——
    # "喜欢的话后续再添加"这条产品口径就落在这个信号上。
    park_words = ["公园", "游乐园", "动物园", "植物园", "乐园", "园林"]
    if any(k in t for k in park_words) and positive_mention(park_words):
        signals["wants_park"] = True
        signals["requirements"].append("城市公园/乐园")
    if distance_tolerance == "high":
        signals["requirements"].append("不介意跑远（跨景区）")
    elif distance_tolerance == "low":
        signals["requirements"].append("尽量就近")
    if excludes_cultural:
        signals["requirements"] = [item for item in signals["requirements"] if item != "文化历史"]
    if not signals["requirements"]:
        signals["requirements"] = ["常规观光、特色美食与舒适住宿"]
    return signals


def validate_preference_coverage(routes: List[Dict], signals: Dict[str, Any]) -> Dict[str, Any]:
    """校验用户输入偏好是否都在路线中被覆盖，返回结构化结果。"""
    checks: List[Dict[str, Any]] = []
    cats: set = set()
    for r in (routes or []):
        if isinstance(r, dict):
            cats.add(classify_poi_category(
                str(r.get("name") or r.get("location") or ""),
                str(r.get("type") or ""),
                bool(r.get("is_hotel") or (isinstance(r.get("tags"), list) and "住宿" in r.get("tags", []))),
            ))
    mapping = [
        ("wants_hotel", "hotel", "住宿/酒店"),
        ("wants_food", "food", "餐饮/美食"),
        ("wants_scenic", "scenic", "自然风景"),
        ("wants_cultural", "cultural", "文化历史"),
        ("wants_shopping", "shopping", "购物商圈"),
    ]
    for flag_key, cat, label in mapping:
        if signals.get(flag_key):
            checks.append({"requirement": label, "addressed": cat in cats})
    all_ok = all(c["addressed"] for c in checks) if checks else True
    return {
        "requirements": list(signals.get("requirements") or []),
        "checks": checks,
        "all_addressed": all_ok,
    }


# ==========================================
# 👑 容错 JSON 修复与解析器：彻底解决 JSON 语法崩溃与字符串内部非安全字符报错
# ==========================================
def safe_repair_and_parse_json(json_str: str) -> dict:
    if not json_str:
        return {}

    clean_str = str(json_str).strip()
    clean_str = re.sub(r'```json', '', clean_str)
    clean_str = re.sub(r'```', '', clean_str).strip()

    try:
        return json.loads(clean_str)
    except Exception:
        pass

    # 正则修复：清理 JSON 数组/对象末尾多余的逗号
    clean_str = re.sub(r',\s*([\}\]])', r'\1', clean_str)
    # 替换未转义的普通换行符
    clean_str = clean_str.replace('\n', '\\n').replace('\r', '')

    try:
        return json.loads(clean_str)
    except Exception:
        last_brace = clean_str.rfind("}")
        if last_brace != -1:
            try:
                return json.loads(clean_str[:last_brace+1])
            except Exception:
                pass
    return {}


# 风险计算逻辑（compute_risk_dimensions / compute_node_risk / detect_risk_changes）
# 已抽离至 api/risk_service.py，本模块通过顶部 import 引用（RiskService / compute_node_risk / detect_risk_changes）。


# 风控层 WorldMonitorClient 已抽离至 api/risk_service.py（RiskService 提供等价的
# get_city_safety_intel / aggregate_city_risk / build_snapshot 方法）。


# ==========================================
# 1. 架构层：正统的 Redis 分布式共享黑板系统
# ==========================================
class BlackboardSystem:
    def __init__(self):
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")

    async def write(self, key: str, data: dict):
        try:
            r = redis.from_url(self.redis_url, decode_responses=True)
            await r.set(str(key), json.dumps(data, ensure_ascii=False, default=str), ex=3600)
            await r.aclose()
            print(f"✅ [黑板系统] 成功将数据写入 Redis: {key}")
        except Exception as e:
            print(f"⚠️ [黑板系统] Redis 写入跳过 (不影响主推演流程): {e}")

    async def record_reasoning(self, room_key: str, step: dict):
        """将一条智能体推理步骤追加到 Redis 列表（用于博弈过程可视化回放）。
        步骤结构：{agent, action(claim|compromise|decision), claim, basis, ts}。
        Redis 不可用时静默跳过，不影响主推演流程。
        """
        try:
            r = redis.from_url(self.redis_url, decode_responses=True)
            await r.rpush(f"reasoning:{room_key}", json.dumps(step, ensure_ascii=False, default=str))
            await r.expire(f"reasoning:{room_key}", 3600)
            await r.aclose()
        except Exception as e:
            print(f"⚠️ [黑板系统] 推理步骤写入跳过 (不影响主推演流程): {e}")


# ==========================================
# 1.5 事件流层：Redis Stream 实时事件总线（P6 个性化与实时事件流）
# ==========================================
class RedisEventStream:
    """基于 Redis Stream 的实时事件总线：emit 发布事件、consume 阻塞订阅消费。

    - emit：XADD 写入 Stream（MAXLEN 近似裁剪，防止无限膨胀）
    - consume：XREAD BLOCK 阻塞拉取新事件，供前端/网关实时订阅
    Redis 不可用时静默跳过（与 BlackboardSystem / 语义缓存保持一致的降级策略）。
    """

    def __init__(self):
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        # 事件流会被订阅端点长时间挂在后台，所以这里：
        #  1) 复用同一个 Redis 客户端（原来每轮循环都 redis.from_url 新建一次）；
        #  2) 记录上次报错时间做**日志节流**（否则 Redis 挂掉时日志会刷爆）。
        self._client = None
        self._last_error_log = 0.0

    async def emit(
        self,
        stream: str,
        event_type: str,
        payload: dict,
        room_id: str = "",
        max_len: int = 1000,
        *,
        correlation_id: str = "",
        causation_id: str = "",
        producer: str = "coordinator",
        target: str = "coordinator",
        evidence: list | None = None,
        confidence: float | None = None,
        event_id: str = "",
    ):
        """发布一条事件到 Redis Stream。

        优先使用**统一协议信封**（`core/events.py`）：带 `event_id / correlation_id /
        causation_id / event_type / producer / target / version / occurred_at / evidence / confidence`，
        否则事件无法回答"属于哪次请求""谁触发了谁"，重放与审计都无从谈起。

        ⚠️ 迁移期兼容：未登记的 event_type 或缺少 correlation_id 时**退回旧信封**
        （`{type, room_id, payload, ts}`）并打一条告警 —— 不允许因为协议校验把既有链路打断。
        新信封同时保留 `type/room_id/payload/ts` 镜像字段，所以网关 WS 桥与前端无需同时改。
        """
        event = None
        if correlation_id:
            try:
                event = new_event(
                    event_type,
                    payload or {},
                    correlation_id=correlation_id,
                    producer=producer,
                    target=target,
                    causation_id=causation_id or None,
                    room_id=room_id,
                    evidence=evidence,
                    confidence=confidence,
                    event_id=event_id or None,
                )
            except Exception as exc:  # 协议未通过：退回旧信封，绝不因此丢事件
                print(f"⚠️ [事件流] 协议校验未通过，退回旧信封: {exc}")
        if event is None:
            event = {
                "type": event_type,
                "room_id": room_id or "",
                "payload": payload or {},
                "ts": int(datetime.datetime.now().timestamp() * 1000),
            }
        try:
            r = redis.from_url(self.redis_url, decode_responses=True)
            await r.xadd(
                str(stream),
                {"data": json.dumps(event, ensure_ascii=False, default=str)},
                maxlen=max_len, approximate=True,
            )
            await r.aclose()
        except Exception as e:
            print(f"⚠️ [事件流] Redis emit 跳过 (不影响主推演流程): {e}")

    async def consume(self, stream: str, last_id: str = "$", count: int = 50, block_ms: int = 5000):
        """阻塞消费 Stream 新事件，返回 [(event_id, event_dict), ...]。

        last_id 传 "$" 表示仅订阅新消息；block_ms 为阻塞超时毫秒（0 表示永不超时）。

        ⚠️ 异常时**必须等一会儿再返回空**：订阅端是 `while True` + 依赖本方法的 block 来
        "睡 5 秒"。如果这里立刻返回空，那个循环会退化成毫秒级空转，把一整个核心烧满
        —— 实测 2026-10-03：ai-service 空转 87.6% 单核、开机 25 小时累计 22 小时 CPU、
        而 8000 端口一个客户端连接都没有（原因就是 Redis 侧的异常被这里立即 return 掉）。
        """
        try:
            if self._client is None:
                self._client = redis.from_url(self.redis_url, decode_responses=True)
            resp = await self._client.xread({str(stream): last_id}, count=count, block=block_ms)
        except Exception as e:
            self._client = None  # 连接可能已损坏，下一轮重建
            now = _time.monotonic()
            if now - self._last_error_log > 10:  # 日志节流：10 秒最多一条
                print(f"⚠️ [事件流] Redis consume 失败，退避后重试（不影响主推演流程）: {e}")
                self._last_error_log = now
            await asyncio.sleep(max(0.5, min(5.0, (block_ms or 5000) / 1000.0)))
            return []

        events = []
        for _stream, entries in (resp or []):
            for _event_id, fields in entries:
                raw = (fields or {}).get("data")
                if not raw:
                    continue
                try:
                    events.append((_event_id, json.loads(raw)))
                except Exception:
                    continue
        return events


# 全局事件总线实例：推演流程（emit）与订阅端点（consume）共用
event_bus = RedisEventStream()

# 统一事件流 Stream 名：所有事件写入单一全局流，靠事件信封内的 room_id 路由
OMNI_EVENTS_STREAM = "omni:events:global"


def build_reasoning_step(agent: str, action: str, claim: str, basis: str) -> dict:
    """构造一条结构化推理步骤（供前端博弈过程可视化回放 + Redis 存档）。
    action: claim（主张）/ compromise（妥协）/ decision（决策）。
    """
    return {
        "agent": agent,
        "action": action,
        "claim": claim,
        "basis": basis,
        "ts": datetime.datetime.now().strftime("%H:%M:%S"),
    }


def audit_route_agent_proposal(route: list[dict], *, agent_id: str = "route-planner") -> dict:
    """Run the generated route through the coordinator-facing proposal gate.

    This audit is non-blocking during migration: invalid or unsourced output is
    marked degraded and remains visible to the coordinator/UI, but it cannot be
    mistaken for a fully evidenced specialist proposal.
    """
    evidence = []
    for node in route if isinstance(route, list) else []:
        if not isinstance(node, dict):
            continue
        sources = node.get("data_sources") or {}
        if not isinstance(sources, dict):
            continue
        for field, provider in sources.items():
            provider_name = provider.get("provider") if isinstance(provider, dict) else provider
            if provider_name in (None, "", "unavailable", "estimated"):
                continue
            evidence.append({"field": str(field), "provider": str(provider_name), "observed_at": proposal_timestamp(), "estimated": bool(node.get("estimated", False))})
    payload = {
        "agent_id": agent_id,
        "domain": "itinerary",
        "status": "ok" if route and evidence else "degraded",
        "degraded": not bool(route and evidence),
        "candidates": [{"name": node.get("name") or node.get("location")} for node in route[:50] if isinstance(node, dict)],
        "hard_constraint_results": {"route_present": bool(route)},
        "evidence": evidence[:200],
        "confidence": 0.75 if evidence else 0.0,
        "failure": None if route and evidence else {"code": "evidence_missing", "message": "路线存在但缺少可核验来源", "retryable": False, "fallback": "supplier_refresh"},
    }
    try:
        proposal = validate_agent_proposal(payload)
        return {"agent_id": proposal.agent_id, "status": proposal.status, "evidence_count": len(proposal.evidence), "candidate_count": len(proposal.candidates), "degraded": proposal.degraded}
    except Exception as error:
        return {"agent_id": agent_id, "status": "failed", "evidence_count": len(evidence), "candidate_count": len(payload["candidates"]), "degraded": True, "error": str(error)}


# ==========================================
# 2. 算法层：文旅 POI 硬核清洗过滤器（彻底杜绝油漆/手机/建材等非旅游门店）
# ==========================================
NON_TOURIST_BLACKLIST = [
    "涂料", "油漆", "建材", "专卖店", "体验店", "手机", "数码", "五金", "营业厅", 
    "汽修", "药房", "大药房", "门诊", "卫浴", "门窗", "窗帘", "家电", "家装", 
    "二手车", "驾校", "物流", "批发", "劳保", "电焊", "钢材", "板材", "石材",
    "通信", "移动", "联通", "电信", "电脑", "维修", "管业", "照明", "灯饰",
    "vivo", "oppo", "华为", "小米之家", "荣耀", "苹果", "三棵树", "立邦", "多乐士",
    "汽车", "电动车", "电池", "百货批发", "建材市场", "装饰", "型材", "门市部", "配件",
    "机电", "机械", "钢构", "化工", "印刷", "包装", "饲料", "兽药", "农资"
]

def deduplicate_and_diversify_pois(pois: List[Dict]) -> List[Dict]:
    if not isinstance(pois, list):
        return []

    seen_names = set()
    seen_categories = set()
    seen_root_names = set()
    # 👑 类型多样性追踪：确保风景区/文化/美食三类都有充足候选
    type_buckets = {"scenic": [], "cultural": [], "food": [], "shopping": [], "other": []}
    result = []

    # 景区关键词（自然风景/公园/山/湖/河/园林等）
    scenic_keywords = [
        "山", "湖", "公园", "园林", "风景", "峡谷", "瀑布", "温泉", "海滩", "海岛",
        "草原", "森林", "湿地", "花海", "溶洞", "天池", "雪山", "冰川", "丹霞",
        "石窟", "沙漠", "绿洲", "河谷", "草原", "海岸", "礁石", "栈道", "索道",
        "漂流", "竹海", "峰林", "梯田", "古树", "植物园", "动物园", "海洋馆"
    ]
    # 文化关键词（博物馆/古迹/寺庙/历史街区等）
    cultural_keywords = [
        "博物馆", "展览馆", "纪念馆", "故居", "古城", "古镇", "古村", "遗址",
        "文庙", "书院", "寺庙", "道观", "清真寺", "教堂", "佛塔", "石窟寺",
        "城墙", "宫殿", "陵墓", "碑林", "老街", "胡同", "里弄", "碉楼",
        "美术馆", "科技馆", "天文馆", "图书馆", "大剧院", "音乐厅", "艺术中心",
        "非遗", "民俗", "文化宫", "文化馆", "文化园", "创意园"
    ]
    food_category_keywords = [
        "壮馍", "羊汤", "牛肉汤", "胡辣汤", "凉皮", "肉夹馍", "火锅", 
        "烧烤", "面馆", "烩面", "包子", "汉堡", "炸鸡", "烤鸭", "米线", "螺蛳粉", 
        "烤包子", "手抓肉", "抓饭", "拌面", "大盘鸡", "馕", "羊肉串", "三套车", "行面",
        "黄焖羊肉", "牛肉拉面", "炒米粉", "丸子汤", "油塔子", "粉汤",
        "餐厅", "酒楼", "食府", "饭庄", "菜馆", "小吃", "夜市", "美食",
        "老字号", "私房菜", "土菜", "农家菜", "海鲜", "早茶", "茶餐厅"
    ]
    shopping_keywords = [
        "步行街", "商业街", "商场", "购物中心", "百货", "商圈", "市场",
        "太古里", "IFS", "万象城", "大悦城", "万达", "银泰", "SKP",
        "奥莱", "outlets", "集市", "夜市街", "文创市集", "古玩城"
    ]

    suffix_clean_list = [
        "夜市", "美食街", "酒楼", "餐馆", "分店", "总店", "景区", 
        "公园-入口", "停车场", "步行街", "小吃街", "遗址公园", "创意园", "文化园", 
        "(艺术学院店)", "(总店)", "店", "(旗舰店)", "(专营店)", "(分店)"
    ]

    def _classify_poi_type(name: str, poi_type: str) -> str:
        """根据名称和类型分类 POI"""
        name_lower = name.lower()
        poi_type_str = str(poi_type or "").lower()
        # 高德 type 码直接映射
        if any(t in poi_type_str for t in ["110000", "风景名胜", "公园", "景点"]):
            return "scenic"
        if any(t in poi_type_str for t in ["141200", "博物馆", "展览馆"]):
            return "cultural"
        if any(t in poi_type_str for t in ["050000", "餐饮"]):
            return "food"
        if any(t in poi_type_str for t in ["060400", "060100", "购物", "商场"]):
            return "shopping"
        # 名称关键词匹配
        for kw in scenic_keywords:
            if kw in name:
                return "scenic"
        for kw in cultural_keywords:
            if kw in name:
                return "cultural"
        for kw in food_category_keywords:
            if kw in name:
                return "food"
        for kw in shopping_keywords:
            if kw in name:
                return "shopping"
        return "other"

    for poi in pois:
        if not isinstance(poi, dict):
            continue
        name = str(poi.get("name") or "").strip()
        if not name or name in seen_names:
            continue

        # 👑 严格负向过滤：剔除三棵树、vivo、建材、油漆、手机专卖店
        name_lower = name.lower()
        if any(bad.lower() in name_lower for bad in NON_TOURIST_BLACKLIST):
            continue

        root_name = name
        for sfx in suffix_clean_list:
            if root_name.endswith(sfx) and len(root_name) > len(sfx) + 1:
                root_name = root_name[:-len(sfx)].strip()

        if root_name in seen_root_names:
            continue

        is_dup_category = False
        for kw in food_category_keywords:
            if kw in name:
                if kw in seen_categories:
                    is_dup_category = True
                    break
                else:
                    seen_categories.add(kw)

        if not is_dup_category:
            seen_names.add(name)
            seen_root_names.add(root_name)
            # 👑 分类到对应桶中
            bucket = _classify_poi_type(name, str(poi.get("type") or ""))
            type_buckets[bucket].append(poi)
            result.append(poi)

    # 👑 类型多样性重排：交错排列确保风景区/文化/美食/购物交替出现
    # 这样 LLM 在按顺序遍历候选池时自然做到类型穿插
    diversified = []
    buckets = [
        ("scenic", type_buckets["scenic"]),
        ("cultural", type_buckets["cultural"]),
        ("food", type_buckets["food"]),
        ("shopping", type_buckets["shopping"]),
        ("other", type_buckets["other"]),
    ]
    max_len = max(len(b) for _, b in buckets)
    for i in range(max_len):
        for _, bucket in buckets:
            if i < len(bucket):
                diversified.append(bucket[i])
    # 追加剩余未取完的
    for _, bucket in buckets:
        if len(bucket) > max_len:
            diversified.extend(bucket[max_len:])

    return diversified


# ==========================================
# 3. 算法层：纳什均衡与帕累托最优
# ==========================================
class NashEquilibriumSolver:
    @staticmethod
    def _numeric(value: Any) -> float:
        try:
            match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
            return float(match.group()) if match else 0.0
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def resolve_conflicts(pois: List[Dict]) -> Dict[str, Any]:
        """Return a real Pareto audit instead of a threshold-filtered list.

        Candidate fitness remains one objective for compatibility, while cost,
        risk and distance are kept explicit so the caller can inspect why a
        candidate was retained or dominated.
        """
        if not isinstance(pois, list):
            return {"frontier": [], "rejected": [], "objectives": []}
        valid = []
        rejected_invalid = []
        for index, candidate in enumerate(pois):
            if not isinstance(candidate, dict):
                rejected_invalid.append({"candidate": "", "index": index, "reason": "candidate_not_object"})
                continue
            validation = validate_candidate(candidate)
            if validation.get("valid"):
                valid.append(candidate)
            else:
                rejected_invalid.append({"candidate": str(candidate.get("name") or candidate.get("location") or ""), "index": index, "reason": "hard_constraint_violation", "violations": validation.get("violations", [])})
        result = pareto_frontier(
            valid,
            {
                "fitness": lambda row: float(row.get("fitness_score", 0) or 0),
                "cost": lambda row: NashEquilibriumSolver._numeric(row.get("cost", row.get("cost_estimate", 0))),
                "risk": lambda row: NashEquilibriumSolver._numeric(row.get("risk_score", row.get("riskScore", 0))),
            },
            {"fitness": True, "cost": False, "risk": False},
        )
        result["rejected"] = rejected_invalid + result.get("rejected", [])
        result["rejected_count"] = len(result["rejected"])
        return result


# ==========================================
# 4. 算法层：DBSCAN 聚类与 NetworkX 图论寻优
# ==========================================
class GraphRouteOptimizer:
    def __init__(self, fitness_calculator):
        self.calc = fitness_calculator

    def optimize_and_sort(self, pois: List[Dict], max_nodes: int) -> List[Dict]:
        if not pois or not isinstance(pois, list):
            return []
        valid_pois = []
        coords = []

        for p in pois:
            if not isinstance(p, dict):
                continue
            loc = p.get("location", "")
            if isinstance(loc, list) and len(loc) >= 2:
                try:
                    lat, lon = float(loc[1]), float(loc[0])
                    coords.append([lat, lon])
                    valid_pois.append(p)
                except Exception:
                    pass
            elif isinstance(loc, str) and "," in loc:
                try:
                    lon, lat = map(float, loc.split(","))
                    coords.append([lat, lon]) 
                    valid_pois.append(p)
                except Exception:
                    pass

        if len(coords) < 3: 
            return sorted(valid_pois, key=lambda x: float(x.get("fitness_score", 0) or 0), reverse=True)[:max_nodes]

        try:
            X = np.radians(np.array(coords))
            db = DBSCAN(eps=6/6371.0, min_samples=1, algorithm='ball_tree', metric='haversine').fit(X)

            G = nx.Graph()
            for i, poi in enumerate(valid_pois):
                poi['cluster'] = int(db.labels_[i])
                G.add_node(i, attr=poi)

            for i in range(len(valid_pois)):
                for j in range(i + 1, len(valid_pois)):
                    dist = self.calc._haversine_distance(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
                    score_val = float(valid_pois[j].get("fitness_score", 0) or 0)
                    edge_weight = dist - (score_val * 0.1)
                    G.add_edge(i, j, weight=max(0.1, edge_weight))

            tsp_path = nx.approximation.traveling_salesman_problem(G, cycle=False)
            optimized_pois = [G.nodes[n]['attr'] for n in tsp_path]
            return optimized_pois[:max_nodes]
        except Exception:
            return sorted(valid_pois, key=lambda x: float(x.get("fitness_score", 0) or 0), reverse=True)[:max_nodes]


# ==========================================
# 5. 算法层：全方位拓扑适应度计算器（全面防 Null 校验）
# ==========================================
class TopologyFitnessCalculator:
    def __init__(self, user_prefs: dict, city_center_coord: str, evolution_memory: list, safety_intel: dict = None, is_luxury: bool = False, preference_signals: dict = None, profile_boost: dict = None, budget_gamma_scale: float = 1.0, fine_tag_boost: dict = None):
        self.user_prefs = user_prefs if isinstance(user_prefs, dict) else {}
        self.mode = str(self.user_prefs.get("mode") or "coop").lower()
        self.center_lat, self.center_lon = self._parse_coord(city_center_coord)
        self.safety_intel = safety_intel if isinstance(safety_intel, dict) else {}

        self.alpha = 1.0  # 体验权重
        self.beta = 1.0   # 距离权重
        self.gamma = 1.0  # 消费/能耗权重
        self.delta = 2.0  # WorldMonitor 风险惩罚权重

        self.is_luxury = bool(is_luxury)
        # 👑 用户偏好信号：驱动不同类型 POI 的权重加成（住宿/餐饮/景点均衡的关键）
        self._signals = preference_signals if isinstance(preference_signals, dict) else {}
        self._category_boost = {"hotel": 0.0, "food": 0.0, "scenic": 0.0, "cultural": 0.0, "shopping": 0.0}
        # 👑 A/B 画像品类加成（treatment 组硬策略）：由 build_profile_category_boost 计算，control 组为空 dict
        self._profile_boost = profile_boost if isinstance(profile_boost, dict) else {}
        # 👑 A/B 细标签加成（treatment 组硬策略）：由 build_profile_fine_tag_boost 计算，control 组为空 dict
        self._fine_tag_boost = fine_tag_boost if isinstance(fine_tag_boost, dict) else {}
        self._budget_gamma_scale = float(budget_gamma_scale) if budget_gamma_scale else 1.0
        self._adjust_weights_by_intent()
        self._apply_rl_evolution(evolution_memory)
        # 👑 A/B 画像消费倾向倾斜（treatment 组硬策略）：low→更省钱 / high→更愿花钱；
        #    luxury 显式诉求优先不覆盖；放在 RL 进化之后作为画像对消费权重的最终叠加。
        if not self.is_luxury and self._budget_gamma_scale != 1.0:
            self.gamma = round(self.gamma * self._budget_gamma_scale, 4)

    def _apply_rl_evolution(self, evolution_memory: list):
        if not isinstance(evolution_memory, list) or not evolution_memory:
            return
        penalty = sum(1 for m in evolution_memory if isinstance(m, dict) and m.get("Score") == -1)
        reward = sum(1 for m in evolution_memory if isinstance(m, dict) and m.get("Score") == 1)
        if penalty > reward:
            self.gamma *= 1.25  
            self.alpha *= 1.15  

    def _parse_coord(self, coord):
        if isinstance(coord, (list, tuple)):
            if len(coord) >= 2:
                try:
                    return float(coord[1]), float(coord[0])
                except Exception:
                    return 0.0, 0.0
            return 0.0, 0.0
        if not isinstance(coord, str) or "," not in coord:
            return 0.0, 0.0
        parts = coord.split(",")
        try:
            return float(parts[1]), float(parts[0])
        except Exception:
            return 0.0, 0.0

    def _haversine_distance(self, lat1, lon1, lat2, lon2):
        if lat1 == 0.0 or lat2 == 0.0:
            return 5.0
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def _extract_cost(self, cost_val) -> float:
        if isinstance(cost_val, list):
            cost_val = cost_val[0] if cost_val else "50"
        if not cost_val or cost_val == "未知":
            return 50.0
        match = re.search(r'\d+(\.\d+)?', str(cost_val))
        return float(match.group()) if match else 50.0

    def _adjust_weights_by_intent(self):
        intent_str = str(self.user_prefs).lower()
        if self.is_luxury or any(kw in intent_str for kw in ["1万", "万元", "10000", "预算充足", "不差钱", "高档", "高端", "豪华", "五星", "奢华", "享受", "品质", "好看", "商圈"]):
            self.is_luxury = True
            self.alpha = 3.5
            self.gamma = -1.5
        elif self.mode == "pvp" or any(kw in intent_str for kw in ["穷游", "省钱", "性价比", "学生", "平替"]):
            self.gamma = 4.5
            self.alpha = 0.8
        elif self.mode == "solo":
            self.alpha = 2.0
            self.beta = 1.5
        else:
            self.beta = 2.5

        if "出片" in intent_str or "网红" in intent_str:
            self.alpha += 1.5
        if "带小孩" in intent_str or "老人" in intent_str or "休闲" in intent_str:
            self.beta += 1.0

        # 👑 偏好信号 -> 类型权重加成：用户明确诉求的品类在候选池排序中优先胜出
        s = self._signals
        if s.get("wants_hotel"):
            self._category_boost["hotel"] += 1.0
        if s.get("luxury_hotel"):
            self._category_boost["hotel"] += 1.5
        if s.get("wants_food"):
            self._category_boost["food"] += 1.0
        if s.get("wants_scenic"):
            self._category_boost["scenic"] += 1.0
        if s.get("wants_cultural"):
            self._category_boost["cultural"] += 1.0
        if s.get("wants_shopping"):
            self._category_boost["shopping"] += 1.0

    def calculate_fitness(self, poi: dict) -> float:
        if not isinstance(poi, dict):
            return 0.0

        rating_val = poi.get("rating")
        if isinstance(rating_val, list):
            rating_val = rating_val[0] if rating_val else None

        try:
            experience_score = float(rating_val)
        except (ValueError, TypeError):
            experience_score = 3.0

        poi_lat, poi_lon = self._parse_coord(poi.get("location", ""))
        distance_km = self._haversine_distance(self.center_lat, self.center_lon, poi_lat, poi_lon)
        base_cost = self._extract_cost(poi.get("cost"))

        safety_penalty = 0.0
        poi_name = str(poi.get("name") or "")
        active_alerts = self.safety_intel.get("active_alerts", []) if isinstance(self.safety_intel, dict) else []

        if isinstance(active_alerts, list):
            for alert in active_alerts:
                if isinstance(alert, dict):
                    detail = str(alert.get("detail") or "")
                    if any(kw in poi_name for kw in ["山", "峡谷", "盘山", "户外", "索道"]) and "降雨" in detail:
                        safety_penalty += self.delta * 2.0

        norm_exp = experience_score / 5.0
        norm_dist = min(distance_km / 20.0, 1.0)
        norm_cost = min(base_cost / 300.0, 1.0)

        if self.is_luxury:
            fitness = (self.alpha * norm_exp) - (self.beta * norm_dist) + (1.5 * norm_cost) - safety_penalty
            if any(k in poi_name for k in ["太古里", "IFS", "商圈", "万象城", "步行街", "黑珍珠", "五星", "度假", "艺术中心", "5A", "故宫", "大巴扎", "博物馆", "文庙", "雷台", "天山"]):
                fitness += 5.0
        else:
            fitness = (self.alpha * norm_exp) - (self.beta * norm_dist) - (self.gamma * norm_cost) - safety_penalty

        if "免门票" in poi_name and self.gamma > 2.0:
            fitness += 1.5 

        # 👑 偏好类型加成：住宿/餐饮/景点等用户明确诉求的品类获得额外适应度
        cat = classify_poi_category(poi_name, str(poi.get("type") or ""), bool(poi.get("is_hotel")))
        if self._category_boost:
            fitness += self._category_boost.get(cat, 0.0)
        # 👑 A/B 画像品类加成：treatment 组按画像维度比例对 scenic/cultural/food 施加差异化权重（硬策略）
        if self._profile_boost:
            fitness += self._profile_boost.get(cat, 0.0)
        # 👑 A/B 细标签加成：按地点名命中的细标签叠加用户喜欢/不喜欢的细分权重（与粗品类正交）
        if self._fine_tag_boost:
            for ft in classify_fine_tags(poi_name):
                fitness += self._fine_tag_boost.get(ft, 0.0)

        return round(fitness, 4)


# ==========================================
# 6. 工具层：专家工具箱（严格文旅分类码）
# ==========================================
class ExpertToolbox:
    def __init__(self):
        self.amap_key = os.getenv("AMAP_API_KEY")
        # Provider credentials must come exclusively from the runtime
        # environment. Never ship a usable key in source or fallback values.
        self.seniverse_key = os.getenv("SENIVERSE_API_KEY", "").strip()
        self.amap_url = "https://restapi.amap.com/v3"

    async def enrich_poi_with_web_search(self, city: str, poi_name: str) -> str:
        def sync_search():
            try:
                query = f"{city} {poi_name} 招牌特色 历史背景 游玩攻略"
                with DDGS(timeout=2) as ddgs:
                    results = list(ddgs.text(query, max_results=1))
                    if results and results[0].get('body'):
                        return results[0]['body']
            except Exception:
                pass
            return ""
        try:
            info = await asyncio.wait_for(asyncio.to_thread(sync_search), timeout=1.2)
            if info:
                return info
        except Exception:
            pass
        return "本地高人气热门目的地，融汇了独特的地域人文与招牌风味体验。"

    async def get_real_time_web_price(self, city: str, target: str) -> str:
        """联网搜一条价格（DuckDuckGo）。失败如实回"未抓取到"，绝不编数字。

        2026-10-04 修正：这里原来既没走 `WEB_SEARCH_PROXY`，也把超时掐到 2 秒 ——
        于是"配了代理"和"没配"完全一样，而且几乎必然超时。现在：
          - 搜索走 `core/web_price_tool.search_texts()`（统一处理代理与超时配置）；
          - 超时从 `WEB_PRICE_TIMEOUT_SECONDS` 读（默认 6s，不再写死 2s）。
        """
        from core.web_price_tool import price_timeout_seconds, search_texts

        query = f"{city} {target} 门票 价格 人均 每晚"
        try:
            results = await asyncio.wait_for(
                search_texts(query, max_results=3),
                timeout=price_timeout_seconds() + 2,
            )
        except Exception:
            return "未抓取到可信供应商价格"
        if not results:
            return "未抓取到外网实时价格"
        return " | ".join(str(item.get("body") or "") for item in results if isinstance(item, dict))

    async def get_coordinates(self, address: str) -> str:
        async with httpx.AsyncClient(timeout=3.0) as http_client:
            try:
                params = {"address": address, "key": self.amap_key}
                resp = await http_client.get(f"{self.amap_url}/geocode/geo", params=params)
                data = resp.json()
                if data.get("status") == "1" and data.get("geocodes"):
                    return str(data["geocodes"][0]["location"])

                search_params = {"keywords": address, "key": self.amap_key}
                search_resp = await http_client.get(f"{self.amap_url}/place/text", params=search_params)
                search_data = search_resp.json()
                if search_data.get("status") == "1" and search_data.get("pois"):
                    return str(search_data["pois"][0]["location"])
            except Exception:
                pass
            return ""

    async def get_geocode(self, address: str) -> Dict[str, Any]:
        """geocode 的**结构化**结果（province / city / district / level / center）。

        为什么要这个：目的地的"真景区"经常不在市辖区里 —— 实测库尔勒市区检索只有 7 个市区公园
        （全部 ≤5km），而博斯腾湖风景名胜区在 47km 外的博湖县、罗布人村寨在 75km 外的尉犁县。
        高德 geocode 对"库尔勒"回的是 `city=巴音郭楞蒙古自治州 / district=库尔勒市 / level=区县`，
        于是我们能**自动拿到上级区域**去补检索，而不必维护一张"市→州"的映射表。
        """
        if not self.amap_key or not str(address or "").strip():
            return {}
        try:
            async with httpx.AsyncClient(timeout=4.0) as http_client:
                resp = await http_client.get(
                    f"{self.amap_url}/geocode/geo",
                    params={"key": self.amap_key, "address": str(address), "extensions": "base"},
                )
                data = resp.json()
        except Exception:
            return {}
        if not isinstance(data, dict) or str(data.get("status")) != "1" or not data.get("geocodes"):
            return {}
        first = data["geocodes"][0] if isinstance(data["geocodes"], list) else {}
        if not isinstance(first, dict):
            return {}
        return {
            "province": str(first.get("province") or ""),
            "city": str(first.get("city") or ""),
            "district": str(first.get("district") or ""),
            "level": str(first.get("level") or ""),
            "adcode": str(first.get("adcode") or ""),
            "center": str(first.get("location") or ""),
        }

    # 👑 精准文旅分类码：110000(风景名胜) | 141200(博物馆展览馆) | 060400(特色步行街) | 060100(综合商场)
    async def get_place_photos(self, name: str, city: str = "") -> List[str]:
        """③ 按**精确名称**再查一次高德，把它自己的实景图取回来（官方接口、配额内）。

        为什么要单独查：`get_dynamic_pois` 是按"城市+关键词"批量搜，回来的 POI 未必就是行程里
        这个节点；而实景图只挂在**具体那个 POI** 上。这里用节点原名精确搜一次，取 photos 字段。
        失败/无图一律返回空列表 —— 配图永远不能拖垮出方案。
        """
        if not self.amap_key or not str(name or "").strip():
            return []
        from core.poi_photos import amap_photo_urls, amap_place_text_url

        try:
            async with httpx.AsyncClient(timeout=5.0) as http_client:
                resp = await http_client.get(amap_place_text_url(str(name), city, self.amap_key))
                data = resp.json()
        except Exception:
            return []
        if not isinstance(data, dict) or data.get("status") != "1":
            return []
        target = self._norm_place_name(str(name))
        for poi in data.get("pois") or []:
            if not isinstance(poi, dict):
                continue
            # 只认同名/同前缀的那一个，避免把别家的图挂到这个节点上
            candidate = self._norm_place_name(str(poi.get("name") or ""))
            if not candidate:
                continue
            if candidate.startswith(target[:6]) or target.startswith(candidate[:6]):
                urls = amap_photo_urls(poi)
                if urls:
                    return urls
        return []

    @staticmethod
    def _norm_place_name(value: str) -> str:
        import re as _re

        return _re.sub(r"[\s（）()\-·]", "", str(value or "")).lower()

    async def get_dynamic_pois(self, city: str, keywords: str, types: str = "110000|141200|060400|060100", limit: int = 40, play_worthy: bool = True) -> List[Dict]:
        if not self.amap_key:
            return []
        from core.poi_pool import parse_amap_location as _parse_amap_location
        from core.poi_pool import is_play_worthy, poi_record_from_amap, amap_error_is_retryable, amap_error_note
        async with httpx.AsyncClient(timeout=4.0) as http_client:
            params = {
                "key": self.amap_key, "keywords": keywords, "city": city,
                "sortrule": "weight", "offset": limit, "page": 1, "extensions": "all"
            }
            # 👑 有 keywords 时**不要**再传 types：实测（2026-09-19）传了 types 会把关键词相关性
            # 打没 —— keywords=博物馆 + types=110000|141200|060400|060100 返回的是"泉舜购物中心/
            # 大商新玛特/正大广场"等商场，而不传 types 时正确返回"洛阳博物馆/洛阳城定鼎门遗址博物馆"。
            # types 只在没有关键词（纯按类别浏览）时才带上。
            if not str(keywords or "").strip():
                params["types"] = types
            try:
                # 👑 供应商错误不能当成"这里没有 POI"：实测高德会对景点查询返回
                # status=0 / CUQPS_HAS_EXCEEDED_THE_LIMIT（QPS 限流），静默吞掉会让候选池
                # 凭空变小、模型改用自己的记忆编地点。限流等 1.2s 重试一次，其它错误如实打印。
                data: Dict[str, Any] = {}
                for attempt in range(2):
                    resp = await http_client.get(f"{self.amap_url}/place/text", params=params)
                    data = resp.json()
                    if str(data.get("status")) == "1":
                        break
                    info = str(data.get("info") or "")
                    if attempt == 0 and amap_error_is_retryable(info):
                        print(f"⚠️ [POI 检索] 高德限流（{info}），city={city} keywords={keywords!r} 等 1.2s 重试一次", flush=True)
                        await asyncio.sleep(1.2)
                        continue
                    print(
                        f"⚠️ [POI 检索] {amap_error_note(info, data.get('status'))}：city={city} keywords={keywords!r}（不编造候选）",
                        flush=True,
                    )
                    break
                if data.get("status") == "1" and data.get("pois"):
                    pois = []
                    for poi in data["pois"]:
                        if not isinstance(poi, dict):
                            continue
                        poi_name = str(poi.get("name") or "")

                        # 严格负向过滤：直接丢弃非文旅商业门店
                        poi_lower = poi_name.lower()
                        if any(b.lower() in poi_lower for b in NON_TOURIST_BLACKLIST):
                            continue
                        # 👑 还要挡住"名字里借了景点关键词、其实是设施/商店/住宿"的 POI：
                        # 实测（2026-09-19）「xx博物馆-西北门地上停车场」「xx博物馆文创空间」
                        # 「xx园林宾馆」都被当成景点排进了 7 天行程。
                        #
                        # ⚠️ 但"必须属于玩点类别"这一条**只适用于景点检索**。实测（2026-10-04 乌鲁木齐）：
                        # 对美食/住宿套用同一条，会把高德明明返回的 48 家餐厅、30 家酒店**全部丢掉**
                        # （它们的类别是餐饮服务/住宿服务，永远不属于"玩点类别"），
                        # 表现为"美食/住宿候选 = 0 → 行程只能排文庙/博物馆这类人文景点"，
                        # 而用户明确要求了自然景观与地道美食。
                        #
                        # 注意**不能**整个跳过 is_play_worthy：那样连"xx酒店-停车场""xx出入口"
                        # 都会混进来（这条测试抓过我一次）。所以只放宽 strict_type / allow_commerce：
                        # 设施名（停车场/出入口/售票处…）在任何类别下都仍然被挡住。
                        if not is_play_worthy(
                            poi_name,
                            poi.get("type"),
                            strict_type=play_worthy,
                            allow_commerce=not play_worthy,
                        ):
                            continue

                        biz_ext = poi.get("biz_ext")
                        if not isinstance(biz_ext, dict):
                            biz_ext = {}

                        rating = biz_ext.get("rating")
                        if isinstance(rating, list):
                            rating = rating[0] if rating else None

                        cost = biz_ext.get("cost")
                        if isinstance(cost, list):
                            cost = cost[0] if cost else None
                        rating_source = "amap" if rating not in (None, "") else "unavailable"
                        cost_source = "amap" if cost not in (None, "") else "unavailable"

                        # 记录构造走 core/poi_pool.poi_record_from_amap（纯函数、有单测）：
                        # 它保证**同时**给出 location（"lng,lat" 字符串，供静态地图/跳转）与
                        # lnglat（[lng, lat] 数组，供三层匹配与前端绘制）。见那里的契约注释。
                        pois.append(
                            poi_record_from_amap(
                                poi,
                                city=city,
                                amap_key=self.amap_key or "",
                            )
                        )
                    # 👑 实景图增强：对未返回实拍照片但含高德 ID 的 POI，批量调用 place/detail 按 ID 补齐真实照片
                    no_photo_pois = [p for p in pois if not p.get("photos") and p.get("id")]
                    if no_photo_pois:
                        try:
                            detail_ids = [p["id"] for p in no_photo_pois[:12]]
                            detail_tasks = [
                                http_client.get(f"{self.amap_url}/place/detail",
                                                params={"key": self.amap_key, "id": pid})
                                for pid in detail_ids
                            ]
                            detail_resps = await asyncio.gather(*detail_tasks, return_exceptions=True)
                            id_to_photos = {}
                            for pid, dresp in zip(detail_ids, detail_resps):
                                if isinstance(dresp, Exception):
                                    continue
                                try:
                                    dj = dresp.json()
                                    if dj.get("status") == "1" and dj.get("pois"):
                                        urls = []
                                        for ph in dj["pois"][0].get("photos", []):
                                            if isinstance(ph, dict) and ph.get("url"):
                                                u = str(ph["url"]).strip()
                                                if u.startswith("//"):
                                                    u = "https:" + u
                                                elif u.startswith("http://"):
                                                    u = "https://" + u[len("http://"):]
                                                if u.startswith("https://"):
                                                    urls.append(u)
                                        if urls:
                                            id_to_photos[pid] = urls[:3]
                                except Exception:
                                    continue
                            for p in pois:
                                if not p.get("photos") and p.get("id") in id_to_photos:
                                    p["photos"] = id_to_photos[p["id"]]
                        except Exception as e:
                            print(f"🔥 [实景图增强异常]: {e}")
                    return pois
            except Exception as e:
                print(f"🔥 [POI抓取异常]: {e}")
        return []

    async def get_traffic_status(self, location_coord: str) -> Dict[str, Any]:
        if not location_coord:
            return {"status_code": "unknown", "description": "暂无供应商数据", "advice": "缺少坐标，无法获取实时路况", "source": "unavailable", "estimated": True}

        cache_key = f"traffic:{location_coord}"
        cached = _cache_get(cache_key)
        if cached:
            return cached

        async with httpx.AsyncClient(timeout=3.0) as http_client:
            params = {"key": self.amap_key, "location": location_coord, "radius": 5000, "level": 5}
            try:
                resp = await http_client.get(f"{self.amap_url}/traffic/status/circle", params=params)
                data = resp.json()
                if data.get("status") == "1" and "trafficinfo" in data:
                    info = data["trafficinfo"]
                    status_desc = str(info.get("description", "路况正常"))
                    raw_eval = info.get("evaluation", {}).get("status", "1")
                    eval_code_int = int(raw_eval) if str(raw_eval).isdigit() else 1

                    advice = "实时路况良好，整体畅通。"
                    if eval_code_int >= 3:
                        advice = "注意！局部路段存在拥堵，请预留缓冲时间并错峰排布。"
                    result = {"status_code": str(eval_code_int), "description": status_desc, "advice": advice, "source": "amap", "estimated": False}
                    _cache_set(cache_key, result, ttl=900)
                    return result
            except Exception:
                pass
            return {"status_code": "unknown", "description": "暂无供应商数据", "advice": "请在出行前重新获取实时路况", "source": "unavailable", "estimated": True}

    def _extract_polyline_coords(self, path_obj) -> List[List[float]]:
        coords = []
        if not isinstance(path_obj, dict):
            return coords
        steps = path_obj.get("steps", [])
        for step in steps:
            if not isinstance(step, dict):
                continue
            polyline_str = step.get("polyline", "")
            if polyline_str:
                for pt in polyline_str.split(";"):
                    try:
                        lng, lat = pt.split(",")
                        coords.append([float(lng), float(lat)])
                    except Exception:
                        pass
        return coords

    async def get_travel_options(self, origin_lnglat: str, dest_lnglat: str, city: str = "") -> Dict[str, Any]:
        if not self.amap_key or not origin_lnglat or not dest_lnglat:
            return {}
        async with httpx.AsyncClient(timeout=3.5) as http_client:
            tasks = [
                # 策略 10（高德默认）：躲避拥堵 + 路程较短 + 尽量缩短时间，返回多条路线取 paths[0]，
                # 避免 strategy=0（速度优先）在往返时因高速/环线绕出明显更远的“理论最快”路线
                http_client.get(f"{self.amap_url}/direction/driving", params={"key": self.amap_key, "origin": origin_lnglat, "destination": dest_lnglat, "strategy": 10}),
                http_client.get(f"{self.amap_url}/direction/walking", params={"key": self.amap_key, "origin": origin_lnglat, "destination": dest_lnglat})
            ]
            if city:
                tasks.append(http_client.get(f"{self.amap_url}/direction/transit/integrated", params={"key": self.amap_key, "origin": origin_lnglat, "destination": dest_lnglat, "city": city, "cityd": city}))

            names = ["driving", "walking", "transit"] if city else ["driving", "walking"]

            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                travel_info = {}
                for name, resp in zip(names, results):
                    if isinstance(resp, Exception) or not resp:
                        continue
                    try:
                        data = resp.json()

                        if name == "transit" and data.get("status") == "1" and data.get("route", {}).get("transits"):
                            transit = data["route"]["transits"][0]
                            dist_km = round(int(transit.get("distance", 0)) / 1000, 1)
                            dur_min = round(int(transit.get("duration", 0)) / 60)

                            transit_steps = []
                            for seg in transit.get("segments", []):
                                bus = seg.get("bus", {})
                                for line in bus.get("buslines", []):
                                    line_name = line.get("name", "").split("(")[0]
                                    transit_steps.append(f"乘坐【{line_name}】至【{line.get('arrival_stop', {}).get('name', '')}】")
                                rail = seg.get("railway", {})
                                if rail and rail.get("name"):
                                    transit_steps.append(f"乘坐【{rail.get('name')}】")

                            travel_info["transit"] = {
                                "label": "公交/地铁", 
                                "distance_km": dist_km, 
                                "duration_min": dur_min,
                                "steps": transit_steps if transit_steps else [f"搭乘城市公共交通线路约 {dur_min} 分钟"]
                            }

                        elif data.get("status") == "1" and data.get("route", {}).get("paths"):
                            path = data["route"]["paths"][0]
                            dist_km = round(int(path["distance"]) / 1000, 1)
                            dur_min = round(int(path["duration"]) / 60)
                            label_map = {"walking": "步行", "driving": "驾车"}

                            actual_coords = self._extract_polyline_coords(path)
                            steps = path.get("steps", [])

                            clean_steps = []
                            for s in steps:
                                inst = s.get("instruction", "")
                                clean_inst = re.sub(r'<[^>]+>', '', str(inst)).strip()
                                if clean_inst:
                                    clean_steps.append(clean_inst)

                            travel_info[name] = {
                                "label": label_map.get(name, name), 
                                "distance_km": dist_km, 
                                "duration_min": dur_min,
                                "actual_path": actual_coords,
                                "steps": clean_steps,
                                "navi_summary": " -> ".join(clean_steps[:3]) if clean_steps else f"沿主要道路出行约{dur_min}分钟"
                            }
                    except Exception:
                        continue
                return travel_info
            except Exception:
                return {}

    async def get_amap_weather(self, city: str) -> Dict[str, Any]:
        """对接高德天气 API（weatherInfo），返回实时天气+未来3天预报；失败返回空兜底由上层回退。"""
        if not self.amap_key:
            return {}
        cache_key = f"amap_weather:{city}"
        cached = _cache_get(cache_key)
        if cached:
            return cached

        try:
            async with httpx.AsyncClient(timeout=3.5) as http_client:
                geo_resp = await http_client.get(f"{self.amap_url}/geocode/geo", params={"address": city, "key": self.amap_key})
                geo_data = geo_resp.json()
                adcode = ""
                if geo_data.get("status") == "1" and geo_data.get("geocodes"):
                    adcode = str(geo_data["geocodes"][0].get("adcode") or "")
                if not adcode:
                    return {}

                weather_resp = await http_client.get(
                    f"{self.amap_url}/weather/weatherInfo",
                    params={"city": adcode, "key": self.amap_key, "extensions": "all"}
                )
                wdata = weather_resp.json()
                if wdata.get("status") == "1" and wdata.get("forecasts"):
                    casts = wdata["forecasts"][0].get("casts") or []
                    now_text, now_temp = "", ""
                    if casts:
                        day0 = casts[0]
                        now_text = str(day0.get("dayweather") or "")
                        now_temp = str(day0.get("daytemp") or "")

                    forecast = []
                    for i, d in enumerate(casts[:3]):
                        forecast.append({
                            "day": f"Day {i + 1}",
                            "text": str(d.get("dayweather") or "暂无供应商数据"),
                            "temp": f"{d.get('nighttemp', '')}~{d.get('daytemp', '')}°C"
                        })
                    condition = f"{now_text} {now_temp}°C" if now_temp else now_text
                    result = {"condition": condition or "暂无供应商数据", "forecast": forecast, "source": "amap", "estimated": False}
                    _cache_set(cache_key, result, ttl=3600)
                    return result
        except Exception:
            pass
        return {}

    async def get_indoor_alternatives(self, city: str) -> List[Dict]:
        """雨天备选方案：检索室内文旅场所（博物馆/展览馆/综合商场等），覆盖户外景点。"""
        return await self.get_dynamic_pois(
            city,
            keywords="博物馆|科技馆|美术馆|展览馆|综合商场|图书馆|剧院|艺术馆|天文馆",
            types="141200|060100",
            limit=10
        )

    async def get_real_weather(self, location_coord_or_city: str) -> Dict[str, Any]:
        # 👑 优先对接高德天气 API，缓存命中率低时回退心知天气，最终走本地保底
        amap_weather = await self.get_amap_weather(str(location_coord_or_city))
        if amap_weather and amap_weather.get("condition"):
            return amap_weather

        # 心知天气兜底（密钥已在环境变量中配置）
        if self.seniverse_key:
            try:
                async with httpx.AsyncClient(timeout=3.5) as http_client:
                    now_resp = await http_client.get(
                        "https://api.seniverse.com/v3/weather/now.json",
                        params={"key": self.seniverse_key, "location": str(location_coord_or_city), "language": "zh-Hans", "unit": "c"}
                    )
                    daily_resp = await http_client.get(
                        "https://api.seniverse.com/v3/weather/daily.json",
                        params={"key": self.seniverse_key, "location": str(location_coord_or_city), "language": "zh-Hans", "unit": "c", "start": 0, "days": 3}
                    )

                    now_text, now_temp = "", ""
                    forecast: List[Dict[str, str]] = []

                    if now_resp.status_code == 200:
                        data = now_resp.json()
                        results = data.get("results") or []
                        if results:
                            now = results[0].get("now") or {}
                            now_text = str(now.get("text") or "")
                            now_temp = str(now.get("temperature") or "")

                    if daily_resp.status_code == 200:
                        ddata = daily_resp.json()
                        dresults = ddata.get("results") or []
                        if dresults:
                            for i, d in enumerate((dresults[0].get("daily") or [])[:3]):
                                forecast.append({
                                    "day": f"Day {i + 1}",
                                    "text": str(d.get("text_day") or d.get("text") or "暂无供应商数据"),
                                    "temp": f"{d.get('low', '')}~{d.get('high', '')}°C"
                                })

                    condition = f"{now_text} {now_temp}°C" if now_temp else now_text
                    if now_text or now_temp or forecast:
                        return {"condition": condition or "暂无供应商数据", "forecast": forecast, "source": "seniverse", "estimated": False}

            except Exception:
                pass

        # Never synthesize current weather when every supplier is unavailable.
        # Callers can keep planning with an explicit unknown/estimated state.
        return {"condition": "暂无供应商数据", "forecast": [], "source": "unavailable", "estimated": True}


# ==========================================
# 6.5 🚀 LLM 语义缓存层：embedding 相似度 > 阈值直接复用，Redis 共享缓存避免多实例重复推理
# ==========================================
def _static_map_url(lnglat, amap_key, size: str = "720*480", zoom: int = 15) -> str:
    """按坐标生成高德静态地图 URL（保底图片，永不空白）。"""
    if not (isinstance(lnglat, list) and len(lnglat) >= 2):
        return ""
    try:
        lng, lat = float(lnglat[0]), float(lnglat[1])
    except (TypeError, ValueError):
        return ""
    if lng == 0.0 and lat == 0.0 or not amap_key:
        return ""
    loc_str = f"{lng},{lat}"
    return (
        f"https://restapi.amap.com/v3/staticmap?location={loc_str}&zoom={zoom}&size={size}&scale=2"
        f"&markers=mid,0xFF5722,A:{loc_str}&key={amap_key}"
    )


# 👑 热门城市预生成种子模板：坐标全部来自前端 provinceData 真实数据源，保证冷启动首屏 < 1s
HOT_CITY_SEED_ROUTES: Dict[str, Dict[str, Any]] = {
    "北京": {
        "summary": "北京 3 日经典皇城巡游（预生成模板，命中秒回）",
        "nodes": [
            ("天安门广场", [116.3975, 39.9087], "共和国的中心，见证无数历史时刻"),
            ("故宫博物院", [116.3970, 39.9181], "明清两代皇家宫殿，中国古代宫廷建筑之精华"),
            ("八达岭长城", [116.0178, 40.3547], "不到长城非好汉，最壮观的明长城段"),
            ("颐和园", [116.2787, 39.9969], "中国古典园林之首，皇家园林博物馆"),
        ],
    },
    "成都": {
        "summary": "成都 3 日巴蜀悠闲之旅（预生成模板，命中秒回）",
        "nodes": [
            ("大熊猫繁育研究基地", [104.1019, 30.7352], "近距离接触国宝滚滚的绝佳地"),
            ("青城山", [103.5832, 30.9005], "青城天下幽，道教发源地之一"),
            ("峨眉山", [103.4842, 29.5850], "秀甲天下，普贤菩萨道场"),
            ("九寨沟", [103.9193, 33.2668], "童话世界，水景之王（建议单独安排往返）"),
        ],
    },
    "三亚": {
        "summary": "三亚 3 日热带滨海度假（预生成模板，命中秒回）",
        "nodes": [
            ("南山文化旅游区", [109.2087, 18.3083], "108米海上观音，震撼心灵"),
            ("天涯海角", [109.3504, 18.2295], "海畔巨石，象征爱情忠贞的浪漫之地"),
            ("蜈支洲岛", [109.7624, 18.3101], "中国的马尔代夫，潜水胜地"),
            ("亚龙湾", [109.6418, 18.2181], "天下第一湾，水清沙幼的高端度假区"),
        ],
    },
}


def _build_seed_final_data(city: str, trip_days: int) -> Optional[Dict[str, Any]]:
    """将热门城市种子地标展开为 trip_days 天的完整 route 节点（冷启动首屏兜底）。"""
    seed = HOT_CITY_SEED_ROUTES.get(city)
    if not seed:
        return None
    amap_key = os.getenv("AMAP_API_KEY")
    nodes = seed["nodes"]
    route: List[Dict[str, Any]] = []
    slot_idx = 0
    for day in range(1, trip_days + 1):
        # 每天至少 1 个节点，不足则循环复用种子地标，保证天数完整
        day_nodes = 2 if day <= 2 else max(1, len(nodes) - 2)
        for _ in range(day_nodes):
            name, lnglat, desc = nodes[slot_idx % len(nodes)]
            slot_idx += 1
            route.append({
                "day": day,
                "name": name,
                "location": name,
                "lnglat": list(lnglat),
                "type": "风景",
                "desc": desc,
                "time": ["09:00", "11:00", "14:00", "17:30"][slot_idx % 4],
                "transport": "打车/自驾",
                "tags": ["经典必游", "预生成模板"],
                "cost_estimate": "暂无供应商数据",
                "photos": [],
                "amap_url": f"https://www.amap.com/search?query={urllib.parse.quote(f'{city} {name}')}",
                "map_image": _static_map_url(list(lnglat), amap_key),
                "rating": "暂无供应商数据",
                "open_time": "暂无供应商数据",
                "data_sources": {"cost_estimate": "seed_template", "rating": "seed_template", "open_time": "seed_template"},
                "estimated": True,
                "is_hotel": False,
                "trust_reason": "热门城市预生成种子模板（首屏秒回）",
            })
    return {
        "status": "seed_template",
        "negotiation_summary": seed["summary"],
        "route": route,
        "recommended_pois": [],
    }


class SemanticItineraryCache:
    """LLM 推理结果语义缓存。

    1. 精确命中：md5(city+days+画像) -> 直接复用（含 Redis 共享，多实例免重复推理）
    2. 语义命中：同城市+同天数，画像 embedding 余弦相似度 >= threshold 时复用
    3. Redis 不可用时自动降级为进程内存缓存，不影响主推演流程
    """

    def __init__(self):
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.sim_threshold = float(os.getenv("CACHE_SIM_THRESHOLD", "0.92"))
        self.embed_model = os.getenv("LLM_EMBED_MODEL", "text-embedding-v3").strip()
        self.ttl = int(os.getenv("CACHE_TTL", "86400"))  # 默认 24h
        self._mem: Dict[str, Dict[str, Any]] = {}
        self._vec_index: Dict[str, Dict[str, Any]] = {}  # key -> {city, days, vector}

    def _key(self, city: str, days: int, profile_text: str) -> str:
        # 👑 版本号参与哈希：修复生成逻辑后 bump 版本即可让旧缓存（缺美食/酒店）自动失效
        # v4 invalidates plans cached before the final daily-balance gate.
        # v5：类别/原始诉求进入精确缓存键，避免同城同天数的文化路线
        # 污染自然、美食、购物或普通均衡路线。
        raw = f"v5|{city}|{days}|{''.join(str(profile_text or '').split())}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    async def _embed(self, text: str) -> Optional[List[float]]:
        if not self.embed_model:
            return None
        try:
            resp = await client.embeddings.create(model=self.embed_model, input=[str(text)[:1800]])
            vec = resp.data[0].embedding
            return list(vec) if vec else None
        except Exception as e:
            print(f"⚠️ [语义缓存] embedding 调用失败，退化为精确匹配: {e}")
            return None

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        try:
            v1 = np.asarray(a, dtype=float)
            v2 = np.asarray(b, dtype=float)
            n1, n2 = float(np.linalg.norm(v1)), float(np.linalg.norm(v2))
            if n1 == 0.0 or n2 == 0.0:
                return 0.0
            return float(np.dot(v1, v2) / (n1 * n2))
        except Exception:
            return 0.0

    async def _redis_get(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            r = redis.from_url(self.redis_url, decode_responses=True)
            raw = await r.get(key)
            await r.aclose()
            if raw:
                return json.loads(raw)
        except Exception as e:
            print(f"⚠️ [语义缓存] Redis 读取跳过: {e}")
        return None

    async def _redis_set(self, key: str, payload: Dict[str, Any]):
        try:
            r = redis.from_url(self.redis_url, decode_responses=True)
            await r.set(key, json.dumps(payload, ensure_ascii=False, default=str), ex=self.ttl)
            await r.aclose()
        except Exception as e:
            print(f"⚠️ [语义缓存] Redis 写入跳过: {e}")

    async def lookup(self, city: str, days: int, profile_text: str) -> Optional[Dict[str, Any]]:
        days = int(days)
        key = self._key(city, days, profile_text)
        hit = self._mem.get(key) or await self._redis_get(key)
        if hit:
            self._mem[key] = hit
            print(f"✅ [语义缓存] 精确命中 {city}/{days}天")
            return hit

        # 规划结果不能只按“城市 + 天数 + 语义相似”复用。
        # “想看博物馆”和“想看自然风景/美食”在 embedding 空间可能很接近，
        # 但对用户而言是完全不同的路线。以前的语义命中会把文化主题缓存
        # 复用到普通/深度探索请求，表现为“无论输入什么都在逛博物馆”。
        # 保留精确画像命中；跨画像的语义缓存暂时只用于离线实验，不参与线上规划。
        return None

    async def store(self, city: str, days: int, profile_text: str, payload: Dict[str, Any]):
        days = int(days)
        key = self._key(city, days, profile_text)
        self._mem[key] = payload
        await self._redis_set(key, payload)
        # fire-and-forget 补充 embedding 向量索引（best-effort，不阻塞主推演流程）
        asyncio.ensure_future(self._index_embedding(key, city, days, profile_text))

    async def _index_embedding(self, key: str, city: str, days: int, profile_text: str):
        try:
            vec = await self._embed(profile_text)
            if vec is not None:
                self._vec_index[key] = {"city": city, "days": days, "vector": vec}
        except Exception as e:
            print(f"⚠️ [语义缓存] 向量索引建立跳过: {e}")


_semantic_cache = SemanticItineraryCache()


# ==========================================
# 7. 路由层：主推演协商 API 端点（提高节点密度 + 动态消费 + 严格多天输出）
# ==========================================
@router.post("/agent/negotiate")
async def run_negotiate(msg: GatewayMessage):
    toolbox = ExpertToolbox()
    wm_client = RiskService()
    blackboard = BlackboardSystem()
    payload = msg.payload or {}
    room_id = msg.room_id or "global"
    user_id = msg.user_id or "anonymous"
    # 本次推演的关联键：一次规划发出的所有事件（推演开始 / 推理步骤 / 专业提案 / 生成完成）
    # 共用它，事件流才能按"一次规划"串起来重放与审计。调用方给了就用（网关/前端将来可传），
    # 没给就自己铸一个，并回填进 final_data 供前端与网关关联。
    plan_correlation_id = str(
        payload.get("correlation_id") or payload.get("plan_id") or payload.get("trip_id") or ""
    ).strip() or f"plan-{uuid.uuid4().hex[:12]}"

    user_prefs = payload.get("user_preferences") or payload.get("current_request", {}).get("user_preferences", {})
    if not isinstance(user_prefs, dict):
        user_prefs = {}

    # 👑 后端枚举校验：出行模式/偏好定位非法值直接拒绝，杜绝「or coop / or 默认角色」的静默回退
    try:
        mode = normalize_mode(user_prefs.get("mode"))
        role = normalize_role(user_prefs.get("role"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # 回写规范值，供下游（适应度计算器 / 缓存键 / 成员兜底）统一使用校验结果
    user_prefs["mode"] = mode
    user_prefs["role"] = role

    history_sequence = user_prefs.get("history_sequence", [])
    if not isinstance(history_sequence, list):
        history_sequence = []

    current_existing_route = user_prefs.get("current_existing_route", [])
    if not isinstance(current_existing_route, list):
        current_existing_route = []

    intent_str = str(user_prefs.get("intent") or "")

    room_members = user_prefs.get("room_members", [])
    if not isinstance(room_members, list) or len(room_members) == 0:
        room_members = [
            {"id": "u1", "name": "Felix (主控)", "role": role, "intent": intent_str or "常规游览体验"},
            {"id": "u2", "name": "Alice", "role": "视觉体验", "intent": "探访城市核心商圈与地标，拍照出片"},
            {"id": "u3", "name": "Bob", "role": "休闲漫步", "intent": "步调宽松，安排特色茶社或公园慢游"}
        ]

    full_text_context = " | ".join(history_sequence) + " " + intent_str + " " + " ".join([str(m.get("intent", "")) for m in room_members if isinstance(m, dict)])

    # 👑 抽取用户偏好信号（住宿/餐饮/景点/购物），用于偏好权重、提示词与覆盖校验
    pref_signals = extract_preference_signals(full_text_context)
    print(f"🎯 [偏好信号] {pref_signals}", flush=True)
    # 👑 只看**发起人自己这一轮说的话**：配比口径（默认自然=人文各半）与检索配额都以它为准。
    # 实测事故（2026-10-04）：诉求"帮我安排一下就行"本身没提自然，但 room_members 里的占位 intent
    # （"公园慢游"之类）让 pref_signals.wants_scenic=True，配比目标被算成"自然 6 / 人文 2" ——
    # 用户根本没提自然，却拿到一份自然压倒人文的目标。成员偏好仍会通过房间公平性参与平衡。
    own_signals = extract_preference_signals(intent_str)
    # 事件流：需求解析完成。解析出的信号是后面所有取舍的依据 —— 只留在日志里，
    # 事后就说不清"为什么给他排了这么多住宿"。producer=coordinator（解析目前内联在协调器里）。
    await event_bus.emit(OMNI_EVENTS_STREAM, "intent.parsed", {
        "room_id": room_id, "user_id": user_id,
        "signals": pref_signals,
        "intent_chars": len(intent_str),
        "member_count": len(room_members),
        "correlation_id": plan_correlation_id,
    }, room_id, correlation_id=plan_correlation_id, producer="coordinator")

    # 参考路线画像：用户主动提供的"我喜欢的一条路线"（例如小红书帖子）抽取出的特征。
    # **只在当轮诉求没说明白时才参与决策** —— 用户说了什么永远优先，否则会出现
    # "我明明说了这次看人文，却被画像排成自然"。抽不到特征（unknown）就不影响任何旋钮。
    reference_profile = user_prefs.get("reference_profile") if isinstance(user_prefs.get("reference_profile"), dict) else None
    reference_hint = None
    if reference_profile:
        try:
            from core.route_profile import reference_profile_hint

            # 判据只看发起人自己说的话（own_signals 在上方统一算好，见那里的实测事故说明）。
            reference_hint = reference_profile_hint(reference_profile, explicit_signals=own_signals)
            if reference_hint:
                print(f"🧭 [参考画像] 本次采用参考路线偏好：{reference_hint['disclosure']}", flush=True)
            else:
                print(
                    f"🧭 [参考画像] 本轮诉求已足够明确，画像不参与决策（诉求优先）: {own_signals.get('requirements')}",
                    flush=True,
                )
        except Exception as _reference_exc:  # 画像出问题绝不影响出方案
            print(f"⚠️ [参考画像] 忽略（不影响主流程）: {_reference_exc}", flush=True)

    # 🎯 个性化旅行画像注入：读取网关注入的画像，A/B 分组决定是否应用个性化提示词
    personalized_profile = user_prefs.get("personalized_profile") or {}
    if not isinstance(personalized_profile, dict):
        personalized_profile = {}
    member_profiles = user_prefs.get("member_profiles") or {}
    if not isinstance(member_profiles, dict):
        member_profiles = {}
    # 🎯 融合手选角色与算法画像：手选 role 始终生效（覆盖冷启动/对照组），算法画像按 A/B 注入
    personalization_hint = build_personalization_hint(personalized_profile, member_profiles, room_members, role)
    if reference_hint:
        # 让模型也知道"这次是按你参考过的路线偏好来的"，而不是我们悄悄换了口味
        personalization_hint = f"{personalization_hint} {reference_hint['disclosure']}"

    # 1. 👑 智能解析游玩天数（支持中文与复合词，如 "三天", "5天", "两天一夜"）
    trip_days = parse_chinese_days(full_text_context)
    print(f"🎯 [天数解析] 解析到 trip_days={trip_days} | 上下文: {full_text_context[:160]!r}", flush=True)

    # 2. 👑 提升单日节点密度：标准提升到 5 个丰富节点（早茶、上午景点、午餐、午后景点、晚间美食、夜间住宿）
    count_match = re.search(r'(?:每天|日|一天|每日)(\d+)[个处条项点]', full_text_context)
    # 深度探索默认提高白天节点密度；显式“每天 N 个”仍优先遵循用户约束。
    target_daily_count = int(count_match.group(1)) if count_match else (6 if role == "深度探索" else 5)

    # 3. 城市识别与锁定
    candidate_city = ""
    city_patterns = [
        r'(?:保持在|锁死|在|去|到|前往|抵达|想?[去在到])([一-龥]{2,6})(?:不变|玩|游玩|旅游|旅行|逛|耍|转转|待|呆|深度|周边|的)',
        r'(?:去|到|前往|想去|目的地是?|帮我规划?)([一-龥]{2,6})',  
        r'^([一-龥]{2,6})(?:旅游|攻略|路书|行程)'
    ]
    common_cities = [
        "武威", "兰州", "张掖", "酒泉", "敦煌", "甘肃", "乌鲁木齐", "喀什", "伊犁", "阿勒泰", "库尔勒", "吐鲁番",
        "成都", "北京", "上海", "广州", "深圳", "洛阳", "徐州", "海南", "海口", "三亚", "西安", "重庆", 
        "杭州", "南京", "武汉", "长沙", "拉萨", "青岛", "厦门", "哈尔滨", "大理", "丽江", "新疆", "西藏", "濮阳", "安阳"
    ]

    # 👑 省份/自治区 -> 默认核心城市：避免将“新疆/西藏/甘肃”等省级行政区直接作为高德 city 参数查询，
    # 否则 POI 检索失败导致地点、图片、坐标全部失真
    province_default_city = {
        "新疆": "乌鲁木齐", "西藏": "拉萨", "甘肃": "兰州", "青海": "西宁",
        "宁夏": "银川", "内蒙": "呼和浩特", "内蒙古": "呼和浩特", "海南": "海口",
    }

    all_inputs_to_check = [intent_str] + list(reversed(history_sequence))

    for text in all_inputs_to_check:
        if not text:
            continue
        c_text = str(text).strip('。，！!?,. \n\t')

        if 2 <= len(c_text) <= 6 and not any(kw in c_text for kw in ["怎么", "如何", "推荐", "行程", "安排", "换", "修改", "平替"]):
            candidate_city = c_text
            break

        matched = False
        for pattern in city_patterns:
            match = re.search(pattern, str(text))
            if match:
                candidate_city = match.group(1)
                matched = True
                break
        if matched:
            break

        for city in common_cities:
            if city in str(text):
                candidate_city = city
                matched = True
                break
        if matched:
            break

    if not candidate_city and current_existing_route and len(current_existing_route) > 0:
        first_route_name = str(current_existing_route[0].get("name", ""))
        for c in common_cities:
            if c in first_route_name or c in full_text_context:
                candidate_city = c
                break

    if candidate_city:
        if candidate_city.endswith("市") or candidate_city.endswith("省"):
            candidate_city = candidate_city[:-1]
        elif len(candidate_city) > 2 and candidate_city[-1] in ['州', '县', '区']:
            candidate_city = candidate_city[:-1]
        target_city = candidate_city
    else:
        target_city = "武威"

    # 👑 省级行政区自动收敛到省会/核心城市，保证高德检索能命中真实 POI
    target_city = province_default_city.get(target_city, target_city)

    # 👑 社区软引导：读取网关注入的高赞社区行程，作为软参考（仅借鉴，非硬约束）
    community_reference = user_prefs.get("community_reference", [])
    if not isinstance(community_reference, list):
        community_reference = []
    community_hint = "（暂无匹配城市的社区高赞行程，本轮以实时地标数据为准）"
    if community_reference:
        matched_refs = []
        for ref in community_reference:
            if not isinstance(ref, dict):
                continue
            ref_city = str(ref.get("dest_city") or "")
            if target_city in ref_city or ref_city in target_city:
                matched_refs.append(ref)
        if matched_refs:
            lines = []
            for ref in matched_refs[:2]:
                title = str(ref.get("title") or "社区高赞行程")
                content = str(ref.get("content") or "").strip()
                lines.append(f"- 《{title}》：{content[:280]}")
            community_hint = "\n".join(lines)

    coord = await toolbox.get_coordinates(target_city) or "102.63,37.93"
    current_mode = mode
    user_role = role
    evolution_memory = payload.get("evolution_memory", [])
    if not isinstance(evolution_memory, list):
        evolution_memory = []

    is_refinement = len(history_sequence) > 0 and len(current_existing_route) > 0
    llm_temperature = 0.55 if is_refinement else 0.2

    # 动态调优候选池数量，确保足够支撑多天无重复
    # 长行程需要留出候选冗余：模型会做去重、类别均衡和最小扰动重排，
    # 仅按“天数 × 节点数”拉取很容易让最后一天在清洗后只剩住宿。
    poi_limit = max(60, trip_days * (target_daily_count + 8))

    # —— 动态预算解析：优先识别用户明确给出的预算金额，其次识别品质/高消费诉求 ——
    luxury_keywords = ["1万", "万元", "10000", "预算充足", "不差钱", "高档", "高端", "豪华", "五星", "奢华", "享受", "品质", "好看", "商圈"]
    is_luxury = any(kw in full_text_context.lower() for kw in luxury_keywords)

    _member_count = len([m for m in room_members if isinstance(m, dict)]) or 1
    _exact_budget = parse_budget_amount(full_text_context, trip_days=trip_days, member_count=_member_count)

    if _exact_budget:
        budget_mode = "EXACT_AMOUNT"
        total_calc_budget = _exact_budget
    elif is_luxury:
        budget_mode = "HIGH_LUXURY"
        total_calc_budget = trip_days * 1200
    else:
        budget_mode = "VALUE_COST_EFFECTIVE"
        total_calc_budget = trip_days * 350

    hotel_budget = round(total_calc_budget * 0.45)
    dining_budget = round(total_calc_budget * 0.30)
    ticket_budget = round(total_calc_budget * 0.15)
    traffic_budget = total_calc_budget - hotel_budget - dining_budget - ticket_budget

    budget_breakdown_payload = {
        "budget_mode": budget_mode,
        "total_budget": total_calc_budget,
        "daily_avg": round(total_calc_budget / trip_days),
        "member_count": _member_count,
        "hotel": hotel_budget, "dining": dining_budget, "ticket": ticket_budget, "traffic": traffic_budget
    }

    # —— 🚀 LLM 语义缓存层：命中时秒回，跳过完整推理 ——
    profile_text = " | ".join([
        intent_str,
        # 结构化类别签名比 embedding 更可靠；它会随用户明确的“想吃/想逛
        # 景区/少博物馆/购物”等诉求变化。
        "signals:" + json.dumps({
            key: bool(pref_signals.get(key))
            for key in ("wants_scenic", "wants_food", "wants_cultural", "wants_shopping", "wants_hotel")
        }, ensure_ascii=False, sort_keys=True),
        personalization_hint,
        budget_mode,
        str(user_role),
        " ".join([str(m.get("intent", "")) for m in room_members if isinstance(m, dict)]),
        "prefs:" + ",".join(sorted(pref_signals.get("requirements", []))),
    ])

    async def cached_stream(cached_payload: dict):
        cb = cached_payload.get("budget_breakdown") or budget_breakdown_payload
        cfd = cached_payload.get("final_data") or {}
        ctc = cached_payload.get("target_city") or {}
        yield json.dumps({"type": "budget_breakdown", "payload": cb}, ensure_ascii=False) + "\n"
        yield json.dumps({"token": f"[缓存加速]: 命中【{target_city}】{trip_days} 天行程缓存，首屏 < 1s（跳过 LLM 推理）\n"}, ensure_ascii=False) + "\n"
        if ctc:
            yield json.dumps({"type": "target_city", "payload": ctc}, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "final_route", "payload": cfd}, ensure_ascii=False) + "\n"

    if not is_refinement:
        cached_hit = await _semantic_cache.lookup(target_city, trip_days, profile_text)
        if cached_hit:
            cached_route = ((cached_hit.get("final_data") or {}).get("route") if isinstance(cached_hit, dict) else None)
            try:
                from core.itinerary_balance import is_daily_balance_acceptable

                cache_ok = is_daily_balance_acceptable(cached_route or [], trip_days, target_daily_count)
            except Exception:
                cache_ok = False
            # 旧缓存只做了“每天有节点”校验，无法阻止整套路线被单一文化场馆占满。
            # 普通城市游默认需要景区 + 餐饮 + 人文/地标 + 住宿；只有用户明确要求
            # 只看博物馆/历史场馆时，才允许单一文化主题缓存继续复用。
            if cache_ok and isinstance(cached_route, list):
                cached_categories = {
                    classify_poi_category(
                        str(item.get("name") or item.get("location") or ""),
                        str(item.get("type") or ""),
                        bool(item.get("is_hotel")),
                    )
                    for item in cached_route
                    if isinstance(item, dict) and not item.get("is_hotel")
                }
                explicit_culture_only = bool(pref_signals.get("wants_cultural")) and not any(
                    pref_signals.get(key) for key in ("wants_food", "wants_scenic", "wants_hotel", "wants_shopping")
                )
                if not explicit_culture_only:
                    cache_ok = {"scenic", "food"}.issubset(cached_categories) and "cultural" in cached_categories
                    if cache_ok:
                        cached_by_day: Dict[int, set[str]] = {}
                        for item in cached_route:
                            if not isinstance(item, dict) or item.get("is_hotel"):
                                continue
                            try:
                                cached_day = int(item.get("day") or 1)
                            except (TypeError, ValueError):
                                cached_day = 1
                            cached_by_day.setdefault(cached_day, set()).add(
                                classify_poi_category(
                                    str(item.get("name") or item.get("location") or ""),
                                    str(item.get("type") or ""),
                                    bool(item.get("is_hotel")),
                                )
                            )
                        cache_ok = all(
                            {"scenic", "food"}.issubset(cached_by_day.get(day, set()))
                            for day in range(1, trip_days + 1)
                        )
            if cache_ok:
                print(f"✅ [语义缓存] {target_city}/{trip_days}天命中，秒回", flush=True)
                return StreamingResponse(cached_stream(cached_hit), media_type="application/x-ndjson")
            print(f"♻️ [语义缓存] {target_city}/{trip_days}天命中但密度或类别多样性不达标，跳过旧方案", flush=True)
        # Seed templates are intentionally limited to short cold-start trips.
        # Long/deep itineraries must use the supplier-backed candidate pool so
        # every requested day receives a balanced set of unique nodes.
        seed_fd = _build_seed_final_data(target_city, trip_days) if trip_days <= 3 and role != "深度探索" else None
        if seed_fd:
            seed_payload = {
                "final_data": seed_fd,
                "budget_breakdown": budget_breakdown_payload,
                "target_city": {"name": target_city, "lnglat": [float(x) for x in coord.split(",")] if coord and "," in coord else []},
            }
            print(f"🚀 [热门城市模板] {target_city} 命中预生成种子模板，首屏 < 1s", flush=True)
            return StreamingResponse(cached_stream(seed_payload), media_type="application/x-ndjson")

    async def event_stream():
        async def emit_reasoning(agent: str, action: str, claim: str, basis: str):
            """下发结构化推理步骤（前端回放）并写入 Redis 黑板存档 + 事件流。"""
            step = build_reasoning_step(agent, action, claim, basis)
            await blackboard.record_reasoning(f"room_{target_city}", step)
            await event_bus.emit(
                OMNI_EVENTS_STREAM, "reasoning_step", step, room_id,
                correlation_id=plan_correlation_id, producer="coordinator",
            )
            yield json.dumps({"type": "reasoning_step", "payload": step}, ensure_ascii=False) + "\n"

        yield json.dumps({"type": "budget_breakdown", "payload": budget_breakdown_payload}, ensure_ascii=False) + "\n"
        # 事件流：推演启动事件（emit 到全局 Redis Stream，供订阅方实时消费）
        await event_bus.emit(OMNI_EVENTS_STREAM, "negotiation_started", {
            "room_id": room_id, "user_id": user_id, "city": target_city,
            "days": trip_days, "budget_mode": budget_mode, "member_count": _member_count,
            "correlation_id": plan_correlation_id,
        }, room_id, correlation_id=plan_correlation_id, producer="coordinator")

        if is_luxury:
            yield json.dumps({"token": f"[地理精算 Agent]: 捕获到团队品质诉求，开启【臻选豪华】策略，正在为【{target_city}】排布完整 {trip_days} 天精品行程...\n"}, ensure_ascii=False) + "\n"
            async for _e in emit_reasoning("地理精算 Agent", "claim", f"开启【臻选豪华】策略，为【{target_city}】排布 {trip_days} 天精品行程", f"识别到品质诉求关键词，预算模式 HIGH_LUXURY，日均 ¥{round(total_calc_budget / trip_days)}"):
                yield _e
        else:
            yield json.dumps({"token": f"[地理精算 Agent]: 启动多智能体【帕累托均衡博弈】模型，正在为【{target_city}】全员排布完整 {trip_days} 天（每日 {target_daily_count} 节点）高共识度路线...\n"}, ensure_ascii=False) + "\n"
            async for _e in emit_reasoning("地理精算 Agent", "claim", f"启动【帕累托均衡博弈】，为【{target_city}】全员排布 {trip_days} 天高共识度路线", f"成员 {_member_count} 人，每日 {target_daily_count} 节点，预算模式 {budget_mode}"):
                yield _e

        await asyncio.sleep(0.1)

        if coord and "," in coord:
            try:
                lon, lat = coord.split(",")
                yield json.dumps({
                    "type": "target_city", 
                    "payload": {
                        "name": target_city,
                        "lnglat": [float(lon), float(lat)]
                    }
                }, ensure_ascii=False) + "\n"
            except Exception:
                pass

        coord_task = toolbox.get_coordinates(target_city)

        # 不再把所有景点类型塞进一次宽泛检索。高德在“景点|博物馆|文化地标”
        # 这类混合关键词下很容易返回大量博物馆，导致模型看到的候选池本身就失衡。
        # 分领域检索后再合并，保证自然景区、文化、人文商业和特色餐饮都有真实候选。
        # 候选供给诊断：记录每一类真实候选有多少、有没有补检索（随方案如实下发）
        pool_diagnostics: Dict[str, Any] = {}
        # geocode 结构化结果（province/city/district/level/center）：内层补检索往外层共用一个**可变字典**，
        # 而不是内层重新绑定一个局部名 —— 实测坑：内层 `_geocode = ...` 只改内层局部变量，
        # 外层硬约束过滤读它直接 NameError，还被兜底 except 吞掉（日志里什么都看不到）。
        geo_info: Dict[str, Any] = {}
        # 提前初始化：保底路线的 except 分支也要能引用它（否则异常早于下面的赋值时会 NameError）
        candidate_supply: Dict[str, Any] = {}
        # 行程配比目标（用户口径）：默认自然=人文各半并穿插美食/住宿；明确说了偏好就加权。
        # 取数与排布都按它走，最后再用 composition_audit 对账，不靠"看起来挺全"。
        composition = composition_targets(
            trip_days=trip_days,
            daily_nodes=target_daily_count,
            wants_scenic=bool(own_signals.get("wants_scenic")),
            wants_cultural=bool(own_signals.get("wants_cultural")),
            wants_food=bool(own_signals.get("wants_food")),
            exclude_cultural=bool(own_signals.get("exclude_cultural")),
            wants_hotel=bool(own_signals.get("wants_hotel")),
        )
        print(f"⚖️ [配比目标] {summarize_targets(composition)}", flush=True)
        # 目标配比写进提示词（软约束 + "不要为凑数硬塞"）——只靠候选池与事后对账补不回
        # "模型自己排偏"（实测默认那轮人文 2 vs 目标 4）。
        _scenic_only_flag = bool(own_signals.get("exclude_cultural")) and bool(own_signals.get("wants_scenic"))
        composition_hint = composition_hint_text(
            composition,
            park_cap=park_cap(
                trip_days, scenic_only=_scenic_only_flag, wants_park=bool(own_signals.get("wants_park"))
            ),
        )
        if (pref_signals.get("requirements") or []) != (own_signals.get("requirements") or []):
            print(
                f"   （房间/历史文本里另有偏好 {pref_signals.get('requirements')}，"
                f"它通过房间公平性参与平衡，不改你本人的默认口径）",
                flush=True,
            )

        # 👑 统一事实快照（提案阶段的输入契约）：用户说的 + 约束 + 目标配比，一次算好，
        #    后面每个专业智能体的输入契约里都带同一版 `facts_version` ——
        #    这样"多智能体协同"才有共同的讨论前提（此前各智能体拿到的约束是空的）。
        from core.plan_facts import build_request_facts, proposal_facts_alignment

        request_facts = build_request_facts(
            correlation_id=plan_correlation_id,
            city=target_city,
            trip_days=trip_days,
            intent=intent_str,
            signals=own_signals,
            composition=composition,
            budget_total=float(total_calc_budget) if total_calc_budget else None,
        )
        facts_version_string = str(request_facts.get("facts_version") or "")
        print(f"🧾 [统一事实] 快照 {facts_version_string}（约束 {len(request_facts.get('constraints') or {})} 项）", flush=True)

        async def _fetch_attraction_mix() -> List[Dict[str, Any]]:
            from core.route_profile import profile_to_retrieval_hints

            # 配额优先来自**参考画像**（仅当本轮采用了它，即用户没说清楚偏好时），
            # 否则按当轮诉求推断：想看自然就把自然放大、人文收成点缀；明确排除人文就不发这次检索。
            # 实测（2026-10-04）：固定配额下"想看自然景观"的诉求拿到的是 16/17 人文的池子。
            if reference_hint and reference_profile:
                # 用真实 poi_limit 重算（算 hint 时还不知道 poi_limit）
                limits = profile_to_retrieval_hints(reference_profile, poi_limit=poi_limit)["quota"]
            else:
                limits = attraction_query_limits(
                    poi_limit,
                    # 配额也按发起人自己的口径：模糊诉求就该自然/人文各半，
                    # 不能被房间占位意图带成"自然压倒人文"（实测事故，见 own_signals 处说明）
                    wants_scenic=bool(own_signals.get("wants_scenic")),
                    exclude_cultural=bool(own_signals.get("exclude_cultural")),
                )
            scenic_keywords = "自然风景|公园|天山|天池|草原|湖|峡谷|沙漠|绿洲|植物园|动物园|5A景区"
            if reference_hint and reference_hint["hints"].get("extra_scenic_keywords"):
                scenic_keywords = scenic_keywords + "|" + "|".join(reference_hint["hints"]["extra_scenic_keywords"])
            scenic_query = toolbox.get_dynamic_pois(
                target_city,
                keywords=scenic_keywords,
                types="110000",
                limit=limits["scenic"],
            )
            cultural_query = (
                toolbox.get_dynamic_pois(
                    target_city,
                    keywords="文化地标|历史古迹|博物馆|文庙|古城|遗址|非遗",
                    types="141200",
                    limit=limits["cultural"],
                )
                if limits["cultural"] > 0
                else asyncio.sleep(0, result=[])
            )
            shopping_query = toolbox.get_dynamic_pois(
                target_city,
                keywords="特色步行街|夜市|商圈|集市|老街|文创市集",
                types="060400|060100",
                limit=limits["shopping"],
            )
            scenic, cultural, shopping = await asyncio.gather(
                scenic_query, cultural_query, shopping_query, return_exceptions=True
            )
            # 高德对“关键词1|关键词2”偶尔会按名称相关性返回跨类 POI。
            # 例如自然查询混入博物馆，最终模型看到的候选池就会被文化场馆淹没。
            # 这里按查询意图做一次保守的类别隔离：明显属于另一类的记录丢弃，
            # 无法判断的记录保留，避免误删真实景点。
            def keep_for(result: Any, expected: str) -> List[Dict[str, Any]]:
                if not isinstance(result, list):
                    return []
                kept: List[Dict[str, Any]] = []
                for item in result:
                    if not isinstance(item, dict):
                        continue
                    actual = classify_poi_category(
                        str(item.get("name") or ""),
                        str(item.get("type") or ""),
                        bool(item.get("is_hotel")),
                    )
                    if expected == "scenic" and actual in {"cultural", "food", "hotel"}:
                        continue
                    if expected == "shopping" and actual in {"cultural", "food", "hotel"}:
                        continue
                    if expected == "cultural" and actual in {"food", "hotel", "scenic"}:
                        continue
                    item = dict(item)
                    item["planning_category"] = expected if actual == "other" else actual
                    if expected == "scenic":
                        # 自然景观口径（用户明确要求）：只认景区/有自然词的地方；
                        # 城市公园（孔雀公园/龙山公园…）单独一档，可以去但不顶自然景观名额。
                        item["scenic_tier"] = scenic_tier(item.get("name"), item.get("type"))
                    kept.append(item)
                return kept

            merged: List[Dict[str, Any]] = []
            merged.extend(keep_for(scenic, "scenic"))
            merged.extend(keep_for(cultural, "cultural"))
            merged.extend(keep_for(shopping, "shopping"))

            _counts = {"scenic": 0, "cultural": 0, "shopping": 0, "other": 0}
            for item in merged:
                _counts[str(item.get("planning_category") or "other")] = _counts.get(str(item.get("planning_category") or "other"), 0) + 1
            pool_diagnostics.update({
                "query_limits": {"scenic": limits["scenic"], "cultural": limits["cultural"], "shopping": limits["shopping"]},
                "by_category": _counts,
            })

            # 自然候选连"每天一个"都凑不齐 → 补一次更宽的检索（只补一次：高德有 QPS 限制）
            if needs_widening(_counts.get("scenic", 0), required_scenic_candidates(trip_days)):
                widen_result = await widen_candidates(
                    toolbox.get_dynamic_pois,
                    city=target_city,
                    keywords=SCENIC_WIDENING_KEYWORDS,
                    types="110000",
                    limit=max(int(limits["scenic"]), 30),
                    existing_names=[str(item.get("name") or "") for item in merged],
                )
                added = keep_for(widen_result["added"], "scenic")
                merged.extend(added)
                pool_diagnostics["widened"] = list(pool_diagnostics.get("widened") or []) + ["自然景观"]
                pool_diagnostics["widened_added"] = {"自然景观": len(added)}
                if widen_result.get("error"):
                    pool_diagnostics["widening_error"] = widen_result["error"]
                print(
                    f"🔎 [候选补检索] 自然候选只有 {_counts.get('scenic', 0)} 个 → 更宽检索补回 {len(added)} 个"
                    + (f"（错误：{widen_result['error']}）" if widen_result.get("error") else ""),
                    flush=True,
                )
            # 目的地是县级市时，真景区往往在**周边县市**：实测库尔勒市区检索只有 7 个市区公园
            # （全部 ≤5km），而博斯腾湖风景名胜区在 47km 外的博湖县、罗布人村寨在 75km 外的尉犁县。
            # 所以用 geocode 拿到的上级区域（库尔勒 → 巴音郭楞蒙古自治州）再补一次"景区专用关键词"
            # 检索，按行程长度限制距离（2 天 90km / 3-4 天 150km / 5 天以上 250km），
            # 并去掉同一景区的子景点（博斯腾湖的芦花港/阿洪口/码头只留主景区）。
            _neighbor_region = ""
            try:
                geo_info.update(await toolbox.get_geocode(target_city) or {})
                _neighbor_region = parent_region(target_city, geo_info)
            except Exception as _geo_exc:
                pool_diagnostics["neighbor_region_error"] = str(_geo_exc)[:120]

            if _neighbor_region:
                try:
                    _radius = scenic_radius_km(trip_days, str(own_signals.get("distance_tolerance") or "normal"))
                    # 市中心坐标取 geocode 的 center（"lng,lat"）；拿不到就让距离筛选整体放行
                    _center_text = str(geo_info.get("center") or "")
                    _center_for_radius: List[float] = []
                    if "," in _center_text:
                        try:
                            _lng_text, _, _lat_text = _center_text.partition(",")
                            _center_for_radius = [float(_lng_text), float(_lat_text)]
                        except ValueError:
                            _center_for_radius = []
                    _neighbor_raw = await toolbox.get_dynamic_pois(
                        _neighbor_region,
                        keywords=NEIGHBOR_SCENIC_KEYWORDS,
                        types="110000",
                        limit=max(20, int(limits["scenic"])),
                    )
                    _neighbor_scenic = keep_for(_neighbor_raw, "scenic")
                    # 只保留真景区（城市公园在市区已经有了，不必从州里再拉一遍）
                    _neighbor_scenic = [
                        item for item in _neighbor_scenic if str(item.get("scenic_tier") or "") == "landmark"
                    ]
                    _neighbor_near, _neighbor_far = filter_by_radius(_neighbor_scenic, _center_for_radius, _radius)
                    _neighbor_kept, _neighbor_merged = dedupe_sub_spots(_neighbor_near)
                    _seen_neighbor = {str(item.get("name") or "") for item in merged}
                    _neighbor_added = [
                        item for item in _neighbor_kept if str(item.get("name") or "") not in _seen_neighbor
                    ]
                    for item in _neighbor_added:
                        item["neighbor_region"] = _neighbor_region
                    merged.extend(_neighbor_added)
                    pool_diagnostics.update({
                        "neighbor_region": _neighbor_region,
                        "neighbor_radius_km": _radius,
                        "neighbor_found": len(_neighbor_scenic),
                        "neighbor_added": len(_neighbor_added),
                        "neighbor_too_far": _neighbor_far,
                        "neighbor_sub_spots_merged": _neighbor_merged,
                    })
                    print(
                        f"🗺️ [周边景区] {target_city} → {_neighbor_region}（半径 {_radius}km）："
                        f"真景区 {len(_neighbor_scenic)} 个，补入 {len(_neighbor_added)} 个，"
                        f"太远丢弃 {_neighbor_far} 个，同景区子景点合并 {_neighbor_merged} 个",
                        flush=True,
                    )
                except Exception as _neighbor_exc:
                    pool_diagnostics["neighbor_error"] = str(_neighbor_exc)[:120]
                    print(f"⚠️ [周边景区] 补检索失败（不影响主流程）: {_neighbor_exc}", flush=True)

            return merged

        attractions_task = _fetch_attraction_mix()
        # 美食/住宿**不能**套用"像不像玩点"过滤器（详见 get_dynamic_pois 里的实测说明）
        foods_task = toolbox.get_dynamic_pois(target_city, keywords="特色老字号|地方特色美食|正宗名吃|特色正餐|本地特色菜", types="050000", limit=poi_limit, play_worthy=False)
        hotels_task = toolbox.get_dynamic_pois(target_city, keywords="品质酒店|高端度假酒店|高分精品住宿", types="100000", limit=30, play_worthy=False)
        safety_task = wm_client.get_city_safety_intel(target_city)
        weather_task = toolbox.get_real_weather(target_city)
        traffic_task = toolbox.get_traffic_status(coord)  # 👑 免费平替：实时路况并入风控融合

        if current_mode == "pvp":
            web_price_task = toolbox.get_real_time_web_price(target_city, "快捷酒店与热门景区门票")
            city_center_coord, raw_attractions, raw_foods, raw_hotels, safety_intel, weather_data, traffic_data, web_price_intel = await asyncio.gather(
                coord_task, attractions_task, foods_task, hotels_task, safety_task, weather_task, traffic_task, web_price_task
            )
        else:
            city_center_coord, raw_attractions, raw_foods, raw_hotels, safety_intel, weather_data, traffic_data = await asyncio.gather(
                coord_task, attractions_task, foods_task, hotels_task, safety_task, weather_task, traffic_task
            )

        # 👑 免费平替 WorldMonitor：本地多源聚合风控（高德实时天气 + 实时交通 + 官方气象预警公开接口）动态融合 CII
        is_weekend = datetime.datetime.now().weekday() >= 5
        safety_intel = await wm_client.aggregate_city_risk(
            safety_intel, target_city,
            weather=weather_data, traffic=traffic_data, is_weekend=is_weekend
        )

        unique_foods = deduplicate_and_diversify_pois(raw_foods)
        unique_attractions = deduplicate_and_diversify_pois(raw_attractions)

        # 美食候选同样可能因为关键词太窄而只剩几条（实测库尔勒：生产关键词 3 条，
        # 通用兜底「美食|餐厅|小吃|本地菜」40 条）。下限定成"一天两顿"：
        # 库尔勒那种 3 条的情况必须补，否则模型只能自己编馆子（真实路线里出现过 0 个餐饮节点）。
        if needs_widening(len(unique_foods), required_food_candidates(trip_days)):
            _food_names = [str(item.get("name") or "") for item in unique_foods if isinstance(item, dict)]
            _food_widen = await widen_candidates(
                toolbox.get_dynamic_pois,
                city=target_city,
                keywords=FOOD_WIDENING_KEYWORDS,
                types="050000",
                limit=poi_limit,
                existing_names=_food_names,
                # 餐饮必须显式关掉"像不像玩点"过滤：默认 True 会把餐饮类别整类丢光，
                # 表现成"补了却一条都没加回来"（实测事故，见 widen_candidates 的说明）
                fetch_kwargs={"play_worthy": False},
            )
            _added_foods = _food_widen["added"]
            if _added_foods:
                unique_foods = list(unique_foods) + _added_foods
                pool_diagnostics["widened"] = list(pool_diagnostics.get("widened") or []) + ["美食"]
                pool_diagnostics.setdefault("widened_added", {})["美食"] = len(_added_foods)
                print(f"🔎 [候选补检索] 美食候选只有 {len(_food_names)} 个 → 更宽检索补回 {len(_added_foods)} 个", flush=True)
            if _food_widen.get("error"):
                pool_diagnostics["widening_error"] = _food_widen["error"]
                print(f"⚠️ [候选补检索] 美食补充检索失败（不影响主流程）: {_food_widen['error']}", flush=True)

        cleaned_hotels = []
        if isinstance(raw_hotels, list):
            for h in raw_hotels:
                if not isinstance(h, dict):
                    continue
                h_name = str(h.get("name") or "")
                if any(bad in h_name for bad in ["餐", "菜", "火锅", "酒楼", "宴", "小吃", "面", "茶", "油漆", "体验店", "手机"]):
                    continue
                cleaned_hotels.append(h)

        # Specialist runtime: these domain tasks run independently and return
        # validated proposals before the coordinator performs final route
        # selection. Empty supplier data is kept as a degraded proposal rather
        # than being replaced with fabricated facts.
        specialist_runtime_result = {"status": "degraded", "proposals": [], "completed": 0, "failed": 0, "degraded": 0, "parallel": True}
        try:
            from core.agent_runtime import build_pool_runtime
            from core.proposal_governance import apply_candidate_gates

            _runtime, _runtime_requests = build_pool_runtime(
                attractions=unique_attractions,
                transport=[traffic_data] if isinstance(traffic_data, dict) else [],
                lodging=cleaned_hotels,
                dining=unique_foods,
                risk=[safety_intel] if isinstance(safety_intel, dict) else [],
                budget=[],
                # 实测缺口（2026-10-04）：这几个参数此前没传，于是健康智能体拿不到每日节点数、
                # 住宿智能体拿不到夜数与预算 —— 它们在"提案分歧"里永远显示"未参与"，
                # 也就是**根本没参与博弈**。现在把手上真实的上下文传进去。
                profile=personalized_profile,
                weather=weather_data if isinstance(weather_data, dict) else {},
                daily_nodes=target_daily_count,
                nights=max(1, trip_days - 1),
                budget_total=float(total_calc_budget) if total_calc_budget else None,
                route=[],
                facts=request_facts,
            )
            specialist_runtime_result = await _runtime.execute(_runtime_requests)
            # 提案是否基于**同一版事实**：不基于就如实点名（每个提案都从输入契约里带 facts_version）
            _facts_alignment = proposal_facts_alignment(
                specialist_runtime_result.get("proposals"), facts_version_string
            )
            specialist_runtime_result["facts_version"] = facts_version_string
            specialist_runtime_result["facts_alignment"] = _facts_alignment
            if not _facts_alignment.get("aligned"):
                print(f"⚠️ [统一事实] 有提案不是基于同一版事实：{_facts_alignment.get('mismatched')}", flush=True)

            # 👑 提案分歧显式化（闭环第②–④段之间）：谁站在什么立场 / 谁和谁冲突 / 裁决凭什么。
            #    没有这一条，"多智能体协同"在审计上跟"并行各算一遍"没有区别。
            try:
                from core.proposal_conflicts import (
                    build_proposal_conflicts,
                    build_revision_directives,
                    evaluate_revisions,
                    should_continue_negotiation,
                )

                _conflict_facts = build_proposal_conflicts(
                    specialist_runtime_result.get("proposals"),
                    forbidden_categories=("cultural",) if own_signals.get("exclude_cultural") else (),
                    distance_tolerance=str(own_signals.get("distance_tolerance") or "normal"),
                    classify=classify_poi_category,
                    trip_days=trip_days,
                    budget_total=float(total_calc_budget) if total_calc_budget else None,
                    nights=max(1, trip_days - 1),
                )

                # 👑 协商回环（**有界多轮 + 收敛判断**）：把裁决变成相关智能体的新输入契约，
                #    让它们重提；然后看分歧有没有减少，决定要不要再来一轮。
                #    为什么要收敛判断：固定跑一轮的话，"分歧没变"和"分歧解决了"看起来一样；
                #    而无限重提又会拖长推演。所以：最多 NEGOTIATION_MAX_ROUNDS 轮，
                #    每轮记录 分歧 before→after，最后如实说明为什么停。
                NEGOTIATION_MAX_ROUNDS = 2
                _revision_rounds: List[Dict[str, Any]] = []
                _current_conflicts = list(_conflict_facts.get("conflicts") or [])
                if _current_conflicts:
                    try:
                        for _round_index in range(1, NEGOTIATION_MAX_ROUNDS + 1):
                            _directives = build_revision_directives(
                                _current_conflicts,
                                trip_days=trip_days,
                                forbidden_categories=("cultural",) if own_signals.get("exclude_cultural") else (),
                            )
                            if not _directives:
                                break
                            _revised: Dict[str, Any] = {}
                            _revised_pools: Dict[str, Any] = {}
                            for _agent_id, _directive in _directives.items():
                                _pool_by_domain = {
                                    "route-planner": unique_attractions,
                                    "dining-agent": unique_foods,
                                    "lodging-agent": cleaned_hotels,
                                }
                                _pool = _pool_by_domain.get(_agent_id)
                                if _pool is None:
                                    continue
                                _revision_runtime, _revision_requests = build_pool_runtime(
                                    attractions=_pool if _agent_id == "route-planner" else (),
                                    dining=_pool if _agent_id == "dining-agent" else (),
                                    lodging=_pool if _agent_id == "lodging-agent" else (),
                                    facts=request_facts,
                                )
                                # 只跑目标智能体，并在它的输入契约里加上裁决指令
                                _target_requests = [
                                    _req for _req in _revision_requests if _req.agent_id == _agent_id
                                ]
                                for _req in _target_requests:
                                    _req.hard_constraints.update(_directive.get("hard_constraints") or {})
                                    _req.context["revision_directive"] = str(_directive.get("message") or "")
                                    _req.context["revision_reason"] = str(_directive.get("reason") or "")
                                _revision_result = await _revision_runtime.execute(_target_requests)
                                for _proposal in _revision_result.get("proposals") or []:
                                    _revised[str(_proposal.get("agent_id"))] = _proposal
                                    if _agent_id == "route-planner":
                                        _revised_pools["attractions"] = [
                                            dict(item) for item in (_proposal.get("candidates") or []) if isinstance(item, dict)
                                        ]
                            if not _revised:
                                break
                            # 用重提结果覆盖原提案，并把这些候选真正用于后续方案
                            _merged_proposals = []
                            for _proposal in specialist_runtime_result.get("proposals") or []:
                                _agent_id = str(_proposal.get("agent_id") or "")
                                _merged_proposals.append(_revised.get(_agent_id, _proposal))
                            specialist_runtime_result["proposals"] = _merged_proposals
                            if _revised_pools.get("attractions"):
                                unique_attractions = _revised_pools["attractions"]
                            _after_facts = build_proposal_conflicts(
                                _merged_proposals,
                                forbidden_categories=("cultural",) if own_signals.get("exclude_cultural") else (),
                                distance_tolerance=str(own_signals.get("distance_tolerance") or "normal"),
                                classify=classify_poi_category,
                                trip_days=trip_days,
                                budget_total=float(total_calc_budget) if total_calc_budget else None,
                                nights=max(1, trip_days - 1),
                            )
                            _round_record = evaluate_revisions(
                                _current_conflicts,
                                _after_facts.get("conflicts"),
                                _revised,
                                round_index=_round_index,
                            )
                            _convergence = should_continue_negotiation(
                                _current_conflicts,
                                _after_facts.get("conflicts"),
                                round_index=_round_index,
                                max_rounds=NEGOTIATION_MAX_ROUNDS,
                            )
                            _round_record["convergence"] = _convergence
                            _revision_rounds.append(_round_record)
                            _current_conflicts = list(_after_facts.get("conflicts") or [])
                            print(
                                f"🔁 [协商回环] 第 {_round_index} 轮：重提 {_round_record['revised_agents']}；"
                                f"分歧 {_round_record['conflicts_before']} → {_round_record['conflicts_after']}"
                                f"｜{_convergence['reason']}",
                                flush=True,
                            )
                            for _item in _round_record.get("changed") or []:
                                print(
                                    f"   · {_item['agent_id']} 按裁决重提：{_item.get('applied')}",
                                    flush=True,
                                )
                            if not _convergence["continue"]:
                                break
                        _conflict_facts["conflicts"] = _current_conflicts
                        _conflict_facts["revision"] = {
                            "rounds": _revision_rounds,
                            "max_rounds": NEGOTIATION_MAX_ROUNDS,
                            "final_conflicts": _current_conflicts,
                            # 兼容旧字段（界面/事实里已有的取值）
                            "round": len(_revision_rounds),
                            "revised_agents": sorted({a for item in _revision_rounds for a in item.get("revised_agents") or []}),
                            "conflicts_before": _revision_rounds[0]["conflicts_before"] if _revision_rounds else [],
                            "conflicts_after": _current_conflicts,
                            "resolved": sorted({c for item in _revision_rounds for c in item.get("resolved") or []}),
                            "changed": [c for item in _revision_rounds for c in item.get("changed") or []][:4],
                            "notes": _revision_rounds[-1]["notes"] if _revision_rounds else [],
                            "convergence": (_revision_rounds[-1].get("convergence") if _revision_rounds else {}),
                        }
                        specialist_runtime_result["proposal_conflicts"] = _conflict_facts
                        if _current_conflicts:
                            print(
                                f"⚠️ [协商回环] {len(_revision_rounds)} 轮后仍有分歧：{_current_conflicts}"
                                f"（已交硬约束过滤兜底）",
                                flush=True,
                            )
                    except Exception as _revision_exc:  # 协商失败绝不影响出方案（硬约束层仍会兜住）
                        _conflict_facts["revision"] = {"error": str(_revision_exc)[:200]}
                        print(f"⚠️ [协商回环] 重提失败（不影响出方案）: {_revision_exc}", flush=True)

                specialist_runtime_result["proposal_conflicts"] = _conflict_facts
                _codes = [str(item.get("code")) for item in _conflict_facts.get("conflicts") or []]
                await event_bus.emit(OMNI_EVENTS_STREAM, "proposal.conflicts", {
                    "room_id": room_id,
                    "city": target_city,
                    "facts_version": facts_version_string,
                    "conflicts": _codes,
                    "conflict_count": len(_codes),
                    # 冲突细节（谁提了什么、差在哪）也进事件：否则审计只能看到"有冲突"，看不见冲突内容
                    "details": [
                        {
                            "code": str(item.get("code")),
                            "between": list(item.get("between") or []),
                            "detail": str(item.get("detail") or "")[:200],
                            "severity": str(item.get("severity") or ""),
                        }
                        for item in (_conflict_facts.get("conflicts") or [])[:5]
                    ],
                    "arbitration": [
                        {"code": str(item.get("code")), "mechanism": str(item.get("mechanism"))}
                        for item in (_conflict_facts.get("arbitration") or [])[:5]
                    ],
                    "inactive_agents": [str(item.get("agent_id")) for item in _conflict_facts.get("inactive") or []],
                    "correlation_id": plan_correlation_id,
                }, room_id, correlation_id=plan_correlation_id, producer="coordinator")
                print(
                    f"⚔️ [提案分歧] {len(_codes)} 条：{_codes or '无显式分歧'}"
                    + (
                        "；未参与：" + str([str(item.get("agent_id")) for item in _conflict_facts.get("inactive") or []])
                        if _conflict_facts.get("inactive")
                        else ""
                    ),
                    flush=True,
                )
                for _conflict in (_conflict_facts.get("conflicts") or [])[:3]:
                    print(f"   · {_conflict.get('code')}：{_conflict.get('detail')}", flush=True)
            except Exception as _conflict_exc:  # 分歧记录失败绝不影响出方案
                specialist_runtime_result["proposal_conflicts"] = {"error": str(_conflict_exc)[:200]}
            _gated = apply_candidate_gates(
                {"attractions": unique_attractions, "dining": unique_foods, "lodging": cleaned_hotels},
                specialist_runtime_result,
            )
            # Only contract-approved candidates enter the coordinator's
            # scoring pool. A degraded proposal leaves that domain empty and
            # is surfaced in the audit instead of silently becoming a fact.
            unique_attractions = _gated["pools"].get("attractions", [])
            unique_foods = _gated["pools"].get("dining", [])
            cleaned_hotels = _gated["pools"].get("lodging", [])
            specialist_runtime_result["gate_audits"] = _gated["audits"]
            # 事件流：**逐个智能体**提出候选（producer=该智能体），这是"地点从哪来"的源头。
            # 只发聚合数量，不逐条展开 —— 一次规划几十个候选会把流刷满，审计反而看不清。
            for _proposal in specialist_runtime_result.get("proposals") or []:
                if not isinstance(_proposal, Mapping):
                    continue
                await event_bus.emit(OMNI_EVENTS_STREAM, "candidate.proposed", {
                    "room_id": room_id, "city": target_city,
                    "domain": _proposal.get("domain"),
                    "status": _proposal.get("status"),
                    "candidates": len(_proposal.get("candidates") or []),
                    "correlation_id": plan_correlation_id,
                }, room_id, correlation_id=plan_correlation_id,
                    producer=str(_proposal.get("agent_id") or "coordinator"))
            # 事件流：候选被淘汰的**聚合口径**（gate audits 里只有计数与状态/原因，
            # 那就如实发计数，不编"某某因为什么被淘汰"的明细）
            for _pool, _audit in (specialist_runtime_result.get("gate_audits") or {}).items():
                if not isinstance(_audit, Mapping) or not _audit.get("rejected"):
                    continue
                await event_bus.emit(OMNI_EVENTS_STREAM, "candidate.rejected", {
                    "room_id": room_id, "city": target_city, "pool": _pool,
                    "accepted": _audit.get("accepted"),
                    "rejected": _audit.get("rejected"),
                    "status": _audit.get("status"),
                    "reason": _audit.get("reason"),
                    "correlation_id": plan_correlation_id,
                }, room_id, correlation_id=plan_correlation_id, producer="validator")
            await event_bus.emit(OMNI_EVENTS_STREAM, "specialist_proposals_ready", {
                "room_id": room_id,
                "city": target_city,
                "status": specialist_runtime_result.get("status"),
                "completed": specialist_runtime_result.get("completed", 0),
                "degraded": specialist_runtime_result.get("degraded", 0),
                "facts_version": facts_version_string,
                "facts_aligned": bool((specialist_runtime_result.get("facts_alignment") or {}).get("aligned")),
                "hard_constraints": sorted(
                    key for key in ((request_facts.get("constraints") or {}).keys())
                ),
            }, room_id, correlation_id=plan_correlation_id, producer="coordinator")
        except Exception as _runtime_error:
            specialist_runtime_result = {
                **specialist_runtime_result,
                "status": "degraded",
                "error": str(_runtime_error)[:240],
            }

        # 👑 候选池层面就剔除"用户明说不要"的类别：不让专业智能体拿到用户已经否掉的东西
        #    （此前只在候选桶/提示词层面清空，`unique_attractions` 里仍然留着人文 →
        #     提案里还带着博物馆，得靠后面的分歧检测 + 排除修复兜住）。
        #    分歧检测与排除修复都保留：它们现在是安全网，不是主路径。
        if own_signals.get("exclude_cultural") and unique_attractions:
            _before_exclusion = len(unique_attractions)
            unique_attractions = [
                item for item in unique_attractions
                if classify_poi_category(
                    str(item.get("name") or ""), str(item.get("type") or ""), bool(item.get("is_hotel"))
                ) != "cultural"
            ]
            _dropped_cultural = _before_exclusion - len(unique_attractions)
            if _dropped_cultural:
                pool_diagnostics["excluded_cultural_from_pool"] = _dropped_cultural
                print(f"🚫 [排除人文] 候选池层面剔除 {_dropped_cultural} 个人文候选（不让智能体再拿它提案）", flush=True)

        # 景点与餐饮都属于可执行的白天节点。此前只统计景点会在长行程中
        # 过早降低 actual_daily_count，导致后续日期只剩住宿。
        available_total = len(unique_attractions) + len(unique_foods)
        actual_daily_count = target_daily_count
        # 深度探索的“每天节点数”是用户可感知的硬期望，不能因为候选池偏小
        # 就静默改成另一个规划密度。保持目标值，让后续均衡门禁尽力补齐，
        # 并将真实候选缺口写入审计字段；普通模式仍允许适度降密度。
        if role != "深度探索" and available_total < trip_days * target_daily_count and available_total > 0:
            actual_daily_count = max(5 if role == "深度探索" else 3, available_total // trip_days)
            yield json.dumps({"token": f"[协商提示]: 已根据当地核心地标池精选每日 {actual_daily_count} 个核心游览节点！\n"}, ensure_ascii=False) + "\n"
            async for _e in emit_reasoning("时空调度体", "compromise", f"候选池仅 {available_total} 个核心地标，将每日节点由 {target_daily_count} 妥协下调至 {actual_daily_count} 个", "避免多天重复排布，保证每个节点真实可达"):
                yield _e
        elif role == "深度探索" and available_total < trip_days * target_daily_count:
            shortage = trip_days * target_daily_count - available_total
            yield json.dumps({
                "type": "candidate_shortage",
                "payload": {
                    "requested_daytime_nodes": trip_days * target_daily_count,
                    "available_daytime_candidates": available_total,
                    "shortage": shortage,
                    "message": "真实候选池不足，系统不会复制地点或编造地点；已尽力均衡到各天。",
                },
            }, ensure_ascii=False) + "\n"

        # 候选供给如实披露：这次真实候选够不够、有没有补检索、供应商有没有报错。
        # 不写清楚就是拿用户的信任换"看起来挺全"——所以既进 final_data，也进降级清单。
        _by_category = pool_diagnostics.get("by_category") or {}
        _widened_added = pool_diagnostics.get("widened_added") or {}
        candidate_supply = pool_supply_report(
            attractions=len(unique_attractions),
            foods=len(unique_foods),
            trip_days=trip_days,
            daily_nodes=target_daily_count,
            scenic=int(_by_category.get("scenic") or 0) + int(_widened_added.get("自然景观") or 0),
            widened=pool_diagnostics.get("widened") or [],
            provider_notes=[str(pool_diagnostics["widening_error"])] if pool_diagnostics.get("widening_error") else None,
        )
        candidate_supply["diagnostics"] = pool_diagnostics
        # 周边县市景区：它们离市区几十公里，必须让用户知道"要预留出行时间"，
        # 否则一个 76km 的景区被排进 2 天行程，用户会以为是市区的点。
        _out_of_town = [p for p in unique_attractions if isinstance(p, dict) and p.get("out_of_town")]
        candidate_supply["out_of_town_scenic"] = len(_out_of_town)
        candidate_supply["farthest_scenic_km"] = round(
            max([float(p.get("distance_km") or 0) for p in _out_of_town], default=0.0), 1
        )
        if candidate_supply.get("status") != "ok":
            print(f"📉 [候选供给] status={candidate_supply.get('status')} known={candidate_supply.get('known_daytime')}/{candidate_supply.get('needed_daytime')} {candidate_supply.get('notes')}", flush=True)

        fusion_signals = (safety_intel.get("fusion") or {}).get("signals") or []
        fusion_summary = "、".join(fusion_signals) if fusion_signals else "无显著风险增量"
        yield json.dumps({"token": f"[SafetyAgent/免费风控融合]: 本地多源聚合完成，【{target_city}】动态 CII {safety_intel.get('cii_score', 0)}（{safety_intel.get('risk_level', 'LOW')}），融合信号: {fusion_summary}\n"}, ensure_ascii=False) + "\n"
        async for _e in emit_reasoning("SafetyAgent", "decision", f"判定【{target_city}】动态 CII {safety_intel.get('cii_score', 0)}（{safety_intel.get('risk_level', 'LOW')}），行程按可通行编排", f"五维风险分解 + 融合信号: {fusion_summary}"):
            yield _e
        yield json.dumps({"type": "safety_info", "payload": safety_intel}, ensure_ascii=False) + "\n"

        yield json.dumps({"type": "weather_info", "payload": weather_data}, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "traffic_info", "payload": traffic_data}, ensure_ascii=False) + "\n"

        # 🌦️ 天气感知调度：雨天自动生成室内景点备选方案，并融入候选池实现智能调整
        weather_condition = str(weather_data.get("condition") or "")
        weather_advice = analyze_weather_for_planning(weather_condition)
        indoor_alternatives = []
        if weather_advice.get("prefer_indoor"):
            indoor_alternatives = await toolbox.get_indoor_alternatives(target_city)
            if indoor_alternatives:
                seen_names = {str(p.get("name")) for p in unique_attractions if isinstance(p, dict)}
                for ind in indoor_alternatives:
                    if isinstance(ind, dict) and str(ind.get("name")) not in seen_names:
                        unique_attractions.append(ind)
        yield json.dumps({
            "type": "weather_advice",
            "payload": {"condition": weather_condition, "advice": weather_advice, "indoor_alternatives": indoor_alternatives}
        }, ensure_ascii=False) + "\n"

        try:
            # 👑 A/B 差异化硬策略：treatment 组把画像维度比例映射为品类加成、消费倾向映射为 gamma 缩放，
            #    作用于候选 fitness 排序；control 组为空 dict / 1.0（均衡基线），产生真实可衡量的差异。
            profile_boost = build_profile_category_boost(personalized_profile)
            budget_gamma_scale = build_profile_budget_gamma(personalized_profile)
            fine_tag_boost = build_profile_fine_tag_boost(personalized_profile)
            if profile_boost:
                print(f"🎯 [A/B 画像加成] treatment 组生效，品类加成={profile_boost}", flush=True)
            if budget_gamma_scale != 1.0:
                print(f"💰 [A/B 消费倾向] treatment 组生效，gamma 缩放={budget_gamma_scale}", flush=True)
            if fine_tag_boost:
                print(f"🎯 [A/B 细标签加成] treatment 组生效，细标签加成={fine_tag_boost}", flush=True)
            fitness_calculator = TopologyFitnessCalculator(user_prefs, coord, evolution_memory, safety_intel=safety_intel, is_luxury=is_luxury, preference_signals=pref_signals, profile_boost=profile_boost, budget_gamma_scale=budget_gamma_scale, fine_tag_boost=fine_tag_boost)

            daytime_pois = unique_attractions + unique_foods
            for p in daytime_pois:
                if isinstance(p, dict):
                    p["fitness_score"] = fitness_calculator.calculate_fitness(p)
                    p["is_hotel"] = False

            for h in cleaned_hotels:
                if isinstance(h, dict):
                    h["fitness_score"] = fitness_calculator.calculate_fitness(h)
                    h["is_hotel"] = True

            pareto_result = NashEquilibriumSolver.resolve_conflicts(daytime_pois)
            pareto_daytime = pareto_result.get("frontier", [])
            # Pareto 前沿用于裁决与解释，但极端情况下前沿可能很小，无法覆盖
            # 长行程的每日节点容量。将被支配候选作为“可选补充池”保留，仍在
            # optimization_audit.rejected 中明确记录淘汰原因，避免路线退化。
            _min_daytime_pool = trip_days * actual_daily_count
            if len(pareto_daytime) < _min_daytime_pool:
                _normalize_candidate_name = lambda value: re.sub(r'[\\(（].*?[\\)）]', '', str(value or '')).strip()
                _frontier_names = {_normalize_candidate_name(p.get("name")) for p in pareto_daytime if isinstance(p, dict)}
                for _candidate in daytime_pois:
                    _name = _normalize_candidate_name(_candidate.get("name")) if isinstance(_candidate, dict) else ""
                    if _name and _name not in _frontier_names:
                        pareto_daytime.append(_candidate)
                        _frontier_names.add(_name)
                        if len(pareto_daytime) >= _min_daytime_pool:
                            break
            optimization_audit = {
                "method": "pareto_frontier",
                "objectives": pareto_result.get("objectives", []),
                "frontier_count": pareto_result.get("frontier_count", len(pareto_daytime)),
                "rejected_count": pareto_result.get("rejected_count", 0),
                "rejected": pareto_result.get("rejected", [])[:100],
            }
            route_optimizer = GraphRouteOptimizer(fitness_calculator)
            # 👑 空间排序先取完整顺序（不在此处过早截断），后续按类别均衡挑选，
            #    避免美食/文化类 POI 被「TSP 头部截断」整体挤出候选池
            spatial_daytime = route_optimizer.optimize_and_sort(pareto_daytime, max_nodes=max(len(pareto_daytime), 1))

            def _day_cat(p):
                return classify_poi_category(str(p.get('name') or ''), str(p.get('type') or ''), bool(p.get('is_hotel')))

            def _pool_cat(p):
                """候选池分桶用：把"城市公园"从自然景观里拆出来（口径见 core/route_composition.scenic_tier）。"""
                category = _day_cat(p)
                if category == "scenic" and str(p.get("scenic_tier") or "") != "landmark":
                    return "park"
                return category

            # 👑 酒店按适应度精选并限量：用户有住宿/高端诉求时高分酒店优先，但绝不
            #    让酒店挤占白天 POI 容量（此前 29 家酒店把候选池白天席位全部挤掉）
            sorted_hotels = sorted(cleaned_hotels, key=lambda x: float(x.get("fitness_score", 0) or 0), reverse=True)
            _hotel_cap = min(len(sorted_hotels), trip_days * 2 + 2)
            sorted_hotels = sorted_hotels[:_hotel_cap]

            # 酒店是每日额外的夜间节点，不应挤占白天候选容量。
            _top_limit = max(trip_days * actual_daily_count + len(sorted_hotels), 1)
            _day_capacity = max(_top_limit - len(sorted_hotels), 0)

            # 👑 分类均衡候选池：把白天 POI 按类别分桶，风景区/文化/美食/购物轮流各取 1 个，
            #    保证每类都有真实 POI 进入候选池与 LLM 提示词（美食/文化不再被整体截断）
            _day_buckets = {"scenic": [], "cultural": [], "food": [], "shopping": [], "park": [], "other": []}
            for p in spatial_daytime:
                if isinstance(p, dict):
                    _day_buckets.setdefault(_pool_cat(p), []).append(p)
            # 👑 类别下限兜底（真实缺口，2026-10-04 库尔勒实测）：候选桶建在**帕累托前沿**上，
            #    前沿会把餐饮整类挤掉 —— 餐饮候选已经有 50 个，最终 top 里却是 0 个，
            #    于是"按类别轮流取"的均衡逻辑手里根本没有餐饮可轮，方案长期没有正经吃饭安排。
            #    这里按**用户口径算出的目标配比**（默认自然=人文各半 + 穿插美食，见
            #    core/route_composition）从完整候选池补齐，只补到下限、不改变前沿取舍。
            _day_buckets, _topped_up = top_up_category_buckets(
                _day_buckets, daytime_pois, targets=composition, classify=lambda item: _pool_cat(item)
            )
            if _topped_up:
                pool_diagnostics["category_topup"] = _topped_up
                print(f"🧩 [候选均衡] 按目标配比补齐被前沿挤掉的类别：{_topped_up}", flush=True)
            # 👑 不走回头路：spatial_daytime 已按 TSP 空间最近邻串接，分桶后【不再按适应度重排】，
            #    保留桶内空间序，仅靠轮询交错维持类别多样性，避免「东拉西扯」式回头路写进 Prompt。

            # 👑 城市公园限量（用户口径：可以限量）。默认"每天最多 1 个"；
            #    用户明确"只想看自然"时 **0 个** —— 按他的定义公园不算自然景观，不该拿公园凑数。
            #    公园是顺路落脚点，不能占掉景区与吃饭的名额（真实事故：12 节点里 5 个是公园）。
            _scenic_only = bool(own_signals.get("exclude_cultural")) and bool(own_signals.get("wants_scenic"))
            _park_allowed = park_cap(
                trip_days, scenic_only=_scenic_only, wants_park=bool(own_signals.get("wants_park"))
            )
            if len(_day_buckets.get("park") or []) > _park_allowed:
                _park_dropped = len(_day_buckets["park"]) - _park_allowed
                _day_buckets["park"] = (_day_buckets["park"] or [])[:_park_allowed]
                pool_diagnostics["park_capped"] = {"allowed": _park_allowed, "dropped": _park_dropped}
                print(
                    f"🌳 [公园限量] 城市公园整趟最多 {_park_allowed} 个"
                    f"（{'你只看了自然，公园不算自然景观' if _scenic_only else ('你想逛公园，已放宽' if own_signals.get('wants_park') else '默认 1~2 个；想多逛公园可以直接说')}），"
                    f"本次候选里砍掉 {_park_dropped} 个",
                    flush=True,
                )
            # 用户明确排除人文时，候选池里也不留人文（不靠模型自觉）
            if own_signals.get("exclude_cultural") and _day_buckets.get("cultural"):
                pool_diagnostics["cultural_excluded"] = len(_day_buckets["cultural"])
                _day_buckets["cultural"] = []
                print("🚫 [排除人文] 按你的要求，候选池里的人文史迹已全部移除", flush=True)

            _interleaved = []
            # 自然景区与人文先取、美食次之，城市公园排在其后（用户口径：自然景观要景区，
            # 公园可以去但不该顶掉景区与吃饭的名额）
            _bucket_order = ["scenic", "cultural", "food", "park", "shopping", "other"]
            while len(_interleaved) < _day_capacity:
                _advanced = False
                for _c in _bucket_order:
                    if _day_buckets.get(_c):
                        _interleaved.append(_day_buckets[_c].pop(0))
                        _advanced = True
                        if len(_interleaved) >= _day_capacity:
                            break
                if not _advanced:
                    break

            top_candidates = _interleaved + sorted_hotels
            final_candidates = top_candidates

            # 写入 Redis 黑板
            await blackboard.write(f"room_{target_city}_context", {
                "traffic": traffic_data, 
                "weather": weather_data, 
                "safety": safety_intel, 
                "poi": final_candidates,
                "members": room_members
            })

            yield json.dumps({"token": f"[知识增强Agent]: 正在为【{target_city}】多成员检索候选地标的招牌特色与真实口碑...\n"}, ensure_ascii=False) + "\n"
            async for _e in emit_reasoning("知识增强Agent", "claim", f"为【{target_city}】多成员检索候选地标招牌特色与真实口碑", f"候选池 attractions={len(unique_attractions)} foods={len(unique_foods)} hotels={len(sorted_hotels)}，优先回填真实 POI 类型与图片"):
                yield _e

            from collections import Counter as _Cnt
            print(f"🔬 [候选池] attractions={len(unique_attractions)} foods={len(unique_foods)} hotels={len(sorted_hotels)} daytime={len(daytime_pois)} top={len(top_candidates)} 类别={dict(_Cnt(_day_cat(p) for p in top_candidates))}", flush=True)
            search_candidates = top_candidates[:8]
            enrich_tasks = [toolbox.enrich_poi_with_web_search(target_city, str(p.get('name', ''))) for p in search_candidates if isinstance(p, dict)]

            try:
                web_knowledge_list = await asyncio.wait_for(
                    asyncio.gather(*enrich_tasks, return_exceptions=True),
                    timeout=3.5
                )
            except Exception:
                web_knowledge_list = ["本地高人气打卡目的地，融入独特文化特色与地道风味体验。"] * len(search_candidates)

            poi_pool_data = []
            prompt_pool_data = []
            for i, p in enumerate(top_candidates):
                if not isinstance(p, dict):
                    continue
                lonlat = [0.0, 0.0]
                loc_val = p.get("location")
                if isinstance(loc_val, str) and "," in loc_val:
                    try:
                        parts = loc_val.split(",")
                        lonlat = [float(parts[0]), float(parts[1])]
                    except Exception:
                        pass
                elif isinstance(loc_val, (list, tuple)) and len(loc_val) >= 2:
                    try:
                        lonlat = [float(loc_val[0]), float(loc_val[1])]
                    except Exception:
                        pass

                web_info = web_knowledge_list[i] if i < len(web_knowledge_list) and isinstance(web_knowledge_list[i], str) else "本地特色文旅目的地。"
                p_cost_num = p.get('cost')
                p_type = str(p.get('type') or '')
                p_name = str(p.get('name') or '特色地标')

                if p_cost_num in (None, "", "未知", "暂无供应商数据"):
                    formatted_cost = "暂无供应商数据"
                elif p.get('is_hotel'):
                    formatted_cost = f"住宿预留 ¥{p_cost_num}/晚"
                elif any(k in p_type or k in p_name for k in ['餐', '美食', '肉', '面', '火锅', '包子', '三套车']):
                    formatted_cost = f"人均餐饮 ¥{p_cost_num}"
                elif str(p_cost_num) == '0' or '免费' in p_name:
                    formatted_cost = "免费游览"
                else:
                    formatted_cost = f"门票 ¥{p_cost_num}/人"

                # 保证生成干净的高德搜索外链
                clean_name_for_link = re.sub(r'[\(（].*?[\)）]', '', p_name).strip()
                encoded_query = urllib.parse.quote(f"{target_city} {clean_name_for_link}")
                amap_link = f"https://www.amap.com/search?query={encoded_query}"

                poi_pool_data.append({
                    'name': p_name, 
                    'rating': str(p.get('rating') or '暂无供应商数据'),
                    'lnglat': lonlat,
                    'type': p_type or '风景',
                    'cost_estimate': formatted_cost,
                    'photos': p.get('photos', []) if isinstance(p.get('photos'), list) else [],
                    'web_knowledge': str(web_info),
                    'is_hotel': bool(p.get("is_hotel", False)),
                    'planning_category': classify_poi_category(p_name, p_type, bool(p.get("is_hotel", False))),
                    # 优先保留 get_dynamic_pois 生成的精确坐标标点链接（uri.amap.com/marker），缺失时回退关键词搜索
                    'amap_url': str(p.get('amap_url') or amap_link),
                    'map_image': str(p.get('map_image') or ''),
                    'data_sources': p.get('data_sources') or {'rating': 'unavailable', 'cost_estimate': 'unavailable', 'open_time': 'unavailable'},
                    'estimated': bool(p.get('estimated', True)),
                    # 👥 景点拥挤度可视化数据（口碑热度+实时路况+周末因子）
                    'crowdedness': estimate_crowdedness(p.get('rating'), traffic_data.get('status_code', 'unknown'), is_weekend)
                })

                # 👑 裁剪版提示词池：去除图片外链与冗长百科，压缩 token，避免大模型输出被截断（5天只出4天）
                short_hint = str(web_info).strip()
                if len(short_hint) > 80:
                    short_hint = short_hint[:80] + "..."
                prompt_pool_data.append({
                    'name': p_name,
                    'rating': str(p.get('rating') or '暂无供应商数据'),
                    'lnglat': lonlat,
                    'type': p_type or '风景',
                    'cost_estimate': formatted_cost,
                    'is_hotel': bool(p.get("is_hotel", False)),
                    'planning_category': classify_poi_category(p_name, p_type, bool(p.get("is_hotel", False))),
                    'data_sources': p.get('data_sources') or {'rating': 'unavailable', 'cost_estimate': 'unavailable', 'open_time': 'unavailable'},
                    'estimated': bool(p.get('estimated', True)),
                    'hint': short_hint
                })

            yield json.dumps({"token": f"[时空调度体]: DBSCAN 空间降维与纳什博弈完成！正在交织生成覆盖完整 {trip_days} 天的高密路书...\n\n"}, ensure_ascii=False) + "\n"
            async for _e in emit_reasoning("时空调度体", "claim", f"DBSCAN 空间降维与纳什博弈收敛，交织生成 {trip_days} 天高密路书", "按地理邻近聚类节点，均衡景点/美食/住宿三类配比"):
                yield _e

            team_members_text = "\n".join([f"- 成员【{m.get('name', '游客')}】({m.get('role', '常规')}): 诉求「{m.get('intent', '随心探索')}」" for m in room_members])

            # 👑 用户核心诉求必达指令：把抽取到的偏好信号转化为 LLM 必须满足的硬约束
            _pref_flags = []
            if pref_signals.get("wants_hotel"):
                _pref_flags.append("每天必须安排 1 家酒店/特色民宿（夜间住宿节点 is_hotel=True，并给出 2 个备选酒店）" + ("，且优先高品质中高端住宿" if pref_signals.get("luxury_hotel") else ""))
            if pref_signals.get("wants_food"):
                _pref_flags.append("每天至少安排 1 顿当地特色美食/老字号正餐")
            if pref_signals.get("wants_scenic"):
                _pref_flags.append("必须包含自然风景区/公园类景点")
            if pref_signals.get("wants_cultural"):
                _pref_flags.append("必须包含博物馆/文化古迹类景点")
            if pref_signals.get("wants_shopping"):
                _pref_flags.append("应包含核心商圈/步行街购物体验")
            preference_instruction = ("；".join(_pref_flags) + "。") if _pref_flags else "每天均衡穿插风景区、特色美食与文化地标，并安排舒适住宿。"

            if budget_mode == "EXACT_AMOUNT":
                summary_desc = f"已按您的预算诉求【¥{total_calc_budget}】为【{target_city}】{trip_days}天行程精算排布：覆盖完整 {trip_days} 天，每日 {actual_daily_count} 个文旅节点，花销严格控制在预算内。"
            elif is_luxury:
                summary_desc = f"已为您开启【{target_city}】{trip_days}天团队臻选奢华行程，预估总消费{total_calc_budget}元：覆盖完整 {trip_days} 天，每日精细规划 {actual_daily_count} 个文旅节点，匹配核心商圈、特色正餐与高端度假住宿。"
            else:
                summary_desc = f"已为您匹配【{target_city}】{trip_days}天高性价比精算路线，预估总花销{total_calc_budget}元（日均约{round(total_calc_budget/trip_days)}元）：覆盖完整 {trip_days} 天，每日精细规划 {actual_daily_count} 个文旅节点，平衡美食打卡与文化慢游。"

            # 👑 构成策略 → 提示词硬规则：目标值由"目的地 + 偏好 + 二次增量"推导（不是写死的），
            # 让模型第一轮就按它排，而不是全靠事后 gate + 自动补点去救。
            try:
                from core.composition import composition_policy, composition_prompt_rules
                from core.increment import parse_increment as _parse_increment_for_prompt

                _prompt_policy = composition_policy(
                    {
                        "days": trip_days,
                        "city": target_city,
                        "request_text": intent_str,
                        "preferences": user_prefs if isinstance(user_prefs, dict) else {},
                    },
                    increment=_parse_increment_for_prompt(intent_str) if is_refinement else None,
                    signals=pref_signals if isinstance(pref_signals, dict) else None,
                )
                composition_rules_block = composition_prompt_rules(_prompt_policy)
            except Exception:
                composition_rules_block = ""

            # 👑 严格保证天数输出、每日 5 节点、真实花费与干净外链的 Prompt
            system_prompt = f"""
你是一个专业的多智能体团队旅行协同专家。当前任务：规划【{target_city}】完整【{trip_days}天】的深度团队行程。
【重要警告：绝对禁止越界！】你安排的所有景点、餐厅、酒店必须严格属于【{target_city}】！

同行房间成员画像与诉求 ({len(room_members)}人)：
{team_members_text}

【🔴 规则零：用户核心诉求必达（绝对铁律，最高优先级！）】
{preference_instruction} 上述每一项诉求都必须体现在整套行程中，缺失任何一项即为不合格输出！

【社区软引导参考（高赞行程，仅供借鉴，非硬约束）】:
{community_hint}

【个性化画像（A/B 实验注入，软约束）】:
{personalization_hint}

【本次目标配比（软约束，但**不要为凑数硬塞**）】:
{composition_hint}

【候选文旅 POI 底座数据 (已附带标准预估花销)】:
{json.dumps(prompt_pool_data, ensure_ascii=False)}

【🔴 规则一：天数绝对完整性（必须输出完整 Day 1 到 Day {trip_days} 所有天数，严禁偷懒只输出1天！）】
1. 本次规划要求输出完整【{trip_days}天】！你的 `route` 数组中必须包含从 `"day": 1`、`"day": 2` 直到 `"day": {trip_days}` 的所有节点！
2. 绝对不能中途截断！绝对不能只输出 Day 1！每天排布 {actual_daily_count} 个节点（上午首站景区 -> 上午第二景区 -> 午餐特色正餐 -> 下午深度文化打卡 -> 晚间特色正餐/夜市 -> 21:30 夜间住宿）！

【🔴 规则二：地点全局唯一去重（绝对铁律！）】
任何地点在整套 {trip_days} 天规划中【只能出现 1 次】！严禁单日重复，严禁跨天重复！严禁安排建材、涂料、手机店等非文旅场所！

【🔴 规则三：费用、评分和开放时间必须可追溯】
仅可使用候选底座或供应商返回的价格、评分和开放时间，并保留其来源。供应商缺失时必须输出“暂无供应商数据”，同时标记 `estimated: true`，严禁编造具体金额、评分、库存或营业时间。

【🔴 规则四：单日有且仅有 1 家夜间住宿酒店】
1. 每天最后一个节点（Night 21:30 后），安排【1 家】住宿酒店（is_hotel=True 的对象），并在其 `hotel_candidates` 数组中提供 2 个备选酒店对象（纯净名称与参考价格）。
2. 白天的景点与餐厅节点（08:00 - 20:00）严禁生成 `hotel_candidates` 字段！

【🔴 规则五：location 名称与 lnglat 必须照抄底座数据，photos/amap_url 一律留空】
1. 每个节点的 `location` 字段必须是底座数据中某个对象的 `name` 字段【逐字原样照抄】，严禁改写、缩写、意译！这是图片 100% 匹配的生命线，严禁编造底座中不存在的地点！
2. 每个节点的 `lnglat` 必须【逐字照抄】底座数据中该地点对应的坐标，严禁自行编造！
3. `photos` 必须输出空数组 `[]`，`amap_url` 必须输出空字符串 `""`（系统会在后台按名称回填真实图片与导航外链，你无需填写）！

【🔴 规则六：每日节点类型多样化（绝对铁律！风景区/美食/文化必须穿插，严禁只推一类！）】
1. 每天的 {actual_daily_count} 个节点中，必须同时包含【至少 1 个自然风景区/公园】+【至少 1 个特色美食/老字号餐厅】+【至少 1 个文化地标/博物馆/古迹】！严禁全天只安排博物馆或只安排餐厅！
2. 跨天必须轮换不同类型的景点：如果 Day 1 去了博物馆，Day 2 应优先安排风景区或商业街区，Day 3 再穿插文化古迹，确保每天体验新鲜感！
3. 午餐和晚餐节点必须从底座数据中 type 包含 "餐饮" 或 name 含美食关键词（如 火锅/面馆/老字号/特色菜/小吃/夜市）的对象中选取，严禁用景区名称充当餐饮节点！
4. 上午节点优先安排户外风景区（天气好时）或文化地标，下午可穿插博物馆/商业街/文创园，形成「上午户外 + 中午美食 + 下午文化 + 晚间夜市」的节奏感！
5. 【开放时间感知】博物馆/美术馆/科技馆/纪念馆等场馆普遍【周一闭馆】——若行程覆盖周一，必须把此类场馆安排到非周一日期，并在其 `open_time` 字段如实标注闭馆日（如 "周二-周日 09:00-17:00，周一闭馆"），严禁在周一安排此类场馆。

{composition_rules_block}

[FINAL_JSON]
{{
  "status": "consensus_reached",
  "negotiation_summary": "{summary_desc}",
  "route": [
    {{
      "day": 1,
      "time": "Day 1 | 08:30 - 10:30 (建议游玩 2 小时)",
      "location": "某景点",
      "lnglat": [102.63, 37.93],
      "desc": "基于底座真实数据的一两句精炼介绍",
      "cost_estimate": "暂无供应商数据",
      "tags": ["地标", "文化"],
      "photos": [],
      "amap_url": "",
      "split_info": "",
      "merge_point": false,
      "is_hotel": false
    }}
  ]
}}
"""
        except Exception as e:
            print(f"🔥 [算法计算崩溃 Traceback]:")
            traceback.print_exc()
            yield json.dumps({"token": f"\n[系统异常] 算法引擎计算失败: {str(e)}\n[FINAL_JSON]\n{{\"status\": \"error\", \"negotiation_summary\": \"算法计算异常\", \"route\": []}}\n"}, ensure_ascii=False) + "\n"
            return

        try:
            full_response = ""

            # —— 公共小工具：名字归一化 ——
            def _norm(n):
                if not isinstance(n, str):
                    return ""
                return re.sub(r'[\(（].*?[\)）]', '', n).strip()

            # —— 公共小工具：按坐标生成高德静态地图URL（保底图片，永不空白）——
            def _make_static_map(lnglat, size: str = "720*480", zoom: int = 15):
                if not (isinstance(lnglat, list) and len(lnglat) >= 2):
                    return ""
                try:
                    lng, lat = float(lnglat[0]), float(lnglat[1])
                except (TypeError, ValueError):
                    return ""
                if lng == 0.0 and lat == 0.0:
                    return ""
                if not self.amap_key:
                    return ""
                loc_str = f"{lng},{lat}"
                return (
                    f"https://restapi.amap.com/v3/staticmap"
                    f"?location={loc_str}&zoom={zoom}&size={size}&scale=2"
                    f"&markers=mid,0xFF5722,A:{loc_str}"
                    f"&key={self.amap_key}"
                )

            # —— 👑 候选池合成兜底：LLM 彻底失败时仍能给前端一份完整路线（防死锁）——
            # 实景图补齐的收集区（Post-Route 里逐节点收集，最后统一并发查询 + 兜底图）
            _photo_targets: List[Dict[str, Any]] = []
            def _synthesize_from_pool(reason: str = ""):
                """从 poi_pool_data 确定性合成完整 trip_days 天 * actual_daily_count 节点路线。"""
                if not poi_pool_data:
                    return [], f"AI 服务异常且无候选池数据：{reason}"
                import random as _rnd
                _rnd.seed(42)  # 确定性，避免每次闪烁
                pool = [p for p in poi_pool_data if isinstance(p, dict) and p.get("name") and p.get("lnglat")]
                hotels = [p for p in pool if p.get("is_hotel")]
                others = [p for p in pool if not p.get("is_hotel")]
                _rnd.shuffle(others)
                time_slots = ["08:30", "10:30", "12:30", "14:30", "17:30", "21:30"]
                slot_tags = [["地标","文化"], ["风景","出片"], ["餐饮","老字号"], ["文化","深度"], ["美食","夜市"], ["住宿"]]
                syn = []
                needed = trip_days * (actual_daily_count + 1)
                taken_names = set()
                for d in range(1, trip_days + 1):
                    day_nodes = []
                    for slot_i in range(actual_daily_count):
                        chosen = None
                        while others:
                            cand = others.pop(0)
                            nm = _norm(str(cand.get("name") or ""))
                            if nm and nm not in taken_names:
                                chosen = cand
                                taken_names.add(nm)
                                break
                        if not chosen:
                            break
                        slot_label = time_slots[min(slot_i, len(time_slots)-2)]
                        _c_lnglat = chosen.get("lnglat") or [0.0, 0.0]
                        _c_photos = chosen.get("photos") if isinstance(chosen.get("photos"), list) else []
                        _c_map = chosen.get("map_image") or _make_static_map(_c_lnglat)
                        day_nodes.append({
                            "day": d,
                            "time": f"Day {d} | {slot_label} (建议游玩 1.5 小时)",
                            "location": chosen.get("name"),
                            "name": chosen.get("name"),
                            "lnglat": _c_lnglat,
                            "type": chosen.get("type") or "风景",
                            "desc": chosen.get("web_knowledge") or f"{target_city}本地特色文旅目的地。",
                            "transport": "自驾/打车",
                            "tags": slot_tags[min(slot_i, len(slot_tags)-2)],
                            "cost_estimate": chosen.get("cost_estimate") or "暂无供应商数据",
                            "photos": _c_photos,
                            "amap_url": chosen.get("amap_url") or "",
                            "map_image": _c_map,
                            "rating": chosen.get("rating") or "暂无供应商数据",
                            "open_time": chosen.get("open_time") or "暂无供应商数据",
                            "is_hotel": False,
                            "split_info": "",
                            "merge_point": False,
                            "trust_reason": "智能体降级合成 · 基于真实候选池数据",
                        })
                    hotel = None
                    for h in hotels:
                        nm = _norm(str(h.get("name") or ""))
                        if nm and nm not in taken_names:
                            hotel = h
                            taken_names.add(nm)
                            break
                    if hotel:
                        _h_lnglat = hotel.get("lnglat") or [0.0, 0.0]
                        _h_photos = hotel.get("photos") if isinstance(hotel.get("photos"), list) else []
                        _h_map = hotel.get("map_image") or _make_static_map(_h_lnglat)
                        day_nodes.append({
                            "day": d,
                            "time": f"Day {d} | 21:30 入住",
                            "location": hotel.get("name"),
                            "name": hotel.get("name"),
                            "lnglat": _h_lnglat,
                            "type": "酒店住宿",
                            "desc": f"{target_city}精选住宿，交通便利。",
                            "transport": "自驾/打车",
                            "tags": ["住宿"],
                            "cost_estimate": hotel.get("cost_estimate") or "暂无供应商数据",
                            "photos": _h_photos,
                            "amap_url": hotel.get("amap_url") or "",
                            "map_image": _h_map,
                            "rating": hotel.get("rating") or "暂无供应商数据",
                            "open_time": hotel.get("open_time") or "暂无供应商数据",
                            "is_hotel": True,
                            "split_info": "",
                            "merge_point": False,
                            "trust_reason": "智能体降级合成 · 候选池酒店兜底",
                        })
                    syn.extend(day_nodes)
                    if len(syn) >= needed:
                        break
                summary = f"⚠️ 大模型服务暂时不可用（{reason or '网络连接失败'}），已基于高德候选池自动合成 {trip_days} 天保底路线，可直接浏览或点击「换一换」重试生成更高质量方案。"
                return syn[:needed], summary

            # 👑 天数补全器：主推演若遗漏某几天，则二次请求补齐，保证 5 天就是 5 天
            async def _complete_missing_days(missing_days):
                if not missing_days:
                    return []
                days_str = "、".join([f"Day {d}" for d in missing_days])
                prompt = (
                    f"上一轮已生成部分行程，但遗漏了 {days_str}。"
                    f"请【只输出】这几个缺失天数（{days_str}）的行程节点，"
                    f"每个节点的 location 与 lnglat 必须【逐字照抄】底座数据中对应对象的 name 与坐标（这是图片100%匹配的生命线），"
                    f"photos 输出 []、amap_url 输出 \"\"，"
                    f"地点不得与已有天数重复，并严格按照底座数据中的真实花费输出 cost_estimate。"
                    f"请直接输出一个 JSON 数组（数组内为这些缺失天数的完整节点对象）。"
                )
                resp = await client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=llm_cfg.temperature(),
                    max_tokens=llm_cfg.max_tokens(),
                )
                content = (resp.choices[0].message.content if resp.choices else "") or ""
                arr_match = re.search(r'\[[\s\S]*\]', content)
                if arr_match:
                    try:
                        arr_data = json.loads(arr_match.group(0))
                        if isinstance(arr_data, list):
                            return arr_data
                    except Exception:
                        pass
                obj = safe_repair_and_parse_json(content)
                if isinstance(obj, dict) and isinstance(obj.get("route"), list):
                    return obj["route"]
                return []

            # 👑 主推演：带重试 + 阶段进度日志，防止 LLM 网络抖动/长时间无响应造成前端卡死
            llm_call_succeeded = False
            last_llm_err: Optional[Exception] = None
            for attempt in range(LLM_MAX_RETRIES + 1):
                try:
                    if attempt > 0:
                        wait_s = min(2 ** attempt, 6)
                        yield json.dumps({"token": f"\n[连接自愈]: 第 {attempt}/{LLM_MAX_RETRIES} 次重试（等待 {wait_s}s）...\n"}, ensure_ascii=False) + "\n"
                        await asyncio.sleep(wait_s)
                    # 每次尝试前重置 full_response，避免前次失败残留干扰解析
                    full_response = ""
                    # 连接前提示，让日志实时反映阶段切换
                    yield json.dumps({"token": f"\n[语言模型]: 正在接入 {MODEL_NAME} 进行多智能体协同推理（attempt {attempt+1}/{LLM_MAX_RETRIES+1}）...\n"}, ensure_ascii=False) + "\n"
                    await asyncio.sleep(0)

                    response = await client.chat.completions.create(
                        model=MODEL_NAME,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"请严格根据约束，为我生成【{target_city}】完整 {trip_days} 天（Day 1 到 Day {trip_days} 所有天数全部输出，每天 {actual_daily_count} 个节点且含夜间住宿，每个节点必须标注具体的预估花费 cost_estimate，全程地点绝对不重复，每天必须穿插风景区+美食+文化地标，严禁只推博物馆或只推餐厅）的团队协同行程 JSON。"}
                        ],
                        stream=True,
                        temperature=llm_temperature,
                        max_tokens=llm_cfg.max_tokens(long_output=True)
                    )

                    yield json.dumps({"token": "[神经链路已建立] 开始接收流式推演 tokens...\n"}, ensure_ascii=False) + "\n"
                    token_count = 0
                    async for chunk in response:
                        if chunk.choices and len(chunk.choices) > 0:
                            delta = chunk.choices[0].delta
                            if delta and delta.content:
                                token = delta.content
                                full_response += token
                                token_count += 1
                                yield json.dumps({"token": token}, ensure_ascii=False) + "\n"
                                # 长生成中定期给出生成进度
                                if token_count % 600 == 0:
                                    yield json.dumps({"token": f"\n[生成进度] 已输出 {token_count} tokens，正在编织完整 {trip_days} 天行程...\n"}, ensure_ascii=False) + "\n"
                    llm_call_succeeded = True
                    break
                except (BadRequestError,) as e:
                    # 平台侧业务错误（如欠费/参数错）：不重试
                    last_llm_err = e
                    print(f"⚠️ [LLM 业务错误]: {e}", flush=True)
                    yield json.dumps({"token": f"\n[系统告警] LLM 平台拒绝服务: {str(e)[:120]}\n"}, ensure_ascii=False) + "\n"
                    break
                except Exception as e:
                    last_llm_err = e
                    err_name = type(e).__name__
                    print(f"⚠️ [LLM 第 {attempt+1} 次调用失败] {err_name}: {e}", flush=True)
                    yield json.dumps({"token": f"\n[连接自愈]: LLM 调用异常（{err_name}），正在重连...\n"}, ensure_ascii=False) + "\n"
                    continue

            if not llm_call_succeeded:
                # 所有重试都失败：显式错误 + 候选池兜底路线，保证前端 100% 收到 final_route
                err_msg = str(last_llm_err) if last_llm_err else "未知错误"
                friendly = (f"AI 大模型服务暂时不可达（{type(last_llm_err).__name__ if last_llm_err else 'Error'}），"
                            f"系统已自动切换至【候选池保底模式】：路线基于高德真实 POI 合成，图片/导航/花费均可正常使用。"
                            f"可稍后点击「换一换」重试高质量智能体推演。")
                print(f"🔥 [LLM 彻底失败] {err_msg}，切换至候选池兜底", flush=True)
                yield json.dumps({"type": "error", "payload": friendly}, ensure_ascii=False) + "\n"
                yield json.dumps({"token": f"\n[系统兜底] {friendly}\n"}, ensure_ascii=False) + "\n"

                syn_routes, syn_summary = _synthesize_from_pool(err_msg[:80])
                try:
                    from core.itinerary_balance import enforce_category_mix

                    _culture_only_fallback = bool(pref_signals.get("wants_cultural")) and not any(
                        pref_signals.get(key) for key in ("wants_food", "wants_scenic", "wants_hotel", "wants_shopping")
                    )
                    if not _culture_only_fallback:
                        _fallback_mix = enforce_category_mix(
                            syn_routes,
                            poi_pool_data,
                            days=trip_days,
                            target_daytime=target_daily_count,
                            required_categories=("scenic", "food") if pref_signals.get("exclude_cultural") else ("scenic", "food", "cultural"),
                            max_cultural_share=0 if pref_signals.get("exclude_cultural") else 0.4,
                        )
                        syn_routes = _fallback_mix["route"]
                        syn_summary = f"{syn_summary} 已执行景区、餐饮、人文类别均衡。"
                except Exception as _fallback_mix_exc:
                    print(f"⚠️ [兜底类别均衡] 失败，保留候选池顺序: {_fallback_mix_exc}", flush=True)
                final_data = {
                    "status": "degraded_fallback",
                    "negotiation_summary": syn_summary,
                    # team_satisfaction is filled in from the Monte-Carlo simulation
                    # below. It used to be a flat literal 88 for every member,
                    # which is a fabricated score rather than an estimate.
                    "route": syn_routes,
                    "optimization_audit": optimization_audit,
                    "fairness": fairness_report(syn_routes, room_members),
                }
                # 保底路线同样给出不确定度报告，而不是让前端在降级时丢失该信息。
                # 注意两侧都要兜住：这段是"最后的兜底"，它自己再抛异常就等于用户什么都拿不到
                # （实测 2026-09-19：这里引用了不存在的 `weather_info` → NameError → 流直接断，
                #  前端只能显示"推演超时"，而后端日志里明明写了"已切换候选池保底模式"）。
                try:
                    final_data["simulation"] = _simulate_final_plan(
                        final_data,
                        room_members=room_members,
                        budget=total_calc_budget,
                        weather=weather_data,
                    )
                    computed_satisfaction = _satisfaction_from_simulation(final_data["simulation"])
                    if computed_satisfaction:
                        final_data["team_satisfaction"] = computed_satisfaction
                except Exception as _fallback_sim_exc:
                    print(f"⚠️ [兜底] 不确定度报告生成失败，仍下发保底路线: {_fallback_sim_exc}", flush=True)
                # 直接发送 final_route，跳过 Post-Route 中依赖 LLM 输出的回填逻辑（保底路线已经带齐所有字段）
                final_data["correlation_id"] = plan_correlation_id
                # 保底路线也必须说清"这轮有没有用上历史反馈"：否则用户只看到结果变了，
                # 却不知道是画像起作用还是模型随机性（见 core/travel_utils.build_profile_applied）
                final_data["profile_applied"] = build_profile_applied(personalized_profile)
                # 保底路线正是"候选池合成"的，供给情况更要如实说（它是这版方案的全部来源）
                if candidate_supply:
                    final_data["candidate_supply"] = candidate_supply
                if reference_hint:
                    # 保底路线同样要披露画像依据（它也是按这份画像去候选池里挑的）
                    final_data["reference_profile"] = {
                        "applied": True,
                        "reference_count": reference_hint.get("reference_count"),
                        "reasons": reference_hint["hints"].get("reasons") or [],
                        "disclosure": reference_hint["disclosure"],
                    }
                yield json.dumps({"type": "final_route", "payload": final_data}, ensure_ascii=False) + "\n"
                # 保底路线也必须进事件流：否则审计里这次规划只有"开始"、没有"结束"，
                # 看起来像服务挂了（真实原因是走了候选池保底）。用 fallback=True 如实标注。
                await event_bus.emit(OMNI_EVENTS_STREAM, "final_route_ready", {
                    "room_id": room_id, "user_id": user_id, "city": target_city,
                    "variants": len(final_data.get("plan_variants", [])),
                    "fallback": True,
                    "status": final_data.get("status"),
                    "correlation_id": plan_correlation_id,
                }, room_id, correlation_id=plan_correlation_id, producer="coordinator")
                print(f"🏆 [兜底完成] 已下发 {len(syn_routes)} 个合成节点", flush=True)
                return

            # 👑 Post-Route：回填真实坐标/图片/外链 + 天数补全 + P2P 精准路网计算
            try:
                def _norm_name(n):
                    if not isinstance(n, str):
                        return ""
                    return re.sub(r'[\(（].*?[\)）]', '', n).strip()

                pool_by_name = {}
                for entry in poi_pool_data:
                    if isinstance(entry, dict) and entry.get("name"):
                        pool_by_name[_norm_name(entry["name"])] = entry

                def _haversine_m(lng1, lat1, lng2, lat2):
                    import math
                    R = 6371000.0
                    p1, p2 = math.radians(lat1), math.radians(lat2)
                    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
                    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
                    return 2 * R * math.asin(math.sqrt(a))

                def _find_match_by_coord(r_lnglat):
                    # 👑 坐标最近邻匹配：以 lnglat 为确定性主键，杜绝 LLM 名称漂移导致的假图/张冠李戴
                    if not (isinstance(r_lnglat, list) and len(r_lnglat) >= 2):
                        return None
                    try:
                        lng, lat = float(r_lnglat[0]), float(r_lnglat[1])
                    except (TypeError, ValueError):
                        return None
                    if lng == 0.0 and lat == 0.0:
                        return None
                    best, best_d = None, float("inf")
                    for entry in poi_pool_data:
                        if not isinstance(entry, dict):
                            continue
                        ec = entry.get("lnglat")
                        if not (isinstance(ec, list) and len(ec) >= 2):
                            continue
                        try:
                            elng, elat = float(ec[0]), float(ec[1])
                        except (TypeError, ValueError):
                            continue
                        if elng == 0.0 and elat == 0.0:
                            continue
                        d = _haversine_m(lng, lat, elng, elat)
                        if d < best_d:
                            best_d, best = d, entry
                    return best if (best is not None and best_d < 1500) else None

                # 👑 图片匹配度验证机制全局状态
                rebind_stats = {"matched": 0, "swapped": 0}
                used_pool_names: set = set()

                def _name_similarity(a: str, b: str) -> float:
                    if not a or not b:
                        return 0.0
                    return difflib.SequenceMatcher(None, a, b).ratio()

                def _fill_from_match(r, match):
                    """用候选池真实 POI 数据整体覆盖节点：名称/坐标/实景图/静态地图 100% 来自高德真实数据源"""
                    _mn = str(match.get("name") or "")
                    if _mn:
                        # 展示名称与图片 POI 强制对齐，杜绝「标题 A、图片 B」的张冠李戴
                        r["location"] = _mn
                        r["name"] = _mn
                    if isinstance(match.get("lnglat"), list) and len(match["lnglat"]) >= 2:
                        r["lnglat"] = match["lnglat"]
                    # 👑 用候选池真实 POI 的 type/is_hotel 覆盖 LLM 幻觉标注，杜绝「景点/餐厅被误标为酒店」
                    r["type"] = match.get("type") or r.get("type")
                    r["is_hotel"] = bool(match.get("is_hotel", False))
                    r["photos"] = match.get("photos") or []
                    r["amap_url"] = match.get("amap_url") or ""
                    r["map_image"] = match.get("map_image") or ""
                    r["rating"] = match.get("rating") or "暂无供应商数据"
                    r["open_time"] = match.get("open_time") or "暂无供应商数据"
                    r["cost_estimate"] = match.get("cost_estimate") or "暂无供应商数据"
                    r["data_sources"] = match.get("data_sources") or {"rating": "unavailable", "cost_estimate": "unavailable", "open_time": "unavailable"}
                    r["estimated"] = bool(match.get("estimated", True))
                    r["address"] = match.get("address") or r.get("address") or ""
                    if match.get("crowdedness"):
                        r["crowdedness"] = match.get("crowdedness")
                    # 👑 终极图片保证：即使候选POI没有预生成静态图，也按坐标现场生成，确保map_image永不为空
                    if not r.get("map_image") and isinstance(r.get("lnglat"), list) and len(r["lnglat"]) >= 2:
                        r["map_image"] = _make_static_map(r["lnglat"])
                    # 👑 ③ 实景图补齐：高德按**节点原名**再查一次，把官方实景图取回来
                    # （最多 8 个节点、并发 + 整体超时；取不到就用上一段的卫星影像兜底）
                    try:
                        if not has_real_photo(r):
                            _photo_targets.append(r)
                    except Exception:
                        pass
                    used_pool_names.add(_norm_name(str(match.get("name") or "")))

                def _rebind(route_nodes):
                    for r in route_nodes:
                        if not isinstance(r, dict):
                            continue
                        loc_name = _norm_name(str(r.get("location") or r.get("name") or ""))

                        # 👑 匹配度验证（三层递进，杜绝张冠李戴与空白图）：
                        # 第 1 层：精确名称匹配（LLM 逐字照抄底座名称时 O(1) 命中，相似度 1.0）
                        match = pool_by_name.get(loc_name)
                        if match:
                            _fill_from_match(r, match)
                            rebind_stats["matched"] += 1
                            continue

                        # 第 2 层：模糊名称匹配（difflib 相似度 > 0.6 才接受）
                        best_entry, best_ratio = None, 0.6
                        for pname, entry in pool_by_name.items():
                            if pname in used_pool_names:
                                continue
                            ratio = _name_similarity(loc_name, pname)
                            if ratio > best_ratio:
                                best_ratio = ratio
                                best_entry = entry
                        if best_entry:
                            _fill_from_match(r, best_entry)
                            rebind_stats["matched"] += 1
                            continue

                        # 第 3 层：坐标最近邻（<1.5km）——名称确认失败后的最后手段
                        coord_match = _find_match_by_coord(r.get("lnglat"))
                        if coord_match:
                            _fill_from_match(r, coord_match)
                            rebind_stats["matched"] += 1
                            continue

                        # 👑 匹配度校验失败：用未使用的候选池真实 POI 整体置换节点，
                        # 彻底消除「LLM 幻觉名称/坐标 → 假图/空白图」这一根因
                        fallback = next(
                            (p for p in poi_pool_data
                             if isinstance(p, dict) and p.get("name")
                             and _norm_name(str(p.get("name"))) not in used_pool_names),
                            None
                        )
                        if fallback:
                            r["location"] = fallback.get("name")
                            r["name"] = fallback.get("name")
                            r["type"] = fallback.get("type") or r.get("type")
                            r["desc"] = fallback.get("web_knowledge") or r.get("desc") or "本地高人气文旅目的地。"
                            r["cost_estimate"] = fallback.get("cost_estimate") or r.get("cost_estimate") or "暂无供应商数据"
                            r["is_hotel"] = bool(fallback.get("is_hotel", False))
                            r["trust_reason"] = "匹配度校验：未识别地点已置换为候选池真实 POI（100% 匹配）"
                            _fill_from_match(r, fallback)
                            rebind_stats["swapped"] += 1
                        else:
                            # 👑 候选池耗尽：保留原节点（保留LLM原名称），但强制按坐标生成静态地图兜底
                            if not r.get("amap_url") and loc_name:
                                r["amap_url"] = f"https://www.amap.com/search?query={urllib.parse.quote(f'{target_city} {loc_name}')}"
                            # 强制静态地图兜底：有坐标用坐标，无坐标用城市中心
                            if not r.get("map_image"):
                                if isinstance(r.get("lnglat"), list) and len(r["lnglat"]) >= 2:
                                    r["map_image"] = _make_static_map(r["lnglat"])
                                elif hasattr(self, 'center_lon') and hasattr(self, 'center_lat'):
                                    r["map_image"] = _make_static_map([self.center_lon, self.center_lat])
                            if not r.get("photos"):
                                r["photos"] = []
                    return route_nodes

                def _synth_full_trip():
                    """候选池确定性合成全部天数：每天 1 家酒店 + 白天节点，坐标/实景图 100% 来自高德真实数据源"""
                    _nodes = []
                    _used = set()
                    _slots = ["09:00", "10:30", "12:30", "14:00", "17:00", "21:30"]

                    def _next_unused(_pool):
                        for _x in _pool:
                            if isinstance(_x, dict) and _x.get("name") and _norm_name(str(_x.get("name"))) not in _used:
                                return _x
                        return None

                    def _make_node(_p, _d, _time):
                        _used.add(_norm_name(str(_p.get("name"))))
                        return {
                            "day": _d,
                            "name": _p.get("name") or "特色地标",
                            "location": _p.get("name") or "特色地标",
                            "lnglat": _p.get("lnglat") or [0.0, 0.0],
                            "type": _p.get("type") or "风景",
                            "desc": _p.get("web_knowledge") or "本地高人气文旅目的地。",
                            "time": _time,
                            "transport": "自驾/打车",
                            "tags": ["智能补全"] + (["住宿"] if _p.get("is_hotel") else []),
                            "cost_estimate": _p.get("cost_estimate") or "暂无供应商数据",
                            "photos": _p.get("photos") or [],
                            "amap_url": _p.get("amap_url") or "",
                            "map_image": _p.get("map_image") or "",
                            "rating": _p.get("rating") or "暂无供应商数据",
                            "open_time": _p.get("open_time") or "暂无供应商数据",
                            "data_sources": _p.get("data_sources") or {"rating": "unavailable", "cost_estimate": "unavailable", "open_time": "unavailable"},
                            "estimated": bool(_p.get("estimated", True)),
                            "is_hotel": bool(_p.get("is_hotel", False)),
                            "trust_reason": "候选池真实数据确定性合成（100% 实景匹配）"
                        }

                    # 👑 酒店与白天节点分池：每晚分配 1 家酒店，避免酒店全部堆到最后一天
                    _hotels = [p for p in poi_pool_data if isinstance(p, dict) and p.get("is_hotel")]
                    _others = [p for p in poi_pool_data if isinstance(p, dict) and not p.get("is_hotel")]

                    for _d in range(1, trip_days + 1):
                        for _slot_idx in range(actual_daily_count):
                            _p = _next_unused(_others)
                            if not _p:
                                break
                            _nodes.append(_make_node(_p, _d, _slots[min(_slot_idx, len(_slots) - 2)]))
                        _h = _next_unused(_hotels)
                        if _h:
                            _nodes.append(_make_node(_h, _d, "21:30"))
                    return _nodes

                # 👑 鲁棒 JSON 解析：优先 [FINAL_JSON] 标记段；模型漏打标记时直接对全文做正则提取，
                # 彻底修复「LLM 漏打 [FINAL_JSON] 标记 → 整条路线静默消失」的问题
                json_candidates = []
                if "[FINAL_JSON]" in full_response:
                    json_candidates.extend(full_response.split("[FINAL_JSON]")[1:])
                json_candidates.append(full_response)

                final_data = None
                parsed_list = []
                for _cand in json_candidates:
                    _m = re.search(r'\{[\s\S]*\}', _cand)
                    if not _m:
                        continue
                    _raw = _m.group(0)
                    try:
                        _parsed = json.loads(_raw)
                    except Exception:
                        _parsed = safe_repair_and_parse_json(_raw)
                    if isinstance(_parsed, dict):
                        parsed_list.append(_parsed)
                for _p in parsed_list:
                    if isinstance(_p.get("route"), list) and _p.get("route"):
                        final_data = _p
                        break
                if final_data is None and parsed_list:
                    final_data = parsed_list[0]

                llm_routes = final_data.get("route", []) if isinstance(final_data, dict) else []
                if not isinstance(llm_routes, list):
                    llm_routes = []
                # 事件流：路线草案（模型这一轮到底吐了几个节点；0 表示要走兜底合成，
                # 这条事件能让"为什么这次是候选池拼的"一眼可查，而不用去翻日志）
                await event_bus.emit(OMNI_EVENTS_STREAM, "route.drafted", {
                    "room_id": room_id, "city": target_city, "days": trip_days,
                    "nodes": len(llm_routes),
                    "source": "llm" if llm_routes else "empty_draft",
                    "correlation_id": plan_correlation_id,
                }, room_id, correlation_id=plan_correlation_id, producer="route-planner")

                if not llm_routes:
                    # 👑 终极兜底：LLM 输出漏标记/被截断/完全不可解析时，用候选池真实 POI 合成全部天数，
                    # 保证任何情况下都返回带真实坐标与真实图片的完整行程（图片 100% 来自高德真实数据源）
                    print(f"⚠️ [终极兜底] 未解析出可用路线（响应 {len(full_response)} 字符），改用候选池真实 POI 合成完整 {trip_days} 天行程。", flush=True)
                    if not final_data:
                        final_data = {
                            "status": "consensus_reached",
                            "negotiation_summary": f"已为您生成【{target_city}】{trip_days} 天完整行程，全部地点、坐标与图片均来自高德真实数据源。",
                            "route": []
                        }
                    llm_routes = _synth_full_trip()

                if isinstance(final_data, dict):
                    final_data["specialist_runtime"] = specialist_runtime_result

                if isinstance(llm_routes, list) and llm_routes:
                    llm_routes = _rebind(llm_routes)

                    # 👑 节点级风险画像：逐点计算质化风险分（城市治安基线 × 场所类型 × 时段 × 拥挤度）
                    _city_crime = float((safety_intel or {}).get("crime_score") or 10.0)
                    for _r in llm_routes:
                        if isinstance(_r, dict):
                            _r["risk_score"] = compute_node_risk(_r, _city_crime, str(_r.get("crowdedness") or ""))

                    # 👑 最终图片强制保证：遍历所有节点，确保每个节点都有 map_image（100% 图片覆盖）
                    _force_guaranteed = 0
                    for _r in llm_routes:
                        if not isinstance(_r, dict):
                            continue
                        _has_photo = bool(_r.get("photos"))
                        _has_map = bool(_r.get("map_image"))
                        if not _has_map:
                            _coord = _r.get("lnglat")
                            if isinstance(_coord, list) and len(_coord) >= 2:
                                _gu = _make_static_map(_coord)
                                if _gu:
                                    _r["map_image"] = _gu
                                    _has_map = True
                                    _force_guaranteed += 1
                            if not _has_map and hasattr(self, 'center_lon') and hasattr(self, 'center_lat'):
                                _r["map_image"] = _make_static_map([self.center_lon, self.center_lat])
                                _force_guaranteed += 1
                        if not _r.get("photos"):
                            _r["photos"] = []
                        if not isinstance(_r.get("data_sources"), dict):
                            _r["data_sources"] = {"rating": "unverified", "cost_estimate": "unverified", "open_time": "unverified"}
                            _r["estimated"] = True
                            _r["rating"] = "暂无供应商数据"
                            _r["cost_estimate"] = "暂无供应商数据"
                            _r["open_time"] = "暂无供应商数据"
                        elif "estimated" not in _r:
                            _r["estimated"] = any(value in {"unavailable", "unverified", "seed_template"} for value in _r["data_sources"].values())
                        for field, source_key in (("rating", "rating"), ("cost_estimate", "cost_estimate"), ("open_time", "open_time")):
                            source_value = _r["data_sources"].get(source_key)
                            if field == "cost_estimate" and source_value is None:
                                source_value = _r["data_sources"].get("cost")
                            if source_value in {"unverified", "unavailable"}:
                                _r[field] = "暂无供应商数据"
                        if not _r.get("amap_url"):
                            _nn = _norm_name(str(_r.get("name") or _r.get("location") or ""))
                            if _nn:
                                _r["amap_url"] = f"https://www.amap.com/search?query={urllib.parse.quote(f'{target_city} {_nn}')}"
                    if _force_guaranteed:
                        print(f"🛡️ [最终图片保证] 强制补图 {_force_guaranteed} 个节点，确保图片覆盖率100%", flush=True)

                    # 👑 图片匹配度验证结果输出（可被日志采集，作为准确性校验凭证）
                    _total_nodes = len([x for x in llm_routes if isinstance(x, dict)])
                    _photos_ok = len([x for x in llm_routes if isinstance(x, dict) and (x.get("photos") or x.get("map_image"))])
                    print(
                        f"🔍 [图片匹配度验证] 节点={_total_nodes} | 精准匹配={rebind_stats['matched']} | 置换={rebind_stats['swapped']} | "
                        f"名称匹配率={round(rebind_stats['matched'] / max(_total_nodes, 1) * 100, 1)}% | "
                        f"图片覆盖={_photos_ok}/{_total_nodes} ({round(_photos_ok / max(_total_nodes, 1) * 100, 1)}%)",
                        flush=True
                    )

                    # 天数校验：保证 Day 1..trip_days 全覆盖，缺则二次补齐
                    present_days = sorted({int(r.get("day") or 1) for r in llm_routes if isinstance(r, dict)})
                    missing_days = [d for d in range(1, trip_days + 1) if d not in present_days]
                    print(f"🎯 [主推演天数] LLM 实返回天数={present_days}，节点总数={len(llm_routes)}，期望 Day 1..{trip_days}，缺失={missing_days if missing_days else '无'}", flush=True)
                    if missing_days:
                        print(f"⚠️ [天数补全] 主推演遗漏 {missing_days}，正在二次补齐...")
                        try:
                            extra = await _complete_missing_days(missing_days)
                            if isinstance(extra, list) and extra:
                                llm_routes.extend(_rebind(extra))
                        except Exception as e:
                            print(f"🔥 [天数补全失败]: {e}")

                    # 👑 确定性兜底：模型仍未补齐全部天数时，用候选池合成缺失天，硬保证「玩几天=几天」
                    still_missing = [d for d in range(1, trip_days + 1) if d not in {int(r.get("day") or 1) for r in llm_routes if isinstance(r, dict)}]
                    if still_missing and poi_pool_data:
                        used_names = {_norm_name(str(r.get("name") or r.get("location") or "")) for r in llm_routes if isinstance(r, dict)}
                        pool_left = [p for p in poi_pool_data if isinstance(p, dict) and _norm_name(str(p.get("name") or "")) not in used_names]
                        time_slots = ["09:00", "10:30", "12:30", "14:00", "17:00", "21:30"]
                        synth_nodes = []
                        for d in still_missing:
                            for slot in range(actual_daily_count + 1):
                                if not pool_left:
                                    break
                                p = pool_left.pop(0)
                                synth_nodes.append({
                                    "day": d,
                                    "name": p.get("name") or "特色地标",
                                    "location": p.get("name") or "特色地标",
                                    "lnglat": p.get("lnglat") or [0.0, 0.0],
                                    "type": p.get("type") or "风景",
                                    "desc": p.get("web_knowledge") or "本地高人气文旅目的地。",
                                    "time": time_slots[min(slot, len(time_slots) - 1)],
                                    "transport": "自驾/打车",
                                    "tags": ["智能补全"] + (["住宿"] if p.get("is_hotel") else []),
                                    "cost_estimate": p.get("cost_estimate") or "暂无供应商数据",
                                    "photos": p.get("photos") or [],
                                    "amap_url": p.get("amap_url") or "",
                                    "map_image": p.get("map_image") or "",
                                    "rating": p.get("rating") or "暂无供应商数据",
                                    "open_time": p.get("open_time") or "暂无供应商数据",
                                    "data_sources": p.get("data_sources") or {"rating": "unavailable", "cost_estimate": "unavailable", "open_time": "unavailable"},
                                    "estimated": bool(p.get("estimated", True)),
                                    "is_hotel": bool(p.get("is_hotel", False)),
                                    "trust_reason": "智能补全保障完整天数"
                                })
                        if synth_nodes:
                            print(f"⚠️ [确定性兜底] 模型遗漏 {still_missing}，已从候选池合成补全，保证 {trip_days} 天完整输出。")
                            llm_routes.extend(synth_nodes)

                    # 按 day 排序，保证展示顺序
                    llm_routes.sort(key=lambda r: int(r.get("day") or 1))

                    # 👑 每日均衡校验与补全：保证每天「景点 + 美食 + 夜间住宿」三类体验齐全（人性化节奏）
                    def _pool_node(p, day, time_label, is_hotel, tags):
                        _hotel = bool(is_hotel)
                        _lnglat = p.get("lnglat") or [0.0, 0.0]
                        return {
                            "day": day,
                            "name": p.get("name") or "特色地标",
                            "location": p.get("name") or "特色地标",
                            "lnglat": _lnglat,
                            "type": p.get("type") or ("酒店住宿" if _hotel else "风景"),
                            "desc": p.get("web_knowledge") or (f"{target_city}精选住宿，交通便利。" if _hotel else "本地高人气文旅目的地。"),
                            "time": time_label,
                            "transport": "自驾/打车",
                            "tags": (tags or []) + (["住宿"] if _hotel else []),
                            "cost_estimate": p.get("cost_estimate") or "暂无供应商数据",
                            "photos": p.get("photos") or [],
                            "amap_url": p.get("amap_url") or "",
                            "map_image": p.get("map_image") or "",
                            "rating": p.get("rating") or "暂无供应商数据",
                            "open_time": p.get("open_time") or "暂无供应商数据",
                            "data_sources": p.get("data_sources") or {"rating": "unavailable", "cost_estimate": "unavailable", "open_time": "unavailable"},
                            "estimated": bool(p.get("estimated", True)),
                            "address": p.get("address") or "",
                            "hotel_candidates": p.get("hotel_candidates") or [],
                            "is_hotel": _hotel,
                            "trust_reason": "每日均衡补全 · 真实候选池数据",
                        }

                    def _node_cat(r):
                        return classify_poi_category(
                            str(r.get("name") or r.get("location") or ""),
                            str(r.get("type") or ""),
                            bool(r.get("is_hotel") or (isinstance(r.get("tags"), list) and "住宿" in r.get("tags", []))),
                        )

                    def _ensure_daily_balance(routes):
                        routes = [r for r in routes if isinstance(r, dict)]
                        _used = {_norm_name(str(r.get("name") or r.get("location") or "")) for r in routes}
                        _pool = [p for p in poi_pool_data if isinstance(p, dict) and _norm_name(str(p.get("name") or "")) not in _used]

                        def _take(category):
                            for p in _pool:
                                if _norm_name(str(p.get("name") or "")) in _used:
                                    continue
                                if classify_poi_category(str(p.get("name") or ""), str(p.get("type") or ""), bool(p.get("is_hotel"))) == category:
                                    _used.add(_norm_name(str(p.get("name") or "")))
                                    return p
                            return None

                        by_day = {}
                        for r in routes:
                            by_day.setdefault(int(r.get("day") or 1), []).append(r)
                        for d in range(1, trip_days + 1):
                            nodes = by_day.get(d, [])
                            cats = {_node_cat(r) for r in nodes}
                            # A missing model day is a planning defect, not a
                            # reason to omit the day. Build it from the same
                            # verified candidate pool used for normal output.
                            if "hotel" not in cats:
                                h = _take("hotel")
                                if h:
                                    nodes.append(_pool_node(h, d, f"Day {d} | 21:30 入住", True, []))
                            if "food" not in cats:
                                f = _take("food")
                                if f:
                                    nodes.append(_pool_node(f, d, f"Day {d} | 12:30 午餐", False, ["餐饮", "老字号"]))
                            if "scenic" not in cats and "cultural" not in cats:
                                a = _take("scenic") or _take("cultural")
                                if a:
                                    nodes.append(_pool_node(a, d, f"Day {d} | 09:00 游览", False, ["景点"]))
                            if "other" not in cats:
                                activity = _take("other") or _take("shopping") or _take("cultural")
                                if activity:
                                    nodes.append(_pool_node(activity, d, f"Day {d} | 16:00 休闲活动", False, ["活动", "休闲"]))
                            # 深度探索要求在完成类别覆盖后继续填充额外的
                            # 独立节点，不能让 Day 2..N 只剩住宿。
                            daytime_count = sum(1 for item in nodes if _node_cat(item) != "hotel")
                            while daytime_count < actual_daily_count:
                                extra = None
                                for candidate in _pool:
                                    name = _norm_name(str(candidate.get("name") or ""))
                                    if name and name not in _used:
                                        extra = candidate
                                        break
                                if not extra:
                                    break
                                _used.add(_norm_name(str(extra.get("name") or "")))
                                nodes.append(_pool_node(extra, d, f"Day {d} | {14 + daytime_count:02d}:00 深度探索", False, ["深度探索"]))
                                daytime_count += 1
                            by_day[d] = nodes
                        # 候选池耗尽时做“最小扰动”跨日均衡：从白天节点超额的日期
                        # 迁移到缺口日期。这样不会复制地点，也不会让最后一天只剩酒店。
                        def _daytime(items):
                            return [item for item in items if _node_cat(item) != "hotel"]

                        total_daytime = sum(len(_daytime(by_day.get(d, []))) for d in range(1, trip_days + 1))
                        even_base = min(actual_daily_count, total_daytime // trip_days) if trip_days else 0
                        even_remainder = max(0, total_daytime - even_base * trip_days)
                        desired_counts = {
                            d: even_base + (1 if d <= min(trip_days, even_remainder) and even_base < actual_daily_count else 0)
                            for d in range(1, trip_days + 1)
                        }
                        for target_day in range(1, trip_days + 1):
                            target_nodes = by_day.setdefault(target_day, [])
                            while len(_daytime(target_nodes)) < desired_counts[target_day]:
                                donor_day = next((candidate_day for candidate_day in range(1, trip_days + 1)
                                                  if candidate_day != target_day and len(_daytime(by_day.get(candidate_day, []))) > desired_counts[candidate_day]), None)
                                if donor_day is None:
                                    break
                                donor_nodes = by_day[donor_day]
                                donor_idx = next((idx for idx in range(len(donor_nodes) - 1, -1, -1) if _node_cat(donor_nodes[idx]) != "hotel"), None)
                                if donor_idx is None:
                                    break
                                moved = donor_nodes.pop(donor_idx)
                                moved["day"] = target_day
                                moved["time"] = f"Day {target_day} | {14 + len(_daytime(target_nodes)):02d}:00 深度探索"
                                target_nodes.append(moved)
                        for d, nodes in by_day.items():
                            daytime = _daytime(nodes)
                            for idx, item in enumerate(daytime):
                                if not str(item.get("time") or "").startswith(f"Day {d} |"):
                                    item["time"] = f"Day {d} | {9 + min(idx * 2, 10):02d}:00 深度探索"
                        # 输出可观测的每日白天/住宿计数，前端可据此明确展示缺口。
                        balance_audit = {
                            str(d): {
                                "daytime": len(_daytime(by_day.get(d, []))),
                                "hotel": sum(1 for item in by_day.get(d, []) if _node_cat(item) == "hotel"),
                                # target_daytime preserves the user's requested
                                # density; achievable_target is the amount
                                # reachable from the verified pool after
                                # deduplication and cross-day balancing.
                                "target_daytime": actual_daily_count,
                                "achievable_target": desired_counts[d],
                                "satisfied": len(_daytime(by_day.get(d, []))) >= actual_daily_count,
                            }
                            for d in range(1, trip_days + 1)
                        }
                        out = []
                        for d in sorted(by_day):
                            out.extend(by_day[d])
                        return out, balance_audit

                    final_data["route"] = llm_routes
                    try:
                        llm_routes, balance_audit = _ensure_daily_balance(llm_routes)
                        llm_routes.sort(key=lambda r: int(r.get("day") or 1))
                        final_data["route"] = llm_routes
                        final_data["daily_balance"] = balance_audit
                    except Exception as _bal_err:
                        print(f"⚠️ [每日均衡补全] 失败，回退 LLM 原始路线: {_bal_err}", flush=True)

                    # Every route leaving the AI service receives an explicit
                    # hard-constraint and fairness audit. Exact user budgets
                    # are enforced; heuristic budget modes remain auditable
                    # without rejecting candidates whose supplier price is
                    # legitimately unavailable.
                    llm_routes = repair_route(llm_routes, max_nodes_per_day=actual_daily_count + 1)
                    final_data["route"] = llm_routes
                    route_validation = validate_route(
                        llm_routes,
                        budget=total_calc_budget if budget_mode == "EXACT_AMOUNT" else None,
                        days=trip_days,
                        max_nodes_per_day=actual_daily_count + 1,
                        unique_locations=True,
                    )
                    final_data["constraints"] = route_validation
                    final_data["optimization_audit"] = optimization_audit
                    final_data["fairness"] = fairness_report(llm_routes, room_members)

                    # 👑 不走回头路：按天对节点做空间最近邻（贪心 TSP）重排，消除当天往返折返。
                    #    白天节点从当日原首节点出发、逐点就近串接；酒店固定当天末尾；时段标签单调递增。
                    try:
                        def _coord_of(node):
                            c = node.get("lnglat")
                            if isinstance(c, (list, tuple)) and len(c) >= 2:
                                try:
                                    lng, lat = float(c[0]), float(c[1])
                                except (TypeError, ValueError):
                                    return None
                                if not (lng == 0.0 and lat == 0.0):
                                    return (lng, lat)
                            return None

                        _day_slots = ["09:00", "10:30", "12:00", "13:30", "15:00", "16:30", "18:00"]
                        _cat_label = {"scenic": "游览", "cultural": "文化", "food": "美食", "shopping": "购物", "other": "休闲"}

                        def _reassign_times(seq):
                            if not seq:
                                return seq
                            day = int(seq[0].get("day") or 1)
                            stops = [x for x in seq if _node_cat(x) != "hotel"]
                            hotels = [x for x in seq if _node_cat(x) == "hotel"]
                            for idx, node in enumerate(stops):
                                slot = _day_slots[min(idx, len(_day_slots) - 1)]
                                cat = _node_cat(node)
                                label = "午餐" if cat == "food" and idx <= len(stops) // 2 else ("晚餐" if cat == "food" else _cat_label.get(cat, "游览"))
                                node["time"] = f"Day {day} | {slot} {label}"
                            for node in hotels:
                                node["time"] = f"Day {day} | 21:30 入住"
                            return seq

                        def _reorder_day_no_backtrack(nodes):
                            hotels = [x for x in nodes if _node_cat(x) == "hotel"]
                            stops = [x for x in nodes if _node_cat(x) != "hotel"]
                            if len(stops) < 3:
                                return _reassign_times(stops + hotels)
                            ordered = [stops[0]]
                            remaining = set(range(1, len(stops)))
                            cur = _coord_of(stops[0])
                            while remaining:
                                best_i, best_d = None, float("inf")
                                for i in remaining:
                                    c = _coord_of(stops[i])
                                    if c is None:
                                        continue
                                    d = _haversine_m(cur[0], cur[1], c[0], c[1]) if cur else 0.0
                                    if d < best_d:
                                        best_d, best_i = d, i
                                if best_i is None:
                                    ordered.extend(stops[i] for i in sorted(remaining))
                                    break
                                ordered.append(stops[best_i])
                                remaining.remove(best_i)
                                cur = _coord_of(stops[best_i])
                            return _reassign_times(ordered + hotels)

                        _grouped = {}
                        for r in llm_routes:
                            if isinstance(r, dict):
                                _grouped.setdefault(int(r.get("day") or 1), []).append(r)
                        _reordered = []
                        for d in sorted(_grouped):
                            _reordered.extend(_reorder_day_no_backtrack(_grouped[d]))
                        llm_routes = _reordered
                        final_data["route"] = llm_routes
                        print(f"🛣️ [不走回头路] 已按天级空间重排 {len(llm_routes)} 个节点，消除当天往返折返。", flush=True)
                    except Exception as _no_bt_err:
                        print(f"⚠️ [不走回头路重排] 失败，回退原始顺序: {_no_bt_err}", flush=True)

                    # 👑 覆协校验：验证用户所有输入偏好是否都已被覆盖
                    final_data["preference_validation"] = validate_preference_coverage(llm_routes, pref_signals)
                    # Structured proposal audit: arbitration receives an
                    # explicit evidence/degradation status alongside the
                    # human-readable reasoning stream.
                    _specialist_audits = []
                    for _proposal in specialist_runtime_result.get("proposals", []) if isinstance(specialist_runtime_result, dict) else []:
                        if not isinstance(_proposal, dict):
                            continue
                        _specialist_audits.append({
                            "agent_id": _proposal.get("agent_id"),
                            "domain": _proposal.get("domain"),
                            "status": _proposal.get("status", "failed"),
                            "evidence_count": len(_proposal.get("evidence") or []),
                            "candidate_count": len(_proposal.get("candidates") or []),
                            "degraded": bool(_proposal.get("degraded")),
                            "latency_ms": _proposal.get("latency_ms", 0),
                        })
                    final_data["agent_contracts"] = [audit_route_agent_proposal(llm_routes), *_specialist_audits]
                    print(f"🔍 [偏好覆协校验] {final_data['preference_validation']}", flush=True)

                    # 👑 多方案生成：同一候选池产出 2 套差异化风格的完整方案，供用户对比选择
                    _CAT_TAG = {"scenic": "风景", "cultural": "文化", "food": "美食", "shopping": "购物", "hotel": "住宿", "other": "休闲"}

                    def _assemble_variant(style, seed):
                        import random as _rnd
                        rng = _rnd.Random(seed)
                        buckets = {"scenic": [], "cultural": [], "food": [], "shopping": [], "other": [], "hotel": []}
                        for p in poi_pool_data:
                            if isinstance(p, dict) and p.get("name"):
                                buckets[classify_poi_category(str(p.get("name") or ""), str(p.get("type") or ""), bool(p.get("is_hotel")))].append(p)
                        if style == "niche":
                            # Prefer cultural/community and low-popularity
                            # candidates; popular landmarks are a fallback,
                            # so the niche arm is observably different.
                            for category, items in buckets.items():
                                items.sort(key=lambda p: (
                                    any(token in str(p.get("name") or "") for token in ("故宫", "长城", "天安门", "迪士尼")),
                                    -float(p.get("rating") or 0) if str(p.get("rating") or "").replace('.', '', 1).isdigit() else 0,
                                ))
                        for b in buckets.values():
                            rng.shuffle(b)
                        day_cap = actual_daily_count
                        if style == "relaxed":
                            day_cap = max(3, actual_daily_count - 1)
                        elif style == "intense":
                            day_cap = actual_daily_count + 1
                        used = set()

                        def _draw(*cats):
                            for c in cats:
                                while buckets.get(c):
                                    p = buckets[c].pop(0)
                                    nm = _norm_name(str(p.get("name") or ""))
                                    if nm and nm not in used:
                                        used.add(nm)
                                        return p, c
                            return None, None

                        nodes = []
                        for d in range(1, trip_days + 1):
                            seq = []
                            first_order = ("cultural", "other", "scenic") if style == "niche" else ("scenic", "cultural")
                            p, c = _draw(*first_order)
                            if p:
                                seq.append((f"Day {d} | 09:00 游览", p, c))
                            p, c = _draw("cultural", "other", "shopping", "scenic") if style == "niche" else _draw("cultural", "scenic", "shopping")
                            if p:
                                seq.append((f"Day {d} | 10:30 观光", p, c))
                            p, c = _draw("food")
                            if p:
                                seq.append((f"Day {d} | 12:30 午餐", p, c))
                            p, c = _draw("other", "cultural", "shopping", "scenic") if style == "niche" else _draw("cultural", "scenic", "shopping", "other")
                            if p:
                                seq.append((f"Day {d} | 14:30 深度游", p, c))
                            p, c = _draw("food", "shopping", "other")
                            if p:
                                seq.append((f"Day {d} | 17:30 晚餐", p, c))
                            while len(seq) < day_cap:
                                p, c = _draw("scenic", "cultural", "food", "shopping", "other")
                                if not p:
                                    break
                                seq.append((f"Day {d} | 16:00 漫游", p, c))
                            hp, _hc = _draw("hotel")
                            if hp:
                                seq.append((f"Day {d} | 21:30 入住", hp, "hotel"))
                            for time_label, pp, cc in seq:
                                if not pp:
                                    continue
                                nodes.append(_pool_node(pp, d, time_label, cc == "hotel", [_CAT_TAG.get(cc, "休闲")]))
                        return nodes

                    _variant_styles = [
                        ("relaxed", "慵懒度假", "节奏松弛、住宿舒适，适合轻松慢游"),
                        ("intense", "特种兵暴走", "节奏紧凑、覆盖更多景点，适合打卡达人"),
                        ("niche", "小众探索", "偏爱文化与小众去处，避开热门人流"),
                    ]
                    fairness_members = [
                        {"id": str(member.get("id") or member.get("name") or f"member-{index}"), "interestTags": [str(member.get("intent") or "")]}
                        for index, member in enumerate(room_members) if isinstance(member, dict)
                    ]
                    try:
                        plan_variants = []
                        for _i, (_st, _name, _desc) in enumerate(_variant_styles):
                            _vr = _assemble_variant(_st, 1000 + _i * 77)
                            if _vr:
                                _vr, _variant_balance = _ensure_daily_balance(_vr)
                            if _vr:
                                plan_variants.append({"id": f"variant_{_i + 1}", "name": _name, "style": _st, "desc": _desc, "route": _vr, "daily_balance": _variant_balance})
                        if plan_variants:
                            final_data["plan_variants"] = plan_variants
                            fairness_members = [
                                {"id": str(member.get("id") or member.get("name") or f"member-{index}"), "interestTags": [str(member.get("intent") or "")]}
                                for index, member in enumerate(room_members) if isinstance(member, dict)
                            ]
                            fair_routes = [llm_routes] + [variant["route"] for variant in plan_variants]
                            # 🔒 约束引擎：先按 L0 硬约束过滤不可行方案，再对可行子集做 maximin 公平选择
                            _planning_context = {
                                "destination": target_city,
                                "budget": total_calc_budget if budget_mode == "EXACT_AMOUNT" else None,
                                "days": trip_days,
                                "max_nodes_per_day": actual_daily_count + 1,
                                "travelers": max(1, len(room_members)),
                            }
                            _constraint_resolution = resolve_conflicts(fair_routes, _planning_context, fairness_members)
                            _feasible_indices = [int(entry.get("index", 0)) for entry in _constraint_resolution.get("ranked", [])]
                            if _feasible_indices:
                                _feasible_routes = [fair_routes[i] for i in _feasible_indices]
                                fair_selection = fairness_gate(_feasible_routes, fairness_members, max_stddev=_fairness_limit_from_env())
                                selected_index = _feasible_indices[int(fair_selection.get("index", 0))]
                                _report_route_indices = _feasible_indices
                                _selection_method = "hard_constraint_filter_then_maximin"
                            else:
                                fair_selection = fairness_gate(fair_routes, fairness_members, max_stddev=_fairness_limit_from_env())
                                selected_index = int(fair_selection.get("index", 0))
                                _report_route_indices = list(range(len(fair_routes)))
                                _selection_method = "maximin_fallback_all_infeasible"
                            final_data["constraints_engine"] = {
                                "infeasible_count": _constraint_resolution.get("infeasible_count", 0),
                                "conflict_summary": _constraint_resolution.get("conflict_summary", []),
                                "selection_method": _selection_method,
                            }
                            if selected_index > 0:
                                llm_routes = list(fair_routes[selected_index])
                                final_data["route"] = llm_routes
                            selected_variant_index = selected_index - 1 if selected_index > 0 else None
                            fairness_base = fair_selection.get("report") or fairness_report(llm_routes, fairness_members)
                            _candidate_reports = []
                            for _report_index, _report in enumerate(fair_selection.get("reports", [])):
                                _report_copy = dict(_report)
                                _route_index = _report_route_indices[_report_index] if _report_index < len(_report_route_indices) else _report_index
                                _report_copy["candidate_name"] = "AI 主方案" if _route_index == 0 else str(plan_variants[_route_index - 1].get("name") or f"候选方案 {_route_index}") if _route_index - 1 < len(plan_variants) else f"候选方案 {_route_index}"
                                _report_copy["candidate_index"] = _route_index
                                _candidate_reports.append(_report_copy)
                            _fairness_threshold = _fairness_limit_from_env()
                            _fairness_exceeded = bool(fair_selection.get("threshold_exceeded", float(fairness_base.get("utility_stddev", 0)) > _fairness_threshold))
                            final_data["fairness"] = dict(
                                fairness_base,
                                selected_variant_index=selected_variant_index,
                                selection_method=_selection_method,
                                candidate_reports=_candidate_reports,
                                threshold=_fairness_threshold,
                                threshold_exceeded=_fairness_exceeded,
                                repair_attempted=_fairness_exceeded,
                                repair_status="no_candidate_within_threshold" if _fairness_exceeded else "not_needed",
                            )
                            if selected_index > 0:
                                llm_routes = repair_route(llm_routes, max_nodes_per_day=actual_daily_count + 1)
                                final_data["route"] = llm_routes
                                final_data["constraints"] = validate_route(
                                    llm_routes,
                                    budget=total_calc_budget if budget_mode == "EXACT_AMOUNT" else None,
                                    days=trip_days,
                                    max_nodes_per_day=actual_daily_count + 1,
                                    unique_locations=True,
                                )
                            print(f"🏆 [多方案] 已生成 {len(plan_variants)} 套差异化方案（{', '.join(v['style'] for v in plan_variants)}）", flush=True)
                    except Exception as _var_err:
                        print(f"⚠️ [多方案生成] 失败，仅返回主方案: {_var_err}", flush=True)

                    # Optional contextual exploration happens after hard
                    # constraints and fairness selection. A disabled bandit
                    # leaves the selected route byte-for-byte unchanged.
                    if planning_bandit.enabled and plan_variants:
                        # Arm identity is (policy version, itinerary style) rather
                        # than the per-response variant id. Variant ids are
                        # positional ("variant_1"), so they carry no meaning
                        # across a redeploy: evidence gathered for "variant_2"
                        # would silently attach to a different style after the
                        # next release. Style-keyed arms survive redeploys and
                        # are the thing we actually want to learn about.
                        _arms = [
                            {
                                "id": stable_arm_id(MODEL_NAME, str(v.get("style") or "")),
                                "name": v.get("name"),
                                "score": 0.0,
                                "tags": [v.get("style", "")],
                                "route": v.get("route", []),
                                "variant_id": v.get("id"),
                            }
                            for v in plan_variants
                        ]
                        _decision = planning_bandit.select(
                            _arms,
                            {
                                "intent": intent_str,
                                "room_size": len(room_members),
                                "budget": total_calc_budget,
                                "travelers": len(room_members),
                            },
                        )
                        _selected_arm = _decision.get("selected") or {}
                        _selected_route = _selected_arm.get("route")
                        if isinstance(_selected_route, list) and _selected_route:
                            final_data["route"] = _selected_route
                            # Expose the arm so the client can attribute a later
                            # reward to it. Without this round trip the reward
                            # endpoint is never called with a real arm id and the
                            # policy has nothing to learn from.
                            final_data["bandit"] = {
                                "enabled": True,
                                "arm_id": _selected_arm.get("id"),
                                "variant_id": _selected_arm.get("variant_id"),
                                "reason": _decision.get("reason"),
                                "explored": _decision.get("explored"),
                                "propensity": _decision.get("propensity"),
                                "candidate_count": _decision.get("candidate_count"),
                            }
                            final_data["fairness"] = {**(final_data.get("fairness") or {}), **fairness_report(_selected_route, fairness_members)}

                    # 回发校正后的最终路线，前端以 final_route 为准（真实图片/坐标/完整天数）
                    # 🧠 智能时间管理：注入节奏判断 + 周一闭馆感知
                    if isinstance(final_data.get("route"), list):
                        final_data["pace_profile"] = analyze_pace(trip_days, len(final_data["route"]), intent_str)
                        final_data["monday_closure_notes"] = monday_closure_notes(final_data["route"])
                        # 概率化数字孪生：用蒙特卡洛仿真替换点估计与伪造指标。
                        # 原先 team_satisfaction 是写死在 prompt 模板里的 96/94/93，
                        # 由模型原样回显；现在改为按成员在真实路线上的效用分布计算。
                        final_data["simulation"] = _simulate_final_plan(
                            final_data,
                            room_members=fairness_members or room_members,
                            budget=total_calc_budget,
                            weather=final_data.get("weather_info") or weather_data,
                        )
                        computed = _satisfaction_from_simulation(final_data["simulation"])
                        if computed:
                            final_data["team_satisfaction"] = computed

                        # 👑 阶段 1：规划质量门禁 + 预算可行性 + 兜底方案（纯确定性，零网络）
                        # 业务逻辑在 core/plan_quality.plan_quality_snapshot 里（可单测），
                        # 这里只做薄接线；任何异常都不得阻断规划主流程。
                        # 👑 长途（≥14 天）：**首轮按段生成** + 事后修补（两层，都有界）
                        # 30 天 × 每天多节点靠单次生成不可靠（会截断、漏天、编造）。所以：
                        #   第一层 首轮分段生成：按 7 天一段逐段生成、边生成边合并，每段的"衔接简报"
                        #          都取自上一段**真实产出**的最后节点 → 长行程不再断链；
                        #   第二层 修补：首轮之后仍不合格的段，最多再补 2 段（防止一轮打太多请求）。
                        # 全程有界：段数上限 6 段（42 天）、单段 45s 超时、整段生成 240s 预算，
                        # 超了就把没生成的段如实列出来（failed/skipped），绝不假装排好了。
                        if trip_days >= 14:
                            try:
                                from core.long_trip import (
                                    build_segmented_plan,
                                    merge_segment_nodes,
                                    segment_prompt,
                                    segment_repair_targets,
                                    summarize_segments,
                                )

                                _long_context = {"days": trip_days, "budget": total_calc_budget}
                                _long_deadline = _time.monotonic() + LONG_TRIP_GENERATION_SECONDS
                                # 👑 B-1：分段调用不能重发整份路书的巨型 prompt（实测 30 天行程
                                # 调 6 次、每次 8,932 字符，其中 **8.4k 是候选池 JSON**，97% 的输入
                                # 都是这段的重复）。分段只带"规则 + 输出契约 + 候选池最小索引"，
                                # 并顺手修掉"系统提示要对象、真实任务要数组"的语义打架。
                                from core.prompts import segment_system_prompt

                                try:
                                    _segment_system_prompt = segment_system_prompt(system_prompt, prompt_pool_data)
                                except Exception:
                                    _segment_system_prompt = system_prompt  # 裁剪失败就用原文，绝不因此失败

                                async def _generate_segment(_target: Dict[str, Any]) -> List[Any]:
                                    """只生成该段的节点数组（越界天数由 core 丢弃）。"""
                                    if _time.monotonic() > _long_deadline:
                                        return []  # 超预算：如实记为"这一段没生成出来"
                                    _resp = await asyncio.wait_for(
                                        client.chat.completions.create(
                                            model=MODEL_NAME,
                                            messages=[
                                                {"role": "system", "content": _segment_system_prompt},
                                                {"role": "user", "content": segment_prompt(_target)},
                                            ],
                                            temperature=llm_cfg.temperature(),
                                            max_tokens=llm_cfg.max_tokens(),
                                        ),
                                        timeout=45.0,
                                    )
                                    _content = (_resp.choices[0].message.content if _resp.choices else "") or ""
                                    _match = re.search(r"\[[\s\S]*\]", _content)
                                    if not _match:
                                        return []
                                    try:
                                        _nodes = json.loads(_match.group(0))
                                    except Exception:
                                        return []
                                    return _nodes if isinstance(_nodes, list) else []

                                _segmented = await build_segmented_plan(final_data, _long_context, _generate_segment)
                                _segmented_plan = _segmented.get("plan")
                                if _segmented.get("applied") and isinstance(_segmented_plan, dict) and isinstance(
                                    _segmented_plan.get("route"), list
                                ):
                                    final_data["route"] = _segmented_plan["route"]
                                _first_round = {
                                    "chunk_days": _segmented.get("chunk_days"),
                                    "generated": _segmented.get("generated_segments") or [],
                                    "failed": _segmented.get("failed_segments") or [],
                                    "skipped": _segmented.get("skipped_segments") or [],
                                    "truncated": bool(_segmented.get("truncated")),
                                    "out_of_range_days": sorted(
                                        {
                                            int(day)
                                            for _record in (_segmented.get("segments") or [])
                                            for day in (_record.get("out_of_range") or [])
                                        }
                                    )[:5],
                                }

                                _segment_targets = segment_repair_targets(final_data, _long_context)
                                _repaired: List[Dict[str, Any]] = []
                                _repair_failed: List[int] = []
                                for _target in _segment_targets[:2]:
                                    try:
                                        _seg_resp = await client.chat.completions.create(
                                            model=MODEL_NAME,
                                            messages=[
                                                {"role": "system", "content": _segment_system_prompt},
                                                {"role": "user", "content": segment_prompt(_target)},
                                            ],
                                            temperature=llm_cfg.temperature(),
                                            max_tokens=llm_cfg.max_tokens(),
                                        )
                                        _seg_content = (_seg_resp.choices[0].message.content if _seg_resp.choices else "") or ""
                                        _seg_match = re.search(r"\[[\s\S]*\]", _seg_content)
                                        _seg_nodes = json.loads(_seg_match.group(0)) if _seg_match else []
                                        if not isinstance(_seg_nodes, list) or not _seg_nodes:
                                            _repair_failed.append(int(_target["start_day"]))
                                            continue
                                        _seg_merged = merge_segment_nodes(
                                            final_data, int(_target["start_day"]), int(_target["end_day"]), _seg_nodes
                                        )
                                        final_data["route"] = _seg_merged["plan"]["route"]
                                        _repaired.append(
                                            {
                                                "segment": _target["index"],
                                                "days": f"{_target['start_day']}-{_target['end_day']}",
                                                "added": _seg_merged["added"],
                                                "dropped_out_of_range": _seg_merged["out_of_range"][:5],
                                                "reasons": _target["reasons"],
                                            }
                                        )
                                    except Exception:
                                        _repair_failed.append(int(_target["start_day"]))
                                final_data["long_trip"] = {
                                    **summarize_segments(final_data, _long_context),
                                    "first_round": _first_round,
                                    "repaired": _repaired,
                                    "repair_failed_segments": _repair_failed,
                                }
                            except Exception as _long_exc:  # 分段生成/修补失败绝不影响出方案
                                final_data["long_trip"] = {"error": str(_long_exc)[:200]}

                        try:
                            from core.increment import parse_increment
                            from core.planning_governance import prepare_governance

                            _quality_signals = pref_signals if isinstance(pref_signals, dict) else None
                            _quality_context = {
                                "days": trip_days,
                                "budget": total_calc_budget,
                                # 原话带上：质量层要判断"这趟是不是以自然风景为主"这类诉求
                                "request_text": intent_str,
                                "preferences": {
                                    "pace": intent_str,
                                    "interest": (_quality_signals or {}).get("interest"),
                                },
                            }
                            # 二次增量：把"这次新说的话"解析成结构化 delta（排他 / 配额 / 天数预算）。
                            # 只有 refine（已有历史 + 既有路线）时才解析，首次生成不会是增量。
                            # 放在复核之前：排他词要用来剔除候选池，复核补点时不能把用户点名不要的
                            # 地方又补回来。
                            _refinement_delta = None
                            if is_refinement:
                                _known_names: List[str] = []
                                if isinstance(current_existing_route, str) and current_existing_route.strip().startswith("["):
                                    try:
                                        _parsed_prev = json.loads(current_existing_route)
                                        if isinstance(_parsed_prev, list):
                                            _known_names = [
                                                str(item.get("name") or item.get("location") or "")
                                                for item in _parsed_prev
                                                if isinstance(item, dict)
                                            ]
                                    except Exception:
                                        _known_names = []
                                _refinement_delta = parse_increment(
                                    intent_str,
                                    previous_plan=final_data,
                                    known_names=_known_names,
                                )
                                # 二次增量直接影响"该看什么"的构成策略（例如"想多打卡自然景观"
                                # 会抬高每天自然景观目标、压低文化类上限），所以要带进质量上下文。
                                _quality_context["increment"] = _refinement_delta

                            # 👑 候选池：只有"复核发现结构缺口、或预算吃紧、或有排他/配额"时才去取，
                            # 一次取好、复核与治理共用（避免同一请求打两遍数据源）。
                            _pool: Dict[str, Any] = {}
                            try:
                                from core.poi_pool import build_candidate_pool, intents_for_plan
                                from core.plan_review import critique_plan as _critique_plan

                                _pre = _critique_plan(
                                    _quality_context, final_data, signals=_quality_signals, pool_available=True
                                )
                                _exclude_terms = [str(item) for item in ((_refinement_delta or {}).get("exclude") or [])]
                                # 只在"确实需要新候选"时取池子：结构缺口（缺玩点/餐/住宿/必去项）
                                # 或这次是排他/配额类增量。纯时间顺序问题用确定性修补就够了，
                                # 没必要为它多打几次数据源。
                                _need_pool = bool(
                                    "needs_candidates" in (_pre.get("fixable") or [])
                                    or _exclude_terms
                                    or (_refinement_delta or {}).get("quota")
                                )
                                _fetch_pois = getattr(toolbox, "get_dynamic_pois", None)
                                if _need_pool and _fetch_pois and target_city:
                                    _pool = await asyncio.wait_for(
                                        build_candidate_pool(
                                            target_city,
                                            intents_for_plan(final_data, extra=_exclude_terms),
                                            _fetch_pois,
                                            limit_per_intent=6,
                                            exclude_names=_exclude_terms,
                                        ),
                                        timeout=POOL_FETCH_TIMEOUT_SECONDS,
                                    ) or {}
                            except Exception:  # 取不到候选池不影响出方案，复核会如实报"要数据"
                                _pool = {}

                            # 👑 方案复核 + 修补（propose → critique → repair → rescore，≤3 轮）：
                            # 先做确定性修补（时间倒序/越界、重复、越界天数），有候选池时再补结构
                            # 缺口（空天/缺餐/住宿夜数/必去项）——**一律只从真实候选里取，绝不编造**；
                            # 修不动的（价格不可核实、结构性改动）留给用户确认。掉门禁或掉分即回滚，
                            # 修补毫无改动就停（不空转烧预算）。跑在治理快照之前，保证面板上的分数
                            # 与门禁都基于修好的方案。
                            try:
                                from core.plan_review import review_plan

                                _review = review_plan(
                                    _quality_context,
                                    final_data,
                                    signals=_quality_signals,
                                    max_rounds=3,
                                    pool=_pool or None,
                                )
                                _reviewed_plan = _review.get("plan")
                                if isinstance(_reviewed_plan, dict) and isinstance(_reviewed_plan.get("route"), list):
                                    final_data["route"] = _reviewed_plan["route"]
                                _review_actions = list(_review.get("actions") or [])
                                final_data["review"] = {
                                    "stopped_reason": _review.get("stopped_reason"),
                                    "rounds": len(_review.get("rounds") or []),
                                    "actions": _review_actions[:8],
                                    "action_count": len(_review_actions),
                                    "initial_score": _review.get("initial_score"),
                                    "final_score": _review.get("final_score"),
                                    "initial_hard_failures": _review.get("initial_hard_failures"),
                                    "remaining_hard": _review.get("remaining_hard"),
                                    "needs_data": _review.get("needs_data") or [],
                                    "used_pool": bool(_pool),
                                }
                                # 事件流：自动复核**真的改动了**方案才发（action_count=0 时发就是噪音）。
                                # 带改了几处、修前修后的分、还剩几处硬失败 —— 事后能回答
                                # "这套方案被机器改过什么、改完之后好没好"。
                                if len(_review_actions) > 0:
                                    await event_bus.emit(OMNI_EVENTS_STREAM, "route.repaired", {
                                        "room_id": room_id, "city": target_city,
                                        "action_count": len(_review_actions),
                                        "action_codes": sorted({
                                            str(_item.get("code") or "unknown")
                                            for _item in _review_actions if isinstance(_item, Mapping)
                                        }),
                                        "initial_score": _review.get("initial_score"),
                                        "final_score": _review.get("final_score"),
                                        "remaining_hard": _review.get("remaining_hard"),
                                        "stopped_reason": _review.get("stopped_reason"),
                                        "correlation_id": plan_correlation_id,
                                    }, room_id, correlation_id=plan_correlation_id, producer="coordinator")
                            except Exception as _review_exc:  # 复核失败绝不影响出方案
                                final_data["review"] = {"error": str(_review_exc)[:200]}

                            # 候选池只在"需要平替/换点/改配额"时才去取；数据源失败不影响出方案。
                            _governance = await prepare_governance(
                                _quality_context,
                                final_data,
                                signals=_quality_signals,
                                city=target_city,
                                fetch=getattr(toolbox, "get_dynamic_pois", None),
                                request_text=intent_str,
                                increment=_refinement_delta,
                                pool=_pool or None,
                            )
                            _snapshot = _governance["snapshot"]
                            # 增量真的改了方案：用调整后的路线替换（前端以 final_route 为准）
                            _adjusted = _governance.get("plan")
                            if isinstance(_adjusted, dict) and isinstance(_adjusted.get("route"), list):
                                if _governance.get("exclusions") or _governance.get("quota"):
                                    final_data["route"] = _adjusted["route"]
                            final_data["quality"] = _snapshot["quality"]
                            final_data["budget_report"] = _snapshot["budget"]
                            final_data["horizon"] = _snapshot["horizon"]
                            if _governance.get("pool_sizes"):
                                final_data["candidate_pool"] = _governance["pool_sizes"]
                            # 真实候选够不够、有没有补检索、供应商有没有报错：随方案下发（界面据此
                            # 说明"哪些地点来自数据源、哪些是靠模型知识补的"）
                            if candidate_supply:
                                final_data["candidate_supply"] = candidate_supply
                            if _governance.get("increment"):
                                final_data["increment"] = _governance["increment"]
                            # 升/降档 → 排序偏好（网关 ranker 下次推演会读这些键；本次的配额填充与
                            # 兜底方向已经在核心层按同一份策略执行）
                            if _governance.get("tier_policy"):
                                final_data["tier_policy"] = _governance["tier_policy"]
                            if _governance.get("ranking_preferences"):
                                final_data["ranking_preferences"] = _governance["ranking_preferences"]

                            # 👑 用户诉求逐条核对（后端真值字段）：每条诉求一个稳定 id + 状态
                            # （已落实 / 部分落实 / 未核实 / 仅建议 / 没做到），前端逐条渲染。
                            # 教训来自同类开源项目：它把这些字段算出来了，但前端适配层从没映射，
                            # 界面上"未核实"一次都没出现 —— **没渲染出来的未核实，等于没做**。
                            try:
                                from core.constraint_coverage import constraint_coverage

                                final_data["constraints"] = constraint_coverage(
                                    _quality_context,
                                    final_data,
                                    signals=_quality_signals,
                                    increment=_refinement_delta,
                                    increment_metrics=_governance.get("increment"),
                                    tier_policy_map=_governance.get("tier_policy"),
                                    exclusions=_governance.get("exclusions"),
                                    request_text=intent_str,
                                )
                            except Exception as _coverage_exc:  # 诉求核对失败绝不影响出方案
                                final_data["constraints"] = {"error": str(_coverage_exc)[:200]}

                            # 👑 价格补全（有界）：只查**缺价**节点、并发执行、整体超时保护；
                            # 来源标为 web → 结构上仍是"估算"，必须标明，不会冒充已验证价格。
                            try:
                                from core.price_sources import (
                                    merge_observations,
                                    parse_price_from_text,
                                    pending_price_report,
                                    pending_price_targets,
                                    price_coverage,
                                    summarize_merge,
                                )

                                # 补价节点数改为**可配置**（默认 12），并且先诊断"取价源通不通"：
                                # 实测（2026-10-04）本机 DuckDuckGo 直接失败（回退 bing 也返回 None），
                                # 于是"一个价都没补上"——如果不诊断，用户只会看到一片"未取到"，
                                # 根本不知道是搜索源不可达。现在把原因如实写进 price_audit 与降级清单。
                                from core.web_price_tool import (
                                    affordable_target_count,
                                    price_budget_seconds,
                                    price_max_nodes,
                                )

                                _price_budget = price_budget_seconds()
                                _price_node_cap = affordable_target_count(price_max_nodes(), budget_seconds=_price_budget)
                                _targets = pending_price_targets(final_data, limit=_price_node_cap)
                                _pending_total = len(pending_price_report(final_data)["pending"])
                                _fetcher = getattr(toolbox, "get_real_time_web_price", None)
                                if _targets and _fetcher:
                                    async def _lookup(_item: Dict[str, Any]) -> Dict[str, Any]:
                                        """查一个节点的价格：搜索 + **能直抓官方页面就直抓**，带出处。

                                        统一走 `web_price_tool.lookup_price`（一处口径）：
                                        摘要抽价 → 命中白名单页面再抓正文抽价 → 拿不到如实 None。
                                        """
                                        from core.web_price_tool import lookup_price

                                        _name = str(_item.get("name") or "")
                                        try:
                                            return await lookup_price(_name, target_city, max_results=3)
                                        except Exception as _lookup_exc:
                                            from core.web_price_tool import build_observation

                                            return build_observation(
                                                name=_name, value=None, source="web", provider="search"
                                            ) | {"error": f"{type(_lookup_exc).__name__}: {str(_lookup_exc)[:120]}"}

                                    _observations = await asyncio.wait_for(
                                        asyncio.gather(*[_lookup(item) for item in _targets]),
                                        timeout=_price_budget,
                                    )
                                    _merged = merge_observations(final_data, list(_observations))
                                    if _merged["updated"]:
                                        final_data["route"] = _merged["plan"]["route"]
                                    _report = pending_price_report(final_data)
                                    # 取价源状态**从真实取价尝试里推导**（而不是再单独诊断一次）：
                                    # 单测/实测都表明"诊断比功能本身更容易超时"，
                                    # 早先那次 TimeoutError 就是这样把整个补价带走的。
                                    from core.web_price_tool import last_search_attempts as _attempts

                                    _reachable = None
                                    _attempt_error = ""
                                    if _attempts:
                                        _reachable = any(bool(item.get("ok")) for item in _attempts)
                                        for _attempt in _attempts:
                                            if not _attempt.get("ok") and _attempt.get("error"):
                                                _attempt_error = str(_attempt["error"])[:160]
                                                break
                                    _filled = [
                                        item for item in (_observations or [])
                                        if isinstance(item, Mapping) and item.get("value") is not None
                                    ]
                                    final_data["price_audit"] = {
                                        **_merged["coverage"],
                                        "updated": len(_merged["updated"]),
                                        "still_unknown": _merged["coverage"]["unknown"][:5],
                                        "summary": summarize_merge(_merged),
                                        "pending": _report["pending"],
                                        "pending_summary": _report["summary"],
                                        # 取价源的可达性与失败原因（用户能看到"为什么没价"）
                                        "provider_reachable": _reachable,
                                        "provider_attempts": list(_attempts or [])[:4],
                                        "provider_status": {"error": _attempt_error} if _attempt_error else {},
                                        "looked_up": len(_targets),
                                        "filled_from_web": len(_filled),
                                        # 预算只够查这么多：剩下的如实留给「需要你确认」，
                                        # 不让用户以为"系统把该查的都查了"
                                        "budget_seconds": _price_budget,
                                        "not_looked_up": max(0, _pending_total - len(_targets)),
                                    }
                                    print(
                                        f"🌐 [联网取价] 查了 {len(_targets)}/{_pending_total} 个缺价节点，补到 {len(_filled)} 条"
                                        f"（预算 {_price_budget:.0f}s，来源可达={_reachable}）"
                                        + (f"；失败原因：{_attempt_error}" if _attempt_error else ""),
                                        flush=True,
                                    )
                                else:
                                    _coverage = price_coverage(final_data)
                                    _report = pending_price_report(final_data)
                                    # 这一步**没跑**时要写清原因：是"没有缺价节点"还是"取价函数缺失"，
                                    # 否则界面上只能看到"未核实"，无法判断是没查还是查了没结果。
                                    _skip_reason = (
                                        "所有节点都已有可核实价格，无需补价"
                                        if not _targets
                                        else "取价函数不可用（工具箱未提供 get_real_time_web_price）"
                                    )
                                    final_data["price_audit"] = {
                                        **_coverage,
                                        "pending": _report["pending"],
                                        "pending_summary": _report["summary"],
                                        # 没跑这一步也要留痕：否则界面只能显示"未核实"，
                                        # 分不清是"没去查"还是"查了没结果"
                                        "provider_reachable": None,
                                        "skipped_reason": _skip_reason,
                                    }
                            except Exception as _price_exc:  # 价格补全失败绝不能影响出方案
                                # 但要**说出来**：早先这里只写 coverage，把异常咽掉了 ——
                                # 于是"补价这一整段没跑"在界面上长得跟"查了没结果"一模一样。
                                print(f"⚠️ [价格补全] 这一步失败（不影响出方案）: {type(_price_exc).__name__}: {_price_exc}", flush=True)
                                try:
                                    from core.price_sources import price_coverage as _coverage

                                    final_data["price_audit"] = {
                                        **_coverage(final_data),
                                        "error": f"{type(_price_exc).__name__}: {str(_price_exc)[:180]}",
                                    }
                                except Exception:
                                    final_data["price_audit"] = {"error": f"{type(_price_exc).__name__}: {str(_price_exc)[:180]}"}

                            # 👑 跨城腿的出行方式比较（有界：最多 2 段；票价无来源就标未核实）
                            try:
                                from core.transport_options import build_transport_audit, find_transfer_legs

                                _transfer_legs = find_transfer_legs(final_data, min_km=150, max_legs=3)
                                if _transfer_legs:
                                    final_data["transport_audit"] = await build_transport_audit(
                                        _transfer_legs,
                                        getattr(toolbox, "get_travel_options", None),
                                        city=target_city,
                                        preferences={
                                            "transport_preference": (user_prefs or {}).get("transport_preference"),
                                            "pace": intent_str,
                                        },
                                        max_legs=2,
                                    )
                            except Exception as _transport_exc:  # 出行比较失败绝不能影响出方案
                                final_data["transport_audit"] = {"error": str(_transport_exc)[:200]}
                            if _snapshot.get("fallback"):
                                final_data["fallback"] = _snapshot["fallback"]
                        except Exception as _quality_exc:  # 质量评估失败绝不能影响出方案
                            final_data["quality"] = {"error": str(_quality_exc)[:200]}
                        # 👑 硬约束过滤（禁令）：用户明确说了"不要博物馆/人文"，就不能只是报警 ——
                        #    模型自己塞进来的也真的换掉或移出（从候选池同类替换），并在事件流留痕。
                        #    这一步放在配比对账之前，好让对账看到修复后的真实路线。
                        if own_signals.get("exclude_cultural"):
                            try:
                                _replacement_pool: List[Dict[str, Any]] = []
                                for _item in list(unique_attractions or []) + list(unique_foods or []):
                                    if isinstance(_item, dict):
                                        _replacement_pool.append(_item)
                                _route_before_exclusion = list(final_data.get("route") or [])
                                _exclusion_kept, _exclusion_actions = repair_forbidden_categories(
                                    _route_before_exclusion,
                                    forbidden=("cultural",),
                                    classify=classify_poi_category,
                                    pool=_replacement_pool,
                                )
                                if _exclusion_actions:
                                    final_data["route"] = _exclusion_kept
                                    final_data["exclusion_repair"] = {
                                        "forbidden": ["cultural"],
                                        "actions": _exclusion_actions,
                                    }
                                    await event_bus.emit(OMNI_EVENTS_STREAM, "route.repaired", {
                                        "room_id": room_id, "city": target_city,
                                        "action_count": len(_exclusion_actions),
                                        "action_codes": sorted({str(item.get("code") or "") for item in _exclusion_actions}),
                                        "examples": [
                                            f"{item.get('from')} → {item.get('to') or '（移出）'}"
                                            for item in _exclusion_actions[:5]
                                        ],
                                        "correlation_id": plan_correlation_id,
                                    }, room_id, correlation_id=plan_correlation_id, producer="validator")
                                    print(
                                        f"🚫 [硬约束过滤] 排除人文：修复 {len(_exclusion_actions)} 处 → "
                                        + "；".join(
                                            f"{item.get('from')} → {item.get('to') or '（移出）'}"
                                            for item in _exclusion_actions[:4]
                                        ),
                                        flush=True,
                                    )
                            except Exception as _exclusion_exc:  # 修复失败绝不影响出方案
                                final_data["exclusion_repair"] = {"error": str(_exclusion_exc)[:200]}

                        # 👑 城市公园限量落到**最终路线**上：候选池限量只管住"模型能看到什么"，
                        #    模型照样能多排（实测 3 个 > 上限 2）。这里把超出的换成真景区/美食，
                        #    换不了就移出，并留痕（与远景区修复同一套路）。
                        try:
                            _park_trim_pool: List[Dict[str, Any]] = [
                                dict(item)
                                for item in list(unique_attractions or []) + list(unique_foods or [])
                                if isinstance(item, dict)
                            ]
                            _park_trimmed, _park_actions = trim_category_over_cap(
                                final_data.get("route") or [],
                                cap=int(_park_allowed or 0),
                                classify=classify_poi_category,
                                scenic_tier_of=scenic_tier,
                                category="city_park",
                                pool=_park_trim_pool,
                            )
                            if _park_actions:
                                final_data["route"] = _park_trimmed
                                final_data["park_trim"] = {"cap": int(_park_allowed or 0), "actions": _park_actions}
                                await event_bus.emit(OMNI_EVENTS_STREAM, "route.repaired", {
                                    "room_id": room_id, "city": target_city,
                                    "action_count": len(_park_actions),
                                    "action_codes": sorted({str(item.get("code")) for item in _park_actions}),
                                    "examples": [
                                        f"{item.get('from')} → {item.get('to') or '（移出）'}"
                                        for item in _park_actions[:4]
                                    ],
                                    "correlation_id": plan_correlation_id,
                                }, room_id, correlation_id=plan_correlation_id, producer="validator")
                                print(
                                    f"🌳 [公园限量] 路线里超出的公园已处理 {len(_park_actions)} 处："
                                    + "；".join(
                                        f"{item.get('from')} → {item.get('to') or '（移出）'}"
                                        for item in _park_actions[:3]
                                    ),
                                    flush=True,
                                )
                        except Exception as _park_exc:  # 修复失败绝不影响出方案（对账仍会报超限）
                            final_data["park_trim"] = {"error": str(_park_exc)[:200]}

                        # 👑 配比对账：实际排出来的构成 vs 用户口径算出的目标（默认自然=人文各半
                        # + 穿插美食住宿；明确偏好则加权）。对不上就如实记账，进"遗留问题"。
                        try:
                            _route_counts = count_route_composition(
                                final_data.get("route") or [], classify_poi_category, scenic_tier
                            )
                            _composition_audit = composition_audit(
                                _route_counts,
                                composition,
                                # 用户明说不要人文时，"一个都不许有"要当成硬判据（目标 0 默认会被跳过）
                                forbidden=("cultural",) if own_signals.get("exclude_cultural") else (),
                            )
                            # 城市公园上限也要对账：候选池限了量，但模型仍可能多排 —— 超了就说出来，
                            # 不能让"默认 1~2 个"只活在候选池那一步（实测路线里出现过 3 个公园 > 上限 2）。
                            _urban_park = int(_route_counts.get("city_park") or 0)
                            if _urban_park > int(_park_allowed or 0):
                                _composition_audit["notes"] = list(_composition_audit.get("notes") or []) + [
                                    f"城市公园排了 {_urban_park} 个，超过上限 {_park_allowed} 个（想把公园换成景区/吃饭可以直接说）"
                                ]
                                _composition_audit["ok"] = False
                                _composition_audit["deviations"] = list(_composition_audit.get("deviations") or []) + [
                                    {"intent": "city_park", "target": int(_park_allowed or 0), "actual": _urban_park, "severity": "over"}
                                ]
                            composition["park_cap"] = int(_park_allowed or 0)
                            final_data["composition"] = {
                                **composition,
                                "actual": _route_counts,
                                "audit": _composition_audit,
                            }
                            print(
                                f"⚖️ [配比对账] 实际 自然景区={_route_counts.get('landmark')}/城市公园={_route_counts.get('city_park')}"
                                f"/人文={_route_counts.get('cultural')}/美食={_route_counts.get('food')}/住宿={_route_counts.get('hotel')}"
                                f" | 目标 {composition.get('scenic')}/{composition.get('cultural')}/{composition.get('food')}/{composition.get('hotel')}"
                                f" | {'一致' if _composition_audit['ok'] else _composition_audit['notes']}",
                                flush=True,
                            )
                        except Exception as _comp_exc:  # 对账失败绝不影响出方案
                            final_data["composition"] = {"error": str(_comp_exc)[:200]}

                        # 👑 硬约束过滤：日内物理现实性（用户"不考虑距离"≠"一天能跑 500km"）。
                        #    实测事故：3 天方案里同时排了博斯腾湖（47km）与巴音布鲁克（270km）。
                        #    发现同一天塞了多个远景区就**移出多余的**（保留最近的，每天至少留一个），
                        #    并把这件事写进事件流与「遗留问题」—— 这才是闭环里的"硬约束过滤"阶段。
                        try:
                            from core.travel_feasibility import (
                                evaluate_travel_feasibility,
                                repair_day_overload,
                            )

                            _center_for_check: List[float] = []
                            _center_text_check = str(geo_info.get("center") or coord or "")
                            if "," in _center_text_check:
                                try:
                                    _lng_check, _, _lat_check = _center_text_check.partition(",")
                                    _center_for_check = [float(_lng_check), float(_lat_check)]
                                except ValueError:
                                    _center_for_check = []
                            _before = evaluate_travel_feasibility(
                                final_data.get("route") or [],
                                city_center=_center_for_check,
                                distance_tolerance=str(own_signals.get("distance_tolerance") or "normal"),
                                days=trip_days,
                            )
                            if not _before.get("ok"):
                                _kept_route, _moved_out = repair_day_overload(
                                    final_data.get("route") or [],
                                    city_center=_center_for_check,
                                    distance_tolerance=str(own_signals.get("distance_tolerance") or "normal"),
                                )
                                if _moved_out:
                                    final_data["route"] = _kept_route
                                    final_data["travel_repair"] = {
                                        "moved_out": [
                                            {"name": str(item.get("name") or ""), "day": item.get("day"), "reason": item.get("removed_reason")}
                                            for item in _moved_out
                                        ],
                                        "reason": "一天跑不了两个远景区",
                                    }
                                    await event_bus.emit(OMNI_EVENTS_STREAM, "route.repaired", {
                                        "room_id": room_id, "city": target_city,
                                        "action_count": len(_moved_out),
                                        "action_codes": ["too_many_far_scenic_in_one_day"],
                                        "moved_out": [str(item.get("name") or "") for item in _moved_out][:5],
                                        "correlation_id": plan_correlation_id,
                                    }, room_id, correlation_id=plan_correlation_id, producer="validator")
                                _after = evaluate_travel_feasibility(
                                    final_data.get("route") or [],
                                    city_center=_center_for_check,
                                    distance_tolerance=str(own_signals.get("distance_tolerance") or "normal"),
                                    days=trip_days,
                                )
                                final_data["travel_feasibility"] = _after
                                if not _after.get("ok"):
                                    await event_bus.emit(OMNI_EVENTS_STREAM, "constraint.failed", {
                                        "room_id": room_id, "city": target_city,
                                        "violations": _after.get("violations", [])[:10],
                                        "total": len(_after.get("violations", [])),
                                        "correlation_id": plan_correlation_id,
                                    }, room_id, correlation_id=plan_correlation_id, producer="validator")
                                print(
                                    f"🚧 [硬约束过滤] 日内现实性：{'修复 ' + str(len(_moved_out)) + ' 个节点' if _moved_out else '无法自动修复'}"
                                    f" → {'一致' if _after.get('ok') else _after.get('notes')}",
                                    flush=True,
                                )
                            else:
                                final_data["travel_feasibility"] = _before
                        except Exception as _travel_exc:  # 检查失败绝不影响出方案
                            final_data["travel_feasibility"] = {"error": str(_travel_exc)[:200]}

                        # 👑 数据降级登记：哪个数据源没取到、用户损失了什么，统一在这里说一次
                        # （文案只在 core/degradation.DATA_SOURCES 里定义，前端照抄渲染）。
                        # 放在最后：前面所有 payload 字段都已经定稿，推导才准确。
                        try:
                            from core.degradation import collect_degradations

                            final_data["degradations"] = collect_degradations(final_data)
                        except Exception as _degrade_exc:  # 登记失败绝不影响出方案
                            final_data["degradations"] = {"error": str(_degrade_exc)[:200]}

                        # 👑 统一事实记录（闭环第①段）：把"依据了什么/决定了什么/缺了什么"
                        #    收成**一份带出处与核实状态**的事实，随方案下发并发一条事件。
                        #    为什么放在最后：前面所有字段（供给/配比/硬约束/画像/降级）都已定稿，
                        #    这份记录才是这一轮真正的事实快照。
                        try:
                            from core.plan_facts import build_plan_facts, summarize_facts

                            _facts = build_plan_facts(
                                correlation_id=plan_correlation_id,
                                city=target_city,
                                trip_days=trip_days,
                                intent=intent_str,
                                signals=own_signals,
                                composition=composition,
                                supply=candidate_supply,
                                feasibility=final_data.get("travel_feasibility"),
                                profile_applied=final_data.get("profile_applied"),
                                reference_profile=final_data.get("reference_profile"),
                                route=final_data.get("route"),
                                degradations=final_data.get("degradations"),
                            )
                            final_data["facts"] = _facts
                            # 👑 协商纪要（给用户看的紧凑版）：谁参与了、站在什么立场、
                            #    争了什么、裁决凭什么、谁没参与 —— 多智能体协同要看得见，
                            #    而不是只活在事件流里。
                            _conflict_record = specialist_runtime_result.get("proposal_conflicts") or {}
                            final_data["negotiation"] = {
                                "facts_version": facts_version_string,
                                "agents": [
                                    {
                                        "agent_id": str(item.get("agent_id") or ""),
                                        "domain": str(item.get("domain") or ""),
                                        "prefers": str(item.get("prefers") or ""),
                                        "candidates": int(item.get("candidates") or 0),
                                        "status": str(item.get("status") or ""),
                                    }
                                    for item in (_conflict_record.get("stances") or [])[:12]
                                ],
                                "inactive": [
                                    {
                                        "agent_id": str(item.get("agent_id") or ""),
                                        "reason": str(item.get("reason") or "")[:120],
                                    }
                                    for item in (_conflict_record.get("inactive") or [])[:8]
                                ],
                                "conflicts": [
                                    {
                                        "code": str(item.get("code") or ""),
                                        "between": [str(name) for name in (item.get("between") or [])],
                                        "detail": str(item.get("detail") or "")[:240],
                                        "severity": str(item.get("severity") or ""),
                                    }
                                    for item in (_conflict_record.get("conflicts") or [])[:6]
                                ],
                                "arbitration": [
                                    {
                                        "code": str(item.get("code") or ""),
                                        "decision": str(item.get("decision") or "")[:200],
                                        "mechanism": str(item.get("mechanism") or "")[:160],
                                        "why": str(item.get("why") or "")[:200],
                                    }
                                    for item in (_conflict_record.get("arbitration") or [])[:6]
                                ],
                                "notes": [str(item)[:200] for item in (_conflict_record.get("notes") or [])[:4]],
                                # 协商回环的结果（分歧解掉没、谁按裁决重提了什么）
                                "revision": (
                                    {
                                        "round": int((_conflict_record.get("revision") or {}).get("round") or 1),
                                        "revised_agents": [
                                            str(name) for name in (_conflict_record.get("revision") or {}).get("revised_agents") or []
                                        ],
                                        "conflicts_before": list((_conflict_record.get("revision") or {}).get("conflicts_before") or []),
                                        "conflicts_after": list((_conflict_record.get("revision") or {}).get("conflicts_after") or []),
                                        "resolved": list((_conflict_record.get("revision") or {}).get("resolved") or []),
                                        "changed": [
                                            {
                                                "agent_id": str(item.get("agent_id") or ""),
                                                "applied": item.get("applied") or {},
                                                "candidates_after": item.get("candidates_after"),
                                            }
                                            for item in ((_conflict_record.get("revision") or {}).get("changed") or [])[:3]
                                        ],
                                        "notes": [str(item)[:200] for item in ((_conflict_record.get("revision") or {}).get("notes") or [])[:3]],
                                        "error": str((_conflict_record.get("revision") or {}).get("error") or "")[:200],
                                    }
                                    if _conflict_record.get("revision")
                                    else None
                                ),
                            }
                            # 事实记录里也带上提案阶段那一版快照的版本号：整轮规划可对齐
                            _facts["facts_version"] = facts_version_string
                            _facts["proposal_alignment"] = specialist_runtime_result.get("facts_alignment")
                            # 提案分歧与裁决也进事实记录（"当时分歧在哪、凭什么这么定"）
                            _facts["proposal_conflicts"] = {
                                "codes": [str(item.get("code")) for item in _conflict_record.get("conflicts") or []],
                                "arbitration": [
                                    {"code": str(item.get("code")), "mechanism": str(item.get("mechanism"))}
                                    for item in _conflict_record.get("arbitration") or []
                                ],
                                "inactive_agents": [
                                    str(item.get("agent_id")) for item in _conflict_record.get("inactive") or []
                                ],
                            }
                            await event_bus.emit(OMNI_EVENTS_STREAM, "plan.facts.ready", {
                                "room_id": room_id,
                                "user_id": user_id,
                                **summarize_facts(_facts),
                            }, room_id, correlation_id=plan_correlation_id, producer="coordinator")
                            _gaps = len(_facts.get("gaps") or [])
                            print(
                                f"🧾 [统一事实] schema=v{_facts.get('schema_version')} 城市={target_city} "
                                f"节点={len(final_data.get('route') or [])} 缺口={_gaps} 条",
                                flush=True,
                            )
                        except Exception as _facts_exc:  # 事实记录失败绝不影响出方案
                            final_data["facts"] = {"error": str(_facts_exc)[:200]}

                    # 👑 ③ 实景图补齐 + ①② 影像兜底（放在下发前最后一步）
                    # 顺序很重要：先尽力拿**真照片**（高德官方，最多 8 个节点、并发 + 整体超时），
                    # 拿不到的才给卫星影像/街道图兜底，并如实标注"位置示意，非实景照片"。
                    try:
                        from core.poi_photos import attach_photo_fallbacks, has_real_photo

                        _targets = [node for node in _photo_targets if isinstance(node, dict)][:8]
                        if _targets and getattr(toolbox, "amap_key", None):
                            async def _fetch_photos(_node: Dict[str, Any]) -> None:
                                try:
                                    urls = await toolbox.get_place_photos(
                                        str(_node.get("name") or ""), str(final_data.get("city") or "")
                                    )
                                except Exception:
                                    urls = []
                                if urls:
                                    _node["photos"] = urls
                                    _node["photo_source"] = "amap"

                            try:
                                await asyncio.wait_for(
                                    asyncio.gather(*[_fetch_photos(node) for node in _targets]), timeout=8.0
                                )
                            except Exception:
                                pass  # 超时也要继续：没取到的走影像兜底
                        _with_fallback = attach_photo_fallbacks(final_data)
                        final_data["route"] = _with_fallback["route"]
                        if _with_fallback["filled"]:
                            final_data["photo_fallback_stats"] = {
                                "filled": _with_fallback["filled"],
                                "kinds": _with_fallback["kinds"],
                            }
                    except Exception as _photo_exc:  # 配图失败绝不影响出方案
                        print(f"⚠️ [配图] 补齐失败，继续下发: {_photo_exc}", flush=True)

                    # Final daily-density gate.  Fairness, bandit, review and
                    # long-trip stages may replace the route after the first
                    # balance pass; run the same deterministic check immediately
                    # before emitting and caching the result.
                    try:
                        from core.itinerary_balance import enforce_category_mix, rebalance_daily_route

                        _final_target = target_daily_count if role == "深度探索" else min(target_daily_count, 5)
                        _balanced = rebalance_daily_route(
                            final_data.get("route") or [],
                            poi_pool_data,
                            days=trip_days,
                            target_daytime=_final_target,
                        )
                        final_data["route"] = _balanced["route"]
                        llm_routes = final_data["route"]
                        final_data["daily_balance"] = _balanced["audit"]
                        final_data["daily_balance"]["gate"] = "final_pre_emit"

                        # 普通游的最终门禁：用户没有明确要求“只看博物馆”时，
                        # 每天至少出现景区、特色餐饮、人文/地标三类体验，且文化场馆
                        # 不得占掉大多数白天节点。只使用同一候选池中的真实 POI 替换，
                        # 不编造名称、价格或坐标。
                        _culture_only_request = bool(pref_signals.get("wants_cultural")) and not any(
                            pref_signals.get(key) for key in ("wants_food", "wants_scenic", "wants_hotel", "wants_shopping")
                        )
                        if not _culture_only_request:
                            _mixed = enforce_category_mix(
                                final_data["route"],
                                poi_pool_data,
                                days=trip_days,
                                target_daytime=_final_target,
                                required_categories=("scenic", "food") if pref_signals.get("exclude_cultural") else ("scenic", "food", "cultural"),
                                max_cultural_share=0 if pref_signals.get("exclude_cultural") else 0.4,
                            )
                            final_data["route"] = _mixed["route"]
                            llm_routes = final_data["route"]
                            final_data["category_balance"] = _mixed["audit"]
                            final_data["daily_balance"] = {
                                **final_data["daily_balance"],
                                "category_balance": _mixed["audit"],
                            }
                        _final_fairness = fairness_report(final_data["route"], fairness_members or room_members)
                        _fairness_meta = final_data.get("fairness") or {}
                        _threshold = float(_fairness_meta.get("threshold", 0.15) or 0.15)
                        final_data["fairness"] = {
                            **_fairness_meta,
                            **_final_fairness,
                            "threshold": _threshold,
                            "threshold_exceeded": float(_final_fairness.get("utility_stddev", 0)) > _threshold,
                            "repair_attempted": bool(_fairness_meta.get("repair_attempted", False)),
                            "repair_status": _fairness_meta.get("repair_status", "not_needed"),
                        }
                        try:
                            final_data["constraints"] = validate_route(
                                final_data["route"],
                                budget=total_calc_budget if budget_mode == "EXACT_AMOUNT" else None,
                                days=trip_days,
                                max_nodes_per_day=_final_target + 1,
                                unique_locations=True,
                            )
                        except Exception:
                            pass
                        # 事件流：路线校验结果 + 硬约束失败。此前这两件事只回填到
                        # final_data.constraints 给面板看，事件流里查不到 —— 事后无法回答
                        # "这次方案到底过没过硬约束、卡在哪一条"。
                        _constraint_report = final_data.get("constraints") if isinstance(final_data.get("constraints"), dict) else {}
                        _violations = _constraint_report.get("violations") or _constraint_report.get("hard_failures") or []
                        # validate_route 的真实字段是 feasible（不是 passed）：取不到就按"有没有违规"推，
                        # 不硬编一个"通过"出来
                        _passes = _constraint_report.get("feasible")
                        if not isinstance(_passes, bool):
                            _passes = not _violations
                        await event_bus.emit(OMNI_EVENTS_STREAM, "route.validated", {
                            "room_id": room_id, "city": target_city,
                            "passed": _passes,
                            "violations": len(_violations) if isinstance(_violations, list) else 0,
                            "correlation_id": plan_correlation_id,
                        }, room_id, correlation_id=plan_correlation_id, producer="validator")
                        if isinstance(_violations, list) and _violations:
                            await event_bus.emit(OMNI_EVENTS_STREAM, "constraint.failed", {
                                "room_id": room_id, "city": target_city,
                                "violations": _violations[:10],
                                "total": len(_violations),
                                "correlation_id": plan_correlation_id,
                            }, room_id, correlation_id=plan_correlation_id, producer="validator")
                        # 住宿联合评估（P2）：路线定型后，把"住哪"的连锁影响（通勤/费用/体力/
                        # 碳排/可玩节点/预算）一并交给用户，而不是只报房价。
                        try:
                            from core.joint_optimization import lodging_joint_payload

                            final_data["lodging_joint"] = lodging_joint_payload(
                                cleaned_hotels, final_data.get("route")
                            )
                        except Exception as _joint_exc:
                            final_data["lodging_joint"] = {"status": "failed", "error": str(_joint_exc)[:200]}
                        try:
                            from core.price_sources import pending_price_report, price_coverage

                            _final_coverage = price_coverage(final_data)
                            _final_pending = pending_price_report(final_data)
                            final_data["price_audit"] = {
                                **(final_data.get("price_audit") or {}),
                                **_final_coverage,
                                "pending": _final_pending["pending"],
                                "pending_summary": _final_pending["summary"],
                            }
                        except Exception:
                            pass
                    except Exception as _final_balance_exc:
                        final_data["daily_balance"] = {"gate": "final_pre_emit", "error": str(_final_balance_exc)[:200]}

                    # 把本次规划的关联键交给前端/网关：以后用户采纳、确认重规划、标记到访
                    # 都能带着同一个 correlation_id 回到事件流里（否则审计只能看到孤立的片段）
                    final_data["correlation_id"] = plan_correlation_id
                    # 这轮到底吃没吃进学习到的画像，作为结构化事实随结果下发（界面「这次怎么定的口味」用它）
                    final_data["profile_applied"] = build_profile_applied(personalized_profile)
                    # 参考画像披露：这次是按"你参考过的路线偏好"来的，说清依据，并给出覆盖入口
                    if reference_hint:
                        final_data["reference_profile"] = {
                            "applied": True,
                            "reference_count": reference_hint.get("reference_count"),
                            "reasons": reference_hint["hints"].get("reasons") or [],
                            "disclosure": reference_hint["disclosure"],
                        }
                    yield json.dumps({"type": "final_route", "payload": final_data}, ensure_ascii=False) + "\n"
                    # 事件流：行程生成完成事件（emit 到全局 Redis Stream）
                    await event_bus.emit(OMNI_EVENTS_STREAM, "final_route_ready", {
                        "room_id": room_id, "user_id": user_id, "city": target_city,
                        "variants": len(final_data.get("plan_variants", [])),
                        "correlation_id": plan_correlation_id,
                    }, room_id, correlation_id=plan_correlation_id, producer="coordinator")
                    # 事件流：这次规划最终选中的是哪一套。与 route.drafted 对照，
                    # 就能回答"草案 → 定稿"之间到底发生了什么（而不是只看一个结果）
                    await event_bus.emit(OMNI_EVENTS_STREAM, "route.selected", {
                        "room_id": room_id, "city": target_city,
                        "nodes": len(final_data.get("route") or []),
                        "variants": len(final_data.get("plan_variants", [])),
                        "status": final_data.get("status"),
                        "correlation_id": plan_correlation_id,
                    }, room_id, correlation_id=plan_correlation_id, producer="coordinator")

                    # 🚀 写入 LLM 语义缓存（fire-and-forget，不阻塞后续 P2P 导航推送）
                    if not is_refinement:
                        asyncio.ensure_future(_semantic_cache.store(target_city, trip_days, profile_text, {
                            "final_data": final_data,
                            "budget_breakdown": budget_breakdown_payload,
                            "target_city": {"name": target_city, "lnglat": [float(x) for x in coord.split(",")] if coord and "," in coord else []},
                        }))

                    # P2P 精准导航计算（覆盖全部相邻节点，保证 5 天 25 个节点 24 对都有真实驾车/步行/公交导航）
                    def _valid_pt(c):
                        if not (isinstance(c, list) and len(c) >= 2):
                            return False
                        try:
                            lng, lat = float(c[0]), float(c[1])
                        except (TypeError, ValueError):
                            return False
                        return not (lng == 0.0 and lat == 0.0)

                    post_p2p_tasks = []
                    post_pair_coords = []
                    for i in range(len(llm_routes) - 1):
                        r1, r2 = llm_routes[i], llm_routes[i+1]
                        l1, l2 = r1.get("lnglat"), r2.get("lnglat")
                        if _valid_pt(l1) and _valid_pt(l2):
                            c1 = (float(l1[0]), float(l1[1]))
                            c2 = (float(l2[0]), float(l2[1]))
                            str_loc1 = f"{c1[0]},{c1[1]}"
                            str_loc2 = f"{c2[0]},{c2[1]}"
                            pair_key = f"{r1.get('location')}|{r2.get('location')}"
                            post_pair_coords.append((pair_key, r1.get('location'), c1, c2))
                            post_p2p_tasks.append(toolbox.get_travel_options(str_loc1, str_loc2, target_city))

                    # 👑 回头路检测：计算相邻导航段航向夹角，>150° 视为折返（U 型回头），
                    #    用于验证天级空间重排效果并暴露残留折返段（仅观测，不改变拼接行为）。
                    def _bearing(a, b):
                        import math
                        lng1, lat1 = a
                        lng2, lat2 = b
                        p1, p2 = math.radians(lat1), math.radians(lat2)
                        dl = math.radians(lng2 - lng1)
                        y = math.sin(dl) * math.cos(p2)
                        x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
                        return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

                    _seg_bearings = [_bearing(c1, c2) for (_pk, _ln, c1, c2) in post_pair_coords]
                    _backtrack_count = 0
                    for _bi in range(1, len(_seg_bearings)):
                        _diff = abs(_seg_bearings[_bi] - _seg_bearings[_bi - 1]) % 360.0
                        _diff = min(_diff, 360.0 - _diff)
                        if _diff > 150.0:
                            _backtrack_count += 1
                    if _backtrack_count:
                        print(f"🔁 [回头路检测] 检测到 {_backtrack_count} 处疑似折返段（相邻航向夹角>150°）。", flush=True)

                    post_travel_details = {}
                    final_stitched_path = []
                    if post_p2p_tasks:
                        post_results = await asyncio.gather(*post_p2p_tasks, return_exceptions=True)
                        for (p_key, loc_name, c1, c2), res_opts in zip(post_pair_coords, post_results):
                            if isinstance(res_opts, dict) and res_opts:
                                post_travel_details[p_key] = res_opts
                                post_travel_details[loc_name] = res_opts
                            # 👑 兜底：驾车算路失败/缺失时，用两点直线段补上，保证「每个被推荐节点都被路线覆盖」
                            seg = res_opts.get("driving", {}).get("actual_path") if isinstance(res_opts, dict) else None
                            final_stitched_path.extend(seg if seg else [list(c1), list(c2)])

                    if post_travel_details:
                        yield json.dumps({"type": "travel_details", "payload": post_travel_details}, ensure_ascii=False) + "\n"
                    if final_stitched_path:
                        yield json.dumps({"actual_path": final_stitched_path}, ensure_ascii=False) + "\n"
                        print(f"🛣️ [精准链路推送] 成功下发 {len(final_stitched_path)} 个对齐的高德路网坐标！")
            except Exception as e:
                print(f"🔥 [真实路网拼装失败]: {e}")

        except BadRequestError as e:
            # 阿里云百炼等大模型平台侧错误（如账号欠费 Arrearage / 无权限）：不打印整段 Traceback
            err_text = str(e)
            if "Arrearage" in err_text or "overdue" in err_text.lower() or "欠费" in err_text:
                print("⚠️ [大模型服务不可用] 阿里云百炼账号欠费（Arrearage），请充值或更换 API Key 后重试。")
                friendly = "AI 服务账号余额不足，请联系管理员充值（阿里云百炼 Arrearage）后重试。"
            else:
                print(f"⚠️ [大模型请求被拒绝] {err_text[:200]}")
                friendly = "AI 大模型服务暂不可用（请求被拒绝），请稍后重试或联系管理员。"
            yield json.dumps({"token": f"\n[FINAL_JSON]\n{{\"status\": \"error\", \"negotiation_summary\": \"{friendly}\", \"route\": []}}\n"}, ensure_ascii=False) + "\n"

        except Exception as e:
            print(f"🔥 [流式推演崩溃 Traceback]:")
            traceback.print_exc()
            yield json.dumps({"token": f"\n[FINAL_JSON]\n{{\"status\": \"error\", \"negotiation_summary\": \"{str(e)}\", \"route\": []}}\n"}, ensure_ascii=False) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.post("/agent/bandit/feedback")
async def record_bandit_feedback(payload: Dict[str, Any]):
    """Record explicit acceptance/rating reward; clicks alone are rejected."""
    arm_id = str(payload.get("arm_id") or "").strip()
    if not arm_id:
        raise HTTPException(status_code=400, detail="arm_id_required")
    if arm_id not in planning_bandit.snapshot().get("arms", {}):
        planning_bandit.register(arm_id)
    try:
        if "reward" in payload and payload.get("reward") is not None:
            reward = float(payload.get("reward"))
            if not 0.0 <= reward <= 1.0:
                raise ValueError
        else:
            reward = derive_reward(payload)
        planning_bandit.record(arm_id, reward)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="reward_must_be_between_0_and_1")
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown_arm")
    return {"ok": True, "arm_id": arm_id, "reward": reward, "bandit": planning_bandit.snapshot()}


@router.get("/agent/bandit")
async def bandit_status():
    return planning_bandit.snapshot()


@router.get("/agent/capabilities")
async def agent_capabilities():
    """Return the canonical, secret-free specialist capability matrix."""
    return {
        "version": "agent-contract-v1",
        "agents": [capability.model_dump(mode="json") for capability in capability_registry()],
        "proposal_gate": {
            "schema": "AgentProposal",
            "hard_constraints_required": True,
            "evidence_required": True,
            "natural_language_decides": False,
        },
    }
