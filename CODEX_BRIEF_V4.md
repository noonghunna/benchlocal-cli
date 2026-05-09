# Codex implementation brief — benchlocal-cli v0.4 (sandboxed verifiers for BugFind + CLI + HermesAgent)

## Context

v0.3 (commits `a0ca3a5` → `60eb461`) added reasoning-model-aware defaults. Live validation against club-3090's Qwen3.6-27B and Gemma 4 31B dual-card composes confirmed the architecture works end-to-end. Both `--quick` and `--medium` produce comparable, deterministic, latency-tracked results.

The 3 sandboxed packs (BugFind-15, CLI-40, HermesAgent-20) still emit `verifier_not_implemented` because their scoring requires execution-backed infrastructure beyond simple HTTP-call-and-deterministic-verify:

- **BugFind-15** — model proposes a fix to buggy code; verifier must run pytest against the fix to determine pass/fail
- **CLI-40** — model produces a CLI command; verifier must execute it in a sandbox and check stdout/stderr
- **HermesAgent-20** — model runs a multi-turn agent loop with 5 tools (browser, cron, memory, artifact, trace); verifier checks the trace + final state

This brief implements all 3 in a single architectural pass.

## Why all 3 in one brief

- Shared architecture: HTTP verifier-server pattern, container lifecycle managed by `benchlocal_cli/sandbox.py`, runner dispatch via `--enable-sandboxed-packs`
- Shared decisions: mocked-tools approach for determinism, port allocation, retry/timeout policy, fixture lifting from upstream
- Clean phasing: each pack is self-contained per-Dockerfile, but they all integrate into the runner the same way

After v0.4 ships, `--full` mode covers all 8 packs (110 scenarios) — true canonical coverage.

## Architecture

Three containers, one HTTP verifier protocol, shared lifecycle:

```
benchlocal-cli/
├── sandboxes/                        # NEW — verifier sandbox containers
│   ├── bugfind/
│   │   ├── Dockerfile                # python:3.12-slim + pytest
│   │   ├── server.py                 # HTTP verifier on :9001
│   │   ├── fixtures/                 # buggy code + tests per scenario (lifted from upstream)
│   │   └── README.md
│   ├── cli/
│   │   ├── Dockerfile                # debian:slim + coreutils + bash
│   │   ├── server.sh                 # HTTP verifier on :9002 (could be Python instead — your call)
│   │   ├── fixtures/                 # canned input data + expected outputs
│   │   └── README.md
│   └── hermes/
│       ├── Dockerfile                # python:3.12-slim + mocked tools
│       ├── server.py                 # HTTP verifier on :9003 + multi-turn agent loop
│       ├── fixtures/                 # canned tool responses keyed on scenario+turn
│       └── README.md
│
├── benchlocal_cli/
│   └── sandbox.py                    # NEW — Docker lifecycle + verifier HTTP client
│       └── (manages start/stop of sandboxes, POSTs scenario data + model response)
│
└── tools/
    └── build-sandboxes.sh            # NEW — `docker build` all 3 sandboxes
```

### Verifier HTTP protocol

Each sandbox container exposes a single endpoint on its mapped port:

```
POST /verify
Content-Type: application/json

Request body:
{
  "scenario_id": "BF-01",
  "scenario": { /* full upstream scenario object */ },
  "response": { /* OpenAI completion response */ },
  "messages": [ /* full conversation history if multi-turn */ ]
}

Response body:
{
  "passed": true | false,
  "failure_mode": "passed" | "verifier_fail" | "wrong_answer" | "invalid_json" | "timeout" | ...,
  "detail": "human-readable explanation",
  "trace": { /* optional pack-specific debug info, e.g. pytest output, agent trace */ }
}
```

Same shape as `ScenarioResult` from the deterministic verifiers, just delivered over HTTP from the sandbox container instead of computed in-process.

### Runner integration

`benchlocal_cli/sandbox.py` provides:

```python
class SandboxClient:
    """One per pack-id. Lazily starts container, exposes verify(scenario, response).

    Container lifecycle:
      - start(): docker run --rm -d -p <port>:9000 benchlocal-sandbox-<pack>:latest
      - stop(): docker stop <container>

    On runner startup, if any pack needs a sandbox AND --enable-sandboxed-packs
    is set, the relevant SandboxClients are started in parallel. Cleanup on
    runner exit (SIGINT/SIGTERM caught).
    """
```

`benchlocal_cli/runner.py` updated:
- When iterating a sandboxed pack: dispatch to `sandbox_client.verify()` instead of `_stub.score_scenario()`
- If `--enable-sandboxed-packs` is NOT set: skip sandboxed packs with warning (current behavior, unchanged)
- If `--enable-sandboxed-packs` IS set but a container fails to start: skip that pack with warning, continue with others

### Why three separate containers (not one)

