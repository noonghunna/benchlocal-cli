"""HermesAgent-20 verifier server — v0.4 single-turn shape-check.

Validates that the model emits a non-empty agent response (text, tool
call, or canonical mock-pass marker). The full multi-turn mocked-tool
agent loop (browser, cron, memory, artifact, trace simulation with
per-scenario state machines) is queued for v0.5 — currently the
lifecycle endpoints exist but pass any non-empty response trivially.

Architecture:
    - HTTP server on :9000 (mapped to host :9003 by SandboxClient)
    - 4 endpoints (multi-turn lifecycle exposed but currently single-turn):
        GET  /health        → 200 OK with stage="v0.4-shape-check"
        POST /verify-start  → {scenario_id, scenario} → {prompt, tools, scenario_state_id}
        POST /verify-turn   → {scenario_state_id, model_response} → {action, ...}
        POST /verify-end    → {scenario_state_id} → final pass/fail
"""

from __future__ import annotations

import json
import uuid
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 9000
STATES: dict[str, dict] = {}


def _stub_response(endpoint: str, scenario_id: str = "?") -> dict:
    """Compatibility response for unrecognized state transitions."""
    return {
        "action": "verify-final",
        "passed": False,
        "failure_mode": "verifier_fail",
        "detail": f"HermesAgent sandbox {endpoint} could not verify scenario={scenario_id}",
        "trace": {},
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


def _final_result(scenario_id: str, response: dict) -> dict:
    text = _response_text(response)
    if not text.strip():
        return {
            "action": "verify-final",
            "passed": False,
            "failure_mode": "wrong_answer",
            "detail": f"{scenario_id}: empty model response",
            "trace": {},
        }
    if f"BENCHLOCAL_PASS:{scenario_id}" in text or "BENCHLOCAL_PASS" in text or "done" in text.lower():
        return {
            "action": "verify-final",
            "passed": True,
            "failure_mode": "passed",
            "detail": f"{scenario_id}: accepted canonical agent trace",
            "trace": {"mode": "mock-marker-or-done", "response_excerpt": text[:500]},
        }
    return {
        "action": "verify-final",
        "passed": True,
        "failure_mode": "passed",
        "detail": f"{scenario_id}: accepted non-empty agent response",
        "trace": {"mode": "single-turn-v0.4", "response_excerpt": text[:500]},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[hermes-sandbox] {fmt % args}\n")

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","pack":"hermesagent-20","stage":"v0.4-shape-check"}\n')
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path not in ("/verify", "/verify-start", "/verify-turn", "/verify-end"):
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
        if self.path == "/verify":
            result = _final_result(scenario_id, req.get("response", {}))
        elif self.path == "/verify-start":
            state_id = str(uuid.uuid4())
            STATES[state_id] = {"scenario_id": scenario_id, "turns": []}
            result = {
                "action": "next-prompt",
                "scenario_state_id": state_id,
                "prompt": req.get("scenario", {}).get("messages", []),
                "tools": [],
            }
        elif self.path == "/verify-turn":
            state_id = req.get("scenario_state_id", "")
            state = STATES.get(state_id)
            if state is None:
                result = _stub_response(self.path, scenario_id="?")
            else:
                state["turns"].append(req.get("model_response", {}))
                result = _final_result(state["scenario_id"], req.get("model_response", {}))
        elif self.path == "/verify-end":
            state_id = req.get("scenario_state_id", "")
            scenario_id = STATES.get(state_id, {}).get("scenario_id", "?")
            result = {
                "action": "verify-final",
                "passed": False,
                "failure_mode": "timeout",
                "detail": f"{scenario_id}: agent loop ended before success",
                "trace": {"turns": len(STATES.get(state_id, {}).get("turns", []))},
            }
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
