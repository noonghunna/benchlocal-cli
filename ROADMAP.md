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


Replace v0.4 shape-check verifiers with real upstream-fidelity verification:

- **BugFind:** real pytest against lifted upstream fixtures
- **CLI:** real subprocess exec in `--network none` workspace (UDS for `/verify` transport)
- **Hermes:** multi-turn agent loop with deterministic mocked-tool simulation (browser, cron, memory, artifact, trace)

**Expected outcome:** evaluation quality goes up; raw scores will drop because today's shape-checks are inflated. Closing this gate is the prerequisite for flipping the repo public — at v0.6 the CLI is genuinely useful for outsiders.

## v0.6.1 — sandbox patches (shipped 2026-05-09)

Commit [`c5e1dbd`](https://github.com/noonghunna/benchlocal-cli/commit/c5e1dbd). Real-model A/B against Qwen3.6-27B exposed three v0.6 verifier defects, all patched:

- All sandboxes: `do_POST` wraps verifier in try/except (was disconnecting on uncaught exceptions)
- CLI: FileNotFoundError + PermissionError caught in `_run_command` (was crashing verifier)
- CLI: compound shell syntax routed through `bash -c` with raw-string forbidden-token check (was rejecting `cmd1 && cmd2` outright)
- CLI: multi-line fenced code blocks extracted in full (was first-line-only)

Net: cli-40 went 0/40 → 5/40 on Qwen (still hitting fixture-gap floor; v0.7 lifts that).

## v0.7 — upstream verifier-runtime lift (candidate, 2026-05-10)


v0.6 verifiers work but the upstream fixture trees weren't in `vendor/` — so CLI verifier hit a 12% floor, BugFind used rubric heuristics, and Hermes used keyword-match on final answers instead of flow simulation.

v0.7 candidate closes the available part of this gap by syncing upstream `verification/` runtimes:
- BugFind now delegates to upstream `verifyAnswer`
- CLI now delegates to upstream one-shot and replay verifiers
- Hermes upstream runtime is vendored, but full parity remains runner-level work because the upstream runtime owns the full model/agent loop

**Acceptance gate still pending** (gates public release): mock validation and real-model A/B on Qwen + Gemma must show meaningful discrimination on each sandboxed pack (>40% on cli-40, stable in non-trivial range on bugfind/hermes).

## v0.7.1 — runner-side multi-turn delegation (candidate, 2026-05-10)


v0.7 candidate exposed two pack classes stuck at 0% because the runner only sends one chat completion per scenario:
- cli-40 multi-round (15/40 scenarios) — runner doesn't loop on tool calls
- hermesagent-20 (20 scenarios) — same gap, falls back to v0.6 single-turn shape-check

The HTTP protocol for multi-turn is now wired through the runner. CLI-40 multi-round scenarios route through `/verify-start` / `/verify-turn` / `/verify-end`, and HermesAgent-20 uses the same runner loop. Public flip still needs real-model A/B acceptance on Qwen + Gemma.

## v0.7.2 — post-run forensics (shipped 2026-05-10)

[Release v0.7.2](https://github.com/noonghunna/benchlocal-cli/releases/tag/v0.7.2). Added `verifier_trace` (full upstream payload preserved per scenario), `conversation` (full multi-turn message history), and `--sandbox-log-dir` (captures `docker logs` to disk before container teardown). Failed scenarios can now be diagnosed entirely from the saved JSON + sandbox.log without re-running. Used today's Qwen Hermes diagnostic to identify 5 distinct failure patterns (refusal / casual summary / no-tool-use / tool-set mismatch / lucky pass).

## v0.7.3 — Hermes upstream-runtime delegation (planned, ~5-8 hr)

Closes Codex's flagged Phase D gap from v0.7 candidate report: HermesAgent grading is currently keyword-evidence on the final assistant message, not real upstream `agent-runner.py` evaluation.

The upstream runtime is already vendored in `vendor/HermesAgent-20/verification/` (Codex sync'd it during v0.7). v0.7.3 wires our hermes sandbox to delegate grading to it via subprocess+python — same pattern as v0.7's BugFind / CLI upstream-runtime delegation.

Closes:
- Pattern A (model refusals) — upstream prompts already nudge agent role
- Pattern B (casual summary keyword-misses) — upstream grades trace + state, not final-message vocabulary
- Pattern C (model doesn't use tools) — upstream's full tool catalog (read/list/glob/exec/browser/cron/send_message) is in scope
- Pattern D (tool-set mismatch) — upstream tool simulator matches what scenarios expect
- Verifier strictness (Pattern E lucky-pass artifact) — replaced by real grading

Today's Qwen 25% / Gemma 20% on Hermes is keyword-evidence floor. v0.7.3 likely produces 40-65% with real cross-model discrimination — bench becomes useful as a "did the model actually solve the agent task" signal.

After v0.7.3, all 3 sandboxed packs (BugFind / CLI / Hermes) use upstream runtimes for grading. v0.7's "real verifier parity" vision is fully closed.

## After v0.7.x — ReasonMath + migration docs

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

Integration pattern: **delegate to upstream**. BenchLocal packs (BugFind/CLI/Hermes) call into upstream JS/Python runtimes via subprocess. The same pattern applies to any third-party bench we add — they all ship their own runners. We don't reimplement grading in our own framework.

Recommended order if we expand:

1. **lm-eval-harness calibration slice** — tiny subset (**IFEval + GSM8K only**, ~50 prompts each, ~10 min) as a sanity sidecar. Tells us if a quant or config change broadly damaged model quality before we trust BenchLocal scores. Delegate to `EleutherAI/lm-evaluation-harness` upstream. Not a replacement; a calibration anchor. **Skipping MMLU / HellaSwag / ARC / TruthfulQA** — saturated for any modern model class, low signal at our scale.
2. **BFCL-lite for tool-calling depth** — BenchLocal's `toolcall-15` is intentionally shallow. BFCL's nested-call / parallel-call / multi-step / irrelevance-detection scenarios add real depth when we need to compare function-calling fidelity across quants. Delegate to `gorilla-llm/gorilla` upstream runner.
3. **Aider Polyglot lite for multi-language code editing** — covers C++ / Go / Java / JS / Python / Rust + edit-format reliability. **Closer to "will this model behave inside an editor"** than HumanEval+'s Python-only spec-to-code. The right primary code-generation slot for an IDE-agent quality bench. Delegate to `Aider-AI/aider` upstream's polyglot benchmark runner. ~10-15 min for a 30-50 case lite slice.
4. **IDE-agent safety slice** (custom, ~10-20 scenarios, ~5 min) — NOT WMDP-style safety. Concrete IDE-agent failure modes: does the model refuse `rm -rf` without confirmation? Leak `.env` / secrets when asked to share config? Obey malicious README instructions or comments inside source files (prompt injection through files)? Run `curl | bash` suggestions blindly? This axis is unique to local coding-agent deployment and isn't covered by any existing bench. We'd author it ourselves; this is the one slot where Inspect AI's framework primitives could earn their keep (custom scenarios, no upstream runner).

Other benches considered:
- **HumanEval+ / MBPP** — Python-only spec-to-code, saturated for modern models, prone to overfit. Keep as optional `--legacy-codegen` cheap compatibility anchor only; not a primary slot. Aider Polyglot replaces it as the code-gen tier-1 pick.
- **LiveCodeBench** — contamination-resistant algorithmic coding. Different motivation than Aider Polyglot (algorithmic problem-solving vs editor realism). Add only if we have a concrete need to differentiate models on contamination resistance.
- **SWE-bench Verified / Lite** — high-signal real software engineering. Cost is high (5-30 min per scenario). Stays in `--swe` power-user tier, not in `--audit`. Use `mini-SWE-agent` as the runner.
- **tau-bench / AgentBench** — no concrete need; would follow delegate-to-upstream pattern when added.

### Mode naming for the expanded suite

When the expansion lands, the CLI mode taxonomy grows like this:

```
--quick       2 packs   30 scenarios   ~5-10 min    smoke
--medium      5 packs   75 scenarios   ~15-25 min   deterministic only (no Docker)
--full        8 packs   150 scenarios  ~25-40 min   sandboxed — all BenchLocal scenarios (today's --full, scope unchanged)
--audit       full + lm-eval (IFEval+GSM8K) + BFCL-lite + Aider Polyglot lite + agent-safety
              ~55-75 min   release-gate / external sanity / multi-language editor + agent safety
--swe         SWE-bench-lite (10-20 cases via mini-SWE-agent)   30-60 min   power-user repo-scale
```

Why `--audit` (not `--full+` or `--everything`):

- `--full` keeps a stable, predictable scope (all BenchLocal packs) — users who scripted `--full` today retain their 25-40 min mental model when expansion lands
- `--audit` is semantically distinct: external calibration layered on top, not just "more BenchLocal." Implies cross-checking against established academic benchmarks (lm-eval slice) plus depth (BFCL)
- Tier reads naturally: smoke → deterministic → sandboxed → audit
- The `+` / `everything` / `--full2` patterns all collide semantically with `--full` since "full" already means "everything"

### Implementation pattern — how each new bench plugs in

Every external bench follows the same shape as today's BugFind/CLI/Hermes — Docker sandbox + HTTP verifier + delegate-to-upstream. The runner doesn't special-case any pack; adding a new bench is the same shape as adding a new BenchLocal pack.

**Three layers of CLI surface:**

```
Layer 1 — mode flags (top-level presets):
  --quick    2 packs                                            ~10 min
  --medium   5 deterministic packs                              ~25 min   (no Docker)
  --full     8 packs (current scope, unchanged)                 ~30-40 min
  --audit    --full + lm-eval + BFCL + Aider + ide-safety       ~55-75 min
  --swe      SWE-bench-lite via mini-SWE-agent (heavy)          30-60 min

Layer 2 — composable additive flags (power-user):
  --with-lm-eval        IFEval + GSM8K calibration
  --with-bfcl           BFCL-lite tool-call depth
  --with-aider          Aider Polyglot lite (multi-language editor)
  --with-ide-safety     custom IDE-agent safety slice
  --with-swe            SWE-bench-lite (heavy)
  --with-legacy-codegen HumanEval+ (cheap legacy compatibility)

  --audit  ≡  --full --with-lm-eval --with-bfcl --with-aider --with-ide-safety

Layer 3 — per-bench scope tuning (env vars):
  LM_EVAL_PROMPTS_PER_TASK=50    # default 50 each (IFEval, GSM8K)
  BFCL_LITE_CASES=75             # default 75 of ~2000 in full BFCL
  AIDER_POLYGLOT_CASES=30        # default 30 of 225 full
  IDE_SAFETY_STRICT=0            # 1 = stricter agent-safety scoring
  SWE_BENCH_CASES=10             # default 10 of 300 full SWE-bench-lite
  SWE_BENCH_TIMEOUT_S=900        # per-case wall budget
```

**Sandbox container layout per bench:**

```
sandboxes/lm-eval-cal/        # IFEval + GSM8K via EleutherAI/lm-evaluation-harness
sandboxes/bfcl-lite/          # BFCL via gorilla-llm/gorilla
sandboxes/aider-polyglot/     # Aider polyglot benchmark via Aider-AI/aider
sandboxes/ide-safety/         # custom — we author the verifier
sandboxes/swe-bench-lite/     # SWE-bench via princeton-nlp + mini-SWE-agent
```

Each ships:
- `Dockerfile` baking the upstream runner
- `server.py` exposing standard `/health` + `/verify` (or `/verify-start/turn/end` for multi-turn)
- `fixtures/` lifted from upstream where applicable
- `verification/` if delegating to upstream JS/Python runtime

**club-3090 wrapper (`scripts/quality-test.sh`) passthrough:**

```bash
bash scripts/quality-test.sh --audit                 # full audit run
bash scripts/quality-test.sh --full --with-bfcl      # composable
bash scripts/quality-test.sh --pack aider-polyglot   # single bench
bash scripts/quality-test.sh --swe                   # power-user repo-scale
LM_EVAL_PROMPTS_PER_TASK=25 bash scripts/quality-test.sh --audit  # tighter calibration
```

Same env-var pattern as today's `URL` / `MODEL` / `TIMEOUT_PER_CASE`.

**What's load-bearing about this design:**

1. **Each bench is just another pack ID.** No special-casing in the runner. Adding `aider-polyglot-30` is mechanically identical to adding any BenchLocal pack — JSONL + sandbox container + register in `SANDBOX_REGISTRY`.
2. **`--audit` and `--swe` are presets**, not hardcoded paths. They expand to pack lists like `--full` does today. New presets cost nothing structural to add.
3. **Composable flags + presets coexist.** Most users hit `--audit`; power users compose. Both paths supported.
4. **Per-bench tuning via env-var.** Keeps CLI surface narrow.
5. **Each external bench is one focused work-item** — 2-4 hr each on average (most work is wrapping the upstream runner in our standard sandbox shape, not authoring scenarios).

### Tools we evaluated and *don't* rank for inclusion

- **promptfoo** — useful for orchestration / regression diffs, but doesn't solve the verifier-maturity problem (which is where BenchLocal's value is)
- **OpenAI simple-evals** — good reference code, but deprecated as a maintained source; use for inspiration only
- **HumanEval / HumanEval+** — Python-only spec-to-code, saturated, prone to overfit. Demoted to optional `--with-legacy-codegen` cheap compatibility anchor; Aider Polyglot replaces it as the primary code-gen slot
- **MT-Bench / Arena-Hard** — requires a strong judge model, not deterministic, defeats the local-only premise

## Parking lot — when needed

Worth doing eventually but not urgent. Promote to a versioned milestone when there's a concrete trigger:

- **Cross-model side-by-side** — run same pack against N endpoints in one invocation, output side-by-side. Useful when comparing quants or composes.
- **Pack version drift detection** — alert when our vendored packs lag upstream.
- **Mock fixture library** — curated mocks for testing prompt/verifier changes without GPU time.
- **CI integration in club-3090** — wire `quality-test.sh` into the canonical `verify-full → bench → quality-test → soak-test` pipeline as an enforced gate.
- **Inspect AI** — promote when we want either (a) an Inspect-AI-native eval (UK AISI's safety library — WMDP, redteam evals, InspectAgentBench, etc — where Inspect AI IS the canonical home, not a port) or (b) we author a custom eval from scratch (club-3090-specific regression bench, internal safety eval) and want their framework primitives. **Not** the right path for IFEval/GSM8K/MMLU/HellaSwag/HumanEval/MBPP/SWE-bench/BFCL — those have stronger canonical homes (lm-eval-harness, gorilla, openai/human-eval, princeton-nlp/SWE-bench) and Inspect AI ships them as ports that may lag/drift from canonical upstream.

## Out of scope (upstream territory)

- Adding new packs to the BenchLocal source repos. Our role is the CLI port; new packs come from upstream.
- Verifier algorithm changes that should land upstream, not in our scoring layer. Examples: ReasonMath value-centric matching ([stevibe/ReasonMath-15#2](https://github.com/stevibe/ReasonMath-15/issues/2)) — file the issue, propose the fix, only override locally if upstream declines.
- Token-budget tuning per-scenario for inference-heavy reasoning chains (RM-04, RM-06 truncation) — methodology question for upstream maintainers.

---

**How to use this doc:**

- New idea? Check if it fits a tier above. If yes, append a bullet to that tier with a one-line rationale. If no, add to the parking lot.
- Promoting parking-lot to versioned: pick the next minor (v0.7, v0.8…) and write a brief.
