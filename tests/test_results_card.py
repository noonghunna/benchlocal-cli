from __future__ import annotations

import json

from benchlocal_cli.cli import _results_card_markdown, main
from benchlocal_cli.types import PackResult, RunResult, ScenarioResult, ScenarioRun


def _scenario(
    scenario_id: str,
    *,
    passed: bool,
    latency: float,
    repeat_index: int = 1,
) -> ScenarioRun:
    failure_mode = "passed" if passed else "wrong_answer"
    return ScenarioRun(
        id=scenario_id,
        result=ScenarioResult(
            scenario_id=scenario_id,
            passed=passed,
            failure_mode=failure_mode,
            detail="correct" if passed else "expected 42",
            latency_seconds=latency,
        ),
        raw_scenario={"id": scenario_id},
        raw_response={"choices": []},
        request={},
        sampling_params={"temperature": 0},
        status_code=200,
        repeat_index=repeat_index,
    )


def _pack(
    pack_id: str,
    *,
    passed: int,
    total: int,
    scenarios: list[ScenarioRun],
    variance: dict | None,
    latency: tuple[float, float],
) -> PackResult:
    return PackResult(
        pack_id=pack_id,
        version="1.0.0",
        upstream_commit="abc123",
        scenario_count=1,
        passed=passed,
        total=total,
        score=passed / total,
        latency={"p50": latency[0], "p95": latency[1]},
        scenarios=scenarios,
        variance=variance,
    )


def _result(*, repeat: int = 3) -> RunResult:
    alpha_runs = [
        _scenario("A-01", passed=True, latency=1.0, repeat_index=1),
        _scenario("A-01", passed=False, latency=2.0, repeat_index=2),
        _scenario("A-01", passed=True, latency=3.0, repeat_index=3),
    ]
    beta_runs = [
        _scenario("B-01", passed=True, latency=2.0, repeat_index=index)
        for index in range(1, 4)
    ]
    packs = [
        _pack(
            "alpha-1",
            passed=2,
            total=3,
            scenarios=alpha_runs,
            variance={"repeat": 3, "mean": 2 / 3, "std": 0.2357, "cv": 0.3536},
            latency=(2.0, 2.9),
        ),
        _pack(
            "beta-1",
            passed=3,
            total=3,
            scenarios=beta_runs,
            variance={"repeat": 3, "mean": 1.0, "std": 0.0, "cv": 0.0},
            latency=(2.0, 2.0),
        ),
    ]
    return RunResult(
        schema_version="1",
        runner_version="0.9.9",
        endpoint="mock",
        model="mock-model",
        mode="custom",
        started_at="2026-07-30T00:00:00Z",
        finished_at="2026-07-30T00:01:00Z",
        packs=packs,
        totals={"passed": 5, "total": 6, "score": 5 / 6},
        thinking_enabled=False,
        thinking_mode="force-off",
        repeat=repeat,
    )


def _mock_response(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"completion_tokens": 3},
    }


def _mock_run_args(tmp_path) -> tuple[list[str], object, object]:
    mock_path = tmp_path / "mock.json"
    result_path = tmp_path / "result.json"
    report_path = tmp_path / "results.md"
    mock_path.write_text(
        json.dumps(
            {"SO-01": _mock_response('{"title":"The Great Gatsby","year":1925}')}
        )
    )
    return (
        [
            "run",
            "--endpoint",
            "mock",
            "--model",
            "mock",
            "--measured-tps",
            "100",
            "--mock-responses-from-json",
            str(mock_path),
            "--scenario",
            "structoutput-15/SO-01",
            "--save-json",
            str(result_path),
            "--report",
            "md",
            "--report-out",
            str(report_path),
        ],
        result_path,
        report_path,
    )


def test_results_card_repeat_three_has_stable_headline_shape_and_json_fields():
    result = _result()
    rendered = _results_card_markdown(result)
    headline = rendered.split("\n\n<details>", 1)[0]

    assert headline == """## Quality bench, thinking off, benchlocal-cli v0.9.9, repeat = 3

Pack | Pass / Total | Score | Std | CV | p50 latency | p95 latency | Status
---|---:|---:|---:|---:|---:|---:|---
alpha-1 (v1.0.0) | 2 / 3 | 67% | 23.6% | 0.35 | 2.00s | 2.90s | ok
beta-1 (v1.0.0) | 3 / 3 | 100% | 0.0% | 0.00 | 2.00s | 2.00s | ok

TOTAL | 5 / 6 | 83% |  |  |  |  |

Equivalent to: 125/150"""
    payload = result.to_dict()
    assert payload["repeat"] == 3
    assert payload["equivalent_score_150"] == 125


def test_results_card_repeat_one_uses_em_dashes_for_variance():
    run = _scenario("A-01", passed=True, latency=1.25)
    pack = _pack(
        "alpha-1",
        passed=1,
        total=1,
        scenarios=[run],
        variance=None,
        latency=(1.25, 1.25),
    )
    result = _result(repeat=1)
    result.packs = [pack]
    result.totals = {"passed": 1, "total": 1, "score": 1.0}

    rendered = _results_card_markdown(result)

    assert "alpha-1 (v1.0.0) | 1 / 1 | 100% | — | — | 1.25s | 1.25s | ok" in rendered
    assert "Equivalent to: 150/150" in rendered


def test_results_card_raw_block_reconstructs_all_items_and_failure_details():
    rendered = _results_card_markdown(_result())

    assert "<details>\n<summary>Raw data</summary>" in rendered
    assert "  [1/3] A-01 ✓ passed (1.0s)" in rendered
    assert "  [2/3] A-01 ✗ wrong_answer (2.0s)" in rendered
    assert "Failure breakdown:" in rendered
    assert "alpha-1 A-01: wrong_answer [fail] (expected 42)" in rendered
    raw = rendered.split("<summary>Raw data</summary>", 1)[1]
    assert raw.index("=== benchlocal-cli") < raw.index("[1/3] A-01")
    assert rendered.endswith("</details>")


def test_cli_report_stdout_matches_file_and_saves_aggregate_fields(tmp_path, capsys):
    args, result_path, report_path = _mock_run_args(tmp_path)

    assert main(args) == 0

    stdout = capsys.readouterr().out
    assert stdout == report_path.read_text()
    assert stdout.startswith("## Quality bench, thinking off(pack-defaults),")
    assert "repeat = 1" in stdout
    assert " | — | — | " in stdout
    payload = json.loads(result_path.read_text())
    assert payload["repeat"] == 1
    assert payload["equivalent_score_150"] == 150


def test_cli_can_keep_json_stdout_when_report_has_file_target(tmp_path, capsys):
    args, _result_path, report_path = _mock_run_args(tmp_path)
    args.extend(["--output", "json"])

    assert main(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["repeat"] == 1
    assert payload["equivalent_score_150"] == 150
    assert report_path.read_text().startswith("## Quality bench")


def test_cli_rejects_ambiguous_report_output_combinations(tmp_path, capsys):
    assert main(["run", "--report-out", str(tmp_path / "report.md")]) == 1
    assert "--report-out requires --report md" in capsys.readouterr().err

    assert main(["run", "--report", "md", "--output", "json"]) == 1
    assert "--output json with --report requires --report-out" in capsys.readouterr().err
