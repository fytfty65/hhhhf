# ai-service/api/carbon_service.py
"""独立碳足迹 / 绿色交通评估服务（P5）。

依据出行方式碳排放因子（kg CO2e / 人·公里）对行程各段出行进行碳足迹核算，
输出：
  - 逐段碳排放分解（breakdown）
  - 全程总排放（total_kg）
  - 绿色里程占比（green_ratio）
  - 绿色替代建议（alternatives）：将高排放段替换为低排放方式可减少的排放量
  - 树木固碳等价（tree_equivalent）

设计原则：
  1. 纯计算、无外部依赖，逻辑可单测。
  2. 所有排放因子为业界公开估算量级，返回显式标注 source / is_estimated，
     避免把估算值当权威监测结果对外展示。
  3. 缺省距离 / 未知方式时按保守默认估算并标注，绝不臆造「零排放」假象。
"""

import datetime
from typing import List, Dict, Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

# ==========================================
# 1. 碳排放因子（kg CO2e / 人·公里，业界公开估算量级）
# ==========================================
EMISSION_FACTORS: Dict[str, float] = {
    "walking": 0.0,          # 步行
    "cycling": 0.0,          # 骑行
    "metro": 0.028,          # 地铁
    "bus": 0.035,            # 公交
    "transit": 0.030,        # 公交/地铁综合（不确定时取中值）
    "highspeed_rail": 0.040, # 高铁
    "driving": 0.190,        # 私家车/燃油车
    "taxi": 0.190,           # 出租车/网约车
    "flight": 0.280,         # 民航
}

# 绿色（低排放）方式集合，用于绿色里程占比与「最优绿色替代」比较。
GREEN_MODES = {"walking", "cycling", "metro", "bus", "highspeed_rail"}

# 每棵树一年的固碳量（kg CO2/年，公开估算中值）。
KG_CO2_PER_TREE_YEAR = 21.0

# 缺省单段距离（公里）：路段距离缺失时按城市内典型接驳距离估算，并标注估算。
DEFAULT_SEGMENT_KM = 3.0

MODE_LABELS: Dict[str, str] = {
    "walking": "步行",
    "cycling": "骑行",
    "metro": "地铁",
    "bus": "公交",
    "transit": "公交/地铁",
    "highspeed_rail": "高铁",
    "driving": "驾车",
    "taxi": "打车",
    "flight": "飞机",
}


# ==========================================
# 2. 核心计算
# ==========================================
def _factor(mode: str) -> float:
    return EMISSION_FACTORS.get(mode, EMISSION_FACTORS["transit"])


def _is_green(mode: str) -> bool:
    return mode in GREEN_MODES


def _green_alternative(mode: str) -> str:
    """给出某高排放方式的最优绿色替代；本身已绿色时返回空串。"""
    if mode in ("driving", "taxi"):
        return "metro"
    if mode == "flight":
        return "highspeed_rail"
    if mode == "transit":
        return "metro"
    return ""


def segment_emission(mode: str, distance_km: float) -> Dict[str, Any]:
    """计算单段排放：返回 {mode, distance_km, emission_kg, is_estimated}。"""
    mode = mode if mode in EMISSION_FACTORS else "transit"
    est_km = False
    km = float(distance_km) if distance_km is not None else 0.0
    if km <= 0:
        km = DEFAULT_SEGMENT_KM
        est_km = True
    emission = round(km * _factor(mode), 3)
    return {
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, mode),
        "distance_km": round(km, 2),
        "emission_kg": emission,
        "is_estimated": est_km,
    }


def compute_footprint(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """聚合多段出行的碳足迹，输出可解释的完整评估结果。"""
    per_segment: List[Dict[str, Any]] = []
    total_kg = 0.0
    total_km = 0.0
    green_km = 0.0
    alternatives: List[Dict[str, Any]] = []
    any_estimated = False

    for idx, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        mode = str(seg.get("mode") or "transit").strip().lower()
        raw_km = seg.get("distance_km")
        res = segment_emission(mode, raw_km)

        from_name = str(seg.get("from") or f"第{idx + 1}段起点")
        to_name = str(seg.get("to") or f"第{idx + 1}段终点")
        total_kg += res["emission_kg"]
        total_km += res["distance_km"]
        any_estimated = any_estimated or res["is_estimated"]
        if _is_green(res["mode"]):
            green_km += res["distance_km"]

        # 绿色替代建议：仅对高排放方式给出，「替换后排放」与「可减排量」
        alt_mode = _green_alternative(res["mode"])
        if alt_mode:
            saved = round(res["emission_kg"] - res["distance_km"] * _factor(alt_mode), 3)
            if saved > 0:
                alternatives.append({
                    "from": from_name,
                    "to": to_name,
                    "current_mode": res["mode_label"],
                    "alternative_mode": MODE_LABELS.get(alt_mode, alt_mode),
                    "saved_kg": saved,
                    "hint": f"「{from_name} → {to_name}」改乘{MODE_LABELS.get(alt_mode, alt_mode)}约可减排 {saved} kg CO2e",
                })

        per_segment.append({
            "index": idx,
            "from": from_name,
            "to": to_name,
            **res,
        })

    total_kg = round(total_kg, 2)
    total_km = round(total_km, 2)
    green_ratio = round(green_km / total_km, 3) if total_km > 0 else 1.0

    return {
        "total_kg": total_kg,
        "total_km": total_km,
        "green_km": round(green_km, 2),
        "green_ratio": green_ratio,
        "tree_equivalent": round(total_kg / KG_CO2_PER_TREE_YEAR, 2),
        "segments": per_segment,
        "alternatives": alternatives,
        "saved_if_green_kg": round(sum(a["saved_kg"] for a in alternatives), 3),
        "source": "carbon_emission_factors_estimation",
        "is_estimated": any_estimated,
        "method": "distance_x_emission_factor",
    }


# ==========================================
# 3. HTTP 端点
# ==========================================
@router.post("/carbon/footprint")
async def carbon_footprint(payload: Dict[str, Any]) -> Any:
    """碳足迹评估端点。

    入参：{"segments": [{"from","to","mode","distance_km"}, ...]}
      mode ∈ walking/cycling/metro/bus/transit/highspeed_rail/driving/taxi/flight
      distance_km 可选，缺失按典型城市接驳距离估算并标注 is_estimated。

    校验失败返回 422，而不是 200 + error 字段：后者会让监控、重试与前端
    按状态码分流全部失效，把一次调用错误伪装成成功。
    """
    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list):
        return JSONResponse(
            {"error": "segments 必填且必须为数组", "code": "SEGMENTS_INVALID", "result": None},
            status_code=422,
        )
    if not raw_segments:
        return JSONResponse(
            {"error": "segments 不能为空", "code": "SEGMENTS_EMPTY", "result": None},
            status_code=422,
        )
    valid_segments = [s for s in raw_segments if isinstance(s, dict)]
    if not valid_segments:
        return JSONResponse(
            {"error": "segments 内每一项都必须是对象", "code": "SEGMENTS_INVALID", "result": None},
            status_code=422,
        )
    result = compute_footprint(valid_segments)
    result["ts"] = int(datetime.datetime.now().timestamp() * 1000)
    return {"result": result}


@router.get("/carbon/factors")
async def carbon_factors():
    """返回当前碳排放因子与绿色方式集合（供前端展示口径与信任提示）。"""
    return {
        "factors": EMISSION_FACTORS,
        "green_modes": sorted(GREEN_MODES),
        "tree_kg_per_year": KG_CO2_PER_TREE_YEAR,
        "source": "carbon_emission_factors_estimation",
        "is_estimated": True,
    }