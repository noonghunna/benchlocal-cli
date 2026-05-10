# Codex implementation brief — benchlocal-cli v0.7.1 (runner-side multi-turn delegation)

## Context

v0.7 shipped real upstream-runtime verifiers for BugFind / CLI / Hermes (commits `027263e` → `4d911f3`). Real-model A/B revealed the verifiers work — but two pack classes are stuck at 0% because the runner only sends ONE chat completion per scenario:

| Pack class | Today's score on Qwen3.6-27B | Why |
|---|---:|---|
| cli-40 one-shot (25 scenarios) | 10/25 = 40% | Working as designed |
| cli-40 multi-round (15 scenarios) | 0/15 | **Runner doesn't loop on tool calls — sends one assistant response, sandbox can't grade an iterative trace** |
| hermesagent-20 (20 scenarios) | 6-8/20 = 30-40% | v0.6 state-machine adapter; not measuring real multi-turn behavior |

The HTTP protocol for multi-turn is **already defined** (in `docs/SANDBOX_PROTOCOL.md`): `/verify-start` returns `{prompt, tools, scenario_state_id}`, `/verify-turn` accepts `{scenario_state_id, model_response}` and returns either `{action: "next-prompt", prompt, tools}` or `{action: "verify-final", passed, ...}`. The Hermes sandbox already implements these endpoints (`sandboxes/hermes/server.py`).

**Missing piece**: the runner's `run_scenario()` doesn't loop on `next-prompt` — it only sends one chat completion and stops. v0.7.1 closes this gap.

## Why one brief

CLI multi-round + HermesAgent want the same multi-turn loop primitive. Building it once in the runner unlocks both. Sandbox-side differences (which tools, how to grade) are already handled per-pack. The runner doesn't need to know what the tools do — just that it should loop.

## Starting state — what already works

You start from `master` HEAD (currently `12d7be4` v0.7 candidate). Don't undo:

- `benchlocal_cli/sandbox.py`: `SandboxConfig.multi_turn: bool` flag is set per-pack (`hermesagent-20: True`, others: False). `SandboxClient.verify_hermes_{start,turn,end}()` methods exist + work today.
- `sandboxes/hermes/server.py`: `/verify-start`, `/verify-turn`, `/verify-end` endpoints are implemented (v0.7 candidate). They use scenario-scoped state in `STATES: dict[str, dict]` keyed by `scenario_state_id`.
- `sandboxes/cli/server.py`: only `/verify` (single-turn) right now. v0.7.1 adds `/verify-start/turn/end` for multi-round scenarios.
- `benchlocal_cli/runner.py`: `run_scenario()` does single chat completion → `/verify`. v0.7.1 branches on `meta.get("supports_sandboxed_only") and config.multi_turn` to take the multi-turn path.
- All v0.5/v0.6/v0.7 patches preserved (sandbox `/health` reports `stage="v0.7.1"` after this round).

### What changed since v0.7 (don't undo)

- v0.7 candidate: BugFind + CLI delegate to upstream `verifyAnswer / verifyOneShotSubmission / verifyMultiRoundReplay` JS runtime (already in `vendor/<Pack>/verification/`)
- v0.7 hotfix (commit `[TBD]` — pending in this round): `sandboxes/cli/Dockerfile` creates `/workspace` with verifier ownership instead of overriding `CLI40_WORKSPACE_DIR` env var. The upstream CLI prompt hardcodes `/workspace/<file>` paths; redirecting via env breaks the seed-vs-prompt-vs-grade alignment. Don't reintroduce the env override.

## Architecture

### The HTTP protocol (already designed; we're wiring the runner side)

```
Runner: POST /verify-start
        { scenario_id, scenario }
        ← { prompt: [...messages...], tools: [...], scenario_state_id }

Runner: send chat completion to model with `prompt` + `tools`
        → model_response

Runner: POST /verify-turn
        { scenario_state_id, model_response }
        ← either:
          { action: "next-prompt", prompt, tools, turn_count }
          (loop continues)
        OR:
          { action: "verify-final", passed, failure_mode, detail, trace }
          (scenario done)

  ← if turn_count exceeds max_turns:
        Runner: POST /verify-end
                { scenario_state_id }
                ← { passed: false, failure_mode: "timeout", ... }
```

### Tool call shape (standard OpenAI)

When the sandbox returns `tools`, those are OpenAI-style tool definitions. The model's response will include `tool_calls` in `choices[0].message`. The runner re-sends the chat completion with the full conversation history including `tool` role messages whose content is whatever the sandbox returned in the next `prompt` (the sandbox simulates the tool execution).

### Per-pack turn limits

- `cli-40` multi-round: 15 turns max (matches upstream `runMultiRoundModelScenario` loop bound)
- `hermesagent-20`: 20 turns max
- General: scenario-level override via `scenario.max_turns` if needed

