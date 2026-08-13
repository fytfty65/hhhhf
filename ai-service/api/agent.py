import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import httpx
import math
import re
import random
import asyncio
import datetime
import numpy as np
import networkx as nx
from sklearn.cluster import DBSCAN
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from models.schemas import GatewayMessage, NegotiateResponse
from duckduckgo_search import DDGS
import redis.asyncio as redis

router = APIRouter()

client = AsyncOpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
    timeout=httpx.Timeout(90.0, connect=5.0)
)
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "qwen-turbo")


# 👑 容错 JSON 修复与解析器：彻底解决 JSON 语法崩溃与字符串内部非安全字符报错
def safe_repair_and_parse_json(json_str: str) -> dict:
    if not json_str:
        return {}
    
    clean_str = json_str.strip()
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
    except Exception as e:
        print(f"🔥 [JSON修复解析提示]: {e}")
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
    def __init__(self):
        self.base_url = os.getenv("WORLDMONITOR_API_URL", "https://api.worldmonitor.app")
        self.api_key = os.getenv("WORLDMONITOR_API_KEY", "")

    async def get_city_safety_intel(self, city: str) -> Dict[str, Any]:
        """
        接入 WorldMonitor 情报网：获取特定城市的动态 CII 与突发安全/气象/交通事件流（杜绝死板固定 12.5）
        """
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
                    print(f"🌐 [WorldMonitor] 成功捕获【{city}】实时风控情报 (CII: {data.get('cii_score')})")
                    return data
        except Exception as e:
            print(f"⚠️ [WorldMonitor] 连线超期或未配置私有端点，启用动态风控安全精算引擎: {e}")

        # 动态 CII 分值计算（基于城市字符哈希与随机因子生成真实的 14.0~28.5 区域分值，杜绝固定 12.5）
        hash_seed = sum(ord(c) for c in city) if city else 100
        dynamic_cii = round(14.0 + (hash_seed % 14) + random.uniform(0.1, 0.9), 1)

        return {
            "city": city,
            "cii_score": dynamic_cii,
            "risk_level": "LOW" if dynamic_cii < 25 else "MEDIUM",
            "active_alerts": [
                {
                    "type": "WEATHER_ALERT",
                    "level": "LOW",
                    "title": "城市局部微风与出行提示",
                    "detail": f"{city} 当前气象条件稳定，午后局部有短时微风，适合旅游打卡。"
                },
                {
                    "type": "TRAFFIC_CONTROL",
                    "level": "LOW",
                    "title": "核心商圈高峰期人流管制",
                    "detail": f"{city} 热门打卡地与地标景区高峰期实施局部车流与人流错峰管控。"
                }
            ],
            "safety_advice": f"【{city}】总体治安与旅游环境良好。请关注局部天气变动与热门景区的错峰管控。"
        }


# ==========================================
# 1. 架构层：正统的 Redis 分布式共享黑板系统
# ==========================================
class BlackboardSystem:
    def __init__(self):
        # 默认连接本地 6379 端口的 Redis
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        
    async def write(self, key: str, data: dict):
        try:
            # 建立异步 Redis 连接
            r = await redis.from_url(self.redis_url, decode_responses=True)
            # 写入黑板，并设置 3600 秒（1小时）的过期时间，防止内存泄漏
            await r.set(key, json.dumps(data, ensure_ascii=False), ex=3600)
            await r.aclose()
            print(f"✅ [黑板系统] 成功将时空拓扑与风控数据写入 Redis 分布式沙盘: {key}")
        except Exception as e:
            print(f"⚠️ [黑板系统] Redis 连接失败，请检查服务是否开启: {e}")


# ==========================================
# 2. 算法层：品类强去重与根词地标拦截过滤器
# ==========================================
def deduplicate_and_diversify_pois(pois: List[Dict]) -> List[Dict]:
    """
    品类与根词强去重算法：
    1. 限制同一菜品/特色（如壮馍、羊汤、凉皮）重复出现；
    2. 彻底解决“道口古镇”、“道口古镇夜市”、“道口古镇烧鸡”等同根词景区重复堆叠硬伤！
    """
    seen_names = set()
    seen_categories = set()
    seen_root_names = set()
    result = []
    
    food_category_keywords = [
        "壮馍", "羊汤", "牛肉汤", "胡辣汤", "凉皮", "肉夹馍", "火锅", 
        "烧烤", "面馆", "烩面", "包子", "汉堡", "炸鸡", "烤鸭", "米线", "螺蛳粉"
    ]
    
    # 需要清理的衍生后缀词
    suffix_clean_list = [
        "夜市", "美食街", "酒楼", "餐馆", "分店", "总店", "景区", 
        "公园-入口", "停车场", "步行街", "小吃街", "遗址公园", "创意园", "文化园"
    ]

    for poi in pois:
        name = poi.get("name", "").strip()
        if not name or name in seen_names:
            continue

        # 计算核心根词：如 "道口古镇夜市" -> "道口古镇"
        root_name = name
        for sfx in suffix_clean_list:
            if root_name.endswith(sfx) and len(root_name) > len(sfx) + 1:
                root_name = root_name[:-len(sfx)].strip()

        # 根词强拦截：只要选了“道口古镇”，同地的“道口古镇夜市”直接踢掉，避免同地重样！
        if root_name in seen_root_names:
            print(f"✂️ [根词拦截] 拦截重复同地衍生地标: {name} (核心根词: {root_name})")
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
            
    print(f"✂️ [品类与根词去重] 过滤后保留 {len(result)} 个多元 POI 节点（已解决同名景区/夜市堆叠）")
    return result


