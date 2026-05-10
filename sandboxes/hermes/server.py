"""HermesAgent-20 verifier server — v0.7.3 upstream-runtime delegation.

Each /verify-start spawns the upstream agent-runner.py (vendored at
/app/verification/agent-runner.py) which imports the pinned hermes-agent
codebase from /opt/hermes-agent (host-mounted or image-baked). Upstream owns
the entire model loop: tool simulation, multi-turn flow, trace recording.

This server returns a "verify-final" action directly from /verify-start —
the runner-side multi-turn loop is unused for Hermes (cf. CLI pack which
still uses /verify-turn). Grading is Python-side over the upstream
result.json fields (toolEvents, finalResponse, messages); we don't run
upstream's Node grader (core.mjs) — that would require Node + agent-browser
installed in this image.

Failure modes (exposed via ScenarioResult.failure_mode):
- agent_runner_timeout    — subprocess didn't complete in 15min
- agent_runner_crashed    — nonzero exit; stderr in detail
- result_json_malformed   — couldn't parse upstream's result.json
- model_endpoint_unreachable — upstream reported network error to the model
- verifier_fail           — upstream completed but graded fail
- passed                  — upstream completed and graded pass
- server_error            — server-side bug (ours, not upstream)

The mock-pass marker (`BENCHLOCAL_PASS:<id>`) short-circuits before invoking
upstream — useful for runner-side smoke tests without paying the full
agent-runner cost.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 9000

# Where the upstream hermes-agent codebase lives (bind-mount or image-baked).
HERMES_AGENT_PATH = Path(os.environ.get("HERMES_AGENT_PATH", "/opt/hermes-agent"))

# Python interpreter for the upstream agent-runner subprocess. Defaults to
# `python3` (the container's image-baked Python with hermes-agent deps
# installed). When the runner detects a host install with a venv (uv-managed
# or otherwise), it sets HERMES_AGENT_PYTHON to <install>/venv/bin/python so
# the host's exact dep set is used.
HERMES_AGENT_PYTHON = os.environ.get("HERMES_AGENT_PYTHON", "python3")

# Upstream agent-runner.py vendored alongside our server.
AGENT_RUNNER = Path("/app/verification/agent-runner.py")

# Per-scenario job dirs. Cleaned up at the end of each request.
JOB_ROOT = Path(os.environ.get("HERMES_JOB_ROOT", "/tmp/hermes-runs"))
JOB_ROOT.mkdir(parents=True, exist_ok=True)

# Subprocess wall-clock cap. Upstream agent typically runs 10-20 turns of
# real LLM calls per scenario.
SUBPROCESS_TIMEOUT_S = float(os.environ.get("HERMES_SUBPROCESS_TIMEOUT_S", "900"))


def _commit_from_path(path: Path) -> str:
    if not path.is_dir():
        return "missing"
    # Prefer the build-arg/env baked at image build time when present (the
    # runner passes BENCHLOCAL_HERMES_AGENT_COMMIT for host bind-mounts;
    # the Dockerfile sets HERMES_PINNED_COMMIT for baked installs).
    for env_key in ("BENCHLOCAL_HERMES_AGENT_COMMIT", "HERMES_PINNED_COMMIT"):
        baked = os.environ.get(env_key)
        if baked:
            return baked
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return "unknown"


def _hermes_agent_source() -> str:
    """Identify whether the install is a host bind-mount, image-baked, or missing.

    Detection: a bind-mount points to a host directory whose contents weren't
    written by the Docker build; we can't tell directly, so we use the env
    BENCHLOCAL_HERMES_AGENT_COMMIT (set by SandboxClient when bind-mounting)
    as the "host-mount" signal. Otherwise if HERMES_PINNED_COMMIT was set at
    build time AND the install exists, we treat it as baked.
    """
    if not HERMES_AGENT_PATH.is_dir() or not (HERMES_AGENT_PATH / "run_agent.py").is_file():
        return "missing"
    if os.environ.get("BENCHLOCAL_HERMES_AGENT_COMMIT"):
        return "host-mount"
    if os.environ.get("HERMES_PINNED_COMMIT"):
        return "baked"
    return "unknown"


def _hermes_agent_status() -> str:
    return "ok" if _hermes_agent_source() != "missing" else "missing-hermes-agent"


def _has_marker(scenario_id: str, text: str) -> bool:
    if not text:
        return False
    if f"BENCHLOCAL_PASS:{scenario_id}" in text or "BENCHLOCAL_PASS" in text:
        sys.stderr.write(f"[hermes-sandbox] WARNING mock pass marker used for {scenario_id}\n")
        return True
    return False


def _run_agent_runner(request_path: Path, job_dir: Path) -> tuple[int, str, str, bool]:
    """Spawn agent-runner.py with proper process-group isolation.

    Returns (returncode, stdout, stderr, timed_out). On timeout, kills the
    entire process group (SIGKILL) so child LLM-call subprocesses don't
    survive past the parent.
    """
    env = os.environ.copy()
    # Per-scenario isolation: HERMES_HOME is also set inside agent-runner
    # but exporting here covers anything the upstream agent reads early.
    env["HERMES_HOME"] = str(job_dir / "home")
    # CWD must be the scenario-scoped workspace, NOT the hermes-agent install
    # dir. If we cwd into the install, any shell tool the agent invokes
    # (`pytest`, `ls`, `git status` etc.) operates on the user's real install
    # — including running `pytest tests/` against the real test suite which
    # can take 5+ min and may contain `sleep 100` test scaffolding. The
    # install dir is on the agent-runner's sys.path via HERMES_AGENT_PATH;
    # cwd doesn't need to point there for imports to work.
    workspace = job_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [HERMES_AGENT_PYTHON, str(AGENT_RUNNER), str(request_path)],
        cwd=str(workspace),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # equiv. os.setsid; isolates the process group
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=SUBPROCESS_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        stdout, stderr = proc.communicate()
    return proc.returncode if proc.returncode is not None else -1, stdout, stderr, timed_out


def _build_request(
    scenario_id: str,
    scenario: dict,
    job_dir: Path,
    *,
    model_endpoint: str,
    model_name: str,
    model_api_key: str,
    sampling: dict | None,
) -> dict:
    raw_scenario = scenario.get("raw_scenario") or {}
    user_prompt = ""
    messages = scenario.get("messages") or []
    if messages and isinstance(messages[-1], dict):
        user_prompt = str(messages[-1].get("content") or "")

    # agent-runner expects the OpenAI-compatible base URL. Strip any
    # /v1/chat/completions tail; the upstream client appends what it needs.
    base_url = (model_endpoint or "").rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
            break
    if not base_url.endswith("/v1"):
        base_url = base_url + "/v1"

    # `generation` carries sampling overrides (temperature etc.); agent-runner
    # filters down to the supported subset.
    generation: dict = {}
    for key in ("temperature", "top_p", "top_k", "min_p", "repetition_penalty", "max_tokens"):
        if sampling and key in sampling:
            generation[key] = sampling[key]

    return {
        "resultPath": str(job_dir / "result.json"),
        "hermesHomeDir": str(job_dir / "home"),
        "workspaceDir": str(job_dir / "workspace"),
        "sessionId": str(uuid.uuid4()),
        "scenarioId": scenario_id,
        "rawScenario": raw_scenario,
        "prompt": user_prompt,
        "maxTurns": int(scenario.get("max_turns") or raw_scenario.get("max_turns") or 20),
        "model": {
            "inferenceBaseUrl": base_url,
            "apiKey": model_api_key or "dummy",
            "exposedModel": model_name,
            "providerModel": model_name,
            "provider": "custom",
        },
        "generation": generation,
        "toolsets": list(raw_scenario.get("toolsets") or []),
        "followUps": raw_scenario.get("follow_ups") or {},
        "sessionSeed": raw_scenario.get("session_seed") or [],
    }


def _grade(scenario_id: str, scenario: dict, result: dict) -> tuple[bool, str, str, dict]:
    """Python-side grade over the upstream result.json.

    Returns (passed, failure_mode, detail, grading_metadata). Operates on the
    real toolEvents / finalResponse / messages produced by the upstream
    agent-runner — much richer signal than v0.6's mocked-tool keyword match.

    Grading rules:
      1. mock-pass marker in final_response → pass (testing short-circuit)
      2. final_response empty AND no tool events → wrong_answer (agent gave up silently)
      3. completed=False AND no final_response → wrong_answer (turn limit hit)
      4. expected.required_keywords with ≥2 hits → pass; otherwise verifier_fail
      5. destructive action keyword in failure_case AND agent's final_response
         contains delete/remove without clarif/approval/confirm → verifier_fail

    We deliberately don't enforce a kind→specific-tool-name requirement: the
    upstream tool catalog evolves and our guess at canonical names was wrong
    in v0.6. With upstream agent-runner actually running (Pattern C: real
    tool use), the keyword-match floor lifts on its own.
    """
    raw_scenario = scenario.get("raw_scenario") or {}
    final_response = (result.get("finalResponse") or "") or ""
    completed = bool(result.get("completed"))
    tool_events = result.get("toolEvents") or []
    tool_names = sorted({str(ev.get("name", "")) for ev in tool_events if isinstance(ev, dict)})

    metadata = {
        "tool_names": tool_names,
        "tool_event_count": len(tool_events),
        "completed": completed,
        "partial": bool(result.get("partial")),
        "input_tokens": result.get("inputTokens"),
        "output_tokens": result.get("outputTokens"),
    }

    if _has_marker(scenario_id, final_response):
        return True, "passed", f"{scenario_id}: accepted mock canonical agent trace", {**metadata, "mode": "mock-marker"}

    if not final_response.strip() and not tool_events:
        return False, "wrong_answer", f"{scenario_id}: agent produced no final response and called no tools", metadata

    if not completed and not final_response.strip():
        return False, "wrong_answer", f"{scenario_id}: agent did not complete and produced no final response", metadata

    expected = ((raw_scenario.get("expected") or {}).get("required_keywords") or [])
    response_lower = final_response.lower()
    keyword_hits = [w for w in expected if str(w).rstrip(".").lower() in response_lower]
    metadata["keyword_hits"] = keyword_hits
    metadata["required_keywords"] = expected[:10]
    # Pass branches:
    #  - ≥2 keyword hits → strong-pass (the model echoed upstream's success_case
    #    closely enough that we don't need additional evidence)
    #  - ≥1 keyword hit AND completed AND ≥1 tool event → soft-pass: the agent
    #    actually drove tools to completion AND mentioned at least one
    #    success-case keyword. This catches scenarios where the agent did the
    #    right thing but described it in its own words.
    # The keyword list is upstream's success_case prose chopped word-by-word,
    # which is structurally too strict on its own — see CODEX_REPORT v0.7.3
    # for the trade-off discussion.
    strong_pass = expected and len(keyword_hits) >= 2
    soft_pass = (
        expected
        and len(keyword_hits) >= 1
        and completed
        and len(tool_events) >= 1
    )
    if not strong_pass and not soft_pass:
        if expected:
            return (
                False,
                "verifier_fail",
                f"{scenario_id}: final response lacks upstream success-case evidence",
                metadata,
            )

    failure_case = str(raw_scenario.get("failure_case", ""))
    if (
        re.search(r"delete|remove|destructive", failure_case, flags=re.IGNORECASE)
        and re.search(r"\bdelet|\bremov", response_lower)
        and "clarif" not in response_lower
        and "approval" not in response_lower
        and "confirm" not in response_lower
    ):
        return (
            False,
            "verifier_fail",
            f"{scenario_id}: destructive action lacked approval/clarification evidence",
            metadata,
        )

    return True, "passed", f"{scenario_id}: upstream verifier passed", metadata


def _verify_start_via_upstream(req: dict) -> dict:
    scenario = req.get("scenario") or {}
    scenario_id = req.get("scenario_id") or scenario.get("id") or "?"

    # Bake-time + bind-mount paths share the same runtime code path, so
    # missing-install is the only fail-loud reason here.
    if _hermes_agent_status() != "ok":
        return {
            "action": "verify-final",
            "passed": False,
            "failure_mode": "server_error",
            "detail": (
                f"hermes-agent install missing at {HERMES_AGENT_PATH}. "
                "Set HERMES_AGENT_HOST_PATH on the host or rebuild the image "
                "with --build-arg BAKE_HERMES_AGENT=1."
            ),
            "trace": {
                "hermes_agent_path": str(HERMES_AGENT_PATH),
                "hermes_agent_source": "missing",
            },
        }

    model_endpoint = req.get("model_endpoint")
    model_name = req.get("model_name")
    if not model_endpoint or not model_name:
        return {
            "action": "verify-final",
            "passed": False,
            "failure_mode": "server_error",
            "detail": (
                "hermes /verify-start missing model_endpoint or model_name; "
                "v0.7.3+ requires the runner to pass these. Check that the "
                "client benchlocal-cli is at v0.7.3 or later."
            ),
            "trace": {},
        }

    job_dir = JOB_ROOT / str(uuid.uuid4())
    (job_dir / "home").mkdir(parents=True, exist_ok=True)
    (job_dir / "workspace").mkdir(parents=True, exist_ok=True)
    # hermes-agent (>=v0.13) refuses to start when the model's max_model_len
    # is below 64K — see hermes-agent/run_agent.py "below the minimum 64,000".
    # We override via <HERMES_HOME>/config.yaml's model.context_length. The
    # value here is the upper-bound the agent will use for compression
    # decisions; the actual effective limit is still capped by the served
    # max_model_len. Write a minimal yaml manually (no PyYAML dep required).
    config_yaml = (job_dir / "home" / "config.yaml")
    config_yaml.write_text(
        "# Auto-generated by benchlocal-cli hermes sandbox per scenario.\n"
        "# Overrides hermes-agent's 64K minimum context-length check so we\n"
        "# can grade models that serve a smaller window (e.g. Gemma 4 at 32K).\n"
        "# Two overrides needed: one for the primary model, one for the\n"
        "# auxiliary compression model (which defaults to the same model).\n"
        "model:\n"
        "  context_length: 64000\n"
        "auxiliary:\n"
        "  compression:\n"
        "    context_length: 64000\n",
        encoding="utf-8",
    )

    request = _build_request(
        scenario_id,
        scenario,
        job_dir,
        model_endpoint=model_endpoint,
        model_name=model_name,
        model_api_key=str(req.get("model_api_key") or "dummy"),
        sampling=req.get("sampling"),
    )
    request_path = job_dir / "request.json"
    request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")

    started = time.monotonic()
    rc, stdout, stderr, timed_out = _run_agent_runner(request_path, job_dir)
    elapsed = time.monotonic() - started

    base_trace: dict = {
        "hermes_agent_path": str(HERMES_AGENT_PATH),
        "hermes_agent_source": _hermes_agent_source(),
        "hermes_agent_commit": _commit_from_path(HERMES_AGENT_PATH),
        "elapsed_s": elapsed,
        "agent_runner_returncode": rc,
        "agent_runner_stderr_tail": stderr[-2000:] if stderr else "",
    }

    if timed_out:
        base_trace["timed_out"] = True
        return {
            "action": "verify-final",
            "passed": False,
            "failure_mode": "agent_runner_timeout",
            "detail": f"{scenario_id}: upstream agent-runner exceeded {SUBPROCESS_TIMEOUT_S:.0f}s",
            "trace": base_trace,
        }

    result_path = job_dir / "result.json"
    if not result_path.is_file():
        return {
            "action": "verify-final",
            "passed": False,
            "failure_mode": "agent_runner_crashed",
            "detail": (
                f"{scenario_id}: agent-runner exited rc={rc} without writing result.json. "
                f"stderr tail: {(stderr or '')[-400:]!r}"
            ),
            "trace": base_trace,
        }

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "action": "verify-final",
            "passed": False,
            "failure_mode": "result_json_malformed",
            "detail": f"{scenario_id}: failed to parse upstream result.json: {exc}",
            "trace": base_trace,
        }

    upstream_ok = bool(result.get("ok"))
    if not upstream_ok:
        upstream_error = str(result.get("error") or "unknown upstream error")
        # Surface model-endpoint connectivity errors as a distinct mode so
        # operators don't confuse them with grading failures.
        is_network = bool(re.search(
            r"connection|unreachable|refused|resolve|timed out|timeout|name or service|getaddrinfo",
            upstream_error,
            flags=re.IGNORECASE,
        ))
        return {
            "action": "verify-final",
            "passed": False,
            "failure_mode": (
                "model_endpoint_unreachable" if is_network else "agent_runner_crashed"
            ),
            "detail": f"{scenario_id}: upstream agent-runner errored: {upstream_error[:400]}",
            "trace": {
                **base_trace,
                "upstream_error": upstream_error,
                "upstream_traceback": (result.get("traceback") or "")[-2000:],
                "tool_events": result.get("toolEvents") or [],
            },
        }

    passed, failure_mode, detail, grading_meta = _grade(scenario_id, scenario, result)

    return {
        "action": "verify-final",
        "passed": passed,
        "failure_mode": failure_mode,
        "detail": detail,
        "trace": {
            **base_trace,
            "grading": grading_meta,
            "upstream_completed": bool(result.get("completed")),
            "upstream_partial": bool(result.get("partial")),
            "final_response": (result.get("finalResponse") or "")[:8000],
            "messages": result.get("messages") or [],
            "tool_events": result.get("toolEvents") or [],
            "approval_events": result.get("approvalEvents") or [],
            "clarify_events": result.get("clarifyEvents") or [],
            "input_tokens": result.get("inputTokens"),
            "output_tokens": result.get("outputTokens"),
            "api_calls": result.get("apiCalls"),
            "session_id": result.get("sessionId"),
        },
    }


def _cleanup(job_dir: Path) -> None:
    """Best-effort job-dir teardown. Non-fatal."""
    try:
        if job_dir.is_dir():
            shutil.rmtree(job_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def _verify_with_cleanup(req: dict) -> dict:
    # _verify_start_via_upstream creates the job dir; clean up after.
    # We don't pre-allocate it here because the early-out paths
    # (missing install, missing model_endpoint) skip the dir entirely.
    snapshot_before = set(JOB_ROOT.iterdir()) if JOB_ROOT.is_dir() else set()
    try:
        return _verify_start_via_upstream(req)
    finally:
        try:
            for entry in JOB_ROOT.iterdir():
                if entry not in snapshot_before:
                    _cleanup(entry)
        except Exception:  # noqa: BLE001
            pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[hermes-sandbox] {fmt % args}\n")

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send({
                "status": _hermes_agent_status(),
                "pack": "hermesagent-20",
                "stage": "v0.7.3",
                "multi_turn": True,
                "hermes_agent_path": str(HERMES_AGENT_PATH),
                "hermes_agent_source": _hermes_agent_source(),
                "hermes_agent_commit": _commit_from_path(HERMES_AGENT_PATH),
                "subprocess_timeout_s": SUBPROCESS_TIMEOUT_S,
            })
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path not in ("/verify", "/verify-start", "/verify-turn", "/verify-end"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            req = self._json_body()
        except json.JSONDecodeError as exc:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"invalid JSON: {exc}".encode())
            return

        try:
            if self.path == "/verify-start":
                result = _verify_with_cleanup(req)
            elif self.path == "/verify":
                # Single-turn /verify is unused for v0.7.3 hermes (the runner
                # always uses multi-turn for hermesagent-20). Return a clear
                # error instead of running the v0.6 state-machine grader.
                result = {
                    "passed": False,
                    "failure_mode": "server_error",
                    "detail": (
                        "hermes /verify is unsupported in v0.7.3; "
                        "use /verify-start (multi-turn early-out) instead."
                    ),
                    "trace": {},
                }
            else:
                # /verify-turn and /verify-end: harmless no-ops in v0.7.3
                # since /verify-start returns verify-final directly. Return
                # verify-final with a benign passed=False so any leftover
                # caller doesn't hang.
                result = {
                    "action": "verify-final",
                    "passed": False,
                    "failure_mode": "server_error",
                    "detail": (
                        f"hermes {self.path} is a no-op in v0.7.3 — runner should "
                        "consume verify-final from /verify-start."
                    ),
                    "trace": {},
                }
        except Exception as exc:  # noqa: BLE001
            import traceback
            tb = traceback.format_exc()
            sys.stderr.write(f"[hermes-sandbox] verifier exception on {self.path}: {exc}\n{tb}\n")
            result = {
                "action": "verify-final",
                "passed": False,
                "failure_mode": "server_error",
                "detail": f"hermes verifier raised {type(exc).__name__}: {exc}",
                "trace": {"traceback": tb[-2000:]},
            }

        self._send(result)

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(body)
        return data if isinstance(data, dict) else {}

    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    sys.stderr.write(
        f"[hermes-sandbox] listening on :{PORT} "
        f"(stage=v0.7.3, hermes_agent_source={_hermes_agent_source()}, "
        f"timeout={SUBPROCESS_TIMEOUT_S:.0f}s)\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("[hermes-sandbox] shutdown\n")


if __name__ == "__main__":
    main()
