"""Scoring modules — one per pack dimension.

Each module exposes:

    score_scenario(scenario: dict, response: dict) -> ScenarioResult

where:
    - scenario:  the parsed JSONL entry for this scenario (prompts, expected, asserts)
    - response:  the OpenAI-compatible chat-completions response (with HTTP metadata)
    - ScenarioResult: dataclass with .passed (bool), .failure_mode (enum), .detail (str)

Modules:
    tool_call         — JSON tool-call structure + per-scenario asserts
    instruct_follow   — constraint validators (length, format, citations, exclusions)
    struct_output     — JSON / YAML / grammar schema validation
    reason_math       — numeric extraction + tolerance compare
    data_extract      — field-level match
    _stub             — placeholder for execution-backed packs (BugFind/HermesAgent/CLI)
                        until verifier infrastructure (sandbox / mocks) is implemented
"""
