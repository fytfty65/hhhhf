"""Pre-start dependency checks for the AI service.

Why this exists
---------------
`requirements.txt` is pinned, but keeping it pinned does not guarantee that the
interpreter actually running `main.py` has those packages installed. A missing
package used to surface as a `ModuleNotFoundError` from deep inside `api.agent`
with no hint about which environment was active, and a test run against such an
interpreter "passed" only because the missing module was never reached.

The check is deliberately standard-library only: it must run *before* any
third-party import, otherwise it would fail for the very reason it is meant to
explain.

Usage
-----
    python -m core.preflight        # report and exit non-zero on problems
    assert_runtime_ready()          # called from main.py at startup
"""

from __future__ import annotations

import importlib
import importlib.metadata
import re
import sys
from pathlib import Path

# The version pinned in `.python-version` and used by CI. Local and CI runs must
# be comparable, so a different interpreter is reported explicitly.
CANONICAL_PYTHON = "3.14"
MIN_PYTHON = (3, 14)

REQUIREMENTS_PATH = Path(__file__).resolve().parent.parent / "requirements.txt"

# Distribution name -> module that the service imports. Distribution names are
# not import names (`ddgs` is `ddgs`, but `python-dotenv` is `dotenv` and
# `scikit-learn` is `sklearn`), so they are listed explicitly instead of guessed.
RUNTIME_MODULES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pydantic": "pydantic",
    "openai": "openai",
    "httpx": "httpx",
    "requests": "requests",
    "ddgs": "ddgs",
    "python-dotenv": "dotenv",
    "numpy": "numpy",
    "networkx": "networkx",
    "scikit-learn": "sklearn",
    "redis": "redis",
}

_PIN = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*(?:\[[^\]]*\])?\s*==\s*([^\s#]+)")


def python_version_problem(version_info: tuple[int, ...] | None = None) -> str | None:
    """Return a human-readable problem with the interpreter, or None.

    A newer minor version is a warning rather than an error: the code targets
    3.14 syntax and the suite is verified there, but refusing to start on 3.15
    would turn an untested-but-probably-fine environment into an outage.
    """
    current = tuple((version_info or sys.version_info)[:2])
    if current < MIN_PYTHON:
        return (
            f"Python {current[0]}.{current[1]} is too old; this service targets "
            f"Python {CANONICAL_PYTHON} (see ai-service/.python-version)."
        )
    if current != tuple(int(part) for part in CANONICAL_PYTHON.split(".")):
        return (
            f"Python {current[0]}.{current[1]} differs from the pinned "
            f"{CANONICAL_PYTHON}; results may not match CI. Use "
            f"`scripts/init-ai-service.ps1` to create the pinned .venv."
        )
    return None


def pinned_requirements(path: Path | None = None) -> dict[str, str]:
    """Parse the `name==version` pins from requirements.txt.

    Comments, blank lines and `-r` includes are skipped; this file has no
    environment markers, and silently ignoring anything else would hide a pin
    that the check no longer verifies.
    """
    pins: dict[str, str] = {}
    for raw in (path or REQUIREMENTS_PATH).read_text(encoding="utf-8").splitlines():
        match = _PIN.match(raw)
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def _installed_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def dependency_problems(path: Path | None = None) -> list[str]:
    """Return every missing/mismatched runtime dependency, with the fix."""
    problems: list[str] = []
    pins = pinned_requirements(path)
    if not pins:
        return [f"no pinned requirements found in {path or REQUIREMENTS_PATH}"]

    for distribution, expected in pins.items():
        installed = _installed_version(distribution)
        if installed is None:
            problems.append(f"{distribution}=={expected} is not installed")
        elif installed != expected:
            problems.append(
                f"{distribution}=={installed} does not match pinned {expected}"
            )
    return problems


def import_problems() -> list[str]:
    """Return runtime modules that cannot be imported.

    A distribution can be "installed" yet unusable (broken wheel, missing
    compiled extension for the current interpreter), so presence in metadata
    alone is not enough.
    """
    problems: list[str] = []
    for distribution, module in RUNTIME_MODULES.items():
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - the point is to report any failure
            problems.append(f"{distribution} ({module}) failed to import: {exc}")
    return problems


def run_check() -> list[str]:
    """Run all preflight checks and return the problems found."""
    problems: list[str] = []
    version_problem = python_version_problem()
    if version_problem:
        problems.append(version_problem)
    problems.extend(dependency_problems())
    problems.extend(import_problems())
    return problems


def assert_runtime_ready() -> None:
    """Abort startup with an actionable message instead of a deep traceback."""
    problems = run_check()
    if not problems:
        return
    lines = [
        "AI service preflight failed:",
        *(f"  - {problem}" for problem in problems),
        "",
        "Fix it with the pinned environment:",
        "  powershell -ExecutionPolicy Bypass -File scripts/init-ai-service.ps1",
        "or inside ai-service:",
        "  .venv/Scripts/python.exe -m pip install -r requirements-dev.txt",
    ]
    print("\n".join(lines), file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    problems = run_check()
    if problems:
        print("AI service preflight failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Run `scripts/init-ai-service.ps1` to (re)create the pinned .venv.",
            file=sys.stderr,
        )
        return 1
    print(
        f"AI service preflight OK (Python {sys.version.split()[0]}, "
        f"{len(pinned_requirements())} pinned packages)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
