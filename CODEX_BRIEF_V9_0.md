# Codex implementation brief — benchlocal-cli v0.9.0 (Aider Polyglot lite, single-scoreboard)

## Context

After v0.8.1 shipped (delta + inspect + history + diff/logs), v0.9.0 starts
the **eval expansion track** — adding new bench surfaces beyond the original
8 packs. Each suite ships independently (no bundle dependency), runs via
`--pack <id> --enable-sandboxed-packs` like our existing sandboxed packs.

This is the **second-pass brief.** First pass proposed a "Variant C synchronous
batch on first verify-start" architecture. Codex review of that brief flagged
30+ findings, several critical:
- `/verify-start` semantically bent into a hidden batch runner
- Cache key only by exercise name was unsafe (no model / endpoint / commit isolation)
- "After last scenario, clear cache" assumed ordered traversal — broken under
  retries, parallelism, `--scenario` filter
- Per-scenario latency = "batch / 30" was false data, not just docs

Rather than re-engineer 30+ concerns, this redraft **changes the architecture**:
**single-scoreboard pack.** One scenario, one HTTP call, one wall-clock latency,
no cache. Per-exercise detail lives in `verifier_trace.upstream_per_exercise`.

This sidesteps ~70% of Codex's first-pass concerns by design.

## Why single-scoreboard

Aider's `benchmark.py` is intrinsically a batch runner. It clones exercises,
runs them in a fixed order (or parallel via `--threads`), edits files, executes
tests, retries on failure, and emits a yaml summary. The 30 exercises form one
indivisible execution unit. Trying to fit that into our existing
"1 scenario = 1 verify call" protocol forces per-scenario lies.

The **single-scoreboard pack** matches the upstream's natural shape:
- Pack `aider-polyglot-30` has **1 scenario**: `id = "aider-polyglot-30-batch"`
- One `/verify-start` call → spawn `benchmark.py` once → wait → grade aggregate
- Pass criterion: `pass_rate >= 0.5` (configurable via env). Below threshold
  is a real fail; the per-exercise breakdown lives in `verifier_trace` so
  `inspect` can show exactly which 4/30 failed.
- Latency is the real wall clock for the batch (no averaging fiction)
- No cache, no single-flight, no ordered-traversal assumption — every concern
  about parallelism / filtering / retry semantics is moot when there's 1
  scenario per pack

## Codex second-pass review findings (folded in 2026-05-09 PM)

Codex reviewed this redraft after it superseded the first-pass brief. Verdict:
*"much better architecture than Variant C... the architecture is not delusional...
would proceed with this direction"*. But flagged 5 specific tightenings, all
folded into the phases below:

