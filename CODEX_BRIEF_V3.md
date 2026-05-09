# Codex implementation brief — benchlocal-cli v0.3 (reasoning-model handling)

## Context

v0.2 (commits `93a299c` → `b29ef5e`) restored verbatim BenchLocal upstream fidelity via the vendor + extractor architecture. Live validation against a club-3090 Qwen3.6-27B compose surfaced a config gap, NOT a fidelity issue:

- 14 of 19 failures in `--quick` mode were **silent-empty content** with `tokens_completion: 1024` (max budget exhausted)
- Diagnosis: Qwen3.6-27B is a reasoning model. Default behavior is `enable_thinking: true`, which routes most output to `<think>...</think>` blocks. Constraint-heavy InstructFollow-15 prompts trigger long deliberation that exhausts the 1024-token budget before the actual answer lands.
- Confirmation: same IF-01 prompt with `chat_template_kwargs: {enable_thinking: false}` → 50-token clean answer, no exhaustion.

BenchLocal upstream was calibrated on non-reasoning models (gpt-4 / claude / etc.). Running it against reasoning-capable models in default mode produces uninterpretable scores — most failures aren't quality regressions, just budget exhaustion.

The fix: make benchlocal-cli reasoning-model-aware by default, with an explicit override for diagnostic thinking-mode runs.

This aligns with club-3090's existing convention — every test script in that stack (`verify-full.sh`, `bench.sh`, `soak-test.sh`, `verify-stress.sh`, `power-cap-sweep.sh`) passes `chat_template_kwargs: {enable_thinking: false}` by default.

## Goals

1. **Default behavior: thinking OFF.** When the runner sends a chat-completions request, it should include `chat_template_kwargs: {enable_thinking: false}` automatically. This makes scores comparable across both non-reasoning and reasoning-capable models.

2. **Override flag: `--enable-thinking`.** When set, drop the auto-injection, bump max_tokens (since thinking eats budget), and read `reasoning_content` / `reasoning` fields as fallback when `content` is empty.

3. **Generic escape valve: `--extra-body '{"key": "value"}'`.** Pass arbitrary fields through to the chat-completions request body. Useful for vendor-specific options beyond `chat_template_kwargs`.

4. **Quality line annotates thinking state.** The compose `Quality:` schema field should distinguish thinking-on vs thinking-off runs explicitly.

## Phases

### Phase A — Pack-level default (~15 min)

Every pack's metadata line should declare `chat_template_kwargs: {enable_thinking: false}` in `sampling_defaults`. Either:

(a) Bake into `tools/build-packs.js` so future re-syncs always include it, OR
(b) Add a post-extraction step in build-packs that injects it into the metadata line

I'd recommend (a) — a single line in the extractor's metadata builder so re-syncs of upstream don't lose the augmentation. Document the augmentation in `docs/EXTRACTOR_NOTES.md` so future-Claude understands why our generated metadata includes a field upstream's `benchlocal.pack.json` doesn't.

After this change, regenerate all 8 packs:

```bash
node tools/build-packs.js --all
```

The 8 JSONL files should now have `"chat_template_kwargs": {"enable_thinking": false}` in their metadata lines.

### Phase B — CLI flag wiring (~15 min)

Add two flags to `benchlocal-cli run`:

```
--enable-thinking          (boolean, default false)
                           When set, drop the auto-injected enable_thinking=false
                           AND bump default max_tokens to 4096 (configurable via
                           --thinking-max-tokens N).

--extra-body JSON          (string, optional)
                           Arbitrary JSON object merged into the request body.
                           Wins over pack defaults but loses to per-scenario
                           sampling_overrides if any conflict.
```

Update `benchlocal_cli/cli.py` argparse + propagate to `Runner` constructor.

### Phase C — Runner-level handling (~15 min)

In `benchlocal_cli/runner.py`:

1. **Request builder** (`build_request`):
   - If `--enable-thinking` is NOT set: ensure `chat_template_kwargs.enable_thinking` is `false` in the request (pack default + force).
   - If `--enable-thinking` IS set: don't inject; respect whatever the pack metadata says (which after Phase A will be `false`, so we override to `true` when the flag is set).
   - Apply `--extra-body` after sampling defaults but before per-scenario overrides — so scenario overrides still win.
   - When thinking is enabled, bump `max_tokens` to `--thinking-max-tokens` (default 4096) UNLESS scenario explicitly overrides.

2. **Response reader** (where it currently reads `delta.content` from the response):
   - Look for content in the order: `delta.content` → `delta.reasoning_content` → `delta.reasoning`
   - Use whichever is non-empty; if multiple non-empty, prefer `content` (final answer)
   - Optionally annotate `ScenarioRun` with which field-path was used (helpful for debugging)

