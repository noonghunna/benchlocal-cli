"""club-3090#1396: token accounting for hermesagent-20's own model calls.

The agent calls the model from inside its sandbox, so the runner never saw a
response and the pack reported no token counts. The sandbox now routes the
agent through a loopback pass-through proxy that relays every request unchanged
and tallies the `usage` block of each response. These tests drive the proxy
against a fake upstream: bytes must relay untouched, usage must be counted once
per response, and nothing about the proxy may turn into a failed scenario.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _server():
    spec = importlib.util.spec_from_file_location("hermes_server_usage", ROOT / "sandboxes/hermes/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sse(*objs: object) -> bytes:
    out = b"".join(b"data: " + json.dumps(o).encode() + b"\n\n" for o in objs)
    return out + b"data: [DONE]\n\n"


USAGE_A = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
           "completion_tokens_details": {"reasoning_tokens": 2}}
USAGE_B = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


class _Upstream(BaseHTTPRequestHandler):
    """Fake OpenAI-compatible endpoint. The request body picks the response shape."""

    def log_message(self, *args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"data": [{"id": "fake"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/chat/completions", self.path
        shape = req.get("shape")
        if req.get("stream"):
            if shape == "cumulative":  # usage on EVERY chunk, growing
                body = _sse({"choices": [{"delta": {"content": "a"}}], "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5}},
                            {"choices": [{"delta": {"content": "b"}}], "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}})
            else:  # usage only on the final, choices-less chunk (vLLM include_usage)
                body = _sse({"choices": [{"delta": {"content": "hi"}}]}, {"choices": [], "usage": USAGE_A})
            ctype = "text/event-stream"
        else:
            body = json.dumps({"choices": [{"message": {"content": "ok"}}],
                               **({} if shape == "no-usage" else {"usage": USAGE_B})}).encode()
            ctype = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        _Upstream.last_body = body


@pytest.fixture()
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def test_relays_bytes_unchanged_and_counts_json_and_stream_usage(upstream):
    proxy = _server().UsageProxy()
    base = proxy.begin(upstream)
    assert base.startswith("http://127.0.0.1:") and base.endswith("/v1")

    plain = httpx.post(f"{base}/chat/completions", json={"stream": False})
    assert plain.status_code == 200 and plain.content == _Upstream.last_body
    streamed = httpx.post(f"{base}/chat/completions", json={"stream": True})
    assert streamed.status_code == 200 and streamed.content == _Upstream.last_body
    assert streamed.headers["content-type"].startswith("text/event-stream")

    assert proxy.end() == {"requests": 2, "requests_with_usage": 2, "prompt_tokens": 17,
                           "completion_tokens": 8, "total_tokens": 25, "reasoning_tokens": 2}


def test_cumulative_stream_usage_is_counted_once_not_summed(upstream):
    proxy = _server().UsageProxy()
    base = proxy.begin(upstream)
    httpx.post(f"{base}/chat/completions", json={"stream": True, "shape": "cumulative"})
    tally = proxy.end()
    assert (tally["completion_tokens"], tally["total_tokens"]) == (2, 6)  # the LAST block, not 1+2 / 5+6


def test_response_without_usage_counts_the_request_but_no_tokens(upstream):
    proxy = _server().UsageProxy()
    base = proxy.begin(upstream)
    httpx.post(f"{base}/chat/completions", json={"shape": "no-usage"})
    assert proxy.end() == {"requests": 1, "requests_with_usage": 0, "prompt_tokens": 0,
                           "completion_tokens": 0, "total_tokens": 0, "reasoning_tokens": 0}


def test_get_is_relayed_but_not_counted_and_begin_resets_the_tally(upstream):
    proxy = _server().UsageProxy()
    base = proxy.begin(upstream)
    httpx.post(f"{base}/chat/completions", json={})
    assert httpx.get(f"{base}/models").json() == {"data": [{"id": "fake"}]}
    assert proxy.end()["requests"] == 1
    proxy.begin(upstream)  # next scenario starts from zero
    assert proxy.end()["requests"] == 0


def test_dead_upstream_answers_502_and_never_raises():
    proxy = _server().UsageProxy()
    base = proxy.begin("http://127.0.0.1:9/v1")  # discard port — refuses
    resp = httpx.post(f"{base}/chat/completions", json={})
    assert resp.status_code == 502 and "usage proxy" in resp.json()["error"]["message"]
    assert proxy.end()["requests_with_usage"] == 0


def test_env_opt_out_disables_the_proxy(monkeypatch):
    server = _server()
    monkeypatch.setenv("BENCHLOCAL_HERMES_USAGE_PROXY", "0")
    assert server._usage_proxy() is None


def test_verify_start_routes_the_agent_through_the_proxy_and_attaches_usage(monkeypatch, upstream):
    server = _server()
    monkeypatch.setattr(server, "_hermes_agent_source", lambda: "baked")
    monkeypatch.setattr(server, "_upstream_node_ready", lambda: True)
    monkeypatch.setattr(server, "_detect_model_endpoint_reachable", lambda *a: {"ok": True})
    seen: dict = {}

    def fake_agent_run(scenario_id: str, upstream_request: dict) -> dict:
        base = upstream_request["model"]["inferenceBaseUrl"]
        seen["base"] = base
        httpx.post(f"{base}/chat/completions", json={"stream": True})  # the agent's own call
        return {"action": "verify-final", "passed": True, "failure_mode": "passed", "detail": "ok", "trace": {}}

    monkeypatch.setattr(server, "_run_upstream_scenario", fake_agent_run)
    result = server._verify_start_via_upstream({"scenario_id": "HA-01", "model_endpoint": upstream, "model_name": "m"})

    assert seen["base"].startswith("http://127.0.0.1:") and seen["base"] != upstream
    assert result["passed"] is True
    assert result["usage"]["completion_tokens"] == 5 and result["usage"]["requests"] == 1
