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

    def post(self, url: str, json: dict, **_kwargs) -> FakeHTTPResponse:
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


def test_single_turn_retries_429_then_passes(monkeypatch):
    import benchlocal_cli.runner as runner_module

    # 429 = rate-limited = transient (common cloud-provider throttle): retry, then pass.
    _install_sequence_client(monkeypatch, runner_module, [(429, {"error": "rate limited"}), (200, _ok_payload())])
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True, max_transient_retries=2)
    runner._sandbox_clients["bugfind-15"] = FakeSandbox()

    run = runner.run_scenario(_sandbox_meta(), _single_scenario())

    assert run.result.passed is True
    assert SequenceHTTPClient.calls == 2
    assert run.result.verifier_trace["transient_retries"] == 1
    assert run.result.verifier_trace["transient_errors"] == ["attempt 1: HTTP 429"]


def test_single_turn_429_exhausted_is_http_error(monkeypatch):
    import benchlocal_cli.runner as runner_module

    # Persistent 429 → retries exhaust → return the 429 → scored http_error (not a hang).
    _install_sequence_client(monkeypatch, runner_module, [(429, {}), (429, {}), (429, {})])
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True, max_transient_retries=2)
    runner._sandbox_clients["bugfind-15"] = FakeSandbox()

    run = runner.run_scenario(_sandbox_meta(), _single_scenario())

    assert run.result.passed is False
    assert run.result.failure_mode == "http_error"
    assert SequenceHTTPClient.calls == 3


def test_retry_after_header_honored_and_capped(monkeypatch):
    import benchlocal_cli.runner as runner_module

    slept: list[float] = []
    monkeypatch.setattr(runner_module.time, "sleep", lambda d: slept.append(d))

    class _RespWithHeader:
        def __init__(self, value):
            self.headers = {"retry-after": value}

    Runner._sleep_before_transient_retry(1, retry_after=Runner._retry_after_seconds(_RespWithHeader("7")))    # honored
    Runner._sleep_before_transient_retry(1, retry_after=Runner._retry_after_seconds(_RespWithHeader("999")))  # capped at 30s
    Runner._sleep_before_transient_retry(2, retry_after=Runner._retry_after_seconds(object()))                # no header → backoff 2**(2-1)
    assert slept == [7.0, 30.0, 2.0]


def test_request_delay_paces_requests(monkeypatch):
    import benchlocal_cli.runner as runner_module

    _install_sequence_client(monkeypatch, runner_module, [(200, _ok_payload()), (200, _ok_payload())])
    clock = {"t": 1000.0}
    slept: list[float] = []
    monkeypatch.setattr(runner_module.time, "monotonic", lambda: clock["t"])

    def _sleep(d):
        slept.append(d)
        clock["t"] += d

    monkeypatch.setattr(runner_module.time, "sleep", _sleep)  # overrides the helper's no-op

    runner = Runner(endpoint="http://x", model="m", request_delay=5.0, max_transient_retries=0)
    runner._post_chat({"messages": []}, 10.0)  # first call: nothing to wait for
    runner._post_chat({"messages": []}, 10.0)  # immediate 2nd call → paced to the 5s min interval
    assert slept == [5.0]


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


def test_single_turn_does_not_retry_timeout_by_default(monkeypatch):
    # #58: a timeout means the budget was genuinely exceeded; retrying just burns
    # another full budget for the same outcome. Fail fast after the FIRST timeout.
    import benchlocal_cli.runner as runner_module

    _install_sequence_client(monkeypatch, runner_module, [httpx.ReadTimeout("slow read"), (200, _ok_payload())])
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True, max_transient_retries=3)
    runner._sandbox_clients["bugfind-15"] = FakeSandbox()

    run = runner.run_scenario(_sandbox_meta(), _single_scenario())

    assert run.result.passed is False
    assert run.result.failure_mode == "timeout"
    # Exactly one attempt — the (200, ...) follow-up event is never consumed.
    assert SequenceHTTPClient.calls == 1
    assert run.result.verifier_trace is not None
    assert run.result.verifier_trace["transient_retries"] == 0
    assert "ReadTimeout" in run.result.verifier_trace["transient_errors"][0]


