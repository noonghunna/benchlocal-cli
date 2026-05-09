# Codex implementation brief — benchlocal-cli v0.6 (real verifier parity for sandboxed packs)

## Context

v0.4 shipped working sandbox infrastructure (Docker lifecycle, HTTP protocol, runner integration) plus deterministic shape-check verifiers for BugFind / CLI / Hermes. v0.5 polished the UX (mode reshuffle, `--full` defaults sandboxed, loud failures, URL norm, ReasonMath prompt fix).

Both shipped versions are validated end-to-end against real models (Qwen3.6-27B and Gemma 4 31B on dual 3090) — see `docs/CODEX_REPORT.md` and the Quality lines on `noonghunna/club-3090`'s `dual.yml` composes.

**v0.6 is the verifier-parity lift**: replace the v0.4 shape-checks with real upstream-fidelity verification. Three sandboxes, three different verification models. Single brief because the sandbox lifecycle + HTTP protocol is shared infrastructure already in place.

## Why all 3 in one brief

- Shared Docker / HTTP / SandboxClient lifecycle from v0.4 — reuse, don't re-architect
- Each pack is independent verifier-side; can land in any order
- Tests and docs share patterns; one round of build-sandboxes.sh + test-sandboxes.sh + cross-rig validation covers everything
- Ergonomically: easier for a reviewer to see "v0.4 was infrastructure, v0.6 is the real verifiers" as one diff

## Starting state — what already works

You start from `master` HEAD with these committed:

- `benchlocal_cli/sandbox.py` — full Docker lifecycle + HTTP client (Phase A from v0.4 is **done**)
- `benchlocal_cli/runner.py` — sandbox dispatch + auto-start on `--full` + signal-clean cleanup
- `sandboxes/{bugfind,cli,hermes}/` — Dockerfile + server.py + fixtures/.gitkeep present
- `sandboxes/{bugfind,cli,hermes}/server.py` — v0.4 shape-check verifiers (replace with real)
- `tools/build-sandboxes.sh` + `tools/test-sandboxes.sh` — work today
- `tests/test_sandbox_runner.py` — covers sandbox dispatch via mock
- `vendor/{BugFind-15,CLI-40,HermesAgent-20}/` — upstream sources mirrored locally; `lib/` has the TS source for fixture details, `cli/` has runtime

The HTTP verifier protocol (`/health`, `/verify`, `/verify-{start,turn,end}`) is unchanged. **You're replacing the verifier *implementation* inside each sandbox container, not the wire protocol.**

## Architecture

### Recap of the HTTP verifier protocol (v0.4 — unchanged)

Single-turn (BugFind, CLI):
```http
POST /verify
{ "scenario_id": "BF-01", "scenario": {...full upstream scenario...},
  "response": {...OpenAI completion...}, "messages": [...history...] }

→ 200 OK
{ "passed": true|false,
  "failure_mode": "passed"|"verifier_fail"|"wrong_answer"|"invalid_json"|"timeout"|...,
  "detail": "human-readable",
  "trace": {...} }
```

Multi-turn (Hermes): `/verify-start` → `/verify-turn` × N → `/verify-end`. The runner orchestrates the loop; sandbox tracks state per scenario via `scenario_state_id`.

### Why three separate containers (unchanged from v0.4)

- BugFind needs Python + pytest + per-scenario fixture trees → ~210 MB image
- CLI needs bash + coreutils + jq + Python (verifier) + a `--network none` workspace → ~170 MB
- Hermes needs Python + scenario state machines + mocked tool fixtures → ~180 MB

Three separate images keep concerns isolated and security models distinct.

## Phases

### Phase A — BugFind-15 real pytest verification (~3-5 hr)

**Goal:** replace shape-check with real pytest-against-fixtures.

**Files to touch:**

