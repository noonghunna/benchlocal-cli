"""Cloud-endpoint Bearer auth (--api-key) + cumulative spend guard (--max-total-tokens).

Local vLLM/llama.cpp endpoints need no auth (header stays empty); cloud
OpenAI-compatible providers (OpenRouter, DashScope, …) require
`Authorization: Bearer <key>`. The spend guard trips on cumulative usage so a
cloud run can't silently overspend.
"""

from __future__ import annotations

import pytest

import benchlocal_cli.runner as runner_module
from benchlocal_cli.runner import Runner, _SpendGuardExceeded


class _Resp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._p = payload
        self.status_code = status
        self.text = ""

    def json(self) -> dict:
        return self._p


def _ok(tokens: int) -> _Resp:
    return _Resp(
        {"choices": [{"message": {"role": "assistant", "content": "ok"}}],
         "usage": {"total_tokens": tokens}}
    )


def test_api_key_builds_bearer_header():
    r = Runner(endpoint="https://openrouter.ai/api/v1", model="m", api_key="sk-abc")
    assert r._request_headers == {"Authorization": "Bearer sk-abc"}


def test_no_api_key_means_no_auth_header():
    r = Runner(endpoint="http://localhost:8010", model="m")
    assert r._request_headers == {}


def test_auth_header_is_sent_on_chat_post(monkeypatch):
    seen: dict = {}

    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def post(self, url, json, **kwargs):
            seen["headers"] = kwargs.get("headers")
            return _ok(5)

    monkeypatch.setattr(runner_module.httpx, "Client", _Client)
    r = Runner(endpoint="https://cloud/v1", model="m", api_key="sk-xyz")
    status, _body, _trace = r._post_chat({"messages": []}, 10.0)

    assert status == 200
    assert seen["headers"] == {"Authorization": "Bearer sk-xyz"}


def test_spend_guard_trips_over_cumulative_cap(monkeypatch):
    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def post(self, url, json, **kwargs):
            return _ok(60)

    monkeypatch.setattr(runner_module.httpx, "Client", _Client)
    r = Runner(endpoint="https://cloud/v1", model="m", max_total_tokens=100)

    # First call: 60 tokens, under the 100 cap → returns normally.
    r._post_chat({"messages": []}, 10.0)
    assert r.tokens_used == 60

    # Second call: cumulative 120 > 100 → guard trips.
    with pytest.raises(_SpendGuardExceeded):
        r._post_chat({"messages": []}, 10.0)


def test_no_spend_guard_when_cap_unset(monkeypatch):
    class _Client:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def post(self, url, json, **kwargs):
            return _ok(10_000)

    monkeypatch.setattr(runner_module.httpx, "Client", _Client)
    r = Runner(endpoint="http://localhost:8010", model="m")  # max_total_tokens=None
    for _ in range(3):
        r._post_chat({"messages": []}, 10.0)
    assert r.tokens_used == 30_000  # accumulates, never trips
