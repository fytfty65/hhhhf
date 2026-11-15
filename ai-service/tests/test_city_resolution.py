"""目的地解析的诚实口径（2026-10-08）。

实测背景：诉求里**没提城市**时，管线会静默兜底到一个默认城市（以前是
`common_cities[0]`，正好是"武威"）并照常出方案 —— 用户完全不知道自己拿到的是默认值。
实测撞到过：诉求里写了喀什但没进 `intent_str`，最终产出的是**武威**行程。

现在的要求：
  · 识别出城市 → `resolution="matched"`；
  · 没识别出且用户**没写**目的地 → 仍然出方案（否则检索直接失败），但必须标
    `"fallback_default"`，让前端能如实说"没识别出目的地"；
  · 写了目的地却识别不出 → **显式失败**，不再拿兜底城市出一份不相干的方案
    （2026-11-03：目的地填 "Osaka" 会静默产出武威行程）；
  · 兜底城市**显式命名** `DEFAULT_CITY`，不再依赖 `common_cities` 的数组顺序。

口径断言直接对**源码**做，避免把整条重依赖链路拉起来；
纯函数的形状测试（目的地提取 / 大陆范围 / 错误事件）才真正 import `api.agent`。
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:9/v1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

AGENT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api", "agent.py"
)


def source() -> str:
    return io.open(AGENT_PATH, encoding="utf-8").read()


class TestDestinationResolution(unittest.TestCase):
    def test_default_city_is_named_not_indexed(self):
        """兜底城市必须显式命名 —— 不能再是 `common_cities[0]` 的副作用。"""
        text = source()
        self.assertIn('DEFAULT_CITY = "', text, "兜底城市应当显式命名")
        self.assertIn("target_city = DEFAULT_CITY", text, "走兜底时应赋 DEFAULT_CITY")
        # 以前那种"直接写死字面量"的兜底不许回来
        self.assertNotIn('        target_city = "武威"', text, "不许再硬编码兜底字面量")

    def test_resolution_flag_has_both_states(self):
        text = source()
        self.assertIn('city_resolution = "matched"', text, "识别成功要标记 matched")
        self.assertIn('city_resolution = "fallback_default"', text, "走兜底要标记 fallback_default")

    def test_target_city_event_carries_resolution(self):
        """`target_city` 事件必须带上 resolution —— 否则前端无从判断这是不是兜底。

        实测：这个 payload 有**三处**构造点，第一版只改了两处，
        事件里 `resolution=None`，看起来像"没做"：
          · 正常事件流（直接 yield 事件）；
          · 种子模板 payload（`cached_stream` 会把它当事件发出去）；
          · 语义缓存写入区（存进去，命中缓存时由 L3035 原样发出）。
        缓存那条不发字段、只是转存，所以它依赖前两处带上 —— 三处都要有。
        """
        text = source()
        # 三处构造点都写死 resolution，缺一处就会出现 resolution=None
        self.assertEqual(
            text.count('"resolution": city_resolution'),
            3,
            "三处 payload 构造点都要带 resolution",
        )
        # 缓存命中时的转发必须原样透传（不能自己另造一个只有 name/lnglat 的字典）
        self.assertIn('"payload": ctc', text, "缓存命中应原样转发已带 resolution 的 payload")

    def test_missing_coord_still_emits_event(self):
        """没有坐标时也要发事件（以前整个被 `if coord` 包住，前端拿不到目的地）。"""
        text = source()
        # 事件发出应当与坐标解析解耦：先算 city_lnglat，再统一 yield
        self.assertIn("city_lnglat = []", text, "坐标缺失应降级为空数组，而不是不发事件")

    def test_explicit_unresolved_destination_fails_loudly(self):
        """明确写了目的地却解析不出来时必须显式失败（2026-11-03 实测的 Osaka 事故）。

        以前：目的地 "Osaka" 中文规则全不命中 → 兜底成武威照常出方案，用户拿到
        「Osaka 探索路书」但正文全是武威。现在要求先判断"用户明确写了目的地"，
        再判断"是否解析成中国大陆坐标"，任一不满足就下发 error 事件，而不是出方案。
        """
        text = source()
        self.assertIn("DESTINATION_UNRESOLVED_CODE", text, "失败码要能被前端识别")
        self.assertIn('"type": "error"', text, "显式失败要下发 error 事件")
        self.assertIn("_pick_explicit_destination(payload, intent_str)", text, "要取用户明确写下的目的地")
        self.assertIn('"destinations"', text, "结构化 destinations 是首选来源")
        self.assertIn("_coord_in_mainland(coord)", text, "识别成功也要拦住境外坐标")
        # 失败必须发生在拿兜底坐标/命中缓存之前，否则旧缓存会把错误目的地"救活"
        fail_at = text.index("_destination_error_stream(explicit_destination")
        default_at = text.index('coord = coord or "102.63,37.93"')
        self.assertLess(fail_at, default_at, "显式失败必须早于兜底坐标")

    def test_error_message_names_requested_destination(self):
        """错误文案要写出用户写的那个目的地，而不是兜底城市。"""
        text = source()
        self.assertIn('没能识别目的地「{requested}」', text)
        self.assertIn("请改用中文城市名", text)


class ExplicitDestinationFailureRun(unittest.TestCase):
    """显式失败的行为测试：目的地提取 / 大陆范围判定 / 错误事件形状。"""

    @classmethod
    def setUpClass(cls):
        from api import agent

        cls.agent = agent

    def test_structured_destinations_win(self):
        payload = {"current_request": {"destinations": ["Osaka"]}}
        self.assertEqual(
            self.agent._pick_explicit_destination(payload, "目的地：Osaka；"), "Osaka"
        )

    def test_intent_prefix_is_fallback_source(self):
        payload = {"current_request": {"destinations": []}}
        self.assertEqual(
            self.agent._pick_explicit_destination(payload, "帮我规划 目的地：大阪；3天"), "大阪"
        )

    def test_no_destination_means_no_failure(self):
        self.assertEqual(self.agent._pick_explicit_destination({}, "帮我规划 3 天行程"), "")

    def test_mainland_bounds(self):
        self.assertTrue(self.agent._coord_in_mainland("102.63,37.93"))
        self.assertFalse(self.agent._coord_in_mainland("135.5,34.7"), "大阪在海外的坐标要拦住")
        self.assertFalse(self.agent._coord_in_mainland(""))
        self.assertFalse(self.agent._coord_in_mainland("not-a-coord"))

    def test_error_stream_shape(self):
        chunks = list(self.agent._destination_error_stream("Osaka", "fallback_default"))
        self.assertEqual(len(chunks), 1)
        event = json.loads(chunks[0])
        self.assertEqual(event["type"], "error")
        self.assertEqual(event["payload"]["code"], self.agent.DESTINATION_UNRESOLVED_CODE)
        self.assertIn("Osaka", event["payload"]["message"])


if __name__ == "__main__":
    unittest.main()
