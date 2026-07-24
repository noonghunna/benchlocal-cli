from __future__ import annotations

from collections.abc import Iterable

from benchlocal_cli.cli import _markdown
from benchlocal_cli.runner import Runner
from benchlocal_cli.types import ScenarioResult, ScenarioRun


def _scenario_run(scenario: dict, passed: bool, failure_mode: str, attempt: int) -> ScenarioRun:
    return ScenarioRun(
        id=scenario["id"],
        result=ScenarioResult(
            scenario_id=scenario["id"],
            passed=passed,
            failure_mode=failure_mode,  # type: ignore[arg-type]
            detail=f"attempt {attempt}",
            latency_seconds=float(attempt),
        ),
        raw_scenario=scenario,
        raw_response={"attempt": attempt},
        request={"attempt": attempt},
        sampling_params={"temperature": 0},
        status_code=200,
    )


def _runner_and_pack(
    monkeypatch,
    outcomes: Iterable[tuple[bool, str]],
    *,
    retry_failures: int = 3,
    retry_runaways: bool = False,
    scenario_overrides: dict | None = None,
    repeat: int = 1,
    inline_retries_enabled: bool = True,
):
    meta = {
        "pack_id": "test-pack",
        "version": "1.0.0",
        "upstream_commit": "abc123",
        "sampling_defaults": {"max_tokens": 32},
    }
    scenario = {
        "id": "T-01",
        "pack_id": "test-pack",
        "messages": [{"role": "user", "content": "test"}],
        **(scenario_overrides or {}),
    }
    monkeypatch.setattr(
        "benchlocal_cli.runner.load_pack",
        lambda _pack_id: (meta, [scenario]),
    )
    runner = Runner(
        endpoint="mock",
        model="mock",
        retry_failures=retry_failures,
        retry_runaways=retry_runaways,
        inline_retries_enabled=inline_retries_enabled,
    )
    sequence = iter(outcomes)
    calls: list[tuple[bool, str]] = []

    def fake_run_scenario(_meta, current_scenario, *, repeat_index=1):
        passed, failure_mode = next(sequence)
        calls.append((passed, failure_mode))
        run = _scenario_run(current_scenario, passed, failure_mode, len(calls))
        run.repeat_index = repeat_index
        return run

    monkeypatch.setattr(runner, "run_scenario", fake_run_scenario)
    return runner, runner.run_pack("test-pack", repeat=repeat), calls


def test_inline_retry_rescues_content_failure_without_changing_pass_at_one(monkeypatch):
    _runner, pack, calls = _runner_and_pack(
        monkeypatch,
        [(False, "wrong_answer"), (True, "passed")],
    )

    assert calls == [(False, "wrong_answer"), (True, "passed")]
    assert (pack.passed, pack.total, pack.score) == (0, 1, 0.0)
    assert pack.pass_at_k == {
        "k": 3,
        "passed": 1,
        "total": 1,
        "score": 1.0,
        "credited_flaky": 1,
        "safety_flaky": 0,
        "systematic": 0,
        "retried_scenarios": 1,
        "retry_attempts": 1,
    }
    run = pack.scenarios[0]
    assert run.result.passed is False
    assert run.label == "pass@2"
    assert run.attempt_count == 2
    assert run.pass_at_k is True
    assert run.retry_attempts[0]["raw_response"] == {"attempt": 2}


def test_inline_retry_stops_after_first_pass(monkeypatch):
    _runner, pack, calls = _runner_and_pack(
        monkeypatch,
        [(False, "wrong_answer"), (True, "passed")],
        retry_failures=5,
    )

    assert len(calls) == 2
    assert pack.scenarios[0].label == "pass@2"
    assert pack.pass_at_k["k"] == 5


def test_inline_retry_labels_all_failed_attempts_systematic(monkeypatch):
    _runner, pack, calls = _runner_and_pack(
        monkeypatch,
        [
            (False, "wrong_answer"),
            (False, "wrong_answer"),
            (False, "wrong_answer"),
        ],
    )

    assert len(calls) == 3
    assert pack.scenarios[0].label == "fail"
    assert pack.scenarios[0].attempt_count == 3
    assert pack.pass_at_k["passed"] == 0
    assert pack.pass_at_k["systematic"] == 1


