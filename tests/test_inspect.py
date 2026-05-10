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