1. `vendor/BugFind-15/lib/` — read scenario test files. Each scenario has a buggy code snippet + a test file. Lift these into the sandbox container.
2. `tools/build-packs.js` — when generating `bugfind-15.jsonl`, embed the buggy code + test code into `raw_scenario.code` and `raw_scenario.test` (or similar). The runner forwards these to the sandbox via the existing `/verify` payload.
3. `sandboxes/bugfind/Dockerfile` — already has `python:3.12-slim` + `pytest` + `pytest-timeout`. May need to add `tmpfile`-friendly tooling.
4. `sandboxes/bugfind/server.py` — replace `_verify()` with real pytest execution:
   - Extract candidate fix from `response.choices[0].message.content`
   - Strategies: look for `<solution verdict="fix">...code...</solution>` blocks first; fall back to fenced code blocks (```` ```python ... ``` ````); last resort treat the response as raw code
   - Locate fixture: `/app/fixtures/<scenario_id>/buggy.py` + `test_fix.py`
   - Apply candidate fix to a tmp copy of `buggy.py` in `tempfile.TemporaryDirectory()`
   - Run `pytest test_fix.py --timeout=10` from that tmpdir, capture stdout/stderr/exit_code
   - Pass if pytest exits 0; fail with the captured output as `trace.stdout` + `trace.stderr`
5. `sandboxes/bugfind/fixtures/<scenario_id>/` — lifted from `vendor/BugFind-15/lib/scenarios/<id>/`. Build into the image at build time (COPY into Docker layer).
6. `sandboxes/bugfind/test_server.py` — unit tests stubbing model responses + verifying pass/fail discrimination.

**Failure modes to map:**

- `wrong_answer` — model didn't emit a candidate fix at all (no code in response)
- `invalid_json` — already covered, can keep
- `verifier_fail` — pytest red, candidate fix syntactically valid but semantically wrong
- `timeout` — pytest hit 10s timeout
- `server_error` — fixture missing, container OOM, etc

### Phase B — CLI-40 real command execution (~3-5 hr)

**Goal:** replace shape-check with real subprocess execution + fixture-backed expected-output diff.

**Hard problem to solve up-front:** v0.4 deferred this — `docker run --network none` and Docker port publishing conflict. The CLI sandbox **must** be network-isolated to safely run untrusted commands, but the runner needs to reach `/verify` over HTTP. Three options:

1. **Unix domain socket** instead of TCP — `--network none` + bind-mount a `/tmp/cli-sandbox.sock`; SandboxClient connects via httpx unix transport. Cleanest separation.
2. **Custom Docker network with host-only routing** — network exists for /verify but commands inside the container can't reach the internet (verified via iptables rules).
3. **Two-process container** — verifier server on host network namespace, command-exec subprocess in a chroot/jail with no network. More complex.

I recommend option 1 (UDS). Document the choice + rationale in `docs/SANDBOX_PROTOCOL.md`.

**Files to touch:**

1. `vendor/CLI-40/lib/` — read scenario expected-output specs.
2. `tools/build-packs.js` — when generating `cli-40.jsonl`, embed each scenario's expected stdout/stderr/exit_code into `raw_scenario.expected`.
3. `sandboxes/cli/Dockerfile` — add unprivileged `verifier` user; ensure tools list (bash, coreutils, jq, grep, sed, awk) covers all 40 scenarios.
4. `sandboxes/cli/server.py` — replace `_verify()` with real exec:
   - Extract command from `response.choices[0].message.content` (existing `_extract_command` is fine)
   - `subprocess.run(shlex.split(cmd), shell=False, timeout=scenario.timeout_s or 10, capture_output=True, cwd=workspace_tmpdir)`
   - Workspace tmpdir cleared between scenarios (`shutil.rmtree` + recreate)
   - Truncate stdout/stderr to 64 KB
   - Compare against `raw_scenario.expected` with normalization (trim trailing newline, optional `--ignore-whitespace` flag per scenario)
