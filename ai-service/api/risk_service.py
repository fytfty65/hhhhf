# ai-service/api/risk_service.py
"""独立风险计算服务（P1 重构）。

将原先耦合在 agent.py 中的风险计算逻辑抽离，供以下消费方复用：
  - 3D 态势感知雷达（WorldSafetyGlobe）
  - 3D 行程雷达 / 风控（FullRouteVisualizer）
  - 旅中风险推送中心（risk_realtime）
  - 行程生成（run_negotiate 节点级风险画像）

核心能力：
  1. 统一数据模型 RiskReport：规范风险快照结构，含 source / is_estimated 溯源元数据。
  2. IntelligenceProvider 情报源降级链：WorldMonitor -> GDELT -> local_baseline，
     降级必须显式标注 source。
  3. 五维风险分解 compute_risk_dimensions、节点级风险 compute_node_risk、
     变更检测 detect_risk_changes，全部基于真实信号，杜绝前端字符串猜测。
"""

import asyncio
import os
import re
import time
import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Protocol

import httpx


def _intel_cache_ttl_seconds() -> float:
    """TTL for cached city safety intelligence (default 15 minutes)."""
    try:
        return max(0.0, float(os.getenv("RISK_INTEL_TTL_SECONDS", "900")))
    except (TypeError, ValueError):
        return 900.0


def _intel_budget_seconds() -> float:
    """Total wall-clock budget for the provider chain (default 6s).

    Bounds the worst case: without it a cold miss pays every provider timeout in
    turn, which measured ~8.5s on a machine that cannot reach the providers.
    """
    try:
        return max(0.0, float(os.getenv("RISK_INTEL_BUDGET_SECONDS", "6")))
    except (TypeError, ValueError):
        return 6.0


# ==========================================
# 1. 统一数据模型：RiskReport
# ==========================================
@dataclass
class RiskReport:
    """统一风险快照数据模型。

    供态势感知 / 风控雷达 / 风险推送三方复用，字段契约一致，
    避免「同一份数据、多套字段名」导致的前端二次解析与猜测。
    """
    city: str
    cii_score: float = 10.0
    risk_level: str = "LOW"  # LOW / MEDIUM / HIGH
    crime_score: float = 10.0
    weather_score: float = 10.0
    political_score: float = 10.0
    health_score: float = 10.0
    traffic_score: float = 10.0
    source: str = ""
    is_estimated: bool = False
    active_alerts: List[Dict[str, Any]] = field(default_factory=list)
    safety_advice: str = ""
    weather: Optional[Dict[str, Any]] = None
    traffic: Optional[Dict[str, Any]] = None
    cii_decomposition: Optional[Dict[str, Any]] = None
    signal_sources: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["risk_ts"] = int(datetime.datetime.now().timestamp() * 1000)
        return data


# ==========================================
# 2. 情报源接口：IntelligenceProvider（降级链）
# ==========================================
class IntelligenceProvider(Protocol):
    """情报源接口。降级链按优先级依次尝试，首个可用者胜出。

    关键约束：无论命中哪一级，返回结果必须带 source 字段，
    兜底基线必须显式 is_estimated=True，杜绝把「无源估算」当权威情报。
    """
    name: str

    async def fetch(self, city: str) -> Optional[Dict[str, Any]]:
        ...


