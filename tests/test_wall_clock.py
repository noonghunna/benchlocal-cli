"""#146: wall-clock duration per pack and for the run.

p50/p95 are per-scenario latency percentiles and do not add up to elapsed time
(they exclude inline retries, sandbox setup/teardown, verification and
inter-scenario overhead). The run now records `duration_s` at both levels and
the markdown adds an additive block after `Failure breakdown:` — omitted when
the result carries no duration, which is what keeps the default markdown
byte-stable for pinned downstream parsers.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from benchlocal_cli.cli import _format_duration, _markdown, _wall_clock_lines, main
from benchlocal_cli.persistence import ResumeState, _build_result, merge_resume
from benchlocal_cli.rescore import _recompute_pack
from benchlocal_cli.runner import Runner, _duration_between
from benchlocal_cli.types import PackResult, RunResult, ScenarioResult, ScenarioRun

# ------------------------------------------------------------------ helpers


def test_format_duration_is_compact_and_monotone():
    assert _format_duration(0.42) == "0.4s"
    assert _format_duration(59.94) == "59.9s"
    assert _format_duration(90) == "1m30s"
    assert _format_duration(3671) == "1h01m11s"
    assert _format_duration(4 * 3600 + 12 * 60) == "4h12m00s"
    assert _format_duration(-3) == "0.0s"


def test_duration_between_parses_z_and_offset_stamps_and_never_goes_negative():
    assert _duration_between("2026-09-22T10:00:00Z", "2026-09-22T13:58:42Z") == 14322.0
    assert _duration_between("2026-09-22T10:00:00+00:00", "2026-09-22T10:00:01.500000Z") == 1.5
    assert _duration_between("2026-09-22T10:00:10Z", "2026-09-22T10:00:00Z") == 0.0
    assert _duration_between(None, "2026-09-22T10:00:00Z") is None
    assert _duration_between("garbage", "2026-09-22T10:00:00Z") is None


# ------------------------------------------------------------------ fixtures


def _run(scenario_id: str, latency: float, *, passed: bool = True, retries: tuple[float, ...] = ()) -> ScenarioRun:
    run = ScenarioRun(
        id=scenario_id,
        result=ScenarioResult(
            scenario_id, passed, "passed" if passed else "verifier_fail", "x", latency_seconds=latency
        ),
        raw_scenario={}, raw_response={}, request={}, sampling_params={}, status_code=200,
    )
    run.retry_attempts = [{"latency_seconds": value, "passed": False} for value in retries]
    run.attempt_count = 1 + len(retries)
    return run


def _pack(pack_id: str, runs: list[ScenarioRun], duration_s: float | None, *, skipped: bool = False) -> PackResult:
    passed = sum(1 for run in runs if run.result.passed)
    return PackResult(
        pack_id=pack_id, version="1.0.0", upstream_commit="abc", scenario_count=len(runs),
        passed=passed, total=len(runs), score=(passed / len(runs)) if runs else 0.0,
        latency={"p50": 2.0, "p95": 3.0, "mean": 2.5}, scenarios=runs, skipped=skipped,
        status="skipped" if skipped else "ok", duration_s=duration_s,
    )


def _result(packs: list[PackResult], duration_s: float | None, warnings: list[str] | None = None) -> RunResult:
    total = sum(p.total for p in packs)
    passed = sum(p.passed for p in packs)
    return RunResult(
        schema_version="1", runner_version="0.9.9", endpoint="http://x", model="m", mode="custom",
        started_at="2026-09-22T00:00:00Z", finished_at="2026-09-22T00:10:00Z", packs=packs,
        totals={"passed": passed, "total": total, "score": passed / total if total else 0.0},
        warnings=warnings or [], duration_s=duration_s,
    )


def _two_packs() -> list[PackResult]:
    alpha = _pack("alpha-1", [_run("A-01", 10.0), _run("A-02", 20.0, passed=False, retries=(5.0, 6.0))], 60.0)
    beta = _pack("beta-1", [_run("B-01", 100.0)], 130.0)
    return [alpha, beta]


# Captured from `_markdown` at 47d1d66 (pre-change) for `_two_packs()` with no
# durations, warnings=["w1"]. NOT regenerated from the new code.
_GOLDEN = """=== benchlocal-cli --custom  (endpoint: http://x, model: m, thinking=off(pack-defaults), 2026-09-22T00:00:00Z) ===

Pack | Pass / Total | Score | p50 latency | p95 latency | Status
---|---:|---:|---:|---:|---
alpha-1 (v1.0.0) | 1 / 2 | 50% | 2.00s | 3.00s | ok
beta-1 (v1.0.0) | 1 / 1 | 100% | 2.00s | 3.00s | ok

TOTAL | 2 / 3 | 67% |  |  |

Failure breakdown:
- alpha-1 A-02: verifier_fail [fail] (x)

Warnings:
- w1"""


def test_markdown_is_byte_stable_without_durations():
    packs = _two_packs()
    for pack in packs:
        pack.duration_s = None
    result = _result(packs, None, warnings=["w1"])

    assert _markdown(result) == _GOLDEN
    assert "duration_s" not in result.to_dict()
    assert all("duration_s" not in pack for pack in result.to_dict()["packs"])


def test_markdown_adds_the_wall_clock_block_between_failure_breakdown_and_warnings():
    result = _result(_two_packs(), 600.0, warnings=["w1"])
    rendered = _markdown(result)

    expected_block = (
        "\nWall clock: 10m00s — packs 3m10s, scenario latency 2m10s, retries 11.0s, "
        "overhead 7m39s (sandbox setup/teardown, verification, inter-scenario)\n"
        "- alpha-1: 1m00s (latency 30.0s, retries 11.0s, overhead 19.0s)\n"
        "- beta-1: 2m10s (latency 1m40s, overhead 30.0s)"
    )
    golden_head, golden_tail = _GOLDEN.split("\n\nWarnings:")
    assert rendered == golden_head + "\n" + expected_block + "\n\nWarnings:" + golden_tail
    assert rendered.index("Failure breakdown:") < rendered.index("Wall clock:") < rendered.index("Warnings:")


def test_wall_clock_lines_skip_unmeasured_and_skipped_packs():
    measured = _pack("alpha-1", [_run("A-01", 10.0)], 12.0)
    rebuilt = _pack("beta-1", [_run("B-01", 5.0)], None)  # journal rebuild: not measured
    skipped = _pack("gamma-1", [], 0.01, skipped=True)
    lines = _wall_clock_lines(_result([measured, rebuilt, skipped], 100.0))

    assert lines[1] == (
        "Wall clock: 1m40s — packs 12.0s, scenario latency 15.0s, "
        "overhead 1m25s (sandbox setup/teardown, verification, inter-scenario)"
    )
    assert lines[2:] == ["- alpha-1: 12.0s (latency 10.0s, overhead 2.0s)"]


def test_json_carries_duration_at_both_levels():
    payload = _result(_two_packs(), 600.0).to_dict()
    assert payload["duration_s"] == 600.0
    assert [pack["duration_s"] for pack in payload["packs"]] == [60.0, 130.0]
    # totals is untouched: consumers compare it literally
    assert payload["totals"] == {"passed": 2, "total": 3, "score": 2 / 3}


# ------------------------------------------------------------------ live runner


def test_runner_measures_pack_wall_clock_and_run_duration(monkeypatch):
    import benchlocal_cli.runner as runner_module

    meta = {"pack_id": "test-pack", "version": "1.0.0", "upstream_commit": "abc123", "sampling_defaults": {"max_tokens": 32}}
    scenario = {"id": "T-01", "pack_id": "test-pack", "messages": [{"role": "user", "content": "test"}]}
    monkeypatch.setattr("benchlocal_cli.runner.load_pack", lambda _pack_id: (meta, [scenario]))
    runner = Runner(endpoint="mock", model="mock", retry_failures=0)

    def slow_scenario(_meta, scenario, *, repeat_index=1):
        time.sleep(0.05)  # real time inside the pack: the wall clock must cover it
        return _run(scenario["id"], 1.5)

    monkeypatch.setattr(runner, "run_scenario", slow_scenario)
    stamps = iter(["2026-09-22T00:00:00Z", "2026-09-22T00:00:12Z"])
    monkeypatch.setattr(runner_module, "_utc_now", lambda: next(stamps))

    result = runner.run(["test-pack"])

    assert result.packs[0].duration_s >= 0.05
    assert result.packs[0].duration_s < 5.0
    assert result.duration_s == 12.0  # started_at -> finished_at
    assert result.to_dict()["duration_s"] == 12.0


# ------------------------------------------------------------------ persistence


def _journal_config() -> dict:
    return {
        "target_selection": ["structoutput-15/SO-01"],
        "pack_ids": ["structoutput-15"],
        "started_at": "2026-09-22T00:00:00Z",
    }


def _saved_row(latency: float = 1.0) -> dict:
    return {
        "id": "SO-01", "passed": True, "failure_mode": "passed", "detail": "ok",
        "latency_seconds": latency, "raw_scenario": {}, "raw_response": {}, "request": {},
        "sampling_params": {}, "status_code": 200, "repeat_index": 1,
    }


def test_journal_rebuild_derives_run_duration_but_not_pack_duration():
    result = _build_result(_journal_config(), [("structoutput-15", _saved_row())], finished_at="2026-09-22T00:03:00Z")
    assert result.duration_s == 180.0
    assert result.packs[0].duration_s is None
    lines = _wall_clock_lines(result)
    assert lines[1].startswith("Wall clock: 3m00s — scenario latency 1.0s, overhead 2m59s")
    assert len(lines) == 2  # no per-pack line for an unmeasured pack


def test_journal_rebuild_accepts_carried_pack_durations():
    result = _build_result(
        _journal_config(), [("structoutput-15", _saved_row())],
        finished_at="2026-09-22T00:03:00Z", pack_durations={"structoutput-15": 42.0},
    )
    assert result.packs[0].duration_s == 42.0


def test_merge_resume_sums_a_packs_duration_across_sessions(tmp_path):
    previous = _result([_pack("structoutput-15", [], 30.0)], 30.0).to_dict()
    previous["packs"][0]["scenarios"] = [_saved_row(1.0)]
    state = ResumeState(
        source_path=tmp_path / "r.partial.jsonl", final_path=tmp_path / "r.json",
        sidecar_path=tmp_path / "r.partial.jsonl", config=_journal_config(),
        previous_result=previous, missing_selection=[], missing_by_pack={}, completed_repeats={},
    )
    fresh = _result([_pack("structoutput-15", [_run("SO-01", 2.0)], 12.0)], 12.0)
    fresh.finished_at = "2026-09-22T00:05:00Z"

    merged = merge_resume(state, fresh)

    assert merged.packs[0].duration_s == 42.0  # 30 s in session one + 12 s now
    assert merged.duration_s == 300.0  # original started_at to the new finished_at


def test_rescore_preserves_duration():
    pack = {"duration_s": 5.5, "scenarios": [_saved_row()]}
    _recompute_pack(pack)
    assert pack["duration_s"] == 5.5


# ------------------------------------------------------------------ CLI end to end


def test_cli_mock_run_reports_wall_clock(tmp_path: Path, capsys):
    mock_path = tmp_path / "mock.json"
    result_path = tmp_path / "result.json"
    mock_path.write_text(json.dumps({"SO-01": {
        "choices": [{"message": {"content": '{"title":"The Great Gatsby","year":1925}'}}],
        "usage": {"completion_tokens": 3},
    }}))
    rc = main([
        "run", "--endpoint", "mock", "--model", "mock", "--measured-tps", "100",
        "--mock-responses-from-json", str(mock_path), "--scenario", "structoutput-15/SO-01",
        "--save-json", str(result_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "\nWall clock: " in out
    assert "\n- structoutput-15: " in out
    saved = json.loads(result_path.read_text())
    assert isinstance(saved["duration_s"], float)
    assert isinstance(saved["packs"][0]["duration_s"], float)
    assert saved["packs"][0]["duration_s"] <= saved["duration_s"] + 1e-6 or saved["duration_s"] == 0.0
