"""节点配图兜底链：实景照片 → 卫星影像 → 街道地图 → 什么都不给（但绝不给假图）。

为什么需要
----------
用户实测反馈"规划的地点还是看不到真实照片"：高德对很多 POI（公园/果园/村寨…）**不返回实景图**，
前端只能显示一块纯色占位。这里给出**真实的地理影像**兜底，并且**如实标注它是什么**：

1. 首选 **Esri World Imagery 卫星影像导出**（真实地表影像：湖、沙漠、城区一眼可辨）；
   实测高德静态地图的 `style` 参数不生效（style=6/7/8 与默认返回同一张图），所以卫星图走 Esri；
2. 退一步用我们已经生成的**高德街道静态地图**（有标记点）；
3. 两者都没有（没有坐标/没有 key）→ 返回 None，前端继续显示"暂无实景照片"。

**永远不做的**：拿别的景点照片、或通用图库图冒充"这个地点"。那不是缺图问题，是捏造问题。
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

SATELLITE_CAPTION = "位置示意：卫星影像（真实地表，非实景照片）"
STREET_CAPTION = "位置示意：街道地图（非实景照片）"
ESRI_IMAGERY_EXPORT = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"
)
# 维基图片通道：默认关闭（见 fetch_wikimedia_photo 的说明：robot policy 403 + commons TLS 被断）
WIKIMEDIA_ENABLED = False
WIKIMEDIA_USER_AGENT = "OminRoute/1.0 (personal research project; contact: set-your-email-here)"


def parse_lnglat(value: Any) -> Optional[list]:
    """容错解析坐标：接受 [lng, lat] / "lng,lat" / {"lng","lat"}；非法一律 None。"""
    if isinstance(value, Mapping):
        lng, lat = value.get("lng"), value.get("lat")
        try:
            return [float(lng), float(lat)]
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        parts = value.split(",")
        if len(parts) != 2:
            return None
        value = parts
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 2:
        try:
            lng, lat = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
        if lng == 0.0 and lat == 0.0:
            return None
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            return None
        return [lng, lat]
    return None


def satellite_image_url(lnglat: Any, size: str = "600,400", span_deg: float = 0.012) -> str:
    """Esri World Imagery 卫星影像导出 URL（真实地表影像，无标记点，前端自己叠定位点）。"""
    coords = parse_lnglat(lnglat)
    if not coords:
        return ""
    lng, lat = coords
    half = abs(float(span_deg)) / 2
    bbox = f"{lng - half},{lat - half},{lng + half},{lat + half}"
    return (
        f"{ESRI_IMAGERY_EXPORT}?bbox={bbox}&bboxSR=4326&imageSR=4326"
        f"&size={size}&format=png&f=image"
    )


def photo_fallback_for(node: Mapping[str, Any], street_map_url: str = "") -> Optional[Dict[str, Any]]:
    """给"没有实景照片"的节点选一张**真实影像/地图**，并带上如实说明。

    返回 `{url, kind, caption, is_real, attribution}`；没有可用图时返回 None（前端显示占位）。
    """
    if not isinstance(node, Mapping):
        return None
    if has_real_photo(node):
        return None  # 已经有实景照片，不需要兜底
    coords = node.get("lnglat") or node.get("location")
    satellite = satellite_image_url(coords)
    if satellite:
        return {
            "url": satellite,
            "kind": "satellite",
            "caption": SATELLITE_CAPTION,
            "is_real": False,
            "attribution": "影像：Esri World Imagery",
        }
    if street_map_url:
        return {
            "url": str(street_map_url),
            "kind": "street_map",
            "caption": STREET_CAPTION,
            "is_real": False,
            "attribution": "底图：高德地图",
        }
    return None


def wikimedia_api_url(name: str) -> str:
    """维基百科按标题取主图（pageimages）+ 授权信息（imageinfo/extmetadata）的 API URL。"""
    from urllib.parse import quote

    title = quote(str(name or "").strip())
    return (
        "https://zh.wikipedia.org/w/api.php?action=query&format=json&prop=pageimages|images"
        "&piprop=original&titles=" + title
    )


def wikimedia_file_info_url(file_title: str) -> str:
    """取某个维基文件（File:xxx）的作者与许可：extmetadata 里的 Artist/LicenseShortName。"""
    from urllib.parse import quote

    return (
        "https://commons.wikimedia.org/w/api.php?action=query&format=json&prop=imageinfo"
        "&iiprop=url|extmetadata&titles=" + quote(str(file_title or "").strip())
    )


def amap_place_text_url(keyword: str, city: str, key: str, limit: int = 5) -> str:
    """高德 place/text（extensions=all）——按**精确名称**搜同一个地点，为的是把它自己的实景图取回来。"""
    from urllib.parse import quote

    return (
        "https://restapi.amap.com/v3/place/text"
        f"?key={quote(str(key or ''))}&keywords={quote(str(keyword or '').strip())}"
        f"&city={quote(str(city or '').strip())}&offset={int(limit)}&page=1&extensions=all"
    )


def amap_photo_urls(poi: Mapping[str, Any], limit: int = 3) -> list:
    """从高德 POI 里取出实景图 URL（官方数据，可直接用）。

    只接受 http(s) 且能识别的图片扩展名；`http` 一律升级成 `https`（页面是 https，
    混用会被浏览器按混合内容拦掉）。没有任何实景图时返回空列表 —— 不编、不凑。
    """
    if not isinstance(poi, Mapping):
        return []
    photos = poi.get("photos")
    if not isinstance(photos, (list, tuple)):
        return []
    urls: list = []
    for item in photos:
        raw = ""
        if isinstance(item, Mapping):
            raw = str(item.get("url") or "")
        elif isinstance(item, str):
            raw = item
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith("http://"):
            raw = "https://" + raw[len("http://"):]
        if not raw.startswith("https://"):
            continue
        if not any(ext in raw.lower() for ext in (".jpg", ".jpeg", ".png", ".webp", "/pic")):
            continue
        if raw not in urls:
            urls.append(raw)
        if len(urls) >= max(1, int(limit)):
            break
    return urls


def parse_wikimedia_photo(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """从 pageimages 响应里取出原图 URL（没有图时返回 None）。纯函数，便于离线单测。"""
    if not isinstance(payload, Mapping):
        return None
    pages = ((payload.get("query") or {}).get("pages")) or {}
    if not isinstance(pages, Mapping):
        return None
    for page in pages.values():
        if not isinstance(page, Mapping):
            continue
        original = page.get("original") or {}
        url = str(original.get("source") or "").strip()
        if url.startswith("http"):
            return {"url": url, "title": str(page.get("title") or ""), "width": original.get("width")}
    return None


def parse_wikimedia_credit(payload: Mapping[str, Any], photo_url: str = "") -> Dict[str, str]:
    """从 imageinfo/extmetadata 里取作者与许可，拼成人话署名（CC 授权要求必须显示）。

    取不到作者或许可时返回空 dict —— 调用方**据此拒绝使用这张图**（宁可没有图，
    也不能用一张授权不明的图，这跟"价格来源不明就不算可核实"是同一条规矩）。
    """
    if not isinstance(payload, Mapping):
        return {}
    pages = ((payload.get("query") or {}).get("pages")) or {}
    if not isinstance(pages, Mapping):
        return {}
    for page in pages.values():
        if not isinstance(page, Mapping):
            continue
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0] or {}
        meta = info.get("extmetadata") or {}
        artist = _strip_html(str(((meta.get("Artist") or {}).get("value")) or ""))
        license_name = _strip_html(str(((meta.get("LicenseShortName") or {}).get("value")) or ""))
        if not license_name:
            return {}
        credit = f"图片：Wikimedia Commons{(' · ' + artist) if artist else ''} · {license_name}"
        return {
            "credit": credit,
            "author": artist,
            "license": license_name,
            "source_url": str(info.get("descriptionurl") or info.get("url") or ""),
            "photo_url": str(info.get("url") or photo_url or ""),
        }
    return {}


def _strip_html(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", text or "").strip()


async def fetch_wikimedia_photo(name: str, timeout: float = 6.0) -> Optional[Dict[str, Any]]:
    """按名称找一张**有明确授权**的维基实景照片；失败/无图/授权不明一律返回 None。

    ⚠️ 默认关闭（`WIKIMEDIA_ENABLED = False`），实测两个硬阻塞（2026-09-19，本机网络）：
    1. `zh.wikipedia.org` 直接返回 **403 + robot policy**（"Please respect our robot policy …
       Contact bot-traffic@wikimedia.org"）—— 它有明确的机器人访问政策，不按它的要求来就是不合规抓取，
       这跟我们不做大众点评抓取是同一条底线；
    2. `commons.wikimedia.org` **TLS 层就被断**（SSL: UNEXPECTED_EOF_WHILE_READING），典型网络环境封锁；
       即使元数据取到了，浏览器也大概率加载不了 `upload.wikimedia.org` 的图。
    所以这条路**不在默认链路上**：真要用，先确认网络可达 + 按官方 UA 政策配置并留联系方式，
    再显式打开这个开关，并且节点上必须带署名与授权（`parse_wikimedia_credit` 的 `credit`）。
    """
    if not WIKIMEDIA_ENABLED:
        return None
    import httpx

    title = str(name or "").strip()
    if not title:
        return None
    headers = {"User-Agent": WIKIMEDIA_USER_AGENT, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(wikimedia_api_url(title), headers=headers)
            if response.status_code != 200:
                return None  # 403 robot policy 等一律当作"拿不到"，不退化成不合规抓取
            photo = parse_wikimedia_photo(response.json())
            if not photo:
                return None
            # 主图 URL 反查文件页，拿作者与许可
            file_title = "File:" + str(photo["url"]).rsplit("/", 1)[-1].replace("_", " ")
            credit_response = await client.get(wikimedia_file_info_url(file_title), headers=headers)
            if credit_response.status_code != 200:
                return None
            credit = parse_wikimedia_credit(credit_response.json(), photo_url=str(photo["url"]))
            if not credit:
                return None  # 授权不明 → 不用这张图
            return {"url": credit.get("photo_url") or photo["url"], **credit}
    except Exception:
        return None


def has_real_photo(node: Mapping[str, Any]) -> bool:
    if not isinstance(node, Mapping):
        return False
    photos = node.get("photos")
    if isinstance(photos, (list, tuple)) and any(str(item).startswith("http") for item in photos):
        return True
    return bool(str(node.get("photo_url") or "").startswith("http"))


def attach_photo_fallbacks(plan: Any, street_map_urls: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """给整份方案补齐"无实景照片"的兜底图（纯函数，不修改入参）。

    返回 `{route, filled, kinds}`：`filled` 是补了几张、`kinds` 是按类型计数，便于如实统计。
    """
    from .plan_quality import nodes_of  # 延迟导入，避免模块级循环

    street_map_urls = street_map_urls or {}
    route = []
    filled = 0
    kinds: Dict[str, int] = {}
    for node in nodes_of(plan):
        item = dict(node)
        if "photo_fallback" not in item:
            fallback = photo_fallback_for(item, street_map_urls.get(str(item.get("name") or ""), ""))
            if fallback:
                item["photo_fallback"] = fallback
                filled += 1
                kinds[fallback["kind"]] = kinds.get(fallback["kind"], 0) + 1
        route.append(item)
    return {"route": route, "filled": filled, "kinds": kinds}
