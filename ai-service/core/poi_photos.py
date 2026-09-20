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


def has_real_photo(node: Mapping[str, Any]) -> bool:
    """节点是否已有**真实照片**（高德返回的 photos 或其它带授权的实景图）。"""
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
