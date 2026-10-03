"""Run a credential-free staging smoke check for the evaluation contract.

The runner deliberately opens the Gateway SQLite database in read-only mode,
executes the same offline evaluator as the scheduler, and validates the
allowlisted payload that would be sent to Gateway.  It never calls a provider,
never writes to the database, and never prints raw events or credentials.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Mapping, Optional, Sequence

from .build_decision_dataset import load_sqlite
from .run_model_evaluation import gateway_payload, post_report
from .evaluate_decision_dataset import evaluate


ALLOWED_PAYLOAD_KEYS = {
    "evaluation_version", "status", "promotion_ready", "model_version", "reasons",
    "evaluation_samples", "validation_samples", "offline_score", "drift_score",
    "counterfactual_samples", "fairness_min_utility", "fairness_utility_variance",
    "policy_comparison_required", "policy_comparison_status", "policy_baseline_samples",
    "policy_behavior_samples", "policy_comparison_ready", "policy_reward_lift",
    "policy_adoption_signal_lift", "policy_segment_coverage", "fairness_segment_required",
    "fairness_segment_status", "fairness_segment_max_stddev",
    "fairness_segment_threshold_exceeded",
    "provider_quality_required", "provider_quality_status",
    "provider_quality_providers", "provider_quality_ready_providers",
    "provider_quality_insufficient_providers", "provider_quality_blocked_providers",
}


def _contains_sensitive(value: Any) -> bool:
    """Reject accidental identifiers, tokens, URLs or raw model payloads."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in ("token", "secret", "password", "user_id", "trip_id", "payload", "url")):
                return True
            if _contains_sensitive(nested):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive(item) for item in value)
    return False


def run_smoke(db_path: str, model_version: str, callback_url: str = "", token: str = "") -> dict[str, Any]:
    events = load_sqlite(db_path)
    report = evaluate(events)
    payload = gateway_payload(report, model_version)
    unexpected = sorted(set(payload) - ALLOWED_PAYLOAD_KEYS)
    if unexpected:
        raise ValueError("evaluation payload contains non-allowlisted fields")
    if _contains_sensitive(payload):
        raise ValueError("evaluation payload contains a sensitive field")
    # Return only bounded aggregates.  Do not include report.reasons, events,
    # paths, provider responses, or nested segment samples in smoke output.
    result = {
        "status": "pass",
        "evaluation_status": str(payload.get("status") or "insufficient_evidence"),
        "promotion_ready": bool(payload.get("promotion_ready", False)),
        "model_version": model_version,
        "sample_count": int(payload.get("evaluation_samples") or 0),
        "validation_samples": int(payload.get("validation_samples") or 0),
        "fairness_segment_status": payload.get("fairness_segment_status"),
        "fairness_segment_max_stddev": payload.get("fairness_segment_max_stddev"),
        "fairness_segment_threshold_exceeded": payload.get("fairness_segment_threshold_exceeded"),
        "provider_quality_status": payload.get("provider_quality_status"),
        "provider_quality_providers": payload.get("provider_quality_providers", 0),
        "provider_quality_ready_providers": payload.get("provider_quality_ready_providers", 0),
        "provider_quality_insufficient_providers": payload.get("provider_quality_insufficient_providers", 0),
        "provider_quality_blocked_providers": payload.get("provider_quality_blocked_providers", 0),
        "contract_fields": len(payload),
    }
    if callback_url:
        if not token:
            raise ValueError("AI_SERVICE_INTERNAL_TOKEN is required for callback smoke")
        callback = post_report(callback_url, token, payload)
        result["callback"] = {
            "accepted": bool(callback.get("accepted", False)),
            "status_code": callback.get("status_code"),
        }
        if not result["callback"]["accepted"]:
            result["status"] = "callback_rejected"
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run a read-only, credential-free staging evaluation smoke check")
    parser.add_argument("--db", required=True, help="Gateway SQLite database (opened read-only)")
    parser.add_argument("--model-version", default=os.getenv("MODEL_EVALUATION_MODEL_VERSION", ""))
    parser.add_argument("--callback-url", default=os.getenv("STAGING_SMOKE_CALLBACK_URL", ""), help="Optional internal Gateway callback URL; only used when --callback is set")
    parser.add_argument("--callback", action="store_true", help="Explicitly perform one aggregate-only Gateway callback")
    args = parser.parse_args(argv)
    if not args.model_version.strip():
        parser.error("--model-version 或 MODEL_EVALUATION_MODEL_VERSION 必须提供")
    if args.callback and not args.callback_url.strip():
        parser.error("--callback 需要 --callback-url 或 STAGING_SMOKE_CALLBACK_URL")
    try:
        result = run_smoke(
            args.db,
            args.model_version.strip(),
            callback_url=args.callback_url.strip() if args.callback else "",
            token=os.getenv("AI_SERVICE_INTERNAL_TOKEN", "") if args.callback else "",
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