1. **Previous-result semantics need defining precisely** (Codex review #2).
   Top-level scenario stays "passed" at 23/30 AND 20/30 if both above
   threshold, so naive delta would mark this as "stable" while actual
   capability regressed. Fix: promote `passed_count`, `total_count`,
   `pass_rate` to **first-class displayed metrics in the markdown output
   and saved JSON**, not trace-only data. v0.8 `--previous-result` delta
   gains a per-pack secondary metric column for these.

2. **`_resolve_endpoint_for_container()` retroactive hermes fix is risky**
   (Codex review #3). Scope it tightly to the sandbox container's egress
   env (don't silently change hermes runtime behavior in cross-rig setups
   that don't need it). Handle: `localhost`, `127.0.0.1`, `[::1]`, `0.0.0.0`
   (bind syntax, not target — preserve as error), already-container
   hostnames (passthrough), URLs with paths/ports. On Linux, ensure
   `docker run --add-host=host.docker.internal:host-gateway`. Make the
   rewrite **opt-in via env** for hermes (preserve existing behavior by
   default) and **opt-out via env** for aider-polyglot (default-on since
   it's a fresh pack).

3. **Acceptance gate `pass_rate >= 1/30` is too lax** (Codex review #4).
   Split into:
   - **Harness smoke** (Phase E test, mock model): `num_tests=2`,
     deterministic expected outcomes, ~2 min. Validates the pipe.
   - **Full-completion check**: all 30 exercise result files discovered;
     exact-id list matches `exercises.json` (not just count).
   - **Threshold-fixture tests**: synthetic results at 0/30, 1/30, 15/30,
     30/30 → verify pass/fail boundary at threshold default 0.5.
   - **Live plumbing smoke** (renamed from "calibration"): real model,
     completes <25 min, produces 30 parsed outcomes, `pass_rate >= 1/30`.
     This is **plumbing validation, not capability calibration**.

4. **Upstream CLI drift needs an adapter boundary** (Codex review #5).
   Centralize all `benchmark.py` arg construction in one
   `_build_benchmark_args()` function (pure, testable). Add a
   container-level contract test that runs `benchmark.py --help` and
   greps for the flags we depend on (`--num-tests`, `--keywords`,
   `--model`, `--edit-format`, `--exercises-dir`, `--read-model-settings`).
   If any required flag is missing, fail loud at /health (not at first
   /verify-start). Surface the detected CLI signature in `/health` so
   pin-bump regressions are visible.

5. **Exact-id resolution**, not count-only (Codex review #1+#5). Startup
   asserts that the resolved exercise list **equals** `exercises.json`,
   not just `len() == 30`. Two exercises with substring-colliding
   `--keywords` patterns could yield 30 exercises that aren't the 30 we
   curated. Compare resolved set vs canonical set; fail loud on mismatch.

These additions don't change the architecture. They tighten the
implementation around it.

## Trade-off being made

**What we lose:** per-exercise pass/fail in the saved JSON's top-level
`scenarios[]` list. v0.8 `--previous-result` delta only sees ONE
"aider-polyglot-30-batch" verdict per run, not 30. To know "did `cpp/anagram`
specifically regress between two runs" you have to drill into
`verifier_trace.upstream_per_exercise` rather than reading the top-level
delta column.

**What we gain:** all 30+ Codex findings on the per-scenario batch architecture
become moot. Implementation budget drops from 12-18 hr to ~7-9 hr (the original
estimate). Saved-JSON shape is a proper fit for `inspect`'s existing rendering.
We don't need a `/verify-batch` HTTP protocol or single-flight cache lock.

**Why this is acceptable:** the v0.8 delta column already shows pack-level
counts, not always per-scenario detail. For Aider specifically, "the model
went from 23/30 → 20/30 on aider-polyglot" is the headline; "specifically
`go/leap` flipped" is `inspect --scenario aider-polyglot-30-batch` territory.

## Architecture

```
runner POST /verify-start (port 9000, our shape)
  → sandboxes/aider-polyglot/server.py
    → spawn `python /opt/aider-bench/benchmark.py
        --num-tests 30
        --keywords "<pinned-exact-list-pattern>"
        --model <model_name>
        --edit-format whole
        --threads <NUM>
        --exercises-dir polyglot-benchmark
        --no-unit-tests-time-limit-multiplier 1
        --new`
        with OPENAI_BASE_URL=<runner endpoint, host-resolved>
        +  OPENAI_API_KEY=<runner-supplied; default a non-empty placeholder>
      → wait for completion (single 1500s subprocess timeout)
      → walk tmp.benchmarks/<run>/<exercise>/.aider.results.json (per upstream output)
      → compute pass_rate, per-exercise summary
    → return ONE verify-final response
  → runner consumes ScenarioResult (1 per pack)
```

**No cache. No long-lived subprocess. No batch-scenario fan-out.** Single
HTTP call → single subprocess → single response.

## Phases

### Phase A — Vendor + pack metadata + curated 30-id list (~2 hr)

Same structure as the first-pass brief, with corrections from Codex review:
- `vendor/AiderPolyglot-30/` — pin upstream `Aider-AI/aider` benchmark/
  + `Aider-AI/polyglot-benchmark` exercises at specific commits
- `vendor/AiderPolyglot-30/exercises.json` — **exact** 30-id list,
  documented selection criteria. Each entry: `{name, language, difficulty}`.
  Hand-pick 5 from each language, mixing easy/medium/hard.
- **Codex review #5 fix**: keyword matching can substring-match. Document
  whether `--keywords` is exact or substring; if substring, our pack startup
  must enumerate exact matches and assert count == 30 before proceeding.
  If a pinned exercise gets renamed upstream, fail loud with a re-sync
  recipe rather than silently skip.
- `benchlocal_cli/packs/aider-polyglot-30.jsonl`:
  ```jsonl
  {"__meta__": true, "pack_id": "aider-polyglot-30", "version": "1.0.0",
   "upstream_repo": "Aider-AI/aider+polyglot-benchmark", ...,
   "scenario_count": 1, "supports_sandboxed_only": true,
   "default_max_seconds": 1500, "verifier_module": "_stub"}
  {"id": "aider-polyglot-30-batch",
   "description": "30-exercise lite slice across C++/Go/Java/JS/Python/Rust …",
   "messages": [{"role": "user", "content": "<runner-side placeholder; aider drives>"}],
   "verifier": {"type": "_stub"},
   "raw_scenario": {"kind": "aider-polyglot-batch", "exercises": [<30 entries>]}}
  ```

### Phase B — Sandbox container (~3 hr, was 3-4)

`sandboxes/aider-polyglot/Dockerfile`:
- Base: **start FROM upstream's `aider/benchmark/Dockerfile`** unmodified
  (Codex review concern #10: aider expects its own Docker; respect it).
  Install our Python proxy + `httpx` on top. Don't try to recreate aider's
  per-language toolchain bake from scratch — reuse upstream's known-good
  recipe.
- Specifically: bake the pinned `aider-polyglot-benchmark` exercises clone
  AND the aider source clone at `pip install -e`, both at our pinned commits.
  This matches upstream's documented setup flow.
- Image size: ~1.5-2 GB final (Python + 6 language toolchains).
  **Codex review concern #12**: brief now explicitly warns about disk:
  recommend `docker system prune -a -f --volumes` before build.

`sandboxes/aider-polyglot/entrypoint.sh`:
- Trivial — start our Python proxy on :9000.
- No upstream HTTP server to coordinate (unlike hermes v0.7.4).

`sandboxes/aider-polyglot/server.py`:
- `/health`: report `status`, `stage="v0.9.0"`, `aider_commit`,
  `polyglot_commit`, exact-match-checked `exercise_count` (verified at boot
  via `--keywords` enumeration), `aider_python_version`.
- `/verify-start`: **single batch invocation**. No cache, no state.
  - Honor `BENCHLOCAL_PASS:<scenario_id>` mock-pass marker (preserved from
    earlier packs).
  - Spawn `benchmark.py` as subprocess with explicit env:
    - `OPENAI_BASE_URL=<resolved endpoint>` (Codex review #4: brief
      now uses `OPENAI_BASE_URL` not `OPENAI_API_BASE`. Both are honored
      by litellm but `OPENAI_BASE_URL` is the modern canonical name; ALSO
      set `OPENAI_API_BASE` to the same value for older code paths.
      Implementation MUST verify against pinned aider's litellm version
      during a Phase E contract test before tagging.)
    - `OPENAI_API_KEY=<runner-supplied>` (Codex review #6: don't pass
      literal `"dummy"` — accept whatever the runner provides; default
      to a long random string the runner generates so any "must be
      non-empty" or "must look like sk-..." check upstream still passes).
    - `AIDER_NO_PRETTY=1`, `AIDER_NO_AUTO_COMMITS=1` for stable output.
  - Wait with `subprocess.Popen + start_new_session=True + os.killpg(SIGKILL)`
    on timeout (same hardening as v0.7.3 hermes server.py).
  - On completion: walk `tmp.benchmarks/<run-id>/` and read each per-exercise
    `.aider.results.json`. Codex review #2 + #6: per-exercise output is
    indeed JSON (the `.aider.results.json` files), not yaml. The yaml is
    just the optional `--stats` summary. Read the JSON files directly.
  - Compute `pass_rate`. Return `{action: "verify-final", passed:
    pass_rate >= threshold, failure_mode: ..., detail: "23/30 (77%)",
    trace: {..., upstream_per_exercise: {<name>: {passed, duration, cost,
    test_outcomes}, ...}, batch_wall_clock_s, exact_match_count, ...}}`
  - **Codex review #7 fix**: cache key is moot — there's no cache.
- `/verify`, `/verify-turn`, `/verify-end`: no-op return-final stubs for
  back-compat with the runner's protocol assumptions, same as v0.7.4 hermes.

### Phase C — Runner-side passthrough (~1 hr, was 30 min)

Slightly bigger than initial estimate because Codex second-pass flagged
two material additions beyond the timeout bump:

- Bump `SandboxConfig.request_timeout_s` for `aider-polyglot-30` to **1800**
  (30 min) — accommodates the entire batch wall-clock plus headroom.

- **Codex 2nd-pass #1: promote pass_rate to first-class metrics.** Extend
  `ScenarioResult` (or `ScenarioRun`) with optional fields:
  ```python
  passed_count: int | None = None    # 23
  total_count: int | None = None     # 30
  pass_rate: float | None = None     # 0.7666
  ```
  All optional, default None — no impact on existing v0.8.x packs (which
  don't set these). For aider-polyglot-30, server.py populates them in
  the verify-final response and the runner promotes them onto the
  `ScenarioResult`. v0.8's `--previous-result` delta module learns to
  read them: per-pack delta gains a `pass_rate_delta` field
  (`current.pass_rate - previous.pass_rate`) when both runs have it.
  Markdown output for delta column on packs that have these fields shows
  e.g. `23/30 (77%) → 20/30 (67%)  ⚠ -10pp`. Without these, falls back
  to the existing v0.8 verdict-flip column.

- **Codex 2nd-pass #2: scope `_resolve_endpoint_for_container()` correctly.**
  - Implementation in `benchlocal_cli/sandbox.py`. Pure function: takes
    URL string, returns URL string. Handles: `localhost`, `127.0.0.1`,
    `[::1]`, `0.0.0.0` (raise — bind-only, not a target), URLs with
    paths/ports/queries (preserve them), already-container hostnames
    (passthrough).
  - Apply by default to **aider-polyglot-30 only** (fresh pack, no
    back-compat concern). For hermes pack, behavior unchanged unless
    user opts in via `BENCHLOCAL_HERMES_RESOLVE_LOCALHOST=1` env. This
    avoids surprising existing hermes deployments that already work via
    bind-mount or service-name resolution.
  - On Linux, `SandboxClient.start()` adds
    `--add-host=host.docker.internal:host-gateway` to `docker run` for
    aider-polyglot. (Already present on Docker Desktop / macOS; needs
    explicit flag on Linux.)
  - Tests for all input shapes (loopback v4, loopback v6, bind, normal
    host, IP, with-path).

### Phase D — Mode taxonomy: NONE in v0.9.0 (~0 hr)

**Codex review #4 + brief redesign**: original first-pass brief added an
`--audit` mode that bundled aider with future lm-eval/BFCL slots. Per the
"each suite ships independently" decision, **v0.9.0 adds NO mode flags.**
Users compose:
```
benchlocal-cli run --pack aider-polyglot-30 --enable-sandboxed-packs ...
```
The `--audit` bundle is deferred to a later "stitching" version (likely
v0.9.99 or v1.0) once 0.9.0/0.9.1/0.9.2 have all shipped and the actual
combination semantics are clear.

This decision drops one whole phase from the brief — saves ~30 min of
brief-time scope and avoids backwards-compat traps.

### Phase E — Tests + docs + version bump + acceptance gate (~2 hr, was 1)

Tests:
- Unit-test `_grade_aider_batch_result()` against fixture JSON files (a
  synthetic `tmp.benchmarks/<run>/` tree we commit under
  `tests/fixtures/aider/`)
- Unit-test `_resolve_endpoint_for_container()` for localhost / 127.0.0.1
  / 0.0.0.0 / host names / IPs
- Unit-test exact-match exercise enumeration (Codex review #5): given
  upstream renaming a pinned exercise, fail loud
- Unit-test `OPENAI_BASE_URL` vs `OPENAI_API_BASE` env passthrough
  (the env Popen receives must include BOTH)

**Contract test (Codex review concern, marked `docker`, opt-in)**:
- Boot the v0.9.0 image
- Health check: `/health` reports both upstream commits
- One mock-pass `/verify-start` (uses `BENCHLOCAL_PASS:` marker; doesn't
  spawn `benchmark.py`) — verifies the proxy path works without paying
  the 15-min batch cost
- One LIVE smoke test (separate `pytest -m docker_live`): real model
  endpoint + `--num-tests 2` (only 2 exercises, ~1-2 min). Verifies
  end-to-end aider works AT ALL on this image. Skip in CI; required
  before tagging on a dev rig.

Docs:
- `sandboxes/aider-polyglot/README.md` — architecture, deps, iteration
  recipe (especially: how to update upstream pin without breaking
  exercise enumeration)
- `docs/AIDER_POLYGLOT_30.md` (new) — pack overview, exercise list +
  selection criteria, expected runtime, known limitations
- `docs/SANDBOX_PROTOCOL.md` — note that `aider-polyglot-30` uses single-
  scoreboard architecture (one scenario per pack), differs from v0.7.4
  hermes's per-scenario shape
- `CHANGELOG.md` — v0.9.0 entry
- `pyproject.toml` + `__init__.py` → 0.9.0
- README usage section: add example for `--pack aider-polyglot-30`

Acceptance gate (Codex 2nd-pass #4 — split into harness smoke / completion
/ threshold / live, instead of one vague bar):

1. **Build clean**: `tools/build-sandboxes.sh aider-polyglot` succeeds;
   image size ≤ 2.5 GB.

2. **/health robust**: reports `stage="v0.9.0"`, `exercise_count=30`
   (exact set match against `exercises.json`, not just count), both
   upstream commits, detected CLI signature (output of `benchmark.py
   --help` flag-grep). Required flags present: `--num-tests`, `--keywords`,
   `--model`, `--edit-format`, `--exercises-dir`, `--read-model-settings`.

3. **pytest**: target **92+ passing** (was 81; adds ~11 unit tests):
   `_build_benchmark_args` (3), `_grade_aider_batch_result` happy +
   2 sad (3), `_resolve_endpoint_for_container` 6 input shapes (6),
   threshold-fixture pass/fail at 0/30, 1/30, 15/30, 30/30 (1).

4. **Harness smoke** (Phase E test, mock model, deterministic):
   `num_tests=2` against a tiny mock OpenAI server. Validates the pipe
   start-to-finish without paying for a real model call. ~2 min.

5. **Full-completion check** (Phase E live test, opt-in `pytest -m
   docker_live`): real Qwen endpoint, all 30 exercises run, ALL 30
   per-exercise result files discovered, exact-id list matches
   `exercises.json`. Does NOT assert any specific pass rate — that's
   model-quality territory, not harness validity.

6. **Live plumbing smoke**: `--pack aider-polyglot-30 --endpoint <Qwen>`
   on dev rig completes in <25 min, returns `pass_rate >= 1/30`. This
   is **plumbing validation, not capability calibration**. Capability
   calibration (e.g., "what should Qwen3.6-27B score here?") is a
   follow-up bench, not a v0.9.0 gate.

7. **v0.8 inspect surface check**: `inspect --scenario aider-polyglot-30-batch`
   renders the per-exercise table from `verifier_trace.upstream_per_exercise`
   cleanly. Surfaces pass/fail per exercise + language + duration.

8. **v0.8 delta surface check**: `--previous-result` between two
   aider-polyglot-30 runs renders the new `pass_rate_delta` column
   (e.g., `23/30 (77%) → 20/30 (67%)  ⚠ -10pp`). Existing pack delta
   semantics unchanged.

9. **Back-compat**: existing v0.8.1 saved JSONs load cleanly with the new
   `inspect`/`run --previous-result`; no schema-version break on
   non-aider-polyglot packs.

## Constraints (revised from first-pass)

- **Don't break v0.8.x.** Single-scoreboard pack uses existing /verify-start
  with verify-final early-out — same shape as v0.7.4 hermes from the
  runner's perspective.
- **Don't bend `/verify-start` into a batch protocol.** This was the
  central first-pass mistake. There's 1 scenario per pack; 1 call; 1 result.
- **Save real per-exercise data in trace.** `verifier_trace.upstream_per_exercise`
  is the source-of-truth for "which exercises failed". `inspect` should
  surface this prominently for this pack.
- **Don't fake latency.** `result.latency_seconds` = real wall clock for
  the batch. No "batch / 30" averaging. Codex review #9 / #11 fix.
- **Image size +1.5-2 GB.** Disk preflight: brief recommends running
  `docker system prune -a -f --volumes` before this build. Document the
  recommendation in the build script's README.
- **Pin BOTH upstream repos.** aider commit AND polyglot-benchmark commit.
  Both into the manifest. Both surfaced in `/health` and trace.

## What CHANGED from the first-pass brief

| First-pass concern | Resolution |
|---|---|
| Variant C: 1st scenario takes 12min, next 29 cached | **DROPPED.** Single-scoreboard pack: 1 scenario per pack, 1 call, no cache. |
| Cache key only by exercise name | **DROPPED.** No cache. |
| "After last scenario, clear cache" | **DROPPED.** No cache. |
| Single-flight lock for parallel /verify-start | **DROPPED.** Multiple /verify-start calls would each start a fresh batch — same as any other sandboxed pack on parallel runs. Existing runner doesn't fan out per-scenario in parallel. |
| Per-scenario latency lie | **DROPPED.** 1 scenario = 1 real wall-clock latency. |
| `--audit` mode in v0.9.0 | **DROPPED to a later version.** v0.9.0 ships independent pack only. |
| `OPENAI_API_BASE` vs `OPENAI_BASE_URL` | **Both set in env.** Phase E contract test verifies. |
| `localhost` not reachable from container | **`_resolve_endpoint_for_container()` helper.** Applied to aider-polyglot AND retroactively to hermes. |
| Exact-match exercise curation | **`exercises.json` with explicit name list; startup asserts count == 30.** Fail loud on rename. |
| `OPENAI_API_KEY=dummy` may be rejected | **Pass through whatever runner provides.** Don't hardcode dummy. |
| Aider expects own Docker | **Acknowledged: brief now starts FROM upstream's benchmark Dockerfile** rather than wrapping it from outside. |
| Disk pressure | **Preflight prune in build instructions.** Same advice as v0.7.4. |
| ~30 minor edge cases (retry, cancel, partial failure) | **Mostly moot under single-scoreboard.** Remaining few covered in Phase E tests. |

## Async report-back protocol

Same as prior versions. Write `docs/CODEX_REPORT.md` with phase-by-phase
status. File `docs/QUESTIONS.md` if Phase B blocks (especially: upstream's
`benchmark.py` argument shape changes between aider commits — verify the
pinned commit's CLI args BEFORE Phase B implementation).

## What to ASK rather than guess

- **Pinned upstream commits**: the brief assumes user is OK with picking
  recent stable commits. If the user has specific commits in mind for
  reproducibility (e.g., to match a published Aider Polyglot leaderboard
  entry), they should provide them.
- **Edit format choice**: brief recommends `whole` for the lite slice
  (broadest model compatibility, simplest per-test grading). User may
  prefer `diff` if they want to test edit-format adherence specifically.
- **Pass threshold**: brief defaults to 0.5 (pass if 15+/30). User may
  want stricter (e.g., 0.7) for a meaningful Qwen vs Gemma A/B.
- **Curated 30 names**: I'll commit my selection but it should be
  reviewable / overrideable via `BENCHLOCAL_AIDER_KEYWORDS_OVERRIDE` env
  for users who want a different slice.

## Estimated total effort

After Codex 2nd-pass tightenings:
- Phase A (vendor + 30-id curation + exact-id contract): 2 hr
- Phase B (Dockerfile from upstream + entrypoint + server.py + `_build_benchmark_args`): 3 hr
- Phase C (runner-side timeout bump + endpoint-resolve helper +
  pass_rate first-class metrics + delta-module update): 1.5 hr (was 30 min)
- Phase D: REMOVED (no `--audit` mode in v0.9.0)
- Phase E (tests + docs + bump + 4-part acceptance gate): 2.5 hr (was 2)

**Total: 9 hr.** Slightly higher than first-pass redraft (7-8) because
2nd-pass surfacing of pass_rate as first-class metric requires touching
the v0.8 delta module. Still substantially under the 12-18 hr bar from
first-pass-with-Variant-C concerns.

## When done

Acceptance gate (crisp + measurable per Codex review):
1. `tools/build-sandboxes.sh aider-polyglot` builds clean (no errors;
   image size ≤ 2.5 GB)
2. `tools/test-sandboxes.sh` reports `stage="v0.9.0"`, both upstream
   commits, exercise_count=30 (exact match passed)
3. `pytest tests/` = 88 passing (was 81 in v0.8.1)
4. Real-model A/B: `--pack aider-polyglot-30 --endpoint <Qwen3.6-27B>`
   completes <25 min wall-clock and returns pass_rate >= 1/30 (any pass
   at all)
5. `inspect` cleanly surfaces `verifier_trace.upstream_per_exercise` —
   `inspect --scenario aider-polyglot-30-batch` shows per-exercise
   pass/fail table
6. `--previous-result` delta: aider-polyglot-30 row reports stable /
   regression / fix correctly when comparing two runs

After acceptance:
- Tag v0.9.0
- Push (per user's auto-mode pattern: ask before push)
- v0.9.1 brief: lm-eval IFEval + GSM8K (next eval slot — per-scenario
  architecture, not single-scoreboard, fits naturally)

---

**Cross-reference:**
- v0.7.4 hermes commit `5322624` — closest existing pattern (upstream
  owns the loop, our Python proxies HTTP)
- v0.8.1 commit `75cd902` — current master baseline; this brief is locked to it
- ROADMAP.md "Optional bench expansion" — the original v0.9+ design notes
- Upstream Aider benchmark: https://github.com/Aider-AI/aider/tree/main/benchmark
- Polyglot exercises: https://github.com/Aider-AI/polyglot-benchmark
- First-pass brief (now superseded): see git blame on this file for the
  original Variant C design + the 30+ Codex findings that drove this redraft
