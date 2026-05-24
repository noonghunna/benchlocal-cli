# Verifier Fidelity Audit

This audit compares the in-process Python scorers in `benchlocal_cli/scoring/` with the vendored upstream TypeScript verifier sources under `vendor/*/lib/benchmark.ts` plus the StructOutput sandbox verifier in `vendor/StructOutput-15/verification/core.mjs`.

## Scope

In scope: `tool_call.py`, `reason_math.py`, `instruct_follow.py`, `struct_output.py`, and `data_extract.py`.

Out of scope: sandbox-only packs (`bugfind-15`, `hermesagent-20`, `cli-40`, `aider-polyglot-30`, `humaneval-plus-30`, `lcb-v6-30`) because they either already execute an upstream verifier runtime or use separate dataset-gated answer matching.

## Gap Table

| Pack | Upstream behavior | Local status after this audit | Residual risk |
| --- | --- | --- | --- |
| ToolCall-15 | True multi-turn tool loop with dynamic tool results and per-scenario `evaluate(state)`. Dependent chains stop after the first correct call until a tool result is available. | The scorer keeps the PR #31 dependent-prefix approximation, and `tools/build-packs.js` now preserves `dependent: true` for TC-03, TC-07, TC-08, and TC-15 so regeneration no longer erases it. | Still a single-response approximation; it cannot validate second-turn argument propagation from tool results. |
| ReasonMath-15 | Two-axis scoring: final `ANSWER: ` line versus canonical/accepted/partial answers, plus trace checkpoints. `pass` means score >= 85. | Regenerated JSONL now carries `canonical_answer`, `accepted_answers`, `partial_answers`, and `checkpoints`. `reason_math.py` uses the upstream-style answer axis, trace axis, 70/30 score weighting, and stores the score in `verifier_trace`. | Binary BenchLocal pass/fail cannot expose upstream `partial` status directly; partial/low-score cases are represented as failures with trace detail. |
| InstructFollow-15 | Fifteen scenario-specific evaluators with exact counts, ordering, closed-set membership, punctuation, paragraph/list shape, and negative constraints. | No code change. Current local assertions remain a deliberately shallow subset. | High residual fidelity gap: scenarios IF-05 through IF-09, IF-11, and IF-13 mostly check non-empty output only. Full parity requires porting the scenario-specific evaluators or running an upstream verifier service. |
| StructOutput-15 | Upstream sandbox verifier checks exact JSON/CSV values, plus scenario-specific YAML/TOML/SQL/ICS/XML/Markdown/Mermaid/HTML/BSON rules and output-discipline scoring. | No code change. Current local assertions remain parse/header/regex approximations. | High residual fidelity gap: many non-JSON formats are currently `format_regex: .+`, and JSON/CSV checks are partial rather than exact. Full parity should reuse the upstream `verification/core.mjs` service or port its scenario-specific validators. |
| DataExtract-15 | Parses JSON, compares every expected atomic field, handles arrays with scenario anchors, applies numeric tolerance, and records compliance notes for shape/extra/missing fields. `pass` means score >= 85. | `data_extract.py` now uses the pack's `expected` payload when present and applies upstream-style recursive scoring with object-array anchors and verifier traces. Legacy field-level assertions remain supported for ad hoc scenarios. | The local result is still binary, so upstream partial status is represented as failure with `upstream_style_score` in `verifier_trace`. |
| AnswerMatch | No corresponding vendored TS verifier for the new reasoning-suite packs. | Not changed. | Standalone local grader by design. |

## Expected Score Movement

ReasonMath and DataExtract are now stricter and more faithful. Correct-final-answer-only ReasonMath responses that omit published checkpoints can move from pass to fail because upstream would score them around 70 and mark them below the pass threshold. DataExtract responses that only include the first eight required fields can also move down because all expected fields are now scored.

ToolCall scores should not shift relative to PR #31, but the fix prevents future `node tools/build-packs.js ToolCall-15` runs from reverting TC-03/07/08/15 to strict all-calls-required assertions.
