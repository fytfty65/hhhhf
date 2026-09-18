#!/usr/bin/env python
"""规划质量评测 CLI（阶段 0）。

四种用法
--------
1) 自检（不需要任何服务/网络，验证"尺子"有区分度）：
       python tools/eval_plan_quality.py --self-check

2) 给真实规划输出打分（把每份规划 dump 成 <plans_dir>/<case_id>.json）：
       python tools/eval_plan_quality.py --plans work/eval-runs --out work/eval-report.md

3) 当门禁用（硬约束必须全过 + 平均分不低于阈值）：
       python tools/eval_plan_quality.py --plans work/eval-runs --min-score 65 --require-hard-gates

4) 长途用例的 horizon 判定（本次新增）
   用例里写了 `horizon` 的（分段天数 / 最少休整日 / 是否要求首轮分段生成），会额外跑一遍
   `core/long_trip.continuity_report` 与 `summarize_segments`：
   - 天数断档、越界天、某段住宿夜数不足 → 记为长途失败，**计入 `--require-hard-gates`**；
   - 跳段跨城、休整日不足、分段超预算 → 只做顾问（跨城本来就要坐车）；
   - 用例要求首轮分段生成（`horizon.first_round_required`）时，plan dump 里必须带
     `long_trip.first_round`（由 api 层下发），否则记为 `long_trip_first_round_missing`。

5) 不凭空编造（g_no_invention）+ 离线夹具
   报告里单列一节统计"写了具体数值却没有可核实来源"的节点（价格/评分/营业时间），
   逐条点名到节点与字段；默认只报告，需要当门禁时用 `--max-unsourced N`。
   离线夹具在 `tests/eval/fixtures/`（cases.json + plans/*.json），**跟着仓库走**：
   全新 clone、没有 Key、不联网也能 `python -m unittest tests.test_plan_eval` 验证评测器
   有区分度（干净方案 0 个无来源数值，编造方案被抓出来）。

原则
----
- **没跑的用例不算 0 分**，单独统计为"未运行"，避免用"没数据"冒充"质量差"。
- **未核实字段不当作通过**：硬约束里缺价格/开放时间的节点记 unverifiable 并单独计数。
- 报告同时落 markdown 与 JSON，便于人工审阅与后续接 CI。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from core.long_trip import continuity_report, summarize_segments  # noqa: E402
from core.plan_quality import DEFAULT_WEIGHTS, evaluate_plan, stability_score  # noqa: E402

DEFAULT_CASES = SERVICE_ROOT / "tests" / "eval" / "golden_cases.json"
REPO_ROOT = SERVICE_ROOT.parent
DEFAULT_CHUNK_DAYS = 7


def load_cases(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise ValueError(f"{path} 里没有 cases 数组")
    return cases


def find_plan_files(plans_dir: Path, case_id: str) -> List[Path]:
    """支持单次运行 <id>.json 与多次运行 <id>.1.json / <id>.2.json。"""
    if not plans_dir or not plans_dir.exists():
        return []
    single = plans_dir / f"{case_id}.json"
    numbered = sorted(plans_dir.glob(f"{case_id}.*.json"))
    if single.exists():
        return [single] + [p for p in numbered if p != single]
    return numbered


def long_trip_check(case: Dict[str, Any], plan: Any) -> Optional[Dict[str, Any]]:
    """长途用例的 horizon 判定（用例没写 horizon 就返回 None）。

    - 硬失败：天数断档 / 越界天 / 某段住宿夜数不足（计入硬门禁）；
    - 顾问：跳段跨城距离、休整日不足、分段花费超分配；
    - `horizon.first_round_required`：要求 plan dump 里带 `long_trip.first_round`
      （api 层下发的首轮分段生成结果），缺失或 `failed` 非空都记为失败 —— 这是
      "30 天不许假装一次排好"的机器化判定。
    """
    horizon = case.get("horizon")
    if not isinstance(horizon, Mapping):
        return None
    context = case.get("context") or {}
    chunk_days = int(horizon.get("segment_days") or DEFAULT_CHUNK_DAYS)
    check_context = {
        "days": context.get("days"),
        "budget": context.get("budget"),
        "expectations": {"min_rest_days": horizon.get("min_rest_days")},
    }
    continuity = continuity_report(plan, check_context, chunk_days)
    summary = summarize_segments(plan, check_context, chunk_days)
    failures: List[Dict[str, Any]] = [dict(item) for item in continuity["failures"]]

    first_round: Optional[Dict[str, Any]] = None
    if isinstance(plan, Mapping):
        raw_first_round = plan.get("long_trip")
        if isinstance(raw_first_round, Mapping):
            candidate = raw_first_round.get("first_round")
            if isinstance(candidate, Mapping):
                first_round = dict(candidate)
    if horizon.get("first_round_required"):
        if first_round is None:
            failures.append(
                {
                    "code": "long_trip_first_round_missing",
                    "detail": "这条用例要求首轮分段生成，但方案里没有 long_trip.first_round（api 层未下发）",
                }
            )
        elif first_round.get("failed"):
            failures.append(
                {
                    "code": "long_trip_first_round_failed",
                    "detail": "首轮分段生成有 %d 段没生成出来（第 %s 段）"
                    % (len(first_round.get("failed") or []), "、".join(str(i) for i in first_round.get("failed") or [])),
                }
            )

    return {
        "chunk_days": chunk_days,
        "ok": not failures,
        "failures": failures,
        "advisories": continuity["advisories"][:5],
        "segments": summary["segments"],
        "rest_days": continuity["rest_days"],
        "rest_days_required": continuity["rest_days_required"],
        "first_round_required": bool(horizon.get("first_round_required")),
        "first_round": first_round,
    }


def score_case(case: Dict[str, Any], plans: Sequence[Any]) -> Dict[str, Any]:
    context = case.get("context") or {}
    expectations = case.get("expectations") or {}
    signals = case.get("signals") or {}
    primary = evaluate_plan(context, plans[0], expectations=expectations, signals=signals)
    stability = stability_score(plans)
    truthfulness = (primary.get("dimensions") or {}).get("truthfulness") or {}
    unsourced = truthfulness.get("unsourced_claims") or []
    return {
        "id": case.get("id"),
        "title": case.get("title"),
        "request": case.get("request"),
        "runs": len(plans),
        "score": primary["score"],
        "verdict": primary["verdict"],
        "gate_passed": primary["gate"]["passed"],
        "gate_failures": primary["gate"]["failures"],
        "unverifiable_count": primary["gate"]["unverifiable_count"],
        "unverifiable": primary["gate"]["unverifiable"][:5],
        "dimensions": primary["dimensions"],
        "nodes": primary["nodes"],
        "stability": stability.get("value"),
        "long_trip": long_trip_check(case, plans[0]),
        # 不凭空编造（g_no_invention）：没有来源却写了具体数值的节点，一条都不许放过
        "no_invention": {
            "value": truthfulness.get("value"),
            "verified_nodes": truthfulness.get("verified_nodes"),
            "unsourced_count": truthfulness.get("unsourced_count", len(unsourced)),
            "unsourced_claims": unsourced[:5],
        },
    }


def build_report(cases: Sequence[Dict[str, Any]], plans_dir: Optional[Path]) -> Dict[str, Any]:
    scored: List[Dict[str, Any]] = []
    missing: List[str] = []
    for case in cases:
        case_id = str(case.get("id"))
        files = find_plan_files(plans_dir, case_id) if plans_dir else []
        if not files:
            missing.append(case_id)
            continue
        plans = [json.loads(path.read_text(encoding="utf-8")) for path in files]
        scored.append(score_case(case, plans))

    dimension_means: Dict[str, float] = {}
    if scored:
        for key in DEFAULT_WEIGHTS:
            values = [row["dimensions"][key]["value"] for row in scored if key in row["dimensions"]]
            dimension_means[key] = round(sum(values) / len(values), 3) if values else 0.0

    scores = [row["score"] for row in scored]
    long_trip_rows = [row for row in scored if row.get("long_trip")]
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cases_total": len(cases),
        "cases_scored": len(scored),
        "cases_missing": missing,
        "mean_score": round(sum(scores) / len(scores), 1) if scores else None,
        "min_score": min(scores) if scores else None,
        "max_score": max(scores) if scores else None,
        "gate_failure_cases": [row["id"] for row in scored if not row["gate_passed"]],
        "hard_gate_failures": sum(len(row["gate_failures"]) for row in scored),
        "unverifiable_total": sum(row["unverifiable_count"] for row in scored),
        "dimension_means": dimension_means,
        "stability_mean": (
            round(
                sum(row["stability"] for row in scored if row["stability"] is not None)
                / max(1, len([row for row in scored if row["stability"] is not None])),
                3,
            )
            if any(row["stability"] is not None for row in scored)
            else None
        ),
        "long_trip_cases": len(long_trip_rows),
        "long_trip_failures": sum(len(row["long_trip"]["failures"]) for row in long_trip_rows),
        "long_trip_failure_cases": [row["id"] for row in long_trip_rows if not row["long_trip"]["ok"]],
        "no_invention_mean": (
            round(
                sum(row["no_invention"]["value"] or 0.0 for row in scored) / len(scored),
                3,
            )
            if scored
            else None
        ),
        "unsourced_claims_total": sum(row["no_invention"]["unsourced_count"] or 0 for row in scored),
        "unsourced_cases": [row["id"] for row in scored if (row["no_invention"]["unsourced_count"] or 0) > 0],
        "cases": scored,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# 规划质量评测报告")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(f"- 用例：{report['cases_total']} 条，已评分 {report['cases_scored']} 条，未运行 {len(report['cases_missing'])} 条")
    if report["mean_score"] is not None:
        lines.append(f"- 总分：均值 **{report['mean_score']}**，最低 {report['min_score']}，最高 {report['max_score']}")
        lines.append(f"- 硬约束失败：**{report['hard_gate_failures']}** 处，涉及用例 {report['gate_failure_cases'] or '无'}")
        lines.append(f"- 「未核实」条目合计：{report['unverifiable_total']}（**不计入通过**，需人工/阶段 1 补数据）")
        if report.get("stability_mean") is not None:
            lines.append(f"- 多次运行稳定性（节点集合 Jaccard 均值）：{report['stability_mean']}")
    if report["cases_missing"]:
        lines.append("")
        lines.append(f"> 未运行用例（不按 0 分计）：{', '.join(report['cases_missing'])}")
    if report.get("dimension_means"):
        lines.append("")
        lines.append("## 维度均值（0-1）")
        lines.append("")
        lines.append("| 维度 | 权重 | 均值 |")
        lines.append("|---|---|---|")
        for key, value in report["dimension_means"].items():
            lines.append(f"| {key} | {DEFAULT_WEIGHTS.get(key, 0)} | {value} |")
    if report["cases"]:
        lines.append("")
        lines.append("## 逐用例")
        lines.append("")
        lines.append("| 用例 | 天数/节点 | 分数 | 判定 | 硬约束失败 | 未核实 | 稳定性 |")
        lines.append("|---|---|---|---|---|---|---|")
        for row in report["cases"]:
            lines.append(
                f"| {row['id']} | {row['nodes']} | {row['score']} | {row['verdict']} | "
                f"{len(row['gate_failures'])} | {row['unverifiable_count']} | {row['stability'] if row['stability'] is not None else '-'} |"
            )
        failures = [row for row in report["cases"] if row["gate_failures"]]
        if failures:
            lines.append("")
            lines.append("## 硬约束失败明细")
            for row in failures:
                lines.append("")
                lines.append(f"**{row['id']}**（{row['title']}）")
                for item in row["gate_failures"]:
                    lines.append(f"- `{item['code']}` {item['detail']}")
    long_trip_rows = [row for row in report["cases"] if row.get("long_trip")]
    if long_trip_rows:
        lines.append("")
        lines.append("## 长途（horizon 判定）")
        lines.append("")
        lines.append("| 用例 | 段数 | 休整日/要求 | 首轮分段生成 | 结论 |")
        lines.append("|---|---|---|---|---|")
        for row in long_trip_rows:
            trip = row["long_trip"]
            first = trip.get("first_round")
            if first:
                first_text = "生成 %d 段%s" % (
                    len(first.get("generated") or []),
                    ("，失败 %d 段" % len(first.get("failed") or [])) if first.get("failed") else "",
                )
            elif trip.get("first_round_required"):
                first_text = "**未下发（用例要求了首轮分段生成）**"
            else:
                first_text = "用例未要求"
            lines.append(
                f"| {row['id']} | {len(trip['segments'])} | {trip['rest_days']}/{trip['rest_days_required']} | "
                f"{first_text} | {'通过' if trip['ok'] else '失败'} |"
            )
        trip_failures = [row for row in long_trip_rows if row["long_trip"]["failures"]]
        if trip_failures:
            lines.append("")
            lines.append("### 长途失败明细")
            for row in trip_failures:
                lines.append("")
                lines.append(f"**{row['id']}**")
                for item in row["long_trip"]["failures"]:
                    lines.append(f"- `{item['code']}` {item['detail']}")
        advisories = [(row["id"], text) for row in long_trip_rows for text in row["long_trip"]["advisories"]]
        if advisories:
            lines.append("")
            lines.append("### 长途顾问（不判死：跨城本来就要坐车）")
            for case_id, text in advisories:
                lines.append(f"- **{case_id}**：{text}")
    if report["cases"]:
        lines.append("")
        lines.append("## 不凭空编造（g_no_invention）")
        lines.append("")
        lines.append(
            f"- 均值 **{report.get('no_invention_mean')}**（1.0 = 每个带数值的节点都有可核实来源）；"
            f"无来源却写了具体数值的节点合计 **{report.get('unsourced_claims_total')}** 个"
        )
        if report.get("unsourced_cases"):
            lines.append(f"- 涉及用例：{', '.join(report['unsourced_cases'])}")
        offenders = [row for row in report["cases"] if (row["no_invention"]["unsourced_count"] or 0) > 0]
        for row in offenders:
            lines.append("")
            lines.append(f"**{row['id']}**（{row['title']}）：{row['no_invention']['unsourced_count']} 个节点")
            for claim in row["no_invention"]["unsourced_claims"]:
                lines.append(f"- 「{claim['name']}」的 {'、'.join(claim['fields'])} 没有可核实来源")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 自检：用内置的"好/坏"两份规划证明尺子有区分度（不需要服务与网络）
# --------------------------------------------------------------------------
def _node(name: str, day: int, time: str, lnglat, node_type: str, cost: str, open_time: str,
          verified: bool = True, **extra: Any) -> Dict[str, Any]:
    source = "amap" if verified else "seed_template"
    return {
        "day": day, "name": name, "location": name, "time": time, "lnglat": list(lnglat),
        "type": node_type, "cost_estimate": cost, "open_time": open_time,
        "rating": "4.6" if verified else "暂无供应商数据",
        "data_sources": {"cost_estimate": source, "open_time": source, "rating": source},
        "estimated": not verified,
        **extra,
    }


def self_check() -> int:
    context = {"city": "西安", "days": 2, "budget": 2000, "preferences": {"pace": "relaxed", "interest": ["博物馆", "美食"]}}
    expectations = {"must_have": ["酒店", "餐"], "min_nodes_per_day": 2, "max_nodes_per_day": 4}
    good = {"route": [
        _node("钟楼附近酒店", 1, "09:00", (108.94, 34.26), "住宿", "¥300", "00:00-23:59"),
        _node("陕西历史博物馆", 1, "10:30", (108.95, 34.22), "博物馆", "¥0", "09:00-17:00"),
        _node("回民街小吃", 1, "12:30", (108.94, 34.26), "餐饮", "¥60", "10:00-22:00"),
        _node("大雁塔", 1, "15:00", (108.96, 34.22), "文化", "¥50", "08:00-18:00"),
        _node("城墙南门", 2, "09:30", (108.94, 34.25), "文化", "¥54", "08:00-20:00"),
        _node("永兴坊美食", 2, "12:00", (108.97, 34.27), "餐饮", "¥70", "10:00-22:00"),
    ]}
    bad = {"route": [
        _node("钟楼附近酒店", 1, "07:00", (108.94, 34.26), "住宿", "¥900", "00:00-23:59", verified=False),
        _node("兵马俑", 1, "07:30", (109.28, 34.38), "文化", "¥1200", "09:00-17:00", verified=False),
        _node("兵马俑", 1, "07:40", (109.28, 34.38), "文化", "¥1200", "09:00-17:00", verified=False),
        _node("华山", 1, "08:00", (110.09, 34.49), "文化", "¥1600", "07:00-16:00", verified=False),
    ]}
    good_report = evaluate_plan(context, good, expectations=expectations)
    bad_report = evaluate_plan(context, bad, expectations=expectations)
    print("=== 自检：好方案 ===")
    print(f"  score={good_report['score']}  verdict={good_report['verdict']}  硬约束失败={len(good_report['gate']['failures'])}  未核实={good_report['gate']['unverifiable_count']}")
    for item in good_report["gate"]["failures"]:
        print(f"    - {item['code']}: {item['detail']}")
    print("=== 自检：坏方案 ===")
    print(f"  score={bad_report['score']}  verdict={bad_report['verdict']}  硬约束失败={len(bad_report['gate']['failures'])}  未核实={bad_report['gate']['unverifiable_count']}")
    for item in bad_report["gate"]["failures"]:
        print(f"    - {item['code']}: {item['detail']}")
    ok = (
        good_report["gate"]["passed"]
        and not bad_report["gate"]["passed"]
        and good_report["score"] - bad_report["score"] >= 25
    )
    print("")
    print(f"区分度检查：好方案过门禁={good_report['gate']['passed']}，坏方案过门禁={bad_report['gate']['passed']}，"
          f"分差={round(good_report['score'] - bad_report['score'], 1)}（要求 >= 25）")
    print("SELF-CHECK " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="规划质量评测（阶段 0）")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="金标需求集 JSON")
    parser.add_argument("--plans", type=Path, default=None, help="规划 dump 目录：<case_id>.json 或 <case_id>.<n>.json")
    parser.add_argument("--out", type=Path, default=None, help="markdown 报告输出路径（默认 work/eval-report.md）")
    parser.add_argument("--json-out", type=Path, default=None, help="JSON 报告输出路径（默认与 --out 同名 .json）")
    parser.add_argument("--min-score", type=float, default=None, help="平均分低于该值即失败（门禁模式）")
    parser.add_argument("--require-hard-gates", action="store_true", help="任何硬约束失败即失败（门禁模式）")
    parser.add_argument(
        "--max-unsourced",
        type=int,
        default=None,
        help="无来源却写了具体数值的节点数超过该值即失败（g_no_invention 门禁，默认只报告不判死）",
    )
    parser.add_argument("--self-check", action="store_true", help="用内置样例验证评分器有区分度")
    args = parser.parse_args(argv)

    if args.self_check:
        return self_check()

    cases = load_cases(args.cases)
    report = build_report(cases, args.plans)
    out_path = args.out or (REPO_ROOT / "work" / "eval-report.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(report)
    out_path.write_text(markdown, encoding="utf-8")
    json_path = args.json_out or out_path.with_suffix(".json")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(markdown)
    print(f"\n报告已写入：{out_path}\nJSON：{json_path}")

    exit_code = 0
    if args.require_hard_gates and report["hard_gate_failures"]:
        print(f"[门禁] 硬约束失败 {report['hard_gate_failures']} 处 -> FAIL")
        exit_code = 1
    if args.require_hard_gates and report.get("long_trip_failures"):
        print(
            f"[门禁] 长途（horizon）失败 {report['long_trip_failures']} 处 -> FAIL"
            f"（涉及用例 {report.get('long_trip_failure_cases')}）"
        )
        exit_code = 1
    if args.max_unsourced is not None and (report.get("unsourced_claims_total") or 0) > args.max_unsourced:
        print(
            f"[门禁] 无来源却写了具体数值的节点 {report['unsourced_claims_total']} 个 > {args.max_unsourced} -> FAIL"
            f"（涉及用例 {report.get('unsourced_cases')}）"
        )
        exit_code = 1
    if args.min_score is not None:
        if report["mean_score"] is None:
            print("[门禁] 没有任何可评分用例 -> FAIL")
            exit_code = 1
        elif report["mean_score"] < args.min_score:
            print(f"[门禁] 平均分 {report['mean_score']} < {args.min_score} -> FAIL")
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
