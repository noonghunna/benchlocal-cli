# Failure Triage — model, prompt, harness, or verifier?

Every failed scenario gets one label, and the burden of proof sits on
anyone claiming the pack or the runner is at fault. This policy exists
because `verifier_fail` was repeatedly mis-labeled as "test case bug" or
"verifier issue" when the model had simply interpreted the prompt
differently (#123 / #124 went through three rounds of this before the
policy landed).

## Default: model miss

If the prompt set the expectation clearly, the verifier is aligned with
the prompt, and the model's answer still did not match — that is a
**model miss**, not a test case failure and not grounds for a test
redesign. Guard the verifier from suspicion arising from model
interpretation; only escalate with evidence.

## The three escalation classes

| Class | Definition | Evidence bar |
|---|---|---|
| **Harness / environment** | Mechanical defect in runner code: response extraction, request shape, scoring plumbing | Reproducible independent of model quality; provable from code or transcript. Examples: the multi-fence extraction drop (#116/#117), the stray trailing-fence truncation (#120), the cli-40 contradictory thinking params (#129) |
| **Verifier bug** | The prompt clearly compelled the model's answer and the verifier rejected it | Name the exact prompt text that compels the answer, weighed against the scenario's design `description`. Example: RM-04's format rules accept an answer prefix but the accepted list omitted the prefix+unit combination (#125, fixed in #132) |
| **Prompt gap** | The prompt is silent or self-inconsistent on a dimension the verifier grades | Name the missing statement. The fix is a prompt clarification (pack bump + re-baseline), never a verifier loosening. Examples recorded in #123/#124's closure: DE-10's undeclared field types, SO-07's undeclared `user` wrapper |

Everything else is a model miss — including "the model's reading is also
reasonable." Reasonable-but-non-matching interpretations are what the
packs exist to measure.

## Method

1. **Reproduce at temperature 0 with both thinking arms.** Failure in one
   arm only → model variance, stop. Identical failure in both arms →
   **flag the scenario for review**; this eliminates flakiness as the
   explanation, but is **not** proof of a pack bug by itself.
2. **Read the scenario's `raw_scenario` source and its `description`** —
   the description documents design intent (e.g. DE-10's "return null for
   genuinely uncertain fields" lists exactly which fields are uncertain).
3. **Read the format contract**: system rules, schema, and the extractor
   contract for format-based packs (`docs/EXTRACTOR_NOTES.md`).
4. **Ask: does the prompt compel the model's answer?**
   - Yes, and the verifier rejected it → **verifier bug**. Fix the
     verifier or its accepted list; the model did what it was told.
   - The prompt is silent/ambiguous on the graded dimension → **prompt
     gap**. Optionally fix by clarifying the prompt (version bump +
     re-baseline); never by loosening the verifier.
   - No — the prompt supported the verifier's reading → **model miss**.
5. Harness bugs announce themselves mechanically: the same defect bites
   every model regardless of answer quality, and you can point at the line
   of code or the request payload. When found, that is the first thing to
   rule out — it invalidates the numbers entirely (#126's validity check
   exists to catch the thinking-mode variants automatically).

## Anti-patterns

- Labeling `verifier_fail` a "verifier issue" because the model's
  answer seems reasonable. Reasonable ≠ what the prompt asked for.
- Treating identical-at-temp-0 failure as proof. It is a review flag;
  the proof is the prompt-text/design-description analysis in step 4.
- Fixing a model miss by widening the verifier. That turns the pack into
  a different test and silently re-baselines every historical score.

## Worked example

[PROMPT_VERIFIER_AUDIT.md](./PROMPT_VERIFIER_AUDIT.md) (2026-08-12) is
the reference application of this policy: every pack audited against its
verifier, anchored on a real model's identical-at-temp-0 failures, with
zero verifier bugs surviving the bar and the two surviving prompt gaps
(SO-07, dataextract field types) routed to prompt clarification rather
than verifier loosening.
