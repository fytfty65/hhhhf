"""诉求 → 检索配额：为什么"想看自然"却拿到 16/17 人文的池子。

实测背景（2026-10-04，用户真实规划 乌鲁木齐）：
    诉求原文含"我想看更多的自然景观，吃地道的美食"
    偏好信号却解析成 {'wants_food': True, 'wants_scenic': False}
    候选池          attractions=17 foods=0 hotels=0 类别={'scenic': 1, 'cultural': 16}
    → 行程只能排文庙/博物馆这类人文景点。

这些测试把三件事钉死：
  1. "自然景观"必须被识别为想看自然（含"人文景观"**不能**被误判成自然）；
  2. 检索配额按诉求分配：想看自然时自然配额 > 人文配额，明确排除人文时不发人文检索；
  3. 美食/住宿检索不再套用"像不像玩点"的景点过滤器（否则餐厅/酒店会被全部丢掉）。
"""

import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from api.agent import ExpertToolbox, extract_preference_signals  # noqa: E402
from core.poi_pool import attraction_query_limits  # noqa: E402
from core.route_profile import extract_route_profile, reference_profile_hint  # noqa: E402


class TestScenicPreferenceParsing(unittest.TestCase):
    def test_natural_landscape_phrase_is_scenic(self):
        signals = extract_preference_signals("我打算去乌鲁木齐玩几天，我想看更多的自然景观，吃地道的美食")
        self.assertTrue(signals["wants_scenic"], "「自然景观」必须被识别为想看自然")
        self.assertTrue(signals["wants_food"])

    def test_cultural_landscape_is_not_scenic(self):
        """护栏：用户抱怨的原话里就有"人文景观"，它绝不能把诉求判成想看自然。"""
        signals = extract_preference_signals("这次想多看人文景观，文庙和博物馆都要")
        self.assertFalse(signals["wants_scenic"], "人文景观 ≠ 自然景观")
        self.assertTrue(signals["wants_cultural"])

    def test_exclusion_phrases_still_win(self):
        signals = extract_preference_signals("不想去博物馆，也不要人文景观")
        self.assertTrue(signals["exclude_cultural"])
        self.assertFalse(signals["wants_cultural"])

    def test_nature_keywords_are_recognised(self):
        for text in ("想去天山草原徒步", "想看看湖泊和森林", "安排沙漠和地质公园", "多去湿地公园"):
            with self.subTest(text=text):
                self.assertTrue(extract_preference_signals(text)["wants_scenic"], text)

    def test_vague_own_intent_stays_vague_even_when_member_intents_are_rich(self):
        """事故回归（2026-10-04 真实推演）：发起人只说"去库尔勒玩2天"，
        但 full_text_context 里混进了房间默认成员的占位意图（"公园慢游""文创商圈"），
        池化信号于是变成 requirements=['自然风景','购物商圈'] —— 被当成"用户已说清楚"，
        参考画像因此永远轮不到生效。所以**画像判据只能用发起人自己这一轮的话**。"""
        own = extract_preference_signals("去库尔勒玩2天")
        pooled = extract_preference_signals("去库尔勒玩2天 | 步调宽松，安排特色茶社或公园慢游 | 想逛文创商圈")
        self.assertEqual(own["requirements"], ["常规观光、特色美食与舒适住宿"], "没提要求时是默认兜底串")
        self.assertTrue(pooled["requirements"], "成员占位意图会污染池化信号（这正是要用 own 判据的原因）")
        # 关键：默认兜底串**不能**被当成"用户说清楚了"，否则画像永远不生效
        profile = extract_route_profile("自然景观很多，美食也多，节奏轻松，住民宿")
        self.assertIsNotNone(
            reference_profile_hint(profile, explicit_signals=own),
            "发起人没提要求时，参考画像应当接手",
        )
        # 而池化信号（含成员意图）会让画像被跳过 —— 这就是当时的 bug 现象
        self.assertIsNone(reference_profile_hint(profile, explicit_signals=pooled))


class TestAttractionQueryLimits(unittest.TestCase):
    def test_scenic_wish_shifts_quota_towards_nature(self):
        limits = attraction_query_limits(40, wants_scenic=True)
        self.assertGreater(limits["scenic"], limits["cultural"], "想看自然时自然配额必须压过人文")
        self.assertGreaterEqual(limits["cultural"], 1, "仍然保留少量人文点缀（想看自然≠不要文化）")

    def test_default_quota_keeps_both_sides(self):
        limits = attraction_query_limits(40)
        self.assertGreater(limits["scenic"], 0)
        self.assertGreater(limits["cultural"], 0)

    def test_excluding_culture_skips_the_query(self):
        self.assertEqual(attraction_query_limits(40, exclude_cultural=True)["cultural"], 0)

    def test_tiny_limit_stays_sane(self):
        limits = attraction_query_limits(1, wants_scenic=True)
        self.assertGreaterEqual(limits["scenic"], 1)
        self.assertGreaterEqual(limits["shopping"], 8)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """假的 httpx.AsyncClient：返回固定高德响应，不联网。"""

    payload = {"status": "1", "info": "OK", "pois": []}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        return _FakeResponse(_FakeClient.payload)


class TestPlayWorthyFilterScope(unittest.TestCase):
    """实测：高德明明返回 48 家餐厅 / 30 家酒店，却因为套用"像不像玩点"而全部丢掉。"""

    PAYLOAD = {
        "status": "1",
        "info": "OK",
        "pois": [
            {"name": "老地方特色餐厅", "type": "餐饮服务;中餐厅;新疆菜", "location": "87.61,43.79"},
            {"name": "某博物馆-西北门地上停车场", "type": "交通设施服务;停车场", "location": "87.60,43.80"},
        ],
    }

    def _toolbox(self):
        toolbox = ExpertToolbox()
        toolbox.amap_key = "test-key"  # 不依赖真实 key；网络已被替换
        return toolbox

    def test_food_query_keeps_restaurants_but_still_drops_facilities(self):
        _FakeClient.payload = self.PAYLOAD
        with mock.patch("api.agent.httpx.AsyncClient", _FakeClient):
            rows = asyncio.run(
                self._toolbox().get_dynamic_pois("测试城", keywords="特色美食", types="050000", play_worthy=False)
            )
        names = [row.get("name") for row in rows]
        self.assertIn("老地方特色餐厅", names, "美食检索不能被景点过滤器干掉")
        self.assertNotIn("某博物馆-西北门地上停车场", names, "但设施（停车场）依然要挡住")

    def test_attraction_query_still_applies_the_play_worthy_filter(self):
        _FakeClient.payload = self.PAYLOAD
        with mock.patch("api.agent.httpx.AsyncClient", _FakeClient):
            rows = asyncio.run(
                self._toolbox().get_dynamic_pois("测试城", keywords="博物馆", types="141200", play_worthy=True)
            )
        names = [row.get("name") for row in rows]
        self.assertNotIn("老地方特色餐厅", names, "景点检索仍要挡住餐厅")
        self.assertNotIn("某博物馆-西北门地上停车场", names)


if __name__ == "__main__":
    unittest.main()
