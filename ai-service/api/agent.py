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
import numpy as np
import networkx as nx
from sklearn.cluster import DBSCAN
from typing import List, Dict, Any, Optional, Tuple
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI, BadRequestError
from models.schemas import GatewayMessage, NegotiateResponse
from core.travel_utils import analyze_weather_for_planning, estimate_crowdedness, build_personalization_hint, build_profile_category_boost, build_profile_budget_gamma, build_profile_fine_tag_boost, classify_fine_tags, normalize_mode, normalize_role
from core.time_intelligence import analyze_pace, monday_closure_notes
from core.optimization import fairness_report, pareto_frontier, repair_route, select_fair_route, validate_candidate, validate_route
from core.constraints import resolve_conflicts
from core.contextual_bandit import ContextualThompsonBandit, derive_reward, stable_arm_id
from core.simulation import member_utilities_for, simulate_plan


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
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "qwen-turbo")

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
for _arm in filter(None, (item.strip() for item in os.getenv("AI_BANDIT_ARMS", "builtin-joint-v1").split(","))):
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
_CULTURAL_KW = ["博物馆", "展览馆", "纪念馆", "故居", "古城", "古镇", "古村", "遗址", "文庙", "书院", "寺庙", "道观", "清真寺", "教堂", "佛塔", "城墙", "宫殿", "陵墓", "碑林", "老街", "胡同", "美术馆", "科技馆", "天文馆", "图书馆", "大剧院", "音乐厅", "艺术中心", "非遗", "民俗", "文化园", "创意园"]
_FOOD_KW = ["美食", "餐厅", "酒楼", "食府", "饭庄", "菜馆", "小吃", "夜市", "老字号", "私房菜", "土菜", "农家菜", "海鲜", "早茶", "茶餐厅", "火锅", "烧烤", "面馆", "包子", "烤鸭", "米线", "羊肉", "牛肉", "拉面", "抓饭", "拌面", "大盘鸡", "馕", "三套车"]
_SHOPPING_KW = ["步行街", "商业街", "商场", "购物中心", "百货", "商圈", "市场", "太古里", "IFS", "万象城", "大悦城", "万达", "银泰", "SKP", "奥莱", "outlets", "集市", "古玩城", "文创市集"]


