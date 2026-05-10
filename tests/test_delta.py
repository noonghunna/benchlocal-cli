"""Tests for benchlocal_cli.delta — --previous-result classification."""

from __future__ import annotations

import json

import pytest

from benchlocal_cli import delta as delta_module


def _run_dict(scenarios_by_pack, schema_version="2"):
    """Build a minimal RunResult.to_dict() shape for tests."""
    return {
        "schema_version": schema_version,
        "packs": [
            {
                "pack_id": pack_id,
                "scenarios": [
                    {"id": sid, "passed": passed} for sid, passed in scenarios
                ],
            }
            for pack_id, scenarios in scenarios_by_pack.items()
        ],
    }


def test_classify_all_stable_pass(tmp_path):
    prev = _run_dict({"toolcall-15": [("TC-01", True), ("TC-02", True)]})
    cur = _run_dict({"toolcall-15": [("TC-01", True), ("TC-02", True)]})
    prev_path = tmp_path / "prev.json"
    prev_path.write_text(json.dumps(prev))

    result = delta_module.classify(cur, prev_path)
    assert result.total_regressions == 0
    assert result.total_fixes == 0
    assert result.total_stable_pass == 2
    assert result.total_stable_fail == 0
    assert result.schema_version_match is True


def test_classify_regression_detected(tmp_path):
    prev = _run_dict({"toolcall-15": [("TC-01", True), ("TC-02", True)]})
    cur = _run_dict({"toolcall-15": [("TC-01", True), ("TC-02", False)]})
    prev_path = tmp_path / "prev.json"
    prev_path.write_text(json.dumps(prev))

    result = delta_module.classify(cur, prev_path)
    assert result.total_regressions == 1
    assert result.by_pack[0].regressions_list == ["TC-02"]
    assert result.total_stable_pass == 1
    assert delta_module.has_regressions(result) is True


def test_classify_fix_detected(tmp_path):
    prev = _run_dict({"hermesagent-20": [("HA-01", False), ("HA-02", True)]})
    cur = _run_dict({"hermesagent-20": [("HA-01", True), ("HA-02", True)]})
    prev_path = tmp_path / "prev.json"
    prev_path.write_text(json.dumps(prev))

    result = delta_module.classify(cur, prev_path)
    assert result.total_fixes == 1
    assert result.by_pack[0].fixes_list == ["HA-01"]
    assert result.total_regressions == 0


def test_classify_new_and_dropped(tmp_path):
    prev = _run_dict({"toolcall-15": [("TC-01", True)]})
    cur = _run_dict({"toolcall-15": [("TC-01", True), ("TC-NEW", True)]})
    prev_path = tmp_path / "prev.json"
    prev_path.write_text(json.dumps(prev))

    result = delta_module.classify(cur, prev_path)
    assert result.total_new == 1
    assert result.total_dropped == 0

    # Now reverse: previous has scenario that's been dropped
    prev2 = _run_dict({"toolcall-15": [("TC-01", True), ("TC-DROPPED", True)]})
    cur2 = _run_dict({"toolcall-15": [("TC-01", True)]})
    prev_path2 = tmp_path / "prev2.json"
    prev_path2.write_text(json.dumps(prev2))
    result2 = delta_module.classify(cur2, prev_path2)
    assert result2.total_new == 0
    assert result2.total_dropped == 1


def test_classify_keys_by_pack_and_scenario_id(tmp_path):
    """Codex review #1: keying by (pack_id, scenario_id), not bare id.
    Same scenario id in different packs should be tracked separately."""
    prev = _run_dict({
        "toolcall-15": [("X-01", True)],
        "instructfollow-15": [("X-01", False)],
    })
    cur = _run_dict({
        "toolcall-15": [("X-01", True)],     # stable_pass
        "instructfollow-15": [("X-01", True)], # fix
    })
    prev_path = tmp_path / "prev.json"
    prev_path.write_text(json.dumps(prev))

    result = delta_module.classify(cur, prev_path)
    assert result.total_fixes == 1
    assert result.total_stable_pass == 1
    by_pack = {d.pack_id: d for d in result.by_pack}
    assert by_pack["instructfollow-15"].fixes == 1
    assert by_pack["toolcall-15"].fixes == 0
    assert by_pack["toolcall-15"].stable_pass == 1


def test_classify_repeat_aggregates_to_pass_rate(tmp_path):
    """Codex review #2: --repeat N>1 produces multiple runs per (pack, id).
    Aggregate via pass-rate ≥ 0.5 threshold (default)."""
    # Same scenario, 3 repeats: 2 pass, 1 fail → pass-rate 2/3 = 0.67 → "passed"
    prev = {
        "schema_version": "2",
        "packs": [{
            "pack_id": "toolcall-15",
            "scenarios": [
                {"id": "TC-01", "passed": True},
                {"id": "TC-01", "passed": True},
                {"id": "TC-01", "passed": False},
            ],
        }],
    }
    # Current: 1 pass, 2 fail → 0.33 → "failed" → REGRESSION
    cur = {
        "schema_version": "2",
        "packs": [{
            "pack_id": "toolcall-15",
            "scenarios": [
                {"id": "TC-01", "passed": True},
                {"id": "TC-01", "passed": False},
                {"id": "TC-01", "passed": False},
            ],
        }],
    }
    prev_path = tmp_path / "prev.json"
    prev_path.write_text(json.dumps(prev))

    result = delta_module.classify(cur, prev_path)
    assert result.total_regressions == 1
    assert result.by_pack[0].regressions_list == ["TC-01"]


def test_classify_warns_on_schema_version_mismatch(tmp_path):
    prev = _run_dict({"toolcall-15": [("TC-01", True)]}, schema_version="1")
    cur = _run_dict({"toolcall-15": [("TC-01", True)]}, schema_version="2")
    prev_path = tmp_path / "prev.json"
    prev_path.write_text(json.dumps(prev))

    result = delta_module.classify(cur, prev_path)
    assert result.schema_version_match is False
    assert any("schema_version mismatch" in w for w in result.warnings)
    # Comparison still proceeds
    assert result.total_stable_pass == 1


def test_classify_missing_previous_raises():
    with pytest.raises(FileNotFoundError):
        delta_module.classify({"packs": []}, "/tmp/nonexistent-prev-result-xyz.json")


def test_has_regressions_with_none():
    assert delta_module.has_regressions(None) is False
