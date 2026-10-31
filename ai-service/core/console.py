"""Console encoding bootstrap.

Windows 的 PowerShell/CMD 在没有设置 UTF-8 时是 cp936（GBK）。当 stdout 被重定向
到文件或管道时，Python 会按 cp936 编码写入，任何 emoji（✅ ❌ ⚠️ 🚀 …）都会抛出
`UnicodeEncodeError`，把一次本来成功的检查/探针伪装成崩溃——日志里只剩一条与真实
结果无关的 traceback。

这里统一在入口把 stdout/stderr 重配成 UTF-8，并给不可编码字符留降级出口：

    from core.console import ensure_utf8_console
    ensure_utf8_console()

约束：
- 幂等：main.py、tools/check.py 与各 CLI 工具都可安全重复调用。
- 容错：没有 `reconfigure` 的流（部分测试捕获器、包装过的管道）直接跳过。
- 不致命：改不了编码顶多是显示问题，绝不能因此中断进程。
"""

from __future__ import annotations

import sys

_ENCODING = "utf-8"
_ERRORS = "replace"


def ensure_utf8_console(streams: tuple[object, ...] | None = None) -> None:
    """把给定流（默认 stdout/stderr）重配成 UTF-8，且永不抛异常。"""
    targets = streams if streams is not None else (sys.stdout, sys.stderr)
    for stream in targets:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding=_ENCODING, errors=_ERRORS)
        except (AttributeError, OSError, ValueError):
            # io.UnsupportedOperation 同时是 OSError 和 ValueError 的子类；
            # 已关闭或不可重配的流不值得让调用方失败。
            continue
