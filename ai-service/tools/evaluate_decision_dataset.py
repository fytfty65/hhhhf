"""Evaluate a decision dataset without making unsupported promotion claims.

This is an offline-only companion to ``build_decision_dataset``.  It reports
per-version outcome metrics, normalized IPS estimates, temporal drift and
fairness coverage.  A model is promotion-ready only when all required
evidence is present; missing evidence is a blocking state, never a zero.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from typing import Any, Mapping, Optional, Sequence

from .build_decision_dataset import build_dataset, load_json, load_sqlite


MIN_EVALUATION_SAMPLES = 20
MIN_VALIDATION_SAMPLES = 5
MIN_OFFLINE_SCORE = 0.80
MAX_DRIFT_SCORE = 0.20
MAX_FAIRNESS_STDDEV = 0.10
MIN_POLICY_SAMPLES = 5
POLICY_SEGMENT_KEYS = ("traveler_group", "destination", "season", "budget_bucket")
MIN_PROVIDER_QUALITY_OBSERVATIONS = 5
MAX_PROVIDER_STALE_RATIO = 0.20
MAX_PROVIDER_SOURCE_CONFLICT_RATIO = 0.20
MAX_PROVIDER_ENUM_INVALID_RATIO = 0.05
MIN_PROVIDER_ELIGIBLE_RATIO = 0.70


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _ips(samples: list[Mapping[str, Any]]) -> dict[str, Any]:
    weighted_reward = 0.0
    weight = 0.0
    count = 0
    for sample in samples:
        reward, propensity = sample.get("reward"), sample.get("propensity")
        if not isinstance(reward, (int, float)) or not isinstance(propensity, (int, float)):
            continue
        if propensity <= 0 or propensity > 1:
            continue
        weighted_reward += float(reward) / float(propensity)
        weight += 1.0 / float(propensity)
        count += 1
    if count == 0 or weight == 0:
        return {"status": "insufficient_data", "samples": 0, "method": "ips", "estimate": None}
    return {"status": "available", "samples": count, "method": "ips", "estimate": round(weighted_reward / weight, 6)}


def _distribution(samples: list[Mapping[str, Any]], key: str) -> Counter[str]:
    return Counter(str(sample.get(key) or "") for sample in samples if str(sample.get(key) or "").strip())


def _total_variation(left: Counter[str], right: Counter[str]) -> Optional[float]:
    if not left or not right:
        return None
    left_total, right_total = sum(left.values()), sum(right.values())
    keys = set(left) | set(right)
    return 0.5 * sum(abs(left[key] / left_total - right[key] / right_total) for key in keys)


def _drift(samples: list[Mapping[str, Any]]) -> dict[str, Any]:
    train = [sample for sample in samples if sample.get("split") == "train"]
    validation = [sample for sample in samples if sample.get("split") == "validation"]
    scores: dict[str, float] = {}
    for key in ("destination", "season", "budget_bucket", "traveler_group"):
        score = _total_variation(_distribution(train, key), _distribution(validation, key))
        if score is not None:
            scores[key] = round(score, 6)
    if not scores:
        return {"status": "insufficient_data", "score": None, "dimensions": {}}
    score = sum(scores.values()) / len(scores)
    status = "stable" if score <= MAX_DRIFT_SCORE else "alert"
    return {"status": status, "score": round(score, 6), "dimensions": scores, "threshold": MAX_DRIFT_SCORE}


def _fairness(samples: list[Mapping[str, Any]]) -> dict[str, Any]:
    utilities = [float(sample["fairness_minimum_utility"]) for sample in samples if isinstance(sample.get("fairness_minimum_utility"), (int, float))]
    deviations = [float(sample["fairness_utility_stddev"]) for sample in samples if isinstance(sample.get("fairness_utility_stddev"), (int, float))]
    thresholds = [float(sample["fairness_threshold"]) for sample in samples if isinstance(sample.get("fairness_threshold"), (int, float))]
    exceeded = sum(1 for sample in samples if sample.get("fairness_threshold_exceeded") is True)
    if not utilities or not deviations:
        return {"status": "insufficient_data", "samples": 0, "minimum_utility": None, "max_stddev": None, "threshold_exceeded": None}
    max_stddev = max(deviations)
    return {
        "status": "available",
        "samples": len(deviations),
        "minimum_utility": round(min(utilities), 6),
        "mean_minimum_utility": round(sum(utilities) / len(utilities), 6),
        "max_stddev": round(max_stddev, 6),
        "mean_threshold": round(sum(thresholds) / len(thresholds), 6) if thresholds else None,
        "threshold_exceeded": exceeded,
        "threshold": MAX_FAIRNESS_STDDEV,
    }


def _fairness_by_segment(samples: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize fairness evidence independently for each known segment."""
    result: dict[str, Any] = {}
    for dimension in POLICY_SEGMENT_KEYS:
        values = sorted({str(sample.get(dimension) or "").strip() for sample in samples if str(sample.get(dimension) or "").strip()})
        segments: dict[str, Any] = {}
        available = 0
        stddevs: list[float] = []
        exceeded = 0
        for value in values:
            fairness = _fairness([sample for sample in samples if str(sample.get(dimension) or "").strip() == value])
            segments[value] = fairness
            if fairness.get("status") == "available":
                available += 1
                if isinstance(fairness.get("max_stddev"), (int, float)):
                    stddevs.append(float(fairness["max_stddev"]))
                exceeded += int(fairness.get("threshold_exceeded") or 0)
        result[dimension] = {
            "status": "available" if available else "insufficient_data",
            "available_segments": available,
            "segment_count": len(values),
            "max_stddev": round(max(stddevs), 6) if stddevs else None,
            "threshold_exceeded": exceeded,
            "segments": segments,
        }
    available_summaries = [item for item in result.values() if item.get("status") == "available"]
    max_stddevs = [item["max_stddev"] for item in available_summaries if isinstance(item.get("max_stddev"), (int, float))]
    return {
        # Promotion requires coverage in every protected dimension. A partial
        # report remains visible for diagnosis but is not rollout-ready.
        "status": "available" if len(available_summaries) == len(POLICY_SEGMENT_KEYS) else ("partial" if available_summaries else "insufficient_data"),
        "available_dimensions": len(available_summaries),
        "dimension_count": len(POLICY_SEGMENT_KEYS),
        "max_stddev": round(max(max_stddevs), 6) if max_stddevs else None,
        "threshold_exceeded": sum(int(item.get("threshold_exceeded") or 0) for item in available_summaries),
        "dimensions": result,
    }


