# Changelog

## 0.7.4

- Hermes grading parity: replaced our v0.7.3 keyword-match Python grader
  with upstream's `core.mjs`. Hermes container now runs upstream's
  `verification/server.mjs` (Node) on internal port 4010; our Python
  proxies `/verify-start` to upstream's `POST /run-scenario`.
  Visible scores now match what upstream's verifier would report.
  **Gemma 4 31B v0.7.4 actual: 10/20 = 50%** (was 6/20 = 30% with v0.7.3
  keyword grader). v0.7.4 caught 6 false-negatives that v0.7.3 missed
  (HA-03 refusal, HA-06/11/13/15/18 actual wins) and correctly failed 2
  v0.7.3 lucky-passes (HA-04, HA-14). See `docs/HERMES_V073_AB.md`.
- Re-pinned upstream HermesAgent-20 from `ea74f61` (6mo stale) to
  `44cdf555` (upstream main HEAD). Newer pin ships hermes-agent v0.13.0
  with months of tool-calling reliability fixes.
- `_normalize_base_url` in server.py now ensures `/v1` suffix on the
  endpoint passed to upstream (was previously stripping it; caused HTTP
  404 on every model call → 0 tool events).
- Patched upstream's `writeHermesConfig` to inject `context_length: 64000`
  under `model:` and `compression:` blocks (driven by
  `BENCHLOCAL_HERMES_CONTEXT_OVERRIDE` env, default 64000) — works around
  Hermes' 64K minimum context-window check on models served at smaller
  windows (e.g. Gemma 4 at 32K).
- Dockerfile: explicit `BAKE_HERMES_AGENT=1 must succeed` (no silent
  fallback per Codex review); /opt/hermes-venv on PATH so upstream Node
  spawning python3 resolves to the venv with hermes-agent installed.
- Verification dir mounted at `/opt/verification/` (not `/app/verification/`)
  to match upstream's hermes-runtime.mjs hardcoded path.
- Image gains Node 22, Chromium, agent-browser, and a Python venv with
  hermes-agent editable-installed (~600 MB → ~1.5 GB). Use
  `--build-arg BAKE_HERMES_AGENT=0` for bind-mount-only images.
- New `entrypoint.sh` boots upstream Node first, polls `/health` until
  ready (fail-loud + `exit 1` if it doesn't), then runs the Python
  proxy. Cleanup trap kills Node on Python exit.
- `_grade()`, `_run_agent_runner()`, scenario-kind tool requirements all
  removed — upstream owns grading + agent loop entirely.
- New trace fields: `upstream_status`, `upstream_score`, `upstream_note`,
  `upstream_summary`, `upstream_verifier`, `upstream_output`,
  `upstream_timings`, `upstream_raw` (capped at 16KB), `raw_log_tail`
  (last 4KB of upstream's full rawLog).
- Schema version bumped to "2". `failure_mode` and `detail` fields
  retained for back-compat with v0.7.3 downstream readers.
- `/health` now probes both the install (run_agent.py exists?) AND the
  upstream Node grader's `/health` — surfaces split-brain conditions.
- Mock-pass marker (`BENCHLOCAL_PASS:<id>`) preserved in our Python
  before the upstream proxy call; mimics v0.7.4 schema with
  `mock_pass: true` in the trace.
- 40/40 tests passing (was 33). Test churn: removed obsolete `_grade`
  tests, added 12 new tests covering `_translate_request`,
  `_translate_upstream_result`, `_classify_failure`, `_cap_upstream_for_trace`,
  `_normalize_base_url`, `_mock_pass_response`, and the verify-start
  refusal paths.

## 0.7.3

- Hermes upstream-runtime delegation: `/verify-start` spawns upstream
  `agent-runner.py` from a host-mounted or image-baked
  `nousresearch/hermes-agent` checkout. Replaces the v0.6 mocked-tool
  state machine that capped real-model A/B at 25% / 20%.
- Detection priority for the upstream install: `HERMES_AGENT_FORCE_BAKED=1`
  → `HERMES_AGENT_HOST_PATH` → auto-detect (`/opt/hermes-agent`,
  `~/hermes-agent`, `~/.local/hermes-agent`) → image-baked fallback →
  fail-loud at `/health`.
- New `SandboxConfig` fields: `host_mount`, `env`, `request_timeout_s`
  (Hermes uses 900s for `/verify-start`; bugfind/cli stay at 60s).
- Distinct failure modes surfaced from upstream: `agent_runner_timeout`,
  `agent_runner_crashed`, `result_json_malformed`,
  `model_endpoint_unreachable`. v0.8 `inspect --mode` will filter on these.
- Subprocess hardening: per-scenario job dir under `/tmp/hermes-runs/`,
  upstream agent runs in its own process group (`start_new_session=True`),
  timeout uses `os.killpg` for process-group cleanup, job dirs removed
  after each scenario.
- Reproducibility: `/health` and `verifier_trace` carry `hermes_agent_path`,
  `hermes_agent_source ∈ {host-mount, baked, missing}`, `hermes_agent_commit`
  (best-effort `git rev-parse HEAD`).
- Runner now passes `model_endpoint`, `model_name`, `sampling` to Hermes
  `/verify-start` so the upstream agent can call the same endpoint the
  runner is benching.
- Multi-turn early-out path now propagates the sandbox `trace` payload
  into `ScenarioResult.verifier_trace` (bug fix from v0.7.2 forensics).
- Mark Hermes `/health` stage as `"v0.7.3"`. BugFind / CLI unchanged.

## 0.7.1

- Add runner-side multi-turn sandbox orchestration for CLI-40 multi-round scenarios and HermesAgent-20.
- Generalize sandbox client multi-turn methods while keeping Hermes aliases for compatibility.
- Add CLI-40 `/verify-start`, `/verify-turn`, and `/verify-end` endpoints with iterative bash feedback and upstream replay grading.
- Persist multi-turn diagnostics in scenario results: turn count, assistant messages, and tool calls.
- Mark sandbox health endpoints as `stage="v0.7.1"`.

## 0.7.0

- Vendor upstream `verification/` runtimes for BugFind-15, CLI-40, and HermesAgent-20.
- Delegate BugFind verification to upstream `verifyAnswer`, with runtime support for Python, Node, Go, and Rust checks.
- Delegate CLI one-shot and replay verification to upstream verifier functions, and relax scripting-language bans to match the upstream execution model.
- Copy vendored verification runtimes into Docker build contexts during `tools/build-sandboxes.sh`.
- Mark sandbox health endpoints as `stage="v0.7"` and document the remaining Hermes runner-integration gap.

## 0.6.0

- Add v0.6 sandbox verifier implementations for BugFind, CLI, and HermesAgent using upstream-derived raw scenario metadata, deterministic rubric checks, safe command execution, and stateful mocked-tool tracing.
