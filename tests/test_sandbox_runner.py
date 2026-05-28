from __future__ import annotations

import pytest

from benchlocal_cli.runner import Runner
from benchlocal_cli.types import ScenarioResult


class FakeSandbox:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, dict, list[dict]]] = []

    def verify(self, scenario: dict, response: dict, messages: list[dict]) -> ScenarioResult:
        self.calls.append((scenario, response, messages))
        return ScenarioResult(
            scenario_id=scenario["id"],
            passed=True,
            failure_mode="passed",
            detail="fake sandbox pass",
        )


class FakeMultiTurnConfig:
    multi_turn = True


class FakeMultiTurnSandbox:
    def __init__(self) -> None:
        self.config = FakeMultiTurnConfig()
        self.turns = 0
        self.ended = False
        self.start_kwargs: dict = {}

    def verify_multiturn_start(self, scenario: dict, **kwargs) -> dict:
        self.start_kwargs = dict(kwargs)
        return {
            "scenario_state_id": "state-1",
            "prompt": scenario["messages"],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
                    },
                }
            ],
        }

    def verify_multiturn_turn(self, scenario_state_id: str, model_response: dict) -> dict:
        self.turns += 1
        assert scenario_state_id == "state-1"
        if self.turns == 1:
            return {
                "action": "next-prompt",
                "prompt": [{"role": "tool", "tool_call_id": "call-1", "name": "bash", "content": "{\"exit_code\":0}"}],
                "tools": [],
            }
        return {
            "action": "verify-final",
            "passed": True,
            "failure_mode": "passed",
            "detail": "multi-turn pass",
            "trace": {"turn_count": self.turns},
        }

    def verify_multiturn_end(self, scenario_state_id: str) -> dict:
        self.ended = True
        return {"action": "verify-final", "passed": False, "failure_mode": "timeout", "detail": "ended", "trace": {}}


def test_runner_dispatches_sandboxed_scenario_to_client():
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        enable_sandboxed_packs=True,
        mock_responses={"BF-01": {"choices": [{"message": {"content": "BENCHLOCAL_PASS:BF-01"}}]}},
    )
    fake = FakeSandbox()
    runner._sandbox_clients["bugfind-15"] = fake
    meta = {
        "supports_sandboxed_only": True,
        "default_max_seconds": 60,
        "sampling_defaults": {"max_tokens": 16},
    }
    scenario = {
        "id": "BF-01",
        "pack_id": "bugfind-15",
        "messages": [{"role": "user", "content": "fix it"}],
        "verifier": {"type": "_stub", "asserts": []},
    }

    run = runner.run_scenario(meta, scenario)

    assert run.result.passed is True
    assert run.result.detail == "fake sandbox pass"
    assert len(fake.calls) == 1


def test_runner_sanitizes_sandboxed_single_turn_response_before_verify():
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        enable_sandboxed_packs=True,
        mock_responses={"CLI-30": {"choices": [{"message": {"content": "</think> </think> <solution>done</solution>"}}]}},
    )
    fake = FakeSandbox()
    runner._sandbox_clients["cli-40"] = fake
    meta = {
        "supports_sandboxed_only": True,
        "default_max_seconds": 60,
        "sampling_defaults": {"max_tokens": 16},
    }
    scenario = {
        "id": "CLI-30",
        "pack_id": "cli-40",
        "messages": [{"role": "user", "content": "write solution"}],
        "verifier": {"type": "_stub", "asserts": []},
    }

    run = runner.run_scenario(meta, scenario)

    assert run.result.passed is True
    assert len(fake.calls) == 1
    forwarded = fake.calls[0][1]
    assert forwarded["choices"][0]["message"]["content"] == "<solution>done</solution>"
    assert run.raw_response["choices"][0]["message"]["content"] == "<solution>done</solution>"


def test_runner_skips_sandboxed_pack_without_flag():
    runner = Runner(endpoint="http://localhost:9999", model="fake")

    result = runner.run_pack("bugfind-15")

    assert result.skipped is True
    assert result.status == "stubbed"


class FakeHTTPResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self.payload


class FakeHTTPClient:
    calls = 0
    timeouts: list[float] = []

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self.timeouts.append(timeout)

    def __enter__(self) -> FakeHTTPClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, json: dict) -> FakeHTTPResponse:
        FakeHTTPClient.calls += 1
        if FakeHTTPClient.calls == 1:
            return FakeHTTPResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {"name": "bash", "arguments": "{\"command\":\"pwd\"}"},
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"completion_tokens": 3},
                }
            )
        return FakeHTTPResponse({"choices": [{"message": {"role": "assistant", "content": "<solution verdict=\"done\"></solution>"}}], "usage": {"completion_tokens": 5}})


def test_runner_drives_sandbox_multiturn_loop(monkeypatch):
    import benchlocal_cli.runner as runner_module

    FakeHTTPClient.calls = 0
    FakeHTTPClient.timeouts = []
    monkeypatch.setattr(runner_module.httpx, "Client", FakeHTTPClient)
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True)
    fake = FakeMultiTurnSandbox()
    runner._sandbox_clients["cli-40"] = fake
    meta = {
        "supports_sandboxed_only": True,
        "default_max_seconds": 600,
        "sampling_defaults": {"max_tokens": 16},
    }
    scenario = {
        "id": "CLI-36",
        "pack_id": "cli-40",
        "messages": [{"role": "user", "content": "use jq"}],
        "raw_scenario": {"kind": "multiround"},
        "verifier": {"type": "_stub", "asserts": []},
    }

    run = runner.run_scenario(meta, scenario)

    assert run.result.passed is True
    assert run.result.detail == "multi-turn pass"
    assert run.turn_count == 2
    assert run.result.tokens_completion == 8
    assert len(run.tool_calls) == 1
    assert fake.ended is False
    assert "model_endpoint" not in fake.start_kwargs


