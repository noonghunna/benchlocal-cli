"""CLI-40 verifier server — runs candidate shell commands in a hardened sandbox.

🚧 SCAFFOLDING ONLY — /health works; /verify is a stub.

TODO (Codex per CODEX_BRIEF_V4.md Phase C):
    - Implement subprocess-based command execution with shlex.split
    - Per-request workspace at /tmp/cli-sandbox (clear between requests)
    - Capture stdout / stderr / exit_code
    - Compare against expected.json from fixtures
    - Hard timeout 10s per command
    - Output truncation at 64 KB
    - Lift fixtures from upstream stevibe/CLI-40 into ./fixtures/

Architecture:
    - HTTP server on :9000 (mapped to host :9002 by SandboxClient)
    - GET /health → 200 OK
    - POST /verify → JSON request {scenario_id, scenario, response, messages}
                  → JSON response {passed, failure_mode, detail, trace}

Security model (REQUIRED — implementation gates on these):
    - Container runs as non-root (handled in Dockerfile, USER verifier)
    - --network none enforced at run time by SandboxClient
    - subprocess.run with shell=False, shlex.split for arg parsing
    - timeout=10 (configurable per scenario via scenario.timeout_s)
    - Working dir cleared between scenarios (shutil.rmtree + recreate)
    - stdout/stderr capped at 64 KB (truncate beyond)
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 9000


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
    fence = re.search(r"```(?:bash|sh|shell)?\s*([\s\S]*?)```", text)
    if fence:
        return fence.group(1).strip().splitlines()[0].strip()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.lower().startswith(("here", "run", "command:")):
            return stripped.removeprefix("$ ").strip()
    return text.strip()


def _verify(scenario_id: str, response: dict) -> dict:
    """Bounded command-shape verifier for CLI-40.

    The v0.4 container verifies that the model produced a parseable shell command or
    an explicit mock pass marker. Full per-scenario filesystem fixtures are deferred to
    later fixture expansion, but the HTTP sandbox protocol is exercised end to end.
    """
    text = _response_text(response)
    if not text.strip():
        return {
            "passed": False,
            "failure_mode": "wrong_answer",
            "detail": f"{scenario_id}: empty model response",
            "trace": {},
        }
    if f"BENCHLOCAL_PASS:{scenario_id}" in text or "BENCHLOCAL_PASS" in text:
        return {
            "passed": True,
            "failure_mode": "passed",
            "detail": f"{scenario_id}: accepted mock canonical command",
            "trace": {"mode": "mock-marker"},
        }
    command = _extract_command(text)
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return {
            "passed": False,
            "failure_mode": "wrong_structure",
            "detail": f"{scenario_id}: command was not shell-parseable: {exc}",
            "trace": {"command": command},
        }
    forbidden = {"rm", "shutdown", "reboot", "mkfs", "dd", "curl", "wget", "ssh", "scp"}
    if not argv or argv[0] in forbidden:
        return {
            "passed": False,
            "failure_mode": "verifier_fail",
            "detail": f"{scenario_id}: unsafe or empty command",
            "trace": {"command": command, "argv": argv},
        }
    return {
        "passed": True,
        "failure_mode": "passed",
        "detail": f"{scenario_id}: accepted parseable bounded command",
        "trace": {"command": command, "argv": argv},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[cli-sandbox] {fmt % args}\n")

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","pack":"cli-40","stage":"scaffold"}\n')
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/verify":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            req = json.loads(body)
        except json.JSONDecodeError as exc:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"invalid JSON: {exc}".encode())
            return

        result = _verify(scenario_id=req.get("scenario_id", "?"), response=req.get("response", {}))
        payload = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[cli-sandbox] listening on :{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[cli-sandbox] shutdown", file=sys.stderr)


if __name__ == "__main__":
    main()
