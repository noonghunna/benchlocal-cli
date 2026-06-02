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


# --- sandbox-key forwarding -------------------------------------------------
# The hermes sandbox spawns its OWN agent that calls the model from inside the
# container, so it needs the real key forwarded (not the hardcoded "dummy", which
# only works for auth-less local vLLM). cli-40 / bugfind drive turns via the host
# _post_chat (already keyed), so only hermes (+ aider) carry the key.

class _CapturingMultiTurnSandbox:
    config = type("Cfg", (), {"multi_turn": True})()

    def __init__(self) -> None:
        self.start_kwargs: dict | None = None

    def verify_multiturn_start(self, scenario: dict, **kwargs) -> dict:
        self.start_kwargs = kwargs
        return {"action": "verify-final", "passed": True, "failure_mode": "passed", "detail": "ok"}


_HERMES_META = {"supports_sandboxed_only": True, "default_max_seconds": 60, "sampling_defaults": {"max_tokens": 16}}


def _hermes_scenario() -> dict:
    return {"id": "HA-01", "pack_id": "hermesagent-20",
            "messages": [{"role": "user", "content": "x"}],
            "verifier": {"type": "_stub", "asserts": []}}


def test_hermes_sandbox_gets_real_api_key_when_set():
    runner = Runner(endpoint="https://openrouter.ai/api/v1", model="m",
                    api_key="sk-cloud", enable_sandboxed_packs=True)
    sb = _CapturingMultiTurnSandbox()
    runner._sandbox_clients["hermesagent-20"] = sb
    runner.run_scenario(_HERMES_META, _hermes_scenario())
    assert sb.start_kwargs is not None
    assert sb.start_kwargs["model_api_key"] == "sk-cloud"  # forwarded to the container agent


def test_hermes_sandbox_uses_placeholder_key_for_local():
    runner = Runner(endpoint="http://localhost:8010", model="m",
                    enable_sandboxed_packs=True)  # no api_key
    sb = _CapturingMultiTurnSandbox()
    runner._sandbox_clients["hermesagent-20"] = sb
    runner.run_scenario(_HERMES_META, _hermes_scenario())
    assert sb.start_kwargs["model_api_key"] == "dummy"  # unchanged for auth-less local
