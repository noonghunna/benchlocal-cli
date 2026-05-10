# Codex implementation brief — benchlocal-cli v0.8 (diagnostic tooling)

## Context

After v0.7.3 lands, all 3 sandboxed packs use upstream runtimes for grading + the runner correctly orchestrates multi-turn loops. The bench *measures* the right things. v0.8 makes those measurements *usable*: result diffing, per-scenario inspection, trend tracking.

The forensics fields v0.7.2 added (`verifier_trace`, `conversation`, `--sandbox-log-dir`) are the foundation. v0.8 builds the tools that consume them.

**Promoted above eval expansion**: better tooling makes the existing 8 packs more useful before adding new evals. Today's saved JSON has everything needed for diagnosis but requires hand-grep / `python3 -c "import json; ..."` to extract anything meaningful. v0.8 closes that gap.

## Three pieces, one brief

| Piece | What | Time |
|---|---|---|
| **A. `--previous-result PATH` delta** | Compare two runs scenario-by-scenario; emit regression / fix / stable column | 2-3 hr |
| **B. `inspect` subcommand** | `benchlocal-cli inspect <result.json> --scenario X` shows model response + verifier reasoning + trace without manual JSON grep | 3-4 hr |
| **C. Trend tracking** | Append-row history file (`results/quality/history.csv`) + `benchlocal-cli history` query subcommand | 2-3 hr |

Total: ~8-10 hr Codex chunk.

## Starting state — what already works

