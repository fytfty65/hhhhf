"""异步 TTL 缓存（带单飞），供"外部数据源调用贵、但数据变化没那么快"的场景使用。

为什么单独写一个（而不是继续手写 dict + Lock）：
- **异常绝不能缓存**：上游偶发失败如果被缓存住，接下来整个 TTL 窗口都在返回失败，
  用户看到的是"服务坏了"，而实际上一次重试就好了；
- **单飞（single-flight）**：同一 key 并发请求只打一次数据源。实测里 ai-service 的
  风险快照被两个组件同时轮询（推送中心 + 雷达），没有单飞就等于双倍打上游；
- **可测**：`now` 可注入，TTL 行为不依赖真实时钟。

实测背景（2026-10-03）：`/api/v1/risk/realtime` 冷调用 6,882ms、命中缓存 4ms，
而前端轮询间隔（~50-60s）大于原快照 TTL（45s）—— 于是几乎每次轮询都是全量重打
weather + traffic + safety 三个上游。分层缓存后，只有真正"该更新"的那一层才会重打。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple


class AsyncTtlCache:
    """按 key 缓存 async loader 的结果。

    - TTL 内直接复用；
    - 同一 key 的并发调用**只执行一次** loader（其余等待同一结果）；
    - loader 抛异常时**不写入缓存**并把异常原样抛出（调用方自己决定降级）。
    """

    def __init__(self, ttl_seconds: float, *, name: str = "", max_entries: int = 500) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds_must_be_positive")
        self.ttl_seconds = float(ttl_seconds)
        self.name = name or "ttl_cache"
        self.max_entries = max_entries
        self._entries: Dict[str, Tuple[float, Any]] = {}
        self._inflight: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    def _now(self) -> float:
        return time.monotonic()

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[Any]],
        *,
        now: Optional[float] = None,
    ) -> Any:
        moment = self._now() if now is None else now

        async with self._lock:
            entry = self._entries.get(key)
            if entry and moment - entry[0] < self.ttl_seconds:
                self.hits += 1
                return entry[1]
            pending = self._inflight.get(key)
            if pending is None:
                pending = asyncio.get_running_loop().create_future()
                self._inflight[key] = pending
                leader = True
            else:
                leader = False
        # 注意：等结果时**不要**持锁（否则 loader 里再碰缓存就死锁）
        if not leader:
            return await asyncio.shield(pending)

        try:
            value = await loader()
        except BaseException as exc:  # noqa: BLE001 —— 失败必须原样抛出且不缓存
            async with self._lock:
                self._inflight.pop(key, None)
            if not pending.done():
                pending.set_exception(exc)
            # 没有跟随者时，异常会被 asyncio 记为 "never retrieved"，这里吞掉这一份副本
            pending.exception()
            raise
        async with self._lock:
            self._entries[key] = (self._now(), value)
            self._inflight.pop(key, None)
            self._evict_locked()
        self.misses += 1
        if not pending.done():
            pending.set_result(value)
        return value

    def _evict_locked(self) -> None:
        if len(self._entries) <= self.max_entries:
            return
        cutoff = self._now() - self.ttl_seconds
        for stale_key in [key for key, value in self._entries.items() if value[0] < cutoff]:
            self._entries.pop(stale_key, None)
        while len(self._entries) > self.max_entries:
            self._entries.pop(next(iter(self._entries)))

    def stats(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ttl_seconds": self.ttl_seconds,
            "entries": len(self._entries),
            "hits": self.hits,
            "misses": self.misses,
        }

    def invalidate(self, key: Optional[str] = None) -> None:
        if key is None:
            self._entries.clear()
        else:
            self._entries.pop(key, None)
