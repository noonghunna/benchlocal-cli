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
| Rust | decimal | hard | arbitrary precision — effectively unpassable, see [Known limitations](#known-limitations) |
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

## Retry budget (`--aider-tries`)

aider's `benchmark.py` gives each exercise **2 tries** by default (`--tries`), and
its public leaderboard runs that default. A retry sees the previous attempt's test
failures, so the budget moves the score more than anything else measured on this
pack: 16/30 → 20/30 from 2 to 4 tries on one model, with sampling, edit format and
thinking held fixed (#184).

- `benchlocal-cli run --pack aider-polyglot-30 --aider-tries 4 …` passes `--tries 4`;
  without the flag the argv is unchanged (no `--tries`), so default runs are as before.
- The batch time cap (`AIDER_BENCHMARK_TIMEOUT_S` and the runner's read timeout)
  scales by `N / 2`, so a 4-try batch isn't killed at the 2-try clock.
- The sandbox echoes the budget it ran as `tries_budget` (with `tries_budget_source`)
  in the batch trace; the result records `aider_tries` when the flag was given.
- **Not leaderboard-comparable** when N ≠ 2. `--previous-result` warns and
  `--exit-on-regression` refuses across different budgets; a result without
  `aider_tries` ran 2.
- An image built before #184 would ignore the request and run 2 tries. The runner
  reads `/health` (`supports_tries`) and refuses `--aider-tries` against such an
  image before the batch starts — rebuild with `bash tools/build-sandboxes.sh aider-polyglot`.

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

### Thinking on / off

Aider makes its own model calls, so the runner hands the resolved reasoning
switch to the sandbox, which writes it into aider's model settings and loads
them with `--read-model-settings`. The switch reaches the endpoint as top-level
`chat_template_kwargs` (or `reasoning_effort`) on every aider request:

| Flag | Sent on the wire |
|---|---|
| (none) | the pack default: `enable_thinking: false` |
| `--enable-thinking` | `enable_thinking: true` |
| `--no-thinking` | `enable_thinking: false` |

Endpoints without a reasoning switch get nothing extra.

⚠️ **Runs before the #172/#173 fix are not comparable on this pack.** The
settings file was written but never loaded, so every mode, including the
default, ran at the endpoint's own default (thinking ON for Qwen3-style
templates), whatever the result JSON's `thinking_mode` said.

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

- **`rust/decimal` caps the pack at 29/30 (#182).** The exercise's reference
  solution (`.meta/example.rs`) uses the `num-bigint` and `num-traits` crates,
  listed in `.meta/Cargo-example.toml`, while the exercise's own `Cargo.toml`
  declares no dependencies. With those crates declared, the reference passes all
  44 tests in the sandbox image, which can fetch them. But aider's `benchmark.py`
  removes `Cargo.toml` (and `CMakeLists.txt`) from the files the model may edit
  (`ignore_files`), so the model cannot declare them: the only passing answer is
  arbitrary-precision arithmetic written by hand in plain Rust. In practice
  `rust/decimal` passed in 0 of the 37 batches we have results for (to
  2026-10-10). aider's public leaderboard runs the same `benchmark.py`, so it hits
  the same wall.
  - **Effect:** one exercise every model fails, so comparisons between runs and
    between models are unaffected; the absolute ceiling is 29/30 (96.7%), not
    30/30. The 50% pass threshold is not affected in practice.
  - **Why it is kept:** swapping the exercise or adding the crates would make
    scores incomparable with every existing result and depart from aider's
    method. Revisit when the 30-exercise set is next revised.
  - The other 29 reference solutions pass their tests in the image (checked
    2026-10-10).
- **virtiofs log mounts (e.g. Colima `mountType: virtiofs`)**: with
  `--sandbox-log-dir` on, the job dir is a host bind mount, and CMake's build
  tree inside it can intermittently hit `EDEADLK`. The batch survives (the
  result walk only reads `<lang>/exercises/practice/<name>/.aider.results.json`
  and retries or skips an unreadable entry, #170), but an affected cpp exercise
  can still fail its build. `--sandbox-log-dir none` keeps the build tree off
  the mount.
- **Wall clock dominates**: `pass_rate` doesn't separate "model edits
  too slow" from "model edits incorrect". Use `inspect` to surface
  per-exercise duration if a regression looks latency-related.
- **Edit format is fixed at `whole`** by default (broadest model-compat
  + simplest grading). Models that are stronger on `diff` or `udiff`
  formats will under-perform here. Override via
  `raw_scenario.default_edit_format` if needed.
- **Tests run in-image, and this pack's network is NOT isolated**: a
  model-emitted edit could in principle exfiltrate via the unit tests. The
  image is `--rm`, but that is the only containment *for this pack* — do not
  read it as network containment. `sandbox.py` sets
  `network_isolated=False,  # aider needs to call out to model_endpoint`, and
  that is a real requirement rather than an oversight: the exercise loop drives
  `aider` against the runner's model endpoint from inside the container, so the
  container must be able to reach it. The runner now honours `network_isolated`
  — packs declaring `True` (`cli-40`, `humaneval-plus-30`, `lcb-v6-30`) run
  under `--network none` — but aider is deliberately not one of them, and the
  two are mutually exclusive by construction: `sandbox.py` raises if a pack
  declares isolation while also needing
  `--add-host host.docker.internal:host-gateway`. That flag is still added for
  this pack, now only when the model endpoint is a loopback address (the case
  `resolve_endpoint_for_container()` rewrites); for a LAN endpoint it was
  vestigial and is no longer emitted. Treat exercise code here as running with
  full outbound network access, and do not point this pack at untrusted
  exercises or an untrusted model.
