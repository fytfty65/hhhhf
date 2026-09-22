"""Operational trip endpoints separated from the multi-agent planner."""

import asyncio
import datetime
import json
import os
import time
from typing import Any, Dict

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from api.agent import (
    ExpertToolbox,
    OMNI_EVENTS_STREAM,
    RiskService,
    detect_risk_changes,
    event_bus,
)

router = APIRouter()


@router.post("/amap/poi")
async def amap_poi(payload: Dict[str, Any]):
    """Search real POIs using the AI service's server-side Amap credential.

    The gateway deliberately does not need a second copy of the provider key.
    Only the small, public POI shape required by the secondary planner is
    returned; provider credentials and static-map URLs stay inside this service.
    """
    city = str(payload.get("city") or "").strip()
    keywords = str(payload.get("keywords") or "").strip()
    types = str(payload.get("types") or "110000|141200|060400|060100").strip()
    try:
        limit = max(1, min(50, int(payload.get("limit") or 20)))
    except (TypeError, ValueError):
        limit = 20
    if not city or not keywords:
        return {"available": False, "pois": [], "message": "city 和 keywords 必填"}
    if len(city) > 100 or len(keywords) > 200 or len(types) > 200:
        return {"available": False, "pois": [], "message": "搜索参数过长"}

    toolbox = ExpertToolbox()
    if not toolbox.amap_key:
        return {"available": False, "pois": [], "message": "地图数据服务尚未配置"}
    raw_pois = await toolbox.get_dynamic_pois(city, keywords, types=types, limit=limit)
    pois = []
    for item in raw_pois:
        location = str(item.get("location") or "")
        coordinates = location.split(",", 1)
        lng = coordinates[0] if len(coordinates) == 2 else ""
        lat = coordinates[1] if len(coordinates) == 2 else ""
        pois.append({
            "name": str(item.get("name") or ""),
            "type": str(item.get("type") or ""),
            "address": str(item.get("address") or ""),
            "lng": lng,
            "lat": lat,
        })
    return {"available": True, "count": len(pois), "pois": pois}


# Realtime risk snapshots are polled every 30s by the client, and the upstream
# provider calls behind them cost seconds. A short cache keyed by city keeps a
# burst of polls (and the duplicate poller on the radar) from re-hitting every
# provider, without serving genuinely stale risk data.
_RISK_TTL_SECONDS = float(os.getenv("RISK_SNAPSHOT_TTL_SECONDS", "45"))
_risk_cache: Dict[str, tuple] = {}
_risk_cache_lock = asyncio.Lock()
_GLOBAL_RISK_MAX_CITIES = 6
_GLOBAL_RISK_CONCURRENCY = 3


def _risk_cache_key(city: str, coord_str: str) -> str:
    return f"{city.strip().lower()}::{coord_str}"


async def _risk_snapshot(city: str, coord: Any, baseline: Any):
    coord_str = ""
    if isinstance(coord, (list, tuple)) and len(coord) >= 2:
        coord_str = f"{coord[0]},{coord[1]}"
    elif isinstance(coord, str):
        coord_str = coord.strip()

    key = _risk_cache_key(city, coord_str)
    now = time.monotonic()
    async with _risk_cache_lock:
        cached = _risk_cache.get(key)
        if cached and now - cached[0] < _RISK_TTL_SECONDS:
            snapshot = cached[1]
            # The change set is relative to the *caller's* baseline, so it must
            # always be recomputed even when the snapshot itself is reused.
            return snapshot, detect_risk_changes(
                baseline if isinstance(baseline, dict) else None, snapshot
            )

    toolbox = ExpertToolbox()
    risk_service = RiskService()

    # These three calls are independent and previously ran strictly in series:
    # weather ~1.8s + traffic ~1.0s + safety ~8.4s = ~11s of dead time per
    # request. Running them concurrently bounds the wait by the slowest provider
    # instead of their sum.
    weather_task = asyncio.create_task(toolbox.get_real_weather(city))
    safety_task = asyncio.create_task(risk_service.get_city_safety_intel(city))
    traffic_task = (
        asyncio.create_task(toolbox.get_traffic_status(coord_str))
        if coord_str
        else None
    )

    weather, safety = await asyncio.gather(weather_task, safety_task)
    traffic = await traffic_task if traffic_task is not None else None

    safety = await risk_service.aggregate_city_risk(safety, city, weather, traffic)
    snapshot = risk_service.build_snapshot(safety, weather, traffic)

    async with _risk_cache_lock:
        _risk_cache[key] = (time.monotonic(), snapshot)
        # Bound the map so a long-lived process cannot grow it without limit.
        if len(_risk_cache) > 500:
            cutoff = time.monotonic() - _RISK_TTL_SECONDS
            for stale_key in [k for k, v in _risk_cache.items() if v[0] < cutoff]:
                _risk_cache.pop(stale_key, None)
            while len(_risk_cache) > 500:
                _risk_cache.pop(next(iter(_risk_cache)))

    return snapshot, detect_risk_changes(baseline if isinstance(baseline, dict) else None, snapshot)


