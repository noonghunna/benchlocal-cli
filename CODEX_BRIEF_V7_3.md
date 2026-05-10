# Codex implementation brief — benchlocal-cli v0.7.3 (Hermes upstream-runtime delegation)

## Context

v0.7.0 / v0.7.1 / v0.7.2 closed the BugFind + CLI parts of the "real verifier parity" vision. **HermesAgent-20 is the last open piece.** Today it's still using the v0.6 state-machine adapter with keyword-evidence grading on the model's final answer. Real-model A/B (Qwen3.6-27B / Gemma 4 31B on dual-3090) caps at 25% / 20% — real signal but the keyword-match floor distorts the numbers.

Today's diagnostic identified 5 distinct failure patterns (`docs/CODEX_REPORT.md` from v0.7.1, plus today's session investigation):
- **Pattern A — refusals** (~5 scenarios): model treats Hermes scenarios as real-world questions and refuses
- **Pattern B — casual final summary** (~6 scenarios): model uses our 4 mocked tools but final message lacks forensic vocabulary the keyword-match expects
- **Pattern C — no tool use** (~3 scenarios): model just chats, never calls memory_set / artifact_write / trace_append
- **Pattern D — tool-set mismatch** (1+ scenarios): model wants `read`, `list`, `glob`, `execute_command`, `send_message`, `cron_*`, `browser` etc. — tools our minimal mock doesn't simulate. HA-05 hit this directly.
- **Pattern E — keyword-match accidents** (5 lucky-pass scenarios): model emits casual chat that happens to contain ≥2 expected keywords

All five collapse onto one root: **we're not running the upstream Hermes runtime.** v0.7.3 wires it in.

## Why this brief is risk-fronted

The v0.7 candidate vendored `vendor/HermesAgent-20/verification/agent-runner.py` — but inspecting it reveals it's a wrapper that imports from `/opt/hermes-agent`:

```python
from hermes_state import SessionDB
from run_agent import AIAgent
from tools.terminal_tool import set_approval_callback
```

`hermes_state`, `run_agent`, `tools.*` are the **actual Hermes agent codebase**, not in our vendor tree. **Important: many dev rigs already have hermes-agent installed locally.** v0.7.3 prefers bind-mounting an existing host install over baking a fresh copy into the sandbox image.