def test_runner_scales_timeout_budget_for_slow_measured_tps(monkeypatch):
    runner = Runner(endpoint="http://localhost:9999", model="fake", measured_tps=50)
    meta = {"timeout_per_case_default": 300, "timeout_reference_tps": 100}

    assert runner._timeout_budget_for_meta(meta) == 600


def test_runner_keeps_timeout_budget_for_fast_measured_tps_by_default():
    runner = Runner(endpoint="http://localhost:9999", model="fake", measured_tps=200)
    meta = {"timeout_per_case_default": 300, "timeout_reference_tps": 100}

    assert runner._timeout_budget_for_meta(meta) == 300


def test_runner_can_scale_timeout_budget_down_for_fast_measured_tps():
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        measured_tps=200,
        timeout_scale_down=True,
    )
    meta = {"timeout_per_case_default": 300, "timeout_reference_tps": 100}

    assert runner._timeout_budget_for_meta(meta) == 150


def test_runner_measured_tps_override_skips_timeout_probe(monkeypatch):
    runner = Runner(endpoint="http://localhost:9999", model="fake", measured_tps=50)

    def fail_probe():
        raise AssertionError("probe should not run when --measured-tps is set")

    monkeypatch.setattr(runner, "_probe_decode_tps", fail_probe)

    assert runner._timeout_budget_for_meta({"timeout_per_case_default": 300, "timeout_reference_tps": 100}) == 600


def test_runner_probes_tps_once_and_reuses_for_timeout_scaling(monkeypatch):
    runner = Runner(endpoint="http://localhost:9999", model="fake")
    calls = 0

    def fake_probe():
        nonlocal calls
        calls += 1
        return 50.0

    monkeypatch.setattr(runner, "_probe_decode_tps", fake_probe)
    meta = {"timeout_per_case_default": 300, "timeout_reference_tps": 100}

    assert runner._timeout_budget_for_meta(meta) == 600
    assert runner._timeout_budget_for_scenario(meta, {"id": "CLI-01"}) == 600
    assert calls == 1


def test_runner_pack_without_timeout_reference_uses_static_budget(monkeypatch):
    runner = Runner(endpoint="http://localhost:9999", model="fake")

    def fail_probe():
        raise AssertionError("probe should not run without timeout_reference_tps")

    monkeypatch.setattr(runner, "_probe_decode_tps", fail_probe)

    assert runner._timeout_budget_for_meta({"timeout_per_case_default": 300}) == 300


def test_runner_explicit_timeout_per_case_disables_dynamic_scaling(monkeypatch):
    runner = Runner(endpoint="http://localhost:9999", model="fake", timeout_per_case=45)

    def fail_probe():
        raise AssertionError("probe should not run with explicit timeout_per_case")

    monkeypatch.setattr(runner, "_probe_decode_tps", fail_probe)

    assert runner._timeout_budget_for_meta({"timeout_per_case_default": 300, "timeout_reference_tps": 100}) == 45


def test_probe_sends_enable_thinking_false(monkeypatch):
    runner = Runner(endpoint="http://localhost:9999", model="fake")
    requests = []

    def fake_post_chat(request, timeout):
        requests.append(request)
        return 200, {"choices": [{"message": {"content": "ok"}}], "usage": {"completion_tokens": 100}}, None

    monkeypatch.setattr(runner, "_post_chat", fake_post_chat)
    monkeypatch.setattr(
        "benchlocal_cli.runner.time.perf_counter",
        iter([0.0, 2.0, 2.0, 4.0, 4.0, 6.0]).__next__,
    )

    assert runner._probe_decode_tps() == 50.0
    assert len(requests) == 3
    assert all(req["chat_template_kwargs"] == {"enable_thinking": False} for req in requests)


def test_thinking_multiplier_applies_without_timeout_reference():
    # Regression for #54: a deterministic-class pack (no timeout_reference_tps)
    # must still get the thinking-budget multiplier when thinking is on. PR #55
    # gated the multiplier behind the reference check, making it dead code here.
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        thinking_enabled=True,
        thinking_max_tokens=16384,
    )
    meta = {
        "default_max_seconds": 60,
        "sampling_defaults": {"max_tokens": 1024},
        "default_thinking": "off",
        # deliberately NO timeout_reference_tps
    }

    budget = runner._timeout_budget_for_meta(meta)

    # 60 * (16384 / 1024) = 960, the floor before any rig-speed scaling.
    assert budget == 60 * (16384 / 1024)
    assert budget >= 960
    assert "thinking-budget-multiplier" in runner._timeout_scaling_note


def test_thinking_multiplier_composes_with_reference_speed_scale():
    # With a reference present AND thinking on, both factors must compose:
    # rig-speed scale (reference/measured) * thinking multiplier.
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        measured_tps=24,
        thinking_enabled=True,
        thinking_max_tokens=16384,
    )
    meta = {
        "timeout_per_case_default": 60,
        "timeout_reference_tps": 100,
        "sampling_defaults": {"max_tokens": 1024},
        "default_thinking": "off",
    }

    budget = runner._timeout_budget_for_meta(meta)

    assert budget == 60 * (100 / 24) * (16384 / 1024)
    assert "measured_decode_tps=24.0" in runner._timeout_scaling_note
    assert "thinking-budget-multiplier" in runner._timeout_scaling_note
    assert runner._timeout_scaling_note_emitted is True


