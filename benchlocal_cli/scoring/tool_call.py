"""ToolCall scoring — verifier-backed assertions on JSON tool-call shape + content.

TODO (Codex): implement.

Per Codex feedback (2026-05-09): name + JSON-parse + has-required-fields is NOT
enough. Each scenario should specify per-scenario asserts. Supported assertion
primitives the JSONL schema should support:

    - exact_function_name           (e.g. "get_weather")
    - exact_arg_value               (e.g. args.unit == "celsius")
    - arg_regex                     (e.g. args.filename matches r"\\.json$")
    - arg_in_enum                   (e.g. args.priority in ["high", "medium", "low"])
    - arg_numeric_range             (e.g. 0 <= args.temperature <= 1)
    - required_args_present         (list of arg names that MUST appear)
    - forbidden_args_absent         (list of arg names that MUST NOT appear)
    - multi_call_order              (for multi-tool scenarios — list of expected names in order)
    - tool_call_count               (exact count of tool_calls[] expected)

Failure mode dispatch:
    - verifier_fail   → assertion failed (model called wrong tool / wrong args)
    - invalid_json    → tool_calls[].function.arguments not valid JSON
    - wrong_answer    → response had no tool_calls[] when one was expected
    - timeout         → endpoint didn't respond within --timeout-per-case
    - http_error      → non-200 response
    - server_error    → 500-class or model-internal error

Reference upstream: https://github.com/stevibe/ToolCall-15
"""

from __future__ import annotations

# TODO (Codex): replace with real implementation.
def score_scenario(scenario: dict, response: dict) -> dict:
    """Stub. See module docstring."""
    raise NotImplementedError("benchlocal-cli scoring.tool_call is pre-alpha.")
