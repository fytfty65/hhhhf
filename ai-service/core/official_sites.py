"""**景区官方站点/官方购票渠道**登记表（2026-10-07，用户直接给了三个域名）。

用户提供的源（原话要点）：
  · 巴楚红海景区 `www.bachu.gov.cn`（巴楚县政府网）：公示**优惠政策**（半票群体），全价票待确认；
  · 罗布人村寨 `www.xjlbr.com`（独立官网）：门票 35 元/人、观光车票 15 元/人；
    页面明确声明"未授权任何第三方机构或个人代理门票业务，请通过官方小程序购票"；
  · 巴音布鲁克 `www.byblk.cn`：联票 140 元（含门票+区间车）+ 优惠明细；官方购票走微信小程序。

本机实测（`tools/probe_official_ticket_sites.py` / `probe_official_ticket_pages.py` /
`probe_official_api.py`）：
  · 三个域名**都可达**（https 200）；
  · 但**票价页是客户端渲染**：罗布人村寨所有子页都返回同一份 2490 字符壳，
    巴音布鲁克的门票页只有 1432 字符；JS 包里没有真正的票价串（35/15/140 是巧合数字）；
    也没有 `/api/...` 票价接口；公众号文章匿名抓只拿到 480 字符。
  · 所以：**本机取不到这三家的票价**，能可靠提供的只有"官方渠道信息"（官网 + 官方小程序）。

因此本模块做两件**诚实**的事：
  ① 把域名登记为官方站点（`registered_hosts()`），供抓页时放行；页面读得到票价就按 `official`（可核实）采；
  ② 读不到时**如实说明**，并把**官方购票渠道**（官网/小程序名）交给方案与前端展示 ——
     这本身就是用户需要的信息（"别在第三方买"是官方自己写的）。
     用户提供的数字只作为 `reference_prices` **原样登记并标明出处**，不冒充我们抓到的一手数据。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

# 用户提供的官方源（名字/别名/适用城市/官网/官方小程序/备注）
OFFICIAL_SITES: List[Dict[str, Any]] = [
    {
        "key": "bachu_honghai",
        "name": "巴楚红海景区",
        "aliases": ["红海景区", "巴楚红海", "红海湾景区"],
        "city_hints": ["喀什", "巴楚", "麦盖提"],
        "site": "https://www.bachu.gov.cn/",
        "miniprogram": "",
        "note": "政府网公示的是**门票优惠政策**（半票群体条件），全价票需另行确认票务子页",
        "price_page_rendered": False,
        "reference_prices": [],
        "verified": "2026-10-07 用户提供域名；本机实测可达（https 200）",
    },
    {
        "key": "loburen_village",
        "name": "罗布人村寨",
        "aliases": ["罗布人村寨景区", "罗布人村寨风景区"],
        "city_hints": ["库尔勒", "巴州", "尉犁", "巴音郭楞"],
        "site": "https://www.xjlbr.com/",
        "miniprogram": "罗布人村寨",
        "note": "官方声明：**未授权任何第三方机构或个人代理门票业务**，请通过官方小程序购票",
        "price_page_rendered": True,
        "reference_prices": [
            {"label": "景区门票", "value": 35},
            {"label": "观光车票", "value": 15},
        ],
        "reference_source": "用户提供的官方页数字（本机抓取不到：票价页为客户端渲染）",
        "verified": "2026-10-07 用户提供域名与票价；本机实测域名可达、票价页 JS 渲染",
    },
    {
        "key": "bayinbuluke",
        "name": "巴音布鲁克景区",
        "aliases": ["巴音布鲁克", "巴音布鲁克草原", "天鹅湖景区"],
        "city_hints": ["库尔勒", "巴州", "和静", "巴音郭楞"],
        "site": "https://www.byblk.cn/",
        "miniprogram": "巴音布鲁克旅游",
        "note": "官网票务页公示**联票 140 元（含门票+区间车）**；官方购票平台为微信小程序『巴音布鲁克旅游』",
        "price_page_rendered": True,
        "reference_prices": [{"label": "联票（门票+区间车）", "value": 140}],
        "reference_source": "用户提供的官方页数字（本机抓取不到：票价页为客户端渲染）",
        "verified": "2026-10-07 用户提供域名与票价；本机实测域名可达、票价页 JS 渲染",
    },
]


def registered_hosts() -> List[str]:
    """登记表的域名（供抓页放行：这些是**官方**站点，读得到就按可核实采）。"""
    from urllib.parse import urlparse

    hosts = []
    for entry in OFFICIAL_SITES:
        host = urlparse(str(entry.get("site") or "")).hostname or ""
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def official_site_for(name: str, city: str = "") -> Optional[Dict[str, Any]]:
    """按景区名（+城市）找登记项：名字归一化后互相包含即算命中。

    城市只在**两边都给得出城市线索**时用作过滤，避免"乙地的同名景区"被错配。
    """
    from .official_directory import _normalize

    target = _normalize(name)
    if len(target) < 2:
        return None
    city_text = str(city or "")
    for entry in OFFICIAL_SITES:
        names = [str(entry.get("name") or "")] + [str(item) for item in entry.get("aliases") or []]
        for candidate in names:
            core = _normalize(candidate)
            if not core:
                continue
            if not (target == core or target in core or core in target):
                continue
            hints = [str(item) for item in entry.get("city_hints") or []]
            if city_text and hints and not any(hint in city_text or city_text in hint for hint in hints):
                continue
            return dict(entry)
    return None


def channel_summary(entry: Mapping[str, Any]) -> Dict[str, Any]:
    """给方案/前端用的**官方渠道**信息（不含我们没抓到的价格）。"""
    return {
        "official_name": str((entry or {}).get("name") or ""),
        "official_site": str((entry or {}).get("site") or ""),
        "official_miniprogram": str((entry or {}).get("miniprogram") or ""),
        "official_channel_note": str((entry or {}).get("note") or ""),
        "source": "official_site_registry",
        "verified": str((entry or {}).get("verified") or ""),
    }


async def lookup_official_site_price(
    name: str, *, city: str = "", fetch_text: Any = None
) -> Dict[str, Any]:
    """登记表通道取价：能读到官方页就抽价；读不到就**如实说明**并给出官方渠道。

    返回观测（与 `web_price_tool.build_observation` 同构）：
      · 页面有**门票口径**的价 → `value` 有数、`source="official"`（可核实）；
      · 页面是客户端渲染/没有写出票价 → `value=None` + `note`，并把 `official_channel` 带上；
      · 登记表里没有这个景区 → `value=None` + `note`。
    """
    from .web_price_tool import build_observation, extract_ticket_price

    entry = official_site_for(name, city)
    if not entry:
        return build_observation(name=name, value=None, source="official", provider="official_site") | {
            "note": f"官方站点登记表里没有「{name}」",
        }
    channel = channel_summary(entry)
    base = build_observation(name=name, value=None, source="official", provider="official_site") | {
        "source_url": channel["official_site"],
        "official_channel": channel,
    }
    if entry.get("price_page_rendered"):
        # 实测：票价页是 JS 渲染，服务端抓不到 → 不猜价，只给官方渠道
        reference = "、".join(
            f"{item.get('label')} {item.get('value')} 元" for item in entry.get("reference_prices") or []
        )
        return base | {
            "note": (
                f"官方票价页是**客户端渲染**（本机取不到正文）；官方渠道："
                + (f"微信小程序『{channel['official_miniprogram']}』" if channel["official_miniprogram"] else channel["official_site"])
                + (f"；你提供的官方页参考：{reference}（{entry.get('reference_source')}）" if reference else "")
            ),
            "official_channel": channel | {"reference_prices": entry.get("reference_prices") or []},
        }
    fetch = fetch_text
    if fetch is None:
        from .web_price_tool import fetch_page_text_stable as fetch

    try:
        page = await fetch(channel["official_site"], allow_hosts=registered_hosts())
    except TypeError:
        page = await fetch(channel["official_site"])
    except Exception as exc:
        return base | {"note": f"官方页抓取失败（{type(exc).__name__}）"}
    text = str(page.get("text") or "") if isinstance(page, Mapping) else str(page or "")
    ticket = extract_ticket_price(text, require_name=name, name_window=160) if text else None
    if not ticket:
        return base | {"note": "官方页里没有写出这个景区的门票价（可能是优惠政策页/需点击子页）—— 不猜"}
    return build_observation(
        name=name,
        value=float(ticket["value"]),
        source="official",
        source_url=channel["official_site"],
        excerpt=str(ticket.get("excerpt") or ""),
        provider="official_site",
        label="门票",
    ) | {"official_channel": channel}