def test_timeout_scaling_note_emits_once_to_stderr(capsys):
    runner = Runner(endpoint="http://localhost:9999", model="fake", measured_tps=50)
    meta = {"timeout_per_case_default": 300, "timeout_reference_tps": 100}

    assert runner._timeout_budget_for_meta(meta) == 600
    assert runner._timeout_budget_for_meta(meta) == 600

    captured = capsys.readouterr()
    assert captured.err.count("[runner] timeout scaling active") == 1
    assert "measured_decode_tps=50.0" in captured.err


def test_no_multiplier_when_thinking_off():
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        measured_tps=24,
        thinking_enabled=False,
        thinking_max_tokens=16384,
    )
    meta = {
        "timeout_per_case_default": 60,
        "timeout_reference_tps": 100,
        "sampling_defaults": {"max_tokens": 1024},
        "default_thinking": "on",
    }

    assert runner._timeout_budget_for_meta(meta) == 60 * (100 / 24)
    assert "thinking-budget-multiplier" not in runner._timeout_scaling_note


def test_no_multiplier_when_thinking_max_below_nominal():
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        measured_tps=24,
        thinking_enabled=True,
        thinking_max_tokens=512,
    )
    meta = {
        "timeout_per_case_default": 60,
        "timeout_reference_tps": 100,
        "sampling_defaults": {"max_tokens": 1024},
        "default_thinking": "off",
    }

    assert runner._timeout_budget_for_meta(meta) == 60 * (100 / 24)
    assert "thinking-budget-multiplier" not in runner._timeout_scaling_note


def test_runner_uses_pack_timeout_default_when_cli_timeout_unset(monkeypatch):
    import benchlocal_cli.runner as runner_module

    FakeHTTPClient.calls = 0
    FakeHTTPClient.timeouts = []
    monkeypatch.setattr(runner_module.httpx, "Client", FakeHTTPClient)
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True)
    fake = FakeMultiTurnSandbox()
    runner._sandbox_clients["cli-40"] = fake
    meta = {
        "supports_sandboxed_only": True,
        "default_max_seconds": 60,
        "timeout_per_case_default": 300,
        "sampling_defaults": {"max_tokens": 16},
    }
    scenario = {
        "id": "CLI-36",
        "pack_id": "cli-40",
        "messages": [{"role": "user", "content": "use jq"}],
        "raw_scenario": {"kind": "multiround"},
        "verifier": {"type": "_stub", "asserts": []},
    }

    run = runner.run_scenario(meta, scenario)

    assert run.result.passed is True
    assert FakeHTTPClient.timeouts == [300, 300]


def test_runner_explicit_timeout_per_case_wins_over_pack_default(monkeypatch):
    import benchlocal_cli.runner as runner_module

    FakeHTTPClient.calls = 0
    FakeHTTPClient.timeouts = []
    monkeypatch.setattr(runner_module.httpx, "Client", FakeHTTPClient)
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        enable_sandboxed_packs=True,
        timeout_per_case=45,
    )
    fake = FakeMultiTurnSandbox()
    runner._sandbox_clients["cli-40"] = fake
    meta = {
        "supports_sandboxed_only": True,
        "default_max_seconds": 60,
        "timeout_per_case_default": 300,
        "sampling_defaults": {"max_tokens": 16},
    }
    scenario = {
        "id": "CLI-36",
        "pack_id": "cli-40",
        "messages": [{"role": "user", "content": "use jq"}],
        "raw_scenario": {"kind": "multiround"},
        "verifier": {"type": "_stub", "asserts": []},
    }

    run = runner.run_scenario(meta, scenario)

    assert run.result.passed is True
    assert FakeHTTPClient.timeouts == [45, 45]


class FakeAlwaysToolHTTPClient:
    timeouts: list[float] = []

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self.timeouts.append(timeout)

    def __enter__(self) -> "FakeAlwaysToolHTTPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, json: dict) -> FakeHTTPResponse:
        return FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-loop",
                                    "type": "function",
                                    "function": {"name": "bash", "arguments": "{\"command\":\"pwd\"}"},
                                }
                            ],
                        }
                    }
                ],
                "usage": {"completion_tokens": 1},
            }
        )


class FakeTimeoutHTTPClient:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def __enter__(self) -> "FakeTimeoutHTTPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, json: dict) -> FakeHTTPResponse:
        import benchlocal_cli.runner as runner_module

        raise runner_module.httpx.TimeoutException("read timed out")


class FakeCliLoopExhaustedSandbox:
    config = FakeMultiTurnConfig()

    def __init__(self) -> None:
        self.ended = False

    def verify_multiturn_start(self, scenario: dict, **_kwargs) -> dict:
        return {
            "scenario_state_id": "state-loop",
            "prompt": scenario["messages"],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "bash", "parameters": {"type": "object", "properties": {}}},
                }
            ],
        }

    def verify_multiturn_turn(self, scenario_state_id: str, model_response: dict) -> dict:
        assert scenario_state_id == "state-loop"
        return {
            "action": "next-prompt",
            "prompt": [{"role": "tool", "tool_call_id": "call-loop", "name": "bash", "content": "{}"}],
            "tools": [],
        }

    def verify_multiturn_end(self, scenario_state_id: str) -> dict:
        assert scenario_state_id == "state-loop"
        self.ended = True
        return {
            "action": "verify-final",
            "passed": False,
            "failure_mode": "agent_loop_exhausted",
            "detail": "CLI-21: agent loop ended before success",
            "trace": {"turn_count": 1},
        }


