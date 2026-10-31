"""Test package marker.

Why this file exists (2026-10-09): without it, `python -m unittest discover -s
tests` needs the start directory to be importable, and older Pythons enforce that
strictly. CI runs `discover -s tests` on Python 3.12 (which relaxed the rule), so
the breakage was invisible there while a developer on Python 3.11 got:

    ImportError: Start directory is not importable: '.../ai-service/tests'

Marking the directory as a package makes discovery work identically on 3.11,
3.12 and 3.14, and keeps `python -m unittest discover -s tests -t .` (the CI
command) working unchanged. Test modules import the service via absolute paths
(`from core...`, `from api...`), so running them from the service root is still
required — see the PYTHONPATH note in the CI job.
"""
