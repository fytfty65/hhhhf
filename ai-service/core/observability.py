# ai-service/core/observability.py
"""轻量可观测性（P3）：结构化 JSON 日志 + 请求指标 + X-Request-ID 追踪。

不引入第三方依赖，基于 stdlib + 内存指标注册表。
风险数据溯源元数据（source / risk_ts / is_estimated）已由 risk_service.RiskReport 承载，
本模块聚焦「请求级」结构化日志、延迟指标与跨服务追踪。
"""

import json
import time
import uuid
from collections import defaultdict
from threading import Lock
from typing import Dict, Any

SERVICE_NAME = "ai-service"


def _new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def log_event(event: str, **fields: Any) -> None:
    """输出一条结构化 JSON 日志（统一时间戳 + service 标识），供日志采集/审计。"""
    record: Dict[str, Any] = {
        "ts": round(time.time() * 1000),
        "service": SERVICE_NAME,
        "event": event,
        **fields,
    }
    try:
        print(json.dumps(record, ensure_ascii=False, default=str), flush=True)
    except Exception:
        pass  # 日志失败不得影响主流程


class MetricsRegistry:
    """进程内指标统计（线程安全），供 /metrics 端点与日志消费。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._start = time.time()
        self.requests_total = 0
        self.requests_by_path: Dict[str, int] = defaultdict(int)
        self.requests_by_status: Dict[str, int] = defaultdict(int)
        self.latency_by_path: Dict[str, float] = defaultdict(float)
        self.latency_count_by_path: Dict[str, int] = defaultdict(int)

    def observe(self, method: str, path: str, status: int, latency_ms: float) -> None:
        key = f"{method} {path}"
        with self._lock:
            self.requests_total += 1
            self.requests_by_path[key] += 1
            self.requests_by_status[str(status)] += 1
            self.latency_by_path[key] += latency_ms
            self.latency_count_by_path[key] += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            paths = {}
            for key, cnt in self.requests_by_path.items():
                paths[key] = {
                    "requests": cnt,
                    "avg_latency_ms": round(
                        self.latency_by_path[key] / max(self.latency_count_by_path[key], 1), 2
                    ),
                }
            return {
                "service": SERVICE_NAME,
                "uptime_seconds": round(time.time() - self._start, 1),
                "requests_total": self.requests_total,
                "requests_by_status": dict(self.requests_by_status),
                "requests_by_path": paths,
            }


METRICS = MetricsRegistry()


class ObservabilityMiddleware:
    """纯 ASGI 中间件：X-Request-ID 追踪 + 结构化请求日志 + 延迟指标。

    采用纯 ASGI 实现（而非 BaseHTTPMiddleware），以兼容 /agent/negotiate 的流式响应。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        method = scope.get("method", "")
        path = scope.get("path", "")

        # 透传上游 X-Request-ID，缺失则生成
        request_id = ""
        for name, value in scope.get("headers") or []:
            if name == b"x-request-id":
                request_id = value.decode("latin-1")
                break
        if not request_id:
            request_id = _new_request_id()

        status_holder = {"code": 500}

        async def send_wrapper(message: Dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                status_holder["code"] = message.get("status", 500)
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # 记录服务端异常后继续抛出
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            log_event(
                "http_request",
                request_id=request_id,
                method=method,
                path=path,
                status=500,
                latency_ms=latency_ms,
                error=str(exc),
            )
            raise
        finally:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            status = status_holder["code"]
            METRICS.observe(method, path, status, latency_ms)
            log_event(
                "http_request",
                request_id=request_id,
                method=method,
                path=path,
                status=status,
                latency_ms=latency_ms,
            )