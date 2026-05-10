# Sandbox HTTP verifier protocol

Each sandboxed pack (BugFind-15, CLI-40, HermesAgent-20) ships a Docker container that hosts an HTTP verifier on internal port 9000 (mapped to a different host port per pack). Mocked-tools approach throughout — no Playwright, no Chromium, no real network.

## Status

🟡 **v0.7 upstream verifier-runtime lift.** All 3 containers expose `/health` with `stage="v0.7"` and return real verifier result shapes. The upstream repos do not expose the static fixture-tree layout imagined in the v0.7 brief; the fixture source available in practice is each pack's `verification/` runtime.

- BugFind delegates to upstream `verifyAnswer`, with Python/Node/Go/Rust runtime tools available in the sandbox image.
- CLI delegates one-shot scenarios to upstream `verifyOneShotSubmission` and command-block replays to `verifyMultiRoundReplay`.
- Hermes carries the upstream verifier runtime in the image, but full parity still requires runner-side multi-turn delegation because upstream Hermes owns the complete agent/model loop.

| Pack | Host port | Verifier endpoint(s) | Multi-turn? |
|---|---|---|---|
| BugFind-15 | 9001 | POST /verify | no |
| CLI-40 | 9002 | POST /verify | no |
| HermesAgent-20 | 9003 | POST /verify-{start,turn,end} | yes |

## Build + smoke-test

```bash
bash tools/build-sandboxes.sh    # builds all 3
bash tools/test-sandboxes.sh     # confirms /health responds on all 3
```

## Single-turn protocol (BugFind, CLI)

```http
GET /health
→ 200 OK
  {"status": "ok", "pack": "<pack-id>", "stage": "v0.7"}

POST /verify
Content-Type: application/json
{
  "scenario_id": "BF-01",
  "scenario": { /* full upstream scenario object — includes raw_scenario, asserts, etc */ },
  "response": { /* OpenAI completion response from the model under test */ },
  "messages": [ /* full conversation history */ ]
}

→ 200 OK
{
  "passed": true | false,
  "failure_mode": "passed" | "verifier_fail" | "wrong_answer" | "invalid_json" |
                  "timeout" | "server_error" | "verifier_not_implemented",
  "detail": "human-readable explanation",
  "trace": { /* pack-specific debug info, optional */ }
}
```

## Multi-turn protocol (HermesAgent)

The runner orchestrates the agent loop; sandbox tracks per-scenario state.

```http
POST /verify-start
{
  "scenario_id": "HA-01",
  "scenario": { /* full upstream scenario */ }
}

→ 200 OK
{
  "scenario_state_id": "uuid-...",       # opaque ID, sandbox uses to look up state
  "prompt": [ /* messages array — first turn to send to model */ ],
  "tools": [ /* tool definitions */ ]
}

POST /verify-turn
{
  "scenario_state_id": "uuid-...",
  "model_response": { /* OpenAI completion from this turn */ }
}

→ 200 OK — either of:
{
  "action": "next-prompt",
  "prompt": [ /* new messages including tool result */ ],
  "tools": [ /* tool defs (unchanged across turns) */ ],
  "turn_count": 3
}

OR

{
  "action": "verify-final",
  "passed": true | false,
  "failure_mode": "passed" | "verifier_fail" | "wrong_answer" | ...,
  "detail": "...",
  "trace": { /* full agent trace + state */ }
}

POST /verify-end       # explicit "model gave up" or runner hit turn limit
{
  "scenario_state_id": "uuid-..."
}

→ 200 OK
{
  "passed": false,
  "failure_mode": "timeout",
  "detail": "model exceeded 20-turn limit",
  "trace": { /* partial agent trace */ }
}
```

## Failure mode taxonomy

| Mode | Meaning |
|---|---|
| `passed` | All assertions met |
| `verifier_fail` | Real test failure (e.g., pytest red, command output mismatch, trace mismatch) |
| `wrong_answer` | Model emitted unexpected response shape (e.g., text instead of tool call) |
| `invalid_json` | Tool call arguments didn't parse, or fix wasn't valid Python, etc |
| `timeout` | Hit per-scenario time limit (10s for CLI commands; 20-turn limit for HermesAgent) |
| `server_error` | Sandbox infra issue (state id missing, fixture missing, container OOM, etc) |
| `verifier_not_implemented` | Runner-side skip when a sandboxed pack is requested without sandbox support or a sandbox cannot start |

## CLI safety model

The v0.7 CLI sandbox keeps the HTTP verifier on the normal mapped port so the existing runner protocol remains unchanged. Command execution itself is constrained by verifier gates and the upstream verifier runtime:

- container runs as non-root `verifier`
- simple commands are parsed with `shlex.split`
- compound shell syntax is routed through `bash -c` after raw-string safety checks
- network and destructive executables/tokens are rejected before execution
- each scenario gets a fresh temporary workspace
- timeout is capped at 10s
- stdout/stderr are truncated to 64 KiB

Python, Perl, and Ruby are allowed; upstream CLI is a shell-task environment with scripting languages available, not a shell-builtins-only benchmark. `CODEX_BRIEF_V6.md` recommended Unix-domain sockets plus Docker `--network none`, but the local `SandboxClient` still uses HTTP over a host-mapped port. This is a documented parity gap rather than a silent claim of full isolation.

This mirrors the deterministic-pack `ScenarioResult` taxonomy — verifiers in sandbox containers produce the same shape as in-process verifiers, so the runner can treat them uniformly.

## Container lifecycle

The runner manages this via `benchlocal_cli/sandbox.py:SandboxClient`:

```python
client = SandboxClient(SANDBOX_REGISTRY["bugfind-15"])
client.start()                     # docker run + wait for /health
try:
    for scenario in pack.scenarios:
        result = client.verify(scenario, response, messages)
finally:
    client.stop()                  # docker stop --rm
```

- `start()` polls `/health` for up to 30s after `docker run`
- `stop()` is idempotent (safe even if container died)
- SIGINT in the runner triggers `stop()` for all active SandboxClients before exit
- For HermesAgent: `verify_hermes_start` / `verify_hermes_turn` / `verify_hermes_end` instead of `verify`

## Why this protocol

Same shape across all 3 packs (deterministic `passed/failure_mode/detail/trace` response) means the Runner's dispatch logic stays uniform. The only difference is single-turn vs multi-turn (bugfind/cli vs hermes), which the runner detects via `SandboxConfig.multi_turn` and dispatches accordingly.

The HTTP-server pattern (vs subprocess) means containers stay warm across all scenarios in a pack run — no startup cost per scenario. For HermesAgent's 20 scenarios with multi-turn loops, this matters significantly.
