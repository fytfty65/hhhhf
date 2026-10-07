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

import json
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

# 👑 共享 SSL 上下文（2026-10-05 实测的性能发现，与 api/agent.py 同一处理）：
#    httpx 构造客户端时会**同步**建 SSL 上下文，本机实测 0.87s vs 传入已有上下文 0.05s，
#    而这段是**阻塞事件循环**的 —— 取价链路每个节点都要建客户端，累积起来很可观。
try:
    import ssl as _ssl

    _SHARED_SSL_CONTEXT: Optional["_ssl.SSLContext"] = _ssl.create_default_context()
except Exception:  # pragma: no cover - 建不出来就退回 httpx 默认行为
    _SHARED_SSL_CONTEXT = None


def _ssl_verify_kwargs() -> Dict[str, Any]:
    """给 httpx 客户端用的 `verify=`（没有共享上下文时返回空字典，走默认行为）。"""
    return {"verify": _SHARED_SSL_CONTEXT} if _SHARED_SSL_CONTEXT is not None else {}


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
# 明确的价格语境（比"免费/带单位"更能说明这就是我们要的那个价）
STRONG_PRICE_MARKERS = ("门票", "票价", "全价", "半价", "房价", "人均", "每晚", "价格")
# 「免费」只有在这些**门票语境**里才算价格（其余"免费"多是别的意思，见 extract_price_candidates）
FREE_PRICE_MARKERS = ("门票免费", "免门票", "免票", "免费开放", "免费入园", "免费参观", "免费游览", "免费进入")
# 这些"免费"与价格无关，出现即不算
FREE_PRICE_EXCLUSIONS = ("取消", "停车", "wifi", "WiFi", "WIFI", "号码布", "马拉松", "赠", "领取", "试用", "退")


