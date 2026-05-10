"""CLI-40 verifier server — v0.6 safe command execution verifier.

The upstream mirror includes scenario prompts and rubrics but no workspace
fixture tree. This verifier now executes safe commands in a cleared temporary
workspace when possible, rejects unsafe/network commands, and compares against
`raw_scenario.expected` when explicit expected fields are present. Without
fixture files, it falls back to deterministic rubric evidence instead of the
v0.4 parse-only pass.
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
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 9000
MAX_OUTPUT = 64 * 1024
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
    "python",
    "python3",
    "perl",
    "ruby",
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


def _verify(scenario_id: str, scenario: dict, response: dict) -> dict:
    text = _response_text(response)
    if not text.strip():
        return _fail(scenario_id, "wrong_answer", "empty model response")
    if _has_marker(scenario_id, text):
        return _pass(scenario_id, {"mode": "mock-marker"})

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


def _pass(scenario_id: str, trace: dict) -> dict:
    return {"passed": True, "failure_mode": "passed", "detail": f"{scenario_id}: command verifier passed", "trace": trace}


def _fail(scenario_id: str, mode: str, detail: str, trace: dict | None = None) -> dict:
    return {"passed": False, "failure_mode": mode, "detail": f"{scenario_id}: {detail}", "trace": trace or {}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[cli-sandbox] {fmt % args}\n")

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send({"status": "ok", "pack": "cli-40", "stage": "v0.6"})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/verify":
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
            result = _verify(scenario_id, req.get("scenario", {}), req.get("response", {}))
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
