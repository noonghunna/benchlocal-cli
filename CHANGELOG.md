# Changelog

Auto-generated from commit messages by [git-cliff](https://git-cliff.org/).
Write rich commit message bodies (subject + blank line + multi-paragraph body)
and they'll render below in both this file and the
[GitHub Release pages](https://github.com/noonghunna/benchlocal-cli/releases).

---

## Unreleased


### 🐛 Bug fixes

- **fix(hermes): full localhost resolve for sandbox endpoint + drop drifted persist_session kwarg** ([9c1566f](https://github.com/noonghunna/benchlocal-cli/commit/9c1566f53856d56f9bb46dd7861eb01a5bc3608b))


Two hermes harness bugs surfaced by Qwen 3.6 27B head-to-head run
2026-05-10. Both made hermesagent-20 emit spurious 0/20 / 1/20 scores
on a stack that yesterday graded 10/20 on Gemma.

1. **`BENCHLOCAL_HERMES_RESOLVE_LOCALHOST=1` only added `--add-host`,
   never rewrote the endpoint URL.**
   `sandbox.py:290-294` adds `--add-host=host.docker.internal:host-gateway`
   when the env var is set, but `runner.py:527` was still passing the
   raw `self.endpoint` (`http://localhost:8010`) to the hermes-agent
   inside the container. `localhost` inside a Docker container resolves
   to the container itself, not the host — so the agent emitted
   `"API call failed after 3 retries: Connection error."` for every
   scenario and the grader recorded all-fails. (Aider-polyglot path
   already does this rewrite unconditionally; hermes path stayed opt-in
   to preserve k8s/docker-compose service-name resolution but missed
   the URL rewrite half.)

   Fix: in `runner.py`, when `BENCHLOCAL_HERMES_RESOLVE_LOCALHOST=1` is
   set, also call `resolve_endpoint_for_container()` on the hermes
   endpoint. Default-off still preserves existing deployments.

   Repro 2026-05-10:
     URL=http://localhost:8010 quality-test.sh --pack hermesagent-20
       → 0/20 (Connection error)
     URL=http://192.168.86.33:8010 quality-test.sh --pack hermesagent-20
       → 10/20 (works via host LAN IP, but only on the rig that has it)

2. **`vendor/HermesAgent-20/verification/agent-runner.py` passes a
   `persist_session=True` kwarg removed from hermes-agent at commit
   44cdf555.**
   Upstream verifier (`stevibe/HermesAgent-20` HEAD `fa40ab9`) still
   emits this kwarg, but the pinned hermes-agent runtime no longer
   accepts it. `AIAgent.__init__() got an unexpected keyword argument
   'persist_session'` exit-1'd every scenario subprocess. `tools/build-
   sandboxes.sh` rsyncs `vendor/<pack>/verification/` →
   `sandboxes/<pack>/verification/` on every build, so the vendor copy
   is the source-of-truth — patches at `sandboxes/hermes/verification/`
   get reverted on next rebuild.

   Fix: drop the line from `vendor/HermesAgent-20/verification/
   agent-runner.py`. Session persistence is now automatic in
   hermes-agent when `session_db` is passed (still passed on line 241).

Validation: Qwen 3.6 27B dual.yml MTP w/ thinking=off
  pre-fix:    0/20 (5%)  — connection error, no LLM calls
  pre-fix v2: 1/20 (5%)  — agent ran, only refusal scenarios passed by luck
  post-fix:   10/20 (50%) — matches Gemma 4 31B v0.7.4 from 2026-05-09

Companion fix in club-3090 quality-test.sh auto-sets the env var when
a localhost-style URL is detected, so end users get this for free.




## v0.9.1 — 2026-05-10


### 🐛 Bug fixes

- **fix(aider-polyglot): bump SUBPROCESS_TIMEOUT_S 1500s → 2700s** ([913f4d5](https://github.com/noonghunna/benchlocal-cli/commit/913f4d5d74b24257d012661137f0e1053c3f0423))


Current default tripped on Qwen 3.6-27B aider-polyglot-30 batch when
thinking was on (proxy SIGKILL'd aider mid-run, partial-data path saved
us but the batch still failed). Even with thinking off, a slower rig
(longer context, weaker GPU, lower-thread parallelism) could still hit
the cap for 30 exercises × multi-turn aider edit/test loops.

Two timeouts bumped in lockstep:
- sandboxes/aider-polyglot/server.py: SUBPROCESS_TIMEOUT_S 1500 → 2700
  (inner — kills aider's benchmark.py if exceeded; AIDER_BENCHMARK_TIMEOUT_S
  env still overrides). Per-exercise partial-data recovery in the
  timeout branch (added earlier today) means even a hit at 2700s
  surfaces what completed.
- benchlocal_cli/sandbox.py: aider-polyglot-30's SandboxConfig
  request_timeout_s 1800 → 3000 (outer HTTP request the runner waits
  on, +5min headroom over the inner cap as before).

Comment block on the inner constant updated to match.

Existing bench runs in flight keep their old image — only future runs
pick up the new defaults.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>



### 📝 Documentation

- **docs: remove codex briefs + thorough README v0.9 rewrite** ([b8eb442](https://github.com/noonghunna/benchlocal-cli/commit/b8eb442f49853b4fda9b0d3e606e2f8b110ad553))


12 internal Codex review briefs (CODEX_BRIEF*.md) and docs/CODEX_REPORT.md
were build artifacts of the multi-pass design-review cycle, not user-facing
docs. Removed from the repo (preserved in git history); README, CHANGELOG,
ROADMAP, sandbox docs no longer reference them.

README rewrite captures v0.9 reality:
- Top-line reframe: no longer just a BenchLocal port — now also an
  eval-expansion track (BenchLocal packs + agentic packs like
  aider-polyglot-30). Repo description and "Why this exists" updated.
- "Layout (planned)" → "Repo layout" with the actual current tree:
  benchlocal_cli/ (with sandbox.py, types.py, packs/aider-polyglot-30.jsonl),
  sandboxes/{bugfind,cli,hermes,aider-polyglot}/ tree, vendor/AiderPolyglot-30/,
  tools/ (build-packs.js, build-sandboxes.sh, sync-vendor.sh), docs/.
- "Quick start (target UX)" → "Quick start" with an aider-polyglot example
  that includes --timeout-per-case 2700 (the inner-subprocess cap as of
  v0.9 SUBPROCESS_TIMEOUT_S bump).
- "Output (target format)" → "Output" + paragraph explaining how agentic
  packs surface pass_rate over a batch instead of per-scenario rows.
- Attribution gains an "Eval-expansion track" subsection citing
  Aider-AI/aider (Apache-2.0) + Aider-AI/polyglot-benchmark.

Cross-ref cleanup so nothing dangles:
- CHANGELOG.md: dropped trailing "See CODEX_BRIEF_V8.md" pointer.
- ROADMAP.md: dropped 5 "Brief: [...]" lines and inline brief refs.
- docs/SANDBOX_PROTOCOL.md: reworded the V6 isolation-tradeoff line.
- sandboxes/bugfind/README.md + sandboxes/cli/README.md: status flipped
  from "🚧 Pre-alpha. To be implemented per CODEX_BRIEF_V4.md" to
  "✅ Sandboxed verifier (v0.4 lifecycle)" — long since shipped.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

- **docs(readme): surface aider-polyglot-30 (v0.9) headline pack** ([148974e](https://github.com/noonghunna/benchlocal-cli/commit/148974e79c92bc1465ab1af24731aeae1bd35d8d))


README was at v0.8-era content — listed BugFind/Hermes/CLI as the only
sandboxed packs, no mention of v0.9.0's headline aider-polyglot-30.

Updates:
- Status block now mentions AiderPolyglot-30 alongside the other
  sandboxed packs; v0.9.0 eval-expansion track called out with link to
  docs/AIDER_POLYGLOT_30.md.
- Modes table gets a new row for `--pack aider-polyglot-30` (independent —
  not bundled in --quick/--medium/--full because aider's harness is
  a batch runner with multi-turn edit/test loops, different shape from
  per-scenario BenchLocal packs).
- Pack inventory adds AiderPolyglot-30 row (multi-language edit/test
  harness via Aider-AI/aider benchmark.py, sandboxed v0.9).

CHANGELOG.md already covers v0.9 (~80 lines of detail under ## 0.9.0).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>



### 🧹 Other

- **release: v0.9.1 — public release patch** ([1f7c7b0](https://github.com/noonghunna/benchlocal-cli/commit/1f7c7b0041530a7b351ab72a7367849a14f03684))


CHANGELOG entry: aider-polyglot-30 batch_finished_after_first_scenario
hook (runner.py), hermes README dev-rig path cleanup, HERMES_V073_AB
absolute-path shortening. No behavioral changes to result content;
cleanup is for going public.

Bumps:
- pyproject.toml: 0.9.0 → 0.9.1
- benchlocal_cli/__init__.py: __version__ → 0.9.1

- **docs + runner: pre-publish cleanup** ([a9c682b](https://github.com/noonghunna/benchlocal-cli/commit/a9c682b6de71e19060a41b3f73529ab52738f1fc))


- runner.py: register aider-polyglot-30 as a single-scoreboard pack
  (batch_finished_after_first_scenario=True). Missing hook from 058bc65;
  the v0.9 sandbox already returns verify-final on first /verify-start.

- sandboxes/hermes/README.md: drop /home/wasif/ container mount paths
  (container actually runs as user `verifier`; baked-image path is
  /opt/hermes-agent). Use neutral /opt/* mount targets in the fast-
  iteration recipe.

- docs/HERMES_V073_AB.md: shorten absolute /opt/ai/github/club-3090/
  reference to repo-relative club-3090/ path.




[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.9.1`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.9.0...v0.9.1)
## v0.9.0 — 2026-05-10


### ✨ Features

- **v0.9.0: Aider Polyglot lite — first eval-expansion slice** ([058bc65](https://github.com/noonghunna/benchlocal-cli/commit/058bc6567698641b3db48c82ae853460e893e28e))


Adds the `aider-polyglot-30` sandboxed pack: 30 hand-curated exercises
across C++/Go/Java/JS/Python/Rust testing multi-language code editing
+ edit-format adherence via upstream Aider-AI/aider's benchmark.py.

Architecture: single-scoreboard pack (1 scenario per pack). Aider's
benchmark.py is intrinsically a batch runner; we accept that rather
than bend /verify-start into a batch protocol. Per-exercise breakdown
in verifier_trace.upstream_per_exercise; aggregate pass_rate /
passed_count / total_count are first-class ScenarioResult fields.

This was the conclusion of two Codex review passes:
- First pass on a "Variant C synchronous batch caching" architecture
  flagged 30+ findings, several critical (bent /verify-start protocol,
  unsafe cache key, ordered-traversal assumption, latency-lie data).
- Second pass on the single-scoreboard redesign approved the direction
  with 5 specific tightenings. All folded in:
  1. pass_rate/passed_count/total_count promoted to first-class
     ScenarioResult fields (so v0.8 --previous-result sees real
     "23/30 → 20/30" deltas, not just threshold flips).
  2. resolve_endpoint_for_container() helper scoped tightly:
     localhost/127.x/[::1] → host.docker.internal; 0.0.0.0 raises;
     non-loopback hosts unchanged. Default-on for aider-polyglot,
     opt-in for hermes via BENCHLOCAL_HERMES_RESOLVE_LOCALHOST=1
     (preserves existing hermes deployments).
  3. Acceptance gate split into harness smoke / completion /
     threshold-fixture / live plumbing (instead of one vague bar).
  4. _build_benchmark_args() centralized + testable. /health probes
     `benchmark.py --help` for required-flag presence so pin-bump
     regressions are visible at boot, not at first /verify-start.
  5. Exact-id resolution against vendor/AiderPolyglot-30/exercises.json
     (not substring keyword matching). Pre-staged workspace contains
     ONLY the canonical 30 exercises — eliminates substring collision
     risk.

New components:

- vendor/AiderPolyglot-30/ — pin file (_sync.json) + curated exercise
  list (exercises.json: 5 per language, all 6 languages, mix of
  easy/medium/hard difficulty + diverse problem types) + ATTRIBUTION.md.
  Pinned to aider@3ec8ec5a + polyglot-benchmark@7e0611e7.

- benchlocal_cli/packs/aider-polyglot-30.jsonl — 1-scenario pack file.

- sandboxes/aider-polyglot/Dockerfile — image FROM buildpack-deps:jammy
  mirroring upstream's aider/benchmark/Dockerfile per-language
  toolchains (Python 3.11 + JDK 21 + Go 1.21 + Rust + Node 20).
  ~2-2.5 GB final image. tools/build-sandboxes.sh learns the new pack.

- sandboxes/aider-polyglot/server.py — single-scoreboard proxy.
  /verify-start spawns benchmark.py once with model_endpoint resolved
  via host.docker.internal, walks per-exercise .aider.results.json
  files, computes pass_rate, returns ONE verify-final with
  per-exercise breakdown. Subprocess hardening from v0.7.3 hermes
  (Popen + start_new_session + os.killpg(SIGKILL) on timeout).

- benchlocal_cli/types.py — ScenarioResult gains optional pass_rate /
  passed_count / total_count fields.

- benchlocal_cli/sandbox.py — SANDBOX_REGISTRY adds aider-polyglot-30
  config (port 9004, request_timeout_s=1800). resolve_endpoint_for_container()
  helper. SandboxClient.start() adds --add-host=host.docker.internal:host-gateway
  on Linux when a sandboxed pack opts into the rewrite.

- benchlocal_cli/runner.py — multi-turn early-out path extracts
  pass_rate/passed_count/total_count from the verify-final response
  onto the ScenarioResult. aider-polyglot-30 gets endpoint resolution
  applied to its model_endpoint.

- docs/AIDER_POLYGLOT_30.md — pack overview, exercise list rationale,
  re-sync recipe, image preflight notes.

- CODEX_BRIEF_V9_0.md — final brief after both review passes.

104 / 104 tests passing (was 81 in v0.8.1; +23 new in
tests/test_aider_polyglot.py): resolve_endpoint_for_container 7 input
shapes, _build_benchmark_args 3 cases, _grade_aider_batch_result 6
(0/30, below-threshold, at-threshold, full-pass, missing results,
extra results), CANONICAL_EXERCISES integrity 3, ScenarioResult
field optionality 2, pack registration 2.

Schema version "3" used in aider-polyglot-30 saved JSONs. Existing
v0.8.x readers (schema "2"/"1") that don't know about pass_rate
ignore it gracefully — backwards-compat preserved.

Pending acceptance gate (deferred until image build runs):
- tools/build-sandboxes.sh aider-polyglot
- /health reports stage=v0.9.0 + exact_match=True on canonical 30
- Live plumbing smoke against Qwen — completes <25 min, pass_rate >= 1/30

v0.9.1 (lm-eval IFEval+GSM8K) and v0.9.2 (BFCL-lite) follow separately.
No --audit bundle until they're all in.

Refs: CODEX_BRIEF_V9_0.md (with both review passes inlined),
docs/AIDER_POLYGLOT_30.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>




[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.9.0`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.8.1...v0.9.0)
## v0.8.1 — 2026-05-10


### ✨ Features

- **v0.8.1: inspect --diff + inspect --logs (deferred Phase B.5)** ([75cd902](https://github.com/noonghunna/benchlocal-cli/commit/75cd9026e81775d3321c2d1760298f11004d89a3))


Wraps up the diagnostic tooling started in v0.8.0. No new flags on `run`;
pure additions to `inspect` plus a small runner-side stamp.

New inspect features:

- inspect --diff <other.json>: side-by-side scenario comparison vs
  another saved run. Renders verdict flip (FIX/REGRESSION/stable),
  final-response delta, upstream_score delta, latency delta. Per-(pack,
  scenario) keyed so same scenario id across packs doesn't collide
  (Codex review pattern from v0.8 brief #1, applied to inspect too).

- inspect --logs DIR: pull the associated sandbox stdout/stderr file
  after each rendered scenario. Resolves via per-scenario
  verifier_trace.sandbox_log_file (added this release) with fallback
  to <DIR>/sandbox-<pack_id>.log for pre-v0.8.1 saved JSONs.

Phase A runner stamp:

- Runner._inject_sandbox_log_file() called from all 3 sandboxed-result
  paths (single-turn bugfind verify, multi-turn early-out for hermes,
  multi-turn loop-end for cli/hermes). No-op when --sandbox-log-dir is
  unset. Default behavior unchanged.

81/81 tests passing (was 70). 11 new tests covering diff happy paths
(FIX flip, REGRESSION flip, NEW-in-current handling), --logs
resolution + per-scenario field + per-pack fallback + missing-dir
error path, and the runner injection logic.

Verified against real saved data:
  inspect results/gemma-v074-hermes.json --diff results/gemma-v073-hermes.json --scenario HA-03
    → "verdict: (FIX)", upstream_score 0→100, latency 11→3.6s
  inspect ... --scenario HA-04
    → "verdict: (REGRESSION)", correctly identifies the v0.7.3 lucky-pass
      that v0.7.4 (and now this --diff view) catches.

Refs: CODEX_BRIEF_V8.md (Phase B.5 explicitly scoped for v0.8.1).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>




[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.8.1`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.8.0...v0.8.1)
## v0.8.0 — 2026-05-10


### ✨ Features

- **v0.8.0: diagnostic tooling — delta, inspect, history** ([3370d0c](https://github.com/noonghunna/benchlocal-cli/commit/3370d0c05c865add3ba7130416d71e417437bbba))


Builds on v0.7.4's grading parity. Pure Python additions on the runner
side; no sandbox rebuild needed.

New `run` flags:
- --previous-result PATH: classify each scenario as regression / fix /
  stable / new / dropped vs a saved RunResult JSON. Emits a Δ column in
  the markdown output and a `delta` field in the saved JSON. Per-pack
  breakdown with regressions_list / fixes_list for surfacing exactly
  which scenarios moved.
- --exit-on-regression: exit code 3 when delta has any regressions.
  CI-friendly. Requires --previous-result.
- --history-file PATH: append a per-run summary row to a CSV after the
  run completes. Falls back to BENCHLOCAL_HISTORY_FILE env. Opt-in only
  (default unchanged). POSIX flock prevents concurrent-append corruption.

New subcommands:
- `benchlocal-cli inspect <result.json>`: surface saved-JSON forensics
  without manual JSON grep. Filters: --scenario, --pack, --failed,
  --mode. Default truncation: 80 lines for verifier_trace, 5 turns for
  conversation; --full disables. --format json for piping. Tolerates
  v0.5/v0.6/v0.7.0 saved JSONs missing newer fields (gracefully renders
  "<none — pre-v0.7.2>" instead of crashing).
- `benchlocal-cli history`: query the history CSV. Filters: --file,
  --model, --pack (substring), --since YYYY-MM-DD, --last N.
  --format json for plotting.

Codex review of the v0.8 brief (9 findings) folded in:
1. Scenario keying is (pack_id, scenario_id), not bare id.
2. Multi-repeat aggregates to per-scenario pass-rate; default ≥50%
   threshold (override via BENCHLOCAL_DELTA_PASS_THRESHOLD env).
3. inspect MVP scope (B.0); --diff and --logs deferred to v0.8.1
   (Phase B.5 — explicit follow-up).
4. Markdown delta column rendered ONLY when --previous-result is
   passed; default markdown stays byte-stable for pinned downstream
   parsers (club-3090/scripts/quality-test.sh).
5. fcntl.flock around CSV append.
6. Older-shape tolerance: `response` vs `raw_response`, missing
   `verifier_trace`/`conversation`.
7. Locked to v0.7.4 master only (this is post-v0.7.4).
8. inspect --logs sub-task deferred — needs per-scenario
   sandbox_log_file field added to forensics first.
9. schema_version bumped via opt-in `delta` field rather than blanket
   bump; downstream readers that don't know about `delta` ignore it.

70/70 tests passing (was 40). New tests:
- tests/test_delta.py — 9 tests covering classify happy + sad paths,
  scenario keying, multi-repeat threshold, schema-version mismatch
- tests/test_inspect.py — 10 tests covering filter combos, truncation
  defaults vs --full, missing-field tolerance, format json/markdown
- tests/test_history.py — 11 tests covering append/read round-trip,
  filter combos, missing-column tolerance, path resolution precedence

Verified against real saved data (results/gemma-v074-hermes.json):
- Delta vs results/gemma-v073-hermes.json correctly identifies 6
  false-negatives v0.7.4 caught (HA-03, HA-06, HA-11, HA-13, HA-15,
  HA-18) and 2 lucky-passes corrected (HA-04, HA-14).
- inspect --scenario HA-03 renders cleanly with verifier_trace +
  upstream score breakdown.
- History append round-trips without corruption.

Refs: CODEX_BRIEF_V8.md (with all 9 Codex review findings folded in
under "Codex review findings (2026-05-09)").

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>




[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.8.0`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.7.4...v0.8.0)
## v0.7.4 — 2026-05-10


### ✨ Features

- **v0.7.4: Hermes grading-parity via upstream Node grader** ([5322624](https://github.com/noonghunna/benchlocal-cli/commit/53226248a4bb9ec0245d7b01d6caf29949e4c81d))


Replaces v0.7.3's keyword-match Python grader with upstream's core.mjs
running inside the same container. Hermes container now boots upstream's
verification/server.mjs (Node, internal :4010); our Python proxies
/verify-start to upstream's POST /run-scenario.

Real-model A/B against same Gemma endpoint as v0.7.3:
- Gemma 4 31B v0.7.3 (keyword grader): 6/20 = 30%
- Gemma 4 31B v0.7.4 (upstream grader): 10/20 = 50%

v0.7.4 caught 6 false-negatives v0.7.3 missed (HA-03 refusal,
HA-06/11/13/15/18 actual wins) and correctly failed 2 v0.7.3
lucky-passes (HA-04, HA-14). Net +4 correct verdicts. The visible 20pp
shift is keyword-grader floor lifting to truth, not the model improving.
See docs/HERMES_V073_AB.md for the per-scenario reconciliation.

Major changes:

- sandboxes/hermes/Dockerfile: now bakes Node 22 + Chromium +
  agent-browser + Python venv with hermes-agent v0.13 editable-install.
  Image grew ~600 MB → ~1.5 GB. Explicit BAKE_HERMES_AGENT=1 must
  succeed or build fails (no silent fallback).

- sandboxes/hermes/entrypoint.sh (NEW): boots upstream Node grader on
  internal :4010, polls /health, fail-loud if Node doesn't come up
  within 60s. Runs our Python proxy in foreground (NOT exec — preserves
  the cleanup trap that kills Node on Python exit).

- sandboxes/hermes/server.py: rewritten as a protocol translator.
  _translate_request() maps runner's /verify-start payload to upstream's
  POST /run-scenario shape; _translate_upstream_result() maps upstream's
  {status, score, summary, verifier, …} to our ScenarioResult shape with
  upstream_raw capped at 16KB for forensics. /health probes both Python's
  install state AND upstream Node's /health (split-brain prevention).

- vendor/HermesAgent-20/verification/manifest.mjs: re-pinned upstream
  HERMES_PINNED_COMMIT from ea74f61 (~6mo stale) to 44cdf555 (upstream
  main HEAD). Newer pin ships hermes-agent v0.13.0 with months of
  tool-calling reliability fixes.

- vendor/HermesAgent-20/verification/hermes-runtime.mjs: patched
  writeHermesConfig() to inject `context_length: <ctx>` under model:
  and compression: blocks via BENCHLOCAL_HERMES_CONTEXT_OVERRIDE env
  (default 64000). Works around hermes-agent v0.13's 64K minimum
  context-window check on models served at smaller windows (Gemma at
  32K). Without this all scenarios fail at boot.

- benchlocal_cli/sandbox.py: hermes pack injects
  BENCHLOCAL_HERMES_CONTEXT_OVERRIDE=64000 into container env by default.

Critical bug found+fixed during the A/B:

- _normalize_base_url() was STRIPPING /v1 instead of ENSURING it.
  Caused HTTP 404 on every model call → 0 tool events → 5% floor score.
  Fixed to always end the base URL with /v1 (OpenAI client appends
  /chat/completions itself). Test updated to lock in correct behavior.

40/40 tests passing (was 33). New tests cover request translation,
result translation, classify_failure, cap_upstream_for_trace,
normalize_base_url, mock-pass response shape.

Schema_version bumped to "2". Saved JSON traces include upstream_status,
upstream_score (0-100), upstream_verifier (subscore breakdown),
upstream_raw (capped). Back-compat preserved on detail/failure_mode.

Pending: v0.7.4 Qwen leg blocked by canonical compose's
maybe_override_with_speculators + transformers regression on
nightly-01d4d1ad — separate stack-level concern.

Refs: CODEX_BRIEF_V7_4.md (12-finding Codex review folded in),
docs/HERMES_V073_AB.md (v0.7.4 supplement section).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>




[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.7.4`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.7.3...v0.7.4)
## v0.7.3 — 2026-05-10


### ✨ Features

- **v0.7.3: Hermes upstream-runtime delegation + real-model A/B** ([843bd4f](https://github.com/noonghunna/benchlocal-cli/commit/843bd4f72522ad96f71ea3e97411baa499190312))


Replaces the v0.6 mocked-tool state machine with delegation to upstream
nousresearch/hermes-agent (host bind-mount or image-baked clone). Real-model
A/B against the same target endpoint as runner: Qwen3.6-27B 9/20 = 45%
(was 25%, +20pp), Gemma-4-31B 6/20 = 30% (was 20%, +10pp). Both legs run
real multi-turn agent loops — 16-17 of 20 scenarios used tools, 92-128
total tool events. See docs/HERMES_V073_AB.md for the side-by-side.

Major changes:

- sandboxes/hermes/server.py rewritten — /verify-start spawns upstream
  agent-runner.py per scenario via subprocess (process-group isolated,
  300s default cap, per-scenario job dirs cleaned after each request).
  Distinct failure modes: agent_runner_timeout, agent_runner_crashed,
  result_json_malformed, model_endpoint_unreachable.

- Five-level detection priority for the upstream install:
  HERMES_AGENT_FORCE_BAKED=1 → HERMES_AGENT_HOST_PATH → auto-detect at
  /opt/hermes-agent, ~/hermes-agent, ~/.local/hermes-agent,
  ~/.hermes/hermes-agent → which-hermes symlink-walk → image-baked
  fallback → fail-loud at /health.

- SandboxConfig gained host_mounts, env, request_timeout_s. The hermes
  pack uses 900s HTTP read timeout (vs 60s for bugfind/cli) and the
  runner injects HERMES_SUBPROCESS_TIMEOUT_S (default 300s) via
  BENCHLOCAL_HERMES_SUBPROCESS_TIMEOUT_S.

- Multi-turn early-out path now propagates the sandbox trace payload into
  ScenarioResult.verifier_trace (was lost in v0.7.2).

- /health and verifier_trace carry hermes_agent_path, hermes_agent_source
  ∈ {host-mount, baked, missing}, hermes_agent_commit (best-effort
  git rev-parse) for reproducibility.

Drift-handling fixes that landed during the A/B:

- agent-runner.py drops persist_session=True (removed in user's fork)
- agent-runner.py uses HERMES_AGENT_PATH env (was hardcoded /opt/hermes-agent)
- enabled_toolsets passed as None instead of [] (None = all toolsets)
- subprocess cwd is per-scenario workspace, not the install dir
  (prevents `pytest tests/` against the host source from hanging)
- Per-scenario <HERMES_HOME>/config.yaml writes both
  model.context_length and auxiliary.compression.context_length to
  override hermes-agent's 64K minimum check on smaller-window serves

Soft-pass grading branch: scenario passes if ≥1 keyword hit AND
upstream completed AND ≥1 tool event. Catches scenarios where the
agent did the right thing but described it differently than upstream's
success_case prose. Strong-pass (≥2 keywords) preserved.

33/33 tests passing (was 28). Acceptance gate met on Qwen, deflated on
Gemma due to keyword-grader floor (real Gemma capability ~55-65% per
tool-event signals); v0.7.4 grading-parity port queued.

Refs: CODEX_BRIEF_V7_3.md (with Codex 9-finding review folded in),
docs/CODEX_REPORT.md (acceptance gate + drift log),
docs/HERMES_V073_AB.md (per-scenario side-by-side).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>



### 📝 Documentation

- **docs: v0.8 brief — diagnostic tooling (delta + inspect + history)** ([259dd3f](https://github.com/noonghunna/benchlocal-cli/commit/259dd3f267fb1fca8142751ff68722ed88fec3b8))


After v0.7.3 closes the verifier-parity vision, v0.8 makes the measurements
usable. Three pieces, ~8-11 hr Codex chunk:

A. **--previous-result PATH** delta comparison (~2-3 hr)
   - Run accepts a prior run's JSON; emits regression / fix / stable
     classification per scenario
   - --exit-on-regression for CI gating
   - Schema-version-aware (warn but don't refuse on mismatch)

B. **`inspect` subcommand** (~3-4 hr)
   - benchlocal-cli inspect <result.json> --scenario X
   - Pretty-prints scenario metadata + model response + verifier_trace
     + conversation history
   - Filters: --pack, --failed, --mode, --diff, --logs
   - Read-only; no endpoint/model/docker needed
   - Replaces today's hand-grep + python3 -c JSON inspection

C. **Trend tracking via flat CSV** (~2-3 hr)
   - results/quality/history.csv append-row writer (opt-in via
     --history-file flag or env var)
   - benchlocal-cli history subcommand with --model / --pack / --since /
     --last filters
   - Append-only schema, missing-column-tolerant reader

Foundation: v0.7.2's verifier_trace + conversation + sandbox-log-dir fields
(commit 76f8b30) are the data v0.8 reads. Tools just consume what's already
captured.

Acceptance gate covers hand-testing on existing v0.7.2 forensics data:
inspect /tmp/qwen-v071-sandboxed.json --pack hermesagent-20 --failed
should produce useful per-scenario detail without manual Python.

Backwards compat: older result JSONs (v0.5/v0.6/v0.7.0) without forensics
fields work for inspect — sections just aren't rendered. Schema mismatch
on --previous-result warns but proceeds best-effort.

After v0.8 lands: cross-rig regression testing is single-command answerable.
"Did this Genesis pin bump regress quality?" becomes a real workflow,
not a forensics archaeology project.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

- **docs: v0.7.3 brief — make image-baked path testable + cross-validatable** ([721a9dc](https://github.com/noonghunna/benchlocal-cli/commit/721a9dcbe2bd57007e65d19eea35705d561597e4))


Two requirements clarified:

1. **Detection logic**: bind-mount only IF a host hermes-agent is detected;
   otherwise fall through to image-baked install (which Phase B now BUILDS
   by default, not opt-in). Was previously framed as opt-in to baked which
   created a hidden no-op state.

2. **Force-baked test override**: HERMES_AGENT_FORCE_BAKED=1 skips host
   detection entirely, lets us validate the baked path even on dev rigs
   that have a local install. Without this flag, we couldn't regression-
   test the baked setup without uninstalling the local copy.

Updated detection priority (5 levels):
  1. HERMES_AGENT_FORCE_BAKED=1 (test override) → use baked
  2. HERMES_AGENT_HOST_PATH (explicit) → bind-mount
  3. Auto-detect host paths (/opt, ~/, ~/.local) → bind-mount if exactly one
  4. Image-baked fallback (auto, not opt-in)
  5. Fail loud — no host install AND no baked clone

Test matrix added (4 configs with expected behavior). tools/test-sandboxes.sh
should run with HERMES_AGENT_FORCE_BAKED=1 too so both paths get exercised
in CI.

Phase B no longer opt-in — bakes by default. Build-arg
BAKE_HERMES_AGENT=0 lets users skip the bake for smaller bind-mount-only
images. /health reports `hermes_agent_path` + `hermes_agent_source` fields
for debuggability (source ∈ {host-mount, baked, missing}).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

- **docs: v0.7.3 brief — bind-mount host hermes-agent (user already has local install)** ([e873119](https://github.com/noonghunna/benchlocal-cli/commit/e87311987cb1063814cbb8ac791734efe212188f))


User clarified: hermes-agent is already installed on their host system.
Brief was assuming we'd need to clone-into-image in the Dockerfile, which
would force an upstream-discovery phase + duplicate disk/sync. Wrong path
when a working host install already exists.

Reshaped Phase A around detection + bind-mount instead of clone:

  1. HERMES_AGENT_HOST_PATH env var (user-set explicit) — highest priority
  2. Auto-detect common paths: /opt/hermes-agent, ~/hermes-agent,
     ~/.local/hermes-agent. Single hit -> use; multiple hits -> error
     asking user to disambiguate.
  3. Image-baked fallback (opt-in via HERMES_AGENT_BAKED_INSTALL=1) — only
     for users who want self-contained images; OFF by default
  4. Fail loud — if none of the above mountable, /health reports
     missing-hermes-agent. Don't silently fall back to v0.6 keyword-match
     (would mask whether v0.7.3 is actually engaged)

Net effect:
- Phase A no longer GATING (was 1-3 hr risk; now 1-2 hr deterministic)
- Phase B baked-clone is opt-in (off by default), so its scope is just
  "gate the Dockerfile lines behind a build arg"
- Total estimate tightened to 6-9 hr (was 6-10 with risk multiplier)
- No need for Option B fallback architecture — bind-mount of user's
  local install IS the primary path

SandboxConfig grows a `host_mount` field; SandboxClient.start() adds
`-v {host_path}:/opt/hermes-agent:ro` to docker run args when set.
/health endpoint reports which hermes_agent_path is mounted for debug.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

- **docs: v0.7.3 brief — Hermes upstream-runtime delegation + Phase A risk surfaced** ([1d2d42e](https://github.com/noonghunna/benchlocal-cli/commit/1d2d42ec5d43b8b96ea2faf37e0f38bbcaf83773))


Closes Codex's flagged Phase D gap from v0.7 candidate report. v0.7.3
wires upstream agent-runner.py into the hermes sandbox so grading uses
real upstream evaluation instead of v0.6 keyword-evidence on final answer.

Risk-fronted: vendor/HermesAgent-20/verification/agent-runner.py is a
wrapper that imports from /opt/hermes-agent (modules: hermes_state,
run_agent, tools.terminal_tool). The actual upstream Hermes agent
codebase isn't in our vendor tree. **Phase A is locating + installing
that upstream codebase before any code changes**. If not publicly
accessible, brief specifies an Option B fallback (lighter integration,
implement upstream behavior in our sandbox without full upstream install).

Phase plan:
  A. Locate upstream Hermes agent codebase (1-3 hr, GATING)
  B. Install upstream into hermes sandbox image (1-2 hr)
  C. Rewrite hermes server.py to delegate via agent-runner.py (2-3 hr)
  D. Runner: plumb model_endpoint to /verify-start, bump timeout to 15min (1 hr)
  E. Tests + docs + version bump to 0.7.3 (1 hr)

Total: 6-10 hr if upstream reachable. Phase A failure → +2-4 hr Option B.

Departure from v0.7.1's runner-side multi-turn protocol for Hermes:
upstream agent-runner owns the model loop. Hermes /verify-start runs
the entire agent flow synchronously and returns verify-final. The
runner's existing early-out path (Codex implemented in v0.7.1) handles
this without changes to the multi-turn loop logic. CLI multi-round
keeps using the v0.7.1 runner-side loop unchanged.

Acceptance gate: hermesagent-20 in 40-65% range (was 25%/20% with
keyword-match floor). Real Pattern A/B/C/D failures resolve. Cross-
model discrimination > current 5pp gap. After v0.7.3, all 3 sandboxed
packs use upstream runtimes for grading — v0.7's "real verifier
parity" vision fully closed, public flip unblocked.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

- **docs(roadmap): add v0.9+ implementation-pattern section + clean up stale Inspect AI ref** ([578d113](https://github.com/noonghunna/benchlocal-cli/commit/578d113c02014ffec6e2cd2168f0d4b68af2d0f7))


Locks in the design pattern for plugging external benches into benchlocal-cli
before any of v0.9+ work starts. Three layers:

1. Mode flags (--quick / --medium / --full / --audit / --swe) — top-level
   presets, ergonomic, ~90% of users
2. Composable --with-X flags — additive per-bench, power-user composition
3. Per-bench env vars (LM_EVAL_PROMPTS_PER_TASK, BFCL_LITE_CASES, etc) —
   scope tuning without flag proliferation

Sandbox container layout per bench: same shape as BugFind/CLI/Hermes —
Dockerfile bakes upstream runner, server.py exposes /health + /verify, runner
doesn't special-case any pack. Adding `aider-polyglot-30` is mechanically
identical to adding any BenchLocal pack.

Each external bench = one Codex brief, ~2-4 hr each (wrapping upstream
runner is most of the work, not authoring scenarios).

Also cleaned up two stale references:
- Removed "Inspect AI's HermesAgent port replaces..." line (pre-correction
  framing; Inspect AI is parking-lot for AISI-native or custom-authored evals)
- Updated HumanEval+ from "covered by BugFind-15" to "Aider Polyglot replaces
  it as primary; demoted to optional --with-legacy-codegen"

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

- **docs(roadmap): incorporate Codex review — Aider Polyglot for code gen, IDE-agent safety slice** ([88b2d15](https://github.com/noonghunna/benchlocal-cli/commit/88b2d15510b0b2313a070475e92b86ec0954a321))


Bounced the bench portfolio with Codex via MCP. Three sharp pushbacks
worth landing, rest agreed:

1. **HumanEval+ → Aider Polyglot lite** as primary code-gen slot.
   Reasoning: HumanEval+ is Python-weighted, saturated, easy to overfit.
   Aider Polyglot covers C++/Go/Java/JS/Python/Rust + edit-format
   reliability — closer to "will this model behave inside an editor"
   for IDE-agent target. HumanEval+ demoted to optional --legacy-codegen
   cheap compatibility anchor only.

2. **NEW: IDE-agent safety slice** (~10-20 scenarios, ~5 min). NOT
   WMDP-style. Concrete failure modes: rm -rf refusal without confirm,
   .env / secret leak, malicious README/source-file instruction
   injection, curl|bash suggestion compliance. This axis is unique
   to local coding-agent deployment and isn't covered by any existing
   bench. Custom-authored — the one slot where Inspect AI's framework
   primitives could earn their keep (no upstream runner exists).

3. **Explicit lm-eval-harness scope tightening** — IFEval + GSM8K only,
   skipping MMLU/HellaSwag/ARC/TruthfulQA. Saturated for modern model
   classes; low signal at our scale.

Plus minor: noted LiveCodeBench as alternative for contamination-
resistant algo coding (different motivation than Aider Polyglot;
add only if needed). SWE-bench-lite stays --swe power-user tier with
mini-SWE-agent as the runner.

Mode taxonomy after expansion:
  --quick    2 packs / 30 scenarios / ~5-10 min
  --medium   5 packs / 75 scenarios / ~15-25 min  (no Docker)
  --full     8 packs / 150 scenarios / ~25-40 min (current scope)
  --audit    full + IFEval+GSM8K + BFCL-lite + Aider Polyglot lite
             + IDE-agent safety / ~55-75 min     (release-gate)
  --swe      SWE-bench-lite / 30-60 min          (power-user)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

- **docs(roadmap): nuance the Inspect AI position — port vs canonical home** ([be043d3](https://github.com/noonghunna/benchlocal-cli/commit/be043d3f7b2cfb0d07a55efca49f64c6f1beab07))


User pushed back: Inspect AI ships its own bench library. True — but for
most major benches (IFEval/GSM8K/MMLU/HellaSwag/HumanEval/MBPP/SWE-bench/
BFCL) Inspect AI's version is a *port* of canonical upstream, not the
canonical source itself. Choosing Inspect AI's port = picking interpretation
drift / version lag against the more-maintained canonical home.

Updated parking-lot Inspect AI line to clarify the right trigger:

  Promote when we want either:
    (a) Inspect-AI-NATIVE evals — UK AISI's safety library (WMDP, redteam,
        InspectAgentBench) where Inspect AI IS the canonical home
    (b) Custom evals authored from scratch where we want their framework
        primitives

  NOT the right path for canonical-upstream-elsewhere benches — those should
  delegate to upstream (lm-eval-harness, gorilla, etc) for fewer drift
  surprises.

Net: Inspect AI's value emerges when wanting evals it owns, not when wrapping
third-party benches that have stronger canonical homes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

- **docs: roadmap update — promote Hermes upstream-runtime wiring to v0.7.3, demote Inspect AI** ([74b2679](https://github.com/noonghunna/benchlocal-cli/commit/74b26792df8eac3bc676acd4338f937211c36af6))


User correction: Inspect AI doesn't help BenchLocal-specific work because the
BenchLocal packs already have upstream runtimes (BugFind/CLI delegate to
upstream JS via subprocess+node; Hermes has agent-runner.py vendored). Putting
BenchLocal Hermes through Inspect AI would just be an indirection layer over
the same upstream code.

Roadmap changes:

1. **v0.7.3 added (planned, ~5-8 hr)**: Hermes upstream-runtime delegation —
   wire vendor/HermesAgent-20/verification/agent-runner.py for grading via
   subprocess+python, same pattern as BugFind/CLI in v0.7. Closes Codex's
   flagged Phase D gap. Closes today's Hermes Pattern A/B/C/D failures plus
   the keyword-evidence verifier strictness ceiling. Likely lifts Hermes from
   today's 25%/20% (keyword-match floor) to 40-65% with real cross-model
   discrimination.

2. **v0.7.2 captured retroactively** as shipped (post-run forensics).

3. **Inspect AI demoted** from "Mirror HermesAgent into Inspect AI" (v0.9+
   slot 3) to "for NEW agent benchmarks beyond BenchLocal" — tau-bench,
   AgentBench, custom evals. Inspect AI's framework primitives earn their
   keep when adding evals that don't already have upstream runtimes; using
   it to wrap BenchLocal Hermes is the wrong tool for the job.

Net: Hermes fix path is shorter (v0.7.3, ~5-8 hr) than the Inspect AI port
(~10-15 hr) AND closes more of the failure surface because it uses upstream's
own tool catalog (read/list/glob/exec/browser/cron/send_message) which Inspect
AI doesn't ship.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>




[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.7.3`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.7.2...v0.7.3)
## v0.7.2 — 2026-05-10


### ✨ Features

- **v0.7.2: post-run forensics — verifier_trace, sandbox container logs, multi-turn conversation** ([76f8b30](https://github.com/noonghunna/benchlocal-cli/commit/76f8b300facadc8627c1abdb0740ae4432539877))


Three small additions to make failure diagnosis post-run actually possible.
Today's Hermes investigation surfaced the gap: I had to hand-grep raw_response
+ assistant_messages + scenario JSON to reconstruct what happened, and even
then the upstream verifier's rich payload (rawLog, score breakdowns, notes)
was already discarded by the time RunResult was serialized.

1. **types.py**: ScenarioResult.verifier_trace (dict | None) — preserves the
   full upstream payload (rawLog, notes, correctness/efficiency/discipline
   subscores for CLI, etc.). ScenarioRun.conversation (list[dict]) —
   captures the full multi-turn message history including system + user +
   assistant + tool messages. Both serialized through to_dict().

2. **sandbox.py**: SandboxClient.stop(log_dir=...) captures `docker logs`
   to <log_dir>/sandbox-<pack-id>.log BEFORE `docker stop --rm` wipes them.
   Failure to capture is non-fatal. _result_from_payload() now preserves
   the upstream payload as verifier_trace (was: dropping everything except
   passed/failure_mode/detail).

3. **runner.py**: Runner.__init__ accepts sandbox_log_dir; _stop_sandboxes
   forwards it to each client.stop(). Multi-turn loop populates
   ScenarioRun.conversation from `history` and ScenarioResult.verifier_trace
   from final_payload. CLI exposes --sandbox-log-dir flag.

Net: failed scenarios can now be diagnosed entirely from the saved JSON +
sandbox.log without re-running. The upstream JS verifier's rawLog (which
showed the exact "awk: cannot open /workspace/access.log" error in today's
debug) is now captured automatically.

Bumped 0.7.1 → 0.7.2. 18/18 tests still pass; ruff clean; no API breakage
(new fields default to None / empty list).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>




[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.7.2`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.7.1...v0.7.2)
## v0.7.1 — 2026-05-10


### ✨ Features

- **feat(runner): drive sandbox multi-turn scenarios** ([fc20a34](https://github.com/noonghunna/benchlocal-cli/commit/fc20a3427498760db6a6895ea88fd37f918c4bd4))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.7.1`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.7.0...v0.7.1)
## v0.7.0 — 2026-05-10


### ✨ Features

- **feat(sandboxes): adapt to upstream verifier runtimes** ([0c726f8](https://github.com/noonghunna/benchlocal-cli/commit/0c726f844641e655fb1a5be3090d6eb9e2e7f5cd))
- **feat(packs): expose upstream verifier metadata** ([5ffba36](https://github.com/noonghunna/benchlocal-cli/commit/5ffba36a17c0a0aef3bd68b31300e11f7ef02948))
- **feat(vendor): sync upstream verifier runtimes** ([027263e](https://github.com/noonghunna/benchlocal-cli/commit/027263ee1615ffbcbd0e065453ce598397872a24))
- **feat(cli): --sandboxed-only flag for verifier debug iteration** ([12d7be4](https://github.com/noonghunna/benchlocal-cli/commit/12d7be4123443d23d27f9dbeccc6e1c60357dc6a))


### 🐛 Bug fixes

- **fix(cli): pre-create /workspace with verifier ownership instead of CLI40_WORKSPACE_DIR override** ([bc52a32](https://github.com/noonghunna/benchlocal-cli/commit/bc52a325ad1383515b82dd2088e4f67508f6eb36))


Real-model A/B on Qwen3.6-27B revealed cli-40 was scoring 5/40 (and earlier
2/40 in mid-debug iterations) because of a workspace path mismatch:

  - Upstream system prompt hardcodes `/workspace/<file>` paths (what the
    model is TOLD to read/write)
  - Earlier patch redirected the verifier's seed/check via
    `CLI40_WORKSPACE_DIR=/tmp/cli40-workspace` env var to dodge the
    /workspace mkdir-as-root permission issue
  - Net effect: verifier seeded `access.log` into `/tmp/cli40-workspace`,
    model wrote command targeting `/workspace/access.log` (per the prompt),
    awk failed with "cannot open /workspace/access.log: No such file"
  - Score: correctness=0 (ENOENT), score=25, status="fail" — even though
    the model's command was textbook-correct

Fix: drop the env override + pre-create `/workspace` with verifier ownership
in the Dockerfile. Now the path the prompt tells the model to use matches
the path the verifier seeds into.

Repro after fix (manual node REPL inside container):
  CLI-01 with model command:
    awk '{print $1}' /workspace/access.log | sort | uniq -c | sort -rn | head -20 > /workspace/top_ips.txt
  → status: pass, score: 100, correctness=2, efficiency=2, discipline=2

Real-model A/B with fix:
  Qwen3.6-27B cli-40: 5/40 → 10/40 (25%); 10/25 (40%) on one-shot
  Gemma 4 31B cli-40: 5/40 → 11/40 (28%); 11/25 (44%) on one-shot

Multi-round (15/40) remains at 0/15 — that's the runner-side multi-turn
delegation gap addressed by CODEX_BRIEF_V7_1.md, separately.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

- **fix(sandboxes): exception handling + bash -c routing + multi-line extraction** ([c5e1dbd](https://github.com/noonghunna/benchlocal-cli/commit/c5e1dbd5c7d1078d60af6ab0126ab0266c08da8c))


Real-model A/B against Qwen3.6-27B surfaced three v0.6 verifier defects:

1. **All 3 sandboxes**: do_POST didn't wrap _verify() in try/except, so any
   verifier exception killed the connection without sending a response.
   Runner saw 'Server disconnected without sending a response.' Now: catch
   anything, return server_error with traceback.

2. **CLI-only**: subprocess.run() raises FileNotFoundError when the executable
   isn't on the sandbox PATH (e.g. 'git', 'if', shebang lines). Was bubbling
   to do_POST and crashing. Now caught in _run_command, returns exit_code=127
   + 'command not found' stderr — verifier_fail with clean message.

3. **CLI-only**: shell=False rejected ALL compound commands (cmd1 && cmd2,
   pipes, redirects, multi-line scripts). Models legitimately emit these.
   Now: detect shell metacharacters via _needs_shell(), route to bash -c
   with raw-string forbidden-token check (_is_safe_shell). Direct exec
   path retained for simple single commands.

4. **CLI extraction**: fenced code blocks were truncated to first line —
   killed multi-line scripts. Now: full block content returned, shebangs
   stripped, $-prompts stripped per line.

Net impact on Qwen3.6-27B cli-40 score: 0/40 → 5/40 (12%). The remaining
failures are mostly upstream-fixture-gap (commands reference workspace
files that don't exist in our local mirror) — that's a v0.7 problem.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>



### 📝 Documentation

- **docs: v0.7.1 brief — runner-side multi-turn delegation (unblocks public flip)** ([8a121de](https://github.com/noonghunna/benchlocal-cli/commit/8a121deb90033220df0156f270b8e2c4d36228d6))


v0.7 candidate exposed two pack classes stuck at 0% because the runner
sends only one chat completion per scenario:
  - cli-40 multi-round (15/40 scenarios)
  - hermesagent-20 (all 20 scenarios — falls back to v0.6 single-turn
    shape-check)

The HTTP protocol for multi-turn is already designed (/verify-start /
/verify-turn / /verify-end). The Hermes sandbox already implements it.
v0.7.1 closes the gap on the runner side + adds the same endpoints to
the CLI sandbox so multi-round scenarios can be driven iteratively.

Phases:
  A. Generalize SandboxClient.verify_hermes_* → verify_multiturn_* (1h)
  B. Add /verify-start/turn/end to CLI sandbox using upstream BashSession (2-3h)
  C. Runner multi-turn loop on next-prompt responses (3-5h ⭐ core work)
  D. Tests + docs + version bump to 0.7.1 (1-2h)

Total estimate: 7-11 hr. After v0.7.1 acceptance gate (cli-40 ≥40%
overall, hermesagent-20 measuring real multi-turn behavior), public
flip is unblocked.

Brief at CODEX_BRIEF_V7_1.md. Roadmap updated to slot v0.7.1 between
v0.7 and v0.8.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

- **docs: --audit as the v0.9+ release-gate mode name (avoid --full/--everything overlap)** ([984bf68](https://github.com/noonghunna/benchlocal-cli/commit/984bf68c2c84197263f3518781f697b4d4cccd89))
- **docs: promote diagnostic tooling above further evals + expansion-order rationale** ([d0678c9](https://github.com/noonghunna/benchlocal-cli/commit/d0678c95ed77fa55b036bf490efccb942b240e10))


User calibration on the bench-comparison framing:

1. **Promote diagnostic tooling (v0.8) above eval expansion**: result diffing,
   trend tracking, and per-scenario inspection make existing 8 packs more
   useful. Don't add new evals before tooling lets us actually use the ones
   we have.

2. **Optional expansion order (v0.9+)** — when expansion is needed:
   - lm-eval calibration slice (NOT replacement; tiny sanity sidecar
     covering IFEval/GSM8K/MMLU/HellaSwag for "did a quant/config change
     broadly damage model quality?")
   - BFCL-lite for tool-calling depth (BenchLocal toolcall-15 is shallow
     by design)
   - Mirror HermesAgent into Inspect AI (strongest "maybe we should have
     started there" point — v0.7's Hermes architecture gap exists because
     upstream agent runner owns the model loop while our SandboxClient
     sends one response/call. Inspect AI's framework primitives would
     have avoided this re-invention.)

3. **Tools evaluated and not ranked for inclusion**: promptfoo (doesn't
   solve verifier maturity), OpenAI simple-evals (deprecated source),
   HumanEval+ (covered by BugFind-15), MT-Bench (requires judge model,
   not deterministic).

BenchLocal stays the primary 30-45 min local quality gate. Other tools
become complementary when there's a concrete need on a specific axis.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

- **docs: report v0.7 verifier-runtime lift** ([4d911f3](https://github.com/noonghunna/benchlocal-cli/commit/4d911f33da0c74118b76fbe3dd187d1036d1b09a))
- **docs: v0.7 brief + roadmap update — fixture-gap closure for public release** ([44a5be0](https://github.com/noonghunna/benchlocal-cli/commit/44a5be06cfff69b8d16ff9b70605413b743a9faa))


v0.6 verifier infrastructure works but the upstream fixture trees aren't
in vendor/, so CLI hits a 12% floor (workspace inputs missing), BugFind
uses rubric heuristics instead of pytest, Hermes uses keyword-match
instead of flow simulation. v0.7 lifts the fixtures + wires real
verification.

Phases:
- A: sync vendor fixture trees from upstream (most uncertain — could be
  quick or could require manual lift)
- B: BugFind real pytest against lifted fixtures
- C: CLI workspace-input + expected-output comparison
- D: Hermes flow-driven multi-turn with browser/cron fixture mocks
- E: docs + validation + 0.7.0 version bump

Acceptance gate gates public release: real-model A/B must show
meaningful discrimination on each sandboxed pack (>40% on cli-40,
stable non-trivial ranges on bugfind/hermes).

Roadmap also captures v0.6.1 patches as shipped (c5e1dbd) and
demotes diagnostic tooling to v0.8.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>



### 🛠️ Tooling + CI

- **ci: comment out [remote.github] in cliff.toml until repo flips public** ([470d6b1](https://github.com/noonghunna/benchlocal-cli/commit/470d6b1751503c11adf6b50e1008ddc31944e9c9))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.7.0`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.6.0...v0.7.0)
## v0.6.0 — 2026-05-09


### ✨ Features

- **feat(sandboxes): replace shape checks with v0.6 verifiers** ([22466ff](https://github.com/noonghunna/benchlocal-cli/commit/22466ff95e1742dca0d50191d7be6d177eaa1b16))
- **feat(packs): add raw sandbox scenario metadata** ([749eacb](https://github.com/noonghunna/benchlocal-cli/commit/749eacb1ddba9b34427ef375ed75ec756fb0891e))


### 📝 Documentation

- **docs: report v0.6 verifier lift** ([b3005f4](https://github.com/noonghunna/benchlocal-cli/commit/b3005f4037043e428d0c9350280b8cddb9fdbded))
- **docs: ROADMAP.md + v0.5-deltas section in v0.6 brief** ([7337cd7](https://github.com/noonghunna/benchlocal-cli/commit/7337cd753cb4f9bf2d41fbfb3eb9a16b7a91d53b))


ROADMAP.md captures the full pipeline:
- v0.6 = verifier parity (in flight, brief at CODEX_BRIEF_V6.md)
- v0.6.1 = ReasonMath value-matcher + migration notes (~4-6 hr)
- v0.7 = diagnostic tooling (~8-10 hr)
- parking lot = side-by-side, drift detection, mock library, CI gate

CODEX_BRIEF_V6.md amended with "What changed between v0.4 and v0.6"
section so Codex doesn't accidentally undo the v0.5 UX patches:
mode reshuffle, --full default-sandboxed, loud failures, URL norm,
ReasonMath prompt patch, /health stage labels, version bump.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

- **docs: v0.6 brief — real verifier parity for sandboxed packs** ([7d737ba](https://github.com/noonghunna/benchlocal-cli/commit/7d737ba2192f0a07feba1d742a801504856bde4c))


Replaces v0.4 shape-check verifiers (BugFind solution-block pattern,
CLI shell-parse+safety, Hermes any-non-empty-response) with real
upstream-fidelity verification:

- Phase A: BugFind real pytest against lifted fixtures
- Phase B: CLI real subprocess exec in --network none workspace
  (UDS for /verify transport — Docker port-publishing + --network
  none can't coexist; CLI sandbox needs the isolation)
- Phase C: Hermes multi-turn agent loop with deterministic
  mocked-tool simulation (browser, cron, memory, artifact, trace)
- Phase D: docs + validation + reconcile 110→150 scenario count

Total estimate: 14-20 hr. Sandbox infrastructure from v0.4 unchanged
(HTTP protocol, SandboxClient lifecycle, runner dispatch all stable);
v0.6 swaps the verifier implementations behind those interfaces.

Filed upstream-tracking issue stevibe/ReasonMath-15#2 covers value-
centric verifier matching for the in-process ReasonMath pack — that
work is out of v0.6 scope (in-process scoring layer, not sandbox).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>



### 🛠️ Tooling + CI

- **ci: drop git-cliff --github-repo flag (private repo can't access GitHub API)** ([06c8ef6](https://github.com/noonghunna/benchlocal-cli/commit/06c8ef61f446ffd812ef4f45c8bb77049b7612b8))


### 🧪 Tests

- **test(sandboxes): cover v0.6 verifier paths** ([b45519f](https://github.com/noonghunna/benchlocal-cli/commit/b45519f9375d5ee07433aeb8cac03eadbbf291fe))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.6.0`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.5.0...v0.6.0)
## v0.5.0 — 2026-05-09


### ✨ Features

- **v0.5: --full enables sandboxed by default + reasonmath prompt fix** ([eb7ddb0](https://github.com/noonghunna/benchlocal-cli/commit/eb7ddb0b1308530f564bad08722b2e0a5873199a))


UX changes (cli.py + runner.py):
- Mode reshuffle: reasonmath-15 promoted from --full to --medium.
  --quick (2 packs, no Docker) / --medium (5 packs, no Docker, all
  deterministic) / --full (8 packs, requires Docker).
- --full now defaults to enabling sandboxed packs (no --enable-sandboxed-packs
  flag needed). New --no-sandboxed-packs opt-out for users who want the
  --medium scope explicitly. Old --enable-sandboxed-packs kept as a no-op
  for backwards compat.
- Loud sandbox bring-up failures: ⚠️ stderr line at the moment a sandbox
  fails to start, with actionable hint ("ensure Docker is running and
  bash tools/build-sandboxes.sh has been run; or use --medium").
- Endpoint URL normalization: accept http://host:port, http://host:port/v1,
  or http://host:port/v1/chat/completions. Previously `/v1` suffix produced
  a doubled `/v1/v1/chat/completions` 404.
- Improved --help with mode descriptions, examples, and per-flag help text.

ReasonMath prompt fix (vendor/ReasonMath-15/lib/benchmark.ts):
- Old prompt: "If the question asks for more than one value, format as
  semicolon-separated key=value pairs."
- New prompt: "Always format the final answer as key=value pairs (semicolon-
  separated when multiple values)."
- Models were following the old prompt literally (single-value answers
  emitted as `ANSWER: 35.98` instead of `ANSWER: per_person=$35.98`).
  Verifier expects key=value for single-value too.
- Synthetic example values used in the prompt — verified to not match any
  expected answer in the test set.
- Filed upstream as stevibe/ReasonMath-15#1.

Sandbox /health labels (server.py × 3):
- Was: "stage":"scaffold" (stale, contradicted v0.4 implementation)
- Now: "stage":"v0.4-shape-check" (honest about deterministic-shape-check
  scope; full upstream fixture parity queued for v0.6).
- Module docstrings cleaned up to drop "🚧 SCAFFOLDING ONLY — STUB" lines.

Validation: 14/14 pytest pass; tools/build-sandboxes.sh + tools/test-sandboxes.sh
both green; URL normalization unit-checked across all 5 endpoint shapes.

Bumps benchlocal-cli 0.0.1 → 0.5.0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

- **feat(hermes): implement sandbox verifier endpoint** ([34e6388](https://github.com/noonghunna/benchlocal-cli/commit/34e63880d7f6f3aefd5769088fc01ecd87b38550))
- **feat(cli): implement sandbox verifier endpoint** ([c9cac81](https://github.com/noonghunna/benchlocal-cli/commit/c9cac8171054044288b8c53d90b616935d7631a6))
- **feat(bugfind): implement sandbox verifier endpoint** ([b68af8f](https://github.com/noonghunna/benchlocal-cli/commit/b68af8f1659841b77ae34a262720badc2358b3ed))
- **feat(sandbox): integrate HTTP verifier clients** ([9a6f3f9](https://github.com/noonghunna/benchlocal-cli/commit/9a6f3f94bf8b20e4c084c602343cade0cd99f0a5))
- **feat: add reasoning-aware runner flags** ([3f7c4ec](https://github.com/noonghunna/benchlocal-cli/commit/3f7c4ec405fbb734a52e8126d2acdbfa8928ad70))
- **feat(packs): default generated packs to thinking off** ([f8187ad](https://github.com/noonghunna/benchlocal-cli/commit/f8187ad0533d0cc4c607d5cb9918ca7ea507dff3))
- **feat(extractor): preserve ToolCall reference date metadata** ([11ea4ec](https://github.com/noonghunna/benchlocal-cli/commit/11ea4ec5e4c1eb9cff3f2a7d4fed07ad7f07cd1d))
- **feat(packs): regenerate JSONL from vendor sources** ([36a7c18](https://github.com/noonghunna/benchlocal-cli/commit/36a7c18bac67e4f48c845a2fc117bd71c1adc504))
- **feat(packs): support extractor-generated assertions** ([093d0e0](https://github.com/noonghunna/benchlocal-cli/commit/093d0e0850cdc057d41aad86d9fbaa8eed83bc65))
- **feat(extractor): add Node build-packs generator** ([d682dcc](https://github.com/noonghunna/benchlocal-cli/commit/d682dccd12939c45de553b9f82d52d96bbd83d62))
- **feat(vendor): scaffold vendor mirrors and sync script** ([93a299c](https://github.com/noonghunna/benchlocal-cli/commit/93a299c63641e809c94cc7210b6bd20d52320a95))
- **feat: v0.1 implementation complete; see docs/CODEX_REPORT.md** ([928291f](https://github.com/noonghunna/benchlocal-cli/commit/928291f09ca0e0a4135cafe86aa20a6e893ad867))
- **feat: vendor BenchLocal JSONL packs** ([689d907](https://github.com/noonghunna/benchlocal-cli/commit/689d9071805e9b4842d2ffb202598ea342da829d))
- **feat: implement deterministic scorers** ([276e70b](https://github.com/noonghunna/benchlocal-cli/commit/276e70b26de812155d7a2536286337452cb36fdd))
- **feat: implement core runner and CLI** ([14de749](https://github.com/noonghunna/benchlocal-cli/commit/14de74988357e6e58fda935a1286a5e146574f7b))


### 🐛 Bug fixes

- **fix: apply thinking token budget to scenario requests** ([7d79840](https://github.com/noonghunna/benchlocal-cli/commit/7d798409bece2fc03265189db0d30bc335784cd0))


### 📝 Documentation

- **docs: report v0.4 sandbox implementation** ([6a2656e](https://github.com/noonghunna/benchlocal-cli/commit/6a2656e16536d7e667f9def4285c1105b8c7e095))
- **docs: v0.4 brief — unified sandbox infrastructure (BugFind + CLI + HermesAgent)** ([e5bb8ff](https://github.com/noonghunna/benchlocal-cli/commit/e5bb8ffbf7bbd3f8bd3542f7b6259a139036d1d6))


Single brief covering all 3 sandboxed packs with shared HTTP verifier
protocol, container-per-pack architecture, and mocked-tools approach
for HermesAgent (deterministic > realistic for benchmarking).

5 phases (~10-14 hr total):
- Phase A: SandboxClient + runner integration
- Phase B: BugFind sandbox (Python + pytest)
- Phase C: CLI sandbox (debian:slim + bash exec)
- Phase D: HermesAgent sandbox (mocked browser/cron/memory/artifact/trace + agent loop)
- Phase E: Documentation + validation

After v0.4 ships, --full mode covers all 8 packs (110 scenarios) — true
canonical coverage matching BenchLocal upstream's full catalog.

Hand off to Codex when usage limits reset.

- **docs: report v0.3 reasoning-model handling** ([60eb461](https://github.com/noonghunna/benchlocal-cli/commit/60eb46177851c9f801d3a59963e130de7bfba479))
- **docs: clarify thinking token budget behavior** ([f399bf8](https://github.com/noonghunna/benchlocal-cli/commit/f399bf8dca93f5ba3c6a171b6ef3523bce4e8d03))
- **docs: document reasoning-model defaults** ([f7544d2](https://github.com/noonghunna/benchlocal-cli/commit/f7544d260c5bc1086965157c9874936d7b7ed0f8))
- **docs: add v0.3 brief — reasoning-model handling (default thinking=off + --enable-thinking flag + reasoning_content reader)** ([a0ca3a5](https://github.com/noonghunna/benchlocal-cli/commit/a0ca3a554fffbf4914bcd46aeab78fc1aa63010a))
- **docs: report v0.2 vendor extractor completion** ([9ce2b52](https://github.com/noonghunna/benchlocal-cli/commit/9ce2b529dcab5c55ab0df76c6ab27f4c5c966527))
- **docs: document v0.2 vendor sync workflow** ([812a715](https://github.com/noonghunna/benchlocal-cli/commit/812a7158f20dcb5de5a784fce6c6864c3c182eaa))
- **docs: add v0.2 brief — vendor/ + Node extractor for verbatim upstream fidelity + future-proof re-sync** ([23e9158](https://github.com/noonghunna/benchlocal-cli/commit/23e915803a9e7c274cb76cd9810258975d2256d4))
- **docs: add async report-back protocol for Codex handoff (questions / completion / report template)** ([62a4dcf](https://github.com/noonghunna/benchlocal-cli/commit/62a4dcf860d039538738db3f299b434f297ab9ef))


### 🚧 Scaffolding

- **scaffolding(v0.4): build + smoke-test sandbox containers (Codex implementation pending)** ([5fd35ef](https://github.com/noonghunna/benchlocal-cli/commit/5fd35ef5f0915bcfbfd00342621839553f0f475a))


Pre-build the v0.4 sandbox infrastructure so Codex starts from a working
baseline rather than designing structure + implementing verifiers from
scratch. Saves Codex context budget; lets us validate the architecture.

What's scaffolded (16 files):
- sandboxes/{bugfind,cli,hermes}/ — Dockerfile + server.py + README + fixtures/
  Each Dockerfile builds cleanly (172-208 MB images).
  Each server.py hosts /health (200 OK) and stub /verify (returns
  verifier_not_implemented per the failure-mode taxonomy).
  Hermes stub also handles the 3 multi-turn endpoints (start/turn/end).
- benchlocal_cli/sandbox.py — SandboxClient class + SandboxConfig dataclass +
  SANDBOX_REGISTRY for the 3 packs. Methods raise NotImplementedError;
  Codex Phase A wires them up.
- tools/build-sandboxes.sh — `docker build` all 3, or one named pack
- tools/test-sandboxes.sh — smoke-test /health on each container, confirm
  clean shutdown
- docs/SANDBOX_PROTOCOL.md — HTTP verifier protocol spec covering single-turn
  (BugFind, CLI) + multi-turn (HermesAgent) request/response shapes

Validation:
- `bash tools/build-sandboxes.sh` → builds 3 images cleanly
- `bash tools/test-sandboxes.sh` → all 3 /health endpoints respond, clean stop
- `python -c "from benchlocal_cli.sandbox import SANDBOX_REGISTRY"` → imports OK
- Image sizes: bugfind 208MB, cli 172MB, hermes 177MB (lean, no Chromium)

What Codex still needs to do (per CODEX_BRIEF_V4.md):
- Phase A: implement SandboxClient.start/stop/verify + Runner integration
- Phase B: real BugFind /verify (pytest harness)
- Phase C: real CLI /verify (subprocess + sandbox)
- Phase D: real Hermes /verify-{start,turn,end} (multi-turn agent loop + 5 mocked tools)
- Phase E: docs + final validation



### 🛠️ Tooling + CI

- **ci: add git-cliff release notes (SemVer)** ([d16eb60](https://github.com/noonghunna/benchlocal-cli/commit/d16eb606901ca6a88442bb8b28b27343d59db473))


Same setup as club-3090 but SemVer instead of CalVer (this is a real
package with a version field; SemVer fits naturally).

Workflow fires on `vMAJOR.MINOR.PATCH` tag push, runs git-cliff to
categorize commits since the last tag by conventional-commit prefix
(feat, fix, docs, test, ci, refactor, chore, scaffolding), creates
GitHub Release with the rendered body.

Tag cadence: at each minor/patch ship.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>



### 🧪 Tests

- **test: cover sandbox runner dispatch** ([e3c2597](https://github.com/noonghunna/benchlocal-cli/commit/e3c2597ff261139d639c0e2b78c6cc2aa8158ada))
- **test: cover reasoning request handling** ([9e82a7c](https://github.com/noonghunna/benchlocal-cli/commit/9e82a7c85b84719f7e113418c760fc1928d15c2e))


### 🧹 Other

- **Initial scaffolding for benchlocal-cli (Codex implementation pending)** ([3546c1f](https://github.com/noonghunna/benchlocal-cli/commit/3546c1fd68c3dc352f8ff4a350d05c9e2e2df049))


Pre-alpha CLI port of BenchLocal quality bench packs. Architecture decisions
documented in docs/DESIGN.md (sanity-checked via Codex on 2026-05-09);
implementation handoff at CODEX_BRIEF.md.

Layout:
- benchlocal_cli/{cli,runner}.py — entry point + orchestrator (NotImplementedError stubs)
- benchlocal_cli/scoring/{tool_call,instruct_follow,struct_output,reason_math,data_extract}.py — verifier stubs
- benchlocal_cli/scoring/_stub.py — implemented (returns verifier_not_implemented)
- benchlocal_cli/packs/<pack-id>.jsonl — 8 stub pack files (metadata-only)
- tests/test_scoring_smoke.py — confirms imports + stub behavior

Phase 1 (Codex): runtime + CLI argument parsing + ScenarioResult dataclass
Phase 2 (Codex): port 5 deterministic packs from upstream + implement verifiers
Phase 3 (Codex): smoke test against mock endpoint + final validation



### 🧹 Refactoring + maintenance

- **refactor: rename scripts/ → tools/ to signal maintainer-only tooling** ([b29ef5e](https://github.com/noonghunna/benchlocal-cli/commit/b29ef5eebe914a5cc4d19513b8524bc4fcf7f47e))


Per Python ecosystem convention: scripts/ is for installable CLI entry
points (which benchlocal-cli already exposes via pyproject.toml's
[project.scripts] table — the user-facing 'benchlocal-cli' command).
tools/ is the conventional home for dev/maintainer scripts.

The sync + extractor tooling is exclusively for re-syncing vendor/
TS sources with upstream BenchLocal and regenerating the JSONL pack
data. End users 'pip install benchlocal-cli' and never touch these.

References updated in README, ATTRIBUTION, CONTRIBUTING, CODEX_BRIEF_V1,
CODEX_BRIEF_V2, EXTRACTOR_NOTES, CODEX_REPORT, and the scripts
themselves. INTEGRATION.md references to scripts/* are intentionally
unchanged — those refer to the parent project (club-3090) consuming
this CLI, not to benchlocal-cli's own dev tooling.




[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.5.0`]

