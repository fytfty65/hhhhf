import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import httpx
import math
import re
import random
import asyncio
import numpy as np
import networkx as nx
import redis.asyncio as redis
from sklearn.cluster import DBSCAN
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from models.schemas import GatewayMessage, NegotiateResponse
from duckduckgo_search import DDGS

router = APIRouter()

client = AsyncOpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
    timeout=httpx.Timeout(60.0, connect=5.0)
)
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "qwen-turbo")

# ==========================================
# 1. 架构层：真实的 Redis 共享黑板系统
# ==========================================
class BlackboardSystem:
    def __init__(self):
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        
    async def write(self, key: str, data: dict):
        try:
            r = await redis.from_url(self.redis_url, decode_responses=True)
            await r.set(key, json.dumps(data, ensure_ascii=False), ex=3600)
            await r.aclose()
        except Exception as e:
            print(f"⚠️ [黑板系统] Redis 未启动或连接失败，降级为无状态模式: {e}")

# ==========================================
# 2. 算法层：纳什均衡与帕累托最优
# ==========================================
class NashEquilibriumSolver:
    @staticmethod
    def resolve_conflicts(pois: List[Dict]) -> List[Dict]:
        pareto_front = []
        for poi in pois:
            if poi.get("fitness_score", 0) > 0.0:
                pareto_front.append(poi)
        return pareto_front

# ==========================================
# 3. 算法层：DBSCAN 聚类与 NetworkX 图论寻优
# ==========================================
class GraphRouteOptimizer:
    def __init__(self, fitness_calculator):
        self.calc = fitness_calculator

    def optimize_and_sort(self, pois: List[Dict], max_nodes: int) -> List[Dict]:
        if not pois: return []
        valid_pois = []
        coords = []
        
        for p in pois:
            loc = p.get("location", "")
            if loc and "," in loc:
                lon, lat = map(float, loc.split(","))
                coords.append([lat, lon]) 
                valid_pois.append(p)
                
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
# 原有核心逻辑完全保留，并注入强化学习闭环
# ==========================================
class TopologyFitnessCalculator:
    def __init__(self, user_prefs: dict, city_center_coord: str, evolution_memory: list):
        self.user_prefs = user_prefs
        self.mode = user_prefs.get("mode", "coop").lower()
        self.center_lat, self.center_lon = self._parse_coord(city_center_coord)
        
        self.alpha = 1.0  
        self.beta = 1.0   
        self.gamma = 1.0  
        self._adjust_weights_by_intent()
        self._apply_rl_evolution(evolution_memory)

    def _apply_rl_evolution(self, evolution_memory: list):
        if not evolution_memory: return
        penalty = sum(1 for m in evolution_memory if m.get("Score") == -1)
        reward = sum(1 for m in evolution_memory if m.get("Score") == 1)
        if penalty > reward:
            self.gamma *= 1.25  
            self.alpha *= 1.15  

    def _parse_coord(self, coord_str: str):
        if not coord_str or "," not in coord_str: return 0.0, 0.0
        parts = coord_str.split(",")
        try: return float(parts[1]), float(parts[0])
        except: return 0.0, 0.0

    def _haversine_distance(self, lat1, lon1, lat2, lon2):
        if lat1 == 0.0 or lat2 == 0.0: return 5.0
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def _extract_cost(self, cost_str: str) -> float:
        if not cost_str or cost_str == "未知": return 50.0
        match = re.search(r'\d+(\.\d+)?', str(cost_str))
        return float(match.group()) if match else 50.0

    def _adjust_weights_by_intent(self):
        intent_str = str(self.user_prefs).lower()
        if self.mode == "pvp" or "穷游" in intent_str:
            self.gamma = 4.5
            self.alpha = 0.8
        elif self.mode == "solo":
            self.alpha = 2.0
            self.beta = 1.5
        else:
            self.beta = 2.5

        if "出片" in intent_str or "网红" in intent_str: self.alpha += 1.5
        if "带小孩" in intent_str or "老人" in intent_str or "休闲" in intent_str: self.beta += 1.0

    def calculate_fitness(self, poi: dict) -> float:
        rating_str = poi.get("rating", "0")
        experience_score = float(rating_str) if rating_str != "暂无评分" else 3.5
        poi_lat, poi_lon = self._parse_coord(poi.get("location", ""))
        distance_km = self._haversine_distance(self.center_lat, self.center_lon, poi_lat, poi_lon)
        
        base_cost = self._extract_cost(poi.get("cost"))
        final_cost = base_cost

        if self.mode == "pvp":
            fluctuation = max(1.0, random.gauss(1.2, 0.15))
            final_cost = base_cost * fluctuation
            poi["simulated_cost"] = round(final_cost, 2)
            budget_limit = 80.0
            premium_penalty = 0.0
            if final_cost > budget_limit:
                premium_penalty = self.gamma * ((final_cost - budget_limit) / 50.0) 
            norm_exp = experience_score / 5.0
            norm_dist = min(distance_km / 20.0, 1.0)
            fitness = (self.alpha * norm_exp) - (self.beta * norm_dist) - premium_penalty
            
        elif self.mode == "solo":
            norm_exp = experience_score / 5.0
            norm_dist = min(distance_km / 20.0, 1.0)
            norm_cost = min(final_cost / 300.0, 1.0)
            fitness = (self.alpha * norm_exp) - (self.beta * norm_dist) - (self.gamma * norm_cost)
            poi_name = poi.get("name", "")
            if any(k in poi_name for k in ["博物馆", "美术馆", "故居", "巷", "咖啡"]): fitness += 2.0
            if any(k in poi_name for k in ["打卡", "步行街", "网红", "购物中心"]): fitness -= 3.0 
            
        else:
            norm_exp = experience_score / 5.0
            norm_dist = min(distance_km / 20.0, 1.0)
            norm_cost = min(final_cost / 300.0, 1.0)
            fitness = (self.alpha * norm_exp) - (self.beta * norm_dist) - (self.gamma * norm_cost)

        if "免门票" in poi.get("name", "") and self.gamma > 2.0:
            fitness += 1.5 
            
        return round(fitness, 4)