This is the same 3-field-path pattern from club-3090's `power-cap-sweep.sh` fix (commit `1528b59`) — distinct OpenAI-compatible servers emit reasoning differently, and the safe play is to check all three.

### Phase D — Output annotation (~5 min)

In the markdown table output, add a column or footer line indicating thinking state:

```
=== benchlocal-cli --quick  (endpoint: ..., thinking=off, 2026-05-09T15:30) ===
                                                       ^^^^^^^^^^^^
```

In the JSON result schema, add `thinking_enabled: bool` at the top level so consumers (club-3090's `quality-test.sh` quality-line generator) can include it in the compose schema field:

```yaml
Quality:   toolcall-15 14/15 (93%) · instructfollow-15 12/15 (80%) (--quick, thinking=off, 2026-05-09)
```

### Phase E — Tests + docs (~10 min)

1. Unit tests:
   - Request builder injects `enable_thinking: false` by default
   - `--enable-thinking` flag drops the injection
   - `--extra-body` JSON merges correctly (wins over defaults, loses to scenario overrides)
   - Response reader walks the 3 field paths in order

2. Update `README.md` "Quick start" section to mention the thinking-off default + `--enable-thinking` override.

3. Update `docs/DESIGN.md` "Modes" section with the thinking-state semantics.

4. Update `docs/PACK_FORMAT.md` to document `chat_template_kwargs` in `sampling_defaults`.

5. Add a section to `docs/EXTRACTOR_NOTES.md` explaining the metadata augmentation (why our generated JSONL includes `chat_template_kwargs` even though upstream `benchlocal.pack.json` doesn't).

6. Update `docs/CODEX_REPORT.md` with v0.3 status.

## Validation gate

- [ ] `pytest tests/` passes (with new tests for the flag handling + 3-field-path reader)
- [ ] All 8 packs regenerated; metadata lines show `chat_template_kwargs: {enable_thinking: false}`
- [ ] `pip install -e .` works in fresh venv
- [ ] `benchlocal-cli list` works (unchanged from v0.2)
- [ ] `benchlocal-cli run --pack toolcall-15 --endpoint <mock>` — request body shows `chat_template_kwargs.enable_thinking: false`
- [ ] `benchlocal-cli run --pack toolcall-15 --endpoint <mock> --enable-thinking` — request body shows `chat_template_kwargs.enable_thinking: true` AND max_tokens bumped to 4096
- [ ] `benchlocal-cli run --pack toolcall-15 --endpoint <mock> --extra-body '{"foo":"bar"}'` — request body has `foo: bar`
- [ ] Response reader test: when `content` is empty but `reasoning_content` has text, scenario passes (assuming verifier asserts pass)
- [ ] README + DESIGN + PACK_FORMAT + EXTRACTOR_NOTES + CODEX_REPORT all updated

## Constraints

1. **Backwards-compatible JSON output schema** — adding `thinking_enabled` at the top level is fine; don't reshape existing fields. Consumers of v0.2 JSON should still parse v0.3 JSON.
2. **Don't break the 3 stubbed packs** — they should still emit `verifier_not_implemented` regardless of thinking state.
3. **No new runtime deps** — stdlib + httpx + jsonschema only (already in pyproject.toml).
4. **Stay within v0.2 file structure** — no new top-level dirs; new modules only if necessary inside `benchlocal_cli/`.

## Async report-back protocol

Same as v0.2 — see `CODEX_BRIEF.md` "How to communicate back to Claude". File `docs/QUESTIONS.md` if blocked, build all phases, push to origin/master, overwrite `docs/CODEX_REPORT.md` with v0.3 status when done.

## Background context for the diagnosis

If you want to verify the problem before designing the fix, here's the live test result that surfaced it (from club-3090 — Qwen3.6-27B dual on port 8010, 2026-05-09):

```
Pack                       Pass / Total   Score
toolcall-15 (v1.0.1)         9 / 15        60%
instructfollow-15 (v1.0.0)   2 / 15        13%

Of 19 failures: 14 had content=null but tokens_completion=1024 (max).
Manual repro of IF-01 with chat_template_kwargs.enable_thinking=false → 50-token correct answer.
```

The same 30-prompt --quick run, after v0.3 ships, should produce dramatically better instruct-follow numbers and slightly better tool-call (since 1 of the 6 tool-call failures was also silent-empty).

Goal of v0.3: make benchlocal-cli's defaults right for reasoning-capable models, so the score is meaningful out of the box. v0.3 gives users (and us) a comparable baseline across reasoning + non-reasoning models.

## Estimated effort

~45 min total Codex work. Phase A is the smallest (single-line metadata augmentation in extractor); Phase C is the highest-judgment (3-field-path response reading needs care).
