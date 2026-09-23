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
| **InstructFollow-15** | Deterministic — constraint validators; 7 scenarios run ports of upstream's evaluators, IF-11 with a documented prompt-faithful deviation (v2.0.0, #143) | ✅ vendor-generated |
| **StructOutput-15** | Deterministic — JSON / CSV / markdown / YAML-lite validate; TOML / SQL / ICS / XML / HTML / BSON run ports of upstream's validators, and the Mermaid flowchart is judged against its prompt (v2.0.0, #143) | ✅ vendor-generated |
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
   - `measured_tps` = a one-shot startup decode-TPS probe of the endpoint (reasoning is clamped with the resolved model-specific control; skip it by passing `--measured-tps N`). The probe runs a reachability preflight (`GET /v1/models`, 5s, no retry) and **fails fast** — it never hangs a run against a dead or blackholed endpoint.
   - `thinking_multiplier` = `thinking_max_tokens / nominal_max_tokens`, applied only when thinking is enabled and the budget exceeds the nominal output. Prevents thinking-on runs from spuriously timing out (#54).
   - `max(1, …)` means a faster rig never shrinks the budget below `base`. The result deliberately **over-budgets** — a timeout is a ceiling, not a target.
3. **Static default** — the pack's `default_max_seconds`, when no `reference_tps` is set or the probe is unavailable.

The automatic or static result is then capped by `--timeout-ceiling-s N` (env `BENCHLOCAL_TIMEOUT_CEILING_S`) when set. A pack may provide the same guard as `timeout_ceiling_s` metadata; the CLI/environment value wins, and `--timeout-ceiling-s 0` explicitly disables a pack ceiling. The exact `--timeout-per-case` override is never capped.

Runner-owned model calls in sandboxed packs have a second, independent watchdog: `--model-turn-timeout N` (default `300` seconds; env `BENCHLOCAL_MODEL_TURN_TIMEOUT`). It caps one endpoint call even when speed/thinking scaling gives the scenario a much larger overall budget. Pass `0` to disable the cap. Sandbox-owned agent processes retain their own subprocess watchdogs.

**Agent-owned packs are bounded by an in-container clock, not by either of the runner's.** `hermesagent-20` and `aider-polyglot-30` run their agent *inside* the sandbox and it makes its own model calls, so `--timeout-per-case` (the runner's HTTP read) and `--model-turn-timeout` (one runner-owned call) cannot govern an episode by construction. What does: `HERMES_SUBPROCESS_TIMEOUT_S` (default `300` s per scenario) for hermes and `AIDER_BENCHMARK_TIMEOUT_S` (default `3600` s for the whole batch) for aider. An explicit `--timeout-per-case N` **floors** both caps — it can raise them, never lower them — and `BENCHLOCAL_HERMES_SUBPROCESS_TIMEOUT_S` still wins verbatim when set. It is a floor and not `turn × N` on purpose: an episode is an open-ended number of turns, and the 300 s guard exists so a stuck scenario cannot burn the whole bench; it just must not sit below what you asked for (#149). The runner prints the effective clocks for every sandboxed pack at start, e.g.

```
[runner] hermesagent-20 clocks: episode 900s (floor from --timeout-per-case 900; default 300s), verify-start read 1200s, model-turn n/a (agent makes its own model calls in-container)
```

and records a warning in the result when a runner-side knob was set on a pack it cannot reach. If hermes scenarios die at exactly `300.1s` with `agent_runner_timeout`, this is the clock to look at.

Inside the hermes sandbox the cap is enforced by the verifier's own agent watchdog (`hermes-runtime.mjs`), and the sandbox proxy waits the cap plus 180 s so that watchdog's result — not a transport cut-off — is what gets recorded. Until that watchdog read `HERMES_SUBPROCESS_TIMEOUT_S` it was a fixed 600 s, so **any cap above 600 s was silently clamped to 600 s** while the clocks line above still reported the larger value. A hermes image built before the fix still behaves that way: rebuild it with `tools/build-sandboxes.sh`.

### Streaming and stall detection (`--stream`, #157)

Without streaming, every model call is one blocking read: while it is open, "generating normally" and "hung" look the same until the whole budget expires, so every timeout knob trades a guillotine against a blind window. `--stream` (env `BENCHLOCAL_STREAM=1`; **off by default** — without it the request and every saved byte are unchanged) sends runner-owned calls with `stream: true` and `stream_options.include_usage`, and reassembles the events into exactly the non-streaming response shape, so scoring, truncation relabelling, the #152 engine-rejection classifier and token accounting read it unchanged. It then runs two clocks:

- **First-token allowance** = the request budget. Queueing and prefill legitimately send nothing, so the gap clock does not start until the first event.
- **Stall gap** = `--stream-stall-timeout N` (default `120`; env `BENCHLOCAL_STREAM_STALL_TIMEOUT`). No `data:` event for N seconds after the first one fails the request as **`stall`**. Keep-alive comment lines (llama.cpp sends `:` pings) do **not** reset it — a server that keeps pinging while producing nothing is exactly what it catches.

The request budget stays as a backstop with the same meaning as without streaming. `stall` is an **infra** failure, not a runaway: the endpoint stopped producing, the model did not run long. Like a timeout (#58) it is not retried by the transport loop; the inline infra-retry policy still applies. An error event mid-stream (`data: {"error": …}`) is surfaced with the status its `code` names (llama.cpp: 500; vLLM: 400 by default), so it classifies like a non-streaming error body. With streaming on, each result's `verifier_trace.stream` records per-request `first_chunk_s` and `max_gap_s`, and `packs[].diagnostics.stream` rolls them up (`max_gap_s`, `p95_gap_s`, `first_chunk_p50_s`, `first_chunk_max_s`) — the cheapest endpoint-health signal there is. club-3090's `quality-test.sh` has no named flag for it; pass it through: `bash scripts/quality-test.sh --full -- --stream`.

Streaming reaches only the calls the **runner** makes. `hermesagent-20` and `aider-polyglot-30` make their own model calls inside the sandbox. hermes-agent streams and has its own stall detection, which it switches **off** for local endpoints (how this harness points it). Two **opt-in** knobs turn it back on: `BENCHLOCAL_HERMES_STREAM_STALE_TIMEOUT_S` and `BENCHLOCAL_HERMES_STREAM_READ_TIMEOUT_S` are passed verbatim into the container as `HERMES_STREAM_STALE_TIMEOUT` / `HERMES_STREAM_READ_TIMEOUT` — **only when set**. With neither set nothing is injected and hermes behaves exactly as before. Values must be positive seconds. Two caveats before reaching for them:

- **A value equal to hermes' own default is inert** — hermes reads exactly `180` (stale) or `120` (read) as "not set" and, for a local endpoint, keeps the detector off. Use any other value.
- **They rarely fire under the default episode cap.** Measured against an endpoint that never answers: the **read** timeout, not the stale timer, bounds each attempt, and hermes makes about **6 attempts** before giving up — so a dead endpoint ends an episode after roughly 6 × the read timeout. Under the default 300 s cap (`HERMES_SUBPROCESS_TIMEOUT_S`) the cap almost always fires first; these knobs matter only with a long cap.

Independently of those knobs: when the agent gives up on the endpoint mid-episode it exits 0 and reports `API call failed after N retries: …` as its final answer, which used to be graded as a model `verifier_fail`. It is now classified **`model_endpoint_unreachable`**.

A **request timeout is not retried** by the transport retry loop (a timeout means the request budget was genuinely hit); connection errors and HTTP 5xx still are. `--retry-on-timeout` (default off) restores the old transport behavior. Scenario-level timeout/runaway retries are separately controlled by `--retry-runaways`.

### Stopping on a dead endpoint (`--stop-after-infra-failures`, #160)

Infrastructure failures (`http_error`, `server_error`, `stall`, `model_endpoint_unreachable`, `agent_runner_crashed`, `result_json_malformed`) are retried inline even under `--no-retry`, because a harness hiccup should not count against the model. Against an endpoint that is simply gone, that multiplies the cost of finding out: a hung endpoint costs every remaining scenario its full retries, and a `hermesagent-20` pack against an endpoint that never answers could spend hours doing it.

So after **`--stop-after-infra-failures N`** consecutive scenarios (default `3`, `0` disables; env `BENCHLOCAL_STOP_AFTER_INFRA_FAILURES`) end in an infrastructure failure, the runner sends **one liveness probe**: a 1-token chat completion, unretried, bounded by `--endpoint-probe-timeout` (default `30` s; env `BENCHLOCAL_ENDPOINT_PROBE_TIMEOUT`). It is a completion rather than `GET /v1/models` because a hung engine keeps answering `/v1/models` while generation never returns.

- **The probe answers** (HTTP 200 with `choices`): the failures were transient. The counter resets and the pack continues, exactly as before.
- **It does not**: the pack **stops**. The infra-failed streak is set aside **unscored** (it was the endpoint, not the model), the remaining scenarios are **not run**, the pack's status is `endpoint-down`, a warning says it is not a complete score, and the run exits **`5`**. Each later pack probes once before starting and is skipped the same way while the endpoint stays down, or runs normally if it came back.
- **`--resume <saved json>`** runs both the set-aside and the not-run scenarios; a resume that completes them clears the stop. The saved JSON records it per pack as `packs[].stop` (`after_scenario`, `probe`, `unscored`, `not_run`), and history ingestion refuses a stopped run without `--allow-partial`.

A non-infrastructure result (a pass, or any model verdict) breaks the streak. `timeout` does not count: without `--stream` it cannot tell a runaway on a live server from a dead one, and a probe after a runaway could queue behind it. With `--stream`, a hung endpoint shows up as `stall`, which does count. Synthetic traffic (mocks, the negative control) never probes.

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
├── FAILURE_TRIAGE.md           # classify failures: model miss (default) vs prompt gap vs harness/verifier bug
├── HERMES_V073_AB.md           # forensic notes from the Hermes A/B run
├── INTEGRATION.md              # how club-3090 (or other repos) consume this CLI
├── PACK_FORMAT.md              # JSONL schema each pack file follows
├── PROMPT_VERIFIER_AUDIT.md    # 2026-08-12 prompt↔verifier alignment audit across all packs
├── REASONING_PACKS.md          # humaneval-plus-30 / lcb-v6-30 provenance, drift, extension convention
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

# effort-based model/provider: off sends none; on sends high
benchlocal-cli run --quick --endpoint http://localhost:8020 --model inkling \
  --enable-thinking --reasoning-effort high

# pass vendor-specific request body fields
benchlocal-cli run --quick --endpoint http://localhost:8020 --model qwen3.6-27b-autoround --extra-body '{"foo":"bar"}'

# run full mode with custom timeout per scenario
benchlocal-cli run --full --endpoint http://localhost:8010 --model qwen3.6-27b-autoround --timeout-per-case 60

# retain automatic speed/thinking scaling, but never allow more than 20 minutes per scenario
benchlocal-cli run --full --endpoint http://localhost:8010 --model qwen3.6-27b-autoround --timeout-ceiling-s 1200

# disable model-verdict inline retries (infrastructure recovery still applies)
benchlocal-cli run --quick --endpoint http://localhost:8020 --model qwen3.6-27b-autoround --no-retry

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

## Scenario selection

Run exact scenarios with repeatable pack-qualified IDs, or keep a reusable newline-delimited selection file:

```bash
benchlocal-cli run \
  --scenario cli-40/CLI-34 \
  --scenario reasonmath-15/RM-04 \
  --endpoint http://localhost:8010 --model qwen3.6-27b-autoround

benchlocal-cli run --scenarios-file targeted.txt \
  --endpoint http://localhost:8010 --model qwen3.6-27b-autoround
```

Selection alone defines the run set. With `--pack`, `--quick`, `--medium`, `--full`, `--reasoning-packs`, or `--sandboxed-only`, it intersects with that pack set. Selection files accept one `PACK_ID/SCENARIO_ID` per line, blank lines, and `#` comments. Unknown IDs fail before any model call and include near matches. Thinking and sampling follow the same pack defaults and overrides as ordinary runs.

Selected results are intentionally explicit: JSON includes top-level `selection`, each pack's `scenario_count` is the selected subset, and `catalog_scenario_count` records the complete pack size. Human output labels subset scores `partial`. These are optional additive fields, so `schema_version` remains `1` and older result JSON stays readable. Partial results are refused by history ingestion and `rescore` unless `--allow-partial` is supplied. `--exit-on-regression` is allowed: with `--previous-result` it gates only selected scenario keys, while non-canonical sampling overrides remain blocked as before.

## Incremental persistence and resume

`--incremental` writes one scored scenario per line to a crash-safe sidecar named `<save-json>.partial.jsonl`. On normal completion the journal is folded into the ordinary result JSON and deleted; the final artifact shape and `schema_version` are unchanged. If the process is interrupted, inspect or resume the surviving journal directly:

```bash
benchlocal-cli run --full --incremental --save-json r.json \
  --endpoint http://localhost:8010 --model qwen3.6-27b-autoround
benchlocal-cli inspect r.json.partial.jsonl --failed
benchlocal-cli run --resume r.json.partial.jsonl
```

`--resume` also accepts an incomplete or completed result JSON. It reconstructs the original target set, repeat count, thinking mode, sampling, and timeout configuration; internally it runs #83's selection complement for only missing `(pack, scenario, repeat_index)` arms, then merges them in canonical order. Endpoint and model are restored from the journal, while credentials still come from the current CLI/environment. A completed result is a successful no-op with a clear message.

## Running against a cloud / managed endpoint

The same packs run against any cloud OpenAI-compatible endpoint — a managed API, a router, your own hosted model — for a like-for-like local-vs-cloud comparison (identical prompts, identical verifiers).

```bash
# the cloud knobs: --api-key (Bearer auth) + the model id your endpoint serves
benchlocal-cli run --pack toolcall-15 \
  --endpoint https://your-host/v1 \
  --model your-model-id \
  --api-key "$YOUR_KEY" \
  --save-json cloud-toolcall.json
```

`--api-key` is sent as `Authorization: Bearer <key>` on every request (defaults to `$BENCHLOCAL_API_KEY`).

**Rate limits.** Two controls, designed to compose — *pace to avoid the throttle, retry to recover when it still hits*:

| Flag | Role |
|---|---|
| `--request-delay <sec>` | **proactive** — minimum seconds between requests, to stay under the endpoint's RPM ceiling (env `BENCHLOCAL_REQUEST_DELAY`) |
| `--max-transient-retries <N>` | **reactive** — auto-retry 429 / transient failures before failing a scenario (default 3). Server `Retry-After` is honored up to 120s; without it, 429 retries wait 10s, 20s, then 40s so the default spans a minute window. |

Leave `--retry-on-timeout` **off** for cloud — a timeout means the token budget was genuinely exhausted, so retrying just burns another budget.

**Spend guard.** `--max-total-tokens <N>` is a hard cost ceiling for the run. The agentic packs (`cli-40`, `hermesagent-20`, `aider-polyglot-30`) use the most tokens — set it generously so it stops a runaway without truncating a legitimate run.

**Where the tokens went (#147).** Every run now aggregates the per-scenario counts it already persisted: `packs[].tokens` and a top-level `tokens` rollup — `{"completion", "retries", "counted", "missing", "total"}` plus `prompt` / `total_tokens` / `reasoning` where the endpoint reports them — and the markdown closes with one line for the run and one per pack, e.g. `Tokens: 364,047 completion across 129 / 150 scored rows (21 without a count: hermesagent-20 20, cli-40 1); prompt 1,234,567; endpoint-reported total 1,700,000 (every request, including probes and retries)`. `completion` is summed over the same attempt-1 rows as `passed / total`; `retries` is the nested inline-retry cost; `endpoint_reported_total` is the spend guard's own counter (`usage.total_tokens` over every request the runner made), which used to surface only when the guard tripped — so **cost-per-run = your price × endpoint_reported_total**. Per scenario the saved JSON now keeps `tokens_prompt`, `tokens_total` and `tokens_reasoning` beside `tokens_completion`. Two gaps are reported rather than hidden: `hermesagent-20` shows `missing` for every row because its agent calls the model from inside the container, and a scenario that timed out carries no count because the response never arrived — both read as `without a count`, never as zero. The pack table and `TOTAL` row are unchanged; the block is omitted for results that carry no rollup.

**Pin a provider / quant** (for routers like OpenRouter) via `--extra-body`:

```bash
--extra-body '{"provider":{"only":["DeepInfra"],"allow_fallbacks":false}}'
```

⚠️ **`instructfollow-15` and `structoutput-15` v2.0.0 are not comparable with v1.x.** In v1.x, 7 scenarios in each accepted any non-empty answer (a junk answer scored 7/15); v2 grades them with upstream's own checks, so scores can only go down. `benchlocal-cli rescore <result.json>` re-grades a saved v1 result with the current verifiers — see [docs/VERIFIER_FIDELITY_AUDIT.md](docs/VERIFIER_FIDELITY_AUDIT.md).

**What to compare.** The **deterministic** packs (`toolcall-15`, `instructfollow-15`, `structoutput-15`, `dataextract-15`, `reasonmath-15`) are the cleanest apples-to-apples — single-shot, verifier-graded, no Docker. The **sandboxed/agentic** packs run a *local* Docker agent loop that calls your endpoint over the network, so they also need the sandbox images built (`bash tools/build-sandboxes.sh` from a checkout) and are less validated over a remote endpoint — land the deterministic set first.

**Reasoning state — match it explicitly.** Each pack carries `default_thinking` metadata, while the request key is model/provider-specific (see [Reasoning models](#reasoning-models)). For a managed endpoint that does not expose its live template, pass `--reasoning-effort VALUE` when that is the provider's control, or opt into the tiny behavioral detector with `--probe-thinking-control`. For a fair local-vs-cloud comparison, run both `--no-thinking` and `--enable-thinking` arms and inspect the saved request payloads. An unexpected p95 spike on thinking-on packs usually means the endpoint reasoned longer than intended.

## Reasoning models

`benchlocal-cli` uses each pack's `default_thinking` metadata by default. Reasoning-rewarding packs such as `reasonmath-15`, `bugfind-15`, `instructfollow-15`, `hermesagent-20`, and every `--reasoning-packs` pack run thinking-on; execution/format packs such as `toolcall-15`, `structoutput-15`, `dataextract-15`, and `cli-40` run answer-only. Use `--enable-thinking` to force thinking on for every pack, or `--no-thinking` to force it off for every pack.

The runner resolves the request switch once per model:

| Endpoint/template | Control |
|---|---|
| `GET /props` template mentions `enable_thinking` | `chat_template_kwargs.enable_thinking` (compatibility default) |
| Template mentions only `reasoning_effort` | top-level `reasoning_effort` plus a `chat_template_kwargs` copy for llama.cpp |
| Template mentions both | `enable_thinking` wins, preserving existing request behavior |
| No `/props` (vLLM/SGLang/managed endpoint) | compatibility default, or opt-in `--probe-thinking-control` |
| Explicit `--reasoning-effort VALUE` | effort control without probing |

The behavioral probe sends up to two real 24-token inference requests, so it is deliberately opt-in; it first checks endpoint reachability and never classifies an unreachable server as “no switch.” Accepted effort values are `none|minimal|low|medium|high|xhigh|max` or a float from `0.0` to `0.99`. Effort is guidance, not a token budget: the off arm sends `none`, the on arm sends the requested value, and `--thinking-max-tokens` remains the separate output ceiling. Provider effort labels are not necessarily comparable.

Whenever thinking is enabled for a pack, request `max_tokens` is raised to `--thinking-max-tokens` (default `16384`) and the request uses the recommended thinking sampler (`temperature=1.0`, `top_p=0.95`, `top_k=20`, `min_p=0.0`) instead of the deterministic pack's greedy sampler. Override it with `--thinking-sampler '{"temperature":0.7,"top_p":0.9}'`, override individual sampling keys with `--temperature`/`--top-p`/`--top-k`/`--min-p`, or use `--sampling-from-server` to omit sampler params entirely. HumanEval+ and LiveCodeBench also carry 16K scenario budgets so thinking-on code runs do not measure a 4K truncation failure; hardest LCB items may still exceed 16K, so compare against `--no-thinking` for budget-runaway diagnostics. `--extra-body` remains the escape hatch for controls the detector does not know. Saved JSON records `thinking_enabled` per pack and adds `thinking_control`/`reasoning_effort` when the run uses the non-default effort path.

### Derive the token ceiling from the clock (`--budget-from-timeout`, #145)

Timeout scaling derives the **clock** from measured TPS and holds the **token ceiling** fixed. When the clock is capped instead — `--timeout-ceiling-s`, an explicit `--timeout-per-case`, or an operator who will not run a six-hour suite — the clock silently becomes the tighter limit again: every long trace dies as `timeout`, and "needed more thought than we allow" is indistinguishable from "stuck". `--budget-from-timeout` is the dual direction: **fix the clock, derive the tokens.**

- Per request, `budget = floor(headroom × effective_clock × measured_tps)`, clamped to the arm's ceiling (`--thinking-max-tokens` on thinking arms — note it overrides `--max-tokens` there, so pin it explicitly for an A/B — `--max-tokens` or the pack default otherwise). `effective_clock` is whatever governs that request after scaling, ceiling and the model-turn cap, so this composes with every timeout knob rather than replacing one. `--budget-headroom F` (default `0.8`) leaves room for prefill, verification, turn overhead and the probe's empty-context optimism.
- The budget is delivered as `max_tokens` (engine-agnostic, always) and, on a thinking request, the reasoning share — budget minus an answer reserve (the pack's `timeout_baseline_tokens`, at most a quarter of the budget) — as `thinking_budget_tokens` / `reasoning_budget_tokens` **on llama.cpp only** (detected via `GET /props`). Both are sent because trees differ in which key they read. vLLM, SGLang and cloud endpoints drop those keys silently, so there the run warns and enforces `max_tokens` only. The pair is kept coherent on purpose: a reasoning cap alone relocates the overrun into content (observed: reasoning capped at 8,192 with no total cap still reached 13,238 tokens).
- **Positive control.** On llama.cpp, one throwaway thinking-ON request with a 128-token budget is sent at startup; if it comes back with more than 1,024 completion tokens the budget was ignored (server booted with `--reasoning-budget`, a tree reading a different key) and the run **refuses to start** rather than running a whole suite unbudgeted. `--no-budget-control` skips it. `--measured-tps` is required when the startup probe cannot run; use the *slowest* arm's rate when comparing configs, so every arm gets the same budget and the score does not measure throughput.
- Agent-owned packs (`hermesagent-20`, `aider-polyglot-30`) are `n/a`: their agent makes its own model calls in-container and an episode is an open-ended number of turns, so a per-request budget has no meaning there (the same reasoning as the #149 clock floor).
- The run prints one line per pack (`[runner] reasonmath-15 token budget: 12288 (clock binds: derived 12288 = 0.8 x 334s x 46.0 tok/s, ceiling 65536), reasoning 11264 + answer reserve 1024; delivered as max_tokens + thinking_budget_tokens + reasoning_budget_tokens`), records the whole thing in the result JSON as `token_budget` (mode, headroom, measured rate, engine, control verdict, per-pack numbers), and tags the markdown header `[TOKEN BUDGET: derived …]`. Scores are poolable only across runs with the same budget; a result without `token_budget` ran with a fixed ceiling.

In multi-turn CLI and Hermes agent loops, captured assistant reasoning stays in the saved result for inspection, but prior `reasoning`, `reasoning_content`, `reasoning_details`, and `codex_reasoning_items` fields are stripped from the next outgoing request by default. This avoids replaying provider-private or stale reasoning state. Use `--preserve-reasoning-history` only when an endpoint explicitly requires that history for signed or encrypted reasoning continuity.

**Arm validity is checked automatically (#126).** Every completed pack is inspected for whether the requested reasoning state actually took effect: a thinking arm where *no* response returned reasoning is flagged `silent` (the model may not support thinking, or the server is not parsing it — the arm is not a valid thinking arm); a thinking arm where *fewer than half* the responses returned reasoning is flagged `sparse` (usually an adaptive-thinking model choosing not to think — not a misconfiguration, but the arm's scores were mostly produced without reasoning, so a warning only, never a `--strict-thinking` failure); and a no-thinking arm where *any* response returned reasoning is flagged `contaminated` (the server is likely forcing reasoning, e.g. llama.cpp `--reasoning on`, or the off-switch sent is not the one the model's chat template reads — the arm is not a valid baseline). Reasoning counts `reasoning_content`/`reasoning` fields and `<think>` markers left in `content` (thinking happened but was not extracted, so the grader scores it as answer text). Findings appear in the run-summary `Warnings:` list and in saved JSON as `thinking_validity` (per-pack `expected` / `responses` / `with_reasoning` / `status`), so the invalidity travels with the data. Add `--strict-thinking` to turn a finding into exit code 4 for CI. The check runs on the responses already collected — no extra requests — and is skipped for synthetic traffic (mocks / negative control).

## Output

```
=== benchlocal-cli --medium  (endpoint: http://localhost:8020, model: qwen3.6-27b-autoround, 2026-05-09T10:30) ===

Pack                      | Pass@1       | Pass@3         | Flaky | p50 latency | p95 latency | Status
ToolCall-15 (v1.0.1)      | 14 / 15 (93%)| 15 / 15 (100%) |   1   |     8.2s    |     12.1s   | ✅
InstructFollow-15 (v1.0.0)| 13 / 15 (87%)| 14 / 15 (93%)  |   1   |    11.4s    |     17.8s   | ✅
StructOutput-15 (v1.0.0)  | 15 / 15 (100%)|15 / 15 (100%) |   0   |     6.9s    |      9.2s   | ✅
DataExtract-15 (v1.0.0)   | 12 / 15 (80%)| 12 / 15 (80%)  |   0   |     7.3s    |     10.5s   | ✅
─────────────────────────|──────────────|─────────────────|───────|─────────────|─────────────|──────
TOTAL                     | 54 / 60 (90%)| 56 / 60 (93%)  |   2   |             |             |

Failure breakdown:
- toolcall-15 TC-07: pass@2 (wrong arg value on pass@1; recovered on retry)
- instructfollow-15 IF-03: pass@2 (word count missed on pass@1; recovered on retry)
- dataextract-15 DE-05: verifier_fail (7/14 atomic fields correct (50%). product_name: mismatch)
```

Normal runs use failure-only inline retries by default: `--retry-failures 3` means at most three total attempts, stops on the first pass, and never reruns a clean pass@1. The official `passed`, `score`, and regression fields remain strict pass@1; the additive `pass_at_k` summary and `pass@1`/`pass@2`/`pass@3`/`fail` labels expose recovery without rewriting the baseline. Saved JSON keeps the baseline scenario at its existing location and nests complete later attempts under `retry_attempts`.

Completed content failures are eligible by default. Harness/infrastructure failures are also retried, even with `--retry-failures 0`; expensive `token_limit`, `timeout`, and `agent_runner_timeout` runaways require `--retry-runaways`, while `verifier_not_implemented` is never retried. `model_output_unparseable` (#152) — the engine returned 5xx because it could not parse the model's own output — is a model verdict with runaway cost, so it follows the runaway rule: not retried unless `--retry-runaways`, and even then a retry that reproduces an earlier attempt's rejection byte-for-byte (same parse position) stops the remaining attempts (`verifier_trace.retry_stopped` records this). Such a 5xx is also never retried by the transport loop, unlike a genuine 5xx. Use `--no-retry` for strict content pass@1-only execution. Pack authors can mark a safety-sensitive scenario with `no_best_of_n: true`: retries and their labels remain visible, but a later pass receives no pass@k credit.

For agentic packs (e.g. `aider-polyglot-30`), the headline number is `pass_rate` over 30 exercises rather than per-scenario pass/fail; per-exercise breakdown is surfaced in the JSON `verifier_trace.upstream_per_exercise`. See [docs/AIDER_POLYGLOT_30.md](docs/AIDER_POLYGLOT_30.md) for the full output shape.

When `--repeat N` is greater than 1, the markdown table adds per-pack `Std` and `CV` columns derived from repeat-arm pass rates. The saved JSON includes the same data under each pack result as `variance: {"repeat", "mean", "std", "cv"}` so cross-rig runs can distinguish real deltas from run-to-run noise.


`--repeat N` is the rigorous whole-benchmark variance tool: it re-runs passes and failures, so it can detect flaky passes as well as flaky failures. The CLI default is `--repeat 0`, where `0` selects the normal failure-only inline path; `--repeat 3` disables inline retries and executes every selected scenario three times.

For an issue-ready Results Card v2, opt in with `--report md`. Its stable table always includes `Std`/`CV` (shown as `—` for a single run), p50/p95 latency, the score normalized to `/150`, and a collapsed raw per-item log reconstructed from the saved scenario records:

```bash
benchlocal-cli run --full --repeat 3 --report md --report-out results.md \
  --endpoint http://localhost:8020 --model qwen3.6-27b --save-json results.json
```

With Markdown output, the card is printed to stdout and optionally copied to `--report-out`. JSON stdout remains available with `--output json --report md --report-out results.md`; the file receives Markdown while stdout stays pure JSON. Without `--report`, the existing stdout shape is unchanged. Saved JSON adds top-level `repeat` and `equivalent_score_150` fields, alongside each pack's existing `variance` and `latency` aggregates.

For the cheaper everyday question "are the failures in this saved run systematic or flaky?", retry only its failed pass@1 scenarios:

```bash
benchlocal-cli run --retry-failed --previous-result baseline.json \
  --api-key "$BENCHLOCAL_API_KEY" --save-json failure-retries.json

# Bare --retry-failed means 3 attempts per failed scenario; choose another count explicitly.
benchlocal-cli run --retry-failed 5 --previous-result baseline.json \
  --save-json failure-retries-5x.json
```

The source must be a pass@1 result; a prior `--repeat N` artifact already contains the rigorous variance signal and is rejected as a retry baseline.

The endpoint, model, thinking mode, and recorded sampling overrides are inherited when available; current credentials still come from the CLI or environment. Pack-set flags such as `--pack` intersect with the generated failed-scenario selection. Human output keeps the baseline pass@1 score first and labels retry totals as `RETRY SAMPLE`; JSON remains an honest partial-selection artifact and adds top-level `retry_failed` consistency metadata. `0/N` is `systematic`; any passing retry is `flaky` because the baseline arm failed. This diagnostic cannot use `--exit-on-regression` and cannot overwrite its `--previous-result`.

## Where the time went

The number in each pack line is `p50` — median per-scenario latency — and the markdown table adds `p95`. Neither adds up to elapsed time: per-scenario latency excludes inline retries (a `verifier_fail` can run three times and only one attempt is summarised), sandbox build/boot/teardown, verification calls, and inter-scenario overhead. A pack whose `p50` is 38 s can still own most of a four-hour run. So every run now records its wall clock (#146):

- `packs[].duration_s` — wall clock around the whole pack as it ran, retries and verification included; `duration_s` at the top level — `started_at` to `finished_at` (for a resumed run this spans the sessions and the time between them; the per-pack figure is the sum of the sessions that ran part of the pack).
- After the `Failure breakdown:` block the markdown adds one line for the run and one per pack, splitting each into the parts the percentiles do and do not see:

  ```
  Wall clock: 3h58m42s — packs 3h55m10s, scenario latency 2h01m03s, retries 12m30s, overhead 1h41m33s (sandbox setup/teardown, verification, inter-scenario)
  - cli-40: 1h40m02s (latency 1h02m11s, retries 8m00s, overhead 29m51s)
  - hermesagent-20: 1h22m40s (latency 1h20m03s, overhead 2m37s)
  ```

  The pack table and the `TOTAL` row are unchanged — their bytes are pinned by downstream parsers — and the block is omitted for results that carry no duration (pre-#146 JSON), so existing output is byte-identical. `overhead` is exactly the retry/sandbox/verify cost that was invisible before; at the run level it also includes sandbox start/stop and the gaps between packs. Use it with `--budget-from-timeout` (#145) to check that `N scenarios × derived budget` fits the time you have.

## Inspecting failures

The `Failure breakdown:` block above is the quickest read — `failure_mode` + full detail per failed scenario, printed at the end of every run. For deeper forensics, any run with `--save-json` (which `quality-test.sh` sets) records per-scenario tokens, latency, and the full verifier trace; the `inspect` subcommand reads it back:

```
benchlocal-cli inspect results.json --failed                 # every failure + reason + tokens + latency
benchlocal-cli inspect results.json --scenario IF-10 --full  # full prompt/response/verifier trace + conversation
benchlocal-cli inspect results.json --mode timeout           # only this failure_mode
benchlocal-cli inspect results.json --diff previous.json     # side-by-side vs a prior run (regressions + latency delta)
benchlocal-cli inspect results.json --logs ./sandbox-logs    # pull sandboxed-pack stdout/stderr
benchlocal-cli rescore results.json --pack reasonmath-15 --output rescored.json
```

Use `rescore` when a scorer changes and the saved JSON already contains `raw_response`. It re-grades from stored model responses without calling the endpoint again. Replayable single-turn sandbox packs (currently BugFind, HumanEval+, and LiveCodeBench) are re-run inside their pre-built verifier containers; multi-turn/workspace packs remain skipped because their destroyed filesystem or agent state cannot be reconstructed from JSON. Use `--sandbox-image-tag` when the corrected verifier was built under a non-`latest` tag.

`failure_mode` is one of `verifier_fail`, `token_limit`, `timeout`, `agent_runner_timeout`, `agent_runner_crashed`, `server_error`, `model_output_unparseable`, `stall` (#157, `--stream` only: the endpoint went quiet mid-stream), `http_error`, `model_endpoint_unreachable`, `result_json_malformed`, `wrong_answer`, `verifier_not_implemented`.

`model_output_unparseable` (#152) means the engine answered 5xx because it could not parse what the model generated — llama.cpp's `Failed to parse tool call arguments as JSON` (typically a repetition loop of empty tool calls that ran to the cap) or `Failed to parse input at pos N`. It is split from `server_error` because it is a deterministic property of the generation, not a sign the server is unwell: retrying reproduces the same rejection, so neither the transport loop nor the inline retries repeat it (see `--retry-runaways` above). `detail` keeps the engine's message, trimmed, so the parse position that fingerprints the generation survives; filter with `inspect --mode model_output_unparseable`. An unrecognised 5xx stays `server_error` and keeps its retries.

`token_limit` (#61) means a failed completion hit the token cap (`finish_reason == "length"`) and was truncated mid-output — the model overthought or looped until the budget ran out, *not* a content verdict. It's reclassified from the underlying content-failure (the original verdict is kept in `detail`), so "looped / truncated" reads distinctly from "ran to completion but wrong" (`verifier_fail`). Filter it with `inspect --mode token_limit`. The pack-level `diagnostics.finish_reasons` rollup counts `length` across every recorded completion, including successful, retry, and multi-turn responses that the failure label intentionally does not reclassify. Code-reasoning packs also expose extraction method, issue, and selected response-field distributions under `diagnostics.extraction`.

**Runaways are surfaced separately from wrong answers (#148).** `token_limit`, `timeout` and `agent_runner_timeout` mean the model *never finished*; `verifier_fail` means it answered and was wrong. Both score as a fail — the arithmetic is unchanged, so results stay comparable with every published number — but when any runaway occurred the markdown adds one summary line after the TOTAL row, e.g. `Runaway: 3 / 54 scenarios never finished (token_limit 3) — still counted as failures. Each is either a budget artifact (the cap cut off an answer still in progress) or a model loop that would never finish; inspect the output to tell which.`, followed by a `- <pack>: n / N (…)` line per affected pack. The line is omitted entirely when the count is zero, so the default markdown is byte-identical to before. The saved JSON always carries the rollup under `packs[].runaway` and top-level `runaway` (`{"count", "total", "rate", "modes": {"token_limit", "timeout", "agent_runner_timeout"}}`), counted over the same attempt-1 rows as `passed / total`; `rescore` recomputes it. A runaway rate that is not near zero means something other than wrong answers shaped the score: either a cap too low for answers still in progress, or a model loop that would never finish (a scenario that runs away on every attempt, with repeating output, is the second kind and is a real model failure). Re-running the affected scenarios with a larger budget tells them apart — see "Per-case timeouts" and `--retry-runaways`.

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
