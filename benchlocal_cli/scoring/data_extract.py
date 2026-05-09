"""DataExtract scoring — field-level match against expected extraction targets.

TODO (Codex): implement.

Strategy:
    1. Parse response as JSON (or extract JSON from markdown code-fence)
    2. Compare per-field against scenario.expected_fields:
       - exact match (string / number / bool)
       - case-insensitive match
       - regex match
       - in-set / membership
       - field-presence-only (any non-empty value passes)

Failure mode:
    - missing_field        → expected field not present
    - wrong_value          → field present but value mismatches
    - extra_fields         → response has fields not in expected set
                             (configurable strict-mode per scenario)
    - invalid_json / timeout / etc.

Reference upstream: https://github.com/stevibe/DataExtract-15
"""

from __future__ import annotations

# TODO (Codex): replace with real implementation.
def score_scenario(scenario: dict, response: dict) -> dict:
    """Stub. See module docstring."""
    raise NotImplementedError("benchlocal-cli scoring.data_extract is pre-alpha.")
