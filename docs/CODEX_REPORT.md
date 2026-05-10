# Codex implementation report — benchlocal-cli v0.7

**Status:** Implemented with one runner-level Hermes gap and incomplete local acceptance gate  
**Date:** 2026-05-10

## Phases completed

- [x] Phase A — vendored upstream `verification/` runtimes for BugFind-15, CLI-40, and HermesAgent-20
- [x] Phase B — BugFind sandbox delegates verification to upstream `verifyAnswer`
- [x] Phase C — CLI sandbox delegates one-shot and replay checks to upstream verifier functions
- [x] Phase D — Hermes upstream runtime vendored and documented; current HTTP verifier remains the v0.6 state/trace adapter because upstream Hermes runtime owns the full model loop
- [x] Phase E — pack metadata, Docker build wiring, version, changelog, and docs updated

## Fixture source

The expected static fixture trees (`scenarios/<id>/workspace`, `buggy.py`, `flow.json`, etc.) are not present in the upstream mirrors. The usable fixture source is the upstream `verification/` runtime shipped in each pack repository:

- BugFind-15: `verification/core.mjs`, `manifest.mjs`, service helpers, and runtime server
- CLI-40: `verification/core.mjs`, `manifest.mjs`, `bash-session.mjs`, and `scenario-data.json`
- HermesAgent-20: `verification/core.mjs`, `manifest.mjs`, `hermes-runtime.mjs`, and `agent-runner.py`

`tools/sync-vendor.sh` now syncs those verifier runtimes into `vendor/<Pack>/verification/`, and `tools/build-sandboxes.sh` copies them into each Docker build context before image build.

## Implementation summary

- `tools/build-packs.js` now records `raw_scenario.fixture_status: "upstream-verification-runtime"` for sandboxed packs.
- CLI scenarios use the upstream one-shot or multi-round system prompt extracted from `vendor/CLI-40/verification/manifest.mjs`.
- `sandboxes/bugfind/server.py` calls upstream BugFind `verifyAnswer(scenarioId, answer)` through Node, preserving the mock-pass shortcut and returning upstream payloads in traces.
- `sandboxes/cli/server.py` calls upstream `verifyOneShotSubmission` for one-shot scenarios and `verifyMultiRoundReplay` for replayable multi-round command blocks. Python, Perl, and Ruby are allowed because the upstream runtime is a general shell task environment rather than shell-only.
- `sandboxes/hermes/server.py` carries the upstream verifier runtime in the image but does not yet delegate to it. The upstream Hermes entrypoint drives full model runs against a pinned Hermes checkout; benchlocal-cli currently owns model calls and posts one model response at a time to `/verify`. Full parity needs runner-side multi-turn delegation instead of only a sandbox-local patch.
- All sandbox `/health` endpoints now report `stage="v0.7"`.

## Validation

- `bash tools/build-sandboxes.sh`: pass. Built all three images:
  - `benchlocal-sandbox-bugfind:latest`
  - `benchlocal-sandbox-cli:latest`
  - `benchlocal-sandbox-hermes:latest`
- `python3 -m py_compile benchlocal_cli/cli.py benchlocal_cli/runner.py sandboxes/bugfind/server.py sandboxes/cli/server.py sandboxes/hermes/server.py`: pass
- `/tmp/benchlocal-cli-v03-venv/bin/pytest tests/`: pass, 17/17
- `/tmp/benchlocal-cli-v03-venv/bin/ruff check benchlocal_cli tests`: pass
- `bash tools/test-sandboxes.sh`: pass, all three `/health` endpoints report `stage="v0.7"`
- Mock validation: not rerun in this local pass.
- Real-model A/B on Qwen3.6-27B and Gemma 4 31B: not run here.

## Remaining gaps

- HermesAgent-20 is not fully upstream-runtime-backed yet. The upstream verifier is vendored, but integrating it cleanly requires changing the runner contract so the sandbox can own the full agent loop or adding a runner adapter that can stream Hermes tool turns through the existing model client.
- CLI multi-round parity is partial. The sandbox can replay command blocks through the upstream verifier, but benchlocal-cli still asks for a single assistant response and does not yet orchestrate iterative CLI tool turns.
- The public-release acceptance gate is not complete until mock validation and real-model A/B are rerun.

## Commits in this round

- `feat(vendor): sync upstream verifier runtimes`
- `feat(packs): expose upstream verifier metadata`
- `feat(sandboxes): adapt to upstream verifier runtimes`
- `docs: report v0.7 verifier-runtime lift`

## Tag status

No `v0.7.0` tag should be cut until the remaining acceptance checks pass. The version is bumped to `0.7.0` in-tree so the branch represents the v0.7 candidate.
