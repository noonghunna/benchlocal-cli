# HermesAgent-20 sandbox — v0.7.3 (upstream-runtime delegation)

The most complex of the 3 sandboxes — delegates the entire agent loop to the
pinned upstream `nousresearch/hermes-agent` codebase. Replaces the v0.6 mocked-
tool state machine that capped real-model scores at 25% / 20%.

## What it does

1. Runner POSTs `/verify-start` with `{scenario, model_endpoint, model_name, sampling}`
2. Sandbox writes `request.json` and spawns upstream `agent-runner.py` (Python)
3. Upstream agent runs the entire scenario: real tool catalog, multi-turn flow,
   trace recording — all via real LLM calls against `model_endpoint`
4. Upstream writes `result.json` (toolEvents, finalResponse, messages)
5. Sandbox grades the result Python-side (keyword match + completion check)
6. Returns `{action: "verify-final", passed, failure_mode, detail, trace}`
   with the upstream payload preserved in `verifier_trace`

The runner-side `/verify-turn` loop is unused for Hermes (cf. CLI pack which
still uses it for multi-round shell sessions).

## Where the upstream Hermes install lives

Detection priority (`benchlocal_cli/sandbox.py:detect_hermes_agent_host_path`):

1. `HERMES_AGENT_FORCE_BAKED=1` → skip host detection, use image-baked install
2. `HERMES_AGENT_HOST_PATH=<dir>` → bind-mount this path RO at `/opt/hermes-agent`
3. Auto-detect: `/opt/hermes-agent`, `~/hermes-agent`, `~/.local/hermes-agent`,
   `~/.hermes/hermes-agent` (the layout the official `hermes` installer creates)
   — single match wins; multiple matches → error with set-HOST_PATH guidance
4. `which hermes` → follow the symlink → walk up to install root. Catches
   non-standard install layouts the candidate list doesn't cover.
5. Image-baked fallback (Dockerfile `BAKE_HERMES_AGENT=1`, default ON)
6. Fail loud — `/health` reports `status: "missing-hermes-agent"`,
   `/verify-start` returns `server_error` with rebuild guidance

A valid install must contain `run_agent.py` and `hermes_state.py`.

## Failure modes

| failure_mode | When |
|---|---|
| `passed` | Upstream completed; required keywords matched; no destructive-action issues |
| `wrong_answer` | Empty final response and no tool calls, or partial run with no answer |
| `verifier_fail` | Upstream completed but final response lacks success-case keywords |
| `agent_runner_timeout` | Upstream subprocess exceeded 15min (configurable via `HERMES_SUBPROCESS_TIMEOUT_S`) |
| `agent_runner_crashed` | Upstream exited nonzero or didn't write `result.json` |
| `result_json_malformed` | Upstream's result.json couldn't be parsed |
| `model_endpoint_unreachable` | Upstream reported network error connecting to `model_endpoint` |
| `server_error` | Server-side bug or missing config (model_endpoint absent, hermes-agent install missing) |

## Isolation guarantees — bench runs do NOT modify your host install

When bind-mounting a host hermes-agent install, the bench is read-only against
your install:

- **`docker run -v <host>:<container>:ro`** — hardcoded `:ro` flag. The
  container cannot write to `~/.hermes/hermes-agent/` even if upstream code
  tried.
- **Per-scenario `HERMES_HOME`** — agent-runner.py overrides `HERMES_HOME`
  to a throwaway `/tmp/hermes-runs/<uuid>/home/` per scenario. Your real
  `~/.hermes/state.db`, sessions, memory, etc. are never touched.
- **Per-scenario `HERMES_WRITE_SAFE_ROOT`** — any tool that writes files
  lands in `/tmp/hermes-runs/<uuid>/workspace/`, also throwaway.
- **Container `HOME=/home/verifier`** — anything else that reads `$HOME`
  writes inside the container (where it gets wiped on `--rm`), not on your
  host filesystem.
- **Container is `--rm`** — all in-container state vaporizes on stop.

If you want to verify after a bench run: `git -C ~/.hermes/hermes-agent status`
should show no changes; `ls -la ~/.hermes/state.db ~/.hermes/sessions/ 2>/dev/null`
should reflect only your real prior usage, not the bench's scenario IDs.

## Build

