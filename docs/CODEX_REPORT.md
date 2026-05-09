# Codex implementation report — benchlocal-cli v0.3

**Status:** ✅ Done
**Date:** 2026-05-09
**Total time:** ~1 hour

## Phases completed

- [x] Phase A — Pack-level thinking-off default
- [x] Phase B — CLI flag wiring
- [x] Phase C — Runner-level request/response handling
- [x] Phase D — Output annotation
- [x] Phase E — Tests + docs + validation

## Test results

- pytest: 12/12 tests passed
- ruff check benchlocal_cli tests: pass
- pip install -e .: pass in fresh Python 3.11 venv at `/tmp/benchlocal-cli-v03-venv`
- benchlocal-cli list: pass
- benchlocal-cli run --pack toolcall-15 --endpoint <mock>: pass, 15/15
- benchlocal-cli run --quick --endpoint <mock>: pass, 30/30
- default request body: `chat_template_kwargs.enable_thinking=false`
- `--enable-thinking` request body: `chat_template_kwargs.enable_thinking=true`, `max_tokens=4096`
- `--extra-body '{"foo":"bar"}'`: request body includes `foo=bar`
- response fallback: tested `content -> reasoning_content -> reasoning` reader path
- all 8 pack metadata lines include `chat_template_kwargs: {"enable_thinking": false}`

## Implementation summary

- `tools/build-packs.js` now augments generated pack metadata with `chat_template_kwargs.enable_thinking=false`.
- `benchlocal-cli run` adds `--enable-thinking`, `--thinking-max-tokens`, and `--extra-body`.
- Runner request construction now merges pack defaults, extra body, and scenario overrides while enforcing thinking state.
- Thinking-on diagnostic runs set `max_tokens` to `--thinking-max-tokens` (default 4096), overriding scenario token caps so reasoning output has budget.
- JSON output adds top-level `thinking_enabled` without reshaping existing fields.
- Markdown header now includes `thinking=off|on`.
- Response parsing now falls back from `content` to `reasoning_content` to `reasoning`, and `ScenarioRun` records `response_field_used`.

## Pack generation summary

| Pack | Scenarios | Thinking default |
|---|---:|---|
| ToolCall-15 | 15 | off |
| InstructFollow-15 | 15 | off |
| StructOutput-15 | 15 | off |
| ReasonMath-15 | 15 | off |
| DataExtract-15 | 15 | off |
| BugFind-15 | 15 | off / stub verifier |
| HermesAgent-20 | 20 | off / stub verifier |
| CLI-40 | 40 | off / stub verifier |

## Deviations from CODEX_BRIEF_V3.md

- The brief had one internal tension: request merge rules said scenario overrides should win, but the validation gate explicitly required `--enable-thinking` on ToolCall to produce `max_tokens=4096` even though ToolCall scenarios specify `max_tokens=512`. I implemented the validation-gate behavior because it matches the live failure mode: thinking-mode diagnostics need a larger budget.

## Open questions filed

- none

## Notes for Claude's review

- Review `benchlocal_cli/runner.py::build_request` for request merge precedence.
- Review `benchlocal_cli/scoring/common.py::content_with_source` for the three-field response fallback.
- Docs updated: README, DESIGN, PACK_FORMAT, EXTRACTOR_NOTES.
