# benchlocal-cli

CLI for running LLM behavioral evaluation packs against any OpenAI-compatible endpoint, with deterministic verifier-backed scoring (no LLM-as-judge). Two pack families today:

- **BenchLocal** ports — tool-call · instruction-follow · structured output · numeric reasoning · data extraction · debug · multi-tool agent · CLI exec (8 packs from [stevibe/BenchLocal](https://github.com/stevibe/BenchLocal), MIT-licensed).
- **Eval-expansion track** — additional packs vendored from upstream open-source benches. v0.9 ships `aider-polyglot-30` (multi-language code editing via [Aider-AI/aider](https://github.com/Aider-AI/aider)'s `benchmark.py`).

Companion to [club-3090](https://github.com/noonghunna/club-3090) — primarily intended for measuring quality on quantized models served by club-3090's compose stack, but works against any OpenAI-compatible API.

## Why this exists

We needed a headless, scriptable quality gate for compose-release validation on an inference rig. BenchLocal is a great Electron desktop app for human-in-the-loop quality A/B; this repo turns the same pack semantics into a CLI that:

- Hits any OpenAI-compatible HTTP endpoint
- Runs BenchLocal's 8 deterministic-verifier packs + agentic eval packs (currently 1: `aider-polyglot-30`)
- Supports `--quick` / `--medium` / `--full` budget modes for the BenchLocal packs (~30-45 min for `--full`); agentic packs run independently via `--pack <name>`
- Outputs paste-ready markdown for benchmark tables + JSON for machine consumption
- Keeps quantized-model quality measurement light enough to run as a CI gate

## Status

🟢 **Beta — full BenchLocal prompt fidelity, reasoning-model aware, sandbox-capable, plus eval-expansion track.** JSONL packs are generated from vendored upstream TypeScript mirrors; deterministic packs use upstream system prompts and scenario prompts verbatim. Requests default to `chat_template_kwargs: {enable_thinking: false}` so reasoning-capable models do not spend the benchmark token budget on hidden deliberation. BugFind-15, HermesAgent-20, CLI-40, and **AiderPolyglot-30** run through Docker-hosted HTTP verifier sandboxes when `--enable-sandboxed-packs` is set.

**v0.9.0** added the eval-expansion track — `aider-polyglot-30` ships as the first non-BenchLocal sandboxed pack: 30-exercise multi-language code-editing bench across cpp/go/java/javascript/python/rust, vendored upstream from `Aider-AI/aider`'s `benchmark.py`. Run with `--pack aider-polyglot-30 --enable-sandboxed-packs`. See [docs/AIDER_POLYGLOT_30.md](docs/AIDER_POLYGLOT_30.md).

## Modes

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

## Repo layout

```
benchlocal_cli/                 # Python package (CLI entry point + runner)
├── __init__.py
├── cli.py                      # `benchlocal-cli run …` entry point
├── runner.py                   # core: dispatch packs, score, aggregate, output
├── sandbox.py                  # SandboxClient — Docker lifecycle for sandboxed packs
├── types.py                    # ScenarioRun / ScenarioResult / PackRun shapes
├── scoring/                    # verifier modules (one per deterministic pack)
│   ├── tool_call.py
│   ├── instruct_follow.py
│   ├── struct_output.py
│   ├── reason_math.py
│   ├── data_extract.py
│   └── _stub.py                # dispatch to Docker verifier when --enable-sandboxed-packs
└── packs/                      # vendored JSONL pack data
    ├── toolcall-15.jsonl
    ├── instructfollow-15.jsonl
    ├── structoutput-15.jsonl
    ├── reasonmath-15.jsonl
    ├── dataextract-15.jsonl
    ├── bugfind-15.jsonl
    ├── hermesagent-20.jsonl
    ├── cli-40.jsonl
    └── aider-polyglot-30.jsonl

sandboxes/                      # Docker images for execution-backed verifier packs
├── bugfind/                    # Python pytest harness for BugFind-15 candidate fixes
├── cli/                        # Linux exec sandbox for CLI-40 commands
├── hermes/                     # Hermes-agent runtime + Node grader for HermesAgent-20
└── aider-polyglot/             # Aider + polyglot-benchmark for AiderPolyglot-30

vendor/                         # vendored upstream sources for pack generation
├── ToolCall-15/  …             # one dir per BenchLocal pack (TypeScript mirror)
└── AiderPolyglot-30/           # exercise manifest + sync metadata

tools/
├── build-packs.js              # generates JSONL packs from vendor/ TypeScript mirrors
├── build-sandboxes.sh          # builds the Docker images under sandboxes/
└── sync-vendor.sh              # bumps vendored upstream pin

tests/                          # pytest unit tests (33+ tests; runs against the JSONL packs)

docs/
├── AIDER_POLYGLOT_30.md        # aider-polyglot-30 pack details + cross-rig run guide
├── DESIGN.md                   # design rationale (why these choices)
├── EXTRACTOR_NOTES.md          # how vendor/ → JSONL extraction works per pack
├── HERMES_V073_AB.md           # forensic notes from the Hermes A/B run
├── INTEGRATION.md              # how club-3090 (or other repos) consume this CLI
├── PACK_FORMAT.md              # JSONL schema each pack file follows
├── SANDBOX_PROTOCOL.md         # HTTP protocol the sandboxed packs implement
└── VENDOR_SYNC.md              # how to bump vendored upstream pins
```

## Quick start

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

# run a single deterministic pack with detailed per-scenario output
benchlocal-cli run --pack toolcall-15 --endpoint http://localhost:8020 --model qwen3.6-27b-autoround

# run aider-polyglot-30 (multi-language code editing — not bundled in --quick/--medium/--full)
benchlocal-cli run --pack aider-polyglot-30 --enable-sandboxed-packs \
  --endpoint http://localhost:8010 --model qwen3.6-27b-autoround \
  --timeout-per-case 2700

# emit machine-readable JSON instead of markdown
benchlocal-cli run --quick --endpoint http://localhost:8020 --model qwen3.6-27b-autoround --output json > results.json
```

## Reasoning models

`benchlocal-cli` sends `chat_template_kwargs: {"enable_thinking": false}` by default. This keeps BenchLocal-style quality prompts comparable on reasoning-capable models such as Qwen3.6, where default thinking can exhaust `max_tokens` before the final answer is emitted. Use `--enable-thinking` for diagnostic runs; it sets `enable_thinking=true` and bumps request `max_tokens` to `--thinking-max-tokens` (default `4096`) so diagnostic thinking runs have enough budget. Use `--extra-body` to pass any other OpenAI-compatible server extension fields.

## Output

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

For agentic packs (e.g. `aider-polyglot-30`), the headline number is `pass_rate` over 30 exercises rather than per-scenario pass/fail; per-exercise breakdown is surfaced in the JSON `verifier_trace.upstream_per_exercise`. See [docs/AIDER_POLYGLOT_30.md](docs/AIDER_POLYGLOT_30.md) for the full output shape.

## Attribution

This repo ports MIT-licensed bench pack scenarios from [stevibe/BenchLocal](https://github.com/stevibe/BenchLocal) and the individual pack repos:

- [stevibe/ToolCall-15](https://github.com/stevibe/ToolCall-15) (v1.0.1)
- [stevibe/InstructFollow-15](https://github.com/stevibe/InstructFollow-15) (v1.0.0)
- [stevibe/StructOutput-15](https://github.com/stevibe/StructOutput-15) (v1.0.0)
- [stevibe/ReasonMath-15](https://github.com/stevibe/ReasonMath-15) (v1.0.0)
- [stevibe/DataExtract-15](https://github.com/stevibe/DataExtract-15) (v1.0.0)
- [stevibe/BugFind-15](https://github.com/stevibe/BugFind-15) (v1.0.1)
- [stevibe/HermesAgent-20](https://github.com/stevibe/HermesAgent-20) (v1.0.0)
- [stevibe/CLI-40](https://github.com/stevibe/CLI-40) (v1.0.2)

Eval-expansion track:

- [Aider-AI/aider](https://github.com/Aider-AI/aider) (Apache-2.0) — `benchmark.py` harness for AiderPolyglot-30
- [Aider-AI/polyglot-benchmark](https://github.com/Aider-AI/polyglot-benchmark) (CC-BY-SA-3.0 / various Exercism licenses) — exercise tree

See [`ATTRIBUTION.md`](./ATTRIBUTION.md) for full attribution + license preservation per pack.

## License

MIT — same as upstream BenchLocal. See [`LICENSE`](./LICENSE).

## Contributing

Beta. Pack updates should go through `tools/sync-vendor.sh` and `tools/build-packs.js`; see `CONTRIBUTING.md`.
