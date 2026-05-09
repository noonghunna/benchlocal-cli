# Codex implementation report — benchlocal-cli v0.1

**Status:** ⚠️ Done with caveats
**Date:** 2026-05-09
**Total time:** ~2 hours

## Phases completed

- [x] Phase 1 — Core runtime
- [x] Phase 2 — Pack porting + verifiers
- [x] Phase 3 — Validation

## Test results

- pytest: 6/6 tests passed
- pip install -e .: pass in fresh Python 3.11 venv at `/tmp/benchlocal-cli-venv`
- benchlocal-cli list: pass
- benchlocal-cli run --pack toolcall-15 --endpoint <mock>: pass, 15/15
- benchlocal-cli run --quick --endpoint <mock>: pass, 29/30 in the smoke fixture
- ATTRIBUTION.md fully filled: yes

## Pack porting summary

| Pack | Scenarios ported | Scenarios dropped | Reason if dropped |
|---|---:|---:|---|
| ToolCall-15 | 15 | 0 | — |
| InstructFollow-15 | 15 | 0 | — |
| StructOutput-15 | 15 | 0 | — |
| ReasonMath-15 | 15 | 0 | — |
| DataExtract-15 | 15 | 0 | — |
| BugFind-15 | 15 | 0 | Verifier stubbed for v0.1 |
| HermesAgent-20 | 20 | 0 | Verifier stubbed for v0.1 |
| CLI-40 | 40 | 0 | Verifier stubbed for v0.1 |

## Deviations from DESIGN.md

- `--mock-responses-from-json` was added as an explicit CLI flag because the brief allowed stub responses for Phase 3 smoke validation.
- The v0.1 pack ports preserve upstream IDs and metadata commits, but several non-primary deterministic pack prompts/assertions are summarized into deterministic JSONL assertions rather than byte-for-byte TypeScript verifier equivalents. Claude should review pack fidelity before treating scores as canonical BenchLocal parity.
- StructOutput YAML support is intentionally YAML-lite and stdlib-only; it validates simple key/value YAML structure but does not replace a full YAML parser.

## Open questions filed

- none

## Notes for Claude's review

- Review `benchlocal_cli/runner.py` first for output schema and reproducibility fields.
- Review `benchlocal_cli/scoring/*` next; all scorer modules now return the shared `ScenarioResult` dataclass.
- Sandbox-backed packs are present and skipped by default unless `--enable-sandboxed-packs` is set; they return `verifier_not_implemented`.
- The implementation is functional for local/mock smoke and quick-mode plumbing. Pack semantic fidelity is the main remaining review surface before public release.
