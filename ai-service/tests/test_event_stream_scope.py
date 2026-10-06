"""`event_stream` 生成器里的"裸 self"回归防护（2026-10-05 实测崩溃）。

事故形态：`api/agent.py` 的 `event_stream` 是**嵌套异步生成器**（不是类方法），
但里面有一行写的是 `hasattr(self, 'center_lon')` —— 平时因为 `_has_map` 为真而短路，
一直没炸；实测某次伊宁 geocode 拿不到城市中心，`_has_map` 为假 → 求值 `self` → NameError
→ **整条下发链路被 except 接住**，用户表现成"未收到终稿"，日志只有一句
`🔥 [真实路网拼装失败]: NameError: name 'self' is not defined`（标题还把人带偏，
以为是路网拼装的问题）。

这类"某个分支只在罕见输入下走到"的错误，靠单测很难覆盖，所以用 AST 直接钉住：
`event_stream` 里不许出现裸 `self`。
"""

import ast
import io
import os
import sys
import unittest

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVICE_ROOT)


def _function(name: str) -> ast.AST:
    with io.open(os.path.join(SERVICE_ROOT, "api", "agent.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"agent.py 里找不到函数 {name}")


class NestedGeneratorScopeTests(unittest.TestCase):
    def test_event_stream_never_references_self(self):
        target = _function("event_stream")
        offenders = [
            node.lineno
            for node in ast.walk(target)
            if isinstance(node, ast.Name) and node.id == "self"
        ]
        self.assertEqual(
            offenders,
            [],
            f"event_stream 是嵌套生成器、没有 self；这些行引用了它：{offenders}（会 NameError 把下发链路带走）",
        )

    def test_no_borrowed_city_center_in_the_map_fallback(self):
        """"没有坐标的节点借城市中心当自己的示意图"这条兜底已被移除（那是标错位置）。

        用 AST 看属性名（不是搜文本）：注释里提到旧写法不该让测试变红。
        """
        target = _function("event_stream")
        attributes = {node.attr for node in ast.walk(target) if isinstance(node, ast.Attribute)}
        self.assertNotIn("center_lon", attributes)
        self.assertNotIn("center_lat", attributes)


if __name__ == "__main__":
    unittest.main()
