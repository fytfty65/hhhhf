"""静态护栏：模块里引用了**从未绑定**的名字（NameError 的温床）。

为什么需要
----------
2026-09-19 实测事故：`api/agent.py` 的"候选池保底路线"里引用了不存在的 `weather_info`
（正确变量是 `weather_data`）→ 保底路径直接 `NameError` → 流断掉，
前端只看到"推演超时"，而后端日志明明写着"已切换至【候选池保底模式】"。
用户当时正赶上大模型账户欠费，正好每次都走这条分支 —— 一个只在罕见分支上触发的拼写错误，
把"最后的兜底"变成了"必然失败"。

这种 bug 单测很难覆盖（要构造罕见分支），但静态就能确定性地发现，所以这里做一次**保守**的
AST 扫描：凡是在模块里从未被绑定、没有被 import、也不是内置的名字，一律报出来。
保守的含义：只要名字在模块里任何地方被赋值/定义/import/as 绑定过，就算它合法（不做作用域时序分析），
所以误报率很低，而 `weather_info` 这种"模块里根本没有"的必然被抓住。
"""

from __future__ import annotations

import ast
import builtins
import unittest
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [
    SERVICE_ROOT / "api",
    SERVICE_ROOT / "core",
    SERVICE_ROOT / "tools",
    SERVICE_ROOT / "tests",
    SERVICE_ROOT / "main.py",
]

# 运行时注入 / 解释器提供的名字，不需要在模块里绑定
EXTRA_ALLOWED = {
    "__name__", "__file__", "__doc__", "__package__", "__spec__", "__loader__", "__builtins__",
    "__debug__", "self", "cls",
}


def _collect_bound(tree: ast.AST) -> set[str]:
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
        elif hasattr(ast, "MatchAs") and isinstance(node, ast.MatchAs) and node.name:  # pragma: no cover
            bound.add(node.name)
    return bound


def _has_star_import(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    return True
    return False


def _undefined_names(path: Path) -> list[tuple[int, str]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    if _has_star_import(tree):
        return []
    known = _collect_bound(tree) | set(dir(builtins)) | EXTRA_ALLOWED
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in known:
            found.append((node.lineno, node.id))
    return found


class TestNoUndefinedModuleLevelNames(unittest.TestCase):
    def test_no_name_is_used_without_ever_being_bound(self):
        files: list[Path] = []
        for root in SCAN_ROOTS:
            if root.is_file():
                files.append(root)
            elif root.exists():
                files.extend(sorted(root.rglob("*.py")))
        self.assertGreater(len(files), 10, "扫描范围异常，没找到源文件")

        problems: list[str] = []
        for path in files:
            if "venv" in path.parts or "__pycache__" in path.parts:
                continue
            for lineno, name in _undefined_names(path):
                problems.append(f"{path.relative_to(SERVICE_ROOT)}:{lineno} 使用了从未绑定的名字 `{name}`")

        self.assertEqual(problems, [], "发现可能 NameError 的名字：\n" + "\n".join(problems))

    def test_scanner_would_catch_the_weather_info_regression(self):
        """自检：扫描器必须能抓出"模块里从未绑定"的名字（防止它悄悄失效）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample.py"
            sample.write_text(
                "def stream():\n"
                "    weather = {'condition': 'sunny'}\n"
                "    return simulate(weather=weather_info)\n",
                encoding="utf-8",
            )
            hits = [name for _, name in _undefined_names(sample)]
            self.assertIn("weather_info", hits)
            self.assertNotIn("weather", hits)


if __name__ == "__main__":
    unittest.main()