def test_runaway_is_not_retried_without_opt_in(monkeypatch):
    _runner, pack, calls = _runner_and_pack(
        monkeypatch,
        [(False, "token_limit")],
    )

    assert len(calls) == 1
    assert pack.scenarios[0].retry_eligible is False
    assert pack.scenarios[0].label == "fail"
    assert pack.pass_at_k["systematic"] == 1


def test_retry_runaways_opts_expensive_failures_back_in(monkeypatch):
    _runner, pack, calls = _runner_and_pack(
        monkeypatch,
        [(False, "timeout"), (True, "passed")],
        retry_runaways=True,
    )

    assert len(calls) == 2
    assert pack.scenarios[0].label == "pass@2"
    assert pack.pass_at_k["passed"] == 1


def test_no_best_of_n_safety_scenario_keeps_retry_information_without_credit(monkeypatch):
    _runner, pack, calls = _runner_and_pack(
        monkeypatch,
        [(False, "verifier_fail"), (True, "passed")],
        scenario_overrides={"no_best_of_n": True},
    )

    assert len(calls) == 2
    run = pack.scenarios[0]
    assert run.label == "pass@2"
    assert run.best_of_n_eligible is False
    assert run.pass_at_k is False
    assert pack.pass_at_k["passed"] == 0
    assert pack.pass_at_k["credited_flaky"] == 0
    assert pack.pass_at_k["safety_flaky"] == 1


def test_infra_failure_retries_even_when_model_verdict_retries_are_disabled(monkeypatch):
    _runner, pack, calls = _runner_and_pack(
        monkeypatch,
        [(False, "http_error"), (True, "passed")],
        retry_failures=0,
    )

    assert len(calls) == 2
    assert pack.scenarios[0].label == "pass@2"
    assert pack.pass_at_k["k"] == 3
    assert pack.pass_at_k["passed"] == 1


def test_no_retry_disables_content_retries_and_pass_at_k(monkeypatch):
    _runner, pack, calls = _runner_and_pack(
        monkeypatch,
        [(False, "wrong_answer")],
        retry_failures=0,
    )

    assert len(calls) == 1
    assert pack.scenarios[0].label == "fail"
    assert pack.pass_at_k is None


def test_hard_bypass_disables_infra_retries_for_posthoc_diagnostics(monkeypatch):
    _runner, pack, calls = _runner_and_pack(
        monkeypatch,
        [(False, "http_error")],
        retry_failures=0,
        inline_retries_enabled=False,
    )

    assert len(calls) == 1
    assert pack.scenarios[0].label is None
    assert pack.pass_at_k is None


def test_repeat_remains_symmetric_and_disables_nested_inline_retries(monkeypatch):
    _runner, pack, calls = _runner_and_pack(
        monkeypatch,
        [(False, "wrong_answer"), (True, "passed")],
        repeat=2,
    )

    assert len(calls) == 2
    assert [run.repeat_index for run in pack.scenarios] == [1, 2]
    assert all(run.retry_attempts == [] for run in pack.scenarios)
    assert all(run.label is None for run in pack.scenarios)
    assert pack.pass_at_k is None
    assert pack.variance is not None


def test_run_and_markdown_report_pass_at_one_and_pass_at_k_together(monkeypatch):
    meta = {
        "pack_id": "test-pack",
        "version": "1.0.0",
        "upstream_commit": "abc123",
        "sampling_defaults": {"max_tokens": 32},
    }
    scenario = {
        "id": "T-01",
        "pack_id": "test-pack",
        "messages": [{"role": "user", "content": "test"}],
    }
    monkeypatch.setattr(
        "benchlocal_cli.runner.load_pack",
        lambda _pack_id: (meta, [scenario]),
    )
    runner = Runner(endpoint="mock", model="mock")
    outcomes = iter([(False, "wrong_answer"), (True, "passed")])

    def fake_run_scenario(_meta, current_scenario, *, repeat_index=1):
        passed, failure_mode = next(outcomes)
        return _scenario_run(current_scenario, passed, failure_mode, 1 if not passed else 2)

    monkeypatch.setattr(runner, "run_scenario", fake_run_scenario)
    result = runner.run(["test-pack"])
    rendered = _markdown(result)

    assert result.totals == {"passed": 0, "total": 1, "score": 0.0}
    assert result.pass_at_k["passed"] == 1
    assert "Pack | Pass@1 | Pass@3 | Flaky" in rendered
    assert "TOTAL | 0 / 1 (0%) | 1 / 1 (100%) | 1" in rendered
    assert "test-pack/T-01 | pass@2 | 2 | yes" in rendered
