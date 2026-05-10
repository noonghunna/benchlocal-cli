# Changelog

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
