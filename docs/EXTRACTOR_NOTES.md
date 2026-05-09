# Extractor notes

`node tools/build-packs.js` is the canonical v0.2 pack generator. It reads vendored upstream sources from `vendor/<PackName>/` and writes JSONL to `benchlocal_cli/packs/`.

## Source of truth

- `SYSTEM_PROMPT` is extracted from upstream `lib/benchmark.ts` and inserted as the first message for deterministic packs that define one.
- Scenario IDs, titles, descriptions, success cases, failure cases, and user prompts are extracted from upstream scenario definitions.
- Metadata records both `upstream_commit` and `_synced_from_commit` from `vendor/<PackName>/_sync.json`.

## Callback translation

Upstream deterministic packs often score via `evaluate(state)` callbacks. JSONL cannot represent arbitrary callback code, so the extractor emits deterministic assertion primitives that match the primary pass condition where practical.

Known lossy surfaces:

- ToolCall-15 partial-credit branches are collapsed to pass/fail assertions. Dynamic `handleToolCall` fixtures remain in `vendor/ToolCall-15/lib/benchmark.ts`; generated JSONL records an `upstream_evaluate_summary` note per scenario.
- InstructFollow-15 complex set/order constraints are represented with regex, word-count, phrase, and bullet-count assertions. The upstream callback remains the review oracle for exact parity disputes.
- StructOutput-15 upstream Docker verifier semantics are approximated by deterministic local checks for JSON, CSV, Markdown structure, YAML-lite, and regex shape. Full Docker verifier parity is deferred.
- DataExtract-15 embeds upstream `expected` JSON fixtures and checks required fields plus top-level field discipline. The detailed upstream atomic-field scoring remains more granular than v0.2 JSONL scoring.

Sandbox-backed packs (BugFind-15, HermesAgent-20, CLI-40) are generated from vendor metadata but keep `_stub` verifiers.

## Design choice

The extractor favors prompt/source fidelity over pretending callback logic can be serialized perfectly. When callback fidelity matters, review the generated assertions against the vendored TypeScript in the same commit.
