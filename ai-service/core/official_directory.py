"""官方文旅**名录**通道：把"景区名 → 官方详情页 → 官方写的门票价"这条链路做成可复用的一层。

为什么要有它（2026-10-07 实测）
------------------------------
通用搜索这条路对这些小众景点已经走到头了：
  · `cn.bing.com` 现在返回的是**随机热门页**（实测同一个 query 两次拿到"Switch怎么查看系统版本"、
    "微信"这类与查询完全无关的结果），`site:` 限定也被忽略 —— 不是查询写法问题，是它把我们当机器人；
  · ddgs 被限流（TimeoutError）、baidu 回安全验证页、sogou 0 条。
于是"查 12 / 补 0"是**如实**的，但用户要的是票价，不是"查不到"。

而官方文旅站里**真的有票价**：
  · `http://ylwl.xjyl.gov.cn/show/scenicspot/detail?id=581`（伊宁县天鹅泉湿地公园）
    正文写着「门票价格 门票30元/人」—— 我们先前那条可核实价就是这么来的；
  · 它的**名录页** `…/show/scenicspot/list?nodeId=2465` 是纯 HTML（不是 JS 壳）：
    `<a href="/show/scenicspot/detail?id=581&nodeId=2465">` + `<h2>伊宁县天鹅泉湿地公园</h2>`。
    也就是说：**名录可以枚举**，于是可以建"景区名 → 官方页"的索引，直接去官方页拿价。

口径
----
· 抓的是**政府域名**（gov.cn）的页面，命中即 `web_page` = **可核实**（2026-10-05 用户拍板：
  官方自己写的票价、带 URL 可点开，就算可核实）；
· 索引落盘缓存（默认 24h），页面按 `page_cap` 限量、请求间隔与其它取数一致 —— 少打扰官方站；
· 匹配不上、页面里没有票价 → 如实返回 None（**绝不猜**）。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urljoin

# 渠道登记表：目前**逐条实测**过的只有伊犁文旅（伊宁用）。
# 其它地区待补 —— 猜过的域名都不通（喀什 `kswl.xjks.gov.cn` 502、`xjks.gov.cn/whly` 404、
# 巴州 `bzwl.xinjiang.gov.cn` 502），所以宁可留空，也不写一个没验证过的地址进去。
CHANNELS: List[Dict[str, Any]] = [
    {
        "key": "yili_wenlv",
        "label": "伊犁州文化和旅游（ylwl.xjyl.gov.cn）",
        "base_url": "http://ylwl.xjyl.gov.cn",
        "list_path": "/show/scenicspot/list?nodeId=2465",
        "page_param": "page",
        "city_hints": ("伊宁", "伊犁", "伊宁县", "霍城", "察布查尔", "新源", "特克斯", "昭苏", "巩留", "尼勒克"),
        "verified": "2026-10-07：名录页纯 HTML；天鹅泉详情页正文含「门票价格 门票30元/人」",
    },
]


def channels_for_city(city: str) -> List[Dict[str, Any]]:
    """按城市挑渠道（`city_hints` 里出现即算命中）；挑不到就返回空列表。"""
    text = str(city or "")
    if not text:
        return []
    return [
        channel
        for channel in CHANNELS
        if any(hint in text or text in hint for hint in channel.get("city_hints") or ())
    ]


def parse_directory_list(html: str, base_url: str) -> List[Dict[str, str]]:
    """从名录页 HTML 里抠出 `[{name, url}]`。

    结构（实测 2026-10-07）：
        <a href="/show/scenicspot/detail?id=581&nodeId=2465"> … <h2>伊宁县天鹅泉湿地公园</h2> …
    ⚠️ 链接里带 `&nodeId=`，所以正则**不能**要求 `id` 后面紧跟引号 —— 第一版就是这么写的，
    结果"一个 detail 链接都没抓到"，差点误判成"名录是 JS 渲染的"。
    """
    import re

    out: List[Dict[str, str]] = []
    seen: set = set()
    for block in re.split(r"<li\b", html or "", flags=re.I)[1:]:
        link = re.search(r'href="([^"]*scenicspot/detail\?id=\d+[^"]*)"', block, flags=re.I)
        name = re.search(r"<h2[^>]*>(.*?)</h2>", block, flags=re.S)
        if not link or not name:
            continue
        clean_name = re.sub(r"<[^>]+>", "", name.group(1)).strip()
        url = urljoin(str(base_url).rstrip("/") + "/", link.group(1).strip())
        if not clean_name or url in seen:
            continue
        seen.add(url)
        out.append({"name": clean_name, "url": url})
    return out


def _normalize(text: str) -> str:
    """去掉行政前缀/通词，留可比对的核心字（"伊宁县天鹅泉湿地公园" → "天鹅泉"）。"""
    value = str(text or "")
    for word in (
        "新疆维吾尔自治区", "自治区", "自治州", "地区", "伊宁县", "伊宁市", "伊犁哈萨克", "伊犁州", "伊犁",
        "喀什市", "喀什地区", "喀什", "库尔勒市", "巴音郭楞", "巴州", "县", "市", "区",
    ):
        value = value.replace(word, "")
    for word in ("国家", "旅游景区", "风景区", "景区", "湿地公园", "森林公园", "公园", "乐园", "博物馆", "古城"):
        value = value.replace(word, "")
    return value.strip()


def match_entry(name: str, entries: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, str]]:
    """在名录里找这个名字：核心字互相包含即算命中（官方名录常带县名/全称，我们只有简称）。"""
    core = _normalize(name)
    if not core:
        return None
    best: Optional[Dict[str, str]] = None
    for entry in entries or []:
        entry_name = str(entry.get("name") or "")
        entry_core = _normalize(entry_name)
        if not entry_core:
            continue
        if core == entry_core or core in entry_core or entry_core in core:
            # 更长的包含关系更具体（"天鹅泉" vs "天鹅泉湿地公园"），命中即返回第一个
            if best is None or len(entry_core) > len(_normalize(best.get("name") or "")):
                best = {"name": entry_name, "url": str(entry.get("url") or "")}
    return best


def directory_cache_path() -> str:
    import os

    raw = str(os.getenv("OFFICIAL_DIRECTORY_CACHE_FILE") or "").strip()
    if raw:
        return raw
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "official_directory_cache.json")


def directory_cache_ttl_seconds() -> float:
    import os

    raw = str(os.getenv("OFFICIAL_DIRECTORY_CACHE_TTL_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 86400.0
    return value if value >= 0 else 86400.0


def load_cached_index(channel_key: str) -> Optional[List[Dict[str, str]]]:
    import json
    import time

    try:
        with open(directory_cache_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None
    item = (data or {}).get(str(channel_key))
    if not isinstance(item, dict):
        return None
    fetched_at = float(item.get("fetched_at") or 0.0)
    if directory_cache_ttl_seconds() <= 0 or time.time() - fetched_at > directory_cache_ttl_seconds():
        return None
    entries = item.get("entries")
    if not isinstance(entries, list):
        return None
    return [{"name": str(row.get("name")), "url": str(row.get("url"))} for row in entries if isinstance(row, Mapping)]


def save_cached_index(channel_key: str, entries: Sequence[Mapping[str, Any]]) -> None:
    import json
    import time

    path = directory_cache_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data[str(channel_key)] = {
        "fetched_at": time.time(),
        "entries": [{"name": str(row.get("name")), "url": str(row.get("url"))} for row in entries or []],
    }
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def build_index(channel: Mapping[str, Any], *, pages: int = 3, fetch_text: Any = None) -> List[Dict[str, str]]:
    """抓名录页 1..pages，拼出 `[{name, url}]`（抓到就写缓存；抓不到返回空列表）。"""
    entries: List[Dict[str, str]] = []
    seen: set = set()
    fetch = fetch_text
    if fetch is None:
        from .web_price_tool import fetch_page_html_stable as fetch  # 名录要**HTML**（纯文本里没有链接）

    base = str(channel.get("base_url") or "").rstrip("/")
    for page in range(1, max(1, int(pages)) + 1):
        path = str(channel.get("list_path") or "")
        url = f"{base}{path}" + (f"&{channel.get('page_param') or 'page'}={page}" if page > 1 else "")
        try:
            page_result = await fetch(url)
        except Exception:
            continue
        if isinstance(page_result, Mapping):
            html = str(page_result.get("html") or page_result.get("text") or "")
        else:
            html = str(page_result or "")
        if not html:
            continue
        for entry in parse_directory_list(html, base):
            if entry["url"] in seen:
                continue
            seen.add(entry["url"])
            entries.append(entry)
    if entries:
        save_cached_index(str(channel.get("key") or ""), entries)
    return entries


async def directory_lookup(
    name: str,
    *,
    city: str = "",
    fetch_text: Any = None,
    pages: int = 3,
) -> Optional[Dict[str, str]]:
    """按渠道找这个景点的**官方详情页**（先查缓存，未命中再抓名录）。"""
    for channel in channels_for_city(city):
        cached = load_cached_index(str(channel.get("key") or ""))
        entries = cached if cached else await build_index(channel, pages=pages, fetch_text=fetch_text)
        hit = match_entry(name, entries)
        if hit:
            return {**hit, "channel": str(channel.get("key") or ""), "channel_label": str(channel.get("label") or "")}
    return None


async def lookup_official_price(
    name: str,
    *,
    city: str = "",
    fetch_text: Any = None,
    pages: int = 3,
) -> Dict[str, Any]:
    """**定向通道的入口**：景区名 → 官方名录 → 官方详情页 → 门票价。

    返回观测（与 `web_price_tool.build_observation` 同构）：
      · 命中官方页且页面里有**门票口径**的价 → `value` 有数、`source="web_page"`（可核实）；
      · 命中官方页但页面里没有票价 → `value=None` + `note` 说明；
      · 名录里没有这个景点 → `value=None` + `note` 说明。
    """
    from .web_price_tool import build_observation, extract_ticket_price

    hit = await directory_lookup(name, city=city, fetch_text=fetch_text, pages=pages)
    if not hit:
        return build_observation(name=name, value=None, source="official", provider="official_directory") | {
            "note": f"官方名录（{city}）里没有找到「{name}」，这条通道拿不到价"
        }
    fetch = fetch_text
    if fetch is None:
        from .web_price_tool import fetch_page_text_stable as fetch

    try:
        page = await fetch(hit["url"])
    except Exception as exc:
        return build_observation(name=name, value=None, source="official", provider="official_directory") | {
            "source_url": hit["url"],
            "note": f"官方页抓取失败（{type(exc).__name__}）",
        }
    text = str(page.get("text") or "") if isinstance(page, Mapping) else str(page or "")
    # 官方详情页是"一页一个景区"的结构化页面，地点名离价格常 >40 字 → 窗口放宽到 120
    ticket = extract_ticket_price(text, require_name=name, name_window=120) if text else None
    if not ticket:
        # 再给一次机会：**页面本身就是这个景区**（名字来自名录匹配），
        # 只要正文里确实出现这个景区的名字、且价签是"门票价格"这种结构化写法，就采信；
        # 采信的结果里带上官方页名，用户点开就能核对。
        from .web_price_tool import name_core_tokens

        tokens = name_core_tokens(name)
        if text and tokens and any(token in text for token in tokens):
            ticket = extract_ticket_price(text)
            if ticket and "门票" not in str(ticket.get("excerpt") or ""):
                ticket = None
    if not ticket:
        return build_observation(name=name, value=None, source="official", provider="official_directory") | {
            "source_url": hit["url"],
            "note": f"官方页（{hit['name']}）里没有写出这个景点的门票价 —— 不猜",
        }
    return build_observation(
        name=name,
        value=float(ticket["value"]),
        source="web_page",
        source_url=hit["url"],
        excerpt=str(ticket.get("excerpt") or ""),
        provider="official_directory",
        label="门票",
    ) | {"official_page_name": hit["name"], "channel": hit.get("channel_label") or ""}