def test_cli_multiturn_agent_loop_exhausted_is_not_timeout(monkeypatch):
    import benchlocal_cli.runner as runner_module

    FakeAlwaysToolHTTPClient.timeouts = []
    monkeypatch.setattr(runner_module.httpx, "Client", FakeAlwaysToolHTTPClient)
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True)
    fake = FakeCliLoopExhaustedSandbox()
    runner._sandbox_clients["cli-40"] = fake
    meta = {
        "supports_sandboxed_only": True,
        "timeout_per_case_default": 300,
        "sampling_defaults": {"max_tokens": 16},
    }
    scenario = {
        "id": "CLI-21",
        "pack_id": "cli-40",
        "messages": [{"role": "user", "content": "use the shell"}],
        "raw_scenario": {"kind": "multiround", "max_turns": 1},
        "verifier": {"type": "_stub", "asserts": []},
    }

    run = runner.run_scenario(meta, scenario)

    assert run.result.passed is False
    assert run.result.failure_mode == "agent_loop_exhausted"
    assert "agent loop ended before success" in run.result.detail
    assert FakeAlwaysToolHTTPClient.timeouts == [300]
    assert fake.ended is True


def test_cli_multiturn_wall_clock_timeout_remains_timeout(monkeypatch):
    import benchlocal_cli.runner as runner_module

    monkeypatch.setattr(runner_module.httpx, "Client", FakeTimeoutHTTPClient)
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True)
    fake = FakeCliLoopExhaustedSandbox()
    runner._sandbox_clients["cli-40"] = fake
    meta = {
        "supports_sandboxed_only": True,
        "timeout_per_case_default": 300,
        "sampling_defaults": {"max_tokens": 16},
    }
    scenario = {
        "id": "CLI-21",
        "pack_id": "cli-40",
        "messages": [{"role": "user", "content": "use the shell"}],
        "raw_scenario": {"kind": "multiround", "max_turns": 1},
        "verifier": {"type": "_stub", "asserts": []},
    }

    run = runner.run_scenario(meta, scenario)

    assert run.result.passed is False
    assert run.result.failure_mode == "timeout"
    assert "timed out after 300" in run.result.detail
    assert fake.ended is True


class FakeHermesEarlyOutSandbox:
    """Hermes v0.7.3 sandbox: /verify-start returns verify-final directly,
    no /verify-turn loop. Captures the start kwargs so we can assert the
    runner passed model_endpoint/model_name/sampling through.
    """

    def __init__(self, *, passed: bool = True, failure_mode: str = "passed", detail: str = "upstream pass") -> None:
        self.config = FakeMultiTurnConfig()
        self.start_kwargs: dict = {}
        self.turn_called = False
        self.end_called = False
        self._passed = passed
        self._failure_mode = failure_mode
        self._detail = detail

    def verify_multiturn_start(self, scenario: dict, **kwargs) -> dict:
        self.start_kwargs = dict(kwargs)
        return {
            "action": "verify-final",
            "passed": self._passed,
            "failure_mode": self._failure_mode,
            "detail": self._detail,
            "trace": {
                "hermes_agent_source": "host-mount",
                "hermes_agent_commit": "abc123",
                "tool_events": [{"name": "memory_set"}, {"name": "memory_set"}],
                "final_response": "Stored CockroachDB memory.",
            },
        }

    def verify_multiturn_turn(self, scenario_state_id: str, model_response: dict) -> dict:
        self.turn_called = True
        raise AssertionError("verify_multiturn_turn must not be called for Hermes v0.7.3")

    def verify_multiturn_end(self, scenario_state_id: str) -> dict:
        self.end_called = True
        return {"action": "verify-final", "passed": False, "failure_mode": "timeout", "detail": "", "trace": {}}


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:9999",
        "http://127.0.0.1:9999/v1",
        "http://127.1.2.3:9999",
        "http://[::1]:9999/v1/chat/completions",
        "http://[::]:9999",
    ],
)
def test_endpoint_is_loopback_covers_local_variants(endpoint):
    from benchlocal_cli.sandbox import endpoint_is_loopback

    assert endpoint_is_loopback(endpoint) is True


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com:9999",
        "http://host.docker.internal:9999",
        "http://10.0.0.5:9999",
        "https://model.internal/v1",
    ],
)
def test_endpoint_is_loopback_rejects_non_loopback(endpoint):
    from benchlocal_cli.sandbox import endpoint_is_loopback

    assert endpoint_is_loopback(endpoint) is False


def test_hermes_docker_argv_adds_host_gateway_for_loopback_without_env(monkeypatch):
    from benchlocal_cli.sandbox import SandboxClient, config_for_pack

    monkeypatch.delenv("BENCHLOCAL_HERMES_RESOLVE_LOCALHOST", raising=False)
    client = SandboxClient(config_for_pack("hermesagent-20"), model_endpoint="http://localhost:9999/v1")
    argv = client._build_docker_run_argv("test-name", None)

    assert "--add-host" in argv
    assert "host.docker.internal:host-gateway" in argv


def test_hermes_docker_argv_keeps_non_loopback_env_gated(monkeypatch):
    from benchlocal_cli.sandbox import SandboxClient, config_for_pack

    monkeypatch.delenv("BENCHLOCAL_HERMES_RESOLVE_LOCALHOST", raising=False)
    client = SandboxClient(config_for_pack("hermesagent-20"), model_endpoint="http://host.docker.internal:9999/v1")
    argv = client._build_docker_run_argv("test-name", None)

    assert "--add-host" not in argv
    assert "host.docker.internal:host-gateway" not in argv