You start from `master` HEAD (v0.7.3 just shipped, or v0.7.2 if v0.7.3 isn't done yet — both are fine). Don't undo:

- `benchlocal_cli/types.py`: `ScenarioResult.verifier_trace`, `ScenarioRun.conversation` fields exist + serialize. **These are the data v0.8 reads.**
- `--save-json` writes the full `RunResult.to_dict()` JSON. v0.8's input.
- `--sandbox-log-dir` writes per-pack stderr captures. v0.8's `inspect --logs` should reference them.
- v0.7.2 commit `76f8b30` for forensics field shapes.

## Architecture

### Delta comparison (`--previous-result`)

Existing `run` subcommand grows a flag:

```
benchlocal-cli run --full --endpoint ... --model ... \
  --previous-result results/last-good.json \
  [--exit-on-regression]
```

Behavior:
1. Run as normal, save current result
2. Load `--previous-result` JSON, build `{scenario_id: passed}` map (or richer per-scenario record)
3. After run completes, walk current results, classify each scenario:
   - **stable-pass**: passed in both
   - **regression**: passed previously, failed now ⚠
   - **fix**: failed previously, passed now ⭐
   - **stable-fail**: failed in both (look at failure_mode change too)
   - **new**: scenario not in previous (different mode / pack / version)
   - **dropped**: scenario in previous but not in current (e.g., pack version bumped, scenarios removed)
4. Emit delta block in markdown table + `delta` field in saved JSON
5. If `--exit-on-regression` set: exit code 3 when any regression count > 0 (CI-friendly)

Markdown delta column shape:
```
Pack | Pass / Total | Score | Δ (vs last) | Status
toolcall-15  | 11/15 | 73% | +1 (1 fix, 0 regr)  | improved
cli-40       | 18/40 | 45% | -2 (3 regr, 1 fix)  | ⚠ regression
hermesagent  | 12/20 | 60% | unchanged           | stable
```

### Inspect subcommand (`inspect`)

New top-level subcommand alongside `run` and `list`:

```
benchlocal-cli inspect <result.json> [--scenario ID] [--pack ID]
                                     [--failed] [--mode FAILURE_MODE]
                                     [--diff] [--logs DIR] [--format ...]
```

Behavior per filter combo:

```bash
# Single scenario detail (most common):
benchlocal-cli inspect results/run.json --scenario RM-01
  → Renders: scenario metadata, expected, model's full response,
    verifier_trace, request/sampling, latency, conversation if multi-turn

# Pack-wide failure scan:
benchlocal-cli inspect results/run.json --pack hermesagent-20 --failed
  → For each failed scenario: id, failure_mode, detail, brief response excerpt

# Mode filter:
benchlocal-cli inspect results/run.json --failed --mode timeout
  → All scenarios that timed out across all packs

# Side-by-side diff vs another run:
benchlocal-cli inspect results/run.json --scenario RM-01 \
  --diff results/last-good.json
  → Two-column: expected | previous-actual | current-actual | matched-keywords

# Pull associated sandbox logs:
benchlocal-cli inspect results/run.json --scenario CLI-01 \
  --logs results/sandbox-logs/
  → Inspect output + tail of sandbox-cli-40.log
```

Output formatting:
- Default: human-readable markdown to stdout
- `--format json` for piping
- For `verifier_trace` and `conversation` (potentially large): truncate to ~80 lines by default; `--full` flag for unbounded

Read-only — doesn't need endpoint or model. Just a JSON file reader + pretty-printer.

### Trend tracking (`history`)

Two parts: writer (auto-appends after each run) + reader subcommand.

**Writer**: extend `Runner.run()` to optionally append summary row to history file. Driven by env var or new flag:

```bash
benchlocal-cli run --full ... --history-file results/quality/history.csv
```

CSV row schema:
```
timestamp,run_id,mode,endpoint,model,thinking,
total_pass,total,score,
toolcall_pass,toolcall_total,
instructfollow_pass,instructfollow_total,
structoutput_pass,structoutput_total,
dataextract_pass,dataextract_total,
reasonmath_pass,reasonmath_total,
bugfind_pass,bugfind_total,
hermesagent_pass,hermesagent_total,
cli_pass,cli_total,
runner_version,git_commit
```

Append-only. New columns added at end as packs evolve (older rows have empty cells in new columns — readers handle gracefully).

**Reader**: new top-level subcommand:

```bash
benchlocal-cli history [--file PATH] [--model MODEL] [--pack PACK]
                        [--since DATE] [--last N] [--format ...]
```

Examples:
```bash
# Latest 10 runs for a model
benchlocal-cli history --model qwen3.6-27b-autoround --last 10

# Trend on one pack
benchlocal-cli history --pack hermesagent-20 --since 2026-05-01

# Compare two models
benchlocal-cli history --model qwen3.6-27b-autoround --pack cli-40
benchlocal-cli history --model gemma-4-31b-autoround --pack cli-40
```

Output: markdown table or JSON (for plotting).

Minimum file resolution: `--file path/to/history.csv`, `BENCHLOCAL_HISTORY_FILE` env var, or default `./history.csv` if neither provided.

## Phases

### Phase A — Delta comparison (~2-3 hr)

**Goal**: `--previous-result` works end-to-end with markdown + JSON output.

Files to touch:
- `benchlocal_cli/cli.py`: add `--previous-result PATH` and `--exit-on-regression` flags to `run` subparser
- `benchlocal_cli/runner.py`: load previous JSON, classify each current scenario, emit `delta` field in `RunResult`
- `benchlocal_cli/types.py`: extend `RunResult` with optional `delta` field — `{regressions: int, fixes: int, stable_pass: int, stable_fail: int, new: int, dropped: int, regressions_list: [scenario_id], fixes_list: [scenario_id]}`. Per-pack delta nested in `PackResult.delta`.
- Update `_markdown()` in cli.py to render delta column when `result.delta` present
- Tests: `tests/test_delta.py` covering: empty previous, mismatched modes, all-stable, mix of regressions/fixes, missing scenarios

Schema-versioning consideration: if `previous.schema_version != current.schema_version`, log a warning but proceed with best-effort comparison.

### Phase B — Inspect subcommand (~3-4 hr)

**Goal**: `benchlocal-cli inspect <result.json>` covers all filter combinations from spec above.

Files to touch:
- `benchlocal_cli/cli.py`: new `inspect` subparser with all flags
- New `benchlocal_cli/inspect.py` module: result-JSON parser + filter logic + pretty-printer
- `benchlocal_cli/inspect.py` should expose `inspect_result(json_path, filters, formatter)` function so it's importable from tests
- Tests: `tests/test_inspect.py` covering each filter combo, large-trace truncation, missing field tolerance (older v0.5/v0.6 JSON without verifier_trace)

Pretty-printer should handle:
- `verifier_trace` — collapsed by default, unfold with `--full`
- `conversation` — turn-by-turn with role headers; collapsed past 5 turns by default
- `raw_response` — show only `choices[0].message` portion unless `--full`
- Color (only if stdout is a TTY) for pass ✓ / fail ✗ / failure_mode tags

### Phase C — Trend tracking (~2-3 hr)

**Goal**: history CSV written automatically after runs; `history` subcommand queries it.

Files to touch:
- `benchlocal_cli/runner.py`: `Runner.__init__` accepts `history_file: Path | None`. After `run()` completes successfully, append a row.
- `benchlocal_cli/cli.py`: add `--history-file PATH` flag to `run`; new `history` subparser
- New `benchlocal_cli/history.py` module: CSV reader + filter + formatter
- Tests: `tests/test_history.py` — write/append/read round-trip, missing-column tolerance, date filter

CSV column compat:
- Use Python `csv.DictWriter` so adding columns later doesn't break old readers
- Reader treats missing columns as empty strings; doesn't fail
- New rows always include all current columns (older rows just have fewer)

### Phase D — Tests + docs + version bump (~1 hr)

1. Pytest covers Phases A/B/C — target 22+ tests passing
2. README usage section — add brief examples for `inspect` and `history`
3. `docs/DESIGN.md` (if exists) — describe delta/inspect/history schema
4. `pyproject.toml` + `__init__.py` → `0.8.0`
5. CHANGELOG entry
6. `docs/CODEX_REPORT.md` overwrite with v0.8 status

## Constraints

- **Read-only for inspect.** No endpoint, no model, no Docker. Pure JSON-file processing.
- **Backwards compat for older result JSONs.** Files saved with v0.5/v0.6/v0.7.0 don't have `verifier_trace` / `conversation` fields. `inspect` should handle their absence gracefully (just don't render those sections), not error.
- **Schema-versioning awareness.** Compare `schema_version` field across previous/current JSONs in delta mode; warn on mismatch but don't refuse.
- **Don't change the run path's defaults.** History writing should be opt-in via flag/env, not auto-append. Inspect is a separate command, doesn't run anything.
- **Multi-repeat handling.** When `--repeat N > 1` was used, `delta` and `history` should aggregate per-scenario across repeats (e.g., pass-rate not binary). For v0.8, simplest: classify on whichever scenarios were emitted (each repeat is a separate ScenarioRun); document the behavior; revisit if it produces noisy deltas.

