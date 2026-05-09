# Codex implementation brief — benchlocal-cli v0.2 (fidelity restoration + sync infrastructure)

## Context

You shipped v0.1 (commits `14de749` → `928291f`). It works end-to-end but the pack ports simplified upstream BenchLocal prompts and verifier logic. Spot-check confirmed:

- **Upstream system prompt** (4-line "Rules:" block) was simplified to a 1-line generic prompt
- **`BENCHMARK_REFERENCE_DATE = "2026-03-20"`** likely not threaded through
- **`handleToolCall` callback fixtures** for multi-turn scenarios likely missing
- **`evaluate(state)` callback verifier logic** ported as summarized assertions

Result: scores from v0.1 are calibrated to our prompt set, NOT directly comparable to BenchLocal desktop scores. Codex's own report flagged this honestly: "Pack semantic fidelity is the main remaining review surface before public release."

The user wants:

1. **Verbatim fidelity** — bit-exact prompts, tool defs, sampling params, fixtures, verifier semantics. No simplification.
2. **Future-proof re-sync** — when BenchLocal updates upstream packs, re-syncing should be a mechanical script, not a manual re-port.

This v0.2 brief does both.

## Architecture

Two-layer model: upstream TS lives in `vendor/`, generated JSONL lives in `benchlocal_cli/packs/`. A Node-based extractor walks vendor and produces JSONL.

```
benchlocal-cli/
├── vendor/                                  # NEW — upstream TS mirrors
│   ├── ToolCall-15/
│   │   ├── lib/
│   │   │   ├── benchmark.ts                 # bit-exact mirror of stevibe/ToolCall-15/lib/benchmark.ts
│   │   │   ├── llm-client.ts
│   │   │   └── ... (any other lib/ files)
│   │   ├── benchlocal.pack.json             # upstream metadata
│   │   └── _sync.json                       # {commit, fetched_at, source_url, source_files}
│   ├── InstructFollow-15/
│   ├── StructOutput-15/
│   ├── ReasonMath-15/
│   ├── DataExtract-15/
│   ├── BugFind-15/
│   ├── HermesAgent-20/
│   └── CLI-40/
│
├── scripts/
│   ├── sync-vendor.sh                       # NEW — `gh api` fetch upstream → vendor/<pack>/
│   ├── build-packs.js                       # NEW — Node extractor: parse vendor TS → emit JSONL
│   └── extractor/                           # NEW — supporting Node code if needed
│       ├── package.json
│       └── ...
│
└── benchlocal_cli/packs/                    # GENERATED — replaces v0.1's hand-ported JSONL
    ├── toolcall-15.jsonl                    # output of build-packs.js
    └── ... (all 8 packs)
```

## Phases

### Phase A — Vendor scaffold + sync script (~1 hr)

1. Create `vendor/<PackName>/` for all 8 packs
2. Write `scripts/sync-vendor.sh <PackName>`:
   - Fetches `lib/` directory contents from `stevibe/<PackName>` via `gh api`
   - Saves files to `vendor/<PackName>/lib/<file>`
   - Saves `benchlocal.pack.json` to `vendor/<PackName>/`
   - Records commit SHA in `vendor/<PackName>/_sync.json` with `{commit, fetched_at, source_url, source_files}`
   - Idempotent — re-running on same commit produces no diff
3. Run sync against all 8 packs at the latest available commit (or pin to specific SHAs documented in v0.1 ATTRIBUTION.md if upstream has moved)
4. Commit: `feat(vendor): scaffold vendor/ + sync-vendor.sh; sync all 8 upstream packs`

### Phase B — Node-based extractor (~1.5 hr)

`scripts/build-packs.js` is the canonical extractor. Architecture:

