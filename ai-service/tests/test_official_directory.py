"""官方文旅**名录**通道的测试（2026-10-07）。

这条路是"接一个票务/官方数据的定向通道"的落地：通用搜索拿不到的小众景点，
在官方文旅站里**真的写着票价**（伊犁文旅：天鹅泉 ¥30、六星街 免费、薰衣草园 ¥35）。
这里把三个易错点钉住：
  ① 名录页的条目结构（`href="…detail?id=581&nodeId=2465"` —— 链接带 `&nodeId`，
     第一版正则要求 id 后紧跟引号，结果"0 条"，差点误判成"名录是 JS 渲染的"）；
  ② 名字匹配（官方名录写全称「伊宁县天鹅泉湿地公园」，我们只有「天鹅泉湿地公园」）；
  ③ 门票抽取：**官方整页**里别处出现过"40元至100元"这种区间，不能因此整页作废
     （那是实测抓到的 bug：区间判断原来扫的是整篇）。
"""

import asyncio
import os
import sys
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.official_directory import (  # noqa: E402
    channels_for_city,
    lookup_official_price,
    match_entry,
    parse_directory_list,
)

LIST_HTML = """
<html><body><ul>
  <li>
    <a href="/show/scenicspot/detail?id=581&nodeId=2465">
      <div class="itemimg"><span>人文景观</span></div>
      <div class="itemtxt"><div class="title clearfix">
        <h2>伊宁县天鹅泉湿地公园</h2><h3><span>国内最大疣鼻天鹅越冬栖息地</span></h3>
      </div></div>
    </a>
  </li>
  <li>
    <a href="/show/scenicspot/detail?id=724&nodeId=2465">
      <div class="itemtxt"><div class="title clearfix"><h2>免费伊宁市六星街</h2></div></div>
    </a>
  </li>
</ul></body></html>
"""

DETAIL_TEXT = (
    "首页 文旅资讯 旅游景点 景点详情 中文名称 天鹅泉 类别 国内最大疣鼻天鹅越冬栖息地 "
    "门票价格 门票30元/人 地点 新疆伊宁县英塔木镇夏合勒克塔木村 著名景点 伊宁县天鹅泉湿地公园木栈道观景台 "
    "其他景区门票 40元至100元（区间价，与本景区无关）"
)


class DirectoryParseTests(unittest.TestCase):
    def test_entries_with_nodid_suffix_are_parsed(self):
        entries = parse_directory_list(LIST_HTML, "http://ylwl.xjyl.gov.cn")
        self.assertEqual(len(entries), 2, entries)
        self.assertEqual(entries[0]["name"], "伊宁县天鹅泉湿地公园")
        self.assertTrue(entries[0]["url"].endswith("detail?id=581&nodeId=2465"), entries[0]["url"])
        self.assertTrue(entries[0]["url"].startswith("http://ylwl.xjyl.gov.cn/"), entries[0]["url"])

    def test_name_matching_tolerates_the_county_prefix(self):
        entries = parse_directory_list(LIST_HTML, "http://ylwl.xjyl.gov.cn")
        hit = match_entry("天鹅泉湿地公园", entries)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["name"], "伊宁县天鹅泉湿地公园")

    def test_no_match_returns_none(self):
        entries = parse_directory_list(LIST_HTML, "http://ylwl.xjyl.gov.cn")
        self.assertIsNone(match_entry("图开沙漠冰雪乐园", entries))

    def test_channels_are_only_used_for_their_region(self):
        self.assertTrue(channels_for_city("伊宁"))
        self.assertEqual(channels_for_city("喀什"), [], "喀什的官方站还没验证过，宁可留空")


class OfficialPriceTests(unittest.TestCase):
    def test_price_comes_from_the_official_page(self):
        async def fake_fetch(url):
            if "list" in url:
                return {"ok": True, "html": LIST_HTML}
            return {"ok": True, "text": DETAIL_TEXT}

        observation = asyncio.run(lookup_official_price("天鹅泉湿地公园", city="伊宁", fetch_text=fake_fetch))
        self.assertEqual(observation["value"], 30.0)
        self.assertEqual(observation["source"], "web_page")
        self.assertTrue(observation["verified"], "政府页面上的票价按可核实算（用户拍板）")
        self.assertIn("门票30元", observation["excerpt"])
        self.assertEqual(observation["official_page_name"], "伊宁县天鹅泉湿地公园")

    def test_a_range_elsewhere_on_the_page_does_not_kill_the_price(self):
        """实测 bug：区间判断原来扫整篇 → 官方整页只要别处提过"40元至100元"就什么都抽不出。

        现在的口径：区间只看数字**紧邻 ±12 字**（贴着的那种照样挡），页面别处的区间不再连坐。
        """
        from core.web_price_tool import extract_ticket_price

        text = "其他景区门票 40元至100元 是常见区间，与本景区无关的说明文字若干。本景区门票价格 门票30元/人。"
        self.assertEqual((extract_ticket_price(text) or {}).get("value"), 30.0)

    def test_a_range_still_blocks_that_very_price(self):
        from core.web_price_tool import extract_ticket_price

        self.assertIsNone(extract_ticket_price("门票40元至100元"))

    def test_no_directory_hit_is_reported_honestly(self):
        async def fake_fetch(url):
            return {"ok": True, "html": LIST_HTML}

        observation = asyncio.run(
            lookup_official_price("图开沙漠冰雪乐园", city="伊宁", fetch_text=fake_fetch)
        )
        self.assertIsNone(observation["value"])
        self.assertIn("没有找到", observation["note"])

    def test_official_page_without_a_price_says_so(self):
        async def fake_fetch(url):
            if "list" in url:
                return {"ok": True, "html": LIST_HTML}
            return {"ok": True, "text": "景点详情 中文名称 天鹅泉 开放时间 全年 类别 湿地"}

        observation = asyncio.run(lookup_official_price("天鹅泉湿地公园", city="伊宁", fetch_text=fake_fetch))
        self.assertIsNone(observation["value"])
        self.assertIn("没有写出", observation["note"])


if __name__ == "__main__":
    unittest.main()
