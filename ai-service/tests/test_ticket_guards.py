"""门票抽取器的"闸门"测试（2026-10-07 实跑抓到两个**误杀真价**的闸门）。

背景：巴音布鲁克官网票务页（渲染后）写着
    `游玩时段： 全年 08:30-19:00 票价： 140元（包含门票+区间车票）`
结果我们的抽取器连续两道闸门把它否掉：
  ① 模糊词里有光秃秃的「区间」→ 撞上**区间车票**（新疆的区间车是正经票种）；
  ② 价格区间正则不要求"钱"的标记 → 撞上**运营时段** `08:30-19:00`（"0-19"被当成价格区间）。
两道都修了，这里钉住"该收的收、该拒的拒"。
"""

import os
import sys
import unittest

os.environ.setdefault("LLM_API_KEY", "test-key-not-used")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.web_price_tool import extract_ticket_price  # noqa: E402

# (文本, 期望值, 说明)
CASES = [
    ("巴音布鲁克景区联票 票价：140元（包含门票+区间车票）", 140.0, "区间车票是真票种，不能当模糊词否掉"),
    ("巴音布鲁克 全年 08:30-19:00 票价：140元", 140.0, "运营时段不是价格区间"),
    ("巴音布鲁克 开放时间 10:00至21:00 门票 140元/人", 140.0, "「10:00至21:00」不是价格区间"),
    ("罗布人村寨景区门票：35元/人 观光车票：15元/人", 35.0, "第一档票价取门票"),
    ("巴音布鲁克门票 50元至100元", None, "真·价格区间要拒"),
    ("巴音布鲁克门票 50-100元", None, "真·价格区间要拒"),
    ("巴音布鲁克门票 ￥50-￥100", None, "真·价格区间要拒"),
    ("巴音布鲁克门票价格区间 50-100", None, "模糊表述要拒"),
    ("巴音布鲁克票价大约60元，以现场为准", None, "模糊表述要拒"),
]


class TicketGuardTests(unittest.TestCase):
    def test_extraction_guards(self):
        failures = []
        for text, expected, why in CASES:
            name = text.split()[0]
            got = (extract_ticket_price(text, require_name=name, name_window=160) or {}).get("value")
            if got != expected:
                failures.append(f"{why}｜文本={text!r}｜期望 {expected} 实得 {got}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_name_must_appear_near_the_price(self):
        """名字对不上的页面不许取价（否则会把甲景区的价安给乙景区）。"""
        self.assertIsNone(extract_ticket_price("喀什古城 票价：29元", require_name="巴音布鲁克", name_window=160))
        self.assertEqual(
            (extract_ticket_price("喀什古城 票价：29元", require_name="喀什古城", name_window=160) or {}).get("value"),
            29.0,
        )


if __name__ == "__main__":
    unittest.main()