1. Import the upstream pack module via `tsx` or `ts-node` so TypeScript executes natively
2. Reach into the imported namespace — extract:
   - `SYSTEM_PROMPT` (and any other top-level constants like `BENCHMARK_REFERENCE_DATE`)
   - The scenarios array (whatever it's named in each pack — `SCENARIOS`, `BENCHMARK_SCENARIOS`, etc.)
   - For multi-turn scenarios, capture `handleToolCall` outcomes by replaying expected tool invocations
   - For `evaluate(state)` verifier callbacks, port the logic into JSONL `verifier.asserts[]` array using the assertion primitives from `docs/PACK_FORMAT.md`
3. Emit JSONL with the metadata line first, then one scenario per line
4. CLI: `node scripts/build-packs.js <PackName>` regenerates `benchlocal_cli/packs/<pack-id>.jsonl` from `vendor/<PackName>/`
5. CLI: `node scripts/build-packs.js --all` regenerates all 8 packs

**Key constraint:** the extractor must include the upstream `SYSTEM_PROMPT` verbatim as the system message in EVERY scenario of that pack. The current v0.1 pack files have a simplified system prompt that needs to be replaced with upstream's verbatim version.

If upstream has constants like `BENCHMARK_REFERENCE_DATE` referenced from scenario prompts, the extractor must inline the resolved value. The runtime should never need to evaluate template strings.

For `evaluate(state)` callbacks that compute pass/fail: port the logic to deterministic JSONL asserts. Where the upstream callback logic uses helpers like `hasToolCall(state, "name", predicate)`, translate to the appropriate `tool_call.<assertion>` primitives. Document any unavoidable lossy translations in `docs/EXTRACTOR_NOTES.md` so future-Claude can review.

5. Commit: `feat(extractor): Node-based build-packs.js; regenerate all 8 packs from vendor/`

### Phase C — Replace v0.1 JSONL with v0.2 extractor output (~30 min)

1. Run `node scripts/build-packs.js --all`
2. The 8 JSONL files in `benchlocal_cli/packs/` will be overwritten with verbatim-from-upstream versions
3. `git diff` should show:
   - Updated metadata lines (extractor-generated, with `_synced_from_commit` field)
   - Verbatim system prompts replacing the simplified ones
   - Restored constants (BENCHMARK_REFERENCE_DATE etc)
   - Restored multi-turn fixtures where applicable
   - Verifier asserts now sourced from upstream `evaluate()` logic
4. Run `pytest tests/` — all should still pass (or be updated to match new fidelity)
5. Run smoke validation from v0.1's CODEX_BRIEF.md "Phase 3 — Validation" — `benchlocal-cli list`, mock pack run, `--quick` mock run
6. Commit: `feat(packs): regenerate from vendor/ — verbatim BenchLocal upstream fidelity`

### Phase D — Documentation update (~30 min)

1. Update `README.md`:
   - Status: `🚧 Alpha — quick mode functional` → `🟢 Beta — full BenchLocal fidelity, all 5 deterministic packs working`
   - Drop the "non-canonical" caveats; explicitly state scores ARE comparable to BenchLocal desktop runs since prompts are byte-for-byte identical to upstream
2. Update `ATTRIBUTION.md`:
   - Drop the "summarized assertions" caveat
   - Add a "How to re-sync with upstream" section pointing at `scripts/sync-vendor.sh` + `scripts/build-packs.js`
3. Create `CONTRIBUTING.md` with the upstream-sync workflow:
   ```bash
   bash scripts/sync-vendor.sh ToolCall-15
   node scripts/build-packs.js ToolCall-15
   git diff benchlocal_cli/packs/toolcall-15.jsonl
   git commit -am "feat: sync ToolCall-15 to upstream commit X"
   ```
4. Create `docs/EXTRACTOR_NOTES.md` documenting any extractor design decisions (especially handleToolCall fixture freezing, evaluate-callback-to-asserts translation strategy)
5. Commit: `docs: update README + ATTRIBUTION + add CONTRIBUTING + EXTRACTOR_NOTES for v0.2`

### Phase E — Final validation (~30 min)

1. `pytest tests/` passes
2. `pip install -e .` works in fresh venv
3. `benchlocal-cli list` works
4. Mock smoke (`--pack toolcall-15` and `--quick`) passes
5. Spot-check 1 scenario manually: read `benchlocal_cli/packs/toolcall-15.jsonl` line 2 (TC-01) → confirm system prompt matches upstream's full 4-line "Rules:" block exactly, BENCHMARK_REFERENCE_DATE is inlined where referenced, verifier asserts are tighter than v0.1
6. Update `docs/CODEX_REPORT.md` (overwrite v0.1 report) with v0.2 status

## Constraints (carry-overs from v0.1)

1. **Stdlib-first runtime** for the Python CLI — only `httpx` and `jsonschema` runtime deps. The `vendor/` and extractor are dev/sync-time only; the runtime never imports Node.
2. **Deterministic verifiers only** — no LLM-as-judge.
3. **Failure mode taxonomy mandatory.** Same enum as v0.1.
4. **Reproducibility** — JSONL metadata records `vendor_commit` so result blobs can be cross-referenced to the exact upstream version.
5. **Attribution hygiene** — vendor/ files are byte-exact mirrors; extractor output preserves upstream IDs verbatim.

## What's STILL out of scope for v0.2

- BugFind / HermesAgent / CLI sandbox infrastructure (v0.3+)
- LLM-as-judge fallback verifiers
- `benchlocal-cli diff` / `reproduce` subcommands
- PyPI publish

## Async report-back protocol

Same as v0.1 — see `CODEX_BRIEF.md` "How to communicate back to Claude" section.

If anything in this v0.2 brief is ambiguous (especially around evaluate-callback-to-asserts translation), file in `docs/QUESTIONS.md` and stop. Examples of likely ambiguity:
- "What if upstream's `evaluate()` callback uses set logic / partial credit / multi-step state?" — document the lossy translation in EXTRACTOR_NOTES.md
- "What if scenario IDs collide with v0.1?" — preserve upstream IDs verbatim; if v0.1 used different IDs, the diff will show it
- "Should the extractor handle scenarios where `evaluate()` references mutable state across turns?" — for multi-turn scenarios, the extractor should freeze the expected sequence as a series of asserts. Where the logic is too dynamic to freeze, port to a pack-specific Python helper.

When done, write `docs/CODEX_REPORT.md` (overwriting v0.1's) with the v0.2 summary, push, and stop.

## Validation gate before declaring v0.2 done

- [ ] All 8 vendor/ dirs scaffolded with upstream lib/ files mirrored
- [ ] `scripts/sync-vendor.sh <PackName>` is idempotent + records commit SHA
- [ ] `scripts/build-packs.js [--all|<PackName>]` regenerates JSONL from vendor
- [ ] All 5 deterministic packs (toolcall, instructfollow, structoutput, reasonmath, dataextract) have verbatim upstream system prompts in their JSONL
- [ ] BENCHMARK_REFERENCE_DATE (and any other shared constants) inlined into scenario prompts where referenced
- [ ] Multi-turn scenarios have handleToolCall fixtures captured (or the lossy translation is documented in EXTRACTOR_NOTES)
- [ ] `evaluate(state)` callback logic ported to JSONL assertion primitives 1:1 where possible
- [ ] 3 stubbed packs (bugfind, hermesagent, cli-40) have vendored TS even though verifier stays `_stub`
- [ ] `pytest tests/` passes
- [ ] `pip install -e .` works in fresh venv
- [ ] Smoke validation passes (mock --pack + --quick runs)
- [ ] README updated to "Beta — full fidelity"
- [ ] ATTRIBUTION + CONTRIBUTING + EXTRACTOR_NOTES updated
- [ ] CODEX_REPORT.md (overwriting v0.1) reflects v0.2 status
