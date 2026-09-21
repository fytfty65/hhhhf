"""B-1 回归锁：分段 prompt 必须**不带**候选池的全字段 JSON，且规则与坐标不能丢。

实测背景（2026-09-19，`work/probe_prompt_cost.py`，30 天新疆行程，6 次分段调用）：
8,932 字符的 system prompt 里 **8.4k 是候选池 JSON**，每次分段都原样重发 → 97% 的输入是重复。
修法是分段只带"规则 + 输出契约 + 候选池最小索引（name/lnglat/type）"。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompts import (  # noqa: E402
    compact_pool_index,
    compact_system_prompt,
    segment_system_prompt,
)

FULL_POOL = [
    {
        "name": "博斯腾湖",
        "lnglat": [86.15, 41.76],
        "type": "风景名胜",
        "cost_estimate": "¥45",
        "rating": "4.6",
        "open_time": "09:00-19:00",
        "photos": ["https://aos-comment.amap.com/x.jpg"],
        "amap_url": "https://uri.amap.com/marker?position=86.15,41.76",
        "address": "新疆巴州博湖县",
        "crowdedness": "适中",
        "data_sources": {"cost_estimate": "amap", "rating": "amap"},
    },
    {"name": "罗布人村寨", "lnglat": [86.31, 41.02], "type": "风景名胜", "cost_estimate": "¥40", "rating": "4.4"},
    {"name": "无坐标的点", "type": "餐饮"},
    {"lnglat": [1, 2]},  # 没有名字 → 丢弃
]

SYSTEM_WITH_POOL = (
    "【🔴 规则零：用户核心诉求必达】…\n"
    "【🔴 规则五：location 名称与 lnglat 必须照抄底座数据，photos/amap_url 一律留空】…\n"
    "【候选文旅 POI 底座数据 (已附带标准预估花销)】:\n"
    + json.dumps(FULL_POOL, ensure_ascii=False)
    + "\n[FINAL_JSON]\n{\"status\": \"consensus_reached\", \"route\": []}\n"
)


class TestCompactPoolIndex(unittest.TestCase):
    def test_keeps_only_name_coords_type(self):
        index = compact_pool_index(FULL_POOL)
        names = [item["name"] for item in index]
        self.assertEqual(names, ["博斯腾湖", "罗布人村寨", "无坐标的点"], "应丢掉没有名字的条目")
        for item in index:
            self.assertLessEqual(set(item.keys()), {"name", "lnglat", "location", "type"})
        # 坐标必须保留（分段就是靠它"逐字照抄"）
        self.assertEqual(index[0]["lnglat"], [86.15, 41.76])
        # 对分段无用的重字段必须被丢掉
        for dropped in ("cost_estimate", "rating", "open_time", "photos", "amap_url", "address", "data_sources"):
            self.assertNotIn(dropped, index[0])

    def test_accepts_plan_shaped_input(self):
        self.assertEqual(compact_pool_index({"route": FULL_POOL})[0]["name"], "博斯腾湖")
        self.assertEqual(compact_pool_index([]), [])


class TestSegmentSystemPrompt(unittest.TestCase):
    def test_segment_prompt_drops_heavy_pool_fields(self):
        segment = segment_system_prompt(SYSTEM_WITH_POOL, FULL_POOL)
        # 规则与坐标必须在（否则分段会编造地点）
        self.assertIn("规则五", segment)
        self.assertIn("86.15", segment)
        # 重字段与对象 schema 必须不在
        self.assertNotIn("data_sources", segment)
        self.assertNotIn("aos-comment.amap.com", segment)
        self.assertNotIn("[FINAL_JSON]", segment)
        # 输出契约要明确"只输出节点数组"
        self.assertIn("只输出**本段的节点数组**", segment)

    def test_segment_prompt_is_much_smaller_than_the_full_one(self):
        segment = segment_system_prompt(SYSTEM_WITH_POOL, FULL_POOL)
        self.assertLess(len(segment), len(SYSTEM_WITH_POOL) * 0.9, "分段 prompt 应明显小于整份 prompt")

    def test_without_pool_it_still_returns_rules(self):
        segment = segment_system_prompt(SYSTEM_WITH_POOL, None)
        self.assertIn("规则五", segment)
        self.assertIn("只输出**本段的节点数组**", segment)
        self.assertNotIn("[FINAL_JSON]", segment)


if __name__ == "__main__":
    unittest.main()
