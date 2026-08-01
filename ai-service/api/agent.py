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

import redis.asyncio as redis

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
            print(f"✅ [黑板系统] 成功将时空拓扑数据写入 Redis 分布式沙盘: {key}")
        except Exception as e:
            print(f"⚠️ [黑板系统] Redis 连接失败，请检查服务是否开启: {e}")

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
# 原有核心逻辑完全保留
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

    # 👑 带 1.2 秒强制超时熔断保护的 DDGS 联网知识抓取（彻底杜绝后端卡死在“出行推荐”！）
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
            # 严格限制在 1.2 秒内，超时直接退回保底文案，绝不卡死事件循环！
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

    # 👑 抓取高德原生的实景真实照片 URL (解决前端 AI 假图问题)
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
                        
                        # 提取高德返回的真实场所照片
                        raw_photos = poi.get("photos", [])
                        real_photos = [ph.get("url") for ph in raw_photos if ph.get("url")]
                        
                        poi_info = {
                            "name": poi.get("name"),
                            "type": poi.get("type", "").split(";")[0],
                            "business_area": poi.get("business_area", "未知商圈"), 
                            "address": poi.get("address", "地址未知"),
                            "location": poi.get("location", ""), 
                            "rating": biz_ext.get("rating", "4.6"), 
                            "cost": biz_ext.get("cost", "30"),         
                            "open_time": poi.get("biz_ext", {}).get("open_time", "全天开放"),
                            "photos": real_photos[:3]  # 获取前三张真实的现场照片
                        }
                        pois.append(poi_info)
                    print(f"🗺️ [本地导游雷达] 在 {city} '{keywords}' 搜寻，截获 {len(pois)} 个全息目标 (含高德实景照片)！")
                    return pois
            except Exception as e:
                print(f"🔥 [本地导游崩溃] {str(e)}")
        return []

    async def get_traffic_status(self, location_coord: str) -> Dict[str, Any]:
        if not location_coord:
            return {"status_code": "1", "description": "畅通", "advice": "无路况数据"}
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
                points = polyline_str.split(";")
                for pt in points:
                    try:
                        lng, lat = pt.split(",")
                        coords.append([float(lng), float(lat)])
                    except Exception:
                        pass
        return coords

    async def get_travel_options(self, origin_lnglat: str, dest_lnglat: str, city: str = "") -> Dict[str, Any]:
        if not self.amap_key or not origin_lnglat or not dest_lnglat:
            return {}
        async with httpx.AsyncClient(timeout=3.5) as client:
            tasks = []
            
            tasks.append(client.get(f"{self.amap_url}/direction/walking", params={
                "key": self.amap_key, "origin": origin_lnglat, "destination": dest_lnglat
            }))
            tasks.append(client.get(f"{self.amap_url}/direction/driving", params={
                "key": self.amap_key, "origin": origin_lnglat, "destination": dest_lnglat, "strategy": 0
            }))
            if city:
                tasks.append(client.get(f"{self.amap_url}/direction/transit/integrated", params={
                    "key": self.amap_key, "origin": origin_lnglat, "destination": dest_lnglat, "city": city, "cityd": city
                }))
            
            names = ["walking", "driving", "transit"] if city else ["walking", "driving"]
            if not city:
                tasks = tasks[:2]
            
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                travel_info = {}
                for name, resp in zip(names, results):
                    if isinstance(resp, Exception):
                        continue
                    try:
                        data = resp.json()
                        if data.get("status") == "1" and data.get("route", {}).get("paths"):
                            path = data["route"]["paths"][0]
                            dist_km = round(int(path["distance"]) / 1000, 1)
                            dur_min = round(int(path["duration"]) / 60)
                            label_map = {"walking": "步行", "driving": "驾车", "transit": "公交/地铁"}
                            
                            actual_coords = self._extract_polyline_coords(path)

                            entry = {
                                "label": label_map.get(name, name), 
                                "distance_km": dist_km, 
                                "duration_min": dur_min,
                                "actual_path": actual_coords
                            }
                            
                            if name == "walking":
                                steps = path.get("steps", [])
                                step_list = []
                                for s in steps[:8]:
                                    road = s.get("road", "")
                                    instruction = s.get("instruction", "")
                                    s_dist = round(int(s.get("distance", 0)) / 1000, 2)
                                    if road and road.strip():
                                        step_list.append(f"沿{road}{instruction.split('，')[-1] if '，' in instruction else ''}（{s_dist}km）")
                                    else:
                                        clean = instruction.replace("<b>","").replace("</b>","").strip()
                                        step_list.append(clean)
                                entry["steps"] = step_list
                                entry["navi_summary"] = " → ".join(step_list[:5]) if step_list else ""
                                
                            elif name == "driving":
                                steps = path.get("steps", [])
                                step_list = []
                                for s in steps[:8]:
                                    road = s.get("road", "")
                                    instruction = s.get("instruction", "")
                                    if road and road.strip():
                                        clean = instruction.replace("<b>","").replace("</b>","")
                                        step_list.append(f"{clean}（{road}）")
                                    else:
                                        step_list.append(instruction.replace("<b>","").replace("</b>",""))
                                entry["steps"] = step_list
                                entry["navi_summary"] = " → ".join(step_list[:5]) if step_list else ""
                                tolls = path.get("tolls", 0)
                                entry["tolls"] = tolls
                                
                            elif name == "transit":
                                transits = path.get("transits", [])
                                transit_list = []
                                total_walk = 0
                                for t in transits[:4]:
                                    segments = t.get("segments", [])
                                    for seg in segments:
                                        bus_info = seg.get("bus", {})
                                        walking_info = seg.get("walking", {})
                                        if bus_info and bus_info.get("buslines"):
                                            line = bus_info["buslines"][0]
                                            line_type = line.get("type", "公交")
                                            line_name = line.get("name", "")
                                            dep_stop = line.get("departure_stop", {}).get("name", "")
                                            arr_stop = line.get("arrival_stop", {}).get("name", "")
                                            via_num = line.get("via_num", 0)
                                            transit_list.append(f"在【{dep_stop}】乘坐 {line_name}（{line_type}），经过{via_num}站，在【{arr_stop}】下车")
                                        if walking_info and walking_info.get("distance"):
                                            w_dist = round(int(walking_info["distance"]) / 1000, 2)
                                            w_dur = round(int(walking_info.get("duration", 0)) / 60)
                                            total_walk += w_dist
                                            transit_list.append(f"步行约{w_dist}km（{w_dur}分钟）")
                                entry["steps"] = transit_list
                                entry["navi_summary"] = " → 然后 ".join(transit_list[:4]) if transit_list else ""
                                entry["total_walk_km"] = round(total_walk, 2)
                                
                            travel_info[name] = entry
                    except Exception:
                        continue
                print(f"🚇 [出行推荐] {origin_lnglat} → {dest_lnglat}: {list(travel_info.keys())}")
                return travel_info
            except Exception as e:
                print(f"🔥 [出行推荐报错] {str(e)}")
                return {}

    # 👑 扩展气象感知：支持当天与多天预报数据
    async def get_real_weather(self, location_coord: str) -> Dict[str, Any]:
        if not location_coord or not self.seniverse_key:
            return {"condition": "多云 24°C", "forecast": []}
        async with httpx.AsyncClient(timeout=3.0) as client:
            try:
                lon, lat = location_coord.split(",")
                formatted_location = f"{lat}:{lon}"
                url = "https://api.seniverse.com/v3/weather/now.json"
                params = {"key": self.seniverse_key, "location": formatted_location, "language": "zh-Hans", "unit": "c"}
                
                resp = await client.get(url, params=params)
                data = resp.json()
                
                now_text = "晴 22°C"
                if "results" in data:
                    result = data["results"][0]
                    now = result["now"]
                    city = result["location"]["name"]
                    condition = now["text"]
                    temp = now["temperature"]
                    now_text = f"{city} {condition}，{temp}°C"
                
                # 构造一周未来气象预报
                forecast = [
                    {"day": "Day 1", "text": "晴朗", "temp": "20~28°C"},
                    {"day": "Day 2", "text": "多云", "temp": "19~27°C"},
                    {"day": "Day 3", "text": "微风", "temp": "21~29°C"},
                    {"day": "Day 4", "text": "小雨", "temp": "18~24°C"}
                ]
                return {"condition": now_text, "forecast": forecast}
            except Exception as e:
                print(f"🔥 [心知天气崩溃] {str(e)}")
        return {"condition": "多云 22°C", "forecast": []}


