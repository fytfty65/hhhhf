"""省级行政区必须收敛到可用于高德检索的核心城市。"""

import unittest

from api.agent import resolve_search_city


class ProvinceResolutionTest(unittest.TestCase):
    def test_province_becomes_capital(self):
        self.assertEqual(resolve_search_city("四川"), "成都")
        self.assertEqual(resolve_search_city("四川省"), "成都")
        self.assertEqual(resolve_search_city("广西壮族自治区"), "南宁")
        self.assertEqual(resolve_search_city("新疆维吾尔自治区"), "乌鲁木齐")
        self.assertEqual(resolve_search_city("内蒙古自治区"), "呼和浩特")
        self.assertEqual(resolve_search_city("香港特别行政区"), "香港")

    def test_real_city_is_untouched(self):
        self.assertEqual(resolve_search_city("成都"), "成都")
        self.assertEqual(resolve_search_city("九寨沟"), "九寨沟")


if __name__ == "__main__":
    unittest.main()
