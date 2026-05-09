# Codex implementation brief — benchlocal-cli v0.1

## What you have

A scaffolded Python project at `/opt/ai/github/benchlocal-cli/` (will be hosted at `noonghunna/benchlocal-cli`). All architectural decisions have been made and documented:

- [`README.md`](./README.md) — public-facing overview + UX targets
- [`docs/DESIGN.md`](./docs/DESIGN.md) — full architecture, JSONL schema, scoring contract, output format, failure-mode taxonomy
- [`docs/PACK_FORMAT.md`](./docs/PACK_FORMAT.md) — JSONL pack file format + assertion primitives per verifier type
- [`docs/INTEGRATION.md`](./docs/INTEGRATION.md) — how the parent project (`club-3090`) consumes this CLI
- [`ATTRIBUTION.md`](./ATTRIBUTION.md) — MIT attribution to upstream BenchLocal
- [`pyproject.toml`](./pyproject.toml) — package metadata, dependencies, ruff/pytest config
- `benchlocal_cli/` — Python package with stub modules:
  - `cli.py` — entry point stub (raises NotImplementedError)
  - `runner.py` — orchestrator stub (raises NotImplementedError)
  - `scoring/{tool_call,instruct_follow,struct_output,reason_math,data_extract}.py` — verifier stubs (raise NotImplementedError)
  - `scoring/_stub.py` — placeholder for sandboxed packs (BugFind/HermesAgent/CLI); already implemented (returns `verifier_not_implemented`)
  - `packs/<pack-id>.jsonl` — 8 stub pack files (metadata-only, no scenarios yet)
- `tests/test_scoring_smoke.py` — smoke tests confirming imports work + stubs raise NotImplementedError as expected

## What you need to build

### Phase 1 — Core runtime (highest priority, ~3-4 hr)

1. **`benchlocal_cli/runner.py`** — implement the full orchestrator per the architecture diagram in DESIGN.md:
   - Load a JSONL pack file → parse metadata + scenarios
   - For each scenario: build OpenAI-compatible chat-completions request (apply `sampling_defaults` + per-scenario `sampling_overrides`)
   - POST to endpoint with `--timeout-per-case` timeout (use `httpx`)
   - Capture (response body, latency, HTTP status, completion token count)
   - Dispatch to `scoring.<verifier_module>.score_scenario(scenario, response)`
   - Aggregate per-pack: pass count, total, score, p50/p95/mean latency
   - Return a result dataclass; the CLI handles output formatting

2. **`benchlocal_cli/cli.py`** — implement argument parsing per the target UX in cli.py's docstring + DESIGN.md "Modes" section:
   - `benchlocal-cli list`
   - `benchlocal-cli run --quick|--medium|--full --endpoint URL --model NAME [--timeout-per-case N] [--output markdown|json] [--save-json PATH] [--repeat N] [--enable-sandboxed-packs] [--pack PACK_ID]`
   - Mode → pack list dispatch (hardcoded mapping per DESIGN.md)
   - Output formatting (markdown table OR JSON blob)
   - Exit code 0 on success, non-zero on errors (HTTP failure, no packs run, etc.)

3. **`ScenarioResult` dataclass** — define per the contract in DESIGN.md "Scoring module contract" section. Used by all scoring modules + the runner.

### Phase 2 — Pack porting + verifier implementation (~3-4 hr)

For each of the 5 deterministic packs (ToolCall-15, InstructFollow-15, StructOutput-15, ReasonMath-15, DataExtract-15):

1. **Visit the upstream pack repo** (e.g. `https://github.com/stevibe/ToolCall-15`) — read `lib/scenarios.ts` / `lib/scoring.ts` (or equivalent) to understand:
   - The 15 scenarios — prompts, expected outputs, sampling defaults
   - The verifier logic — what makes each scenario pass/fail

2. **Port scenarios to JSONL** at `benchlocal_cli/packs/<pack-id>.jsonl`:
   - Replace the stub metadata line with real upstream commit + ported_at date + porter
   - Add 15 scenario lines, one per upstream scenario
   - Use `id` matching the upstream scenario ID for cross-comparability
   - Translate the upstream verifier intent into the assertion primitives documented in `docs/PACK_FORMAT.md`
   - If a scenario can't be deterministically scored (unlikely for these packs but check), DROP it and document the drop in `ATTRIBUTION.md`

3. **Implement the verifier module** at `benchlocal_cli/scoring/<verifier_module>.py`:
   - Replace the stub `score_scenario()` with real implementation
   - Handle every assertion primitive documented for that verifier type
   - Distinguish failure modes per the taxonomy in DESIGN.md
   - Write unit tests in `tests/test_scoring_<verifier_module>.py` covering at least 2 PASS + 2 FAIL cases per assertion primitive

4. **For execution-backed packs (BugFind, HermesAgent, CLI)** — only port the scenarios + metadata to JSONL with `verifier_module: "_stub"`. Document in ATTRIBUTION.md that the verifier is deferred. Don't try to wire up the sandbox infrastructure in v1.

### Phase 3 — End-to-end smoke (~1 hr)

