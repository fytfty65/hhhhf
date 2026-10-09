"""目的地解析的诚实口径（2026-10-08）。

实测背景：诉求里**没提城市**时，管线会静默兜底到一个默认城市（以前是
`common_cities[0]`，正好是"武威"）并照常出方案 —— 用户完全不知道自己拿到的是默认值。
实测撞到过：诉求里写了喀什但没进 `intent_str`，最终产出的是**武威**行程。

现在的要求：
  · 识别出城市 → `resolution="matched"`；
  · 没识别出 → 仍然出方案（否则检索直接失败），但必须标 `"fallback_default"`，
    让前端能如实说"没识别出目的地"，而不是假装这是用户要的城市；
  · 兜底城市**显式命名** `DEFAULT_CITY`，不再依赖 `common_cities` 的数组顺序。

这里不去 import `api.agent`（那会拉起整条重依赖链路），
而是直接对**源码**断言这几个口径 —— 目的是防止有人改回去。
"""

from __future__ import annotations

import io
import os
import re
import sys
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")
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


if __name__ == "__main__":
    unittest.main()