## Phases

### Phase A — Generalize SandboxClient multi-turn methods (~1 hr)

**Goal**: rename `verify_hermes_*` → `verify_multiturn_*` so they work for any multi-turn pack. Keep aliases for back-compat (Hermes tests reference them).

Files to touch:
- `benchlocal_cli/sandbox.py`:
  - Add `verify_multiturn_start(scenario)`, `verify_multiturn_turn(state_id, model_response)`, `verify_multiturn_end(state_id)`
  - Keep `verify_hermes_*` as thin aliases that call the new methods (back-compat)
- `tests/test_sandbox_runner.py`: extend with multi-turn dispatch test (mock the loop)

### Phase B — CLI sandbox multi-turn endpoints (~2-3 hr)

**Goal**: add `/verify-start`, `/verify-turn`, `/verify-end` to the CLI sandbox so multi-round scenarios can be driven iteratively.

The upstream `verifyMultiRoundReplay()` accepts a list of commands all-at-once and replays them through a `BashSession`. For iterative use, you need a thinner wrapper that holds the `BashSession` open across HTTP calls and grades at end.

Files to touch:
- `sandboxes/cli/server.py`:
  - Add `STATES: dict[str, dict]` keyed by `scenario_state_id` (mirror the Hermes pattern)
  - `/verify-start`: load multi-round scenario, seed workspace, init `BashSession`, return upstream `MULTIROUND_SYSTEM_PROMPT` + bash tool definition
  - `/verify-turn`: parse model's `tool_calls`, execute bash command via the held session, return result as next prompt (or grade and finalize when model emits a non-tool-call assistant message)
  - `/verify-end`: timeout / abandon — grade what we have, return final
  - Reuse upstream grading: `gradeMultiRoundScenario(ctx, attempt)` (find it in `vendor/CLI-40/verification/core.mjs`)
- `vendor/CLI-40/verification/`: extract the bash tool definition + multi-round system prompt from `manifest.mjs` + grading helpers from `core.mjs`. The upstream code already has a `BashSession` class — use it directly.
- `sandboxes/cli/test_server.py`: multi-turn flow test with mock model responses

State shape (mirror Hermes):
```python
STATES[state_id] = {
    "scenario_id": "CLI-XX",
    "scenario": {...},
    "session": BashSession instance,
    "tool_calls": [],
    "tool_results": [],
    "assistant_messages": [],
    "turn_count": 0,
    "started_at": iso8601,
}
```

### Phase C — Runner multi-turn loop (~3-5 hr) ⭐ core work

**Goal**: when running a sandboxed scenario where `SandboxConfig.multi_turn = True`, drive the multi-turn loop instead of the single chat-completion path.

Files to touch:
- `benchlocal_cli/runner.py`:
  - In `run_scenario()`, branch early on `meta.get("supports_sandboxed_only")` AND `self._sandbox_clients[pack_id].config.multi_turn`
  - New `_run_multiturn_scenario(meta, scenario, sandbox_client)` method:
    1. Call `sandbox_client.verify_multiturn_start(scenario)` → get `{prompt, tools, scenario_state_id}`
    2. Initialize conversation history with `prompt` (the system + user messages)
    3. Loop up to `max_turns` (default 15 or 20 per pack):
        a. Build chat completion request with current history + tools
        b. Send to endpoint, parse response
        c. Append assistant message to history
        d. POST to `verify-turn` with `{scenario_state_id, model_response: response}`
        e. If `action == "verify-final"`: build ScenarioResult from `{passed, failure_mode, detail, trace}`, break
        f. If `action == "next-prompt"`: append the new `prompt` messages to history (these are the simulated tool results), continue
    4. If turn limit exceeded: call `verify_multiturn_end(state_id)` for final grading, build ScenarioResult
  - Token accounting: sum `tokens_completion` across all turns
  - Latency accounting: total wall time across all chat completions
  - Cleanup: ensure `verify_multiturn_end` is called on exception paths

State to capture in ScenarioRun for diagnostics (extend `ScenarioRun` if needed):
- `turn_count`: how many turns the scenario took
- `assistant_messages`: list of model responses across turns
- `tool_calls`: aggregated across turns

### Phase D — Tests + docs + version bump (~1-2 hr)

