# Codex implementation brief — benchlocal-cli v0.7 (close the fixture-gap; bench-quality public release)

## Context

v0.6 shipped real verifier infrastructure (subprocess exec for CLI, multi-turn protocol for Hermes, structural+rubric checks for BugFind). But real-model A/B against Qwen3.6-27B and Gemma 4 31B revealed the verifiers are bottlenecked by the **vendor mirror lacking upstream fixture trees**. The numbers are honest about this, but they're not yet bench-quality:

| Pack | v0.6 score (Qwen3.6-27B) | Bottleneck |
|---|---:|---|
| BugFind-15 | 12/15 (80%) | No pytest fixtures (`buggy.py` + `test_fix.py`) — verifier uses solution-block + rubric heuristics from `lib/benchmark.ts` |
| CLI-40 | 5/40 (12%) | Workspace input files missing (`/workspace/access.log`, etc.); Python/Perl/Ruby in FORBIDDEN list rejects most natural responses |
| HermesAgent-20 | 6/20 (30%) | No browser/cron fixtures; multi-turn loop works but tool-result simulation is heuristic-only |

CLI's 12% is the most distorted score — ~30 of the 35 fails are "command exited non-zero" because the input files the model's command references don't exist in the temp workspace.

**v0.7's mission: close the fixture-gap so the scores measure real model behavior, not local mirror state. Public release of the repo is gated on v0.7 passing real-model A/B.**

## Why a single brief

Three packs, one shared problem (fixtures). The fix in each is structurally similar (lift fixtures from upstream → bake into sandbox image → wire verifier to use them). Doing them in one round means one cycle of build-sandboxes + test-sandboxes + cross-rig validation.

## Starting state — what already works

You start from `master` HEAD (commit `c5e1dbd` as of brief draft):