**Detection priority order** (Phase A's first job is wiring this):

1. **`HERMES_AGENT_HOST_PATH` env var** (user-set) → bind-mount into sandbox container at `/opt/hermes-agent`
2. **Common host paths** — auto-detect at runner startup. Check `/opt/hermes-agent`, `~/hermes-agent`, `~/.local/hermes-agent`. If exactly one is found, use it; if multiple are found, error and ask user to set `HERMES_AGENT_HOST_PATH` explicitly.
3. **Image-baked fallback** — if no host install found AND `HERMES_AGENT_BAKED_INSTALL=1` env var is set, the Dockerfile cloned a copy at build time → use the baked version
4. **Fail loud** — if none of the above, return clear error from `/health` endpoint and during `/verify-start`. Don't silently fall back to v0.6 keyword-match.

## Architecture — the integration shape

**Phase D / Codex-flagged gap closure pattern**: Hermes sandbox calls upstream `agent-runner.py` synchronously. The upstream agent owns the entire model loop — tool simulation, multi-turn flow, trace recording, grading. Our runner just kicks off the scenario and waits for the result.

This is a **departure from v0.7.1's runner-side multi-turn protocol** for Hermes specifically. The runner's `/verify-start` → `/verify-turn` loop is correct for CLI multi-round (where model + sandbox interleave naturally). Hermes is different: upstream agent-runner wants to drive its OWN model calls.

**The key insight**: v0.7.1's protocol already supports this. When `/verify-start` returns `action: "verify-final"` directly (Codex implemented this early-out path), the runner skips the turn loop. v0.7.3 has Hermes `/verify-start` do all the work and return final result.

```
Runner: POST /verify-start
        { scenario_id, scenario, model_endpoint, model_name, ... }

Hermes sandbox:
  - Build request.json for agent-runner.py
  - Spawn `python3 /app/verification/agent-runner.py /tmp/<job>/request.json`
  - Wait for completion (10-15 min timeout — hermes scenarios with 15-20 turns of real LLM calls)
  - Read result JSON
  - Map upstream pass/fail to our ScenarioResult
  - Return { action: "verify-final", passed, failure_mode, detail, trace, verifier_trace }

Runner: receives verify-final, builds ScenarioRun directly. No /verify-turn loop.
```

## Phases

### Phase A — Wire detection + bind-mount of host hermes-agent (~1-2 hr)

**Deliverable**: hermes sandbox container at `/opt/hermes-agent` is populated from one of: user-set bind mount, auto-detected host path, or baked-in clone.

The user already has `hermes-agent` installed somewhere on their host. Don't force a fresh clone; bind-mount theirs.

Files to touch:
- `benchlocal_cli/sandbox.py` — add `host_mount` field to `SandboxConfig` (None for non-Hermes packs). For Hermes, populate at `config_for_pack()` time:
  ```python
  def _detect_hermes_agent_host_path() -> str | None:
      explicit = os.environ.get("HERMES_AGENT_HOST_PATH")
      if explicit:
          if not Path(explicit).is_dir():
              raise RuntimeError(f"HERMES_AGENT_HOST_PATH={explicit} is not a directory")
          return explicit
      candidates = [
          Path("/opt/hermes-agent"),
          Path.home() / "hermes-agent",
          Path.home() / ".local/hermes-agent",
      ]
      found = [str(p) for p in candidates if p.is_dir()]
      if len(found) == 1:
          return found[0]
      if len(found) > 1:
          raise RuntimeError(f"multiple hermes-agent installs found: {found}; set HERMES_AGENT_HOST_PATH")
      return None  # caller decides whether to use baked-in fallback or fail
  ```
- `SandboxClient.start()` — when starting hermes container, if `config.host_mount` is set, add `-v {host_path}:/opt/hermes-agent:ro` to the `docker run` args. Read-only mount is fine since hermes-agent shouldn't need to mutate its own source.
- `sandboxes/hermes/server.py` `/health` — report `hermes_agent_path` field showing which path is mounted (for debuggability)
- `--list-packs` / quality-test wrapper — surface `HERMES_AGENT_HOST_PATH` in the env-var list

**Fail-loud policy**: if no hermes-agent path is mountable AND `HERMES_AGENT_BAKED_INSTALL=1` is unset (default), the hermes sandbox `/health` should report `status: "missing-hermes-agent"` and `/verify-start` should return a clear error. Don't silently fall back to v0.6 keyword-match — that hides whether v0.7.3 is actually engaged.

### Phase B — Optional: bake a fallback clone into the sandbox image (~1-2 hr, opt-in)

Files to touch:
- `sandboxes/hermes/Dockerfile`:
  - Install Python deps the upstream agent needs (openai-python, sqlite3 likely already present, anyio, httpx, etc.) — these install regardless of bake choice
  - **Conditional clone**: behind a build arg `BAKE_HERMES_AGENT=1`, clone upstream Hermes agent repo into `/opt/hermes-agent`. Off by default to keep image size small for users who bind-mount.
  - Set `HERMES_HOME` to a writable verifier-owned directory
  - Pre-create `/tmp/hermes-runs/` with verifier ownership for per-scenario job dirs
- `tools/build-sandboxes.sh` — pass `--build-arg BAKE_HERMES_AGENT=$BAKE_HERMES_AGENT` and document the env var

Default Codex deliverable: image WITHOUT baked clone (smaller, relies on bind-mount). User who wants a self-contained image runs `BAKE_HERMES_AGENT=1 bash tools/build-sandboxes.sh`.

**If upstream `hermes-agent` repo URL isn't determinable** (Phase A's secondary risk), file `docs/QUESTIONS.md` with what you found + recommend running with `HERMES_AGENT_HOST_PATH` only (skip the bake path). User confirmed at brief authoring time that they have a local install, so the bind-mount path is the primary working config either way.

### Phase C — Rewrite `sandboxes/hermes/server.py` to delegate (~2-3 hr)

