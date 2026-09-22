"""#157: streamed chat completions with a liveness clock.

A non-streaming request is one blocking read: while it is open, "generating
normally" and "hung" look the same until the whole budget expires. Streaming
turns every token into a liveness signal, so a stall can be detected by the
gap since the last event instead of by the total wall clock.

`post_chat_streaming` sends the request with `stream: true`, reassembles the
server-sent events into exactly the shape of a non-streaming response (so
scoring, truncation relabelling, the #152 engine-rejection classifier and
usage accounting all read it unchanged), and enforces two clocks:

- **first-token allowance** — the httpx read timeout. It covers queueing and
  prefill, during which a server legitimately sends nothing. It is captured
  once per response body by httpcore and cannot be tightened mid-stream, so it
  is only the allowance, never the stall detector.
- **stall gap** — enforced on our own clock, from the moment the first data
  event arrives: no `data:` event for `stall_timeout` seconds raises
  `StreamStall`. SSE comment lines (llama.cpp's `:` keep-alive pings) do NOT
  reset it — a server that keeps pinging while producing nothing is exactly
  the case this exists to catch.

The request's total budget stays as a backstop, with the same meaning it has
without streaming: exceeding it raises `httpx.ReadTimeout`.
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx


class StreamStall(Exception):
    """No data event for longer than the stall timeout, after the first one."""

    def __init__(self, gap_s: float, stall_timeout: float, stats: dict) -> None:
        super().__init__(
            f"stream stalled: no data for {gap_s:.1f}s (limit {stall_timeout:.0f}s) "
            f"after {stats.get('chunks', 0)} chunks"
        )
        self.gap_s = gap_s
        self.stats = stats


class StreamedResponse:
    """The minimal httpx.Response surface `Runner._post_chat` reads."""

    def __init__(self, status_code: int, payload: Any, text: str, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("response body is not JSON")
        return self._payload


class StreamAccumulator:
    """Folds OpenAI chat-completion chunks back into one non-streaming response."""

    _TOP_LEVEL_FIRST_SEEN = ("id", "created", "model", "system_fingerprint")

    def __init__(self) -> None:
        self._top: dict[str, Any] = {}
        self._extra: dict[str, Any] = {}
        self._choices: dict[int, dict[str, Any]] = {}
        self._usage: Any = None

    def add(self, chunk: Any) -> None:
        if not isinstance(chunk, dict):
            return
        for key in self._TOP_LEVEL_FIRST_SEEN:
            if chunk.get(key) is not None:
                self._top.setdefault(key, chunk[key])
        if chunk.get("usage") is not None:
            self._usage = chunk["usage"]
        for key, value in chunk.items():
            # e.g. llama.cpp `timings`: last value wins, like the final response.
            if key not in ("choices", "usage", "object", *self._TOP_LEVEL_FIRST_SEEN) and value is not None:
                self._extra[key] = value
        for position, choice in enumerate(chunk.get("choices") or []):
            if not isinstance(choice, dict):
                continue
            index = choice.get("index")
            index = index if isinstance(index, int) else position
            state = self._choices.setdefault(
                index, {"role": None, "fields": {}, "tool_calls": {}, "finish_reason": None}
            )
            delta = choice.get("delta") or {}
            if isinstance(delta, dict):
                self._add_delta(state, delta)
            if choice.get("finish_reason") is not None:
                state["finish_reason"] = choice["finish_reason"]

    @staticmethod
    def _add_delta(state: dict, delta: dict) -> None:
        for key, value in delta.items():
            if key == "role":
                if value:
                    state["role"] = value
            elif key == "tool_calls":
                StreamAccumulator._add_tool_calls(state["tool_calls"], value)
            elif value is None:
                continue
            elif isinstance(value, str):
                # content / reasoning_content / reasoning / refusal — whichever
                # the server uses, generically.
                state["fields"][key] = state["fields"].get(key, "") + value
            else:
                state["fields"][key] = value

    @staticmethod
    def _add_tool_calls(calls: dict[int, dict], deltas: Any) -> None:
        if not isinstance(deltas, list):
            return
        for position, delta in enumerate(deltas):
            if not isinstance(delta, dict):
                continue
            index = delta.get("index")
            index = index if isinstance(index, int) else position
            call = calls.setdefault(
                index, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}}
            )
            if delta.get("id") and not call["id"]:
                call["id"] = delta["id"]
            if delta.get("type"):
                call["type"] = delta["type"]
            function = delta.get("function") or {}
            if isinstance(function, dict):
                if function.get("name") and not call["function"]["name"]:
                    call["function"]["name"] = function["name"]
                if isinstance(function.get("arguments"), str):
                    call["function"]["arguments"] += function["arguments"]

    def result(self) -> dict:
        out: dict[str, Any] = dict(self._top)
        out["object"] = "chat.completion"
        choices = []
        for index in sorted(self._choices):
            state = self._choices[index]
            message: dict[str, Any] = {"role": state["role"] or "assistant"}
            message.update(state["fields"])
            message.setdefault("content", None)
            if state["tool_calls"]:
                message["tool_calls"] = [state["tool_calls"][i] for i in sorted(state["tool_calls"])]
            choices.append({"index": index, "message": message, "finish_reason": state["finish_reason"]})
        out["choices"] = choices
        if self._usage is not None:
            out["usage"] = self._usage
        for key, value in self._extra.items():
            out.setdefault(key, value)
        return out


def _abort(holder: dict) -> None:
    """Best-effort: unblock the reader thread and tell the server to stop.

    Closing a socket from another thread does not reliably wake a blocked
    recv(); shutdown() does.
    """
    response = holder.get("response")
    if response is None:
        return
    try:
        stream = response.extensions.get("network_stream")
        sock = stream.get_extra_info("socket") if stream is not None else None
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)
    except Exception:  # best effort only
        pass


def _json_or_text(data: bytes) -> tuple[Any, str]:
    text = data.decode("utf-8", errors="replace")
    try:
        return json.loads(text), text
    except ValueError:
        return None, text


def _stats(started: float, first_at: float | None, max_gap: float, chunks: int) -> dict:
    return {
        "first_chunk_s": None if first_at is None else round(first_at - started, 3),
        "max_gap_s": round(max_gap, 3),
        "chunks": chunks,
    }


def post_chat_streaming(
    url: str,
    payload: dict,
    headers: dict | None,
    *,
    timeout: float,
    stall_timeout: float,
    client_factory: Callable[..., Any] = httpx.Client,
) -> tuple[StreamedResponse, dict]:
    """POST a streamed chat completion; return (response-like, stream stats).

    Raises `StreamStall` on a mid-stream gap, and `httpx.TimeoutException`
    when the first-token allowance or the total budget (both `timeout`) is
    exhausted — the same exception the non-streaming path raises, so it keeps
    its `timeout` classification.
    """
    body = dict(payload)
    body["stream"] = True
    options = dict(body.get("stream_options") or {})
    options.setdefault("include_usage", True)
    body["stream_options"] = options

    events: queue.Queue = queue.Queue()
    holder: dict[str, Any] = {}
    stop = threading.Event()

    def reader() -> None:
        try:
            with (
                client_factory(timeout=timeout) as client,
                client.stream("POST", url, json=body, headers=headers) as response,
            ):
                holder["response"] = response
                content_type = response.headers.get("content-type", "")
                if response.status_code != 200 or "text/event-stream" not in content_type:
                    # An error status, or a server that ignored `stream`
                    # and answered with one JSON body: hand it back whole.
                    events.put(("whole", response.status_code, response.read(), dict(response.headers)))
                    return
                events.put(("open", dict(response.headers)))
                for line in response.iter_lines():
                    if stop.is_set():
                        return
                    events.put(("line", line))
            events.put(("end",))
        except BaseException as exc:  # forwarded to the caller
            if not stop.is_set():
                events.put(("exc", exc))

    started = time.monotonic()
    threading.Thread(target=reader, name="benchlocal-stream", daemon=True).start()

    accumulator = StreamAccumulator()
    response_headers: dict = {}
    first_at: float | None = None
    last_at: float | None = None
    max_gap = 0.0
    chunks = 0

    def finish(exc: BaseException | None = None) -> None:
        stop.set()
        _abort(holder)
        if exc is not None:
            raise exc

    while True:
        now = time.monotonic()
        total_left = timeout - (now - started)
        wait = total_left if last_at is None else min(total_left, stall_timeout - (now - last_at))
        try:
            item = events.get(timeout=max(wait, 0.01))
        except queue.Empty:
            now = time.monotonic()
            if last_at is not None and now - last_at >= stall_timeout:
                stats = _stats(started, first_at, max(max_gap, now - last_at), chunks)
                finish(StreamStall(now - last_at, stall_timeout, stats))
            if now - started >= timeout:
                finish(httpx.ReadTimeout(f"stream exceeded the {timeout:.0f}s request budget"))
            continue

        kind = item[0]
        if kind == "exc":
            finish(item[1])
        if kind == "whole":
            _, status, data, whole_headers = item
            parsed, text = _json_or_text(data)
            finish()
            return StreamedResponse(status, parsed, text, whole_headers), _stats(started, first_at, max_gap, chunks)
        if kind == "open":
            response_headers = item[1]
            continue
        if kind == "end":
            break

        line = item[1]
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue  # blank separator, keep-alive comment, or `event:` line
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        now = time.monotonic()
        if first_at is None:
            first_at = now
        else:
            max_gap = max(max_gap, now - last_at)
        last_at = now
        chunks += 1
        try:
            chunk = json.loads(data)
        except ValueError:
            continue
        if isinstance(chunk, dict) and "error" in chunk and not chunk.get("choices"):
            # An error after the stream started. llama.cpp:
            #   data: {"error": {"code": 500, "message": ..., "type": "server_error"}}
            # vLLM: data: {"error": {"message", "type", "param", "code"}} then [DONE].
            # Surface it as the status it names so the caller classifies it
            # exactly like a non-streaming error body.
            error = chunk["error"]
            code = error.get("code") if isinstance(error, dict) else None
            status = code if isinstance(code, int) and 400 <= code < 600 else 500
            finish()
            return (
                StreamedResponse(status, {"error": error}, json.dumps({"error": error}), response_headers),
                _stats(started, first_at, max_gap, chunks),
            )
        accumulator.add(chunk)

    finish()
    result = accumulator.result()
    return StreamedResponse(200, result, json.dumps(result), response_headers), _stats(started, first_at, max_gap, chunks)
