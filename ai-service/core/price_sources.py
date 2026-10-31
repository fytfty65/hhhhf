"""价格来源与归总：把"检索到的价格"结构化、标来源、合并回方案节点。

现状（为什么要这一层）
--------------------
`api/agent.py` 已有 `get_real_time_web_price()`：用 DuckDuckGo 抓 2 条搜索摘要，返回**一段文本**
（失败就是"未抓取到外网实时价格"）。它只作为给 LLM 的上下文，**没有**：
① 结构化（无法参与预算计算）② 逐节点（一次调用只给"城市+大类"）③ 来源标注（用户看不出可信度）
④ 合并回方案（节点上的 `cost_estimate` 仍是"暂无供应商数据"）。

本模块补齐"归总"这一段（纯函数、可离线测）：
- `pending_price_targets()`：只挑**缺价格**的节点去查（避免每次全量检索）；
- `merge_observations()`：把检索结果按**来源优先级**合并回节点，**弱来源不覆盖强来源**；
- `price_coverage()`：给出已验证/估算/未取到的比例，供报告与前端展示；
- 来源分级与 `budget_planner` 的三级价格模型一致：`provider/amap/official` = 可核实；
  `web/estimate` = **估算（必须标明）**；没有 = 未取到。

红线：**绝不为了让预算好看而把估算当已验证**；弱来源不覆盖强来源；过期观测不采用。
"""

from __future__ import annotations

import re
import time
import urllib.parse
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .candidate_index import intent_of, normalize_text
from .plan_quality import cost_of, node_name, nodes_of

# 来源可信度：数字越大越可信（强来源不打败弱来源）
# `web_page` = **白名单里的官方/政府页面正文**（2026-10-05 用户口径：
#   "把 gov/景区官网页面（白名单里的）视作可核实来源"）。
#   它不等于"任意网页"：`web` 仍然是搜索结果摘要、一律估算。
# `rollinggo` = **供应商只读 MCP 的实时房价**（2026-10-05 实测：`searchHotels` 返回
#   "1晚总价：131CNY" + 可点开的 bookingUrl）。它与 provider/vendor 同级（真供应商接口），
#   必须在这里登记 —— 漏登记会让它 rank=0：既覆盖不了已有的网摘价，
#   合并回去还会被标成"估算"（测试抓到过：`rollinggo` 曾在 plan_quality 里而不是这里）。
SOURCE_RANK: Dict[str, int] = {
    "provider": 4,
    "vendor": 4,
    "official": 4,
    "web_page": 4,
    "rollinggo": 4,
    "amap": 3,
    # `platform` = **票务平台**（携程/去哪儿等）页面或摘要里的票价（2026-10-07 用户放开数据源后加的）。
    # 它**不是官方**，所以不进 `VERIFIED_SOURCES`（不冒充"可核实"）；但它**有出处**（带 URL 与原文片段），
    # 所以也不能当"没出处的网摘"处理 —— 方案里按「票务平台价（非官方）」如实标明。
    "platform": 3,
    "web": 2,
    "estimate": 1,
    "llm_estimate": 1,
    "": 0,
    "unavailable": 0,
}
# 可核实来源：供应商/官方接口 + **白名单官方页面正文** + 供应商只读 MCP
VERIFIED_SOURCES = {"provider", "vendor", "official", "amap", "web_page", "rollinggo"}
# 「有出处」的来源（可核实 + 票务平台）：判"这句话有没有出处"用它，判"算不算可核实"用上面那个
SOURCED_SOURCES = VERIFIED_SOURCES | {"platform"}


def source_rank(source: Any) -> int:
    return SOURCE_RANK.get(str(source or "").strip().lower(), 0)


def is_verified_source(source: Any) -> bool:
    return str(source or "").strip().lower() in VERIFIED_SOURCES


def make_observation(name: str, value: Optional[float], source: str, retrieved_at: Optional[float] = None) -> Dict[str, Any]:
    """一条价格观测。`value=None` 表示"查了但没查到"，同样要如实记录。"""
    return {
        "name": str(name or "").strip(),
        "value": float(value) if isinstance(value, (int, float)) else None,
        "source": str(source or "").strip().lower(),
        "retrieved_at": float(retrieved_at if retrieved_at is not None else time.time()),
    }


