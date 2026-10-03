"""风险画像"新鲜度"口径的回归锁。

背景：给上游数据源加分层缓存后（冷调用 6.8s → 缓存命中 4ms），如果 `risk_ts` 仍然取
"组装时间"，那么一份由 10 分钟前的安全情报拼出来的画像会声称自己是"刚刚同步"的 ——
这正是本项目最不能接受的那类谎报。所以 risk_ts 改成"画像真正对应的时间"：
各来源 `retrieved_at` 里最早的一个；一个都拿不到时才退回当前时间。
"""

import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.risk_service import RiskReport, timestamp_to_ms  # noqa: E402


class TestTimestampToMs(unittest.TestCase):
    def test_iso8601_with_z(self):
        self.assertEqual(timestamp_to_ms("2026-09-22T08:00:00Z"), 1790064000000)

    def test_iso8601_with_offset(self):
        expected = int(datetime(2026, 9, 22, 8, 0, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)
        self.assertEqual(timestamp_to_ms("2026-09-22T08:00:00+08:00"), expected)

    def test_epoch_numbers(self):
        self.assertEqual(timestamp_to_ms(1770000000000), 1770000000000, "毫秒原样")
        self.assertEqual(timestamp_to_ms(1770000000), 1770000000000, "秒要换算成毫秒")

    def test_unrecognisable_values_are_none_not_guesses(self):
        for value in (None, "", "  ", "不是时间", True, False, 12, 0, [], {}):
            self.assertIsNone(timestamp_to_ms(value), f"{value!r} 不该被猜成时间")


class TestRiskTimestampHonesty(unittest.TestCase):
    def _report(self, **sources):
        return RiskReport(city="杭州", signal_sources=sources)

    def test_risk_ts_is_the_oldest_source_moment(self):
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        fresh = datetime.now(timezone.utc).isoformat()
        snapshot = self._report(
            safety={"provider": "risk-feed", "available": True, "retrieved_at": old},
            weather={"provider": "amap", "available": True, "retrieved_at": fresh},
        ).to_dict()
        self.assertEqual(snapshot["risk_ts"], timestamp_to_ms(old), "必须取最早的那个来源时间，不能按组装时间谎报新鲜")

    def test_falls_back_to_now_only_when_no_source_time(self):
        before = int(time.time() * 1000)
        snapshot = self._report(safety={"provider": "risk-feed", "available": False, "retrieved_at": None}).to_dict()
        self.assertGreaterEqual(snapshot["risk_ts"], before)

    def test_source_metadata_is_preserved(self):
        retrieved = "2026-09-22T08:00:00Z"
        snapshot = self._report(safety={"provider": "risk-feed", "available": True, "retrieved_at": retrieved}).to_dict()
        self.assertEqual(snapshot["signal_sources"]["safety"]["retrieved_at"], retrieved, "各来源时间要原样保留")


if __name__ == "__main__":
    unittest.main()
