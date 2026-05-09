"""HermesAgent-20 verifier server — multi-turn agent loop with mocked tools.

🚧 SCAFFOLDING ONLY — /health works; the 3 verify endpoints are stubs.

TODO (Codex per CODEX_BRIEF_V4.md Phase D):
    - Implement /verify-start → init scenario state, return first prompt + tool defs
    - Implement /verify-turn  → simulate tool from model's tool call, return next prompt
    - Implement /verify-end   → final pass/fail
    - Build the 5 mocked tools (browser, cron, memory, artifact, trace) deterministically
    - Lift fixtures from upstream stevibe/HermesAgent-20

Architecture:
    - HTTP server on :9000 (mapped to host :9003 by SandboxClient)
    - 4 endpoints (multi-turn lifecycle):
        GET  /health        → 200 OK
        POST /verify-start  → {scenario_id, scenario} → {prompt, tools, scenario_state_id}
        POST /verify-turn   → {scenario_state_id, model_response} → {action, ...}
                                where action ∈ {next-prompt | verify-final}
        POST /verify-end    → {scenario_state_id} → final pass/fail (timeout/giveup case)

Mocked tools (deterministic, Codex implements):
    - browser(url) → keyed JSON fixture lookup
    - cron(when) → fixed timestamp arithmetic on scenario reference clock
    - memory.{get,set,delete}(key, [value]) → in-process dict, scenario-scoped
    - artifact.{read,write}(name, [bytes]) → in-process bytes-store, scenario-scoped
    - trace.append(event) → append-only log, checked at end

State shape (per active scenario, keyed by scenario_state_id):
    {
        "scenario_id": "HA-01",
        "scenario": { /* full upstream scenario */ },
        "memory": {},
        "artifact": {},
        "trace": [],
        "turn_count": 0,
        "started_at": <iso8601>,
    }
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 9000


def _stub_response(endpoint: str, scenario_id: str = "?") -> dict:
    """Stub — Codex replaces with full multi-turn loop implementation."""
    return {
        "action": "verify-final",
        "passed": False,
        "failure_mode": "verifier_not_implemented",
        "detail": (
            f"HermesAgent sandbox {endpoint} not implemented (scenario={scenario_id}). "
            "See sandboxes/hermes/server.py module docstring + CODEX_BRIEF_V4.md Phase D."
        ),
        "trace": {},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[hermes-sandbox] {fmt % args}\n")

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","pack":"hermesagent-20","stage":"scaffold"}\n')
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path not in ("/verify-start", "/verify-turn", "/verify-end"):
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

        scenario_id = req.get("scenario_id") or req.get("scenario", {}).get("id", "?")
        result = _stub_response(self.path, scenario_id=scenario_id)
        payload = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[hermes-sandbox] listening on :{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[hermes-sandbox] shutdown", file=sys.stderr)


if __name__ == "__main__":
    main()
