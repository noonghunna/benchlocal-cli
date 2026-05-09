"""Core orchestrator — sends prompts, collects responses, dispatches to scoring, aggregates.

TODO (Codex): implement.

Architecture overview:

    1. Load pack JSONL files from benchlocal_cli/packs/
    2. For each scenario in pack:
       a. Build OpenAI-compatible chat-completions request
          (apply per-pack sampling defaults: temperature, top_p, max_tokens, tool_choice)
       b. POST to endpoint with --timeout-per-case timeout
       c. Capture: response body, latency, HTTP status
       d. Dispatch to scoring module:
            scoring.<pack_dimension>.score_scenario(scenario, response) -> Result
          where Result distinguishes:
            - PASS / FAIL (verifier outcome)
            - failure mode: verifier_fail | wrong_answer | invalid_json |
              timeout | http_error | server_error
       e. Aggregate per-pack: pass count, total, score, p50/p95 latency
    3. Emit output (markdown table + per-failure breakdown OR JSON blob)

Key design points (from Codex sanity-check 2026-05-09):

    - PER-SCENARIO ASSERTS: each scenario in JSONL specifies its own pass conditions.
      Scoring module dispatches on scenario.verifier_type (e.g. "tool_call_exact",
      "tool_call_regex", "json_schema", "numeric_match"). Verifier logic lives in
      scoring.<pack_dimension>; assertion data lives in the JSONL.

    - LATENCY-BOUND, NOT THROUGHPUT-BOUND: tool-call/struct-output completions are
      short. Per-prompt overhead matters more than TPS. Always emit p50/p95.

    - DETERMINISTIC ONLY: no LLM-as-judge fallback. If a scenario can't be scored
      deterministically, it doesn't belong in a pack.

    - FAILURE MODE TAXONOMY: distinguish verifier_fail vs invalid_json vs timeout
      vs server_error in output, so users can tell "model gave wrong tool" from
      "endpoint hit OOM mid-completion".

    - REPRODUCIBILITY: result JSON includes pack version, runner version, endpoint,
      model id, sampling params, scenario IDs, raw responses, scored outcomes.
      Output should be re-runnable from the JSON for debugging.
"""

from __future__ import annotations

# TODO (Codex): replace with real implementation.
class Runner:
    """Stub. See module docstring for architecture brief."""

    def __init__(self) -> None:
        raise NotImplementedError("benchlocal-cli is pre-alpha. See docs/DESIGN.md.")