- v0.6 sandbox infrastructure intact: HTTP protocol, container lifecycle, runner dispatch, signal-clean cleanup
- v0.6.1 patches landed (commit `c5e1dbd`): exception handling in `do_POST` for all 3 sandboxes; CLI uses `bash -c` for compound commands; multi-line code-block extraction; FileNotFoundError handled gracefully
- Build/test scripts work: `tools/build-sandboxes.sh` + `tools/test-sandboxes.sh`
- 17/17 pytest pass; ruff clean
- `vendor/{BugFind-15,CLI-40,HermesAgent-20}/` mirrors upstream **without** fixture trees (this is the gap we're closing)
- Pack JSONLs are regenerated via `tools/build-packs.js` — need to be re-regenerated after fixtures are lifted into vendor

### What changed since v0.6 (don't undo)

Same v0.5/v0.6 deltas as before (mode taxonomy, `--full` defaults sandboxed, URL norm, `/health` stage labels at `"v0.6"`, ReasonMath prompt patch, etc.). New v0.6.1 deltas to preserve:

- `sandboxes/cli/server.py`: `_needs_shell()` + `_is_safe_shell()` route compound commands through `bash -c`. `_extract_command()` returns full fenced-block content (was first-line-only). FileNotFoundError + PermissionError caught in `_run_command` → return clean exit_code 127/126.
- `sandboxes/{bugfind,cli,hermes}/server.py`: `do_POST` wraps verifier calls in try/except → returns `server_error` failure_mode with traceback instead of disconnecting.

## Phases

### Phase A — Vendor fixture lift (~3-5 hr)

**Goal:** get the upstream fixture trees into `vendor/{BugFind-15,CLI-40,HermesAgent-20}/` so the sandboxes can use them.

Three possible sources (try in order; document which worked):

1. **Upstream repo's `lib/scenarios/` or equivalent** — check if the upstream GitHub repos have a directory hierarchy with per-scenario fixture files. If yes, `tools/sync-vendor.sh` may need to be relaxed (it currently filters too aggressively). Look at the actual TS scenario loaders to find where they read fixtures from.
2. **Upstream npm package contents** — the published npm tarballs may include fixtures even if the GitHub repo doesn't expose them at the top level. `npm pack` followed by extracting the tarball should reveal the full tree.
3. **Upstream's CI test-data** — sometimes fixtures live in a separate `test-data/` branch or repo. Check if there's a sibling repo or release-asset.

Files to touch:
- `tools/sync-vendor.sh` — may need to widen the path filter or pull additional dirs.
- Document in `docs/VENDOR_SYNC.md` (new file) which source you used per pack and how to re-sync when upstream updates.

If a source IS available, lift fixtures into:
- `vendor/BugFind-15/scenarios/<id>/{buggy.py,test_fix.py,reference_fix.py}`
- `vendor/CLI-40/scenarios/<id>/{workspace/,expected.json}` (workspace = pre-populated input files; expected.json = stdout/stderr/exit_code)
- `vendor/HermesAgent-20/scenarios/<id>/{flow.json,browser/,cron-clock.json,trace-expected.json}`

If no source is available, file a question (`docs/QUESTIONS.md`) describing what you tried — that's a real blocker that needs Claude+user input on whether to (a) reach out to upstream maintainer, (b) accept the gap as documented, or (c) lift fixtures manually for a subset of scenarios.

### Phase B — BugFind real pytest (~2-3 hr, gated on Phase A)

**Goal:** replace v0.6's solution-block + rubric heuristics with real pytest execution.

Files to touch:
- `tools/build-packs.js` — embed buggy code + test code into `raw_scenario.code` and `raw_scenario.test` from the lifted fixtures.
- `sandboxes/bugfind/Dockerfile` — confirm pytest + pytest-timeout are installed (they are in v0.6).
- `sandboxes/bugfind/server.py` — replace `_verify` body:
  - Extract candidate fix from response (existing `<solution>` + fenced-code heuristics work fine)
  - Apply candidate fix to a tmp copy of `buggy.py` in `tempfile.TemporaryDirectory()`
  - Run `pytest test_fix.py --timeout=10 -q` from that tmpdir
  - Pass if exit_code 0; fail with stdout+stderr in trace
- `sandboxes/bugfind/fixtures/<scenario_id>/` — populated from vendor at build time (COPY into Docker layer)
- `sandboxes/bugfind/test_server.py` — extend to mock candidate fixes + verify pass/fail discrimination on a few representative scenarios

Failure-mode mapping:
- `wrong_answer` — model didn't emit a candidate fix at all (no extractable code)
- `verifier_fail` — pytest red (the test_fix.py case detected the candidate fix is wrong)
- `timeout` — pytest hit 10s
- `server_error` — fixture missing, container OOM, etc

Keep the v0.6 mock-pass marker (`BENCHLOCAL_PASS:scenario_id`) for backwards compat with existing tests.

### Phase C — CLI real fixture-driven verification (~3-5 hr, gated on Phase A)

**Goal:** replace v0.6's run-and-check-exit with real workspace-input + expected-output comparison.

Files to touch:
- `sandboxes/cli/Dockerfile` — expand tooling. Confirmed missing in v0.6: `git`, `find` (likely there via coreutils, verify), `awk` (gawk is there), possibly `tree` or other utilities scenarios reference. Also: relax FORBIDDEN list — review whether `python3`, `perl`, `ruby` should be allowed for scenarios that legitimately need scripting languages. (Upstream design decision: check upstream methodology doc to see if shell-only is intended.)
- `tools/build-packs.js` — embed each scenario's expected stdout/stderr/exit_code into `raw_scenario.expected` from `expected.json`.
- `sandboxes/cli/server.py`:
  - Remove the placeholder `_seed_workspace` README + scenario.json — replace with actual fixture copy:
    `shutil.copytree(f"/app/fixtures/{scenario_id}/workspace", workspace, dirs_exist_ok=True)` for scenarios that have a workspace dir
  - Update `_expected_compare` — already exists, just ensure it's exercised when `raw_scenario.expected` is populated
  - Consider per-scenario PATH override if some scenarios need specific tools others shouldn't have access to (probably unnecessary for v0.7)
- `sandboxes/cli/fixtures/<scenario_id>/{workspace/,expected.json}` — lifted from vendor
- `sandboxes/cli/test_server.py` — mock model commands, verify exec + diff against expected

**FORBIDDEN list reconsideration:** check upstream `vendor/CLI-40/lib/benchmark.ts` or `methodology.md` to see if scenarios are shell-only by design or allow scripting. If they allow scripting, drop `python`, `python3`, `perl`, `ruby` from FORBIDDEN — keep the network/destructive bans (`rm`, `curl`, etc.). Document the choice in the server.py module docstring.

**Network isolation:** v0.6 left `--network none` deferred (Docker port-publishing conflict). For v0.7, resolve this OR document that subprocess.run still doesn't actually have network access because of the verifier-level pre-checks (curl/wget/ssh forbidden) PLUS per-process firewall rules added at sandbox boot. Pick one path; document it.

### Phase D — Hermes real fixture-driven multi-turn (~5-7 hr, gated on Phase A)

**Goal:** replace v0.6's heuristic single-turn-friendly verifier with the real multi-turn agent loop using lifted fixtures.

Files to touch:
- `tools/build-packs.js` — embed each scenario's `flow.json` (sequence of expected interactions), `cron-clock.json` (reference timestamps), and `browser/` fixture catalog (URL → response JSON mapping) into `raw_scenario`.
- `sandboxes/hermes/server.py` — major rewrite of `/verify-turn`:
  - State machine per scenario_state_id, driven by `flow.json`
  - Mocked tool implementations:
    - `browser(url)` → look up `/app/fixtures/<scenario_id>/browser/<sha256(url)>.json`; if missing, return 404-shape result
    - `cron(when)` → arithmetic on `reference_clock` from fixture
    - `memory.{set,get,delete}(key, [value])` → already deterministic; keep
    - `artifact.{read,write}(name, [bytes])` → already deterministic; keep
    - `trace.append(event)` → already deterministic; keep
  - At end of flow, run `verify_final` against expected trace from fixture (compare event sequence + final state)
- `sandboxes/hermes/fixtures/<scenario_id>/{flow.json,cron-clock.json,browser/}` — lifted from vendor
- `sandboxes/hermes/test_server.py` — mock model multi-turn responses, verify state machine progresses + final assertions fire

Backwards-compat: keep `/verify` (single-turn) endpoint working for any tests that hit it directly with mock-pass markers.

### Phase E — Tooling, docs, validation (~2 hr)

1. **Update Dockerfiles:**
   - BugFind: confirm pytest + pytest-timeout (already present)
   - CLI: add tools per fixture analysis (likely `git`, `tree`, maybe `python3` if FORBIDDEN list relaxes); strip placeholder workspace seed
   - Hermes: add no new system tools; extend Python deps if needed
2. **Refresh `/health` stage labels** to `"v0.7"` on all 3 sandboxes.
3. **Update server.py module docstrings** to describe fixture-driven verification accurately (drop "no fixture tree" caveats from v0.6).
4. **Update `docs/CODEX_REPORT.md`** — overwrite with v0.7 status.
5. **Update `docs/PACK_FORMAT.md`** with new `raw_scenario` fields.
6. **Update `ROADMAP.md`** — move v0.7 → "shipped"; promote any deferred items.
7. **Run mock validation** (`/tmp/benchlocal-v07-mock.json` mixed pass/fail responses):
   - bugfind-15: target 5-10/15 with mock fixes (some correct, some wrong)
   - cli-40: target 15-25/40 with mock commands (some correct, some wrong)
   - hermesagent-20: target 5-12/20 with mock agent traces (some correct, some wrong)
   - These are sanity targets — actual real-model scores will differ
8. **Bump version** `pyproject.toml` + `__init__.py` → `0.7.0`.
9. **CHANGELOG entry** with the score-impact + fixture lift summary.

## Constraints

- **Don't break HTTP wire protocol.** SandboxClient and runner shouldn't need code changes (only sandbox containers + pack JSONLs change).
- **Backwards compat for mock-pass marker.** Many tests still use `BENCHLOCAL_PASS:scenario_id` — keep the short-circuit path with a stderr WARNING when used, so it's visible in CI.
- **Test coverage.** Every verifier path (real fixture pass, real fixture fail, missing fixture, timeout, server_error) needs a test reproducible without a running container.
- **Sandboxes stay self-contained.** All fixtures baked into images at build time; no runtime network access for the sandboxes.
- **Honest scoring.** If a scenario's fixture lift fails (e.g., upstream doesn't have the file), explicitly mark `raw_scenario.fixture_status: "missing"` and have the verifier emit `failure_mode: "fixture_missing"` rather than silently passing or trivially failing. Real-model scores must reflect what the model can actually do, not local mirror state.

