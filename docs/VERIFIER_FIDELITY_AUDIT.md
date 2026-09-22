# Verifier Fidelity Audit

This audit compares the in-process Python scorers in `benchlocal_cli/scoring/` with the vendored upstream TypeScript verifier sources under `vendor/*/lib/benchmark.ts` plus the StructOutput sandbox verifier in `vendor/StructOutput-15/verification/core.mjs`.

> If you landed here because a run failed and you suspect the verifier, read [FAILURE_TRIAGE.md](./FAILURE_TRIAGE.md) first — most `verifier_fail`s are model misses, and that doc carries the burden-of-proof policy for escalation.

## Scope

In scope: `tool_call.py`, `reason_math.py`, `instruct_follow.py`, `struct_output.py`, and `data_extract.py`.

Out of scope: sandbox-only packs (`bugfind-15`, `hermesagent-20`, `cli-40`, `aider-polyglot-30`, `humaneval-plus-30`, `lcb-v6-30`) because they either already execute an upstream verifier runtime or use separate dataset-gated answer matching.

## Gap Table

| Pack | Upstream behavior | Local status after this audit | Residual risk |
| --- | --- | --- | --- |
| ToolCall-15 | True multi-turn tool loop with dynamic tool results and per-scenario `evaluate(state)`. Dependent chains stop after the first correct call until a tool result is available. | The scorer keeps the PR #31 dependent-prefix approximation, and `tools/build-packs.js` now preserves `dependent: true` for TC-03, TC-07, TC-08, and TC-15 so regeneration no longer erases it. | Still a single-response approximation; it cannot validate second-turn argument propagation from tool results. |
| ReasonMath-15 | Two-axis scoring: final `ANSWER: ` line versus canonical/accepted/partial answers, plus trace checkpoints. The upstream composite uses a score >= 85 pass threshold. | `reason_math.py` retains the upstream-style answer axis, trace axis, and 70/30 composite in `verifier_trace`, but BenchLocal pass/fail is answer-authoritative: a full 2/2 answer passes and trace remains diagnostic. | This intentionally differs from the upstream composite threshold because notation-specific checkpoint matching can miss equivalent reasoning. Partial and wrong answers still fail regardless of trace score. |
| InstructFollow-15 | Fifteen scenario-specific evaluators with exact counts, ordering, closed-set membership, punctuation, paragraph/list shape, and negative constraints. | **v2.0.0 (#143):** IF-05–IF-09, IF-11 and IF-13 — which used to pass any non-empty answer (`format_regex: .+`) — run line-for-line Python ports of upstream's evaluators (`scoring/instruct_follow_upstream.py`) with upstream's pass rule (score >= 85). Parity with the vendored TypeScript is tested on 404 recorded cases and re-checked live under node (`tests/test_upstream_verifier_parity.py`). | The other eight scenarios keep the earlier shallow assertions (e.g. IF-01 checks a numbered-list regex and a word cap, not upstream's four constraints). Upstream's own IF-11 check rejects a label on its own line (`I.` then its sub-items), because its `^(I|II|III)\.\s` runs on trimmed lines — the port reproduces that faithfully. |
| StructOutput-15 | Upstream sandbox verifier checks exact JSON/CSV values, plus scenario-specific YAML/TOML/SQL/ICS/XML/Markdown/Mermaid/HTML/BSON rules and output-discipline scoring. | **v2.0.0 (#143):** SO-04 (TOML), SO-05 (SQL), SO-06 (ICS), SO-09 (XML), SO-11 (Mermaid), SO-12 (HTML) and SO-15 (BSON boundary) — formerly `format_regex: .+` — run Python ports of `verification/core.mjs` plus `lib/benchmark.ts` axis scoring (`scoring/struct_output_upstream.py`); pass = score >= 85, i.e. parseable, correct, and no wrapper prose (a single code fence is allowed). Same parity tests as InstructFollow. | The other eight scenarios keep parse/header approximations; JSON/CSV checks are still partial rather than exact. Upstream's SO-11 check accepts only `flowchart TD` and `C -- Yes -->`/`B -->\|Valid\|` edges, so standard Mermaid such as `graph TD` with `C -->\|Yes\| D` fails — 0 of 76 distinct saved answers pass it. Reproduced faithfully. |
| DataExtract-15 | Parses JSON, compares every expected atomic field, handles arrays with scenario anchors, applies numeric tolerance, and records compliance notes for shape/extra/missing fields. `pass` means score >= 85. | `data_extract.py` now uses the pack's `expected` payload when present and applies upstream-style recursive scoring with object-array anchors and verifier traces. Legacy field-level assertions remain supported for ad hoc scenarios. | The local result is still binary, so upstream partial status is represented as failure with `upstream_style_score` in `verifier_trace`. |
| AnswerMatch | No corresponding vendored TS verifier for the new reasoning-suite packs. | Not changed. | Standalone local grader by design. |

## Expected Score Movement

**InstructFollow-15 / StructOutput-15 v2.0.0 (#143):** scores can only go down, and v2 results are not comparable with v1.x. A junk answer used to score 7/15 on each pack; it now scores 0/15. `benchlocal-cli rescore` re-grades saved results with the current verifiers — across the 115 saved club-3090 runs where nothing but these fourteen scenarios changed, pass@1 moved **IF 90.5% → 80.7%** and **SO 92.5% → 70.4%**. Rescoring a May-era result can also move *other* scenarios, because it applies every verifier fix made since; compare within the ported scenarios when isolating this change.


Correct-final-answer-only ReasonMath responses that omit published checkpoints now move from fail to pass while retaining an upstream-style score around 70 and a zero trace diagnostic. DataExtract responses that only include the first eight required fields can move down because all expected fields are scored.

ToolCall scores should not shift relative to PR #31, but the fix prevents future `node tools/build-packs.js ToolCall-15` runs from reverting TC-03/07/08/15 to strict all-calls-required assertions.