1. **Tests**: extend `tests/test_sandbox_runner.py` with multi-turn dispatch tests (mock SandboxClient that returns `next-prompt` for N-1 turns then `verify-final`).
2. **Docs**: update `docs/SANDBOX_PROTOCOL.md` to mark `/verify-start/turn/end` as fully implemented (was: "scaffold" / "Hermes-only"). Add a runner-side section explaining the loop.
3. **Sandbox `/health` stage labels**: bump to `"v0.7.1"` on all 3 sandboxes (CLI gains `multi_turn=true` indicator in /health).
4. **`benchlocal_cli/sandbox.py`**: flip `SandboxConfig.multi_turn = True` for `cli-40` (auto-routes to multi-turn path for multi-round scenarios).
5. **Pack metadata**: ensure `cli-40` JSONL marks each scenario's kind so the runner knows when to use single-turn vs multi-turn dispatch (CLI is mixed: 25 oneshot + 15 multiround).
6. **Version bump**: `pyproject.toml` + `benchlocal_cli/__init__.py` → `0.7.1`.
7. **CHANGELOG entry**: brief explaining what v0.7.1 unlocks.

## Constraints

- **Don't break single-turn scenarios.** The single chat-completion → `/verify` path stays for all packs that aren't `multi_turn=True`. Only sandbox-side multi-round scenarios route through the new path.
- **Backwards compat for Hermes tests.** Existing `verify_hermes_*` methods need to keep working (alias them to `verify_multiturn_*`).
- **Mock-pass marker still works.** `BENCHLOCAL_PASS:scenario_id` short-circuits multi-turn dispatch the same way it does single-turn.
- **Token/time budget.** Multi-turn scenarios take longer. Per-scenario timeout `meta.default_max_seconds` should apply across the WHOLE scenario, not per-turn. Default: 600s for multi-round scenarios.
- **Tool call format**: standard OpenAI. If the model's chat-completion response contains `tool_calls`, the runner has to know to send them back through the multi-turn protocol. Handle the case where the model emits prose-only (no tool calls): post the response anyway, sandbox decides if scenario is final or not.

## Async report-back protocol

Same as v0.4/v0.6/v0.7: write `docs/CODEX_REPORT.md` with phase-by-phase status. File `docs/QUESTIONS.md` if you hit a design choice that needs Claude+user input.

## What to ASK rather than guess

- **Per-scenario turn limits.** Upstream uses 15 for CLI multi-round + 15 for HermesAgent runs. We could pull from `scenario.max_turns` if the upstream metadata exposes it, or use defaults. If upstream's manifests have a per-scenario value, use it; otherwise default and document.
- **Tool result formatting.** Different model APIs format tool results slightly differently (`role: "tool"` with `tool_call_id` vs `role: "function"`). vLLM's qwen3_coder parser expects a specific shape — check what's actually in the chat history vs what the model expects to reply to.
- **Token budget across multi-turn.** A single-turn scenario uses `max_tokens=1024` from sampling. Multi-turn would either use the same per-turn (cheap, may truncate complex responses) or grow it for longer responses. Decide based on what upstream uses for similar scenarios.

## Estimated total effort

- Phase A (rename + alias): 1 hr
- Phase B (CLI multi-turn sandbox endpoints): 2-3 hr
- Phase C (runner multi-turn loop): 3-5 hr ⭐ main work
- Phase D (tests + docs + bump): 1-2 hr

**Total: ~7-11 hr.** Phase C is the variable; the runner orchestration logic for multi-turn agentic loops is fiddly because of model-quirk handling (truncated responses, tool-call format variants, history-pruning if scenarios go long).

## When done

Acceptance gate:

1. `tools/build-sandboxes.sh` builds clean
2. `tools/test-sandboxes.sh` reports all 3 healthy with `stage="v0.7.1"`
3. `pytest tests/` passes (target: 18+ tests with new multi-turn coverage)
4. `--sandboxed-only` on Qwen3.6-27B AND Gemma 4 31B:
   - cli-40: ≥40% overall (was 25% — multi-round 0/15 should now register some passes)
   - hermesagent-20: real multi-turn behavior measurable (should differ from v0.7's 30-40% shape-check ceiling — could go up OR down depending on model)
   - bugfind-15: unchanged (still single-turn)
5. `docs/CODEX_REPORT.md` overwritten with v0.7.1 status

After acceptance gate:
- Tag `v0.7.1` (release-notes workflow auto-publishes)
- Re-run cross-rig A/B → update `noonghunna/club-3090` compose Quality lines
- This is the *real* v0.7 vision completed. After v0.7.1: public flip is unblocked.

---

**Cross-reference:**
- v0.7 brief: [`CODEX_BRIEF_V7.md`](CODEX_BRIEF_V7.md) — fixture-gap closure (vendored upstream verifier runtimes)
- v0.7 report: [`docs/CODEX_REPORT.md`](docs/CODEX_REPORT.md) — flagged the multi-turn gap explicitly
- HTTP protocol spec: [`docs/SANDBOX_PROTOCOL.md`](docs/SANDBOX_PROTOCOL.md) — already designed for multi-turn
- Roadmap: [`ROADMAP.md`](ROADMAP.md) — v0.7.1 unblocks the public flip