## Async report-back protocol

Same as v0.4/v0.6: write `docs/CODEX_REPORT.md` with phase-by-phase status. File `docs/QUESTIONS.md` if you hit a blocker (especially in Phase A — fixture sourcing is the most uncertain phase).

## What to ASK rather than guess

- **Phase A fixture source.** If the obvious sources don't have fixtures, file a question with what you tried before pursuing alternatives. The user may have access patterns Claude doesn't.
- **CLI FORBIDDEN list.** If upstream methodology is explicit about shell-only scope, keep python/perl/ruby banned. If it's ambiguous or pro-scripting, drop them. Cite the upstream doc you used to decide.
- **Hermes flow.json schema.** If the lifted fixtures use a different schema than `CODEX_BRIEF_V6.md` Phase C imagined, document the actual schema in `docs/PACK_FORMAT.md` and adapt the state machine.

## Estimated total effort

- Phase A (vendor fixture lift): 3-5 hr (most uncertain — could be quick if a clear source exists, or could blow up if it requires manual lift)
- Phase B (BugFind pytest): 2-3 hr
- Phase C (CLI fixture-driven): 3-5 hr
- Phase D (Hermes flow-driven): 5-7 hr
- Phase E (docs + validation): 2 hr

**Total: ~15-22 hr.** Phase A's outcome controls the timeline of B/C/D — if fixtures aren't reachable at all, those phases regress to "best-effort heuristics" rather than real parity.

