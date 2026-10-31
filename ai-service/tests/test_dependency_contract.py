"""Dependency contract tests.

These guard the gap between "the code prefers package X" and "requirements.txt
actually installs X". That gap is invisible to every other test: on a developer
machine X is usually present by accident, so the suite stays green while a clean
`pip install -r requirements.txt` (exactly what CI does) installs a service that
silently degrades at runtime.

The concrete incident (found 2026-10-09): `core/web_price_tool.py:1459` tries
`from ddgs import DDGS` first, falling back to the abandoned
`duckduckgo_search` package that its own comment documents as returning zero
results — but requirements.txt pinned only the abandoned one. CI was green and
web price search was dead on any clean install.
"""

import re
import unittest
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_REQUIREMENTS = _SERVICE_ROOT / "requirements.txt"


def _requirement_names() -> set:
    """Return the set of top-level distribution names pinned in requirements.txt."""
    names = set()
    for raw in _REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        # Strip extras (`uvicorn[standard]`) and any version specifier.
        name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip().lower()
        if name:
            names.add(name.replace("_", "-"))
    return names


class DeclaredDependenciesTests(unittest.TestCase):
    def test_requirements_file_exists(self):
        self.assertTrue(_REQUIREMENTS.is_file(), f"missing {_REQUIREMENTS}")

    def test_ddgs_is_pinned_because_the_code_prefers_it(self):
        """`ddgs` must be a declared dependency, not an accident of the local venv."""
        source = (_SERVICE_ROOT / "core" / "web_price_tool.py").read_text(encoding="utf-8")
        self.assertIn(
            "from ddgs import DDGS",
            source,
            "web_price_tool no longer prefers the ddgs package; update this test "
            "and requirements.txt together",
        )
        self.assertIn(
            "ddgs",
            _requirement_names(),
            "core/web_price_tool.py prefers `ddgs`, but requirements.txt does not "
            "pin it — a clean install silently falls back to the abandoned "
            "duckduckgo_search backend",
        )

    def test_importable_runtime_dependencies_are_declared(self):
        """Every third-party module imported at runtime must be pinned.

        Only modules that actually get imported by the service are checked, and
        the optional ones the code deliberately guards with try/except are
        exempted (they have working fallbacks rather than being required).
        """
        optional = {
            # Guarded by try/except in web_price_tool._ddgs_client (legacy fallback).
            "duckduckgo-search",
        }
        declared = _requirement_names()
        # Modules the service imports that must come from a distribution.
        required = {
            "fastapi": "fastapi",
            "uvicorn": "uvicorn",
            "pydantic": "pydantic",
            "openai": "openai",
            "httpx": "httpx",
            "requests": "requests",
            "numpy": "numpy",
            "networkx": "networkx",
            "sklearn": "scikit-learn",
            "redis": "redis",
            "dotenv": "python-dotenv",
            "ddgs": "ddgs",
        }
        for module, distribution in required.items():
            if distribution in optional:
                continue
            with self.subTest(module=module):
                self.assertIn(
                    distribution,
                    declared,
                    f"{module} is imported at runtime but {distribution} is not "
                    f"declared in requirements.txt",
                )


if __name__ == "__main__":
    unittest.main()
