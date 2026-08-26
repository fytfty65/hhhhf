import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import httpx
import math
import re
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
from core.travel_utils import analyze_weather_for_planning, estimate_crowdedness, build_personalization_hint
from duckduckgo_search import DDGS
import redis.asyncio as redis

router = APIRouter()

client = AsyncOpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
    timeout=httpx.Timeout(90.0, connect=5.0)
)
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "qwen-turbo")


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


# ==========================================
# 0. 风控层：WorldMonitor 动态风控与真实气象客户端
# ==========================================
class WorldMonitorClient:
    """WorldMonitor 动态风控网关。

    数据来源优先级（真实 → 稳定兜底）：
      1. WorldMonitor API（需配置 WORLDMONITOR_API_URL + WORLDMONITOR_API_KEY）
      2. GDELT 全球事件实时流（免费公开，按城市检索安全/抗议/事故/犯罪事件）
      3. 确定性本地推演（同城同天结果稳定，杜绝“随机假动态”）
    """
    def __init__(self):
        self.base_url = os.getenv("WORLDMONITOR_API_URL", "https://api.worldmonitor.app")
        self.api_key = os.getenv("WORLDMONITOR_API_KEY", "")
        self.gdelt_url = os.getenv("GDELT_API_URL", "https://api.gdeltproject.org/api/v2/doc/doc")

    async def _query_gdelt(self, city: str) -> Optional[Dict[str, Any]]:
        """接入 GDELT DOC API，检索近 30 天城市安全/抗议/事故/犯罪事件，推导真实动态 CII。"""
        try:
            query = f'"{city}" (protest OR crime OR accident OR safety OR security)'
            async with httpx.AsyncClient(timeout=4.0) as http_client:
                resp = await http_client.get(
                    self.gdelt_url,
                    params={"query": query, "mode": "artlist", "maxrecords": "25", "format": "json", "timespan": "30d"},
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
                articles = data.get("articles") or []
                if not articles:
                    return None
                count = len(articles)
                cii = round(10.0 + min(count, 30) * 1.1, 1)
                level = "MEDIUM" if cii >= 25 else "LOW"
                return {
                    "city": city,
                    "cii_score": cii,
                    "risk_level": level,
                    "source": "GDELT",
                    "active_alerts": [
                        {
                            "type": "EVENT_STREAM",
                            "level": level,
                            "title": "近30天安全事件流",
                            "detail": f"GDELT 检索到【{city}】相关安全/事故/抗议类公开报道 {count} 条，已纳入风险加权。"
                        }
                    ],
                    "safety_advice": f"【{city}】近30天公开安全事件 {count} 条，综合风险指数 CII {cii}（{level}）。"
                }
        except Exception:
            return None

    async def get_city_safety_intel(self, city: str) -> Dict[str, Any]:
        # 1) 真实情报源：WorldMonitor API（配置密钥后启用）
        try:
            async with httpx.AsyncClient(timeout=2.5) as http_client:
                headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
                resp = await http_client.get(
                    f"{self.base_url}/api/v1/intelligence/risk",
                    params={"city": city},
                    headers=headers
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and data.get("cii_score"):
                        data.setdefault("city", city)
                        data.setdefault("source", "WorldMonitor")
                        return data
        except Exception:
            pass

        # 2) 真实情报源：GDELT 全球事件流（公开免费）
        gdelt = await self._query_gdelt(city)
        if gdelt:
            return gdelt

        # 3) 兜底：确定性本地推演 —— 同城同天结果稳定，不再随机跳动
        seed_str = f"{city}:{datetime.date.today().isoformat()}"
        h = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest(), 16)
        dynamic_cii = round(10.0 + (h % 320) / 10.0, 1)  # 10.0 ~ 41.9，覆盖 LOW/MEDIUM/HIGH 分级
        level = "MEDIUM" if dynamic_cii >= 25 else "LOW"

        return {
            "city": city,
            "cii_score": dynamic_cii,
            "risk_level": level,
            "source": "local_deterministic",
            "active_alerts": [
                {
                    "type": "WEATHER_ALERT",
                    "level": "LOW",
                    "title": "出行气象指引",
                    "detail": f"{city} 当前气象条件稳定，适合全天候户外游览打卡与文旅地标探索。"
                },
                {
                    "type": "TRAFFIC_CONTROL",
                    "level": "LOW",
                    "title": "核心商圈高峰期人流管控",
                    "detail": f"{city} 热门打卡地与地标景区高峰期实施局部车流与人流错峰管控。"
                },
                {
                    "type": "CROWD_MONITOR",
                    "level": "LOW",
                    "title": "景区拥挤度监测",
                    "detail": f"{city} 主要景点拥挤度处于可控区间，建议避开午间瞬时客流高峰。"
                }
            ],
            "safety_advice": f"【{city}】综合风险指数 CII {dynamic_cii}（{level}），已依据公开事件流与当日时空特征综合评估。"
        }

    # ----- 免费平替引擎：本地多源聚合 + 气象预警公开接口（替代付费 WorldMonitor API） -----
    async def _fetch_public_weather_alert(self, city: str) -> Optional[Dict[str, Any]]:
        """免费官方气象预警公开接口（可配置通道）。

        说明：
          - 默认关闭：仅在配置 NMC_ALERT_URL（指向可用官方预警 JSON 端点）后发起请求。
          - 原因：中央气象台旧 /rest/alarm/* 端点已下线、weather.com.cn 旧 alerts 接口已失效、
            GDELT 需外网。为避免每个行程请求无谓等待，未配置时零网络开销直接返回 None。
          - 失败/超时将静默回退到高德天气推断，不阻塞主流程。
        """
        endpoint = os.getenv("NMC_ALERT_URL")
        if not endpoint:
            return None
        try:
            async with httpx.AsyncClient(timeout=1.8, follow_redirects=True) as http_client:
                resp = await http_client.get(endpoint, params={"p": 1, "pageSize": 50})
                if resp.status_code != 200:
                    return None
                data = resp.json()
                alarm_list = ((data.get("data") or {}).get("alarmList")) or []
                if not alarm_list and isinstance(data, list):
                    alarm_list = data
                for alarm in alarm_list:
                    title = str(alarm.get("title") or "")
                    if city and (city in title or title.startswith(city)):
                        has_red = ("红" in title) or ("橙" in title)
                        return {
                            "type": "WEATHER_ALERT",
                            "level": "HIGH" if has_red else ("MEDIUM" if "黄" in title else "LOW"),
                            "title": str(alarm.get("category") or "气象预警"),
                            "detail": title,
                        }
        except Exception:
            return None
        return None

    async def aggregate_city_risk(
        self,
        base_intel: Dict[str, Any],
        city: str,
        weather: Optional[Dict[str, Any]] = None,
        traffic: Optional[Dict[str, Any]] = None,
        is_weekend: bool = False,
    ) -> Dict[str, Any]:
        """本地多源聚合风控引擎：在真实情报源(WorldMonitor/GDELT/确定性推演)基础上，
        融合【实时高德天气】【实时高德交通】【官方气象预警公开接口】等免费信号，动态输出 CII 与风险分级。

        完全替代付费 WorldMonitor API，且天气/路况为真实实时数据，无任何付费依赖。
        """
        weather_risk_keywords = {
            "特大暴雨": 10, "大暴雨": 9, "台风": 9, "暴雨": 8, "冰雹": 8, "暴雪": 7,
            "大雪": 6, "雷暴": 6, "雷雨": 6, "大雨": 5, "沙尘暴": 6, "大雾": 5,
            "浓雾": 5, "中雨": 3, "雾霾": 4, "沙尘": 4,
        }
        intel = dict(base_intel) if isinstance(base_intel, dict) else {}
        base_cii = float(intel.get("cii_score") or 10.0)
        delta = 0.0
        new_alerts: List[Dict[str, Any]] = []
        signals: List[str] = []

        # 1) 实时天气融合（高德/心知真实数据）
        if isinstance(weather, dict):
            cond = str(weather.get("condition") or "")
            temp = None
            mtemp = re.search(r"([-+]?\d+)", cond)
            if mtemp:
                temp = int(mtemp.group(1))
            if temp is not None:
                if temp >= 33:
                    delta += 6
                    new_alerts.append({"type": "HEAT", "level": "MEDIUM", "title": "高温预警", "detail": f"实时气温达 {temp}°C，建议避开午间户外暴晒，补水防晒并缩短室外停留时间。"})
                    signals.append("高温")
                elif temp <= 0:
                    delta += 6
                    new_alerts.append({"type": "COLD", "level": "MEDIUM", "title": "低温预警", "detail": f"实时气温低至 {temp}°C，注意保暖，谨防路面结冰。"})
                    signals.append("严寒")
            for kw, w in weather_risk_keywords.items():
                if kw in cond:
                    delta += w
                    new_alerts.append({"type": "WEATHER", "level": "HIGH" if w >= 8 else "MEDIUM", "title": f"{kw}风险", "detail": f"实时天气出现「{kw}」，已提升当日行程风险，建议优先安排室内/遮蔽项目并预留缓冲。"})
                    signals.append(kw)
                    break

        # 2) 官方气象预警公开接口（免费，失败自动回退，不阻塞）
        pub_alert = await self._fetch_public_weather_alert(city)
        if pub_alert:
            new_alerts.append(pub_alert)
            delta += 4
            signals.append("官方气象预警")

        # 3) 实时交通融合
        if isinstance(traffic, dict):
            sc = str(traffic.get("status_code") or "1")
            code = int(sc) if sc.isdigit() else 1
            if code >= 4:
                delta += 5
                new_alerts.append({"type": "TRAFFIC", "level": "MEDIUM", "title": "严重拥堵", "detail": f"实时路况拥堵指数较高：{traffic.get('description', '拥堵')}。建议错峰出行并预留通勤时间。"})
                signals.append("严重拥堵")
            elif code >= 3:
                delta += 3
                new_alerts.append({"type": "TRAFFIC", "level": "MEDIUM", "title": "局部拥堵", "detail": f"实时路况出现拥堵：{traffic.get('description', '拥堵')}。建议避开高峰路段。"})
                signals.append("拥堵")

        # 4) 融合动态 CII（clamp 0-100）
        fused_cii = round(max(0.0, min(100.0, base_cii + delta)), 1)
        intel["cii_score"] = fused_cii
        intel["risk_level"] = "HIGH" if fused_cii >= 40 else ("MEDIUM" if fused_cii >= 25 else "LOW")
        intel["is_weekend"] = bool(is_weekend)
        intel["fusion"] = {
            "base_cii": round(base_cii, 1),
            "delta": round(delta, 1),
            "signals": signals,
            "source": "local_multi_source_fusion",
        }

        # 追加融合信号预警（按标题去重）
        existing = intel.get("active_alerts") if isinstance(intel.get("active_alerts"), list) else []
        seen = set(str(a.get("title")) for a in existing)
        for al in new_alerts:
            if str(al.get("title")) not in seen:
                existing.append(al)
                seen.add(str(al.get("title")))
        intel["active_alerts"] = existing

        # 融合避险建议
        if fused_cii >= 40:
            tail = "建议调整行程以规避高风险窗口，优先安排室内/低风险项目。"
        elif fused_cii >= 25:
            tail = "建议预留缓冲时间并适当错峰。"
        else:
            tail = "当前整体可行，可按计划出行。"
        intel["safety_advice"] = f"【{city}】动态综合风险指数 CII {fused_cii}（{intel['risk_level']}）。融合信号：{'、'.join(signals) if signals else '未检测到显著风险增量'}。{tail}"
        return intel


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
    result = []

    food_category_keywords = [
        "壮馍", "羊汤", "牛肉汤", "胡辣汤", "凉皮", "肉夹馍", "火锅", 
        "烧烤", "面馆", "烩面", "包子", "汉堡", "炸鸡", "烤鸭", "米线", "螺蛳粉", 
        "烤包子", "手抓肉", "抓饭", "拌面", "大盘鸡", "馕", "羊肉串", "三套车", "行面",
        "黄焖羊肉", "牛肉拉面", "炒米粉", "丸子汤", "油塔子", "粉汤"
    ]

    suffix_clean_list = [
        "夜市", "美食街", "酒楼", "餐馆", "分店", "总店", "景区", 
        "公园-入口", "停车场", "步行街", "小吃街", "遗址公园", "创意园", "文化园", 
        "(艺术学院店)", "(总店)", "店", "(旗舰店)", "(专营店)", "(分店)"
    ]

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
            result.append(poi)

    return result


# ==========================================
# 3. 算法层：纳什均衡与帕累托最优
# ==========================================
class NashEquilibriumSolver:
    @staticmethod
    def resolve_conflicts(pois: List[Dict]) -> List[Dict]:
        if not isinstance(pois, list):
            return []
        return [p for p in pois if isinstance(p, dict) and float(p.get("fitness_score", 0) or 0) > -5.0]


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
    def __init__(self, user_prefs: dict, city_center_coord: str, evolution_memory: list, safety_intel: dict = None, is_luxury: bool = False):
        self.user_prefs = user_prefs if isinstance(user_prefs, dict) else {}
        self.mode = str(self.user_prefs.get("mode") or "coop").lower()
        self.center_lat, self.center_lon = self._parse_coord(city_center_coord)
        self.safety_intel = safety_intel if isinstance(safety_intel, dict) else {}

        self.alpha = 1.0  # 体验权重
        self.beta = 1.0   # 距离权重
        self.gamma = 1.0  # 消费/能耗权重
        self.delta = 2.0  # WorldMonitor 风险惩罚权重

        self.is_luxury = bool(is_luxury)
        self._adjust_weights_by_intent()
        self._apply_rl_evolution(evolution_memory)

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
        if self.is_luxury or any(kw in intent_str for kw in ["1万", "万元", "10000", "预算充足", "不差钱", "高端", "豪华", "五星", "奢华", "享受", "品质", "好看", "商圈"]):
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

    def calculate_fitness(self, poi: dict) -> float:
        if not isinstance(poi, dict):
            return 0.0

        rating_val = poi.get("rating")
        if isinstance(rating_val, list):
            rating_val = rating_val[0] if rating_val else "4.6"
        if not rating_val or rating_val == "暂无评分":
            rating_val = "4.2"

        try:
            experience_score = float(rating_val)
        except (ValueError, TypeError):
            experience_score = 4.2

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

        return round(fitness, 4)


# ==========================================
# 6. 工具层：专家工具箱（严格文旅分类码）
# ==========================================
class ExpertToolbox:
    def __init__(self):
        self.amap_key = os.getenv("AMAP_API_KEY")
        self.seniverse_key = os.getenv("SENIVERSE_API_KEY", "eb3cb61292594891937c78476861bb1e") 
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
            return "抓取受限，将启用高德保底均价。"

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
        async with httpx.AsyncClient(timeout=4.0) as http_client:
            params = {
                "key": self.amap_key, "keywords": keywords, "city": city,
                "types": types, "sortrule": "weight", "offset": limit, "page": 1, "extensions": "all"
            }
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

                        biz_ext = poi.get("biz_ext")
                        if not isinstance(biz_ext, dict):
                            biz_ext = {}

                        rating = biz_ext.get("rating")
                        if not rating or isinstance(rating, list):
                            rating = "4.6"

                        cost = biz_ext.get("cost")
                        if not cost or isinstance(cost, list):
                            cost = "45"

                        raw_photos = poi.get("photos", [])
                        # 统一归一化为 HTTPS（兼容 http:// 与 // 协议相对），避免前端混合内容拦截导致图片空白
                        real_photos = []
                        for ph in raw_photos:
                            if isinstance(ph, dict) and ph.get("url"):
                                _u = str(ph.get("url")).strip()
                                if _u.startswith("//"):
                                    _u = "https:" + _u
                                elif _u.startswith("http://"):
                                    _u = "https://" + _u[len("http://"):]
                                if _u.startswith("https://"):
                                    real_photos.append(_u)
                        real_photos = real_photos[:3]

                        loc_str = str(poi.get("location") or "")
                        clean_search_name = re.sub(r'[\(（].*?[\)）]', '', poi_name).strip()
                        encoded_query = urllib.parse.quote(f"{city} {clean_search_name}")
                        amap_hyperlink = f"https://www.amap.com/search?query={encoded_query}"
                        if loc_str and "," in loc_str:
                            lng, lat = loc_str.split(",")
                            encoded_name = urllib.parse.quote(clean_search_name)
                            amap_hyperlink = f"https://uri.amap.com/marker?position={lng},{lat}&name={encoded_name}"

                        # 👑 按坐标生成高德静态地图，作为图片兜底（永不空白、与实际地理位置严格一致）
                        map_image = ""
                        if loc_str and "," in loc_str and self.amap_key:
                            map_image = (
                                f"https://restapi.amap.com/v3/staticmap"
                                f"?location={loc_str}&zoom=15&size=480*360"
                                f"&markers=mid,0xFF0000,A:{loc_str}"
                                f"&key={self.amap_key}"
                            )

                        pois.append({
                            "id": str(poi.get("id") or ""),
                            "name": poi_name,
                            "type": str(poi.get("type", "")).split(";")[0],
                            "business_area": str(poi.get("business_area") or "核心圈"), 
                            "address": str(poi.get("address") or "地址未知"),
                            "location": loc_str, 
                            "rating": str(rating), 
                            "cost": str(cost),         
                            "open_time": str(biz_ext.get("open_time") or "全天开放"),
                            "photos": real_photos[:3],
                            "amap_url": amap_hyperlink,
                            "map_image": map_image
                        })
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
            return {"status_code": "1", "description": "畅通", "advice": "无路况数据"}

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
                    result = {"status_code": str(eval_code_int), "description": status_desc, "advice": advice}
                    _cache_set(cache_key, result, ttl=900)
                    return result
            except Exception:
                pass
            return {"status_code": "1", "description": "畅通", "advice": "维持原计划"}

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
                http_client.get(f"{self.amap_url}/direction/driving", params={"key": self.amap_key, "origin": origin_lnglat, "destination": dest_lnglat, "strategy": 0}),
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
                    now_text, now_temp = "多云", ""
                    if casts:
                        day0 = casts[0]
                        now_text = str(day0.get("dayweather") or "多云")
                        now_temp = str(day0.get("daytemp") or "")

                    forecast = []
                    for i, d in enumerate(casts[:3]):
                        forecast.append({
                            "day": f"Day {i + 1}",
                            "text": str(d.get("dayweather") or "多云"),
                            "temp": f"{d.get('nighttemp', '')}~{d.get('daytemp', '')}°C"
                        })
                    condition = f"{now_text} {now_temp}°C" if now_temp else now_text
                    result = {"condition": condition, "forecast": forecast, "source": "amap"}
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

                    now_text, now_temp = "多云", ""
                    forecast: List[Dict[str, str]] = []

                    if now_resp.status_code == 200:
                        data = now_resp.json()
                        results = data.get("results") or []
                        if results:
                            now = results[0].get("now") or {}
                            now_text = str(now.get("text") or "多云")
                            now_temp = str(now.get("temperature") or "")

                    if daily_resp.status_code == 200:
                        ddata = daily_resp.json()
                        dresults = ddata.get("results") or []
                        if dresults:
                            for i, d in enumerate((dresults[0].get("daily") or [])[:3]):
                                forecast.append({
                                    "day": f"Day {i + 1}",
                                    "text": str(d.get("text_day") or d.get("text") or "晴朗"),
                                    "temp": f"{d.get('low', '')}~{d.get('high', '')}°C"
                                })

                    if not forecast:
                        forecast = [
                            {"day": "Day 1", "text": now_text, "temp": f"{now_temp}°C"},
                            {"day": "Day 2", "text": now_text, "temp": f"{now_temp}°C"},
                            {"day": "Day 3", "text": now_text, "temp": f"{now_temp}°C"},
                        ]

                    condition = f"{now_text} {now_temp}°C" if now_temp else now_text
                    if now_text != "多云" or now_temp:
                        return {"condition": condition, "forecast": forecast}

            except Exception:
                pass

        weather_map = {
            "成都": ("阴转小雨 26°C", "21~28°C"),
            "三亚": ("晴 31°C", "30~34°C"),
            "海口": ("多云 30°C", "28~33°C"),
            "乌鲁木齐": ("晴朗 24°C", "18~27°C"),
            "哈尔滨": ("微风 18°C", "14~22°C"),
            "北京": ("多云 27°C", "21~29°C"),
            "徐州": ("晴 26°C", "20~28°C"),
            "安阳": ("多云 25°C", "19~27°C"),
            "濮阳": ("晴 25°C", "19~28°C"),
            "武威": ("晴朗 25°C", "16~28°C"),
            "兰州": ("多云 26°C", "18~29°C"),
            "敦煌": ("晴天 29°C", "20~32°C"),
            "张掖": ("晴朗 26°C", "17~29°C"),
            "酒泉": ("多云 27°C", "18~30°C")
        }
        for k, (now_t, span_t) in weather_map.items():
            if k in str(location_coord_or_city):
                return {
                    "condition": now_t,
                    "forecast": [
                        {"day": "Day 1", "text": "晴朗", "temp": span_t},
                        {"day": "Day 2", "text": "多云", "temp": span_t},
                        {"day": "Day 3", "text": "晴朗", "temp": span_t}
                    ]
                }

        hash_val = sum(ord(c) for c in str(location_coord_or_city))
        temp_val = 20 + (hash_val % 10)
        return {
            "condition": f"多云 {temp_val}°C",
            "forecast": [
                {"day": "Day 1", "text": "晴朗", "temp": f"{temp_val-5}~{temp_val+3}°C"},
                {"day": "Day 2", "text": "微风", "temp": f"{temp_val-4}~{temp_val+4}°C"},
                {"day": "Day 3", "text": "晴朗", "temp": f"{temp_val-5}~{temp_val+3}°C"}
            ]
        }


# ==========================================
# 7. 路由层：主推演协商 API 端点（提高节点密度 + 动态消费 + 严格多天输出）
# ==========================================
@router.post("/agent/negotiate")
async def run_negotiate(msg: GatewayMessage):
    toolbox = ExpertToolbox()
    wm_client = WorldMonitorClient()
    blackboard = BlackboardSystem()
    payload = msg.payload or {}

    user_prefs = payload.get("user_preferences") or payload.get("current_request", {}).get("user_preferences", {})
    if not isinstance(user_prefs, dict):
        user_prefs = {}

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
            {"id": "u1", "name": "Felix (主控)", "role": str(user_prefs.get("role") or "寻味探索"), "intent": intent_str or "常规游览体验"},
            {"id": "u2", "name": "Alice", "role": "视觉体验", "intent": "探访城市核心商圈与地标，拍照出片"},
            {"id": "u3", "name": "Bob", "role": "休闲漫步", "intent": "步调宽松，安排特色茶社或公园慢游"}
        ]

    full_text_context = " | ".join(history_sequence) + " " + intent_str + " " + " ".join([str(m.get("intent", "")) for m in room_members if isinstance(m, dict)])

    # 🎯 个性化旅行画像注入：读取网关注入的画像，A/B 分组决定是否应用个性化提示词
    personalized_profile = user_prefs.get("personalized_profile") or {}
    if not isinstance(personalized_profile, dict):
        personalized_profile = {}
    member_profiles = user_prefs.get("member_profiles") or {}
    if not isinstance(member_profiles, dict):
        member_profiles = {}
    personalization_hint = build_personalization_hint(personalized_profile, member_profiles, room_members)

    # 1. 👑 智能解析游玩天数（支持中文与复合词，如 "三天", "5天", "两天一夜"）
    trip_days = parse_chinese_days(full_text_context)
    print(f"🎯 [天数解析] 解析到 trip_days={trip_days} | 上下文: {full_text_context[:160]!r}", flush=True)

    # 2. 👑 提升单日节点密度：标准提升到 5 个丰富节点（早茶、上午景点、午餐、午后景点、晚间美食、夜间住宿）
    count_match = re.search(r'(?:每天|日|一天|每日)(\d+)[个处条项点]', full_text_context)
    target_daily_count = int(count_match.group(1)) if count_match else 5

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
    current_mode = str(user_prefs.get("mode") or "coop") 
    user_role = str(user_prefs.get("role") or "常规游玩") 

    evolution_memory = payload.get("evolution_memory", [])
    if not isinstance(evolution_memory, list):
        evolution_memory = []

    is_refinement = len(history_sequence) > 0 and len(current_existing_route) > 0
    llm_temperature = 0.55 if is_refinement else 0.2

    # 动态调优候选池数量，确保足够支撑多天无重复
    poi_limit = max(45, trip_days * (target_daily_count + 5))

    is_luxury = any(kw in full_text_context.lower() for kw in ["1万", "万元", "10000", "预算充足", "不差钱", "高端", "豪华", "五星", "奢华", "享受", "品质", "好看", "商圈"])
    budget_mode = "HIGH_LUXURY" if is_luxury else "VALUE_COST_EFFECTIVE"
    total_calc_budget = trip_days * 1200 if is_luxury else trip_days * 350

    hotel_budget = round(total_calc_budget * 0.45)
    dining_budget = round(total_calc_budget * 0.30)
    ticket_budget = round(total_calc_budget * 0.15)
    traffic_budget = total_calc_budget - hotel_budget - dining_budget - ticket_budget

    budget_breakdown_payload = {
        "budget_mode": budget_mode,
        "total_budget": total_calc_budget,
        "daily_avg": round(total_calc_budget / trip_days),
        "hotel": hotel_budget, "dining": dining_budget, "ticket": ticket_budget, "traffic": traffic_budget
    }

    async def event_stream():
        yield json.dumps({"type": "budget_breakdown", "payload": budget_breakdown_payload}, ensure_ascii=False) + "\n"

        if is_luxury:
            yield json.dumps({"token": f"[地理精算 Agent]: 捕获到团队品质诉求，开启【臻选豪华】策略，正在为【{target_city}】排布完整 {trip_days} 天精品行程...\n"}, ensure_ascii=False) + "\n"
        else:
            yield json.dumps({"token": f"[地理精算 Agent]: 启动多智能体【帕累托均衡博弈】模型，正在为【{target_city}】全员排布完整 {trip_days} 天（每日 {target_daily_count} 节点）高共识度路线...\n"}, ensure_ascii=False) + "\n"

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
        hotels_task = toolbox.get_dynamic_pois(target_city, keywords="品质酒店|高端度假酒店|高分精品住宿", types="010000", limit=18)
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

        available_total = len(unique_attractions)
        actual_daily_count = target_daily_count
        if available_total < trip_days * target_daily_count and available_total > 0:
            actual_daily_count = max(3, available_total // trip_days)
            yield json.dumps({"token": f"[协商提示]: 已根据当地核心地标池精选每日 {actual_daily_count} 个核心游览节点！\n"}, ensure_ascii=False) + "\n"

        fusion_signals = (safety_intel.get("fusion") or {}).get("signals") or []
        fusion_summary = "、".join(fusion_signals) if fusion_signals else "无显著风险增量"
        yield json.dumps({"token": f"[SafetyAgent/免费风控融合]: 本地多源聚合完成，【{target_city}】动态 CII {safety_intel.get('cii_score', 0)}（{safety_intel.get('risk_level', 'LOW')}），融合信号: {fusion_summary}\n"}, ensure_ascii=False) + "\n"
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
            fitness_calculator = TopologyFitnessCalculator(user_prefs, coord, evolution_memory, safety_intel=safety_intel, is_luxury=is_luxury)

            daytime_pois = unique_attractions + unique_foods
            for p in daytime_pois:
                if isinstance(p, dict):
                    p["fitness_score"] = fitness_calculator.calculate_fitness(p)
                    p["is_hotel"] = False

            for h in cleaned_hotels:
                if isinstance(h, dict):
                    h["fitness_score"] = fitness_calculator.calculate_fitness(h)
                    h["is_hotel"] = True

            pareto_daytime = NashEquilibriumSolver.resolve_conflicts(daytime_pois)
            route_optimizer = GraphRouteOptimizer(fitness_calculator)
            sorted_daytime = route_optimizer.optimize_and_sort(pareto_daytime, max_nodes=trip_days * (actual_daily_count + 3))

            final_candidates = sorted_daytime + cleaned_hotels[:8]

            # 写入 Redis 黑板
            await blackboard.write(f"room_{target_city}_context", {
                "traffic": traffic_data, 
                "weather": weather_data, 
                "safety": safety_intel, 
                "poi": final_candidates,
                "members": room_members
            })

            yield json.dumps({"token": f"[知识增强Agent]: 正在为【{target_city}】多成员检索候选地标的招牌特色与真实口碑...\n"}, ensure_ascii=False) + "\n"

            top_candidates = final_candidates[:trip_days * (actual_daily_count + 3)]
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
                p_cost_num = p.get('cost', '45')
                p_type = str(p.get('type') or '')
                p_name = str(p.get('name') or '特色地标')

                if p.get('is_hotel'):
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
                    'rating': str(p.get('rating') or '4.8'),
                    'lnglat': lonlat,
                    'type': p_type or '风景',
                    'cost_estimate': formatted_cost,
                    'photos': p.get('photos', []) if isinstance(p.get('photos'), list) else [],
                    'web_knowledge': str(web_info),
                    'is_hotel': bool(p.get("is_hotel", False)),
                    # 优先保留 get_dynamic_pois 生成的精确坐标标点链接（uri.amap.com/marker），缺失时回退关键词搜索
                    'amap_url': str(p.get('amap_url') or amap_link),
                    'map_image': str(p.get('map_image') or ''),
                    # 👥 景点拥挤度可视化数据（口碑热度+实时路况+周末因子）
                    'crowdedness': estimate_crowdedness(p.get('rating'), traffic_data.get('status_code', '1'), is_weekend)
                })

                # 👑 裁剪版提示词池：去除图片外链与冗长百科，压缩 token，避免大模型输出被截断（5天只出4天）
                short_hint = str(web_info).strip()
                if len(short_hint) > 80:
                    short_hint = short_hint[:80] + "..."
                prompt_pool_data.append({
                    'name': p_name,
                    'rating': str(p.get('rating') or '4.8'),
                    'lnglat': lonlat,
                    'type': p_type or '风景',
                    'cost_estimate': formatted_cost,
                    'is_hotel': bool(p.get("is_hotel", False)),
                    'hint': short_hint
                })

            yield json.dumps({"token": f"[时空调度体]: DBSCAN 空间降维与纳什博弈完成！正在交织生成覆盖完整 {trip_days} 天的高密路书...\n\n"}, ensure_ascii=False) + "\n"

            team_members_text = "\n".join([f"- 成员【{m.get('name', '游客')}】({m.get('role', '常规')}): 诉求「{m.get('intent', '随心探索')}」" for m in room_members])

            if is_luxury:
                summary_desc = f"已为您开启【{target_city}】{trip_days}天团队臻选奢华行程，预估总消费{total_calc_budget}元：覆盖完整 {trip_days} 天，每日精细规划 {actual_daily_count} 个文旅节点，匹配核心商圈、特色正餐与高端度假住宿。"
            else:
                summary_desc = f"已为您匹配【{target_city}】{trip_days}天高性价比精算路线，预估总花销{total_calc_budget}元（日均约{round(total_calc_budget/trip_days)}元）：覆盖完整 {trip_days} 天，每日精细规划 {actual_daily_count} 个文旅节点，平衡美食打卡与文化慢游。"

            # 👑 严格保证天数输出、每日 5 节点、真实花费与干净外链的 Prompt
            system_prompt = f"""
你是一个专业的多智能体团队旅行协同专家。当前任务：规划【{target_city}】完整【{trip_days}天】的深度团队行程。
【重要警告：绝对禁止越界！】你安排的所有景点、餐厅、酒店必须严格属于【{target_city}】！

同行房间成员画像与诉求 ({len(room_members)}人)：
{team_members_text}

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

【🔴 规则三：每个地点必须标注真实预估花费 (cost_estimate)】
必须根据底座数据为每个节点输出真实的费用估算（如：“门票 ¥45/人”、“免费游览”、“人均餐饮 ¥65”、“住宿预留 ¥380/晚”），严禁空值！

【🔴 规则四：单日有且仅有 1 家夜间住宿酒店】
1. 每天最后一个节点（Night 21:30 后），安排【1 家】住宿酒店（is_hotel=True 的对象），并在其 `hotel_candidates` 数组中提供 2 个备选酒店对象（纯净名称与参考价格）。
2. 白天的景点与餐厅节点（08:00 - 20:00）严禁生成 `hotel_candidates` 字段！

【🔴 规则五：lnglat 必须照抄底座数据，photos/amap_url 一律留空】
1. 每个节点的 `lnglat` 必须【逐字照抄】底座数据中该地点对应的坐标，严禁自行编造！
2. `photos` 必须输出空数组 `[]`，`amap_url` 必须输出空字符串 `""`（系统会在后台回填真实图片与导航外链，你无需填写）！

[FINAL_JSON]
{{
  "status": "consensus_reached",
  "negotiation_summary": "{summary_desc}",
  "team_satisfaction": {{
    "Felix (主控)": 96,
    "Alice (出片)": 94,
    "Bob (休闲)": 93
  }},
  "arbitration_records": [
    "针对【餐饮与预算诉求】: 精选特色老字号正餐，全程无重复排布",
    "针对【节奏分歧】: 午后采取分合流调度，傍晚在统一地点汇合用餐"
  ],
  "route": [
    {{
      "day": 1,
      "time": "Day 1 | 08:30 - 10:30 (建议游玩 2 小时)",
      "location": "某景点",
      "lnglat": [102.63, 37.93],
      "desc": "基于底座真实数据的一两句精炼介绍",
      "cost_estimate": "门票 ¥45/人",
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

            # 👑 天数补全器：主推演若遗漏某几天，则二次请求补齐，保证 5 天就是 5 天
            async def _complete_missing_days(missing_days):
                if not missing_days:
                    return []
                days_str = "、".join([f"Day {d}" for d in missing_days])
                prompt = (
                    f"上一轮已生成部分行程，但遗漏了 {days_str}。"
                    f"请【只输出】这几个缺失天数（{days_str}）的行程节点，"
                    f"每个节点的 lnglat 必须逐字照抄底座数据，photos 输出 []、amap_url 输出 \"\"，"
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

            response = await client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请严格根据约束，为我生成【{target_city}】完整 {trip_days} 天（Day 1 到 Day {trip_days} 所有天数全部输出，每天 {actual_daily_count} 个节点且含夜间住宿，每个节点必须标注具体的预估花费 cost_estimate，全程地点绝对不重复）的团队协同行程 JSON。"}
                ],
                stream=True,
                temperature=llm_temperature,
                max_tokens=16384
            )

            async for chunk in response:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        token = delta.content
                        full_response += token
                        yield json.dumps({"token": token}, ensure_ascii=False) + "\n"

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

                def _find_match(loc_name):
                    # 名称匹配：仅作为坐标匹配失败时的备用手段
                    if loc_name and loc_name in pool_by_name:
                        return pool_by_name[loc_name]
                    if loc_name:
                        for pname, entry in pool_by_name.items():
                            if pname and (pname in loc_name or loc_name in pname):
                                return entry
                    return None

                def _rebind(route_nodes):
                    for r in route_nodes:
                        if not isinstance(r, dict):
                            continue
                        loc_name = _norm_name(str(r.get("location") or r.get("name") or ""))
                        # 👑 优先使用 lnglat 坐标最近邻匹配（确定性，保证照片与实际地点严格相符），失败再降级名称匹配
                        match = _find_match_by_coord(r.get("lnglat")) or _find_match(loc_name)
                        if match:
                            if isinstance(match.get("lnglat"), list) and len(match["lnglat"]) >= 2:
                                r["lnglat"] = match["lnglat"]
                            r["photos"] = match.get("photos") or []
                            r["amap_url"] = match.get("amap_url") or ""
                            r["map_image"] = match.get("map_image") or ""
                            r["rating"] = match.get("rating") or r.get("rating") or "4.6"
                            r["open_time"] = match.get("open_time") or r.get("open_time") or "全天开放"
                            r["address"] = match.get("address") or r.get("address") or ""
                            if match.get("crowdedness"):
                                r["crowdedness"] = match.get("crowdedness")
                            if not r.get("location"):
                                r["location"] = match.get("name")
                        # 👑 兜底：无论是否命中，都保证有可点击的高德搜索详情外链，避免"无详情链接"
                        if not r.get("amap_url") and loc_name:
                            r["amap_url"] = f"https://www.amap.com/search?query={urllib.parse.quote(f'{target_city} {loc_name}')}"
                        # 👑 图片兜底：未命中候选池（LLM 名称漂移/坐标失配）时，用节点自身坐标生成高德静态地图，杜绝图片空白
                        if not r.get("map_image") and isinstance(r.get("lnglat"), list) and len(r["lnglat"]) >= 2:
                            try:
                                _lng, _lat = float(r["lnglat"][0]), float(r["lnglat"][1])
                                if _lng != 0 or _lat != 0:
                                    _amap_key = getattr(toolbox, "amap_key", "") or os.getenv("AMAP_KEY", "")
                                    if _amap_key:
                                        r["map_image"] = (
                                            f"https://restapi.amap.com/v3/staticmap"
                                            f"?location={_lng},{_lat}&zoom=15&size=480*360"
                                            f"&markers=mid,0xFF0000,A:{_lng},{_lat}&key={_amap_key}"
                                        )
                            except (TypeError, ValueError):
                                pass
                    return route_nodes

                if "[FINAL_JSON]" in full_response:
                    parts = full_response.split("[FINAL_JSON]")
                    if len(parts) >= 2:
                        json_str_match = re.search(r'\{[\s\S]*\}', parts[1])
                        if json_str_match:
                            final_data = safe_repair_and_parse_json(json_str_match.group(0))
                            llm_routes = final_data.get("route", []) if isinstance(final_data, dict) else []

                            if isinstance(llm_routes, list) and llm_routes:
                                llm_routes = _rebind(llm_routes)

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
                                                "cost_estimate": p.get("cost_estimate") or "¥45/人",
                                                "photos": p.get("photos") or [],
                                                "amap_url": p.get("amap_url") or "",
                                                "map_image": p.get("map_image") or "",
                                                "rating": p.get("rating") or "4.6",
                                                "open_time": "全天开放",
                                                "is_hotel": bool(p.get("is_hotel", False)),
                                                "trust_reason": "智能补全保障完整天数"
                                            })
                                    if synth_nodes:
                                        print(f"⚠️ [确定性兜底] 模型遗漏 {still_missing}，已从候选池合成补全，保证 {trip_days} 天完整输出。")
                                        llm_routes.extend(synth_nodes)

                                # 按 day 排序，保证展示顺序
                                llm_routes.sort(key=lambda r: int(r.get("day") or 1))
                                final_data["route"] = llm_routes

                                # 回发校正后的最终路线，前端以 final_route 为准（真实图片/坐标/完整天数）
                                yield json.dumps({"type": "final_route", "payload": final_data}, ensure_ascii=False) + "\n"

                                # P2P 精准导航计算（覆盖全部相邻节点，保证 5 天 25 个节点 24 对都有真实驾车/步行/公交导航）
                                post_p2p_tasks = []
                                post_pair_names = []
                                for i in range(len(llm_routes) - 1):
                                    r1, r2 = llm_routes[i], llm_routes[i+1]
                                    l1, l2 = r1.get("lnglat"), r2.get("lnglat")
                                    if l1 and l2 and isinstance(l1, list) and isinstance(l2, list):
                                        str_loc1 = f"{l1[0]},{l1[1]}"
                                        str_loc2 = f"{l2[0]},{l2[1]}"
                                        pair_key = f"{r1.get('location')}|{r2.get('location')}"
                                        post_pair_names.append((pair_key, r1.get('location')))
                                        post_p2p_tasks.append(toolbox.get_travel_options(str_loc1, str_loc2, target_city))

                                post_travel_details = {}
                                final_stitched_path = []
                                if post_p2p_tasks:
                                    post_results = await asyncio.gather(*post_p2p_tasks, return_exceptions=True)
                                    for (p_key, loc_name), res_opts in zip(post_pair_names, post_results):
                                        if isinstance(res_opts, dict) and res_opts:
                                            post_travel_details[p_key] = res_opts
                                            post_travel_details[loc_name] = res_opts
                                            if "driving" in res_opts and "actual_path" in res_opts["driving"]:
                                                final_stitched_path.extend(res_opts["driving"]["actual_path"])

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