## When done

Acceptance gate (must all pass before flipping the repo public):

1. `tools/build-sandboxes.sh` builds all 3 cleanly with new fixtures
2. `tools/test-sandboxes.sh` reports all 3 healthy with `stage="v0.7"`
3. `tests/` pytest passes (target: 20+ tests)
4. **Real-model A/B baseline** on Qwen3.6-27B + Gemma 4 31B (not just mock):
   - cli-40: ≥40% (vs v0.6's 12% — fixture lift removes the floor)
   - bugfind-15: stable in 60-85% range (real pytest may move score either way)
   - hermesagent-20: stable in 30-60% range (real flow checks should be more discriminating)
   - Rule: a 30B model should score *meaningfully different from 0% and from 100%* on each pack — that's the discrimination signal we need
5. `docs/CODEX_REPORT.md` overwritten with v0.7 status, any remaining gaps explicitly listed
6. Version bumped, CHANGELOG entry, commit clean

After acceptance gate passes:
- Tag `v0.7.0` (release-notes workflow handles GitHub release)
- Run `--full` on Qwen + Gemma → update `noonghunna/club-3090` compose Quality lines with v0.7 numbers
- Repo flips public. README updated with `pip install` instructions referencing the public URL.

---

**Cross-reference:**
- v0.4 brief: [`CODEX_BRIEF_V4.md`](CODEX_BRIEF_V4.md) — sandbox infrastructure scaffolding
- v0.6 brief: [`CODEX_BRIEF_V6.md`](CODEX_BRIEF_V6.md) — first verifier-implementation pass
- v0.6 report: [`docs/CODEX_REPORT.md`](docs/CODEX_REPORT.md) — fixture-gap caveat
- v0.6.1 patches: commit `c5e1dbd` — exception handling + bash-c routing
- Upstream verifier strictness (out of scope): [stevibe/ReasonMath-15#2](https://github.com/stevibe/ReasonMath-15/issues/2)
- Roadmap: [`ROADMAP.md`](ROADMAP.md)
