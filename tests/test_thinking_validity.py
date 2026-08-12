"""Tests for #126 -- thinking-validity detection."""

from __future__ import annotations

import pytest

from benchlocal_cli.runner import Runner
from benchlocal_cli.thinking_validity import (
    message_has_reasoning,
    pack_thinking_validity,
    response_has_reasoning,
    thinking_validity_for_packs,
    validity_warning,
)
from benchlocal_cli.types import PackResult, ScenarioResult, ScenarioRun

THINK_OPEN = "<" + "think" + ">"
THINK_CLOSE = "</" + "think" + ">"


def _message(content="The answer is 4.", reasoning=None):
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return message


def _response(content="The answer is 4.", reasoning=None):
    return {"choices": [{"message": _message(content, reasoning), "finish_reason": "stop"}]}


def test_message_has_reasoning_detects_parsed_and_embedded_forms():
    assert message_has_reasoning(_message(reasoning="Let me think."))
    assert message_has_reasoning({"reasoning": "alt field"})
    assert message_has_reasoning({"content": f"hmm\n{THINK_OPEN}workings{THINK_CLOSE}answer"})
    assert not message_has_reasoning(_message())
    # Blank reasoning_content is not reasoning.
    assert not message_has_reasoning(_message(reasoning="   "))


def test_response_has_reasoning_shapes():
    assert response_has_reasoning(_response(reasoning="x")) is True
    assert response_has_reasoning(_response()) is False
    # No message at all (timeout / HTTP failure) is not evidence either way.
    assert response_has_reasoning(None) is None
    assert response_has_reasoning({"choices": []}) is None
    assert response_has_reasoning({"error": "boom"}) is None


def test_response_has_reasoning_walks_multiturn_responses():
    nested = {
        "multi_turn": True,
        "responses": [_response(), _response(reasoning="turn two thinks")],
    }
    assert response_has_reasoning(nested) is True
    nested_silent = {"multi_turn": True, "responses": [_response(), _response()]}
    assert response_has_reasoning(nested_silent) is False


def _run(scenario_id, raw_response):
    return ScenarioRun(
        id=scenario_id,
        result=ScenarioResult(scenario_id, True, "passed", "ok", 1.0),
        raw_scenario={"id": scenario_id},
        raw_response=raw_response,
        request={},
        sampling_params={},
        status_code=200,
    )


def _pack(pack_id, thinking_enabled, runs, skipped=False):
    return PackResult(
        pack_id=pack_id,
        version="1.0.0",
        upstream_commit="abc",
        scenario_count=len(runs),
        passed=len(runs),
        total=len(runs),
        score=1.0,
        latency={},
        scenarios=runs,
        skipped=skipped,
        thinking_enabled=thinking_enabled,
    )


def test_pack_validity_statuses():
    silent = _pack("p-on", True, [_run("a", _response()), _run("b", _response())])
    assert pack_thinking_validity(silent) == {
        "expected": "on", "responses": 2, "with_reasoning": 0, "status": "silent",
    }

    partial = _pack("p-on", True, [_run("a", _response()), _run("b", _response(reasoning="x"))])
    # Models may think conditionally: partial presence is legitimate.
    assert pack_thinking_validity(partial)["status"] == "ok"

    clean_off = _pack("p-off", False, [_run("a", _response())])
    assert pack_thinking_validity(clean_off)["status"] == "ok"

    contaminated = _pack("p-off", False, [_run("a", _response()), _run("b", _response(reasoning="x"))])
    observation = pack_thinking_validity(contaminated)
    assert observation["status"] == "contaminated"
    assert observation["with_reasoning"] == 1

    # Errors carry no message: nothing to judge.
    errors_only = _pack("p-err", True, [_run("a", None), _run("b", {"choices": []})])
    assert pack_thinking_validity(errors_only) is None


def test_validity_warning_text():
    silent = {"status": "silent", "responses": 5, "with_reasoning": 0}
    assert "NOT a valid thinking arm" in validity_warning("p", silent)
    contaminated = {"status": "contaminated", "responses": 5, "with_reasoning": 2}
    warning = validity_warning("p", contaminated)
    assert "CONTAMINATED" in warning
    assert "2 of 5" in warning
    assert validity_warning("p", {"status": "ok"}) is None


def test_thinking_validity_for_packs_skips_skipped_and_empty():
    packs = [
        _pack("skipped", True, [_run("a", _response())], skipped=True),
        _pack("empty", True, []),
        _pack("real", False, [_run("a", _response(reasoning="x"))]),
    ]
    observations, warnings = thinking_validity_for_packs(packs)
    assert set(observations) == {"real"}
    assert len(warnings) == 1