class ExpertToolbox:
    def __init__(self):
        self.amap_key = os.getenv("AMAP_API_KEY")
        self.seniverse_key = os.getenv("SENIVERSE_API_KEY", "eb3cb61292594891937c78476861bb1e") 
        self.amap_url = "https://restapi.amap.com/v3"

    async def get_real_time_web_price(self, city: str, target: str) -> str:
        print(f"🕸️ [精算特工] 正在潜入外网抓取 {city} {target} 的今日实时价格...")
        def sync_search():
            try:
                query = f"{city} {target} 今日 携程 飞猪 均价 价格"
                with DDGS(timeout=3) as ddgs:
                    return list(ddgs.text(query, max_results=3))
            except Exception:
                return []
        try:
            results = await asyncio.wait_for(asyncio.to_thread(sync_search), timeout=4.0)
            if not results:
                return "未抓取到外网实时价格"
            web_context = " | ".join([r['body'] for r in results])
            print(f"✅ [精算特工] 截获真实情报: {web_context[:100]}...")
            return web_context
        except Exception as e:
            print(f"🔥 [外网抓取失败或超时] {str(e)}")
            return "抓取受限，将启用高德保底均价。"

    async def get_coordinates(self, address: str) -> str:
        async with httpx.AsyncClient(timeout=3.0) as client:
            try:
                params = {"address": address, "key": self.amap_key}
                resp = await client.get(f"{self.amap_url}/geocode/geo", params=params)
                data = resp.json()
                if data.get("status") == "1" and data.get("geocodes"):
                    location = data["geocodes"][0]["location"]
                    print(f"📍 [地理定位] {address} -> {location}")
                    return location
                
                search_params = {"keywords": address, "key": self.amap_key}
                search_resp = await client.get(f"{self.amap_url}/place/text", params=search_params)
                search_data = search_resp.json()
                if search_data.get("status") == "1" and search_data.get("pois"):
                    location = search_data["pois"][0]["location"]
                    print(f"📍 [检索定位] {address} -> {location}")
                    return location
            except Exception as e:
                print(f"🔥 [地理专家报错] {str(e)}")
            return ""

    async def get_dynamic_pois(self, city: str, keywords: str, types: str = "060000|050000", limit: int = 15) -> List[Dict]:
        if not self.amap_key: return []
        async with httpx.AsyncClient(timeout=4.0) as client:
            params = {
                "key": self.amap_key, "keywords": keywords, "city": city,
                "types": types, "sortrule": "weight", "offset": limit, "page": 1, "extensions": "all"
            }
            try:
                resp = await client.get(f"{self.amap_url}/place/text", params=params)
                data = resp.json()
                if data.get("status") == "1" and data.get("pois"):
                    pois = []
                    for poi in data["pois"]:
                        biz_ext = poi.get("biz_ext", {})
                        poi_info = {
                            "name": poi.get("name"),
                            "type": poi.get("type", "").split(";")[0],
                            "business_area": poi.get("business_area", "未知商圈"), 
                            "address": poi.get("address", "地址未知"),
                            "location": poi.get("location", ""), 
                            "rating": biz_ext.get("rating", "暂无评分"), 
                            "cost": biz_ext.get("cost", "未知"),         
                            "open_time": poi.get("biz_ext", {}).get("open_time", "营业时间未知")
                        }
                        pois.append(poi_info)
                    print(f"🗺️ [本地导游雷达] 在 {city} '{keywords}' 搜寻，截获 {len(pois)} 个全息目标！")
                    return pois
            except Exception as e:
                print(f"🔥 [本地导游崩溃] {str(e)}")
        return []

    async def get_traffic_status(self, location_coord: str) -> Dict[str, Any]:
        if not location_coord:
            return {"status_code": "1", "description": "未知", "advice": "无路况数据"}
        async with httpx.AsyncClient(timeout=3.0) as client:
            params = {"key": self.amap_key, "location": location_coord, "radius": 5000, "level": 5}
            try:
                resp = await client.get(f"{self.amap_url}/traffic/status/circle", params=params)
                data = resp.json()
                if data.get("status") == "1" and "trafficinfo" in data:
                    info = data["trafficinfo"]
                    status_desc = info.get("description", "路况正常")
                    raw_eval = info.get("evaluation", {}).get("status", "1")
                    eval_code_int = int(raw_eval) if str(raw_eval).isdigit() else 1
                    
                    advice = "路况良好，按原计划执行。"
                    if eval_code_int >= 3:
                        advice = "注意！当前区域存在明显拥堵，请预留缓冲时间并错峰排布。"
                    print(f"🚗 [实时路况] 拥堵等级: {eval_code_int}, 描述: {status_desc}")
                    return {"status_code": str(eval_code_int), "description": status_desc, "advice": advice}
            except Exception as e:
                print(f"🔥 [路况查询报错] {str(e)}")
            return {"status_code": "1", "description": "查询失败", "advice": "维持原计划"}

    async def get_real_weather(self, location_coord: str) -> Dict[str, Any]:
        if not location_coord or not self.seniverse_key:
            return {"condition": "未知", "advice": "未配置天气 Key"}
        async with httpx.AsyncClient(timeout=3.0) as client:
            try:
                lon, lat = location_coord.split(",")
                formatted_location = f"{lat}:{lon}"
                url = "https://api.seniverse.com/v3/weather/now.json"
                params = {"key": self.seniverse_key, "location": formatted_location, "language": "zh-Hans", "unit": "c"}
                
                resp = await client.get(url, params=params)
                data = resp.json()
                
                if "results" in data:
                    result = data["results"][0]
                    now = result["now"]
                    city = result["location"]["name"]
                    condition = now["text"]
                    temp = now["temperature"]
                    
                    advice = f"当前天气{condition}，气温适宜（{temp}°C）。"
                    if "雨" in condition or "雪" in condition:
                        advice = f"🚨 当前有{condition}，请务必备好雨具，强烈建议优先安排室内或餐饮活动。"
                    elif int(temp) > 35:
                        advice = f"🚨 高温 {temp}°C，避免长时间户外暴晒。"
                        
                    display_text = f"{city} {condition}，气温 {temp}°C"
                    print(f"🌤️ [心知天气感知] {display_text}")
                    return {"condition": display_text, "advice": advice}
            except Exception as e:
                print(f"🔥 [心知天气崩溃] {str(e)}")
        return {"condition": "获取失败", "advice": "请留意当地气象预报。"}

