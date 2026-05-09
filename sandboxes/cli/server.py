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
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 9000


def _stub_verify(scenario_id: str) -> dict:
    """Stub — Codex replaces with shell-exec harness."""
    return {
        "passed": False,
        "failure_mode": "verifier_not_implemented",
        "detail": (
            f"CLI sandbox /verify not implemented (scenario={scenario_id}). "
            "See sandboxes/cli/server.py module docstring + CODEX_BRIEF_V4.md Phase C."
        ),
        "trace": {},
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

        result = _stub_verify(scenario_id=req.get("scenario_id", "?"))
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