1. Manually verify the CLI works against a mock OpenAI-compatible endpoint (a small Python httpx-mock server, OR stub responses fed in via `--mock-responses-from-json`):
   - `benchlocal-cli list` — should print all 8 packs with versions + verifier types
   - `benchlocal-cli run --pack toolcall-15 --endpoint http://localhost:9999 --model fake` — should run all 15 ToolCall scenarios against the mock + emit markdown table
   - `benchlocal-cli run --quick ...` — should run ToolCall + InstructFollow

2. Verify pytest passes: `pytest tests/`

3. Update README badge / status from "🚧 Pre-alpha" to "🚧 Alpha — quick mode functional" once Phase 1+2 ToolCall is working end-to-end.

## Constraints + non-negotiables

1. **Stdlib-first** — minimize dependencies. `httpx` for HTTP, `jsonschema` for StructOutput-15 schema validation; everything else should be Python stdlib (json, re, statistics, dataclasses, argparse, asyncio if needed for parallelism).

2. **No LLM-as-judge** — all verifiers are deterministic. If you find a scenario that can't be deterministically scored, drop it and document.

3. **Per-scenario asserts** (Codex's own design feedback) — don't generalize. Each scenario specifies its own pass conditions via the asserts array. The verifier module dispatches to the right assertion primitive per kind.

4. **Failure mode taxonomy is mandatory** — every scoring module returns `failure_mode` from the enum in DESIGN.md. Users need to distinguish "model gave wrong tool" from "endpoint hit OOM mid-completion" when triaging.

5. **Reproducibility** — output JSON includes everything to re-run a scenario for debugging. Include raw scenario JSONL line + raw response body + sampling params + endpoint + model + timestamps.

6. **Latency tracking** — emit p50/p95/mean per pack. Tool-call/struct-output workloads are latency-bound, not throughput-bound. Include `tokens_completion` per scenario.

7. **Attribution hygiene** — every JSONL pack file's metadata line cites upstream_repo + upstream_commit. Every ported scenario uses upstream's ID verbatim (preserves cross-comparability with BenchLocal desktop runs).

## Deferred / out of scope for v1

- LLM-as-judge fallback verifiers
- BugFind / HermesAgent / CLI verifier infrastructure (sandbox / mocks) — port scenarios only, stub verifier
- `benchlocal-cli diff` / `benchlocal-cli reproduce` subcommands — defer
- Custom user-authored packs — JSONL format documented but only BenchLocal ports for v1
- Streaming response evaluation — single-shot completions only
- PyPI publish — local install + git+https for now

## Where to find the upstream packs

GitHub repos under [@stevibe](https://github.com/stevibe):

- `stevibe/ToolCall-15` (v1.0.1)
- `stevibe/InstructFollow-15` (v1.0.0)
- `stevibe/StructOutput-15` (v1.0.0)
- `stevibe/ReasonMath-15` (v1.0.0)
- `stevibe/DataExtract-15` (v1.0.0)
- `stevibe/BugFind-15` (v1.0.0)
- `stevibe/HermesAgent-20` (v1.0.0)
- `stevibe/CLI-40` (v1.0.2)

Each repo contains a `lib/` directory with TypeScript sources for scenarios + scoring. Use `gh api repos/stevibe/<pack-name>/contents/lib --jq '.[].name'` to list contents, then `gh api repos/stevibe/<pack-name>/contents/lib/<file>.ts --jq '.content' | base64 -d` to read.

The packs all have `benchlocal.pack.json` at the repo root with sampling defaults + metadata; lift values from there for the JSONL metadata line.

## Validation gate before shipping

Before declaring v0.1 done:

- [ ] `pytest tests/` passes
- [ ] `benchlocal-cli list` works
- [ ] `benchlocal-cli run --pack toolcall-15 --endpoint <mock>` produces sane markdown output
- [ ] `benchlocal-cli run --quick --endpoint <mock>` runs ToolCall + InstructFollow end-to-end
- [ ] All 5 deterministic packs ported (60 scenarios in JSONL)
- [ ] All 3 stubbed packs have scenarios in JSONL with `verifier_module: "_stub"`
- [ ] ATTRIBUTION.md filled in (no `_TBD_` rows)
- [ ] `pip install -e .` works in a fresh venv

Don't worry about live testing against real club-3090 endpoints — that's for the parent project's integration step.

## Branching + commit hygiene

- Work on `main` branch is fine for v0.1 (private repo, pre-alpha)
- Commit per logical unit (one commit per pack port, one per scoring module, one per CLI feature)
- Conventional commit messages (`feat:` / `fix:` / `docs:` / `test:`)
- No `--amend` after pushing
- Don't mix Phase 1 / Phase 2 / Phase 3 in the same commit

When done, push to GitHub. The parent project (club-3090) will install and integrate.

## What to ask before starting

If anything in DESIGN.md / PACK_FORMAT.md is ambiguous, file a question rather than guess. The architecture has been sanity-checked but implementation details may surface gaps.

Specific things you might hit:

- BenchLocal's verifier may use TS-specific patterns (e.g. zod schemas) that translate ambiguously to Python — pick the most semantically faithful translation, document the choice in a comment
- Some scenarios may use BenchLocal's "verifier server" pattern (separate Docker service); in our CLI those become `_stub` packs
- Sampling defaults in `benchlocal.pack.json` may vary per pack; respect them per-pack (don't normalize to a global default)
- Tool-call response shape varies per OpenAI-compatible endpoint (some emit `tool_calls` at top level, some inside `delta`); normalize to OpenAI's documented shape
