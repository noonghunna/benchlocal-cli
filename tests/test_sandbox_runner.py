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
