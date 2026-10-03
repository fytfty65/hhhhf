"""Run one scheduled model evaluation and optionally write it to Gateway.

This job is safe to invoke from Task Scheduler, cron, or a container timer.
It has no model-promotion side effect: it only writes a validated aggregate
report to the internal evaluation endpoint.  A lock prevents overlapping jobs
from racing to update the same deployment.

Example::

    python -m tools.run_model_evaluation --db ../gateway/data/omniroute.db \
      --model-id 3 --model-version planner-v4

Required environment variables for a real callback:

* ``AI_SERVICE_INTERNAL_TOKEN``
* ``MODEL_EVALUATION_GATEWAY_URL`` (or ``--gateway-url``)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .build_decision_dataset import load_sqlite
from .evaluate_decision_dataset import evaluate


DEFAULT_LOCK_AGE_SECONDS = 2 * 60 * 60
MAX_RESPONSE_BYTES = 64 * 1024


def _bounded_fairness_dimensions(value: Any) -> dict[str, dict[str, Any]]:
    """Keep only dimension-level aggregates in the scheduler audit."""
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for dimension in ("traveler_group", "destination", "season", "budget_bucket"):
        summary = value.get(dimension)
        if not isinstance(summary, Mapping):
            continue
        max_stddev = summary.get("max_stddev")
        if not isinstance(max_stddev, (int, float)) or max_stddev != max_stddev:
            max_stddev = None
        result[dimension] = {
            "status": str(summary.get("status") or "insufficient_data"),
            "available_segments": max(0, int(summary.get("available_segments") or 0)),
            "segment_count": max(0, int(summary.get("segment_count") or 0)),
            "max_stddev": max_stddev,
            "threshold_exceeded": max(0, int(summary.get("threshold_exceeded") or 0)),
        }
    return result


def append_audit(path: str, result: Mapping[str, Any]) -> None:
    """Append a small, credential-free JSONL execution record."""
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "recorded_at": int(time.time()),
        "status": result.get("status"),
        "promotion_ready": bool(result.get("promotion_ready", False)),
        "model_version": result.get("model_version"),
        "sample_count": result.get("sample_count", 0),
        "validation_samples": result.get("validation_samples", 0),
        "callback": result.get("callback"),
        "reason": result.get("reason"),
        "policy_comparison_status": result.get("policy_comparison_status"),
        "policy_baseline_samples": result.get("policy_baseline_samples"),
        "policy_behavior_samples": result.get("policy_behavior_samples"),
        "policy_reward_lift": result.get("policy_reward_lift"),
        "policy_adoption_signal_lift": result.get("policy_adoption_signal_lift"),
        "policy_comparison_ready": result.get("policy_comparison_ready"),
        "policy_segment_coverage": result.get("policy_segment_coverage"),
        "fairness_segment_status": result.get("fairness_segment_status"),
        "fairness_segment_max_stddev": result.get("fairness_segment_max_stddev"),
        "fairness_segment_threshold_exceeded": result.get("fairness_segment_threshold_exceeded"),
        "fairness_segment_dimensions": _bounded_fairness_dimensions(result.get("fairness_segment_dimensions")),
        # Provider quality is a release-gate aggregate.  Keep it in the local
        # scheduler audit so an operator can correlate a blocked evaluation
        # with the gate that caused it, without persisting provider payloads.
        "provider_quality_status": result.get("provider_quality_status"),
        "provider_quality_providers": max(0, int(result.get("provider_quality_providers") or 0)),
        "provider_quality_ready_providers": max(0, int(result.get("provider_quality_ready_providers") or 0)),
        "provider_quality_insufficient_providers": max(0, int(result.get("provider_quality_insufficient_providers") or 0)),
        "provider_quality_blocked_providers": max(0, int(result.get("provider_quality_blocked_providers") or 0)),
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class EvaluationLock:
    """Small cross-platform lock based on exclusive file creation."""

    def __init__(self, path: str, max_age_seconds: int = DEFAULT_LOCK_AGE_SECONDS):
        self.path = Path(path)
        self.max_age_seconds = max_age_seconds
        self.owned = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                descriptor = self.path.open("x", encoding="utf-8")
                descriptor.write(json.dumps({"pid": os.getpid(), "started_at": int(time.time())}))
                descriptor.close()
                self.owned = True
                return True
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    age = 0
                if attempt == 0 and age > self.max_age_seconds:
                    try:
                        self.path.unlink()
                    except OSError:
                        return False
                    continue
                return False
        return False

    def release(self) -> None:
        if not self.owned:
            return
        try:
            self.path.unlink()
        except OSError:
            pass
        self.owned = False

    def __enter__(self) -> "EvaluationLock":
        if not self.acquire():
            raise RuntimeError("evaluation_lock_busy")
        return self

    def __exit__(self, *_: Any) -> None:
        self.release()


def gateway_payload(report: Mapping[str, Any], model_version: str) -> dict[str, Any]:
    """Flatten only the aggregate fields accepted by Gateway."""
    payload: dict[str, Any] = {
        "evaluation_version": report.get("evaluation_version", "decision-evaluation-v1"),
        "status": report.get("status", "insufficient_evidence"),
        "promotion_ready": bool(report.get("promotion_ready", False)),
        "model_version": model_version,
        "reasons": list(report.get("reasons") or []),
    }
    for key in (
        "evaluation_samples", "validation_samples", "offline_score", "drift_score",
        "counterfactual_samples", "fairness_min_utility", "fairness_utility_variance",
    ):
        value = report.get(key)
        if value is not None:
            payload[key] = value
    # Keep the policy comparison as a small, numeric aggregate contract.  The
    # nested report contains no raw candidates, users, URLs or provider data,
    # but those details must never be sent to the gateway anyway.
    comparison = report.get("policy_comparison")
    if isinstance(comparison, Mapping):
        baseline = comparison.get("baseline") if isinstance(comparison.get("baseline"), Mapping) else {}
        behavior = comparison.get("behavior_aware") if isinstance(comparison.get("behavior_aware"), Mapping) else {}
        status = str(comparison.get("status") or "insufficient_data")
        payload.update({
            "policy_comparison_required": True,
            "policy_comparison_status": status,
            "policy_baseline_samples": int(baseline.get("samples") or 0),
            "policy_behavior_samples": int(behavior.get("samples") or 0),
            "policy_comparison_ready": status == "available",
        })
        for source_key, target_key in (
            ("reward_lift", "policy_reward_lift"),
            ("adoption_signal_lift", "policy_adoption_signal_lift"),
        ):
            value = comparison.get(source_key)
            if isinstance(value, (int, float)) and value == value:
                payload[target_key] = value
    # Segment coverage is diagnostic-only. It is intentionally kept out of
    # the gateway numeric model metrics, but remains available in the local
    # scheduler audit for fairness and rollout review.
    coverage = report.get("policy_segment_coverage")
    if isinstance(coverage, Mapping):
        allowed_dimensions = ("traveler_group", "destination", "season", "budget_bucket")
        payload["policy_segment_coverage"] = {
            str(dimension): {
                "available_segments": int(summary.get("available_segments") or 0),
                "segment_count": int(summary.get("segment_count") or 0),
                "status": str(summary.get("status") or "insufficient_data"),
            }
            for dimension in allowed_dimensions
            for summary in [coverage.get(dimension)]
            if isinstance(summary, Mapping)
        }
    fairness_segments = report.get("fairness_segment_summary")
    if isinstance(fairness_segments, Mapping):
        payload["fairness_segment_required"] = True
        payload["fairness_segment_status"] = str(fairness_segments.get("status") or "insufficient_data")
        max_stddev = fairness_segments.get("max_stddev")
        if isinstance(max_stddev, (int, float)) and max_stddev == max_stddev:
            payload["fairness_segment_max_stddev"] = max_stddev
        payload["fairness_segment_threshold_exceeded"] = int(fairness_segments.get("threshold_exceeded") or 0)
    # Provider quality is an input-boundary gate, not a decision sample. Keep
    # only bounded aggregates so the callback cannot carry supplier payloads,
    # URLs, identities or credentials.
    provider_gate = report.get("provider_quality_gate")
    if isinstance(provider_gate, Mapping):
        status = str(provider_gate.get("status") or "not_required")
        payload.update({
            "provider_quality_required": status != "not_required",
            "provider_quality_status": status,
            "provider_quality_providers": max(0, int(provider_gate.get("providers") or 0)),
            "provider_quality_ready_providers": max(0, int(provider_gate.get("ready_providers") or 0)),
            "provider_quality_insufficient_providers": max(0, int(provider_gate.get("insufficient_providers") or 0)),
            "provider_quality_blocked_providers": max(0, int(provider_gate.get("blocked_providers") or 0)),
        })
    return payload


def post_report(url: str, token: str, payload: Mapping[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Omni-Internal-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES)
            accepted = 200 <= response.status < 300
            # Gateway's stable callback contract is an envelope with
            # data.accepted=true. A 2xx proxy page or an empty response must
            # not be reported as a successful write.
            try:
                decoded = json.loads(raw.decode("utf-8")) if raw else {}
                data = decoded.get("data") if isinstance(decoded, Mapping) else None
                if isinstance(data, Mapping) and "accepted" in data:
                    accepted = accepted and data.get("accepted") is True
                else:
                    accepted = False
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                accepted = False
            return {"status_code": int(response.status), "accepted": accepted}
    except urllib.error.HTTPError as exc:
        exc.read(MAX_RESPONSE_BYTES)
        return {"status_code": int(exc.code), "accepted": False}


def run_once(
    db_path: str,
    model_version: str,
    model_id: str,
    gateway_url: str = "",
    token: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    events = load_sqlite(db_path)
    report = evaluate(events)
    payload = gateway_payload(report, model_version)
    result: dict[str, Any] = {
        "status": report.get("status"),
        "promotion_ready": report.get("promotion_ready", False),
        "model_version": model_version,
        "sample_count": report.get("evaluation_samples", 0),
        "validation_samples": report.get("validation_samples", 0),
        "callback": {"skipped": True, "reason": "dry_run"} if dry_run else None,
    }
    comparison = report.get("policy_comparison")
    if isinstance(comparison, Mapping):
        baseline = comparison.get("baseline") if isinstance(comparison.get("baseline"), Mapping) else {}
        behavior = comparison.get("behavior_aware") if isinstance(comparison.get("behavior_aware"), Mapping) else {}
        result.update({
            "policy_comparison_status": comparison.get("status", "insufficient_data"),
            "policy_baseline_samples": int(baseline.get("samples") or 0),
            "policy_behavior_samples": int(behavior.get("samples") or 0),
            "policy_comparison_ready": comparison.get("status") == "available",
            "policy_reward_lift": comparison.get("reward_lift"),
            "policy_adoption_signal_lift": comparison.get("adoption_signal_lift"),
            "policy_segment_coverage": report.get("policy_segment_coverage"),
            "fairness_segment_status": report.get("fairness_segment_status"),
            "fairness_segment_max_stddev": report.get("fairness_segment_max_stddev"),
            "fairness_segment_threshold_exceeded": report.get("fairness_segment_threshold_exceeded"),
        })
    fairness_segments = report.get("fairness_segment_summary")
    if isinstance(fairness_segments, Mapping):
        dimensions: dict[str, Any] = {}
        for dimension in ("traveler_group", "destination", "season", "budget_bucket"):
            summary = fairness_segments.get("dimensions", {}).get(dimension) if isinstance(fairness_segments.get("dimensions"), Mapping) else None
            if isinstance(summary, Mapping):
                dimensions[dimension] = {
                    "status": str(summary.get("status") or "insufficient_data"),
                    "available_segments": int(summary.get("available_segments") or 0),
                    "segment_count": int(summary.get("segment_count") or 0),
                    "max_stddev": summary.get("max_stddev"),
                    "threshold_exceeded": int(summary.get("threshold_exceeded") or 0),
                }
        result.update({
            "fairness_segment_status": fairness_segments.get("status", "insufficient_data"),
            "fairness_segment_max_stddev": fairness_segments.get("max_stddev"),
            "fairness_segment_threshold_exceeded": int(fairness_segments.get("threshold_exceeded") or 0),
            "fairness_segment_dimensions": dimensions,
        })
    provider_gate = report.get("provider_quality_gate")
    if isinstance(provider_gate, Mapping):
        result.update({
            "provider_quality_status": str(provider_gate.get("status") or "not_required"),
            "provider_quality_providers": int(provider_gate.get("providers") or 0),
            "provider_quality_ready_providers": int(provider_gate.get("ready_providers") or 0),
            "provider_quality_insufficient_providers": int(provider_gate.get("insufficient_providers") or 0),
            "provider_quality_blocked_providers": int(provider_gate.get("blocked_providers") or 0),
        })
    if dry_run:
        return result
    if not token:
        raise RuntimeError("AI_SERVICE_INTERNAL_TOKEN is required")
    if not gateway_url:
        raise RuntimeError("MODEL_EVALUATION_GATEWAY_URL is required")
    callback_url = gateway_url.rstrip("/") + "/" + str(model_id).strip() + "/evaluation"
    result["callback"] = post_report(callback_url, token, payload)
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run one planning model evaluation")
    parser.add_argument("--db", required=True, help="gateway SQLite database")
    parser.add_argument("--model-id", default=os.getenv("MODEL_EVALUATION_MODEL_ID", ""), help="Gateway model deployment id (or MODEL_EVALUATION_MODEL_ID)")
    parser.add_argument("--model-version", default=os.getenv("MODEL_EVALUATION_MODEL_VERSION", ""), help="registered model version (or MODEL_EVALUATION_MODEL_VERSION)")
    parser.add_argument("--gateway-url", default=os.getenv("MODEL_EVALUATION_GATEWAY_URL", ""))
    parser.add_argument("--lock-file", default=os.getenv("MODEL_EVALUATION_LOCK_FILE", "./work/model-evaluation.lock"))
    parser.add_argument("--audit-file", default=os.getenv("MODEL_EVALUATION_AUDIT_FILE", "./work/model-evaluation.jsonl"))
    parser.add_argument("--dry-run", action="store_true", help="evaluate without calling Gateway")
    args = parser.parse_args(argv)
    if not args.model_id or not args.model_version:
        parser.error("--model-id/--model-version 或 MODEL_EVALUATION_MODEL_ID/MODEL_EVALUATION_MODEL_VERSION 必须提供")
    lock = EvaluationLock(args.lock_file)
    try:
        with lock:
            result = run_once(
                args.db,
                args.model_version,
                args.model_id,
                gateway_url=args.gateway_url,
                token=os.getenv("AI_SERVICE_INTERNAL_TOKEN", ""),
                dry_run=args.dry_run,
            )
    except RuntimeError as exc:
        result = {"status": "blocked", "reason": str(exc)}
        append_audit(args.audit_file, result)
        print(json.dumps(result, ensure_ascii=False))
        return 2
    except (OSError, ValueError) as exc:
        result = {"status": "error", "reason": str(exc)}
        append_audit(args.audit_file, result)
        print(json.dumps(result, ensure_ascii=False))
        return 3
    append_audit(args.audit_file, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    callback = result.get("callback") or {}
    return 0 if result.get("status") == "ready" and (args.dry_run or callback.get("accepted") is True) else 2


if __name__ == "__main__":
    sys.exit(main())
