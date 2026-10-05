"""联网取价工具链（可配置 + 可诊断 + 带出处）。

为什么要有它（2026-10-04 用户提问 + 本机实测）
--------------------------------------------
用户问："我们没法用官方 API 拿实时价格，那不能直接联网搜索/直接打开官方页面拿吗？"
答案分三层，这个模块把三层都变成可执行的代码，而不是文档里的说辞：

1. **能搜**：我们本来就用 DuckDuckGo（`duckduckgo_search`）。但**本机实测直接失败**：
   `DuckDuckGoSearchException: https://www.bing.com/search?q=… return None`（库会回退到 Bing，
   国际 443 不通）。所以用户看到"一个价都没有"的直接原因是**搜索源不可达**，
   而不是"我们没做"。这一层现在会被**明确诊断并如实上报**（见 `probe_providers`）。

2. **能直接抓官方页面**：技术上可以，但必须分域名对待 ——
   景区官网/政府文旅公示页这类**公开、静态、允许抓取**的页面可以抓；
   携程/美团/大众点评这类平台的价格是**登录态 + 动态渲染 + 风控签名**，
   直接 GET 拿到的 HTML 里通常没有价格，且抓取违反其服务条款。
   所以 `fetch_page_text` 默认只对**允许清单域名**（gov.cn / 景区官网等）生效，
   抓不到就如实返回 error，绝不拿其它页面的数字冒充。

3. **口径不变**：搜索/网页来的价格一律 `estimated`（不得当已核实），
   并且**必须带出处 URL 与抓取时间**，用户能点开自己核对。
   这也是为什么 `core/price_sources.py` 把 `web` 排在 `amap/official/provider` 之后。

可配置（全部走环境变量，绝不设全局代理 —— 那会把高德流量也带出国）：
    WEB_SEARCH_PROXY          仅本模块使用的代理（例如 http://127.0.0.1:7890）
    WEB_PRICE_TIMEOUT_SECONDS 单次搜索/抓取超时（默认 6）
    WEB_PRICE_MAX_NODES       一次规划最多查几个缺价节点（默认 12）
    WEB_PRICE_PROVIDERS       启用的取价提供方，逗号分隔：search,official_page
    WEB_PRICE_ALLOW_ANY_HOST  1 = 允许抓取任意域名（默认 0，只抓允许清单）
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

# 允许直接抓取的域名后缀：
#   ① 政府/事业单位这类明显公开的页面；
#   ② **酒店集团官网**（公开房价页）—— 用户口径 2026-10-04："可以加入酒店官网/民宿平台的可读页面"。
#      民宿平台多数要登录，不在默认清单里；需要时用 `WEB_PRICE_HOST_SUFFIXES` 自己加。
# 平台电商页（携程/美团/点评…）仍然明确不做：价格在登录态接口里、页面抓不到，抓取也违反其条款。
DEFAULT_OFFICIAL_SUFFIXES = (
    ".gov.cn", "gov.cn", ".gov.com.cn", ".org.cn", ".museum.cn",
    # 酒店集团官网（公开可读的房价页）
    "ihg.com", "marriott.com", "hilton.com", "hyatt.com", "accor.com", "wyndhamhotels.com",
    "bestwestern.com", "radissonhotels.com", "huazhu.com", "jinjianghotels.com", "jinjiang.com",
    "hworld.com", "7daysinn.cn", "homeinns.com", "motel168.com", "rujia.com", "vlovehotel.com",
)
BLOCKED_HOST_MARKERS = (
    "ctrip.com", "meituan.com", "dianping.com", "qunar.com", "fliggy.com", "taobao.com", "tmall.com",
    "tuniu.com", "ly.com", "elong.com", "booking.com", "agoda.com", "airbnb.",
)


def official_host_suffixes() -> tuple[str, ...]:
    """允许清单 = 默认（政府/事业单位）+ 环境变量里显式补充的景区官网域名。"""
    extra = str(os.getenv("WEB_PRICE_HOST_SUFFIXES") or "").strip()
    items = [item.strip().lower() for item in extra.split(",") if item.strip()]
    return tuple(DEFAULT_OFFICIAL_SUFFIXES) + tuple(items)

_PRICE_PATTERNS = (
    # 免费 / 免票
    (re.compile(r"(免费|免票|不收门票|无需门票)"), 0.0),
    # ¥ / ￥ / R 前缀
    (re.compile(r"[¥￥]\s*(\d+(?:\.\d+)?)"), None),
    # 数字 + 元
    (re.compile(r"(\d+(?:\.\d+)?)\s*元"), None),
    # "门票 45" / "人均 60" / "每晚 180" 这类没有单位的写法
    (re.compile(r"(?:门票|票价|人均|每晚|房价|价格)\s*[:：]?\s*(\d+(?:\.\d+)?)"), None),
)
FREE_MARKER = "免费"
CONTEXT_MARKERS = ("门票", "票价", "人均", "每晚", "房价", "价格", "费用", "起")


def budget_daily_max_requests() -> int:
    """联网取价的**每日请求上限**（默认 120）。

    用户口径 2026-10-04：加一个日请求上限开关，避免无意中超量（批量预热几千个 POI 时尤其重要）。
    达到上限就不再发起请求，并**如实说明"今天配额用完了"**，而不是静默失败。
    """
    raw = str(os.getenv("WEB_PRICE_DAILY_MAX_REQUESTS") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return 120
    return value if value > 0 else 120


def usage_state_path() -> str:
    raw = str(os.getenv("WEB_PRICE_USAGE_FILE") or "").strip()
    if raw:
        return raw
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web_price_usage.json")


def _today() -> str:
    import datetime

    return datetime.date.today().isoformat()


def read_usage() -> Dict[str, Any]:
    """读当日用量（`{date, requests}`）；文件缺失/损坏一律当成 0，绝不因此报错。"""
    import json

    path = usage_state_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and str(data.get("date")) == _today():
            return {"date": data.get("date"), "requests": int(data.get("requests") or 0)}
    except Exception:
        pass
    return {"date": _today(), "requests": 0}


def record_request(count: int = 1) -> Dict[str, Any]:
    """记一次请求（原子性不做强保证：这是配额保护，不是计费系统）。返回最新用量。"""
    import json

    state = read_usage()
    state["requests"] = int(state.get("requests") or 0) + max(0, int(count))
    try:
        with open(usage_state_path(), "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False)
    except Exception:
        pass
    return state


def daily_quota_left() -> int:
    return max(0, budget_daily_max_requests() - int(read_usage().get("requests") or 0))


def price_timeout_seconds() -> float:
    raw = str(os.getenv("WEB_PRICE_TIMEOUT_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 6.0
    return value if value > 0 else 6.0


def price_max_nodes() -> int:
    raw = str(os.getenv("WEB_PRICE_MAX_NODES") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return 12
    return value if value > 0 else 12


def enabled_providers() -> List[str]:
    raw = str(os.getenv("WEB_PRICE_PROVIDERS") or "sogou,search,official_page").strip()
    items = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return items or ["sogou"]


def search_proxy() -> str:
    """只给联网取价用的代理 —— 绝不用全局 HTTPS_PROXY（那会把高德流量也带出国）。"""
    return str(os.getenv("WEB_SEARCH_PROXY") or "").strip()


def allow_any_host() -> bool:
    return str(os.getenv("WEB_PRICE_ALLOW_ANY_HOST") or "").strip().lower() in {"1", "true", "yes", "on"}


def host_is_allowed(url: str) -> tuple[bool, str]:
    """这个 URL 能不能直接抓：返回 `(是否允许, 原因)`。"""
    text = str(url or "").strip()
    if not text.startswith(("http://", "https://")):
        return False, "不是 http(s) 链接"
    host = text.split("//", 1)[-1].split("/", 1)[0].lower()
    for marker in BLOCKED_HOST_MARKERS:
        if marker in host:
            return False, f"{host} 属于平台电商页（价格在登录态接口里，且抓取违反其服务条款）"
    if allow_any_host():
        return True, "允许任意域名（WEB_PRICE_ALLOW_ANY_HOST=1）"
    if any(host.endswith(suffix) or host == suffix.lstrip(".") for suffix in official_host_suffixes()):
        return True, "公开的官方/事业单位页面"
    return False, f"{host} 不在官方页面允许清单里（可用 WEB_PRICE_HOST_SUFFIXES 显式补充）"


def extract_price_candidates(text: str) -> List[Dict[str, Any]]:
    """从一段文本里抽价格候选（纯函数，单测覆盖）。

    只抽**有上下文或单位**的数字（"45元"/"￥45"/"门票 45"/"免费"），
    裸数字不抽 —— 否则"2026 年""2 天""3 号"都会被当成价格。
    """
    value = str(text or "")
    if not value.strip():
        return []
    found: List[Dict[str, Any]] = []
    seen: set[float] = set()

    for pattern, forced in _PRICE_PATTERNS:
        for match in pattern.finditer(value):
            if forced is not None:
                amount = float(forced)
                excerpt = match.group(0)
            else:
                try:
                    amount = float(match.group(1))
                except (TypeError, ValueError, IndexError):
                    continue
                excerpt = match.group(0)
            if amount < 0 or amount > 100000:
                continue
            if amount in seen:
                continue
            seen.add(amount)
            start = max(0, match.start() - 12)
            end = min(len(value), match.end() + 12)
            found.append({
                "value": amount,
                "currency": "CNY",
                "excerpt": value[start:end].strip(),
                "has_context": any(marker in value[start:end] for marker in CONTEXT_MARKERS) or forced is not None,
            })
    # 有上下文的排前面（"门票45元" 比孤立的 "45元" 可信）
    found.sort(key=lambda item: (not item["has_context"], item["value"]))
    return found


async def fetch_page_text(url: str, *, timeout: Optional[float] = None) -> Dict[str, Any]:
    """直接打开一个**允许抓取**的官方页面并取正文（用户问的"直接抵达官方页面"）。

    安全与礼貌：带 UA、限体积（512KB）、限时；域名不在允许清单直接拒绝并说明原因。
    """
    import httpx

    allowed, reason = host_is_allowed(url)
    if not allowed:
        return {"ok": False, "url": str(url), "error": reason}
    limit = float(timeout or price_timeout_seconds())
    proxy = search_proxy()
    try:
        async with httpx.AsyncClient(
            timeout=limit,
            follow_redirects=True,
            headers={"User-Agent": "OmniRoute/1.0 (+travel planning; contact: local)"},
            proxy=proxy or None,
        ) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                return {"ok": False, "url": str(url), "error": f"HTTP {response.status_code}"}
            body = response.text[: 512 * 1024]
    except Exception as exc:
        return {"ok": False, "url": str(url), "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return {"ok": True, "url": str(url), "text": text, "retrieved_at": time.time()}


async def probe_providers(*, query: str = "库尔勒 门票 价格") -> Dict[str, Any]:
    """诊断每个取价提供方能不能用。**第一个能用的就停**，且每个源都有独立超时预算。

    实测教训（2026-10-04）：早先这里会把所有提供方都试一遍（搜狗 2s + DuckDuckGo 10s），
    调用方用 `wait_for(10s)` 包着 → 诊断超时，异常把**整个补价功能**带走了
    （方案里显示"补价报错：TimeoutError"，而其实搜狗明明是好的）。
    诊断永远不能比它诊断的功能更脆弱。
    """
    import asyncio

    results: Dict[str, Any] = {}
    providers = enabled_providers()
    per_provider_budget = max(3.0, min(8.0, price_timeout_seconds()))
    for provider in providers:
        if provider == "sogou":
            try:
                rows = await asyncio.wait_for(fetch_sogou(query, max_results=3), timeout=per_provider_budget + 2)
                results["sogou"] = {"ok": bool(rows), "results": len(rows)}
                if rows:
                    break  # 第一个能用的就够，别再花时间试别的
            except Exception as exc:
                results["sogou"] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:140]}"}
        elif provider == "search":
            try:
                rows = await asyncio.wait_for(search_duckduckgo(query, max_results=3), timeout=per_provider_budget + 2)
                results["search"] = {"ok": bool(rows), "results": len(rows), "proxy": bool(search_proxy())}
                if rows:
                    break
            except Exception as exc:
                proxy = search_proxy()
                hint = "国际线路不通时，设置 WEB_SEARCH_PROXY（只作用于本模块，不动全局代理）"
                if proxy and "ConnectError" in f"{type(exc).__name__}{exc}":
                    hint = f"已配置代理 {proxy} 但连不上：先确认代理在运行（端口/进程）"
                results["search"] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {str(exc)[:140]}",
                    "proxy": bool(proxy),
                    "hint": hint,
                }
        elif provider == "official_page":
            # 注意：这一项只是**声明能力**（有允许清单、可按 URL 抓取），不是"可达性"。
            # 早先这里写 ok=True，结果 reachable 变成 true —— 而搜索明明失败了，
            # 于界面上会显示"取价源可达"，是彻头彻尾的误导。能力与可达性必须分开。
            results["official_page"] = {
                "configured": True,
                "mode": "allowlist",
                "suffixes": list(official_host_suffixes())[:4],
            }
    # 可达性只由**真正去取数的提供方**决定（官方页面要给定 URL 才知道通不通）
    probed = [item for key, item in results.items() if key in {"sogou", "search"}]
    return {
        "providers": results,
        "reachable": any(item.get("ok") for item in probed) if probed else False,
        "probed": sorted(key for key in results if key in {"sogou", "search"}),
    }


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
SOGOU_HOME = "https://www.sogou.com/"
SOGOU_SEARCH = "https://www.sogou.com/web"


def _strip_tags(fragment: str) -> str:
    import html as _html

    text = re.sub(r"<[^>]+>", "", fragment or "")
    return _html.unescape(text).strip()


def parse_sogou_results(body: str, limit: int = 8) -> List[Dict[str, Any]]:
    """解析搜狗结果页（真实结构，2026-10-04 抓取样本验证）。

    每个结果块形如：
      <div class="vrwrap" id="sogou_vr_..._wrap_1">
        <h3 class="vr-title"><a href="/link?url=…">标题</a></h3>
        <div class="fz-mid space-txt …" id="cacheresult_summary_1">摘要（常含"门票价格:大河口45元"）</div>
        <a class="citeLinkClass" …><span>相约久久旅游网</span><span>https://www.meet99.com/j…</span><span>2026-09-19</span>
    所以能同时拿到**标题、摘要、来源站名、来源 URL、日期** —— 出处齐全，用户可核对。
    """
    rows: List[Dict[str, Any]] = []
    if not body:
        return rows
    blocks = re.split(r'(?=<div class="vrwrap")', body)[1:]
    for block in blocks[:limit]:
        # 标题：先取整个 <h3 class="vr-title">…</h3>，再从里面找第一个 href ——
        # 比"一条正则同时匹配属性和锚文本"稳得多（实测后者在真实页面上会漏 URL）。
        title_html = ""
        href = ""
        title_match = re.search(r'<h3 class="vr-title">(.*?)</h3>', block, flags=re.S)
        if title_match:
            title_html = title_match.group(1)
            anchor = re.search(r'<a[^>]*href="([^"]+)"', title_html)
            if anchor:
                href = anchor.group(1)
        if not href:
            # 兜底：引用链接（cite）与标题指向同一篇，别让出处空着
            cite_anchor = re.search(r'<a class="citeLinkClass"[^>]*href="([^"]+)"', block)
            if cite_anchor:
                href = cite_anchor.group(1)
        snippet_match = re.search(r'<div class="fz-mid[^"]*"[^>]*>(.*?)</div>', block, flags=re.S)
        cite_match = re.search(r'<a class="citeLinkClass".*?</a>', block, flags=re.S)
        title = _strip_tags(title_html)
        snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""
        if not title and not snippet:
            continue
        cite_site = cite_url = cite_date = ""
        if cite_match:
            spans = re.findall(r"<span[^>]*>(.*?)</span>", cite_match.group(0), flags=re.S)
            cleaned = [_strip_tags(item) for item in spans]
            cite_site = cleaned[0] if cleaned else ""
            cite_url = cleaned[1] if len(cleaned) > 1 else ""
            cite_date = cleaned[2] if len(cleaned) > 2 else ""
        absolute = f"https://www.sogou.com{href}" if href.startswith("/") else href
        rows.append({
            "title": title[:160],
            "url": absolute,
            "snippet": snippet[:300],
            # 引用信息（站名/真实 URL/日期）：搜索跳转链接之外，给出可读来源与**发布日期**
            # （价格会过期，用户需要知道这条信息是什么时候的）
            "cite_site": cite_site[:60],
            "cite_url": cite_url[:160],
            "cite_date": cite_date[:40],
            "provider": "sogou",
        })
    return rows


async def fetch_sogou(query: str, *, max_results: int = 5) -> List[Dict[str, Any]]:
    """用搜狗搜一次（国内直连可达；DuckDuckGo/Bing 在本机不可达）。

    先访问首页拿会话 cookie 再搜 —— 实测不带会话时 bing 会返回填充推荐，
    搜狗同样需要一次首页访问才稳定。
    """
    import httpx

    timeout = price_timeout_seconds()
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
    ) as client:
        try:
            await client.get(SOGOU_HOME)
        except Exception:
            pass  # 首页拿不到也继续试搜索
        response = await client.get(SOGOU_SEARCH, params={"query": query})
        if response.status_code >= 400:
            raise RuntimeError(f"sogou HTTP {response.status_code}")
        return parse_sogou_results(response.text, limit=max_results)


def price_budget_seconds() -> float:
    """一次规划里"联网取价"这条链路的**总时间预算**（默认 45s）。

    这个数字是用**实测单点耗时**反推的，不是拍的：
      - 2026-10-04 实测搜狗单次约 2.5s（+1.2s 节流）；
      - 2026-10-05 用 `tools/probe_web_price.py` 连查 3 个库尔勒节点耗时 **19.7s**
        （≈6.5s/节点：搜索 + 命中白名单页面还要再抓一次正文），
        而当时预算 25s 只够 5 个节点 × 6.5s = 32.5s 里的前 3~4 个 ——
        矩阵里 4 个场景全部出现"查了 5 个、超预算 4 个、补到 0 条"。
      - 所以预算按"最多 6 个节点 × 7.2s"给到 45s，并且**逐点公平分预算**
        （一个慢点不许吃光全部预算）。仍然可配置：`WEB_PRICE_BUDGET_SECONDS`。
    """
    raw = str(os.getenv("WEB_PRICE_BUDGET_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 45.0
    return value if value > 0 else 45.0


def affordable_target_count(max_nodes: int, *, budget_seconds: Optional[float] = None) -> int:
    """在时间预算内**大概**能查几个节点。

    单节点成本按"节流间隔 + 一次搜索/抓页的上限（`price_timeout_seconds`）"算 ——
    上一版写死 `+3.0s`，比实测的 6.5s 乐观太多，于是"能查 5 个"的目标里有 4 个
    根本没轮到（2026-10-05 矩阵实测）。
    """
    budget = float(budget_seconds if budget_seconds is not None else price_budget_seconds())
    per_node = max(1.0, search_min_interval_seconds() + float(price_timeout_seconds()))
    return max(1, min(int(max_nodes or 1), int(budget // per_node) or 1))


def search_min_interval_seconds() -> float:
    """同一搜索源两次请求之间的最小间隔（默认 1.2s）。

    实测（2026-10-04）：连续快速请求搜狗后，接下来几次会拿到"填充/无结果"页面，
    表现为"同一个地点一会儿能搜到价、一会儿搜不到"。节流不是可选项，是可用性的一部分。
    """
    raw = str(os.getenv("WEB_PRICE_MIN_INTERVAL_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 1.2
    return value if value >= 0 else 1.2


def search_cache_ttl_seconds() -> float:
    raw = str(os.getenv("WEB_SEARCH_CACHE_TTL_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 6 * 3600.0
    return value if value > 0 else 6 * 3600.0


def pick_price_from_rows(rows: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """从搜索结果里挑一条价格：**优先有出处的**（纯函数，单测覆盖）。

    为什么不是"第一条有价格的就行"：实测搜狗会返回若干"无标题锚点"的聚合块，
    价格在里面但没有 URL —— 直接用就会得到"有价格、没来源"这种最糟的组合。
    这里按 (有 URL, 有发布日期, 有上下文) 排序后取第一条。
    """
    best: Optional[Dict[str, Any]] = None
    best_score: tuple = ()
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        text = f"{row.get('title') or ''} {row.get('snippet') or row.get('body') or ''}"
        candidates = extract_price_candidates(text)
        if not candidates:
            continue
        candidate = candidates[0]
        url = str(row.get("url") or row.get("href") or "")
        score = (
            1 if url else 0,
            1 if str(row.get("cite_date") or "") else 0,
            1 if candidate.get("has_context") else 0,
        )
        if best is None or score > best_score:
            best = {
                "value": float(candidate["value"]),
                "excerpt": str(candidate.get("excerpt") or ""),
                "url": url,
                "publisher": str(row.get("cite_site") or ""),
                "source_date": str(row.get("cite_date") or ""),
                "provider": str(row.get("provider") or ""),
            }
            best_score = score
    return best


async def search_texts(query: str, *, max_results: int = 3) -> List[Dict[str, Any]]:
    """按配置的提供方顺序搜一次，返回**第一个有结果**的提供方给出的结果。

    顺序默认 `sogou,search,official_page`（搜狗国内直连可达；DuckDuckGo 在境外网络可用）。
    带**同一进程内的节流与结果缓存**：
      - 两次搜索至少间隔 `WEB_PRICE_MIN_INTERVAL_SECONDS`（默认 1.2s）—— 防限流；
      - 相同 query 在 `WEB_SEARCH_CACHE_TTL_SECONDS`（默认 6h）内直接复用 —— 少打扰对方站点；
      - **提供方熔断**：一个源连续失败 2 次就先冷却 `WEB_PRICE_PROVIDER_COOLDOWN_SECONDS`
        （默认 180s），后面的取价点直接跳过它。
        为什么必须要（2026-10-05 实测）：搜狗当时已经查不动了，而每个取价点都还按顺序
        先去问它、等它失败，才轮到能用的源 —— 单点预算被这个"死源"吃光，
        表现成"查了 6 个、补到 0 条、超预算 5 个"。
      - 兜底原则：**所有源都在冷却时仍然试第一个**（宁可慢，也不能因为熔断把功能关掉），
        并在尝试记录里标明这是"冷却被强制放行"。
    每个提供方的成败都记进 `last_search_attempts`，供诊断与"为什么没取到"的披露。
    """
    global last_search_attempts, _last_search_at  # noqa: PLW0603 - 模块级诊断/节流状态

    import asyncio
    import time as _time

    cache_key = f"{','.join(enabled_providers())}|{max_results}|{query}"
    cached = _search_cache.get(cache_key)
    if cached and (_time.monotonic() - cached[0]) < search_cache_ttl_seconds():
        last_search_attempts = [{"provider": "cache", "ok": True, "results": len(cached[1])}]
        return [dict(row) for row in cached[1]]

    # 日请求上限：到顶就不再发请求，并如实说明原因（用户口径：避免无意中超量）
    if daily_quota_left() <= 0:
        last_search_attempts = [{
            "provider": "quota",
            "ok": False,
            "error": f"今日联网取价配额已用完（上限 {budget_daily_max_requests()} 次）",
        }]
        return []

    async with _search_lock:
        cached = _search_cache.get(cache_key)
        if cached and (_time.monotonic() - cached[0]) < search_cache_ttl_seconds():
            last_search_attempts = [{"provider": "cache", "ok": True, "results": len(cached[1])}]
            return [dict(row) for row in cached[1]]
        gap = search_min_interval_seconds() - (_time.monotonic() - _last_search_at)
        if gap > 0:
            await asyncio.sleep(gap)
        attempts: List[Dict[str, Any]] = []
        searchable = [name for name in enabled_providers() if name != "official_page"]
        available = [name for name in searchable if provider_is_available(name)]
        if searchable and not available:
            # 全都冷却中：强制放行第一个（并如实记下来），否则一次网络抖动会把功能关死
            available = [searchable[0]]
            attempts.append({
                "provider": searchable[0],
                "ok": False,
                "cooldown_overridden": True,
                "error": "所有取价源都在冷却中，仍强制试一次（避免因为熔断直接放弃）",
            })
        for provider in available:
            if provider == "sogou":
                try:
                    rows = await fetch_sogou(query, max_results=max_results)
                    record_request()  # 计入日配额（无论成败都算一次真实请求）
                    attempts.append({"provider": "sogou", "ok": bool(rows), "results": len(rows)})
                    _note_provider_result("sogou", bool(rows))
                    if rows:
                        _last_search_at = _time.monotonic()
                        _search_cache[cache_key] = (_last_search_at, [dict(row) for row in rows])
                        last_search_attempts = attempts
                        return rows
                except Exception as exc:
                    attempts.append({"provider": "sogou", "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
                    _note_provider_result("sogou", False)
            elif provider == "search":
                try:
                    rows = await search_duckduckgo(query, max_results=max_results)
                    record_request()
                    attempts.append({"provider": "search", "ok": bool(rows), "results": len(rows)})
                    _note_provider_result("search", bool(rows))
                    if rows:
                        _last_search_at = _time.monotonic()
                        _search_cache[cache_key] = (_last_search_at, [dict(row) for row in rows])
                        last_search_attempts = attempts
                        return rows
                except Exception as exc:
                    attempts.append({"provider": "search", "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
                    _note_provider_result("search", False)
            # official_page 不是通用搜索，跳过
        _last_search_at = _time.monotonic()
        last_search_attempts = attempts
        return []


# —— 提供方熔断状态（进程内）——
_PROVIDER_FAILURES: Dict[str, int] = {}
_PROVIDER_COOLDOWN_UNTIL: Dict[str, float] = {}
# 连续失败几次就冷却：2 次足够区分"偶发失败"和"这个源现在不行了"
PROVIDER_FAILURE_THRESHOLD = 2


def provider_cooldown_seconds() -> float:
    raw = str(os.getenv("WEB_PRICE_PROVIDER_COOLDOWN_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 180.0
    return value if value >= 0 else 180.0


def provider_is_available(provider: str, *, now: Optional[float] = None) -> bool:
    """这个源现在能不能用（不在冷却期内）。"""
    import time as _time

    moment = float(now if now is not None else _time.monotonic())
    return moment >= float(_PROVIDER_COOLDOWN_UNTIL.get(str(provider)) or 0.0)


def _note_provider_result(provider: str, ok: bool, *, now: Optional[float] = None) -> None:
    """记一次成败：成功清零；连续失败到阈值就进入冷却（默认 180s）。"""
    import time as _time

    moment = float(now if now is not None else _time.monotonic())
    name = str(provider)
    if ok:
        _PROVIDER_FAILURES[name] = 0
        _PROVIDER_COOLDOWN_UNTIL.pop(name, None)
        return
    failures = int(_PROVIDER_FAILURES.get(name) or 0) + 1
    _PROVIDER_FAILURES[name] = failures
    if failures >= PROVIDER_FAILURE_THRESHOLD:
        _PROVIDER_COOLDOWN_UNTIL[name] = moment + provider_cooldown_seconds()


def provider_states(*, now: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """给诊断用：每个源连续失败几次、还要冷却多久。"""
    import time as _time

    moment = float(now if now is not None else _time.monotonic())
    states: Dict[str, Dict[str, Any]] = {}
    for name in enabled_providers():
        until = float(_PROVIDER_COOLDOWN_UNTIL.get(name) or 0.0)
        states[name] = {
            "consecutive_failures": int(_PROVIDER_FAILURES.get(name) or 0),
            "cooldown_remaining_s": round(max(0.0, until - moment), 1),
            "available": moment >= until,
        }
    return states


def reset_provider_state() -> None:
    """清空熔断状态（测试与"换一换"重试时用）。"""
    _PROVIDER_FAILURES.clear()
    _PROVIDER_COOLDOWN_UNTIL.clear()


last_search_attempts: List[Dict[str, Any]] = []
_last_search_at: float = 0.0
_search_cache: Dict[str, Any] = {}
try:  # 锁在导入时创建（同进程内串行化搜索，避免互相触发限流）
    import asyncio as _asyncio

    _search_lock = _asyncio.Lock()
except Exception:  # pragma: no cover - 极端环境下退化为无锁
    _search_lock = None


async def search_duckduckgo(query: str, *, max_results: int = 3) -> List[Dict[str, Any]]:
    """DuckDuckGo（境外网络可用）。**把 WEB_SEARCH_PROXY 传进搜索引擎**。

    实测教训（2026-10-04）：这个模块最初只把代理给了 httpx（抓页面那条路径），
    而 DuckDuckGo 搜索根本没走代理 —— 于是界面上写着"可配置 WEB_SEARCH_PROXY 让搜索走代理"，
    配了也照样连不通。文档承诺与代码行为必须一致，所以这里显式传 `proxy=`，
    并额外在调用期间设置 HTTP(S)_PROXY 环境变量（DDGS 内部用 requests/urllib3，
    两层都覆盖），调用结束立刻恢复现场。
    """
    import asyncio

    def _run() -> List[Dict[str, Any]]:
        from duckduckgo_search import DDGS

        proxy = search_proxy()
        saved = {key: os.environ.get(key) for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}
        try:
            if proxy:
                os.environ["HTTP_PROXY"] = proxy
                os.environ["HTTPS_PROXY"] = proxy
            kwargs: Dict[str, Any] = {"timeout": min(8.0, price_timeout_seconds())}
            if proxy:
                kwargs["proxy"] = proxy
            with DDGS(**kwargs) as ddgs:
                rows = list(ddgs.text(query, max_results=max_results))
            return [{**row, "provider": "search"} for row in rows if isinstance(row, dict)]
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    return await asyncio.wait_for(asyncio.to_thread(_run), timeout=price_timeout_seconds() + 2)


async def search_result_count(query: str, *, max_results: int = 3) -> int:
    """诊断用：真正搜一次，返回结果条数（走同一套提供方顺序与代理处理）。"""
    rows = await search_texts(query, max_results=max_results)
    return len(rows)


async def lookup_price(name: str, city: str, *, max_results: int = 3) -> Dict[str, Any]:
    """一个地点的取价：先搜，再**能直抓官方页面就直抓**（用户口径：加入酒店官网/民宿白名单）。

    顺序与口径：
      1. 搜索 → 从摘要里抽价（门票/景区类命中率最高）；
      2. 若命中的链接在**允许清单**里（政府页/酒店集团官网/用户配置域名），直抓该页面，
         从**页面正文**抽价 —— 这比摘要可靠（摘要可能只是标题里的数字）；
      3. 两者都拿不到 → 返回 `value=None` 并说明，绝不填数字。

    页面来的价格仍标 `source="web_page"`（⇒ estimated）：我们抓的是公开页面文本，
    不等于官方报价接口，不能冒充"已核实"。
    """
    query = f"{city} {name} 门票 价格 人均 每晚"
    try:
        rows = await search_texts(query, max_results=max_results)
    except Exception as exc:
        return build_observation(name=name, value=None, source="web", provider=",".join(enabled_providers())) | {
            "error": f"{type(exc).__name__}: {str(exc)[:120]}"
        }

    snippet_pick = pick_price_from_rows(rows)
    page_url = ""
    for row in rows or []:
        candidate_url = str(row.get("url") or row.get("href") or "")
        if candidate_url and host_is_allowed(candidate_url)[0]:
            page_url = candidate_url
            break

    if page_url:
        page = await fetch_page_text(page_url)
        if page.get("ok"):
            candidates = extract_price_candidates(str(page.get("text") or ""))
            if candidates:
                best = candidates[0]
                return build_observation(
                    name=name,
                    value=float(best["value"]),
                    source="web_page",
                    source_url=page_url,
                    excerpt=str(best.get("excerpt") or ""),
                    provider="official_page",
                )

    if snippet_pick is not None:
        return build_observation(
            name=name,
            value=float(snippet_pick["value"]),
            source="web",
            source_url=str(snippet_pick.get("url") or ""),
            excerpt=str(snippet_pick.get("excerpt") or ""),
            provider=str(snippet_pick.get("provider") or ""),
            publisher=str(snippet_pick.get("publisher") or ""),
            source_date=str(snippet_pick.get("source_date") or ""),
        )
    return build_observation(
        name=name,
        value=None,
        source="web",
        provider=",".join(enabled_providers()),
        source_url=page_url,
    )


def build_observation(
    *,
    name: str,
    value: Optional[float],
    source: str,
    source_url: str = "",
    excerpt: str = "",
    provider: str = "",
    retrieved_at: Optional[float] = None,
    publisher: str = "",
    source_date: str = "",
) -> Dict[str, Any]:
    """一条取价结果：**必须带出处**，并明确它是估算而不是已核实。

    `publisher`/`source_date` 是给"这条价格什么时候发的"用的 —— 价格会过期，
    用户需要判断新鲜度（真实搜狗结果里带发布日期，如 2026-09-19）。
    """
    verified_sources = {"provider", "vendor", "official", "amap"}
    return {
        "name": str(name or ""),
        "value": value,
        "currency": "CNY",
        "source": str(source),
        "source_url": str(source_url or ""),
        "excerpt": str(excerpt or "")[:160],
        "provider": str(provider or ""),
        "publisher": str(publisher or "")[:60],
        "source_date": str(source_date or "")[:40],
        "retrieved_at": float(retrieved_at or time.time()),
        # 搜索/网页来的价格永远是估算：不允许写成 verified
        "verified": str(source) in verified_sources,
        "estimated": str(source) not in verified_sources,
    }