def budget_daily_max_requests() -> int:
    """联网取价的**每日请求上限**（默认 240）。

    用户口径 2026-10-04：加一个日请求上限开关，避免无意中超量（批量预热几千个 POI 时尤其重要）。
    达到上限就不再发起请求，并**如实说明"今天配额用完了"**，而不是静默失败。

    默认从 120 提到 240 的理由（2026-10-07 实测）：取价改成**多源合并**之后，
    一个缺价节点要花 **2 次**请求（ddgs + Bing），120 次只够 60 个节点 ——
    一份 5 天的方案光景点就能把额度吃光，表现成"查了 5 个、补到 0 条、
    失败原因是今日配额已用完"（本次实跑就撞上了）。240 仍然是有界的：
    它防的是"无意中批量预热"，不是正常规划。
    """
    raw = str(os.getenv("WEB_PRICE_DAILY_MAX_REQUESTS") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return 240
    return value if value > 0 else 240


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


def price_budget_seconds() -> float:
    """一次规划里"联网取价"这条链路的**总时间预算**（默认 90s）。

    这个数字是反推出来的：
      - 2026-10-04 实测搜狗单次约 2.5s（+1.2s 节流）；
      - 2026-10-05 用 `tools/probe_web_price.py` 连查 3 个库尔勒节点耗时 **19.7s**
        （≈6.5s/节点：搜索 + 命中白名单页面还要再抓一次正文），而当时预算 25s 只够
        5 个节点 × 6.5s = 32.5s 里的前 3~4 个 —— 矩阵里 4 个场景全部出现
        "查了 5 个、超预算 4 个、补到 0 条"；
      - 2026-10-07 再加一倍（45 → 90）：取价改成**多源合并**（一个节点要问 ddgs + Bing，
        才既拿得到票务平台价、又有机会撞上官方页面），单点成本翻倍；而且**不缓存半途而废的方案**
        之后，"预算不够"不再被缓存固化，值得多给一点时间。用户的优先级是方案质量，不是延迟。
    仍然可配置：`WEB_PRICE_BUDGET_SECONDS`。
    """
    raw = str(os.getenv("WEB_PRICE_BUDGET_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 90.0
    return value if value > 0 else 90.0


def price_max_nodes() -> int:
    raw = str(os.getenv("WEB_PRICE_MAX_NODES") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return 12
    return value if value > 0 else 12


def enabled_providers() -> List[str]:
    # 默认顺序的由来（2026-10-07 **按产出实测**重排，见 `tools/probe_provider_yield.py`）：
    #   同一批 8 条真实 query（"城市 景点 门票 价格"）各问一遍的结果：
    #     · **bing 8/8 成功、共 48 条**（不弹验证码，带出 .gov.cn —— 官方页面链路的入口）；
    #     · baidu 先 8/8、随后开始返回**安全验证页**（直接调 `fetch_baidu` 会抛）；
    #     · ddgs 只被轮到 2 次且 **TimeoutError**（限流），0 条；
    #     · sogou 0 条；`search`（旧 `duckduckgo_search` 包）已改名失效，只会吃时间。
    #   所以：**能出结果的排前面**，同时保留 ddgs（它好的时候能带出携程/去哪儿这类票务平台链接）。
    #   merge 时还会按**近期健康分**动态排序（成功 +2/失败 −3），静态顺序只是并列时的兜底。
    raw = str(os.getenv("WEB_PRICE_PROVIDERS") or "bing,baidu,ddgs,sogou,official_page").strip()
    items = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return items or ["bing"]


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


# —— 官方域名**候选发现**（只提议、不自动信任）——
# 用户口径（2026-10-05）："把 gov/景区官网页面（白名单里的）视作可核实来源"。
# 但白名单是**人工确认**的：景区官网域名千奇百怪（xxx-scenic.com / xxxlvyou.cn…），
# 自动信任任意 .com 就等于把"可核实"这四个字废掉。所以这里只做一件事：
# 从搜索结果里**认出"这看起来是官网"的域名**，记进待确认清单，由人加进
# `WEB_PRICE_HOST_SUFFIXES`。提议不落库、不抓取、不影响当次价格结论。
OFFICIAL_TITLE_MARKERS = (
    "官网", "官方网站", "官方站", "景区官网", "管理委员会", "管委会", "旅游局",
    "文化和旅游", "文旅集团", "旅游发展", "景区管理局", "自然保护区管理局",
)


def official_domain_hints(
    rows: Sequence[Mapping[str, Any]],
    *,
    query_name: str = "",
    max_items: int = 3,
) -> List[Dict[str, Any]]:
    """从搜索结果里挑出"像官网但不在白名单"的域名（纯函数，可单测）。

    判定只用**搜索结果自己给出的信息**（标题/摘要/站点名），不额外抓页面：
    - 标题或站点名里出现「官网/管委会/旅游局/文旅集团…」这类官方身份词；
    - 域名不在允许清单里（已在清单里的没必要提议）；
    - 平台电商页直接排除。
    返回 `[{host, url, title, why}]`，供人工确认后写入 `WEB_PRICE_HOST_SUFFIXES`。
    """
    hints: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, Mapping):  # 搜索结果里混进 None/字符串不该把整段带崩
            continue
        url = str(row.get("url") or row.get("href") or "").strip()
        if not url or not url.startswith(("http://", "https://")):
            continue
        host = _host_of(url)
        if not host or host in seen:
            continue
        if any(marker in host for marker in BLOCKED_HOST_MARKERS):
            continue
        if host_is_allowed(url)[0]:
            continue  # 已经在白名单里，没什么可确认的
        title = str(row.get("title") or "")
        site = str(row.get("cite_site") or row.get("publisher") or "")
        text = f"{title} {site}"
        why = next((marker for marker in OFFICIAL_TITLE_MARKERS if marker in text), "")
        if not why:
            continue
        seen.add(host)
        hints.append(
            {
                "host": host,
                "url": url,
                "title": title[:80],
                "why": f"标题/站点名里出现「{why}」",
                "query_name": str(query_name or ""),
            }
        )
        if len(hints) >= max(1, int(max_items)):
            break
    return hints


def domain_candidate_path() -> str:
    """待确认的官方域名清单落盘位置（默认在 ai-service 目录下，可被环境变量覆盖）。"""
    configured = str(os.getenv("WEB_PRICE_DOMAIN_CANDIDATES_FILE") or "").strip()
    if configured:
        return configured
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "official_domain_candidates.json")


def load_domain_candidates() -> List[Dict[str, Any]]:
    path = domain_candidate_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return []
    items = data.get("candidates") if isinstance(data, dict) else data
    return [item for item in (items or []) if isinstance(item, dict)]


def record_domain_candidates(hints: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """把"像官网"的域名追加进待确认清单（去重、只记域名与标题，不记正文）。

    ⚠️ 只在**这次没能从白名单页面拿到价**时才值得记（调用方把关）：
    已经能查到价的时候再去扩白名单是没有收益的动作。
    `WEB_PRICE_RECORD_DOMAIN_CANDIDATES=0` 可整体关掉。
    """
    if str(os.getenv("WEB_PRICE_RECORD_DOMAIN_CANDIDATES") or "").strip().lower() in {"0", "false", "off", "no"}:
        return {"recorded": 0, "reason": "WEB_PRICE_RECORD_DOMAIN_CANDIDATES=0（显式关闭）"}
    clean = [dict(item) for item in (hints or []) if isinstance(item, Mapping) and item.get("host")]
    if not clean:
        return {"recorded": 0, "reason": "没有候选"}

    path = domain_candidate_path()
    existing = load_domain_candidates()
    known = {str(item.get("host") or "") for item in existing}
    added = [item for item in clean if str(item.get("host") or "") not in known]
    if not added:
        return {"recorded": 0, "reason": "这些域名已经在待确认清单里", "already_known": len(clean)}
    merged = existing + [
        {**item, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S")} for item in added
    ]
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"candidates": merged}, handle, ensure_ascii=False, indent=2)
    except Exception as exc:
        return {"recorded": 0, "reason": f"写入失败：{type(exc).__name__}: {str(exc)[:100]}"}
    return {"recorded": len(added), "path": path, "hosts": [str(item.get("host") or "") for item in added]}


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
            window = value[start:end]
            # 「免费」必须**在门票语境里**才算价格（2026-10-05 实测的错价）：
            #   · "凭号码布免费游"（马拉松）→ 被当成博斯腾湖门票 ¥0；
            #   · "18:00前可免费取消"（酒店条款）→ 被当成房价 ¥0。
            # 这两个都是**假数据**，比"没取到"糟得多。只有出现下面这些明确说法才认。
            if amount == 0.0:
                if not any(marker in window for marker in FREE_PRICE_MARKERS):
                    continue
                if any(marker in window for marker in FREE_PRICE_EXCLUSIONS):
                    continue
            # 上下文分强弱（2026-10-05 实测）：一条摘要里可能同时出现"门票45元"和"环湖免费"，
            # 只按"有没有上下文 + 数值升序"排，会把 免费(0) 排在 45 前面 —— 挑出来的是错的那个。
            # 所以：**门票/票价/房价/人均 这类明确的价格语境 > 免费 > 其它带单位的数字 > 无上下文**。
            strong = any(marker in window for marker in STRONG_PRICE_MARKERS)
            found.append({
                "value": amount,
                "currency": "CNY",
                "excerpt": window.strip(),
                "has_context": any(marker in window for marker in CONTEXT_MARKERS) or forced is not None,
                "context_strength": 0 if strong else (1 if forced is not None else (2 if any(marker in window for marker in CONTEXT_MARKERS) else 3)),
            })
    # 语境强的排前面，其次数值小的（同一语境下"起价"通常更接近门槛价）
    found.sort(key=lambda item: (item["context_strength"], item["value"]))
    return found


async def fetch_page_text(
    url: str, *, timeout: Optional[float] = None, allow_hosts: Sequence[str] = ()
) -> Dict[str, Any]:
    """直接打开一个**允许抓取**的官方页面并取正文（用户问的"直接抵达官方页面"）。

    安全与礼貌：带 UA、限体积（512KB）、限时；域名不在允许清单直接拒绝并说明原因。

    `allow_hosts`（2026-10-07 加）：**另外**允许的主机名。用于文旅部名录登记的
    景区官网/官方预约平台 —— 那些域名不在我们的白名单里（白名单是政府站 + 用户配置域名），
    但它们由**部级官方名录**背书，属于"官方页面"。不放开这个参数，
    MCT 通道即使命中了 5A 景区也永远抓不到它的官方页（实测 `host_is_allowed` 直接拒绝）。
    """
    import httpx

    allowed, reason = host_is_allowed(url)
    if not allowed:
        host = str(url or "").split("/")[2] if "//" in str(url or "") else ""
        if host and host in {str(item).strip().lower() for item in allow_hosts if str(item).strip()}:
            allowed, reason = True, "文旅部名录登记的景区官网"
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
            **_ssl_verify_kwargs(),
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


async def fetch_page_html(url: str, *, timeout: Optional[float] = None) -> Dict[str, Any]:
    """抓官方页面的**原始 HTML**（给"名录/列表页解析"用）。

    为什么需要它（2026-10-07 实测）：`fetch_page_text` 会把 HTML 剥成纯文本
    （伊犁文旅名录页 40146 字符 → 5401 字符），而名录的条目结构
    （`<a href="/show/scenicspot/detail?id=581&nodeId=2465">` + `<h2>名字</h2>`）只在 HTML 里 ——
    第一版拿纯文本去解析，得到"0 条"，差点误判成"名录是 JS 渲染的、抓不到"。
    安全与礼貌同 `fetch_page_text`：白名单域名、512KB 上限、限时、明确 UA。
    """
    import httpx

    allowed, reason = host_is_allowed(url)
    if not allowed:
        return {"ok": False, "url": str(url), "error": reason}
    limit = float(timeout or price_timeout_seconds())
    try:
        async with httpx.AsyncClient(
            timeout=limit,
            follow_redirects=True,
            headers={"User-Agent": "OmniRoute/1.0 (+travel planning; contact: local)"},
            proxy=search_proxy() or None,
            **_ssl_verify_kwargs(),
        ) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                return {"ok": False, "url": str(url), "error": f"HTTP {response.status_code}"}
            html = response.text[: 512 * 1024]
    except Exception as exc:
        return {"ok": False, "url": str(url), "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    return {"ok": True, "url": str(url), "html": html, "retrieved_at": time.time()}


async def fetch_page_html_stable(url: str, *, attempts: int = 2, timeout: Optional[float] = None) -> Dict[str, Any]:
    """`fetch_page_html` 的重试版（与 `fetch_page_text_stable` 同口径）。"""
    last: Dict[str, Any] = {"ok": False, "url": str(url), "error": "未尝试"}
    for _ in range(max(1, int(attempts))):
        last = await fetch_page_html(url, timeout=timeout)
        if last.get("ok") and last.get("html"):
            return last
    return last


async def fetch_page_text_stable(
    url: str, *, attempts: int = 2, timeout: Optional[float] = None, allow_hosts: Sequence[str] = ()
) -> Dict[str, Any]:
    """抓官方页面正文：失败**重试一次**（默认最多 2 次）。

    为什么值得重试（2026-10-05 实测）：伊宁方案里「伊宁县文化艺术中心」拿到了
    `¥0（web）`，而它的来源 URL 是 `xjyn.gov.cn`（白名单里的政府页）——说明当时**直抓失败了**，
    只能退回搜索结果摘要，来源因此从"可核实"掉成"估算"。事后单独再抓同一个 URL：
    `ok=True`、正文里就写着"文化馆免费开放" —— 那次失败是**偶发**的（超时/连接抖动）。
    一个点的代价是一次请求，换回来的是一次"估算 → 可核实"的翻转，值。
    上限写死为 `attempts`：不做无界重试（预算有限，且失败原因多半是持续性的）。
    """
    last: Dict[str, Any] = {"ok": False, "url": str(url), "error": "未尝试"}
    for _ in range(max(1, int(attempts))):
        last = await fetch_page_text(url, timeout=timeout, allow_hosts=allow_hosts)
        if last.get("ok"):
            return last
    return last


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
        if provider == "baidu":
            try:
                rows = await asyncio.wait_for(fetch_baidu(query, max_results=3), timeout=per_provider_budget + 2)
                results["baidu"] = {"ok": bool(rows), "results": len(rows)}
                if rows:
                    break  # 第一个能用的就够，别再花时间试别的
            except Exception as exc:
                results["baidu"] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:140]}"}
        elif provider == "sogou":
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
    # 可达性只由**真正去取数的提供方**决定（官方页面要给定 URL 才知道通不通）。
    # ⚠️ 这个集合必须跟着"能真的取数的源"一起更新：漏掉 baidu 会让"百度明明返回了 3 条结果、
    #    界面上却写着'取价源不可达'"（2026-10-05 实测就是这么误导的）。
    probed = [item for key, item in results.items() if key in SEARCH_PROVIDERS]
    return {
        "providers": results,
        "reachable": any(item.get("ok") for item in probed) if probed else False,
        "probed": sorted(key for key in results if key in SEARCH_PROVIDERS),
    }


# 真正"去网上搜"的源（official_page 只声明能力，不算可达性）
SEARCH_PROVIDERS = ("baidu", "sogou", "search")


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
        **_ssl_verify_kwargs(),
    ) as client:
        try:
            await client.get(SOGOU_HOME)
        except Exception:
            pass  # 首页拿不到也继续试搜索
        response = await client.get(SOGOU_SEARCH, params={"query": query})
        if response.status_code >= 400:
            raise RuntimeError(f"sogou HTTP {response.status_code}")
        return parse_sogou_results(response.text, limit=max_results)