def _current_price_source(node: Mapping[str, Any]) -> str:
    raw_sources = node.get("data_sources")
    sources: Mapping[str, Any] = raw_sources if isinstance(raw_sources, Mapping) else {}
    for field in ("cost_estimate", "cost", "price"):
        if field in sources:
            return str(sources.get(field) or "").strip().lower()
    return ""


def price_coverage(plan: Any) -> Dict[str, Any]:
    """方案的价格覆盖情况：已验证 / 估算 / 未取到（比例 + 节点名单，便于"还要查哪几个"）。"""
    verified: List[str] = []
    estimated: List[str] = []
    unknown: List[str] = []
    for node in nodes_of(plan):
        name = node_name(node)
        value = cost_of(node)
        source = _current_price_source(node)
        if value is None:
            unknown.append(name)
        elif is_verified_source(source):
            verified.append(name)
        else:
            estimated.append(name)
    total = len(verified) + len(estimated) + len(unknown)
    return {
        "total": total,
        "verified": verified,
        "estimated": estimated,
        "unknown": unknown,
        "verified_ratio": round(len(verified) / total, 3) if total else 0.0,
        "unknown_ratio": round(len(unknown) / total, 3) if total else 0.0,
    }


def unsourced_price_claims(plan: Any, limit: int = 20) -> List[Dict[str, Any]]:
    """**有价格数字、但出处不可核实**的节点（"有数字没出处"）。

    为什么单独挑出来（2026-10-05 抽查表上发现的真实形态）：喀什 15 个节点里有 5 个是
    `¥206（source=web）`——数字来自搜索摘要（别人转载的页面），点开也不是官方报价。
    这类节点原来的取价步骤**根本不会再看它**（`pending_price_targets` 只挑 `value is None`），
    但它恰恰最值得再查一次：一旦命中白名单官方页面，来源就从 `web`（估算）
    变成 `web_page`（可核实）—— 同一趟请求里，真实度是"不可核实 → 可核实"的翻转。

    返回 `[{name, intent, current_value, current_source}]`（与 pending 清单同形状）。
    """
    claims: List[Dict[str, Any]] = []
    for node in nodes_of(plan):
        value = cost_of(node)
        if value is None:
            continue
        source = _current_price_source(node)
        if is_verified_source(source):
            continue
        claims.append(
            {
                "name": node_name(node),
                "intent": intent_of(node),
                "current_value": value,
                "current_source": source or "missing",
            }
        )
        if len(claims) >= max(1, int(limit)):
            break
    return claims


def pending_price_targets(
    plan: Any,
    limit: int = 20,
    include_estimated: bool = False,
    include_unsourced: bool = False,
) -> List[Dict[str, Any]]:
    """挑出**需要去查价格**的节点（默认只挑完全没有价格的）。

    返回按意图分组用的清单：`[{name, intent, current_source, current_value}]`，
    调用方据此逐节点检索（而不是像现在这样一次只查"城市+大类"）。

    `include_unsourced=True`：把"有数字但出处不可核实"的节点**排在前面**先查。
    取价预算有限（默认 12 个点 / 约 90s），优先顺序本身就是一次价值判断：
    给空白填一个网摘估算价 vs 把已有的网摘价升级成官方页面价（可核实）——
    后者能翻转真实度，且用户已经能看见那个数字、更需要知道它算不算数。
    """
    targets: List[Dict[str, Any]] = []
    if include_unsourced:
        targets.extend(unsourced_price_claims(plan, limit=max(1, int(limit))))
    for node in nodes_of(plan):
        if len(targets) >= max(1, int(limit)):
            break
        value = cost_of(node)
        source = _current_price_source(node)
        if value is not None and not include_estimated:
            continue
        if value is not None and is_verified_source(source):
            continue
        name = node_name(node)
        if any(item.get("name") == name for item in targets):
            continue
        targets.append(
            {
                "name": name,
                "intent": intent_of(node),
                "current_value": value,
                "current_source": source or "missing",
            }
        )
    return targets


