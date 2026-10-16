"""The result's top-level reasoning_effort is the effort its requests carried.

A thinking-off run on the effort control sends `reasoning_effort: "none"` with
every request, but the result used to record the effort a thinking run would
have sent (`high` by default), so an instruct leg read as a high-effort leg
(club-3090 Discussion #1700).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchlocal_cli.cli import main
from benchlocal_cli.runner import (
    THINKING_CONTROL_EFFORT,
    THINKING_CONTROL_ENABLE,
    THINKING_CONTROL_NONE,
    Runner,
    recorded_reasoning_effort,
)


@pytest.mark.parametrize(
    ("control", "mode", "effort", "expected"),
    [
        (THINKING_CONTROL_EFFORT, "force-off", "xhigh", "none"),
        (THINKING_CONTROL_EFFORT, "force-off", "high", "none"),
        (THINKING_CONTROL_EFFORT, "force-on", "xhigh", "xhigh"),
        (THINKING_CONTROL_EFFORT, "pack-defaults", 0.75, 0.75),
        (THINKING_CONTROL_ENABLE, "force-off", "high", None),
        (THINKING_CONTROL_NONE, "force-on", "high", None),
    ],
)
def test_recorded_effort_is_what_the_requests_carried(control, mode, effort, expected):
    assert recorded_reasoning_effort(control, mode, effort) == expected


# ------------------------------------------- a live run with a detected control


def _meta() -> dict:
    return {
        "pack_id": "test-pack",
        "version": "1.0.0",
        "upstream_commit": "abc123",
        "sampling_defaults": {"max_tokens": 32},
        "default_thinking": "off",
        "default_max_seconds": 60,
    }


def _scenario() -> dict:
    return {
        "id": "T-01",
        "pack_id": "test-pack",
        "messages": [{"role": "user", "content": "test"}],
        "verifier": {"type": "instruct_follow", "asserts": []},
    }


def _detected_effort_run(monkeypatch, thinking_enabled: bool):
    """No --reasoning-effort: the control comes from the template, as on Project Maya."""
    monkeypatch.setattr(
        "benchlocal_cli.runner.load_pack", lambda _pack_id: (_meta(), [_scenario()])
    )
    runner = Runner(
        endpoint="http://localhost:1",
        model="fake",
        thinking_enabled=thinking_enabled,
        measured_tps=100.0,
    )
    monkeypatch.setattr(runner, "_endpoint_reachable", lambda: True)
    monkeypatch.setattr(
        runner, "_props_chat_template", lambda: "Thinking effort: {{ reasoning_effort }}"
    )
    requests: list[dict] = []

    def fake_post(request, timeout, *, max_attempts=None):
        requests.append(request)
        return 200, {"choices": [{"message": {"content": "OK"}}]}, None

    monkeypatch.setattr(runner, "_post_chat", fake_post)
    return runner.run(["test-pack"]), requests


def test_thinking_off_run_on_a_detected_effort_control_records_none(monkeypatch):
    result, requests = _detected_effort_run(monkeypatch, thinking_enabled=False)

    assert requests and all(req["reasoning_effort"] == "none" for req in requests)
    assert result.thinking_control == THINKING_CONTROL_EFFORT
    assert result.to_dict()["reasoning_effort"] == "none"


def test_thinking_on_run_on_a_detected_effort_control_records_the_effort_sent(monkeypatch):
    result, requests = _detected_effort_run(monkeypatch, thinking_enabled=True)

    assert requests and all(req["reasoning_effort"] == "high" for req in requests)
    assert result.to_dict()["reasoning_effort"] == "high"


# ------------------------------------------------------------------ end to end

MOCK = {
    "SO-01": {"choices": [{"message": {"content": '{"title":"The Great Gatsby","year":1925}'}}],
              "usage": {"completion_tokens": 3}},
    "SO-02": {"choices": [{"message": {"content": "not json"}}], "usage": {"completion_tokens": 3}},
}


def _args(tmp_path: Path, out: Path, *extra: str) -> list[str]:
    mock = tmp_path / "mock.json"
    mock.write_text(json.dumps(MOCK))
    return ["run", "--endpoint", "mock", "--model", "mock", "--measured-tps", "100",
            "--mock-responses-from-json", str(mock), "--scenario", "structoutput-15/SO-01",
            "--scenario", "structoutput-15/SO-02", "--save-json", str(out), *extra]


def test_no_thinking_with_an_explicit_effort_records_none(tmp_path, capsys):
    out = tmp_path / "result.json"
    assert main(_args(tmp_path, out, "--no-thinking", "--reasoning-effort", "xhigh")) == 0
    saved = json.loads(out.read_text())
    assert saved["thinking_control"] == THINKING_CONTROL_EFFORT
    assert saved["reasoning_effort"] == "none"


def test_enable_thinking_with_an_explicit_effort_records_it(tmp_path, capsys):
    out = tmp_path / "result.json"
    assert main(_args(tmp_path, out, "--enable-thinking", "--reasoning-effort", "xhigh")) == 0
    assert json.loads(out.read_text())["reasoning_effort"] == "xhigh"


def test_journal_recovery_records_none_for_a_thinking_off_run(tmp_path, monkeypatch, capsys):
    out = tmp_path / "result.json"
    original = Runner.run_scenario
    calls = 0

    def interrupt_on_second(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Runner, "run_scenario", interrupt_on_second)
    with pytest.raises(KeyboardInterrupt):
        main(_args(tmp_path, out, "--no-thinking", "--reasoning-effort", "xhigh", "--incremental"))
    monkeypatch.setattr(Runner, "run_scenario", original)

    from benchlocal_cli.persistence import result_from_journal

    sidecar = Path(f"{out}.partial.jsonl")
    assert result_from_journal(sidecar)["reasoning_effort"] == "none"

    assert main(["run", "--resume", str(sidecar),
                 "--mock-responses-from-json", str(tmp_path / "mock.json")]) == 0
    assert json.loads(out.read_text())["reasoning_effort"] == "none"


def test_retry_of_a_thinking_off_baseline_does_not_send_none_to_a_thinking_retry(
    tmp_path, capsys
):
    """A baseline's `none` is the off switch it sent, not an effort to think at."""
    baseline = tmp_path / "baseline.json"
    assert main(_args(tmp_path, baseline, "--no-thinking", "--reasoning-effort", "xhigh")) == 0
    assert json.loads(baseline.read_text())["reasoning_effort"] == "none"

    retry = tmp_path / "retry.json"
    mock = tmp_path / "mock.json"
    assert main(["run", "--endpoint", "mock", "--model", "mock", "--measured-tps", "100",
                 "--mock-responses-from-json", str(mock), "--previous-result", str(baseline),
                 "--retry-failed", "1", "--enable-thinking", "--save-json", str(retry)]) == 0
    saved = json.loads(retry.read_text())
    assert saved["thinking_mode"] == "force-on"
    assert saved.get("reasoning_effort") != "none"


def test_retry_of_a_thinking_off_baseline_keeps_its_control(tmp_path, capsys):
    baseline = tmp_path / "baseline.json"
    assert main(_args(tmp_path, baseline, "--no-thinking", "--reasoning-effort", "xhigh")) == 0

    retry = tmp_path / "retry.json"
    mock = tmp_path / "mock.json"
    assert main(["run", "--endpoint", "mock", "--model", "mock", "--measured-tps", "100",
                 "--mock-responses-from-json", str(mock), "--previous-result", str(baseline),
                 "--retry-failed", "1", "--save-json", str(retry)]) == 0
    saved = json.loads(retry.read_text())
    assert saved["thinking_mode"] == "force-off"
    assert saved["thinking_control"] == THINKING_CONTROL_EFFORT
    assert saved["reasoning_effort"] == "none"
