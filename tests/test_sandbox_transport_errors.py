"""A sandbox verifier call that never returns a verdict costs one scenario, not the run.

Before this, `SandboxClient` transport failures (container gone, exec transport
failed, HTTP read timeout, non-JSON reply) propagated out of `run_scenario`,
`run_pack` and `run`, and the CLI exited 1 without writing `--save-json`, so a
single verifier hiccup discarded every scored scenario in the run.
"""

from __future__ import annotations

import httpx
import pytest

from benchlocal_cli.runner import Runner
from benchlocal_cli.sandbox import SandboxClient, SandboxTransportError, config_for_pack
from benchlocal_cli.types import ScenarioResult

_META = {
    "supports_sandboxed_only": True,
    "default_max_seconds": 60,
    "sampling_defaults": {"max_tokens": 16},
}


class FlakySandbox:
    """Single-turn verifier whose first `fail_first` calls never answer."""

    def __init__(self, fail_first: int = 1) -> None:
        self.fail_first = fail_first
        self.calls = 0

    def verify(self, scenario: dict, response: dict, messages: list[dict]) -> ScenarioResult:
        self.calls += 1
        if self.calls <= self.fail_first:
            raise SandboxTransportError("sandbox bugfind-15 /verify failed: ReadTimeout: timed out")
        return ScenarioResult(scenario["id"], True, "passed", "fake sandbox pass")


class _MultiTurnConfig:
    multi_turn = True


class BrokenTurnSandbox:
    """Multi-turn verifier that starts fine, then loses the container mid-episode."""

    def __init__(self, fail_on: str = "turn") -> None:
        self.config = _MultiTurnConfig()
        self.fail_on = fail_on
        self.end_calls = 0

    def verify_multiturn_start(self, scenario: dict, **_kwargs) -> dict:
        if self.fail_on == "start":
            raise SandboxTransportError("sandbox cli-40 exec transport failed on /verify-start (exit 1)")
        return {"scenario_state_id": "state-1", "prompt": scenario["messages"], "tools": []}

    def verify_multiturn_turn(self, scenario_state_id: str, model_response: dict) -> dict:
        raise SandboxTransportError("sandbox cli-40 exec transport failed on /verify-turn (exit 137)")

    def verify_multiturn_end(self, scenario_state_id: str) -> dict:
        self.end_calls += 1
        raise SandboxTransportError("container is gone")


class _OneReplyClient:
    """httpx.Client stand-in for the model endpoint: always one assistant reply."""

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def __enter__(self) -> _OneReplyClient:
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def post(self, url: str, json: dict, **_kwargs):
        class _Response:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"choices": [{"message": {"role": "assistant", "content": "ls"}}], "usage": {"completion_tokens": 1}}

        return _Response()


def _runner(**kwargs) -> Runner:
    return Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True, **kwargs)


def test_single_turn_transport_error_scores_the_scenario_as_server_error():
    runner = _runner(mock_responses={"BF-01": {"choices": [{"message": {"content": "fixed"}}]}})
    runner._sandbox_clients["bugfind-15"] = FlakySandbox(fail_first=1)
    scenario = {"id": "BF-01", "pack_id": "bugfind-15", "messages": [{"role": "user", "content": "fix it"}]}

    run = runner.run_scenario(_META, scenario)

    assert run.result.passed is False
    assert run.result.failure_mode == "server_error"
    assert "no verdict" in run.result.detail
    assert "ReadTimeout" in run.result.detail


@pytest.mark.parametrize("fail_on", ["start", "turn"])
def test_multiturn_transport_error_scores_the_scenario_as_server_error(monkeypatch, fail_on):
    import benchlocal_cli.runner as runner_module

    monkeypatch.setattr(runner_module.httpx, "Client", _OneReplyClient)
    runner = _runner()
    fake = BrokenTurnSandbox(fail_on=fail_on)
    runner._sandbox_clients["cli-40"] = fake
    scenario = {
        "id": "CLI-36",
        "pack_id": "cli-40",
        "messages": [{"role": "user", "content": "use jq"}],
        "raw_scenario": {"kind": "multiround"},
    }

    run = runner.run_scenario({**_META, "default_max_seconds": 600}, scenario)

    assert run.result.passed is False
    assert run.result.failure_mode == "server_error"
    assert "no verdict" in run.result.detail
    # A started episode is still ended (best effort); one that never started is not.
    assert fake.end_calls == (1 if fail_on == "turn" else 0)