def classify_poi_category(name: str, poi_type: str = "", is_hotel: bool = False) -> str:
    """将 POI 归一为 scenic/cultural/food/shopping/hotel/other，供权重与均衡排布复用。"""
    if is_hotel:
        return "hotel"
    n = str(name or "").lower()
    t = str(poi_type or "").lower()
    # 高德 type 码直接映射（优先于名称）：用大类前缀覆盖全部子类（05=餐饮，10=住宿，11=风景，14=科教文化，06=购物）
    if t.startswith("10") or any(k in t for k in ["住宿", "酒店", "宾馆", "民宿"]):
        return "hotel"
    if t.startswith("11") or any(k in t for k in ["风景名胜", "风景", "公园", "景点", "景区"]):
        return "scenic"
    if t.startswith("14") or any(k in t for k in ["博物馆", "展览馆", "科教", "文化"]):
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
    signals: Dict[str, Any] = {
        "wants_hotel": False, "wants_food": False, "wants_scenic": False,
        "wants_cultural": False, "wants_shopping": False, "luxury_hotel": False,
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
    if any(k in t for k in ["风景", "自然风光", "公园", "景点", "景区", "户外", "峡谷", "草原", "温泉", "园林", "爬山", "雪山", "海岛", "湖边", "看海"]):
        signals["wants_scenic"] = True
        signals["requirements"].append("自然风景")
    if any(k in t for k in ["博物馆", "文化", "古迹", "历史", "古城", "寺庙", "遗址", "文庙", "故居", "人文"]):
        signals["wants_cultural"] = True
        signals["requirements"].append("文化历史")
    if any(k in t for k in ["购物", "商场", "商圈", "商业街", "逛街"]):
        signals["wants_shopping"] = True
        signals["requirements"].append("购物商圈")
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

    async def emit(self, stream: str, event_type: str, payload: dict, room_id: str = "", max_len: int = 1000):
        """发布一条事件到 Redis Stream。事件结构：{type, room_id, payload, ts}。

        room_id 注入事件信封顶层，供网关桥接按房间定向广播到 WebSocket。
        """
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
        """
        try:
            r = redis.from_url(self.redis_url, decode_responses=True)
            resp = await r.xread({str(stream): last_id}, count=count, block=block_ms)
            await r.aclose()
        except Exception as e:
            print(f"⚠️ [事件流] Redis consume 跳过 (不影响主推演流程): {e}")
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
        def sync_search():
            try:
                query = f"{city} {target} 今日 携程 飞猪 均价 价格"
                with DDGS(timeout=2) as ddgs:
                    return list(ddgs.text(query, max_results=2))
            except Exception:
                return []
        try:
            results = await asyncio.wait_for(asyncio.to_thread(sync_search), timeout=2.0)
            if not results:
                return "未抓取到外网实时价格"
            return " | ".join([str(r.get('body', '')) for r in results if isinstance(r, dict)])
        except Exception:
            return "未抓取到可信供应商价格"

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

    # 👑 精准文旅分类码：110000(风景名胜) | 141200(博物馆展览馆) | 060400(特色步行街) | 060100(综合商场)
    async def get_dynamic_pois(self, city: str, keywords: str, types: str = "110000|141200|060400|060100", limit: int = 40) -> List[Dict]:
        if not self.amap_key:
            return []
        from core.poi_pool import parse_amap_location as _parse_amap_location
        from core.poi_pool import is_play_worthy, poi_record_from_amap
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
                resp = await http_client.get(f"{self.amap_url}/place/text", params=params)
                data = resp.json()
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
                        if not is_play_worthy(poi_name, poi.get("type")):
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
        raw = f"v3|{city}|{days}|{''.join(str(profile_text or '').split())}"
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

        # 语义命中：对同城市+同天数条目计算画像 embedding 余弦相似度
        vec = await self._embed(profile_text)
        if vec is not None:
            best_key, best_sim = None, 0.0
            for k, item in self._vec_index.items():
                if item.get("city") != city or int(item.get("days", 0)) != days:
                    continue
                iv = item.get("vector")
                if not iv:
                    continue
                s = self._cosine(vec, iv)
                if s > best_sim:
                    best_sim, best_key = s, k
            if best_key and best_sim >= self.sim_threshold:
                hit = self._mem.get(best_key) or await self._redis_get(best_key)
                if hit:
                    print(f"✅ [语义缓存] 相似画像命中 (cosine={best_sim:.3f} >= {self.sim_threshold}) {city}/{days}天")
                    return hit
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

    # 🎯 个性化旅行画像注入：读取网关注入的画像，A/B 分组决定是否应用个性化提示词
    personalized_profile = user_prefs.get("personalized_profile") or {}
    if not isinstance(personalized_profile, dict):
        personalized_profile = {}
    member_profiles = user_prefs.get("member_profiles") or {}
    if not isinstance(member_profiles, dict):
        member_profiles = {}
    # 🎯 融合手选角色与算法画像：手选 role 始终生效（覆盖冷启动/对照组），算法画像按 A/B 注入
    personalization_hint = build_personalization_hint(personalized_profile, member_profiles, room_members, role)

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
    poi_limit = max(45, trip_days * (target_daily_count + 5))

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
            print(f"✅ [语义缓存] {target_city}/{trip_days}天命中，秒回", flush=True)
            return StreamingResponse(cached_stream(cached_hit), media_type="application/x-ndjson")
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
            await event_bus.emit(OMNI_EVENTS_STREAM, "reasoning_step", step, room_id)
            yield json.dumps({"type": "reasoning_step", "payload": step}, ensure_ascii=False) + "\n"

        yield json.dumps({"type": "budget_breakdown", "payload": budget_breakdown_payload}, ensure_ascii=False) + "\n"
        # 事件流：推演启动事件（emit 到全局 Redis Stream，供订阅方实时消费）
        await event_bus.emit(OMNI_EVENTS_STREAM, "negotiation_started", {
            "room_id": room_id, "user_id": user_id, "city": target_city,
            "days": trip_days, "budget_mode": budget_mode, "member_count": _member_count,
        }, room_id)

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
        # 精准文旅分类，剔除建材数码五金
        attractions_task = toolbox.get_dynamic_pois(target_city, keywords="必玩景点|文化地标|历史古迹|博物馆|文庙|古城|5A景区", types="110000|141200|060400|060100", limit=poi_limit)
        foods_task = toolbox.get_dynamic_pois(target_city, keywords="特色老字号|地方特色美食|正宗名吃|特色正餐|川菜", types="050000", limit=poi_limit)
        hotels_task = toolbox.get_dynamic_pois(target_city, keywords="品质酒店|高端度假酒店|高分精品住宿", types="100000", limit=30)
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

        cleaned_hotels = []
        if isinstance(raw_hotels, list):
            for h in raw_hotels:
                if not isinstance(h, dict):
                    continue
                h_name = str(h.get("name") or "")
                if any(bad in h_name for bad in ["餐", "菜", "火锅", "酒楼", "宴", "小吃", "面", "茶", "油漆", "体验店", "手机"]):
                    continue
                cleaned_hotels.append(h)

        # 景点与餐饮都属于可执行的白天节点。此前只统计景点会在长行程中
        # 过早降低 actual_daily_count，导致后续日期只剩住宿。
        available_total = len(unique_attractions) + len(unique_foods)
        actual_daily_count = target_daily_count
        if available_total < trip_days * target_daily_count and available_total > 0:
            actual_daily_count = max(5 if role == "深度探索" else 3, available_total // trip_days)
            yield json.dumps({"token": f"[协商提示]: 已根据当地核心地标池精选每日 {actual_daily_count} 个核心游览节点！\n"}, ensure_ascii=False) + "\n"
            async for _e in emit_reasoning("时空调度体", "compromise", f"候选池仅 {available_total} 个核心地标，将每日节点由 {target_daily_count} 妥协下调至 {actual_daily_count} 个", "避免多天重复排布，保证每个节点真实可达"):
                yield _e

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
            _day_buckets = {"scenic": [], "cultural": [], "food": [], "shopping": [], "other": []}
            for p in spatial_daytime:
                if isinstance(p, dict):
                    _day_buckets.setdefault(_day_cat(p), []).append(p)
            # 👑 不走回头路：spatial_daytime 已按 TSP 空间最近邻串接，分桶后【不再按适应度重排】，
            #    保留桶内空间序，仅靠轮询交错维持类别多样性，避免「东拉西扯」式回头路写进 Prompt。

            _interleaved = []
            _bucket_order = ["scenic", "cultural", "food", "shopping", "other"]
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
                    temperature=0.2,
                    max_tokens=4096,
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
                        max_tokens=16384
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
                yield json.dumps({"type": "final_route", "payload": final_data}, ensure_ascii=False) + "\n"
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
                                "target_daytime": desired_counts[d],
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
                                fair_selection = select_fair_route(_feasible_routes, fairness_members)
                                selected_index = _feasible_indices[int(fair_selection.get("index", 0))]
                                _selection_method = "hard_constraint_filter_then_maximin"
                            else:
                                fair_selection = select_fair_route(fair_routes, fairness_members)
                                selected_index = int(fair_selection.get("index", 0))
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
                            final_data["fairness"] = dict(fairness_base, selected_variant_index=selected_variant_index, selection_method=_selection_method, candidate_reports=fair_selection.get("reports", []))
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
                            final_data["fairness"] = fairness_report(_selected_route, fairness_members)

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

                                async def _generate_segment(_target: Dict[str, Any]) -> List[Any]:
                                    """只生成该段的节点数组（越界天数由 core 丢弃）。"""
                                    if _time.monotonic() > _long_deadline:
                                        return []  # 超预算：如实记为"这一段没生成出来"
                                    _resp = await asyncio.wait_for(
                                        client.chat.completions.create(
                                            model=MODEL_NAME,
                                            messages=[
                                                {"role": "system", "content": system_prompt},
                                                {"role": "user", "content": segment_prompt(_target)},
                                            ],
                                            temperature=0.2,
                                            max_tokens=4096,
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
                                                {"role": "system", "content": system_prompt},
                                                {"role": "user", "content": segment_prompt(_target)},
                                            ],
                                            temperature=0.2,
                                            max_tokens=4096,
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

                                # 补价节点数 3 → 8（有界）：太少会导致"未取到"一大片，
                                # 用户看到的全是"需要你确认"。并发执行 + 整体超时保护不变。
                                _targets = pending_price_targets(final_data, limit=8)
                                _fetcher = getattr(toolbox, "get_real_time_web_price", None)
                                if _targets and _fetcher:
                                    async def _lookup(_item: Dict[str, Any]) -> Dict[str, Any]:
                                        try:
                                            _text = await _fetcher(target_city, str(_item.get("name") or ""))
                                        except Exception:
                                            _text = ""
                                        return {
                                            "name": _item.get("name"),
                                            "value": parse_price_from_text(_text),
                                            "source": "web",
                                        }

                                    _observations = await asyncio.wait_for(
                                        asyncio.gather(*[_lookup(item) for item in _targets]),
                                        timeout=12.0,
                                    )
                                    _merged = merge_observations(final_data, list(_observations))
                                    if _merged["updated"]:
                                        final_data["route"] = _merged["plan"]["route"]
                                    _report = pending_price_report(final_data)
                                    final_data["price_audit"] = {
                                        **_merged["coverage"],
                                        "updated": len(_merged["updated"]),
                                        "still_unknown": _merged["coverage"]["unknown"][:5],
                                        "summary": summarize_merge(_merged),
                                        "pending": _report["pending"],
                                        "pending_summary": _report["summary"],
                                    }
                                else:
                                    _coverage = price_coverage(final_data)
                                    _report = pending_price_report(final_data)
                                    final_data["price_audit"] = {
                                        **_coverage,
                                        "pending": _report["pending"],
                                        "pending_summary": _report["summary"],
                                    }
                            except Exception as _price_exc:  # 价格补全失败绝不能影响出方案
                                try:
                                    from core.price_sources import price_coverage as _coverage

                                    final_data["price_audit"] = _coverage(final_data)
                                except Exception:
                                    pass

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
                        # 👑 数据降级登记：哪个数据源没取到、用户损失了什么，统一在这里说一次
                        # （文案只在 core/degradation.DATA_SOURCES 里定义，前端照抄渲染）。
                        # 放在最后：前面所有 payload 字段都已经定稿，推导才准确。
                        try:
                            from core.degradation import collect_degradations

                            final_data["degradations"] = collect_degradations(final_data)
                        except Exception as _degrade_exc:  # 登记失败绝不影响出方案
                            final_data["degradations"] = {"error": str(_degrade_exc)[:200]}

                    yield json.dumps({"type": "final_route", "payload": final_data}, ensure_ascii=False) + "\n"
                    # 事件流：行程生成完成事件（emit 到全局 Redis Stream）
                    await event_bus.emit(OMNI_EVENTS_STREAM, "final_route_ready", {
                        "room_id": room_id, "user_id": user_id, "city": target_city,
                        "variants": len(final_data.get("plan_variants", [])),
                    }, room_id)

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
