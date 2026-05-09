"""BugFind-15 verifier server — runs candidate code fixes through pytest.

🚧 SCAFFOLDING ONLY — /health endpoint works for SandboxClient detection;
/verify is a STUB that returns verifier_not_implemented.

TODO (Codex per CODEX_BRIEF_V4.md Phase B):
    - Replace _stub_verify() with real implementation
    - Lift fixtures from upstream stevibe/BugFind-15 into ./fixtures/
    - Update tools/build-packs.js to extract scenario.code into raw_scenario

Architecture:
    - HTTP server on :9000 (inside container; mapped to host :9001 by SandboxClient)
    - GET /health → 200 OK (used by SandboxClient.start() to detect ready state)
    - POST /verify → JSON request {scenario_id, scenario, response, messages}
                  → JSON response {passed, failure_mode, detail, trace}

Verification flow per scenario (when implemented):
    1. Extract candidate fix from response.choices[0].message.content
    2. Locate fixture dir: /app/fixtures/<scenario_id>/
    3. Apply candidate fix to a tmp copy of buggy.py
    4. Run pytest against test_fix.py with --timeout=10
    5. Pass if pytest exits 0; fail otherwise
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 9000


def _stub_verify(scenario_id: str) -> dict:
    """Stub — returns verifier_not_implemented. Codex replaces with pytest harness."""
    return {
        "passed": False,
        "failure_mode": "verifier_not_implemented",
        "detail": (
            f"BugFind sandbox /verify not implemented (scenario={scenario_id}). "
            "See sandboxes/bugfind/server.py module docstring + CODEX_BRIEF_V4.md Phase B."
        ),
        "trace": {},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # Quieter logs; default BaseHTTPRequestHandler logs to stderr per request
        sys.stderr.write(f"[bugfind-sandbox] {fmt % args}\n")

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","pack":"bugfind-15","stage":"scaffold"}\n')
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
    print(f"[bugfind-sandbox] listening on :{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[bugfind-sandbox] shutdown", file=sys.stderr)


if __name__ == "__main__":
    main()