| Decision | Reason |
|---|---|
| Per-pack containers | Matches upstream BenchLocal pattern (each pack has `verification/Dockerfile`); independent versioning; failure isolation |
| HTTP verifier protocol (not stdin/stdout) | Allows long-running container reuse across scenarios in the same pack run; clean async semantics |
| Mocked tools (not real Playwright/cron/etc) | Determinism — bench packs need bit-exact reproducibility; real tools introduce flakiness from network / time / page-content changes |
| Container lifecycle managed by runner | Same UX pattern as `bench.sh` managing vLLM container; one less thing for users to install/manage |

## Phases

### Phase A — Sandbox client + runner integration (~2 hr)

Build the shared infrastructure first so all 3 sandboxes plug into the same runner.

1. **`benchlocal_cli/sandbox.py`** — `SandboxClient` class:
   - `__init__(pack_id, image_name, host_port)` — config
   - `start()` — `docker run -d --rm -p <port>:9000 <image>` + wait for `:port/health` to respond
   - `verify(scenario, response, messages) -> ScenarioResult` — HTTP POST `/verify`, parse + return
   - `stop()` — `docker stop <container>`
   - Context manager (`__enter__` / `__exit__`) for clean lifecycle

2. **`benchlocal_cli/runner.py`** updates:
   - On `Runner.run()` start: if `--enable-sandboxed-packs` AND any requested pack has `supports_sandboxed_only: true`, start the relevant `SandboxClient`s
   - In `run_scenario()`: dispatch to `sandbox_client.verify()` for sandboxed packs
   - On `Runner.run()` end: stop all sandbox clients
   - Catch SIGINT and stop containers cleanly before exit

3. **`benchlocal_cli/cli.py`** updates:
   - `--enable-sandboxed-packs` flag already exists (added in v0.2 stub) — wire through to runner properly
   - New flag `--sandbox-image-tag <tag>` (default `latest`) for testing different image versions

4. **`tools/build-sandboxes.sh`** — builds all 3 images:
   ```bash
   docker build -t benchlocal-sandbox-bugfind:latest sandboxes/bugfind/
   docker build -t benchlocal-sandbox-cli:latest sandboxes/cli/
   docker build -t benchlocal-sandbox-hermes:latest sandboxes/hermes/
   ```

5. Tests:
   - `tests/test_sandbox.py` — mock SandboxClient (httpx mock); confirm runner dispatches correctly
   - Integration test (separate file, marked `@pytest.mark.integration`): actually starts a stub Docker container, verifies HTTP round-trip

Commit: `feat(sandbox): SandboxClient + runner integration for HTTP verifier protocol`

### Phase B — BugFind-15 sandbox (~2-3 hr)

Easiest pack. Pure Python + pytest. Pack scenarios in `vendor/BugFind-15/lib/benchmark.ts` define buggy code + tests.

1. **`sandboxes/bugfind/Dockerfile`** — `python:3.12-slim` + `pytest` + `pytest-timeout`
2. **`sandboxes/bugfind/server.py`** — small HTTP server (Flask or stdlib http.server):
   - GET `/health` → 200 OK
   - POST `/verify` → run candidate fix in pytest, return result
3. **`sandboxes/bugfind/fixtures/`** — for each upstream BF-N scenario, lift:
   - `bf-N/buggy.py` — original buggy code from upstream pack
   - `bf-N/test_fix.py` — pytest tests from upstream
   - `bf-N/expected.json` — expected pass/fail criteria from upstream `evaluate(state)`
4. **Update `tools/build-packs.js`** — when generating `bugfind-15.jsonl`, lift the actual buggy code into `raw_scenario.code` field so the runner forwards it to the sandbox as part of the request
5. Tests in `sandboxes/bugfind/test_server.py`: stub a candidate fix, verify pytest runs and result is correct
6. Update `benchlocal_cli/scoring/bugfind.py` (replaces `_stub.py` for this pack) — just routes to SandboxClient

Commit: `feat(bugfind): Docker sandbox + pytest verifier for BugFind-15`

### Phase C — CLI-40 sandbox (~3-4 hr)

Linux exec sandbox. Pack scenarios test the model's ability to produce correct CLI commands.

1. **`sandboxes/cli/Dockerfile`** — `debian:slim` + bash + coreutils + grep + sed + awk + `jq` + `curl` (offline-only — no DNS) + Python (for the verifier server itself)
2. **`sandboxes/cli/server.py`** — HTTP server:
   - POST `/verify` → run candidate command in `subprocess.run` with timeout, capture stdout/stderr, compare to expected
3. **Security hardening:**
   - Container runs as non-root user
   - `--network none` flag at run time (no network access for the sandbox itself)
   - Filesystem write limited to `/tmp/cli-sandbox` (cleared between scenarios)
   - 10s wall-time limit per command (configurable)
   - Output capture limited to 64 KB (truncate beyond)
