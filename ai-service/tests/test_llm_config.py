"""A 项回归锁：LLM 默认值必须只有**一处**来源。

实测事故（2026-09-19）：`api/agent.py` 与 `api/conflict.py` 读同一个 `LLM_MODEL_NAME`，
却各自写了不同的硬编码兜底（`qwen-turbo` vs `qwen2.5:0.5b`）→ 环境变量一漏配，
两条链路会调**两个不同的模型**，且失败得很隐蔽。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm_config import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL_NAME,
    DEFAULT_TEMPERATURE,
    max_tokens,
    model_name,
    temperature,
)


class TestSingleSourceOfTruth(unittest.TestCase):
    def setUp(self):
        self.saved = {key: os.environ.get(key) for key in ("LLM_MODEL_NAME", "LLM_TEMPERATURE", "LLM_MAX_TOKENS", "LLM_MAX_TOKENS_LONG")}
        for key in self.saved:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_defaults_are_the_documented_ones(self):
        self.assertEqual(DEFAULT_MODEL_NAME, "qwen-turbo")
        self.assertEqual(model_name(), DEFAULT_MODEL_NAME)
        self.assertEqual(temperature(), DEFAULT_TEMPERATURE)
        self.assertEqual(max_tokens(), DEFAULT_MAX_TOKENS)
        self.assertGreater(max_tokens(long_output=True), DEFAULT_MAX_TOKENS)

    def test_env_wins_and_is_shared(self):
        os.environ["LLM_MODEL_NAME"] = "qwen-plus"
        self.assertEqual(model_name(), "qwen-plus")
        # 显式传入优先级最高
        self.assertEqual(model_name("qwen-max"), "qwen-max")

    def test_bad_env_values_fall_back_instead_of_crashing(self):
        os.environ["LLM_TEMPERATURE"] = "not-a-number"
        os.environ["LLM_MAX_TOKENS"] = "-5"
        self.assertEqual(temperature(), DEFAULT_TEMPERATURE)
        self.assertEqual(max_tokens(), DEFAULT_MAX_TOKENS)

    def test_api_modules_no_longer_hardcode_their_own_defaults(self):
        """契约：两个 api 模块都不许再写自己的兜底值（导入它们会顺带构造 LLM 客户端，
        所以这里做**静态**断言，而不是 import —— 否则单测会被环境变量/网络拖住）。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("api/agent.py", "api/conflict.py"):
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                source = handle.read()
            self.assertNotIn(
                'os.getenv("LLM_MODEL_NAME",',
                source,
                f"{name} 又出现了自己的模型名兜底值 —— 必须走 core/llm_config",
            )
            self.assertNotIn(
                "localhost:11434",
                source,
                f"{name} 又兜底到本地 Ollama 端点 —— 必须走 core/llm_config.base_url()",
            )
            if name.endswith("conflict.py"):
                self.assertIn("model_name()", source)
            else:
                self.assertIn("model_name()", source)


if __name__ == "__main__":
    unittest.main()