def pending_price_report(plan: Any, limit: int = 6) -> Dict[str, Any]:
    """给用户的"需要你确认"清单：哪些节点没价格、**为什么**、去哪里能核。

    为什么单独做这一层：用户看到"未取到 73%"只会一脸问号。所以要逐条说清：
    - 住宿：高德对酒店普遍不返回房价 → 明确写"高德不提供房价，需要你确认"，
      而不是笼统的"暂无供应商数据"；
    - 门票/其它：没有可核实报价 → "没有可核实报价，需要你确认"；
    并给出可以点开核价的入口（高德 marker 深链 / 关键词搜索），用户自己 30 秒能确认。
    """
    pending: List[Dict[str, Any]] = []
    for node in nodes_of(plan):
        value = cost_of(node)
        source = _current_price_source(node)
        if value is not None and is_verified_source(source):
            continue  # 已经是可核实价格，不用麻烦用户
        if value is not None and source in {"amap", "official", "provider", "vendor", "ticketing", "12306"}:
            continue
        name = node_name(node)
        if not name:
            continue
        kind = intent_of(node)
        is_lodging = kind == "hotel"
        reason = (
            "高德不提供房价，需要你确认"
            if is_lodging
            else "没有可核实报价，需要你确认"
        )
        # 已经有**联网估算价**的节点：不是"没有报价"，而是"估算价、来源可核对"——
        # 这时把出处链接直接给用户（他点开就能看到原始页面/搜索结果）。
        estimated_url = str(node.get("price_source_url") or "").strip()
        if value is not None and estimated_url:
            reason = "这是联网估算价（不是官方报价），可点开来源核对"
        url = estimated_url or str(node.get("amap_url") or "").strip()
        if not url:
            query = urllib.parse.quote(name)
            url = f"https://www.amap.com/search?query={query}"
        pending.append({
            "name": name,
            "kind": kind or "other",
            "reason": reason,
            "url": url,
            "estimated": bool(node.get("estimated")),
            "value": value,
        })
        if len(pending) >= max(1, int(limit)):
            break
    lodging = sum(1 for item in pending if item["kind"] == "hotel")
    others = len(pending) - lodging
    if not pending:
        summary = "价格都拿到了可核实来源，没有需要你确认的。"
    else:
        parts = []
        if lodging:
            parts.append(f"{lodging} 个住宿节点高德不提供房价")
        if others:
            parts.append(f"{others} 个节点没有可核实报价")
        summary = f"{len(pending)} 项需要你确认：" + "；".join(parts) + "。点右侧入口可直接去核价。"
    return {"pending": pending, "total": len(pending), "lodging": lodging, "summary": summary}