def test_runner_rewrites_hermes_loopback_endpoint_without_env(monkeypatch):
    monkeypatch.delenv("BENCHLOCAL_HERMES_RESOLVE_LOCALHOST", raising=False)
    runner = Runner(
        endpoint="http://localhost:9999/v1/chat/completions",
        model="qwen3.6-27b-autoround",
        enable_sandboxed_packs=True,
    )
    fake = FakeHermesEarlyOutSandbox()
    runner._sandbox_clients["hermesagent-20"] = fake
    meta = {
        "supports_sandboxed_only": True,
        "default_max_seconds": 60,
        "sampling_defaults": {"max_tokens": 256, "temperature": 0.0},
    }
    scenario = {
        "id": "HA-01",
        "pack_id": "hermesagent-20",
        "messages": [{"role": "user", "content": "remember CockroachDB"}],
        "raw_scenario": {"kind": "memory_replace_contradiction"},
        "verifier": {"type": "_stub", "asserts": []},
    }

    run = runner.run_scenario(meta, scenario)

    assert run.result.passed is True
    assert fake.start_kwargs["model_endpoint"] == "http://host.docker.internal:9999/v1/chat/completions"


def test_runner_preserves_hermes_non_loopback_endpoint_without_env(monkeypatch):
    monkeypatch.delenv("BENCHLOCAL_HERMES_RESOLVE_LOCALHOST", raising=False)
    runner = Runner(
        endpoint="http://host.docker.internal:9999/v1",
        model="qwen3.6-27b-autoround",
        enable_sandboxed_packs=True,
    )
    fake = FakeHermesEarlyOutSandbox()
    runner._sandbox_clients["hermesagent-20"] = fake
    meta = {
        "supports_sandboxed_only": True,
        "default_max_seconds": 60,
        "sampling_defaults": {"max_tokens": 256, "temperature": 0.0},
    }
    scenario = {
        "id": "HA-01",
        "pack_id": "hermesagent-20",
        "messages": [{"role": "user", "content": "remember CockroachDB"}],
        "raw_scenario": {"kind": "memory_replace_contradiction"},
        "verifier": {"type": "_stub", "asserts": []},
    }

    run = runner.run_scenario(meta, scenario)

    assert run.result.passed is True
    assert fake.start_kwargs["model_endpoint"] == "http://host.docker.internal:9999/v1"

def test_runner_uses_hermes_verify_start_early_out_and_passes_endpoint():
    runner = Runner(
        endpoint="http://10.0.0.5:8001",
        model="qwen3.6-27b-autoround",
        enable_sandboxed_packs=True,
    )
    fake = FakeHermesEarlyOutSandbox()
    runner._sandbox_clients["hermesagent-20"] = fake
    meta = {
        "supports_sandboxed_only": True,
        "default_max_seconds": 60,
        "sampling_defaults": {"max_tokens": 256, "temperature": 0.0},
    }
    scenario = {
        "id": "HA-01",
        "pack_id": "hermesagent-20",
        "messages": [{"role": "user", "content": "remember CockroachDB"}],
        "raw_scenario": {"kind": "memory_replace_contradiction"},
        "verifier": {"type": "_stub", "asserts": []},
    }

    run = runner.run_scenario(meta, scenario)

    assert run.result.passed is True
    assert run.result.detail == "upstream pass"
    assert run.turn_count == 0
    assert fake.turn_called is False
    assert fake.end_called is False
    # Runner must pass endpoint/model through to upstream agent-runner
    assert fake.start_kwargs["model_endpoint"] == "http://10.0.0.5:8001"
    assert fake.start_kwargs["model_name"] == "qwen3.6-27b-autoround"
    assert fake.start_kwargs["model_api_key"] == "dummy"
    assert fake.start_kwargs["sampling"]["temperature"] == 0.0
    assert fake.start_kwargs["sampling"]["max_tokens"] == 256
    # verifier_trace populated from upstream payload — preserves the
    # `trace` sub-dict the sandbox returned (one level of nesting because
    # the runner strips top-level action/passed/failure_mode/detail keys).
    assert run.result.verifier_trace is not None
    nested_trace = run.result.verifier_trace.get("trace") or {}
    assert nested_trace.get("hermes_agent_commit") == "abc123"
    assert nested_trace.get("hermes_agent_source") == "host-mount"


def test_runner_propagates_hermes_failure_mode_from_verify_start():
    runner = Runner(endpoint="http://localhost:8001", model="fake", enable_sandboxed_packs=True)
    fake = FakeHermesEarlyOutSandbox(
        passed=False, failure_mode="agent_runner_timeout", detail="HA-09: upstream exceeded 900s"
    )
    runner._sandbox_clients["hermesagent-20"] = fake
    meta = {"supports_sandboxed_only": True, "default_max_seconds": 60, "sampling_defaults": {}}
    scenario = {
        "id": "HA-09",
        "pack_id": "hermesagent-20",
        "messages": [{"role": "user", "content": "do work"}],
        "raw_scenario": {"kind": "skill_run"},
        "verifier": {"type": "_stub", "asserts": []},
    }

    run = runner.run_scenario(meta, scenario)

    assert run.result.passed is False
    assert run.result.failure_mode == "agent_runner_timeout"
    assert "exceeded 900s" in run.result.detail


def test_detect_hermes_agent_host_path_force_baked_returns_none(monkeypatch):
    from benchlocal_cli import sandbox as sandbox_module

    monkeypatch.setenv("HERMES_AGENT_FORCE_BAKED", "1")
    monkeypatch.delenv("HERMES_AGENT_HOST_PATH", raising=False)
    assert sandbox_module.detect_hermes_agent_host_path() is None


