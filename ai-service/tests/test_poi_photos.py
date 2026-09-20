"""配图兜底与图片来源的确定性单测（离线，不打网络）。

背景：用户反馈"规划的地点还是看不到真实照片"。高德对很多 POI 不返回实景图，
所以我们要给**真实地理影像**兜底，同时严守"不冒充"：`is_real=False` + 如实说明。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.poi_photos import (  # noqa: E402
    WIKIMEDIA_ENABLED,
    attach_photo_fallbacks,
    has_real_photo,
    parse_lnglat,
    parse_wikimedia_credit,
    parse_wikimedia_photo,
    photo_fallback_for,
    satellite_image_url,
)


class TestCoordinates(unittest.TestCase):
    def test_accepts_common_shapes(self):
        self.assertEqual(parse_lnglat([86.15, 41.76]), [86.15, 41.76])
        self.assertEqual(parse_lnglat("86.15,41.76"), [86.15, 41.76])
        self.assertEqual(parse_lnglat({"lng": 86.15, "lat": 41.76}), [86.15, 41.76])

    def test_rejects_junk_and_zero(self):
        for bad in [None, "", "abc", [0, 0], [999, 999], [1], {"lng": "x"}]:
            self.assertIsNone(parse_lnglat(bad), f"{bad} 不该解析成坐标")


class TestFallbackChain(unittest.TestCase):
    def test_satellite_first_then_street_map(self):
        node = {"name": "某公园", "lnglat": [86.15, 41.76]}
        fallback = photo_fallback_for(node, "https://restapi.amap.com/static")
        self.assertEqual(fallback["kind"], "satellite")
        self.assertIn("非实景照片", fallback["caption"])
        self.assertFalse(fallback["is_real"])  # 绝不能谎称是实景

        # 没有坐标 → 退回街道图
        street = photo_fallback_for({"name": "无坐标"}, "https://restapi.amap.com/static")
        self.assertEqual(street["kind"], "street_map")
        # 都没有 → None（前端显示占位，不编图）
        self.assertIsNone(photo_fallback_for({"name": "无坐标"}, ""))

    def test_real_photo_is_never_replaced(self):
        node = {"name": "有图", "lnglat": [86.15, 41.76], "photos": ["https://img.example/1.jpg"]}
        self.assertTrue(has_real_photo(node))
        self.assertIsNone(photo_fallback_for(node, "https://x"))

    def test_satellite_url_is_esri_imagery_export(self):
        url = satellite_image_url([86.15, 41.76])
        self.assertIn("World_Imagery/MapServer/export", url)
        self.assertIn("bbox=", url)
        self.assertIn("f=image", url)

    def test_attach_photo_fallbacks_only_fills_missing(self):
        plan = {
            "route": [
                {"day": 1, "name": "A", "lnglat": [86.15, 41.76]},
                {"day": 1, "name": "B", "lnglat": [86.16, 41.77], "photos": ["https://x/1.jpg"]},
            ]
        }
        result = attach_photo_fallbacks(plan)
        self.assertEqual(result["filled"], 1)
        self.assertEqual(result["kinds"], {"satellite": 1})
        self.assertNotIn("photo_fallback", result["route"][1])


class TestWikimediaChannel(unittest.TestCase):
    """维基通道：默认关闭（robot policy 403 + commons 在本机网络 TLS 被断），解析逻辑仍要正确。"""

    def test_channel_is_disabled_by_default(self):
        self.assertFalse(WIKIMEDIA_ENABLED, "默认不能打开：未确认网络可达与 UA 合规前不许走这条路")

    def test_parse_photo_payload(self):
        payload = {"query": {"pages": {"1": {"title": "博斯腾湖", "original": {"source": "https://upload.wikimedia.org/x.jpg", "width": 1200}}}}}
        self.assertEqual(parse_wikimedia_photo(payload)["url"], "https://upload.wikimedia.org/x.jpg")
        self.assertIsNone(parse_wikimedia_photo({"query": {"pages": {"1": {"title": "无图"}}}}))

    def test_credit_requires_a_license(self):
        with_license = {
            "query": {"pages": {"1": {"imageinfo": [{"url": "https://upload.wikimedia.org/x.jpg", "descriptionurl": "https://commons.wikimedia.org/wiki/File:X", "extmetadata": {"Artist": {"value": "<a>Someone</a>"}, "LicenseShortName": {"value": "CC BY-SA 4.0"}}}]}}}
        }
        credit = parse_wikimedia_credit(with_license)
        self.assertIn("CC BY-SA 4.0", credit["credit"])
        self.assertIn("Someone", credit["credit"])  # 署名必须有作者
        # 没有许可 → 空（调用方据此拒绝使用这张图）
        self.assertEqual(parse_wikimedia_credit({"query": {"pages": {"1": {"imageinfo": [{"extmetadata": {}}]}}}}), {})


class TestAmapPhotoChannel(unittest.TestCase):
    """③ 高德官方实景图通道：URL 构造与图片 URL 清洗（离线可测）。"""

    def test_place_text_url_uses_exact_name_and_extensions(self):
        from core.poi_photos import amap_place_text_url

        url = amap_place_text_url("博斯腾湖", "库尔勒", "KEY123")
        self.assertIn("/place/text", url)
        self.assertIn("extensions=all", url)  # 不加 extensions=all 就没有 photos 字段
        self.assertIn("keywords=", url)
        self.assertIn("KEY123", url)

    def test_photo_urls_normalize_and_filter(self):
        from core.poi_photos import amap_photo_urls

        poi = {
            "photos": [
                {"url": "http://aos-comment.amap.com/x.jpg"},   # http → 必须升级成 https
                {"url": "https://aos-comment.amap.com/y.jpg"},
                {"url": "https://aos-comment.amap.com/y.jpg"},   # 重复要去掉
                {"url": "javascript:alert(1)"},                  # 非 http(s) 丢掉
                {"title": "没有 url"},                            # 缺 url 丢掉
                "https://aos-comment.amap.com/z.png",            # 也接受裸字符串
            ]
        }
        urls = amap_photo_urls(poi)
        self.assertEqual(urls[0], "https://aos-comment.amap.com/x.jpg")
        self.assertEqual(len(urls), len(set(urls)))
        self.assertTrue(all(item.startswith("https://") for item in urls))
        self.assertNotIn("javascript:alert(1)", urls)

    def test_no_photos_returns_empty(self):
        from core.poi_photos import amap_photo_urls

        self.assertEqual(amap_photo_urls({}), [])
        self.assertEqual(amap_photo_urls({"photos": []}), [])
        self.assertEqual(amap_photo_urls({"photos": "https://x/1.jpg"}), [])


if __name__ == "__main__":
    unittest.main()
