"""Run every AI service gate with one command.

`python tools/check.py` executes, in order:

1. preflight (interpreter + pinned package versions, see core/preflight.py)
2. `unittest discover` (the actual test suite)
3. ruff (lint)
4. mypy (types)
5. pip-audit (installed dependency advisories)

CI runs exactly this script, so "green locally" and "green in CI" mean the same
thing. Every step runs even if an earlier one fails; the summary at the end
lists what failed, and the process exits 1 if anything did.

Usage:
    .venv/Scripts/python.exe tools/check.py            # Windows
    .venv/bin/python tools/check.py                    # macOS/Linux
    python tools/check.py --steps unittest,ruff        # subset while iterating
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
PYTHON_VERSION_FILE = SERVICE_ROOT / ".python-version"

# 本脚本以 `python tools/check.py` 直接运行时 sys.path[0] 是 tools/，
# 需要先把服务根目录加进来才能导入 core。
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from core.console import ensure_utf8_console  # noqa: E402

ensure_utf8_console()


def _python_version() -> str:
    try:
        return PYTHON_VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "3.14"


def _steps() -> dict[str, list[str]]:
    python = sys.executable
    version = _python_version()
    github = os.getenv("GITHUB_ACTIONS", "").lower() == "true"
    ruff_format = "github" if github else "concise"
    return {
        "preflight": [python, "-m", "core.preflight"],
        # PYTHONPATH is set below; -t . is what makes absolute test imports work.
        "unittest": [python, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        "ruff": [
            python,
            "-m",
            "ruff",
            "check",
            f"--output-format={ruff_format}",
            "core",
            "api",
            "tools",
            "main.py",
        ],
        "mypy": [
            python,
            "-m",
            "mypy",
            f"--python-version={version}",
            "--explicit-package-bases",
            "--ignore-missing-imports",
            "--no-error-summary",
            "core",
            "api",
        ],
        "pip-audit": [python, "-m", "pip_audit", "--progress-spinner", "off"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all AI service checks.")
    parser.add_argument(
        "--steps",
        default="",
        help="comma-separated subset of: " + ", ".join(_steps()),
    )
    args = parser.parse_args()

    steps = _steps()
    selected = [name.strip() for name in args.steps.split(",") if name.strip()]
    unknown = [name for name in selected if name not in steps]
    if unknown:
        parser.error(f"unknown step(s): {', '.join(unknown)}")
    names = selected or list(steps)

    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(SERVICE_ROOT))
    # 子进程也必须在 GBK 控制台/重定向下安全输出：强制 UTF-8，不可编码字符降级。
    env["PYTHONIOENCODING"] = "utf-8:replace"

    results: list[tuple[str, bool, float]] = []
    for name in names:
        print(f"\n=== {name} ===", flush=True)
        started = time.perf_counter()
        completed = subprocess.run(steps[name], cwd=SERVICE_ROOT, env=env)
        elapsed = time.perf_counter() - started
        results.append((name, completed.returncode == 0, elapsed))
        # Preflight failures make every later step meaningless.
        if name == "preflight" and completed.returncode != 0:
            break

    print("\n=== summary ===")
    for name, ok, elapsed in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<10} {elapsed:.1f}s")
    if len(results) < len(names):
        skipped = [name for name in names if name not in {item[0] for item in results}]
        print(f"SKIP  {', '.join(skipped)} (preflight failed)")

    return 0 if all(ok for _, ok, _ in results) and len(results) == len(names) else 1


if __name__ == "__main__":
    raise SystemExit(main())