Replace the v0.6 state-machine `_verify_final()` keyword-match logic with upstream-runner delegation.

New `/verify-start` body:
```python
def handle_verify_start(req):
    scenario_id = req["scenario_id"]
    scenario = req.get("scenario", {})
    model_endpoint = req.get("model_endpoint")  # NEW — runner passes this
    model_name = req.get("model_name")          # NEW
    api_key = req.get("model_api_key", "dummy") # NEW; vLLM doesn't validate

    # Build request.json for upstream agent-runner
    job_dir = Path(f"/tmp/hermes-runs/{uuid.uuid4()}")
    job_dir.mkdir(parents=True, exist_ok=True)
    request = {
        "resultPath": str(job_dir / "result.json"),
        "hermesHomeDir": str(job_dir / "home"),
        "workspaceDir": str(job_dir / "workspace"),
        "sessionId": str(uuid.uuid4()),
        "prompt": scenario["messages"][-1]["content"],  # last user message
        "scenarioId": scenario_id,
        "rawScenario": scenario.get("raw_scenario", {}),
        "generation": {
            "baseUrl": model_endpoint,
            "apiKey": api_key,
            "model": model_name,
            "provider": "openai",  # vLLM is OpenAI-compatible
        },
        # ... any other fields agent-runner expects
    }
    request_path = job_dir / "request.json"
    request_path.write_text(json.dumps(request, indent=2))

    # Spawn upstream agent-runner; wait for completion
    proc = subprocess.run(
        ["python3", "/app/verification/agent-runner.py", str(request_path)],
        capture_output=True, text=True, timeout=900,  # 15 min cap
        cwd="/opt/hermes-agent",
    )
    result = json.loads((job_dir / "result.json").read_text())

    # Grade — call the upstream JS grader (core.mjs) on the result
    # OR implement Python-side grading from the result.json fields directly
    grading = grade_via_upstream(scenario_id, result)

    return {
        "action": "verify-final",
        "passed": grading["status"] == "pass",
        "failure_mode": "passed" if grading["status"] == "pass" else "verifier_fail",
        "detail": grading["summary"],
        "trace": {
            "upstream_result": result,
            "upstream_grading": grading,
            "tool_events": result.get("toolEvents", []),
            "messages": result.get("messages", []),
        },
    }
```