def test_single_turn_retries_timeout_when_opted_in(monkeypatch):
    # Opt-in regression guard: --retry-on-timeout restores the old behavior.
    import benchlocal_cli.runner as runner_module

    _install_sequence_client(monkeypatch, runner_module, [httpx.ReadTimeout("slow read"), (200, _ok_payload())])
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        enable_sandboxed_packs=True,
        max_transient_retries=1,
        retry_on_timeout=True,
    )
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


class ProbeHTTPClient:
    """Counts GET (preflight) and POST (decode probe) calls for probe tests."""

    get_calls = 0
    post_calls = 0
    get_behavior: object = None  # exception to raise, or status code to return
    post_payload: dict | None = None

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def __enter__(self) -> ProbeHTTPClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str, **_kwargs) -> FakeHTTPResponse:
        cls = type(self)
        cls.get_calls += 1
        behavior = cls.get_behavior
        if isinstance(behavior, BaseException):
            raise behavior
        return FakeHTTPResponse({}, status_code=int(behavior or 200))

    def post(self, url: str, json: dict, **_kwargs) -> FakeHTTPResponse:
        cls = type(self)
        cls.post_calls += 1
        return FakeHTTPResponse(cls.post_payload or {}, status_code=200)


def _install_probe_client(monkeypatch, *, get_behavior, post_payload=None) -> None:
    import benchlocal_cli.runner as runner_module

    ProbeHTTPClient.get_calls = 0
    ProbeHTTPClient.post_calls = 0
    ProbeHTTPClient.get_behavior = get_behavior
    ProbeHTTPClient.post_payload = post_payload
    monkeypatch.setattr(runner_module.httpx, "Client", ProbeHTTPClient)
    monkeypatch.setattr(runner_module.time, "sleep", lambda delay: None)


def test_probe_fails_fast_when_endpoint_unreachable(monkeypatch):
    # A blackholed/unreachable endpoint must NOT trigger 3 samples x retry-loop of
    # _post_chat. The preflight catches it and the probe returns None immediately.
    _install_probe_client(monkeypatch, get_behavior=httpx.ConnectError("blackholed"))
    runner = Runner(endpoint="http://localhost:9999", model="fake", max_transient_retries=3)

    measured = runner._probe_decode_tps()

    assert measured is None
    # Preflight tried /v1/models and /models, then bailed — no probe POSTs at all.
    assert ProbeHTTPClient.get_calls == 2
    assert ProbeHTTPClient.post_calls == 0


def test_probe_measures_tps_when_endpoint_reachable(monkeypatch):
    # Regression guard: a reachable endpoint still measures TPS normally.
    _install_probe_client(
        monkeypatch,
        get_behavior=200,
        post_payload={
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"completion_tokens": 200},
        },
    )
    runner = Runner(endpoint="http://localhost:9999/v1", model="fake", max_transient_retries=3)

    measured = runner._probe_decode_tps()

    assert measured is not None
    assert measured > 0
    # Preflight answered on the first probe (/v1/models), then 3 decode samples.
    assert ProbeHTTPClient.get_calls == 1
    assert ProbeHTTPClient.post_calls == 3


def test_probe_post_does_not_retry_on_dead_endpoint(monkeypatch):
    # Even if the preflight is somehow satisfied but the chat POST then stalls,
    # the probe's _post_chat(max_attempts=1) must not loop through retries.
    import benchlocal_cli.runner as runner_module

    state = {"get_calls": 0, "post_calls": 0}

    class StallAfterPreflightClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url: str, **_kwargs) -> FakeHTTPResponse:
            state["get_calls"] += 1
            return FakeHTTPResponse({}, status_code=200)

        def post(self, url: str, json: dict, **_kwargs):
            state["post_calls"] += 1
            raise httpx.ReadTimeout("stalled")

    monkeypatch.setattr(runner_module.httpx, "Client", StallAfterPreflightClient)
    monkeypatch.setattr(runner_module.time, "sleep", lambda delay: None)
    runner = Runner(endpoint="http://localhost:9999", model="fake", max_transient_retries=3)

    measured = runner._timeout_measured_tps()

    assert measured is None
    # Exactly one POST attempt despite max_transient_retries=3 (max_attempts=1).
    assert state["post_calls"] == 1