# —— 百度：搜狗被反爬打死之后的**国内可用替代**（2026-10-05 实测）——
# 同一时刻的实测对照：
#   · 搜狗：HTTP 200 但只有 5.4KB 的"填充页"，结果块里 0 个含查询词（等于没有结果）；
#   · 必应国内：不带会话返回填充推荐，0 个相关块；
#   · DuckDuckGo：连通性时好时坏，实测连查 5 个查询全部 0 行（装了代理也一样）；
#   · **百度：HTTP 200 / 1.25MB / 31 个结果块，第一个标题就是「博斯腾湖门票45元…」**。
# 所以百度成为默认顺序里的第二个源，而且是"有真结果"的那个。
BAIDU_SEARCH = "https://www.baidu.com/s"
BAIDU_HOME = "https://www.baidu.com/"
# 进程内的百度会话缓存（cookie + 上次拿会话的时间）：避免每个取价点都去访问首页，
# 也避免"一次推演 6 个点连着查"被百度当成机器人（见 fetch_baidu 的说明）。
BAIDU_SESSION_TTL_SECONDS = 1800.0
_BAIDU_COOKIES: Dict[str, str] = {}
_BAIDU_SESSION_AT: float = 0.0


def parse_baidu_results(html: str, *, limit: int = 5) -> List[Dict[str, Any]]:
    """解析百度搜索结果页（纯函数、单测覆盖真实样本）。

    只取**结果容器**里的条目（`result c-container` / `result-op c-container`），
    跳过"相关搜索/热搜/工具栏"这些非结果块。URL 用容器上的 `mu="真实地址"` ——
    百度自己的 `<a href>` 是 `baidu.com/link?url=...` 跳转，指向不明；
    `mu` 是真实落地页，出处才说得清。
    """
    import html as _html
    import re as _re

    text = str(html or "")
    if not text:
        return []
    # 以结果容器为界切片：容器之后到下一个容器之前就是一个结果。
    # ⚠️ 切片要从**容器的 `<div` 开头**起（不是从 class 属性起）：否则属性文本会被当成正文，
    #    没有 <h3> 的那些 widget 块会把 `class="result-op c-container ..."` 原样漏进摘要里。
    markers = [match.start() for match in _re.finditer(r'class="[^"]*result(?:-op)? c-container[^"]*"', text)]
    rows: List[Dict[str, Any]] = []
    for index, start in enumerate(markers):
        end = markers[index + 1] if index + 1 < len(markers) else min(len(text), start + 12000)
        block = text[start:end]
        head = text.rfind("<div", max(0, start - 400), start)
        if head != -1:
            block = text[head:end]

        # 百度自己的内部 widget（热搜/推荐/百科聚合）不是搜索结果，跳过
        tpl_match = _re.search(r'\stpl="([^"]+)"', block[:400])
        tpl = tpl_match.group(1) if tpl_match else ""
        mu_match = _re.search(r'\smu="([^"]+)"', block[:600])
        mu = _html.unescape(mu_match.group(1)).strip() if mu_match else ""
        if tpl in {"right_citation_aggregated", "new_baikan_index", "recommend_list"}:
            continue
        mu_host = _host_of(mu)
        if mu in {"", "null"} or mu_host.startswith(("nourl.", "recommend_list.", "top.baidu.")):
            continue

        title_match = _re.search(r"<h3[^>]*>(.*?)</h3>", block, _re.S)
        title = ""
        if title_match:
            title = _re.sub(r"<[^>]+>", "", title_match.group(1))
            title = _html.unescape(_re.sub(r"\s+", " ", title)).strip()

        url = mu
        if not url:
            link_match = _re.search(r'href="(http[^"]+)"', block)
            if link_match:
                url = _html.unescape(link_match.group(1)).strip()

        # 摘要：把整块去掉标签后的文本拿来做兜底，但**先去掉标题**（标题会重复出现）
        plain = _re.sub(r"<script.*?</script>", " ", block, flags=_re.S)
        plain = _re.sub(r"<style.*?</style>", " ", plain, flags=_re.S)
        plain = _html.unescape(_re.sub(r"<[^>]+>", " ", plain))
        plain = _re.sub(r"\s+", " ", plain).strip()
        # 百科实体卡这类块的正文里混着**渲染用的内联 JSON**（实测：
        # `…景区 - 百度百科","size":"md"},"abstract":"新疆…`）。它不是给用户看的文本，
        # 留着会污染抽价（把 JSON 里的数字当价格）。见到 JSON 断点就截断。
        for _marker in ('","', '"},"', '":{"', '","size', '"}'):
            _cut = plain.find(_marker)
            if _cut != -1:
                plain = plain[:_cut]
        plain = plain.strip().strip('"\',')
        snippet = plain.replace(title, " ", 1).strip() if title else plain
        snippet = snippet[:300]

        if not title and not snippet:
            continue
        rows.append({
            "title": title,
            "snippet": snippet,
            "url": url,
            "provider": "baidu",
            "cite_site": _host_of(url),
        })
        if len(rows) >= max(1, int(limit or 1)):
            break
    return rows


def _host_of(url: str) -> str:
    raw = str(url or "")
    if "//" not in raw:
        return ""
    return raw.split("//", 1)[-1].split("/", 1)[0]


async def fetch_baidu(query: str, *, max_results: int = 5) -> List[Dict[str, Any]]:
    """用百度搜一次（国内直连可达；实测是当前唯一**真的返回结果**的通用搜索源）。

    ⚠️ **必须先访问首页拿会话 cookie**（BAIDUID/BIDUPSID）**并稍等一下**：
    实测（2026-10-05）不拿会话直接查，前几次能返回真结果，随后就被跳到
    `wappass.baidu.com/.../captcha`「百度安全验证」；而"访问首页→紧接着立刻查"
    同样会被判成机器人。更狠的是**一次推演里 6 个取价点连着查**（每个都访问首页 + 搜索
    = 12 个请求）会立刻触发验证 —— 所以会话要**缓存复用**：进程内保留 cookie，
    30 分钟内不再重复访问首页，每个取价点只发 1 个请求。
    """
    import asyncio
    import httpx

    global _BAIDU_COOKIES, _BAIDU_SESSION_AT  # noqa: PLW0603 - 进程内会话缓存

    async with httpx.AsyncClient(
        timeout=price_timeout_seconds(),
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        **_ssl_verify_kwargs(),
    ) as client:
        if _BAIDU_COOKIES and (time.monotonic() - _BAIDU_SESSION_AT) < BAIDU_SESSION_TTL_SECONDS:
            for name, value in _BAIDU_COOKIES.items():
                try:
                    client.cookies.set(name, value, domain=".baidu.com")
                except Exception:
                    continue
        last_error = ""
        for attempt in range(2):
            need_home = not _BAIDU_COOKIES or (time.monotonic() - _BAIDU_SESSION_AT) >= BAIDU_SESSION_TTL_SECONDS
            if need_home or attempt > 0:
                try:
                    home = await client.get(BAIDU_HOME)
                    _BAIDU_COOKIES = {cookie.name: cookie.value for cookie in client.cookies.jar}
                    _BAIDU_SESSION_AT = time.monotonic()
                    _ = home
                except Exception:
                    pass  # 首页拿不到也继续试搜索（可能仍然可用）
                await asyncio.sleep(0.5 if attempt == 0 else 1.5)
            response = await client.get(BAIDU_SEARCH, params={"wd": query, "rn": 20})
            if response.status_code >= 400:
                last_error = f"baidu HTTP {response.status_code}"
                continue
            text = response.text
            head = text[:3000]
            if "百度安全验证" in head or "wappass.baidu.com" in head:
                last_error = "baidu 返回安全验证页（需要会话/被限流）"
                # 丢掉脏会话，下一轮重新拿
                _BAIDU_COOKIES = {}
                _BAIDU_SESSION_AT = 0.0
                client.cookies.clear()
                continue
            rows = parse_baidu_results(text, limit=max_results)
            if rows or attempt == 1:
                return rows
            last_error = "baidu 结果页没有可用结果块"
        raise RuntimeError(last_error or "baidu 失败")


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