```bash
bash tools/build-sandboxes.sh
# OR just hermes:
docker build -t benchlocal-sandbox-hermes:latest sandboxes/hermes/

# Skip the upstream clone (bind-mount-only image):
docker build --build-arg BAKE_HERMES_AGENT=0 \
  -t benchlocal-sandbox-hermes:latest sandboxes/hermes/
```

## Run

```bash
# With auto-detected host hermes-agent install:
benchlocal-cli run --pack hermesagent-20 --enable-sandboxed-packs \
  --endpoint http://localhost:8001 --model qwen3.6-27b

# Force bind-mount of a specific install:
HERMES_AGENT_HOST_PATH=~/work/hermes-agent \
  benchlocal-cli run --pack hermesagent-20 ...

# Force the image-baked install (regression-test the bake path even on a
# dev rig that has hermes-agent installed locally):
HERMES_AGENT_FORCE_BAKED=1 \
  benchlocal-cli run --pack hermesagent-20 ...
```

## Configuration

Container env:

| Var | Purpose | Default |
|---|---|---|
| `HERMES_AGENT_PATH` | Where the install lives inside the container | `/opt/hermes-agent` |
| `HERMES_JOB_ROOT` | Per-scenario job dirs (cleaned after each scenario) | `/tmp/hermes-runs` |
| `HERMES_SUBPROCESS_TIMEOUT_S` | Upstream agent-runner wall-clock cap | `900` (15min) |
| `BENCHLOCAL_HERMES_AGENT_COMMIT` | Override the commit reported in `/health` | git-detected at runtime |
| `HERMES_PINNED_COMMIT` | Build-time commit (set by Dockerfile) | manifest.mjs default |

Runner-side env:

| Var | Purpose |
|---|---|
| `HERMES_AGENT_FORCE_BAKED` | `1` → skip host detection (test override) |
| `HERMES_AGENT_HOST_PATH` | Explicit bind-mount source on the host |

## Iterating on agent-runner.py against a fork's API drift

`verification/agent-runner.py` is vendored from upstream `nousresearch/hermes-agent`
at a pinned commit. Active forks (especially the official installer's
`~/.hermes/hermes-agent`) often drift on `AIAgent.__init__` kwargs and
`run_conversation` return shape.

When you hit `agent_runner_crashed: AIAgent.__init__() got an unexpected
keyword argument '...'` errors, you have two iteration paths:

**Fast iteration (no rebuild)** — bind-mount your local `verification/` over
the baked one so edits take effect on the next sandbox start:

```bash
# Edit sandboxes/hermes/verification/agent-runner.py (in your benchlocal-cli clone)
# Then drive the runner with the verification dir bind-mounted:
docker run --rm -d --name hermes-iter \
  -p 9003:9000 \
  -v ~/.hermes/hermes-agent:/opt/hermes-agent:ro \
  -v ~/.local/share/uv/python:/opt/uv-python:ro \
  -v "$(pwd)/sandboxes/hermes/verification:/app/verification:ro" \
  -v "$(pwd)/sandboxes/hermes/server.py:/app/server.py:ro" \
  -e HERMES_AGENT_PATH=/opt/hermes-agent \
  -e HERMES_AGENT_PYTHON=/opt/uv-python/cpython-3.12/bin/python \
  benchlocal-sandbox-hermes:latest
# POST a single scenario to /verify-start to test the fix
docker stop hermes-iter
```

**Once you have a working agent-runner, rebuild the image:**

```bash
docker build --build-arg BAKE_HERMES_AGENT=0 \
  -t benchlocal-sandbox-hermes:latest sandboxes/hermes/
```

Common drift points to check first when iterating:
- `inspect.signature(AIAgent.__init__)` — kwargs renamed, removed, or made positional
- Return shape of `agent.run_conversation(prompt)` — keys like `final_response`,
  `messages`, `completed`, `partial`, `api_calls`, `model`, `input_tokens`,
  `output_tokens` may have moved or been renamed

Quick check against your install:
```bash
~/.hermes/hermes-agent/venv/bin/python -c "
import inspect
from run_agent import AIAgent
print(list(inspect.signature(AIAgent.__init__).parameters))
"
```

## Re-sync upstream

```bash
bash tools/sync-vendor.sh HermesAgent-20
node tools/build-packs.js HermesAgent-20
docker build -t benchlocal-sandbox-hermes:latest sandboxes/hermes/
```

The pinned upstream commit lives in `vendor/HermesAgent-20/verification/manifest.mjs`
and is mirrored into the Dockerfile build arg.