## Async report-back protocol

Same as v0.4/v0.6/v0.7/v0.7.3: write `docs/CODEX_REPORT.md` with phase-by-phase status. File `docs/QUESTIONS.md` if you hit a design choice that needs Claude+user input.

## What to ASK rather than guess

- **Schema-version mismatch handling** — if previous JSON is from v0.5 and current is v0.8, what should `delta` do? Best-effort with warning vs refuse vs skip-with-message? My instinct: best-effort with warning, but ask if you find a case where it produces nonsense.
- **History CSV stability vs SQLite migration** — flat CSV is fine for v0.8. If you find a use case where querying becomes painful (e.g., joining across N runs), file a question for v0.8.x SQLite migration.
- **Inspect default truncation thresholds** — 80 lines for trace, 5 turns for conversation are guesses. If real-world usage suggests different defaults, log it in CODEX_REPORT.

## Estimated total effort

- Phase A (delta comparison): 2-3 hr
- Phase B (inspect subcommand): 3-4 hr
- Phase C (trend tracking): 2-3 hr
- Phase D (tests + docs + bump): 1 hr

**Total: ~8-11 hr.** Phase B is the variable — pretty-printer + filter combos can sprawl if not tightly scoped.

## When done

Acceptance gate:
1. `tools/build-sandboxes.sh` + `tools/test-sandboxes.sh` still pass (no sandbox changes)
2. `pytest tests/` passes (target 22+ tests with Phase A/B/C coverage)
3. **Hand-test `inspect`** on the v0.7.2 forensics data we already have: `benchlocal-cli inspect /tmp/qwen-v071-sandboxed.json --pack hermesagent-20 --failed` should produce useful per-scenario detail without me having to write Python.
4. **Hand-test `--previous-result`**: run `--quick` twice on the same model, then once with `--previous-result <first.json>` — delta should show all-stable. Then deliberately break a scenario (e.g., switch endpoint to a different model) and verify regression detection.
5. **Hand-test `history`**: append 3 rows from quick runs, query with `--last 3` and `--pack toolcall-15`.
6. `docs/CODEX_REPORT.md` overwritten with v0.8 status

After acceptance:
- Tag `v0.8.0` (release-notes workflow auto-publishes)
- Update `noonghunna/club-3090`'s `scripts/quality-test.sh` to take advantage of new flags (auto-write history, support `--inspect-last` shortcut)
- v0.8 unblocks repeatable cross-rig regression testing — contributors can now answer "did this Genesis pin bump regress quality?" with a single command.

---

**Cross-reference:**
- v0.7.2 commit `76f8b30` — verifier_trace + conversation + sandbox-log-dir fields (the data v0.8 reads)
- v0.7.3 brief: [`CODEX_BRIEF_V7_3.md`](CODEX_BRIEF_V7_3.md) — Hermes upstream-runtime delegation, ships before v0.8
- Roadmap: [`ROADMAP.md`](ROADMAP.md) — v0.8 promoted ahead of further eval expansion (v0.9+)
- After v0.8: optional bench expansion (lm-eval / BFCL / Aider Polyglot / IDE-agent safety / SWE-bench-lite) per the v0.9+ implementation pattern
