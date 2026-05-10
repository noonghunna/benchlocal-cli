from __future__ import annotations

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

    def verify_multiturn_start(self, scenario: dict) -> dict:
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

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

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