4. **`sandboxes/cli/fixtures/`** — per upstream CLI-N scenario:
   - `cli-N/input.txt` — input data the command operates on
   - `cli-N/expected.json` — expected stdout, stderr, exit code, side-effect-files-state
5. **Update `tools/build-packs.js`** — extract upstream CLI scenarios with their input fixtures + expected outcomes
6. **Update `benchlocal_cli/scoring/cli.py`** — routes to SandboxClient

Commit: `feat(cli): Docker sandbox + bash exec verifier for CLI-40`

### Phase D — HermesAgent-20 sandbox (~5-7 hr)

Most complex. Multi-turn agent loop with 5 mocked tools.

1. **`sandboxes/hermes/Dockerfile`** — `python:3.12-slim` + httpx + standard tools (no Playwright, no Chromium — all tools mocked)
2. **`sandboxes/hermes/server.py`** — HTTP server hosting:
   - The 5 mocked tools (browser, cron, memory, artifact, trace)
   - The agent-loop driver: receives initial scenario from runner, loops calling the model with tool definitions, captures every tool call + response, ends after N turns or model returns done
   - Verifier: checks final state + trace against scenario's expected outcomes
3. **Mocked tools — deterministic implementations:**
   - **`browser`** — keyed on URL; returns canned page-content fixtures from `fixtures/browser/<url-hash>.html`
   - **`cron`** — receives "schedule for X minutes from now"; returns fixed timestamp from scenario's reference time
   - **`memory`** — in-process `dict`; persists across turns within a single scenario, cleared between scenarios
   - **`artifact`** — in-process bytes-store; same lifecycle as memory
   - **`trace`** — in-process append-only log; checked at end by verifier
4. **`sandboxes/hermes/fixtures/`** — per upstream HA-N scenario:
   - `ha-N/initial_state.json` — starting memory + artifact state
   - `ha-N/browser_responses.json` — URL → canned response mapping
   - `ha-N/expected_trace.json` — the trace the verifier compares against
   - `ha-N/expected_final_state.json` — final memory + artifact state
5. **Agent loop integration:**
   - The runner's `verify()` call to this sandbox needs to be DIFFERENT — it's not a single response to score, it's a multi-turn loop. The sandbox calls the model directly via `--endpoint` (passed in env or config) for each turn, captures the trace, then verifies.
   - Alternative: the runner orchestrates the multi-turn loop and sends each turn's response to the sandbox for trace-tracking + final verification. Cleaner separation.
   - **Decision: runner orchestrates loop; sandbox tracks state.** Each turn:
     1. Runner sends current state + last tool response to sandbox `/verify-turn`
     2. Sandbox returns: model prompt to send next, OR final pass/fail
     3. Runner sends prompt to model endpoint, gets response
     4. Repeat until sandbox returns final pass/fail or hits N=20 turn limit
6. **`sandboxes/hermes/server.py`** endpoints:
   - `POST /verify-start` — initialize scenario state, return first prompt to send to model
   - `POST /verify-turn` — receive model response + tool call, simulate tool, return next prompt OR final result
   - `POST /verify-end` — explicit "model gave up", return final pass/fail
7. **Update `benchlocal_cli/runner.py`** — special-case multi-turn dispatch for HermesAgent (or generalize the loop for any future multi-turn pack)
8. **Update `benchlocal_cli/scoring/hermes.py`** — multi-turn dispatch to sandbox
9. **Update `tools/build-packs.js`** — extract upstream HA scenarios + tool fixtures from `vendor/HermesAgent-20/lib/`

Commit: `feat(hermes): Docker sandbox + mocked-tool agent loop + multi-turn verifier`

### Phase E — Documentation + validation (~1-2 hr)

1. **README.md** — drop "🚧 Beta — quick mode functional" → "🟢 Beta — all 8 packs runnable (5 deterministic + 3 sandboxed)"
2. **docs/DESIGN.md** — add "Sandboxed packs" section explaining the SandboxClient architecture + HTTP verifier protocol
3. **docs/PACK_FORMAT.md** — document new fields per pack (e.g., `raw_scenario.code` for BugFind, browser_responses for HermesAgent)
4. **docs/CONTRIBUTING.md** — add "Build sandboxes" section: `bash tools/build-sandboxes.sh`
5. **docs/EXTRACTOR_NOTES.md** — document the fixture-lifting logic in `tools/build-packs.js` for each sandboxed pack
6. **README.md "Quick start"** — add `bash tools/build-sandboxes.sh` step before `--full` runs
7. **docs/INTEGRATION.md** (in benchlocal-cli) — update club-3090 integration notes: `pip install benchlocal-cli[sandbox]` + `bash tools/build-sandboxes.sh`
8. **`pyproject.toml`** — add `[project.optional-dependencies] sandbox = ["docker>=7.0", "httpx>=0.27"]` (already present from v0.1; just confirm)