def test_detect_hermes_agent_host_path_explicit_must_be_valid(monkeypatch, tmp_path):
    from benchlocal_cli import sandbox as sandbox_module
    import pytest

    monkeypatch.delenv("HERMES_AGENT_FORCE_BAKED", raising=False)
    # Empty dir: fails the run_agent.py / hermes_state.py existence check.
    empty = tmp_path / "stub"
    empty.mkdir()
    monkeypatch.setenv("HERMES_AGENT_HOST_PATH", str(empty))
    with pytest.raises(RuntimeError, match="does not look like a hermes-agent install"):
        sandbox_module.detect_hermes_agent_host_path()

    # Valid dir: required files present → returns the resolved path.
    valid = tmp_path / "real"
    valid.mkdir()
    (valid / "run_agent.py").write_text("# stub")
    (valid / "hermes_state.py").write_text("# stub")
    monkeypatch.setenv("HERMES_AGENT_HOST_PATH", str(valid))
    assert sandbox_module.detect_hermes_agent_host_path() == str(valid.resolve())


def test_detect_hermes_agent_host_path_missing_returns_none(monkeypatch, tmp_path):
    from benchlocal_cli import sandbox as sandbox_module

    monkeypatch.delenv("HERMES_AGENT_FORCE_BAKED", raising=False)
    monkeypatch.delenv("HERMES_AGENT_HOST_PATH", raising=False)
    # Point Path.home() at an empty dir so auto-detect finds nothing.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("benchlocal_cli.sandbox.Path.home", lambda: fake_home)
    # Block the `which hermes` fallback too — on dev rigs this finds the real
    # user install and would mask the all-empty case.
    monkeypatch.setattr("benchlocal_cli.sandbox.shutil.which", lambda _: None)
    # /opt/hermes-agent on the test box may exist but is unlikely to be valid;
    # we don't fake it here — the test is about the all-empty case.
    if not (
        sandbox_module._is_valid_hermes_agent_install(sandbox_module.Path("/opt/hermes-agent"))
    ):
        assert sandbox_module.detect_hermes_agent_host_path() is None


def test_detect_hermes_agent_host_path_falls_back_to_which_hermes(monkeypatch, tmp_path):
    """When no candidate dir matches, follow `which hermes` through the
    symlink and walk up to the install root. Catches non-standard install
    layouts (custom prefixes, pipx-style locations).
    """
    from benchlocal_cli import sandbox as sandbox_module

    monkeypatch.delenv("HERMES_AGENT_FORCE_BAKED", raising=False)
    monkeypatch.delenv("HERMES_AGENT_HOST_PATH", raising=False)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("benchlocal_cli.sandbox.Path.home", lambda: fake_home)

    # Simulate an install layout: <root>/venv/bin/hermes is a symlink
    # target; <root> is the install root with the required files.
    install = tmp_path / "custom-prefix" / "hermes-agent"
    install.mkdir(parents=True)
    (install / "run_agent.py").write_text("# stub")
    (install / "hermes_state.py").write_text("# stub")
    venv_bin = install / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    binary = venv_bin / "hermes"
    binary.write_text("#!/usr/bin/env python3\nprint('hermes stub')\n")
    binary.chmod(0o755)

    if sandbox_module._is_valid_hermes_agent_install(sandbox_module.Path("/opt/hermes-agent")):
        import pytest as _pytest
        _pytest.skip("/opt/hermes-agent is also a valid install on this box")

    monkeypatch.setattr("benchlocal_cli.sandbox.shutil.which", lambda name: str(binary) if name == "hermes" else None)

    detected = sandbox_module.detect_hermes_agent_host_path()
    assert detected == str(install.resolve())


def test_detect_hermes_agent_host_path_picks_up_dot_hermes_layout(monkeypatch, tmp_path):
    """The official `hermes` installer lays its source at ~/.hermes/hermes-agent.
    Auto-detect must include this path — added 2026-05-09 after the v0.7.3 A/B
    where the user's existing pipx-style install was missed by the original
    {/opt, ~, ~/.local} candidate list.
    """
    from benchlocal_cli import sandbox as sandbox_module

    monkeypatch.delenv("HERMES_AGENT_FORCE_BAKED", raising=False)
    monkeypatch.delenv("HERMES_AGENT_HOST_PATH", raising=False)
    fake_home = tmp_path / "home"
    install = fake_home / ".hermes" / "hermes-agent"
    install.mkdir(parents=True)
    (install / "run_agent.py").write_text("# stub")
    (install / "hermes_state.py").write_text("# stub")
    monkeypatch.setattr("benchlocal_cli.sandbox.Path.home", lambda: fake_home)

    # Skip if /opt/hermes-agent on the test box is also a valid install — the
    # multi-match case is handled by a separate test path in the helper itself.
    if sandbox_module._is_valid_hermes_agent_install(sandbox_module.Path("/opt/hermes-agent")):
        import pytest as _pytest
        _pytest.skip("/opt/hermes-agent is also a valid install on this box; multi-match case")

    detected = sandbox_module.detect_hermes_agent_host_path()
    assert detected == str(install.resolve())


def test_config_for_pack_hermes_populates_bind_mounts(monkeypatch, tmp_path):
    from benchlocal_cli import sandbox as sandbox_module

    valid = tmp_path / "hermes-agent"
    valid.mkdir()
    (valid / "run_agent.py").write_text("# stub")
    (valid / "hermes_state.py").write_text("# stub")
    monkeypatch.delenv("HERMES_AGENT_FORCE_BAKED", raising=False)
    monkeypatch.setenv("HERMES_AGENT_HOST_PATH", str(valid))

    config = sandbox_module.config_for_pack("hermesagent-20")

    # The hermes install is bind-mounted at the SAME path inside the container
    # (so venv shebangs resolve). No separate /opt/hermes-agent target.
    assert config.host_mounts != ()
    assert config.host_mounts[0][0] == str(valid.resolve())
    assert config.host_mounts[0][1] == str(valid.resolve())  # host == container
    # request_timeout_s preserved from registry default
    assert config.request_timeout_s == 900.0
    # commit env var + HERMES_AGENT_PATH injected
    env_keys = {k for k, _ in config.env}
    assert "BENCHLOCAL_HERMES_AGENT_COMMIT" in env_keys
    assert "HERMES_AGENT_PATH" in env_keys
    # No venv in this stub, so HERMES_AGENT_PYTHON not injected
    assert "HERMES_AGENT_PYTHON" not in env_keys


