import sys
import os
import time
import time as _time
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
import redis.asyncio as redis
from duckduckgo_search import DDGS
from typing import List, Dict, Any, Optional, Mapping, Sequence, Tuple
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI, BadRequestError
from models.schemas import GatewayMessage
from api.risk_service import RiskService, compute_node_risk
from core.travel_utils import analyze_weather_for_planning, estimate_crowdedness, build_personalization_hint, build_profile_category_boost, build_profile_budget_gamma, build_profile_fine_tag_boost, build_profile_applied, classify_fine_tags, normalize_mode, normalize_role
from core.time_intelligence import analyze_pace, monday_closure_notes
from core.optimization import fairness_gate, fairness_report, repair_route, validate_route
from core.constraints import resolve_conflicts
from core.contextual_bandit import ContextualThompsonBandit, derive_reward, stable_arm_id
from core.agent_contracts import capability_registry, audit_route_agent_proposal
from core.agent_bus import AgentMessageBus  # 多轮智能体消息总线（可复现、无网络）
from core.simulation import simulate_plan
from core.member_negotiation import member_interests, negotiate_member_routes
from core import llm_config as llm_cfg  # LLM 调用参数单一来源（模型/端点/temperature/max_tokens）
from core.llm_config import model_name  # LLM 默认值单一来源（曾与 conflict.py 各写一套）
from core.events import new_event  # 统一事件协议信封（correlation_id / causation_id / version）
from core.cache_registry import BoundedTTLCache, register_cache  # 有界 TTL 缓存：容量上限 + 淘汰 + 命中率
from core.llm_metrics import record_llm_call  # LLM 调用记账：次数/失败率/延迟分位/token/成本
# 候选池相关工具统一在模块级导入：实测教训（2026-10-04）—— 把它们各自写在函数内部局部导入，
# 结果外层代码用了 `needs_widening` 却只在内层函数里导过 → NameError 让整条推演流中断，
# 而单测全绿（因为没有任何单测覆盖这条长生成器路径）。放在顶层，缺名字在导入时就暴露。
from core.place_quality import (  # 简介口径：能核实就用，不能就如实说没有
    SUPPLIER_FACT_FIELDS,
    amap_fact_line,
    clean_web_knowledge,
    clear_placeholder_supplier_facts,
    description_of,
    supplier_facts_disclosure,
)
# 省域覆盖（A）：把目的地写成一个省时，检索范围是**全省地州**而不只是省会。
# 实测（2026-11-01，四川 3 天）：省级单查询前 25 条全在成都各区 → 九寨沟/四姑娘山
# 这些 300km 外的川西景点根本进不了候选池；`config/district` + 逐地州检索能覆盖。
from core.province_scope import (
    PROVINCE_DEFAULT_CITY,
    PROVINCE_SUFFIXES,
    anchor_quota,
    assign_cluster_days,
    choose_region_line,
    cluster_places,
    estimate_rail_hours,
    pick_anchors,
    province_of,
    province_scope_note,
    short_region_name,
)
from core.poi_pool import (
    FOOD_WIDENING_KEYWORDS,
    LODGING_WIDENING_KEYWORDS,
    NEIGHBOR_SCENIC_KEYWORDS,
    SCENIC_WIDENING_KEYWORDS,
    amap_error_is_retryable,
    amap_error_note,
    attraction_query_limits,
    collapse_sub_spots,
    dedupe_sub_spots,
    filter_by_radius,
    haversine_km,
    needs_widening,
    parent_region,
    park_cap,
    parse_amap_location,
    pool_supply_report,
    required_food_candidates,
    required_lodging_candidates,
    required_scenic_candidates,
    scenic_radius_km,
    top_up_category_buckets,
    widen_candidates,
)
# 行程配比口径（用户明确的口味）：默认自然=人文各半并穿插美食/住宿；自然景观只算景区
# （城市公园另算一档）；用户明确说了就加权。纯函数在 core/route_composition，便于单测。
from core.route_composition import (
    clamp_targets_by_supply,
    composition_audit,
    composition_hint as composition_hint_text,
    composition_targets,
    count_route_composition,
    normalize_day_sequence,
    numeric_targets,
    order_day_stops_by_nearest,
    order_route_by_day,
    rebalance_days_by_geography,
    repair_forbidden_categories,
    scenic_tier,
    settle_lodging,
    summarize_supply_limits,
    summarize_targets,
    trim_category_over_cap,
)


# —— 出稿后路网拼装的并发控制与缓存（2026-10-05 实测：这一段要 48.4s，且算在用户等待里）——
# 为什么要信号量：15 对相邻节点 × 3 种出行方式 = 45 个请求一次打出去，高德会 QPS 限流，
# 表现成"大家一起排队等到超时"；有界并发反而更快也更省配额。
_TRAVEL_OPTIONS_SEMAPHORE = asyncio.Semaphore(8)
_TRAVEL_OPTIONS_CACHE: BoundedTTLCache = register_cache(
    BoundedTTLCache("travel_options", max_entries=512, ttl_seconds=3600.0)
)

# —— 取数细分的耗时账本（2026-10-05）——
# 分段耗时显示「取数与风控」要 28–34s，但里面是十几次高德调用并发跑，
# 只看总数无法判断该优化谁。这里按"调用种类"累计**墙钟时间与次数**，
# 出方案时打一行汇总（纯观测，不改变任何行为）。
_FETCH_TIMING: Dict[str, float] = {}
_FETCH_COUNTS: Dict[str, int] = {}


# 👑 全进程的**高德请求闸门**（2026-10-05 实测，反直觉但数据很清楚）：
#    高德对同一把 key 的 QPS 限制很紧，**并发请求不但更慢，还会静默丢掉整条查询**。
#    同一台机器、同一把 key 的实测：
#      · 单个 `place/text`：0.39~0.44s，status=1；
#      · 5 个并发：总 12.86s，单个 10.52 / 8.0 / 5.46 / 3.02 / 0.42s，其中 1 个 status=0（被限流）；
#      · 并发 2：总 1.32s，但 7 个里 **2 个 status=0** —— 那两条查询什么都没查到；
#      · 串行：7 个共 2.69s，全部 status=1。
#    所以这里给**所有高德请求**加同一道闸门（并发 1 + 最小间隔），
#    而不是只管 `place/text`：天气/路况/地理编码用的是同一把 key，
#    它们在别的协程里并发跑，一样会把这把 key 顶到限流上 ——
#    这解释了"应用里单次请求要 3.6s，而单独跑只要 0.4s"。
#    间隔可用 AMAP_MIN_INTERVAL_SECONDS 调（默认 0.15s：比 QPS 上限留一点余量，又不白等）。
_AMAP_GATE_LOCKS: Dict[int, asyncio.Lock] = {}
_AMAP_LAST_AT_BY_LOOP: Dict[int, float] = {}
# 闸门自身的账本：进了几次、排队等了多久、真正占着闸门多久。
# 实测（2026-10-05）：加闸门后单次请求从 3.6s 掉回 0.44s，但阶段总耗时没变 ——
# 那就必须能回答"到底有多少个高德请求在排队"，否则没法判断该减请求还是该放开并发。
_AMAP_GATE_STATS: Dict[str, float] = {"count": 0.0, "wait": 0.0, "held": 0.0}
# 高德 POI 实景图缓存（按 POI id）：同一个 POI 常出现在多个关键词查询里，
# 不缓存就会把同一张图问好几遍 —— 而每次问都在占用同一把 key 的 QPS。
# 实景图基本不变，TTL 给 7 天；超限丢最久未用的一条，不再整体清空。
_AMAP_PHOTO_CACHE: BoundedTTLCache = register_cache(
    BoundedTTLCache("amap_photos", max_entries=512, ttl_seconds=7 * 24 * 3600.0)
)


def amap_gate_summary() -> str:
    return (
        f"请求={int(_AMAP_GATE_STATS['count'])} 排队={_AMAP_GATE_STATS['wait']:.1f}s "
        f"占用={_AMAP_GATE_STATS['held']:.1f}s"
    )


# —— 事件循环卡顿探针（2026-10-05）——
# 实测：单发高德 `place/text` 只要 0.4s，但在应用里同一时刻要 4.2s。
# `time.monotonic()` 量到的是**墙钟**，如果事件循环被本地 CPU 活（分类/去重/帕累托）
# 堵住，等待响应的协程就会被推迟、表现成"请求变慢"。这个探针用"每 50ms 该醒一次、
# 实际醒了多久"来量化卡顿，避免把"本地算得慢"误判成"供应商慢"。
class LoopLagProbe:
    def __init__(self, interval: float = 0.05) -> None:
        self.interval = float(interval)
        self.max_lag = 0.0
        self.total_lag = 0.0
        self.samples = 0
        self._task: Optional[asyncio.Task] = None

    async def _run(self) -> None:
        while True:
            started = time.monotonic()
            await asyncio.sleep(self.interval)
            lag = max(0.0, (time.monotonic() - started) - self.interval)
            self.max_lag = max(self.max_lag, lag)
            self.total_lag += lag
            self.samples += 1

    def start(self) -> "LoopLagProbe":
        try:
            self._task = asyncio.ensure_future(self._run())
        except RuntimeError:
            self._task = None
        return self

    def stop(self) -> "LoopLagProbe":
        if self._task is not None:
            self._task.cancel()
            self._task = None
        return self

    def summary(self) -> str:
        return f"最大卡顿={self.max_lag * 1000:.0f}ms 累计={self.total_lag:.1f}s/{self.samples}次"


class LoopWatchdog:
    """线程看门狗：事件循环**卡住时立刻打印它卡在哪一行**。

    为什么必须用线程：`LoopLagProbe` 自己也跑在事件循环里，等它被调度到时卡顿已经结束，
    只能告诉你"卡了 19.7 秒"，告诉不了"卡在哪儿"。这个看门狗是真正的 OS 线程，
    每 0.5s 检查一次心跳；心跳超过阈值没更新就 `sys._current_frames()` 把主线程栈打出来。
    只在 `PLAN_LOOP_WATCHDOG=1` 时启用（默认关，生产不引入线程）。
    """

    def __init__(self, threshold: float = 2.0) -> None:
        self.threshold = float(threshold)
        self.heartbeat = time.monotonic()
        self._stop = False
        self._thread: Optional[Any] = None
        self._task: Optional[asyncio.Task] = None

    @staticmethod
    def enabled() -> bool:
        return str(os.getenv("PLAN_LOOP_WATCHDOG") or "").strip() in {"1", "true", "on", "yes"}

    async def _beat(self) -> None:
        while not self._stop:
            self.heartbeat = time.monotonic()
            await asyncio.sleep(0.1)

    def _watch(self) -> None:
        import threading
        import traceback

        main_thread = threading.main_thread()
        while not self._stop:
            time.sleep(0.5)
            stalled = time.monotonic() - self.heartbeat
            if stalled <= self.threshold:
                continue
            ident = main_thread.ident
            frame = sys._current_frames().get(ident) if ident is not None else None
            print(f"🐌 [事件循环看门狗] 卡住 {stalled:.1f}s，主线程栈：", flush=True)
            if frame is not None:
                for line in traceback.format_stack(frame)[-12:]:
                    print("    " + line.strip(), flush=True)

    def start(self) -> "LoopWatchdog":
        if not self.enabled():
            return self
        import threading

        self._thread = threading.Thread(target=self._watch, name="plan-loop-watchdog", daemon=True)
        self._thread.start()
        self._task = asyncio.ensure_future(self._beat())
        return self

    def stop(self) -> "LoopWatchdog":
        self._stop = True
        if self._task is not None:
            self._task.cancel()
            self._task = None
        return self


def _amap_gate_lock() -> asyncio.Lock:
    """拿到**当前事件循环**的闸门锁。

    ⚠️ 不能把一个模块级 `asyncio.Lock()` 跨事件循环用：它第一次 acquire 时会绑定到当时
    那个 loop，之后从另一个 loop 再 acquire 会抛 "bound to a different event loop"
    （单测里每个用例一个 `asyncio.run` 就是这种情况 —— 实测被这条抓过一次，
    表现成"并发那几种出行方式整段查不到"）。按 loop 分开存，生产（单 loop）行为不变。
    """
    key = id(asyncio.get_running_loop())
    lock = _AMAP_GATE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _AMAP_GATE_LOCKS[key] = lock
    return lock


