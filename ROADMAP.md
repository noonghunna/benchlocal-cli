# benchlocal-cli — roadmap

Tracking-doc for upcoming work. Use this to find the right brief / issue, see what's in flight, and decide if a new ask fits an existing tier or warrants a new one.

## Now — v0.5 (shipped 2026-05-09)

Released: [v0.5.0](https://github.com/noonghunna/benchlocal-cli/releases/tag/v0.5.0). Highlights:

- Mode reshuffle: `--quick` (2 packs) / `--medium` (5 packs, all deterministic) / `--full` (8 packs incl. sandboxed, requires Docker).
- `--full` defaults sandboxed-on; `--no-sandboxed-packs` opts out.
- Loud sandbox failures + endpoint URL normalization + `--list-packs` + improved help.
- ReasonMath prompt fix (vendor patch — see [stevibe/ReasonMath-15#1](https://github.com/stevibe/ReasonMath-15/issues/1)).
- Sandbox `/health` stage labels + module docstrings cleaned.
- git-cliff release-notes infra in place.

## Next — v0.6: real verifier parity

**Brief:** [`CODEX_BRIEF_V6.md`](CODEX_BRIEF_V6.md) (Codex hand-off, ~14-20 hr).

Replace v0.4 shape-check verifiers with real upstream-fidelity verification:

- **BugFind:** real pytest against lifted upstream fixtures
- **CLI:** real subprocess exec in `--network none` workspace (UDS for `/verify` transport)
- **Hermes:** multi-turn agent loop with deterministic mocked-tool simulation (browser, cron, memory, artifact, trace)

**Expected outcome:** evaluation quality goes up; raw scores will drop because today's shape-checks are inflated. See `CODEX_BRIEF_V6.md` for the score-drop estimate. Closing this gate is the prerequisite for flipping the repo public — at v0.6 the CLI is genuinely useful for outsiders.

## v0.6.1 — sandbox patches (shipped 2026-05-09)

Commit [`c5e1dbd`](https://github.com/noonghunna/benchlocal-cli/commit/c5e1dbd). Real-model A/B against Qwen3.6-27B exposed three v0.6 verifier defects, all patched:

- All sandboxes: `do_POST` wraps verifier in try/except (was disconnecting on uncaught exceptions)
- CLI: FileNotFoundError + PermissionError caught in `_run_command` (was crashing verifier)
- CLI: compound shell syntax routed through `bash -c` with raw-string forbidden-token check (was rejecting `cmd1 && cmd2` outright)
- CLI: multi-line fenced code blocks extracted in full (was first-line-only)

Net: cli-40 went 0/40 → 5/40 on Qwen (still hitting fixture-gap floor; v0.7 lifts that).

## v0.7 — upstream verifier-runtime lift (candidate, 2026-05-10)

**Brief:** [`CODEX_BRIEF_V7.md`](CODEX_BRIEF_V7.md).

v0.6 verifiers work but the upstream fixture trees weren't in `vendor/` — so CLI verifier hit a 12% floor, BugFind used rubric heuristics, and Hermes used keyword-match on final answers instead of flow simulation.

v0.7 candidate closes the available part of this gap by syncing upstream `verification/` runtimes:
- BugFind now delegates to upstream `verifyAnswer`
- CLI now delegates to upstream one-shot and replay verifiers
- Hermes upstream runtime is vendored, but full parity remains runner-level work because the upstream runtime owns the full model/agent loop

**Acceptance gate still pending** (gates public release): mock validation and real-model A/B on Qwen + Gemma must show meaningful discrimination on each sandboxed pack (>40% on cli-40, stable in non-trivial range on bugfind/hermes).

## After v0.7 — v0.7.x follow-ups

- **ReasonMath value-centric verifier.** In-process scoring path (separate from sandbox layer). Implements value-match assertion type per [stevibe/ReasonMath-15#2](https://github.com/stevibe/ReasonMath-15/issues/2). Path depends on upstream response:
  - If stevibe accepts the proposal → align with upstream
  - If declined → override locally in `benchlocal_cli/scoring/value_match.py`, document as intentional divergence
- **CHANGELOG + migration notes for v0.6/v0.7 score drops.** Document that v0.4 numbers measured shape, v0.6 added structure, v0.7 measures correctness.

## Diagnostic tooling — v0.8

Brief TBD. Lands after v0.7 + ReasonMath follow-up. ~8-10 hr Codex chunk. Surface area:

- **`--previous-result PATH`** — compare runs, emit delta column. Was in the v0.1 design notes but never implemented. Catches regressions on patch bumps.
- **Result inspection subcommand** — `benchlocal-cli inspect <result.json> --scenario RM-01` shows model response + verifier reasoning + trace. Avoids manual JSON grepping.
- **Trend tracking** — historical scores per (model, compose) tuple. Flat-file aggregator or "results catalog" doc that quality-test.sh appends to.

## Parking lot — when needed

Worth doing eventually but not urgent. Promote to a versioned milestone when there's a concrete trigger:

- **Cross-model side-by-side** — run same pack against N endpoints in one invocation, output side-by-side. Useful when comparing quants or composes.
- **Pack version drift detection** — alert when our vendored packs lag upstream.
- **Mock fixture library** — curated mocks for testing prompt/verifier changes without GPU time.
- **CI integration in club-3090** — wire `quality-test.sh` into the canonical `verify-full → bench → quality-test → soak-test` pipeline as an enforced gate.

## Out of scope (upstream territory)

- Adding new packs to the BenchLocal source repos. Our role is the CLI port; new packs come from upstream.
- Verifier algorithm changes that should land upstream, not in our scoring layer. Examples: ReasonMath value-centric matching ([stevibe/ReasonMath-15#2](https://github.com/stevibe/ReasonMath-15/issues/2)) — file the issue, propose the fix, only override locally if upstream declines.
- Token-budget tuning per-scenario for inference-heavy reasoning chains (RM-04, RM-06 truncation) — methodology question for upstream maintainers.

---

**How to use this doc:**

- New idea? Check if it fits a tier above. If yes, append a bullet to that tier with a one-line rationale. If no, add to the parking lot.
- Promoting parking-lot to versioned: pick the next minor (v0.7, v0.8…) and write a brief.
- Briefs go in repo root as `CODEX_BRIEF_V<N>.md` to keep them discoverable next to this doc.