# 票务平台域名（用户 2026-10-07 放开"其他数据源"后加入取价链）：
# 它们**不是官方**，所以标 `platform`（有出处但不冒充可核实）。
TICKET_PLATFORM_MARKERS = (
    "piao.qunar.com",
    "qunar.com",
    "ctrip.com",
    "ly.com",
    "tuniu.com",
    "mafengwo.cn",
    "lvmama.com",
    "piaowang",
)


def host_is_ticket_platform(url: str) -> bool:
    host = str(url or "").strip().split("//", 1)[-1].split("/", 1)[0].lower()
    return any(marker in host for marker in TICKET_PLATFORM_MARKERS)


# 门票价必须**贴着门票语境**：实测（2026-10-07）不带语境的数字会被误采 ——
# 「森林公园」的摘要里出现"40元至100元是较为常见的价格区间"，被当成这个公园的门票 100 元。
# 数字必须带**币种符号或"元"单位**（否则会抓到"314国道"这种路号），
# 且左右 30 字窗口里要出现「门票/票价」——窗口放宽是安全的：只有带币种/单位的数字才算候选。
_TICKET_PRICE_PATTERNS = (
    re.compile(r"[¥￥]\s*(\d+(?:\.\d+)?)"),
    re.compile(r"(\d+(?:\.\d+)?)\s*元"),
)
_TICKET_CONTEXT_MARKERS = ("门票", "票价")
_TICKET_FREE_PATTERN = re.compile(r"(?:门票|票价)[^。；;]{0,8}?(?:免费|免票|免门票|不收费)")
# 区间/模糊表述一律不采（"40元至100元""数十元""几百元"都不是一个确定价）
_TICKET_PRICE_RANGE = re.compile(r"\d+\s*(?:元)?\s*(?:至|到|-|~|—)\s*\d+")
_TICKET_PRICE_VAGUE = ("不等", "常见", "区间", "大约", "左右", "起价", "数十", "几百", "以现场")


def extract_ticket_price(
    text: Any, *, require_name: str = "", name_window: int = 30
) -> Optional[Dict[str, Any]]:
    """从一段文字里抽**确定的门票价**；抽不到就返回 None（纯函数）。

    与 `extract_price_candidates` 的区别（为什么不能直接复用）：
    后者是通用取价（人均/每晚/房价都收），对"门票"太宽 —— 实测把
    「40元至100元是较为常见的价格区间」当成了某个公园的门票价、把"314国道"里的 314
    当成了票价。这里只认带**币种/元单位**、且窗口里有「门票/票价」的数字，
    并且**拒绝区间与模糊表述**；"门票免费/免票"是有效结论（0 元），单独识别。

    `require_name`（2026-10-07 实测加）：要求**数字附近**就提到这个地点。
    踩坑经过：只有"整页提到地名"不够 —— 喀什市政府的景区票价通知页整页确实提到「阳光公园」，
    但紧挨着那个 30 元的是**香妃园**（"香妃园景区门票价格由30元/人·次调整为40元/人"），
    于是阳光公园被安上了一个别人的价、而且是调价前的旧价。
    两道新闸门：① 数字 ±30 字窗口里必须出现地点名；② 出现"调整为"时取**调整后**的价格
    （旧价不是现价），取不到就放弃。

    `name_window`（2026-10-07 加）：窗口大小。摘要里用默认 30 字；
    **官方名录详情页**用 120 —— 那种页面是"一页一个景区"的结构化页面
    （伊犁文旅：「… 门票价格 门票30元/人 地点 … 著名景点 伊宁县天鹅泉湿地公园木栈道观景台 …」），
    地点名离价格常常 40 字开外，30 字窗口会把它误杀（实测 30 字窗口 → None，120 字 → ¥30）。
    放宽的代价可控：调用方是**按名字匹配到的官方页**（不是搜索摘要），页面本身就是这个景区的。
    """
    raw = str(text or "")
    if not raw:
        return None
    name = str(require_name or "").strip()
    span = max(10, int(name_window or 30))
    for pattern in _TICKET_PRICE_PATTERNS:
        for match in pattern.finditer(raw):
            window = raw[max(0, match.start() - span): match.end() + span]
            # 区间/模糊表述只看**数字紧邻的 ±12 字**（2026-10-07 收紧）：
            # 官方整页里别处常出现"40元至100元是常见区间"这类描述，用大窗口会把整页的价全误杀
            # （实测天鹅泉官方页因此什么都抽不出）。而"门票40元至100元"这种**贴着的**区间照样挡得住。
            near = raw[max(0, match.start() - 12): match.end() + 12]
            if _TICKET_PRICE_RANGE.search(near):
                continue
            if any(marker in near for marker in _TICKET_PRICE_VAGUE):
                continue
            if not any(marker in window for marker in _TICKET_CONTEXT_MARKERS):
                continue
            if name and name not in window:
                continue
            try:
                value = float(match.group(1))
            except (TypeError, ValueError):
                continue
            if not (0 < value <= 3000):  # 门票不可能是这个量级 —— 八成抽错了
                continue
            # 调价公告：这个数字可能是**旧价**（"由 30 元调整为 40 元"）→ 取调整后的
            if "调整" in window:
                adjusted = re.search(r"调整(?:为|至)\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*元?", window)
                if not adjusted:
                    continue
                try:
                    value = float(adjusted.group(1))
                except (TypeError, ValueError):
                    continue
                if not (0 < value <= 3000):
                    continue
            return {"value": value, "excerpt": window.strip()[:80], "context": "ticket"}
    free_match = _TICKET_FREE_PATTERN.search(raw)
    if free_match:
        window = raw[max(0, free_match.start() - 30): free_match.end() + 30]
        if name and name not in window:
            return None
        if not any(marker in window for marker in _TICKET_PRICE_VAGUE):
            return {"value": 0.0, "excerpt": window.strip()[:80], "context": "ticket"}
    return None


def ticket_price_from_rows(
    rows: Sequence[Mapping[str, Any]],
    name: str,
    *,
    require_named: bool = True,
) -> Optional[Dict[str, Any]]:
    """从搜索结果里挑一条**这个地点的**门票价；挑不到返回 None（纯函数）。

    两条硬门槛（都是实测逼出来的，2026-10-07）：
      ① 摘要/标题/链接里必须提到**地点名**（去掉括号的核心名）—— 否则会拿别处的价：
         实测「胡杨书院」被塞了金湖杨景区的 40 元、「疏勒历史博物馆」被塞了通用的免票政策；
      ② 数字必须贴着门票语境，且不是区间（见 `extract_ticket_price`）。
    排序：官方白名单页面 > 票务平台 > 其它（同档再看是否有 URL）。
    """
    core = str(name or "").strip()
    for opener, closer in (("(", ")"), ("（", "）")):
        if opener in core and closer in core:
            core = core.split(opener)[0].strip()
    if not core:
        return None
    best: Optional[Dict[str, Any]] = None
    best_score: tuple = ()
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        url = str(row.get("url") or row.get("href") or "")
        blob = f"{row.get('title') or ''} {row.get('body') or row.get('snippet') or ''} {url}"
        if require_named and core not in blob:
            continue
        # ⚠️ 名称必须**挨着价格**（不是"整页提到过"）：实测喀什市政府的票价通知页整页提到
        #    「阳光公园」，但紧挨 30 元的是香妃园 —— 于是阳光公园被安上了别人的价。
        #    例外：**标题本身就是一个具体地点的页**（标题含地点名、且不长）→ 该行就是讲它的，
        #    此时不再要求"名字紧挨数字"（票务平台的标题往往把名字放在价格前面很远）。
        title = str(row.get("title") or "")
        specific_title = bool(core) and core in title and len(title) <= 40
        found = extract_ticket_price(
            f"{title} {row.get('body') or row.get('snippet') or ''}",
            require_name="" if specific_title else (core if require_named else ""),
        )
        if not found:
            continue
        try:
            official = bool(url) and host_is_allowed(url)[0]
        except Exception:
            official = False
        platform = host_is_ticket_platform(url)
        score = (1 if official else 0, 1 if platform else 0, 1 if url else 0)
        if best is None or score > best_score:
            best = {
                "value": float(found["value"]),
                "excerpt": str(found.get("excerpt") or ""),
                "url": url,
                "provider": str(row.get("provider") or ""),
                "publisher": str(row.get("cite_site") or ""),
                "source_date": str(row.get("cite_date") or ""),
                "official_source": official,
                "platform_source": platform,
                "source": "web_page" if official else ("platform" if platform else "web"),
            }
            best_score = score
    return best


