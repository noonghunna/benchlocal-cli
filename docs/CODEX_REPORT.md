# Codex implementation report — benchlocal-cli v0.2

**Status:** ⚠️ Done with caveats
**Date:** 2026-05-09
**Total time:** ~1.5 hours

## Phases completed

- [x] Phase A — Vendor scaffold + sync script
- [x] Phase B — Node-based extractor
- [x] Phase C — Replace v0.1 JSONL with extractor output
- [x] Phase D — Documentation update
- [x] Phase E — Final validation

## Test results

- pytest: 6/6 tests passed
- pip install -e .: pass in fresh Python 3.11 venv at `/tmp/benchlocal-cli-v02-venv`
- benchlocal-cli list: pass
- benchlocal-cli run --pack toolcall-15 --endpoint <mock>: pass, 15/15
- benchlocal-cli run --quick --endpoint <mock>: pass, 30/30
- ruff check benchlocal_cli tests: pass
- TC-01 spot check: upstream system prompt match true; full Rules block present; benchmark_reference_date recorded as 2026-03-20

## Pack generation summary

| Pack | Vendor source | Scenarios generated | Verifier |
|---|---|---:|---|
| ToolCall-15 | `vendor/ToolCall-15/lib/benchmark.ts` | 15 | `tool_call` |
| InstructFollow-15 | `vendor/InstructFollow-15/lib/benchmark.ts` | 15 | `instruct_follow` |
| StructOutput-15 | `vendor/StructOutput-15/lib/benchmark.ts` | 15 | `struct_output` |
| ReasonMath-15 | `vendor/ReasonMath-15/lib/benchmark.ts` | 15 | `reason_math` |
| DataExtract-15 | `vendor/DataExtract-15/lib/benchmark.ts` | 15 | `data_extract` |
| BugFind-15 | `vendor/BugFind-15/lib/benchmark.ts` | 15 | `_stub` |
| HermesAgent-20 | `vendor/HermesAgent-20/lib/benchmark.ts` | 20 | `_stub` |
| CLI-40 | `vendor/CLI-40/verification/scenario-data.json` | 40 | `_stub` |

## Deviations from CODEX_BRIEF_V2.md

- Prompt/source fidelity is restored from vendor mirrors, but arbitrary upstream `evaluate(state)` callbacks are not bytecode-equivalent in JSONL. The extractor emits deterministic assertion primitives and documents lossy surfaces in `docs/EXTRACTOR_NOTES.md`.
- StructOutput-15 still uses local deterministic checks instead of the upstream Docker verifier. This keeps v0.2 within the stated no-sandbox scope but is not full verifier parity.
- `scripts/build-packs.js` uses a dependency-free TypeScript text extractor rather than `tsx`/`ts-node`. The runtime remains Python-only; Node is sync-time only.

## Open questions filed

- none

## Notes for Claude's review

- Review `scripts/build-packs.js` and `docs/EXTRACTOR_NOTES.md` first; callback-to-assert fidelity is the main review surface.
- `scripts/sync-vendor.sh` records `_sync.json` and can refresh a single pack from GitHub with `gh api`.
- Generated JSONL metadata includes `_synced_from_commit`; ToolCall scenarios also carry `benchmark_reference_date` and `benchmark_reference_day`.
- Sandbox packs are vendored and generated but intentionally remain `_stub`.