def test_config_for_pack_hermes_adds_venv_python_mount(monkeypatch, tmp_path):
    """When the host install has a venv with a uv-managed python, both the
    install dir AND the uv python tree are bind-mounted, and HERMES_AGENT_PYTHON
    points at the venv's python.
    """
    from benchlocal_cli import sandbox as sandbox_module

    install = tmp_path / "hermes-agent"
    install.mkdir()
    (install / "run_agent.py").write_text("# stub")
    (install / "hermes_state.py").write_text("# stub")
    # Simulate uv-managed python: the venv's python is a symlink chain leading
    # to ~/.local/share/uv/python/<dist>/bin/python3.11.
    uv_root = tmp_path / "fake_home" / ".local" / "share" / "uv" / "python"
    uv_dist = uv_root / "cpython-3.11-linux-x86_64-gnu"
    (uv_dist / "bin").mkdir(parents=True)
    real_python = uv_dist / "bin" / "python3.11"
    real_python.write_text("#!/bin/sh\nexit 0\n")
    real_python.chmod(0o755)
    venv_bin = install / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(real_python)

    monkeypatch.delenv("HERMES_AGENT_FORCE_BAKED", raising=False)
    monkeypatch.setenv("HERMES_AGENT_HOST_PATH", str(install))

    config = sandbox_module.config_for_pack("hermesagent-20")

    mounts = {host: container for host, container in config.host_mounts}
    # Two mounts: install + uv python root, both at same path host=container
    assert str(install.resolve()) in mounts
    assert str(uv_root.resolve()) in mounts
    # HERMES_AGENT_PYTHON points at the venv's python (host path)
    env = dict(config.env)
    assert env["HERMES_AGENT_PYTHON"] == str(install / "venv" / "bin" / "python")


def test_config_for_pack_non_hermes_no_bind_mount():
    from benchlocal_cli import sandbox as sandbox_module

    config = sandbox_module.config_for_pack("bugfind-15")
    assert config.host_mounts == ()
    assert config.env == ()
    assert config.request_timeout_s == 60.0


# ---------------------------------------------------------------------------
# #6: writable host run-dir bind-mount (durable sandbox artifacts)
# ---------------------------------------------------------------------------

def test_config_for_pack_aider_declares_run_output_dir():
    from benchlocal_cli import sandbox as sandbox_module

    config = sandbox_module.config_for_pack("aider-polyglot-30")
    assert config.run_output_dir == "/tmp/aider-polyglot-runs"
    # keep-jobdirs env is gated on the run mount being active
    assert ("BENCHLOCAL_AIDER_KEEP_JOBDIRS", "1") in config.run_mount_env


def test_aider_docker_argv_adds_writable_run_mount(tmp_path):
    """With a host run-dir, aider's docker argv bind-mounts it WRITABLE (not :ro)
    at the container run_output_dir and sets the keep-jobdirs env so server.py
    doesn't rmtree the artifacts."""
    from benchlocal_cli.sandbox import SandboxClient, config_for_pack

    client = SandboxClient(config_for_pack("aider-polyglot-30"))
    run_dir = str(tmp_path / "aider-run")
    argv = client._build_docker_run_argv("test-name", run_dir)

    assert f"{run_dir}:/tmp/aider-polyglot-runs" in argv          # writable mount present
    assert f"{run_dir}:/tmp/aider-polyglot-runs:ro" not in " ".join(argv)  # NOT read-only
    assert "BENCHLOCAL_AIDER_KEEP_JOBDIRS=1" in argv             # keep-jobdirs env present


def test_aider_docker_argv_no_run_mount_without_run_dir():
    from benchlocal_cli.sandbox import SandboxClient, config_for_pack

    client = SandboxClient(config_for_pack("aider-polyglot-30"))
    argv = client._build_docker_run_argv("test-name", None)

    assert "/tmp/aider-polyglot-runs" not in " ".join(argv)
    assert "BENCHLOCAL_AIDER_KEEP_JOBDIRS=1" not in argv


def test_non_runmount_pack_ignores_run_dir(tmp_path):
    """A pack without run_output_dir (bugfind) gets no run mount even when a
    host run-dir is supplied."""
    from benchlocal_cli.sandbox import SandboxClient, config_for_pack

    client = SandboxClient(config_for_pack("bugfind-15"))
    argv = client._build_docker_run_argv("test-name", str(tmp_path / "x"))

    assert "-v" not in argv  # no host_mounts and no run_output_dir → no bind-mounts


# ---------------------------------------------------------------------------
# #3-A: aider batch timeout honors --timeout-per-case (raises, never lowers)
# ---------------------------------------------------------------------------

def test_config_for_pack_aider_default_batch_timeout():
    from benchlocal_cli import sandbox as sandbox_module
    config = sandbox_module.config_for_pack("aider-polyglot-30")
    env = dict(config.env)
    assert env["AIDER_BENCHMARK_THREADS"] == "1"
    assert env["AIDER_BENCHMARK_TIMEOUT_S"] == "3600"
    assert config.request_timeout_s == 3900.0



def test_config_for_pack_cli_raises_request_timeout_from_pack_budget():
    from benchlocal_cli import sandbox as sandbox_module

    config = sandbox_module.config_for_pack("cli-40", batch_timeout_s=300)

    assert config.request_timeout_s == 300.0

