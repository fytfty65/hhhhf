"""User-facing text honesty guard for the negotiation stream.

Background (fixed 2026-10-10): the negotiation stream told the user

    [时空调度体]: DBSCAN 空间降维与**纳什博弈**完成！

while the code on that path calls ``ParetoFrontierSelector.resolve_conflicts``,
which performs **Pareto non-dominance filtering** over three objectives
(fitness↑ / cost↓ / risk↓). No Nash equilibrium is computed anywhere. The old
class name ``NashEquilibriumSolver`` overstated what ran and has been retired.

This matters more here than in most codebases because the product's whole
proposition is that what it tells you is verifiable — the same discipline the
price model enforces with verified/estimated/unknown. An algorithm name that
overstates what ran is the same class of harm as a fabricated price.

The check is deliberately source-level (like ``test_agent_import_guard``) rather
than an end-to-end stream capture: importing ``api.agent`` builds HTTP clients and
LLM configuration, and the property under test is a property of the text we emit,
not of runtime behaviour.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_AGENT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api", "agent.py"
)

#: Terms that must never appear in emitted text, with the reason each is barred.
FORBIDDEN_IN_OUTPUT = {
    "纳什": (
        "no Nash equilibrium is computed on any code path; the solver performs "
        "Pareto non-dominance filtering. Do not claim Nash convergence to users."
    ),
    "多智能体协同推理": (
        "the main itinerary is produced by one LLM call; specialists run as "
        "deterministic handlers and member negotiation is rule-based. Do not "
        "describe that call as multi-agent collaborative reasoning."
    ),
    "专属智能体": (
        "no per-member agent exists; members are negotiation parties with "
        "declared preferences, not individual LLM agents."
    ),
    "帕累托均衡博弈": (
        "the planner does maximin selection plus Pareto non-dominance filtering; "
        "there is no equilibrium game."
    ),
}

#: A line is treated as output if it yields, is passed to the reasoning emitter,
#: or assigns to a message/token/text field. These are the shapes that reach the
#: user through the NDJSON stream.
_OUTPUT_SHAPES = (
    re.compile(r"\byield\b"),
    re.compile(r"\bemit_reasoning\s*\("),
    re.compile(r"[\"'](?:token|message|text|content|reply)[\"']\s*:"),
)


def _strip_comment(line: str) -> str:
    """Remove a trailing ``#`` comment, ignoring ``#`` inside string literals."""
    in_single = in_double = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double:
            return line[:index]
    return line


def _output_lines():
    with open(_AGENT_PATH, encoding="utf-8") as handle:
        for number, raw in enumerate(handle, start=1):
            code = _strip_comment(raw)
            if not code.strip():
                continue
            if any(pattern.search(code) for pattern in _OUTPUT_SHAPES):
                yield number, code


class NegotiationOutputHonestyTests(unittest.TestCase):
    def test_output_lines_never_claim_nash(self):
        offenders = []
        for number, code in _output_lines():
            for term, reason in FORBIDDEN_IN_OUTPUT.items():
                if term in code:
                    offenders.append(f"agent.py:{number}: {code.strip()}\n    ({reason})")
        if offenders:
            self.fail(
                "user-visible output claims something the code does not do:\n  "
                + "\n  ".join(offenders)
            )

    def test_the_guard_actually_scans_something(self):
        """A guard that matches nothing is worse than no guard.

        If the output shapes ever stop matching (e.g. the emitter is renamed),
        this test fails so the guard is updated instead of silently passing.
        """
        lines = list(_output_lines())
        self.assertGreater(
            len(lines), 5, "the output-line detector matched almost nothing; it likely drifted"
        )

    def test_the_true_claims_are_still_present(self):
        """The corrected wording must still describe what really runs.

        Guards against the lazy fix of deleting the text entirely: the DBSCAN
        clustering and the TSP ordering genuinely do run on this path, and users
        benefit from being told.
        """
        text = "".join(code for _, code in _output_lines())
        self.assertIn("DBSCAN", text, "the geographic clustering claim was removed rather than corrected")
        self.assertTrue(
            "帕累托" in text or "非支配" in text,
            "the corrected text should name the Pareto/non-dominance step that actually runs",
        )

    def test_comment_stripping_does_not_hide_real_output(self):
        """The comment stripper must not eat output that merely contains '#'."""
        self.assertEqual(_strip_comment('x = "#hashtag"  # real comment'), 'x = "#hashtag"  ')
        self.assertIn("token", _strip_comment('yield json.dumps({"token": "hi"})  # note'))


if __name__ == "__main__":
    unittest.main()
