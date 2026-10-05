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

# 允许直接抓取的域名后缀：政府/事业单位这类公开页面。
# 平台电商页（携程/美团/点评…）不在其中：它们的价格在登录态接口里，抓页面拿不到，
# 而且抓取违反其服务条款 —— 与其"看起来能拿到"不如明确不做。
# 景区官网各异，靠 `WEB_PRICE_HOST_SUFFIXES` 显式扩充（配置决策，不做隐藏假设）。
DEFAULT_OFFICIAL_SUFFIXES = (".gov.cn", "gov.cn", ".gov.com.cn", ".org.cn", ".museum.cn")
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
    raw = str(os.getenv("WEB_PRICE_PROVIDERS") or "search,official_page").strip()
    items = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return items or ["search"]


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
    """诊断每个取价提供方能不能用（本机实测：DuckDuckGo 直接失败，会在这里如实暴露）。"""
    results: Dict[str, Any] = {}
    providers = enabled_providers()
    if "search" in providers:
        try:
            count = await search_result_count(query, max_results=3)
            results["search"] = {"ok": count > 0, "results": count, "proxy": bool(search_proxy())}
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
    if "official_page" in providers:
        # 注意：这一项只是**声明能力**（有允许清单、可按 URL 抓取），不是"可达性"。
        # 早先这里写 ok=True，结果 reachable 变成 true —— 而搜索明明失败了，
        # 于界面上会显示"取价源可达"，是彻头彻尾的误导。能力与可达性必须分开。
        results["official_page"] = {
            "configured": True,
            "mode": "allowlist",
            "suffixes": list(official_host_suffixes())[:4],
        }
    # 可达性只由**真正去取数的提供方**决定（当前是搜索；官方页面要给定 URL 才知道通不通）
    probed = [item for key, item in results.items() if key == "search"]
    return {
        "providers": results,
        "reachable": any(item.get("ok") for item in probed) if probed else False,
        "probed": ["search"] if probed else [],
    }


async def search_texts(query: str, *, max_results: int = 3) -> List[Dict[str, Any]]:
    """搜一次并返回原始结果（带代理与超时配置）—— 供推演里的取价与诊断共用。

    与 `search_result_count` 同一套代理处理：显式 `proxy=` + 调用期间覆盖
    HTTP(S)_PROXY，结束后立刻恢复（绝不把代理留在进程环境里）。
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
            return [row for row in rows if isinstance(row, dict)]
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    return await asyncio.wait_for(asyncio.to_thread(_run), timeout=price_timeout_seconds() + 2)


async def search_result_count(query: str, *, max_results: int = 3) -> int:
    """真正去搜一次（供诊断使用），**把 WEB_SEARCH_PROXY 传进搜索引擎**。

    实测教训（2026-10-04）：这个模块最初只把代理给了 httpx（抓页面那条路径），
    而 DuckDuckGo 搜索根本没走代理 —— 于是界面上写着"可配置 WEB_SEARCH_PROXY 让搜索走代理"，
    配了也照样连不通。文档承诺与代码行为必须一致，所以这里显式传 `proxy=`，
    并额外在调用期间设置 HTTP(S)_PROXY 环境变量（DDGS 内部用 requests/urllib3，
    两层都覆盖），调用结束立刻恢复现场。
    """
    rows = await search_texts(query, max_results=max_results)
    return len(rows)


def build_observation(
    *,
    name: str,
    value: Optional[float],
    source: str,
    source_url: str = "",
    excerpt: str = "",
    provider: str = "",
    retrieved_at: Optional[float] = None,
) -> Dict[str, Any]:
    """一条取价结果：**必须带出处**，并明确它是估算而不是已核实。"""
    verified_sources = {"provider", "vendor", "official", "amap"}
    return {
        "name": str(name or ""),
        "value": value,
        "currency": "CNY",
        "source": str(source),
        "source_url": str(source_url or ""),
        "excerpt": str(excerpt or "")[:160],
        "provider": str(provider or ""),
        "retrieved_at": float(retrieved_at or time.time()),
        # 搜索/网页来的价格永远是估算：不允许写成 verified
        "verified": str(source) in verified_sources,
        "estimated": str(source) not in verified_sources,
    }
