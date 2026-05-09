# Codex implementation report — benchlocal-cli v0.6

**Status:** Implemented with explicit fixture-parity gap
**Date:** 2026-05-09

## Phases completed

- [x] Phase A — BugFind verifier replaced with strict solution-block + upstream-rubric checks
- [x] Phase B — CLI verifier replaced with safe `shell=False` command execution, workspace reset, timeout, output capture, and safety rejects
- [x] Phase C — Hermes verifier replaced with stateful mocked-tool trace handling and final success-case checks
- [x] Phase D — Docs, version bump, changelog, tests, sandbox build/smoke validation

## Important gap

`CODEX_BRIEF_V6.md` assumes upstream fixture assets that are not present in the local mirrors:

- `vendor/BugFind-15` has rubric callbacks in `lib/benchmark.ts`, but no `lib/scenarios/<id>/buggy.py` or `test_fix.py` fixture tree.
- `vendor/CLI-40` has `verification/scenario-data.json`, but no workspace input files or expected output files.
- `vendor/HermesAgent-20` has scenario metadata, but no browser/cron/memory/artifact trace fixtures.

So v0.6 is a real verifier lift from the available vendored data, not literal hidden-fixture parity. The pack generator now embeds `raw_scenario.fixture_status` to make this visible at runtime.

## Implementation summary

- `tools/build-packs.js` emits `raw_scenario` metadata for BugFind, CLI, and Hermes.
- `sandboxes/bugfind/server.py` validates candidate structure, trap/no-bug discipline, known failure patterns, and per-scenario rubric evidence.
- `sandboxes/cli/server.py` extracts one command, rejects unsafe/network/destructive commands, runs it with `shell=False` in a fresh temp workspace, caps timeout/output, and compares explicit expectations when present.
- `sandboxes/hermes/server.py` supports `/verify`, `/verify-start`, `/verify-turn`, and `/verify-end` with scenario-scoped memory/artifact/trace state.
- All sandbox `/health` endpoints now report `stage="v0.6"`.
- Version bumped to `0.6.0`; `CHANGELOG.md` added.

## Validation

- `python3 -m py_compile benchlocal_cli/*.py sandboxes/bugfind/server.py sandboxes/cli/server.py sandboxes/hermes/server.py`: pass
- `/tmp/benchlocal-cli-v03-venv/bin/pytest tests/`: pass
- `/tmp/benchlocal-cli-v03-venv/bin/ruff check benchlocal_cli tests`: pass
- `bash tools/build-sandboxes.sh`: pass
- `bash tools/test-sandboxes.sh`: pass, all three `/health` endpoints report `stage="v0.6"`
- Mock validation with mixed pass/fail responses:
  - `--full --enable-sandboxed-packs --mock-responses-from-json /tmp/benchlocal-v06-mixed-mock.json`: 19 / 150 overall
  - sandbox distributions in that run: BugFind 2 / 15, CLI 1 / 40, Hermes 1 / 20
  - deterministic packs intentionally received generic mock pass markers in this mixed fixture, so they mostly failed their normal in-process verifiers; the acceptance point was that sandbox packs no longer trivially return 150 / 150

## Deviations from the brief

- Did not implement BugFind pytest execution because no pytest fixtures are present in the vendored mirror.
- Did not implement CLI UDS + `--network none`; the runner protocol remains HTTP over a mapped port. Command execution itself is non-root, `shell=False`, timeout-limited, temp-workspace scoped, and network/destructive commands are rejected before execution.
- Did not implement full Hermes browser/cron fixture simulation because no flow fixtures are present locally. The server implements deterministic memory/artifact/trace mocks and stateful lifecycle semantics.

## Open questions filed

- none. The missing fixture trees are documented as an implementation gap rather than a design blocker.