def test_run_pack_keeps_going_past_a_transport_error():
    """The failed scenario is kept as an attempt-1 infra failure, its inline
    infra retry gets a real verdict, and the next scenario still runs."""
    mock = {sid: {"choices": [{"message": {"content": "fixed"}}]} for sid in ("BF-01", "BF-02")}
    runner = _runner(mock_responses=mock)
    fake = FlakySandbox(fail_first=1)
    runner._sandbox_clients["bugfind-15"] = fake

    pack = runner.run_pack("bugfind-15", scenario_ids=["BF-01", "BF-02"])

    assert [run.id for run in pack.scenarios] == ["BF-01", "BF-02"]
    first, second = pack.scenarios
    assert first.result.failure_mode == "server_error"  # attempt 1 stays authoritative
    assert first.label == "pass@2"
    assert second.result.passed is True
    assert fake.calls == 3


class _DeadContainerClient:
    """Stands in for SandboxClient: starts fine, then never answers a verify."""

    def __init__(self, config, model_endpoint=None) -> None:
        self.config = config

    def start(self, run_dir=None) -> None:
        return None

    def stop(self, log_dir=None) -> None:
        return None

    def verify(self, scenario: dict, response: dict, messages: list[dict]) -> ScenarioResult:
        raise SandboxTransportError("sandbox bugfind-15 /verify failed: ConnectError: connection refused")


def test_cli_run_saves_its_json_when_the_verifier_container_dies(tmp_path, monkeypatch, capsys):
    """End to end through `main`: the run used to exit 1 with no JSON."""
    import json

    from benchlocal_cli.cli import main

    monkeypatch.setattr("benchlocal_cli.runner.SandboxClient", _DeadContainerClient)
    mock = tmp_path / "mock.json"
    mock.write_text(json.dumps({sid: {"choices": [{"message": {"content": "fixed"}}]} for sid in ("BF-01", "BF-02")}))
    out = tmp_path / "run.json"

    rc = main([
        "run", "--endpoint", "http://mock", "--model", "mock",
        "--scenario", "bugfind-15/BF-01", "--scenario", "bugfind-15/BF-02",
        "--measured-tps", "100", "--mock-responses-from-json", str(mock), "--save-json", str(out),
    ])

    assert rc == 0, capsys.readouterr().err
    saved = json.loads(out.read_text())
    (pack,) = [p for p in saved["packs"] if p["pack_id"] == "bugfind-15"]
    assert [row["id"] for row in pack["scenarios"]] == ["BF-01", "BF-02"]
    assert {row["failure_mode"] for row in pack["scenarios"]} == {"server_error"}


def test_post_wraps_http_transport_failures(monkeypatch):
    """Open (published-port) packs: an httpx failure becomes SandboxTransportError,
    which is still a RuntimeError for existing callers."""
    client = SandboxClient(config_for_pack("bugfind-15"))

    def _timeout(*_args, **_kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr("benchlocal_cli.sandbox.httpx.post", _timeout)

    with pytest.raises(SandboxTransportError) as caught:
        client._post("/verify", {"scenario_id": "BF-01"})

    assert isinstance(caught.value, RuntimeError)
    assert "ReadTimeout" in str(caught.value)


def test_post_wraps_a_non_object_reply(monkeypatch):
    client = SandboxClient(config_for_pack("bugfind-15"))
    request = httpx.Request("POST", "http://127.0.0.1:9001/verify")
    monkeypatch.setattr(
        "benchlocal_cli.sandbox.httpx.post",
        lambda *_a, **_k: httpx.Response(200, json=["not", "an", "object"], request=request),
    )

    with pytest.raises(SandboxTransportError, match="non-object JSON"):
        client._post("/verify", {})
