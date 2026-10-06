"""一次性排查：event_stream 里还有哪些裸 self 引用（紧凑输出，不打印源码）。"""

from __future__ import annotations

import ast
import io
import os
import sys

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = os.path.join(SERVICE_ROOT, "api", "agent.py")
with io.open(path, encoding="utf-8") as handle:
    lines = handle.readlines()
tree = ast.parse("".join(lines))
for node in ast.walk(tree):
    if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == "event_stream":
        hits = [n.lineno for n in ast.walk(node) if isinstance(n, ast.Name) and n.id == "self"]
        print("event_stream self 引用行号:", hits)
        for lineno in hits:
            print(f"  {lineno}: {lines[lineno - 1].strip()[:120]}")
        sys.exit(0)
print("没找到 event_stream")
