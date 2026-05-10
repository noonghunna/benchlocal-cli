# Attribution — Aider Polyglot lite (`aider-polyglot-30`)

## Upstream sources

This pack delegates execution to two upstream projects:

1. **Aider** — the AI pair-programming tool whose `benchmark/benchmark.py`
   harness drives multi-turn code-edit-and-test scenarios.
   - Repository: <https://github.com/Aider-AI/aider>
   - License: Apache-2.0
   - Pinned commit: see `_sync.json` (`aider.commit`)

2. **polyglot-benchmark** — the 225-exercise multi-language test corpus
   adapted from Exercism. Each exercise is a directory under `<lang>/exercises/`
   containing a problem statement, a stub solution, and unit tests.
   - Repository: <https://github.com/Aider-AI/polyglot-benchmark>
   - License: see upstream README (Exercism exercises are MIT-licensed)
   - Pinned commit: see `_sync.json` (`polyglot_benchmark.commit`)

## What benchlocal-cli adds

- `exercises.json` — a curated 30-exercise subset (5 per language,
  mixing easy/medium/hard difficulty and varied problem types). This
  is the canonical lite slice used by `aider-polyglot-30`.
- `sandboxes/aider-polyglot/Dockerfile` — image build that bakes both
  upstreams at the pinned commits and adds our Python proxy server.
- `sandboxes/aider-polyglot/server.py` — thin HTTP proxy that translates
  benchlocal-cli's `/verify-start` protocol into a single
  `benchmark.py` subprocess invocation, returning aggregate pass-rate
  + per-exercise breakdown.

## Aider Polyglot benchmark methodology reference

The original Aider Polyglot benchmark writeup lives at:
<https://aider.chat/2024/12/21/polyglot.html>

That writeup describes how aider runs against the full 225-exercise
corpus. benchlocal-cli's `aider-polyglot-30` is a deliberate **lite
slice** for sub-25-minute wall clock — it is NOT a substitute for the
full polyglot benchmark and the absolute scores are not directly
comparable to Aider's published numbers.

## Re-sync

When upstream commits drift:
1. Update `_sync.json` `aider.commit` and `polyglot_benchmark.commit`
2. Update `sandboxes/aider-polyglot/Dockerfile` build-arg defaults
3. Run a clean `tools/build-sandboxes.sh aider-polyglot`
4. Boot the image and check `/health` — the startup must successfully
   resolve all 30 exercises in `exercises.json`. If any are missing
   from upstream (renamed / removed), fail loud and fix `exercises.json`
   in the same commit (replace the missing exercise with a comparable
   one in the same language + difficulty band).
