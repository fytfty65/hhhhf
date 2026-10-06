"""人工抽查表：把一份（或两份）真实方案摊成**能逐条打分**的表格（2026-10-05）。

为什么要它：用户的第一优先级是「地点好不好」，而这件事**机器判不了**（需要外部知识）。
能自动判定的部分已经进了 `core/place_quality.py`（是不是真实检索到的、名字像不像玩点、
方案周不周全）；剩下的必须由人读。所以这里不发明分数，而是把"人判断所需要的信息"
一次性摆齐，并留出打分栏：

    · 每个节点：第几天/几点、名称、类别、**价格与出处（可点开核对）**、与前一站的距离、
      是"真实检索到的"还是"模型凭记忆写的"、有没有被标成可疑；
    · 机器已经抓到的问题：可疑名字、待人工确认的泛称、周全性没过的那几项、披露缺口；
    · 打分栏（两份方案时并排）：地点好不好(1–5) / 是否贴合诉求(是/否) / 备注。

用法：
    # 单份
    python tools/plan_review_sheet.py --plan work/plans/库尔勒-1.json
    # 并排对照两份（例如 qwen-plus vs qwen-turbo）
    python tools/plan_review_sheet.py --plan work/plans/plus-库尔勒.json --compare work/plans/turbo-库尔勒.json
    # 批量：把目录下所有 plan json 都摊成一份表
    python tools/plan_review_sheet.py --dir work/plans

输出写到 `--out`（默认 work/plan-review-sheet.md），同时把摘要打到 stdout。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent
sys.path.insert(0, str(SERVICE_ROOT))

from core.place_quality import (  # noqa: E402
    _is_lodging,
    is_real_retrieval,
    looks_like_a_visitable_place,
    needs_human_review,
    score_completeness,
    score_place_quality,
)
from core.plan_quality import _coarse_category, haversine_km, node_day, node_lnglat, node_name  # noqa: E402

CATEGORY_LABEL = {
    "scenic": "自然/景区",
    "cultural": "人文",
    "food": "餐饮",
    "hotel": "住宿",
    "shopping": "购物",
    "other": "其它",
}


def load_plan(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    # 兼容两种落盘形态：整个 final_data，或 {"final_data": {...}}
    if isinstance(payload, Mapping) and isinstance(payload.get("final_data"), Mapping):
        return dict(payload["final_data"])
    return dict(payload) if isinstance(payload, Mapping) else {}


def plan_title(plan: Mapping[str, Any], fallback: str) -> str:
    city = str(plan.get("city") or "").strip()
    days = plan.get("trip_days") or plan.get("days")
    intent = str(plan.get("intent") or "").strip()
    parts = [item for item in (city, f"{days} 天" if days else "", intent) if item]
    return "｜".join(parts) or fallback


def _price_cell(node: Mapping[str, Any]) -> str:
    price = str(node.get("cost_estimate") or node.get("price") or "").strip() or "未取到"
    sources = node.get("data_sources") if isinstance(node.get("data_sources"), Mapping) else {}
    source = str((sources or {}).get("cost_estimate") or "").strip() or "—"
    url = str(node.get("price_source_url") or "").strip()
    excerpt = str(node.get("price_excerpt") or "").strip()
    cell = f"{price}（{source}）"
    if excerpt:
        cell += f" 原文片段：{excerpt[:40]}"
    if url:
        cell += f" [来源]({url})"
    return cell


def _day_rows(plan: Mapping[str, Any]) -> Dict[int, List[Mapping[str, Any]]]:
    grouped: Dict[int, List[Mapping[str, Any]]] = {}
    route = plan.get("route") if isinstance(plan.get("route"), list) else []
    for index, node in enumerate(route):
        if not isinstance(node, Mapping):
            continue
        grouped.setdefault(node_day(node, index), []).append(node)
    return grouped


def _context_of(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """从方案自身推出评分上下文（天数/距离容忍度）——方案里带着这些事实，不必再问一遍。"""
    return {
        "days": plan.get("trip_days") or plan.get("days") or 1,
        "distance_tolerance": (plan.get("facts") or {}).get("constraints", {}).get("distance_tolerance", {}).get("value")
        if isinstance(plan.get("facts"), Mapping)
        else "normal",
    }


def render_single(plan: Mapping[str, Any], label: str) -> List[str]:
    route = plan.get("route") if isinstance(plan.get("route"), list) else []
    place = score_place_quality(plan, candidates=None)
    complete = score_completeness(_context_of(plan), plan)
    lines = [
        f"## {plan_title(plan, label)}",
        "",
        f"- 节点 {len(route)} 个｜地点质量（可判定部分）{place['value']:.2f}"
        f"（真实检索 {place['real_retrieval_ratio']:.0%} / 像玩点 {place['visitable_ratio']:.0%}）",
        f"- 方案周全（可判定部分）{complete['value']:.2f}｜未过项："
        + ("、".join(complete["failed_checks"]) or "无"),
    ]
    if place.get("suspect_names"):
        lines.append(
            "- ⚠️ 可疑名字：" + "；".join(f"{item['name']}（{item['why']}）" for item in place["suspect_names"])
        )
    if place.get("review_required"):
        lines.append(
            "- 👀 待人工确认：" + "；".join(f"{item['name']}（{item['why']}）" for item in place["review_required"])
        )
    lines.append("")
    lines.append("| 天 | 时间 | 地点 | 类别 | 价格（出处） | 距上一站 | 来源 | 请你打分 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for day in sorted(_day_rows(plan)):
        previous = None
        for node in _day_rows(plan)[day]:
            coord = node_lnglat(node)
            distance = "—"
            if previous and coord:
                km = haversine_km(previous, coord)
                if km == km:
                    distance = f"{km:.1f}km"
            if coord:
                previous = coord
            real = "真实检索" if is_real_retrieval(node) else "模型知识？"
            looks_ok, why = looks_like_a_visitable_place(node)
            if not looks_ok:
                real += f"⚠️{why}"
            review_why = needs_human_review(node)
            if review_why:
                real += "👀"
            lines.append(
                f"| D{day} | {str(node.get('time') or '—')[:5]} | {node_name(node)} | "
                f"{CATEGORY_LABEL.get(_coarse_category(node), _coarse_category(node))} | {_price_cell(node)} | "
                f"{distance} | {real} | 地点好不好(1-5)：__ ｜贴合诉求：是/否 ｜备注： |"
            )
    lines.append("")
    return lines


def render_compare(left: Mapping[str, Any], right: Mapping[str, Any], left_label: str, right_label: str) -> List[str]:
    lines = [
        f"## 并排对照：{left_label} vs {right_label}",
        "",
        "> 说明：自动评分只能判「结构/真实性/周全性」，**地点好不好要你读**。",
        "> 两列是两份方案里同一天的节点，用来快速看「换了模型之后地点变了没、变好还是变差」。",
        "",
    ]
    left_days = _day_rows(left)
    right_days = _day_rows(right)
    for day in sorted(set(left_days) | set(right_days)):
        lines.append(f"### 第 {day} 天")
        lines.append("")
        lines.append(f"| {left_label} | {right_label} |")
        lines.append("|---|---|")
        left_names = [node_name(node) for node in left_days.get(day, [])]
        right_names = [node_name(node) for node in right_days.get(day, [])]
        for index in range(max(len(left_names), len(right_names))):
            left_name = left_names[index] if index < len(left_names) else ""
            right_name = right_names[index] if index < len(right_names) else ""
            mark = "　" if left_name == right_name else "←不同"
            lines.append(f"| {left_name} {mark} | {right_name} |")
        lines.append("")
    left_place = score_place_quality(left)
    right_place = score_place_quality(right)
    left_complete = score_completeness(_context_of(left), left)
    right_complete = score_completeness(_context_of(right), right)
    lines.extend([
        "### 自动指标对照",
        "",
        f"| 指标 | {left_label} | {right_label} |",
        "|---|---|---|",
        f"| 节点数 | {len(left.get('route') or [])} | {len(right.get('route') or [])} |",
        f"| 地点质量（可判定） | {left_place['value']:.2f} | {right_place['value']:.2f} |",
        f"| 真实检索比例 | {left_place['real_retrieval_ratio']:.0%} | {right_place['real_retrieval_ratio']:.0%} |",
        f"| 方案周全（可判定） | {left_complete['value']:.2f} | {right_complete['value']:.2f} |",
        f"| 周全未过项 | {'、'.join(left_complete['failed_checks']) or '无'} | {'、'.join(right_complete['failed_checks']) or '无'} |",
        "",
    ])
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="人工抽查表：把真实方案摊成可打分的表格")
    parser.add_argument("--plan", help="单份方案 JSON（或整个 final_data）")
    parser.add_argument("--compare", help="并排对照的另一份方案 JSON")
    parser.add_argument("--dir", help="批量：把目录下所有 *.json 都摊成表")
    parser.add_argument("--out", default=str(REPO_ROOT / "work" / "plan-review-sheet.md"))
    parser.add_argument("--label", default="方案 A")
    parser.add_argument("--compare-label", default="方案 B")
    args = parser.parse_args(argv)

    lines = [
        "# 人工抽查表（地点好不好 / 方案周全 / 个性化）",
        "",
        "生成时间：" + __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "",
        "打分口径（建议）：地点好不好 1=不值得去、3=一般、5=很值得；贴合诉求按用户原话逐条看。",
        "",
    ]
    if args.plan and args.compare:
        left = load_plan(Path(args.plan))
        right = load_plan(Path(args.compare))
        lines.extend(render_compare(left, right, args.label, args.compare_label))
    elif args.plan:
        lines.extend(render_single(load_plan(Path(args.plan)), args.label))
    elif args.dir:
        for path in sorted(Path(args.dir).glob("*.json")):
            lines.extend(render_single(load_plan(path), path.stem))
    else:
        parser.print_help()
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"已写入 {out_path}（{len(lines)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
