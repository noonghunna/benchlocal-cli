# Design — benchlocal-cli

## Goal

CLI tool that runs LLM behavioral evaluations against any OpenAI-compatible HTTP endpoint, with deterministic verifier-backed scoring. Companion to [club-3090](https://github.com/noonghunna/club-3090) for measuring quality on quantized inference stacks where vendor BF16 quality numbers don't apply.

## Constraints (from upstream design discussion)

- **CLI-only** — no Electron, no GUI; runs on a headless inference rig
- **Total runtime budget** — 30-45 min for a `--medium` run (the default tier)
- **Deterministic scoring only** — no LLM-as-judge fallback; reproducibility matters more than nuance
- **Quantized-model focus** — measures quality on Q4/INT4 quants where official model-provider quality numbers don't apply
- **Most-important dimensions for our users** — tool-call + instruction-follow are primary signals (IDE-agent regressions land here)

## Modes

| Mode | Packs | Budget | Use case |
|---|---|---|---|
| `--quick` | ToolCall-15 + InstructFollow-15 | ~10-15 min | Per-commit gate; pre-push smoke |
| `--medium` (default) | + StructOutput-15 + DataExtract-15 | ~25-30 min | Pre-release; pin bumps; new compose authoring |
| `--full` | + ReasonMath-15 + BugFind/HermesAgent/CLI when sandboxed | ~45-60 min | Cross-rig comparison; quality A/B vs another quant |

Mode ↔ pack mapping is hardcoded in the runner (not user-configurable), to keep `--quick` / `--medium` / `--full` as well-known semantics. Users wanting flexibility use `--pack <pack-id>` to run a single named pack.

Reasoning-capable model handling is standardized per pack: metadata declares `default_thinking: on|off` and the runner honors it by default, so reasoning-rewarding packs can use `chat_template_kwargs.enable_thinking=true` while execution/format packs stay answer-only. `--enable-thinking` forces all packs on; `--no-thinking` forces all packs off. JSON output records `thinking_enabled` per pack and `thinking_mode` at the run level so downstream quality lines can distinguish mixed pack-default runs from force-on/off A/Bs.

`BugFind-15` / `HermesAgent-20` / `CLI-40` are execution-backed packs. The runner skips them with a warning by default so users without Docker get deterministic behavior. When `--enable-sandboxed-packs` is set, the runner starts one Docker HTTP verifier per requested sandboxed pack and dispatches those scenarios through `benchlocal_cli.sandbox.SandboxClient`.

The v0.4 sandboxes validate the shared HTTP lifecycle and pack dispatch path end-to-end. They are intentionally conservative first-pass verifiers: BugFind accepts solution-block shaped answers or explicit canonical pass markers, CLI accepts safe parseable commands or explicit canonical pass markers, and Hermes supports the multi-turn protocol endpoints plus a single-turn verifier path. Full upstream fixture execution parity remains future work.

## Architecture

```
                        ┌─────────────────────────┐
                        │        cli.py           │   parses argv → mode → pack list
                        └────────────┬────────────┘
                                     │
                        ┌────────────▼────────────┐
                        │       runner.py         │   for each pack:
                        │                         │     load JSONL
                        │                         │     for each scenario:
                        │                         │       build chat-completions request
                        │                         │       POST → endpoint
                        │                         │       capture (response, latency, status)
                        │                         │       dispatch → scoring.<dimension>
                        │                         │     aggregate → mean / p50 / p95
                        └────────────┬────────────┘
                                     │
                ┌────────────────────┼────────────────────┐
                │                    │                    │
        ┌───────▼─────┐    ┌─────────▼──────┐    ┌────────▼──────┐
        │ scoring/    │    │ scoring/       │    │ scoring/      │
        │ tool_call.py│    │ instruct_      │    │ struct_       │
        │             │    │ follow.py      │    │ output.py     │
        └─────────────┘    └────────────────┘    └───────────────┘
                                     │
                        ┌────────────▼────────────┐
                        │    output formatter     │   stdout markdown table
                        │                         │   OR JSON (machine readable)
                        └─────────────────────────┘
```

Execution-backed packs add a side path:

```
runner.py ── SandboxClient ── docker run benchlocal-sandbox-<pack>:<tag>
                              └─ HTTP POST /verify on internal port 9000
```

The runner owns container startup, health checks, per-pack dispatch, and cleanup on normal exit or SIGINT/SIGTERM.

## JSONL pack format

Each pack is one file at `benchlocal_cli/packs/<pack-id>.jsonl`. Each line is one scenario as a JSON object.

Required first line: a metadata line with `__meta__: true`:

```json
{"__meta__": true, "pack_id": "toolcall-15", "version": "1.0.1", "upstream_repo": "stevibe/ToolCall-15", "upstream_commit": "abc123def456", "scenario_count": 15, "sampling_defaults": {"temperature": 0.0, "top_p": 1.0, "max_tokens": 1024, "tool_choice": "auto", "chat_template_kwargs": {"enable_thinking": false}}, "default_thinking": "off", "default_max_seconds": 60}
```

Subsequent lines: scenarios. Schema:

```json
{
  "id": "toolcall-15-001",
  "description": "Single-tool call with date-format constraint",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant with tool access."},
    {"role": "user", "content": "What's the weather like in Paris on March 15, 2026?"}
  ],
  "tools": [
    {"type": "function", "function": {"name": "get_weather", "description": "...", "parameters": {...}}}
  ],
  "verifier": {
    "type": "tool_call",
    "asserts": [
      {"kind": "exact_function_name", "value": "get_weather"},
      {"kind": "required_args_present", "args": ["location", "date"]},
      {"kind": "exact_arg_value", "arg": "location", "value": "Paris"},
      {"kind": "arg_regex", "arg": "date", "pattern": "^2026-03-15$"}
    ]
  },
  "sampling_overrides": {
    "max_tokens": 256
  }
}
```

The `verifier.type` field tells the runner which scoring module to dispatch to. The `asserts` array is module-specific.

## Scoring module contract

Each scoring module exposes:

```python
def score_scenario(scenario: dict, response: dict) -> ScenarioResult
```

Where `ScenarioResult` is:

```python
@dataclass
class ScenarioResult:
    scenario_id: str
    passed: bool
    failure_mode: Literal[
        "passed",
        "verifier_fail",       # assertion failed (e.g. wrong tool name, wrong arg value)
        "wrong_answer",        # response had wrong shape (e.g. no tool_call when expected)
        "invalid_json",        # JSON parse failed where required
        "no_answer_found",     # could not extract any answer (ReasonMath specific)
        "missing_field",       # expected field absent (DataExtract specific)
        "extra_fields",        # unexpected fields present (DataExtract strict mode)
        "schema_violation",    # JSON parsed but schema rejected
        "wrong_structure",     # markdown / yaml structure mismatch
        "timeout",             # HTTP timeout exceeded --timeout-per-case
        "http_error",          # 4xx / 5xx response
        "server_error",        # 500 / model-internal error
        "verifier_not_implemented",   # skipped sandboxed pack or unavailable sandbox
    ]
    detail: str                # human-readable explanation
    latency_seconds: float
    tokens_completion: int | None
```

Per-failure-mode taxonomy is from Codex sanity-check feedback (2026-05-09): users need to distinguish "model gave wrong tool" from "endpoint hit OOM mid-completion" when triaging regressions.

## Output formats

### Default: markdown to stdout

```
=== benchlocal-cli --medium  (endpoint: http://localhost:8020, model: qwen3.6-27b-autoround, 2026-05-09T10:30) ===

Pack                       | Pass / Total | Score | p50 latency | p95 latency | Status
ToolCall-15 (v1.0.1)       |   14 / 15    |  93%  |     8.2s    |     12.1s   | ✅
InstructFollow-15 (v1.0.0) |   13 / 15    |  87%  |    11.4s    |     17.8s   | ✅
StructOutput-15 (v1.0.0)   |   15 / 15    | 100%  |     6.9s    |      9.2s   | ✅
DataExtract-15 (v1.0.0)    |   12 / 15    |  80%  |     7.3s    |     10.5s   | ✅
─────────────────────────|──────────────|───────|─────────────|─────────────|──────
TOTAL                      |   54 / 60    |  90%  |             |             |

Failure breakdown:
  ToolCall-15           1 verifier_fail (toolcall-15-007: wrong arg value for "filename")
  InstructFollow-15     2 verifier_fail (instructfollow-15-003 word-count, instructfollow-15-009 citation-format)
  DataExtract-15        2 missing_field, 1 wrong_value

Run details saved to: results/benchlocal-2026-05-09T10-30-15.json
```

### Optional: `--output json` to stdout

Full structured result blob:

```json
{
  "schema_version": "1",
  "runner_version": "0.0.1",
  "endpoint": "http://localhost:8020",
  "model": "qwen3.6-27b-autoround",
  "thinking_enabled": false,
  "thinking_mode": "pack-defaults",
  "started_at": "2026-05-09T10:30:00Z",
  "finished_at": "2026-05-09T10:55:42Z",
  "mode": "medium",
  "packs": [
    {
      "pack_id": "toolcall-15",
      "version": "1.0.1",
      "upstream_commit": "abc123",
      "scenario_count": 15,
      "thinking_enabled": false,
      "passed": 14,
      "score": 0.933,
      "latency": {"p50": 8.2, "p95": 12.1, "mean": 9.4},
      "scenarios": [
        {
          "id": "toolcall-15-001",
          "passed": true,
          "failure_mode": "passed",
          "detail": "...",
          "latency_seconds": 8.1,
          "tokens_completion": 42,
          "raw_response": { /* OpenAI completion */ }
        }
      ]
    }
  ],
  "totals": {"passed": 54, "total": 60, "score": 0.900}
}
```

## Reproducibility

The output JSON includes everything needed to re-run a scenario for debugging:

- runner version (git SHA at build time)
- pack version + upstream commit
- endpoint URL
- model id
- sampling params (resolved per-scenario)
- raw scenario JSONL line
- raw response

Storing the JSON enables `--previous-result PATH --emit-delta` for regression-tracking.

## Failure mode handling

| Failure mode | Behavior |
|---|---|
| `passed` | Counted as pass |
| `verifier_fail` / `wrong_answer` / `invalid_json` / `missing_field` / `extra_fields` / `schema_violation` / `wrong_structure` / `no_answer_found` | Counted as fail; included in failure breakdown |
| `timeout` | Counted as fail; flag separately ("3 timeouts on this pack — endpoint may need bigger --timeout-per-case") |
| `http_error` / `server_error` | Counted as fail; flag separately ("endpoint instability — N requests got 5xx; investigate before trusting score") |
| `verifier_not_implemented` | Skipped with warning (not counted in totals); shown only when a sandboxed pack is requested without `--enable-sandboxed-packs` or its container cannot start |

## Threshold policy

**Phase 1: raw scores only, no hard gate.**

The `Status:` field in club-3090 compose schema already gates ✅ Production via operational tests (verify-full + verify-stress + bench + soak). Adding another hard gate via quality scores is premature without baselines.

**Phase 2: advisory thresholds + delta tracking.**

Once we have ~10 cross-rig baseline runs (Qwen dual / Qwen single / Gemma dual / etc.), introduce:

- `--threshold pack:percent` flag to set per-pack pass gates
- `--previous-result PATH --regression-threshold 10` to fail on >10pp drop from previous
- Default warn-only behavior at <80% on ToolCall/InstructFollow

**Phase 3: incorporate into compose Status promotion.**

Once Phase 2 has demonstrated stable signal, consider requiring ≥X% on `--quick` packs for `Status: ✅ Production` in club-3090's AGENTS.md.

## Verifier authoring guidance

When porting a pack from BenchLocal:

1. **Lift scenario IDs verbatim** — `toolcall-15-001` etc. — for cross-comparability with the desktop app
2. **Lift prompts unchanged** in semantics; you can normalize whitespace but don't rewrite for clarity
3. **Lift sampling defaults** from the upstream pack's `benchlocal.pack.json` (if accessible) or from observed runs
4. **Port verifier intent, not implementation** — the TypeScript verifier is the spec; Python should produce the same pass/fail outcome on every scenario
5. **Add per-scenario asserts richly** — don't generalize. If a scenario tests "model picks the right tool", spell out exact_function_name + required_args_present + exact_arg_value asserts. The verifier doesn't need to be smart; it needs to be specific.

If a BenchLocal scenario can't be deterministically scored (relies on LLM-judge or human grading), don't port it. Drop the scenario, document the drop in `ATTRIBUTION.md`, and adjust the pack's `scenario_count` accordingly.

## Out of scope (for v1)

- LLM-as-judge fallback verifiers
- Multi-turn / stateful pack scenarios beyond what BenchLocal already provides
- Comparative bench (run two endpoints, compute delta) — defer to Phase 2 via `--previous-result`
- Streaming response evaluation — pack scenarios use single-shot completions
- Cost / token-budget tracking — endpoints typically don't return billing info
- Custom user-authored packs — the JSONL format is documented (PACK_FORMAT.md), but maintaining only the BenchLocal ports for v1
