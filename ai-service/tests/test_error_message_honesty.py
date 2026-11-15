"""用户可见文案不许暴露原始异常（2026-11-03）。

实测背景：LLM 端点连不上时，降级摘要直接把底层异常原文抛给用户 ——
「大模型服务暂时不可用（Connection error.）」；重试提示还会把异常类名
（`ConnectError`）写进 token 流。用户既看不懂，也不知道下一步做什么。

口径：
  · 用户可见文案（token 流 / error 事件 / negotiation_summary）只出现
    `_friendly_llm_error` 给出的中文可操作提示；
  · 原始异常（`str(e)`、异常类名）只进日志（`print`）。
"""

from __future__ import annotations

import io
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
CJK = re.compile(r"[一-龥]")


def source() -> str:
    with io.open(AGENT_PATH, encoding="utf-8") as handle:
        return handle.read()


class SourceHonesty(unittest.TestCase):
    def test_helper_exists(self):
        self.assertIn("def _friendly_llm_error(", source())

    def test_degraded_summary_uses_friendly_text(self):
        text = source()
        self.assertIn("_friendly_llm_error(last_llm_err)", text)
        self.assertIn('_synthesize_from_pool(_friendly_llm_error(last_llm_err))', text)

    def test_no_raw_exception_in_user_visible_strings(self):
        text = source()
        # 异常类名进过 token 流（「LLM 调用异常（ConnectError）」）
        self.assertNotIn("type(last_llm_err).__name__", text)
        # 平台拒绝服务时把 `str(e)[:120]` 直接写进 token 流
        self.assertNotIn('{str(e)[:120]}', text)
        # 兜底崩溃时把 `str(e)` 当成 negotiation_summary 下发
        self.assertNotIn('negotiation_summary\\": \\"{str(e)}\\"', text)
        # 算法引擎崩溃时把异常原文写进 token 流
        self.assertNotIn("算法引擎计算失败: {str(e)}", text)


class FriendlyErrorMapping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from api import agent

        cls.agent = agent

    def test_connection_error_is_translated(self):
        import httpx

        message = self.agent._friendly_llm_error(httpx.ConnectError("Connection error."))
        self.assertEqual(message, "网络连接失败，请稍后重试")
        self.assertNotIn("Connection", message)
        self.assertTrue(CJK.search(message))

    def test_timeout_is_translated(self):
        import httpx

        message = self.agent._friendly_llm_error(httpx.ReadTimeout("timed out"))
        self.assertEqual(message, "网络连接失败，请稍后重试")

    def test_auth_error_points_to_admin(self):
        message = self.agent._friendly_llm_error(PermissionError("401 invalid api key"))
        self.assertIn("联系管理员", message)

    def test_unknown_error_stays_generic(self):
        message = self.agent._friendly_llm_error(ValueError("some internal shame"))
        self.assertEqual(message, "服务暂时不可用，请稍后重试")
        self.assertNotIn("shame", message)

    def test_no_error_stays_generic(self):
        self.assertEqual(self.agent._friendly_llm_error(None), "服务暂时不可用，请稍后重试")


if __name__ == "__main__":
    unittest.main()
