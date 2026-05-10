# Codex implementation report — benchlocal-cli v0.7.1

**Status:** Implemented; local structural validation complete; real-model A/B not run here  
**Date:** 2026-05-10

## Phases completed

- [x] Phase A — generalized sandbox multi-turn client methods
- [x] Phase B — added CLI-40 multi-turn HTTP endpoints
- [x] Phase C — added runner-side multi-turn loop
- [x] Phase D — tests, docs, stage labels, and version bump

## Implementation summary

- `benchlocal_cli/sandbox.py` now exposes `verify_multiturn_start`, `verify_multiturn_turn`, and `verify_multiturn_end`.
- `verify_hermes_start`, `verify_hermes_turn`, and `verify_hermes_end` remain as aliases for backwards compatibility.
- `cli-40` is marked `multi_turn=True` in the sandbox registry. The runner only uses the multi-turn path for CLI scenarios whose `raw_scenario.kind` is `multiround`; one-shot CLI scenarios still use `/verify`.
- `benchlocal_cli/runner.py` now loops through `/verify-start` and `/verify-turn` until the sandbox returns `verify-final` or the turn limit is hit.
- Multi-turn runs aggregate completion tokens, total wall-clock latency, assistant messages, tool calls, and turn count into the saved scenario result.
- `sandboxes/cli/server.py` implements `/verify-start`, `/verify-turn`, and `/verify-end`. It seeds the upstream workspace via the vendored CLI runtime, returns a bash tool definition, captures commands across turns, gives iterative command feedback, and grades final state with upstream `verifyMultiRoundReplay()`.
- `sandboxes/hermes/server.py` now reports `stage="v0.7.1"` and returns only incremental tool-result messages on `next-prompt`, matching the runner's append-only history loop.
- All sandbox `/health` endpoints now report `stage="v0.7.1"`.

## Validation

- `python3 -m py_compile benchlocal_cli/runner.py benchlocal_cli/sandbox.py benchlocal_cli/types.py sandboxes/bugfind/server.py sandboxes/cli/server.py sandboxes/hermes/server.py`: pass
- `/tmp/benchlocal-cli-v03-venv/bin/pytest tests/`: pass, 18/18
- `/tmp/benchlocal-cli-v03-venv/bin/ruff check benchlocal_cli tests`: pass
- `bash tools/build-sandboxes.sh`: pass
- `bash tools/test-sandboxes.sh`: pass, all three `/health` endpoints report `stage="v0.7.1"`
- Real-model A/B on Qwen3.6-27B and Gemma 4 31B: not run here.

## Design notes

- The CLI sandbox does not fork upstream `core.mjs` to export private grading helpers. Instead, `/verify-turn` executes model commands for feedback and `/verify-final` grades by replaying the captured command trace through upstream `verifyMultiRoundReplay()`. This keeps upstream runtime code as the grading source of truth.
- CLI command feedback currently runs each command through `bash -lc` in `/workspace`; filesystem changes persist, while shell-local state such as `cd` or exported variables does not persist between turns. The upstream replay grader remains authoritative for final scoring.
- HermesAgent is now measured through the runner-side multi-turn loop, but the sandbox still uses the existing deterministic mocked-tool adapter rather than fully delegating to upstream Hermes' own model-runner entrypoint.

## Remaining acceptance work

- Run `--sandboxed-only` against Qwen3.6-27B and Gemma 4 31B.
- Confirm CLI-40 multi-round scenarios move off the previous 0/15 floor.
- Decide whether the CLI feedback loop needs a truly persistent shell process for better parity with upstream `BashSession`.
