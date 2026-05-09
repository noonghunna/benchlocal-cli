"""ReasonMath scoring — numeric extraction + tolerance compare.

TODO (Codex): implement.

Strategy:
    1. Extract candidate numeric answer from response.
       - prefer last "answer:" / "= " / "→ " / boxed{} markers
       - fallback: last well-formed number in the response
    2. Compare against scenario.expected_answer:
       - exact_numeric         (int compare, ratio compare)
       - tolerance_numeric     (within ±tolerance)
       - exact_string          (e.g. "x=3, y=5")
       - regex_match           (e.g. answer matches r"x = -?\\d+")

Failure mode:
    - wrong_answer    → extracted number doesn't match expected
    - no_answer_found → couldn't extract any numeric value
    - timeout / http_error / server_error

Reference upstream: https://github.com/stevibe/ReasonMath-15
"""

from __future__ import annotations

# TODO (Codex): replace with real implementation.
def score_scenario(scenario: dict, response: dict) -> dict:
    """Stub. See module docstring."""
    raise NotImplementedError("benchlocal-cli scoring.reason_math is pre-alpha.")