def merge_observations(
    plan: Any,
    observations: Sequence[Mapping[str, Any]],
    max_age_seconds: Optional[float] = 6 * 3600,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """把价格观测合并回方案节点（不修改传入 plan）。

    规则（严格口径）：
    - 名称匹配（规范化后包含或相等）才合并；匹配不上如实进 `unmatched`，不瞎猜；
    - **弱来源不覆盖强来源**：已有 `amap` 价格时，`web` 观测只记录不覆盖；
    - 观测过期（超过 `max_age_seconds`）不采用，进 `stale`；
    - `value=None`（查了没查到）不改动节点，只进 `missed`，供"还需补价"清单使用。
    """
    now = float(now if now is not None else time.time())
    nodes = [dict(node) for node in nodes_of(plan)]
    updated: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    unmatched: List[str] = []
    stale: List[str] = []
    missed: List[str] = []

    def match_index(name: str) -> Optional[int]:
        key = normalize_text(name)
        if not key:
            return None
        for index, node in enumerate(nodes):
            target = normalize_text(node_name(node))
            if not target:
                continue
            if key == target or key in target or target in key:
                return index
        return None

    for observation in observations:
        name = str(observation.get("name") or "").strip()
        index = match_index(name)
        if index is None:
            unmatched.append(name)
            continue
        retrieved_at = observation.get("retrieved_at")
        if max_age_seconds is not None and isinstance(retrieved_at, (int, float)):
            if now - float(retrieved_at) > float(max_age_seconds):
                stale.append(name)
                continue
        value = observation.get("value")
        source = str(observation.get("source") or "").strip().lower()
        node = nodes[index]
        if value is None:
            missed.append(name)
            continue

        current_value = cost_of(node)
        current_source = _current_price_source(node)
        if current_value is not None and source_rank(current_source) >= source_rank(source):
            skipped.append({"name": name, "kept": current_source or "missing", "dropped": source or "missing"})
            continue

        new_node = dict(node)
        new_node["cost_estimate"] = f"¥{float(value):g}"
        # 票务平台价**不是官方**，必须在用户看到的那一行里说明（2026-10-07 用户放开数据源时
        # 定的口径）：只写「¥38.5」会被读成"官方票价"，而它来自携程/去哪儿的页面。
        if source == "platform":
            new_node["cost_estimate"] = f"¥{float(value):g}（票务平台价，非官方）"
            new_node["price_source_label"] = "票务平台价，非官方"
        sources = dict(new_node.get("data_sources") or {})
        sources["cost_estimate"] = source or "unavailable"
        new_node["data_sources"] = sources
        # 只有"可核实来源"才取消 estimated 标记
        new_node["estimated"] = not is_verified_source(source)
        # 出处（URL + 抓取时间 + 原文片段）：估算价的关键配套 —— 用户能自己点开核对，
        # 而不是只看到一个数字（用户原话："不能直接打开官方页面看看吗"）。
        url = str(observation.get("source_url") or "").strip()
        if url:
            new_node["price_source_url"] = url
        # ⚠️ 抓取时间**不能挂在 `if url` 里面**（2026-10-08 实测修）：
        #    观测时间与"有没有 URL"是两件事 —— 高德/供应商接口给价时没有网页 URL，
        #    但**抓取时间是真实存在的**。原来写在 if 里，这些价的时间戳就丢了，
        #    节点只能落到"估算"、无法按新鲜度判定（"缺观测时间≠新鲜"）。
        retrieved_at = observation.get("retrieved_at")
        if retrieved_at:
            new_node["price_retrieved_at"] = float(retrieved_at)
        elif value is not None:
            # 观测本身产生了"现在"这个时间点，但上游没给就**不编**一个精确值，
            # 只记到日期粒度，避免伪造秒级精度。
            new_node["price_retrieved_at"] = float(now)
        excerpt = str(observation.get("excerpt") or "").strip()
        if excerpt:
            new_node["price_excerpt"] = excerpt[:160]
        nodes[index] = new_node
        updated.append({
            "name": name,
            "value": float(value),
            "source": source or "unavailable",
            "source_url": url,
        })

    new_plan: Any = dict(plan) if isinstance(plan, Mapping) else {"route": nodes}
    new_plan["route"] = nodes
    return {
        "plan": new_plan,
        "updated": updated,
        "skipped": skipped,
        "unmatched": unmatched,
        "stale": stale,
        "missed": missed,
        "coverage": price_coverage(new_plan),
    }


_PRICE_PATTERN = re.compile(r"(?:¥|￥|价格|票价|均价|每晚|人均|约)?\s*([0-9]{2,6}(?:\.[0-9]{1,2})?)\s*(?:元|块|RMB)?", re.I)


def parse_price_from_text(text: Any) -> Optional[float]:
    """从检索文本里抠出第一个像价格的数量（2-6 位数字）。

    只做"取数"，不判断真假：来源标记由调用方给定（网络报价 → source='web' → 仍属**估算**，
    必须标明，绝不当作已验证）。
    """
    raw = str(text or "")
    if not raw or "未抓取" in raw or "未获取" in raw:
        return None
    match = _PRICE_PATTERN.search(raw)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def summarize_merge(result: Mapping[str, Any]) -> str:
    """给人看的合并结果说明（前端与日志共用，避免各写一套文案）。"""
    parts: List[str] = []
    updated = result.get("updated") or []
    if updated:
        verified = [item for item in updated if is_verified_source(item.get("source"))]
        part = f"补上 {len(updated)} 个价格"
        if verified:
            part += f"（其中 {len(verified)} 个来自可核实来源）"
        parts.append(part)
    if result.get("skipped"):
        parts.append(f"{len(result['skipped'])} 个已有更可信来源，未覆盖")
    if result.get("missed"):
        parts.append(f"{len(result['missed'])} 个查了没查到（保持未核实）")
    if result.get("stale"):
        parts.append(f"{len(result['stale'])} 个观测已过期，未采用")
    if result.get("unmatched"):
        parts.append(f"{len(result['unmatched'])} 个没匹配到方案节点")
    coverage = result.get("coverage") or {}
    if coverage:
        parts.append(
            f"价格覆盖：可核实 {coverage.get('verified_ratio', 0) * 100:.0f}% / "
            f"未取到 {coverage.get('unknown_ratio', 0) * 100:.0f}%"
        )
    return "；".join(parts) if parts else "没有可合并的价格观测"
