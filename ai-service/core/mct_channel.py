"""文旅部（MCT）**全国 A 级景区**通道：`lyfw.mct.gov.cn/api/marker/list`。

为什么值得接（2026-10-07 实测，参数是试出来的）
--------------------------------------------
地方名录（伊犁那条）只要那个州有；而文旅部这个接口是**全国**的，字段还特别合用：
```json
{"name": "喀什地区金湖杨景区", "longitude": "...", "latitude": "...",
 "address": "新疆维吾尔自治区喀什地区泽普县", "introduce": "景区以广袤的天然胡杨林…",
 "web_link_url": "https://…", "appointment_link_url": "https://…"}
```
即：**官方登记的名称/坐标/地址/官方简介 + 景区官网 + 官方预约平台**。

实测口径（避免以后再来回猜）
  · `city=<行政区划码>` 才是地区过滤（`province_id`/`city_id`/`area_id` 都被忽略，
    返回的是全国第一页 —— 这一条试了 9 种参数名才确认）；
  · `type_id=1` = **5A 景区**（全国 359 条）；type_id=3 是酒店/饭店，不是景区；
  · 覆盖**很窄**：喀什地区 3 条（金湖杨、喀什古城、帕米尔）、巴州 2 条、伊犁州 2 条 ——
    也就是说它**只解决大牌 5A 景点**，3A/4A 与未评级景点不在这里（那些仍走地方名录/通用搜索）。

用法：`mct_lookup(name, city="喀什")` → 命中的 marker（没有则 None）；
`official_site_of(marker)` / `official_booking_of(marker)` → 官网与官方预约平台。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

MCT_API = "https://lyfw.mct.gov.cn/api"
MCT_TYPE_SCENIC = 1  # 实测：1 = 5A 景区（全国 359 条）
MCT_UA = "OmniRoute/1.0 (+travel planning; contact: local)"


def _mct_headers() -> Dict[str, str]:
    return {
        "User-Agent": MCT_UA,
        "Accept": "application/json",
        "Referer": "https://lyfw.mct.gov.cn/site/special/scenic",
    }


def mct_cache_path() -> str:
    import os

    raw = str(os.getenv("MCT_MARKER_CACHE_FILE") or "").strip()
    if raw:
        return raw
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mct_marker_cache.json")


def mct_cache_ttl_seconds() -> float:
    import os

    raw = str(os.getenv("MCT_MARKER_CACHE_TTL_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 86400.0
    return value if value >= 0 else 86400.0


def _load_cache() -> Dict[str, Any]:
    import json

    try:
        with open(mct_cache_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(data: Mapping[str, Any]) -> None:
    import json

    try:
        with open(mct_cache_path(), "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _get_json(url: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=15.0, headers=_mct_headers(), follow_redirects=True) as client:
        response = await client.get(url, params=dict(params or {}))
        response.raise_for_status()
        payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def mct_city_code(city: str) -> Optional[int]:
    """把"喀什/库尔勒/伊宁"这类城市名换成文旅部的行政区划码（查省份树，带缓存）。"""
    import time

    text = str(city or "").strip()
    if not text:
        return None
    cache = _load_cache()
    codes = cache.get("city_codes") if isinstance(cache.get("city_codes"), dict) else {}
    fetched_at = float(cache.get("city_codes_at") or 0.0)
    if not codes or time.time() - fetched_at > mct_cache_ttl_seconds():
        try:
            payload = await _get_json(f"{MCT_API}/marker/province/list")
        except Exception:
            return None
        codes = {}
        for province in payload.get("data") or []:
            for child in province.get("children") or []:
                name = str(child.get("name") or "").strip()
                if name and child.get("id"):
                    codes[name] = int(child["id"])
        cache["city_codes"] = codes
        cache["city_codes_at"] = time.time()
        _save_cache(cache)
    if text in codes:
        return codes[text]
    # 模糊匹配：喀什 → 喀什地区；库尔勒 → 巴音郭楞蒙古自治州（用户给的对应关系）
    aliases = {"库尔勒": "巴音郭楞蒙古自治州", "伊宁": "伊犁哈萨克自治州", "喀什": "喀什地区"}
    target = aliases.get(text, "")
    for name, code in codes.items():
        if text and (text in name or name in text):
            return int(code)
        if target and target in name:
            return int(code)
    return None


async def mct_markers(city: str, *, type_id: int = MCT_TYPE_SCENIC) -> List[Dict[str, Any]]:
    """拿这个地区的 A 级景区标记（默认 5A）；带缓存、失败返回空列表（不编造）。"""
    import time

    code = await mct_city_code(city)
    if not code:
        return []
    key = f"{type_id}:{code}"
    cache = _load_cache()
    items = cache.get("markers") if isinstance(cache.get("markers"), dict) else {}
    entry = items.get(key) if isinstance(items, dict) else None
    if isinstance(entry, dict) and time.time() - float(entry.get("fetched_at") or 0.0) <= mct_cache_ttl_seconds():
        rows = entry.get("rows")
        if isinstance(rows, list):
            return [dict(row) for row in rows if isinstance(row, Mapping)]
    try:
        payload = await _get_json(
            f"{MCT_API}/marker/list",
            {"type_id": int(type_id), "city": int(code), "pageNum": 1, "pageSize": 50},
        )
    except Exception:
        return []
    data = payload.get("data") or {}
    rows = data.get("list") if isinstance(data, dict) else data
    rows = [dict(row) for row in (rows or []) if isinstance(row, Mapping)]
    items[key] = {"fetched_at": time.time(), "city": city, "rows": rows}
    cache["markers"] = items
    _save_cache(cache)
    return rows


async def mct_lookup(name: str, *, city: str = "") -> Optional[Dict[str, Any]]:
    """按名字找官方登记的景区（复用地方名录那套"全称/简称互相包含"的匹配）。"""
    from .official_directory import match_entry

    markers = await mct_markers(city)
    if not markers:
        return None
    hit = match_entry(name, [{"name": row.get("name"), "url": row.get("web_link_url") or ""} for row in markers])
    if not hit:
        return None
    for row in markers:
        if str(row.get("name")) == str(hit.get("name")):
            return row
    return None


def official_site_of(marker: Mapping[str, Any]) -> str:
    return str((marker or {}).get("web_link_url") or "").strip()


def official_booking_of(marker: Mapping[str, Any]) -> str:
    return str((marker or {}).get("appointment_link_url") or "").strip()


def profile_of(marker: Mapping[str, Any]) -> Dict[str, Any]:
    """给方案用的**官方登记信息**（名称/地址/简介/官网/官方预约平台）。"""
    return {
        "official_name": str((marker or {}).get("name") or ""),
        "official_address": str((marker or {}).get("address") or ""),
        "official_intro": str((marker or {}).get("introduce") or "")[:400],
        "official_site": official_site_of(marker),
        "official_booking": official_booking_of(marker),
        "lnglat": _coords_of(marker),
        "source": "mct",
    }


def _coords_of(marker: Mapping[str, Any]) -> List[float]:
    try:
        lng = float(str((marker or {}).get("longitude") or "").strip())
        lat = float(str((marker or {}).get("latitude") or "").strip())
    except (TypeError, ValueError):
        return []
    if lng == 0.0 and lat == 0.0:
        return []
    return [lng, lat]


async def lookup_mct_price(
    name: str,
    *,
    city: str = "",
    fetch_page: Any = None,
) -> Dict[str, Any]:
    """文旅部通道取价：命中 5A 景点后，去它登记的**官网/官方预约页**上找票价。

    为什么只能这样：接口本身**不给票价**（字段里没有价格）。所以：
      · 命中且有官网/预约页 → 抓那一页，用同一套门票抽取（要贴门票语境 + 页面是自己的）；
      · 命中但没有官网/预约页（实测喀什 3 条里只有 1 条有）→ 如实说明，拿不到价；
      · 没命中（3A/4A/未评级）→ 如实说明这条通道覆盖不到。
    返回观测（含 `value`/`source`/`note`）。
    """
    from .web_price_tool import build_observation, extract_ticket_price

    marker = await mct_lookup(name, city=city)
    if not marker:
        return build_observation(name=name, value=None, source="official", provider="mct") | {
            "note": f"文旅部名录（{city}）里没有这个景点 —— 这条通道只覆盖 5A",
        }
    profile = profile_of(marker)
    page_url = profile.get("official_booking") or profile.get("official_site")
    if not page_url:
        return build_observation(name=name, value=None, source="official", provider="mct") | {
            "note": f"文旅部登记的是「{profile['official_name']}」，但它没有登记官网/官方预约平台 —— 无从取价",
            "mct_profile": profile,
        }
    fetch = fetch_page
    _registry_hosts = []
    try:
        from urllib.parse import urlparse

        _registry_hosts = [urlparse(page_url).hostname or ""]
    except Exception:
        _registry_hosts = []
    if fetch is None:
        from .web_price_tool import fetch_page_text_stable as fetch

    try:
        # ⚠️ 必须把**名录登记的那个主机**放行：这些景区官网不在我们的白名单里，
        #    `fetch_page_text` 默认会直接拒绝（实测：喀什古城的官网因此永远抓不到）。
        page = await fetch(page_url, allow_hosts=[host for host in _registry_hosts if host])
    except TypeError:
        # 兼容只接受位置参数的旧签名/测试替身
        page = await fetch(page_url)
    except Exception as exc:
        return build_observation(name=name, value=None, source="official", provider="mct") | {
            "source_url": page_url,
            "note": f"官方页抓取失败（{type(exc).__name__}）",
            "mct_profile": profile,
        }
    text = str(page.get("text") or "") if isinstance(page, Mapping) else str(page or "")
    ticket = extract_ticket_price(text, require_name=name, name_window=120) if text else None
    if not ticket:
        return build_observation(name=name, value=None, source="official", provider="mct") | {
            "source_url": page_url,
            "note": f"文旅部登记的官方页（{page_url[:48]}）里没有写出这个景点的票价 —— 不猜",
            "mct_profile": profile,
        }
    return build_observation(
        name=name,
        value=float(ticket["value"]),
        source="official",
        source_url=page_url,
        excerpt=str(ticket.get("excerpt") or ""),
        provider="mct",
        label="门票",
    ) | {"mct_profile": profile}
