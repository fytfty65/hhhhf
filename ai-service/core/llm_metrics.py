"""LLM 调用指标：次数、失败率、延迟分位、token 与成本估算。

为什么单独记账（2026-10-28）：以前只有请求级指标（`/metrics` 的 HTTP 延迟），
看不出"这一次慢是模型慢，还是上游数据源慢"，也没有任何成本口径 —— 换成更贵的
模型、或长行程分段多跑几次，账单变化在系统里完全不可见。

成本口径：**不猜单价**。只有部署方配置了 `LLM_PRICE_INPUT_PER_1K` /
`LLM_PRICE_OUTPUT_PER_1K`（每 1K token 的单价）才会计算，否则
`cost_configured=false`、`cost` 为 None —— 宁可显示"未配置单价"，
也不编一个看起来像账单的数字。流式调用拿不到 usage 时 token 数同样留空并计入
`usage_missing_calls`，不按字符数硬凑。
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional


def _price_env(name: str) -> Optional[float]:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


class LLMCallMetrics:
    """线程安全的进程内 LLM 调用记账。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latencies: Deque[float] = deque(maxlen=1000)
        self._first_token: Deque[float] = deque(maxlen=1000)
        self.calls = 0
        self.failures = 0
        self.streams = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.usage_missing_calls = 0
        self.by_model: Dict[str, Dict[str, Any]] = {}
        self.by_label: Dict[str, Dict[str, Any]] = {}

    def record(
        self,
        *,
        model: str,
        label: str,
        latency_ms: float,
        ok: bool,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        first_token_ms: Optional[float] = None,
        streamed: bool = False,
        error: str = "",
    ) -> None:
        bucket_key_model = model or "unknown"
        bucket_key_label = label or "unknown"
        with self._lock:
            self.calls += 1
            if not ok:
                self.failures += 1
            if streamed:
                self.streams += 1
            self._latencies.append(max(0.0, float(latency_ms)))
            if first_token_ms is not None:
                self._first_token.append(max(0.0, float(first_token_ms)))
            # 只有 prompt 与 completion 都拿得到才算"有 usage"；流式只数得到输出 chunk，
            # prompt 侧永远缺失，所以流式调用一律计入 usage_missing_calls。
            has_usage = prompt_tokens is not None and completion_tokens is not None
            if has_usage:
                self.prompt_tokens += int(prompt_tokens or 0)
                self.completion_tokens += int(completion_tokens or 0)
            else:
                self.usage_missing_calls += 1
                self.completion_tokens += int(completion_tokens or 0)
            for table, key in ((self.by_model, bucket_key_model), (self.by_label, bucket_key_label)):
                row = table.setdefault(
                    key, {"calls": 0, "failures": 0, "latency_ms_sum": 0.0, "last_error": ""}
                )
                row["calls"] += 1
                row["failures"] += 0 if ok else 1
                row["latency_ms_sum"] += max(0.0, float(latency_ms))
                if error:
                    row["last_error"] = error[:200]

    @staticmethod
    def _percentile(samples: list, ratio: float) -> Optional[float]:
        if not samples:
            return None
        ordered = sorted(samples)
        # 最近秩法（nearest-rank）：p95 不会因为四舍五入落到 90 分位
        index = max(0, min(len(ordered) - 1, math.ceil(ratio * len(ordered)) - 1))
        return round(ordered[index], 2)

    def snapshot(self) -> Dict[str, Any]:
        price_in = _price_env("LLM_PRICE_INPUT_PER_1K")
        price_out = _price_env("LLM_PRICE_OUTPUT_PER_1K")
        cost_configured = price_in is not None and price_out is not None
        with self._lock:
            latencies = list(self._latencies)
            first_tokens = list(self._first_token)
            cost = None
            if price_in is not None and price_out is not None:
                cost = round(
                    (self.prompt_tokens / 1000.0) * price_in
                    + (self.completion_tokens / 1000.0) * price_out,
                    4,
                )
            return {
                "calls": self.calls,
                "failures": self.failures,
                "failure_rate": round(self.failures / self.calls, 4) if self.calls else None,
                "streams": self.streams,
                "latency_ms": {
                    "avg": round(sum(latencies) / len(latencies), 2) if latencies else None,
                    "p50": self._percentile(latencies, 0.50),
                    "p95": self._percentile(latencies, 0.95),
                },
                "first_token_ms": {
                    "avg": round(sum(first_tokens) / len(first_tokens), 2) if first_tokens else None,
                    "p95": self._percentile(first_tokens, 0.95),
                },
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "usage_missing_calls": self.usage_missing_calls,
                "cost_configured": cost_configured,
                "cost": cost,
                "cost_currency": str(os.getenv("LLM_PRICE_CURRENCY") or "CNY"),
                "by_model": {
                    key: {**value, "latency_ms_avg": round(value["latency_ms_sum"] / max(value["calls"], 1), 2)}
                    for key, value in sorted(self.by_model.items())
                },
                "by_label": {
                    key: {**value, "latency_ms_avg": round(value["latency_ms_sum"] / max(value["calls"], 1), 2)}
                    for key, value in sorted(self.by_label.items())
                },
            }


LLM_METRICS = LLMCallMetrics()


def record_llm_call(**kwargs: Any) -> None:
    try:
        LLM_METRICS.record(**kwargs)
    except Exception:  # 记账失败绝不影响主流程
        pass


def llm_metrics() -> Dict[str, Any]:
    return LLM_METRICS.snapshot()


def monotonic_ms() -> float:
    return time.perf_counter() * 1000.0