5. `sandboxes/cli/fixtures/<scenario_id>/` — input files some scenarios reference (CSVs, JSON, etc.) — lift from upstream.
6. `benchlocal_cli/sandbox.py` — if Option 1 (UDS): swap httpx transport for unix socket on the cli sandbox. SandboxClient should detect `network_isolated=True` and route accordingly. Existing `SandboxConfig.network_isolated` flag is already wired but unused.
7. `sandboxes/cli/test_server.py` — pass/fail tests covering happy path + timeout + safety reject.

**Safety model (REQUIRED — implement these gates):**

- Container runs as non-root (`USER verifier` in Dockerfile)
- Network isolation enforced (Option 1: `--network none`; Option 2: iptables rules)
- `subprocess.run(shell=False)` always — never `shell=True`
- Hard timeout via `subprocess.run(timeout=...)`
- Workspace cleared between scenarios

### Phase C — HermesAgent-20 real mocked-tool agent loop (~6-8 hr)

**Goal:** replace shape-check with real multi-turn loop where the sandbox simulates the 5 mocked tools deterministically.

**Files to touch:**

1. `vendor/HermesAgent-20/lib/` — read scenario flow + reference tool-result fixtures.
2. `tools/build-packs.js` — embed scenario flows + tool-result fixtures into `hermesagent-20.jsonl`.
3. `sandboxes/hermes/server.py` — implement real multi-turn lifecycle:
   - `/verify-start`: init scenario state (turn_count=0, memory={}, artifact={}, trace=[]), return first prompt + tool definitions, return scenario_state_id
   - `/verify-turn`: parse model response. If it's a tool call, simulate the tool deterministically (see below), generate the next prompt with tool result, return `action: "next-prompt"`. If it's a final assistant text matching the success criteria, run scenario assertions, return `action: "verify-final"` with pass/fail.
   - `/verify-end`: timeout/giveup case — runner hit the 20-turn limit or model gave up.
4. **Mocked tools** (deterministic, scenario-scoped state):
   - `browser(url)` → keyed JSON fixture lookup. URL → fixture file in `/app/fixtures/<scenario_id>/browser/<url-hash>.json`
   - `cron(when)` → fixed timestamp arithmetic on the scenario's reference clock (set at `/verify-start` time)
   - `memory.{get,set,delete}(key, [value])` → in-process dict per scenario_state_id
   - `artifact.{read,write}(name, [bytes])` → in-process bytes-store per scenario_state_id
   - `trace.append(event)` → append-only event log, checked at end against expected sequence
5. `sandboxes/hermes/fixtures/<scenario_id>/` — browser fixtures + scenario reference data.
6. State storage: existing `STATES: dict[str, dict]` already in place from v0.4 — extend with `memory`, `artifact`, `trace`, `reference_clock`, `scenario_assertions`.
7. `sandboxes/hermes/test_server.py` — multi-turn flow tests with mock model responses.

**Determinism is the key invariant.** No real network calls, no random state. A scenario's tool simulation must be reproducible across runs. Use scenario-derived seeds for any randomness (e.g. trace event IDs).

### Phase D — Documentation + validation (~1-2 hr)

1. Update `docs/SANDBOX_PROTOCOL.md` with the real-verifier semantics. Document the UDS choice (or whichever network-isolation pattern you picked).
2. Update `docs/PACK_FORMAT.md` to describe the new `raw_scenario` fields you added (`code`, `test`, `expected`, `flow`, `tool_fixtures`).
3. Update sandbox `/health` `stage` fields: `"v0.4-shape-check"` → `"v0.6"`.
4. Update server.py module docstrings to reflect real verification.
5. Update `docs/CODEX_REPORT.md` (overwrite with v0.6 status).
6. Run `tools/test-sandboxes.sh` — green.
7. Run mock validation:
   - `--pack bugfind-15` with stub-fix mock → verify all 15 produce real verifier-result shapes (not all pass — only canonical-correct fixes should pass)
   - Same for CLI-40 and HermesAgent-20

