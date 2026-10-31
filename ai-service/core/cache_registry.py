"""统一的有界 TTL 缓存 + 缓存指标登记。

为什么需要它（2026-10-28 复盘）：服务里有四处各自手写的 dict 缓存（外部 API、POI
查询、交通方案、高德照片），口径各不相同：

- 有的完全没有上限（`_api_cache` / `_AMAP_PHOTO_CACHE`），长跑进程内存只增不减；
- 有的超限就整体 `clear()`（交通方案 / POI 查询），把还在被反复命中的热点也一起丢掉，
  命中率会从高位直接掉到 0，等于把缓存自己做没了；
- 语义缓存的内存条目只把 TTL 写给了 Redis，进程内存那份**从不判断 TTL**，
  同一个进程跑过一天后仍会命中已经过期的方案。

这里把"容量上限 + TTL + LRU 淘汰 + 命中率"收敛成一份实现，并把每个缓存的
命中/未命中/过期/淘汰计数登记到 /metrics，便于回答"缓存到底有没有用"。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple


class BoundedTTLCache:
    """按 key 缓存，带容量上限、TTL 与 LRU 淘汰；线程安全。

    - `get()` 过期即删并计入 `expired`；未命中计入 `misses`；
    - `set()` 超出 `max_entries` 时淘汰最久未使用的一条（不是整体 clear）；
    - `stats()` 给出命中率，登记后由 /metrics 暴露。
    """

    def __init__(
        self,
        name: str,
        *,
        max_entries: int = 256,
        ttl_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries_must_be_positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds_must_be_positive")
        self.name = name
        self.max_entries = int(max_entries)
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        # (写入时刻, 值)；dict 的插入顺序即 LRU 顺序
        self._entries: Dict[str, Tuple[float, Any]] = {}
        # 每条自己的 TTL：允许同一缓存里不同数据源用不同寿命（天气 1h、照片 7d）。
        # 按"写入时刻 + TTL"判断，而不是记绝对到期时刻 —— 直接改写 entries
        # 里写入时刻的存量代码/测试因此仍然生效（改老时间戳就等于让它过期）。
        self._ttls: Dict[str, float] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.expired = 0
        self.evictions = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def entries(self) -> Dict[str, Tuple[float, Any]]:
        """底层 dict（写入时刻, 值）。保留给需要直接读写的存量代码/测试。"""
        return self._entries

    def get(self, key: str) -> Optional[Any]:
        value, _ = self.get_with_meta(key)
        return value

    def get_with_meta(self, key: str) -> Tuple[Optional[Any], Optional[float]]:
        """返回 (值, 缓存年龄秒)。未命中的年龄为 None。"""
        moment = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None, None
            stored_at, value = entry
            if moment - stored_at >= self._ttls.get(key, self.ttl_seconds):
                self._entries.pop(key, None)
                self._ttls.pop(key, None)
                self.expired += 1
                self.misses += 1
                return None, None
            # 命中就挪到末尾，淘汰时先丢最久未用的
            self._entries.pop(key, None)
            self._entries[key] = entry
            self.hits += 1
            return value, max(0.0, moment - stored_at)

    def set(self, key: str, value: Any, *, ttl: Optional[float] = None) -> None:
        moment = self._clock()
        effective_ttl = self.ttl_seconds if ttl is None else float(ttl)
        with self._lock:
            self._entries.pop(key, None)
            self._entries[key] = (moment, value)
            self._ttls[key] = max(0.0, effective_ttl)
            while len(self._entries) > self.max_entries:
                oldest = next(iter(self._entries))
                self._entries.pop(oldest, None)
                self._ttls.pop(oldest, None)
                self.evictions += 1

    def invalidate(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._entries.clear()
                self._ttls.clear()
            else:
                self._entries.pop(key, None)
                self._ttls.pop(key, None)

    def clear(self) -> None:
        """清空全部条目（与 dict.clear 同名，兼容存量调用）。"""
        self.invalidate(None)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "name": self.name,
                "entries": len(self._entries),
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl_seconds,
                "hits": self.hits,
                "misses": self.misses,
                "expired": self.expired,
                "evictions": self.evictions,
                "hit_rate": round(self.hits / total, 4) if total else None,
            }


_REGISTRY: Dict[str, Any] = {}


def register_cache(cache: Any) -> Any:
    """登记一个缓存（需要 name + stats()），返回入参便于链式使用。"""
    name = str(getattr(cache, "name", "") or "")
    if name:
        _REGISTRY[name] = cache
    return cache


def cache_metrics() -> Dict[str, Any]:
    """所有已登记缓存的快照，供 /metrics 的 JSON 与 Prometheus 使用。"""
    snapshot: Dict[str, Any] = {}
    for name, cache in sorted(_REGISTRY.items()):
        try:
            stats = cache.stats()
        except Exception:  # 指标失败不得影响 /metrics 本身
            continue
        snapshot[name] = stats
    return snapshot