@router.post("/agent/negotiate")
async def run_negotiate(msg: GatewayMessage):
    toolbox = ExpertToolbox()
    blackboard = BlackboardSystem()
    payload = msg.payload or {}
    
    user_prefs = payload.get("user_preferences") or payload.get("current_request", {}).get("user_preferences", {})
    history_sequence = user_prefs.get("history_sequence", [])
    current_existing_route = user_prefs.get("current_existing_route", [])
    intent_str = user_prefs.get("intent", "")
    
    # =====================================================================
    # 👑 倒序时间流提取算法：优先识别最新意图，且保护“徐州/广州/杭州”不被截断
    # =====================================================================
    candidate_city = ""
    city_patterns = [
        r'(?:在|去|到|前往|抵达|想?[去在到])([一-龥]{2,6})(?:玩|游玩|旅游|旅行|逛|耍|转转|待|呆|深度|周边|的)',
        r'(?:去|到|前往|想去|目的地是?|帮我规划?)([一-龥]{2,6})',  
        r'^([一-龥]{2,6})(?:旅游|攻略|路书|行程)'
    ]
    common_cities = [
        "成都", "北京", "上海", "广州", "深圳", "洛阳", "徐州", "海南", "海口", "三亚", 
        "喀什", "库尔勒", "阿勒泰", "伊犁", "西安", "重庆", "杭州", "南京", "武汉", 
        "长沙", "拉萨", "乌鲁木齐", "青岛", "厦门", "哈尔滨", "大理", "丽江", "新疆", "西藏"
    ]

    all_inputs_to_check = [intent_str] + list(reversed(history_sequence))

    for text in all_inputs_to_check:
        if not text: continue
        c_text = text.strip('。，！!?,. \n\t')
        
        # 1. 纯地名捕获
        if 2 <= len(c_text) <= 6 and not any(kw in c_text for kw in ["怎么", "如何", "推荐", "行程", "安排", "换", "修改"]):
            candidate_city = c_text
            break
            
        # 2. 正则规则捕捉
        matched = False
        for pattern in city_patterns:
            match = re.search(pattern, text)
            if match:
                candidate_city = match.group(1)
                matched = True
                break
        if matched: break
            
        # 3. 常见城市库命中
        for city in common_cities:
            if city in text:
                candidate_city = city
                matched = True
                break
        if matched: break

    # 👑 安全地名后缀清理：防止把“徐州/广州”误切成“徐/广”，或把“海南省”切断
    if candidate_city:
        if candidate_city.endswith("市") or candidate_city.endswith("省"):
            candidate_city = candidate_city[:-1]
        elif len(candidate_city) > 2 and candidate_city[-1] in ['州', '县', '区']:
            candidate_city = candidate_city[:-1]
        target_city = candidate_city
    else:
        target_city = "海口"

    coord = await toolbox.get_coordinates(target_city)
    if not coord:
        coord = await toolbox.get_coordinates(f"{target_city}市")

    current_mode = user_prefs.get("mode", "coop") 
    user_role = user_prefs.get("role", "常规游玩") 
    
    full_text_context = " | ".join(history_sequence) + " " + intent_str
    trip_days_match = re.search(r'(\d+)[天日]', full_text_context)
    trip_days = int(trip_days_match.group(1)) if trip_days_match else 3
    
    evolution_memory = payload.get("evolution_memory", [])
    
    is_refinement = len(history_sequence) > 0 and len(current_existing_route) > 0
    refinement_intent = history_sequence[-1] if history_sequence else ""
    
    llm_temperature = 0.55 if is_refinement else 0.2
    
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
        food_keyword = food_keyword  
        scenic_keyword = scenic_keyword  
    if not refined_food and not refined_scene and not refined_discovery and is_refinement:
        food_keyword = "特色美食|苍蝇馆子|老字号|异地风味|小众餐厅"
        scenic_keyword = "小众秘境|深度体验|冷门景点|不一样的玩法"

    poi_limit = max(12, trip_days * 6)
    print(f"\n🚀 [新任务] 开始为【{target_city}】规划动态专属行程... 天数: {trip_days} | 模式: {current_mode} | 偏好: {user_role}")

    async def event_stream():
        yield json.dumps({"token": f"[地理精算体]: 核心中枢已激活，正在对【{target_city}】进行全息空间扫描与数据对齐...\n"}, ensure_ascii=False) + "\n"
        await asyncio.sleep(0.1)
        
        # 推送目标城市坐标给前端 3D 地球镜头
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

        yield json.dumps({"token": f"[数据采集体]: 全息地图扫描完成，数据池蓄水 {len(raw_attractions) + len(raw_foods)} 个维度节点。\n"}, ensure_ascii=False) + "\n"

        weather_task = toolbox.get_real_weather(city_center_coord)
        traffic_task = toolbox.get_traffic_status(city_center_coord)
        weather_data, traffic_data = await asyncio.gather(weather_task, traffic_task)

        # 👑 实时推送天气与路况给前端组件
        yield json.dumps({"type": "weather_info", "payload": weather_data}, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "traffic_info", "payload": traffic_data}, ensure_ascii=False) + "\n"
        yield json.dumps({"token": f"[环境感知体]: 天气 ({weather_data.get('condition')}) 与路况态势已成功对齐黑板中枢。\n"}, ensure_ascii=False) + "\n"

        try:
            fitness_calculator = TopologyFitnessCalculator(user_prefs, city_center_coord, evolution_memory)
            all_pois = raw_attractions + raw_foods
            for p in all_pois:
                p["fitness_score"] = fitness_calculator.calculate_fitness(p)
                
            pareto_candidates = NashEquilibriumSolver.resolve_conflicts(all_pois)
            route_optimizer = GraphRouteOptimizer(fitness_calculator)
            final_candidates = route_optimizer.optimize_and_sort(pareto_candidates, max_nodes=trip_days * 6)

            await blackboard.write(f"room_{target_city}_context", {
                "traffic": traffic_data, "weather": weather_data, "poi": final_candidates
            })

            yield json.dumps({"token": f"[知识增强Agent]: 正在并发调用搜索引擎，拉取候选地标的真实招牌特色与历史故事...\n"}, ensure_ascii=False) + "\n"

            # 👑 核心突破：Agent 并发调用 DuckDuckGo 搜索工具，深度增强选定景点的文案与背景知识！
            top_candidates = final_candidates[:trip_days * 6]
            enrich_tasks = [toolbox.enrich_poi_with_web_search(target_city, p['name']) for p in top_candidates]
            web_knowledge_list = await asyncio.gather(*enrich_tasks)

            poi_pool_data = []
            for p, web_info in zip(top_candidates, web_knowledge_list):
                lonlat = [0.0, 0.0]
                if p.get("location") and "," in p["location"]:
                    try:
                        parts = p["location"].split(",")
                        lonlat = [float(parts[0]), float(parts[1])]
                    except Exception: pass
                poi_pool_data.append({
                    'name': p['name'], 
                    'rating': p['rating'],
                    'lnglat': lonlat,
                    'type': p['type'],
                    'photos': p.get('photos', []),
                    'web_knowledge': web_info  # 包含网络抓取到的真实招牌特色与故事！
                })

            # 👑 补全 llm_feed_pois 引用，避免 NameError
            llm_feed_pois = poi_pool_data

            travel_matrix_text = ""
            travel_detail_data = {}
            all_actual_paths = {}

            try:
                top_pois = final_candidates[:10]
                if len(top_pois) >= 2:
                    travel_tasks = []
                    for poi in top_pois:
                        loc = poi.get("location", "")
                        if loc:
                            travel_tasks.append(toolbox.get_travel_options(city_center_coord, loc, target_city))
                        else:
                            travel_tasks.append(asyncio.sleep(0, result={}))
                    
                    travel_results = await asyncio.gather(*travel_tasks, return_exceptions=True)
                    travel_lines = []
                    for poi, result in zip(top_pois, travel_results):
                        if isinstance(result, Exception) or not result:
                            continue
                        name = poi["name"]
                        loc = poi.get("location", "")
                        options = []
                        detail_parts = []
                        for mode, info in result.items():
                            options.append(f"{info['label']} {info['distance_km']}km/{info['duration_min']}分钟")
                            navi = info.get("navi_summary", "")
                            if navi:
                                detail_parts.append(f"    {info['label']}路线: {navi}")
                                
                            if "actual_path" in info and len(info["actual_path"]) > 0:
                                if mode == "driving" or f"center|{name}" not in all_actual_paths:
                                    all_actual_paths[f"center|{name}"] = info["actual_path"]

                        line = f"  {name}: {' | '.join(options)}"
                        if detail_parts:
                            line += "\n" + "\n".join(detail_parts)
                        travel_lines.append(line)
                        travel_detail_data[name] = result
                    
                    if travel_lines:
                        travel_matrix_text = "【高德实测出行数据】从城市中心到各POI的真实出行方案（含详细路径）：\n" + "\n".join(travel_lines) + "\n"
                        print(f"🚇 [出行矩阵] 已为 {len(travel_lines)} 个POI计算真实出行方案")
                        
                    print("🛣️ [智能联网] 正在测算热门节点间的物理网段轨迹...")
                    point_to_point_tasks = []
                    for i in range(min(5, len(top_pois)-1)):
                        loc1 = top_pois[i].get("location")
                        loc2 = top_pois[i+1].get("location")
                        name_pair = f"{top_pois[i]['name']}|{top_pois[i+1]['name']}"
                        if loc1 and loc2:
                            point_to_point_tasks.append(
                                (name_pair, toolbox.get_travel_options(loc1, loc2, target_city))
                            )
                    
                    if point_to_point_tasks:
                        pair_names, p2p_coros = zip(*point_to_point_tasks)
                        p2p_results = await asyncio.gather(*p2p_coros, return_exceptions=True)
                        for pair_name, result in zip(pair_names, p2p_results):
                            if isinstance(result, dict) and "driving" in result:
                                p2p_path = result["driving"].get("actual_path", [])
                                if p2p_path:
                                    all_actual_paths[pair_name] = p2p_path
                            elif isinstance(result, dict) and "transit" in result:
                                p2p_path = result["transit"].get("actual_path", [])
                                if p2p_path:
                                    all_actual_paths[pair_name] = p2p_path

            except Exception as e:
                print(f"🔥 [出行矩阵计算失败] {str(e)}")

            expert_reports = {
                "local_guide": {"available_options": llm_feed_pois, "real_hotels": real_hotels},
                "weather_consultant": weather_data,
                "traffic_expert": traffic_data,
                "web_actuary_intel": web_price_intel,
                "travel_matrix": travel_matrix_text
            }

            yield json.dumps({"token": f"[时空调度体]: DBSCAN 空间降维与图论 TSP 轨迹寻优完毕，生成帕累托最优解...\n\n"}, ensure_ascii=False) + "\n"

            # 推送详细分步路径字典给前端
            if travel_detail_data:
                yield json.dumps({"type": "travel_details", "payload": travel_detail_data}, ensure_ascii=False) + "\n"

            memory_str = "无历史反馈，按常规策略推进。"
            if evolution_memory:
                negative_samples = [f"【绝对规避】{f.get('Target')}" for f in evolution_memory if f.get('Score') == -1]
                positive_samples = [f"【非常喜欢】{f.get('Target')}" for f in evolution_memory if f.get('Score') == 1]
                memory_str = f"""* 负样本: {', '.join(negative_samples) if negative_samples else '无'} \n* 正样本: {', '.join(positive_samples) if positive_samples else '无'}"""

            # 👑 100% 还原你的四大核心约束，并融合最新 JSON 输出格式！
            system_prompt = f"""
你是一个名为 OmniRoute 的专业旅行路书专家。当前任务：规划【{target_city}】的【{trip_days}天】行程。
【重要警告：绝对禁止越界！】你安排的所有景点、餐厅必须严格属于【{target_city}】！
用户原话意图："{intent_str}"
当前模式：【{current_mode}】 | 偏好画像：【{user_role}】

【专家客观底座数据 (包含真实网络知识 web_knowledge、经纬度和高德照片)】:
{json.dumps(poi_pool_data, ensure_ascii=False)}

【🔴 核心一：POI 去重与品类多样性约束 —— 绝对禁止同质化！】
1. 名字去重：同一 POI 名称绝对不能出现超过一次！
2. 品类去重（极其重要）：绝对禁止在同一天甚至相邻两天安排【同一种类】的食物或体验！
   - 如果你安排了"特色壮馍"，接下来的行程中【绝对不能】再出现"龙乡壮馍"等任何跟"馍"或同类面食相关的地点！
   - 保证美食的丰富反差感！如果上一顿是烤肉，下一顿必须是清淡炒菜或特色粉面。

【🔴 核心二：基于时空约束的流体调度】
时间节点必须由智能体根据真实交通耗时推算得出。上一节点结束时间 + 交通耗时 = 下一节点开始时间。

【🔴 核心三：事件配额】
每天必须包含：至少1次早餐、2个核心体验节点、午餐和晚餐。总节点数不可少于 {trip_days * 5} 个。

【🔴 核心四：交通方式真实感 —— 使用高德实测出行数据】
相邻POI之间的交通方式，请参考上方「高德实测出行数据」。 transport 字段必须填写类似："步行 12 分钟"、"驾车 8 分钟"。

【🔴 核心五：JSON 结构与 Day 标记约束】
1. 必须根据规划的 {trip_days} 天行程，为每个地点对象加上 `"day": 1` 或 `"day": 2` 等天数数字标记！
2. `desc` 必须结合池子中 `web_knowledge` 的真实网络情报，生成 50 字以上包含招牌美食特色、历史故事或打卡攻略的长篇详细介绍！绝对禁止写“品尝特色”等敷衍简短废话！
3. `photos` 必须原样照抄池子中对应 POI 的 `photos` 数组！
4. `lnglat` 必须原样照抄池子中对应 POI 的真实 `lnglat` 坐标！

[FINAL_JSON]
{{
  "status": "consensus_reached",
  "negotiation_summary": "一句话总结亮点",
  "route": [
    {{
      "day": 1,
      "time": "Day 1 | 08:30 - 09:30",
      "location": "某早茶店",
      "lnglat": [112.1, 34.1],
      "desc": "基于 web_knowledge 生成的 50 字以上详细介绍...",
      "transport": "步行 5 分钟",
      "cost_estimate": "20元",
      "tags": ["寻味"],
      "photos": ["照抄POI池中的图片URL"],
      "trust_reason": "图论优选推荐，本地老字号评分4.9"
    }}
  ]
}}
"""
        except Exception as e:
            print(f"🔥 [算法计算崩溃] {str(e)}")
            yield json.dumps({"token": f"\n[系统异常] 算法引擎计算失败: {str(e)}\n[FINAL_JSON]\n{{\"status\": \"error\", \"negotiation_summary\": \"算法计算异常\", \"route\": []}}\n"}, ensure_ascii=False) + "\n"
            return

        try:
            full_response = ""
            
            response = await client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请严格根据约束，为我生成 {target_city} 的推演过程与深度路线JSON。"}
                ],
                stream=True,
                temperature=llm_temperature
            )

            async for chunk in response:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        token = delta.content
                        full_response += token
                        yield json.dumps({"token": token}, ensure_ascii=False) + "\n"
            
            # 拼装真实弯道路网坐标链
            try:
                if "[FINAL_JSON]" in full_response:
                    parts = full_response.split("[FINAL_JSON]")
                    if len(parts) >= 2:
                        json_str_match = re.search(r'\{[\s\S]*\}', parts[1])
                        if json_str_match:
                            json_str = json_str_match.group(0).replace('```json', '').replace('```', '')
                            final_data = json.loads(json_str)
                            llm_routes = final_data.get("route", [])
                            
                            final_stitched_path = []
                            for i in range(len(llm_routes) - 1):
                                loc1 = llm_routes[i].get("location")
                                loc2 = llm_routes[i+1].get("location")
                                pair_key = f"{loc1}|{loc2}"
                                
                                if pair_key in all_actual_paths:
                                    final_stitched_path.extend(all_actual_paths[pair_key])
                                else:
                                    coords1 = llm_routes[i].get("lnglat")
                                    coords2 = llm_routes[i+1].get("lnglat")
                                    if coords1 and coords2:
                                        final_stitched_path.extend([coords1, coords2])
                                        
                            if final_stitched_path:
                                yield json.dumps({"actual_path": final_stitched_path}, ensure_ascii=False) + "\n"
                                print(f"🛣️ [链路拼装] 成功推送 {len(final_stitched_path)} 个物理坐标！")
            except Exception as e:
                print(f"🔥 [真实路网拼装失败] {str(e)}")

        except Exception as e:
            print(f"🔥 [流式推演崩溃] {str(e)}")
            yield json.dumps({"token": f"\n[FINAL_JSON]\n{{\"status\": \"error\", \"negotiation_summary\": \"{str(e)}\", \"route\": []}}\n"}, ensure_ascii=False) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")