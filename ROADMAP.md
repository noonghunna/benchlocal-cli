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

## After v0.6 — v0.6.1 follow-up

Small bundle, ~4-6 hr total. Lands close behind v0.6:

- **ReasonMath value-centric verifier.** In-process scoring path (separate from sandbox layer). Implements value-match assertion type per [stevibe/ReasonMath-15#2](https://github.com/stevibe/ReasonMath-15/issues/2). Path depends on upstream response:
  - If stevibe accepts the proposal → align with upstream
  - If declined → override locally in `benchlocal_cli/scoring/value_match.py`, document as intentional divergence
- **CHANGELOG + migration notes for v0.6 score drop.** Without this, anyone reading historical compose Quality lines will think v0.6 made models worse. Document that v0.4 numbers measured shape, v0.6 measures correctness.

## Diagnostic tooling — v0.7

Brief TBD. Lands after v0.6 + v0.6.1. ~8-10 hr Codex chunk. Surface area:

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
