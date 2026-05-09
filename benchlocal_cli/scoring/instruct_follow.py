"""InstructFollow scoring — constraint validators on free-text completions.

TODO (Codex): implement.

Supported constraint types per scenario:
    - exact_length_words             (e.g. exactly 50 words)
    - exact_length_chars             (e.g. exactly 280 chars)
    - max_length_words / chars
    - min_length_words / chars
    - case_only                      ("lowercase" / "uppercase" / "titlecase")
    - format_regex                   (free-text regex)
    - required_phrase                (e.g. response MUST contain "Step 1:")
    - forbidden_phrase               (e.g. response MUST NOT contain "I cannot")
    - required_url_count             (e.g. cite at least 3 URLs)
    - required_section_headers       (markdown # / ## headers expected)
    - bullet_count                   (e.g. exactly N markdown bullets)
    - language                       ("english" / "code-only" / "json-only")

Reference upstream: https://github.com/stevibe/InstructFollow-15
"""

from __future__ import annotations

# TODO (Codex): replace with real implementation.
def score_scenario(scenario: dict, response: dict) -> dict:
    """Stub. See module docstring."""
    raise NotImplementedError("benchlocal-cli scoring.instruct_follow is pre-alpha.")