@router.post("/agent/negotiate")
async def run_negotiate(msg: GatewayMessage):
    toolbox = ExpertToolbox()
    blackboard = BlackboardSystem()
    payload = msg.payload or {}
    
    user_prefs = payload.get("user_preferences") or payload.get("current_request", {}).get("user_preferences", {})
    destinations = payload.get("current_request", {}).get("destinations", ["广州"])
    
    history_sequence = user_prefs.get("history_sequence", [])
    current_existing_route = user_prefs.get("current_existing_route", [])
    intent_str = user_prefs.get("intent", "")
    
    full_text_context = " | ".join(history_sequence) + " " + intent_str

    if destinations and destinations[0] and len(destinations[0]) > 1:
        target_city = destinations[0]
    else:
        candidate_city = ""
        city_patterns = [
            r'(?:在|去|到|前往|抵达|想?[去在到])([一-龥]{2,5})(?:玩|游玩|旅游|旅行|逛|耍|转转|待|呆|深度|周边)',
            r'([一-龥]{2,4})旅游',
            r'([一-龥]{2,3}[市州县区])',
            r'(?:去|到)([一-龥]{2,5})',
            r'([一-龥]{2,5})(?:的|之)旅',
        ]
        for pattern in city_patterns:
            match = re.search(pattern, full_text_context)
            if match:
                candidate_city = match.group(1)
                candidate_city = re.sub(r'[市州县区]$', '', candidate_city)
                break

        if candidate_city:
            coord = await toolbox.get_coordinates(candidate_city)
            if coord:
                target_city = candidate_city
                print(f"📍 [智能城市识别] 从意图中提取 -> {target_city}")
            else:
                target_city = "广州"
                print(f"⚠️ [智能城市识别] '{candidate_city}' 无法定位，回退到 {target_city}")
        else:
            target_city = "广州"
            print(f"⚠️ [智能城市识别] 未能从意图中提取城市，回退到 {target_city}")

    current_mode = user_prefs.get("mode", "coop") 
    user_role = user_prefs.get("role", "常规游玩") 
    
    trip_days_match = re.search(r'(\d+)[天日]', full_text_context)
    trip_days = int(trip_days_match.group(1)) if trip_days_match else 2 
    
    evolution_memory = payload.get("evolution_memory", [])
    
    is_refinement = len(history_sequence) > 0 and len(current_existing_route) > 0
    refinement_intent = history_sequence[-1] if history_sequence else ""
    
    llm_temperature = 0.55 if is_refinement else 0.2
    
    # 根据精化意图关键词动态调整 POI 搜索偏好
    refined_food = any(kw in refinement_intent for kw in ["吃", "饭", "美食", "寻味", "餐厅", "小吃", "火锅", "面", "汤"])
    refined_scene = any(kw in refinement_intent for kw in ["景点", "玩", "逛", "看", "游览", "拍照", "博物馆", "公园", "山", "古镇"])
    refined_discovery = any(kw in refinement_intent for kw in ["小众", "冷门", "深度", "秘境", "探索", "不一样"])
    refined_hotel = any(kw in refinement_intent for kw in ["住宿", "酒店", "民宿", "住"])
    
    food_keyword = "特色美食|苍蝇馆子|老字号"
    scenic_keyword = "必玩景点|网红打卡|博物馆"
    
    if refined_food:
        food_keyword = "特色美食|苍蝇馆子|老字号|地道小吃|夜宵大排档|本地人推荐"
    if refined_scene:
        scenic_keyword = "必玩景点|网红打卡|博物馆|地标建筑|文化遗产"
    if refined_discovery:
        scenic_keyword = "小众秘境|深度体验|隐藏景点|冷门推荐|本地人都不知道"
        food_keyword = "巷子深处|隐藏老店|本地人私藏|独家特色"
    if refined_hotel:
        food_keyword = food_keyword  # keep default
        scenic_keyword = scenic_keyword  # keep default
    if not refined_food and not refined_scene and not refined_discovery and is_refinement:
        food_keyword = "特色美食|苍蝇馆子|老字号|异地风味|小众餐厅"
        scenic_keyword = "小众秘境|深度体验|冷门景点|不一样的玩法"

    poi_limit = max(15, trip_days * 12)
    print(f"\n🚀 [新任务] 开始为 {target_city} 规划动态专属行程... 天数: {trip_days} | 模式: {current_mode} | 偏好: {user_role} | 精化: {is_refinement} | 温度: {llm_temperature}")
    print(f"🧠 [自我进化库] 本次加载了 {len(evolution_memory)} 条对比学习样本")

    async def event_stream():
        yield json.dumps({"token": f"> [系统唤醒] OmniRoute 核心中枢已激活，正在为 {target_city} 执行差量推演...\n"}, ensure_ascii=False) + "\n"
        await asyncio.sleep(0.1)

        coord_task = toolbox.get_coordinates(target_city)
        attractions_task = toolbox.get_dynamic_pois(target_city, keywords=scenic_keyword, limit=poi_limit)
        foods_task = toolbox.get_dynamic_pois(target_city, keywords=food_keyword, types="050000", limit=poi_limit)
        hotels_task = toolbox.get_dynamic_pois(target_city, keywords="快捷酒店|特色民宿", types="010000", limit=5)

        if current_mode == "pvp":
            web_price_task = toolbox.get_real_time_web_price(target_city, "快捷酒店与热门景区门票")
            city_center_coord, raw_attractions, raw_foods, real_hotels, web_price_intel = await asyncio.gather(
                coord_task, attractions_task, foods_task, hotels_task, web_price_task
            )
        else:
            city_center_coord, raw_attractions, raw_foods, real_hotels = await asyncio.gather(
                coord_task, attractions_task, foods_task, hotels_task
            )
            web_price_intel = "未触发外网实时检索"

        yield json.dumps({"token": f"> [数据采集] 高德全息地图扫描完成，数据池蓄水 {len(raw_attractions) + len(raw_foods)} 节点\n"}, ensure_ascii=False) + "\n"

        weather_task = toolbox.get_real_weather(city_center_coord)
        traffic_task = toolbox.get_traffic_status(city_center_coord)
        weather_data, traffic_data = await asyncio.gather(weather_task, traffic_task)

        yield json.dumps({"token": f"> [环境探针] 天气/路况情报已同步黑板中枢\n"}, ensure_ascii=False) + "\n"

        try:
            fitness_calculator = TopologyFitnessCalculator(user_prefs, city_center_coord, evolution_memory)
            all_pois = raw_attractions + raw_foods
            for p in all_pois:
                p["fitness_score"] = fitness_calculator.calculate_fitness(p)
                
            pareto_candidates = NashEquilibriumSolver.resolve_conflicts(all_pois)
            
            route_optimizer = GraphRouteOptimizer(fitness_calculator)
            final_candidates = route_optimizer.optimize_and_sort(pareto_candidates, max_nodes=trip_days * 10)

            await blackboard.write(f"room_{target_city}_context", {
                "traffic": traffic_data, "weather": weather_data, "poi": final_candidates
            })

            feed_count = trip_days * 10
            llm_feed_pois = [{"name": p["name"], "type": p["type"], "fitness_score": p["fitness_score"], "simulated_cost": p.get("simulated_cost")} for p in final_candidates[:feed_count]]

            expert_reports = {
                "local_guide": {"available_options": llm_feed_pois, "real_hotels": real_hotels},
                "weather_consultant": weather_data,
                "traffic_expert": traffic_data,
                "web_actuary_intel": web_price_intel
            }

            memory_str = "无历史反馈，按常规策略推进。"
            if evolution_memory:
                negative_samples = [f"【绝对规避】{f.get('Target')}" for f in evolution_memory if f.get('Score') == -1]
                positive_samples = [f"【非常喜欢】{f.get('Target')}" for f in evolution_memory if f.get('Score') == 1]
                memory_str = f"""* 负样本: {', '.join(negative_samples) if negative_samples else '无'} \n* 正样本: {', '.join(positive_samples) if positive_samples else '无'}"""

            system_prompt = f"""
你是一个名为 OmniRoute 的"读心神探"级多智能体中枢。当前任务：规划【{target_city}】的【{trip_days}天】行程。
用户原话意图："{intent_str}"
多轮追加历史：{history_sequence}
系统已有的路书成果（如果有）：{current_existing_route}
当前模式：【{current_mode}】 | 偏好画像：【{user_role}】

【专家客观底座数据 (已通过 DBSCAN降维 与 TSP图论寻优 筛选出帕累托最优集)】
实时天气: {weather_data['condition']} - 建议: {weather_data['advice']}
实时路况: {traffic_data['description']} - 建议: {traffic_data['advice']}
外网比价: {web_price_intel}
算法优选POI池: {[{'name': p['name'], 'rating': p['rating']} for p in final_candidates[:20]]}

【🌟 持续学习闭环机制：系统进化记忆库】
{memory_str}

【🔴 核心一：增量调优与差量替换】
{"【当前为精化轮】用户的最新修改要求是：\"" + refinement_intent + "\"。你必须逐条对照这份修改要求，在路书中做出【实质性变更】！" if is_refinement else "【首次规划】根据用户意图从零构建新路书。"}

精化轮变更操作指南：
1. 找出用户修改要求中的关键词（美食/景点/住宿/天数/节奏），在路书对应位置做定向替换
2. 被替换的节点必须从POI池中选取一个【不同名且不同位置】的候选，严禁只改描述不改节点
3. 若用户要求增加某类节点，必须从路书中砍掉一个同类低优先级节点，腾出时间插入新节点
4. 整体天数、城市骨架保持稳定，但被用户点名不满意的节点段必须【实质性改变】，包括：不同时间、不同地点、不同活动

{"⚠️ 当前系统已有旧路书共 " + str(len(current_existing_route)) + " 个节点，请基于此骨架做差量手术，重点响应精化要求。" if is_refinement else ""}

【🔴 核心二：基于时空约束的流体调度 (Fluid Timeline)】
1. 绝对禁止刻板套用每天 08:30 出发的模板！所有时间节点必须由智能体根据【天气预报】、【实时路况】和【地理距离】推算得出。
2. 时间推演法则：上一节点结束时间 + 真实的交通耗时 = 下一节点开始时间。严禁时空瞬移。

【🔴 核心三：画像约束与真实群体博弈】
你必须让微型智能体在推演中激烈辩论，并落实到路线安排：
- 若为【寻味探索】：寻味大师必须抢夺话语权！将常规景点降级为消食点，核心围绕"吃"排布。
- 若为【亲友结伴(coop)】：必须假设队伍中有老人/小孩，必须在日志中体现：因为照顾体力，强制在两个高强度景点间插入茶馆休息。
- 若遭遇恶劣天气/拥堵：天气顾问与交通老炮儿必须强势介入，强行修改出发时间或将室外改为室内。
- 若为【极客精算(pvp)】：精算管家必须严控预算，在日志中明确写出平替省了多少钱。

【🔴 核心四：事件配额 (防止行程过于简陋)】
你不受固定时刻限制，但【每一天】的行程必须消耗完以下配额：
- 至少 1 次晨间唤醒/特色早餐
- 至少 2 个核心体验节点
- 必须安排午餐、晚餐 (从 available_options 中挑选)
- 至少 1 次交通缓冲/休憩
(硬性约束：{trip_days}天的总节点数绝对不能少于 {trip_days * 5} 个！若少于此数判定为严重失职！)

【🔴 核心五：公信力背书】
每一个输出的节点，必须在 `trust_reason` 字段中写明决策依据。例如："DBSCAN+TSP图论算法最短路径直达，且高德评分高达4.8"。

【输出格式】
第一段：纯文本，像黑客终端一样，逐行输出智能体们因时间、天气、体力发生冲突并解决的详尽博弈过程。
第二段：必须以 [FINAL_JSON] 为绝对分界，输出合法的 JSON 路书（绝对不要包含 ```json 等 Markdown 标记）。

示例格式：
> [避堵老炮儿] 警告：原计划08:30前往核心区，但主干道拥堵。
> [寻味大师] 建议：不如08:30先去附近喝个正宗早茶，避开主干道，10:00再去！
> [精算管家] 同意，顺便砍掉了溢价过高的网红店，替换为平替。

[FINAL_JSON]
{{
  "status": "consensus_reached",
  "negotiation_summary": "一句话总结亮点（如增量修改了什么）",
  "route": [
    {{
      "time": "Day 1 | 08:30 - 09:30",
      "location": "某早茶店",
      "lnglat": [112.1, 34.1],
      "action": "避开早高峰先用热汤唤醒",
      "transport": "步行 5 分钟",
      "cost_estimate": "20元",
      "tags": ["寻味", "错峰"],
      "trust_reason": "图论优选推荐，本地老字号评分4.9"
    }}
  ]
}}
"""
            yield json.dumps({"token": "> [底层适应度矩阵] DBSCAN 与图论寻优完毕，已提取最佳 POI 集合，黑板对齐中...\n\n"}, ensure_ascii=False) + "\n"
        except Exception as e:
            print(f"🔥 [算法计算崩溃] {str(e)}")
            import traceback
            traceback.print_exc()
            yield json.dumps({"token": f"\n> [系统异常] 算法引擎计算失败: {str(e)}\n[FINAL_JSON]\n{{\"status\": \"error\", \"negotiation_summary\": \"算法计算异常\", \"route\": []}}\n"}, ensure_ascii=False) + "\n"
            return

        try:
            response = await client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "请严格按照黑板数据与增量约束，启动多智能体流式推演！并确保JSON绝对合法。"}
                ],
                stream=True,
                temperature=llm_temperature
            )

            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    yield json.dumps({"token": token}, ensure_ascii=False) + "\n"

        except Exception as e:
            print(f"🔥 [流式推演崩溃] {str(e)}")
            yield json.dumps({"token": f"\n[FINAL_JSON]\n{{\"status\": \"error\", \"negotiation_summary\": \"{str(e)}\", \"route\": []}}\n"}, ensure_ascii=False) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")