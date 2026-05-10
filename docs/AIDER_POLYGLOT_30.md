# aider-polyglot-30 — Aider Polyglot lite slice

**Pack id:** `aider-polyglot-30`
**Architecture:** single-scoreboard (1 scenario per pack)
**Runtime:** ~10-20 min wall clock (varies by model + threads)
**Image:** ~2-2.5 GB (Python + JDK 21 + Go + Rust + Node)

## What it tests

Multi-language code editing across **C++, Go, Java, JavaScript, Python,
Rust**. Each exercise is a problem statement + stub solution + unit
tests; the model uses aider's edit-format to modify source files until
tests pass. Tests **edit-format reliability** (does the model produce
diffs aider can apply?) AND **algorithmic correctness** (do the tests
pass after edits?).

## Why this pack matters

Per the v0.7→v0.8 work, `bugfind-15` already exercises code-fixing
behavior — but only Python, only one-shot. This pack closes the gap on:
- **Multi-language**: 6 languages, not just Python
- **Multi-turn editing**: aider may attempt 2-3 edits per exercise if
  tests fail on the first try
- **Edit-format adherence**: does the model emit text aider can parse
  into file edits?

It's the closest signal we have to "will this model behave inside an
editor like Cursor / Continue / aider itself."

## Curated 30-exercise list

5 exercises per language, mixing difficulty + problem types:

| Language | Exercise | Difficulty | Type |
|---|---|---|---|
| C++ | clock | easy | math |
| C++ | crypto-square | medium | string |
| C++ | binary-search-tree | medium | data structure |
| C++ | bank-account | medium | concurrency |
| C++ | complex-numbers | medium | math |
| Go | bowling | medium | state machine |
| Go | connect | hard | board game |
| Go | crypto-square | medium | string |
| Go | book-store | medium | optimization |
| Go | alphametics | hard | constraint solving |
| Java | affine-cipher | medium | math |
| Java | bank-account | medium | concurrency |
| Java | change | medium | dynamic programming |
| Java | bowling | medium | state machine |
| Java | alphametics | hard | constraint solving |
| JavaScript | affine-cipher | medium | math |
| JavaScript | complex-numbers | medium | math |
| JavaScript | binary | easy | math |
| JavaScript | book-store | medium | optimization |
| JavaScript | bottle-song | easy | string |
| Python | dominoes | medium | graph |
| Python | dot-dsl | medium | DSL parsing |
| Python | connect | hard | board game |
| Python | bowling | medium | state machine |
| Python | book-store | medium | optimization |
| Rust | acronym | easy | string |
| Rust | decimal | hard | arbitrary precision |
| Rust | dot-dsl | medium | DSL parsing |
| Rust | doubly-linked-list | hard | lifetimes |
| Rust | bowling | medium | state machine |

Selection criteria (committed to `vendor/AiderPolyglot-30/exercises.json`):
- 5 per language, all 6 languages represented
- Mix of easy / medium / hard
- Diverse problem types (state machines, DSL parsing, math, data structures,
  concurrency, string manipulation, dynamic programming, optimization)
- Stable upstream names (less likely to be renamed/removed)

## Single-scoreboard semantics

The pack has **1 scenario** named `aider-polyglot-30-batch`. One
`/verify-start` call → spawn `benchmark.py` once → return aggregate.
Per-exercise pass/fail is in `verifier_trace.upstream_per_exercise`.

This is different from `bugfind-15` / `cli-40` / `hermesagent-20` (one
scenario per test case). The trade-off:

**Lose**: top-level scenario delta only sees the aggregate flip
(threshold-pass changing). For per-exercise regressions: drill into
`inspect --scenario aider-polyglot-30-batch`.

**Gain**: matches aider's natural batch shape. No fake per-scenario
latencies. No cache lifecycle problems. ~70% fewer architectural risks
than bending `/verify-start` into a batch protocol.

## Pass criterion

- Default threshold: `pass_rate >= 0.5` (15 / 30 exercises pass)
- Configurable per-run via `raw_scenario.default_pass_threshold`
- `--previous-result` delta surfaces real `pass_rate_delta` (e.g.,
  `23/30 → 20/30 (-10pp)`) — not just threshold flips, since `pass_rate`
  is first-class on `ScenarioResult`

## Running

```bash
# Build (one-time, ~10-15 min on first build)
bash tools/build-sandboxes.sh aider-polyglot

# Bench against your model
benchlocal-cli run \
  --pack aider-polyglot-30 \
  --enable-sandboxed-packs \
  --endpoint http://localhost:8010 \
  --model qwen3.6-27b-autoround \
  --save-json results/aider-polyglot.json
```

The runner sets `OPENAI_BASE_URL` + `OPENAI_API_BASE` inside the
container to a host-reachable rewrite of your endpoint
(`localhost` → `host.docker.internal`).

## Re-syncing upstream

Both upstream commits are pinned in
`vendor/AiderPolyglot-30/_sync.json`. To bump:

1. Update `_sync.json` with the new aider + polyglot-benchmark commits
2. Update `sandboxes/aider-polyglot/Dockerfile` build-arg defaults
3. Rebuild: `tools/build-sandboxes.sh aider-polyglot`
4. Boot the image and check `/health` — must report
   `exact_match: true` on `exercises` and CLI signature with all required
   flags. If any of the 30 canonical exercises were renamed/removed
   upstream, fail loud.
5. If renamed: update `exercises.json` in the same commit (replace
   missing exercise with comparable one in same language + difficulty).

## Image preflight

Aider-polyglot is the largest sandbox image (~2-2.5 GB — 6 language
toolchains). On rigs with <30 GB free, run
`docker system prune -a -f --volumes` before building.

## Known limitations

- **Wall clock dominates**: `pass_rate` doesn't separate "model edits
  too slow" from "model edits incorrect". Use `inspect` to surface
  per-exercise duration if a regression looks latency-related.
- **Edit format is fixed at `whole`** by default (broadest model-compat
  + simplest grading). Models that are stronger on `diff` or `udiff`
  formats will under-perform here. Override via
  `raw_scenario.default_edit_format` if needed.
- **Tests run in-image**: a model-emitted edit could in principle
  exfiltrate via the unit tests, but the image is `--rm` and exercises
  are network-isolated by default.
