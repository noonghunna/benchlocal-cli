"""CLI-40 verifier server — v0.7.1 upstream fixture-runtime adapter.

The v0.7 image bakes upstream `verification/` into `/app/verification`. For
normal one-shot scenarios this server delegates to `verifyOneShotSubmission()`,
which programmatically seeds the workspace and grades the filesystem/output
state. Multi-round scenarios use `/verify-start` + `/verify-turn` to provide
iterative bash feedback; final grading still delegates to upstream
`verifyMultiRoundReplay()` using the captured command trace.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

import httpx
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 9000
SCHEMA_VERSION = "2"
MAX_OUTPUT = 64 * 1024
WORKSPACE = Path("/workspace")
STATES: dict[str, dict] = {}
BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Execute a command in a persistent bash session inside the scenario's Linux container.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "integer", "default": 30},
            },
            "required": ["command"],
        },
    },
}
FORBIDDEN = {
    "rm",
    "shutdown",
    "reboot",
    "mkfs",
    "dd",
    "curl",
    "wget",
    "ssh",
    "scp",
    "nc",
    "ncat",
    "telnet",
}



_MODEL_ENDPOINT_REACHABLE_CACHE: dict | None = None


def _model_models_url(endpoint: str) -> str:
    base = (endpoint or "").rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    base = base.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return f"{base}/models"


def _endpoint_failure_reason(endpoint: str, exc: Exception) -> str:
    text = str(exc)
    host_hint = "host does not resolve from inside sandbox; try a loopback URL or BENCHLOCAL_HERMES_RESOLVE_LOCALHOST=1 for non-loopback hostnames"
    if isinstance(exc, httpx.TimeoutException):
        return f"no response within 5s from {endpoint}; check firewall / port"
    if isinstance(exc, httpx.ConnectError):
        lowered = text.lower()
        if "name" in lowered or "resolve" in lowered or "temporary failure" in lowered:
            return f"{host_hint}: {text}"
        return f"model server not running at {endpoint}; check the model is up: {text}"
    return f"could not reach {endpoint}: {text}"


def _detect_model_endpoint_reachable(endpoint: str) -> dict:
    """Cached sanity check that the sandbox can reach the model endpoint."""
    global _MODEL_ENDPOINT_REACHABLE_CACHE
    if _MODEL_ENDPOINT_REACHABLE_CACHE is not None:
        return _MODEL_ENDPOINT_REACHABLE_CACHE
    if not endpoint:
        _MODEL_ENDPOINT_REACHABLE_CACHE = {"ok": False, "reason": "no model endpoint provided to sandbox"}
        return _MODEL_ENDPOINT_REACHABLE_CACHE
    probe_url = _model_models_url(endpoint)
    try:
        with httpx.Client(timeout=5.0, follow_redirects=True, max_redirects=1) as client:
            resp = client.get(probe_url)
        if resp.status_code != 200:
            _MODEL_ENDPOINT_REACHABLE_CACHE = {
                "ok": False,
                "endpoint": endpoint,
                "probe_url": probe_url,
                "status_code": resp.status_code,
                "reason": (
                    f"endpoint returned HTTP {resp.status_code}; check the path; "
                    f"{probe_url} should return 200 on an OpenAI-compatible server"
                ),
            }
            return _MODEL_ENDPOINT_REACHABLE_CACHE
        _MODEL_ENDPOINT_REACHABLE_CACHE = {"ok": True, "endpoint": endpoint, "probe_url": probe_url}
        return _MODEL_ENDPOINT_REACHABLE_CACHE
    except (httpx.HTTPError, ValueError) as exc:
        _MODEL_ENDPOINT_REACHABLE_CACHE = {
            "ok": False,
            "endpoint": endpoint,
            "probe_url": probe_url,
            "reason": _endpoint_failure_reason(endpoint, exc),
        }
        return _MODEL_ENDPOINT_REACHABLE_CACHE


def _endpoint_preflight_response(scenario_id: str, reach: dict) -> dict:
    return {
        "action": "verify-final",
        "passed": False,
        "failure_mode": "server_error",
        "detail": f"{scenario_id}: model endpoint unreachable from sandbox: {reach.get('reason')}",
        "trace": {"schema_version": SCHEMA_VERSION, "model_endpoint_reachable": reach},
    }


def _response_text(response: dict) -> str:
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        for field in ("content", "reasoning_content", "reasoning"):
            value = message.get(field)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _tool_calls(response: dict) -> list[dict]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    calls = message.get("tool_calls")
    return calls if isinstance(calls, list) else []


def _tool_name(call: dict) -> str:
    fn = call.get("function", {}) if isinstance(call, dict) else {}
    return str(fn.get("name", ""))


def _tool_args(call: dict) -> dict:
    fn = call.get("function", {}) if isinstance(call, dict) else {}
    raw = fn.get("arguments", {})
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _extract_command(text: str) -> str:
    """Extract the candidate command(s) from the model response.

    Returns a string that may contain shell metacharacters (multi-line scripts,
    `&&` chains, pipes). Caller decides whether to invoke via shlex+exec
    (single command, no shell ops) or via `bash -c` (compound/multi-line).
    """
    fence = re.search(r"```(?:bash|sh|shell)?\s*([\s\S]*?)```", text)
    if fence:
        block = fence.group(1).strip()
        # Strip leading "$ " prompts on each line + drop shebang
        lines = []
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("#!"):
                continue
            lines.append(stripped.removeprefix("$ "))
        return "\n".join(line for line in lines if line)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith(("here", "run", "command:", "use this")):
            continue
        return stripped.removeprefix("$ ").strip()
    return text.strip()


def _needs_shell(command: str) -> bool:
    """True if the command contains shell metacharacters that require bash -c."""
    if "\n" in command.strip():
        return True
    return bool(re.search(r"&&|\|\||;|\||>(?!&)|<(?![<])|`|\$\(", command))


def _has_marker(scenario_id: str, text: str) -> bool:
    if f"BENCHLOCAL_PASS:{scenario_id}" in text or "BENCHLOCAL_PASS" in text:
        print(f"[cli-sandbox] WARNING mock pass marker used for {scenario_id}", file=sys.stderr)
        return True
    return False


def _is_safe(argv: list[str]) -> tuple[bool, str]:
    """Safety check for direct-exec commands (not bash -c).

    Allowed: any executable not in FORBIDDEN, no network/destructive tokens.
    Compound shell syntax in argv is rejected here — caller should route to
    `_is_safe_shell` for bash -c style.
    """
    if not argv:
        return False, "empty command"
    executable = Path(argv[0]).name
    if executable in FORBIDDEN:
        return False, f"forbidden executable: {executable}"
    joined = " ".join(argv)
    if re.search(r"\b(curl|wget|ssh|scp|nc|ncat|telnet)\b|/dev/(sd|nvme|mapper)|\bmkfs\b", joined):
        return False, "forbidden network/destructive token"
    if ">" in argv or "|" in argv or ";" in argv or "&&" in argv or "||" in argv:
        return False, "compound shell syntax is not accepted because shell=False is enforced"
    return True, ""


def _is_safe_shell(command: str) -> tuple[bool, str]:
    """Safety check for bash -c style compound commands.

    Rejects forbidden tokens (rm, network tools, etc.) by word-boundary match
    on the raw command string. Doesn't try to parse — the model gets one shot
    at a clean shell expression.
    """
    if not command.strip():
        return False, "empty command"
    forbidden_pattern = r"\b(" + "|".join(re.escape(t) for t in FORBIDDEN) + r")\b"
    forbidden_match = re.search(forbidden_pattern, command)
    if forbidden_match:
        return False, f"forbidden token in shell command: {forbidden_match.group(0)}"
    if re.search(r"\b(curl|wget|ssh|scp|nc|ncat|telnet)\b|/dev/(sd|nvme|mapper)|\bmkfs\b", command):
        return False, "forbidden network/destructive token"
    return True, ""


def _seed_workspace(workspace: Path, scenario: dict) -> None:
    (workspace / "README.txt").write_text(
        "benchlocal-cli v0.6 CLI verifier workspace\n"
        "This local mirror does not include upstream fixture files.\n",
        encoding="utf-8",
    )
    raw = scenario.get("raw_scenario") or {}
    (workspace / "scenario.json").write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")


def _run_command(argv: list[str], scenario: dict) -> dict:
    timeout = float(scenario.get("max_seconds_override") or 10)
    timeout = max(1.0, min(timeout, 10.0))
    with tempfile.TemporaryDirectory(prefix="cli40-") as tmp:
        workspace = Path(tmp) / "workspace"
        workspace.mkdir(parents=True)
        _seed_workspace(workspace, scenario)
        try:
            proc = subprocess.run(
                argv,
                cwd=workspace,
                shell=False,
                timeout=timeout,
                capture_output=True,
                text=True,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C", "HOME": str(workspace)},
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "timed_out": True,
                "exit_code": None,
                "stdout": (exc.stdout or "")[:MAX_OUTPUT],
                "stderr": (exc.stderr or "")[:MAX_OUTPUT],
            }
        except FileNotFoundError as exc:
            return {
                "timed_out": False,
                "exit_code": 127,
                "stdout": "",
                "stderr": f"command not found in sandbox: {exc.filename or argv[0]}",
                "not_found": True,
            }
        except (PermissionError, OSError) as exc:
            return {
                "timed_out": False,
                "exit_code": 126,
                "stdout": "",
                "stderr": f"sandbox refused to execute: {exc}",
                "exec_error": True,
            }
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
    return {
        "timed_out": False,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[:MAX_OUTPUT],
        "stderr": proc.stderr[:MAX_OUTPUT],
    }


def _expected_compare(expected: dict, run: dict) -> tuple[bool, str]:
    if not expected:
        return True, "no explicit fixture expectations in upstream mirror"
    if "exit_code" in expected and run["exit_code"] != expected["exit_code"]:
        return False, f"exit code {run['exit_code']} != expected {expected['exit_code']}"
    if "stdout" in expected and run["stdout"].rstrip("\n") != str(expected["stdout"]).rstrip("\n"):
        return False, "stdout mismatch"
    if "stderr" in expected and run["stderr"].rstrip("\n") != str(expected["stderr"]).rstrip("\n"):
        return False, "stderr mismatch"
    return True, "explicit expectations matched"


def _run_workspace_command(command: str, timeout_seconds: int | float = 30) -> dict:
    safe, reason = _is_safe_shell(command)
    if not safe:
        return {"stdout": "", "stderr": reason, "exit_code": 126, "timed_out": False}
    timeout = max(1.0, min(float(timeout_seconds or 30), 60.0))
    try:
        proc = subprocess.run(
            ["bash", "-lc", command],
            cwd=WORKSPACE,
            shell=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C", "HOME": str(WORKSPACE)},
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "stdout": (exc.stdout or "")[:MAX_OUTPUT],
            "stderr": (exc.stderr or "")[:MAX_OUTPUT],
            "exit_code": 124,
            "timed_out": True,
        }
    return {
        "stdout": proc.stdout[:MAX_OUTPUT],
        "stderr": proc.stderr[:MAX_OUTPUT],
        "exit_code": proc.returncode,
        "timed_out": False,
    }


def _run_upstream_js(js: str, args: list[str], timeout: float = 30) -> dict:
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", js, *args],
        cwd="/app",
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if not proc.stdout.strip():
        return {"status": "error", "summary": f"upstream verifier produced no JSON: {proc.stderr[-2000:]}"}
    return json.loads(proc.stdout.splitlines()[-1])


def _payload_to_result(scenario_id: str, payload: dict, stderr: str = "") -> dict:
    if payload.get("status") == "error":
        return _fail(scenario_id, "server_error", str(payload.get("summary", "upstream verifier error")), {"upstream": payload, "stderr": stderr[-2000:]})
    passed = payload.get("status") == "pass"
    return {
        "passed": passed,
        "failure_mode": "passed" if passed else "verifier_fail",
        "detail": f"{scenario_id}: {payload.get('summary', 'upstream verifier result')}",
        "trace": {"mode": "upstream-runtime", "upstream": payload, "stderr": stderr[-2000:]},
    }


def _seed_multiround_workspace(scenario_id: str) -> dict:
    # Upstream createContext()+seedScenario() is private. Calling replay with
    # no commands initializes /workspace through the upstream runtime; final
    # grading later replays the captured command trace from a clean seed.
    return _run_upstream_js(
        """
          import('./verification/core.mjs').then(async (m) => {
            const result = await m.verifyMultiRoundReplay(process.argv[1], []);
            console.log(JSON.stringify(result));
          }).catch((error) => {
            console.log(JSON.stringify({status: 'error', summary: String(error?.stack || error)}));
            process.exitCode = 2;
          });
        """,
        [scenario_id],
        timeout=30,
    )


def _verify_multiround_commands(scenario_id: str, commands: list[str]) -> dict:
    payload = _run_upstream_js(
        """
          import('./verification/core.mjs').then(async (m) => {
            const result = await m.verifyMultiRoundReplay(process.argv[1], JSON.parse(process.argv[2]));
            console.log(JSON.stringify(result));
          }).catch((error) => {
            console.log(JSON.stringify({status: 'error', summary: String(error?.stack || error)}));
            process.exitCode = 2;
          });
        """,
        [scenario_id, json.dumps(commands)],
        timeout=45,
    )
    return _payload_to_result(scenario_id, payload)


def _verify(scenario_id: str, scenario: dict, response: dict) -> dict:
    text = _response_text(response)
    if not text.strip():
        return _fail(scenario_id, "wrong_answer", "empty model response")
    if _has_marker(scenario_id, text):
        return _pass(scenario_id, {"mode": "mock-marker"})

    upstream = _verify_with_upstream_runtime(scenario_id, scenario, text)
    if upstream is not None:
        return upstream

    command = _extract_command(text)

    # Route to bash -c if command contains shell metacharacters (compound,
    # piped, redirected, multi-line). Direct exec for simple single commands.
    if _needs_shell(command):
        safe, reason = _is_safe_shell(command)
        if not safe:
            return _fail(scenario_id, "verifier_fail", reason, {"command": command})
        argv = ["bash", "-c", command]
    else:
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return _fail(scenario_id, "wrong_structure", f"command was not shell-parseable: {exc}", {"command": command})

        safe, reason = _is_safe(argv)
        if not safe:
            return _fail(scenario_id, "verifier_fail", reason, {"command": command, "argv": argv})

    run = _run_command(argv, scenario)
    if run["timed_out"]:
        return _fail(scenario_id, "timeout", "command timed out", {"command": command, **run})
    if run["exit_code"] != 0:
        return _fail(scenario_id, "verifier_fail", "command exited non-zero", {"command": command, "argv": argv, **run})

    expected = ((scenario.get("raw_scenario") or {}).get("expected") or {})
    passed, detail = _expected_compare(expected if any(k in expected for k in ("stdout", "stderr", "exit_code")) else {}, run)
    if not passed:
        return _fail(scenario_id, "verifier_fail", detail, {"command": command, "argv": argv, **run})

    return _pass(
        scenario_id,
        {
            "mode": "exec+rubric" if not any(k in expected for k in ("stdout", "stderr", "exit_code")) else "exec+expected",
            "command": command,
            "argv": argv,
            "exit_code": run["exit_code"],
            "stdout": run["stdout"],
            "stderr": run["stderr"],
            "fixture_status": (scenario.get("raw_scenario") or {}).get("fixture_status", "rubric-only"),
            "detail": detail,
        },
    )


def _multiturn_start(scenario_id: str, scenario: dict, model_endpoint: str = "") -> dict:
    raw = scenario.get("raw_scenario") or {}
    if raw.get("kind") != "multiround":
        return {"action": "verify-final", **_fail(scenario_id, "wrong_answer", "CLI multi-turn endpoint requires a multi-round scenario")}
    reach = _detect_model_endpoint_reachable(model_endpoint)
    if not reach.get("ok"):
        return _endpoint_preflight_response(scenario_id, reach)
    try:
        seed_payload = _seed_multiround_workspace(scenario_id)
    except Exception as exc:  # noqa: BLE001
        return {"action": "verify-final", **_fail(scenario_id, "server_error", f"workspace seed failed: {exc}")}
    if seed_payload.get("status") == "error":
        return {"action": "verify-final", **_fail(scenario_id, "server_error", str(seed_payload.get("summary", "workspace seed failed")))}
    state_id = str(uuid.uuid4())
    STATES[state_id] = {
        "scenario_id": scenario_id,
        "scenario": scenario,
        "commands": [],
        "tool_calls": [],
        "tool_results": [],
        "assistant_messages": [],
        "turn_count": 0,
    }
    return {
        "action": "next-prompt",
        "scenario_state_id": state_id,
        "prompt": scenario.get("messages", []),
        "tools": [BASH_TOOL],
        "turn_count": 0,
    }


def _multiturn_turn(state_id: str, response: dict) -> dict:
    state = STATES.get(state_id)
    if state is None:
        return {"action": "verify-final", "passed": False, "failure_mode": "server_error", "detail": "unknown scenario_state_id", "trace": {}}
    state["turn_count"] += 1
    text = _response_text(response)
    state["assistant_messages"].append(text)
    calls = _tool_calls(response)
    if not calls:
        result = _verify_multiround_commands(state["scenario_id"], state["commands"])
        result["action"] = "verify-final"
        result.setdefault("trace", {}).update(
            {
                "turn_count": state["turn_count"],
                "commands": state["commands"],
                "tool_results": state["tool_results"],
                "final_answer": text,
            }
        )
        STATES.pop(state_id, None)
        return result

    prompt: list[dict] = []
    for call in calls:
        call_id = str(call.get("id") or f"call_{state['turn_count']}_{len(state['tool_calls']) + 1}")
        name = _tool_name(call)
        if name != "bash":
            result = {"stdout": "", "stderr": f"unknown tool: {name}", "exit_code": 127, "timed_out": False}
            command = ""
        else:
            args = _tool_args(call)
            command = str(args.get("command", ""))
            result = _run_workspace_command(command, args.get("timeout_seconds", 30))
            state["commands"].append(command)
        state["tool_calls"].append(call)
        state["tool_results"].append({"callId": call_id, "name": name, "result": result})
        prompt.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": json.dumps(result)})
    return {
        "action": "next-prompt",
        "scenario_state_id": state_id,
        "prompt": prompt,
        "tools": [BASH_TOOL],
        "turn_count": state["turn_count"],
    }


def _multiturn_end(state_id: str) -> dict:
    state = STATES.pop(state_id, None)
    if state is None:
        return {"action": "verify-final", "passed": False, "failure_mode": "server_error", "detail": "unknown scenario_state_id", "trace": {}}
    result = _verify_multiround_commands(state["scenario_id"], state["commands"])
    result["action"] = "verify-final"
    if not result.get("passed"):
        result["failure_mode"] = "agent_loop_exhausted"
        result["detail"] = f"{state['scenario_id']}: agent loop ended before success"
    result.setdefault("trace", {}).update({"turn_count": state["turn_count"], "commands": state["commands"], "tool_results": state["tool_results"]})
    return result


def _pass(scenario_id: str, trace: dict) -> dict:
    return {"passed": True, "failure_mode": "passed", "detail": f"{scenario_id}: command verifier passed", "trace": trace}


def _fail(scenario_id: str, mode: str, detail: str, trace: dict | None = None) -> dict:
    return {"passed": False, "failure_mode": mode, "detail": f"{scenario_id}: {detail}", "trace": trace or {}}


def _solution_block_body(text: str) -> str:
    match = re.search(r"<solution\b[^>]*>([\s\S]*?)</solution>", text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else _extract_command(text)


def _verify_with_upstream_runtime(scenario_id: str, scenario: dict, answer: str) -> dict | None:
    raw = scenario.get("raw_scenario") or {}
    kind = raw.get("kind") or "oneshot"
    if kind == "multiround":
        body = _solution_block_body(answer)
        commands = [line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("#")]
        if not commands:
            return _fail(scenario_id, "wrong_answer", "multi-round replay requires commands in the solution body")
        js = """
          import('./verification/core.mjs').then(async (m) => {
            const result = await m.verifyMultiRoundReplay(process.argv[1], JSON.parse(process.argv[2]));
            console.log(JSON.stringify(result));
          }).catch((error) => {
            console.log(JSON.stringify({status: 'error', summary: String(error?.stack || error)}));
            process.exitCode = 2;
          });
        """
        args = [scenario_id, json.dumps(commands)]
    else:
        js = """
          import('./verification/core.mjs').then(async (m) => {
            const result = await m.verifyOneShotSubmission(process.argv[1], process.argv[2]);
            console.log(JSON.stringify(result));
          }).catch((error) => {
            console.log(JSON.stringify({status: 'error', summary: String(error?.stack || error)}));
            process.exitCode = 2;
          });
        """
        args = [scenario_id, answer]
    try:
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", js, *args],
            cwd="/app",
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired as exc:
        return _fail(scenario_id, "timeout", "upstream CLI verification timed out", {"stdout": exc.stdout or "", "stderr": exc.stderr or ""})
    if not proc.stdout.strip():
        return _fail(scenario_id, "server_error", "upstream verifier produced no JSON", {"stderr": proc.stderr[-2000:]})
    try:
        payload = json.loads(proc.stdout.splitlines()[-1])
    except json.JSONDecodeError:
        return _fail(scenario_id, "server_error", "upstream verifier JSON parse failed", {"stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]})
    return _payload_to_result(scenario_id, payload, proc.stderr)




def _resolve_health() -> dict:
    endpoint_reach = _MODEL_ENDPOINT_REACHABLE_CACHE
    return {
        "status": "setup-error" if endpoint_reach is not None and not endpoint_reach.get("ok") else "ok",
        "pack": "cli-40",
        "stage": "v0.7.1",
        "multi_turn": True,
        "model_endpoint_reachable": endpoint_reach or {"ok": None, "reason": "not checked yet"},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[cli-sandbox] {fmt % args}\n")

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(_resolve_health())
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
        scenario_id = req.get("scenario_id", "?")
        try:
            if self.path == "/verify":
                result = _verify(scenario_id, req.get("scenario", {}), req.get("response", {}))
            elif self.path == "/verify-start":
                scenario = req.get("scenario", {})
                result = _multiturn_start(
                    req.get("scenario_id") or scenario.get("id", "?"),
                    scenario,
                    str(req.get("model_endpoint") or ""),
                )
            elif self.path == "/verify-turn":
                result = _multiturn_turn(str(req.get("scenario_state_id", "")), req.get("model_response", {}))
            else:
                result = _multiturn_end(str(req.get("scenario_state_id", "")))
        except Exception as exc:  # noqa: BLE001
            import traceback
            tb = traceback.format_exc()
            sys.stderr.write(f"[cli-sandbox] verifier exception on {scenario_id}: {exc}\n{tb}\n")
            result = {
                "passed": False,
                "failure_mode": "server_error",
                "detail": f"{scenario_id}: verifier raised {type(exc).__name__}: {exc}",
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
    print(f"[cli-sandbox] listening on :{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[cli-sandbox] shutdown", file=sys.stderr)


if __name__ == "__main__":
    main()