**Reconcile the scenario count drift:** `CODEX_BRIEF_V4.md` says `--full` covers 110 scenarios; reality is 150 (5×15 + 15 + 40 + 20). Update v0.4 brief or add a note.

## Constraints

- **Don't change the HTTP wire protocol.** v0.4 → v0.6 is a verifier-implementation refresh; the SandboxClient + runner shouldn't need touching except for the optional UDS path on the CLI sandbox.
- **Backwards compat for the mock-pass marker.** Some tests use `BENCHLOCAL_PASS:scenario_id` to short-circuit verification — keep that path so existing test fixtures still work. Add a runtime warning when a mock marker is detected so it's visible in CI.
- **Sandbox containers stay self-contained.** No external network at runtime (especially for CLI). All fixtures lifted from upstream and baked into the image at build time.
- **Test coverage:** every verifier path needs a test that's reproducible without a running container (mock the HTTP client at SandboxClient layer for runner tests; unit-test the `_verify()` functions directly for sandbox-internal tests).

## Async report-back protocol

Same as v0.4 brief — write `docs/CODEX_REPORT.md` with phase-by-phase status. File `docs/QUESTIONS.md` if you hit a blocker that needs Claude's input on architectural choice (e.g. UDS vs iptables for CLI sandbox network isolation).

## What to ASK rather than guess

- **CLI sandbox network isolation choice** — UDS is my recommendation but I'm not certain it's the cleanest path. If you discover a problem with httpx unix transport at runtime, propose the alternative before implementing the workaround.
- **Hermes scenario flow expressiveness** — if upstream scenarios have flow patterns the v0.4 single-turn shim couldn't capture and you're not sure how to model them (state machines vs hard-coded reference flows), file a question.
- **BugFind candidate-extraction heuristics** — if a scenario expects a structured `<solution>` block but the model emits raw code, decide: fail with `wrong_answer` (strict) or fall back to fenced-code-extraction (lenient)? Document the choice.

## Estimated total effort

- Phase A (BugFind real pytest): 3-5 hr
- Phase B (CLI real exec + UDS): 3-5 hr
- Phase C (Hermes mocked-tool loop): 6-8 hr
- Phase D (docs + validation): 1-2 hr

**Total: ~14-20 hr.** If you exceed estimate on any phase, file a checkpoint in `docs/CODEX_REPORT.md` rather than rushing — Phase B's network-isolation problem could unexpectedly burn time on either UDS-detail or iptables-detail.

## When done

Acceptance gate:
1. `tools/build-sandboxes.sh` builds all 3 images cleanly
2. `tools/test-sandboxes.sh` reports all 3 healthy
3. `tests/` pytest passes
4. Mock-validation run (`--full --enable-sandboxed-packs` against a stub mock-responses fixture that includes a mix of correct + buggy candidates) produces realistic pass/fail distributions — not 150/150 trivial passes
5. `/health` reports `stage="v0.6"` on all 3 sandboxes
6. `docs/CODEX_REPORT.md` overwritten with v0.6 status, gaps explicitly listed if any

Then bump `pyproject.toml` and `__version__` to `0.6.0`, write a one-line CHANGELOG entry, and tag — the release-notes workflow at `.github/workflows/release.yml` handles the GitHub release automatically.

---

**Cross-reference:**
- v0.4 brief (sandbox infrastructure scaffolding): [`CODEX_BRIEF_V4.md`](CODEX_BRIEF_V4.md)
- v0.5 ship (UX patch): commit `eb7ddb0`
- Upstream verifier strictness issue (separate from v0.6): [stevibe/ReasonMath-15#2](https://github.com/stevibe/ReasonMath-15/issues/2) — value-centric matching for the in-process ReasonMath verifier; out of scope for v0.6 but worth tracking
