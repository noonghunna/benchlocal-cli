"""#157: streamed model calls, reassembly and the stall clock, against a real local SSE server."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from benchlocal_cli.diagnostics import pack_diagnostics
from benchlocal_cli.runner import (
    _INFRA_FAILURE_MODES,
    Runner,
    _TransientPostFailure,
    classify_server_error,
)
from benchlocal_cli.streaming import StreamAccumulator, StreamStall, post_chat_streaming
from benchlocal_cli.types import RUNAWAY_FAILURE_MODES


def _chunk(delta: dict | None = None, finish: str | None = None, **top) -> dict:
    body = {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 1, "model": "fake"}
    body.update(top)
    if delta is not None or finish is not None:
        body["choices"] = [{"index": 0, "delta": delta or {}, "finish_reason": finish}]
    return body


# A realistic llama.cpp-shaped stream: role, reasoning, content, two parallel
# tool calls whose arguments arrive in pieces, a keep-alive comment, finish,
# then the include_usage chunk carrying `timings`.
NORMAL_EVENTS = [
    _chunk({"role": "assistant", "content": None}),
    _chunk({"reasoning_content": "Need two "}),
    _chunk({"reasoning_content": "calls."}),
    ":",  # keep-alive comment
    _chunk({"content": "Checking."}),
    _chunk({"tool_calls": [{"index": 0, "id": "call-a", "type": "function", "function": {"name": "bash", "arguments": ""}}]}),
    _chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{\"command\": \"ls"}}]}),
    _chunk({"tool_calls": [{"index": 1, "id": "call-b", "type": "function", "function": {"name": "bash", "arguments": "{\"command\": \"pwd\"}"}}]}),
    _chunk({"tool_calls": [{"index": 0, "function": {"arguments": " -la\"}"}}]}),
    _chunk({}, finish="tool_calls"),
    {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 1, "model": "fake", "choices": [],
     "usage": {"completion_tokens": 42, "prompt_tokens": 7, "total_tokens": 49}, "timings": {"predicted_n": 42}},
    "[DONE]",
]


class _Server:
    """Threaded HTTP server whose /v1/chat/completions behaviour is set per test."""

    def __init__(self) -> None:
        self.behaviour = None
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # quiet
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                outer.requests.append(json.loads(self.rfile.read(length) or b"{}"))
                with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
                    outer.behaviour(self)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    srv = _Server()
    yield srv
    srv.close()


def _sse_headers(handler) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.end_headers()


def _send(handler, event) -> None:
    line = ":\n\n" if event == ":" else f"data: {event if isinstance(event, str) else json.dumps(event)}\n\n"
    handler.wfile.write(line.encode())
    handler.wfile.flush()


def _stream(events, delay: float = 0.0, then_hang: float | None = None, ping_every: float | None = None):
    def behaviour(handler):
        _sse_headers(handler)
        for event in events:
            _send(handler, event)
            if delay:
                time.sleep(delay)
        if then_hang is not None:
            end = time.monotonic() + then_hang
            while time.monotonic() < end:
                time.sleep(ping_every or 0.2)
                if ping_every:
                    _send(handler, ":")
    return behaviour


def _json_reply(status: int, body: dict, content_type: str = "application/json"):
    def behaviour(handler):
        data = json.dumps(body).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
    return behaviour


def _runner(server, **kwargs) -> Runner:
    return Runner(endpoint=server.url, model="fake", stream=True, **kwargs)


# --- reassembly ---------------------------------------------------------------


def test_stream_reassembles_the_non_streaming_shape(server):
    server.behaviour = _stream(NORMAL_EVENTS)
    status, response, trace = _runner(server)._post_chat({"model": "fake", "messages": []}, 10.0)

    assert status == 200
    sent = server.requests[0]
    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    choice = response["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    message = choice["message"]
    assert message["role"] == "assistant"
    assert message["reasoning_content"] == "Need two calls."
    assert message["content"] == "Checking."
    assert message["tool_calls"] == [
        {"id": "call-a", "type": "function", "function": {"name": "bash", "arguments": "{\"command\": \"ls -la\"}"}},
        {"id": "call-b", "type": "function", "function": {"name": "bash", "arguments": "{\"command\": \"pwd\"}"}},
    ]
    assert response["usage"] == {"completion_tokens": 42, "prompt_tokens": 7, "total_tokens": 49}
    assert response["timings"] == {"predicted_n": 42}
    assert response["object"] == "chat.completion"
    stream = trace["stream"]
    assert stream["requests"] == 1
    assert stream["first_chunk_s"][0] is not None
    assert "transient_errors" not in trace  # no errors → only stream stats


def test_accumulator_handles_the_vllm_reasoning_field_and_no_content():
    acc = StreamAccumulator()
    acc.add(_chunk({"role": "assistant"}))
    acc.add(_chunk({"reasoning": "think"}))
    acc.add(_chunk({"reasoning": "ing"}, finish="stop"))
    message = acc.result()["choices"][0]["message"]
    assert message == {"role": "assistant", "reasoning": "thinking", "content": None}


def test_server_that_ignores_stream_is_returned_whole(server):
    body = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}]}
    server.behaviour = _json_reply(200, body)
    status, response, _trace = _runner(server)._post_chat({"model": "fake", "messages": []}, 10.0)
    assert status == 200
    assert response == body


# --- clocks -------------------------------------------------------------------


def test_slow_but_steady_stream_does_not_stall(server):
    events = [_chunk({"content": f"t{i} "}) for i in range(5)] + [_chunk({}, finish="stop"), "[DONE]"]
    server.behaviour = _stream(events, delay=0.4)
    status, response, trace = _runner(server, stream_stall_timeout=1.0)._post_chat({"model": "fake", "messages": []}, 20.0)
    assert status == 200
    assert response["choices"][0]["message"]["content"] == "t0 t1 t2 t3 t4 "
    assert 0.3 <= trace["stream"]["max_gap_s"][0] < 1.0


def test_mid_stream_hang_is_a_stall_at_the_gap_not_the_budget(server):
    # Keep-alive pings every 0.2 s must NOT reset the clock.
    server.behaviour = _stream(NORMAL_EVENTS[:3], then_hang=8.0, ping_every=0.2)
    started = time.monotonic()
    with pytest.raises(_TransientPostFailure) as info:
        _runner(server, stream_stall_timeout=1.0)._post_chat({"model": "fake", "messages": []}, 30.0, max_attempts=3)
    elapsed = time.monotonic() - started
    assert info.value.failure_mode == "stall"
    assert 0.9 <= elapsed < 3.0
    assert len(server.requests) == 1  # not retried at the transport layer
    assert info.value.trace["stream"]["max_gap_s"][0] >= 1.0
    assert "StreamStall" in info.value.trace["transient_errors"][0]


def test_first_token_allowance_governs_before_the_first_event(server):
    # Headers sent, then silence: prefill. The stall clock must not start yet —
    # this ends as `timeout` at the request budget, not `stall` at the gap.
    server.behaviour = _stream([], then_hang=8.0)
    started = time.monotonic()
    with pytest.raises(_TransientPostFailure) as info:
        _runner(server, stream_stall_timeout=0.3)._post_chat({"model": "fake", "messages": []}, 1.5, max_attempts=1)
    elapsed = time.monotonic() - started
    assert info.value.failure_mode == "timeout"
    assert 1.3 <= elapsed < 4.0


def test_no_headers_at_all_is_a_timeout(server):
    server.behaviour = lambda handler: time.sleep(8.0)
    with pytest.raises(_TransientPostFailure) as info:
        _runner(server, stream_stall_timeout=0.3)._post_chat({"model": "fake", "messages": []}, 1.0, max_attempts=1)
    assert info.value.failure_mode == "timeout"


def test_post_chat_streaming_raises_stall_directly(server):
    server.behaviour = _stream(NORMAL_EVENTS[:2], then_hang=5.0)
    with pytest.raises(StreamStall) as info:
        post_chat_streaming(f"{server.url}/v1/chat/completions", {"messages": []}, None, timeout=30.0, stall_timeout=0.5)
    assert info.value.stats["chunks"] == 2


# --- errors -------------------------------------------------------------------


def test_llamacpp_in_stream_error_keeps_the_152_classification(server):
    # llama.cpp b10920 tools/server/server-context.cpp: an error after the first
    # result is sent as `data: {"error": format_error_response(...)}`.
    error = {"code": 500, "message": "Failed to parse tool call arguments as JSON: bad", "type": "server_error"}
    server.behaviour = _stream([*NORMAL_EVENTS[:2], {"error": error}])
    status, response, _trace = _runner(server)._post_chat({"model": "fake", "messages": []}, 10.0)
    assert status == 500
    assert response == {"error": error}
    assert len(server.requests) == 1  # engine rejection is not retried (#152)
    assert classify_server_error(status, response)[0] == "model_output_unparseable"


def test_vllm_in_stream_error_uses_its_code(server):
    # vLLM 0.29 create_streaming_error_response: ErrorResponse(error=ErrorInfo(...)),
    # code defaults to 400, followed by [DONE].
    error = {"message": "bad request", "type": "BadRequestError", "param": None, "code": 400}
    server.behaviour = _stream([{"error": error}, "[DONE]"])
    status, response, _trace = _runner(server)._post_chat({"model": "fake", "messages": []}, 10.0)
    assert status == 400
    assert response == {"error": error}


def test_error_status_before_streaming_is_returned_like_today(server):
    server.behaviour = _json_reply(503, {"error": {"message": "Loading model", "code": 503}})
    status, response, trace = _runner(server)._post_chat({"model": "fake", "messages": []}, 10.0, max_attempts=1)
    assert status == 503
    assert response["error"]["message"] == "Loading model"
    assert trace["transient_errors"] == ["attempt 1: HTTP 503"]


# --- classification and plumbing ----------------------------------------------


def test_stall_is_infra_not_runaway():
    assert "stall" in _INFRA_FAILURE_MODES
    assert "stall" not in RUNAWAY_FAILURE_MODES


def test_run_scenario_records_stall_and_stream_stats(server):
    server.behaviour = _stream(NORMAL_EVENTS[:3], then_hang=6.0)
    runner = _runner(server, stream_stall_timeout=0.5)
    meta = {"default_max_seconds": 30, "sampling_defaults": {"max_tokens": 16}}
    scenario = {"id": "X-01", "pack_id": "x", "messages": [{"role": "user", "content": "hi"}],
                "verifier": {"type": "_stub", "asserts": []}}
    run = runner.run_scenario(meta, scenario)
    assert run.result.failure_mode == "stall"
    assert run.result.verifier_trace["stream"]["requests"] == 1
    diagnostics = pack_diagnostics([run])
    assert diagnostics["stream"]["requests"] == 1
    assert diagnostics["stream"]["max_gap_s"] >= 0.5


def test_run_scenario_scores_the_reassembled_response(server):
    events = [_chunk({"role": "assistant"}), _chunk({"content": "hello"}), _chunk({}, finish="stop"), "[DONE]"]
    server.behaviour = _stream(events)
    meta = {"default_max_seconds": 30, "sampling_defaults": {"max_tokens": 16}}
    scenario = {"id": "X-02", "pack_id": "x", "messages": [{"role": "user", "content": "hi"}],
                "verifier": {"type": "_stub", "asserts": []}}
    run = _runner(server).run_scenario(meta, scenario)
    assert run.raw_response["choices"][0]["message"]["content"] == "hello"
    assert run.response_field_used == "message.content"
    assert run.result.verifier_trace["stream"]["requests"] == 1


def test_pack_diagnostics_stream_rollup():
    def run(gaps, firsts):
        return {"result": {"verifier_trace": {"stream": {"requests": len(gaps), "max_gap_s": gaps, "first_chunk_s": firsts}}}}

    diagnostics = pack_diagnostics([run([0.1, 0.4], [1.0, 2.0]), run([0.2], [None])])
    assert diagnostics["stream"] == {
        "requests": 3,
        "max_gap_s": 0.4,
        "p95_gap_s": 0.4,
        "first_chunk_p50_s": 1.0,
        "first_chunk_max_s": 2.0,
    }


def test_streaming_off_sends_the_same_request_and_records_nothing(monkeypatch):
    import benchlocal_cli.runner as runner_module

    sent: list[dict] = []

    class Client:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def post(self, url, json, **_kwargs):
            sent.append(json)
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(runner_module.httpx, "Client", Client)
    status, _response, trace = Runner(endpoint="http://localhost:9", model="fake")._post_chat({"model": "fake", "messages": []}, 5.0)
    assert status == 200
    assert "stream" not in sent[0] and "stream_options" not in sent[0]
    assert trace is None
    assert pack_diagnostics([{"result": {"verifier_trace": {}}}]) is None


def test_stream_stall_timeout_must_be_positive():
    with pytest.raises(ValueError):
        Runner(endpoint="http://localhost:9", model="fake", stream=True, stream_stall_timeout=0)


# --- hermes half: the agent's own stall detection -----------------------------


_HERMES_STREAM_RUNNER_ENV = ("BENCHLOCAL_HERMES_STREAM_STALE_TIMEOUT_S", "BENCHLOCAL_HERMES_STREAM_READ_TIMEOUT_S")
_HERMES_STREAM_CONTAINER_ENV = ("HERMES_STREAM_STALE_TIMEOUT", "HERMES_STREAM_READ_TIMEOUT")


def test_hermes_stream_env_injects_nothing_by_default(monkeypatch):
    """Opt-in: with no BENCHLOCAL_HERMES_STREAM_* set, the hermes container env
    carries no HERMES_STREAM_* at all — hermes behaves exactly as on master."""
    from benchlocal_cli import sandbox as sandbox_module

    for name in (*_HERMES_STREAM_RUNNER_ENV, *_HERMES_STREAM_CONTAINER_ENV):
        monkeypatch.delenv(name, raising=False)
    assert sandbox_module.hermes_stream_env() == ()
    env_keys = [key for key, _value in sandbox_module.config_for_pack("hermesagent-20").env]
    assert not [key for key in env_keys if key.startswith("HERMES_STREAM_")]
    # Blank counts as unset.
    monkeypatch.setenv("BENCHLOCAL_HERMES_STREAM_STALE_TIMEOUT_S", "  ")
    assert sandbox_module.hermes_stream_env() == ()


@pytest.mark.parametrize(
    ("runner_env", "container_env", "value"),
    [
        ("BENCHLOCAL_HERMES_STREAM_STALE_TIMEOUT_S", "HERMES_STREAM_STALE_TIMEOUT", "45"),
        ("BENCHLOCAL_HERMES_STREAM_READ_TIMEOUT_S", "HERMES_STREAM_READ_TIMEOUT", "90.5"),
        # Equal to hermes' own default: passed verbatim, NOT nudged — the docs
        # say it is inert for local endpoints; the harness does not rewrite it.
        ("BENCHLOCAL_HERMES_STREAM_STALE_TIMEOUT_S", "HERMES_STREAM_STALE_TIMEOUT", "180"),
    ],
)
def test_hermes_stream_env_is_injected_verbatim_when_set(monkeypatch, runner_env, container_env, value):
    from benchlocal_cli import sandbox as sandbox_module

    for name in _HERMES_STREAM_RUNNER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(runner_env, value)
    env = sandbox_module.config_for_pack("hermesagent-20").env
    stream_env = [(key, val) for key, val in env if key.startswith("HERMES_STREAM_")]
    assert stream_env == [(container_env, value)]
    # Only the hermes sandbox gets it.
    assert not [key for key, _v in sandbox_module.config_for_pack("cli-40").env if key.startswith("HERMES_STREAM_")]


@pytest.mark.parametrize("value", ["abc", "-1", "0", "inf"])
def test_hermes_stream_env_rejects_bad_values(value):
    from benchlocal_cli.sandbox import hermes_stream_env

    with pytest.raises(ValueError):
        hermes_stream_env({"BENCHLOCAL_HERMES_STREAM_STALE_TIMEOUT_S": value})


def _hermes_proxy():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "sandboxes/hermes/server.py"
    spec = importlib.util.spec_from_file_location("hermes_proxy_157", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("final_answer", "note", "expected"),
    [
        # Observed live against a dead endpoint: exit 0, no tool events, the
        # failure only in the final answer — previously scored verifier_fail.
        ("API call failed after 3 retries: Connection error.", "", "model_endpoint_unreachable"),
        # Must beat the "timed out" → agent_runner_timeout (runaway) rule.
        ("API call failed after 3 retries: Request timed out.", "", "model_endpoint_unreachable"),
        ("Done. The memory has been updated.", "", "verifier_fail"),
        ("I think the API call failed after 3 retries earlier.", "", "verifier_fail"),
    ],
)
def test_hermes_proxy_classifies_the_agents_api_give_up(final_answer, note, expected):
    proxy = _hermes_proxy()
    upstream = {"status": "fail", "score": 50, "summary": "Hermes failed to replace the memory state.",
                "note": note, "output": {"finalAnswer": final_answer}}
    out = proxy._translate_upstream_result("HA-01", upstream, 1.0)
    assert out["failure_mode"] == expected
    if expected == "model_endpoint_unreachable":
        assert out["detail"].startswith("HA-01: agent gave up on the model endpoint mid-episode")
        assert final_answer in out["detail"]
    else:
        assert out["detail"] == upstream["summary"]
