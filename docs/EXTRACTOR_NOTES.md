# Extractor notes

`node tools/build-packs.js` is the canonical pack generator. It reads vendored upstream sources from `vendor/<PackName>/` and writes JSONL to `benchlocal_cli/packs/`.

## Source of truth

- `SYSTEM_PROMPT` is extracted from upstream `lib/benchmark.ts` and inserted as the first message for deterministic packs that define one.
- Scenario IDs, titles, descriptions, success cases, failure cases, and user prompts are extracted from upstream scenario definitions.
- Metadata records both `upstream_commit` and `_synced_from_commit` from `vendor/<PackName>/_sync.json`.
- Metadata augments upstream sampling defaults with `chat_template_kwargs: {"enable_thinking": false}`. Upstream BenchLocal packs were calibrated on non-reasoning models; this local augmentation prevents reasoning-capable servers from exhausting benchmark token budgets on hidden thinking by default.

## Callback translation

Upstream deterministic packs often score via `evaluate(state)` callbacks. JSONL cannot represent arbitrary callback code, so the extractor emits deterministic assertion primitives that match the primary pass condition where practical.

Known lossy surfaces:

- ToolCall-15 partial-credit branches are collapsed to pass/fail assertions. Dynamic `handleToolCall` fixtures remain in `vendor/ToolCall-15/lib/benchmark.ts`; generated JSONL records an `upstream_evaluate_summary` note per scenario.
- InstructFollow-15 complex set/order constraints are represented with regex, word-count, phrase, and bullet-count assertions. The upstream callback remains the review oracle for exact parity disputes.
- StructOutput-15 upstream Docker verifier semantics are approximated by deterministic local checks for JSON, CSV, Markdown structure, YAML-lite, and regex shape. Full Docker verifier parity is deferred.
- DataExtract-15 embeds upstream `expected` JSON fixtures and checks required fields plus top-level field discipline. The detailed upstream atomic-field scoring remains more granular than v0.2 JSONL scoring.

Sandbox-backed packs (BugFind-15, HermesAgent-20, CLI-40) are generated from vendor metadata and keep `_stub` verifiers in JSONL. In v0.4 that `_stub` type means "dispatch to the Docker verifier when `--enable-sandboxed-packs` is set"; it no longer means the pack is always unimplemented.

The v0.6 pass adds `raw_scenario` payloads for BugFind-15, HermesAgent-20, and CLI-40. These payloads carry upstream IDs, categories/kinds, success/failure cases, and deterministic keyword/rubric metadata. The current local upstream mirrors do not include the pytest fixture trees, CLI workspace fixtures, or Hermes browser/cron/tool-flow fixtures assumed by the v0.6 brief, so full hidden-fixture extraction remains the main parity gap for execution-backed packs.

## Design choice

The extractor favors prompt/source fidelity over pretending callback logic can be serialized perfectly. When callback fidelity matters, review the generated assertions against the vendored TypeScript in the same commit.