def _meta(default_thinking):
    return {
        "pack_id": "test-pack",
        "version": "1.0.0",
        "upstream_commit": "abc123",
        "sampling_defaults": {"max_tokens": 32},
        "default_thinking": default_thinking,
        "default_max_seconds": 60,
    }


def _scenario():
    return {
        "id": "T-01",
        "pack_id": "test-pack",
        "messages": [{"role": "user", "content": "test"}],
        "verifier": {"type": "instruct_follow", "asserts": []},
    }


def _fake_run(monkeypatch, default_thinking, raw_response, **runner_kwargs):
    monkeypatch.setattr(
        "benchlocal_cli.runner.load_pack",
        lambda _pack_id: (_meta(default_thinking), [_scenario()]),
    )
    runner = Runner(endpoint="http://localhost:1", model="mock", **runner_kwargs)

    def fake_run_scenario(meta, scenario, *, repeat_index=1):
        return _run(scenario["id"], raw_response)

    monkeypatch.setattr(runner, "run_scenario", fake_run_scenario)
    return runner.run(["test-pack"])


def test_run_records_contaminated_no_thinking_arm(monkeypatch):
    result = _fake_run(monkeypatch, "off", _response(reasoning="server forced this"))

    assert result.thinking_validity == {
        "test-pack": {"expected": "off", "responses": 1, "with_reasoning": 1, "status": "contaminated"},
    }
    assert any("CONTAMINATED" in warning for warning in result.warnings)


def test_run_records_silent_thinking_arm(monkeypatch):
    result = _fake_run(monkeypatch, "on", _response())

    assert result.thinking_validity["test-pack"]["status"] == "silent"
    assert any("NOT a valid thinking arm" in warning for warning in result.warnings)


def test_run_ok_arm_has_no_validity_warning(monkeypatch):
    result = _fake_run(monkeypatch, "on", _response(reasoning="model thought"))

    assert result.thinking_validity["test-pack"]["status"] == "ok"
    assert result.warnings == []


def test_mock_traffic_skips_the_check(monkeypatch):
    result = _fake_run(
        monkeypatch,
        "off",
        _response(reasoning="mock"),
        mock_responses={"T-01": _response(reasoning="mock")},
    )

    assert result.thinking_validity is None
    assert result.warnings == []


def test_result_json_carries_thinking_validity(monkeypatch):
    result = _fake_run(monkeypatch, "off", _response(reasoning="x"))

    assert result.to_dict()["thinking_validity"]["test-pack"]["status"] == "contaminated"


def test_result_json_omits_field_when_check_did_not_run(monkeypatch):
    result = _fake_run(monkeypatch, "off", _response(), mock_responses={"T-01": _response()})

    assert "thinking_validity" not in result.to_dict()


def test_strict_thinking_exit_code(monkeypatch, capsys):
    """--strict-thinking returns 4 when an arm is contaminated (#126)."""
    from benchlocal_cli.cli import main
    from benchlocal_cli.types import RunResult

    contaminated = RunResult(
        schema_version="1",
        runner_version="test",
        endpoint="http://mock",
        model="mock",
        mode="custom",
        started_at="2026-08-12T00:00:00Z",
        finished_at="2026-08-12T00:00:01Z",
        packs=[],
        totals={"passed": 1, "total": 1, "score": 1.0},
        thinking_validity={"cli-40": {"expected": "off", "responses": 40, "with_reasoning": 25, "status": "contaminated"}},
    )
    clean = RunResult(
        schema_version="1",
        runner_version="test",
        endpoint="http://mock",
        model="mock",
        mode="custom",
        started_at="2026-08-12T00:00:00Z",
        finished_at="2026-08-12T00:00:01Z",
        packs=[],
        totals={"passed": 1, "total": 1, "score": 1.0},
        thinking_validity={"cli-40": {"expected": "off", "responses": 40, "with_reasoning": 0, "status": "ok"}},
    )

    monkeypatch.setattr(Runner, "run", lambda self, *a, **k: contaminated)
    args = [
        "run",
        "--scenario", "structoutput-15/SO-01",
        "--endpoint", "http://mock",
        "--model", "mock",
    ]
    assert main(args + ["--strict-thinking"]) == 4
    assert main(args) == 0  # without the flag the warning alone does not fail the run

    monkeypatch.setattr(Runner, "run", lambda self, *a, **k: clean)
    assert main(args + ["--strict-thinking"]) == 0
