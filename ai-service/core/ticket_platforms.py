"""票务平台通道：去哪儿 / 携程的**结构化票价接口**（2026-10-07 用户："既然票务平台能用为什么不用票务系统"）。

为什么不走"平台搜索页"：实测全是 JS 壳或直接不给内容 ——
`piao.qunar.com/ticket/list.htm` 返回 0 字符、`you.ctrip.com/searchsite/sight/` 7387 字符无景区名、
同程 `ly.com/scenery/search` 每次返回同一个 41KB 页面（参数被忽略）、美团 5KB 壳。

但两家的**接口**是通的（都不需要登录）：
  ① **携程建议词** `m.ctrip.com/restapi/h5api/globalsearch/search?action=gsonline&source=globalonline&keyword=`
     → `{"data":[{"id":144508327,"code":"sight_144508327","word":"阿吾赞沟森林公园", ...}]}`
     拿到 **sightId + 详情页 URL**（`you.ctrip.com/sight/<拼音><城市id>/<sightId>.html`）；
  ② **去哪儿门票列表** `piao.qunar.com/ticket/list.json?keyword=&page=1`
     → 结构化票种与价格，例如博斯腾湖：
     `{"sightName":"博斯腾湖","qunarPrice":"16","price":{"sightPrice":{"priceList":[
        {"typeName":"博斯腾湖大河口景区门票成人票","qunarPrice":"42"}, …]}}}`

口径（重要，避免"把子景点的票当成这个景区的票"）
------------------------------------------------
实测踩点：博斯腾湖命中的票是「博斯腾湖**大河口**景区门票成人票」（子景区）、
喀什古城命中的是「**买买提的老房子**门票成人票」（古城里的一个院子）。
所以本模块：
  · **名字必须对得上**（复用 `official_directory.match_entry` 的归一化与类型分组匹配）；
  · 优先取 **成人票**；取不到成人票才退回该条目的最低价（并在文案里写清是"起价"）；
  · 票种名照抄进 `excerpt`（「博斯腾湖大河口景区门票成人票」），**用户能看见这是哪个票**；
  · `source="platform"` → 沿用既有口径「票务平台价（非官方）」，**不冒充官方价**。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

CTRIP_SUGGEST_API = "https://m.ctrip.com/restapi/h5api/globalsearch/search"
QUNAR_TICKET_API = "https://piao.qunar.com/ticket/list.json"
PLATFORM_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def platform_headers() -> Dict[str, str]:
    return {
        "User-Agent": PLATFORM_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }


async def _get_json(url: str, params: Mapping[str, Any]) -> Dict[str, Any]:
    import httpx

    from .web_price_tool import record_request

    record_request()  # 计入同一本日配额（避免无意中超量打扰平台）
    async with httpx.AsyncClient(timeout=12.0, headers=platform_headers(), follow_redirects=True) as client:
        response = await client.get(url, params=dict(params))
        response.raise_for_status()
        payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _name_key(text: Any) -> str:
    from .official_directory import _normalize

    return _normalize(text)


def pick_sight(entries: Sequence[Mapping[str, Any]], name: str, *, key: str = "sightName") -> Optional[Dict[str, Any]]:
    """在一批平台条目里挑**就是这个名字**的那条（归一化后相等优先，其次包含）。"""
    target = _name_key(name)
    if not target:
        return None
    exact: Optional[Dict[str, Any]] = None
    loose: Optional[Dict[str, Any]] = None
    for entry in entries or []:
        if not isinstance(entry, Mapping):
            continue
        entry_key = _name_key(entry.get(key) or entry.get("word") or entry.get("name"))
        if not entry_key:
            continue
        if entry_key == target:
            exact = dict(entry)
            break
        if loose is None and (target in entry_key or entry_key in target):
            loose = dict(entry)
    return exact or loose


# 票种过滤（2026-10-07 实测逼出来的）：去哪儿会把**跟团游/演艺/住宿**产品挂在景区条目下，
# 不过滤就会把「赛里木湖+库尔德宁景区三日游 ¥2999」当成"天鹅泉湿地公园门票"。
_TICKET_GOOD = ("门票",)
_TICKET_BAD = (
    "日游",
    "跟团",
    "包车",
    "自由行",
    "拼小",
    "演出",
    "演艺",
    "剧场",
    "温泉",
    "酒店",
    "住宿",
    "民宿",
    "餐",
    "接送",
    "导游",
    "套餐",
    "旅拍",
    "写真",
    "营地",
    "露营",
    # 实测（2026-10-07 喀什博物馆）：平台上「门票预约或讲解1.5h」这种**讲解/导览产品**
    # 名字里带"门票"，但价不是门票价 —— 一起挡掉，宁可"平台没有门票价"。
    "讲解",
    "导览",
    "语音",
    "人工",
)


def _is_ticket_product(type_name: str) -> bool:
    text = str(type_name or "")
    if not text:
        return False
    if any(word in text for word in _TICKET_BAD):
        return False
    return any(word in text for word in _TICKET_GOOD)


def adult_ticket_price(entry: Mapping[str, Any]) -> Dict[str, Any]:
    """从去哪儿的条目里取**门票口径**的票价（成人票优先，其次该条目内最便宜的门票）。

    三条硬规矩（都是实跑抓到的错）：
      · **只认票种名里带"门票"** 的产品：不过滤就会把三日游/演艺票当成景区门票（天鹅泉曾被安 ¥2999）；
      · **成人票优先**，拿不到成人票就用同条目里最便宜的门票（`kind="from"`，文案里写"起"）；
      · **不用条目级 `qunarPrice`**（它可能是跟团价）—— 宁可返回"平台没有门票价"。
    """
    prices: List[Dict[str, Any]] = []
    price_block = entry.get("price")
    if isinstance(price_block, Mapping):
        sight_price = price_block.get("sightPrice")
        if isinstance(sight_price, Mapping):
            for item in sight_price.get("priceList") or []:
                if isinstance(item, Mapping):
                    prices.append(dict(item))
    tickets = [item for item in prices if _is_ticket_product(str(item.get("typeName") or ""))]
    if not tickets:
        return {}
    # 成人票优先；同档里优先"票种名包含景区核心名"的（避免把子景点/别处的票当成本景区门票）
    core = _name_key(entry.get("sightName"))
    adults = [item for item in tickets if "成人票" in str(item.get("typeName") or "")]
    pool = adults or tickets
    if core:
        named = [item for item in pool if core in _name_key(item.get("typeName"))]
        if named:
            pool = named
    picked = None
    for item in pool:
        value = _to_float(item.get("qunarPrice"))
        if value is None:
            continue
        if picked is None or value < float(picked["value"]):
            picked = {"value": value, "ticket_name": str(item.get("typeName") or ""), "kind": "from"}
            if "成人票" in str(item.get("typeName") or ""):
                picked["kind"] = "adult"
                break
    if not picked:
        return {}
    # 票种名里有没有这个景区的核心名 —— 用来告诉上层"这张票是景区本身还是里面的某个点"
    picked["ticket_scope"] = "sight" if core and core in _name_key(picked["ticket_name"]) else "related"
    return picked


def _to_float(value: Any) -> Optional[float]:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def qunar_detail_url(sight_id: Any) -> str:
    return f"https://piao.qunar.com/ticket/detail_{sight_id}.html" if sight_id else ""


async def lookup_qunar_price(name: str, *, fetch_json: Any = None) -> Dict[str, Any]:
    """去哪儿门票：景区名 → 结构化票种与价格。

    返回观测：命中 → `value` 有数、`source="platform"`、`provider="qunar"`、`excerpt` 是票种名；
    没命中/没价 → `value=None` + `note`（如实说明，绝不猜）。
    """
    from .web_price_tool import build_observation

    fetch = fetch_json or (lambda url, params: _get_json(url, params))
    try:
        payload = await fetch(QUNAR_TICKET_API, {"keyword": name, "page": "1"})
    except Exception as exc:
        return build_observation(name=name, value=None, source="platform", provider="qunar") | {
            "note": f"去哪儿接口失败（{type(exc).__name__}）",
        }
    data = payload.get("data") if isinstance(payload, Mapping) else {}
    items = (data or {}).get("list") or (data or {}).get("sightList") or []
    entry = pick_sight(items, name)
    if not entry:
        return build_observation(name=name, value=None, source="platform", provider="qunar") | {
            "note": f"去哪儿没有「{name}」这个景区（返回 {len(items)} 条近似结果，都不匹配）",
        }
    picked = adult_ticket_price(entry)
    if not picked:
        return build_observation(name=name, value=None, source="platform", provider="qunar") | {
            "note": (
                f"去哪儿有「{entry.get('sightName')}」，但它下面的产品里没有**门票**口径的价"
                "（可能只有跟团游/演艺票）—— 不采信"
            ),
            "source_url": qunar_detail_url(entry.get("sightId")),
        }
    value = float(picked["value"])
    ticket_name = str(picked.get("ticket_name") or "")
    is_from = picked.get("kind") == "from"
    scope = str(picked.get("ticket_scope") or "")
    suffix = "（起价）" if is_from else ""
    if scope == "related":
        suffix += "（票种名与景区名不同，按平台原文标注）"
    excerpt = (ticket_name or str(entry.get("sightName") or "")) + suffix
    return build_observation(
        name=name,
        value=value,
        source="platform",
        source_url=qunar_detail_url(entry.get("sightId")),
        excerpt=excerpt,
        provider="qunar",
        label="门票",
    ) | {
        "platform": "去哪儿",
        "platform_sight_name": str(entry.get("sightName") or ""),
        "ticket_name": ticket_name,
        "price_kind": "from" if is_from else "adult",
        "ticket_scope": scope,
    }


async def lookup_ctrip_price(name: str, *, fetch_json: Any = None, fetch_page: Any = None) -> Dict[str, Any]:
    """携程：建议词接口拿到**这个景区**的详情页，再在详情页上抽票价。

    （详情页在 `you.ctrip.com`，实测 200；`gs.ctrip.com/html5/...` 那个移动壳页现在只回 177 字符。）
    """
    from .web_price_tool import build_observation, extract_ticket_price

    fetch = fetch_json or (lambda url, params: _get_json(url, params))
    try:
        payload = await fetch(
            CTRIP_SUGGEST_API, {"action": "gsonline", "source": "globalonline", "keyword": name}
        )
    except Exception as exc:
        return build_observation(name=name, value=None, source="platform", provider="ctrip") | {
            "note": f"携程建议词接口失败（{type(exc).__name__}）",
        }
    sights = [
        dict(item)
        for item in (payload.get("data") or [])
        if isinstance(item, Mapping) and str(item.get("code") or "").startswith("sight")
    ]
    entry = pick_sight(sights, name, key="word")
    url = str((entry or {}).get("url") or "")
    if not entry or not url:
        return build_observation(name=name, value=None, source="platform", provider="ctrip") | {
            "note": f"携程建议词里没有「{name}」这个景区（{len(sights)} 条景区候选都不匹配）",
        }
    page_fetch = fetch_page
    if page_fetch is None:
        from .web_price_tool import fetch_page_text_stable as page_fetch

    try:
        page = await page_fetch(url, allow_hosts=["you.ctrip.com"])
    except TypeError:
        page = await page_fetch(url)
    except Exception as exc:
        return build_observation(name=name, value=None, source="platform", provider="ctrip") | {
            "source_url": url,
            "note": f"携程详情页抓取失败（{type(exc).__name__}）",
        }
    text = str(page.get("text") or "") if isinstance(page, Mapping) else str(page or "")
    ticket = extract_ticket_price(text, require_name=name, name_window=120) if text else None
    if not ticket:
        return build_observation(name=name, value=None, source="platform", provider="ctrip") | {
            "source_url": url,
            "note": "携程详情页里没有写出这个景区的票价（可能是免费开放/需现场购票）",
            "platform": "携程",
        }
    return build_observation(
        name=name,
        value=float(ticket["value"]),
        source="platform",
        source_url=url,
        excerpt=str(ticket.get("excerpt") or ""),
        provider="ctrip",
        label="门票",
    ) | {"platform": "携程", "platform_sight_name": str(entry.get("word") or "")}


async def lookup_platform_price(name: str, *, fetch_json: Any = None, fetch_page: Any = None) -> Dict[str, Any]:
    """票务平台通道入口：**去哪儿优先**（它有结构化票种），其次携程（详情页抽价）。"""
    qunar = await lookup_qunar_price(name, fetch_json=fetch_json)
    if qunar.get("value") is not None:
        return qunar
    ctrip = await lookup_ctrip_price(name, fetch_json=fetch_json, fetch_page=fetch_page)
    if ctrip.get("value") is not None:
        return ctrip
    # 两条都没拿到：把两边的说明合并，便于事后回答"为什么没拿到"
    return ctrip | {
        "note": "；".join(item for item in (str(qunar.get("note") or ""), str(ctrip.get("note") or "")) if item)
    }
