from __future__ import annotations

import httpx

from benchlocal_cli.runner import Runner
from benchlocal_cli.types import ScenarioResult


class FakeHTTPResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = "text-body"

    def json(self) -> dict:
        return self.payload


class SequenceHTTPClient:
    events: list[object] = []
    calls = 0

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def __enter__(self) -> "SequenceHTTPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, json: dict) -> FakeHTTPResponse:
        cls = type(self)
        event = cls.events[cls.calls]
        cls.calls += 1
        if isinstance(event, BaseException):
            raise event
        status, payload = event
        return FakeHTTPResponse(payload, status_code=status)


class FakeSandbox:
    config = type("FakeConfig", (), {"multi_turn": False})()

    def __init__(self, *, passed: bool = True, failure_mode: str = "passed") -> None:
        self.calls = 0
        self.passed = passed
        self.failure_mode = failure_mode

    def verify(self, scenario: dict, response: dict, messages: list[dict]) -> ScenarioResult:
        self.calls += 1
        return ScenarioResult(
            scenario_id=scenario["id"],
            passed=self.passed,
            failure_mode=self.failure_mode,
            detail="fake verifier",
        )


class FakeMultiTurnConfig:
    multi_turn = True


class FakeMultiTurnSandbox:
    config = FakeMultiTurnConfig()

    def verify_multiturn_start(self, scenario: dict, **kwargs) -> dict:
        return {"scenario_state_id": "state-1", "prompt": scenario["messages"], "tools": []}

    def verify_multiturn_turn(self, scenario_state_id: str, model_response: dict) -> dict:
        return {
            "action": "verify-final",
            "passed": True,
            "failure_mode": "passed",
            "detail": "multi-turn pass",
            "trace": {"turn_count": 1},
        }

    def verify_multiturn_end(self, scenario_state_id: str) -> dict:
        raise AssertionError("not expected")


def _sandbox_meta() -> dict:
    return {"supports_sandboxed_only": True, "default_max_seconds": 60, "sampling_defaults": {"max_tokens": 16}}


def _single_scenario() -> dict:
    return {
        "id": "BF-01",
        "pack_id": "bugfind-15",
        "messages": [{"role": "user", "content": "fix it"}],
        "verifier": {"type": "_stub", "asserts": []},
    }


def _multi_scenario() -> dict:
    return {
        "id": "CLI-01",
        "pack_id": "cli-40",
        "messages": [{"role": "user", "content": "run a command"}],
        "raw_scenario": {"kind": "multiround"},
        "verifier": {"type": "_stub", "asserts": []},
    }


def _ok_payload(content: str = "ok") -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}], "usage": {"completion_tokens": 3}}


def _install_sequence_client(monkeypatch, runner_module, events: list[object]) -> None:
    SequenceHTTPClient.events = events
    SequenceHTTPClient.calls = 0
    monkeypatch.setattr(runner_module.httpx, "Client", SequenceHTTPClient)
    monkeypatch.setattr(runner_module.time, "sleep", lambda delay: None)


def test_single_turn_retries_connect_error_then_passes(monkeypatch):
    import benchlocal_cli.runner as runner_module

    _install_sequence_client(monkeypatch, runner_module, [httpx.ConnectError("refused"), (200, _ok_payload())])
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True, max_transient_retries=2)
    runner._sandbox_clients["bugfind-15"] = FakeSandbox()

    run = runner.run_scenario(_sandbox_meta(), _single_scenario())

    assert run.result.passed is True
    assert SequenceHTTPClient.calls == 2
    assert run.result.verifier_trace is not None
    assert run.result.verifier_trace["transient_retries"] == 1
    assert "ConnectError" in run.result.verifier_trace["transient_errors"][0]


