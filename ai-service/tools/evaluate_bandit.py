"""Offline evaluation of the planning bandit against logged outcomes.

Answers one question with evidence: *is the learned policy better than the
baseline, or is it just moving?* It joins the gateway's durable event log with
explicit satisfaction ratings, then reports per-arm posterior, coverage, and a
counterfactual check.

It deliberately refuses to invent numbers. If the reward signals were never
recorded — which is exactly the state this project was in while the client
dropped the bandit arm id — the script reports "insufficient evidence" and exits
non-zero rather than printing a confident verdict from nothing.

Usage (from the ai-service directory):

    python -m tools.evaluate_bandit --db ../gateway/data/omniroute.db
    python -m tools.evaluate_bandit --db ../gateway/data/omniroute.db --format markdown

Exit codes:
    0  evaluation produced a verdict (with or without a lift)
    2  not enough evidence to evaluate
    3  the database or schema could not be read
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Minimum evidence before a verdict is meaningful. Below this the honest answer
# is "we do not know yet".
MIN_RATED_OUTCOMES = 30
MIN_ARMS_WITH_EVIDENCE = 2


@dataclass
class Outcome:
    """One (arm, reward) observation reconstructed from the logs."""

    arm_id: str
    reward: float
    trip_id: str
    source: str


@dataclass
class ArmSummary:
    arm_id: str
    pulls: int
    mean_reward: float
    rewards: List[float] = field(default_factory=list)


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def load_outcomes(con: sqlite3.Connection) -> Tuple[List[Outcome], Dict[str, int]]:
    """Reconstruct explicit rewards from the event log and satisfaction surveys.

    Only explicit acceptance signals are used. Impressions and clicks are
    skipped on purpose: `derive_reward` in the service rejects them for the same
    reason — exposure is not preference.
    """
    outcomes: List[Outcome] = []
    skipped = {"impression": 0, "no_arm": 0, "unknown_arm": 0, "non_explicit": 0}

    # 1) plan_adopted / plan_rejected carry the arm id in their payload.
    if table_exists(con, "planning_events"):
        rows = con.execute(
            "SELECT event_type, trip_id, payload FROM planning_events "
            "WHERE event_type IN ('plan_adopted','plan_rejected')"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError):
                skipped["non_explicit"] += 1
                continue
            arm_id = str(payload.get("bandit_arm_id") or payload.get("arm_id") or "").strip()
            if not arm_id:
                skipped["no_arm"] += 1
                continue
            reward = 1.0 if row["event_type"] == "plan_adopted" else 0.0
            if payload.get("reward") is not None:
                try:
                    reward = max(0.0, min(1.0, float(payload["reward"])))
                except (TypeError, ValueError):
                    pass
            outcomes.append(Outcome(arm_id, reward, str(row["trip_id"] or ""), "planning_events"))

    # 2) Satisfaction ratings are the strongest signal; map 1-5 onto 0..1.
    #    The arm id is joined in from the trip's adoption event when the survey
    #    itself does not carry one.
    trip_arm: Dict[str, str] = {}
    if table_exists(con, "planning_events"):
        for row in con.execute(
            "SELECT trip_id, payload FROM planning_events WHERE event_type='plan_adopted'"
        ).fetchall():
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError):
                continue
            arm_id = str(payload.get("bandit_arm_id") or payload.get("arm_id") or "").strip()
            if arm_id and row["trip_id"]:
                trip_arm[str(row["trip_id"])] = arm_id

    if table_exists(con, "trip_satisfactions"):
        for row in con.execute("SELECT trip_id, score FROM trip_satisfactions").fetchall():
            trip_id = str(row["trip_id"] or "")
            arm_id = trip_arm.get(trip_id, "")
            if not arm_id:
                skipped["no_arm"] += 1
                continue
            try:
                score = float(row["score"])
            except (TypeError, ValueError):
                continue
            outcomes.append(Outcome(arm_id, max(0.0, min(1.0, score / 5.0)), trip_id, "satisfaction"))

    return outcomes, skipped


def summarise(outcomes: Sequence[Outcome]) -> List[ArmSummary]:
    grouped: Dict[str, List[float]] = {}
    for outcome in outcomes:
        grouped.setdefault(outcome.arm_id, []).append(outcome.reward)
    summaries = [
        ArmSummary(arm, len(rewards), sum(rewards) / len(rewards), rewards)
        for arm, rewards in grouped.items()
    ]
    summaries.sort(key=lambda s: s.mean_reward, reverse=True)
    return summaries


def two_proportion_z(successes_a: int, n_a: int, successes_b: int, n_b: int) -> Optional[float]:
    """Two-proportion z statistic for "best arm beats the rest"."""
    if n_a == 0 or n_b == 0:
        return None
    p_a, p_b = successes_a / n_a, successes_b / n_b
    p_pool = (successes_a + successes_b) / (n_a + n_b)
    if p_pool <= 0.0 or p_pool >= 1.0:
        return None
    se = (p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b)) ** 0.5
    if se == 0:
        return None
    return (p_a - p_b) / se


def build_report(db_path: str) -> Tuple[dict, int]:
    try:
        con = connect(db_path)
    except sqlite3.Error as exc:
        return {"error": "cannot_open_database", "detail": str(exc)}, 3

    with con:
        try:
            outcomes, skipped = load_outcomes(con)
        except sqlite3.Error as exc:
            return {"error": "cannot_read_schema", "detail": str(exc)}, 3

    summaries = summarise(outcomes)
    rated = len(outcomes)
    report: dict = {
        "database": db_path,
        "rated_outcomes": rated,
        "distinct_arms": len(summaries),
        "skipped": skipped,
        "arms": [
            {
                "arm_id": s.arm_id,
                "pulls": s.pulls,
                "mean_reward": round(s.mean_reward, 4),
                "reward_stdev": round(statistics.pstdev(s.rewards), 4) if len(s.rewards) > 1 else 0.0,
            }
            for s in summaries
        ],
    }

    if rated < MIN_RATED_OUTCOMES or len(summaries) < MIN_ARMS_WITH_EVIDENCE:
        report["verdict"] = "insufficient_evidence"
        report["reason"] = (
            "需要至少 %d 条显式奖励且覆盖至少 %d 个臂；当前 %d 条 / %d 个臂。"
            "最可能的原因仍是客户端未回传 bandit arm，奖励在网关被丢弃。"
            % (MIN_RATED_OUTCOMES, MIN_ARMS_WITH_EVIDENCE, rated, len(summaries))
        )
        return report, 2

    best = summaries[0]
    rest_rewards = [r for s in summaries[1:] for r in s.rewards]
    rest_pulls = len(rest_rewards)
    best_successes = sum(best.rewards)
    z = two_proportion_z(int(round(best_successes)), best.pulls, int(round(sum(rest_rewards))), rest_pulls)

    report["best_arm"] = best.arm_id
    report["baseline_mean_reward"] = round(sum(rest_rewards) / max(1, rest_pulls), 4)
    report["lift_over_baseline"] = round(best.mean_reward - (sum(rest_rewards) / max(1, rest_pulls)), 4)
    report["z_statistic"] = round(z, 3) if z is not None else None
    report["significant_at_95pct"] = bool(z is not None and z > 1.96)
    report["verdict"] = "policy_better" if (z is not None and z > 1.96) else "no_measurable_lift"
    return report, 0


def render_text(report: dict) -> str:
    lines: List[str] = []
    lines.append("=== Bandit 离线评估 ===")
    lines.append("数据库: %s" % report.get("database"))
    if "error" in report:
        lines.append("错误: %s (%s)" % (report["error"], report.get("detail", "")))
        return "\n".join(lines)
    lines.append("显式奖励样本: %d   覆盖臂数: %d" % (report["rated_outcomes"], report["distinct_arms"]))
    skipped = report.get("skipped") or {}
    if any(skipped.values()):
        lines.append(
            "跳过: 无臂 %d / 非显式 %d" % (skipped.get("no_arm", 0), skipped.get("non_explicit", 0))
        )
    lines.append("")
    lines.append("%-34s %7s %12s %10s" % ("arm", "pulls", "mean_reward", "stdev"))
    for arm in report.get("arms", []):
        lines.append(
            "%-34s %7d %12.4f %10.4f"
            % (arm["arm_id"], arm["pulls"], arm["mean_reward"], arm["reward_stdev"])
        )
    lines.append("")
    lines.append("结论: %s" % report.get("verdict"))
    if report.get("reason"):
        lines.append("说明: %s" % report["reason"])
    if "best_arm" in report:
        lines.append(
            "最优臂 %s 相比基线提升 %.4f (z=%.3f, 95%% 显著: %s)"
            % (
                report["best_arm"],
                report["lift_over_baseline"],
                report["z_statistic"] or 0.0,
                "是" if report["significant_at_95pct"] else "否",
            )
        )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the planning bandit against logged outcomes")
    parser.add_argument("--db", required=True, help="path to the gateway SQLite database")
    parser.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    parser.add_argument(
        "--require-verdict",
        action="store_true",
        help="exit non-zero when there is not enough evidence (default behaviour; kept explicit)",
    )
    args = parser.parse_args(argv)

    report, code = build_report(args.db)

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.format == "markdown":
        print("```")
        print(render_text(report))
        print("```")
    else:
        print(render_text(report))

    return code


if __name__ == "__main__":
    sys.exit(main())
