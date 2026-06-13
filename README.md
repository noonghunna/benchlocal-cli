# benchlocal-cli

CLI for running LLM behavioral evaluation packs against any OpenAI-compatible endpoint, with deterministic verifier-backed scoring (no LLM-as-judge). Three pack families today:

- **BenchLocal** ports — tool-call · instruction-follow · structured output · numeric reasoning · data extraction · debug · multi-tool agent · CLI exec (8 packs from [stevibe/BenchLocal](https://github.com/stevibe/BenchLocal), MIT-licensed).
- **Eval-expansion track** — additional packs vendored from upstream open-source benches. v0.9 ships `aider-polyglot-30` (multi-language code editing via [Aider-AI/aider](https://github.com/Aider-AI/aider)'s `benchmark.py`).
- **Reasoning suite** — opt-in `--reasoning-packs` packs for code reasoning, symbolic math, and gated science QA: HumanEval+, LiveCodeBench v6, GSM-Symbolic, and GPQA-Diamond metadata.

Companion to [club-3090](https://github.com/noonghunna/club-3090) — primarily intended for measuring quality on quantized models served by club-3090's compose stack, but works against any OpenAI-compatible API.

## Why this exists

We needed a headless, scriptable quality gate for compose-release validation on an inference rig. BenchLocal is a great Electron desktop app for human-in-the-loop quality A/B; this repo turns the same pack semantics into a CLI that:

- Hits any OpenAI-compatible HTTP endpoint
- Runs BenchLocal's 8 deterministic-verifier packs + agentic eval packs (currently 1: `aider-polyglot-30`) + the opt-in reasoning suite
- Supports `--quick` / `--medium` / `--full` budget modes for the BenchLocal packs (~30-45 min for `--full`) plus a separate `--reasoning-packs` mode; agentic packs can also run independently via `--pack <name>`
- Outputs paste-ready markdown for benchmark tables + JSON for machine consumption
- Keeps quantized-model quality measurement light enough to run as a CI gate

## Status

🟢 **Beta — full BenchLocal prompt fidelity, reasoning-model aware, sandbox-capable, plus eval-expansion track.** JSONL packs are generated from vendored upstream TypeScript mirrors; deterministic packs use upstream system prompts and scenario prompts verbatim. Requests use pack-level `default_thinking` metadata so reasoning-rewarding packs can think while execution/format packs stay answer-only. BugFind-15, HermesAgent-20, CLI-40, **AiderPolyglot-30**, **HumanEval+-30**, and **LiveCodeBench-v6-30** run through Docker-hosted HTTP verifier sandboxes when `--enable-sandboxed-packs` is set.

**v0.9.0** added the eval-expansion track — `aider-polyglot-30` ships as the first non-BenchLocal sandboxed pack: 30-exercise multi-language code-editing bench across cpp/go/java/javascript/python/rust, vendored upstream from `Aider-AI/aider`'s `benchmark.py`. Run with `--pack aider-polyglot-30 --enable-sandboxed-packs`. See [docs/AIDER_POLYGLOT_30.md](docs/AIDER_POLYGLOT_30.md).

## Modes

| Mode | Packs | Budget | Use case |
|---|---|---|---|
| `--quick` | ToolCall-15 + InstructFollow-15 | ~10-15 min | Per-commit gate; pre-push smoke |
| `--medium` (default) | + StructOutput-15 + DataExtract-15 | ~25-30 min | Pre-release; pin bumps; new compose authoring |
| `--full` | + ReasonMath-15 + (BugFind / HermesAgent / CLI when sandboxed) | ~45-60 min | Cross-rig comparison; quality A/B vs another quant |
| `--reasoning-packs` | HumanEval+-30 + LiveCodeBench-v6-30 + GPQA-Diamond (gated) + GSM-Symbolic-30 | ~30-90+ min; code packs need Docker | Dedicated reasoning/code suite; structured-CoT / no-think / thinking A/B |
| `--pack aider-polyglot-30` | aider-polyglot-30 (independent — not bundled in `--quick`/`--medium`/`--full`/`--reasoning-packs`) | ~15-25 min | Agentic code-editing signal; cross-model quality A/B for IDE-agent / coding workloads |

Pack selection in each mode follows Codex design-review feedback (2026-05-09) — ToolCall + InstructFollow are the primary signals for IDE-agent regressions; StructOutput catches grammar/JSON drift; ReasonMath defers to `--full` because it leans toward generic benchmark behavior rather than agent-stack-specific. `--reasoning-packs` stays separate from `--full` because it changes the question from general behavior to code/math/science reasoning under larger thinking budgets. AiderPolyglot-30 is run independently because its harness is a batch runner with multi-turn edit/test loops — different shape from the per-scenario BenchLocal packs.

> **Two orthogonal axes.** A mode flag picks **which packs** run (`--quick` / `--medium` / `--full` / `--reasoning-packs`); `--enable-thinking` / `--no-thinking` pick **the thinking mode** (orthogonal to the pack-set). For a clean *with-vs-without-reasoning* A/B on the standard suite, vary the mode on a fixed pack-set: `--full --no-thinking` vs `--full --enable-thinking`. `--reasoning-packs` was previously named `--reasoning` (it read like a mode but is a pack-set); the old flag still works as a hidden, deprecated alias that prints a warning.

## Sampling

By default, packs sample at their declared per-pack temperature — the deterministic packs use **temperature 0** (greedy) for reproducible, cross-rig-comparable scoring. This is the **canonical** baseline.

Two opt-in flags evaluate a model at a non-default temperature. Both tag the run **⚠ NON-CANONICAL** (markdown header + JSON) and block `--exit-on-regression` (non-canonical runs shouldn't gate CI):

| Flag | Effect |
|---|---|
| `--temperature N` (+ `--top-p` / `--top-k` / `--min-p` / `--repeat-penalty`) | Override sampling with values you specify. |
| `--sampling-from-server` | Omit **all** sampling params from requests so the *server* applies its own configured defaults (e.g. a compose's `--temp` / `--override-generation-config`). Reads the actual values back via `GET /props` (llama.cpp) and records them as `sampling_source: "server"` + `server_defaults`. Mutually exclusive with `--temperature` et al. |

Use the canonical temp-0 default for regression tracking and cross-model ranking (fixed bar, reproducible). Use the override flags to evaluate a model **as it's served / at its recommended temperature** — e.g. reasoning or exploratory fine-tunes that recommend temp 0.75–1, where greedy decoding under-represents what the model was tuned for.

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
| **HumanEval+-30** | **Execution-backed code reasoning** — HumanEval+ functional tests via the `code-reasoning` sandbox | ✅ sandboxed reasoning subset |
| **LiveCodeBench-v6-30** | **Execution-backed code reasoning** — public LCB functional tests via the `code-reasoning` sandbox | ✅ sandboxed reasoning subset |
| **GSM-Symbolic-30** | Deterministic — `answer_match` exact numeric final-answer scoring | ✅ reasoning subset |
| **GPQA-Diamond** | Deterministic — `answer_match` exact letter final-answer scoring | ⚠ gated metadata-only; no restricted data committed |

## Sandboxed packs — networking

The bolded "sandboxed" packs above (`BugFind-15`, `HermesAgent-20`, `CLI-40`, `AiderPolyglot-30`, `HumanEval+-30`, `LiveCodeBench-v6-30`) run their verifier inside a Docker container that calls back out to **your** model endpoint. The networking gotcha: `localhost` inside the container is the *container's own* loopback, not the host. The CLI handles this automatically in most cases:

- **Loopback endpoints (`localhost`, `127.x`, `[::1]`, `[::]`)** — auto-resolved to `host.docker.internal` and `--add-host=host.docker.internal:host-gateway` is injected into the sandbox container. **No env var needed.** Works out of the box for `--endpoint http://localhost:PORT`.
- **Non-loopback endpoints (LAN IPs, k8s service names, docker-compose service DNS)** — passed through verbatim. Assumes you've set up networking so the sandbox container can resolve and reach the host. The container's own DNS is used.
- **Force the host-gateway rewrite for non-loopback** — if you have a custom hostname that actually needs the rewrite (e.g., the name resolves on the host but not inside containers), set `BENCHLOCAL_HERMES_RESOLVE_LOCALHOST=1`. This forces the same rewrite + `--add-host` for hermes-style packs regardless of host type. Aider always uses the rewrite; this flag controls the hermes/cli-class behavior for non-loopback hosts.

If a sandboxed pack scores 0/N with uniform short latencies, networking is the first thing to check. See [`docs/SANDBOX_PROTOCOL.md`](docs/SANDBOX_PROTOCOL.md) for per-pack protocol details and [`docs/PACK_FORMAT.md`](docs/PACK_FORMAT.md) for metadata schema.

## Per-case timeouts

Each scenario's timeout is sized by precedence (highest wins):

1. **`--timeout-per-case N`** (env `TIMEOUT_PER_CASE`) — explicit override, used verbatim.
2. **Auto-scaling (default)** — `timeout = base × max(1, reference_tps / measured_tps) × thinking_multiplier`:
   - `base` = the pack's `default_max_seconds` metadata.
   - `reference_tps` = the pack's `timeout_reference_tps` (the decode rate `base` assumes; override with `--reference-tps`).
   - `measured_tps` = a one-shot startup decode-TPS probe of the endpoint (sent with `enable_thinking=false`; skip it by passing `--measured-tps N`). The probe runs a reachability preflight (`GET /v1/models`, 5s, no retry) and **fails fast** — it never hangs a run against a dead or blackholed endpoint.
   - `thinking_multiplier` = `thinking_max_tokens / nominal_max_tokens`, applied only when thinking is enabled and the budget exceeds the nominal output. Prevents thinking-on runs from spuriously timing out (#54).
   - `max(1, …)` means a faster rig never shrinks the budget below `base`. The result deliberately **over-budgets** — a timeout is a ceiling, not a target.
3. **Static default** — the pack's `default_max_seconds`, when no `reference_tps` is set or the probe is unavailable.

A **timeout is not retried** as transient (a timeout means the budget was genuinely hit); connection errors and HTTP 5xx still retry. `--retry-on-timeout` (default off) restores the old retry behavior.

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
    ├── aider-polyglot-30.jsonl
    ├── humaneval-plus-30.jsonl
    ├── lcb-v6-30.jsonl
    ├── gsm-symbolic-30.jsonl
    └── gpqa-diamond.jsonl

sandboxes/                      # Docker images for execution-backed verifier packs
├── bugfind/                    # Python pytest harness for BugFind-15 candidate fixes
├── cli/                        # Linux exec sandbox for CLI-40 commands
├── hermes/                     # Hermes-agent runtime + Node grader for HermesAgent-20
├── aider-polyglot/             # Aider + polyglot-benchmark for AiderPolyglot-30
└── code-reasoning/             # Python execution sandbox for HumanEval+ and LCB

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

# run quick mode against a local club-3090 endpoint; pack metadata picks thinking on/off
benchlocal-cli run --quick --endpoint http://localhost:8020 --model qwen3.6-27b-autoround

# force reasoning/thinking enabled for every pack with a larger token budget
benchlocal-cli run --quick --endpoint http://localhost:8020 --model qwen3.6-27b-autoround --enable-thinking

# force answer-only mode for every pack, ignoring pack defaults
benchlocal-cli run --quick --endpoint http://localhost:8020 --model qwen3.6-27b-autoround --no-thinking

# pass vendor-specific request body fields
benchlocal-cli run --quick --endpoint http://localhost:8020 --model qwen3.6-27b-autoround --extra-body '{"foo":"bar"}'

# run full mode with custom timeout per scenario
benchlocal-cli run --full --endpoint http://localhost:8010 --model qwen3.6-27b-autoround --timeout-per-case 60

# run full mode including Docker-backed verifier packs
benchlocal-cli run --full --enable-sandboxed-packs --endpoint http://localhost:8010 --model qwen3.6-27b-autoround

# run the dedicated reasoning suite; HumanEval+ and LCB need the Docker code sandbox
benchlocal-cli run --reasoning --enable-sandboxed-packs \
  --endpoint http://localhost:8020 --model qwen3.6-27b-autoround \
  --thinking-max-tokens 16384

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

`benchlocal-cli` uses each pack's `default_thinking` metadata by default. Reasoning-rewarding packs such as `reasonmath-15`, `bugfind-15`, `instructfollow-15`, `hermesagent-20`, and every `--reasoning-packs` pack run with `chat_template_kwargs.enable_thinking=true`; execution/format packs such as `toolcall-15`, `structoutput-15`, `dataextract-15`, and `cli-40` run answer-only. Use `--enable-thinking` to force thinking on for every pack, or `--no-thinking` to force it off for every pack. Whenever thinking is enabled for a pack, request `max_tokens` is raised to `--thinking-max-tokens` (default `16384`) and the request uses the recommended thinking sampler (`temperature=1.0`, `top_p=0.95`, `top_k=20`, `min_p=0.0`) instead of the deterministic pack's greedy sampler. Override it with `--thinking-sampler '{"temperature":0.7,"top_p":0.9}'`, override individual sampling keys with `--temperature`/`--top-p`/`--top-k`/`--min-p`, or use `--sampling-from-server` to omit sampler params entirely. HumanEval+ and LiveCodeBench also carry 16K scenario budgets so thinking-on code runs do not measure a 4K truncation failure; hardest LCB items may still exceed 16K, so compare against `--no-thinking` for budget-runaway diagnostics. Use `--extra-body` to pass any other OpenAI-compatible server extension fields. Saved JSON records `thinking_enabled` per pack plus the run-level `thinking_mode`.

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
- toolcall-15 TC-07: verifier_fail (wrong arg value for "filename": expected report.pdf, got output.pdf)
- instructfollow-15 IF-03: verifier_fail (word count 247, target 250 ±5)
- dataextract-15 DE-05: verifier_fail (7/14 atomic fields correct (50%). product_name: mismatch)
```

For agentic packs (e.g. `aider-polyglot-30`), the headline number is `pass_rate` over 30 exercises rather than per-scenario pass/fail; per-exercise breakdown is surfaced in the JSON `verifier_trace.upstream_per_exercise`. See [docs/AIDER_POLYGLOT_30.md](docs/AIDER_POLYGLOT_30.md) for the full output shape.

When `--repeat N` is greater than 1, the markdown table adds per-pack `Std` and `CV` columns derived from repeat-arm pass rates. The saved JSON includes the same data under each pack result as `variance: {"repeat", "mean", "std", "cv"}` so cross-rig runs can distinguish real deltas from run-to-run noise.

## Inspecting failures

The `Failure breakdown:` block above is the quickest read — `failure_mode` + full detail per failed scenario, printed at the end of every run. For deeper forensics, any run with `--save-json` (which `quality-test.sh` sets) records per-scenario tokens, latency, and the full verifier trace; the `inspect` subcommand reads it back:

```
benchlocal-cli inspect results.json --failed                 # every failure + reason + tokens + latency
benchlocal-cli inspect results.json --scenario IF-10 --full  # full prompt/response/verifier trace + conversation
benchlocal-cli inspect results.json --mode timeout           # only this failure_mode
benchlocal-cli inspect results.json --diff previous.json     # side-by-side vs a prior run (regressions + latency delta)
benchlocal-cli inspect results.json --logs ./sandbox-logs    # pull sandboxed-pack stdout/stderr
```

`failure_mode` is one of `verifier_fail`, `token_limit`, `timeout`, `agent_runner_timeout`, `agent_runner_crashed`, `server_error`, `http_error`, `model_endpoint_unreachable`, `result_json_malformed`, `wrong_answer`, `verifier_not_implemented`.

`token_limit` (#61) means the completion hit the token cap (`finish_reason == "length"`) and was truncated mid-output — the model overthought or looped until the budget ran out, *not* a content verdict. It's reclassified from the underlying content-failure (the original verdict is kept in `detail`), so "looped / truncated" reads distinctly from "ran to completion but wrong" (`verifier_fail`). Filter it with `inspect --mode token_limit`.

## Negative control (grader false-positive probe)

We catch verifiers that are too *strict* by hand (the false-negative audit). `--negative-control` (#62) catches the opposite — verifiers too *lenient* to be measuring anything:

```bash
# feed deterministic junk to every scenario instead of calling a model;
# any PASS = a verifier that accepted junk it should reject. No endpoint/GPU needed.
benchlocal-cli run --negative-control --medium
benchlocal-cli run --negative-control --negative-control-text "" --pack instructfollow-15   # pure-empty control
```

`--endpoint`/`--model` are not required in this mode. The junk defaults to `(no answer)`; override with `--negative-control-text` (an empty string is the purest control, while a constant non-answer additionally surfaces *format-only* verifiers that pass anything shaped right). Any PASS is printed as a candidate false-positive to review — it bounds, from the lenient side, how much a pack's score can be trusted.

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
