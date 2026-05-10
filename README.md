# benchlocal-cli

CLI port of [BenchLocal](https://github.com/stevibe/BenchLocal) quality bench packs. Runs LLM behavioral evaluations (tool-call · instruction-follow · structured output · numeric reasoning · data extraction · debug · multi-tool agent · CLI exec) against any OpenAI-compatible endpoint, with deterministic verifier-backed scoring (no LLM-as-judge).

Companion to [club-3090](https://github.com/noonghunna/club-3090) — primarily intended for measuring quality on quantized models served by club-3090's compose stack, but works against any OpenAI-compatible API.

## Why this exists

BenchLocal is a great Electron desktop app, but our use case (validation gating compose releases on a headless inference rig) wants a CLI. This repo ports BenchLocal's MIT-licensed bench packs to a Python CLI that:

- Hits any OpenAI-compatible HTTP endpoint
- Runs the canonical 8 BenchLocal pack scenarios with verifier-backed scoring
- Supports `--quick` / `--medium` / `--full` modes targeting a 30-45 min total runtime budget
- Outputs paste-ready markdown for benchmark tables + JSON for machine consumption
- Keeps quantized-model quality measurement light enough to run as a CI gate

## Status

🟢 **Beta — full BenchLocal prompt fidelity, reasoning-model aware, sandbox-capable, plus eval-expansion track.** JSONL packs are generated from vendored upstream TypeScript mirrors; deterministic packs use upstream system prompts and scenario prompts verbatim. Requests default to `chat_template_kwargs: {enable_thinking: false}` so reasoning-capable models do not spend the benchmark token budget on hidden deliberation. BugFind-15, HermesAgent-20, CLI-40, and **AiderPolyglot-30** now run through Docker-hosted HTTP verifier sandboxes when `--enable-sandboxed-packs` is set.

**v0.9.0** added the eval-expansion track — `aider-polyglot-30` ships as the first non-BenchLocal sandboxed pack: 30-exercise multi-language code-editing bench across cpp/go/java/javascript/python/rust, vendored upstream from `Aider-AI/aider`'s `benchmark.py`. Run with `--pack aider-polyglot-30 --enable-sandboxed-packs`. See [docs/AIDER_POLYGLOT_30.md](docs/AIDER_POLYGLOT_30.md).

## Modes (target)

| Mode | Packs | Budget | Use case |
|---|---|---|---|
| `--quick` | ToolCall-15 + InstructFollow-15 | ~10-15 min | Per-commit gate; pre-push smoke |
| `--medium` (default) | + StructOutput-15 + DataExtract-15 | ~25-30 min | Pre-release; pin bumps; new compose authoring |
| `--full` | + ReasonMath-15 + (BugFind / HermesAgent / CLI when sandboxed) | ~45-60 min | Cross-rig comparison; quality A/B vs another quant |
| `--pack aider-polyglot-30` | aider-polyglot-30 (independent — not bundled in `--quick`/`--medium`/`--full`) | ~15-25 min | Agentic code-editing signal; cross-model quality A/B for IDE-agent / coding workloads |

Pack selection in each mode follows Codex design-review feedback (2026-05-09) — ToolCall + InstructFollow are the primary signals for IDE-agent regressions; StructOutput catches grammar/JSON drift; ReasonMath defers to `--full` because it leans toward generic benchmark behavior rather than agent-stack-specific. AiderPolyglot-30 is run independently because its harness is a batch runner with multi-turn edit/test loops — different shape from the per-scenario BenchLocal packs.

## Pack inventory

| Pack | Verifier type | Status |
|---|---|---|
| **ToolCall-15** | Deterministic — per-scenario asserts on JSON tool-calls | ✅ vendor-generated |
| **InstructFollow-15** | Deterministic — constraint validators | ✅ vendor-generated |
| **StructOutput-15** | Deterministic — JSON / CSV / markdown / YAML-lite validate | ✅ vendor-generated |
| **ReasonMath-15** | Deterministic — numeric/string/regex compare | ✅ vendor-generated |
| **DataExtract-15** | Deterministic — JSON field-match | ✅ vendor-generated |
| **BugFind-15** | **Execution-backed** — candidate-fix verifier sandbox | ✅ sandboxed v0.4 verifier |
| **HermesAgent-20** | **Multi-tool harness** — browser/cron/memory/artifact mocks | ✅ sandboxed v0.4 verifier |
| **CLI-40** | **Linux exec sandbox** — command verifier sandbox | ✅ sandboxed v0.4 verifier |
| **AiderPolyglot-30** | **Multi-language edit/test harness** — wraps upstream `Aider-AI/aider` `benchmark.py` over 30 curated exercises (cpp / go / java / js / python / rust, 5 each) | ✅ sandboxed v0.9 (single-scoreboard) |

## Layout (planned)

```
benchlocal_cli/
├── __init__.py
├── cli.py                  # entry point: `benchlocal-cli run --quick ...`
├── runner.py               # core: send prompts, score, aggregate, output
├── scoring/                # verifier modules (one per pack dimension)
│   ├── __init__.py
│   ├── tool_call.py
│   ├── instruct_follow.py
│   ├── struct_output.py
│   ├── reason_math.py
│   ├── data_extract.py
│   └── _stub.py            # placeholder for execution-backed packs
└── packs/                  # vendored JSONL pack data
    ├── toolcall-15.jsonl
    ├── instructfollow-15.jsonl
    ├── structoutput-15.jsonl
    ├── reasonmath-15.jsonl
    ├── dataextract-15.jsonl
    ├── bugfind-15.jsonl
    ├── hermesagent-20.jsonl
    └── cli-40.jsonl

tests/                      # pytest unit tests for scoring functions
docs/
├── DESIGN.md               # design rationale (why these choices)
├── PACK_FORMAT.md          # JSONL schema each pack file follows
└── INTEGRATION.md          # how club-3090 (or other repos) consume this CLI
```

## Quick start (target UX)

```bash
# install
pip install -e .

# install with sandbox dependencies and build verifier images
pip install -e '.[sandbox]'
bash tools/build-sandboxes.sh

# list available packs
benchlocal-cli list

# run quick mode against a local club-3090 endpoint; thinking is off by default
benchlocal-cli run --quick --endpoint http://localhost:8020 --model qwen3.6-27b-autoround

# diagnostic run with reasoning/thinking enabled and a larger token budget
benchlocal-cli run --quick --endpoint http://localhost:8020 --model qwen3.6-27b-autoround --enable-thinking

# pass vendor-specific request body fields
benchlocal-cli run --quick --endpoint http://localhost:8020 --model qwen3.6-27b-autoround --extra-body '{"foo":"bar"}'

# run full mode with custom timeout per scenario
benchlocal-cli run --full --endpoint http://localhost:8010 --model qwen3.6-27b-autoround --timeout-per-case 60

# run full mode including Docker-backed verifier packs
benchlocal-cli run --full --enable-sandboxed-packs --endpoint http://localhost:8010 --model qwen3.6-27b-autoround

# run a single pack with detailed per-scenario output
benchlocal-cli run --pack toolcall-15 --endpoint http://localhost:8020 --model qwen3.6-27b-autoround

# emit machine-readable JSON instead of markdown
benchlocal-cli run --quick --endpoint http://localhost:8020 --model qwen3.6-27b-autoround --output json > results.json
```


## Reasoning models

`benchlocal-cli` sends `chat_template_kwargs: {"enable_thinking": false}` by default. This keeps BenchLocal-style quality prompts comparable on reasoning-capable models such as Qwen3.6, where default thinking can exhaust `max_tokens` before the final answer is emitted. Use `--enable-thinking` for diagnostic runs; it sets `enable_thinking=true` and bumps request `max_tokens` to `--thinking-max-tokens` (default `4096`) so diagnostic thinking runs have enough budget. Use `--extra-body` to pass any other OpenAI-compatible server extension fields.

## Output (target format)

```
=== benchlocal-cli --medium  (endpoint: http://localhost:8020, model: qwen3.6-27b-autoround, 2026-05-09T10:30) ===

Pack                      | Pass / Total | Score | p50 latency | p95 latency | Status
ToolCall-15 (v1.0.1)      |   14 / 15    |  93%  |     8.2s    |     12.1s   | ✅
InstructFollow-15 (v1.0.0)|   13 / 15    |  87%  |    11.4s    |     17.8s   | ✅
StructOutput-15 (v1.0.0)  |   15 / 15    | 100%  |     6.9s    |      9.2s   | ✅
DataExtract-15 (v1.0.0)   |   12 / 15    |  80%  |     7.3s    |     10.5s   | ✅
─────────────────────────|──────────────|───────|─────────────|─────────────|──────
TOTAL                     |   54 / 60    |  90%  |             |             |

Failure breakdown:
  ToolCall-15           1 wrong-arg-value
  InstructFollow-15     1 word-count-violation, 1 citation-format-fail
  DataExtract-15        2 missing-field, 1 wrong-format
```

## Attribution

This repo ports MIT-licensed bench pack scenarios from [stevibe/BenchLocal](https://github.com/stevibe/BenchLocal) and the individual pack repos:

- [stevibe/ToolCall-15](https://github.com/stevibe/ToolCall-15) (v1.0.1)
- [stevibe/InstructFollow-15](https://github.com/stevibe/InstructFollow-15) (v1.0.0)
- [stevibe/StructOutput-15](https://github.com/stevibe/StructOutput-15) (v1.0.0)
- [stevibe/ReasonMath-15](https://github.com/stevibe/ReasonMath-15) (v1.0.0)
- [stevibe/DataExtract-15](https://github.com/stevibe/DataExtract-15) (v1.0.0)
- [stevibe/BugFind-15](https://github.com/stevibe/BugFind-15) (v1.0.0)
- [stevibe/HermesAgent-20](https://github.com/stevibe/HermesAgent-20) (v1.0.0)
- [stevibe/CLI-40](https://github.com/stevibe/CLI-40) (v1.0.2)

See [`ATTRIBUTION.md`](./ATTRIBUTION.md) for full attribution + license preservation per pack.

## License

MIT — same as upstream BenchLocal. See [`LICENSE`](./LICENSE).

## Contributing

Beta. Pack updates should go through `tools/sync-vendor.sh` and `tools/build-packs.js`; see `CONTRIBUTING.md`.