Keep the `/verify-turn` and `/verify-end` endpoints functional for back-compat (they shouldn't be hit on Hermes anymore but other tests might rely on them).

### Phase D — Update runner to plumb model endpoint (~1 hr)

Files to touch:
- `benchlocal_cli/runner.py`: when calling `verify_multiturn_start()` for Hermes, pass `model_endpoint`, `model_name`, optional `model_api_key` in the payload. The existing multi-turn loop should already handle the early-out (Codex implemented `verify-final` from `verify-start` in v0.7.1 — verify it still works).
- Bump `/verify-start` HTTP timeout in `SandboxClient._post()` for hermes specifically — current 60s, need 900s (15 min) for full upstream agent runs.

Suggested approach for the timeout: add `timeout_s` parameter to `_post()` (default 60s, callers can pass more). Or special-case hermes-pack at runner level.

### Phase E — Tests + docs + version bump (~1 hr)

1. Update `tests/test_sandbox_runner.py` with mock for hermes early-out path
2. `docs/SANDBOX_PROTOCOL.md`: document the model-endpoint-passthrough flow for Hermes; note `/verify-turn` is unused for Hermes after v0.7.3
3. Sandbox `/health` stage labels: bump to `"v0.7.3"` (Hermes only — others stay `"v0.7"` since BugFind/CLI didn't change)
4. Update `sandboxes/hermes/server.py` module docstring (drop v0.6 state-machine framing)
5. `pyproject.toml` + `__init__.py` → `0.7.3`
6. CHANGELOG entry
7. `docs/CODEX_REPORT.md` overwrite with v0.7.3 status

## Constraints

- **Don't break BugFind / CLI sandbox.** Their `/verify` and CLI's `/verify-start/turn/end` work today; this round is Hermes-only.
- **Don't silently fall back to v0.6 keyword-match.** If hermes-agent isn't mountable AND not baked, the /health endpoint should report `status: "missing-hermes-agent"` with a clear "set HERMES_AGENT_HOST_PATH" message, and /verify-start should refuse with the same error. Hidden fallback to keyword-match would mask whether v0.7.3 is actually engaged on a given run.
- **Mock-pass marker (`BENCHLOCAL_PASS:scenario_id`) still works.** Short-circuit before invoking upstream agent-runner.
- **Verifier_trace populated** — the upstream `result.json` (toolEvents, messages, finalResponse, etc.) goes into `verifier_trace` for v0.7.2-style forensics.
- **Sandbox needs network egress to call model endpoint.** Update `SandboxConfig` for hermes if needed (today `network_isolated=False` already, so this should just work, but verify).

## Async report-back protocol

Same as v0.4/v0.6/v0.7/v0.7.1: write `docs/CODEX_REPORT.md` with phase-by-phase status. **File `docs/QUESTIONS.md` immediately if Phase A blocks.** Don't try to power through if upstream codebase isn't locatable.

## What to ASK rather than guess

- **Phase A** if `hermes_state` / `run_agent` modules don't have an obvious public install path — that's the gating risk. Ask before pivoting to Option B.
- **API key handling** — vLLM doesn't validate but other backends might. If upstream agent-runner requires a real key, ask how to pipe it.
- **Tool catalog mismatch** — if upstream agent-runner expects a tool catalog we can't fully simulate (e.g., a real browser), ask whether to mock vs skip those scenarios.
- **Grading approach** — calling `core.mjs` from Python (subprocess+node) vs implementing Python-side grading on the upstream `result.json`. Pick whichever is cleaner.

## Estimated total effort

- Phase A (detect + bind-mount host hermes-agent): 1-2 hr ← user has local install, this is the primary path
- Phase B (optional baked-clone build arg): 1-2 hr (skip the actual clone if upstream URL is unclear; just gate the Dockerfile lines behind the build-arg)
- Phase C (server.py rewrite to delegate to upstream): 2-3 hr
- Phase D (runner endpoint passthrough + timeout): 1 hr
- Phase E (tests + docs + bump): 1 hr

**Total: 6-9 hr.** No gating Phase A risk anymore — user has a local install we bind-mount.

## When done

Acceptance gate:
1. `tools/build-sandboxes.sh` builds clean
2. `tools/test-sandboxes.sh` reports all 3 healthy; hermes shows `stage="v0.7.3"`
3. `pytest tests/` passes (target 19+ tests with hermes early-out coverage)
4. **Real-model A/B**: hermesagent-20 score on Qwen + Gemma should be in **40-65% range** (was 25% / 20% with keyword-match). Cross-model discrimination should be larger than today's 5pp gap. Real Pattern A/B/C/D failures resolve.
5. `docs/CODEX_REPORT.md` updated with v0.7.3 status

After acceptance:
- Tag `v0.7.3` (release-notes workflow auto-publishes)
- This closes Codex's flagged Phase D gap from v0.7
- All 3 sandboxed packs (BugFind / CLI / Hermes) now use upstream runtimes for grading
- v0.7's "real verifier parity" vision **fully closed**
- Public flip is unblocked

---

**Cross-reference:**
- v0.7 candidate report: [`docs/CODEX_REPORT.md`](docs/CODEX_REPORT.md) — flagged Phase D gap explicitly
- v0.7.1 brief: [`CODEX_BRIEF_V7_1.md`](CODEX_BRIEF_V7_1.md) — runner-side multi-turn loop (the gate this brief depends on)
- v0.7.2 commit: `76f8b30` — verifier_trace + conversation + sandbox-log-dir forensics
- Upstream Hermes verifier runtime: `vendor/HermesAgent-20/verification/agent-runner.py` (wrapper) + `core.mjs` (grader)
- Today's Hermes diagnostic: 5-pattern failure analysis in conversation logs (Qwen 5/20 with keyword-match)
- Roadmap: [`ROADMAP.md`](ROADMAP.md) — v0.7.3 closes the v0.7 vision; v0.8 diagnostic tooling next; v0.9+ optional expansion