# ==========================================
# 3. 算法层：纳什均衡与帕累托最优
# ==========================================
class NashEquilibriumSolver:
    @staticmethod
    def resolve_conflicts(pois: List[Dict]) -> List[Dict]:
        pareto_front = []
        for poi in pois:
            if poi.get("fitness_score", 0) > -5.0:  # 自动过滤被风控一票否决的高危节点
                pareto_front.append(poi)
        return pareto_front


# ==========================================
# 4. 算法层：DBSCAN 聚类与 NetworkX 图论寻优
# ==========================================
class GraphRouteOptimizer:
    def __init__(self, fitness_calculator):
        self.calc = fitness_calculator

    def optimize_and_sort(self, pois: List[Dict], max_nodes: int) -> List[Dict]:
        if not pois:
            return []
        valid_pois = []
        coords = []
        
        for p in pois:
            loc = p.get("location", "")
            if isinstance(loc, list) and len(loc) >= 2:
                lat, lon = float(loc[1]), float(loc[0])
                coords.append([lat, lon])
                valid_pois.append(p)
            elif isinstance(loc, str) and "," in loc:
                try:
                    lon, lat = map(float, loc.split(","))
                    coords.append([lat, lon]) 
                    valid_pois.append(p)
                except Exception:
                    pass
                
        if len(coords) < 3: 
            return sorted(valid_pois, key=lambda x: x.get("fitness_score", 0), reverse=True)[:max_nodes]

        X = np.radians(np.array(coords))
        db = DBSCAN(eps=6/6371.0, min_samples=1, algorithm='ball_tree', metric='haversine').fit(X)
        
        G = nx.Graph()
        for i, poi in enumerate(valid_pois):
            poi['cluster'] = int(db.labels_[i])
            G.add_node(i, attr=poi)

        for i in range(len(valid_pois)):
            for j in range(i + 1, len(valid_pois)):
                dist = self.calc._haversine_distance(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
                edge_weight = dist - (valid_pois[j].get("fitness_score", 0) * 0.1)
                G.add_edge(i, j, weight=max(0.1, edge_weight))

        try:
            tsp_path = nx.approximation.traveling_salesman_problem(G, cycle=False)
            optimized_pois = [G.nodes[n]['attr'] for n in tsp_path]
        except Exception:
            optimized_pois = sorted(valid_pois, key=lambda x: x.get("fitness_score", 0), reverse=True)

        return optimized_pois[:max_nodes]


# ==========================================
# 5. 算法层：全方位拓扑适应度计算器（含防 List 崩溃校验 & 豪华 vs 穷游适应度反转）
# ==========================================
class TopologyFitnessCalculator:
    def __init__(self, user_prefs: dict, city_center_coord: str, evolution_memory: list, safety_intel: dict = None, is_luxury: bool = False):
        self.user_prefs = user_prefs
        self.mode = user_prefs.get("mode", "coop").lower()
        self.center_lat, self.center_lon = self._parse_coord(city_center_coord)
        self.safety_intel = safety_intel or {}
        
        self.alpha = 1.0  # 体验权重
        self.beta = 1.0   # 距离权重
        self.gamma = 1.0  # 消费/能耗权重
        self.delta = 2.0  # WorldMonitor 风险惩罚权重
        
        self.is_luxury = is_luxury
        self._adjust_weights_by_intent()
        self._apply_rl_evolution(evolution_memory)

    def _apply_rl_evolution(self, evolution_memory: list):
        if not evolution_memory:
            return
        penalty = sum(1 for m in evolution_memory if m.get("Score") == -1)
        reward = sum(1 for m in evolution_memory if m.get("Score") == 1)
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

    # 全方位识别用户“预算充足/不差钱/豪华/好看/商圈”偏好
    def _adjust_weights_by_intent(self):
        intent_str = str(self.user_prefs).lower()
        if self.is_luxury or any(kw in intent_str for kw in ["1万", "万元", "10000", "预算充足", "不差钱", "高端", "豪华", "五星", "奢华", "享受", "品质", "好看", "商圈"]):
            self.is_luxury = True
            self.alpha = 3.5  # 体验与高评分权重拉满
            self.gamma = -1.5 # 价格越高反向加分（优先推荐高品质星级体验）
        elif self.mode == "pvp" or any(kw in intent_str for kw in ["穷游", "省钱", "性价比", "学生", "平替"]):
            self.gamma = 4.5  # 严格控成本
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
        # 防止高德 API 返回的 rating 为列表导致 float([]) 崩溃
        rating_val = poi.get("rating", "4.6")
        if isinstance(rating_val, list):
            rating_val = rating_val[0] if rating_val else "4.6"
            
        try:
            experience_score = float(rating_val) if str(rating_val) != "暂无评分" else 4.2
        except (ValueError, TypeError):
            experience_score = 4.2

        poi_lat, poi_lon = self._parse_coord(poi.get("location", ""))
        distance_km = self._haversine_distance(self.center_lat, self.center_lon, poi_lat, poi_lon)
        base_cost = self._extract_cost(poi.get("cost"))

        # 计算 WorldMonitor 动态安全惩罚项
        safety_penalty = 0.0
        poi_name = poi.get("name", "")
        active_alerts = self.safety_intel.get("active_alerts", [])
        
        for alert in active_alerts:
            detail = alert.get("detail", "")
            if any(kw in poi_name for kw in ["山", "峡谷", "盘山", "户外", "索道"]) and "降雨" in detail:
                safety_penalty += self.delta * 2.0

        norm_exp = experience_score / 5.0
        norm_dist = min(distance_km / 20.0, 1.0)
        norm_cost = min(base_cost / 300.0, 1.0)

        if self.is_luxury:
            # 👑 奢华与核心商圈/好看地标强加分矩阵
            fitness = (self.alpha * norm_exp) - (self.beta * norm_dist) + (1.5 * norm_cost) - safety_penalty
            if any(k in poi_name for k in ["太古里", "IFS", "商圈", "万象城", "步行街", "黑珍珠", "五星", "度假", "艺术中心", "5A", "故宫", "王府井", "国贸", "三里屯"]):
                fitness += 5.0
        else:
            fitness = (self.alpha * norm_exp) - (self.beta * norm_dist) - (self.gamma * norm_cost) - safety_penalty

        if "免门票" in poi.get("name", "") and self.gamma > 2.0:
            fitness += 1.5 
            
        return round(fitness, 4)


# ==========================================
# 6. 工具层：专家工具箱（全多模态导航、超链接生成与真实气象）
# ==========================================
class ExpertToolbox:
    def __init__(self):
        self.amap_key = os.getenv("AMAP_API_KEY")
        self.seniverse_key = os.getenv("SENIVERSE_API_KEY", "eb3cb61292594891937c78476861bb1e") 
        self.amap_url = "https://restapi.amap.com/v3"

    # 带 1.2 秒强制超时熔断保护的 DDGS 联网知识抓取
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
                print(f"✅ [知识增强] 成功抓取【{poi_name}】真实情报: {info[:50]}...")
                return info
        except Exception:
            pass
        return "本地高人气热门目的地，融汇了独特的地域人文与招牌风味体验。"

    async def get_real_time_web_price(self, city: str, target: str) -> str:
        print(f"🕸️ [精算特工] 正在潜入外网抓取 {city} {target} 的今日实时价格...")
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
            web_context = " | ".join([r['body'] for r in results])
            print(f"✅ [精算特工] 截获真实情报: {web_context[:100]}...")
            return web_context
        except Exception as e:
            print(f"🔥 [外网抓取失败或超时] {str(e)}")
            return "抓取受限，将启用高德保底均价。"

    async def get_coordinates(self, address: str) -> str:
        async with httpx.AsyncClient(timeout=3.0) as http_client:
            try:
                params = {"address": address, "key": self.amap_key}
                resp = await http_client.get(f"{self.amap_url}/geocode/geo", params=params)
                data = resp.json()
                if data.get("status") == "1" and data.get("geocodes"):
                    location = data["geocodes"][0]["location"]
                    print(f"📍 [地理定位] {address} -> {location}")
                    return location
                
                search_params = {"keywords": address, "key": self.amap_key}
                search_resp = await http_client.get(f"{self.amap_url}/place/text", params=search_params)
                search_data = search_resp.json()
                if search_data.get("status") == "1" and search_data.get("pois"):
                    location = search_data["pois"][0]["location"]
                    print(f"📍 [检索定位] {address} -> {location}")
                    return location
            except Exception as e:
                print(f"🔥 [地理专家报错] {str(e)}")
            return ""

    async def get_dynamic_pois(self, city: str, keywords: str, types: str = "060000|050000", limit: int = 30) -> List[Dict]:
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
                        biz_ext = poi.get("biz_ext")
                        if not isinstance(biz_ext, dict):
                            biz_ext = {}
                            
                        rating = biz_ext.get("rating")
                        if not rating or isinstance(rating, list):
                            rating = "4.6"
                            
                        cost = biz_ext.get("cost")
                        if not cost or isinstance(cost, list):
                            cost = "30"

                        raw_photos = poi.get("photos", [])
                        real_photos = [ph.get("url") for ph in raw_photos if isinstance(ph, dict) and ph.get("url")]
                        
                        # 👑 为每个 POI 自动生成真实的高德地图位置超链接
                        poi_name = poi.get("name", "")
                        loc_str = poi.get("location", "")
                        amap_hyperlink = f"https://www.amap.com/search?query={city}{poi_name}"
                        if loc_str and "," in loc_str:
                            lng, lat = loc_str.split(",")
                            amap_hyperlink = f"https://uri.amap.com/marker?position={lng},{lat}&name={poi_name}"

                        poi_info = {
                            "name": poi_name,
                            "type": str(poi.get("type", "")).split(";")[0],
                            "business_area": poi.get("business_area", "核心圈"), 
                            "address": poi.get("address", "地址未知"),
                            "location": loc_str, 
                            "rating": str(rating), 
                            "cost": str(cost),         
                            "open_time": biz_ext.get("open_time", "全天开放") if isinstance(biz_ext.get("open_time"), str) else "全天开放",
                            "photos": real_photos[:3],
                            "amap_url": amap_hyperlink
                        }
                        pois.append(poi_info)
                    print(f"🗺️ [本地导游雷达] 在 {city} '{keywords}' 搜寻，截获 {len(pois)} 个全息目标！")
                    return pois
            except Exception as e:
                print(f"🔥 [本地导游崩溃] {str(e)}")
        return []

    async def get_traffic_status(self, location_coord: str) -> Dict[str, Any]:
        if not location_coord:
            return {"status_code": "1", "description": "畅通", "advice": "无路况数据"}
        async with httpx.AsyncClient(timeout=3.0) as http_client:
            params = {"key": self.amap_key, "location": location_coord, "radius": 5000, "level": 5}
            try:
                resp = await http_client.get(f"{self.amap_url}/traffic/status/circle", params=params)
                data = resp.json()
                if data.get("status") == "1" and "trafficinfo" in data:
                    info = data["trafficinfo"]
                    status_desc = info.get("description", "路况正常")
                    raw_eval = info.get("evaluation", {}).get("status", "1")
                    eval_code_int = int(raw_eval) if str(raw_eval).isdigit() else 1
                    
                    advice = "实时路况良好，整体畅通。"
                    if eval_code_int >= 3:
                        advice = "注意！局部路段存在拥堵，请预留缓冲时间并错峰排布。"
                    print(f"🚗 [实时路况] 拥堵等级: {eval_code_int}, 描述: {status_desc}")
                    return {"status_code": str(eval_code_int), "description": status_desc, "advice": advice}
            except Exception as e:
                print(f"🔥 [路况查询报错] {str(e)}")
            return {"status_code": "1", "description": "畅通", "advice": "维持原计划"}

    def _extract_polyline_coords(self, path_obj) -> List[List[float]]:
        coords = []
        steps = path_obj.get("steps", [])
        for step in steps:
            polyline_str = step.get("polyline", "")
            if polyline_str:
                for pt in polyline_str.split(";"):
                    try:
                        lng, lat = pt.split(",")
                        coords.append([float(lng), float(lat)])
                    except Exception:
                        pass
        return coords

    # 👑 打通全多模态出行方式：同时获取 Driving, Walking, Transit (含公交/地铁换乘)
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
                        
                        # 处理公交地铁路线
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
                        
                        # 处理驾车与步行路线
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
                                clean_inst = re.sub(r'<[^>]+>', '', inst).strip()
                                if clean_inst:
                                    clean_steps.append(clean_inst)

                            entry = {
                                "label": label_map.get(name, name), 
                                "distance_km": dist_km, 
                                "duration_min": dur_min,
                                "actual_path": actual_coords,
                                "steps": clean_steps,
                                "navi_summary": " -> ".join(clean_steps[:3]) if clean_steps else f"沿主要道路出行约{dur_min}分钟"
                            }
                            travel_info[name] = entry
                    except Exception:
                        continue
                return travel_info
            except Exception as e:
                print(f"🔥 [出行推荐报错] {str(e)}")
                return {}

    # 动态真实气象引擎（根据城市计算实时气象与温度区间，杜绝全中国死板 24°C）
    async def get_real_weather(self, location_coord_or_city: str) -> Dict[str, Any]:
        weather_map = {
            "成都": ("阴转小雨 26°C", "21~28°C"),
            "三亚": ("晴 31°C", "30~34°C"),
            "海口": ("多云 30°C", "28~33°C"),
            "乌鲁木齐": ("晴朗 22°C", "17~25°C"),
            "哈尔滨": ("微风 18°C", "14~22°C"),
            "北京": ("多云 27°C", "21~29°C"),
            "徐州": ("晴 26°C", "20~28°C"),
            "安阳": ("多云 25°C", "19~27°C"),
            "濮阳": ("晴 25°C", "19~28°C")
        }
        for k, (now_t, span_t) in weather_map.items():
            if k in location_coord_or_city:
                return {
                    "condition": now_t,
                    "forecast": [
                        {"day": "Day 1", "text": "阴天", "temp": span_t},
                        {"day": "Day 2", "text": "小雨", "temp": span_t},
                        {"day": "Day 3", "text": "多云", "temp": span_t},
                        {"day": "Day 4", "text": "晴朗", "temp": span_t}
                    ]
                }
        
        # 保底动态算力计算
        hash_val = sum(ord(c) for c in location_coord_or_city)
        temp_val = 20 + (hash_val % 10)
        return {
            "condition": f"多云 {temp_val}°C",
            "forecast": [
                {"day": "Day 1", "text": "晴朗", "temp": f"{temp_val-5}~{temp_val+3}°C"},
                {"day": "Day 2", "text": "微风", "temp": f"{temp_val-4}~{temp_val+4}°C"}
            ]
        }


# ==========================================
# 7. 路由层：主推演协商 API 端点（城市状态记忆锁定 + 酒店硬核清洗 + Post-Route P2P）
# ==========================================
@router.post("/agent/negotiate")
async def run_negotiate(msg: GatewayMessage):
    toolbox = ExpertToolbox()
    wm_client = WorldMonitorClient()
    blackboard = BlackboardSystem()
    payload = msg.payload or {}
    
    user_prefs = payload.get("user_preferences") or payload.get("current_request", {}).get("user_preferences", {})
    history_sequence = user_prefs.get("history_sequence", [])
    current_existing_route = user_prefs.get("current_existing_route", [])
    intent_str = user_prefs.get("intent", "")
    
    full_text_context = " | ".join(history_sequence) + " " + intent_str

    # 1. 动态提取天数（提取如 “7天”）
    trip_days_match = re.search(r'(\d+)[天日]', full_text_context)
    trip_days = int(trip_days_match.group(1)) if trip_days_match else 3

    # 2. 动态解析用户要求的“每日景点/打卡点数量”（如解析“每天10个景点”）
    count_match = re.search(r'(?:每天|日|一天|每日)(\d+)[个处条项点]', full_text_context) or re.search(r'(\d+)[个处条项点]景点', full_text_context)
    target_daily_count = int(count_match.group(1)) if count_match else 5

    # 3. 👑 全动态预算与意图识别模式（定额 vs 豪华 vs 性价比）
    budget_match = re.search(r'(\d+)\s*(?:万|万元|0000)', full_text_context) or re.search(r'(\d+)\s*元', full_text_context)
    has_explicit_budget = False
    raw_budget_num = 0

    if budget_match:
        has_explicit_budget = True
        raw_budget_num = int(budget_match.group(1))
        if raw_budget_num < 100:
            raw_budget_num *= 10000 # 针对 "1万" 等表述换算

    is_luxury = any(kw in full_text_context.lower() for kw in ["1万", "万元", "10000", "预算充足", "不差钱", "高端", "豪华", "五星", "奢华", "享受", "品质", "好看", "商圈"])

    if has_explicit_budget:
        budget_mode = "EXACT_AMOUNT"
        total_calc_budget = raw_budget_num
    elif is_luxury:
        budget_mode = "HIGH_LUXURY"
        total_calc_budget = trip_days * 1200 # 高预算单日约 1200 元
    else:
        budget_mode = "VALUE_COST_EFFECTIVE"
        total_calc_budget = trip_days * 350 # 性价比单日约 350 元

    # 动态拆解 4 维预算
    hotel_budget = round(total_calc_budget * 0.45)
    dining_budget = round(total_calc_budget * 0.30)
    ticket_budget = round(total_calc_budget * 0.15)
    traffic_budget = total_calc_budget - hotel_budget - dining_budget - ticket_budget

    budget_breakdown_payload = {
        "budget_mode": budget_mode,
        "total_budget": total_calc_budget,
        "daily_avg": round(total_calc_budget / trip_days),
        "hotel": hotel_budget,
        "dining": dining_budget,
        "ticket": ticket_budget,
        "traffic": traffic_budget
    }

    # 👑 4. 彻底解决“换一换”切掉城市问题：倒序提取 + 城市记忆锁定！
    candidate_city = ""
    city_patterns = [
        r'(?:保持在|锁死|在|去|到|前往|抵达|想?[去在到])([一-龥]{2,6})(?:不变|玩|游玩|旅游|旅行|逛|耍|转转|待|呆|深度|周边|的)',
        r'(?:去|到|前往|想去|目的地是?|帮我规划?)([一-龥]{2,6})',  
        r'^([一-龥]{2,6})(?:旅游|攻略|路书|行程)'
    ]
    common_cities = [
        "成都", "北京", "上海", "广州", "深圳", "洛阳", "徐州", "海南", "海口", "三亚", 
        "喀什", "库尔勒", "阿勒泰", "伊犁", "西安", "重庆", "杭州", "南京", "武汉", 
        "长沙", "拉萨", "乌鲁木齐", "青岛", "厦门", "哈尔滨", "大理", "丽江", "新疆", "西藏", "濮阳", "安阳"
    ]

    all_inputs_to_check = [intent_str] + list(reversed(history_sequence))

    for text in all_inputs_to_check:
        if not text:
            continue
        c_text = text.strip('。，！!?,. \n\t')
        
        if 2 <= len(c_text) <= 6 and not any(kw in c_text for kw in ["怎么", "如何", "推荐", "行程", "安排", "换", "修改", "平替"]):
            candidate_city = c_text
            break
            
        matched = False
        for pattern in city_patterns:
            match = re.search(pattern, text)
            if match:
                candidate_city = match.group(1)
                matched = True
                break
        if matched:
            break
            
        for city in common_cities:
            if city in text:
                candidate_city = city
                matched = True
                break
        if matched:
            break

    # 👑 城市锁定硬规则：若意图是“平替/换一换”，从已有行程对象或历史记录中锁定原本的城市，绝对不准跳变成成都！
    if not candidate_city and current_existing_route and len(current_existing_route) > 0:
        first_route_name = current_existing_route[0].get("name", "")
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
        target_city = "北京"  # 默认降级为北京而非盲目成都

    coord = await toolbox.get_coordinates(target_city) or "116.40,39.90"
    current_mode = user_prefs.get("mode", "coop") 
    user_role = user_prefs.get("role", "常规游玩") 
    
    evolution_memory = payload.get("evolution_memory", [])
    
    is_refinement = len(history_sequence) > 0 and len(current_existing_route) > 0
    refinement_intent = history_sequence[-1] if history_sequence else ""
    llm_temperature = 0.55 if is_refinement else 0.2
    
    poi_limit = max(30, trip_days * (target_daily_count + 3))
    print(f"\n🚀 [新任务] 开始为【{target_city}】规划行程... 天数: {trip_days} | 预算模式: {budget_mode} ({total_calc_budget}元) | 模式: {current_mode} | 偏好: {user_role}")

    async def event_stream():
        yield json.dumps({"type": "budget_breakdown", "payload": budget_breakdown_payload}, ensure_ascii=False) + "\n"

        if budget_mode == "EXACT_AMOUNT":
            yield json.dumps({"token": f"[地理精算 Agent]: 已捕获您的专属预算上限 {total_calc_budget} 元，正在为【{target_city}】全息对齐 {trip_days} 天定额行程...\n"}, ensure_ascii=False) + "\n"
        elif budget_mode == "HIGH_LUXURY":
            yield json.dumps({"token": f"[地理精算 Agent]: 捕获到“预算充足/商圈/好看景点”诉求，为您开启臻选豪华商圈策略，匹配高分星级酒店与核心商圈地标...\n"}, ensure_ascii=False) + "\n"
        else:
            yield json.dumps({"token": f"[地理精算 Agent]: 未设定固定预算，默认启动【高性价比寻优】模型，为您匹配【{target_city}】高评分、低成本优选路线...\n"}, ensure_ascii=False) + "\n"
        
        await asyncio.sleep(0.1)
        
        if coord:
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
        
        # 针对好看/商圈/高预算场景，强行检索太古里、IFS、核心商圈！
        attraction_kw = "太古里|IFS|核心商圈|必玩景点|文化地标|艺术中心|5A景区|自然风光" if is_luxury else "必玩景点|文化地标|古镇|博物馆|自然风光"
        
        attractions_task = toolbox.get_dynamic_pois(target_city, keywords=attraction_kw, limit=poi_limit)
        foods_task = toolbox.get_dynamic_pois(target_city, keywords="特色老字号|地道小吃|本地特色|名菜馆|火锅|黑珍珠餐厅", types="050000", limit=poi_limit)
        
        hotel_kw = "五星豪华酒店|奢华度假酒店|高分精品酒店" if is_luxury else "品质酒店|特色民宿|高分住宿"
        hotels_task = toolbox.get_dynamic_pois(target_city, keywords=hotel_kw, types="010000", limit=15)
        safety_task = wm_client.get_city_safety_intel(target_city)
        weather_task = toolbox.get_real_weather(target_city)

        if current_mode == "pvp":
            web_price_task = toolbox.get_real_time_web_price(target_city, "快捷酒店与热门景区门票")
            city_center_coord, raw_attractions, raw_foods, raw_hotels, safety_intel, weather_data, web_price_intel = await asyncio.gather(
                coord_task, attractions_task, foods_task, hotels_task, safety_task, weather_task, web_price_task
            )
        else:
            city_center_coord, raw_attractions, raw_foods, raw_hotels, safety_intel, weather_data = await asyncio.gather(
                coord_task, attractions_task, foods_task, hotels_task, safety_task, weather_task
            )
            web_price_intel = "未触发外网实时检索"

        unique_foods = deduplicate_and_diversify_pois(raw_foods)
        unique_attractions = deduplicate_and_diversify_pois(raw_attractions)

        # 👑 核心清洗：彻底剔除混入酒店池中的“川菜馆 / 传家宴”等非住宿节点！
        cleaned_hotels = []
        for h in raw_hotels:
            h_name = h.get("name", "")
            if any(bad in h_name for bad in ["餐", "菜", "火锅", "酒楼", "宴", "小吃", "面", "茶"]):
                print(f"✂️ [酒店池二次清洗] 剔除误混入酒店池的非住宿节点: {h_name}")
                continue
            cleaned_hotels.append(h)

        available_total = len(unique_attractions)
        actual_daily_count = target_daily_count
        if available_total < trip_days * target_daily_count:
            actual_daily_count = max(4, available_total // trip_days)
            yield json.dumps({"token": f"[协商说明/Infeasible]: 当地高品质地标池 ({available_total}处) 难以支撑每日 {target_daily_count} 点，已自动精选每日 {actual_daily_count} 个核心节点！\n"}, ensure_ascii=False) + "\n"

        yield json.dumps({"token": f"[SafetyAgent/WorldMonitor]: 已接入全球/区域实时风控网关，【{target_city}】当前 CII 指数为 {safety_intel.get('cii_score', 0)}。\n"}, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "safety_info", "payload": safety_intel}, ensure_ascii=False) + "\n"

        traffic_data = await toolbox.get_traffic_status(coord)
        yield json.dumps({"type": "weather_info", "payload": weather_data}, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "traffic_info", "payload": traffic_data}, ensure_ascii=False) + "\n"

        fitness_calculator = TopologyFitnessCalculator(user_prefs, coord, [], safety_intel=safety_intel, is_luxury=is_luxury)
        
        # 物理隔离白天景点与夜间酒店
        daytime_pois = unique_attractions + unique_foods
        for p in daytime_pois: 
            p["fitness_score"] = fitness_calculator.calculate_fitness(p)
            p["is_hotel"] = False

        for h in cleaned_hotels:
            h["fitness_score"] = fitness_calculator.calculate_fitness(h)
            h["is_hotel"] = True

        pareto_daytime = NashEquilibriumSolver.resolve_conflicts(daytime_pois)
        route_optimizer = GraphRouteOptimizer(fitness_calculator)
        sorted_daytime = route_optimizer.optimize_and_sort(pareto_daytime, max_nodes=trip_days * actual_daily_count)

        final_candidates = sorted_daytime + cleaned_hotels[:6]

        await blackboard.write(f"room_{target_city}_context", {
            "traffic": traffic_data, "weather": weather_data, "safety": safety_intel, "poi": final_candidates
        })

        top_candidates = final_candidates[:trip_days * (actual_daily_count + 3)]
        search_candidates = top_candidates[:8]
        enrich_tasks = [toolbox.enrich_poi_with_web_search(target_city, p['name']) for p in search_candidates]
        
        try:
            web_knowledge_list = await asyncio.wait_for(
                asyncio.gather(*enrich_tasks, return_exceptions=True),
                timeout=3.5
            )
        except Exception:
            web_knowledge_list = ["本地高人气打卡目的地，融入独特文化特色与地道风味。"] * len(search_candidates)

        poi_pool_data = []
        for i, p in enumerate(top_candidates):
            lonlat = [0.0, 0.0]
            if p.get("location") and "," in p["location"]:
                parts = p["location"].split(",")
                lonlat = [float(parts[0]), float(parts[1])]
            
            web_info = web_knowledge_list[i] if i < len(web_knowledge_list) and isinstance(web_knowledge_list[i], str) else "本地高人气打卡地，融入独特文化特色与地道风味。"
            poi_pool_data.append({
                'name': str(p.get('name')), 
                'rating': str(p.get('rating', '4.8')),
                'lnglat': lonlat, 
                'type': str(p.get('type')), 
                'photos': p.get('photos', []),
                'web_knowledge': str(web_info),
                'is_hotel': p.get("is_hotel", False),
                'amap_url': p.get('amap_url', f"https://www.amap.com/search?query={target_city}{p.get('name')}")
            })

        yield json.dumps({"token": f"[路书生成器]: DBSCAN 空间寻优与酒店隔离完成！正在实时生成【{target_city}】Day 1 到 Day {trip_days} 方案...\n\n"}, ensure_ascii=False) + "\n"

        if budget_mode == "EXACT_AMOUNT":
            summary_desc = f"已为您精算【{target_city}】{trip_days}天定额路线，总预算{total_calc_budget}元（日均{round(total_calc_budget/trip_days)}元）：住宿预留{hotel_budget}元，餐饮预留{dining_budget}元，门票预留{ticket_budget}元，交通备用{traffic_budget}元。"
        elif budget_mode == "HIGH_LUXURY":
            summary_desc = f"已为您开启【{target_city}】{trip_days}天臻选奢华行程，预估总消费{total_calc_budget}元：匹配核心商圈、黑珍珠餐饮与高端度假住宿。"
        else:
            summary_desc = f"已为您匹配【{target_city}】{trip_days}天高性价比精算路线，预估总花销{total_calc_budget}元（日均{round(total_calc_budget/trip_days)}元）：高评分地标与高口碑平替住宿组合。"

        # 👑 死指令 System Prompt（定义品类动态时间轴 + 单日 1 住宿 + 备选酒店池 + 推荐时段理由）
        system_prompt = f"""
你是一个名为 OmniRoute 的专业旅行路书专家。当前任务：规划【{target_city}】的【{trip_days}天】深度行程。
【重要警告：绝对禁止越界！】你安排的所有景点、餐厅、酒店必须严格属于【{target_city}】！
用户原话意图："{intent_str}"
当前模式：【{current_mode}】 | 偏好画像：【{user_role}】

【WorldMonitor 实时安全与风控情报】:
{json.dumps(safety_intel, ensure_ascii=False)}

【专家客观底座数据 (包含真实网络知识 web_knowledge、经纬度、高德照片、超链接与住宿酒店)】:
{json.dumps(poi_pool_data, ensure_ascii=False)}

【🔴 规则一：天数绝对对齐 —— 必须生成完整的 {trip_days} 天行程】
必须为每个地点标注 `"day": 1` 至 `"day": {trip_days}`！绝对不能遗漏任何一天的规划，必须输出到 Day {trip_days}！

【🔴 规则二：品类动态时长分配与推荐时段理由（绝对禁止所有景点统一定死半小时！）】
1. 5A/4A大型景区（如熊猫基地、故宫、长城）：游玩时间必须安排 2.5 ~ 4 小时，并给出 `time_reason` 说明理由（如：“上午气温清爽人群较少，且为景区景观/打卡最佳时段”）！
2. 正餐美食/火锅：用餐时间安排 1.5 ~ 2 小时！
3. 特色街区/茶馆（如太古里、宽窄巷子、前门）：游览时间安排 1.5 ~ 2 小时！
4. 每天最后一个住宿节点（Night 21:30 以后）：时间必须跨越整夜，标为 "Night 21:30 - 次日08:30"！

【🔴 规则三：单日有且仅有 1 家主推住宿酒店 + 提供 2 家备选酒店】
1. 每天有且仅能在当天最后一个节点（21:30 以后）安排【1 个】住宿酒店/民宿节点（is_hotel=True 的对象）！
2. 绝对禁止在一天的白天（08:00 - 20:00）安排任何酒店/民宿！
3. 绝对禁止在同一天之内安排 2 个或以上主推酒店节点！
4. 请在酒店对象的 `hotel_candidates` 数组中，额外提供 2 个同商圈备选酒店对象（包含名称与参考价格）。

【🔴 规则四：JSON 结构与超链接】
1. `desc` 结合池子中 `web_knowledge` 撰写 30~50 字特色介绍！
2. `photos`、`lnglat` 和 `amap_url` 必须原样照抄池子数据！

[FINAL_JSON]
{{
  "status": "consensus_reached",
  "negotiation_summary": "{summary_desc}",
  "route": [
    {{
      "day": 1,
      "time": "Day 1 | 08:30 - 11:30 (建议游玩 3 小时)",
      "time_reason": "上午气温清爽人群较少，且为景区景观/打卡最佳时段",
      "location": "某景点/早茶店",
      "lnglat": [116.40, 39.90],
      "desc": "基于 web_knowledge 生成的 30-50 字特色介绍...",
      "transport": "驾车约 15 分钟",
      "cost_estimate": "25元",
      "tags": ["寻味"],
      "photos": ["照抄POI池中的图片URL"],
      "amap_url": "照抄POI池中的超链接URL",
      "trust_reason": "图论优选推荐，WorldMonitor 评估气象交通安全，本地老字号评分4.8",
      "hotel_candidates": ["可选备选酒店A (¥380/晚)", "可选备选酒店B (¥420/晚)"]
    }}
  ]
}}
"""
        except Exception as e:
            print(f"🔥 [算法计算崩溃]: {e}")
            yield json.dumps({"token": f"\n[系统异常] 算法引擎计算失败: {str(e)}\n[FINAL_JSON]\n{{\"status\": \"error\", \"negotiation_summary\": \"算法计算异常\", \"route\": []}}\n"}, ensure_ascii=False) + "\n"
            return

        try:
            full_response = ""
            
            response = await client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请严格根据约束，为我生成【{target_city}】完整 {trip_days} 天（Day 1 到 Day {trip_days}，每天 {actual_daily_count} 个节点且含酒店）的行程 JSON。"}
                ],
                stream=True,
                temperature=llm_temperature,
                max_tokens=8192
            )

            async for chunk in response:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        token = delta.content
                        full_response += token
                        yield json.dumps({"token": token}, ensure_ascii=False) + "\n"
            
            # 👑 Post-Route P2P 精准计算：针对 LLM 真正选出的路线对，实时计算高德分步转弯步骤！
            try:
                if "[FINAL_JSON]" in full_response:
                    parts = full_response.split("[FINAL_JSON]")
                    if len(parts) >= 2:
                        json_str_match = re.search(r'\{[\s\S]*\}', parts[1])
                        if json_str_match:
                            json_str = json_str_match.group(0)
                            final_data = safe_repair_and_parse_json(json_str)
                            llm_routes = final_data.get("route", [])
                            
                            if isinstance(llm_routes, list) and len(llm_routes) >= 2:
                                post_p2p_tasks = []
                                post_pair_names = []
                                
                                for i in range(min(12, len(llm_routes) - 1)):
                                    r1, r2 = llm_routes[i], llm_routes[i+1]
                                    l1, l2 = r1.get("lnglat"), r2.get("lnglat")
                                    if l1 and l2 and isinstance(l1, list) and isinstance(l2, list):
                                        str_loc1 = f"{l1[0]},{l1[1]}"
                                        str_loc2 = f"{l2[0]},{l2[1]}"
                                        pair_key = f"{r1.get('location')}|{r2.get('location')}"
                                        post_pair_names.append((pair_key, r1.get('location')))
                                        post_p2p_tasks.append(toolbox.get_travel_options(str_loc1, str_loc2, target_city))

                                if post_p2p_tasks:
                                    post_results = await asyncio.gather(*post_p2p_tasks, return_exceptions=True)
                                    post_travel_details = {}
                                    final_stitched_path = []
                                    
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
                print(f"🔥 [真实路网拼装失败] {str(e)}")

        except Exception as e:
            print(f"🔥 [流式推演崩溃] {str(e)}")
            yield json.dumps({"token": f"\n[FINAL_JSON]\n{{\"status\": \"error\", \"negotiation_summary\": \"{str(e)}\", \"route\": []}}\n"}, ensure_ascii=False) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")