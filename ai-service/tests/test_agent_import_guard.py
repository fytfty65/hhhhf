"""推演链路的"缺名字"静态护栏。

真实事故（2026-10-04）：`needs_widening` 只写在 `_fetch_attraction_mix()` **内部**做局部导入，
外层 `event_stream` 里也用了它 → `NameError: name 'needs_widening' is not defined`
让整条推演流中断（房间 WS 那边表现为"永远等不到方案"），而当时 760 个单测全绿 ——
因为没有任何单测覆盖这条几千行的异步生成器路径。

所以这里做一次**廉价的静态检查**：候选池相关的公共名字必须在模块级导入，
让"局部导入了、外层却用"这类错误在测试里就暴露，而不是等真实推演炸。
"""

import ast
import io
import pathlib
import unittest

AGENT_PATH = pathlib.Path(__file__).resolve().parents[1] / "api" / "agent.py"

# 这些名字在推演函数的不同层级都被用到：一律要求模块级导入
REQUIRED_MODULE_LEVEL_IMPORTS = {
    "needs_widening",
    "pool_supply_report",
    "attraction_query_limits",
    "SCENIC_WIDENING_KEYWORDS",
    "FOOD_WIDENING_KEYWORDS",
    "amap_error_is_retryable",
    "amap_error_note",
}


class AgentImportGuardTests(unittest.TestCase):
    def _module_names(self) -> set:
        with io.open(AGENT_PATH, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        names = set()
        for node in tree.body:  # 只看顶层语句
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    names.add((alias.asname or alias.name).split(".")[0])
        return names

    def test_candidate_pool_helpers_are_imported_at_module_level(self):
        names = self._module_names()
        missing = sorted(REQUIRED_MODULE_LEVEL_IMPORTS - names)
        self.assertEqual(
            missing,
            [],
            f"这些名字必须在 api/agent.py 顶层导入（局部导入会在外层使用时 NameError）：{missing}",
        )

    def test_agent_module_imports_without_error(self):
        """能导入就说明顶层名字没写错（语法/循环导入都会在这里暴露）。"""
        import importlib
        import os
        from unittest import mock

        # api.agent 在导入时就会构造 LLM 客户端，缺 key 会直接抛错（这是刻意的：
        # 宁可启动即失败，也不要在第一次推演时才炸）。这里给个假 key 只为了让导入跑通。
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "test-key-not-used"}, clear=False):
            module = importlib.import_module("api.agent")
        self.assertTrue(hasattr(module, "router"))


if __name__ == "__main__":
    unittest.main()
