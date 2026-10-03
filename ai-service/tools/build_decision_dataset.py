"""Build a time-split decision dataset from planning events.

The builder is intentionally conservative.  It only exports whitelisted
planning events, never copies raw user payloads, and refuses to claim that a
policy is better when outcome or counterfactual evidence is missing.

Examples::

    python -m tools.build_decision_dataset --input export.json --format json
    python -m tools.build_decision_dataset --db ../gateway/data/omniroute.db

The input may be the gateway response (``{"samples": [...]}``), a plain
event list, or a SQLite planning_events table.  SQLite is opened read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


ALLOWED_EVENTS = {
    "planning_decision_audit",
    "plan_adopted",
    "plan_rejected",
    "satisfaction",
    "plan_edited",
    "plan_favorited",
    "plan_replaced",
    "node_visited",
    "provider_quality",
}
MIN_SAMPLES = 30
MIN_VERSIONS = 2
MIN_VALIDATION = 1

WHITELIST = (
    "destination", "season", "budget_bucket", "traveler_group", "segment",
    "candidate_count", "pareto_frontier_count", "pareto_rejected_count",
    "pareto_rejection_reasons", "fairness_minimum_utility",
    "fairness_utility_stddev", "fairness_threshold",
    "fairness_threshold_exceeded", "fairness_repair_status",
    "specialist_completed", "specialist_degraded", "hard_satisfied",
    "ranking_status", "ranking_policy", "behavior_enabled",
    "behavior_sample_count", "behavior_verified", "behavior_adopted",
    "baseline_changed_positions",
    "objective", "action", "arm_id", "bandit_arm_id", "model_version",
    "reward", "propensity",
    "kind", "provider", "status", "source_count", "observed_timestamp_count",
    "fact_usable_count", "decision_eligible_count", "decision_blocked_count",
    "decision_gate_contract", "estimated_count", "stale_count", "source_conflict_count",
    "enum_validated_count", "enum_invalid_count", "latency_ms", "quality_contract",
    "field_counts", "field_names",
)


@dataclass(frozen=True)
class RawEvent:
    event_id: str
    event_type: str
    created_at: str
    user_id: str = ""
    trip_id: str = ""
    plan_id: str = ""
    payload: Mapping[str, Any] = None  # type: ignore[assignment]
    value: Any = None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _parse_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return _as_dict(decoded)
        except (TypeError, ValueError):
            return {}
    return {}


def _timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "1970-01-01T00:00:00+00:00"
    # Keep ordering deterministic for timestamps with/without a timezone.
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return text


def _raw_events(items: Iterable[Mapping[str, Any]]) -> list[RawEvent]:
    result: list[RawEvent] = []
    for index, item in enumerate(items):
        row = _as_dict(item)
        event_type = str(row.get("event_type") or row.get("type") or "").strip()
        if event_type not in ALLOWED_EVENTS:
            continue
        payload = _parse_payload(row.get("payload"))
        # Gateway exports put fields in payload; accepting top-level fields
        # makes this builder usable with a redacted fixture without changing
        # the event contract.
        for key in WHITELIST:
            if key in row and key not in payload:
                payload[key] = row[key]
        result.append(RawEvent(
            event_id=str(row.get("event_id") or row.get("id") or index),
            event_type=event_type,
            created_at=_timestamp(row.get("created_at") or row.get("recorded_at") or row.get("timestamp")),
            user_id=str(row.get("user_id") or ""),
            trip_id=str(row.get("trip_id") or ""),
            plan_id=str(row.get("plan_id") or ""),
            payload=payload,
            value=row.get("value"),
        ))
    return sorted(result, key=lambda event: (event.created_at, event.event_id))


def load_json(path: str) -> list[RawEvent]:
    with Path(path).open("r", encoding="utf-8") as handle:
        decoded = json.load(handle)
    if isinstance(decoded, Mapping):
        items = decoded.get("events") or decoded.get("samples") or decoded.get("data") or []
        if isinstance(items, Mapping):
            items = items.get("events") or items.get("samples") or []
    else:
        items = decoded
    return _raw_events(items if isinstance(items, list) else [])


def load_sqlite(path: str) -> list[RawEvent]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='planning_events'").fetchone()
        if exists is None:
            return []
        rows = con.execute(
            "SELECT id, event_type, created_at, user_id, trip_id, plan_id, payload, value "
            "FROM planning_events ORDER BY created_at ASC, id ASC"
        ).fetchall()
        return _raw_events([dict(row) for row in rows])
    finally:
        con.close()


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def _reward(event: RawEvent, payload: Mapping[str, Any]) -> Optional[float]:
    value = _number(payload.get("reward"))
    if value is not None:
        return max(0.0, min(1.0, value))
    if event.event_type == "plan_adopted":
        return 1.0
    if event.event_type == "plan_rejected":
        return 0.0
    if event.event_type == "satisfaction":
        score = _number(payload.get("score", event.value))
        if score is not None:
            return max(0.0, min(1.0, score / 5.0 if score > 1 else score))
    return None


def _sample(event: RawEvent, split: str, outcome: Optional[RawEvent]) -> dict[str, Any]:
    payload = dict(event.payload)
    if outcome is not None:
        outcome_payload = dict(outcome.payload)
        for key in ("reward", "propensity", "arm_id", "bandit_arm_id"):
            if key in outcome_payload and key not in payload:
                payload[key] = outcome_payload[key]
    version = str(payload.get("model_version") or "").strip()
    arm = str(payload.get("bandit_arm_id") or payload.get("arm_id") or "").strip()
    result: dict[str, Any] = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "created_at": event.created_at,
        "trip_hash": _hash(event.trip_id),
        "plan_hash": _hash(event.plan_id),
        "model_version": version,
        "arm_id": arm,
        "split": split,
        "reward": _reward(outcome or event, payload),
        "propensity": _number(payload.get("propensity")),
    }
    for key in WHITELIST:
        if key in payload:
            result[key] = payload[key]
    return result


def _provider_quality_report(events: Sequence[RawEvent]) -> dict[str, Any]:
    """Aggregate provider telemetry without copying identities or raw rows."""
    grouped: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {
        "observations": 0, "candidate_count": 0, "decision_eligible_count": 0,
        "decision_blocked_count": 0, "source_conflict_count": 0, "enum_invalid_count": 0,
        "stale_count": 0, "estimated_count": 0,
    })
    for event in events:
        if event.event_type != "provider_quality":
            continue
        kind = str(event.payload.get("kind") or "").strip().lower()
        provider = str(event.payload.get("provider") or "").strip()
        if not kind or not provider:
            continue
        item = grouped[(kind, provider)]
        item["observations"] += 1
        for key in item:
            if key == "observations":
                continue
            number = _number(event.payload.get(key))
            if number is not None:
                item[key] += int(number)
    providers: dict[str, Any] = {}
    for (kind, provider), item in sorted(grouped.items()):
        total = item["candidate_count"]
        key = f"{kind}:{provider}"
        providers[key] = {
            "kind": kind,
            "provider": provider,
            **item,
            "eligible_ratio": item["decision_eligible_count"] / total if total else 0.0,
            "source_conflict_ratio": item["source_conflict_count"] / total if total else 0.0,
            "enum_invalid_ratio": item["enum_invalid_count"] / total if total else 0.0,
            "stale_ratio": item["stale_count"] / total if total else 0.0,
        }
    return {
        "status": "available" if providers else "insufficient_data",
        "observations": sum(item["observations"] for item in grouped.values()),
        "providers": providers,
        "policy": "aggregate_only_no_raw_supplier_payload",
    }


def build_dataset(events: Sequence[RawEvent]) -> dict[str, Any]:
    ordered = sorted(events, key=lambda event: (event.created_at, event.event_id))
    # Join decision audits to the first explicit outcome for the same plan/trip.
    outcomes: dict[tuple[str, str], RawEvent] = {}
    for event in ordered:
        if event.event_type in {"plan_adopted", "plan_rejected", "satisfaction"}:
            outcomes.setdefault((event.trip_id, event.plan_id), event)
            outcomes.setdefault((event.trip_id, ""), event)
    decision_events = [event for event in ordered if event.event_type == "planning_decision_audit"]
    provider_quality = _provider_quality_report(ordered)
    samples: list[dict[str, Any]] = []
    decision_validation_start = max(1, int(len(decision_events) * 0.8)) if len(decision_events) > 1 else len(decision_events)
    for index, event in enumerate(decision_events):
        if event.event_type != "planning_decision_audit":
            continue
        outcome = outcomes.get((event.trip_id, event.plan_id)) or outcomes.get((event.trip_id, ""))
        split = "validation" if index >= decision_validation_start else "train"
        samples.append(_sample(event, split, outcome))

    # If no audit event exists, retain explicit decision outcomes as a usable
    # but clearly weaker dataset rather than silently returning no samples.
    if not samples:
        outcome_events = [event for event in ordered if event.event_type in {"plan_adopted", "plan_rejected", "satisfaction"}]
        outcome_validation_start = max(1, int(len(outcome_events) * 0.8)) if len(outcome_events) > 1 else len(outcome_events)
        for index, event in enumerate(outcome_events):
            if event.event_type in {"plan_adopted", "plan_rejected", "satisfaction"}:
                split = "validation" if index >= outcome_validation_start else "train"
                samples.append(_sample(event, split, None))

    train_count = sum(sample["split"] == "train" for sample in samples)
    validation_count = sum(sample["split"] == "validation" for sample in samples)
    versions = Counter(sample["model_version"] for sample in samples if sample["model_version"])
    strata: dict[str, Counter[str]] = {key: Counter() for key in ("destination", "season", "budget_bucket", "traveler_group")}
    missing = Counter()
    for sample in samples:
        for key, counter in strata.items():
            value = str(sample.get(key) or "").strip()
            if value:
                counter[value] += 1
        for key in ("model_version", "reward", "propensity"):
            if sample.get(key) in (None, ""):
                missing[key] += 1
    cf_samples = sum(sample["reward"] is not None and sample["propensity"] is not None for sample in samples)
    reason = "ready"
    ready = True
    if len(samples) < MIN_SAMPLES:
        ready, reason = False, f"样本不足：需要至少 {MIN_SAMPLES} 条决策样本，当前 {len(samples)} 条"
    elif validation_count < MIN_VALIDATION:
        ready, reason = False, "验证集为空，无法进行时间切分评估"
    elif len(versions) < MIN_VERSIONS:
        ready, reason = False, f"模型版本不足：至少需要 {MIN_VERSIONS} 个版本，当前 {len(versions)} 个"
    elif missing["reward"] > 0:
        ready, reason = False, f"存在 {missing['reward']} 条样本缺少显式 reward"
    status = "ready" if ready else "insufficient_evidence"
    return {
        "dataset_version": "decision-dataset-v2",
        "status": status,
        "ready": ready,
        "reason": reason,
        "sample_count": len(samples),
        "train_count": train_count,
        "validation_count": validation_count,
        "split_policy": "按 created_at 升序，最早 80% train，最新 20% validation",
        "samples": samples,
        "model_versions": dict(versions),
        "strata": {key: dict(value) for key, value in strata.items()},
        "missing_fields": dict(missing),
        "counterfactual_status": "available" if cf_samples else "insufficient_data",
        "counterfactual_samples": cf_samples,
        "counterfactual_required_fields": ["propensity", "reward"],
        "evidence_policy": "证据不足时不输出策略优劣、提升或显著性结论",
        "provider_quality": provider_quality,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = ["# 决策离线评估数据集", "", f"- 状态：`{report.get('status')}`", f"- 原因：{report.get('reason')}", f"- 样本：{report.get('sample_count', 0)}（train {report.get('train_count', 0)} / validation {report.get('validation_count', 0)}）", f"- 反事实：`{report.get('counterfactual_status')}`（{report.get('counterfactual_samples', 0)} 条）", "", "## 证据边界", "", str(report.get("evidence_policy", ""))]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a time-split planning decision dataset")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="gateway JSON export")
    source.add_argument("--db", help="gateway SQLite database (read-only)")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)
    try:
        events = load_json(args.input) if args.input else load_sqlite(args.db)
        report = build_dataset(events)
    except (OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        report = {"status": "error", "ready": False, "reason": str(exc), "samples": []}
    if args.format == "markdown":
        print(render_markdown(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "ready" else 2


if __name__ == "__main__":
    sys.exit(main())
