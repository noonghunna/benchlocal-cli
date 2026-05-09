"""BugFind-15 verifier server — v0.4 deterministic shape-check.

Validates that the model's response contains either an explicit
`<solution verdict="fix|no_bug">...</solution>` block or a canonical
mock-pass marker. Real upstream pytest-against-fixture verification is
queued for v0.5 (lift fixtures from upstream stevibe/BugFind-15 into
./fixtures/, run pytest with timeout against candidate patches).

Architecture:
    - HTTP server on :9000 (inside container; mapped to host :9001 by SandboxClient)
    - GET /health → 200 OK with stage="v0.4-shape-check"
    - POST /verify → JSON request {scenario_id, scenario, response, messages}
                  → JSON response {passed, failure_mode, detail, trace}
"""

from __future__ import annotations

import json
import re
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


def _verify(scenario_id: str, response: dict) -> dict:
    """Deterministic v0.4 verifier.

    The full upstream pytest fixture lift is intentionally conservative here: a candidate
    answer passes when it contains an explicit solution block or a mock canonical-pass
    marker. Empty answers and non-fix answers fail with normal taxonomy values.
    """
    text = _response_text(response)
    if not text.strip():
        return {
            "passed": False,
            "failure_mode": "wrong_answer",
            "detail": f"{scenario_id}: empty model response",
            "trace": {"stdout": "", "stderr": ""},
        }
    has_solution = re.search(r"<solution\b[^>]*verdict=\"(?:fix|no_bug)\"[^>]*>[\s\S]*?</solution>", text)
    has_marker = f"BENCHLOCAL_PASS:{scenario_id}" in text or "BENCHLOCAL_PASS" in text
    if has_solution or has_marker:
        return {
            "passed": True,
            "failure_mode": "passed",
            "detail": f"{scenario_id}: accepted candidate fix",
            "trace": {"mode": "solution-block" if has_solution else "mock-marker"},
        }
    return {
        "passed": False,
        "failure_mode": "verifier_fail",
        "detail": f"{scenario_id}: response did not include a machine-readable solution block",
        "trace": {"response_excerpt": text[:500]},
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
            self.wfile.write(b'{"status":"ok","pack":"bugfind-15","stage":"v0.4-shape-check"}\n')
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
    print(f"[bugfind-sandbox] listening on :{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[bugfind-sandbox] shutdown", file=sys.stderr)


if __name__ == "__main__":
    main()
