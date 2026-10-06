"""候选池查询缓存（2026-10-05）。

为什么缓存"取候选"而不只是最终方案：
  ① 同一座城市的候选池在几十分钟内是同一份事实（景点/餐饮/住宿不会按小时变）；
  ② 高德那把 key 的 QPS 是本项目最紧的资源（实测并发 2 就会限流、静默丢整条查询）；
  ③ "换一换"、多人同城、以及 warm-up 都会重复同样的查询。
但**只缓存成功且非空的结果** —— 否则一次限流会被固化半小时，表现成"这座城市没有 POI"。
"""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")

from api import agent as A  # noqa: E402


class PoolCacheTests(unittest.TestCase):
    def setUp(self):
        A._POI_QUERY_CACHE.clear()
        A.reset_pool_cache_stats()
        self.calls = []

    def _toolbox(self):
        from api.agent import ExpertToolbox

        box = ExpertToolbox()
        box.amap_key = "test-key"
        return box

    def _patch_amap(self, rows):
        calls = self.calls

        class _Response:
            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

        class _Client:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def get(self, url, params=None):
                calls.append((str(url), dict(params or {})))
                if url.endswith("/place/text"):
                    return _Response({"status": "1", "pois": rows})
                return _Response({"status": "1", "pois": []})

        return mock.patch("api.agent.httpx.AsyncClient", _Client)

    @staticmethod
    def _poi(name, index=0):
        return {
            "id": f"B{index}",
            "name": name,
            "type": "风景名胜;风景名胜;国家级景点",
            "location": f"86.1{index},41.7{index}",
            "biz_ext": {"rating": "4.5", "cost": "45"},
        }

    def test_second_identical_query_is_served_from_cache(self):
        rows = [self._poi("博斯腾湖风景名胜区"), self._poi("天鹅河景区", 1)]
        with self._patch_amap(rows):
            box = self._toolbox()
            first = asyncio.run(box.get_dynamic_pois("库尔勒", keywords="景点|景区", types="110000", limit=40, play_worthy=True))
            calls_after_first = len(self.calls)
            second = asyncio.run(box.get_dynamic_pois("库尔勒", keywords="景点|景区", types="110000", limit=40, play_worthy=True))
        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2, "第二次应该命中缓存并返回同样的候选")
        self.assertEqual(len(self.calls), calls_after_first, "命中缓存时不该再发高德请求")
        self.assertEqual(A._POI_CACHE_STATS["hits"], 1)
        self.assertEqual(A._POI_CACHE_STATS["misses"], 1)

    def test_different_city_or_keywords_do_not_share_cache(self):
        rows = [self._poi("博斯腾湖风景名胜区")]
        with self._patch_amap(rows):
            box = self._toolbox()
            asyncio.run(box.get_dynamic_pois("库尔勒", keywords="景点|景区", types="110000", limit=40, play_worthy=True))
            asyncio.run(box.get_dynamic_pois("喀什", keywords="景点|景区", types="110000", limit=40, play_worthy=True))
            asyncio.run(box.get_dynamic_pois("库尔勒", keywords="美食|餐厅", types="110000", limit=40, play_worthy=True))
        self.assertEqual(A._POI_CACHE_STATS["hits"], 0)
        self.assertEqual(A._POI_CACHE_STATS["misses"], 3)

    def test_play_worthy_flag_is_part_of_the_key(self):
        """同一个查询词、不同过滤器 → 结果完全不同，不能共用缓存。"""
        rows = [self._poi("博斯腾湖风景名胜区")]
        with self._patch_amap(rows):
            box = self._toolbox()
            asyncio.run(box.get_dynamic_pois("库尔勒", keywords="景点", types="110000", limit=40, play_worthy=True))
            asyncio.run(box.get_dynamic_pois("库尔勒", keywords="景点", types="110000", limit=40, play_worthy=False))
        self.assertEqual(A._POI_CACHE_STATS["hits"], 0)

    def test_empty_result_is_not_cached(self):
        with self._patch_amap([]):
            box = self._toolbox()
            asyncio.run(box.get_dynamic_pois("库尔勒", keywords="景点", types="110000", limit=40, play_worthy=True))
            asyncio.run(box.get_dynamic_pois("库尔勒", keywords="景点", types="110000", limit=40, play_worthy=True))
        self.assertEqual(A._POI_CACHE_STATS["hits"], 0)
        self.assertEqual(len(A._POI_QUERY_CACHE), 0, "空结果不能进缓存（会把一次限流固化半小时）")

    def test_ttl_zero_disables_the_cache(self):
        rows = [self._poi("博斯腾湖风景名胜区")]
        with mock.patch.dict(os.environ, {"AMAP_POI_CACHE_TTL_SECONDS": "0"}, clear=False):
            with self._patch_amap(rows):
                box = self._toolbox()
                asyncio.run(box.get_dynamic_pois("库尔勒", keywords="景点", types="110000", limit=40, play_worthy=True))
                asyncio.run(box.get_dynamic_pois("库尔勒", keywords="景点", types="110000", limit=40, play_worthy=True))
        self.assertEqual(A._POI_CACHE_STATS["hits"], 0)

    def test_expired_entry_is_refetched(self):
        rows = [self._poi("博斯腾湖风景名胜区")]
        key = A._poi_cache_key("库尔勒", "景点", "110000", 40, True)
        A._poi_cache_set(key, rows)
        # 手工把时间戳改到 TTL 之前
        stored_at, payload = A._POI_QUERY_CACHE[key]
        A._POI_QUERY_CACHE[key] = (stored_at - A.poi_cache_ttl_seconds() - 1, payload)
        self.assertIsNone(A._poi_cache_get(key))

    def test_cached_rows_are_copies_not_shared_references(self):
        """缓存被下游改（打标签/去重）不能污染缓存本身。"""
        rows = [self._poi("博斯腾湖风景名胜区")]
        key = A._poi_cache_key("库尔勒", "景点", "110000", 40, True)
        A._poi_cache_set(key, rows)
        first = A._poi_cache_get(key)
        first[0]["name"] = "被改掉了"
        second = A._poi_cache_get(key)
        self.assertEqual(second[0]["name"], "博斯腾湖风景名胜区")

    def test_summary_is_reported(self):
        A._POI_CACHE_STATS["hits"] = 2
        A._POI_CACHE_STATS["misses"] = 3
        self.assertIn("命中=2", A.pool_cache_summary())
        self.assertIn("未命中=3", A.pool_cache_summary())


if __name__ == "__main__":
    unittest.main()
