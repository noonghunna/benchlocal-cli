"""Tests for benchlocal_cli.inspect — `inspect` subcommand."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from benchlocal_cli import inspect as inspect_module


def _args(**kwargs) -> argparse.Namespace:
    """Build an argparse.Namespace with inspect's defaults + overrides."""
    defaults = dict(
        scenario=None,
        pack=None,
        failed=False,
        mode=None,
        full=False,
        format="markdown",
        diff=None,    # v0.8.1
        logs=None,    # v0.8.1
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _result_v074(scenarios_by_pack) -> dict:
    """Build a v0.7.4-shape RunResult.to_dict() for tests."""
    return {
        "schema_version": "2",
        "runner_version": "0.7.4",
        "endpoint": "http://localhost:8001",
        "model": "test-model",
        "mode": "custom",
        "started_at": "2026-05-09T07:00:00Z",
        "finished_at": "2026-05-09T07:01:00Z",
        "thinking_enabled": False,
        "warnings": [],
        "totals": {"passed": 1, "total": 2, "score": 0.5},
        "packs": [
            {
                "pack_id": pack_id,
                "version": "1.0.0",
                "upstream_commit": "abc",
                "scenario_count": len(scenarios),
                "passed": sum(1 for s in scenarios if s["passed"]),
                "total": len(scenarios),
                "score": sum(1 for s in scenarios if s["passed"]) / max(1, len(scenarios)),
                "latency": {"p50": 1.0, "p95": 2.0, "mean": 1.2},
                "scenarios": scenarios,
                "skipped": False,
                "status": "ok",
                "warnings": [],
            }
            for pack_id, scenarios in scenarios_by_pack.items()
        ],
    }


def test_inspect_filters_by_scenario(tmp_path, capsys):
    result = _result_v074({
        "hermesagent-20": [
            {"id": "HA-01", "passed": True, "failure_mode": "passed", "detail": "win",
             "latency_seconds": 5.0, "raw_response": {"choices": [{"message": {"content": "ok"}}]},
             "verifier_trace": {"upstream_status": "pass"}, "conversation": []},
            {"id": "HA-02", "passed": False, "failure_mode": "verifier_fail", "detail": "miss",
             "latency_seconds": 10.0, "raw_response": {"choices": [{"message": {"content": "no"}}]},
             "verifier_trace": {"upstream_status": "fail"}, "conversation": []},
        ],
    })
    p = tmp_path / "run.json"
    p.write_text(json.dumps(result))

    rc = inspect_module.inspect_result(p, _args(scenario="HA-01"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "HA-01" in out
    assert "HA-02" not in out


def test_inspect_filters_by_failed(tmp_path, capsys):
    result = _result_v074({
        "toolcall-15": [
            {"id": "TC-01", "passed": True, "failure_mode": "passed", "detail": "ok",
             "raw_response": {}, "verifier_trace": None, "conversation": []},
            {"id": "TC-02", "passed": False, "failure_mode": "wrong_answer", "detail": "miss",
             "raw_response": {}, "verifier_trace": None, "conversation": []},
            {"id": "TC-03", "passed": False, "failure_mode": "timeout", "detail": "slow",
             "raw_response": {}, "verifier_trace": None, "conversation": []},
        ],
    })
    p = tmp_path / "run.json"
    p.write_text(json.dumps(result))

    rc = inspect_module.inspect_result(p, _args(failed=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "TC-01" not in out  # passed → filtered out
    assert "TC-02" in out
    assert "TC-03" in out


def test_inspect_filters_by_mode(tmp_path, capsys):
    result = _result_v074({
        "toolcall-15": [
            {"id": "TC-01", "passed": False, "failure_mode": "wrong_answer", "detail": "x",
             "raw_response": {}, "verifier_trace": None, "conversation": []},
            {"id": "TC-02", "passed": False, "failure_mode": "timeout", "detail": "y",
             "raw_response": {}, "verifier_trace": None, "conversation": []},
        ],
    })
    p = tmp_path / "run.json"
    p.write_text(json.dumps(result))

    rc = inspect_module.inspect_result(p, _args(mode="timeout"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "TC-02" in out
    assert "TC-01" not in out


def test_inspect_filters_by_pack(tmp_path, capsys):
    result = _result_v074({
        "toolcall-15": [
            {"id": "TC-01", "passed": False, "failure_mode": "wrong_answer", "detail": "x",
             "raw_response": {}, "verifier_trace": None, "conversation": []},
        ],
        "hermesagent-20": [
            {"id": "HA-01", "passed": False, "failure_mode": "verifier_fail", "detail": "y",
             "raw_response": {}, "verifier_trace": None, "conversation": []},
        ],
    })
    p = tmp_path / "run.json"
    p.write_text(json.dumps(result))

    rc = inspect_module.inspect_result(p, _args(pack="hermesagent-20"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "HA-01" in out
    assert "TC-01" not in out


def test_inspect_truncates_verifier_trace_by_default(tmp_path, capsys):
    """Codex review #3: default 80-line trace truncation, --full disables."""
    big_trace = {f"key{i}": f"value{i}" * 5 for i in range(200)}  # produces 200+ JSON lines
    result = _result_v074({
        "hermesagent-20": [
            {"id": "HA-BIG", "passed": False, "failure_mode": "verifier_fail", "detail": "x",
             "raw_response": {}, "verifier_trace": big_trace, "conversation": []},
        ],
    })
    p = tmp_path / "run.json"
    p.write_text(json.dumps(result))

    rc = inspect_module.inspect_result(p, _args())  # default truncation
    assert rc == 0
    out = capsys.readouterr().out
    assert "more lines truncated" in out

    rc2 = inspect_module.inspect_result(p, _args(full=True))
    assert rc2 == 0
    out2 = capsys.readouterr().out
    assert "more lines truncated" not in out2


def test_inspect_handles_missing_verifier_trace(tmp_path, capsys):
    """Codex review #6: tolerate v0.5/v0.6 saved JSONs without verifier_trace."""
    result = _result_v074({
        "toolcall-15": [
            {"id": "TC-01", "passed": True, "failure_mode": "passed", "detail": "ok",
             "raw_response": {"choices": [{"message": {"content": "ok"}}]},
             "verifier_trace": None,  # explicitly None — v0.5/v0.6 shape
             "conversation": []},
        ],
    })
    p = tmp_path / "run.json"
    p.write_text(json.dumps(result))

    rc = inspect_module.inspect_result(p, _args())
    assert rc == 0
    out = capsys.readouterr().out
    # Doesn't crash; surfaces a hint instead
    assert "(none — pre-v0.7.2 saved JSON or in-process verifier)" in out


def test_inspect_handles_old_response_field_name(tmp_path, capsys):
    """Codex review #6: pre-v0.7 used `response` not `raw_response`."""
    result = _result_v074({
        "toolcall-15": [
            {"id": "TC-OLD", "passed": False, "failure_mode": "wrong_answer", "detail": "x",
             "response": {"choices": [{"message": {"content": "old shape"}}]},
             "verifier_trace": None, "conversation": []},
        ],
    })
    p = tmp_path / "run.json"
    p.write_text(json.dumps(result))

    rc = inspect_module.inspect_result(p, _args(scenario="TC-OLD"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "old shape" in out


def test_inspect_format_json(tmp_path, capsys):
    result = _result_v074({
        "toolcall-15": [
            {"id": "TC-01", "passed": True, "failure_mode": "passed", "detail": "ok",
             "raw_response": {}, "verifier_trace": None, "conversation": []},
        ],
    })
    p = tmp_path / "run.json"
    p.write_text(json.dumps(result))

    rc = inspect_module.inspect_result(p, _args(format="json"))
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert parsed[0]["id"] == "TC-01"
    assert parsed[0]["pack_id"] == "toolcall-15"


def test_inspect_no_match_returns_2(tmp_path, capsys):
    result = _result_v074({
        "toolcall-15": [
            {"id": "TC-01", "passed": True, "failure_mode": "passed", "detail": "ok",
             "raw_response": {}, "verifier_trace": None, "conversation": []},
        ],
    })
    p = tmp_path / "run.json"
    p.write_text(json.dumps(result))

    rc = inspect_module.inspect_result(p, _args(scenario="DOES-NOT-EXIST"))
    assert rc == 2  # no match → exit 2


def test_inspect_missing_file_returns_1(capsys):
    rc = inspect_module.inspect_result("/tmp/definitely-does-not-exist.json", _args())
    assert rc == 1


# ============================================================================
# v0.8.1 Phase B.5 — --diff side-by-side, --logs DIR integration
# ============================================================================


def test_inspect_diff_renders_regression_flip(tmp_path, capsys):
    """v0.8.1: --diff against a previous run flags PASS→fail as regression."""
    prev = _result_v074({
        "hermesagent-20": [
            {"id": "HA-01", "passed": True, "failure_mode": "passed", "detail": "ok prev",
             "raw_response": {"choices": [{"message": {"content": "good"}}]},
             "verifier_trace": {"trace": {"upstream_score": 100}}, "conversation": []},
        ],
    })
    cur = _result_v074({
        "hermesagent-20": [
            {"id": "HA-01", "passed": False, "failure_mode": "verifier_fail", "detail": "miss now",
             "raw_response": {"choices": [{"message": {"content": "bad"}}]},
             "verifier_trace": {"trace": {"upstream_score": 25}}, "conversation": []},
        ],
    })
    pp = tmp_path / "prev.json"
    cp = tmp_path / "cur.json"
    pp.write_text(json.dumps(prev))
    cp.write_text(json.dumps(cur))

    rc = inspect_module.inspect_result(cp, _args(diff=str(pp), scenario="HA-01"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "DIFF" in out
    assert "REGRESSION" in out
    assert "previous=100" in out and "current=25" in out


def test_inspect_diff_renders_fix(tmp_path, capsys):
    prev = _result_v074({
        "hermesagent-20": [
            {"id": "HA-03", "passed": False, "failure_mode": "verifier_fail", "detail": "miss",
             "raw_response": {}, "verifier_trace": None, "conversation": []},
        ],
    })
    cur = _result_v074({
        "hermesagent-20": [
            {"id": "HA-03", "passed": True, "failure_mode": "passed", "detail": "win",
             "raw_response": {}, "verifier_trace": None, "conversation": []},
        ],
    })
    pp = tmp_path / "prev.json"
    cp = tmp_path / "cur.json"
    pp.write_text(json.dumps(prev))
    cp.write_text(json.dumps(cur))

    rc = inspect_module.inspect_result(cp, _args(diff=str(pp), scenario="HA-03"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "FIX" in out


def test_inspect_diff_handles_new_scenario_not_in_previous(tmp_path, capsys):
    """current has a scenario the previous run didn't."""
    prev = _result_v074({"hermesagent-20": [
        {"id": "HA-01", "passed": True, "failure_mode": "passed", "detail": "x",
         "raw_response": {}, "verifier_trace": None, "conversation": []},
    ]})
    cur = _result_v074({"hermesagent-20": [
        {"id": "HA-01", "passed": True, "failure_mode": "passed", "detail": "x",
         "raw_response": {}, "verifier_trace": None, "conversation": []},
        {"id": "HA-NEW", "passed": True, "failure_mode": "passed", "detail": "fresh",
         "raw_response": {}, "verifier_trace": None, "conversation": []},
    ]})
    pp = tmp_path / "prev.json"
    cp = tmp_path / "cur.json"
    pp.write_text(json.dumps(prev))
    cp.write_text(json.dumps(cur))

    rc = inspect_module.inspect_result(cp, _args(diff=str(pp), scenario="HA-NEW"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "scenario is NEW" in out


def test_inspect_diff_missing_file_returns_1(tmp_path, capsys):
    cur = _result_v074({"toolcall-15": [
        {"id": "TC-01", "passed": True, "failure_mode": "passed", "detail": "x",
         "raw_response": {}, "verifier_trace": None, "conversation": []},
    ]})
    cp = tmp_path / "cur.json"
    cp.write_text(json.dumps(cur))

    rc = inspect_module.inspect_result(cp, _args(diff="/tmp/nope-does-not-exist.json"))
    assert rc == 1


def test_inspect_diff_keys_by_pack_and_scenario_id(tmp_path, capsys):
    """Same scenario id in different packs → tracked separately (Codex review #1
    pattern from delta, applied here too)."""
    prev = _result_v074({
        "toolcall-15": [{"id": "X-01", "passed": True, "failure_mode": "passed", "detail": "tc",
                        "raw_response": {}, "verifier_trace": None, "conversation": []}],
        "instructfollow-15": [{"id": "X-01", "passed": False, "failure_mode": "wrong_answer", "detail": "if",
                              "raw_response": {}, "verifier_trace": None, "conversation": []}],
    })
    cur = _result_v074({
        "toolcall-15": [{"id": "X-01", "passed": True, "failure_mode": "passed", "detail": "tc",
                        "raw_response": {}, "verifier_trace": None, "conversation": []}],  # stable pass
        "instructfollow-15": [{"id": "X-01", "passed": True, "failure_mode": "passed", "detail": "if-fix",
                              "raw_response": {}, "verifier_trace": None, "conversation": []}],  # FIX
    })
    pp = tmp_path / "prev.json"
    cp = tmp_path / "cur.json"
    pp.write_text(json.dumps(prev))
    cp.write_text(json.dumps(cur))

    rc = inspect_module.inspect_result(cp, _args(diff=str(pp), pack="instructfollow-15"))
    assert rc == 0
    out = capsys.readouterr().out
    # Filtered to instructfollow-15 only; toolcall-15 row should NOT appear
    assert "instructfollow-15 :: X-01" in out
    assert "toolcall-15 :: X-01" not in out
    assert "FIX" in out  # instructfollow regression→fix flip
    # Header counts only the matched scenario (1)
    assert "matched: 1 scenario(s)" in out


def test_inspect_logs_renders_log_tail(tmp_path, capsys):
    """v0.8.1: --logs DIR resolves and tails sandbox-<pack>.log."""
    cur = _result_v074({
        "cli-40": [
            {"id": "CLI-01", "passed": False, "failure_mode": "verifier_fail", "detail": "fail",
             "raw_response": {},
             "verifier_trace": {"sandbox_log_file": "sandbox-cli-40.log"},
             "conversation": []},
        ],
    })
    cp = tmp_path / "cur.json"
    cp.write_text(json.dumps(cur))
    logs_dir = tmp_path / "sandbox-logs"
    logs_dir.mkdir()
    log_content = "[sandbox] hello\n[sandbox] world\nERROR: something broke\n"
    (logs_dir / "sandbox-cli-40.log").write_text(log_content)

    rc = inspect_module.inspect_result(cp, _args(scenario="CLI-01", logs=str(logs_dir)))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Sandbox log:" in out
    assert "something broke" in out


def test_inspect_logs_falls_back_to_pack_filename_for_pre_v081(tmp_path, capsys):
    """v0.7.2-v0.8.0 saved JSONs lack `verifier_trace.sandbox_log_file`.
    Inspect should fall back to <DIR>/sandbox-<pack_id>.log."""
    cur = _result_v074({
        "bugfind-15": [
            {"id": "BF-01", "passed": False, "failure_mode": "verifier_fail", "detail": "x",
             "raw_response": {}, "verifier_trace": None,  # ← no per-scenario field
             "conversation": []},
        ],
    })
    cp = tmp_path / "cur.json"
    cp.write_text(json.dumps(cur))
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "sandbox-bugfind-15.log").write_text("[bugfind] log content\n")

    rc = inspect_module.inspect_result(cp, _args(scenario="BF-01", logs=str(logs_dir)))
    assert rc == 0
    out = capsys.readouterr().out
    assert "[bugfind] log content" in out


def test_inspect_logs_missing_log_file_renders_hint(tmp_path, capsys):
    """When --logs is set but no log file resolves, surface a clear hint."""
    cur = _result_v074({
        "hermesagent-20": [
            {"id": "HA-01", "passed": True, "failure_mode": "passed", "detail": "x",
             "raw_response": {}, "verifier_trace": None, "conversation": []},
        ],
    })
    cp = tmp_path / "cur.json"
    cp.write_text(json.dumps(cur))
    logs_dir = tmp_path / "empty-logs"
    logs_dir.mkdir()  # empty dir

    rc = inspect_module.inspect_result(cp, _args(scenario="HA-01", logs=str(logs_dir)))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Sandbox log: not found" in out


def test_inspect_logs_invalid_dir_returns_1(tmp_path, capsys):
    cur = _result_v074({"hermesagent-20": [{"id": "HA-01", "passed": True,
        "failure_mode": "passed", "detail": "x", "raw_response": {},
        "verifier_trace": None, "conversation": []}]})
    cp = tmp_path / "cur.json"
    cp.write_text(json.dumps(cur))

    rc = inspect_module.inspect_result(cp, _args(logs="/tmp/definitely-not-a-dir-xyz"))
    assert rc == 1


# ============================================================================
# v0.8.1 Phase A — runner injects sandbox_log_file when --sandbox-log-dir set
# ============================================================================


def test_runner_injects_sandbox_log_file_when_log_dir_set():
    """Runner.sandbox_log_dir set → sandbox results gain
    verifier_trace.sandbox_log_file = sandbox-<pack_id>.log."""
    from benchlocal_cli.runner import Runner
    from benchlocal_cli.types import ScenarioResult

    runner = Runner(endpoint="http://localhost", model="x", sandbox_log_dir="/tmp/xx")
    result = ScenarioResult(
        scenario_id="HA-01", passed=True, failure_mode="passed", detail="x",
        verifier_trace={"upstream_status": "pass"},
    )
    out = runner._inject_sandbox_log_file(result, "hermesagent-20")
    assert out.verifier_trace["sandbox_log_file"] == "sandbox-hermesagent-20.log"
    # Existing fields preserved
    assert out.verifier_trace["upstream_status"] == "pass"


def test_runner_does_not_inject_sandbox_log_file_without_log_dir():
    from benchlocal_cli.runner import Runner
    from benchlocal_cli.types import ScenarioResult

    runner = Runner(endpoint="http://localhost", model="x")  # no sandbox_log_dir
    result = ScenarioResult(scenario_id="HA-01", passed=True, failure_mode="passed", detail="x")
    out = runner._inject_sandbox_log_file(result, "hermesagent-20")
    # No mutation when log dir is unset
    assert out.verifier_trace is None or "sandbox_log_file" not in (out.verifier_trace or {})