def amap_min_interval_seconds() -> float:
    """闸门的**下限**间隔（可配，默认 0.15s）。自适应退避只会把它调大，不会调小到它以下。"""
    raw = str(os.getenv("AMAP_MIN_INTERVAL_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 0.15
    return value if value >= 0 else 0.15


def amap_max_interval_seconds() -> float:
    """退避上限（默认 2.0s）：再慢也得能跑完一次规划。"""
    raw = str(os.getenv("AMAP_MAX_INTERVAL_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 2.0
    floor = amap_min_interval_seconds()
    return value if value >= floor else floor


# —— 高德闸门的**自适应退避**状态（2026-10-05）——
# 为什么固定间隔不够：这把 key 的 QPS 档位是**外部事实**，而且会随时间/配额变化。
# 实测（tools/probe_hotel_geocode.py）固定 0.15s 时连"酒店"这种宽关键词都回
# CUQPS_HAS_EXCEEDED_THE_LIMIT、静默返回 0 条 —— 候选池因此凭空变小，
# 而方案里看不出任何异样（"这里好像没什么景点"）。
# 做法：被限流就把间隔**翻倍**（翻到上限为止），成功一次就回落一点（不低于下限）。
# 全部状态都暴露出来（`amap_gate_state()`），日志与方案里能如实说"这轮被限流过几次"。
_AMAP_THROTTLE: Dict[str, Any] = {"interval": 0.0, "limits": 0, "requests": 0, "last_limit_at": 0.0}


def note_amap_result(ok: bool, *, info: str = "", now: Optional[float] = None) -> float:
    """上报一次高德调用的结果，返回**下一次要用的间隔**。

    - 失败（限流/其它错误）：间隔 ×2（上限 `amap_max_interval_seconds()`）；
    - 成功：间隔 ×0.8（下限 `amap_min_interval_seconds()`）——慢慢回落，不立刻打回原形，
      否则"刚被限流又立刻全速"会震荡。
    """
    moment = float(now if now is not None else time.monotonic())
    floor = amap_min_interval_seconds()
    ceiling = amap_max_interval_seconds()
    current = float(_AMAP_THROTTLE.get("interval") or 0.0) or floor
    _AMAP_THROTTLE["requests"] = int(_AMAP_THROTTLE.get("requests") or 0) + 1
    if ok:
        current = max(floor, current * 0.8)
    else:
        current = min(ceiling, max(floor, current) * 2.0)
        _AMAP_THROTTLE["limits"] = int(_AMAP_THROTTLE.get("limits") or 0) + 1
        _AMAP_THROTTLE["last_limit_at"] = moment
        if info:
            _AMAP_THROTTLE["last_info"] = str(info)[:120]
        print(f"🚦 [高德闸门] 被限流（{str(info)[:60]}）→ 间隔退避到 {current:.2f}s", flush=True)
    _AMAP_THROTTLE["interval"] = current
    return current


def amap_throttle_interval_seconds() -> float:
    """当前生效的间隔（没退避过就是下限）。"""
    floor = amap_min_interval_seconds()
    return float(_AMAP_THROTTLE.get("interval") or 0.0) or floor


def amap_gate_state() -> Dict[str, Any]:
    """闸门状态：当前间隔 / 这轮被限流几次 / 发过几次请求（供日志与方案披露）。"""
    floor = amap_min_interval_seconds()
    interval = amap_throttle_interval_seconds()
    return {
        "interval_seconds": round(interval, 3),
        "base_interval_seconds": round(floor, 3),
        "throttled": interval > floor + 1e-9,
        "limits": int(_AMAP_THROTTLE.get("limits") or 0),
        "requests": int(_AMAP_THROTTLE.get("requests") or 0),
        "last_info": str(_AMAP_THROTTLE.get("last_info") or ""),
    }


def reset_amap_throttle() -> None:
    _AMAP_THROTTLE.update({"interval": 0.0, "limits": 0, "requests": 0, "last_limit_at": 0.0})
    _AMAP_THROTTLE.pop("last_info", None)


class AmapGate:
    """高德请求闸门：全进程串行 + 最小间隔（用法：`async with AmapGate("weather"):`）。

    为什么不是"限并发数"就完事：实测并发 2 时总耗时确实掉到 1.32s，
    但代价是 7 条查询里有 2 条被限流成 status=0、**静默返回空**。
    对规划来说"快但少了一半候选"比"慢一点但候选完整"更糟 ——
    候选少了会直接改变方案质量，而用户看不到这个差别。

    `name` 只为计时用：把"哪一类高德调用占着闸门多久"记进 `_FETCH_TIMING`，
    否则只知道总数、不知道该减哪一种请求。
    """

    def __init__(self, name: str = "unknown") -> None:
        self.name = str(name or "unknown")

    async def __aenter__(self) -> "AmapGate":
        lock = _amap_gate_lock()
        _wait_started = time.monotonic()
        await lock.acquire()
        key = id(asyncio.get_running_loop())
        _AMAP_GATE_STATS["count"] = float(_AMAP_GATE_STATS.get("count") or 0.0) + 1.0
        _AMAP_GATE_STATS["wait"] = float(_AMAP_GATE_STATS.get("wait") or 0.0) + (time.monotonic() - _wait_started)
        try:
            # 用**自适应间隔**（被限流就退避、成功再回落），而不是写死的 0.15s ——
            # 这把 key 的 QPS 档位是外部事实，固定间隔在它变紧时会静默丢整条查询。
            gap = amap_throttle_interval_seconds() - (time.monotonic() - float(_AMAP_LAST_AT_BY_LOOP.get(key) or 0.0))
            if gap > 0:
                await asyncio.sleep(gap)
            _AMAP_LAST_AT_BY_LOOP[key] = time.monotonic()
        except BaseException:
            if lock.locked():
                lock.release()
            raise
        self._held_started = time.monotonic()
        return self

    async def __aexit__(self, *_exc) -> bool:
        _started = float(getattr(self, "_held_started", 0.0) or 0.0)
        if _started:
            _held = time.monotonic() - _started
            _AMAP_GATE_STATS["held"] = float(_AMAP_GATE_STATS.get("held") or 0.0) + _held
            _note_fetch(f"gate:{self.name}", _held)
        lock = _AMAP_GATE_LOCKS.get(id(asyncio.get_running_loop()))
        if lock is not None and lock.locked():
            lock.release()
        return False



def _note_fetch(name: str, seconds: float, count: int = 1) -> None:
    _FETCH_TIMING[name] = float(_FETCH_TIMING.get(name) or 0.0) + max(0.0, float(seconds))
    _FETCH_COUNTS[name] = int(_FETCH_COUNTS.get(name) or 0) + max(0, int(count))


def reset_fetch_timing() -> None:
    _FETCH_TIMING.clear()
    _FETCH_COUNTS.clear()
    _AMAP_GATE_STATS["count"] = 0.0
    _AMAP_GATE_STATS["wait"] = 0.0
    _AMAP_GATE_STATS["held"] = 0.0


# —— POI 检索的进程内缓存（2026-10-05）——
# 为什么缓存"取候选"这一步（而不只是最终方案）：
#   ① 同一座城市的候选池在几十分钟内是**同一份事实**（景点/餐饮/住宿不会按小时变），
#      连查两次纯属浪费；
#   ② 高德那把 key 的 QPS 是本项目最紧的资源（实测并发 2 就会限流、静默丢整条查询），
#      少发一次请求就是给真正的取数留余量；
#   ③ 用户"换一换"、多人同城、以及我们自己的 warm-up，都会重复同样的查询。
# 口径：只缓存**已经过过滤的真实结果**（不缓存失败、不缓存空），默认 30 分钟；
# 缓存命中会在日志里说明"这一轮的候选来自缓存"，不让它看起来像刚查的。
_POI_QUERY_CACHE_IMPL: BoundedTTLCache = register_cache(
    BoundedTTLCache("poi_query", max_entries=256, ttl_seconds=1800.0)
)
# 兼容存量代码与测试：以底层 dict 视图直接读条目（写入时刻, 行列表）。
# 写入/淘汰/TTL/命中率统一走 _POI_QUERY_CACHE_IMPL，不再"超限整体清空"。
_POI_QUERY_CACHE: Dict[str, tuple] = _POI_QUERY_CACHE_IMPL.entries
_POI_CACHE_STATS: Dict[str, int] = {"hits": 0, "misses": 0}


def poi_cache_ttl_seconds() -> float:
    raw = str(os.getenv("AMAP_POI_CACHE_TTL_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 1800.0
    return value if value >= 0 else 1800.0


def reset_pool_cache_stats() -> None:
    _POI_CACHE_STATS["hits"] = 0
    _POI_CACHE_STATS["misses"] = 0


def pool_cache_summary() -> str:
    return f"候选缓存 命中={_POI_CACHE_STATS.get('hits', 0)} 未命中={_POI_CACHE_STATS.get('misses', 0)}"


def _poi_cache_key(city: str, keywords: str, types: str, limit: int, play_worthy: bool) -> str:
    return f"{str(city).strip()}|{str(keywords).strip()}|{str(types).strip()}|{int(limit)}|{bool(play_worthy)}"


def _poi_cache_get(key: str) -> Optional[List[Dict[str, Any]]]:
    if poi_cache_ttl_seconds() <= 0:
        return None
    rows, age = _POI_QUERY_CACHE_IMPL.get_with_meta(key)
    if rows is None or age is None:
        return None
    return [dict(row) for row in rows]


def _poi_cache_set(key: str, rows: Sequence[Mapping[str, Any]]) -> None:
    ttl = poi_cache_ttl_seconds()
    if ttl <= 0 or not rows:
        return
    # 有容量上限（256）与 LRU 淘汰：不再"超限整体 clear"——整体清空会把还在命中的热点一起丢，
    # 下一次同样的查询又要重新打高德（QPS 是本项目最紧的资源）。
    _POI_QUERY_CACHE_IMPL.set(key, [dict(row) for row in rows], ttl=ttl)



def fetch_timing_summary() -> str:
    if not _FETCH_TIMING:
        return "（没有取数计时）"
    parts = [
        f"{name}={_FETCH_TIMING[name]:.1f}s×{_FETCH_COUNTS.get(name, 0)}"
        for name in sorted(_FETCH_TIMING, key=lambda key: -_FETCH_TIMING[key])
    ]
    return " ".join(parts)



# 👑 共享 SSL 上下文（2026-10-05，看门狗抓到的栈 —— 这条是本轮最大的性能发现）：
#    httpx 在**构造客户端**时会同步调用 `ssl.create_default_context()`，
#    在本机实测：`httpx.AsyncClient(timeout=6.0)` 要 **0.87s**，而传入一个已有上下文只要 **0.05s**（17 倍）。
#    这是**同步**调用，会把整个事件循环堵住 —— 看门狗栈显示卡顿就发生在
#    `get_dynamic_pois → httpx.AsyncClient.__init__ → _config.create_ssl_context → ssl.create_default_context`。
#    一次推演要建十来个客户端（5~7 个 POI 查询 + 天气 + 路况 + 每个城市坐标 + 每一对出行方式），
#    于是"供应商很慢"的假象（单个 place/text 从 0.4s 变成 4.2s）其实全是本地在建 SSL 上下文。
#    在**导入时**建一次，之后所有客户端都复用它。
try:
    import ssl as _ssl

    SHARED_SSL_CONTEXT: Optional["_ssl.SSLContext"] = _ssl.create_default_context()
except Exception:  # pragma: no cover - 建不出来就退回 httpx 默认行为（宁可慢，不要起不来）
    SHARED_SSL_CONTEXT = None


def _http_client(timeout: float) -> "httpx.AsyncClient":
    """建一个复用了共享 SSL 上下文的 httpx 客户端（见 SHARED_SSL_CONTEXT 的实测说明）。"""
    if SHARED_SSL_CONTEXT is not None:
        return httpx.AsyncClient(timeout=timeout, verify=SHARED_SSL_CONTEXT)
    return httpx.AsyncClient(timeout=timeout)


def _straight_line_km(origin: str, destination: str) -> Optional[float]:
    """两条 "lng,lat" 字符串的直线距离（拿不到就 None，调用方按"都要问"处理）。

    ⚠️ 必须**先解析成 [lng, lat]** 再算：`haversine_km` 是按下标取的，
    直接喂 "81.3,43.9" 这种字符串会把第 0/1 个**字符**当成经纬度（不报错，静默算错）。
    """
    origin_point = parse_amap_location(origin)
    destination_point = parse_amap_location(destination)
    if not origin_point or not destination_point:
        return None
    distance = float(haversine_km(origin_point, destination_point))
    return None if distance != distance else distance


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

# —— LLM 驱动的专业智能体（2026-11-03）——
# 默认关闭：开启后主推演之外会多一次并行 LLM 调用（route-planner 由"候选池适配器"
# 换成"LLM 在池内取舍"）；关着时行为与之前完全一致，全部走确定性候选池。
SPECIALIST_LLM_ENABLED_ENV = "SPECIALIST_LLM_ENABLED"
SPECIALIST_LLM_AGENT_IDS: Tuple[str, ...] = ("route-planner",)


def specialist_llm_enabled() -> bool:
    return str(os.getenv(SPECIALIST_LLM_ENABLED_ENV) or "").strip().lower() in {
        "1", "true", "on", "yes",
    }


async def specialist_llm_complete(messages: Sequence[Mapping[str, str]]) -> str:
    """专业智能体的 LLM 调用适配器（与主推演共用模型/端点/参数）。"""
    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[dict(item) for item in messages],
        temperature=llm_cfg.temperature(),
        max_tokens=llm_cfg.max_tokens(),
    )
    return response.choices[0].message.content or ""

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
# Bandit 状态也有 TTL（默认 30 天）：状态文件长期不更新，通常意味着模型版本/奖励口径已经换过，
# 继续拿几十天前学到的后验当"当前最优臂"是在用一个过期的结论做决策。
# 过期就不恢复（从新建状态文件开始学），并在日志里说明原因 —— 不静默沿用。
def _bandit_state_stale_days(path: str) -> Optional[float]:
    # 默认 30 天；显式设为 0/负数表示不做过期判断（仍在日志里给出文件年龄便于排查）
    ttl_days_raw = str(os.getenv("AI_BANDIT_STATE_TTL_DAYS") or "").strip() or "30"
    try:
        ttl_days = float(ttl_days_raw)
    except ValueError:
        ttl_days = 30.0
    if ttl_days <= 0:
        return None
    try:
        age_seconds = max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None
    age_days = age_seconds / 86400.0
    return age_days if age_days > ttl_days else None


if _bandit_state_file:
    _stale_days = _bandit_state_stale_days(_bandit_state_file)
    if _stale_days is not None:
        print(
            f"⌛ [Bandit] 学习状态已过期（{_stale_days:.1f} 天未更新），不恢复旧后验，重新开始学习",
            flush=True,
        )
    if _stale_days is None and planning_bandit.load(_bandit_state_file):
        print(f"♻️ [Bandit] 已从 {_bandit_state_file} 恢复学习状态", flush=True)
    else:
        # The state file does not exist yet (or is stale, see above). Arm the path
        # and write an initial snapshot, otherwise `record()` has no path to save to
        # and every learned posterior is lost on restart — which is what made the
        # learning loop look like it worked while accumulating nothing durable.
        try:
            planning_bandit.save(_bandit_state_file)
            planning_bandit.arm_state_file(_bandit_state_file)
            print(f"🆕 [Bandit] 初始化学习状态文件: {_bandit_state_file}", flush=True)
        except OSError as _bandit_state_err:
            print(f"⚠️ [Bandit] 学习状态文件不可写，学习结果将不会持久化: {_bandit_state_err}", flush=True)


# ==========================================
# 实时数据缓存层：降低 API 调用成本、提高响应速度（TTL=1h，满足更新频率>=每小时1次）
# 容量上限 512：以前是裸 dict 无上限，长跑进程内存只增不减；
# 每条带到期时刻，过期即删，命中率登记到 /metrics（`cache_metrics()`）。
# ==========================================

_api_cache: BoundedTTLCache = register_cache(
    BoundedTTLCache("external_api", max_entries=512, ttl_seconds=3600.0)
)

def _cache_get(key: str):
    val, _age = _api_cache.get_with_meta(key)
    return val

def _cache_get_with_meta(key: str):
    """返回 (值, 缓存年龄秒)：数据卡片要如实说明"这是缓存还是刚取的"。"""
    return _api_cache.get_with_meta(key)

def _cache_set(key: str, val, ttl: int = 3600):
    _api_cache.set(key, val, ttl=ttl)


def _llm_usage_tokens(resp: Any) -> Tuple[Optional[int], Optional[int]]:
    """取 OpenAI 兼容响应的 usage（缺失就如实返回 None，不按字符数瞎估）。"""
    usage = getattr(resp, "usage", None)
    if usage is None:
        return None, None
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    return (
        int(prompt) if isinstance(prompt, int) else None,
        int(completion) if isinstance(completion, int) else None,
    )


# ==========================================
# 👑 辅助工具：中文与多格式游玩天数解析器
# ==========================================
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


async def collect_nearby_food_candidates(
    route: Any,
    *,
    days: int,
    fetch_nearby: Any,
    radius_km: float = 15.0,
    limit: int = 8,
    keywords: str = "餐厅|美食|小吃",
) -> "tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]":
    """对**缺餐的那一天**做一次周边检索，拿"当天玩点附近真实存在的餐馆"。

    为什么必须按天做（2026-10-07 实测）：候选池是全城取的，而缺餐的往往是**远点那天** ——
    喀什第 3 天在岳普湖方向，全城候选里最近的餐馆在 **82.6km** 外的市区（塞进去等于没补），
    而按天做周边检索，同一片区域 9~12km 内就有好几家（岳普湖县双福美食 9.1km 等）。

    返回 `({“food”: [候选…]}, [逐天记录])`；`fetch_nearby(center, keywords, radius_km=…, limit=…)`
    由调用方注入（便于单测），逐天失败**不影响其它天**、也不抛异常。
    """
    from core.plan_quality import node_day, node_lnglat, nodes_of

    def _is_food(node: Mapping[str, Any]) -> bool:
        text = f"{node.get('name')} {node.get('type')}"
        return any(word in text for word in ("餐", "美食", "小吃", "米粉", "拌面", "馕", "烧烤", "火锅"))

    def _is_lodging(node: Mapping[str, Any]) -> bool:
        text = f"{node.get('name')} {node.get('type')}"
        return bool(node.get("is_hotel")) or any(
            word in text for word in ("酒店", "民宿", "客栈", "宾馆", "旅馆", "度假村")
        )

    # ⚠️ 两种入参都要吃（2026-10-07 实测踩到）：传 `plan` dict 时按 `route` 取值，
    #    传**节点列表**时直接用 —— 第一版只走 `nodes_of()`，而它读 `plan["route"]`，
    #    于是直接传列表时静默返回空列表、整个周边检索一次都没发出去。
    if isinstance(route, Mapping):
        nodes = [node for node in nodes_of(route)]
    else:
        nodes = [dict(node) for node in (route or []) if isinstance(node, Mapping)]
    pool: Dict[str, List[Dict[str, Any]]] = {"food": []}
    report: List[Dict[str, Any]] = []
    total_days = max(1, int(days or 1))
    for day in range(1, total_days + 1):
        day_nodes = [node for node in nodes if node_day(node, 1) == day]
        if not day_nodes:
            continue
        if any(_is_food(node) for node in day_nodes):
            continue  # 这天已经有饭点了，不用查
        coords: List[Tuple[float, float]] = []
        for node in day_nodes:
            if _is_lodging(node):
                continue
            coord = node_lnglat(node)
            if coord:
                coords.append(coord)
        if not coords:
            continue
        # ⚠️ 检索中心不能取**质心**（2026-11-02 实测，四川第 3 天）：当天的玩点分布在
        #    毕棚沟 ↔ 玛嘉沟 ↔ 老马坪（跨约 60km）时，质心落在无人区，15km 内一家餐馆都没有，
        #    于是"缺餐"被记成"最近的饭点在 126.8km 外"，而理县镇上的餐馆其实就在毕棚沟旁。
        #    饭点只要挨着当天**某一个**玩点就是可去的（与 `_distance_to` 同口径），
        #    所以按玩点逐个查、合并结果；相距很近的玩点只查一次，最多查 3 个中心。
        centers: List[Tuple[float, float]] = []
        for coord in coords:
            if all(haversine_km(coord, kept) > 5.0 for kept in centers):
                centers.append(coord)
            if len(centers) >= 3:
                break
        existing = {str(node.get("name") or "").strip() for node in day_nodes}
        added = 0
        errors: List[str] = []
        for center in centers:
            try:
                rows = await fetch_nearby(center, keywords, radius_km=radius_km, limit=limit) or []
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {str(exc)[:80]}")
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                name = str(row.get("name") or "").strip()
                if not name or name in existing or any(item.get("name") == name for item in pool["food"]):
                    continue
                pool["food"].append({**dict(row), "planning_category": "food"})
                added += 1
        entry: Dict[str, Any] = {"day": day, "center": centers[0], "centers": centers, "found": added}
        if errors and not added:
            entry["error"] = errors[0]
        report.append(entry)
    return pool, report


def _desc_for(item: Any, city: str, provider_desc: Any = "") -> "tuple[str, str]":
    """一条候选记录能拿到的**诚实简介**：校验过的联网 > 供应商原文 > 高德字段事实 > 暂无简介。

    统一入口的理由（2026-10-05 实测）：简介原来散在 7 处写法里，每处都有自己的"兜底好话"
    （「本地高人气……」那类套话），一处漏改就又把编造放回用户面前。
    现在只有这一个地方决定"写什么、算哪来的"，并且**地址/评分/类别**这些高德真给过的字段
    会以「高德资料：…」的形式兜底（有出处的事实，用于判断地点好不好）。
    """
    facts = ""
    category = (item or {}).get("type")
    # ⚠️ 候选池里的 `type` 在高德没给时会写成占位默认值「风景」—— 那是**我们填充的**，
    #    不能挂在"高德资料："后面当事实（`type_source` 明确标出它是默认值）。
    if str((item or {}).get("type_source") or "").strip().lower() == "default" or (
        not str((item or {}).get("type_source") or "").strip() and str(category or "").strip() == "风景"
    ):
        category = ""
    facts = amap_fact_line(
        category=category,
        rating=(item or {}).get("rating"),
        address=(item or {}).get("address"),
        business_area=(item or {}).get("business_area"),
    )
    return description_of(
        str((item or {}).get("name") or ""),
        web_knowledge=(item or {}).get("web_knowledge"),
        provider_desc=provider_desc if provider_desc is not None else (item or {}).get("desc"),
        city=city,
        facts=facts,
    )


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


# ==========================================
# 2. 算法层：文旅 POI 硬核清洗过滤器（彻底杜绝油漆/手机/建材等非旅游门店）
# ==========================================
# 省级行政区 → 高德检索用的核心城市。高德 place/text 的 city 参数不认省名，
# 传「四川」会返回相邻地市的零星 POI（实测 2026-11-01：德阳/什邡/绵竹一带，
# 3 天方案只剩 1 个景区）。映射表已迁到 core/province_scope.py（省域覆盖也要用它），
# 这里保留同名导出，既有调用方与单测无需改动。
_PROVINCE_SUFFIXES = PROVINCE_SUFFIXES

NON_TOURIST_BLACKLIST = [
    "涂料", "油漆", "建材", "专卖店", "体验店", "手机", "数码", "五金", "营业厅", 
    "汽修", "药房", "大药房", "门诊", "卫浴", "门窗", "窗帘", "家电", "家装", 
    "二手车", "驾校", "物流", "批发", "劳保", "电焊", "钢材", "板材", "石材",
    "通信", "移动", "联通", "电信", "电脑", "维修", "管业", "照明", "灯饰",
    "vivo", "oppo", "华为", "小米之家", "荣耀", "苹果", "三棵树", "立邦", "多乐士",
    "汽车", "电动车", "电池", "百货批发", "建材市场", "装饰", "型材", "门市部", "配件",
    "机电", "机械", "钢构", "化工", "印刷", "包装", "饲料", "兽药", "农资"
]

def resolve_search_city(name: str) -> str:
    """省级行政区 → 用于高德检索的核心城市（省会/直辖市）。

    真实事故（2026-11-01，四川 3 天）：用户填「四川」，旧映射只覆盖了新疆/西藏/甘肃/
    青海/宁夏/内蒙/海南 7 个省级区，于是 city=四川 直接进了高德 place/text，
    返回的是德阳/什邡/绵竹一带的零星 POI —— 3 天方案只剩 1 个景区。
    """
    text = str(name or "").strip()
    if not text:
        return text
    if text in PROVINCE_DEFAULT_CITY:
        return PROVINCE_DEFAULT_CITY[text]
    for suffix in _PROVINCE_SUFFIXES:
        if len(text) > len(suffix) and text.endswith(suffix):
            trimmed = text[: -len(suffix)]
            if trimmed in PROVINCE_DEFAULT_CITY:
                return PROVINCE_DEFAULT_CITY[trimmed]
    return text


def deduplicate_and_diversify_pois(pois: List[Dict]) -> List[Dict]:
    if not isinstance(pois, list):
        return []

    seen_names = set()
    seen_categories = set()
    seen_root_names = set()
    # 👑 类型多样性追踪：确保风景区/文化/美食三类都有充足候选
    type_buckets: Dict[str, List[Dict]] = {"scenic": [], "cultural": [], "food": [], "shopping": [], "other": []}
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
    max_len = max(len(items) for _, items in buckets)
    for i in range(max_len):
        for _, bucket_items in buckets:
            if i < len(bucket_items):
                diversified.append(bucket_items[i])
    # 追加剩余未取完的
    for _, bucket_items in buckets:
        if len(bucket_items) > max_len:
            diversified.extend(bucket_items[max_len:])

    return diversified


# ==========================================
# 3. 算法层：冲突消解（帕累托非支配筛选）与图论寻优
# ==========================================
# 命名说明：类名曾叫 `NashEquilibriumSolver`，但它**不计算纳什均衡**，做的是多目标
# 帕累托非支配筛选（见 core/negotiation_algos.py 的实现与注释）。2026-10 已改名为
# `ParetoFrontierSelector`，避免研究表达与实现不符。
# 已抽取到 core/negotiation_algos.py（2026-10-10）：这两个类与请求/流式状态无关，
# 是纯算法。搬出去后 agent.py 少 ~95 行，且它们可以被单独测试与复用。
# 这里重新导出以保持既有调用方不变（`ParetoFrontierSelector` / `GraphRouteOptimizer`
# 在 agent 命名空间下仍然可用）。
from core.negotiation_algos import GraphRouteOptimizer, ParetoFrontierSelector  # noqa: E402,F401

# 用户输入解析与小工具（2026-10-10 抽取到 core/text_parsing.py，第二批）。
# 这 7 个函数与请求/流式状态无关，AST 实测为零内部依赖，因此可以整批搬走。
# 这里重新导出，既有调用方（含测试与 tools/）无需改动。
from core.text_parsing import (  # noqa: E402,F401
    _fairness_limit_from_env,
    _static_map_url,
    build_reasoning_step,
    extract_preference_signals,
    parse_budget_amount,
    parse_chinese_days,
    safe_repair_and_parse_json,
)


# ==========================================
# 5. 算法层：全方位拓扑适应度计算器（全面防 Null 校验）
# ==========================================
class TopologyFitnessCalculator:
    def __init__(self, user_prefs: dict, city_center_coord: str, evolution_memory: list, safety_intel: Optional[dict] = None, is_luxury: bool = False, preference_signals: Optional[dict] = None, profile_boost: Optional[dict] = None, budget_gamma_scale: float = 1.0, fine_tag_boost: Optional[dict] = None):
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

        rating_val: Any = poi.get("rating")
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
        """联网搜一条地点简介（DuckDuckGo）。

        ⚠️ 必须**校验相关性**（2026-10-05 实测抓到用户可见的编造）：探针跑出来的喀什方案里，
        「胡杨书院」的简介是一段 `Official YouTube TV Help Center where you can find tips and
        tutorials ...` —— 搜索引擎为这条 query 回了一条毫不相干的英文帮助页，而这里**照单全收**。
        取不到 / 不相关就返回**空串**，由调用方如实写「暂无简介」——
        原来这里返回的是"本地高人气热门目的地，融汇了独特的地域人文与招牌风味体验。"
        （一句**没有出处**的赞美，与 (0,0) 坐标同类：看着像事实，其实是编的）。
        """
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
                return clean_web_knowledge(info, poi_name, city)
        except Exception:
            pass
        return ""

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
        """城市/地址 → "lng,lat"（带进程内缓存 + **硬超时 + 快兜底**）。

        为什么要有超时和兜底（2026-10-05 实测，闸门计时抓到的）：
        `gate:geocode/geo=20.0s×2` —— 两次同一个城市的 geocode 占着闸门 **20 秒**
        （`get_coordinates` 在一次推演里被调了两次：先用一次，再作为并发取数的一路）。
        而单独从命令行打同一个 geocode 只要 **0.17~0.36s**，`place/text` 也只要 0.4s。
        既然"为什么在应用里会慢 30 倍"还没有定论，就不要把出方案卡在一个**只是想拿个城市中心点**
        的调用上：给它 2s 的硬期限，超时就用 `place/text`（实测 0.4s）兜底，并把这件事打出来。
        坐标本身是稳定事实，按地址缓存 6 小时，第二次调用不再发请求。
        """
        cache_key = f"coord:{str(address or '').strip()}"
        cached = _cache_get(cache_key)
        if cached:
            return str(cached)
        async with _http_client(3.0) as http_client:
            try:
                params = {"address": address, "key": self.amap_key}
                try:
                    async with AmapGate("geocode/geo"):
                        resp = await asyncio.wait_for(
                            http_client.get(f"{self.amap_url}/geocode/geo", params=params),
                            timeout=2.0,
                        )
                    data = resp.json()
                    if data.get("status") == "1" and data.get("geocodes"):
                        location = str(data["geocodes"][0]["location"])
                        _cache_set(cache_key, location, ttl=6 * 3600)
                        return location
                except asyncio.TimeoutError:
                    # 如实说：不是没查，是查太慢，改用快兜底
                    print(f"⚠️ [地理编码] geocode 超过 2s（地址={address!r}），改用 place/text 兜底", flush=True)
                    _note_fetch("geocode/geo(超时)", 2.0)

                search_params = {"keywords": address, "key": self.amap_key}
                async with AmapGate("place/text(坐标)"):
                    search_resp = await http_client.get(f"{self.amap_url}/place/text", params=search_params)
                search_data = search_resp.json()
                if search_data.get("status") == "1" and search_data.get("pois"):
                    location = str(search_data["pois"][0]["location"])
                    _cache_set(cache_key, location, ttl=6 * 3600)
                    return location
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
            async with _http_client(4.0) as http_client:
                async with AmapGate("geocode/geo(结构化)"):
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

    async def get_sub_districts(self, province: str, *, subdistrict: int = 1) -> List[Dict[str, Any]]:
        """省级行政区 → 下属地州/市清单（name / adcode / center / level）。

        为什么要它（2026-11-01 实测）：高德 `place/text` 的 `city` 参数**不认省名** ——
        传「四川」或 510000 都返回 count=1000，但前 25 条全部在成都各区；而逐地州查是正常的
        （阿坝 311 条、乐山 204 条、九寨沟县 69 条）。所以省域覆盖必须先拿到"全省有哪些地州"，
        再逐地州检索，否则省域就只是"省会 + 周边"。
        行政区划边界基本不变，缓存 30 天；失败如实返回空列表（上层退回按省会检索）。
        """
        if not self.amap_key or not str(province or "").strip():
            return []
        cache_key = f"districts:{str(province).strip()}:{int(subdistrict)}"
        cached = _cache_get(cache_key)
        if isinstance(cached, list) and cached:
            return cached
        try:
            async with _http_client(5.0) as http_client:
                async with AmapGate("config/district"):
                    resp = await http_client.get(
                        f"{self.amap_url}/config/district",
                        params={
                            "key": self.amap_key,
                            "keywords": str(province).strip(),
                            "subdistrict": int(subdistrict),
                            "extensions": "base",
                        },
                    )
                data = resp.json()
        except Exception as exc:
            print(f"⚠️ [省域范围] 地州清单查询失败（{province}）: {type(exc).__name__}: {str(exc)[:80]}", flush=True)
            return []
        if not isinstance(data, dict) or str(data.get("status")) != "1":
            print(
                f"⚠️ [省域范围] {amap_error_note(str(data.get('info') if isinstance(data, dict) else ''), data.get('status') if isinstance(data, dict) else '')}：{province}（不编造地州）",
                flush=True,
            )
            return []
        rows: List[Dict[str, Any]] = []
        for item in data.get("districts") or []:
            if not isinstance(item, dict):
                continue
            # ⚠️ `config/district?keywords=四川&subdistrict=1` 返回的是**包了一层**的结构：
            #    districts[0] 就是「四川省」本身，真正的 21 个地州在它的 districts 里。
            #    实测（2026-11-01，真实请求）：只取外层 → 锚点只有「四川省」1 个 →
            #    全省被当成一个片区，`🗺️ 省域覆盖（5 天）：检索 1 个地州 —— 四川省`，
            #    于是分片/换乘/日内里程全部按"一个片区"算，日行 471km 被判违规又无法自动修复。
            children = item.get("districts")
            nested = [child for child in children if isinstance(child, dict)] if isinstance(children, list) else []
            entries = nested if nested else [item]
            for entry in entries:
                name = str(entry.get("name") or "").strip()
                if not name:
                    continue
                rows.append({
                    "name": name,
                    "adcode": str(entry.get("adcode") or ""),
                    "center": str(entry.get("center") or ""),
                    "level": str(entry.get("level") or ""),
                })
        if rows:
            _cache_set(cache_key, rows, ttl=30 * 24 * 3600)
        return rows

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
            async with _http_client(5.0) as http_client:
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
        _cache_key = _poi_cache_key(city, keywords, types, limit, play_worthy)
        _cached_rows = _poi_cache_get(_cache_key)
        if _cached_rows is not None:
            _POI_CACHE_STATS["hits"] = int(_POI_CACHE_STATS.get("hits") or 0) + 1
            print(f"♻️ [候选缓存] {city} 的这次检索命中缓存（{len(_cached_rows)} 条，TTL {poi_cache_ttl_seconds():.0f}s）", flush=True)
            return _cached_rows
        _POI_CACHE_STATS["misses"] = int(_POI_CACHE_STATS.get("misses") or 0) + 1
        from core.poi_pool import is_play_worthy, poi_record_from_amap
        async with _http_client(6.0) as http_client:
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
                    _search_started = time.monotonic()
                    # 走高德闸门（见 AmapGate 的实测说明）：并发会被限流、更慢，
                    # 而且会静默丢掉整条查询
                    async with AmapGate("place/text"):
                        _fetch_started = time.monotonic()
                        _note_fetch("place/text(排队)", _fetch_started - _search_started)
                        resp = await http_client.get(f"{self.amap_url}/place/text", params=params)
                        data = resp.json()
                        _note_fetch("place/text(请求)", time.monotonic() - _fetch_started)
                    if str(data.get("status")) == "1":
                        note_amap_result(True)
                        break
                    info = str(data.get("info") or "")
                    # 把结果告诉闸门：被限流就自动退避（下一次间隔翻倍），
                    # 否则"固定 0.15s"会在 key 的 QPS 变紧时继续硬撞、静默丢整条查询
                    note_amap_result(False, info=info)
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
                    # 👑 实景图增强：对未返回实拍照片但含高德 ID 的 POI，按 ID 补齐真实照片。
                    #    ⚠️ 三个约束都是实测逼出来的（2026-10-05）：这一批原来一次并发 12 个
                    #    `place/detail`，5 个查询就是 ~60 个**不受闸门约束**的并发请求，
                    #    把同一把 key 的 QPS 顶爆 —— 后果是同一时刻的 `place/text` 每只要等 4.7s
                    #    （闸门计时：`gate:place/text=23.4s×5`，而单发只要 0.4s）。
                    #    所以：① 也走闸门（不再有突发）；② 按 POI id 缓存（同一个 POI 常出现在
                    #    多个查询里，不必重复问）；③ 每查询上限从 12 降到 8。
                    no_photo_pois = [p for p in pois if not p.get("photos") and p.get("id")]
                    if no_photo_pois:
                        try:
                            detail_ids = []
                            for _item in no_photo_pois:
                                _pid = str(_item["id"])
                                if _pid not in detail_ids:
                                    detail_ids.append(_pid)
                            detail_ids = detail_ids[:8]
                            id_to_photos: Dict[str, Any] = {}
                            for pid in detail_ids:
                                cached_photos = _AMAP_PHOTO_CACHE.get(pid)
                                if cached_photos is not None:
                                    id_to_photos[pid] = list(cached_photos)
                            _todo = [pid for pid in detail_ids if pid not in id_to_photos]
                            _note_fetch("place/detail(缓存命中)", 0.0, len(detail_ids) - len(_todo))
                            if _todo:
                                _detail_started = time.monotonic()
                                detail_tasks = [
                                    self._amap_get(
                                        http_client,
                                        f"{self.amap_url}/place/detail",
                                        params={"key": self.amap_key, "id": pid},
                                    )
                                    for pid in _todo
                                ]
                                detail_resps = await asyncio.gather(*detail_tasks, return_exceptions=True)
                                _note_fetch("place/detail(补图)", time.monotonic() - _detail_started, len(_todo))
                                for pid, dresp in zip(_todo, detail_resps):
                                    if isinstance(dresp, BaseException):
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
                                                # 同一个 POI 在别的查询里还会再出现，缓存起来不再问
                                                # （容量/淘汰/命中率由 BoundedTTLCache 统一处理）
                                                _AMAP_PHOTO_CACHE.set(str(pid), list(urls[:3]))
                                    except Exception:
                                        continue
                            for p in pois:
                                if not p.get("photos") and p.get("id") in id_to_photos:
                                    p["photos"] = id_to_photos[p["id"]]
                        except Exception as e:
                            print(f"🔥 [实景图增强异常]: {e}")
                    # 只缓存**成功且非空**的结果：失败/空结果不缓存，
                    # 否则一次限流会被固化半小时（"这里没有 POI"的假象）。
                    # ⚠️ 原来这里**无条件** `_poi_cache_set`（注释写着"只缓存非空"、代码没做）——
                    #    限流/报错时 `pois` 是空列表，于是被当成"这城市没有 POI"缓存 30 分钟，
                    #    后续所有相同查询都拿到空结果（实测：探针连续几轮全 0 条）。
                    if pois:
                        _poi_cache_set(_cache_key, pois)
                    return pois
            except Exception as e:
                print(f"🔥 [POI抓取异常]: {e}")
        return []

    async def get_nearby_pois(
        self,
        center: Sequence[float],
        keywords: str = "",
        *,
        radius_km: float = 10.0,
        limit: int = 10,
        play_worthy: bool = False,
    ) -> List[Dict]:
        """**围绕一个坐标**找 POI（高德 `place/around`）—— 专治"远点那天没有吃住"。

        为什么要它（2026-10-07 实测）：喀什第 3 天在英吉沙/巴楚方向，而候选池是按**全城**
        取的，餐饮候选全在市区 —— 最近的一家也在 82.6km 外，于是"缺餐"只能靠挪市区饭点
        （82.6km，等于没补）或者留空。伊宁同样：吃住离当天玩点 17~38km（`anchoring=0.167`）。
        按天做一次周边检索，就能拿到"当天玩点附近真实存在的餐馆/酒店"。

        规矩与其他取数一致（否则会静默把候选变少）：
          · 走高德闸门 `AmapGate`（并发会被限流，实测同一个 key）；
          · 失败**如实打印**并按供应商错误分类（限流可重试），不伪装成"这里没有"；
          · 结果按**离中心距离**排序（越近越靠前），空结果不缓存。
        """
        if not self.amap_key or not isinstance(center, (list, tuple)) or len(center) < 2:
            return []
        try:
            lng, lat = float(center[0]), float(center[1])
        except (TypeError, ValueError):
            return []
        if lng == 0.0 and lat == 0.0:
            return []
        location = f"{lng:.6f},{lat:.6f}"
        # 坐标进缓存键（同一个城市不同位置的结果完全不同）—— 取整到 ~1km 以复用
        rounded = f"{lng:.2f},{lat:.2f}"
        _cache_key = f"around|{rounded}|{str(keywords).strip()}|{int(radius_km)}|{int(limit)}|{bool(play_worthy)}"
        _cached_rows = _poi_cache_get(_cache_key)
        if _cached_rows is not None:
            _POI_CACHE_STATS["hits"] = int(_POI_CACHE_STATS.get("hits") or 0) + 1
            return _cached_rows
        _POI_CACHE_STATS["misses"] = int(_POI_CACHE_STATS.get("misses") or 0) + 1

        from core.poi_pool import is_play_worthy, poi_record_from_amap

        params: Dict[str, Any] = {
            "key": self.amap_key,
            "location": location,
            "radius": max(100, int(float(radius_km) * 1000)),
            "offset": max(1, int(limit)),
            "page": 1,
            "sortrule": "distance",
            "extensions": "all",
        }
        if str(keywords or "").strip():
            params["keywords"] = str(keywords).strip()
        pois: List[Dict[str, Any]] = []
        async with _http_client(6.0) as http_client:
            data: Dict[str, Any] = {}
            for attempt in range(2):
                # ⚠️ **不要再套一层 `AmapGate`**：`_amap_get` 内部已经走闸门，
                #    而闸门是**不可重入**的锁 —— 同一个任务套两层会直接死锁
                #    （实测：探针卡住 5 分钟没有任何输出，就是这么来的）。
                try:
                    resp = await asyncio.wait_for(
                        self._amap_get(http_client, f"{self.amap_url}/place/around", params=params),
                        timeout=8.0,
                    )
                    data = resp.json()
                except Exception as _around_exc:
                    note_amap_result(False, info=f"{type(_around_exc).__name__}")
                    print(f"⚠️ [周边检索] 请求失败：{type(_around_exc).__name__}: {str(_around_exc)[:80]}", flush=True)
                    break
                if str(data.get("status")) == "1":
                    note_amap_result(True)
                    break
                info = str(data.get("info") or "")
                note_amap_result(False, info=info)
                if attempt == 0 and amap_error_is_retryable(info):
                    await asyncio.sleep(1.2)
                    continue
                print(
                    f"⚠️ [周边检索] {amap_error_note(info, data.get('status'))}："
                    f"center={location} keywords={keywords!r}（不编造候选）",
                    flush=True,
                )
                break
            if data.get("status") == "1" and data.get("pois"):
                for poi in data["pois"]:
                    if not isinstance(poi, dict):
                        continue
                    record = poi_record_from_amap(poi, city="", amap_key=self.amap_key or "")
                    name = str(record.get("name") or "")
                    if not name:
                        continue
                    if play_worthy and not is_play_worthy(name, record.get("type"), strict_type=False):
                        continue
                    pois.append(record)
        # 按离中心的真实距离排序（高德给了 sortrule=distance，但跨页/缺坐标时仍要自己兜住）
        try:
            from core.plan_quality import haversine_km as _haversine_km

            center_coord = (float(lng), float(lat))

            def _distance_to_center(item: Mapping[str, Any]) -> float:
                raw_coord = item.get("lnglat")
                if not raw_coord:
                    return 0.0
                try:
                    item_lng, item_lat = float(raw_coord[0]), float(raw_coord[1])
                except (TypeError, ValueError, IndexError, KeyError):
                    return 0.0
                return float(_haversine_km(center_coord, (item_lng, item_lat)) or 0.0)

            pois.sort(key=_distance_to_center)
        except Exception:
            pass
        if pois:
            _poi_cache_set(_cache_key, pois)
        return pois

    async def get_traffic_status(self, location_coord: str) -> Dict[str, Any]:
        if not location_coord:
            return {"status_code": "unknown", "description": "暂无供应商数据", "advice": "缺少坐标，无法获取实时路况", "source": "unavailable", "estimated": True}

        cache_key = f"traffic:{location_coord}"
        cached = _cache_get(cache_key)
        if cached:
            return cached

        async with _http_client(3.0) as http_client:
            params = {"key": self.amap_key, "location": location_coord, "radius": 5000, "level": 5}
            try:
                resp = await self._amap_get(http_client, f"{self.amap_url}/traffic/status/circle", params=params)
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
        coords: List[List[float]] = []
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
        # 👑 出稿后的路网拼装实测要 48.4s（喀什 3 天 15 个节点 → 15 对），而这段时间
        #    **算在用户等多久里**（服务端请求要等它跑完才结束）。三条优化，都是为了这段：
        #    1. 复用同一个连接池：原先每次调用都新建 AsyncClient（15 对 = 15 次 TCP/TLS 握手）；
        #    2. 有界并发（信号量）：一次性发 45 个请求会被高德 QPS 限流，
        #       表现成"大家排队等到超时"，比少发一点更慢；
        #    3. 只问有意义的出行方式：3km 以上不必问步行（走了也不现实），
        #       40km 以上不必问市内公交；双城/跨城腿本来也不该走公交。
        #    另外按坐标对做进程内缓存：同一对在跨城腿比较与出稿拼装里都可能被问到。
        _pair_key = f"{origin_lnglat}|{dest_lnglat}|{city}"
        _cached = _TRAVEL_OPTIONS_CACHE.get(_pair_key)
        if _cached is not None:
            return dict(_cached)

        _straight_km = _straight_line_km(origin_lnglat, dest_lnglat)
        async with _TRAVEL_OPTIONS_SEMAPHORE:
            async with _http_client(3.5) as http_client:
                tasks = [
                    # 策略 10（高德默认）：躲避拥堵 + 路程较短 + 尽量缩短时间，返回多条路线取 paths[0]，
                    # 避免 strategy=0（速度优先）在往返时因高速/环线绕出明显更远的“理论最快”路线
                    self._amap_get(http_client, f"{self.amap_url}/direction/driving", params={"key": self.amap_key, "origin": origin_lnglat, "destination": dest_lnglat, "strategy": 10}),
                ]
                names = ["driving"]
                if _straight_km is None or _straight_km <= 3.0:
                    tasks.append(self._amap_get(http_client, f"{self.amap_url}/direction/walking", params={"key": self.amap_key, "origin": origin_lnglat, "destination": dest_lnglat}))
                    names.append("walking")
                if city and (_straight_km is None or _straight_km <= 40.0):
                    tasks.append(self._amap_get(http_client, f"{self.amap_url}/direction/transit/integrated", params={"key": self.amap_key, "origin": origin_lnglat, "destination": dest_lnglat, "city": city, "cityd": city}))
                    names.append("transit")
                return await self._collect_travel_options(http_client, tasks, names, _pair_key)

    @staticmethod
    async def _amap_get(http_client, url: str, **kwargs):
        """走**全局高德闸门**的 GET（见 AmapGate 的实测说明）。

        凡是打到高德同一把 key 的请求都要走它：并发不但被服务端排队得更慢，
        还会有一部分被限流成 status=0、**静默返回空**（路线/天气/路况会凭空缺失）。
        """
        _kind = url.rsplit("/", 1)[-1] or "amap"
        async with AmapGate(_kind):
            response = await http_client.get(url, **kwargs)
        # 让闸门知道这一条成没成（限流就退避）：响应体已被 httpx 读完并缓存，
        # 调用方后面再 `.json()` 不受影响。
        try:
            _payload = response.json()
            note_amap_result(
                str(_payload.get("status")) == "1", info=str(_payload.get("info") or "")
            )
        except Exception:
            pass
        return response

    async def _collect_travel_options(self, http_client, tasks, names, cache_key: str) -> Dict[str, Any]:
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            travel_info: Dict[str, Any] = {}
            for name, resp in zip(names, results):
                if isinstance(resp, BaseException) or not resp:
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
            # 只缓存**有结果**的对：空结果不缓存，避免把一次限流的失败固化 6 小时
            if travel_info:
                # 容量上限 512 + LRU 淘汰（不再整体 clear，避免把仍在命中的热点一起丢掉）
                _TRAVEL_OPTIONS_CACHE.set(cache_key, dict(travel_info))
            return travel_info
        except Exception:
            return {}


    async def get_amap_weather(self, city: str) -> Dict[str, Any]:
        """对接高德天气 API（weatherInfo），返回实时天气+未来3天预报；失败返回空兜底由上层回退。"""
        if not self.amap_key:
            return {}
        cache_key = f"amap_weather:{city}"
        cached, cache_age = _cache_get_with_meta(cache_key)
        if cached:
            # 如实标注"这是缓存"：天气卡片要显示采集时间，不能把一小时前取的天气说成刚取的
            return {**cached, "cached": True, "cache_age_seconds": round(float(cache_age or 0.0), 1)}

        try:
            async with _http_client(3.5) as http_client:
                geo_resp = await self._amap_get(http_client, f"{self.amap_url}/geocode/geo", params={"address": city, "key": self.amap_key})
                geo_data = geo_resp.json()
                adcode = ""
                if geo_data.get("status") == "1" and geo_data.get("geocodes"):
                    adcode = str(geo_data["geocodes"][0].get("adcode") or "")
                if not adcode:
                    return {}

                weather_resp = await self._amap_get(
                    http_client,
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
                    result = {
                        "condition": condition or "暂无供应商数据",
                        "forecast": forecast,
                        "source": "amap",
                        "estimated": False,
                        # 采集时间：外部数据必须能被追问"你是什么时候知道的"
                        "fetched_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                        "cached": False,
                    }
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
                async with _http_client(3.5) as http_client:
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
                        return {
                            "condition": condition or "暂无供应商数据",
                            "forecast": forecast,
                            "source": "seniverse",
                            "estimated": False,
                            "fetched_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                            "cached": False,
                        }

            except Exception:
                pass

        # Never synthesize current weather when every supplier is unavailable.
        # Callers can keep planning with an explicit unknown/estimated state.
        return {
            "condition": "暂无供应商数据",
            "forecast": [],
            "source": "unavailable",
            "estimated": True,
            "fetched_at": None,
            "cached": False,
        }


# ==========================================
# 6.5 🚀 LLM 语义缓存层：embedding 相似度 > 阈值直接复用，Redis 共享缓存避免多实例重复推理
# ==========================================
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


# 种子模板的"同城"半径：超出这个范围的景点已不属于市内当天往返行程。
# 继续塞进 3 日路线，用户照着走会发现根本执行不了（例如成都模板里的九寨沟）。
SEED_MAX_RADIUS_KM = 150.0


def _build_seed_final_data(city: str, trip_days: int) -> Optional[Dict[str, Any]]:
    """将热门城市种子地标展开为 trip_days 天的完整 route 节点（冷启动首屏兜底）。

    两条硬规则：

    * **不重复**：同一个地标在一次行程里只出现一次。此前节点不够就取模循环复用，
      于是"3 日游"里故宫会连着出现两天 —— 用户照着走会发现第二天无处可去。
    * **同城半径**：以模板首站为城市锚点，超过 ``SEED_MAX_RADIUS_KM`` 的景点剔除，
      并在 ``seed_note`` 里写明。宁可在某天少排一个节点，也不拿跨城景点冒充市内行程。
    """
    seed = HOT_CITY_SEED_ROUTES.get(city)
    if not seed:
        return None
    amap_key = os.getenv("AMAP_API_KEY")
    anchor = seed["nodes"][0][1]
    available = []
    dropped: List[str] = []
    for name, lnglat, desc in seed["nodes"]:
        try:
            distance = float(haversine_km(anchor, lnglat))
        except Exception:
            distance = 0.0
        if distance > SEED_MAX_RADIUS_KM:
            dropped.append(name)
            continue
        available.append((name, lnglat, desc))
    if not available:
        return None

    trip_days = max(1, int(trip_days))
    # 轮转分配：每个地标只用一次；地标少于天数时，后面的天留空而不是重复。
    day_counts = [0] * trip_days
    for index in range(len(available)):
        day_counts[index % trip_days] += 1

    route: List[Dict[str, Any]] = []
    slot = 0
    for day in range(1, trip_days + 1):
        for position in range(day_counts[day - 1]):
            name, lnglat, desc = available[slot]
            slot += 1
            route.append({
                "day": day,
                "name": name,
                "location": name,
                "lnglat": list(lnglat),
                "type": "风景",
                "desc": desc,
                "time": ["09:00", "11:00", "14:00", "17:30"][position % 4],
                "transport": "打车/自驾",
                "tags": ["经典必游", "预生成模板"],
                # 种子模板只有名称/坐标/简介，没有接供应商：评分/价格/营业时间**留空**，
                # 由下面的 supplier_data_gaps 统一说明（不再每站重复"暂无供应商数据"）。
                "cost_estimate": "",
                "photos": [],
                "amap_url": f"https://www.amap.com/search?query={urllib.parse.quote(f'{city} {name}')}",
                "map_image": _static_map_url(list(lnglat), amap_key),
                "rating": "",
                "open_time": "",
                "data_sources": {"cost_estimate": "seed_template", "rating": "seed_template", "open_time": "seed_template"},
                "estimated": True,
                "is_hotel": False,
                "trust_reason": "热门城市预生成种子模板（首屏秒回）",
            })

    notes: List[str] = []
    if dropped:
        notes.append(
            f"已剔除超出 {int(SEED_MAX_RADIUS_KM)}km 同城半径的地标：{'、'.join(dropped)}"
        )
    empty_days = sum(1 for count in day_counts if count == 0)
    if empty_days:
        notes.append(
            f"可用地标仅 {len(available)} 个，{empty_days} 天未排景点（不重复使用同一地标）"
        )
    result: Dict[str, Any] = {
        "status": "seed_template",
        "negotiation_summary": seed["summary"],
        "route": route,
        "recommended_pois": [],
    }
    # 种子模板没接供应商：评分/开放时间/价格统一在一处说明缺什么（而不是每站写一行占位）。
    seed_gap = supplier_facts_disclosure(
        [{"name": node["name"], "fields": list(SUPPLIER_FACT_FIELDS)} for node in route]
    )
    if seed_gap:
        result["supplier_data_gaps"] = seed_gap
    if notes:
        result["seed_note"] = "；".join(notes)
        result["dropped_nodes"] = dropped
    return result


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
        # 缓存命中率排查开关（默认关）：打开后会打印参与哈希的键原文
        self.debug = str(os.getenv("SEMANTIC_CACHE_DEBUG") or "").strip() in {"1", "true", "on", "yes"}
        # 容量上限（默认 128 份方案）：以前 `_mem` 是裸 dict，且**从不判断 TTL** ——
        # Redis 那份会给 `ex`，进程内存这份却永不过期，长跑进程会一直命中过期方案。
        # 现在内存与向量索引都走有界 TTL 缓存：TTL 到期即失效，超限丢最久未用的。
        try:
            max_entries = int(os.getenv("SEMANTIC_CACHE_MAX_ENTRIES", "128") or 128)
        except ValueError:
            max_entries = 128
        self._mem: BoundedTTLCache = register_cache(
            BoundedTTLCache("semantic_cache", max_entries=max(1, max_entries), ttl_seconds=float(self.ttl))
        )
        self._vec_index: BoundedTTLCache = register_cache(
            BoundedTTLCache("semantic_vectors", max_entries=max(1, max_entries), ttl_seconds=float(self.ttl))
        )

    def _key(self, city: str, days: int, profile_text: str) -> str:
        # 👑 版本号参与哈希：修复生成逻辑后 bump 版本即可让旧缓存（缺美食/酒店）自动失效
        # v4 invalidates plans cached before the final daily-balance gate.
        # v5：类别/原始诉求进入精确缓存键，避免同城同天数的文化路线
        # 污染自然、美食、购物或普通均衡路线。
        # v6：模型名进入精确缓存键。理由（2026-10-05 实测）：同一句诉求在 qwen-plus
        # 与 qwen-turbo 下跑出来的路线**完全一致**、但耗时差一倍；如果模型不进键，
        # 换了模型之后用户拿到的还是旧模型缓存的那份方案 —— "切换模型"这件事在
        # 真实验收里就不可观测了（冷跑会被缓存秒回掩盖）。换模型即失效，代价只是
        # 重算一次，换来的是"所见即本次模型产出"。
        # v7：缓存里装的是**整份 payload**（含 `route` 的坐标、`quality` 的分数与明细），
        #    而这两块刚修了两个"结论早于最终路线"的错（缺坐标住宿补的坐标没写回路线、
        #    质量分在收口前就算完了）。旧条目里的这两项**已知是错的**，继续命中就是拿
        #    错数据骗人（实测：同样的诉求秒回 394ms，日志里没有「📐 [质量重算]」）。
        # v8：`desc`（地点简介）口径改了 —— 联网抓错页面的文字不再顶上，没出处就写「暂无简介」。
        #    旧条目里存着 YouTube 帮助中心、"中医认为酸奶有益健康"这类**假简介**，
        #    命中它们就是继续把编造当介绍渲染给用户。
        # v9：`desc` 再进一层（联网取不到 → 用**高德字段**拼「高德资料：类别｜评分｜地址」），
        #    同时把候选池里被丢掉的 `address` 带进节点。旧条目里地址是空的、简介是"暂无"，
        #    看着像"我们不知道"，其实高德给过 —— 这类"看起来像事实的缺失"不该被缓存固化。
        # v10：取价链路换了（搜索源重排 + 门票专线 + 票务平台价）。旧条目里一整片"未核实"
        #    的价格，现在有一部分是查得到的 —— 命中旧条目会让用户继续看到过时的"查不到"。
        # v11：**「只看自然就别排人文」的用户裁决**（2026-10-07）改了信号/配比/评分三处口径：
        #    同一句"想看自然风景"以前会排 2 个人文点、多样性被打 0.426，现在不该再有人文点。
        #    旧条目正是按老口径生成的，命中就是把已经改掉的错继续发给用户。
        raw = f"v11|{MODEL_NAME}|{city}|{days}|{''.join(str(profile_text or '').split())}"
        if self.debug:
            # 缓存命中率低时，第一件要知道的事就是"键为什么不一样"：
            # 把参与哈希的原文打出来（只在本进程日志里），两次同样的请求一比就知道。
            print(f"🔑 [语义缓存] key={hashlib.md5(raw.encode('utf-8')).hexdigest()[:10]} raw={raw[:200]}", flush=True)
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
            self._mem.set(key, hit)  # 从 Redis 回填内存（同样按 TTL 过期）
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
        self._mem.set(key, payload)
        await self._redis_set(key, payload)
        # fire-and-forget 补充 embedding 向量索引（best-effort，不阻塞主推演流程）
        asyncio.ensure_future(self._index_embedding(key, city, days, profile_text))

    async def _index_embedding(self, key: str, city: str, days: int, profile_text: str):
        try:
            vec = await self._embed(profile_text)
            if vec is not None:
                self._vec_index.set(key, {"city": city, "days": days, "vector": vec})
        except Exception as e:
            print(f"⚠️ [语义缓存] 向量索引建立跳过: {e}")


_semantic_cache = SemanticItineraryCache()


# ==========================================
# 7. 路由层：主推演协商 API 端点（提高节点密度 + 动态消费 + 严格多天输出）
# ==========================================
DESTINATION_UNRESOLVED_CODE = "DESTINATION_UNRESOLVED"

# 中国大陆粗略经纬度范围（含海南，不含港澳台/海外）。用于拦下"识别成了城市、
# 但坐标落在境外"的情况（例如把「大阪」当成一个中文地名送到高德）。
_MAINLAND_LNG_RANGE = (73.0, 135.1)
_MAINLAND_LAT_RANGE = (18.0, 53.6)


def _pick_explicit_destination(payload: Dict[str, Any], intent_text: str) -> str:
    """取出用户**明确写下**的目的地（结构化 destinations 优先，其次「目的地：X」）。

    只用于"解析失败要不要显式报错"这个判断：没写目的地时不能因为兜底城市就报错。
    """
    current = payload.get("current_request") if isinstance(payload, dict) else None
    if isinstance(current, dict):
        destinations = current.get("destinations")
        if isinstance(destinations, list):
            for item in destinations:
                text = str(item or "").strip()
                if text:
                    return text
    match = re.search(r"目的地[：:]\s*([^；;，,。\n]+)", str(intent_text or ""))
    return match.group(1).strip() if match else ""


def _coord_in_mainland(coord: str) -> bool:
    """坐标是否落在中国大陆范围内（格式 "lng,lat"）。解析不出就当作不合法。"""
    parts = str(coord or "").split(",")
    if len(parts) != 2:
        return False
    try:
        lng, lat = float(parts[0]), float(parts[1])
    except ValueError:
        return False
    return (
        _MAINLAND_LNG_RANGE[0] <= lng <= _MAINLAND_LNG_RANGE[1]
        and _MAINLAND_LAT_RANGE[0] <= lat <= _MAINLAND_LAT_RANGE[1]
    )


def _destination_error_stream(requested: str, resolution: str):
    """目的地解析失败时的显式错误流（NDJSON）。

    为什么不再"兜底出方案"（2026-11-03 实测）：目的地填 "Osaka" 时中文规则全不命中，
    引擎静默兜底成武威并照常出方案 —— 用户打开历史行程看到「Osaka 探索路书」，
    正文却全是武威。宁可明确失败，也不要给一份不相干的路线。
    """
    if resolution == "fallback_default":
        message = (
            f"没能识别目的地「{requested}」。当前路书只覆盖中国大陆城市，"
            "请改用中文城市名（如：成都、杭州、西安）。"
        )
    else:
        message = (
            f"定位不到目的地「{requested}」或它不在中国大陆范围内。"
            "请确认城市名是否正确（当前只覆盖中国大陆城市）。"
        )
    yield json.dumps(
        {"type": "error", "payload": {"code": DESTINATION_UNRESOLVED_CODE, "message": message}},
        ensure_ascii=False,
    ) + "\n"


def _friendly_llm_error(err: Optional[BaseException]) -> str:
    """把大模型调用异常翻译成**用户能看懂、能照做**的中文提示。

    实测（2026-11-02）：LLM 端点连不上时，降级摘要直接把底层异常原文抛给用户
    （「大模型服务暂时不可用（Connection error.）」）—— 用户既不知道发生了什么，
    也不知道下一步做什么。这里只给三档可操作提示，原始异常由调用点 `print` 进日志。
    """
    name = type(err).__name__.lower() if err is not None else ""
    text = str(err or "").lower()
    network = ("connection", "connect", "timeout", "timed out", "network", "unreachable")
    # 阿里云百炼欠费会返回 400 Arrearage / overdue-payment，既不是网络错误也不是
    # 普通 401。若不单独识别，它会落到「服务暂时不可用」这种无法照做的提示里。
    if any(key in text for key in ("arrearage", "overdue", "good standing", "insufficient balance")):
        return "AI 模型账户欠费或额度不足，请联系管理员充值或更换 API Key 后重试"
    if (name and any(key in name for key in network)) or any(key in text for key in network):
        return "网络连接失败，请稍后重试"
    if (name and any(key in name for key in ("auth", "permission", "unauthorized"))) or "401" in text or "api key" in text:
        return "服务鉴权失败，请联系管理员检查模型配置"
    if (name and any(key in name for key in ("ratelimit", "quota", "insufficient"))) or any(
        key in text for key in ("429", "quota", "insufficient", "欠费", "余额")
    ):
        return "服务限流或额度不足，请稍后重试或联系管理员"
    return "服务暂时不可用，请稍后重试"


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

    # ⏱️ 分段耗时记号（2026-10-05）：横向对照发现单次推演从 57–90s 涨到 139–191s，
    #    但"慢在哪一段"只能靠猜。这里在几个关键节点打点，出方案时打一行汇总
    #    （纯观测，不改任何行为；`work/plan-quality-matrix.md` 的耗时才有得对照）。
    import time as _stage_time

    reset_fetch_timing()
    reset_pool_cache_stats()
    # 闸门退避状态**不重置**（它是跨请求的进程级状态：这把 key 的 QPS 档位是外部事实，
    # 上一次被限流说明下一条请求也该慢一点），但每次请求记下**本轮**的限流次数，
    # 好让方案里如实说"这一轮被限流过几次"（而不是只报一个进程累计值）。
    _amap_limits_at_start = int(amap_gate_state().get("limits") or 0)
    _stage_marks: Dict[str, float] = {"开始": _stage_time.monotonic()}

    def _stage(name: str) -> None:
        _stage_marks[name] = _stage_time.monotonic()

    def _stage_summary() -> str:
        names = list(_stage_marks.keys())
        parts = []
        for index in range(1, len(names)):
            parts.append(f"{names[index]}={_stage_marks[names[index]] - _stage_marks[names[index - 1]]:.1f}s")
        total = _stage_marks[names[-1]] - _stage_marks[names[0]]
        return " ".join(parts) + f" | 总计={total:.1f}s"

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
    # 👑 「个性化」这一项只认**发起人自己**说的话（本轮 + 历史轮）—— 用户裁决（2026-10-05）：
    #    房间成员（受邀同行的其他人）的诉求走**公平性/满意度**维度，不进"个性化"评分。
    #    为什么要单独一份：评分原来用的是 `pref_signals`（把成员的话也算上），于是
    #    "用户本人没提的东西"会拉低他自己的方案分。实测（喀什 turbo26）：
    #      · 发起人原话：「去喀什玩3天，想多看人文历史和古城」→ requirements=["文化历史"]；
    #      · 探针里的成员占位意图「步调宽松，安排特色茶社或公园慢游」把 requirements 撑成
    #        ["自然风景","文化历史","购物商圈","城市公园/乐园"] → 方案只满足 3/4 →
    #        `preference_coverage=0.75`、总分 78.9；
    #      · 而配比与检索口径**只看发起人**（`own_signals`）—— 两边打架，评分在替成员扣分。
    #    ⚠️ 生成阶段（提示词）仍然把成员偏好带上：那是"尽量照顾一屋子人"，
    #       成员到底满不满意由 fairness / team_satisfaction 那几项如实报。
    self_text_context = " | ".join(history_sequence) + " " + intent_str
    scoring_signals = extract_preference_signals(self_text_context)
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
    # 识别不出城市时的兜底。**显式命名**，不再靠 `common_cities[0]` 的列表顺序 ——
    # 否则以后有人调整数组顺序（比如把喀什提前）就会**悄悄改掉**默认城市。
    DEFAULT_CITY = "武威"
    city_resolution = "unresolved"

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

        # 👑 省级名（四川/新疆/西藏…）也要能识别：用户不一定说"去四川玩"，
        #    实测（2026-11-01）"规划四川4天自然风光行程" 三条规则全没命中 → 兜底成武威，
        #    用户在诉求里明明写了省份，却拿到一份甘肃武威的方案（而省域覆盖根本没机会触发）。
        for _province_name in PROVINCE_DEFAULT_CITY:
            if _province_name and _province_name in str(text):
                candidate_city = _province_name
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
        city_resolution = "matched"
    else:
        # ⚠️ 兜底城市（2026-10-08 查）：以前直接写 `target_city = "武威"`，
        #    而"武威"只是 `common_cities` 数组的**第 0 个元素** —— 兜底值等于列表顺序的副作用。
        #    危险在于它**静默**发生：诉求里没提城市时，系统会一本正经地规划武威，
        #    用户完全不知道自己拿到的是个默认值（实测就撞上：诉求提了喀什但没进 intent_str，
        #    最终产出武威行程）。
        #    现在：保留这个默认（避免检索直接失败），但**记下这是兜底**，
        #    由上层如实告知用户"没识别出目的地"，而不是假装这是用户要的城市。
        target_city = DEFAULT_CITY
        city_resolution = "fallback_default"

    # 👑 省级行政区自动收敛到省会/核心城市，保证高德检索能命中真实 POI
    _province_input = target_city
    target_city = resolve_search_city(target_city)
    if target_city != _province_input:
        print(f"🏙️ [目的地] 省级行政区收敛到检索城市：{_province_input} → {target_city}", flush=True)

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

    # 明确写下了目的地却解析不出来 → 显式失败，不再拿兜底城市出一份不相干的方案。
    # 注意：必须发生在缓存/种子命中之前，否则旧缓存会把错误目的地"救活"。
    explicit_destination = _pick_explicit_destination(payload, intent_str)
    coord = await toolbox.get_coordinates(target_city)
    if explicit_destination and (
        city_resolution == "fallback_default" or not _coord_in_mainland(coord)
    ):
        print(
            f"⚠️ [目的地] 无法解析 {explicit_destination!r}（识别结果={target_city!r} / "
            f"resolution={city_resolution} / coord={coord!r}），显式失败",
            flush=True,
        )
        return StreamingResponse(
            _destination_error_stream(explicit_destination, city_resolution),
            media_type="application/x-ndjson",
        )
    coord = coord or "102.63,37.93"
    current_mode = mode
    user_role = role
    evolution_memory = payload.get("evolution_memory", [])
    if not isinstance(evolution_memory, list):
        evolution_memory = []

    # 👎 节点级负反馈（用户点"不喜欢"时写的原因）→ **真的会被执行**的信号。
    # 用户口径（2026-10-05）：「如果用户点击不喜欢，或者二次增量不想去这么远的地方，
    # 就按照用户的意图来」。此前这条链路只写了 FeedbackLog + 把**地点名的细标签**记进
    # 画像（嫌"太远"被读成"不喜欢山岳"），排除名单也只由**这一轮打的字**解析 ——
    # 用户不再打字，等于没说过。这里补上：点名的地点进排除名单（走 apply_exclusions
    # 剔除+同类替换），原因说"太远"就把距离口径压成 low（远点会被日内修复移出）。
    try:
        from core.feedback_signals import merge_feedback_into_signals, summarize_feedback

        feedback_signals = summarize_feedback(evolution_memory, city=target_city)
        feedback_exclude_names: List[str] = list(feedback_signals.get("exclude_names") or [])
        if feedback_signals.get("notes"):
            print(
                f"👎 [反馈信号] {feedback_signals.get('counts')}｜"
                + "；".join(str(note) for note in feedback_signals["notes"]),
                flush=True,
            )
        # 用户**这一轮亲口说的话优先**：他说了"不介意跑远"就不被历史反馈翻回去；
        # 他这轮没表态（normal）才让反馈把口径压成"就近"。
        own_signals = merge_feedback_into_signals(own_signals, feedback_signals)
        pref_signals = merge_feedback_into_signals(pref_signals, feedback_signals)
        # 反馈是**发起人自己**点的/写的，所以"个性化"评分信号也吃反馈
        scoring_signals = merge_feedback_into_signals(scoring_signals, feedback_signals)
    except Exception as _feedback_exc:  # 反馈解析失败绝不影响出方案
        feedback_signals = {"notes": [], "exclude_names": [], "counts": {}}
        feedback_exclude_names = []
        print(f"⚠️ [反馈信号] 解析失败（不影响出方案）: {_feedback_exc}", flush=True)

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
        # 负反馈也进缓存键：否则用户刚标了"不喜欢帕米尔旅游景区"，
        # 下一次请求会被**含这个点的旧缓存**秒回，看起来像反馈没生效。
        "feedback:" + json.dumps(
            {
                "exclude": sorted(feedback_exclude_names),
                "tolerance": str((own_signals or {}).get("distance_tolerance") or "normal"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    ])

    async def cached_stream(cached_payload: dict):
        cb = cached_payload.get("budget_breakdown") or budget_breakdown_payload
        cfd = cached_payload.get("final_data") or {}
        ctc = cached_payload.get("target_city") or {}
        # 老缓存/种子模板里还留着"暂无供应商数据"的占位（2026-11-03）：输出前统一清掉，
        # 否则命中缓存时又在界面上满屏重复 —— 缓存不该绕过数据质量口径。
        _cached_fact_cleared = clear_placeholder_supplier_facts(cfd, copy=False)
        _cached_fact_gap = supplier_facts_disclosure(_cached_fact_cleared)
        if _cached_fact_gap:
            cfd["supplier_data_gaps"] = _cached_fact_gap
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

                # ⚠️ 必须用**和出稿门禁同一个**刻度：出稿用的是
                #    `target_daily_count if role == "深度探索" else min(target_daily_count, 5)`；
                #    缓存这边原来拿 `target_daily_count` 去卡，比出稿门禁还严 ——
                #    于是系统自己刚产出的方案都被判"每日密度不达标"，缓存等于关掉。
                _cache_min_daytime = target_daily_count if role == "深度探索" else min(target_daily_count, 5)
                cache_balance_ok = bool(is_daily_balance_acceptable(cached_route or [], trip_days, _cache_min_daytime))
            except Exception:
                cache_balance_ok = False
            # 👑 复用判据用**生成时同一套门禁结论**（2026-10-05 实测教训）：
            #    原来这里另写了一条更严的"每天都必须有景区+餐饮"，结果系统自己产出的方案
            #    经常不满足（收口会移出多余节点），缓存命中后又被拒 —— 日志上明明"精确命中"，
            #    整条推演还是跑满 70s，等于把缓存关掉了。判据见 core/cache_policy。
            from core.cache_policy import cached_plan_decision

            _cache_decision = cached_plan_decision(
                payload=cached_hit,
                route=cached_route,
                days=trip_days,
                daily_count=target_daily_count,
                signals=pref_signals,
                classify=classify_poi_category,
                balance_ok=cache_balance_ok,
                exclude_names=feedback_exclude_names,
            )
            cache_ok = bool(_cache_decision.get("reusable"))
            if cache_ok:
                print(
                    f"✅ [语义缓存] {target_city}/{trip_days}天命中，秒回（{_cache_decision.get('reason')}）",
                    flush=True,
                )
                return StreamingResponse(cached_stream(cached_hit), media_type="application/x-ndjson")
            print(
                f"♻️ [语义缓存] {target_city}/{trip_days}天命中但不复用：{_cache_decision.get('reason')}",
                flush=True,
            )
        # Seed templates are intentionally limited to short cold-start trips.
        # Long/deep itineraries must use the supplier-backed candidate pool so
        # every requested day receives a balanced set of unique nodes.
        seed_fd = _build_seed_final_data(target_city, trip_days) if trip_days <= 3 and role != "深度探索" else None
        if seed_fd:
            seed_payload = {
                "final_data": seed_fd,
                "budget_breakdown": budget_breakdown_payload,
                "target_city": {
                    "name": target_city,
                    "lnglat": [float(x) for x in coord.split(",")] if coord and "," in coord else [],
                    # 目的地是怎么定下来的：matched（从诉求里认出来）/
                    # fallback_default（**没认出来**，用了兜底城市）。
                    # 前端据此可以如实提示"没识别出目的地"，而不是让用户以为
                    # 这份武威行程是自己要的（2026-10-08 实测撞到过）。
                    "resolution": city_resolution,
                },
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
            yield json.dumps({"token": f"[地理精算 Agent]: 启动专家智能体并行提案与成员协商，正在为【{target_city}】全员排布完整 {trip_days} 天（每日 {target_daily_count} 节点）高共识度路线...\n"}, ensure_ascii=False) + "\n"
            async for _e in emit_reasoning("地理精算 Agent", "claim", f"启动专家智能体并行提案与成员协商，为【{target_city}】全员排布 {trip_days} 天高共识度路线", f"成员 {_member_count} 人，每日 {target_daily_count} 节点，预算模式 {budget_mode}"):
                yield _e

        await asyncio.sleep(0.1)

        # ⚠️ 以前整个是 `if coord and "," in coord:` 包着的，且 except 直接 pass ——
        #    于是①没有坐标时**根本不发 target_city 事件**（前端拿不到目的地）；
        #    ②坐标解析失败被静默吞掉，日志里什么都看不到（本项目记录过同类坑）。
        #    现在：事件总是发（坐标能给就给），失败留下痕迹。
        if coord and "," in coord:
            try:
                lon, lat = coord.split(",")
                city_lnglat = [float(lon), float(lat)]
            except Exception as exc:
                print(f"⚠️ [目的地] 坐标解析失败（{coord!r}）：{exc}", flush=True)
                city_lnglat = []
        else:
            city_lnglat = []
        yield json.dumps({
            "type": "target_city",
            "payload": {
                "name": target_city,
                "lnglat": city_lnglat,
                # 目的地是怎么定下来的（matched / fallback_default）：
                # 没识别出城市时会静默用兜底城市出方案，前端需要能如实说出来。
                "resolution": city_resolution,
            }
        }, ensure_ascii=False) + "\n"

        coord_task = toolbox.get_coordinates(target_city)

        # 候选供给诊断：记录每一类真实候选有多少、有没有补检索（随方案如实下发）。
        # ⚠️ 必须先于省域锚点解析初始化：省域分支会往里写 province_scope / province_scope_error。
        #    实测（2026-11-01）：把它留在下面，省域请求走到这里就是 UnboundLocalError，
        #    而普通城市请求完全正常 —— 单测也覆盖不到（所以日志里一直看不到省域的任何输出）。
        pool_diagnostics: Dict[str, Any] = {}

        # 👑 省域覆盖（A）：用户把目的地写成「四川」这类省时，检索范围必须是**全省地州**，
        #    而不是省会。实测（2026-11-01）：省级单查询（city=四川 / 510000）返回的前 25 条
        #    全部在成都各区；九寨沟（阿坝）、四姑娘山（阿坝小金）、稻城亚丁（甘孜）离成都
        #    300km 外，靠"省会 + 半径"永远查不到 —— 用户要的"四川"就成了"成都周边"。
        #    这里先拿全省地州清单并按天数选锚点，景点/美食/住宿三路取数共用这份清单。
        province_scope_name = province_of(_province_input)
        province_anchors: List[Dict[str, Any]] = []
        if province_scope_name:
            try:
                province_anchors = pick_anchors(
                    await toolbox.get_sub_districts(province_scope_name),
                    trip_days,
                    capital=target_city,
                )
            except Exception as _province_exc:
                pool_diagnostics["province_scope_error"] = f"{type(_province_exc).__name__}: {str(_province_exc)[:120]}"
                print(f"⚠️ [省域范围] 地州锚点解析失败（退回按省会检索）: {_province_exc}", flush=True)
            if province_anchors:
                pool_diagnostics["province_scope"] = {
                    "province": province_scope_name,
                    "anchor_count": len(province_anchors),
                    "anchors": [str(item.get("name") or "") for item in province_anchors],
                    "quota": anchor_quota(trip_days),
                }
                print("🗺️ " + province_scope_note(province_scope_name, province_anchors, trip_days), flush=True)

        # 👑 省域覆盖（B）：日内编排的"片区口径"。
        #    旧判据按"离省会多远"算每天的里程与远景区数，于是九寨沟（离成都 300km）
        #    会被判成"一天跑不完"并被 `repair_day_overload` 直接移出 —— 省域覆盖等于白做。
        #    省域请求改用：同一天一个片区 + 跨片区一次换乘（高铁估算）+ 片区内里程预算。
        #    `_day_region_plan` 在这里先定空值，真正的分片建议在候选池备好后算（见下方）。
        _day_region_plan: Dict[int, str] = {}
        _day_region_note = ""
        # 👑 省域覆盖（B2）：一个地州跨度可能 250km+（阿坝：九寨沟 ↔ 四姑娘山），
        #    几天之内只能走其中一条线。`_sub_cluster_keep` 记录"某片区这趟只保留哪些候选"，
        #    `_sub_cluster_note` 是写给用户/提示词的如实说明（其余方向这次不排）。
        _sub_cluster_keep: Dict[str, set] = {}
        _sub_cluster_note = ""

        def _province_region_of(node: Mapping[str, Any]) -> str:
            """节点属于哪个片区：优先读节点自带的片区标签，否则按最近的省域锚点判断。

            ⚠️ 模型输出里通常没有 `region_city`（底座里有，但它不一定照抄），
            所以必须能用坐标兜底；两边都拿不到就返回空串（当"片区未知"，不参与片区判定）。
            """
            explicit = str(node.get("region_short") or node.get("region_city") or "").strip()
            if explicit:
                return short_region_name(explicit) or explicit
            from core.travel_feasibility import node_lnglat

            point = node_lnglat(node)
            if not point or not province_anchors:
                return ""
            nearest = None
            for _anchor in province_anchors:
                _center = _anchor.get("center") or []
                if not _center:
                    continue
                _km = haversine_km(point, _center)
                if nearest is None or _km < nearest[0]:
                    nearest = (_km, _anchor)
            if nearest is None:
                return ""
            return str(nearest[1].get("short") or short_region_name(str(nearest[1].get("name") or "")))

        def _province_region_center(region: str) -> List[float]:
            """片区中心坐标（算跨片区换乘耗时用）；找不到返回空列表。"""
            for _anchor in province_anchors:
                if region and region in (str(_anchor.get("short") or ""), str(_anchor.get("name") or "")):
                    return list(_anchor.get("center") or [])
            return []

        def _evaluate_feasibility(
            route: Sequence[Mapping[str, Any]],
            *,
            city_center: Optional[Sequence[float]],
            distance_tolerance: str,
            days: int,
        ) -> Dict[str, Any]:
            """日内现实性：省域请求走片区口径，其余保持原口径（行为不变）。"""
            from core.travel_feasibility import evaluate_province_feasibility, evaluate_travel_feasibility

            if province_scope_name and province_anchors:
                return evaluate_province_feasibility(
                    route,
                    region_of=_province_region_of,
                    region_center_of=_province_region_center,
                    day_regions=_day_region_plan,
                    distance_tolerance=distance_tolerance,
                    days=days,
                )
            return evaluate_travel_feasibility(
                route,
                city_center=city_center,
                distance_tolerance=distance_tolerance,
                days=days,
            )

        def _repair_feasibility(
            route: Sequence[Mapping[str, Any]],
            *,
            city_center: Optional[Sequence[float]],
            distance_tolerance: str,
        ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
            """修复日内容量：省域按片区收口，其余仍按"一天一个远景区"。"""
            from core.travel_feasibility import repair_day_overload, repair_province_overload

            if province_scope_name and province_anchors:
                return repair_province_overload(
                    route,
                    region_of=_province_region_of,
                    region_center_of=_province_region_center,
                    # 分片建议就是"这一天该去哪片"：修复要以它为准，而不是"出现最多的那个"。
                    day_regions=_day_region_plan,
                    distance_tolerance=distance_tolerance,
                )
            return repair_day_overload(
                route,
                city_center=city_center,
                distance_tolerance=distance_tolerance,
            )

        # 不再把所有景点类型塞进一次宽泛检索。高德在“景点|博物馆|文化地标”
        # 这类混合关键词下很容易返回大量博物馆，导致模型看到的候选池本身就失衡。
        # 分领域检索后再合并，保证自然景区、文化、人文商业和特色餐饮都有真实候选。
        # 候选供给诊断已在省域锚点解析之前初始化（见上，省域分支会先往里写）
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
            scenic: List[Dict[str, Any]]
            cultural: List[Dict[str, Any]]
            shopping: List[Dict[str, Any]]
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

            # 👑 省域覆盖（A）：省级目的地把**每个地州**各查一次真景区。
            #    实测（2026-11-01 四川）：阿坝 311 条（九寨沟/神仙池）、乐山 204 条（乐山大佛）、
            #    九寨沟县 69 条；而省级单查询的前 25 条全在成都各区。不逐地州查，省域覆盖就是空话。
            #    只留 `scenic_tier=landmark`（城市公园在省会那次检索里已经有了），
            #    同景区子点先合并，每个地州最多补 10 个，避免候选池被"景区数量"撑爆。
            if province_anchors:
                _province_started = time.monotonic()
                _province_seen = {str(item.get("name") or "") for item in merged}
                _province_added = 0
                _province_by_anchor: Dict[str, int] = {}
                _province_errors: List[str] = []
                for _anchor in province_anchors:
                    _anchor_name = str(_anchor.get("name") or "")
                    if not _anchor_name:
                        continue
                    try:
                        _province_rows = await toolbox.get_dynamic_pois(
                            _anchor_name,
                            keywords=SCENIC_WIDENING_KEYWORDS,
                            types="110000",
                            limit=20,
                        )
                    except Exception as _anchor_exc:
                        _province_errors.append(f"{_anchor_name}:{type(_anchor_exc).__name__}")
                        continue
                    _province_landmarks = [
                        item for item in keep_for(_province_rows, "scenic")
                        if str(item.get("scenic_tier") or "") == "landmark"
                    ]
                    _province_landmarks, _ = dedupe_sub_spots(_province_landmarks)
                    _anchor_added = 0
                    for _item in _province_landmarks:
                        _item_name = str(_item.get("name") or "")
                        if not _item_name or _item_name in _province_seen:
                            continue
                        if _anchor_added >= 10:
                            break
                        _province_seen.add(_item_name)
                        # 片区标签是编排阶段的依据（同一天只在一个片区；跨片区要整段移动）
                        _item["region_city"] = _anchor_name
                        _item["region_short"] = short_region_name(_anchor_name)
                        if _anchor.get("adcode"):
                            _item["region_adcode"] = str(_anchor.get("adcode") or "")
                        _province_km = haversine_km(city_lnglat, _item.get("lnglat"))
                        if _province_km == _province_km:
                            _item["distance_km"] = round(float(_province_km), 1)
                            if float(_province_km) >= 40:
                                _item["out_of_town"] = True
                        merged.append(_item)
                        _anchor_added += 1
                        _province_added += 1
                    if _anchor_added:
                        _province_by_anchor[_anchor_name] = _anchor_added
                pool_diagnostics.setdefault("province_scope", {})
                pool_diagnostics["province_scope"].update({
                    "scenic_added": _province_added,
                    "scenic_by_anchor": _province_by_anchor,
                    "scenic_errors": _province_errors,
                    "scenic_seconds": round(time.monotonic() - _province_started, 1),
                })
                if _province_added or _province_errors:
                    print(
                        f"🗺️ [省域景区] 逐地州补检索：新增真景区 {_province_added} 个"
                        f"（{len(_province_by_anchor)}/{len(province_anchors)} 个地州有产出，"
                        f"{round(time.monotonic() - _province_started, 1)}s）"
                        + (f"；失败：{'、'.join(_province_errors)}" if _province_errors else ""),
                        flush=True,
                    )

            # 👑 同景区子景点合并（2026-10-05 伊宁实测）：高德会一次返回同一个景区的 5 个子点
            #    （「天山花海·花田柳林」「天山花海天山花园」「天山花海旅游景区木屋营地」…），
            #    名字互相不包含，所以更早的 dedupe_sub_spots 抓不住 —— 结果同一天排了 5 个
            #    "天山花海xx"，看起来像 5 个景点。这里按名字前缀归并，只留最短的那条。
            merged, _sub_merged = collapse_sub_spots(merged)
            if _sub_merged:
                pool_diagnostics["sub_spots_merged"] = _sub_merged
                print(
                    f"🧩 [子景点合并] 同一个景区的子景点合并掉 {_sub_merged} 个（保留最短的主名），候选 {len(merged)} 个",
                    flush=True,
                )

            return merged

        attractions_task = _fetch_attraction_mix()
        _loop_probe = LoopLagProbe().start()
        _watchdog = LoopWatchdog().start()
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

        _loop_probe.stop()
        _watchdog.stop()
        print(
            f"⏱️ [取数细分] {fetch_timing_summary()}｜闸门：{amap_gate_summary()}｜"
            f"事件循环：{_loop_probe.summary()}｜{pool_cache_summary()}",
            flush=True,
        )
        # 事件循环卡顿分段快照：明确"是哪一段把循环堵住了"（否则只知道总卡顿）
        _snap = LoopLagProbe().start()

        # 👑 免费平替 WorldMonitor：本地多源聚合风控（高德实时天气 + 实时交通 + 官方气象预警公开接口）动态融合 CII
        is_weekend = datetime.datetime.now().weekday() >= 5
        safety_intel = await wm_client.aggregate_city_risk(
            safety_intel, target_city,
            weather=weather_data, traffic=traffic_data, is_weekend=is_weekend
        )
        _stage("取数与风控")

        # 事件循环卡顿分段快照：明确"是哪一段把循环堵住了"（否则只知道总卡顿）。
        # 只在真的卡了（>0.5s）时打日志，否则每次推演都刷四行没用的 0ms。
        _snap = LoopLagProbe().start()
        unique_foods = deduplicate_and_diversify_pois(raw_foods)
        unique_attractions = deduplicate_and_diversify_pois(raw_attractions)
        # 省域：记住"带地州标签的候选"（都是我们自己按地州名实地检索回来的）。
        # 协商回环里 `unique_attractions` 会被专家重提的候选**整体覆盖**（见下方 `_revised_pools`），
        # 实测 4 天四川：56 个地州景区被冲回 36 个、只剩成都+阿坝两片。这里留一份快照用于补回。
        _province_tagged_attractions: List[Dict[str, Any]] = [
            dict(_p) for _p in unique_attractions
            if isinstance(_p, Mapping) and str(_p.get("region_short") or "").strip()
        ]
        if _snap.max_lag > 0.5:
            print(f"⏱️ [卡顿·去重] {_snap.summary()}", flush=True)
        _snap = LoopLagProbe().start()

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

        # 👑 住宿候选兜底（2026-10-05 横向对照实测的真实事故，伊宁 3 天）：生产用的住宿关键词
        #    「品质酒店|高端度假酒店|高分精品住宿」在高德对伊宁**直接回 count=0**，而同一时刻
        #    「酒店」二字回 86 条 / 30 个 POI。于是住宿候选池为空 → lodging-agent 未参与协商 →
        #    最终方案**一晚住宿都没有**（用户拿到的是"住哪儿没着落"的行程）。
        #    这跟 2026-10-04 餐饮踩的是同一个坑：窄关键词把整类候选清空，所以补一次更宽的检索。
        #    品质/档次排序交给后面的多目标裁决，不该在检索阶段就把整类清空。
        if needs_widening(len(cleaned_hotels), required_lodging_candidates(max(1, trip_days - 1))):
            _hotel_names = [str(item.get("name") or "") for item in cleaned_hotels if isinstance(item, dict)]
            _hotel_widen = await widen_candidates(
                toolbox.get_dynamic_pois,
                city=target_city,
                keywords=LODGING_WIDENING_KEYWORDS,
                types="100000",
                limit=30,
                existing_names=_hotel_names,
                # 与餐饮同理：住宿类别永远不属于"玩点类别"，必须显式关掉 strict_type，
                # 否则补检索"看起来跑了却一条都没加回来"（餐饮踩过这个坑）。
                fetch_kwargs={"play_worthy": False},
            )
            _added_hotels = _hotel_widen["added"]
            if _added_hotels:
                cleaned_hotels = list(cleaned_hotels) + _added_hotels
                pool_diagnostics["widened"] = list(pool_diagnostics.get("widened") or []) + ["住宿"]
                pool_diagnostics.setdefault("widened_added", {})["住宿"] = len(_added_hotels)
                print(
                    f"🔎 [候选补检索] 住宿候选只有 {len(_hotel_names)} 个 → 更宽检索补回 {len(_added_hotels)} 个",
                    flush=True,
                )
            if _hotel_widen.get("error"):
                pool_diagnostics["widening_error"] = _hotel_widen["error"]
                print(f"⚠️ [候选补检索] 住宿补充检索失败（不影响主流程）: {_hotel_widen['error']}", flush=True)

        # 👑 省域覆盖（A）· 美食与住宿：跨地州行程里"吃在哪儿、住在哪儿"也要跟着片区走。
        #    只查省域锚点里的前几个片区（吃住不像景区那样值得全省铺开，否则一天吃住都在 80km 外），
        #    数量按天数封顶。每条候选带 `region_city/region_short`，编排阶段据此按片区分配。
        if province_anchors:
            _inn_started = time.monotonic()
            _inn_anchors = province_anchors[: max(2, min(len(province_anchors), trip_days))]
            _food_seen = {str(item.get("name") or "") for item in unique_foods if isinstance(item, Mapping)}
            _hotel_seen = {str(item.get("name") or "") for item in cleaned_hotels if isinstance(item, Mapping)}
            _food_by_anchor: Dict[str, int] = {}
            _hotel_by_anchor: Dict[str, int] = {}
            _inn_errors: List[str] = []
            for _anchor in _inn_anchors:
                _anchor_name = str(_anchor.get("name") or "")
                if not _anchor_name:
                    continue
                try:
                    _region_foods = await toolbox.get_dynamic_pois(
                        _anchor_name, keywords=FOOD_WIDENING_KEYWORDS, types="050000",
                        limit=20, play_worthy=False,
                    )
                    _region_hotels = await toolbox.get_dynamic_pois(
                        _anchor_name, keywords=LODGING_WIDENING_KEYWORDS, types="100000",
                        limit=20, play_worthy=False,
                    )
                except Exception as _inn_exc:
                    _inn_errors.append(f"{_anchor_name}:{type(_inn_exc).__name__}")
                    continue
                _added_food_here = 0
                for _item in _region_foods:
                    if not isinstance(_item, Mapping):
                        continue
                    _item = dict(_item)
                    _item_name = str(_item.get("name") or "")
                    if not _item_name or _item_name in _food_seen:
                        continue
                    if classify_poi_category(_item_name, str(_item.get("type") or ""), bool(_item.get("is_hotel"))) != "food":
                        continue
                    if _added_food_here >= 8:
                        break
                    _food_seen.add(_item_name)
                    _item["region_city"] = _anchor_name
                    _item["region_short"] = short_region_name(_anchor_name)
                    unique_foods.append(_item)
                    _added_food_here += 1
                if _added_food_here:
                    _food_by_anchor[_anchor_name] = _added_food_here
                _added_hotel_here = 0
                for _item in _region_hotels:
                    if not isinstance(_item, Mapping):
                        continue
                    _item = dict(_item)
                    _item_name = str(_item.get("name") or "")
                    if not _item_name or _item_name in _hotel_seen:
                        continue
                    if classify_poi_category(_item_name, str(_item.get("type") or ""), bool(_item.get("is_hotel"))) != "hotel":
                        continue
                    if _added_hotel_here >= 6:
                        break
                    _hotel_seen.add(_item_name)
                    _item["region_city"] = _anchor_name
                    _item["region_short"] = short_region_name(_anchor_name)
                    cleaned_hotels.append(_item)
                    _added_hotel_here += 1
                if _added_hotel_here:
                    _hotel_by_anchor[_anchor_name] = _added_hotel_here
            pool_diagnostics.setdefault("province_scope", {})
            pool_diagnostics["province_scope"].update({
                "food_added": sum(_food_by_anchor.values()),
                "food_by_anchor": _food_by_anchor,
                "lodging_added": sum(_hotel_by_anchor.values()),
                "lodging_by_anchor": _hotel_by_anchor,
                "inn_errors": _inn_errors,
                "inn_seconds": round(time.monotonic() - _inn_started, 1),
            })
            print(
                f"🍜 [省域吃住] {len(_inn_anchors)} 个片区补检索：美食 +{sum(_food_by_anchor.values())}、"
                f"住宿 +{sum(_hotel_by_anchor.values())}（{round(time.monotonic() - _inn_started, 1)}s）"
                + (f"；失败：{'、'.join(_inn_errors)}" if _inn_errors else ""),
                flush=True,
            )

        # 👑 住宿候选接上**供应商实价**（2026-10-05）：高德不给房价，所以"住哪"一直没有价格依据，
        #    最后只能标"需要你确认"。供应商一次城市检索就能拿回「酒店名 + 实价 + 星级 + 地址 +
        #    下单链接」，于是住宿候选**本身带价**（"按实价选酒店"，而不是先选完再想办法补价）。
        #    预算/档次是用户的约束，交给服务端筛（hotelTags.maxPricePerNight / starRatings）。
        _lodging_supply: Dict[str, Any] = {"status": "not_attempted", "added": 0}
        try:
            from core.candidate_index import normalize_text as _normalize_text
            from core.rollinggo_mcp import search_hotels as _supplier_search_hotels

            _hotel_nights = max(1, trip_days - 1)
            _nightly_cap = round(float(hotel_budget) / _hotel_nights) if hotel_budget else None
            _supplier_hotels = await _supplier_search_hotels(
                target_city,
                check_in=str(user_prefs.get("start_date") or ""),
                nights=_hotel_nights,
                adults=max(1, _member_count),
                size=6,
                max_price_per_night=_nightly_cap,
                min_star=4.0 if is_luxury else None,
            )
            _lodging_supply = {
                "status": _supplier_hotels.get("status"),
                "error": _supplier_hotels.get("error") or "",
                "nightly_cap": _nightly_cap,
                "min_star": 4.0 if is_luxury else None,
                "added": 0,
                "hotels": [],
            }
            if _supplier_hotels.get("status") == "ok":
                _existing_hotel_keys = {
                    _normalize_text(str(item.get("name") or ""))
                    for item in cleaned_hotels
                    if isinstance(item, Mapping)
                }
                for _hotel in _supplier_hotels.get("hotels") or []:
                    _hotel_name = str(_hotel.get("name") or "")
                    if not _hotel_name:
                        continue
                    _key = _normalize_text(_hotel_name)
                    if _key in _existing_hotel_keys:
                        continue
                    # ⚠️ 供应商**没给坐标**的候选不进候选池：它没有位置，规划会把它当"某个地方"，
                    #    实测被写成 [0,0] → 那天里程算出 9440km（整个方案的空间判断全废）。
                    #    这类候选仍然留在 `lodging_supply` 清单里（有名字、有实价、有地址），
                    #    只是不参与排序与路径 —— 用户想住可以直接说。
                    _hotel_lnglat = _hotel.get("lnglat")
                    if not (
                        isinstance(_hotel_lnglat, (list, tuple))
                        and len(_hotel_lnglat) >= 2
                        and not (float(_hotel_lnglat[0]) == 0.0 and float(_hotel_lnglat[1]) == 0.0)
                    ):
                        _lodging_supply.setdefault("without_coords", []).append(_hotel_name)
                        continue
                    _existing_hotel_keys.add(_key)
                    cleaned_hotels.append(
                        {
                            "name": _hotel_name,
                            "type": "住宿服务",
                            "address": _hotel.get("address") or "",
                            "lnglat": [float(_hotel_lnglat[0]), float(_hotel_lnglat[1])],
                            "rating": _hotel.get("star"),
                            "price": _hotel.get("price"),
                            "price_source": "rollinggo",
                            "source": "rollinggo",
                            "booking_url": _hotel.get("booking_url") or "",
                            # 候选来源要能区分：这些是**有实价**的供应商候选，不是模型凭记忆写的
                            "data_source": "rollinggo",
                            "is_hotel": True,
                            "tags": ["住宿"],
                        }
                    )
                    _lodging_supply["added"] += 1
                _lodging_supply["hotels"] = [
                    {
                        "name": str(item.get("name") or ""),
                        "price": item.get("price"),
                        "star": item.get("star"),
                        "address": str(item.get("address") or ""),
                        "booking_url": str(item.get("booking_url") or ""),
                        "message": str(item.get("message") or ""),
                        # 坐标留着：定稿后要算"这家离方案里的玩点多远"（价格可比、距离也要可比）
                        "lnglat": list(item.get("lnglat") or []),
                    }
                    for item in (_supplier_hotels.get("hotels") or [])
                ]
                _lodging_supply["count"] = _supplier_hotels.get("count")
                _lodging_supply["with_price"] = len(
                    [item for item in (_supplier_hotels.get("hotels") or []) if isinstance(item.get("price"), (int, float))]
                )
                print(
                    f"🏨 [住宿候选] 供应商实价候选 {_lodging_supply['count']} 家"
                    f"（其中带价 {_lodging_supply['with_price']} 家）→ 并入候选池 {_lodging_supply['added']} 家"
                    + (f"；每晚上限 ¥{_nightly_cap}" if _nightly_cap else "")
                    + ("；只看 4 星以上" if is_luxury else "")
                    + (
                        f"；供应商没给坐标 {len(_lodging_supply.get('without_coords') or [])} 家（留在可选清单里，不进路线）"
                        if _lodging_supply.get("without_coords")
                        else ""
                    ),
                    flush=True,
                )
            else:
                print(f"🏨 [住宿候选] 供应商没给候选：{_lodging_supply['status']}｜{_lodging_supply['error']}", flush=True)
        except Exception as _lodging_supply_exc:  # 供应商失败绝不影响出方案
            _lodging_supply = {"status": "error", "error": f"{type(_lodging_supply_exc).__name__}: {str(_lodging_supply_exc)[:140]}"}
            print(f"⚠️ [住宿候选] 供应商候选查询失败（不影响出方案）: {_lodging_supply_exc}", flush=True)

        # Specialist runtime: these domain tasks run independently and return
        # validated proposals before the coordinator performs final route
        # selection. Empty supplier data is kept as a degraded proposal rather
        # than being replaced with fabricated facts.
        specialist_runtime_result = {"status": "degraded", "proposals": [], "completed": 0, "failed": 0, "degraded": 0, "parallel": True}
        # 多轮消息总线：初始轮（round 0）的提案会广播出去，重提轮（round 1..N）的智能体
        # 因此能看到"上一轮别人提了什么 + 本轮裁决"，而不是只收到一条孤立指令。
        _agent_bus = AgentMessageBus(correlation_id=plan_correlation_id)
        _llm_specialist_ids = SPECIALIST_LLM_AGENT_IDS if specialist_llm_enabled() else ()
        _llm_specialist_complete = specialist_llm_complete if _llm_specialist_ids else None
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
                # 文化与无障碍智能体（2026-10-08）：这条装配发生在**路线生成之前**，
                # 所以不能从 route 取节点（这里 route 恒为空 —— 实测两个智能体因此
                # 永远显示"未参与"，等于没接线）。改用此刻真实存在的景点候选池。
                cultural_nodes=[
                    item
                    for item in unique_attractions
                    if isinstance(item, dict)
                    and any(
                        keyword in str(item.get("type") or "") + str(item.get("tags") or "")
                        for keyword in ("文化", "博物馆", "古迹", "遗址", "寺", "古城")
                    )
                ][:12],
                city=target_city,
                llm_complete=_llm_specialist_complete,
                llm_agent_ids=_llm_specialist_ids,
            )
            specialist_runtime_result = await _runtime.execute(
                _runtime_requests, bus=_agent_bus, round_no=0
            )
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
                            # 本轮消息总线：先开新轮次，再把裁决作为显式消息广播给目标智能体；
                            # 智能体的收件箱里于是同时有「上一轮提案」和「本轮裁决」。
                            _agent_bus.start_round()
                            _bus_round = _agent_bus.round_no
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
                                    llm_complete=_llm_specialist_complete,
                                    llm_agent_ids=_llm_specialist_ids,
                                )
                                # 只跑目标智能体，并在它的输入契约里加上裁决指令
                                _target_requests = [
                                    _req for _req in _revision_requests if _req.agent_id == _agent_id
                                ]
                                for _req in _target_requests:
                                    _req.hard_constraints.update(_directive.get("hard_constraints") or {})
                                    _req.context["revision_directive"] = str(_directive.get("message") or "")
                                    _req.context["revision_reason"] = str(_directive.get("reason") or "")
                                _agent_bus.publish(
                                    sender="coordinator",
                                    topic="directive",
                                    recipients=(_agent_id,),
                                    content={
                                        "message": str(_directive.get("message") or ""),
                                        "reason": str(_directive.get("reason") or ""),
                                        "hard_constraints": dict(_directive.get("hard_constraints") or {}),
                                        "round": _bus_round,
                                    },
                                    round_no=_bus_round,
                                )
                                _revision_result = await _revision_runtime.execute(
                                    _target_requests, bus=_agent_bus, round_no=_bus_round
                                )
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
                                # 省域：重提候选通常只覆盖省会周边，把地州实地检索回来的候选补回去
                                if _province_tagged_attractions:
                                    _rev_names = {str(_x.get("name") or "") for _x in unique_attractions if isinstance(_x, Mapping)}
                                    _province_restored = [
                                        dict(_x) for _x in _province_tagged_attractions
                                        if str(_x.get("name") or "") not in _rev_names
                                    ]
                                    if _province_restored:
                                        unique_attractions = list(unique_attractions) + _province_restored
                                        print(
                                            f"🗺️ [省域候选] 协商重提把地州景区冲掉了，补回 {len(_province_restored)} 个",
                                            flush=True,
                                        )
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
            _stage("专业提案与协商")
            _pools_pre_gate = {
                "attractions": list(unique_attractions),
                "dining": list(unique_foods),
                "lodging": list(cleaned_hotels),
            }
            _gated = apply_candidate_gates(
                {"attractions": unique_attractions, "dining": unique_foods, "lodging": cleaned_hotels},
                specialist_runtime_result,
            )
            # Only contract-approved candidates enter the coordinator's
            # scoring pool. A degraded proposal leaves that domain empty and
            # is surfaced in the audit instead of silently becoming a fact.
            unique_attractions = _gated["pools"].get("attractions", [])
            # 👑 省域覆盖（A）· 门禁放行：`apply_candidate_gates` 只留"专家提案点名"的候选。
            #    实测（2026-11-01 四川 5 天）：逐地州检索到 57 个真景区（甘孜/凉山/宜宾/攀枝花…），
            #    但提案只点了成都周边的几个，门禁把其余全判 `not_in_specialist_proposal` 丢掉 ——
            #    候选池只剩 2 个片区，省域覆盖在最后一米失效（最终又变成"成都周边 5 天"）。
            #    这里把"我们**自己按地州名实地检索回来**的景区/餐厅/住宿"（带 region_short、
            #    不是模型编的）在通过结构校验后放回；数量如实进审计，其它门禁（证据、提案状态）不变。
            if province_anchors:
                from core.optimization import validate_candidate as _validate_candidate

                for _pool_name in ("attractions", "dining", "lodging"):
                    _accepted = list(_gated["pools"].get(_pool_name) or [])
                    _accepted_names = {str(_item.get("name") or "") for _item in _accepted if isinstance(_item, Mapping)}
                    _readmitted: List[Dict[str, Any]] = []
                    for _item in _pools_pre_gate.get(_pool_name) or []:
                        if not isinstance(_item, Mapping):
                            continue
                        if not str(_item.get("region_short") or "").strip():
                            continue
                        _item_name = str(_item.get("name") or "")
                        if not _item_name or _item_name in _accepted_names:
                            continue
                        if not _validate_candidate(dict(_item)).get("valid"):
                            continue
                        _readmitted.append(dict(_item))
                        _accepted_names.add(_item_name)
                    if not _readmitted:
                        continue
                    _gated["pools"][_pool_name] = _accepted + _readmitted
                    specialist_runtime_result.setdefault("gate_readmitted", {})[_pool_name] = len(_readmitted)
                    print(
                        f"🗺️ [省域候选] {_pool_name}：提案没点名、但确实是地州名实地检索到的，放回 {len(_readmitted)} 个",
                        flush=True,
                    )
            unique_attractions = _gated["pools"].get("attractions", [])
            unique_foods = _gated["pools"].get("dining", [])
            cleaned_hotels = _gated["pools"].get("lodging", [])
            specialist_runtime_result["gate_audits"] = _gated["audits"]
            # 多轮消息总线：完整记录留在结果里（谁在第几轮说了什么、发给了谁），
            # 失败分支（except）不会覆盖这里的值，审计仍能看到协商过程。
            specialist_runtime_result["message_bus"] = _agent_bus.transcript()
            specialist_runtime_result["message_rounds"] = _agent_bus.round_no
            specialist_runtime_result["message_summary"] = _agent_bus.summary()
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
                "message_rounds": _agent_bus.round_no,
                "message_count": _agent_bus.summary().get("messages"),
            }, room_id, correlation_id=plan_correlation_id, producer="coordinator")
        except Exception as _runtime_error:
            specialist_runtime_result = {
                **specialist_runtime_result,
                "status": "degraded",
                "error": str(_runtime_error)[:240],
                # 出错也要留下已发生的协商记录：否则"为什么没协商"无从查起
                "message_bus": _agent_bus.transcript(),
                "message_rounds": _agent_bus.round_no,
                "message_summary": _agent_bus.summary(),
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
                    # 景区档位在"分桶"时只读这个字段（见 `_pool_cat`）。实测（2026-11-01 四川）：
                    # 一部分真实景区（天气备选、地州补检索回来的点）没有这个字段，分桶时被当成
                    # "城市公园"整批砍掉 —— 分片计划里明明有成都 2 天，候选池里成都景区只剩 1 个。
                    if not str(p.get("scenic_tier") or ""):
                        p["scenic_tier"] = scenic_tier(p.get("name"), p.get("type"))

            for h in cleaned_hotels:
                if isinstance(h, dict):
                    h["fitness_score"] = fitness_calculator.calculate_fitness(h)
                    h["is_hotel"] = True

            pareto_result = ParetoFrontierSelector.resolve_conflicts(daytime_pois)
            pareto_daytime = pareto_result.get("frontier", [])
            # Pareto 前沿用于裁决与解释，但极端情况下前沿可能很小，无法覆盖
            # 长行程的每日节点容量。将被支配候选作为“可选补充池”保留，仍在
            # optimization_audit.rejected 中明确记录淘汰原因，避免路线退化。
            _min_daytime_pool = trip_days * actual_daily_count
            if len(pareto_daytime) < _min_daytime_pool:
                def _normalize_candidate_name(value: Any) -> str:
                    return re.sub(r'[\\(（].*?[\\)）]', '', str(value or '')).strip()
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
            # 👑 省域覆盖（A）· 先定片区、再补候选（2026-11-01 实测的顺序倒置）：
            #    帕累托前沿之后只活下来极少数候选，而"补到 20 个"是**按类别交错**取前 N 个 ——
            #    省会候选排在前面，阿坝/甘孜的真实景区根本进不了前沿。等分片建议算出来时，
            #    阿坝只剩 3 个候选，4 天里分到 2 天也排不满（每日密度每天都报不足，地点自然很少）。
            #    这里先用**全量候选**按地州分组定"哪天在哪片"，再按片区保量补进前沿；
            #    下游分片直接复用这份计划，不再重算（那时候选已被截断，判断只会更窄）。
            if province_scope_name and province_anchors:
                # 口径必须与下游分桶（`_pool_cat`）**完全一致**：景区 = 分类器判 scenic **且**
                # 档位是 landmark。此前这里只判了前半句（实测 2026-11-01 四川 4 天）：成都那 11 个
                # 全是公园/乐园档，被算成"成都已有 11 个景区"→ 成都需要补的景区变成 0 个，
                # 一个真景区都没补进前沿；到分桶时它们全落进 `park` 桶被公园上限砍掉，
                # 于是"成都 2 天"的候选池里只剩 1 个景区，每日密度天天报不足。
                def _is_scenic_landmark(p: Mapping) -> bool:
                    if classify_poi_category(
                        str(p.get("name") or ""), str(p.get("type") or ""), bool(p.get("is_hotel"))
                    ) != "scenic":
                        return False
                    tier = str(p.get("scenic_tier") or "") or scenic_tier(p.get("name"), p.get("type"))
                    return tier == "landmark"

                _region_scenic: Dict[str, List[Dict[str, Any]]] = {}
                for _cand in daytime_pois:
                    if not isinstance(_cand, Mapping):
                        continue
                    if not _is_scenic_landmark(_cand):
                        continue
                    _region = str(_cand.get("region_short") or "") or _province_region_of(_cand)
                    if _region:
                        _region_scenic.setdefault(_region, []).append(dict(_cand))
                if _region_scenic:
                    _early_region_names = list(_region_scenic)
                    _capital_short = short_region_name(target_city)
                    _early_start = next(
                        (index for index, name in enumerate(_early_region_names) if name == _capital_short),
                        None,
                    )
                    _early_plan = assign_cluster_days(
                        [list(_region_scenic[name]) for name in _early_region_names],
                        trip_days,
                        max(1, int(actual_daily_count or 1)),
                        start_cluster=_early_start,
                        max_regions=max(2, (trip_days + 1) // 2),
                    )
                    _region_days: Dict[str, int] = {}
                    for _cluster_index, _cluster_days in (_early_plan.get("cluster_days") or {}).items():
                        if 0 <= int(_cluster_index) < len(_early_region_names):
                            _name = _early_region_names[int(_cluster_index)]
                            _region_days[_name] = _region_days.get(_name, 0) + int(_cluster_days)
                    _early_plan_map: Dict[int, str] = {}
                    for _day_index, _cluster_index in enumerate(_early_plan.get("day_to_cluster") or [], start=1):
                        if 0 <= int(_cluster_index) < len(_early_region_names):
                            _early_plan_map[_day_index] = _early_region_names[int(_cluster_index)]
                    if _early_plan_map:
                        _day_region_plan = _early_plan_map
                        # 👑 省域覆盖（B2）· 计划片区内再收敛成"一条线"：
                        #    阿坝一个州同时检索到九寨沟（33.1N）与四姑娘山（31.1N），相隔约 250km ——
                        #    4 天里分给阿坝 2 天，两头都排就是"第 3 天 451km、第 4 天 209km"，
                        #    看着地点多、实际跑不动（质量闸门 space_efficiency=0）。
                        #    ⚠️ 顺序很关键：**先用全量候选决定"去哪几个片区"**（供给多、权重高的州优先），
                        #    再对已选中的片区收敛成一条线。反过来先收敛的话，阿坝候选被砍到 5 个，
                        #    片区权重就输给凉山的 9 个 —— 用户要的川西雪山线会被凉山顶掉。
                        _line_notes: List[str] = []
                        for _region in list(_region_days):
                            _choice = choose_region_line(_region_scenic.get(_region) or [])
                            if not _choice.get("applied"):
                                continue
                            _region_scenic[_region] = list(_choice["kept"])
                            _sub_cluster_keep[_region] = {
                                str(row.get("name") or "")
                                for row in _choice["kept"]
                                if str(row.get("name") or "")
                            }
                            _line_notes.append(
                                f"{_region}跨度约 {_choice['span_km']:.0f}km，这趟只走"
                                f"『{_choice['kept_label']}』这一条线；{_choice['dropped_label']}这次不排"
                                f"（想走那边可以加天数，或直接把景点写进目的地）"
                            )
                        _sub_cluster_note = "；".join(_line_notes)
                        if _sub_cluster_keep:
                            _before_line = len(pareto_daytime)
                            _line_rows: List[Any] = []
                            for _p in pareto_daytime:
                                if not isinstance(_p, Mapping):
                                    _line_rows.append(_p)
                                    continue
                                _p_region = str(_p.get("region_short") or "") or _province_region_of(_p)
                                _keep_names = _sub_cluster_keep.get(_p_region)
                                if (
                                    _keep_names is not None
                                    and _is_scenic_landmark(_p)
                                    and str(_p.get("name") or "") not in _keep_names
                                ):
                                    continue
                                _line_rows.append(_p)
                            pareto_daytime = _line_rows
                            pool_diagnostics.setdefault("province_scope", {})["sub_clusters"] = _line_notes
                            print(
                                f"🗺️ [省域候选] 子片区收敛（每州只走一条线）："
                                + "；".join(_line_notes)
                                + f"｜前沿移出跨线候选 {_before_line - len(pareto_daytime)} 个",
                                flush=True,
                            )
                        # 片区保量：每个计划片区按"片区天数 × 每天景区名额"留足候选，
                        # 否则阿坝 2 天只有 3 个景区可选，模型再听话也排不满。
                        _scenic_target = max(1, int((composition or {}).get("scenic", 0) or 0))
                        _per_day_scenic = max(1, int(math.ceil(_scenic_target / max(1, trip_days))))
                        _present_names = {
                            str(_p.get("name") or "")
                            for _p in pareto_daytime
                            if isinstance(_p, Mapping)
                        }
                        _present_by_region: Dict[str, int] = {}
                        for _p in pareto_daytime:
                            if not isinstance(_p, Mapping) or not _is_scenic_landmark(_p):
                                continue
                            _p_region = str(_p.get("region_short") or "") or _province_region_of(_p)
                            if _p_region:
                                _present_by_region[_p_region] = _present_by_region.get(_p_region, 0) + 1
                        _reserved: Dict[str, int] = {}
                        for _region, _days_here in _region_days.items():
                            _need = _days_here * _per_day_scenic - _present_by_region.get(_region, 0)
                            if _need <= 0:
                                continue
                            for _cand in sorted(
                                _region_scenic.get(_region) or [],
                                key=lambda item: -float(item.get("fitness_score") or 0),
                            ):
                                if _need <= 0:
                                    break
                                _cand_name = str(_cand.get("name") or "")
                                if not _cand_name or _cand_name in _present_names:
                                    continue
                                _present_names.add(_cand_name)
                                pareto_daytime.append(_cand)
                                _reserved[_region] = _reserved.get(_region, 0) + 1
                                _need -= 1
                        if _reserved:
                            pool_diagnostics.setdefault("province_scope", {})["region_scenic_reserved"] = dict(_reserved)
                            print(
                                "🗺️ [省域候选] 按片区保量补进前沿："
                                + "、".join(f"{name}(+{count})" for name, count in _reserved.items()),
                                flush=True,
                            )

            route_optimizer = GraphRouteOptimizer(fitness_calculator)
            # 👑 空间排序先取完整顺序（不在此处过早截断），后续按类别均衡挑选，
            #    避免美食/文化类 POI 被「TSP 头部截断」整体挤出候选池
            spatial_daytime = route_optimizer.optimize_and_sort(pareto_daytime, max_nodes=max(len(pareto_daytime), 1))
            # 诊断（省域）：空间排序会**丢掉没有 `location` 的候选**（它只认 "lng,lat" 字符串）。
            # 实测（2026-11-01 四川 4 天）：前沿里有 8 个成都景区，排序后只剩 1 个 ——
            # 不打印这一步，分片计划里的"成都 2 天"就没有候选可排。
            if province_scope_name and province_anchors:
                _after_regions: Dict[str, int] = {}
                for _x in spatial_daytime:
                    if not isinstance(_x, Mapping) or not _is_scenic_landmark(_x):
                        continue
                    _x_region = str(_x.get("region_short") or "") or _province_region_of(_x)
                    if _x_region:
                        _after_regions[_x_region] = _after_regions.get(_x_region, 0) + 1
                _dropped_no_loc = sum(
                    1 for _x in pareto_daytime
                    if isinstance(_x, Mapping) and "," not in str(_x.get("location") or "")
                )
                print(
                    f"🗺️ [省域候选] 空间排序后真景区片区："
                    + "、".join(f"{name}({count})" for name, count in _after_regions.items())
                    + f"｜缺坐标被丢 {_dropped_no_loc} 个",
                    flush=True,
                )
            if _snap.max_lag > 0.5:
                print(f"⏱️ [卡顿·帕累托+空间排序] {_snap.summary()}", flush=True)
            _snap = LoopLagProbe().start()

            def _day_cat(p):
                return classify_poi_category(str(p.get('name') or ''), str(p.get('type') or ''), bool(p.get('is_hotel')))

            def _pool_cat(p):
                """候选池分桶用：把"城市公园"从自然景观里拆出来（口径见 core/route_composition.scenic_tier）。"""
                category = _day_cat(p)
                if category == "scenic":
                    tier = str(p.get("scenic_tier") or "") or scenic_tier(p.get("name"), p.get("type"))
                    if tier != "landmark":
                        return "park"
                return category

            # 👑 省域覆盖（A）· 片区保底：帕累托前沿 + 空间排序之后，候选会被压到
            #    `trip_days * 每日节点` 这个量级（实测 4 天四川：79 个候选只活下来 20 个，
            #    其中真景区 10 个，全在成都周边）。不在这里补，甘孜/凉山/宜宾这些地州
            #    **在候选池里还在、但进不了提示词** —— 省域覆盖仍然等于没做。
            #    做法：给"还没进候选的每个片区"各放 1 个代表（优先地州实地检索回来的 landmark）。
            if province_scope_name and province_anchors:
                _present_regions = {str(p.get("region_short") or "") for p in spatial_daytime if isinstance(p, Mapping)}
                _lead_regions: set[str] = set()
                _region_leads_missing: List[Dict[str, Any]] = []
                for _candidate in daytime_pois:
                    if not isinstance(_candidate, Mapping) or _pool_cat(_candidate) != "scenic":
                        continue
                    _region = str(_candidate.get("region_short") or "") or _province_region_of(_candidate)
                    if not _region or _region in _present_regions or _region in _lead_regions:
                        continue
                    _lead_regions.add(_region)
                    _region_leads_missing.append(dict(_candidate))
                if _region_leads_missing:
                    spatial_daytime = list(spatial_daytime) + _region_leads_missing
                    pool_diagnostics.setdefault("province_scope", {})["region_leads_added"] = len(_region_leads_missing)
                    print(
                        f"🗺️ [省域候选] 候选池补齐片区代表 {len(_region_leads_missing)} 个："
                        f"{'、'.join(sorted(_lead_regions))}",
                        flush=True,
                    )

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
            if province_scope_name and province_anchors:
                _dump_regions: Dict[str, int] = {}
                for _row in _day_buckets.get("scenic") or []:
                    _r = str(_row.get("region_short") or "") or _province_region_of(_row) or "本城"
                    _dump_regions[_r] = _dump_regions.get(_r, 0) + 1
                print(
                    f"🗺️ [省域候选] 分桶后景区（补齐前）{sum(_dump_regions.values())} 个：{_dump_regions}",
                    flush=True,
                )
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

            # 👑 省域覆盖（A）· 候选池：让**每个行程片区**至少有一个真景区进入模型看到的候选。
            #    不做这一步，帕累托前沿 + 空间 TSP 会把 300km 外的九寨沟排在后面，再被
            #    `_day_capacity` 按顺序截断 —— 用户要的"四川"最终还是"成都周边"。
            #    顺序按片区聚合（同片区的点连在一起），避免提示词里成都↔阿坝来回横跳。
            if province_scope_name and _day_buckets.get("scenic"):
                _scenic_bucket = list(_day_buckets.get("scenic") or [])
                _present_regions = {str(item.get("region_short") or "") for item in _scenic_bucket}
                _region_leads: List[Dict[str, Any]] = []
                for _candidate in daytime_pois:
                    if _pool_cat(_candidate) != "scenic":
                        continue
                    _region = str(_candidate.get("region_short") or "")
                    if not _region or _region in _present_regions:
                        continue
                    _present_regions.add(_region)
                    _region_leads.append(_candidate)
                if _region_leads:
                    _scenic_bucket = _region_leads + _scenic_bucket
                _region_rows: Dict[str, List[Dict[str, Any]]] = {}
                _region_order: List[str] = []
                for _candidate in _scenic_bucket:
                    # 没有片区标签的候选（多半来自省会本城检索）按最近地州锚点归属，
                    # 否则会凭空多出一个「本城」片区，分片建议把某天排去"从四川省换乘"。
                    _region = str(_candidate.get("region_short") or "") or _province_region_of(_candidate) or "本城"
                    if _region not in _region_rows:
                        _region_rows[_region] = []
                        _region_order.append(_region)
                    _region_rows[_region].append(_candidate)
                _region_first: List[Dict[str, Any]] = []
                _region_rest: List[Dict[str, Any]] = []
                for _region in _region_order:
                    _rows = _region_rows[_region]
                    _region_first.append(_rows[0])
                    _region_rest.extend(_rows[1:])
                _day_buckets["scenic"] = _region_first + _region_rest
                pool_diagnostics.setdefault("province_scope", {})["regions_in_prompt"] = len(_region_order)
                print(
                    f"🗺️ [省域候选] 景区按片区打头：{'、'.join(_region_order[:8])}"
                    f"（{len(_region_first)} 个片区代表进入候选）",
                    flush=True,
                )

                # 👑 省域覆盖（B）· 分片建议：按候选池的真实地理分布算出"哪天在哪片"，写进提示词。
                #    只给规则（"同一天同片区"）还不够：模型仍可能把成都与九寨沟塞进同一天，
                #    然后被下游片区口径判违规再移出 —— 用户看到的就是"地点又变少了"。
                #    先告诉它分片，一次到位。（分片是**建议**，最终取舍仍看候选与用户诉求）
                try:
                    # 只有带坐标的景区参与分片：没有坐标的点各自成一片会把分片算歪
                    _cluster_rows = [
                        item for item in (_day_buckets.get("scenic") or [])
                        if isinstance(item.get("lnglat"), (list, tuple))
                        and len(item.get("lnglat") or []) >= 2
                    ]
                    # ✅ 「片区」按**地州**分，不按 150km 几何聚类：实测（2026-11-01 四川 4 天）
                    #    四姑娘山 / 毕棚沟离成都约 140km，几何聚类会把它们并进「成都」，
                    #    分片建议于是变成"4 天全在成都"，模型又把阿坝景点塞进成都那一天，
                    #    再被片区口径的硬约束判违规移出 —— 用户看到的就是"地点越来越少"。
                    #    这里与 feasibility 的 region_of 用**同一口径**（region_short → 最近地州锚点）。
                    _by_region: Dict[str, List[Dict[str, Any]]] = {}
                    for _item in _cluster_rows:
                        _r = str(_item.get("region_short") or "") or _province_region_of(_item) or "本城"
                        _by_region.setdefault(_r, []).append(_item)
                    _clusters = list(_by_region.values())
                    if _clusters:
                        # 如实记录分片依据：哪个片区有几个候选（出现"全在一个片区"时一眼能看出是供给问题）
                        _cluster_desc = []
                        for _cl in _clusters[:8]:
                            _cl_regions = [
                                str(_x.get("region_short") or "") or _province_region_of(_x) or "未标注"
                                for _x in _cl
                            ]
                            _cluster_desc.append(f"{max(set(_cl_regions), key=_cl_regions.count)}({len(_cl)})")
                        print(f"🗺️ [省域分片] 片区候选：{' / '.join(_cluster_desc)}", flush=True)
                    # 计划已在候选截断之前算过（见上方"先定片区、再补候选"）就直接复用：
                    # 那时用的是**全量候选**，片区判断比截断后的候选池更完整。
                    if not _day_region_plan:
                        _capital_short = short_region_name(target_city)
                        _start_cluster = None
                        for _index, _cluster in enumerate(_clusters):
                            if any(str(item.get("region_short") or "") == _capital_short for item in _cluster):
                                _start_cluster = _index
                                break
                        _plan = assign_cluster_days(
                            _clusters,
                            trip_days,
                            max(1, int(actual_daily_count or 1)),
                            start_cluster=_start_cluster,
                            # 每片区至少 2 天：4 天只排 2 个片区、5 天排 3 个，避免"天天换片区"
                            # （每天一次长途换乘等于一直在赶路，用户明确说过"行程不要太赶"）
                            max_regions=max(2, (trip_days + 1) // 2),
                        )
                        _plan_map: Dict[int, str] = {}
                        for _day_index, _cluster_index in enumerate(_plan.get("day_to_cluster") or [], start=1):
                            if _cluster_index < 0 or _cluster_index >= len(_clusters):
                                continue
                            _names = [
                                str(item.get("region_short") or "") or _province_region_of(item) or "本城"
                                for item in _clusters[_cluster_index]
                            ]
                            if _names:
                                _plan_map[_day_index] = max(set(_names), key=_names.count)
                        if _plan_map:
                            _day_region_plan = _plan_map
                    if _day_region_plan:
                        _segments: List[str] = []
                        _prev_region = ""
                        for _region in dict.fromkeys(_day_region_plan[day] for day in sorted(_day_region_plan)):
                            _days_here = [day for day in sorted(_day_region_plan) if _day_region_plan[day] == _region]
                            _segment = (
                                "、".join(f"Day {day}" for day in _days_here) + f" 在【{_region}】"
                            )
                            if _prev_region:
                                _hours = estimate_rail_hours(
                                    _province_region_center(_prev_region),
                                    _province_region_center(_region),
                                )
                                _segment += (
                                    f"（从{_prev_region}乘高铁/动车约 {_hours:g} 小时）"
                                    if _hours
                                    else f"（从{_prev_region}换乘）"
                                )
                            _segments.append(_segment)
                            _prev_region = _region
                        _day_region_note = "；".join(_segments)
                        # 子片区收敛（B2）：提示词与用户披露里都要写清"这趟只走哪条线、哪条没排"，
                        # 否则用户会以为"四川的自然景观就这么多"。
                        if _sub_cluster_note:
                            _day_region_note += f"（{_sub_cluster_note}）"
                        pool_diagnostics.setdefault("province_scope", {})["day_region_plan"] = {
                            str(day): _day_region_plan[day] for day in sorted(_day_region_plan)
                        }
                        print(f"🗺️ [省域分片] {_day_region_note}", flush=True)
                except Exception as _plan_exc:  # 分片算不出来不影响出方案（规则仍在提示词里）
                    print(f"⚠️ [省域分片] 失败（不影响出方案）: {_plan_exc}", flush=True)

            # 👑 省域覆盖（B）· 候选池只保留**这趟真的会去的片区**。
            #    实测（2026-11-01 四川 4 天）：为了让"全省覆盖"看得见，候选池给 9 个地州各塞了
            #    1 个景区代表；模型很听话，把达州/甘孜/广元/凉山/宜宾的景区全排进 4 天 ——
            #    然后"同一天只在一个片区"的硬约束把这 8 个全移出，景区从 14 掉到 6。
            #    分片建议已经算出"4 天只去成都 + 阿坝"，就别再把别的片区塞给模型：
            #    **覆盖全省**是"检索范围覆盖全省"，不是"4 天塞下 9 个地州"。
            if province_scope_name and province_anchors and _day_region_plan:
                _planned_regions = {
                    str(_region).strip() for _region in _day_region_plan.values() if str(_region or "").strip()
                }
                if _planned_regions:
                    _out_of_plan: Dict[str, int] = {}
                    for _bucket_name in ("scenic", "cultural", "food", "park", "shopping", "other"):
                        _kept_rows = []
                        for _row in list(_day_buckets.get(_bucket_name) or []):
                            if not isinstance(_row, Mapping):
                                continue
                            _row_region = str(_row.get("region_short") or "") or _province_region_of(_row)
                            if _row_region and _row_region not in _planned_regions:
                                _out_of_plan[_row_region] = _out_of_plan.get(_row_region, 0) + 1
                                continue
                            _kept_rows.append(_row)
                        _day_buckets[_bucket_name] = _kept_rows
                    if _out_of_plan:
                        pool_diagnostics.setdefault("province_scope", {})["regions_out_of_plan"] = _out_of_plan
                        print(
                            f"🗺️ [省域候选] 分片只去 {'、'.join(sorted(_planned_regions))}，"
                            f"其余片区候选不进候选池（覆盖了但不塞进这 {trip_days} 天）："
                            + "、".join(f"{name}({count})" for name, count in sorted(_out_of_plan.items())),
                            flush=True,
                        )


            # 👑 省域覆盖（A）· 景区名额按**片区天数**分配：
            #    分片后只剩 1~2 个片区，但桶内顺序仍是"按空间串接"（省会片区排在前面）——
            #    按顺序取 14 个景区会先把成都取光，另一个片区那几天又没有候选可用
            #    （实测：阿坝 2 天只有 3 个可选，模型想排也排不满）。
            #    这里按"片区天数 × 每天景区名额"给每个计划片区留足名额，再交给交错取用。
            if province_scope_name and _day_region_plan and _day_buckets.get("scenic"):
                _scenic_quota = max(1, int((composition or {}).get("scenic", 0) or 0))
                _plan_region_days: Dict[str, int] = {}
                for _region in _day_region_plan.values():
                    _plan_region_days[_region] = _plan_region_days.get(_region, 0) + 1
                _scenic_by_region: Dict[str, List[Dict[str, Any]]] = {}
                _scenic_region_order: List[str] = []
                for _row in list(_day_buckets.get("scenic") or []):
                    if not isinstance(_row, Mapping):
                        continue
                    _row_region = str(_row.get("region_short") or "") or _province_region_of(_row)
                    # 子片区收敛（B2）后，类别下限补齐可能从全量池再拉回"另一条线"的候选，
                    # 这里按同一份白名单再挡一次（否则阿坝又会出现九寨沟+四姑娘山）。
                    _keep_names = _sub_cluster_keep.get(_row_region)
                    if _keep_names is not None and str(_row.get("name") or "") not in _keep_names:
                        continue
                    if _row_region not in _scenic_by_region:
                        _scenic_by_region[_row_region] = []
                        _scenic_region_order.append(_row_region)
                    _scenic_by_region[_row_region].append(_row)
                _quota_total = min(_scenic_quota, sum(len(rows) for rows in _scenic_by_region.values()))
                _region_total_days = max(1, sum(_plan_region_days.get(name, 0) for name in _scenic_region_order))
                _region_share: Dict[str, int] = {}
                _share_assigned = 0
                for _name in _scenic_region_order:
                    _region_share[_name] = int(
                        math.floor(_quota_total * _plan_region_days.get(_name, 0) / _region_total_days)
                    )
                    _region_share[_name] = min(_region_share[_name], len(_scenic_by_region[_name]))
                    _share_assigned += _region_share[_name]
                # 整除余下的名额：先补给**天数多的片区**，再补给还有候选富余的片区
                for _name in sorted(_scenic_region_order, key=lambda name: (-_plan_region_days.get(name, 0), name)):
                    if _share_assigned >= _quota_total:
                        break
                    if _region_share[_name] < len(_scenic_by_region[_name]):
                        _region_share[_name] += 1
                        _share_assigned += 1
                _scenic_kept: List[Dict[str, Any]] = []
                for _name in _scenic_region_order:
                    _scenic_kept.extend(_scenic_by_region[_name][: _region_share.get(_name, 0)])
                _day_buckets["scenic"] = _scenic_kept
                pool_diagnostics.setdefault("province_scope", {})["scenic_slots_by_region"] = dict(_region_share)
                print(
                    f"🗺️ [省域候选] 景区名额按片区天数分配（共 {len(_scenic_kept)} 个）："
                    + "、".join(f"{name}({_region_share.get(name, 0)})" for name in _scenic_region_order),
                    flush=True,
                )

            # 👑 省域覆盖（A）· 先把"计划片区里一个餐饮候选都没有"的片区补进桶。
            #    实测（2026-11-02 四川 4 天，run w）：前沿只按总分取 6 家餐饮，6 家全在成都，
            #    阿坝那两天在桶里**一家餐馆都没有**，补餐的周边检索又刚好是无人区 →
            #    第 4 天没有餐饮节点，判 `skeleton_meal_missing`。
            #    这些餐饮是**按地州名实地检索回来**的（带 region_short），只是被前沿挤掉了；
            #    这里按片区补回来，数量如实进审计。
            if province_scope_name and _day_region_plan and unique_foods:
                _bucket_food_regions = {
                    str(_row.get("region_short") or "") or _province_region_of(_row)
                    for _row in (_day_buckets.get("food") or [])
                    if isinstance(_row, Mapping)
                }
                _region_food_topup = 0
                for _region in dict.fromkeys(_day_region_plan.values()):
                    if _region in _bucket_food_regions:
                        continue
                    _taken = 0
                    for _row in unique_foods:
                        if _taken >= 2:
                            break
                        if not isinstance(_row, Mapping):
                            continue
                        if str(_row.get("region_short") or "") != _region:
                            continue
                        _day_buckets.setdefault("food", []).append(dict(_row))
                        _taken += 1
                    if _taken:
                        _bucket_food_regions.add(_region)
                        _region_food_topup += 1
                if _region_food_topup:
                    pool_diagnostics.setdefault("province_scope", {})["food_region_topup"] = _region_food_topup
                    print(
                        f"🍜 [省域候选] 按片区补足餐饮：{_region_food_topup} 个计划片区原本没有餐饮候选",
                        flush=True,
                    )

            # 👑 省域覆盖（A）· **餐饮名额也按片区天数分配**（与景区同一口径）。
            #    实测（2026-11-02 四川 4 天）：只按顺序取 6 家餐饮，结果 6 家全在成都；
            #    阿坝那两天没有一顿饭可排 —— 补餐要跑 156km（超过 30km 上限）只能不补，
            #    质量闸门判 `skeleton_meal_missing`。片区代表进了景区桶，餐饮桶也得进。
            if province_scope_name and _day_region_plan and _day_buckets.get("food"):
                _food_quota = max(1, int((composition or {}).get("food", 0) or 0))
                _food_plan_days: Dict[str, int] = {}
                for _region in _day_region_plan.values():
                    _food_plan_days[_region] = _food_plan_days.get(_region, 0) + 1
                _food_by_region: Dict[str, List[Dict[str, Any]]] = {}
                _food_region_order: List[str] = []
                for _row in list(_day_buckets.get("food") or []):
                    if not isinstance(_row, Mapping):
                        continue
                    _row_region = str(_row.get("region_short") or "") or _province_region_of(_row)
                    if _row_region not in _food_by_region:
                        _food_by_region[_row_region] = []
                        _food_region_order.append(_row_region)
                    _food_by_region[_row_region].append(_row)
                _food_total = min(_food_quota, sum(len(rows) for rows in _food_by_region.values()))
                _food_total_days = max(1, sum(_food_plan_days.get(name, 0) for name in _food_region_order))
                _food_share: Dict[str, int] = {}
                _food_assigned = 0
                for _name in _food_region_order:
                    _food_share[_name] = min(
                        int(math.floor(_food_total * _food_plan_days.get(_name, 0) / _food_total_days)),
                        len(_food_by_region[_name]),
                    )
                    _food_assigned += _food_share[_name]
                for _name in sorted(_food_region_order, key=lambda name: (-_food_plan_days.get(name, 0), name)):
                    if _food_assigned >= _food_total:
                        break
                    if _food_share[_name] < len(_food_by_region[_name]):
                        _food_share[_name] += 1
                        _food_assigned += 1
                _food_kept: List[Dict[str, Any]] = []
                for _name in _food_region_order:
                    _food_kept.extend(_food_by_region[_name][: _food_share.get(_name, 0)])
                _day_buckets["food"] = _food_kept
                pool_diagnostics.setdefault("province_scope", {})["food_slots_by_region"] = dict(_food_share)
                print(
                    f"🗺️ [省域候选] 餐饮名额按片区天数分配（共 {len(_food_kept)} 个）："
                    + "、".join(f"{name}({_food_share.get(name, 0)})" for name in _food_region_order),
                    flush=True,
                )

            _interleaved = []
            # 自然景区与人文先取、美食次之，城市公园排在其后（用户口径：自然景观要景区，
            # 公园可以去但不该顶掉景区与吃饭的名额）
            _bucket_order = ["scenic", "cultural", "food", "park", "shopping", "other"]
            # 先按**目标配比**取（每类不超过目标），再轮询补满容量。
            # 实测（2026-11-01 四川 4 天，用户"只想看自然"）：原来各类一比一轮询，
            # 20 个白天名额被景区/餐饮分成 10/10 —— 模型看到的景区上限就是 10 个，
            # 而目标配比是景区 14 / 餐饮 6，怎么排都差 4 个（"自然景区 6 个，目标 14 个（偏少）"）。
            _target_quota = {
                str(_key): int(_value or 0)
                for _key, _value in (composition or {}).items()
                if _key in _bucket_order
            }
            _taken_by_cat: Dict[str, int] = {}
            while len(_interleaved) < _day_capacity:
                _advanced = False
                for _c in _bucket_order:
                    _quota = _target_quota.get(_c)
                    if not _day_buckets.get(_c) or (_quota is not None and _taken_by_cat.get(_c, 0) >= _quota):
                        continue
                    _interleaved.append(_day_buckets[_c].pop(0))
                    _taken_by_cat[_c] = _taken_by_cat.get(_c, 0) + 1
                    _advanced = True
                    if len(_interleaved) >= _day_capacity:
                        break
                if not _advanced:
                    break
            # 目标配比满足后还有名额（或某一类供给不足）：不再限制类别，补满当日容量。
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

            # 👑 住宿候选**限量**：目标只是"每晚一家"（外加一点备选），但候选池原来把**全部**
            #    酒店都塞进 top（实测 `hotel: 6~8` 而目标是 1~2 个）—— 于是模型以为"这趟要住好几家"，
            #    路线里排出 4 个住宿。给出 `晚数 + 1` 家备选就够了，不挤占景区/人文/美食的名额。
            _lodging_slots = max(2, max(1, trip_days - 1) + 1)
            _room_candidates = list(sorted_hotels[:_lodging_slots])
            if len(sorted_hotels) > len(_room_candidates):
                pool_diagnostics["lodging_capped"] = {
                    "kept": len(_room_candidates),
                    "dropped": len(sorted_hotels) - len(_room_candidates),
                    "nights": max(1, trip_days - 1),
                }
                print(
                    f"🛏️ [住宿候选限量] 目标 {max(1, trip_days - 1)} 晚，只给模型 {len(_room_candidates)} 家备选"
                    f"（其余 {len(sorted_hotels) - len(_room_candidates)} 家不进候选，避免排出一堆住宿）",
                    flush=True,
                )

            top_candidates = _interleaved + _room_candidates
            final_candidates = top_candidates
            if _snap.max_lag > 0.5:
                print(f"⏱️ [卡顿·分桶与均衡] {_snap.summary()}", flush=True)
            _snap.stop()

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
                # 富化失败 → 一律**空串**：让下面如实写「暂无简介」，不要编一句"高人气"顶上
                web_knowledge_list = [""] * len(search_candidates)
            _stage("候选池与增强")

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

                web_info = web_knowledge_list[i] if i < len(web_knowledge_list) and isinstance(web_knowledge_list[i], str) else ""
                # 校验过的联网简介才算；取不到就留空（下面 `description_of` 会写成"暂无简介"）
                web_info = clean_web_knowledge(web_info, str(p.get('name') or ''), target_city)
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
                    # 类别是不是**高德真给的**：`type` 的占位默认值不能挂在"高德资料："后面当事实
                    'type_source': 'amap' if str(p_type or '').strip() else 'default',
                    # ⚠️ 地址/商圈必须带下去（2026-10-05 实测：这里漏了 `address`，
                    #    于是节点里 `address` 一律是空串 —— 高德明明给了地址却被丢掉，
                    #    用户看到的是"没有地址"，把"我们没传"显示成"我们不知道"）
                    'address': str(p.get('address') or ''),
                    'business_area': str(p.get('business_area') or ''),
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
                    # 省域行程：这个点属于哪个地州/片区（保底合成按片区排、下游按片区判可行性；
                    # 非省域请求这里是空串，对其它链路没有含义）
                    'region_short': str(p.get('region_short') or ''),
                    'region_city': str(p.get('region_city') or ''),
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
                    # 省域行程：这个点属于哪个地州/片区（同一天只在一个片区，跨片区要整段移动）。
                    # 非省域请求这里是空串，对模型没有额外含义。
                    'region_city': str(p.get('region_city') or p.get('neighbor_region') or ''),
                    'data_sources': p.get('data_sources') or {'rating': 'unavailable', 'cost_estimate': 'unavailable', 'open_time': 'unavailable'},
                    'estimated': bool(p.get('estimated', True)),
                    'hint': short_hint
                })

            # 文案口径修正（2026-10-10）：这里以前对用户说"纳什博弈完成/收敛"，但这条链路上
            # 实际跑的是 `ParetoFrontierSelector.resolve_conflicts` —— 它做的是**帕累托非支配
            # 筛选**（多目标：fitness↑ / cost↓ / risk↓），全程没有计算任何纳什均衡。类名曾是历史
            # 遗留（旧名 `NashEquilibriumSolver`），不该由它把一句不成立的话带到用户面前。DBSCAN
            # 那半句是真的（紧接着的 `GraphRouteOptimizer.optimize_and_sort` 确实做地理聚类 +
            # TSP 近似），所以保留。
            yield json.dumps({"token": f"[时空调度体]: DBSCAN 空间降维与帕累托前沿筛选完成！正在交织生成覆盖完整 {trip_days} 天的高密路书...\n\n"}, ensure_ascii=False) + "\n"
            async for _e in emit_reasoning("时空调度体", "claim", f"地理聚类（DBSCAN）与非支配筛选（帕累托前沿）完成，交织生成 {trip_days} 天高密路书", "按地理邻近聚类节点，再用帕累托非支配排序保留 fitness 高、成本低、风险低的候选，并均衡景点/美食/住宿三类配比"):
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

            # 👑 省域覆盖（A）· 提示词规则：目的地是"省"时，原来那句
            #    「所有景点必须严格属于【成都】」会直接禁止模型选九寨沟 —— 必须先说清范围。
            if province_scope_name:
                _dest_scope_line = (
                    f"当前任务：规划【{province_scope_name}】完整【{trip_days}天】的深度团队行程"
                    f"（目的地是**省域**，不是单座城市）。"
                )
                _scope_warning = (
                    f"你安排的所有景点、餐厅、酒店必须属于【{province_scope_name}】；"
                    "同一天只能安排同一个片区（region_city 相同）的地点。"
                )
                _province_scope_rules = (
                    "\n【🔴 省域覆盖规则（目的地是省，不是一座城）】\n"
                    "1. 候选里的 `region_city` 是地州/片区标签；同一天只选同一片区的地点。\n"
                    "2. 跨片区必须整段换乘（高铁/动车优先），并在当天第一个节点写明交通方式与耗时"
                    "（例：成都→九寨沟约 2 小时动车 + 接驳车）。\n"
                    "3. 不要把同一天拆成两个相距 300km 的点；宁可在同一片区玩透。\n"
                    "4. 住宿按当天片区选：优先选 `region_city` 与当天景点相同的酒店。\n"
                    "5. 跨城耗时是估算（直线距离/200km/h + 进出站），实际班次请以 12306 为准。\n"
                )
                if _day_region_note:
                    _province_scope_rules += f"6. 推荐分片（按这个排，别把一天拆到两片）：{_day_region_note}。\n"
                _province_scope_rules += (
                    f"本次已检索的片区：{'、'.join(str(item.get('name') or '') for item in province_anchors)}。\n"
                )
            else:
                _dest_scope_line = f"当前任务：规划【{target_city}】完整【{trip_days}天】的深度团队行程。"
                _scope_warning = f"你安排的所有景点、餐厅、酒店必须严格属于【{target_city}】！"
                _province_scope_rules = ""

            # 👑 严格保证天数输出、每日 5 节点、真实花费与干净外链的 Prompt
            system_prompt = f"""
你是一个专业的团队旅行协同规划专家。{_dest_scope_line}
【重要警告：绝对禁止越界！】{_scope_warning}

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
{_province_scope_rules}

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
            print("🔥 [算法计算崩溃 Traceback]:")
            traceback.print_exc()
            yield json.dumps({"token": f"\n[系统异常] 算法引擎计算失败，已停止本次推演，请稍后重试。\n[FINAL_JSON]\n{{\"status\": \"error\", \"negotiation_summary\": \"算法计算异常\", \"route\": []}}\n"}, ensure_ascii=False) + "\n"
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
                # ⚠️ 这里原来写的是 `self.amap_key` —— `event_stream` 是嵌套生成器，**没有 self**，
                #    于是"静态地图保底"这条路只要走到就 NameError（实测：整条下发链路被带走，
                #    用户"未收到终稿"）。密钥在 `toolbox` 上（`ExpertToolbox` 实例），用 getattr 取。
                _map_key = getattr(toolbox, "amap_key", None)
                if not _map_key:
                    return ""
                loc_str = f"{lng},{lat}"
                return (
                    f"https://restapi.amap.com/v3/staticmap"
                    f"?location={loc_str}&zoom={zoom}&size={size}&scale=2"
                    f"&markers=mid,0xFF5722,A:{loc_str}"
                    f"&key={_map_key}"
                )

            # —— 👑 候选池合成兜底：LLM 彻底失败时仍能给前端一份完整路线（防死锁）——
            # 实景图补齐的收集区（Post-Route 里逐节点收集，最后统一并发查询 + 兜底图）
            _photo_targets: List[Dict[str, Any]] = []
            def _synthesize_from_pool(reason: str = ""):
                """从 poi_pool_data 合成行程：按配比分类别取点，标签只描述类别本身。

                2026-11-01 真实事故：旧实现 `rng.shuffle` 后按顺序取点，并用槽位序号
                贴标签（「楠木沟风景区」被贴成「美食·夜市」）。下游类别判定把标签
                当证据，于是真景区被算成餐饮，3 天方案最后是 8 家餐厅 + 1 个景区。
                现在取点交给 core.fallback_synthesis（景区优先、按地理就近），
                标签也只写「自然·风景区」这类可核实的事实。
                """
                if not poi_pool_data:
                    return [], f"AI 服务异常且无候选池数据：{reason or '服务暂时不可用，请稍后重试'}"
                from core.fallback_synthesis import plan_fallback_picks, tags_for

                planned = plan_fallback_picks(
                    poi_pool_data,
                    days=trip_days,
                    daily_nodes=actual_daily_count,
                    targets=composition,
                    # 省域保底也要按片区排：不给分片建议时这两个参数不起作用（非省域行为不变）
                    day_regions=_day_region_plan,
                    # ⚠️ 必须用 `_province_region_of`：候选池里的点有些只有 `region_city`（“阿坝藏族羌族自治州”），
                    #    有些干脆没有片区标签（来自省会检索）。只读标签会把“阿坝”和“阿坝藏族羌族自治州”
                    #    当成两个片区，`_scoped` 全部落空 → 退回全池 → 阿坝的点被摊到每一天
                    #    （实测 2026-11-02：Day 1 成都却排了毕棚沟，Day 4 成都↔阿坝来回两趟 622km）。
                    region_of=_province_region_of,
                )
                time_slots = ["08:30", "10:30", "12:30", "14:30", "17:30", "19:30", "21:30"]
                syn = []
                for pick in planned["picks"]:
                    chosen = pick.get("item")
                    if not isinstance(chosen, dict):
                        continue
                    day = int(pick.get("day") or 1)
                    pick_category = str(pick.get("category") or "other")
                    is_hotel = pick_category == "hotel"
                    # 城市公园在保底排布里单列一档（不占自然景区名额），
                    # 但下游类别口径只有 scenic/cultural/food/shopping/hotel，这里映射回 scenic 并保留 tier。
                    category = "scenic" if pick_category == "city_park" else pick_category
                    slot_i = int(pick.get("slot") or 0)
                    lnglat = chosen.get("lnglat") or [0.0, 0.0]
                    photos = chosen.get("photos") if isinstance(chosen.get("photos"), list) else []
                    map_image = chosen.get("map_image") or _make_static_map(lnglat)
                    # 简介口径：能核实的（校验过的联网简介 / 供应商原文）才用，否则如实说"暂无简介"
                    desc, desc_source = _desc_for(chosen, target_city)
                    node = {
                        "day": day,
                        "time": f"Day {day} | 21:30 入住" if is_hotel
                        else f"Day {day} | {time_slots[min(slot_i, len(time_slots) - 2)]} (建议游玩 1.5 小时)",
                        "location": chosen.get("name"),
                        "name": chosen.get("name"),
                        "lnglat": lnglat,
                        "type": chosen.get("type") or ("酒店住宿" if is_hotel else "风景"),
                        "desc": desc,
                        "desc_source": desc_source,
                        "transport": "自驾/打车",
                        "tags": tags_for(category, chosen),
                        "planning_category": category,
                        "cost_estimate": chosen.get("cost_estimate") or "暂无供应商数据",
                        "photos": photos,
                        "amap_url": chosen.get("amap_url") or "",
                        "map_image": map_image,
                        "rating": chosen.get("rating") or "暂无供应商数据",
                        "open_time": chosen.get("open_time") or "暂无供应商数据",
                        "is_hotel": is_hotel,
                        "split_info": "",
                        "merge_point": False,
                        "trust_reason": "智能体降级合成 · 候选池酒店兜底" if is_hotel
                        else "智能体降级合成 · 基于真实候选池数据",
                    }
                    # 片区标签跟着节点走：下游按片区判"同一天是否跨片"时有据可依
                    if chosen.get("region_short") or chosen.get("region_city"):
                        node["region_city"] = str(chosen.get("region_city") or chosen.get("region_short") or "")
                    if chosen.get("scenic_tier") or pick_category == "city_park":
                        node["scenic_tier"] = chosen.get("scenic_tier") or "city_park"
                    syn.append(node)

                audit = planned["audit"]
                by_category = audit["by_category"]
                shortfall = int(audit["requested_daytime"]) - int(audit["planned_daytime"])
                gap = f" 候选池只够排 {audit['planned_daytime']}/{audit['requested_daytime']} 个白天节点。" if shortfall > 0 else ""
                summary = (
                    f"⚠️ 大模型服务暂时不可用（{reason or '服务暂时不可用，请稍后重试'}），已基于高德候选池合成 "
                    f"{trip_days} 天保底路线（景区 {int(by_category.get('scenic', 0))} 个、"
                    f"餐饮 {int(by_category.get('food', 0))} 个）。{gap}"
                    "可点击「换一换」重试生成更高质量方案。"
                )
                return syn, summary

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
                _missing_started = time.monotonic()
                try:
                    resp = await client.chat.completions.create(
                        model=MODEL_NAME,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=llm_cfg.temperature(),
                        max_tokens=llm_cfg.max_tokens(),
                    )
                except Exception as _missing_err:
                    record_llm_call(
                        model=MODEL_NAME,
                        label="missing_days",
                        latency_ms=(time.monotonic() - _missing_started) * 1000.0,
                        ok=False,
                        error=type(_missing_err).__name__,
                    )
                    raise
                _missing_prompt_tokens, _missing_completion_tokens = _llm_usage_tokens(resp)
                record_llm_call(
                    model=MODEL_NAME,
                    label="missing_days",
                    latency_ms=(time.monotonic() - _missing_started) * 1000.0,
                    ok=True,
                    prompt_tokens=_missing_prompt_tokens,
                    completion_tokens=_missing_completion_tokens,
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
            _stage("提示词组装")
            llm_call_succeeded = False
            last_llm_err: Optional[Exception] = None
            for attempt in range(LLM_MAX_RETRIES + 1):
                _attempt_started = time.monotonic()
                _first_token_at: Optional[float] = None
                try:
                    if attempt > 0:
                        wait_s = min(2 ** attempt, 6)
                        yield json.dumps({"token": f"\n[连接自愈]: 第 {attempt}/{LLM_MAX_RETRIES} 次重试（等待 {wait_s}s）...\n"}, ensure_ascii=False) + "\n"
                        await asyncio.sleep(wait_s)
                    # 每次尝试前重置 full_response，避免前次失败残留干扰解析
                    full_response = ""
                    # 连接前提示，让日志实时反映阶段切换
                    yield json.dumps({"token": f"\n[语言模型]: 正在接入 {MODEL_NAME} 生成主方案，随后由专家智能体并行提案、成员协商定稿（attempt {attempt+1}/{LLM_MAX_RETRIES+1}）...\n"}, ensure_ascii=False) + "\n"
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
                    _llm_started = time.monotonic()
                    _prompt_chars = len(str(system_prompt or ""))

                    yield json.dumps({"token": "[神经链路已建立] 开始接收流式推演 tokens...\n"}, ensure_ascii=False) + "\n"
                    token_count = 0
                    async for chunk in response:
                        if chunk.choices and len(chunk.choices) > 0:
                            delta = chunk.choices[0].delta
                            if delta and delta.content:
                                if _first_token_at is None:
                                    _first_token_at = time.monotonic()
                                token = delta.content
                                full_response += token
                                token_count += 1
                                yield json.dumps({"token": token}, ensure_ascii=False) + "\n"
                                # 长生成中定期给出生成进度
                                if token_count % 600 == 0:
                                    yield json.dumps({"token": f"\n[生成进度] 已输出 {token_count} tokens，正在编织完整 {trip_days} 天行程...\n"}, ensure_ascii=False) + "\n"
                    llm_call_succeeded = True
                    _llm_seconds = max(0.001, time.monotonic() - _llm_started)
                    # 记一笔 LLM 账：流式拿不到 usage，token 数按实际收到的 chunk 数记，
                    # prompt token 留空（不按字符数硬凑），成本因此只在非流式调用上可算。
                    record_llm_call(
                        model=MODEL_NAME,
                        label="main_plan",
                        latency_ms=(time.monotonic() - _attempt_started) * 1000.0,
                        ok=True,
                        streamed=True,
                        completion_tokens=token_count,
                        first_token_ms=(
                            (_first_token_at - _attempt_started) * 1000.0 if _first_token_at is not None else None
                        ),
                    )
                    # 👑 主推演耗时账（2026-10-05）：这一段现在是总时长里唯一的大头（44~69s / 占 6 成以上），
                    #    所以必须能回答"是提示词太长、还是模型吐字太慢、还是有重试"。
                    print(
                        f"⏱️ [主推演] prompt={_prompt_chars} 字符（≈{_prompt_chars // 2} tokens）"
                        f"｜输出≈{token_count} tokens｜{_llm_seconds:.1f}s"
                        f"（≈{token_count / _llm_seconds:.1f} tok/s）｜attempt={attempt + 1}"
                        f"｜max_tokens={llm_cfg.max_tokens(long_output=True)}",
                        flush=True,
                    )
                    break
                except (BadRequestError,) as e:
                    # 平台侧业务错误（如欠费/参数错）：不重试
                    last_llm_err = e
                    record_llm_call(
                        model=MODEL_NAME,
                        label="main_plan",
                        latency_ms=(time.monotonic() - _attempt_started) * 1000.0,
                        ok=False,
                        streamed=True,
                        error=type(e).__name__,
                    )
                    print(f"⚠️ [LLM 业务错误]: {e}", flush=True)
                    yield json.dumps({"token": "\n[系统告警] 大模型服务拒绝了本次请求，正在切换候选池保底模式…\n"}, ensure_ascii=False) + "\n"
                    break
                except Exception as e:
                    last_llm_err = e
                    err_name = type(e).__name__
                    record_llm_call(
                        model=MODEL_NAME,
                        label="main_plan",
                        latency_ms=(time.monotonic() - _attempt_started) * 1000.0,
                        ok=False,
                        streamed=True,
                        error=err_name,
                    )
                    print(f"⚠️ [LLM 第 {attempt+1} 次调用失败] {err_name}: {e}", flush=True)
                    yield json.dumps({"token": "\n[连接自愈] 大模型服务暂时无响应，正在重试连接…\n"}, ensure_ascii=False) + "\n"
                    continue

            _stage("主推演输出")
            if not llm_call_succeeded:
                # 所有重试都失败：显式错误 + 候选池兜底路线，保证前端 100% 收到 final_route
                err_msg = str(last_llm_err) if last_llm_err else "未知错误"
                friendly = (f"AI 大模型服务暂时不可达（{_friendly_llm_error(last_llm_err)}），"
                            f"系统已自动切换至【候选池保底模式】：路线基于高德真实 POI 合成，图片/导航/花费均可正常使用。"
                            f"可稍后点击「换一换」重试高质量智能体推演。")
                print(f"🔥 [LLM 彻底失败] {err_msg}，切换至候选池兜底", flush=True)
                yield json.dumps({"type": "error", "payload": friendly}, ensure_ascii=False) + "\n"
                yield json.dumps({"token": f"\n[系统兜底] {friendly}\n"}, ensure_ascii=False) + "\n"

                syn_routes, syn_summary = _synthesize_from_pool(_friendly_llm_error(last_llm_err))
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
                # 👑 兜底路线同样做目的地范围校验（2026-11-02）：保底合成虽然取自候选池，
                #    仍可能与主路径一样带上"别的城市的坐标"。与主路径共用同一套口径与披露字段，
                #    避免"降级模式下坐标不受约束"这种只在异常时才出现的漏洞。
                _fallback_area_report: Optional[Dict[str, Any]] = None
                try:
                    from core.place_quality import (
                        clear_out_of_area_coordinates,
                        poi_coordinate_radius_km,
                    )

                    _fallback_centers: List[List[float]] = []
                    if isinstance(city_lnglat, (list, tuple)) and len(city_lnglat) >= 2:
                        _fallback_centers.append([float(city_lnglat[0]), float(city_lnglat[1])])
                    if province_scope_name and province_anchors:
                        for _anchor in province_anchors:
                            _anchor_center = _anchor.get("center") or []
                            if isinstance(_anchor_center, (list, tuple)) and len(_anchor_center) >= 2:
                                _fallback_centers.append(
                                    [float(_anchor_center[0]), float(_anchor_center[1])]
                                )
                    _fallback_radius = poi_coordinate_radius_km(
                        trip_days,
                        str(own_signals.get("distance_tolerance") or "normal"),
                        province_scope=bool(province_scope_name and province_anchors),
                    )
                    _fallback_outside = clear_out_of_area_coordinates(
                        {"route": syn_routes}, centers=_fallback_centers, radius_km=_fallback_radius, copy=False
                    )
                    if _fallback_outside:
                        _fallback_area_report = {
                            "names": [str(item.get("name") or "") for item in _fallback_outside],
                            "radius_km": round(_fallback_radius, 1),
                            "centers": _fallback_centers,
                            "items": _fallback_outside,
                            "note": "这些节点的坐标离目的地超出可信范围（跨城/跨省的错误坐标）："
                                    "已按无效清除，不参与里程与距离判断，地图上不标位置；名称等其他字段保留。",
                        }
                        print(
                            "🧹 [坐标校验·兜底] "
                            f"{len(_fallback_outside)} 个坐标超出 {_fallback_radius:.0f}km 可信范围："
                            + "、".join(
                                f"{item.get('name') or '(未命名)'}（{item.get('distance_km')}km）"
                                for item in _fallback_outside[:4]
                            ),
                            flush=True,
                        )
                except Exception as _fallback_area_exc:  # 校验失败不影响出方案
                    print(f"⚠️ [坐标校验·兜底] 失败（不影响出方案）: {_fallback_area_exc}", flush=True)
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
                if _fallback_area_report:
                    final_data["out_of_area_coordinates"] = _fallback_area_report
                # 兜底路线同样清理"暂无供应商数据"占位：降级模式的界面不该更吵。
                try:
                    _fallback_fact_cleared = clear_placeholder_supplier_facts(final_data, copy=False)
                    _fallback_fact_gap = supplier_facts_disclosure(_fallback_fact_cleared)
                    if _fallback_fact_gap:
                        final_data["supplier_data_gaps"] = _fallback_fact_gap
                except Exception as _fallback_fact_exc:  # 清洗失败不影响出方案
                    print(f"⚠️ [供应商事实·兜底] 清洗失败: {_fallback_fact_exc}", flush=True)
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
                            # 置换之后简介也得**跟着换**，并且只能换成有出处的（否则如实说"暂无简介"）
                            r["desc"], r["desc_source"] = _desc_for(
                                fallback, target_city, provider_desc=r.get("desc")
                            )
                            r["address"] = fallback.get("address") or r.get("address") or ""
                            r["cost_estimate"] = fallback.get("cost_estimate") or r.get("cost_estimate") or "暂无供应商数据"
                            r["is_hotel"] = bool(fallback.get("is_hotel", False))
                            r["trust_reason"] = "匹配度校验：未识别地点已置换为候选池真实 POI（100% 匹配）"
                            _fill_from_match(r, fallback)
                            rebind_stats["swapped"] += 1
                        else:
                            # 👑 候选池耗尽：保留原节点（保留LLM原名称），但强制按坐标生成静态地图兜底
                            if not r.get("amap_url") and loc_name:
                                r["amap_url"] = f"https://www.amap.com/search?query={urllib.parse.quote(f'{target_city} {loc_name}')}"
                            # 强制静态地图兜底：**只用自己的坐标**。
                            # ⚠️ 这里原来还有一条 `elif hasattr(self, 'center_lon') …` 拿"城市中心"
                            #    当这个节点的示意图 —— 两处问题：① 这个嵌套函数里没有 `self`，
                            #    走到就 NameError（实测把整条下发链路带走）；② 拿城市中心当某个
                            #    节点的位置示意图本身就是在标错位置。没有坐标就交给
                            #    `attach_photo_fallbacks` 用带"位置示意，非实景照片"标注的影像兜底。
                            if not r.get("map_image"):
                                if isinstance(r.get("lnglat"), list) and len(r["lnglat"]) >= 2:
                                    r["map_image"] = _make_static_map(r["lnglat"])
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
                        _n_desc, _n_desc_src = _desc_for(_p, target_city)
                        return {
                            "day": _d,
                            "name": _p.get("name") or "特色地标",
                            "location": _p.get("name") or "特色地标",
                            "lnglat": _p.get("lnglat") or [0.0, 0.0],
                            "type": _p.get("type") or "风景",
                            "desc": _n_desc,
                            "desc_source": _n_desc_src,
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
                            # ⚠️ 这里原来写的是 `hasattr(self, 'center_lon')` —— 这个**嵌套生成器里没有
                            #    `self`**，一旦走到这一行就 `NameError`，把整条下发链路带走：
                            #    实测（2026-10-05）伊宁某次 geocode 拿不到中心 → 用户"未收到终稿"，
                            #    日志只有一句 `🔥 [真实路网拼装失败]: NameError: name 'self' is not defined`。
                            #    而且"拿城市中心当这个节点的示意图"本身就是在标错位置：
                            #    没有坐标就**不给图**，交给 `attach_photo_fallbacks` 用带
                            #    "位置示意，非实景照片"标注的影像兜底（如实，不误导）。
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
                                _s_desc, _s_desc_src = _desc_for(p, target_city)
                                synth_nodes.append({
                                    "day": d,
                                    "name": p.get("name") or "特色地标",
                                    "location": p.get("name") or "特色地标",
                                    "lnglat": p.get("lnglat") or [0.0, 0.0],
                                    "type": p.get("type") or "风景",
                                    "desc": _s_desc,
                                    "desc_source": _s_desc_src,
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
                        _h_desc, _h_desc_src = _desc_for(p, target_city)
                        return {
                            "day": day,
                            "name": p.get("name") or "特色地标",
                            "location": p.get("name") or "特色地标",
                            "lnglat": _lnglat,
                            "type": p.get("type") or ("酒店住宿" if _hotel else "风景"),
                            "desc": _h_desc,
                            "desc_source": _h_desc_src,
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
                    def _fairness_members_from_room() -> List[Dict[str, Any]]:
                        """把房间成员转成 fairness 与成员协商共用的口径。

                        此前这里只保留 `id + interestTags=[整句 intent]`：role、兴趣标签、
                        是否愿意让步全部丢失，fairness 只能对所有成员给出同一条"未知偏好"
                        分数，成员协商也就无从谈起。现在统一用 `member_interests` 抽取标签，
                        两边看到的是同一组诉求。
                        """
                        rows: List[Dict[str, Any]] = []
                        for index, member in enumerate(room_members):
                            if not isinstance(member, dict):
                                continue
                            interests = member_interests(member)
                            rows.append({
                                "id": str(member.get("id") or member.get("name") or f"member-{index + 1}"),
                                "name": str(member.get("name") or member.get("nickname") or f"成员 {index + 1}"),
                                "role": str(member.get("role") or ""),
                                "interests": interests,
                                "interestTags": interests,
                                "non_negotiable": bool(member.get("non_negotiable")),
                                "tolerance": member.get("tolerance"),
                                "concession_budget": member.get("concession_budget"),
                            })
                        return rows

                    fairness_members = _fairness_members_from_room()
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

                            # 👥 真正的成员级协商（P1）：fairness_gate 只做群体层面的 maximin，
                            #    这里让房间里的**每一位真实成员**独立排序、提出首选，再由协调者
                            #    逐轮尝试"高满意度成员让位"的候选切换。协商只在硬约束筛过的
                            #    可行方案之间进行，不会把已被 L0 淘汰的方案又拉回来。
                            def _route_label(route_index: int) -> str:
                                if route_index == 0:
                                    return "AI 主方案"
                                if 0 < route_index <= len(plan_variants):
                                    return str(plan_variants[route_index - 1].get("name") or f"候选方案 {route_index}")
                                return f"候选方案 {route_index}"

                            _negotiation_scope = _report_route_indices or list(range(len(fair_routes)))
                            try:
                                _member_negotiation = negotiate_member_routes(
                                    [fair_routes[i] for i in _negotiation_scope],
                                    fairness_members,
                                    candidate_labels=[_route_label(i) for i in _negotiation_scope],
                                    max_rounds=2,
                                )
                                _local_index = int(_member_negotiation.get("selected_index") or 0)
                                _negotiated_index = (
                                    _negotiation_scope[_local_index]
                                    if 0 <= _local_index < len(_negotiation_scope)
                                    else selected_index
                                )
                                # 对外统一用"路线索引"，与 fairness.selected_variant_index 同一口径；
                                # 局部索引另存，便于回溯协商是在哪个子集里发生的。
                                _member_negotiation["local_selected_index"] = _local_index
                                _member_negotiation["scope_indices"] = list(_negotiation_scope)
                                _member_negotiation["selected_index"] = _negotiated_index
                                if isinstance(_member_negotiation.get("final_candidate"), dict):
                                    _member_negotiation["final_candidate"]["index"] = _negotiated_index
                                if _negotiated_index != selected_index:
                                    selected_index = _negotiated_index
                                    _selection_method = "maximin_then_member_concession"
                                    final_data["constraints_engine"]["selection_method"] = _selection_method
                                final_data["member_negotiation"] = _member_negotiation
                                print(
                                    f"🤝 [成员协商] {_member_negotiation.get('status')}"
                                    f"｜最终《{(_member_negotiation.get('final_candidate') or {}).get('name')}》"
                                    f"｜异议 {len(_member_negotiation.get('dissenters_final') or [])} 人"
                                    f"｜让步 {len(_member_negotiation.get('concessions') or [])} 次",
                                    flush=True,
                                )
                            except Exception as _nego_err:
                                # 协商失败绝不能连累主方案：如实标记 not_run，前端据此显示"未执行"。
                                final_data["member_negotiation"] = {
                                    "status": "not_run",
                                    "method": "maximin_plus_member_concession",
                                    "transcript": [f"成员协商未执行：{_nego_err}"],
                                }
                                print(f"⚠️ [成员协商] 未执行（不影响主方案）: {_nego_err}", flush=True)

                            if selected_index > 0:
                                llm_routes = list(fair_routes[selected_index])
                                final_data["route"] = llm_routes
                            selected_variant_index = selected_index - 1 if selected_index > 0 else None
                            # 协商可能改变最终方案，公平报告必须按**最终选中**的那条路重算，
                            # 不能沿用 fair_selection 里旧方案的报告。
                            fairness_base = fairness_report(fair_routes[selected_index], fairness_members)
                            _candidate_reports = []
                            for _report_index, _report in enumerate(fair_selection.get("reports", [])):
                                _report_copy = dict(_report)
                                _route_index = _report_route_indices[_report_index] if _report_index < len(_report_route_indices) else _report_index
                                _report_copy["candidate_name"] = _route_label(_route_index)
                                _report_copy["candidate_index"] = _route_index
                                _candidate_reports.append(_report_copy)
                            _fairness_threshold = _fairness_limit_from_env()
                            _fairness_exceeded = float(fairness_base.get("utility_stddev", 0)) > _fairness_threshold
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

                    # 单方案/多方案生成失败时也要留下成员协商记录：至少让每位成员对
                    # 主方案给出首选与满意度，而不是让前端整块消失（消失本身无法区分
                    # "没有分歧"和"根本没算"）。
                    if not final_data.get("member_negotiation"):
                        try:
                            final_data["member_negotiation"] = negotiate_member_routes(
                                [final_data.get("route") or llm_routes or []],
                                fairness_members,
                                candidate_labels=["AI 主方案"],
                                max_rounds=2,
                            )
                        except Exception as _nego_err:
                            final_data["member_negotiation"] = {
                                "status": "not_run",
                                "method": "maximin_plus_member_concession",
                                "transcript": [f"成员协商未执行：{_nego_err}"],
                            }

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
                    # 👑 收口之后要按**最终路线**重算质量分（与 degradations/facts 同一处、同一理由）：
                    #    重算需要这一段里的 context/signals/候选池，但它们只在这个分支里才被赋值，
                    #    所以先在分支开头置空 —— 否则"分支没走到却引用"会抛 NameError，
                    #    而重算又包在 `except: print` 里，会**静默**变成"没重算"。
                    _quality_context_for_final: Optional[Dict[str, Any]] = None
                    _quality_signals_for_final: Optional[Dict[str, Any]] = None
                    _quality_increment_for_final: Optional[Dict[str, Any]] = None
                    _quality_pool_for_final: Optional[Dict[str, Any]] = None
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
                                    _segment_started = time.monotonic()
                                    try:
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
                                    except Exception as _seg_err:
                                        record_llm_call(
                                            model=MODEL_NAME,
                                            label="long_trip_segment",
                                            latency_ms=(time.monotonic() - _segment_started) * 1000.0,
                                            ok=False,
                                            error=type(_seg_err).__name__,
                                        )
                                        raise
                                    _seg_prompt_tokens, _seg_completion_tokens = _llm_usage_tokens(_resp)
                                    record_llm_call(
                                        model=MODEL_NAME,
                                        label="long_trip_segment",
                                        latency_ms=(time.monotonic() - _segment_started) * 1000.0,
                                        ok=True,
                                        prompt_tokens=_seg_prompt_tokens,
                                        completion_tokens=_seg_completion_tokens,
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
                                    _repair_started = time.monotonic()
                                    _repair_recorded = False
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
                                        _repair_prompt_tokens, _repair_completion_tokens = _llm_usage_tokens(_seg_resp)
                                        record_llm_call(
                                            model=MODEL_NAME,
                                            label="long_trip_repair",
                                            latency_ms=(time.monotonic() - _repair_started) * 1000.0,
                                            ok=True,
                                            prompt_tokens=_repair_prompt_tokens,
                                            completion_tokens=_repair_completion_tokens,
                                        )
                                        _repair_recorded = True
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
                                    except Exception as _repair_err:
                                        if not _repair_recorded:
                                            record_llm_call(
                                                model=MODEL_NAME,
                                                label="long_trip_repair",
                                                latency_ms=(time.monotonic() - _repair_started) * 1000.0,
                                                ok=False,
                                                error=type(_repair_err).__name__,
                                            )
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

                            # 「个性化」评分只看发起人自己（本轮+历史轮）——
                            # 成员诉求归 fairness/team_satisfaction，别拿它扣发起人的分（见上方裁决说明）
                            _quality_signals = scoring_signals if isinstance(scoring_signals, dict) else None
                            # 用户说过"不想跑远"时，替换点要按"离市中心多远"来挑 —— 市中心坐标
                            # 来自 geocode（拿不到就传空，退回原行为：不按距离偏好挑，但也不会编）
                            _preferred_near_center: List[float] = []
                            _center_text_for_near = str(geo_info.get("center") or coord or "")
                            if "," in _center_text_for_near:
                                try:
                                    _near_lng, _, _near_lat = _center_text_for_near.partition(",")
                                    _preferred_near_center = [float(_near_lng), float(_near_lat)]
                                except ValueError:
                                    _preferred_near_center = []
                            _quality_context = {
                                "days": trip_days,
                                "budget": total_calc_budget,
                                # 距离口径进质量上下文：完整性核对按用户口径判断"日内距离是否达标"
                                # （说"不介意跑远"的人被按 normal 预算判"跑太远"是误判）。
                                "distance_tolerance": str((own_signals or {}).get("distance_tolerance") or "normal"),
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
                                # ⚠️`known_names` 必须**同时包含候选池的名字**（2026-10-05 实测）：
                                #   用户点「换住这家」时点名的是**候选池里那一家**（还不在方案里），
                                #   只喂方案节点名 → 解析器认不出这个名字 → `swap_to` 为空 →
                                #   点了按钮却什么都没换（真跑验证时才发现的）。
                                _pool_names: List[str] = []
                                for _pool_item in (
                                    list(cleaned_hotels or [])
                                    + list(unique_attractions or [])
                                    + list(unique_foods or [])
                                ):
                                    if isinstance(_pool_item, Mapping):
                                        _pool_name = str(_pool_item.get("name") or "").strip()
                                        if _pool_name and _pool_name not in _pool_names:
                                            _pool_names.append(_pool_name)
                                _refinement_delta = parse_increment(
                                    intent_str,
                                    previous_plan=final_data,
                                    known_names=_known_names + _pool_names,
                                    city=target_city,
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
                                # 👎 用户点过"不喜欢"的地点同样是排除项（不只是这一轮打的字）：
                                # 没有这一步，反馈只进画像不进方案 —— 用户点了不喜欢，点还在。
                                for _feedback_name in feedback_exclude_names:
                                    if _feedback_name not in _exclude_terms:
                                        _exclude_terms.append(_feedback_name)
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
                                # 点过"不喜欢"的地点走这里：剔除 + 从候选池补同类，并在
                                # `constraints` 里如实报"已按你的反馈移除/换成谁"。
                                exclude_terms=feedback_exclude_names,
                                # 👑 用户说了"不想跑远"（含点了不喜欢并写"太远"）时，替换点
                                #    **优先选近的** —— 否则"换掉一个远点"很可能换来另一个远点
                                #    （实测：原来按档位/价格排，跟距离无关）。
                                prefer_near_to=(
                                    _preferred_near_center
                                    if str((own_signals or {}).get("distance_tolerance") or "normal") == "low"
                                    else None
                                ),
                                max_near_km=40.0,
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
                            # 记下这次快照的输入：收口之后（换近点/补坐标/查实价）路线还会变，
                            # 出稿前要用**同一批输入**重算一次，否则面板上的分数与最终路线对不上。
                            _quality_context_for_final = _quality_context
                            _quality_signals_for_final = _quality_signals
                            _quality_increment_for_final = _refinement_delta
                            _quality_pool_for_final = _pool if _pool else None
                            # 👎 反馈落地情况随方案下发：用户点的"不喜欢"到底改了没有，
                            # 必须能看见（没渲染出来的"已按你的反馈修正"等于没做）。
                            if feedback_exclude_names or (feedback_signals.get("notes") or []):
                                final_data["feedback_applied"] = {
                                    "exclude_names": list(feedback_exclude_names),
                                    "distance_tolerance": str((own_signals or {}).get("distance_tolerance") or "normal"),
                                    "budget_tendency": feedback_signals.get("budget_tendency"),
                                    "notes": list(feedback_signals.get("notes") or []),
                                    "counts": dict(feedback_signals.get("counts") or {}),
                                }
                            if _governance.get("pool_sizes"):
                                final_data["candidate_pool"] = _governance["pool_sizes"]
                            # 真实候选够不够、有没有补检索、供应商有没有报错：随方案下发（界面据此
                            # 说明"哪些地点来自数据源、哪些是靠模型知识补的"）
                            if candidate_supply:
                                final_data["candidate_supply"] = candidate_supply
                            # 住宿候选的供应商实价清单：住宿"能不能选得起、选哪家"的依据，
                            # 必须随方案下发（只报"住哪"不报"还有哪些有实价的可选"等于没给选择权）
                            if isinstance(_lodging_supply, dict) and _lodging_supply.get("status") not in (None, "not_attempted"):
                                final_data["lodging_supply"] = _lodging_supply
                            if _governance.get("increment"):
                                final_data["increment"] = _governance["increment"]
                            # 升/降档 → 排序偏好（网关 ranker 下次推演会读这些键；本次的配额填充与
                            # 兜底方向已经在核心层按同一份策略执行）
                            if _governance.get("tier_policy"):
                                final_data["tier_policy"] = _governance["tier_policy"]
                            if _governance.get("ranking_preferences"):
                                final_data["ranking_preferences"] = _governance["ranking_preferences"]

                            # 👑 用户**点名**要换成哪一家（点了「换住这家」/ 说"住 XX 酒店"）：
                            #    这是明确的选择，优先于自动替换 —— 就地换（保留原时段），
                            #    并打上 user_choice（后续的自动优化不许把它移走）。
                            if (_refinement_delta or {}).get("swap_to"):
                                try:
                                    from core.increment import apply_swap_to
                                    from core.candidate_index import normalize_text as _norm

                                    # ⚠️ 模型可能**已经**按用户点的那家排好了（他的原话里就带着名字）——
                                    #    那就直接给它打上 user_choice，别让后面的收口又换掉
                                    #    （实测：`apply_swap_to` 报"已经是这一家了"、没有动作，
                                    #    而收口随后按"有价优先"把住宿换成了池子里的另一家）。
                                    _swap_target = str(_refinement_delta.get("swap_to") or "")
                                    _already_chosen: List[str] = []
                                    for _node in final_data.get("route") or []:
                                        if isinstance(_node, dict) and _norm(str(_node.get("name") or "")) == _norm(_swap_target):
                                            _node["user_choice"] = True
                                            _node["replaced_reason"] = "你点名要这一家"
                                            _already_chosen.append(str(_node.get("name") or ""))
                                    _swap_pool = (
                                        list(cleaned_hotels or [])
                                        + list(unique_attractions or [])
                                        + list(unique_foods or [])
                                    )
                                    _swap_result = apply_swap_to(
                                        {"route": final_data.get("route") or []},
                                        _swap_target,
                                        pool=_swap_pool,
                                        kind=str(_refinement_delta.get("swap_kind") or ""),
                                    )
                                    if _swap_result.get("action"):
                                        final_data["route"] = _swap_result["plan"]["route"]
                                        final_data["node_swap"] = {
                                            "actions": [_swap_result["action"]],
                                            "reason": "你点名要换成这一家，已就地换好（时段不变）",
                                        }
                                        print(
                                            "🔁 [按你指定的换] "
                                            f"第 {_swap_result['action'].get('day')} 天 "
                                            f"{_swap_result['action'].get('from')} → {_swap_result['action'].get('to')}",
                                            flush=True,
                                        )
                                    elif _already_chosen:
                                        final_data["node_swap"] = {
                                            "actions": [],
                                            "already_planned": _already_chosen,
                                            "reason": f"方案里已经是「{_already_chosen[0]}」了（已标为你的选择，收口不会再换掉它）",
                                        }
                                        print(f"✅ [按你指定的换] 方案里已经是「{_already_chosen[0]}」，标记为你的选择", flush=True)
                                    elif _swap_result.get("reason"):
                                        # 找不到那一家的名字：如实说，不编一个替换
                                        final_data["node_swap"] = {"actions": [], "error": str(_swap_result["reason"])}
                                        print(f"⚠️ [按你指定的换] {_swap_result['reason']}", flush=True)
                                except Exception as _swap_to_exc:  # 指定替换失败不影响出方案
                                    final_data["node_swap"] = {"actions": [], "error": f"{type(_swap_to_exc).__name__}: {str(_swap_to_exc)[:140]}"}
                                    print(f"⚠️ [按你指定的换] 失败（不影响出方案）: {_swap_to_exc}", flush=True)

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

                            _stage("复核与多方案")
                            # 👑 价格补全（有界）：只查**缺价**节点、并发执行、整体超时保护；
                            # 来源标为 web → 结构上仍是"估算"，必须标明，不会冒充已验证价格。
                            try:
                                from core.price_sources import (
                                    merge_observations,
                                    pending_price_report,
                                    pending_price_targets,
                                    price_coverage,
                                    summarize_merge,
                                    unsourced_price_claims,
                                )

                                # 补价节点数改为**可配置**（默认 12），并且先诊断"取价源通不通"：
                                # 实测（2026-10-04）本机 DuckDuckGo 直接失败（回退 bing 也返回 None），
                                # 于是"一个价都没补上"——如果不诊断，用户只会看到一片"未取到"，
                                # 根本不知道是搜索源不可达。现在把原因如实写进 price_audit 与降级清单。
                                from core.web_price_tool import (
                                    affordable_target_count,
                                    price_budget_seconds,
                                    price_max_nodes,
                                    price_timeout_seconds,
                                )

                                _price_budget = price_budget_seconds()
                                _price_node_cap = affordable_target_count(price_max_nodes(), budget_seconds=_price_budget)
                                # 👑 "有数字没出处"的节点优先查（2026-10-05 抽查表发现）：
                                #    喀什 15 个节点里 5 个是 `¥206（source=web）`——数字来自搜索摘要，
                                #    点开也不是官方报价。这类原来**永远不会被再查**（只挑 value is None），
                                #    但它最值得再查一次：命中白名单官方页面就把来源从"估算"变成"可核实"。
                                _unsourced_before = unsourced_price_claims(final_data, limit=_price_node_cap)
                                _unsourced_names = {str(item.get("name") or "") for item in _unsourced_before}
                                _targets = pending_price_targets(
                                    final_data,
                                    limit=_price_node_cap,
                                    include_estimated=True,
                                    include_unsourced=True,
                                )
                                # 住宿**不在这里**查：硬约束收口（settle_lodging）之后还会补/换酒店，
                                # 早查的房价会随着换店丢掉（2026-10-05 实测：取价查的是"库尔勒园林宾馆
                                # ¥165"，收口后最终节点是"瑞德假日酒店"，价格白查）。住宿价格统一放到
                                # 收口之后用供应商只读 MCP 查（见下方 `price_lodging_nodes`）。
                                _web_targets = [item for item in _targets if str(item.get("intent") or "") != "hotel"]
                                _pending_total = len(pending_price_report(final_data)["pending"])
                                _fetcher = getattr(toolbox, "get_real_time_web_price", None)

                                # 住宿价格**不在这里**查：硬约束收口（settle_lodging）之后还会补/换酒店，
                                # 早查的房价会随着换店一起丢掉（2026-10-05 实测：取价阶段查到的是
                                # 「库尔勒园林宾馆 ¥165」，收口后最终节点换成「瑞德假日酒店」，
                                # 最终方案里住宿仍是"暂无供应商数据"）。住宿统一放到收口之后用
                                # 供应商只读 MCP 查 —— 见下方 `price_lodging_nodes`。

                                if _web_targets and _fetcher:
                                    async def _lookup(
                                        _item: Dict[str, Any], *, prefer_official: bool = False
                                    ) -> Dict[str, Any]:
                                        """查一个节点的价格：搜索 + **能直抓官方页面就直抓**，带出处。

                                        统一走 `web_price_tool.lookup_price`（一处口径）：
                                        摘要抽价 → 命中白名单页面再抓正文抽价 → 拿不到如实 None。
                                        目标是"把已有的网摘价升级成官方价"时（`prefer_official=True`），
                                        查询带「官网」并遍历多个白名单候选页。
                                        """
                                        from core.web_price_tool import lookup_price

                                        _name = str(_item.get("name") or "")
                                        # 👑 **先走官方名录定向通道**（2026-10-07 加的）：
                                        #    景区名 → 官方文旅名录 → 官方详情页 → 页面写的门票价。
                                        #    为什么放在通用搜索**之前**：这条路既准（命中即"可核实"，
                                        #    实测天鹅泉 ¥30、六星街 免费、薰衣草园 ¥35）又便宜
                                        #    （名录有缓存，命中只多抓 1 个页面）；而通用搜索对这些
                                        #    小众景点已经拿不到东西了（Bing 返回随机热门页）。
                                        _intent = str(_item.get("intent") or "")
                                        if _intent in {"scenic", "cultural"}:
                                            try:
                                                from core.official_directory import lookup_official_price

                                                _official = await lookup_official_price(
                                                    _name, city=target_city
                                                )
                                                if _official.get("value") is not None:
                                                    return _official
                                            except Exception as _official_exc:
                                                print(
                                                    f"⚠️ [官方名录] {_name} 这条通道失败，改用通用搜索: "
                                                    f"{type(_official_exc).__name__}: {str(_official_exc)[:60]}",
                                                    flush=True,
                                                )
                                            # 👑 第二条定向通道：**文旅部全国 A 级景区名录**
                                            #    （2026-10-07 接的）。它只覆盖 5A，但字段很合用：
                                            #    官方名称/地址/简介 + **景区官网 + 官方预约平台**。
                                            #    有官网/预约页就去那一页取票价（`official` = 可核实）；
                                            #    没有就把它登记的官方信息留下（用户能看到官方购票入口）。
                                            try:
                                                from core.mct_channel import (
                                                    lookup_mct_price as _mct_price,
                                                )
                                                from core.mct_channel import (
                                                    mct_lookup as _mct_lookup,
                                                )
                                                from core.mct_channel import (
                                                    profile_of as _mct_profile,
                                                )

                                                _marker = await _mct_lookup(_name, city=target_city)
                                                if _marker:
                                                    final_data.setdefault("official_profiles", {})[
                                                        _name
                                                    ] = _mct_profile(_marker)
                                                _mct = await _mct_price(_name, city=target_city)
                                                if _mct.get("value") is not None:
                                                    return _mct
                                            except Exception as _mct_exc:
                                                print(
                                                    f"⚠️ [文旅部名录] {_name} 这条通道失败: "
                                                    f"{type(_mct_exc).__name__}: {str(_mct_exc)[:60]}",
                                                    flush=True,
                                                )
                                            # 👑 第三条：**景区自己的官方站点/官方购票渠道**
                                            #    （2026-10-07 用户直接给了域名：巴楚红海 bachu.gov.cn、
                                            #    罗布人村寨 xjlbr.com、巴音布鲁克 byblk.cn）。
                                            #    实测这三家的票价页是**客户端渲染**，服务端抓不到价 ——
                                            #    所以这条通道的产出是"官方渠道信息"（官网/官方小程序 + 备注），
                                            #    读得到票价时按 `official`（可核实）采，读不到就如实说明。
                                            try:
                                                from core.official_sites import (
                                                    lookup_official_site_price as _site_price,
                                                )

                                                _site = await _site_price(_name, city=target_city)
                                                _channel = _site.get("official_channel") or {}
                                                if _channel:
                                                    _profiles = final_data.setdefault("official_profiles", {})
                                                    _profiles[_name] = {
                                                        **(_profiles.get(_name) or {}),
                                                        "official_name": _channel.get("official_name") or _name,
                                                        "official_site": _channel.get("official_site") or "",
                                                        "official_miniprogram": _channel.get("official_miniprogram") or "",
                                                        "official_channel_note": _channel.get("official_channel_note") or "",
                                                        # 票价可得性：本机拿不到价时必须说清"为什么"（小程序内/政府页只有优惠政策）
                                                        "official_price_status": _channel.get("price_status") or "",
                                                        "official_price_status_note": _channel.get("price_status_note") or "",
                                                        "source": _channel.get("source") or "official_site_registry",
                                                    }
                                                if _site.get("value") is not None:
                                                    return _site
                                            except Exception as _site_exc:
                                                print(
                                                    f"⚠️ [官方站点] {_name} 这条通道失败: "
                                                    f"{type(_site_exc).__name__}: {str(_site_exc)[:60]}",
                                                    flush=True,
                                                )
                                            # 👑 第四条：**票务平台的结构化接口**
                                            #    （2026-10-07 用户："既然票务平台能用为什么不用票务系统"）。
                                            #    去哪儿 `ticket/list.json` 直接给票种与价格（成人票优先，
                                            #    跟团游/演艺票被过滤）；携程建议词接口拿到详情页再抽价。
                                            #    来源标 `platform` = 「票务平台价，非官方」，不冒充官方价。
                                            try:
                                                from core.ticket_platforms import (
                                                    lookup_platform_price as _platform_price,
                                                )

                                                _platform = await _platform_price(_name, city=target_city)
                                                if _platform.get("value") is not None:
                                                    return _platform
                                                # 👑 平台**已经给过结论**（有景区、但只有跟团游/没有门票；
                                                #    或平台根本没有这个景区）→ 不再退回通用搜索。
                                                #    为什么（2026-10-07 实测）：三个通道串起来后每个节点要
                                                #    7~9s，喀什那一轮"超预算 8 个"——后 8 个节点**根本没被问过**；
                                                #    而通用搜索对这些小众 POI 的产出实测是 0。
                                                #    只有平台**报错**（网络失败，没有结论）时才继续往下试。
                                                if _platform.get("platform_checked"):
                                                    return _platform
                                            except Exception as _platform_exc:
                                                print(
                                                    f"⚠️ [票务平台] {_name} 这条通道失败: "
                                                    f"{type(_platform_exc).__name__}: {str(_platform_exc)[:60]}",
                                                    flush=True,
                                                )
                                        try:
                                            return await lookup_price(
                                                _name,
                                                target_city,
                                                max_results=3,
                                                prefer_official=prefer_official,
                                                # 玩点只认门票口径（景区/人文）；餐饮/住宿才用通用抽价
                                                kind=str(_item.get("intent") or ""),
                                            )
                                        except Exception as _lookup_exc:
                                            from core.web_price_tool import build_observation

                                            return build_observation(
                                                name=_name, value=None, source="web", provider="search"
                                            ) | {"error": f"{type(_lookup_exc).__name__}: {str(_lookup_exc)[:120]}"}

                                    # 👑 **逐点、按共享 deadline 顺序查**（2026-10-05 实测修正）：
                                    #    上一版是"6 个点并发 + 每点公平份额 7.5s"。但取价底层
                                    #    （`search_texts`）在同一进程里是**串行**的（有全局锁 + 节流），
                                    #    并发只是排队：排在第 6 位的点要等前面 5 个跑完，早就超过了自己的
                                    #    7.5s 份额 → 实测表现成"6 个点全部超预算、来源可达=None"，
                                    #    而其实一个搜索都还没发出去。
                                    #    现在改成顺序执行 + 共享 deadline：每个点用"剩余时间"作上限，
                                    #    第一个点慢一点也不会把后面的判死；预算用完的点如实标"没轮到"。

                                    _deadline = _time.monotonic() + float(_price_budget)
                                    # 单点上限要给得下**合并搜索**的开销（2026-10-07 实测修正）：
                                    # 取价现在一个节点要问 2 家搜索源（ddgs 拿票务平台价、Bing 找官方页），
                                    # 上限还按老的"一次搜索"给 6s，结果是**每个点都在发请求前就被取消** ——
                                    # 表现成 `provider_attempts=[]`、`provider_status=单点取价超预算`、
                                    # "查了 5 个补到 0 条"。所以按 2×单次上限算。
                                    _per_point_cap = max(
                                        float(price_timeout_seconds()) * 2.0,
                                        float(_price_budget) / max(1, len(_targets)),
                                    )

                                    async def _lookup_bounded(
                                        _item: Dict[str, Any], *, prefer_official: Optional[bool] = None
                                    ) -> Dict[str, Any]:
                                        from core.web_price_tool import build_observation

                                        _remaining = _deadline - _time.monotonic()
                                        _name = str(_item.get("name") or "")
                                        _want_official = (
                                            _name in _unsourced_names if prefer_official is None else bool(prefer_official)
                                        )
                                        if _remaining <= 0.5:
                                            return build_observation(
                                                name=_name, value=None, source="web", provider="search"
                                            ) | {"timeout": True, "error": "整体预算已用完，这个点没轮到"}
                                        try:
                                            return await asyncio.wait_for(
                                                _lookup(_item, prefer_official=_want_official),
                                                timeout=min(_remaining, _per_point_cap),
                                            )
                                        except asyncio.TimeoutError:
                                            return build_observation(
                                                name=_name, value=None, source="web", provider="search"
                                            ) | {"timeout": True, "error": "单点取价超预算（来源慢或不可达）"}
                                        except Exception as _bounded_exc:
                                            return build_observation(
                                                name=_name, value=None, source="web", provider="search"
                                            ) | {"error": f"{type(_bounded_exc).__name__}: {str(_bounded_exc)[:120]}"}

                                    _observations = []
                                    _early_stop_reason = ""
                                    # 👑 **通道优先的前置一轮**（2026-10-07 实测抓到的问题）：
                                    #    「官方站点」这条通道是**可靠**的（无头浏览器渲染一页 ≈3s，且有落盘缓存），
                                    #    但如果它排在通用搜索后面，通用搜索的"早停"一触发，后面的节点
                                    #    **根本不会被问到** —— 实测和静方案里「天鹅湖公园」能拿到官方 140 元，
                                    #    却因为前 7 个节点搜索全失败而早停，一条都没取到。
                                    #    所以先把**登记表里有的**节点查一遍（一次运行里每个站点只渲染一次），
                                    #    再把剩下的交给搜索；已拿到价的节点不再进搜索队列。
                                    _channel_first: List[Dict[str, Any]] = []
                                    try:
                                        from core.official_sites import official_site_for as _site_for

                                        _matched_targets = [
                                            _target
                                            for _target in _web_targets
                                            if _site_for(str(_target.get("name") or ""), target_city)
                                        ]
                                        for _target in _matched_targets:
                                            try:
                                                _channel_first.append(await _lookup_bounded(_target))
                                            except Exception as _channel_exc:
                                                print(
                                                    f"⚠️ [通道优先] {_target.get('name')} 失败: "
                                                    f"{type(_channel_exc).__name__}",
                                                    flush=True,
                                                )
                                        if _channel_first:
                                            _hit = [
                                                str(item.get("name"))
                                                for item in _channel_first
                                                if isinstance(item, Mapping) and item.get("value") is not None
                                            ]
                                            print(
                                                f"🏛️ [通道优先] 官方站点通道先查 {len(_channel_first)} 个节点，"
                                                f"拿到价 {len(_hit)} 个{('：' + '、'.join(_hit)) if _hit else ''}",
                                                flush=True,
                                            )
                                    except Exception as _channel_first_exc:
                                        print(f"⚠️ [通道优先] 前置一轮失败: {_channel_first_exc}", flush=True)
                                    _channel_names = {
                                        str(item.get("name"))
                                        for item in _channel_first
                                        if isinstance(item, Mapping) and item.get("value") is not None
                                    }
                                    _web_targets = [
                                        _target
                                        for _target in _web_targets
                                        if str(_target.get("name") or "") not in _channel_names
                                    ]
                                    # 酒店已经由 RollingGo（可核实来源）查过，这里只跑**剩下的**缺价节点：
                                    # 否则"所有源连续失败"的早停会被酒店查询的结果干扰，白等一轮。
                                    for _index, _item in enumerate(_web_targets):
                                        # 👑 早停（2026-10-05 实测，2026-10-07 收紧判据）：
                                        #    "所有源都不可用"时逐点重试只是白花 20 多秒（实测 27.4s、补到 0 条），
                                        #    该停；但**"搜到了、只是没抽到可核实的价格"不是源失败** ——
                                        #    实测（2026-10-07）严门口径上线后，前两个点"查了没查到"就把整条
                                        #    取价链叫停（查12/补0），后面 10 个节点根本没被问过。
                                        #    所以判据改成：**只有"连搜索本身都没成功过"才早停** ——
                                        #    看最近一次尝试的 provider_attempts 里有没有任何一家 ok=True。
                                        from core.web_price_tool import last_search_attempts as _attempts_now

                                        _last_attempts = list(_attempts_now or [])
                                        _any_provider_ok = any(
                                            isinstance(_attempt, Mapping) and _attempt.get("ok")
                                            and str(_attempt.get("provider") or "") != "cache"
                                            for _attempt in _last_attempts
                                        )
                                        if (
                                            _index >= 2
                                            and not _any_provider_ok
                                            and not any(
                                                isinstance(item, Mapping) and item.get("value") is not None
                                                for item in _observations
                                            )
                                        ):
                                            _early_stop_reason = "所有取价源连续失败，已提前停止（剩下的节点留给「需要你确认」）"
                                            print(f"⏹️ [联网取价] {_early_stop_reason}", flush=True)
                                            break
                                        _observations.append(await _lookup_bounded(_item))
                                    _observations = list(_channel_first) + list(_observations)
                                    _merged = merge_observations(final_data, list(_observations))
                                    if _merged["updated"]:
                                        final_data["route"] = _merged["plan"]["route"]
                                    # 这一轮"刚补到价"的节点名单（第二轮升级只做这些）
                                    _filled = [
                                        item for item in (_observations or [])
                                        if isinstance(item, Mapping) and item.get("value") is not None
                                    ]
                                    _filled_names = {str(item.get("name") or "") for item in _filled}

                                    # 👑 第二轮：**把这一轮刚补上的"网摘价"再试着升级成官方价**
                                    #    （2026-10-05 真实推演发现的口径错位）：
                                    #    上一版把"有数字没出处"当成**进入取价前的**既存状态 —— 实测
                                    #    喀什跑完发现 `unsourced_before=0`，因为那 5 个"¥206(web)"
                                    #    正是这一轮自己补出来的：先是空白 → 填上网摘价 → 变成"没出处"。
                                    #    所以升级要放在**补价之后**，且只做一轮、只做少量（预算还剩才做）：
                                    #    命中白名单官方页面就把来源从 `web`（估算）翻成 `web_page`（可核实）。
                                    _upgraded: List[str] = []
                                    _budget_left = _deadline - _time.monotonic()
                                    _newly_unsourced = [
                                        item
                                        for item in unsourced_price_claims(final_data, limit=3)
                                        if str(item.get("name") or "") in _filled_names
                                        or str(item.get("name") or "") in _unsourced_names
                                    ]
                                    # 源全死时不空转：这一轮补到过价（说明源活着）才做第二轮；
                                    # 被反爬挡住（安全验证页）时也不做 —— 换个查询词只会再撞一次墙，
                                    # 白白多花 8~10s。
                                    # ⚠️ `_blocked` 必须在用它之前算出来（2026-10-05 真实推演踩过：
                                    #    顺序写反 → `UnboundLocalError: _blocked`，整个取价段被 except
                                    #    接住、价格审计整块丢失，只剩一句 error）。
                                    from core.web_price_tool import last_search_attempts as _upgrade_attempts
                                    from core.web_price_tool import enabled_providers as _enabled_providers
                                    from core.web_price_tool import provider_is_available as _provider_available

                                    _blocked = any(
                                        (not item.get("ok")) and "验证" in str(item.get("error") or "")
                                        for item in (_upgrade_attempts or [])
                                    )
                                    # 还要看"有没有源是活的（不在冷却里）"：第一轮可能是**命中搜索缓存**
                                    # 才补到价的（缓存命中不记尝试），此时 `_blocked` 为假，但源其实已经
                                    # 被反爬打进了冷却 —— 第二轮换个查询词必然再撞一次墙。
                                    _live_provider = any(_provider_available(item) for item in _enabled_providers())
                                    if (_blocked or not _live_provider) and _newly_unsourced and _filled:
                                        print(
                                            "⏭️ [联网取价] 跳过官方页升级："
                                            + ("取价源正在返回安全验证页" if _blocked else "所有取价源都在冷却中")
                                            + f"（{len(_newly_unsourced)} 个网摘价继续保持未核实标注）",
                                            flush=True,
                                        )
                                    if (
                                        _newly_unsourced
                                        and _filled
                                        and _budget_left > 5.0
                                        and not _blocked
                                        and _live_provider
                                    ):
                                        for _item in _newly_unsourced:
                                            _upgrade_observation = await _lookup_bounded(_item, prefer_official=True)
                                            _observations.append(_upgrade_observation)
                                        _merged = merge_observations(final_data, list(_observations))
                                        if _merged["updated"]:
                                            final_data["route"] = _merged["plan"]["route"]
                                        _upgraded = sorted(
                                            {
                                                str(item.get("name") or "")
                                                for item in _observations
                                                if isinstance(item, Mapping)
                                                and str(item.get("source") or "") == "web_page"
                                            }
                                        )
                                        print(
                                            f"⬆️ [联网取价] 官方页升级：试了 {len(_newly_unsourced)} 个网摘价，"
                                            f"升级为可核实 {len(_upgraded)} 个"
                                            + (f"（{'、'.join(_upgraded[:3])}）" if _upgraded else "（没命中白名单官方页面，仍是网摘价）"),
                                            flush=True,
                                        )

                                    _report = pending_price_report(final_data)
                                    # 待人工确认的官方域名：这一步只**汇报**（白名单加不加由人定）
                                    _domain_candidates: Dict[str, Any] = {}
                                    try:
                                        from core.web_price_tool import load_domain_candidates

                                        _candidates = load_domain_candidates()
                                        if _candidates:
                                            _hosts: List[str] = []
                                            for _candidate in _candidates:
                                                _host = str(_candidate.get("host") or "")
                                                if _host and _host not in _hosts:
                                                    _hosts.append(_host)
                                            _domain_candidates = {
                                                "count": len(_hosts),
                                                "hosts": _hosts[:5],
                                                "note": "这些域名在搜索结果里看起来是官网，但没进白名单：确认后写进 WEB_PRICE_HOST_SUFFIXES，这类价格才会从「网摘估算」变成「可核实」。",
                                            }
                                    except Exception:
                                        _domain_candidates = {}
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
                                    # 熔断状态也随方案下发：用户/我们自己都要能回答"这一轮为什么先跳过某个源"
                                    from core.web_price_tool import provider_states as _provider_states

                                    _provider_state = _provider_states()
                                    for _name, _state in _provider_state.items():
                                        if not _state.get("available") and not _attempt_error:
                                            _attempt_error = (
                                                f"{_name} 连续失败 {_state.get('consecutive_failures')} 次，"
                                                f"冷却中（还剩 {_state.get('cooldown_remaining_s')}s）"
                                            )
                                            break
                                    # 升级成效要能看见："有数字没出处"的那批里，有多少个换成了
                                    # 可核实来源（命中白名单官方页面），有多少个还是网摘 ——
                                    # 只报"补到 N 条"会把"补了个新的网摘价"也算成功。
                                    _unsourced_after = unsourced_price_claims(final_data, limit=99)
                                    _unsourced_after_names = {str(item.get("name") or "") for item in _unsourced_after}
                                    _upgraded = sorted(
                                        set(_upgraded) | (_unsourced_names - _unsourced_after_names)
                                    )
                                    _timed_out = [
                                        item for item in (_observations or [])
                                        if isinstance(item, Mapping) and item.get("timeout")
                                    ]
                                    # 👑 每个取价源的**具体失败原因**汇总（2026-10-05 实测需要）：
                                    #    "来源可达=False" 太笼统 —— 用户/我们看不出是"所有源都被反爬挡住"
                                    #    还是"我们根本没查"。这里把每个源的真实原因拼成一句人话。
                                    _provider_summary = "；".join(
                                        f"{item.get('provider')}："
                                        + (
                                            str(item.get("error"))[:60]
                                            if item.get("error")
                                            else ("0 条结果" if not item.get("ok") else f"{item.get('results')} 条结果")
                                        )
                                        for item in list(_attempts or [])[:5]
                                    )
                                    if not _attempt_error and _timed_out:
                                        _attempt_error = str(_timed_out[0].get("error") or "单点取价超预算")[:160]
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
                                        "provider_states": _provider_state,
                                        "provider_summary": _provider_summary,
                                        "early_stop_reason": _early_stop_reason,
                                        "provider_status": {"error": _attempt_error} if _attempt_error else {},
                                        "looked_up": len(_targets),
                                        "filled_from_web": len(_filled),
                                        # "有数字没出处"的台账：查之前有几个、查之后还剩几个、
                                        # 哪几个成功升级成可核实来源（命中白名单官方页面）
                                        "unsourced_before": [str(item.get("name") or "") for item in _unsourced_before],
                                        "unsourced_after": [str(item.get("name") or "") for item in _unsourced_after],
                                        "unsourced_upgrade_attempted": len(_newly_unsourced),
                                        "upgraded_to_verifiable": _upgraded,
                                        # 待人工确认的官方域名（白名单是人工确认的：系统只提议）——
                                        # 用户看到"还有 N 个域名待确认，确认后这类价格就能变成可核实"
                                        "domain_candidates": _domain_candidates,
                                        # 超预算没查完的**如实计数**（不再是整段失败、一片空白）
                                        "timed_out": len(_timed_out),
                                        # 预算只够查这么多：剩下的如实留给「需要你确认」，
                                        # 不让用户以为"系统把该查的都查了"
                                        "budget_seconds": _price_budget,
                                        "not_looked_up": max(0, _pending_total - len(_targets)),
                                    }
                                    print(
                                        f"🌐 [联网取价] 查了 {len(_web_targets)} 个缺价节点"
                                        + (f"（另有 {len(_channel_first)} 个走官方通道前置）" if _channel_first else "")
                                        + f"，补到 {len(_filled)} 条"
                                        f"（预算 {_price_budget:.0f}s，来源可达={_reachable}"
                                        + (f"，超预算 {len(_timed_out)} 个" if _timed_out else "")
                                        + "）"
                                        + (
                                            f"；有数字没出处 {len(_unsourced_before)} 个 → 升级为可核实 {len(_upgraded)} 个"
                                            + (f"（{ '、'.join(_upgraded[:3]) }）" if _upgraded else "")
                                            if _unsourced_before
                                            else ""
                                        )
                                        + (f"；失败原因：{_attempt_error}" if _attempt_error else ""),
                                        flush=True,
                                    )

                                    # 👑 真简介：**只在白名单官方页面**上找（用户裁决 2026-10-07「抓取官网页面」）。
                                    #    之前简介要么是编的（YouTube 帮助页/套话，已撤下），要么是
                                    #    「高德资料：类别｜评分｜地址」这种事实行 —— 都不是"介绍这个地方"。
                                    #    现在搜索链路恢复了（ddgs + Bing，Bing 会带出 .gov.cn），
                                    #    就能把官方页正文里的那一段挑出来。挑不到就保持原样，绝不凑。
                                    #    预算：只用取价**剩余**的时间，最多 4 个点 —— 不额外拉长一次推演。
                                    try:
                                        from core.web_price_tool import lookup_description as _lookup_description

                                        _desc_targets = [
                                            _node
                                            for _node in (final_data.get("route") or [])
                                            if isinstance(_node, dict)
                                            and str(_node.get("desc_source") or "") in {"amap_fact", "missing", ""}
                                            and str(_node.get("name") or "").strip()
                                            and not bool(_node.get("is_hotel"))
                                        ][:2]
                                        # ⚠️ 用**自己的**小预算，不挂在取价 deadline 上（2026-10-07 实测）：
                                        #    取价经常把 90s 用完，挂上去就永远"剩不到 8s → 直接跳过"，
                                        #    等于这一步从没跑过。这里最多 2 个点、20s。
                                        _desc_deadline = _time.monotonic() + 20.0
                                        _desc_found: List[str] = []
                                        _desc_tried = 0
                                        for _node in _desc_targets:
                                            if _desc_deadline - _time.monotonic() < 6.0:
                                                break
                                            _desc_tried += 1
                                            try:
                                                _found = await asyncio.wait_for(
                                                    _lookup_description(str(_node.get("name") or ""), target_city),
                                                    timeout=max(6.0, _desc_deadline - _time.monotonic()),
                                                )
                                            except Exception:
                                                continue
                                            if not str(_found.get("text") or "").strip():
                                                continue
                                            _node["desc"] = str(_found["text"])
                                            _node["desc_source"] = "web_page"
                                            _node["desc_source_url"] = str(_found.get("source_url") or "")
                                            _node["desc_retrieved_at"] = float(_found.get("retrieved_at") or 0)
                                            _desc_found.append(str(_node.get("name") or ""))
                                        # 试过就要说（"试了但没取到"也是事实，不能只在成功时打日志）
                                        if _desc_tried:
                                            print(
                                                f"📖 [地点简介] 官方页面：试了 {_desc_tried} 个，取到 "
                                                f"{len(_desc_found)} 段"
                                                + (f"（{'、'.join(_desc_found)}）" if _desc_found else "（没取到就保持高德资料/暂无）"),
                                                flush=True,
                                            )
                                    except Exception as _desc_exc:  # 取简介失败不影响出方案
                                        print(f"⚠️ [地点简介] 取简介失败（不影响出方案）: {_desc_exc}", flush=True)
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
                            _stage("联网取价")

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

                        # 👑 住宿收口：**每晚一家、不多不少**（两个方向都实测过事故）
                        #    - 少了：伊宁 3 天方案一晚住宿都没有（"住哪儿没着落"）；
                        #    - 多了：喀什 3 天 4 个、乌鲁木齐 2 天 3 个（候选池把全部酒店塞进 top，
                        #      最终均衡又"哪天没酒店就补一个"，连不回程的最后一天也补）。
                        #    顺序是**先补后减**，封在 core/route_composition.settle_lodging 里
                        #    （先减后补会留下一个排在不需要留宿那天的酒店，实测踩过）。
                        try:
                            _settled_route, _lodging_added, _lodging_dropped, _missing_nights = settle_lodging(
                                final_data.get("route") or [],
                                nights=max(1, trip_days - 1),
                                pool=list(cleaned_hotels or []),
                                classify=classify_poi_category,
                            )
                            if _lodging_added or _lodging_dropped or _missing_nights:
                                final_data["route"] = _settled_route
                            if _lodging_added:
                                await event_bus.emit(OMNI_EVENTS_STREAM, "route.repaired", {
                                    "room_id": room_id, "city": target_city,
                                    "action_count": len(_lodging_added),
                                    "action_codes": ["lodging_added"],
                                    "examples": [
                                        f"第 {item.get('day')} 晚补上 {item.get('to')}"
                                        for item in _lodging_added[:4]
                                    ],
                                    "correlation_id": plan_correlation_id,
                                }, room_id, correlation_id=plan_correlation_id, producer="validator")
                                print(
                                    f"🛏️ [住宿兜底] 补上 {len(_lodging_added)} 晚"
                                    + (f"；仍缺 {_missing_nights}（候选池里没有可用住宿）" if _missing_nights else ""),
                                    flush=True,
                                )
                            if _lodging_dropped:
                                await event_bus.emit(OMNI_EVENTS_STREAM, "route.repaired", {
                                    "room_id": room_id, "city": target_city,
                                    "action_count": len(_lodging_dropped),
                                    "action_codes": ["lodging_over_cap_dropped"],
                                    "examples": [
                                        f"{item.get('name')}（第 {item.get('day')} 天）"
                                        for item in _lodging_dropped[:4]
                                    ],
                                    "correlation_id": plan_correlation_id,
                                }, room_id, correlation_id=plan_correlation_id, producer="validator")
                                print(
                                    f"🛏️ [住宿限量] 住宿比夜数多，移出 {len(_lodging_dropped)} 个："
                                    + "、".join(str(item.get("name") or "") for item in _lodging_dropped[:4]),
                                    flush=True,
                                )
                            if _lodging_added or _missing_nights:
                                final_data["lodging_repair"] = {
                                    "actions": _lodging_added,
                                    "missing_days": _missing_nights,
                                }
                            if _lodging_dropped:
                                final_data["lodging_trim"] = {
                                    "nights": max(1, trip_days - 1),
                                    "actions": [
                                        {
                                            "code": "lodging_over_cap_dropped",
                                            "from": str(item.get("name") or ""),
                                            "day": item.get("day"),
                                            "reason": item.get("removed_reason"),
                                        }
                                        for item in _lodging_dropped
                                    ],
                                }
                        except Exception as _lodging_exc:  # 收口失败绝不影响出方案
                            final_data["lodging_repair"] = {"error": str(_lodging_exc)[:200]}

                        # 👑 配比对账：实际排出来的构成 vs 用户口径算出的目标（默认自然=人文各半
                        # + 穿插美食住宿；明确偏好则加权）。对不上就如实记账，进"遗留问题"。
                        # ⚠️ 但要先把目标**按候选供给压一次**：库尔勒目标 4 人文而候选只有 3 个人文点，
                        #    喀什目标 9 人文而候选 7 个 —— 这时报"人文偏少"是在怪方案没做到
                        #    供给本身就不够的事。做不到的部分作为**供给缺口**单独披露。
                        _feasible_targets: Dict[str, int] = numeric_targets(composition)
                        _supply_limited: Dict[str, Dict[str, int]] = {}
                        _supply_limit_notes: List[str] = []
                        _supply_counts: Dict[str, int] = {}
                        try:
                            _supply_counts = {}
                            for _candidate in (top_candidates or []):
                                if not isinstance(_candidate, Mapping):
                                    continue
                                _key = str(_day_cat(_candidate) or "other")
                                _supply_counts[_key] = _supply_counts.get(_key, 0) + 1
                            _feasible_targets, _supply_limited = clamp_targets_by_supply(
                                composition, _supply_counts
                            )
                            _supply_limit_notes = summarize_supply_limits(_supply_limited)
                            _route_counts = count_route_composition(
                                final_data.get("route") or [], classify_poi_category, scenic_tier
                            )
                            _composition_audit = composition_audit(
                                _route_counts,
                                {**composition, **_feasible_targets},
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
                            if _supply_limit_notes:
                                # 供给缺口是"为什么达不到口径目标"，必须和对账结论写在一起
                                _composition_audit["notes"] = list(_composition_audit.get("notes") or []) + _supply_limit_notes
                                _composition_audit["supply_limited"] = _supply_limited
                            composition["park_cap"] = int(_park_allowed or 0)
                            final_data["composition"] = {
                                **composition,
                                "feasible_targets": _feasible_targets,
                                "supply_limited": _supply_limited,
                                "supply_counts": _supply_counts,
                                "actual": _route_counts,
                                "audit": _composition_audit,
                            }
                            print(
                                f"⚖️ [配比对账] 实际 自然景区={_route_counts.get('landmark')}/城市公园={_route_counts.get('city_park')}"
                                f"/人文={_route_counts.get('cultural')}/美食={_route_counts.get('food')}/住宿={_route_counts.get('hotel')}"
                                f" | 目标 {composition.get('scenic')}/{composition.get('cultural')}/{composition.get('food')}/{composition.get('hotel')}"
                                f"（可做到 {_feasible_targets.get('scenic')}/{_feasible_targets.get('cultural')}/{_feasible_targets.get('food')}/{_feasible_targets.get('hotel')}）"
                                f" | {'一致' if _composition_audit['ok'] else _composition_audit['notes']}",
                                flush=True,
                            )
                        except Exception as _comp_exc:  # 对账失败绝不影响出方案
                            # ⚠️ 但**必须打出来**：实测（2026-10-05）这里静默吞掉一个 ValueError，
                            #    表现成"配比对账这一步压根没跑"（对数全空），排查只能靠猜。
                            final_data["composition"] = {"error": f"{type(_comp_exc).__name__}: {str(_comp_exc)[:200]}"}
                            print(f"⚠️ [配比对账] 失败（不影响出方案）: {type(_comp_exc).__name__}: {_comp_exc}", flush=True)
                            traceback.print_exc()

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
                            _before = _evaluate_feasibility(
                                final_data.get("route") or [],
                                city_center=_center_for_check,
                                distance_tolerance=str(own_signals.get("distance_tolerance") or "normal"),
                                days=trip_days,
                            )
                            if not _before.get("ok"):
                                _kept_route, _moved_out = _repair_feasibility(
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
                                _after = _evaluate_feasibility(
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

                    # 👑 省域按片区拆天（方案 1，2026-11-02）：候选池/分片建议都对了，
                    #    但模型仍可能把同一片区 4 个景区全塞进一天（实测第 3 天 251km），
                    #    再把本该在阿坝的第 4 天排成成都餐饮（第 4 天没有玩点）。
                    #    这里在最终门禁**之前**做一次确定性分摊：按"哪天在哪片"块内
                    #    地理最近邻串线 + DP 切天，移出整天跨片区的点，缺的从同片区候选补。
                    #    放在门禁之前，是为了让下面按片区候选池补点/补餐能接住减少的名额。
                    def _split_province_days(stage: str) -> Optional[List[Dict[str, Any]]]:
                        """按片区拆天；动了就返回新路线，没动（或不合规/出错）返回 None。"""
                        if not (province_scope_name and province_anchors and _day_region_plan):
                            return None
                        try:
                            from core.province_scope import redistribute_province_days as _redistribute
                            from core.travel_feasibility import effective_daily_budget_km as _eff_budget

                            _split = _redistribute(
                                final_data.get("route") or [],
                                day_regions=_day_region_plan,
                                region_of=_province_region_of,
                                days=trip_days,
                                pool=poi_pool_data if isinstance(poi_pool_data, list) else [],
                                budget_km=_eff_budget(
                                    str(own_signals.get("distance_tolerance") or "normal")
                                ),
                                region_allow=_sub_cluster_keep or None,
                            )
                            if not _split.get("applied"):
                                return None
                            _previous = final_data.get("province_day_split") or {}
                            final_data["province_day_split"] = {
                                key: _split.get(key) for key in ("days", "dropped", "added", "notes")
                            }
                            final_data["province_day_split"]["passes"] = (
                                int(_previous.get("passes") or 0) + 1
                            )
                            print(
                                f"🗺️ [省域拆天{stage}] " + "；".join(_split.get("notes") or []),
                                flush=True,
                            )
                            return list(_split["route"])
                        except Exception as _split_exc:  # 分摊失败不影响出方案（但要留痕）
                            final_data["province_day_split"] = {"error": str(_split_exc)[:200]}
                            print(f"⚠️ [省域拆天{stage}] 失败（不影响出方案）: {_split_exc}", flush=True)
                            return None

                    _split_route = _split_province_days("")
                    if _split_route is not None:
                        final_data["route"] = _split_route
                        llm_routes = final_data["route"]

                    # Final daily-density gate.  Fairness, bandit, review and
                    # long-trip stages may replace the route after the first
                    # balance pass; run the same deterministic check immediately
                    # before emitting and caching the result.
                    try:
                        from core.itinerary_balance import enforce_category_mix, rebalance_daily_route

                        _final_target = target_daily_count if role == "深度探索" else min(target_daily_count, 5)
                        # 👑 省域行程按**当天片区**补节点（2026-11-01 实测的死循环）：
                        #    全局补全会把别的片区的景区塞进某一天，紧接着又被"同一天一片区"的
                        #    硬约束移出（补 9 个、删 9 个），用户看到的还是"地点很少"。
                        #    分片建议已经定了"哪天在哪片"，补全就只能从当天的片区里选。
                        _day_pools_for_balance: Optional[Dict[int, List[Dict[str, Any]]]] = None
                        if province_scope_name and province_anchors and _day_region_plan:
                            def _balance_region(item: Mapping[str, Any]) -> str:
                                return str(item.get("region_short") or "") or _province_region_of(item)

                            _day_pools_for_balance = {
                                int(_day): [
                                    dict(_item) for _item in poi_pool_data
                                    if isinstance(_item, Mapping)
                                    and (not _balance_region(_item) or _balance_region(_item) == str(_region))
                                ]
                                for _day, _region in _day_region_plan.items()
                            }
                        _balanced = rebalance_daily_route(
                            final_data.get("route") or [],
                            poi_pool_data,
                            days=trip_days,
                            target_daytime=_final_target,
                            day_pools=_day_pools_for_balance,
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
                                day_pools=_day_pools_for_balance,
                            )
                            final_data["route"] = _mixed["route"]
                            llm_routes = final_data["route"]
                            final_data["category_balance"] = _mixed["audit"]
                            final_data["daily_balance"] = {
                                **final_data["daily_balance"],
                                "category_balance": _mixed["audit"],
                            }

                        # 👑 硬约束收口（2026-10-05 横向对照实测发现的闭环漏洞）：
                        #    上面那些修复（排除项 / 公园限量 / 住宿兜底 / 日内现实性）全都跑在
                        #    **最终均衡 + 类别门禁之前**，而 `rebalance_daily_route` /
                        #    `enforce_category_mix` 会**从同一个候选池里按天补节点**（还会补酒店）。
                        #    实测伊宁 3 天：日内修复把「天山花海」的 4 个子景点移出了，
                        #    最终路线里它们**原样回来了**（同一天 5 个同景区子点），
                        #    而我们还在报告里写着"已移出" —— 报的和做的不一致，比不报更糟。
                        #    所以硬约束必须是**出稿前最后一道**，并且对账要按收口后的真实路线重算。
                        try:
                            _closeout_pool: List[Dict[str, Any]] = []
                            for _item in list(unique_attractions or []) + list(unique_foods or []) + list(cleaned_hotels or []):
                                if isinstance(_item, Mapping):
                                    _closeout_pool.append(dict(_item))
                            _closeout_center: List[float] = []
                            _closeout_center_text = str(geo_info.get("center") or coord or "")
                            if "," in _closeout_center_text:
                                try:
                                    _cl_text, _, _cla_text = _closeout_center_text.partition(",")
                                    _closeout_center = [float(_cl_text), float(_cla_text)]
                                except ValueError:
                                    _closeout_center = []
                            _closeout_tolerance = str(own_signals.get("distance_tolerance") or "normal")
                            _closeout_summary: List[str] = []

                            # 0) 👑 用户说了"不想跑远"（含点「不喜欢」写"太远"）时，
                            #    **先把远点就地换成算好的近点，再去做日内现实性检查** ——
                            #    顺序反了就等于"先把远点删掉、再想换什么"（那时已经没得换了）。
                            #    实测（2026-10-05 反馈闭环第 2 轮）：修复先移出胡杨书院，
                            #    后面算出的"可换疏附县博物馆"就落不了地，那天白少一个点。
                            if _closeout_tolerance == "low":
                                try:
                                    from core.travel_feasibility import (
                                        apply_near_substitutions as _apply_near,
                                        suggest_near_alternatives as _suggest_near,
                                    )

                                    _low_alternatives = _suggest_near(
                                        final_data.get("route") or [],
                                        _closeout_pool,
                                        city_center=_closeout_center,
                                        distance_tolerance=_closeout_tolerance,
                                        category_of=lambda item: classify_poi_category(
                                            str(item.get("name") or ""),
                                            str(item.get("type") or ""),
                                            bool(item.get("is_hotel")),
                                        ),
                                        tier_of=lambda item: scenic_tier(
                                            str(item.get("name") or ""), str(item.get("type") or "")
                                        ),
                                    )
                                    _swapped_route, _swap_actions = _apply_near(
                                        final_data.get("route") or [], _low_alternatives
                                    )
                                    if _swap_actions:
                                        final_data["route"] = _swapped_route
                                        _existing_swap = final_data.get("travel_substitution") or {}
                                        final_data["travel_substitution"] = {
                                            "actions": list(_existing_swap.get("actions") or []) + _swap_actions,
                                            "reason": "你说过不想跑那么远，已按你的意思就地换成同类的近点",
                                        }
                                        _closeout_summary.append(f"按你的意思换近点 {len(_swap_actions)} 处")
                                        print(
                                            "🔀 [按你的意思换近点] "
                                            + "；".join(
                                                f"第 {item['day']} 天 {item['from']}（约 {item.get('from_km')}km）→ {item['to']}"
                                                for item in _swap_actions[:3]
                                            ),
                                            flush=True,
                                        )
                                    elif _low_alternatives.get("days"):
                                        # 说得出远、但池子里没有同类同档次的近点：如实记下（不硬凑）
                                        print(
                                            "🔀 [按你的意思换近点] 候选池里没有同类同档次的近点，"
                                            "这一天会按「不跑那么远」把远点移出",
                                            flush=True,
                                        )
                                except Exception as _swap_exc:  # 换不动不影响出方案
                                    print(f"⚠️ [按你的意思换近点] 失败（不影响出方案）: {_swap_exc}", flush=True)

                            # 1) 用户禁令：门禁补分类时可能又把人文塞回来
                            if own_signals.get("exclude_cultural"):
                                _co_kept, _co_actions = repair_forbidden_categories(
                                    final_data.get("route") or [],
                                    forbidden=("cultural",),
                                    classify=classify_poi_category,
                                    pool=_closeout_pool,
                                )
                                if _co_actions:
                                    final_data["route"] = _co_kept
                                    _existing_exclusion = final_data.get("exclusion_repair") or {}
                                    final_data["exclusion_repair"] = {
                                        "forbidden": ["cultural"],
                                        "actions": list(_existing_exclusion.get("actions") or [])
                                        + [{**item, "stage": "final_closeout"} for item in _co_actions],
                                    }
                                    _closeout_summary.append(f"排除项又清掉 {len(_co_actions)} 处")

                            # 2) 城市公园限量
                            _co_park_cap = park_cap(
                                trip_days,
                                scenic_only=bool(own_signals.get("exclude_cultural")) and bool(own_signals.get("wants_scenic")),
                                wants_park=bool(own_signals.get("wants_park")),
                            )
                            _co_park_kept, _co_park_actions = trim_category_over_cap(
                                final_data.get("route") or [],
                                cap=int(_co_park_cap or 0),
                                classify=classify_poi_category,
                                scenic_tier_of=scenic_tier,
                                category="city_park",
                                pool=_closeout_pool,
                            )
                            if _co_park_actions:
                                final_data["route"] = _co_park_kept
                                _existing_park = final_data.get("park_trim") or {}
                                final_data["park_trim"] = {
                                    "cap": int(_co_park_cap or 0),
                                    "actions": list(_existing_park.get("actions") or [])
                                    + [{**item, "stage": "final_closeout"} for item in _co_park_actions],
                                }
                                _closeout_summary.append(f"公园超限又处理 {len(_co_park_actions)} 处")

                            # 3) 日内物理现实性（最容易被打回的一条：补节点正是"又塞远景区"的来源）
                            #    ⚠️ 必须排在**住宿收口之前**：它会移出"一天跑不了的远景区"，
                            #    而被移出的那个节点有可能本身就是住宿（实测伊宁：被移出的
                            #    「南河清泉…度假美宿」在没被移走时会被当成"这一晚已有住宿"，
                            #    于是住宿收口什么都不补；等它被移走，第一晚就空了）。
                            #    收口里任何"会删节点"的步骤都要排在住宿收口之前，住宿收口必须是最后一道。
                            _co_before = _evaluate_feasibility(
                                final_data.get("route") or [],
                                city_center=_closeout_center,
                                distance_tolerance=_closeout_tolerance,
                                days=trip_days,
                            )
                            if not _co_before.get("ok"):
                                _co_kept_route, _co_moved = _repair_feasibility(
                                    final_data.get("route") or [],
                                    city_center=_closeout_center,
                                    distance_tolerance=_closeout_tolerance,
                                )
                                if _co_moved:
                                    final_data["route"] = _co_kept_route
                                    _existing_travel = final_data.get("travel_repair") or {}
                                    final_data["travel_repair"] = {
                                        "moved_out": list(_existing_travel.get("moved_out") or [])
                                        + [
                                            {
                                                "name": str(item.get("name") or ""),
                                                "day": item.get("day"),
                                                "reason": item.get("removed_reason"),
                                                "stage": "final_closeout",
                                            }
                                            for item in _co_moved
                                        ],
                                        "reason": "一天跑不了两个远景区",
                                    }
                                    _closeout_summary.append(f"日内又移出 {len(_co_moved)} 个")
                                final_data["travel_feasibility"] = _evaluate_feasibility(
                                    final_data.get("route") or [],
                                    city_center=_closeout_center,
                                    distance_tolerance=_closeout_tolerance,
                                    days=trip_days,
                                )

                            # 4) 住宿收口（**收口的最后一道**：先补后减，封在 settle_lodging 里）
                            _co_settled, _co_lodge_added, _co_lodge_dropped, _co_missing = settle_lodging(
                                final_data.get("route") or [],
                                nights=max(1, trip_days - 1),
                                pool=list(cleaned_hotels or []),
                                classify=classify_poi_category,
                            )
                            if _co_lodge_added or _co_lodge_dropped or _co_missing:
                                final_data["route"] = _co_settled
                            if _co_lodge_dropped:
                                _existing_lodge_trim = final_data.get("lodging_trim") or {}
                                final_data["lodging_trim"] = {
                                    "nights": max(1, trip_days - 1),
                                    "actions": list(_existing_lodge_trim.get("actions") or [])
                                    + [
                                        {
                                            "code": "lodging_over_cap_dropped",
                                            "from": str(item.get("name") or ""),
                                            "day": item.get("day"),
                                            "reason": item.get("removed_reason"),
                                            "stage": "final_closeout",
                                        }
                                        for item in _co_lodge_dropped
                                    ],
                                }
                                _closeout_summary.append(f"住宿超夜数又移出 {len(_co_lodge_dropped)} 个")
                            if _co_lodge_added or _co_missing:
                                _existing_lodge = final_data.get("lodging_repair") or {}
                                final_data["lodging_repair"] = {
                                    "actions": list(_existing_lodge.get("actions") or [])
                                    + [{**item, "stage": "final_closeout"} for item in _co_lodge_added],
                                    "missing_days": sorted(set(list(_existing_lodge.get("missing_days") or []) + list(_co_missing))),
                                }
                                if _co_lodge_added:
                                    _closeout_summary.append(f"住宿又补 {len(_co_lodge_added)} 晚")

                            # 5) 对账必须按**收口后的真实路线**重算（否则界面上的数字对不上实际路线）
                            if _closeout_summary:
                                _co_counts = count_route_composition(
                                    final_data.get("route") or [], classify_poi_category, scenic_tier
                                )
                                _co_audit = composition_audit(
                                    _co_counts,
                                    {**composition, **_feasible_targets},
                                    forbidden=("cultural",) if own_signals.get("exclude_cultural") else (),
                                )
                                _co_urban_park = int(_co_counts.get("city_park") or 0)
                                if _co_urban_park > int(_co_park_cap or 0):
                                    _co_audit["notes"] = list(_co_audit.get("notes") or []) + [
                                        f"城市公园排了 {_co_urban_park} 个，超过上限 {_co_park_cap} 个（想把公园换成景区/吃饭可以直接说）"
                                    ]
                                    _co_audit["ok"] = False
                                if _supply_limit_notes:
                                    _co_audit["notes"] = list(_co_audit.get("notes") or []) + _supply_limit_notes
                                    _co_audit["supply_limited"] = _supply_limited
                                composition["park_cap"] = int(_co_park_cap or 0)
                                final_data["composition"] = {
                                    **composition,
                                    "feasible_targets": _feasible_targets,
                                    "supply_limited": _supply_limited,
                                    "supply_counts": _supply_counts,
                                    "actual": _co_counts,
                                    "audit": _co_audit,
                                    "recomputed_after_closeout": True,
                                }
                                final_data["hard_constraint_closeout"] = {
                                    "actions": _closeout_summary,
                                    "stage": "final_pre_emit",
                                }
                                print(
                                    "🔒 [硬约束收口] 最终门禁之后重新收口：" + "；".join(_closeout_summary)
                                    + f"｜收口后 自然景区={_co_counts.get('landmark')}/城市公园={_co_counts.get('city_park')}"
                                    f"/人文={_co_counts.get('cultural')}/美食={_co_counts.get('food')}/住宿={_co_counts.get('hotel')}"
                                    + ("" if _co_audit["ok"] else "｜仍有偏差：" + "；".join(_co_audit.get("notes") or [])),
                                    flush=True,
                                )
                        except Exception as _closeout_exc:  # 收口失败绝不影响出方案（但要留痕）
                            final_data["hard_constraint_closeout"] = {"error": str(_closeout_exc)[:200]}
                            print(f"⚠️ [硬约束收口] 失败（不影响出方案）: {_closeout_exc}", flush=True)
                        # 👑 收口之后再拆一次天（2026-11-02 实测）：门禁补点/住宿就近换店/换餐
                        #    都会往某一天塞点，把刚摊平的片区块又挤成"一天 209km、另一天没玩点"。
                        #    这里再跑一遍同一个确定性分摊；`redistribute_province_days` 自己会在
                        #    合规时不动，所以多做一次没有副作用（`province_day_split.passes` 记次数）。
                        _split_route = _split_province_days("·收口后")
                        if _split_route is not None:
                            final_data["route"] = _split_route
                            llm_routes = final_data["route"]
                        _stage("硬约束修复与收口")
                        # 行程数组的顺序就是行程顺序：住宿兜底/补点是"追加"节点的，
                        # 补的第 1 晚会被放到数组末尾（实测伊宁：第 1 天的民宿排在第 3 天之后），
                        # 前端按天分组看不出，但按数组顺序连线/导出的消费方会画出跨天回头线。
                        # 放在收口之后、所有下游（公平性/校验/事实/前端）之前，一次排好。
                        try:
                            final_data["route"] = order_route_by_day(final_data.get("route") or [])
                        except Exception as _order_exc:  # 排序失败不影响出方案
                            print(f"⚠️ [路线排序] 按天排序失败（不影响出方案）: {_order_exc}", flush=True)

                        # 👑 按地理就近重排"天与天之间"的点（2026-10-07 真实诊断后加的）：
                        #    实测喀什第 2 天是"市区点 → 巴楚的胡杨书院(216km) → 回市区住"，一个远点
                        #    花掉出去+回来两趟 = 449km。重排后最赶的一天 449 → 231km、全程 610 → 299km，
                        #    `space_efficiency` 0.0 → 0.35、总分 +3.9。约束都写在
                        #    `core/route_composition.rebalance_days_by_geography` 的文档里
                        #    （住宿/用户点名的点/没坐标的点不动、每天节点数与类别尽量不变、只在真的
                        #    明显变短时才动）。
                        try:
                            # 保护哪一类玩点跟着**用户诉求**走（"想看自然风景"→ 每天保住自然景观）：
                            # 一刀切保护"自然+人文"会把有价值的重排否掉（实测伊宁 167→130km 被否）。
                            _signals_for_rebalance = _quality_signals if isinstance(_quality_signals, dict) else {}
                            _wants_scenic = bool(_signals_for_rebalance.get("wants_scenic"))
                            _wants_cultural = bool(_signals_for_rebalance.get("wants_cultural"))
                            _protect_kinds: List[str] = []
                            if _wants_scenic and not _wants_cultural:
                                _protect_kinds = ["scenic"]
                            elif _wants_cultural and not _wants_scenic:
                                _protect_kinds = ["cultural"]
                            # 🗺️ 省域行程**不做跨片区换天**（2026-11-01 实测）：
                            #    这一趟"哪天在哪片"是分片建议定的（4 天四川 = 成都 2 天 + 阿坝 2 天），
                            #    按里程重排会把都江堰（成都）搬到计划里属于阿坝的那一天 ——
                            #    于是披露里写着"Day 3-4 在阿坝"，实际 Day 3 还在成都，
                            #    阿坝的行程被压成一天。片区内的里程由 `evaluate_province_feasibility`
                            #    按当天预算判（超了会如实披露），不需要靠跨天搬点来降里程。
                            if province_scope_name and province_anchors and _day_region_plan:
                                print("🗺️ [按地理分天] 省域行程按片区结构走，跳过跨天重排", flush=True)
                                _protect_kinds = []
                            else:
                                _rebalanced, _rebalance_report = rebalance_days_by_geography(
                                    final_data.get("route") or [], protect_kinds=_protect_kinds
                                )
                                if _rebalance_report.get("applied"):
                                    final_data["route"] = _rebalanced
                                    final_data["day_rebalance"] = _rebalance_report
                                    print(f"🧭 [按地理分天] {_rebalance_report['note']}", flush=True)
                                elif _rebalance_report.get("moved"):
                                    # 算了但没采用也要留痕：否则"为什么没重排"事后无从回答
                                    final_data["day_rebalance"] = _rebalance_report
                                    print(f"🧭 [按地理分天] 未采用：{_rebalance_report['note']}", flush=True)
                        except Exception as _rebalance_exc:  # 重排失败不影响出方案
                            print(f"⚠️ [按地理分天] 失败（不影响出方案）: {_rebalance_exc}", flush=True)

                        # 👑 缺餐的那天补一个饭点（2026-10-07：喀什/伊宁都出现过 `skeleton_meal_missing`，
                        #    而这一条**能从候选池补**）。必须放在分天重排**之后**：
                        #    重排会把饭点挪到别的天，早补的等于白补。
                        #    候选优先取"离当天玩点最近"的 —— 否则会给远点那天塞一家市区餐馆
                        #    （实测那样会平白多出 city→far→city 两趟）。池子取不到就如实不动。
                        try:
                            from core.plan_review import top_up_missing_meals as _top_up_meals

                            _meal_pool = _quality_pool_for_final if isinstance(_quality_pool_for_final, dict) else None
                            # 👑 缺餐的往往是**远点那天**，而候选池是全城取的 —— 先按天做一次
                            #    **周边检索**（高德 place/around），拿"当天玩点附近真实存在的餐馆"。
                            #    实测：喀什第 3 天全城候选最近 82.6km，周边检索 9.1km 就有。
                            _nearby_fetch = getattr(toolbox, "get_nearby_pois", None)
                            if _nearby_fetch:
                                try:
                                    # 检索半径要和"能不能接受"的尺子对齐：补餐上限是 30km
                                    # （`meal_max_km`），所以周边检索用 25km，别用 15km —— 实测
                                    # 毕棚沟到理县镇上约 17km，15km 会漏掉唯一能吃饭的那一片。
                                    _meal_radius = (
                                        40.0
                                        if str((own_signals or {}).get("distance_tolerance") or "normal") == "high"
                                        else 25.0
                                    )
                                    _nearby_pool, _nearby_report = await asyncio.wait_for(
                                        collect_nearby_food_candidates(
                                            final_data.get("route") or [],
                                            days=trip_days,
                                            fetch_nearby=_nearby_fetch,
                                            radius_km=_meal_radius,
                                        ),
                                        timeout=POOL_FETCH_TIMEOUT_SECONDS,
                                    )
                                    if _nearby_pool.get("food"):
                                        _meal_pool = {
                                            **(dict(_meal_pool) if isinstance(_meal_pool, dict) else {}),
                                            # 周边候选**排在前面**（它们离当天玩点最近）
                                            "food": list(_nearby_pool["food"])
                                            + list(((dict(_meal_pool) if isinstance(_meal_pool, dict) else {}).get("food")) or []),
                                        }
                                        final_data["meal_nearby_search"] = _nearby_report
                                        print(
                                            "🔎 [周边补餐] 对缺餐的那天做了周边检索："
                                            + "、".join(
                                                f"第 {item['day']} 天 {item.get('found')} 家"
                                                for item in _nearby_report
                                                if item.get("found") is not None
                                            ),
                                            flush=True,
                                        )
                                except Exception as _nearby_exc:
                                    print(f"⚠️ [周边补餐] 周边检索失败（改用全城候选）: {_nearby_exc}", flush=True)
                            # 池子空的常见情况：这次规划不需要候选池（没有排他/配额/超预算），
                            # 于是 `_pool` 是 {}。缺餐要补就得**按需取一次餐饮候选**（只取这一类，便宜），
                            # 取不到就退回"从富余那天挪一个"（`top_up_missing_meals` 内部处理）。
                            if not _meal_pool:
                                from core.poi_pool import build_candidate_pool as _build_meal_pool

                                _meal_fetch = getattr(toolbox, "get_dynamic_pois", None)
                                if _meal_fetch and target_city:
                                    try:
                                        _meal_pool = await asyncio.wait_for(
                                            _build_meal_pool(
                                                target_city, ["food"], _meal_fetch, limit_per_intent=8
                                            ),
                                            timeout=POOL_FETCH_TIMEOUT_SECONDS,
                                        )
                                    except Exception as _meal_pool_exc:
                                        print(f"⚠️ [补餐] 餐饮候选取用失败，改用挪动已有饭点: {_meal_pool_exc}", flush=True)
                                        _meal_pool = None
                            _meal_fix = _top_up_meals(
                                {"days": trip_days},
                                final_data,
                                _meal_pool,
                                {
                                    "max_nodes_per_day": _final_target + 1,
                                    # 太远的饭点宁可不塞（跟用户的距离口径走）
                                    "meal_max_km": 60.0
                                    if str((own_signals or {}).get("distance_tolerance") or "normal") == "high"
                                    else 30.0,
                                },
                            )
                            if _meal_fix.get("changed"):
                                final_data["route"] = _meal_fix["plan"]["route"]
                                final_data["route"] = order_route_by_day(final_data["route"])
                                final_data["meal_topup"] = {
                                    "actions": _meal_fix["actions"],
                                    "skipped": _meal_fix.get("skipped") or [],
                                }
                                print(
                                    "🍽️ [补餐] 给缺餐的那天补了 "
                                    + "、".join(
                                        f"第 {item['day']} 天 {item['node']}"
                                        + (f"（距当天玩点 {item['distance_km']}km）" if item.get("distance_km") is not None else "")
                                        for item in _meal_fix["actions"][:3]
                                    ),
                                    flush=True,
                                )
                            elif _meal_fix.get("skipped"):
                                # 算过但没塞也要留痕（"为什么这天没有饭点"事后要能回答）
                                final_data["meal_topup"] = {"actions": [], "skipped": _meal_fix["skipped"]}
                                print(
                                    "🍽️ [补餐] 没补："
                                    + "；".join(str(item.get("reason")) for item in _meal_fix["skipped"][:2]),
                                    flush=True,
                                )
                        except Exception as _meal_exc:  # 补餐失败不影响出方案
                            print(f"⚠️ [补餐] 失败（不影响出方案）: {_meal_exc}", flush=True)

                        # 👑 无效坐标清洗：`(0,0)` 是"海面上"，不是地点。数据源没给坐标时很容易被写成
                        #    0/0（实测 2026-10-05：供应商候选没坐标 → 规划写 [0,0] → "第 2 天 9438km"，
                        #    地图上还会把这个点画到几内亚湾）。这里把它**清成缺失**并如实登记：
                        #    缺坐标的节点不参与里程/预算计算，配图交给带"位置示意"标注的兜底。
                        try:
                            _invalid_coord_names: List[str] = []
                            for _node in final_data.get("route") or []:
                                if not isinstance(_node, dict):
                                    continue
                                _coord_value = _node.get("lnglat")
                                if (
                                    isinstance(_coord_value, (list, tuple))
                                    and len(_coord_value) >= 2
                                ):
                                    try:
                                        _zero = float(_coord_value[0]) == 0.0 and float(_coord_value[1]) == 0.0
                                    except (TypeError, ValueError):
                                        _zero = False
                                    if _zero:
                                        _node["lnglat"] = []
                                        _node["coordinate_status"] = "missing"
                                        _node["coordinate_note"] = "数据源没给坐标（原值 0,0 已清掉，不参与里程计算）"
                                        _invalid_coord_names.append(str(_node.get("name") or ""))
                            if _invalid_coord_names:
                                final_data["invalid_coordinates"] = {
                                    "names": _invalid_coord_names,
                                    "note": "这些节点没有可信坐标（数据源给了 0,0）：不参与里程与距离判断，地图上不标位置。",
                                }
                                print(
                                    "🧹 [坐标清洗] 清掉 "
                                    f"{len(_invalid_coord_names)} 个 0,0 坐标：{'、'.join(_invalid_coord_names[:4])}",
                                    flush=True,
                                )
                        except Exception as _coord_exc:  # 清洗失败不影响出方案
                            print(f"⚠️ [坐标清洗] 失败（不影响出方案）: {_coord_exc}", flush=True)

                        # 👑 目的地范围校验（2026-11-02）：坐标"合法"不等于"属于这趟行程"。
                        #    模型偶尔给出经纬度都在合法区间、却明显在别的城市的坐标 ——
                        #    前端按它把整天地图缩到很低级别，里程里还会凭空多出几十公里的
                        #    "通勤"（实测 DAY 1 曾出现 47.7km 的假段）。
                        #    口径与上面的 `(0,0)` 清洗一致：坐标不可信就当没有（名称等其他字段保留），
                        #    不参与里程/预算判断，地图上不标位置。
                        #    城市行程以城市中心为界；省域行程把各片区中心也算作合法中心，
                        #    否则九寨沟/稻城亚丁这类真景区会被城市口径误删。
                        try:
                            from core.place_quality import (
                                clear_out_of_area_coordinates,
                                poi_coordinate_radius_km,
                            )

                            _guard_centers: List[List[float]] = []
                            if isinstance(city_lnglat, (list, tuple)) and len(city_lnglat) >= 2:
                                _guard_centers.append([float(city_lnglat[0]), float(city_lnglat[1])])
                            if province_scope_name and province_anchors:
                                for _anchor in province_anchors:
                                    _anchor_center = _anchor.get("center") or []
                                    if isinstance(_anchor_center, (list, tuple)) and len(_anchor_center) >= 2:
                                        _guard_centers.append(
                                            [float(_anchor_center[0]), float(_anchor_center[1])]
                                        )
                            _guard_radius = poi_coordinate_radius_km(
                                trip_days,
                                str(own_signals.get("distance_tolerance") or "normal"),
                                province_scope=bool(province_scope_name and province_anchors),
                            )
                            _out_of_area = clear_out_of_area_coordinates(
                                final_data, centers=_guard_centers, radius_km=_guard_radius, copy=False
                            )
                            if _out_of_area:
                                final_data["out_of_area_coordinates"] = {
                                    "names": [str(item.get("name") or "") for item in _out_of_area],
                                    "radius_km": round(_guard_radius, 1),
                                    "centers": _guard_centers,
                                    "items": _out_of_area,
                                    "note": "这些节点的坐标离目的地超出可信范围（跨城/跨省的错误坐标）："
                                            "已按无效清除，不参与里程与距离判断，地图上不标位置；名称等其他字段保留。",
                                }
                                print(
                                    "🧹 [坐标校验] "
                                    f"{len(_out_of_area)} 个坐标超出 {_guard_radius:.0f}km 可信范围："
                                    + "、".join(
                                        f"{item.get('name') or '(未命名)'}（{item.get('distance_km')}km）"
                                        for item in _out_of_area[:4]
                                    ),
                                    flush=True,
                                )
                        except Exception as _area_exc:  # 校验失败不影响出方案
                            print(f"⚠️ [坐标校验] 失败（不影响出方案）: {_area_exc}", flush=True)

                        # 👑 酒店实时房价（供应商只读 MCP）—— **必须放在住宿定型之后**：
                        #    硬约束收口的最后一道是 `settle_lodging`（会补/换酒店），
                        #    取价阶段查到的房价会随着换店一起丢掉（2026-10-05 实测：
                        #    取价查的是「库尔勒园林宾馆 ¥165」，收口后最终节点换了别家）。
                        #    实测真实方案里的酒店名 4/5 能在供应商查到实价（¥131/¥316/¥635…），
                        #    这正是"高德不提供房价"这个最大缺口唯一的真来源。
                        #    只读白名单写死在 `core/rollinggo_mcp.READ_ONLY_TOOLS`，绝不下单/取消。
                        try:
                            from core.rollinggo_mcp import price_lodging_nodes

                            _lodging_price = await price_lodging_nodes(
                                final_data,
                                city=target_city,
                                nights=max(1, trip_days - 1),
                                start_date=str(user_prefs.get("start_date") or ""),
                                limit=3,
                            )
                            if isinstance(_lodging_price.get("plan"), dict) and isinstance(
                                _lodging_price["plan"].get("route"), list
                            ):
                                final_data["route"] = _lodging_price["plan"]["route"]
                            _lodging_report = _lodging_price.get("report") or {}
                            final_data["price_audit"] = {
                                **(final_data.get("price_audit") or {}),
                                "rollinggo": _lodging_report,
                            }
                            if _lodging_report.get("enabled"):
                                print(
                                    f"🏨 [酒店实时价] 供应商只读 MCP 查 {_lodging_report.get('queried', 0)} 家，"
                                    f"匹配到 {_lodging_report.get('matched', 0)} 家，"
                                    f"其中给出数字 {_lodging_report.get('with_price', 0)} 家"
                                    + (
                                        "（"
                                        + "、".join(
                                            f"{key} ¥{value:g}"
                                            for key, value in (_lodging_report.get("prices") or {}).items()
                                        )
                                        + "）"
                                        if _lodging_report.get("prices")
                                        else ""
                                    )
                                    + (
                                        "；查到这家但没有数字：" + "、".join(_lodging_report.get("without_price") or [])
                                        if _lodging_report.get("without_price")
                                        else ""
                                    )
                                    + (
                                        "；供应商没有这一家：" + "、".join(_lodging_report.get("not_found") or [])
                                        if _lodging_report.get("not_found")
                                        else ""
                                    )
                                    + f"；报价口径：{_lodging_report.get('check_in')}",
                                    flush=True,
                                )
                            else:
                                print(
                                    f"🏨 [酒店实时价] 未启用：{_lodging_report.get('reason')}",
                                    flush=True,
                                )
                        except Exception as _lodging_price_exc:  # 供应商失败绝不影响出方案
                            final_data["price_audit"] = {
                                **(final_data.get("price_audit") or {}),
                                "rollinggo": {"enabled": False, "error": f"{type(_lodging_price_exc).__name__}: {str(_lodging_price_exc)[:140]}"},
                            }
                            print(
                                f"⚠️ [酒店实时价] 查询失败（不影响出方案）: {_lodging_price_exc}",
                                flush=True,
                            )

                        # 👑 「这天跑得远，想换的话换哪个」—— 只提议、不改方案（用户口径：远的一天
                        #    可以接受、但要标明；真去掉得等他说一句）。把**换哪个**先算出来，
                        #    免得用户说了之后系统还要现场猜（猜出来的往往还是同类远点）。
                        try:
                            from core.travel_feasibility import suggest_near_alternatives

                            _near_alternatives = suggest_near_alternatives(
                                final_data.get("route") or [],
                                list(unique_attractions or []) + list(unique_foods or []) + list(cleaned_hotels or []),
                                city_center=_closeout_center if isinstance(_closeout_center, list) else [],
                                distance_tolerance=str(own_signals.get("distance_tolerance") or "normal"),
                                category_of=lambda item: classify_poi_category(
                                    str(item.get("name") or ""), str(item.get("type") or ""), bool(item.get("is_hotel"))
                                ),
                                # 档次也不能降：拿"城市公园"顶替"真景区"正是用户明确反对的
                                tier_of=lambda item: scenic_tier(
                                    str(item.get("name") or ""), str(item.get("type") or "")
                                ),
                            )
                            if _near_alternatives.get("days"):
                                final_data["travel_alternatives"] = _near_alternatives
                                print(
                                    "🔁 [远点替代] "
                                    + "；".join(
                                        f"第 {item.get('day')} 天 {item.get('travel_km')}km>{item.get('budget_km')}km"
                                        + (
                                            " → 可换 " + "、".join(option.get("name") or "" for option in item.get("options") or [])
                                            if item.get("options")
                                            else " → 候选池里没有同类近点可换"
                                        )
                                        for item in _near_alternatives["days"][:3]
                                    ),
                                    flush=True,
                                )
                                # 👑 用户**明确说了"不想跑远"**时，就不是"提议"而是**执行**：
                                #    用户口径（2026-10-05）："如果用户点击不喜欢或者二次增量不想去
                                #    这么远的地方，就按照用户的意图来"。就地换（同一天同一时段），
                                #    没同类同档次近点的不动（如实留给披露）。
                                if str(own_signals.get("distance_tolerance") or "normal") == "low":
                                    from core.travel_feasibility import apply_near_substitutions

                                    _swapped, _swap_actions = apply_near_substitutions(
                                        final_data.get("route") or [], _near_alternatives
                                    )
                                    if _swap_actions:
                                        final_data["route"] = _swapped
                                        _existing_swap = final_data.get("travel_substitution") or {}
                                        final_data["travel_substitution"] = {
                                            "actions": list(_existing_swap.get("actions") or []) + _swap_actions,
                                            "reason": "你说过不想跑那么远，已按你的意思就地换成同类的近点",
                                        }
                                        print(
                                            "🔀 [按你的意思换近点] "
                                            + "；".join(
                                                f"第 {item['day']} 天 {item['from']}（约 {item.get('from_km')}km）→ {item['to']}"
                                                for item in _swap_actions[:3]
                                            ),
                                            flush=True,
                                        )
                        except Exception as _near_exc:  # 算不出替代点不影响出方案
                            print(f"⚠️ [远点替代] 计算失败（不影响出方案）: {_near_exc}", flush=True)

                        # 👑 缺坐标的住宿：用高德按**名字**补一次坐标（2026-10-05 实测需要）
                        #    供应商给的酒店候选有时只有名字和地址、没有坐标；节点被清成"缺坐标"
                        #    之后通勤距离算不出来（`anchoring` 掉到 0.8）、地图上也标不出位置。
                        #    这些名字是真实存在的，按名字查一次高德就能补上；**补不到就保持缺失**，
                        #    绝不编坐标（`coordinate_status=missing` 会如实留在方案里）。
                        try:
                            from core.place_quality import nodes_missing_coordinates

                            # ⚠️ 必须 `copy=False`（2026-10-05 实测抓到）：默认返回的是**浅拷贝**，
                            #    改拷贝写不回 `final_data["route"]` —— 日志会骗人（打了"补到 1 个"，
                            #    payload 里还是 `lnglat: []`，`anchoring` 也没变）。见 nodes_of 的说明。
                            _coord_gap = nodes_missing_coordinates(final_data, only_lodging=True, copy=False)[:3]
                            _coord_filled: List[str] = []
                            _coord_failed: List[str] = []
                            for _node in _coord_gap:
                                _node_name = str(_node.get("name") or "")
                                if not _node_name:
                                    continue
                                _found: List[float] = []
                                try:
                                    # ⚠️ **必须显式 `play_worthy=False`**（2026-10-05 实测）：
                                    #    `is_play_worthy` 会把"住宿服务"整类当非玩点丢掉，
                                    #    于是"按名字查酒店"永远 0 条 —— 我一开始把它误判成
                                    #    "高德没有这家/被限流"，探针一比对才看清是过滤器的问题
                                    #    （`tools/probe_hotel_play_worthy.py`：同一个名字
                                    #    play_worthy=True 回 0 条、False 回 1 条且带坐标）。
                                    #    有 keywords 时也不要再传 types（会把关键词相关性打没）。
                                    _pois = await toolbox.get_dynamic_pois(
                                        target_city, keywords=_node_name, limit=3, play_worthy=False
                                    )
                                except Exception:
                                    _pois = []
                                for _poi in _pois or []:
                                    if not isinstance(_poi, Mapping):
                                        continue
                                    _poi_name = _normalize_text(str(_poi.get("name") or ""))
                                    if not _poi_name or _normalize_text(_node_name) not in _poi_name and _poi_name not in _normalize_text(_node_name):
                                        continue
                                    _candidate_coord = _poi.get("lnglat")
                                    if isinstance(_candidate_coord, (list, tuple)) and len(_candidate_coord) >= 2:
                                        try:
                                            if float(_candidate_coord[0]) != 0.0 or float(_candidate_coord[1]) != 0.0:
                                                _found = [float(_candidate_coord[0]), float(_candidate_coord[1])]
                                                break
                                        except (TypeError, ValueError):
                                            continue
                                if _found:
                                    _node["lnglat"] = _found
                                    _node["coordinate_status"] = "verified"
                                    _node["coordinate_source"] = "amap_lookup"
                                    _coord_filled.append(_node_name)
                                else:
                                    _coord_failed.append(_node_name)
                            if _coord_filled or _coord_failed:
                                final_data["coordinate_backfill"] = {
                                    "filled": _coord_filled,
                                    "failed": _coord_failed,
                                    "note": "缺坐标的住宿按名字在高德补了一次（`play_worthy=False`，否则住宿会被当非玩点整类丢掉）。"
                                    "补到的标 coordinate_source=amap_lookup；没补到的仍是缺失（**不编坐标**）——"
                                    "没补到就是高德按这个名字也查不到（或查询被限流），两种可能我们分不清，不猜。",
                                }
                                print(
                                    "📍 [坐标补齐] 按名字补到 "
                                    f"{len(_coord_filled)} 个（{'、'.join(_coord_filled) or '无'}）"
                                    + (f"；仍缺 {len(_coord_failed)} 个（{'、'.join(_coord_failed)}）" if _coord_failed else ""),
                                    flush=True,
                                )
                        except Exception as _coord_fill_exc:  # 补坐标失败不影响出方案
                            print(f"⚠️ [坐标补齐] 失败（不影响出方案）: {_coord_fill_exc}", flush=True)


                        # 👑 住宿离当天玩点太远就换成附近的（2026-10-07：伊宁 anchoring=0.143，
                        #    第 1 天玩点在英塔木镇、酒店在 38.5km 外的市区，第 2 天 46.9km）。
                        #    `ensure_lodging_per_night` 只在缺夜时按"离当天最近"补 ——
                        #    已经有酒店的那一晚没人回头看它远不远，这里补上这一眼。
                        # ⚠️ **必须放在「坐标补齐」之后**（2026-10-07 实测踩到的顺序坑）：
                        #    放在取价之前时，那一晚的酒店那时**还没有坐标**
                        #    （日志原话：`第 1 天 跳过（no_hotel_coord）`）—— 坐标是"按名字在高德查一次"
                        #    那一步才拿到的，于是"离玩点多远"根本量不出来，38.5km 外的酒店就漏过去了。
                        #    换过店之后**再把这个新酒店单独问一次供应商价**（补回顺序带来的缺口）。
                        try:
                            from core.plan_review import adjust_lodging_by_geography as _adjust_lodging

                            _lodging_fetch = getattr(toolbox, "get_nearby_pois", None)
                            if _lodging_fetch:
                                # ⚠️ 住宿的**距离阈值不跟着"不介意跑远"放宽**：那句话说的事是
                                #    "一天可以开很远"，不是"住得离玩点很远没关系"。住得近是质量目标，
                                #    跟里程容忍度无关 —— 否则会出现"用户说能跑远，我们就把 38km 外的
                                #    酒店留着"（伊宁第 1 天就是这样，尺子照旧判它不合格）。
                                _lodging_fix = await asyncio.wait_for(
                                    _adjust_lodging(
                                        final_data.get("route") or [],
                                        days=trip_days,
                                        fetch_nearby=_lodging_fetch,
                                        threshold_km=15.0,
                                        radius_km=25.0,
                                    ),
                                    timeout=POOL_FETCH_TIMEOUT_SECONDS,
                                )
                                if _lodging_fix.get("measured"):
                                    # 逐晚**量出来的距离**都要留痕：不然"为什么这一晚没换"
                                    # 事后无法回答（实测就是这么卡住的）
                                    print(
                                        "🏨 [住宿就近] 各晚距当天玩点："
                                        + "、".join(
                                            (
                                                f"第 {item['day']} 天 {item['km']}km"
                                                if item.get("km") is not None
                                                else f"第 {item['day']} 天 跳过（{item.get('status')}）"
                                            )
                                            for item in _lodging_fix["measured"]
                                        ),
                                        flush=True,
                                    )
                                if _lodging_fix.get("changed"):
                                    final_data["route"] = order_route_by_day(_lodging_fix["plan"]["route"])
                                    final_data["lodging_geo_adjust"] = {
                                        "actions": _lodging_fix["actions"],
                                        "skipped": _lodging_fix.get("skipped") or [],
                                    }
                                    print(
                                        "🏨 [住宿就近] 换掉太远的那晚："
                                        + "、".join(
                                            f"第 {item['day']} 天 {item['from']}（{item['from_km']}km）→ "
                                            f"{item['to']}（{item['to_km']}km）"
                                            for item in _lodging_fix["actions"][:2]
                                        ),
                                        flush=True,
                                    )
                                    # 换过的酒店**现在**才存在，所以单独问一次供应商价：
                                    # 不查就只能是"暂无供应商数据"，而那本来是能查到的（顺序问题，不是数据问题）。
                                    try:
                                        from core.rollinggo_mcp import price_lodging_nodes as _price_swapped

                                        _swapped_names = {
                                            str(item.get("to") or "") for item in _lodging_fix["actions"]
                                        }
                                        _swapped_nodes = [
                                            node
                                            for node in (final_data.get("route") or [])
                                            if str(node.get("name") or "") in _swapped_names
                                        ]
                                        if _swapped_nodes:
                                            _swapped_price = await asyncio.wait_for(
                                                _price_swapped(
                                                    {"route": _swapped_nodes},
                                                    city=target_city,
                                                    nights=1,
                                                    start_date=str(user_prefs.get("start_date") or ""),
                                                    limit=1,
                                                ),
                                                timeout=25.0,
                                            )
                                            _priced = {
                                                str(node.get("name") or ""): node
                                                for node in ((_swapped_price.get("plan") or {}).get("route") or [])
                                            }
                                            if _priced:
                                                final_data["route"] = [
                                                    _priced.get(str(node.get("name") or ""), node)
                                                    for node in (final_data.get("route") or [])
                                                ]
                                            _swapped_report = _swapped_price.get("report") or {}
                                            final_data["price_audit"] = {
                                                **(final_data.get("price_audit") or {}),
                                                "rollinggo_after_lodging_swap": _swapped_report,
                                            }
                                            print(
                                                "🏨 [住宿就近·补价] 换过的酒店单独查了一次："
                                                f"查 {_swapped_report.get('queried', 0)} 家、"
                                                f"给出数字 {_swapped_report.get('with_price', 0)} 家",
                                                flush=True,
                                            )
                                    except Exception as _swapped_exc:  # 补价失败不影响出方案
                                        print(
                                            f"⚠️ [住宿就近·补价] 失败（新酒店价格按未核实处理）: {_swapped_exc}",
                                            flush=True,
                                        )
                                elif _lodging_fix.get("skipped"):
                                    final_data["lodging_geo_adjust"] = {
                                        "actions": [],
                                        "skipped": _lodging_fix["skipped"],
                                    }
                                    for _item in _lodging_fix["skipped"][:2]:
                                        if _item.get("reason"):
                                            print(
                                                f"🏨 [住宿就近] 第 {_item.get('day')} 天住宿距玩点 "
                                                f"{_item.get('current_km')}km：{_item.get('reason')}",
                                                flush=True,
                                            )
                        except Exception as _lodging_exc:  # 住宿就近失败不影响出方案
                            print(f"⚠️ [住宿就近] 失败（不影响出方案）: {_lodging_exc}", flush=True)

                        # 👑 **排得太远**的饭点换成附近的（2026-10-07：伊宁第 1 天三家餐馆都在
                        #    30km 外的县城，玩点在英塔木镇）。与"补餐"分工不同：
                        #    补餐治"没有饭点"，这里治"有饭点但太远"。一对一替换，
                        #    所以"每天至少一餐"不会被破坏。
                        try:
                            from core.plan_review import adjust_meals_by_geography as _adjust_meals

                            _meal_fetch2 = getattr(toolbox, "get_nearby_pois", None)
                            if _meal_fetch2:
                                _meal_geo = await asyncio.wait_for(
                                    _adjust_meals(
                                        final_data.get("route") or [],
                                        days=trip_days,
                                        fetch_nearby=_meal_fetch2,
                                        threshold_km=15.0,
                                        radius_km=25.0,
                                    ),
                                    timeout=POOL_FETCH_TIMEOUT_SECONDS,
                                )
                                if _meal_geo.get("measured"):
                                    print(
                                        "🍜 [餐饮就近] 离当天玩点超过 15km 的饭点："
                                        + "、".join(
                                            f"第 {item['day']} 天 {item['meal'][:12]}（{item['km']}km）"
                                            for item in _meal_geo["measured"]
                                        ),
                                        flush=True,
                                    )
                                if _meal_geo.get("changed"):
                                    final_data["route"] = order_route_by_day(_meal_geo["plan"]["route"])
                                    final_data["meal_geo_adjust"] = {
                                        "actions": _meal_geo["actions"],
                                        "skipped": _meal_geo.get("skipped") or [],
                                    }
                                    print(
                                        "🍜 [餐饮就近] 换掉太远的饭点："
                                        + "、".join(
                                            f"第 {item['day']} 天 {item['from']}（{item['from_km']}km）→ "
                                            f"{item['to']}（{item['to_km']}km）"
                                            for item in _meal_geo["actions"][:2]
                                        ),
                                        flush=True,
                                    )
                                elif _meal_geo.get("skipped"):
                                    final_data["meal_geo_adjust"] = {
                                        "actions": [],
                                        "skipped": _meal_geo["skipped"],
                                    }
                        except Exception as _meal_geo_exc:  # 餐饮就近失败不影响出方案
                            print(f"⚠️ [餐饮就近] 失败（不影响出方案）: {_meal_geo_exc}", flush=True)

                        # 👑 可选住宿的**通勤距离**：只按价格/星级排是不够的（`anchoring` 实测掉到 0.8），
                        #    价格可比、距离也要可比 —— 用户才知道"这家便宜 60 块，但离玩点多 30km"。
                        try:
                            _supply_options = (final_data.get("lodging_supply") or {}).get("hotels") or []
                            if _supply_options:
                                from core.plan_quality import annotate_lodging_options

                                _annotated = annotate_lodging_options(final_data, _supply_options)
                                final_data["lodging_supply"] = {
                                    **(final_data.get("lodging_supply") or {}),
                                    "hotels": _annotated,
                                }
                                _far_options = [
                                    item for item in _annotated if item.get("far_from_plays") is True
                                ]
                                if _far_options:
                                    print(
                                        "🚶 [住宿通勤] "
                                        f"{len(_far_options)}/{len(_annotated)} 家离方案里的玩点超过 15km："
                                        + "、".join(
                                            f"{item.get('name')}（约 {item.get('nearest_play_km')}km）"
                                            for item in _far_options[:3]
                                        ),
                                        flush=True,
                                    )
                        except Exception as _anchor_exc:  # 通勤距离算不出来不影响出方案
                            print(f"⚠️ [住宿通勤] 距离标注失败（不影响出方案）: {_anchor_exc}", flush=True)
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

                    # 👑 出稿前把「顺序与时刻」对齐一次（2026-11-02 实测，四川 4 天）：
                    #    补住宿/就近换酒店会把住宿节点插到当天中间，而时刻是按数组位置贴的
                    #    （住宿固定 21:30）→ 第 2 天「21:30 入住」后面还有「11:30 游览」
                    #    「13:30 用餐」，质量闸门判 `day_out_of_order`，verdict 直接 fail。
                    #    纯本地、零网络，只把住宿挪到当天末尾并重贴时刻，不改地点与跨天归属。
                    try:
                        # 👑 省域行程在收口后补排一次日间顺序：就近换酒店/换餐是逐点替换，
                        #    换完当天的数组顺序会变成"东→西→东→西"（实测第 2 天 244.6km）。
                        #    只排当天、不改天不改点；随后 `normalize_day_sequence` 重贴时刻。
                        if province_scope_name and province_anchors:
                            final_data["route"] = order_day_stops_by_nearest(final_data.get("route") or [])
                        final_data["route"] = normalize_day_sequence(final_data.get("route") or [])
                        llm_routes = final_data["route"]
                    except Exception as _normalize_exc:  # 失败不影响出方案
                        print(f"⚠️ [顺序对齐] 失败，保留原顺序: {_normalize_exc}", flush=True)

                    # 👑 出稿前把「每日密度」按**最终路线**重算一次（2026-10-05 实测发现）：
                    #    上面那道门禁跑在硬约束收口**之前**，而收口会移出节点（例如"一天跑不了
                    #    两个远景区"），于是下发的 payload 里 `daily_balance` 描述的是**收口前**的
                    #    路线 —— 自己产出的方案，拿自己去核对时说"不达标"（语义缓存就是这么被
                    #    判死的）。这里重算，让"方案里写着的结论"和"方案的路线"一致。
                    try:
                        from core.itinerary_balance import daily_balance_audit as _daily_balance_audit

                        _final_target_for_audit = target_daily_count if role == "深度探索" else min(target_daily_count, 5)
                        _final_balance_audit = _daily_balance_audit(
                            final_data.get("route") or [], trip_days, _final_target_for_audit
                        )
                        final_data["daily_balance"] = {
                            **(final_data.get("daily_balance") or {}),
                            **_final_balance_audit,
                            "gate": "final_pre_emit",
                            "recomputed_after_closeout": True,
                        }
                        if not _final_balance_audit.get("satisfied"):
                            print(
                                "⚠️ [每日密度] 收口之后有几天不足目标节点数："
                                + str(_final_balance_audit.get("deficient_days")),
                                flush=True,
                            )
                    except Exception as _balance_recheck_exc:  # 重算失败不影响出方案
                        print(f"⚠️ [每日密度] 收口后重算失败: {_balance_recheck_exc}", flush=True)

                    # 👑 质量分也必须按**最终路线**重算（2026-10-05 实测抓到，与下面两步同一类错）
                    #    上面 `final_data["quality"]` 是治理快照阶段算的，之后的收口还会改路线：
                    #    移出跑不了的远景点、按"不想跑远"就地换近点、给缺坐标的住宿按名字补坐标、
                    #    查实时房价（`cost_estimate` 从估算变可核实）。
                    #    实测症状：运行日志打了「📍 [坐标补齐] 按名字补到 1 个（喀什古城步行街亚朵酒店）」，
                    #    但 payload 里 `quality.anchoring.coordinate_coverage` 还是补之前的 0.833，
                    #    前端因此仍然提示"这批吃住点没坐标" —— **做了但没算进去**。
                    #    重算是纯确定性、零网络、不改路线的，所以直接按最终路线再算一遍。
                    try:
                        if _quality_context_for_final is None:
                            pass  # 没有质量上下文（这条路没走到治理阶段）→ 保持原样，不猜
                        else:
                            from core.planning_governance import refresh_snapshot as _refresh_quality_snapshot

                            _final_snapshot = _refresh_quality_snapshot(
                                _quality_context_for_final,
                                final_data,
                                signals=_quality_signals_for_final,
                                increment=_quality_increment_for_final,
                                candidates_by_intent=_quality_pool_for_final,
                            )
                            _quality_before = (final_data.get("quality") or {}).get("score")
                            final_data["quality"] = _final_snapshot["quality"]
                            # 👑 把**这次评分用的输入**随方案下发（2026-10-05 实测抓到"两把尺子"）：
                            #    分数是拿 `pref_signals`（历史+成员的话）算的，而 `facts` 只记发起人
                            #    自己说的话 —— 离线尺子从 facts 重建信号就会比管线松。
                            #    实测：同一份喀什方案，管线 78.9 / 尺子 83.6（个性化那项 0.75 vs 1.0）。
                            #    方案自己记着输入，尺子才能复现同一个分数。
                            try:
                                from core.plan_quality import scoring_inputs as _scoring_inputs

                                final_data["quality"]["scoring_inputs"] = _scoring_inputs(
                                    _quality_context_for_final, _quality_signals_for_final
                                )
                            except Exception as _scoring_inputs_exc:  # 记不上不影响出方案
                                print(f"⚠️ [评分输入] 记录失败: {_scoring_inputs_exc}", flush=True)
                            final_data["budget_report"] = _final_snapshot["budget"]
                            # `horizon`（含长途衔接顾问）也是从路线推出来的，一起按最终路线刷新
                            final_data["horizon"] = _final_snapshot["horizon"]
                            _anchor_after = _final_snapshot["quality"].get("anchoring") or {}
                            print(
                                "📐 [质量重算] 按最终路线重算：score="
                                f"{_quality_before} → {_final_snapshot['quality'].get('score')}"
                                f"，anchoring={(_final_snapshot['quality'].get('dimensions') or {}).get('anchoring')}"
                                f"，坐标覆盖={_anchor_after.get('coordinate_coverage')}",
                                flush=True,
                            )
                    except Exception as _quality_recheck_exc:
                        print(f"⚠️ [质量重算] 收口后重算失败: {_quality_recheck_exc}", flush=True)

                    # 👑 **给最终路线的每个节点打"事实可信度"四态**（2026-10-08，P3）：
                    #    后端各处早就有了 source / observed_at / estimated，
                    #    但**没有一处把它们合成"这条信息可不可信"**，前端只能自己猜。
                    #    这里在收口之后统一过一遍 `node_contract.normalize_node`：
                    #    每条事实带上 trust（已核实/估算/信息较旧/尚未核实）、source_type、freshness，
                    #    且 confidence **由来源分级推导**（不采信上游或模型自报的值）。
                    #    纯确定性、零网络、不改路线内容，只加标注。
                    try:
                        from core.node_contract import normalize_node as _normalize_node

                        _graded_route = []
                        _trust_counts: Dict[str, int] = {}
                        for _node in final_data.get("route") or []:
                            if not isinstance(_node, Mapping):
                                _graded_route.append(_node)
                                continue
                            _graded = _normalize_node(_node)
                            _merged_node = dict(_node)
                            # 只覆盖/新增可信度相关字段，**不动**原有业务字段
                            for _field in (
                                "trust",
                                "trust_label",
                                "trust_action",
                                "trust_message",
                                "source_type",
                                "source_label",
                                "freshness",
                                "derived_confidence",
                            ):
                                _merged_node[_field] = _graded.get(_field)
                            _graded_route.append(_merged_node)
                            _key = str(_graded.get("trust") or "unknown")
                            _trust_counts[_key] = _trust_counts.get(_key, 0) + 1
                        if _graded_route:
                            final_data["route"] = _graded_route
                            final_data["trust_summary"] = {
                                "counts": _trust_counts,
                                "total": len(_graded_route),
                            }
                            print(
                                "🔎 [可信度分级] "
                                + "、".join(
                                    f"{_key} {_trust_counts[_key]}"
                                    for _key in ("verified", "estimated", "stale", "unknown")
                                    if _trust_counts.get(_key)
                                ),
                                flush=True,
                            )
                    except Exception as _trust_exc:  # 分级失败不影响出方案
                        print(f"⚠️ [可信度分级] 失败: {_trust_exc}", flush=True)

                    # 👑 **按最终路线重算"每天跑多少公里"**（2026-10-07 实测抓到陈旧快照）：
                    #    上面那次评估跑在**分天重排之前**，于是 payload 里写着"第 2 天 445km"，
                    #    而最终路线实际是"第 3 天 233km" —— 前端要拿它做"这天很赶"的提醒，
                    #    拿旧数字就是在骗用户；尺子的 `daily_travel` 又是按最终路线算的，
                    #    两个数不一样还会再演一次"两把尺子"。
                    #    这里按最终路线重算，并把上一次的旧数字留在 `before_*` 里便于核对。
                    try:
                        from core.travel_feasibility import evaluate_travel_feasibility as _travel_again

                        _travel_ctx = _quality_context_for_final if isinstance(_quality_context_for_final, dict) else {}
                        _travel_center = None
                        _facts_center = (((final_data.get("facts") or {}).get("destination") or {}).get("center") or {})
                        if isinstance(_facts_center, dict):
                            _travel_center = _facts_center.get("value") or None
                        _travel_prev = final_data.get("travel_feasibility") if isinstance(final_data.get("travel_feasibility"), dict) else {}
                        _travel_fresh = _evaluate_feasibility(
                            final_data.get("route") or [],
                            city_center=_travel_center,
                            distance_tolerance=str(
                                _travel_ctx.get("distance_tolerance")
                                or _travel_prev.get("distance_tolerance")
                                or "normal"
                            ),
                            days=max(1, int(_travel_ctx.get("days") or 0) or max(1, len({int(n.get("day") or 1) for n in (final_data.get("route") or [])}))),
                        )
                        _prev_max = max((float(d.get("travel_km") or 0) for d in (_travel_prev.get("days") or [])), default=0.0)
                        _fresh_max = max((float(d.get("travel_km") or 0) for d in (_travel_fresh.get("days") or [])), default=0.0)
                        _travel_fresh["recomputed_after_closeout"] = True
                        _travel_fresh["stale_before_recompute"] = {
                            "worst_day_km": round(_prev_max, 1),
                            "days": [
                                {"day": d.get("day"), "travel_km": d.get("travel_km")}
                                for d in (_travel_prev.get("days") or [])
                            ],
                        }
                        final_data["travel_feasibility"] = _travel_fresh
                        if abs(_fresh_max - _prev_max) >= 1.0:
                            print(
                                f"🚗 [每日里程重算] 按最终路线重算：最赶的一天 {_prev_max:g}km → {_fresh_max:g}km"
                                f"（旧数字是重排前的，不能再拿它提示用户）",
                                flush=True,
                            )
                    except Exception as _travel_recheck_exc:
                        print(f"⚠️ [每日里程重算] 失败（保留旧数字，但会标明）: {_travel_recheck_exc}", flush=True)

                    # 👑 高德闸门状态先登记（2026-10-05 实测抓到顺序错了）：这一轮被限流几次、
                    #    闸门退避到多少秒。限流会**静默让候选变少**（看起来像"这城市没什么景点"）——
                    #    用户与我们都需要知道"这次是不是因为被限流才薄"。
                    # ⚠️ 必须在下面「披露按最终路线重算」**之前**登记：原来它排在重算之后，
                    #    于是 `amap_gate` 进了 payload、`degradations` 里却没有这条 ——
                    #    面板上「遗留问题」把限流整条漏掉，**该说的没说**（实测喀什 turbo25 复现）。
                    #    （之后只剩"路网与推送"还会打高德，那一步只画线、不影响候选多少。）
                    try:
                        _gate_state = amap_gate_state()
                        _gate_limits_this_round = max(
                            0, int(_gate_state.get("limits") or 0) - int(_amap_limits_at_start)
                        )
                        if _gate_limits_this_round or _gate_state.get("throttled"):
                            final_data["amap_gate"] = {
                                **_gate_state,
                                "limits_this_round": _gate_limits_this_round,
                                "note": "高德请求被限流过，闸门已自动放慢间隔；候选可能因此变少（不是这座城市没有景点）。",
                            }
                            print(
                                f"🚦 [高德闸门] 本轮限流 {_gate_limits_this_round} 次，"
                                f"间隔已退避到 {_gate_state.get('interval_seconds')}s"
                                f"（基准 {_gate_state.get('base_interval_seconds')}s）",
                                flush=True,
                            )
                    except Exception:  # 闸门状态只是观测，取不到不影响出方案
                        pass

                    # 👑 供应商事实占位清洗（2026-11-03）：查不到的评分/开放时间/价格
                    #    在底座里被写成同一句"暂无供应商数据"，一路复制进最终 payload ——
                    #    信息卡会显示"暂无供应商数据 分"、时间轴每站重复一行同款文案，
                    #    用户读不到任何信息，还容易把占位当成事实。
                    #    口径：**未知就留空**，由 supplier_data_gaps 统一披露一次；真值原样保留。
                    #    必须排在下面「按最终路线重算披露/事实」之前：让 degradations 说的是"缺失"，
                    #    而不是把占位文案当成一个值再抄一遍。
                    try:
                        _fact_cleared = clear_placeholder_supplier_facts(final_data, copy=False)
                        _fact_gap = supplier_facts_disclosure(_fact_cleared)
                        if _fact_gap:
                            final_data["supplier_data_gaps"] = _fact_gap
                            print(
                                f"🧹 [供应商事实] {len(_fact_cleared)} 个节点的占位值已留空"
                                f"（{'/'.join(_fact_gap['fields'])}）→ 统一披露于 supplier_data_gaps",
                                flush=True,
                            )
                    except Exception as _fact_clean_exc:  # 清洗失败不影响出方案
                        print(f"⚠️ [供应商事实] 清洗失败（不影响出方案）: {_fact_clean_exc}", flush=True)

                    # 👑 披露与事实快照必须按**最终路线**重算（2026-10-05 实测抓到）
                    #    上面那两处登记（degradations / facts）跑在"最终均衡门禁 + 出稿前硬约束收口"
                    #    **之前**，而路线在之后还会被改动（移出跑不了的远景点、补/减住宿）。
                    #    实测后果：喀什的 payload 里 `daily_balance.satisfied=False`（收口后确实有几天偏少），
                    #    但"遗留问题"里一条都没有 —— **该说的没说**。
                    #    这两步都是"从 final_data 推导出结论"，重算一次是幂等的，所以直接按最终路线再算一遍。
                    try:
                        from core.degradation import collect_degradations as _collect_degradations_final

                        final_data["degradations"] = _collect_degradations_final(final_data)
                    except Exception as _degrade_recheck_exc:
                        print(f"⚠️ [遗留问题] 收口后重算失败: {_degrade_recheck_exc}", flush=True)
                    try:
                        from core.plan_facts import build_plan_facts as _build_plan_facts_final

                        final_data["facts"] = _build_plan_facts_final(
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
                            budget_total=float(total_calc_budget) if total_calc_budget else None,
                        )
                    except Exception as _facts_recheck_exc:
                        print(f"⚠️ [统一事实] 收口后重算失败: {_facts_recheck_exc}", flush=True)

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
                    _stage("配图与校验")
                    print(f"⏱️ [分段耗时] {_stage_summary()}", flush=True)
                    yield json.dumps({"type": "final_route", "payload": final_data}, ensure_ascii=False) + "\n"
                    _stage("事件流与推送")
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
                    # ⚠️ **半途而废的方案不进缓存**（2026-10-07 实测抓到）：联网取价被提前中止时
                    #    （今日配额用完 / 所有源连续失败），这份 payload 里的"查不到价格"是**临时条件**
                    #    造成的，不是事实。实测后果：第一次跑撞上配额用完 → 缓存了满屏"暂无供应商数据"，
                    #    之后同样的诉求秒回那份残缺方案（补了配额也照样命中）。
                    _price_audit_for_cache = final_data.get("price_audit") or {}
                    _early_stop = str(_price_audit_for_cache.get("early_stop_reason") or "").strip()
                    if is_refinement:
                        pass
                    elif _early_stop:
                        print(
                            f"⏭️ [语义缓存] 本次取价被提前中止（{_early_stop}）→ **不写缓存**："
                            "否则「查不到价格」会被固化一整个 TTL",
                            flush=True,
                        )
                    else:
                        asyncio.ensure_future(_semantic_cache.store(target_city, trip_days, profile_text, {
                            "final_data": final_data,
                            "budget_breakdown": budget_breakdown_payload,
                            "target_city": {
                                "name": target_city,
                                "lnglat": [float(x) for x in coord.split(",")] if coord and "," in coord else [],
                                "resolution": city_resolution,
                            },
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
                    # ⏱️ 收尾（路网拼装 / 事件流 / 语义缓存）也计时：实测这段在 final_route 之后
                    #    还要花几十秒，而服务端整个请求的耗时是把它算进去的。
                    _stage("路网与推送")
                    print(f"⏱️ [分段耗时·含收尾] {_stage_summary()}", flush=True)
            except Exception as e:
                # ⚠️ 这个 except 覆盖的范围**远不止**"路网拼装"（从最终门禁到下发都在里面），
                #    所以标题容易把人带偏 —— 实测（2026-10-05）配比对账里一个 ValueError
                #    被报成"真实路网拼装失败"，只给一行 `{e}` 完全无法定位。
                #    这里保留原标题（前端/日志检索依赖它），但把**真实位置**打出来。
                print(f"🔥 [真实路网拼装失败]: {type(e).__name__}: {e}", flush=True)
                traceback.print_exc()

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
            print("🔥 [流式推演崩溃 Traceback]:")
            traceback.print_exc()
            yield json.dumps({"token": f"\n[FINAL_JSON]\n{{\"status\": \"error\", \"negotiation_summary\": \"{_friendly_llm_error(e)}\", \"route\": []}}\n"}, ensure_ascii=False) + "\n"

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
            raw_reward: Any = payload.get("reward")
            reward = float(raw_reward)
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