@router.post("/risk/realtime")
async def risk_realtime(payload: Dict[str, Any]):
    city = str(payload.get("city") or payload.get("target_city") or "").strip()
    if not city:
        return {"error": "city 必填", "snapshot": None, "changes": []}
    snapshot, changes = await _risk_snapshot(
        city,
        payload.get("coordinate") or payload.get("lnglat") or payload.get("coord"),
        payload.get("baseline"),
    )
    return {"snapshot": snapshot, "changes": changes, "ts": int(datetime.datetime.now().timestamp() * 1000)}


@router.post("/risk/global")
async def risk_global(payload: Dict[str, Any]):
    """按需查询有限数量的全球城市风险快照。

    全球城市目录本身是静态坐标，不应被当作实时情报。此端点只查询调用方
    明确选中的城市，最多 6 个且并发最多 3 个；每个结果仍复用实时风险端点
    的 TTL 缓存，并保留 source / risk_ts / estimated 契约。
    """
    raw_cities = payload.get("cities") if isinstance(payload, dict) else None
    if not isinstance(raw_cities, list) or not raw_cities:
        return {"results": [], "errors": [{"code": "CITIES_REQUIRED", "message": "cities 必须为非空数组"}], "limits": {"max_cities": _GLOBAL_RISK_MAX_CITIES, "concurrency": _GLOBAL_RISK_CONCURRENCY}}

    errors = []
    requests = []
    seen = set()
    for index, item in enumerate(raw_cities):
        if not isinstance(item, dict):
            errors.append({"index": index, "code": "CITY_INVALID", "message": "城市项必须为对象"})
            continue
        city = str(item.get("city") or "").strip()
        if not city:
            errors.append({"index": index, "code": "CITY_REQUIRED", "message": "城市名称不能为空"})
            continue
        if len(city) > 100:
            errors.append({"index": index, "city": city[:32], "code": "CITY_TOO_LONG", "message": "城市名称过长"})
            continue
        key = city.casefold()
        if key in seen:
            continue
        seen.add(key)
        if len(requests) >= _GLOBAL_RISK_MAX_CITIES:
            continue
        requests.append({
            "city": city,
            "coordinate": item.get("coordinate") or item.get("lnglat") or item.get("coord"),
            "baseline": item.get("baseline"),
        })

    if len(raw_cities) > _GLOBAL_RISK_MAX_CITIES:
        errors.append({"code": "CITY_LIMIT", "message": f"单次最多查询 {_GLOBAL_RISK_MAX_CITIES} 个城市"})

    semaphore = asyncio.Semaphore(_GLOBAL_RISK_CONCURRENCY)

    async def resolve(item: Dict[str, Any]) -> Dict[str, Any]:
        async with semaphore:
            try:
                snapshot, changes = await _risk_snapshot(item["city"], item["coordinate"], item["baseline"])
                return {"city": item["city"], "snapshot": snapshot, "changes": changes, "available": bool(snapshot)}
            except Exception as exc:
                # Provider failures are isolated to one city; never fabricate a
                # risk value for the remaining cities.
                return {"city": item["city"], "snapshot": None, "changes": [], "available": False, "error": "CITY_SNAPSHOT_UNAVAILABLE"}

    results = await asyncio.gather(*(resolve(item) for item in requests))
    return {
        "results": results,
        "errors": errors,
        "limits": {"max_cities": _GLOBAL_RISK_MAX_CITIES, "concurrency": _GLOBAL_RISK_CONCURRENCY},
        "ts": int(datetime.datetime.now().timestamp() * 1000),
    }


@router.post("/risk/subscriptions/refresh")
async def refresh_subscriptions(payload: Dict[str, Any]):
    results = []
    for subscription in payload.get("subscriptions") or []:
        if not isinstance(subscription, dict):
            continue
        city = str(subscription.get("city") or "").strip()
        if not city:
            continue
        snapshot, changes = await _risk_snapshot(
            city,
            subscription.get("coordinate") or subscription.get("lnglat"),
            subscription.get("baseline"),
        )
        results.append({"city": city, "snapshot": snapshot, "changes": changes, "has_change": bool(changes)})
    return {"results": results, "ts": int(datetime.datetime.now().timestamp() * 1000)}


@router.get("/events/stream")
async def events_stream(room_id: str = ""):
    async def stream_events():
        last_id = "$"
        while True:
            events = await event_bus.consume(OMNI_EVENTS_STREAM, last_id=last_id, count=50, block_ms=5000)
            if not events:
                yield json.dumps({"type": "keepalive", "ts": int(datetime.datetime.now().timestamp() * 1000)}) + "\n"
                continue
            for event_id, event in events:
                last_id = event_id
                if not room_id or event.get("room_id") == room_id:
                    yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(stream_events(), media_type="application/x-ndjson")