def test_config_for_pack_aider_threads_env_override(monkeypatch):
    from benchlocal_cli import sandbox as sandbox_module
    monkeypatch.setenv("BENCHLOCAL_AIDER_THREADS", "4")
    config = sandbox_module.config_for_pack("aider-polyglot-30")
    assert dict(config.env)["AIDER_BENCHMARK_THREADS"] == "4"


def test_config_for_pack_aider_raises_batch_timeout_from_per_case():
    from benchlocal_cli import sandbox as sandbox_module
    config = sandbox_module.config_for_pack("aider-polyglot-30", batch_timeout_s=7200)
    assert dict(config.env)["AIDER_BENCHMARK_TIMEOUT_S"] == "7200"
    assert config.request_timeout_s == 7500.0


def test_config_for_pack_aider_per_case_never_lowers_default():
    """A small per-case budget must NOT crush the batch below the default."""
    from benchlocal_cli import sandbox as sandbox_module
    config = sandbox_module.config_for_pack("aider-polyglot-30", batch_timeout_s=60)
    assert dict(config.env)["AIDER_BENCHMARK_TIMEOUT_S"] == "3600"
    assert config.request_timeout_s == 3900.0


# ---------------------------------------------------------------------------
# #3-B: single-scoreboard pack headline shows real X/Y, not binary 1/1 or 0/1
# ---------------------------------------------------------------------------

class FakeSingleScoreboardSandbox:
    """Single-scoreboard pack (aider): /verify-start returns the aggregate
    verify-final with pass_rate/passed_count/total_count first-class."""

    def __init__(self, *, passed, failure_mode, passed_count, total_count, pass_rate):
        self.config = FakeMultiTurnConfig()
        self._p = dict(
            passed=passed, failure_mode=failure_mode,
            passed_count=passed_count, total_count=total_count, pass_rate=pass_rate,
        )

    def verify_multiturn_start(self, scenario: dict, **kwargs) -> dict:
        return {"action": "verify-final", "detail": "batch", "trace": {}, **self._p}

    def verify_multiturn_turn(self, *a, **k):  # pragma: no cover
        raise AssertionError("single-scoreboard must early-out, not loop")

    def verify_multiturn_end(self, *a, **k):  # pragma: no cover
        raise AssertionError("single-scoreboard must early-out, not loop")


def _single_scoreboard_fixture():
    meta = {
        "version": "1.0.0",
        "upstream_commit": "deadbeef",
        "supports_sandboxed_only": True,
        "scenario_count": 1,
        "_architecture": "single-scoreboard",
        "default_max_seconds": 1800,
        "sampling_defaults": {"max_tokens": 256, "temperature": 0.0},
    }
    scenarios = [{
        "id": "aider-polyglot-30-batch",
        "pack_id": "aider-polyglot-30",
        "messages": [{"role": "user", "content": "batch"}],
        "raw_scenario": {"kind": "aider-polyglot-batch"},
        "verifier": {"type": "_stub", "asserts": []},
    }]
    return meta, scenarios


def test_run_pack_single_scoreboard_success_shows_real_fraction(monkeypatch):
    from benchlocal_cli import runner as runner_module
    meta, scenarios = _single_scoreboard_fixture()
    monkeypatch.setattr(runner_module, "load_pack", lambda pid: (meta, scenarios))
    r = runner_module.Runner(endpoint="http://h:8000", model="m", enable_sandboxed_packs=True)
    r._sandbox_clients["aider-polyglot-30"] = FakeSingleScoreboardSandbox(
        passed=True, failure_mode="passed", passed_count=16, total_count=30, pass_rate=16 / 30)

    pack = r.run_pack("aider-polyglot-30")

    assert pack.passed == 16          # NOT 1
    assert pack.total == 30           # NOT 1
    assert abs(pack.score - 16 / 30) < 1e-9
    assert pack.status == "ok"


def test_run_pack_single_scoreboard_timeout_with_zero_completed_stays_scoreboard(monkeypatch):
    from benchlocal_cli import runner as runner_module
    meta, scenarios = _single_scoreboard_fixture()
    monkeypatch.setattr(runner_module, "load_pack", lambda pid: (meta, scenarios))
    r = runner_module.Runner(endpoint="http://h:8000", model="m", enable_sandboxed_packs=True)
    r._sandbox_clients["aider-polyglot-30"] = FakeSingleScoreboardSandbox(
        passed=False, failure_mode="agent_runner_timeout",
        passed_count=0, total_count=0, pass_rate=0.0)

    pack = r.run_pack("aider-polyglot-30")

    assert pack.passed == 0
    assert pack.total == 0
    assert pack.score == 0.0
    assert pack.status == "agent_runner_timeout"


def test_run_pack_single_scoreboard_timeout_surfaces_partial(monkeypatch):
    from benchlocal_cli import runner as runner_module
    meta, scenarios = _single_scoreboard_fixture()
    monkeypatch.setattr(runner_module, "load_pack", lambda pid: (meta, scenarios))
    r = runner_module.Runner(endpoint="http://h:8000", model="m", enable_sandboxed_packs=True)
    r._sandbox_clients["aider-polyglot-30"] = FakeSingleScoreboardSandbox(
        passed=False, failure_mode="agent_runner_timeout",
        passed_count=17, total_count=26, pass_rate=17 / 26)

    pack = r.run_pack("aider-polyglot-30")

    assert pack.passed == 17          # partial surfaced, NOT 0
    assert pack.total == 26           # completed denominator, NOT canonical 30
    assert abs(pack.score - 17 / 26) < 1e-9
    assert pack.status == "agent_runner_timeout"   # not masked as "ok"