def _policy_comparison(samples: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Compare baseline and behavior-aware samples without imputing labels."""
    groups: dict[str, list[Mapping[str, Any]]] = {"baseline": [], "behavior_aware": []}
    for sample in samples:
        enabled = sample.get("behavior_enabled")
        if enabled is True or str(enabled).lower() == "true":
            groups["behavior_aware"].append(sample)
        elif enabled is False or str(enabled).lower() == "false":
            groups["baseline"].append(sample)

    def summarize(items: list[Mapping[str, Any]]) -> dict[str, Any]:
        rewards = [float(item["reward"]) for item in items if isinstance(item.get("reward"), (int, float))]
        verified = [float(item["behavior_verified"]) for item in items if isinstance(item.get("behavior_verified"), (int, float))]
        adopted = [float(item["behavior_adopted"]) for item in items if isinstance(item.get("behavior_adopted"), (int, float))]
        changed = [float(item["baseline_changed_positions"]) for item in items if isinstance(item.get("baseline_changed_positions"), (int, float))]
        return {
            "samples": len(items),
            "labeled_samples": len(rewards),
            "mean_reward": round(sum(rewards) / len(rewards), 6) if rewards else None,
            "mean_verified": round(sum(verified) / len(verified), 6) if verified else None,
            "mean_adopted": round(sum(adopted) / len(adopted), 6) if adopted else None,
            "mean_changed_positions": round(sum(changed) / len(changed), 6) if changed else None,
        }

    baseline = summarize(groups["baseline"])
    behavior = summarize(groups["behavior_aware"])
    reasons: list[str] = []
    if baseline["samples"] < MIN_POLICY_SAMPLES:
        reasons.append(f"基线排序样本不足：需要至少 {MIN_POLICY_SAMPLES} 条")
    if behavior["samples"] < MIN_POLICY_SAMPLES:
        reasons.append(f"行为排序样本不足：需要至少 {MIN_POLICY_SAMPLES} 条")
    if baseline["labeled_samples"] == 0 or behavior["labeled_samples"] == 0:
        reasons.append("基线或行为排序缺少显式 reward")
    result: dict[str, Any] = {
        "status": "available" if not reasons else "insufficient_data",
        "method": "time_split_policy_stratification",
        "minimum_samples_per_policy": MIN_POLICY_SAMPLES,
        "baseline": baseline,
        "behavior_aware": behavior,
        "reasons": reasons,
        "evidence_boundary": "仅比较带明确 behavior_enabled 和 reward 的样本，不对缺失标签补零",
    }
    if not reasons:
        result["reward_lift"] = round(behavior["mean_reward"] - baseline["mean_reward"], 6)
        result["adoption_signal_lift"] = round((behavior["mean_adopted"] or 0) - (baseline["mean_adopted"] or 0), 6)
    else:
        result["reward_lift"] = None
        result["adoption_signal_lift"] = None
    return result


def _policy_comparison_by_segment(samples: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Evaluate the two policies independently inside safe, known strata.

    A segment with too little evidence is returned as ``insufficient_data``;
    it is never merged with another segment and never padded with zero reward.
    Values are already allowlisted decision features, not user identifiers.
    """
    result: dict[str, Any] = {}
    for dimension in POLICY_SEGMENT_KEYS:
        values = sorted({str(sample.get(dimension) or "").strip() for sample in samples if str(sample.get(dimension) or "").strip()})
        segments: dict[str, Any] = {}
        available = 0
        for value in values:
            comparison = _policy_comparison([sample for sample in samples if str(sample.get(dimension) or "").strip() == value])
            segments[value] = comparison
            if comparison.get("status") == "available":
                available += 1
        result[dimension] = {
            "status": "available" if available else "insufficient_data",
            "available_segments": available,
            "segment_count": len(values),
            "segments": segments,
        }
    return result


def _provider_quality_gate(provider_quality: Mapping[str, Any]) -> dict[str, Any]:
    """Turn provider telemetry into a release-safe aggregate gate.

    Provider quality is evidence about the input boundary, not a model reward.
    It is therefore kept separate from decision samples.  A missing report is
    ``not_required`` (legacy datasets remain evaluable), while a present but
    sparse report is explicitly insufficient and cannot be interpreted as a
    healthy supplier.  Only bounded ratios/counts are returned.
    """
    if not isinstance(provider_quality, Mapping) or provider_quality.get("status") != "available":
        return {
            "status": "not_required",
            "providers": 0,
            "ready_providers": 0,
            "insufficient_providers": 0,
            "blocked_providers": 0,
            "reasons": [],
            "thresholds": {
                "minimum_observations": MIN_PROVIDER_QUALITY_OBSERVATIONS,
                "maximum_stale_ratio": MAX_PROVIDER_STALE_RATIO,
                "maximum_source_conflict_ratio": MAX_PROVIDER_SOURCE_CONFLICT_RATIO,
                "maximum_enum_invalid_ratio": MAX_PROVIDER_ENUM_INVALID_RATIO,
                "minimum_eligible_ratio": MIN_PROVIDER_ELIGIBLE_RATIO,
            },
        }

    entries = provider_quality.get("providers")
    if not isinstance(entries, Mapping) or not entries:
        return {
            "status": "insufficient_data",
            "providers": 0,
            "ready_providers": 0,
            "insufficient_providers": 0,
            "blocked_providers": 0,
            "reasons": ["供应商质量报告没有可用分层"],
        }

    ready = insufficient = blocked = 0
    reasons: list[str] = []
    summaries: dict[str, Any] = {}
    for raw_key, raw_value in entries.items():
        if not isinstance(raw_value, Mapping):
            insufficient += 1
            reasons.append(f"{str(raw_key)[:80]}:质量记录格式不足")
            continue
        key = str(raw_key)[:120]
        observations = raw_value.get("observations")
        candidate_count = raw_value.get("candidate_count")
        if not isinstance(observations, (int, float)) or not isinstance(candidate_count, (int, float)) or observations < MIN_PROVIDER_QUALITY_OBSERVATIONS or candidate_count <= 0:
            insufficient += 1
            summaries[key] = {"status": "insufficient_data", "observations": max(0, int(observations or 0)), "candidate_count": max(0, int(candidate_count or 0))}
            continue
        stale = float(raw_value.get("stale_ratio") or 0)
        conflict = float(raw_value.get("source_conflict_ratio") or 0)
        enum_invalid = float(raw_value.get("enum_invalid_ratio") or 0)
        eligible = float(raw_value.get("eligible_ratio") or 0)
        ratios = (stale, conflict, enum_invalid, eligible)
        if any(value < 0 or value > 1 for value in ratios):
            blocked += 1
            reasons.append(f"{key}:质量比例超出范围")
            summaries[key] = {"status": "blocked", "observations": int(observations), "candidate_count": int(candidate_count)}
            continue
        entry_reasons: list[str] = []
        if stale > MAX_PROVIDER_STALE_RATIO:
            entry_reasons.append("stale_ratio")
        if conflict > MAX_PROVIDER_SOURCE_CONFLICT_RATIO:
            entry_reasons.append("source_conflict_ratio")
        if enum_invalid > MAX_PROVIDER_ENUM_INVALID_RATIO:
            entry_reasons.append("enum_invalid_ratio")
        if eligible < MIN_PROVIDER_ELIGIBLE_RATIO:
            entry_reasons.append("eligible_ratio")
        status = "blocked" if entry_reasons else "ready"
        summaries[key] = {
            "status": status,
            "observations": int(observations),
            "candidate_count": int(candidate_count),
            "eligible_ratio": round(eligible, 6),
            "stale_ratio": round(stale, 6),
            "source_conflict_ratio": round(conflict, 6),
            "enum_invalid_ratio": round(enum_invalid, 6),
            "reasons": entry_reasons,
        }
        if entry_reasons:
            blocked += 1
            reasons.append(f"{key}:" + ",".join(entry_reasons))
        else:
            ready += 1

    if blocked:
        status = "blocked"
    elif insufficient:
        status = "insufficient_data"
    else:
        status = "available"
    return {
        "status": status,
        "providers": len(entries),
        "ready_providers": ready,
        "insufficient_providers": insufficient,
        "blocked_providers": blocked,
        "reasons": reasons[:20],
        "thresholds": {
            "minimum_observations": MIN_PROVIDER_QUALITY_OBSERVATIONS,
            "maximum_stale_ratio": MAX_PROVIDER_STALE_RATIO,
            "maximum_source_conflict_ratio": MAX_PROVIDER_SOURCE_CONFLICT_RATIO,
            "maximum_enum_invalid_ratio": MAX_PROVIDER_ENUM_INVALID_RATIO,
            "minimum_eligible_ratio": MIN_PROVIDER_ELIGIBLE_RATIO,
        },
        "by_provider": summaries,
    }


def evaluate(events: Sequence[Any]) -> dict[str, Any]:
    dataset = build_dataset(events)
    samples = list(dataset.get("samples") or [])
    by_version: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for sample in samples:
        version = str(sample.get("model_version") or "unversioned")
        by_version[version].append(sample)
    versions: dict[str, Any] = {}
    for version, items in sorted(by_version.items()):
        train = [item for item in items if item.get("split") == "train"]
        validation = [item for item in items if item.get("split") == "validation"]
        train_rewards = [float(item["reward"]) for item in train if isinstance(item.get("reward"), (int, float))]
        validation_rewards = [float(item["reward"]) for item in validation if isinstance(item.get("reward"), (int, float))]
        versions[version] = {
            "samples": len(items),
            "train_samples": len(train),
            "validation_samples": len(validation),
            "train_mean_reward": _mean(train_rewards),
            "validation_mean_reward": _mean(validation_rewards),
            "counterfactual": _ips(items),
        }

    validation_rewards = [float(item["reward"]) for item in samples if item.get("split") == "validation" and isinstance(item.get("reward"), (int, float))]
    drift = _drift(samples)
    fairness = _fairness(samples)
    fairness_segment_summary = _fairness_by_segment(samples)
    policy_comparison = _policy_comparison(samples)
    policy_comparison_by_segment = _policy_comparison_by_segment(samples)
    provider_quality = dataset.get("provider_quality") if isinstance(dataset.get("provider_quality"), Mapping) else {}
    provider_quality_gate = _provider_quality_gate(provider_quality)
    policy_segment_coverage = {
        dimension: {
            "available_segments": summary.get("available_segments", 0),
            "segment_count": summary.get("segment_count", 0),
            "status": summary.get("status", "insufficient_data"),
        }
        for dimension, summary in policy_comparison_by_segment.items()
    }
    reasons: list[str] = []
    if dataset.get("status") != "ready":
        reasons.append(str(dataset.get("reason") or "数据集未达到就绪条件"))
    if len(samples) < MIN_EVALUATION_SAMPLES:
        reasons.append(f"评估样本不足：需要至少 {MIN_EVALUATION_SAMPLES} 条")
    validation_count = sum(item.get("split") == "validation" for item in samples)
    if validation_count < MIN_VALIDATION_SAMPLES:
        reasons.append(f"验证集样本不足：需要至少 {MIN_VALIDATION_SAMPLES} 条")
    if not validation_rewards:
        reasons.append("验证集缺少显式 reward")
    offline_score = _mean(validation_rewards)
    if offline_score is not None and offline_score < MIN_OFFLINE_SCORE:
        reasons.append(f"验证集分数低于 {MIN_OFFLINE_SCORE:.2f}")
    if drift.get("status") == "insufficient_data":
        reasons.append("无法计算训练集与验证集的数据漂移")
    elif float(drift["score"]) > MAX_DRIFT_SCORE:
        reasons.append(f"数据漂移超过 {MAX_DRIFT_SCORE:.2f}")
    if fairness.get("status") != "available":
        reasons.append("公平性指标不足")
    elif float(fairness["max_stddev"]) > MAX_FAIRNESS_STDDEV or int(fairness["threshold_exceeded"]) > 0:
        reasons.append("公平性标准差或超阈值样本不满足门禁")
    ips = _ips(samples)
    if ips["status"] != "available":
        reasons.append("反事实 propensity/reward 证据不足")
    if provider_quality_gate.get("status") in {"insufficient_data", "blocked"}:
        reasons.append("供应商质量门禁未通过：" + "；".join(provider_quality_gate.get("reasons") or ["证据不足"]))
    return {
        "evaluation_version": "decision-evaluation-v1",
        "status": "ready" if not reasons else "insufficient_evidence",
        "promotion_ready": not reasons,
        "reasons": reasons,
        # Flat aliases are the stable contract consumed by the gateway model
        # registry; nested objects below remain useful for diagnostics.
        "evaluation_samples": len(samples),
        "validation_samples": validation_count,
        "offline_score": round(offline_score, 6) if offline_score is not None else None,
        "drift_score": drift.get("score"),
        "fairness_min_utility": fairness.get("minimum_utility"),
        "fairness_utility_variance": fairness.get("max_stddev"),
        "counterfactual_samples": ips.get("samples", 0),
        "versions": versions,
        "counterfactual": ips,
        "drift": drift,
        "fairness": fairness,
        "fairness_segment_summary": fairness_segment_summary,
        "policy_comparison": policy_comparison,
        "policy_comparison_by_segment": policy_comparison_by_segment,
        "policy_segment_coverage": policy_segment_coverage,
        "provider_quality": provider_quality,
        "provider_quality_gate": provider_quality_gate,
        "gate": {
            "minimum_evaluation_samples": MIN_EVALUATION_SAMPLES,
            "minimum_validation_samples": MIN_VALIDATION_SAMPLES,
            "minimum_offline_score": MIN_OFFLINE_SCORE,
            "maximum_drift_score": MAX_DRIFT_SCORE,
            "maximum_fairness_stddev": MAX_FAIRNESS_STDDEV,
            "automatic_rollback": True,
        },
        "dataset": dataset,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a planning decision dataset")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="gateway JSON export")
    source.add_argument("--db", help="gateway SQLite database (read-only)")
    parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args(argv)
    events = load_json(args.input) if args.input else load_sqlite(args.db)
    report = evaluate(events)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["promotion_ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
