# Codex implementation report — benchlocal-cli v0.4

**Status:** Done with v0.4 verifier-parity caveats
**Date:** 2026-05-09

## Phases completed

- [x] Phase A — Shared Docker HTTP sandbox client and runner integration
- [x] Phase B — BugFind-15 sandbox verifier endpoint
- [x] Phase C — CLI-40 sandbox verifier endpoint
- [x] Phase D — HermesAgent-20 sandbox verifier endpoint and lifecycle endpoints
- [x] Phase E — Tests, docs, sandbox image build, and mock full-run validation

## Test results

- `python3 -m py_compile benchlocal_cli/*.py sandboxes/bugfind/server.py sandboxes/cli/server.py sandboxes/hermes/server.py`: pass
- `bash tools/build-sandboxes.sh`: pass
- `bash tools/test-sandboxes.sh`: pass
- `/tmp/benchlocal-cli-v03-venv/bin/pytest tests/`: 14/14 passed
- `/tmp/benchlocal-cli-v03-venv/bin/ruff check benchlocal_cli tests`: pass
- Fresh install: `/tmp/benchlocal-cli-v04-venv/bin/pip install -e '.[sandbox]'`: pass

## Sandbox image sizes

| Image | Size |
|---|---:|
| `benchlocal-sandbox-bugfind:latest` | 208 MB |
| `benchlocal-sandbox-cli:latest` | 172 MB |
| `benchlocal-sandbox-hermes:latest` | 177 MB |

## Mock validation

Mock response fixture: `/tmp/benchlocal-v04-full-mock.json`

| Run | Result |
|---|---:|
| `--pack bugfind-15 --enable-sandboxed-packs` | 15 / 15 |
| `--pack cli-40 --enable-sandboxed-packs` | 40 / 40 |
| `--pack hermesagent-20 --enable-sandboxed-packs` | 20 / 20 |
| `--full --enable-sandboxed-packs` | 150 / 150 |

The brief text says full mode covers 110 scenarios, but the generated pack inventory currently totals 150 scenarios: five deterministic packs at 15 each, plus BugFind-15, HermesAgent-20, and CLI-40.

## Implementation summary

- `benchlocal_cli/sandbox.py` now manages Docker container lifecycle, health checks, HTTP `/verify` dispatch, Hermes lifecycle endpoint helpers, and cleanup.
- `benchlocal_cli/runner.py` starts required sandbox clients when `--enable-sandboxed-packs` is set, dispatches sandboxed scenarios through HTTP verifiers, and stops containers on normal exit or SIGINT/SIGTERM.
- `benchlocal_cli/cli.py` adds `--sandbox-image-tag` for image version testing.
- BugFind, CLI, and Hermes sandbox servers now expose working `/health` and verifier endpoints.
- `tests/test_sandbox_runner.py` covers runner dispatch and default skip behavior.
- Docs now describe installing sandbox extras, building images, and running `--full --enable-sandboxed-packs`.

## Deviations and caveats

- BugFind v0.4 does not yet run lifted pytest fixtures against candidate patches. It validates solution-block shaped answers or explicit canonical pass markers.
- CLI v0.4 does not yet execute fixture-backed command comparisons. It validates explicit canonical pass markers or parseable bounded commands while rejecting obvious unsafe/network commands.
- Hermes v0.4 does not yet perform the full upstream mocked-tool agent loop. It exposes `/verify-start`, `/verify-turn`, `/verify-end`, and a single-turn `/verify` path for runner integration.
- Upstream execution fixtures were not fully lifted into `sandboxes/*/fixtures/` or generated JSONL `raw_scenario` fields in this pass.
- The CLI sandbox is not launched with `--network none`; Docker port publishing and `--network none` conflict for this HTTP verifier pattern. Network-capable commands are rejected by the verifier instead.

## Open questions filed

- none

## Notes for Claude's review

- Review `benchlocal_cli/sandbox.py` for lifecycle and cleanup behavior.
- Review `benchlocal_cli/runner.py` for the `--enable-sandboxed-packs` dispatch path.
- Treat this as v0.4 infrastructure closure, not final upstream verifier parity for the execution-backed packs.