def pick_price_from_rows(rows: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """从搜索结果里挑一条价格：**优先有出处、出处更可信的**（纯函数，单测覆盖）。

    为什么不是"第一条有价格的就行"：实测搜狗会返回若干"无标题锚点"的聚合块，
    价格在里面但没有 URL —— 直接用就会得到"有价格、没来源"这种最糟的组合。
    排序用 (有 URL, 出处是否官方/政府域名, 有发布日期, 语境强度)：
    官方来源（gov/景区官网/酒店集团官网）比自媒体聚合页可信得多 —— 实测同一个景区，
    gov 页面写"45元"、自媒体写"免费/以现场为准"，谁该被采信不言而喻。
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
        try:
            official = bool(url) and host_is_allowed(url)[0]
        except Exception:
            official = False
        score = (
            1 if url else 0,
            1 if official else 0,
            1 if str(row.get("cite_date") or "") else 0,
            -int(candidate.get("context_strength") or 3),
        )
        if best is None or score > best_score:
            best = {
                "value": float(candidate["value"]),
                "excerpt": str(candidate.get("excerpt") or ""),
                "url": url,
                "publisher": str(row.get("cite_site") or ""),
                "source_date": str(row.get("cite_date") or ""),
                "provider": str(row.get("provider") or ""),
                "official_source": official,
            }
            best_score = score
    return best


async def search_texts(
    query: str,
    *,
    max_results: int = 3,
    merge: bool = False,
    max_providers: int = 3,
) -> List[Dict[str, Any]]:
    """按配置的提供方顺序搜一次，返回**第一个有结果**的提供方给出的结果。

    `merge=True`（**取价用**）：把最多 `max_providers` 家的结果**合起来**再返回。
    为什么取价要合并（2026-10-07 实测）：各家覆盖是互补的 —— 同一条 query，
    ddgs 给票务平台链接、Bing 给 gov 页面；"先到先得"等于把另一半证据扔掉。
    取价是"能不能找到那个数字"的问题，不是"谁先答"的问题。

    顺序默认 `ddgs,bing,baidu,sogou,search`（2026-10-07 实测重排：百度安全验证页、
    搜狗验证码页；ddgs 新包可用、Bing 不弹验证码且带 gov.cn 链接）。
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

    cache_key = f"{','.join(enabled_providers())}|{max_results}|{int(merge)}|{max_providers}|{query}"
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
        # 配额用完要**留痕到用量文件之外**：方案层据此告诉用户"不是没有这个价，
        # 而是今天的联网次数用完了"——否则用户看到一片"未核实"，会以为我们没查。
        global last_quota_exhausted_at  # noqa: PLW0603
        last_quota_exhausted_at = _time.time()
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
        # 👑 按**近期健康分**排序（相等时保持配置顺序）：取价是逐点串行的，
        #    每多花一秒在一个已经坏掉的源上，就少查一个节点。
        #    实测（2026-10-07）：ddgs TimeoutError、baidu 验证页、sogou 0 条、search 已改名失效，
        #    而 bing 8/8 成功 —— 按配置顺序问就是每次都先撞死源。
        if len(available) > 1:
            available = sorted(available, key=lambda name: -int(_PROVIDER_SCORE.get(name) or 0))
            # 👑 **已知坏掉的源，别在 merge 里再占名额**（2026-10-07 实测）：
            #    merge 默认问 2 家，而"第 2 家"往往就是刚失败过的那个 ——
            #    实测头 3 条 query 花了 12.6s/6.2s/10.9s（ddgs 超时 + baidu 验证页），
            #    之后它们进了冷却才降到 1.5s/条。整轮 9 个取价点因此花了 43.4s。
            #    所以：只问**健康分为非负**的源；全都为负时再退回完整列表（不能把自己饿死）。
            healthy = [name for name in available if int(_PROVIDER_SCORE.get(name) or 0) >= 0]
            available = healthy or available
        merged: List[Dict[str, Any]] = []
        merged_seen: set = set()
        asked = 0

        async def _ask(provider: str) -> List[Dict[str, Any]]:
            """问一家；**绝不抛异常**（失败只记进 attempts，供诊断与披露）。"""
            try:
                if provider == "ddgs":
                    rows = await search_ddgs(query, max_results=max_results)
                elif provider == "bing":
                    rows = await fetch_bing(query, max_results=max_results)
                elif provider == "sogou":
                    rows = await fetch_sogou(query, max_results=max_results)
                elif provider == "baidu":
                    rows = await fetch_baidu(query, max_results=max_results)
                elif provider == "search":
                    rows = await search_duckduckgo(query, max_results=max_results)
                else:
                    return []
                record_request()  # 计入日配额（无论成败都算一次真实请求）
                if rows and rows_look_like_shell(rows):
                    # 壳页/验证页被解析成"结果"：必须记成**失败**，否则这个源会被当成健康的，
                    # 继续占着 merge 的名额、把垃圾混进证据里（实测 baidu 就这样骗过一次）。
                    attempts.append(
                        {
                            "provider": provider,
                            "ok": False,
                            "results": 0,
                            "error": "结果页像搜索壳/验证页（已按失败处理并冷却）",
                        }
                    )
                    _note_provider_result(provider, False)
                    return []
                attempts.append({"provider": provider, "ok": bool(rows), "results": len(rows)})
                _note_provider_result(provider, bool(rows))
                return [dict(row) for row in rows or []]
            except Exception as exc:
                attempts.append({"provider": provider, "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
                _note_provider_result(provider, False)
                return []

        for provider in available:
            if merge and asked >= max(1, max_providers):
                break
            asked += 1
            rows = await _ask(provider)
            if not merge:
                if rows:
                    _last_search_at = _time.monotonic()
                    _search_cache[cache_key] = (_last_search_at, rows)
                    last_search_attempts = attempts
                    return rows
                continue
            # 合并模式：并集去重后继续问下一家（各家覆盖互补），换源之间留点间隔防限流
            for row in rows:
                key = str(row.get("url") or row.get("href") or row.get("title") or "")
                if not key or key in merged_seen:
                    continue
                merged_seen.add(key)
                merged.append(row)
            if provider != available[-1]:
                # ddgs 连续快查会被限流（实测 TimeoutError），但间隔也不能太大：
                # 取价是**逐点串行**的，每多等 1s 就少查一个节点。0.4s 是实测的折中。
                await asyncio.sleep(0.4)
            # official_page 不是通用搜索，跳过
        _last_search_at = _time.monotonic()
        last_search_attempts = attempts
        if merged:
            _search_cache[cache_key] = (_last_search_at, [dict(row) for row in merged])
            return merged
        return []


# —— 提供方熔断状态（进程内）——
_PROVIDER_FAILURES: Dict[str, int] = {}
_PROVIDER_COOLDOWN_UNTIL: Dict[str, float] = {}
# 近期健康分（成功 +2 / 失败 −3，范围 −20..20）：merge 时按它决定先问谁（见 `_note_provider_result`）
_PROVIDER_SCORE: Dict[str, int] = {}
_PROVIDER_LAST_OK: Dict[str, float] = {}
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
    """记一次成败：成功清零；连续失败到阈值就进入冷却（默认 180s）。

    同时维护一个**近期健康分**（`_PROVIDER_SCORE`）：merge 模式下用它决定"先问谁"——
    否则每次都要先花几秒在已经坏掉的源上（实测 ddgs TimeoutError、baidu 验证页）。
    分只增不减地封顶，成功 +2、失败 -3，越大越优先。
    """
    import time as _time

    moment = float(now if now is not None else _time.monotonic())
    name = str(provider)
    score = int(_PROVIDER_SCORE.get(name) or 0)
    _PROVIDER_SCORE[name] = max(-20, min(20, score + (2 if ok else -3)))
    if ok:
        _PROVIDER_FAILURES[name] = 0
        _PROVIDER_COOLDOWN_UNTIL.pop(name, None)
        _PROVIDER_LAST_OK[name] = moment
        return
    failures = int(_PROVIDER_FAILURES.get(name) or 0) + 1
    _PROVIDER_FAILURES[name] = failures
    if failures >= PROVIDER_FAILURE_THRESHOLD:
        _PROVIDER_COOLDOWN_UNTIL[name] = moment + provider_cooldown_seconds()


def name_core_tokens(name: str) -> List[str]:
    """地点名里**能用来判断"这条结果是不是在讲它"**的核心词。

    中文景点名常带"国家/风景/森林/旅游/区/园"这类通词，去掉它们剩下的才是识别词
    （「英吉沙国家湿地公园」→ 英吉沙、湿地公园；「图开沙漠冰雪乐园」→ 图开、沙漠冰雪乐园）。
    太短的（≤1 字）丢掉，避免"湖"字匹配到一切。
    """
    import re

    generic = ("国家", "风景", "旅游", "森林", "地质", "自然保护", "风景区", "景区", "公园", "乐园", "博物馆", "古城")
    core = str(name or "").strip()
    for word in generic:
        core = core.replace(word, " ")
    tokens = [token for token in re.split(r"[\s·]+", core) if len(token) > 1]
    if not tokens:
        tokens = [str(name or "")[:4]] if len(str(name or "")) >= 3 else []
    return tokens


def _page_worth_fetching(url: str, rows: Sequence[Mapping[str, Any]], name: str) -> bool:
    """这个白名单 URL 值不值得抓页（只挡**站点首页**，官方详情页一律照抓）。"""
    from urllib.parse import urlparse

    row = next(
        (
            item
            for item in rows or []
            if isinstance(item, Mapping) and str(item.get("url") or item.get("href") or "") == str(url or "")
        ),
        None,
    )
    text = f"{row.get('title')} {row.get('body')} {row.get('snippet')}" if row else ""
    tokens = name_core_tokens(name)
    if tokens and any(token in text for token in tokens):
        return True
    parsed = urlparse(str(url or ""))
    path = (parsed.path or "").strip("/")
    if path and path.lower() not in {"index.html", "index.htm", "index.php", "index.jsp"}:
        return True  # 有路径 → 详情/栏目页，值得抓
    if parsed.query:
        return True  # 带参数（如 detail?id=581）也是详情页
    return False  # 只剩域名/首页，且摘要没提到这个地点 → 不值得抓


def rows_look_like_shell(rows: Sequence[Mapping[str, Any]]) -> bool:
    """这批"结果"是不是**搜索壳/验证页**被解析出来的垃圾（2026-10-07 实测分歧）。

    为什么要（实测同一分钟内两个探针结论相反）：`probe_provider_yield` 里 baidu 报
    `ok=True results=6`，而直接调 `fetch_baidu` 却抛「返回安全验证页」—— 说明验证页的
    HTML 里也能抠出 6 条"结果"，于是这个源被记成**健康**、继续占着 merge 的名额，
    而它给的全是垃圾。判据（保守，宁可漏判也不误杀真结果）：
      · 所有 URL 都指向搜索站自己（`baidu.com/search`、`wappass`、`sogou`、`bing.com/search`）；
      · 或者标题**全部相同**（壳页里同一句提示重复出现）。
    """
    urls = [str(row.get("url") or row.get("href") or "") for row in rows or [] if isinstance(row, Mapping)]
    titles = [str(row.get("title") or "").strip() for row in rows or [] if isinstance(row, Mapping)]
    if not urls:
        return True
    engine_shells = ("wappass.baidu.com", "/search?", "verify", "captcha", "passport.")
    if all(any(marker in url for marker in engine_shells) for url in urls):
        return True
    if len(titles) >= 2 and len(set(titles)) == 1:
        return True
    return False


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
    """清空熔断状态（测试与"换一换"重试时用）。

    ⚠️ **健康分也要一起清**（2026-10-07 实测踩到）：只清失败计数、留下健康分，
    会让"重置"之后的源顺序仍然受上一次运行影响 —— 单测之间就会互相干扰
    （同一进程里先跑过的用例把 score 改了，后面的用例看到的是别的顺序）。
    """
    _PROVIDER_FAILURES.clear()
    _PROVIDER_COOLDOWN_UNTIL.clear()
    _PROVIDER_SCORE.clear()
    _PROVIDER_LAST_OK.clear()


last_search_attempts: List[Dict[str, Any]] = []
# 最近一次因为**日配额用完**而放弃联网的时刻（方案层据此解释"为什么全是未核实"）
last_quota_exhausted_at: float = 0.0
_last_search_at: float = 0.0
_search_cache: Dict[str, Any] = {}
try:  # 锁在导入时创建（同进程内串行化搜索，避免互相触发限流）
    import asyncio as _asyncio

    _search_lock = _asyncio.Lock()
except Exception:  # pragma: no cover - 极端环境下退化为无锁
    _search_lock = None


def _ddgs_client(timeout: float, proxy: str):
    """取 DDGS 客户端：优先新包 `ddgs`，退回旧包 `duckduckgo_search`。

    2026-10-07 实测：旧包已废弃（运行时会告警"已改名为 ddgs"）且**返回 0 条**；
    装上 `ddgs` 之后同一个查询能返回 5 条真结果（ctrip/qunar/百度百科）。
    所以先试新包；两个都没有就抛错，让上层如实记"没有可用的搜索后端"。
    """
    kwargs: Dict[str, Any] = {"timeout": timeout}
    if proxy:
        kwargs["proxy"] = proxy
    try:
        from ddgs import DDGS  # type: ignore

        return DDGS(**kwargs)
    except Exception:
        from duckduckgo_search import DDGS  # type: ignore

        return DDGS(**kwargs)


async def search_ddgs(query: str, *, max_results: int = 3) -> List[Dict[str, Any]]:
    """ddgs（DuckDuckGo 的**新包**）：实测当前唯一"搜得到东西又不要验证码"的通用后端。"""
    import asyncio

    def _run() -> List[Dict[str, Any]]:
        proxy = search_proxy()
        saved = {key: os.environ.get(key) for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}
        try:
            if proxy:
                os.environ["HTTP_PROXY"] = proxy
                os.environ["HTTPS_PROXY"] = proxy
            # ddgs 被限流时会**一直挂到超时**，而它现在多数时候是挂的（2026-10-07 实测 8 条 query
            # 只轮到 2 次、全是 TimeoutError）。超时就是纯损失：整轮取价 43.4s 里有 ~25s 是它。
            # 所以把它的等待压到 3s（能给结果的场合 3s 内就给了）。
            with _ddgs_client(min(3.0, price_timeout_seconds()), proxy) as ddgs:
                rows = list(ddgs.text(query, max_results=max_results))
            return [
                {
                    "title": str(row.get("title") or ""),
                    "url": str(row.get("href") or row.get("url") or ""),
                    "body": str(row.get("body") or ""),
                    "provider": "ddgs",
                }
                for row in rows
                if isinstance(row, dict)
            ]
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    return await asyncio.wait_for(asyncio.to_thread(_run), timeout=price_timeout_seconds() + 2)


def parse_bing_results(html: str, *, limit: int = 5) -> List[Dict[str, Any]]:
    """从 Bing 结果页 HTML 里取结果（纯函数，可单测）。

    Bing 现在**不弹验证码**（2026-10-07 实测），而且结果里带 `.gov.cn`
    （英吉沙县政府 / 喀什政府）—— 官方页面这条链路就靠它。
    结果块形如 `<li class="b_algo">…<h2><a href="URL">标题</a></h2>…<p>摘要</p>`；
    Bing 的 href 有时是 `https://www.bing.com/ck/a?...&u=a1aHR0cHM6...` 的跳转壳，
    这里一并解码（base64url 段），解不开就丢掉该条（不猜链接）。
    """
    import base64
    import binascii

    def _unwrap(href: str) -> str:
        if "bing.com/ck/a" not in href:
            return href
        match = re.search(r"[?&]u=a1([A-Za-z0-9_\-=]+)", href)
        if not match:
            return ""
        token = match.group(1)
        token += "=" * (-len(token) % 4)
        try:
            decoded = base64.urlsafe_b64decode(token).decode("utf-8", errors="replace")
        except (binascii.Error, ValueError):
            return ""
        return decoded if decoded.startswith("http") else ""

    rows: List[Dict[str, Any]] = []
    for block in re.split(r'<li class="b_algo"', html or "")[1:]:
        match = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not match:
            continue
        url = _unwrap(match.group(1))
        if not url:
            continue
        title = _strip_tags(match.group(2))
        body_match = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        rows.append(
            {
                "title": title,
                "url": url,
                "body": _strip_tags(body_match.group(1)) if body_match else "",
                "provider": "bing",
            }
        )
        if len(rows) >= limit:
            break
    return rows


async def fetch_bing(query: str, *, max_results: int = 5) -> List[Dict[str, Any]]:
    """Bing 网页结果（HTML，不走官方 API key）。"""
    import httpx

    params = {"q": query, "count": str(max(10, max_results * 2)), "setlang": "zh-CN"}
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"}
    proxy = search_proxy() or None
    async with httpx.AsyncClient(timeout=min(15.0, price_timeout_seconds() + 6), headers=headers, proxy=proxy,
                                 follow_redirects=True, **_ssl_verify_kwargs()) as client:
        resp = await client.get("https://cn.bing.com/search", params=params)
    html = resp.text or ""
    if "安全验证" in html or "captcha" in html.lower():
        raise RuntimeError("bing 返回验证页")
    return parse_bing_results(html, limit=max_results)


async def search_duckduckgo(query: str, *, max_results: int = 3) -> List[Dict[str, Any]]:
    """DuckDuckGo（旧包）。**把 WEB_SEARCH_PROXY 传进搜索引擎**。

    实测教训（2026-10-04）：这个模块最初只把代理给了 httpx（抓页面那条路径），
    而 DuckDuckGo 搜索根本没走代理 —— 于是界面上写着"可配置 WEB_SEARCH_PROXY 让搜索走代理"，
    配了也照样连不通。文档承诺与代码行为必须一致，所以这里显式传 `proxy=`，
    并额外在调用期间设置 HTTP(S)_PROXY 环境变量（DDGS 内部用 requests/urllib3，
    两层都覆盖），调用结束立刻恢复现场。

    ⚠️ 2026-10-07：旧包实测返回 0 条，新包能返回真结果（见 `search_ddgs`）。
    这里保留旧包作为兜底，顺序上排在新包之后。
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


async def search_texts_all(query: str, *, max_results: int = 6, providers: int = 3) -> List[Dict[str, Any]]:
    """合并多家的搜索（`search_texts(merge=True)` 的便捷包装，供探针/诊断调用）。"""
    return await search_texts(query, max_results=max_results, merge=True, max_providers=providers)


async def search_result_count(query: str, *, max_results: int = 3) -> int:
    """诊断用：真正搜一次，返回结果条数（走同一套提供方顺序与代理处理）。"""
    rows = await search_texts(query, max_results=max_results)
    return len(rows)


def _maybe_record_domain_hints(rows: Sequence[Mapping[str, Any]], name: str) -> None:
    """价格**不是**从白名单页面拿到的时候，顺手记下"像官网但没白名单"的域名。

    为什么要单独抽出来（2026-10-07）：门票专线加进来之后有**多个**非官方返回路径
    （票务平台摘要、通用摘要、什么都没查到），任何一条漏记都会让"人工扩白名单"这个
    闭环断掉 —— 于是"像官网的域名"永远进不了待确认清单，可核实价格也就永远多不起来。
    记录失败绝不影响取价结果。
    """
    try:
        from_hints = official_domain_hints(rows, query_name=name)
        if from_hints:
            record_domain_candidates(from_hints)
    except Exception:
        pass


async def lookup_price(
    name: str,
    city: str,
    *,
    max_results: int = 3,
    prefer_official: bool = False,
    kind: str = "",
) -> Dict[str, Any]:
    """一个地点的取价：先搜，再**能直抓官方页面就直抓**（用户口径：加入酒店官网/民宿白名单）。

    顺序与口径：
      1. 搜索 → 从摘要里抽价（门票/景区类命中率最高）；
      2. 若命中的链接在**允许清单**里（政府页/酒店集团官网/用户配置域名），直抓该页面，
         从**页面正文**抽价 —— 这比摘要可靠（摘要可能只是标题里的数字）；
      3. 两者都拿不到 → 返回 `value=None` 并说明，绝不填数字。

    页面来的价格标 `source="web_page"`：**白名单里的官方/政府页面**按"可核实"计
    （2026-10-05 用户拍板：官方自己写的票价、带 URL 可点开，就算可核实）；
    不在白名单的页面根本不抓，搜索摘要仍然是估算（`source="web"`）。

    **门票价另走一条更严的路**（2026-10-07 用户放开"其他数据源"后加的）：
      1. 先问**多家**搜索源并合并结果（`search_texts_all`，各家覆盖互补）；
      2. 用 `ticket_price_from_rows` 抽**贴门票语境**的确定价，且要求摘要里真的提到这个地点；
      3. 排序：官方白名单页面（`web_page`，可核实）> **票务平台**（`platform`，有出处但非官方）
         > 其它（`web`，估算）。
    为什么要单独一条：通用抽价会把「40元至100元是较为常见的价格区间」当成某个公园的门票，
    也会把别处的票价记到这个地点头上（实测「胡杨书院」被塞了金湖杨景区的 40 元）。

    `prefer_official=True`（用于"把已有的网摘价升级成官方价"）：
      - 查询词补「官网」，把官方页面顶到前面；
      - 摘要价先放一边，遍历**多个**白名单候选页面（默认只试第一个），
        只要有一个页面正文抽出价格就采用 —— 升级这类请求，官方页面价才是目标。
    """
    query = f"{city} {name} 门票 价格 人均 每晚"
    if prefer_official:
        query = f"{city} {name} 门票价格 官网"
    # 玩点（景区/人文）**只认门票专线**：实测（2026-10-07 伊宁真跑）通用抽价会给玩点安上
    # 完全不相干的钱 ——
    #   · 「草原遗迹 ¥3000」来自一篇"3条路线+5大景点+人均3000元"的游记（那是整趟预算）；
    #   · 「英塔木镇天山公社 ¥388」来自另一篇游记里**那拉提**的民宿房价。
    # 餐饮/住宿才需要通用抽价（人均/每晚）。判不了的钱就留"未核实"，不能塞个数字进去。
    is_play = str(kind or "").strip().lower() in {"scenic", "cultural", "landmark", "park", "nature", "人文", "自然"}
    try:
        rows = await search_texts(query, max_results=max_results + 2, merge=True, max_providers=2)
    except Exception as exc:
        return build_observation(name=name, value=None, source="web", provider=",".join(enabled_providers())) | {
            "error": f"{type(exc).__name__}: {str(exc)[:120]}"
        }

    # 门票专线（严格）：只有"贴门票语境 + 摘要提到这个地点"才采信
    ticket_pick = ticket_price_from_rows(rows, name)
    if ticket_pick is not None:
        ticket_value = float(ticket_pick.get("value") or 0)
        ticket_source = str(ticket_pick.get("source") or "web")
        # 有价就采；「免费/免票」这种 0 元只在**票务平台/白名单官方页**上采 ——
        # 内容农场说的"免票"不足以让我们对用户断言"这个景点不要钱"（unknown ≠ 0）
        if ticket_value > 0 or ticket_source in {"platform", "web_page"}:
            if ticket_source != "web_page":
                _maybe_record_domain_hints(rows, name)
            return build_observation(
                name=name,
                value=ticket_value,
                source=ticket_source,
                source_url=str(ticket_pick.get("url") or ""),
                excerpt=str(ticket_pick.get("excerpt") or ""),
                provider=str(ticket_pick.get("provider") or ""),
                publisher=str(ticket_pick.get("publisher") or ""),
                source_date=str(ticket_pick.get("source_date") or ""),
                label="门票",
            )

    snippet_pick = pick_price_from_rows(rows)
    # 通用抽价的两道闸门（都是实跑抓到的误采）：
    #  ① 抽到的若是**门票口径**的数字，必须过门票专线那一关 ——
    #     否则「40元至100元是较为常见的价格区间」这种区间价会从后门溜进来；
    #  ② 抽到 **0 元（"免费"）** 时，只有**白名单官方页/票务平台**才允许下这个断言 ——
    #     实测内容农场（educfa.cn）的通用"免票政策详解"页让「英吉沙国家湿地公园」变成免费，
    #     而它讲的根本不是这个公园。unknown ≠ 0：拿不到就不能替它说"不要钱"。
    if snippet_pick is not None:
        pick_excerpt = str(snippet_pick.get("excerpt") or "")
        pick_url = str(snippet_pick.get("url") or "")
        pick_value = float(snippet_pick.get("value") or 0)
        if any(marker in pick_excerpt for marker in _TICKET_CONTEXT_MARKERS) and extract_ticket_price(pick_excerpt) is None:
            snippet_pick = None
        elif pick_value <= 0 and not (host_is_allowed(pick_url)[0] or host_is_ticket_platform(pick_url)):
            snippet_pick = None
    allowed_urls: List[str] = []
    for row in rows or []:
        candidate_url = str(row.get("url") or row.get("href") or "")
        if candidate_url and host_is_allowed(candidate_url)[0] and candidate_url not in allowed_urls:
            allowed_urls.append(candidate_url)
    # 👑 **别抓"站点首页"**（2026-10-07 实测，省时间也省配额）：这些小众景点在 cn.bing.com 上
    #    常常搜不到自己的页面 —— 实测「伊宁 图开沙漠冰雪乐园 门票 价格」返回的是伊宁百科/伊宁政务网
    #    首页，而白名单页面抓取是最贵的一步（整轮取价 43.7s / 9 个点）。首页上不可能有这个景点的票价。
    #    ⚠️ 只挡**首页**（没有路径/参数、摘要也没提到地点）：官方**详情页**必须照抓 ——
    #    天鹅泉的 ¥30 就来自 `ylwl.xjyl.gov.cn/show/scenicspot/detail?id=581`，
    #    而它的摘要里并不一定带着地点名。判据保守，宁可多抓一页，也不漏掉官方详情页。
    relevant_urls = [url for url in allowed_urls if _page_worth_fetching(url, rows, name)]
    # 抓几个白名单页面：玩点（景区/人文）抓 2 个、其他 1 个；official 模式多抓几个。
    page_urls = (
        relevant_urls[:3] if prefer_official else relevant_urls[: (2 if is_play else 1)]
    )

    for page_url in page_urls:
        page = await fetch_page_text_stable(page_url)
        if not page.get("ok"):
            continue
        page_text = str(page.get("text") or "")
        if is_play:
            # 玩点只认**门票口径**的价格（同一套闸门：贴门票语境 + 地点名挨着数字）
            ticket_on_page = extract_ticket_price(page_text, require_name=name)
            if ticket_on_page:
                return build_observation(
                    name=name,
                    value=float(ticket_on_page["value"]),
                    source="web_page",
                    source_url=page_url,
                    excerpt=str(ticket_on_page.get("excerpt") or ""),
                    provider="official_page",
                    label="门票",
                )
            continue
        candidates = extract_price_candidates(page_text)
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

    page_url = page_urls[0] if page_urls else ""
    if snippet_pick is not None and not is_play:
        # 退回摘要价（估算）说明**白名单页面没帮上忙** —— 这时候才值得记"像官网的域名"，
        # 让人工确认后把白名单扩起来（下次这类价格就有机会变成"可核实"）。
        # ⚠️ 玩点不走这条路（见上面 is_play 的说明：会拿到别处的钱）。
        _maybe_record_domain_hints(rows, name)
        # 出处标注：白名单官方页 → 可核实；**票务平台** → 有出处但非官方（不许冒充官方价）；
        # 其余摘要 → 估算。实测（2026-10-07）：携程页面来的 ¥29 原来被写成 `web`，
        # 用户看到的是"网摘估算"，其实它来自票务平台 —— 标错来源等于把出处说小了。
        _snippet_url = str(snippet_pick.get("url") or "")
        try:
            _snippet_official = bool(_snippet_url) and host_is_allowed(_snippet_url)[0]
        except Exception:
            _snippet_official = False
        _snippet_platform = host_is_ticket_platform(_snippet_url)
        _snippet_source = "web_page" if _snippet_official else ("platform" if _snippet_platform else "web")
        return build_observation(
            name=name,
            value=float(snippet_pick["value"]),
            source=_snippet_source,
            source_url=_snippet_url,
            excerpt=str(snippet_pick.get("excerpt") or ""),
            provider=str(snippet_pick.get("provider") or ""),
            publisher=str(snippet_pick.get("publisher") or ""),
            source_date=str(snippet_pick.get("source_date") or ""),
        )
    _maybe_record_domain_hints(rows, name)
    return build_observation(
        name=name,
        value=None,
        source="web",
        provider=",".join(enabled_providers()),
        source_url=page_url,
    )


def extract_description(text: Any, name: str, city: str = "", *, max_chars: int = 160) -> str:
    """从页面正文里挑一段**在讲这个地点**的简介；挑不到返回空串（纯函数）。

    用户 2026-10-07 的裁决：「简介…要真简介就得有能抓的官方页面 → 那就抓取官网页面」。
    这里只做"从已抓到的正文里选句子"，校验一律复用 `place_quality.clean_web_knowledge`
    （必须提到这个地点、不能是抓错页面的英文帮助页）。**挑不到就返回空串**，
    调用方保持"高德资料"或"暂无简介"，绝不为了好看凑一句。
    """
    from .place_quality import clean_web_knowledge, desc_relevance_problem

    raw = " ".join(str(text or "").split())
    core = str(name or "").strip()
    for opener, closer in (("(", ")"), ("（", "）")):
        if opener in core and closer in core:
            core = core.split(opener)[0].strip()
    if not raw or not core or core not in raw:
        return ""
    # 先判**整页**：抓错页面（英文帮助中心、验证页之类）时，页里任何一句都别用 ——
    # 只判候选句是不够的（实测：英文帮助页里恰好也出现了"阳光公园"三个字）。
    if desc_relevance_problem(raw[:4000], name, city):
        return ""
    sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;])", raw) if item.strip()]
    # 拼接下一句时要挡住的"噪声句"（实测政府页正文里紧跟着联系电话/编辑信息）
    noise = ("联系电话", "电话：", "邮编", "来源：", "编辑：", "上一篇", "下一篇", "版权所有", "扫一扫")
    for index, sentence in enumerate(sentences):
        name_pos = sentence.find(core)
        if name_pos < 0 or len(sentence) < 12:
            continue
        candidate = sentence
        # 地名前面还挂着一串空格分隔的导航（"首页 政务公开 通知公告 …"）→ 从**最后一个空格**处截，
        # 不能按固定字数截（会截出半句话）；"喀什市香妃园景区…" 这种就干净了。
        if name_pos > 12:
            cut = sentence.rfind(" ", 0, name_pos)
            if cut > 0:
                candidate = sentence[cut + 1:].strip()
        if len(candidate) < max_chars // 2 and index + 1 < len(sentences):
            nxt = sentences[index + 1]
            if (
                core not in nxt
                and not any(marker in nxt for marker in noise)
                and len(candidate) + len(nxt) <= max_chars
            ):
                candidate = f"{candidate}{nxt}"
        cleaned = clean_web_knowledge(candidate[: max_chars + 40], name, city, max_chars=max_chars)
        if cleaned:
            return cleaned
    return ""


async def lookup_description(name: str, city: str, *, max_pages: int = 2) -> Dict[str, Any]:
    """给一个地点找一段**可核实**的简介：只在白名单（gov/景区官网）页面上找。

    返回 `{text, source, source_url, retrieved_at}`；没找到就是 `text=""`（如实为空）。
    为什么不从任意网页摘要里凑：简介是**要对用户断言"这个地方是什么"**，比价格更该有出处；
    用户口径也明确是"抓取官网页面"。
    """
    result: Dict[str, Any] = {"text": "", "source": "web_page", "source_url": "", "retrieved_at": time.time()}
    query = f"{city} {name} 简介 介绍 官网"
    try:
        rows = await search_texts(query, max_results=6, merge=True, max_providers=2)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:100]}"
        return result
    urls: List[str] = []
    for row in rows or []:
        url = str(row.get("url") or row.get("href") or "")
        if not url or url in urls:
            continue
        try:
            if host_is_allowed(url)[0]:
                urls.append(url)
        except Exception:
            continue
    for url in urls[: max(1, max_pages)]:
        page = await fetch_page_text_stable(url)
        if not page.get("ok"):
            continue
        text = extract_description(str(page.get("text") or ""), name, city)
        if text:
            result["text"] = text
            result["source_url"] = url
            result["retrieved_at"] = float(page.get("retrieved_at") or time.time())
            return result
    return result


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
    label: str = "",
) -> Dict[str, Any]:
    """一条取价结果：**必须带出处**，并标明"可核实"还是"估算"。

    `publisher`/`source_date` 是给"这条价格什么时候发的"用的 —— 价格会过期，
    用户需要判断新鲜度（真实结果里带发布日期，如 2026-09-19）。

    口径（2026-10-05 用户拍板，2026-10-07 增补票务平台）：
      · `web_page` = **白名单里的官方/政府页面正文** → **可核实**（官方自己写的票价，带 URL 可点开）；
      · `platform` = **票务平台**（携程/去哪儿等）→ **有出处但非官方**，标「票务平台价（非官方）」，
        不计入"可核实合计"；
      · `web` = 搜索结果摘要/自媒体页 → 永远是**估算**。
    """
    verified_sources = {"provider", "vendor", "official", "amap", "web_page"}
    normalized = str(source)
    return {
        "name": str(name or ""),
        "value": value,
        "currency": "CNY",
        "source": normalized,
        "source_url": str(source_url or ""),
        "excerpt": str(excerpt or "")[:160],
        "provider": str(provider or ""),
        "publisher": str(publisher or "")[:60],
        "source_date": str(source_date or "")[:40],
        "retrieved_at": float(retrieved_at or time.time()),
        # 只有"可核实来源"（供应商/官方接口/**白名单官方页面**）才算 verified
        "verified": normalized in verified_sources,
        "estimated": normalized not in verified_sources,
        # 这条价是什么口径（门票/人均/每晚…）：合并回节点时用它决定怎么写
        "label": str(label or ""),
    }
