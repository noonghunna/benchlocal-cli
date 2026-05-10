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

### v0.7.1 — runner-side multi-turn delegation ⭐ unblocks public flip

**Brief:** [`CODEX_BRIEF_V7_1.md`](CODEX_BRIEF_V7_1.md) (~7-11 hr Codex chunk).

v0.7 candidate exposed two pack classes stuck at 0% because the runner only sends one chat completion per scenario:
- cli-40 multi-round (15/40 scenarios) — runner doesn't loop on tool calls
- hermesagent-20 (20 scenarios) — same gap, falls back to v0.6 single-turn shape-check

The HTTP protocol for multi-turn is already defined (`/verify-start` / `/verify-turn` / `/verify-end`); the Hermes sandbox already implements it. v0.7.1 wires the runner to actually loop on `next-prompt` responses + adds the same endpoints to the CLI sandbox. After v0.7.1: public flip is unblocked.

### v0.7.x — ReasonMath + migration docs

- **ReasonMath value-centric verifier.** In-process scoring path (separate from sandbox layer). Implements value-match assertion type per [stevibe/ReasonMath-15#2](https://github.com/stevibe/ReasonMath-15/issues/2). Path depends on upstream response:
  - If stevibe accepts the proposal → align with upstream
  - If declined → override locally in `benchlocal_cli/scoring/value_match.py`, document as intentional divergence
- **CHANGELOG + migration notes for v0.6/v0.7 score drops.** Document that v0.4 numbers measured shape, v0.6 added structure, v0.7 measures correctness.

## Diagnostic tooling — v0.8 ⭐ (promoted before further evals)

Brief TBD. Lands after v0.7 + ReasonMath follow-up. ~8-10 hr Codex chunk. **Now ranked higher than expanding the eval surface** — better tooling makes the existing 8 packs more useful before adding new evals. Surface area:

- **`--previous-result PATH`** — compare runs, emit delta column. Was in the v0.1 design notes but never implemented. Catches regressions on patch bumps.
- **Result inspection subcommand** — `benchlocal-cli inspect <result.json> --scenario RM-01` shows model response + verifier reasoning + trace. Avoids manual JSON grepping.
- **Trend tracking** — historical scores per (model, compose) tuple. Flat-file aggregator or "results catalog" doc that quality-test.sh appends to.

## Optional expansion (if/when needed) — v0.9+

BenchLocal stays the primary 30-45 min local quality gate. These are *complementary* additions — promote when the underlying need is real (typically: cross-rig comparisons that need depth on a specific axis, or new model classes that BenchLocal's surface doesn't exercise).

Recommended order if we expand:

1. **lm-eval-harness calibration slice** — tiny subset (IFEval / GSM8K / MMLU / HellaSwag, ~50 prompts each) as a sanity sidecar. Tells us if a quant or config change broadly damaged model quality before we trust BenchLocal scores. Not a replacement; a calibration anchor.
2. **BFCL-lite for tool-calling depth** — BenchLocal's `toolcall-15` is intentionally shallow. BFCL's nested-call / parallel-call / multi-step scenarios add real depth when we need to compare function-calling fidelity across quants.
3. **Mirror HermesAgent into Inspect AI** — strongest "maybe we should have started here" point. v0.7's HermesAgent exposes a real architecture gap: the upstream agent runner owns the model loop, but our SandboxClient sends one assistant response per call. Inspect AI provides the framework primitives (multi-turn loop, tool simulation, trace verification) we'd otherwise re-invent. If serious multi-turn agent eval becomes a project priority, port HermesAgent there rather than deepening our custom Hermes runner.

### Mode naming for the expanded suite

When the expansion lands, the CLI mode taxonomy grows like this:

```
--quick       2 packs   30 scenarios   ~5-10 min    smoke
--medium      5 packs   75 scenarios   ~15-25 min   deterministic only (no Docker)
--full        8 packs   150 scenarios  ~25-40 min   sandboxed — all BenchLocal scenarios (today's --full, scope unchanged)
--audit       8 + lm-eval calibration + BFCL-lite   ~50-90 min   release-gate / external sanity
```

Why `--audit` (not `--full+` or `--everything`):

- `--full` keeps a stable, predictable scope (all BenchLocal packs) — users who scripted `--full` today retain their 25-40 min mental model when expansion lands
- `--audit` is semantically distinct: external calibration layered on top, not just "more BenchLocal." Implies cross-checking against established academic benchmarks (lm-eval slice) plus depth (BFCL)
- Tier reads naturally: smoke → deterministic → sandboxed → audit
- The `+` / `everything` / `--full2` patterns all collide semantically with `--full` since "full" already means "everything"

Inspect AI's HermesAgent port replaces (not adds to) the existing `hermesagent-20` slot — same scenarios, better framework. So `--full` retains 8 packs / 150 scenarios after that swap; only `--audit` grows scenario count.

### Tools we evaluated and *don't* rank for inclusion

- **promptfoo** — useful for orchestration / regression diffs, but doesn't solve the verifier-maturity problem (which is where BenchLocal's value is)
- **OpenAI simple-evals** — good reference code, but deprecated as a maintained source; use for inspiration only
- **HumanEval / HumanEval+** — covered by BenchLocal's BugFind-15 effectively
- **MT-Bench / Arena-Hard** — requires a strong judge model, not deterministic, defeats the local-only premise

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