def test_single_turn_retries_5xx_then_passes(monkeypatch):
    import benchlocal_cli.runner as runner_module

    _install_sequence_client(monkeypatch, runner_module, [(503, {"error": "busy"}), (200, _ok_payload())])
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True, max_transient_retries=2)
    runner._sandbox_clients["bugfind-15"] = FakeSandbox()

    run = runner.run_scenario(_sandbox_meta(), _single_scenario())

    assert run.result.passed is True
    assert SequenceHTTPClient.calls == 2
    assert run.result.verifier_trace is not None
    assert run.result.verifier_trace["transient_retries"] == 1
    assert run.result.verifier_trace["transient_errors"] == ["attempt 1: HTTP 503"]


def test_single_turn_exhausted_remote_protocol_error_fails_with_trace(monkeypatch):
    import benchlocal_cli.runner as runner_module

    _install_sequence_client(
        monkeypatch,
        runner_module,
        [httpx.RemoteProtocolError("disconnect"), httpx.RemoteProtocolError("disconnect again")],
    )
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True, max_transient_retries=1)
    runner._sandbox_clients["bugfind-15"] = FakeSandbox()

    run = runner.run_scenario(_sandbox_meta(), _single_scenario())

    assert run.result.passed is False
    assert run.result.failure_mode == "http_error"
    assert SequenceHTTPClient.calls == 2
    assert run.result.verifier_trace is not None
    assert run.result.verifier_trace["transient_retries"] == 1
    assert len(run.result.verifier_trace["transient_errors"]) == 2


def test_single_turn_retries_timeout_then_passes(monkeypatch):
    import benchlocal_cli.runner as runner_module

    _install_sequence_client(monkeypatch, runner_module, [httpx.ReadTimeout("slow read"), (200, _ok_payload())])
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True, max_transient_retries=1)
    runner._sandbox_clients["bugfind-15"] = FakeSandbox()

    run = runner.run_scenario(_sandbox_meta(), _single_scenario())

    assert run.result.passed is True
    assert SequenceHTTPClient.calls == 2
    assert run.result.verifier_trace is not None
    assert run.result.verifier_trace["transient_retries"] == 1
    assert "ReadTimeout" in run.result.verifier_trace["transient_errors"][0]


def test_single_turn_4xx_is_not_retried(monkeypatch):
    import benchlocal_cli.runner as runner_module

    _install_sequence_client(monkeypatch, runner_module, [(400, {"error": "bad request"}), (200, _ok_payload())])
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True, max_transient_retries=3)
    runner._sandbox_clients["bugfind-15"] = FakeSandbox()

    run = runner.run_scenario(_sandbox_meta(), _single_scenario())

    assert run.result.passed is False
    assert run.result.failure_mode == "http_error"
    assert SequenceHTTPClient.calls == 1
    assert run.result.verifier_trace is None


def test_verifier_fail_is_not_retried(monkeypatch):
    import benchlocal_cli.runner as runner_module

    _install_sequence_client(monkeypatch, runner_module, [(200, _ok_payload())])
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True, max_transient_retries=3)
    fake = FakeSandbox(passed=False, failure_mode="verifier_fail")
    runner._sandbox_clients["bugfind-15"] = fake

    run = runner.run_scenario(_sandbox_meta(), _single_scenario())

    assert run.result.passed is False
    assert run.result.failure_mode == "verifier_fail"
    assert SequenceHTTPClient.calls == 1
    assert fake.calls == 1
    assert run.result.verifier_trace is None


def test_multiturn_retries_transient_post_and_preserves_trace(monkeypatch):
    import benchlocal_cli.runner as runner_module

    _install_sequence_client(monkeypatch, runner_module, [httpx.RemoteProtocolError("disconnect"), (200, _ok_payload())])
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True, max_transient_retries=1)
    runner._sandbox_clients["cli-40"] = FakeMultiTurnSandbox()

    run = runner.run_scenario(_sandbox_meta(), _multi_scenario())

    assert run.result.passed is True
    assert run.turn_count == 1
    assert SequenceHTTPClient.calls == 2
    assert run.result.verifier_trace is not None
    assert run.result.verifier_trace["trace"] == {"turn_count": 1}
    assert run.result.verifier_trace["transient_retries"] == 1
    assert "RemoteProtocolError" in run.result.verifier_trace["transient_errors"][0]