class WorldMonitorProvider:
    """一级情报源：WorldMonitor API（需配置 WORLDMONITOR_API_URL + WORLDMONITOR_API_KEY）。"""
    name = "WorldMonitor"

    def __init__(self):
        self.base_url = os.getenv("WORLDMONITOR_API_URL", "https://api.worldmonitor.app")
        self.api_key = os.getenv("WORLDMONITOR_API_KEY", "")

    async def fetch(self, city: str) -> Optional[Dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=2.5) as http_client:
                headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
                resp = await http_client.get(
                    f"{self.base_url}/api/v1/intelligence/risk",
                    params={"city": city},
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and data.get("cii_score"):
                        data.setdefault("city", city)
                        data.setdefault("source", "WorldMonitor")
                        return data
        except Exception:
            pass
        return None


class GDELTProvider:
    """二级情报源：GDELT 全球事件实时流（免费公开，按城市检索安全/抗议/事故/犯罪事件）。"""
    name = "GDELT"

    def __init__(self):
        self.gdelt_url = os.getenv("GDELT_API_URL", "https://api.gdeltproject.org/api/v2/doc/doc")

    async def fetch(self, city: str) -> Optional[Dict[str, Any]]:
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
                today = datetime.date.today()
                decayed_weight = 0.0
                recent_7d = 0
                for art in articles:
                    if not isinstance(art, dict):
                        continue
                    days_old = _gdelt_days_old(str(art.get("seendate") or ""), today)
                    if days_old is None:
                        days_old = CII_TIME_DECAY_HALF_LIFE  # 无法解析日期时按中位衰减
                    if days_old <= 7:
                        recent_7d += 1
                    decayed_weight += _time_decay_factor(days_old)
                count = len(articles)
                # 时间衰减事件强度（0-100）：久远事件影响指数收敛，避免旧闻持续拉高风险
                intensity = round(max(0.0, min(100.0, 10.0 + min(decayed_weight, 30.0) * 3.0)), 1)
                level = "HIGH" if intensity >= 60 else ("MEDIUM" if intensity >= 30 else "LOW")
                cii = intensity  # 与多维权重复合口径对齐
                return {
                    "city": city,
                    "cii_score": cii,
                    "risk_level": level,
                    "source": "GDELT",
                    "event_intensity": intensity,
                    "event_stats": {
                        "total": count,
                        "recent_7d": recent_7d,
                        "decayed_weight": round(decayed_weight, 2),
                        "time_decay_half_life": CII_TIME_DECAY_HALF_LIFE,
                    },
                    "active_alerts": [
                        {
                            "type": "EVENT_STREAM",
                            "level": level,
                            "title": "近30天安全事件流",
                            "detail": (
                                f"GDELT 检索到【{city}】相关安全/事故/抗议类公开报道 {count} 条"
                                f"（近7天 {recent_7d} 条，时间衰减权重 {decayed_weight:.1f}），已纳入多维加权。"
                            ),
                        }
                    ],
                    "safety_advice": (
                        f"【{city}】近30天公开安全事件 {count} 条（近7天 {recent_7d} 条），"
                        f"综合风险指数 CII {cii}（{level}）。"
                    ),
                }
        except Exception:
            return None


class LocalBaselineProvider:
    """兜底基线：无真实权威情报源时返回中性 LOW 基线，显式标注估算。

    绝不臆造 HIGH/MEDIUM 风险，杜绝把哈希噪声当情报对外展示。
    """
    name = "local_baseline"

    async def fetch(self, city: str) -> Dict[str, Any]:
        return {
            "city": city,
            "cii_score": 10.0,
            "risk_level": "LOW",
            "source": "local_baseline",
            "is_estimated": True,
            "active_alerts": [
                {
                    "type": "DATA_NOTICE",
                    "level": "LOW",
                    "title": "未接入实时权威情报源",
                    "detail": "当前未接入实时权威安全情报源，CII 为基础基线值，仅供行程参考，不构成风险判断依据。",
                }
            ],
            "safety_advice": (
                f"【{city}】当前未接入实时权威情报源，动态综合风险指数 CII 为基础基线（LOW），仅供参考；"
                "如需更准确的风险评估请配置 WorldMonitor / GDELT 数据源。"
            ),
        }


# ==========================================
# 2.5 CII 模型升级：多维权重复合 + 时间衰减 + 可解释分解（P2）
# ==========================================
# 五维风险权重（和为 1.0）：治安为旅行安全首要因素，政治/卫生/气象/交通次之。
CII_DIMENSION_WEIGHTS: Dict[str, float] = {
    "crime": 0.35,
    "political": 0.20,
    "health": 0.15,
    "weather": 0.15,
    "traffic": 0.15,
}

# 时间衰减半衰期（天）：历史事件影响每过半个半衰期减半。
CII_TIME_DECAY_HALF_LIFE = 14.0


def _time_decay_factor(days_old: float, half_life: float = CII_TIME_DECAY_HALF_LIFE) -> float:
    """时间衰减系数：越久远的事件对当前风险贡献越低（指数衰减）。"""
    if days_old is None or days_old < 0:
        days_old = 0.0
    return 0.5 ** (days_old / half_life)


def _gdelt_days_old(seen: str, today: Optional[datetime.date] = None) -> Optional[float]:
    """解析 GDELT seendate（YYYYMMDDHHMMSS / YYYYMMDD）为距今天数，无法解析返回 None。"""
    seen = (seen or "").strip()
    if len(seen) < 8 or not seen[:8].isdigit():
        return None
    try:
        d = datetime.date(int(seen[0:4]), int(seen[4:6]), int(seen[6:8]))
        t = today or datetime.date.today()
        return float((t - d).days)
    except Exception:
        return None


def compute_cii_score(
    dimensions: Dict[str, float],
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """多维权重复合：CII = Σ(维度分 × 权重)，输出可解释的逐维分解而非单一数值。

    dimensions 期望含 crime_score / weather_score / political_score / health_score / traffic_score。
    返回值结构与 RiskReport.cii_decomposition 对齐，供前端直接渲染「这个 CII 从哪来」。
    """
    w = dict(weights or CII_DIMENSION_WEIGHTS)
    decomposition: Dict[str, Any] = {}
    total = 0.0
    for dim in ("crime", "political", "health", "weather", "traffic"):
        score = round(float(dimensions.get(f"{dim}_score", 10.0) or 10.0), 1)
        weight = float(w.get(dim, 0.0))
        contribution = round(score * weight, 1)
        decomposition[dim] = {
            "score": score,
            "weight": weight,
            "contribution": contribution,
        }
        total += contribution
    cii = round(max(0.0, min(100.0, total)), 1)
    return {
        "cii_score": cii,
        "method": "weighted_composite",
        "total_weight": round(sum(w.values()), 4),
        "dimensions": decomposition,
    }


# ==========================================
# 3. 五维风险分解 / 节点风险 / 变更检测
# ==========================================
def compute_risk_dimensions(intel: Dict[str, Any], weather: Optional[Dict] = None, traffic: Optional[Dict] = None) -> Dict[str, float]:
    """基于真实信号计算五维风险分解（0-100），供前端 3D 态势/风控雷达直接消费。

    关键原则：不臆造风险。治安/政治两维仅在接入真实情报源（WorldMonitor/GDELT）时按等级分档，
    否则保持低基线并显式标注 is_estimated=True，杜绝把「哈希噪声」当情报对外展示。
    """
    alerts = intel.get("active_alerts") if isinstance(intel.get("active_alerts"), list) else []
    src = str(intel.get("source") or "")

    # 治安：优先使用时间衰减事件强度（连续值），否则按等级分档；安全/犯罪类告警加权
    crime = 10.0
    if src in ("WorldMonitor", "GDELT"):
        intensity = float(intel.get("event_intensity") or 0.0)
        if intensity > 0:
            crime = round(intensity, 1)
        else:
            rl = str(intel.get("risk_level") or "LOW")
            crime = 75.0 if rl == "HIGH" else 40.0 if rl == "MEDIUM" else 10.0
    for a in alerts:
        if isinstance(a, dict) and str(a.get("type") or "") in ("CRIME", "SAFETY"):
            lvl = str(a.get("level") or "")
            crime = min(crime + (30 if lvl == "HIGH" else 15 if lvl == "MEDIUM" else 5), 100.0)

    # 气象：基于实时天气条件与气温
    weather_score = 10.0
    _weather_kw = {
        "特大暴雨": 90, "大暴雨": 85, "台风": 88, "暴雨": 80, "冰雹": 82, "暴雪": 80,
        "大雪": 65, "雷暴": 60, "雷雨": 60, "大雨": 55, "沙尘暴": 70, "大雾": 55,
        "浓雾": 55, "中雨": 40, "雾霾": 40, "沙尘": 40, "多云": 25, "阴": 25, "晴": 10,
    }
    if isinstance(weather, dict):
        cond = str(weather.get("condition") or "")
        for kw, v in _weather_kw.items():
            if kw in cond:
                weather_score = v
                break
        mtemp = re.search(r"([-+]?\d+)", cond)
        if mtemp:
            t = int(mtemp.group(1))
            if t >= 33:
                weather_score = max(weather_score, 70.0)
            elif t <= 0:
                weather_score = max(weather_score, 65.0)

    # 政治：优先使用时间衰减事件强度（连续值），否则按等级分档；否则低基线（不臆造）
    political = 10.0
    if src in ("WorldMonitor", "GDELT"):
        intensity = float(intel.get("event_intensity") or 0.0)
        if intensity > 0:
            political = round(max(10.0, min(100.0, intensity * 0.8)), 1)
        else:
            rl = str(intel.get("risk_level") or "LOW")
            political = 75.0 if rl == "HIGH" else 40.0 if rl == "MEDIUM" else 10.0

    # 卫生：基于真实卫生/疫情类告警
    health = 15.0
    for a in alerts:
        if isinstance(a, dict) and str(a.get("type") or "") in ("HEALTH", "EPIDEMIC", "MEDICAL"):
            health = max(health, 60.0)

    # 交通：高德实时路况状态码（1 畅通 / 2 缓慢 / 3 拥堵 / 4 严重拥堵）
    traffic_score = 10.0
    if isinstance(traffic, dict):
        sc = str(traffic.get("status_code") or "1")
        code = int(sc) if sc.isdigit() else 1
        traffic_score = {1: 10.0, 2: 40.0, 3: 70.0, 4: 85.0}.get(code, 10.0)

    return {
        "crime_score": round(crime, 1),
        "weather_score": round(weather_score, 1),
        "political_score": round(political, 1),
        "health_score": round(health, 1),
        "traffic_score": round(traffic_score, 1),
    }


def compute_node_risk(node: Dict[str, Any], city_crime: float, crowd_hint: str = "") -> float:
    """节点级风险画像（0-100）：城市治安基线 × 场所类型 × 时段 × 拥挤度，供前端 3D 雷达逐点渲染。"""
    score = float(city_crime or 10.0) * 0.5
    if bool(node.get("is_hotel")):
        score -= 8  # 住宿节点相对安全，略降
    if _is_night_hour(str(node.get("time") or "")):
        score += 6  # 夜间/凌晨时段略升
    if crowd_hint and any(k in crowd_hint for k in ["拥挤", "高峰", "爆满", "密集", "排队"]):
        score += 20
    return max(0.0, min(100.0, round(score, 1)))


# 夜间时段：20:00-23:59 与 00:00-06:59。
# 必须锚定到「时间字符串的小时字段」，否则 "0?[0-6]" 分支会在 "12:30" 的第 2 个
# 字符处匹配到 "2:30"、在 "16:45" 匹配到 "6:45"，把白天误判为夜间并错误加 6 分。
# 原实现未加锚点，实测 12:30 与 16:45 均被加了夜间惩罚。
_NIGHT_HOUR_PATTERN = re.compile(r"(?:^|[^\d])(2[0-3]|0?[0-6]):\d{2}")


def _is_night_hour(time_text: str) -> bool:
    """判断 'HH:MM'（可带前缀如 'Day 1 | '）是否落在夜间时段。"""
    return bool(_NIGHT_HOUR_PATTERN.search(time_text))


def detect_risk_changes(baseline: Optional[Dict[str, Any]], current: Dict[str, Any]) -> List[Dict[str, Any]]:
    """旅中风险推送中心：对比「出发前基线」与「旅中实时快照」，输出可推送的变更事件。

    事件粒度覆盖：综合风险等级跳变 / CII 显著变动 / 天气突变 / 实时路况恶化 / 新增安全告警。
    所有事件均来自真实数据信号（高德天气路况 + WorldMonitor/GDELT 情报），绝不臆造。
    """
    changes: List[Dict[str, Any]] = []
    if not isinstance(baseline, dict):
        return changes

    def _int(s, default: int = 1) -> int:
        try:
            return int(str(s)) if str(s).strip().isdigit() else default
        except Exception:
            return default

    # 1) 风险等级跳变 / CII 显著变动
    prev_level = str(baseline.get("risk_level") or "LOW")
    cur_level = str(current.get("risk_level") or "LOW")
    prev_cii = float(baseline.get("cii_score") or 0.0)
    cur_cii = float(current.get("cii_score") or 0.0)
    if prev_level != cur_level:
        sev = "HIGH" if cur_level == "HIGH" else ("MEDIUM" if cur_level == "MEDIUM" else "LOW")
        changes.append({
            "type": "RISK_LEVEL_CHANGE",
            "severity": sev,
            "title": "综合风险等级变化",
            "detail": f"风险等级由 {prev_level} 变为 {cur_level}（CII {prev_cii} → {cur_cii}）。",
        })
    elif abs(cur_cii - prev_cii) >= 15:
        changes.append({
            "type": "CII_CHANGE",
            "severity": "MEDIUM",
            "title": "综合风险指数显著波动",
            "detail": f"动态 CII 由 {prev_cii} 变为 {cur_cii}，波动幅度 {abs(cur_cii - prev_cii):.1f}。",
        })

    # 2) 天气突变
    _bad_kw = ["暴雨", "台风", "冰雹", "暴雪", "雷暴", "大雨", "沙尘", "大雾", "浓雾", "中雨", "雾霾"]
    _sev_kw = ["暴雨", "台风", "冰雹", "暴雪", "雷暴"]
    prev_cond = str((baseline.get("weather") or {}).get("condition") or "")
    cur_cond = str((current.get("weather") or {}).get("condition") or "")
    prev_bad = any(k in prev_cond for k in _bad_kw)
    cur_bad = any(k in cur_cond for k in _bad_kw)
    if cur_bad and not prev_bad:
        changes.append({
            "type": "WEATHER_CHANGE",
            "severity": "HIGH" if any(k in cur_cond for k in _sev_kw) else "MEDIUM",
            "title": "天气突变预警",
            "detail": f"天气由「{prev_cond or '—'}」变为「{cur_cond}」，请随身携带雨具并调整户外行程。",
        })
    elif prev_cond and cur_cond and prev_cond != cur_cond:
        changes.append({
            "type": "WEATHER_CHANGE",
            "severity": "LOW",
            "title": "天气变化",
            "detail": f"天气由「{prev_cond}」变为「{cur_cond}」。",
        })

    # 3) 实时路况恶化
    prev_traffic = baseline.get("traffic") if isinstance(baseline.get("traffic"), dict) else {}
    cur_traffic = current.get("traffic") if isinstance(current.get("traffic"), dict) else {}
    prev_sc, cur_sc = _int(prev_traffic.get("status_code"), 1), _int(cur_traffic.get("status_code"), 1)
    if cur_sc > prev_sc and cur_sc >= 3:
        changes.append({
            "type": "TRAFFIC_CHANGE",
            "severity": "HIGH" if cur_sc >= 4 else "MEDIUM",
            "title": "实时路况恶化",
            "detail": f"路况由「{prev_traffic.get('description') or '畅通'}」变为「{cur_traffic.get('description') or '拥堵'}」，请预留缓冲时间。",
        })

    # 4) 新增安全告警
    prev_titles = {str(a.get("title")) for a in (baseline.get("active_alerts") or []) if isinstance(a, dict) and a.get("title")}
    for a in (current.get("active_alerts") or []):
        if isinstance(a, dict) and a.get("title") and str(a.get("title")) not in prev_titles:
            changes.append({
                "type": "NEW_ALERT",
                "severity": str(a.get("level") or "MEDIUM"),
                "title": f"新增告警：{a.get('title')}",
                "detail": str(a.get("detail") or ""),
            })

    return changes


# ==========================================
# 4. 风险计算服务：降级链 + 多源聚合 + 快照构建
# ==========================================
class RiskService:
    """风险计算服务。

    替代原 agent.py 中的 WorldMonitorClient，对外暴露相同语义的方法，
    使态势感知 / 风控雷达 / 风险推送三方复用同一套计算逻辑。
    """

    def __init__(self):
        self._providers: List[IntelligenceProvider] = [
            WorldMonitorProvider(),
            GDELTProvider(),
        ]
        self._fallback = LocalBaselineProvider()
        # City-level safety intelligence changes on the order of hours, but the
        # provider chain behind it costs seconds (WorldMonitor 2.5s timeout +
        # GDELT 4s timeout when both miss). Caching it per city is what turns a
        # repeated risk poll from a multi-second request into a near-instant one.
        self._intel_cache: Dict[str, tuple] = {}
        self._intel_ttl = _intel_cache_ttl_seconds()

    async def get_city_safety_intel(self, city: str) -> Dict[str, Any]:
        """情报源降级链：WorldMonitor -> GDELT -> local_baseline（首个可用者胜出）。

        The providers run concurrently under a single overall budget. Two
        measured realities drove this:

        * sequentially the chain costs the *sum* of every provider timeout
          (measured 2.4s + 5.0s ~= 7.4s when both miss);
        * running them concurrently removed the sum but not the problem, because
          each provider still spends seconds before giving up, so a cold miss
          still took ~8.5s end to end.

        A budget is therefore the only thing that actually bounds the request. If
        no provider answers within it we prefer a stale cached reading (clearly
        still better than recomputing the same timeouts) and only then the
        neutral local baseline.
        """
        key = (city or "").strip().lower()
        now = time.monotonic()
        cached = self._intel_cache.get(key) if key else None
        if cached and now - cached[0] < self._intel_ttl:
            return cached[1]

        budget = _intel_budget_seconds()
        tasks = [asyncio.create_task(provider.fetch(city)) for provider in self._providers]
        intel: Optional[Dict[str, Any]] = None
        try:
            done, _pending = await asyncio.wait(tasks, timeout=budget)
            for task in done:
                try:
                    candidate = task.result()
                except Exception:
                    continue
                if candidate:
                    intel = candidate
                    break
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

        if intel:
            self._remember_intel(key, intel)
            return intel

        # No provider answered in budget. A stale reading beats a fresh round of
        # identical timeouts, so prefer it and say so via the source field.
        if cached:
            stale = dict(cached[1])
            stale["is_stale"] = True
            stale["staleness_seconds"] = round(now - cached[0], 1)
            return stale

        fallback = await self._fallback.fetch(city)
        self._remember_intel(key, fallback)
        return fallback

    def _remember_intel(self, key: str, intel: Dict[str, Any]) -> None:
        if not key:
            return
        self._intel_cache[key] = (time.monotonic(), intel)
        if len(self._intel_cache) > 500:
            cutoff = time.monotonic() - self._intel_ttl
            for stale in [k for k, v in self._intel_cache.items() if v[0] < cutoff]:
                self._intel_cache.pop(stale, None)
            while len(self._intel_cache) > 500:
                self._intel_cache.pop(next(iter(self._intel_cache)))

    def cached_city_count(self) -> int:
        """Exposed for tests and diagnostics."""
        return len(self._intel_cache)

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
        new_alerts: List[Dict[str, Any]] = []
        signals: List[str] = []

        # 1) 实时天气融合（高德/心知真实数据）——生成告警与维度分，CII 最终由多维加权复合得出
        if isinstance(weather, dict):
            cond = str(weather.get("condition") or "")
            temp = None
            mtemp = re.search(r"([-+]?\d+)", cond)
            if mtemp:
                temp = int(mtemp.group(1))
            if temp is not None:
                if temp >= 33:
                    new_alerts.append({"type": "HEAT", "level": "MEDIUM", "title": "高温预警", "detail": f"实时气温达 {temp}°C，建议避开午间户外暴晒，补水防晒并缩短室外停留时间。"})
                    signals.append("高温")
                elif temp <= 0:
                    new_alerts.append({"type": "COLD", "level": "MEDIUM", "title": "低温预警", "detail": f"实时气温低至 {temp}°C，注意保暖，谨防路面结冰。"})
                    signals.append("严寒")
            for kw, w in weather_risk_keywords.items():
                if kw in cond:
                    new_alerts.append({"type": "WEATHER", "level": "HIGH" if w >= 8 else "MEDIUM", "title": f"{kw}风险", "detail": f"实时天气出现「{kw}」，已提升当日行程风险，建议优先安排室内/遮蔽项目并预留缓冲。"})
                    signals.append(kw)
                    break

        # 2) 官方气象预警公开接口（免费，失败自动回退，不阻塞）
        pub_alert = await self._fetch_public_weather_alert(city)
        if pub_alert:
            new_alerts.append(pub_alert)
            signals.append("官方气象预警")

        # 3) 实时交通融合
        if isinstance(traffic, dict):
            sc = str(traffic.get("status_code") or "1")
            code = int(sc) if sc.isdigit() else 1
            if code >= 4:
                new_alerts.append({"type": "TRAFFIC", "level": "MEDIUM", "title": "严重拥堵", "detail": f"实时路况拥堵指数较高：{traffic.get('description', '拥堵')}。建议错峰出行并预留通勤时间。"})
                signals.append("严重拥堵")
            elif code >= 3:
                new_alerts.append({"type": "TRAFFIC", "level": "MEDIUM", "title": "局部拥堵", "detail": f"实时路况出现拥堵：{traffic.get('description', '拥堵')}。建议避开高峰路段。"})
                signals.append("拥堵")

        # 4) 五维风险分解：基于真实信号输出维度分
        dims = compute_risk_dimensions(intel, weather, traffic)

        # 5) CII 多维权重复合 + 可解释分解（P2 升级：替代「基础分 + 加性 delta」）
        cii = compute_cii_score(dims)
        fused_cii = cii["cii_score"]
        intel["cii_score"] = fused_cii
        intel["risk_level"] = "HIGH" if fused_cii >= 40 else ("MEDIUM" if fused_cii >= 25 else "LOW")
        intel["is_weekend"] = bool(is_weekend)
        intel["cii_decomposition"] = cii
        intel["fusion"] = {
            "method": "weighted_composite",
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

        # 6) 维度分回填 + 估算标注（契约闭合，杜绝前端字符串猜测）
        intel["crime_score"] = dims["crime_score"]
        intel["weather_score"] = dims["weather_score"]
        intel["political_score"] = dims["political_score"]
        intel["health_score"] = dims["health_score"]
        intel["traffic_score"] = dims["traffic_score"]
        intel["is_estimated"] = bool(intel.get("is_estimated") or (str(intel.get("source") or "") in ("local_baseline", "local_deterministic")))
        return intel

    def build_snapshot(
        self,
        safety_intel: Dict[str, Any],
        weather: Optional[Dict[str, Any]] = None,
        traffic: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """基于聚合后的情报构建统一 RiskReport 快照，注入 source / risk_ts 溯源元数据。"""
        def source_meta(value: Optional[Dict[str, Any]], fallback: str) -> Dict[str, Any]:
            if not isinstance(value, dict) or not value:
                return {
                    "provider": fallback,
                    "available": False,
                    "estimated": False,
                    "retrieved_at": None,
                }
            provider = str(value.get("source") or value.get("provider") or fallback)
            retrieved = value.get("retrieved_at") or value.get("updated_at") or value.get("timestamp")
            return {
                "provider": provider,
                "available": True,
                "estimated": bool(value.get("estimated") or value.get("is_estimated")),
                "retrieved_at": retrieved,
            }

        signal_sources = {
            "safety": source_meta(safety_intel, "safety_unavailable"),
            "weather": source_meta(weather, "weather_unavailable"),
            "traffic": source_meta(traffic, "traffic_unavailable"),
        }
        return RiskReport(
            city=str(safety_intel.get("city") or ""),
            cii_score=float(safety_intel.get("cii_score") or 10.0),
            risk_level=str(safety_intel.get("risk_level") or "LOW"),
            crime_score=float(safety_intel.get("crime_score") or 10.0),
            weather_score=float(safety_intel.get("weather_score") or 10.0),
            political_score=float(safety_intel.get("political_score") or 10.0),
            health_score=float(safety_intel.get("health_score") or 10.0),
            traffic_score=float(safety_intel.get("traffic_score") or 10.0),
            source=str(safety_intel.get("source") or ""),
            is_estimated=bool(safety_intel.get("is_estimated")),
            active_alerts=safety_intel.get("active_alerts") or [],
            safety_advice=str(safety_intel.get("safety_advice") or ""),
            weather=weather,
            traffic=traffic,
            cii_decomposition=safety_intel.get("cii_decomposition"),
            signal_sources=signal_sources,
        ).to_dict()
