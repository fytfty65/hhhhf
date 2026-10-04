"""供应商错误口径测试：限流与错误绝不能被当成"这里没有地点"。

真实事故（2026-10-04 实测，tools/probe_poi_queries.py 库尔勒）：

    --- 景点(生产参数) ---
    高德原始返回: status=0 info=CUQPS_HAS_EXCEEDED_THE_LIMIT 条数=0
    生产 get_dynamic_pois 最终产出: 7 条

`get_dynamic_pois` 原来只认 `status == "1" and pois`，其余一律返回空 —— 限流被当成
"没有景点"，候选池凭空变小，模型改用自己的记忆编地点，而日志里只有一行 attractions=1。
这里把"哪些错误值得重试"和"怎么把错误说成人话"变成可单测的纯逻辑。
"""

import unittest

from core.poi_pool import AMAP_RETRYABLE_INFOS, amap_error_is_retryable, amap_error_note


class AmapErrorTests(unittest.TestCase):
    def test_qps_limit_is_retryable(self):
        self.assertTrue(amap_error_is_retryable("CUQPS_HAS_EXCEEDED_THE_LIMIT"))
        self.assertTrue(amap_error_is_retryable("cuqps_has_exceeded_the_limit"))
        self.assertTrue(amap_error_is_retryable(" CUQPS_HAS_EXCEEDED_THE_LIMIT "))
        self.assertIn("CUQPS_HAS_EXCEEDED_THE_LIMIT", AMAP_RETRYABLE_INFOS)

    def test_key_and_param_errors_are_not_retryable(self):
        for info in ("INVALID_USER_KEY", "INVALID_USER_SCODE", "SERVICE_NOT_AVAILABLE", ""):
            self.assertFalse(amap_error_is_retryable(info), info)

    def test_rate_limit_note_says_candidates_may_be_incomplete(self):
        note = amap_error_note("CUQPS_HAS_EXCEEDED_THE_LIMIT", 0)
        self.assertIn("限流", note)
        self.assertIn("不完整", note)

    def test_key_error_note_says_no_real_candidates(self):
        note = amap_error_note("INVALID_USER_KEY", 0)
        self.assertIn("密钥", note)
        self.assertIn("无真实候选", note)

    def test_unknown_error_note_keeps_the_raw_info(self):
        """不认识的错误码也要把原话带出来，否则排查只能靠猜。"""
        note = amap_error_note("SOME_NEW_LIMIT_CODE", 0)
        self.assertIn("SOME_NEW_LIMIT_CODE", note)
        self.assertIn("status=0", note)

    def test_empty_info_is_reported_as_empty_not_as_success(self):
        self.assertIn("空结果", amap_error_note("", 0))


class GetDynamicPoisRetryWiringTests(unittest.TestCase):
    """把重试/如实打印的接线钉死：源码里必须有这两条（避免以后被"简化"掉）。"""

    def test_source_retries_rate_limit_and_prints_other_errors(self):
        import io
        import pathlib

        source = pathlib.Path(__file__).resolve().parents[1] / "api" / "agent.py"
        with io.open(source, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("amap_error_is_retryable(info)", text)
        self.assertIn("amap_error_note(info", text)
        self.assertIn("await asyncio.sleep(1.2)", text)
        # 错误分支必须留下痕迹，不能静默 return []
        self.assertIn("不编造候选", text)


if __name__ == "__main__":
    unittest.main()
