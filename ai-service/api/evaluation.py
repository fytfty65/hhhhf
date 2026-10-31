"""Read-only evaluation task observability surface.

The scheduler writes a deliberately small JSONL record. This endpoint exposes
only that allowlisted summary to the authenticated Gateway; raw events and
credentials never leave the evaluator process.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter, Query

router = APIRouter()

_AUDIT_KEYS = {
    "recorded_at", "status", "promotion_ready", "model_version",
    "sample_count", "validation_samples", "callback", "reason",
    "policy_comparison_status", "policy_baseline_samples",
    "policy_behavior_samples", "policy_reward_lift",
    "policy_adoption_signal_lift", "policy_comparison_ready",
    "policy_segment_coverage",
    "fairness_segment_status", "fairness_segment_max_stddev",
    "fairness_segment_threshold_exceeded",
    "provider_quality_status", "provider_quality_providers",
    "provider_quality_ready_providers", "provider_quality_insufficient_providers",
    "provider_quality_blocked_providers",
}
DEFAULT_STALE_AFTER_SECONDS = 2 * 60 * 60
DEFAULT_FAILURE_ALERT_COUNT = 3


def _audit_path() -> Path:
    return Path(os.getenv("MODEL_EVALUATION_AUDIT_FILE", "./work/model-evaluation.jsonl"))


def _health_config() -> tuple[int, int]:
    def integer(name: str, fallback: int, minimum: int, maximum: int) -> int:
        try:
            value = int(os.getenv(name, str(fallback)))
        except (TypeError, ValueError):
            value = fallback
        return max(minimum, min(value, maximum))
    return (
        integer("MODEL_EVALUATION_STALE_AFTER_SECONDS", DEFAULT_STALE_AFTER_SECONDS, 60, 7 * 24 * 60 * 60),
        integer("MODEL_EVALUATION_FAILURE_ALERT_COUNT", DEFAULT_FAILURE_ALERT_COUNT, 1, 20),
    )


def evaluation_health(entries: list[dict[str, Any]], now: float | None = None) -> dict[str, Any]:
    """Classify scheduler health without treating insufficient evidence as a crash."""
    stale_after, failure_limit = _health_config()
    if not entries:
        return {
            "status": "insufficient_data",
            "reason": "尚无评估任务审计记录",
            "consecutive_failures": 0,
            "age_seconds": None,
            "stale_after_seconds": stale_after,
            "failure_alert_count": failure_limit,
        }
    current = time.time() if now is None else float(now)
    latest = entries[-1]
    raw_recorded_at: Any = latest.get("recorded_at")
    try:
        recorded_at = float(raw_recorded_at)
    except (TypeError, ValueError):
        recorded_at = 0
    age = max(0, int(current - recorded_at)) if recorded_at > 0 else None
    failures = 0
    for entry in reversed(entries):
        status = str(entry.get("status") or "")
        raw_callback = entry.get("callback")
        callback: Mapping[str, Any] = raw_callback if isinstance(raw_callback, dict) else {}
        callback_failed = callback.get("accepted") is False and callback.get("skipped") is not True
        execution_failed = status in {"error", "blocked", "unavailable"}
        if execution_failed or callback_failed:
            failures += 1
        else:
            break
    reasons: list[str] = []
    if age is None or age > stale_after:
        reasons.append("评估任务超过规定时间未更新")
    if failures >= failure_limit:
        reasons.append(f"连续失败或回写失败达到 {failure_limit} 次")
    if not reasons and str(latest.get("status")) == "insufficient_evidence":
        reasons.append("任务已执行，但评估证据不足，模型晋级仍被阻断")
    if reasons and (age is None or age > stale_after or failures >= failure_limit):
        status = "critical"
    elif reasons:
        status = "warning"
    else:
        status = "healthy"
    return {
        "status": status,
        "reason": "；".join(reasons) if reasons else "评估任务按计划运行",
        "consecutive_failures": failures,
        "age_seconds": age,
        "stale_after_seconds": stale_after,
        "failure_alert_count": failure_limit,
    }


def read_evaluation_audit(limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    path = _audit_path()
    if not path.exists() or not path.is_file():
        return {"status": "insufficient_data", "entries": [], "count": 0, "source": "audit_file"}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return {"status": "unavailable", "entries": [], "count": 0, "source": "audit_file"}
    entries: list[dict[str, Any]] = []
    for line in lines:
        try:
            decoded = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(decoded, dict):
            continue
        entry = {key: decoded[key] for key in _AUDIT_KEYS if key in decoded}
        callback = entry.get("callback")
        if isinstance(callback, dict):
            entry["callback"] = {
                key: callback[key] for key in ("accepted", "status_code", "skipped", "reason") if key in callback
            }
        else:
            entry.pop("callback", None)
        coverage = entry.get("policy_segment_coverage")
        if isinstance(coverage, dict):
            entry["policy_segment_coverage"] = {
                str(dimension): {
                    key: summary[key]
                    for key in ("available_segments", "segment_count", "status")
                    if isinstance(summary, dict) and key in summary
                }
                for dimension in ("traveler_group", "destination", "season", "budget_bucket")
                for summary in [coverage.get(dimension)]
                if isinstance(summary, dict)
            }
        elif coverage is not None:
            entry.pop("policy_segment_coverage", None)
        # Provider quality fields are already aggregate counters written by the
        # scheduler.  Normalize them here so malformed audit lines cannot
        # become unbounded or leak arbitrary nested data through the API.
        if "provider_quality_status" in entry:
            entry["provider_quality_status"] = str(entry["provider_quality_status"] or "not_required")[:32]
        for key in (
            "provider_quality_providers",
            "provider_quality_ready_providers",
            "provider_quality_insufficient_providers",
            "provider_quality_blocked_providers",
        ):
            if key in entry:
                try:
                    entry[key] = max(0, min(int(entry[key]), 1_000_000_000))
                except (TypeError, ValueError):
                    entry.pop(key, None)
        entries.append(entry)
    latest = entries[-1] if entries else None
    health = evaluation_health(entries)
    return {
        "status": "available" if entries else "insufficient_data",
        "health": health,
        "entries": entries,
        "latest": latest,
        "count": len(entries),
        "source": "audit_file",
    }


@router.get("/evaluation/audit")
async def evaluation_audit(limit: int = Query(default=20, ge=1, le=100)):
    return read_evaluation_audit(limit)