Validation gate before declaring v0.4 done:

- [ ] All 3 sandbox Dockerfiles build cleanly via `bash tools/build-sandboxes.sh`
- [ ] `pytest tests/` passes (with new sandbox tests + integration tests)
- [ ] `pip install -e .[sandbox]` works in fresh venv
- [ ] `benchlocal-cli list` shows 5 deterministic + 3 sandboxed (with status flag)
- [ ] `benchlocal-cli run --full --enable-sandboxed-packs --endpoint <mock>` runs all 8 packs end-to-end against a mock endpoint
- [ ] Each sandboxed pack tested independently:
  - `--pack bugfind-15 --enable-sandboxed-packs` → 15/15 against the mock model that always returns the correct fix
  - `--pack cli-40 --enable-sandboxed-packs` → 40/40 against mock model returning correct commands
  - `--pack hermesagent-20 --enable-sandboxed-packs` → 20/20 against mock model following the canonical agent paths
- [ ] CODEX_REPORT.md updated with v0.4 status (overwrite v0.3 report)
- [ ] EXTRACTOR_NOTES.md updated to document fixture-lifting strategy per pack

Commit: `docs: v0.4 sandbox infrastructure complete; ready for live cross-rig validation`

## Constraints

1. **Stdlib + httpx + jsonschema for the Python CLI runtime** — same as v0.3. The sandbox containers can pull whatever they need (pytest, etc.), but the CLI itself stays minimal.
2. **Optional sandbox dep** — `pip install benchlocal-cli` (without `[sandbox]`) still works; the runner detects `docker` SDK presence and emits a helpful error if `--enable-sandboxed-packs` is used without it installed.
3. **Mocked tools only** — no real network access, no real Playwright, no real cron. Determinism wins over realism for benchmarking.
4. **Failure isolation** — if BugFind container crashes mid-run, CLI + Hermes runs continue with that pack flagged "sandbox-unavailable" in the result.
5. **Don't break v0.3 behavior** — non-sandboxed runs (`--quick`, `--medium`, default `--full` without `--enable-sandboxed-packs`) must produce identical output to v0.3.
6. **Container hygiene** — runner cleanly stops + removes all sandbox containers on exit. No orphaned containers if user Ctrl-C's mid-run.
7. **No secrets in fixtures** — upstream packs use synthetic data; preserve that.

## Async report-back protocol

Same as v0.3 — `docs/QUESTIONS.md` if blocked, commit per phase, push at end of each phase, overwrite `docs/CODEX_REPORT.md` with v0.4 status when ALL phases complete.

If you run out of context mid-brief: push what you have to a `wip/v0.4-phase-X` branch, file QUESTIONS.md noting which phase is partial, then stop. Future-you (or future-Codex) can resume from the brief at the right phase.

## What to ASK rather than guess

Likely ambiguity surfaces:

1. **Tool fixture format for HermesAgent** — upstream's `handleToolCall` callbacks may have complex state machines. Lift them into Python equivalents in the sandbox, OR generate JSON traces from upstream TS at extract time.
2. **CLI sandbox security model** — how locked down? Container `--network none` + non-root + tmpfs-backed `/tmp` should be sufficient. If upstream allows network access for any CLI scenarios (unlikely), we have a choice to make.
3. **Multi-turn loop semantics for HermesAgent** — runner-orchestrates vs sandbox-orchestrates. Brief recommends runner-orchestrates; if you discover a pack scenario that requires sandbox-internal state machinery (e.g., dependent timing across turns), we may need to revisit.
4. **Fixture lifting from upstream** — if upstream's TypeScript verifier has complex evaluate() callbacks, the extractor may need pack-specific Python implementations. Document any lossy translations in EXTRACTOR_NOTES.md (same pattern as v0.2).

## Estimated total effort

~10-14 hours Codex work across all 5 phases. Sequential per phase; commit + push per phase so I can review incrementally.

If something blocks (unexpected upstream complexity in HermesAgent fixtures), file QUESTIONS.md after Phase B/C and stop. v0.4-with-2-of-3-sandboxes is a reasonable shippable state if HermesAgent proves harder than estimated.

## When done

Push to origin/master. Reply with the standard report covering all phases, plus:
- Sandbox image sizes (final docker image sizes per container)
- Mock-model validation: 15/15 + 40/40 + 20/20 PASS confirms the verifier infrastructure works correctly
- Live validation against a real club-3090 endpoint can be done by Claude after the report lands

Future v0.5+ items (out of scope for v0.4):
- Real-tool variants (optional Playwright integration for HermesAgent)
- Per-scenario sandbox restart for stricter isolation
- Sandbox image registry (ghcr.io) for users who don't want to build locally
- Cross-platform sandbox support (currently Linux-only via Docker)
