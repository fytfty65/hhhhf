"""Operational trip endpoints separated from the multi-agent planner."""

import asyncio
import datetime
import json
import os
import time
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from core.agent_contracts import AgentInput, validate_agent_proposal
from core.agent_runtime import EMERGENCY_AGENT_ID, emergency_handler
from core.route_profile import (  # 参考路线 → 结构化画像（纯逻辑在 core/route_profile.py）
    extract_route_profile,
    merge_profiles,
    profile_to_retrieval_hints,
)
from core.ttl_cache import AsyncTtlCache  # 分层 TTL 缓存（见 _risk_snapshot 的性能说明）

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

# 分层缓存：不同数据的**自然更新频率**不同，用同一个 TTL 只会两头不讨好。
# 实测（2026-10-03）：冷调用 6,882ms、命中 45s 快照缓存 4ms，而前端轮询间隔 ~50-60s
# 大于 45s —— 于是几乎每次轮询都在重打 weather + traffic + safety 三个上游，
# 每个在线用户每分钟白烧 ~6.8s 服务端时间。
# 现在改为：按来源分层缓存（安全情报最慢也最稳、天气次之、路况最短），
# 快照缓存只负责"同一时刻的重复轮询"这一件事。
_RISK_WEATHER_TTL_SECONDS = float(os.getenv("RISK_WEATHER_TTL_SECONDS", "180"))
_RISK_TRAFFIC_TTL_SECONDS = float(os.getenv("RISK_TRAFFIC_TTL_SECONDS", "120"))
_RISK_SAFETY_TTL_SECONDS = float(os.getenv("RISK_SAFETY_TTL_SECONDS", "600"))
_risk_weather_cache = AsyncTtlCache(_RISK_WEATHER_TTL_SECONDS, name="risk-weather")
_risk_traffic_cache = AsyncTtlCache(_RISK_TRAFFIC_TTL_SECONDS, name="risk-traffic")
_risk_safety_cache = AsyncTtlCache(_RISK_SAFETY_TTL_SECONDS, name="risk-safety")

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

    # 三个上游互不依赖，但现在**各自还带分层缓存**：
    #   - 命中缓存的那一层立刻返回（实测 4ms），未命中的才真的打数据源；
    #   - 分层之后，"路况变了但安全情报没变"不会再连累安全情报重新拉一遍（它最慢，
    #     实测单次 ~8.4s）；
    #   - 同一 key 的并发调用由缓存做单飞，避免推送中心与雷达同时轮询打成双倍。
    #
    # 关键诚实细节：不少来源**不报 retrieved_at**。缓存之后 risk_ts 只能退回"组装时间"，
    # 那会显得比缓存里的数据更新。所以这里在**取数时刻**给缺时间戳的来源补上戳，
    # 缓存里存的就是带戳的那份 —— 于是 risk_ts 始终反映真实数据年龄。
    def _stamp(value: Any, moment_ms: int) -> Any:
        if isinstance(value, dict) and not value.get("retrieved_at"):
            return {**value, "retrieved_at": moment_ms}
        return value

    weather_key = city.strip().lower()
    safety_key = city.strip().lower()

    async def _weather_loader():
        return _stamp(await toolbox.get_real_weather(city), int(time.time() * 1000))

    async def _safety_loader():
        return _stamp(await risk_service.get_city_safety_intel(city), int(time.time() * 1000))

    async def _traffic_loader():
        return _stamp(await toolbox.get_traffic_status(coord_str), int(time.time() * 1000))

    weather_task = asyncio.create_task(_risk_weather_cache.get_or_load(weather_key, _weather_loader))
    safety_task = asyncio.create_task(_risk_safety_cache.get_or_load(safety_key, _safety_loader))
    traffic_task = (
        asyncio.create_task(_risk_traffic_cache.get_or_load(coord_str, _traffic_loader))
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


@router.post("/profile/reference-route")
async def reference_route_profile(payload: Dict[str, Any]):
    """把用户参考的一条路线（例如小红书帖子正文）抽成**结构化画像**。

    - 入参：`{text, source_label?, existing_profile?}`
    - 出参：画像（features / evidence / unknown / confidence / reference_count）+ 检索提示
    - 给了 `existing_profile` 就**合并**（多条参考的口径在 `core.route_profile.merge_profiles`）
    - **不回存原文**：这里只做抽取；调用方（网关）只保存抽出来的特征
    """
    text = str(payload.get("text") or "")
    if not text.strip():
        return {"error": "text 必填（粘贴参考路线的正文；我们只保存抽出来的特征，不保存原文）"}
    if len(text) > 20000:
        text = text[:20000]
    profile = extract_route_profile(text, source_label=str(payload.get("source_label") or ""))
    existing = payload.get("existing_profile")
    if isinstance(existing, dict) and (existing.get("features") or {}):
        profile = merge_profiles([existing, profile])
    return {
        "profile": profile,
        "hints": profile_to_retrieval_hints(profile),
        "note": "只保存抽取后的特征，不保存原文。",
    }


@router.post("/profile/reference-route/ocr")
async def reference_route_ocr(payload: Dict[str, Any]):
    """参考路线**截图 → 文字**（路线截图往往比复制正文更省事）。

    契约刻意做小做硬：
    - 只回**文字**，不回画像 —— 识别结果必须先给用户核对，再由用户决定是否学习，
      否则"识别错一个地名"会被静默抽成偏好；
    - 不保存图片：图片只在内存里转给视觉模型（`core/route_ocr`）；
    - 识别不了就如实回 `ok=false` + 原因码（HEIC/超限/纯风景图/模型故障），
      **绝不返回一段兜底行程**。
    """
    from core.route_ocr import IMAGE_PRIVACY_NOTE, OcrUnavailable, recognize_route_text

    image = payload.get("image_base64") or payload.get("image") or ""
    try:
        result = await recognize_route_text(str(image))
    except OcrUnavailable as exc:
        return {**exc.to_dict(), "privacy_note": IMAGE_PRIVACY_NOTE}
    return {
        **result,
        "note": "识别结果请先核对（可能有错字/漏字），确认后再点学习；服务端不保存图片。",
    }


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


@router.post("/risk/replan-scope")
async def risk_replan_scope(payload: Dict[str, Any]):
    """风险 → 最小扰动重规划范围（旅中闭环的入口）。

    做三件事：
    1. 由 emergency-agent 产出结构化提案（受影响节点 / 保留节点 / 保留率 / 动作）；
    2. 发出**带 correlation_id 的关联事件链**：`risk.changed` →（范围可判定时）`replan.requested`，
       这样整条链可追踪、可重放、可审计；
    3. 范围不可判定时**只发 risk.changed、不发 replan.requested** —— 拒绝整条路线重生成。

    注意：本端点**不写入新版本**。用户确认前不覆盖原方案（`requires_user_confirmation=true`），
    实际写入走既有的 replan_confirm 流程。
    """
    route = payload.get("route") or []
    risk_event = payload.get("risk_event") or {}
    room_id = str(payload.get("room_id") or "")
    correlation_id = str(payload.get("correlation_id") or payload.get("trip_id") or "").strip()
    if not correlation_id:
        correlation_id = f"trip-{uuid.uuid4().hex[:12]}"

    handler = emergency_handler()
    raw = await handler(
        AgentInput(
            request_id=f"replan-scope-{uuid.uuid4().hex[:8]}",
            agent_id=EMERGENCY_AGENT_ID,
            domain="emergency",
            context={"route": list(route), "risk_event": dict(risk_event)},
        )
    )
    proposal = validate_agent_proposal(raw)

    emitted: List[Dict[str, Any]] = []
    risk_event_id = str(uuid.uuid4())
    await event_bus.emit(
        OMNI_EVENTS_STREAM,
        "risk.changed",
        {"risk": dict(risk_event)},
        room_id=room_id,
        correlation_id=correlation_id,
        producer="risk-agent",
        event_id=risk_event_id,
        confidence=None,
    )
    emitted.append({"event_type": "risk.changed", "event_id": risk_event_id})

    if proposal.status == "ok" and proposal.candidates:
        candidate = proposal.candidates[0]
        replan_event_id = str(uuid.uuid4())
        await event_bus.emit(
            OMNI_EVENTS_STREAM,
            "replan.requested",
            {
                "action": candidate.get("action"),
                "affected_nodes": candidate.get("affected_nodes"),
                "preserved_nodes": candidate.get("preserved_nodes"),
                "preserved_ratio": candidate.get("preserved_ratio"),
                "requires_user_confirmation": candidate.get("requires_user_confirmation", True),
            },
            room_id=room_id,
            correlation_id=correlation_id,
            producer="emergency-agent",
            causation_id=risk_event_id,  # 因果链：replan.requested 由这次风险变化触发
            event_id=replan_event_id,
        )
        emitted.append({"event_type": "replan.requested", "event_id": replan_event_id})

    return {
        "correlation_id": correlation_id,
        "status": proposal.status,
        "proposal": proposal.model_dump(mode="json"),
        "events": emitted,
        "note": "仅产出提案与事件；用户确认前不写入新版本。",
    }

@router.get("/events/stream")
async def events_stream(request: Request, room_id: str = ""):
    async def stream_events():
        last_id = "$"
        while True:
            # 客户端断开就必须收工：否则被遗弃的流会永远跑下去（实测它烧过 87% 单核）。
            if await request.is_disconnected():
                break
            events = await event_bus.consume(OMNI_EVENTS_STREAM, last_id=last_id, count=50, block_ms=5000)
            if not events:
                yield json.dumps({"type": "keepalive", "ts": int(datetime.datetime.now().timestamp() * 1000)}) + "\n"
                continue
            for event_id, event in events:
                last_id = event_id
                if not room_id or event.get("room_id") == room_id:
                    yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(stream_events(), media_type="application/x-ndjson")
