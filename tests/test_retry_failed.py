from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchlocal_cli.cli import _parser, main
from benchlocal_cli.retry_failed import failed_selection, load_retry_context
from benchlocal_cli.runner import Runner


def _response(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"completion_tokens": 3},
    }


def _baseline(*, all_passed: bool = False) -> dict:
    return {
        "schema_version": "1",
        "runner_version": "0.9.8",
        "endpoint": "mock",
        "model": "mock",
        "mode": "full",
        "started_at": "2026-07-22T00:00:00+00:00",
        "finished_at": "2026-07-22T00:01:00+00:00",
        "thinking_enabled": False,
        "thinking_mode": "force-off",
        "totals": {
            "passed": 2 if all_passed else 0,
            "total": 2,
            "score": 1.0 if all_passed else 0.0,
        },
        "packs": [
            {
                "pack_id": "reasonmath-15",
                "scenarios": [
                    {
                        "id": "RM-04",
                        "passed": all_passed,
                        "failure_mode": "passed" if all_passed else "wrong_answer",
                    }
                ],
            },
            {
                "pack_id": "structoutput-15",
                "scenarios": [
                    {
                        "id": "SO-01",
                        "passed": all_passed,
                        "failure_mode": "passed" if all_passed else "verifier_fail",
                    }
                ],
            },
        ],
    }


def test_parser_defaults_retry_failed_to_three_and_repeat_to_zero():
    parser = _parser()

    absent = parser.parse_args(["run"])
    bare = parser.parse_args(["run", "--retry-failed"])

    assert absent.repeat == 0
    assert absent.retry_failed is None
    assert bare.retry_failed == 3


def test_failed_selection_uses_pass_at_one_not_later_repeat_arms():
    result = {
        "packs": [
            {
                "pack_id": "reasonmath-15",
                "scenarios": [
                    {"id": "RM-04", "repeat_index": 1, "passed": False},
                    {"id": "RM-04", "repeat_index": 2, "passed": True},
                    {"id": "RM-05", "repeat_index": 1, "passed": True},
                    {"id": "RM-05", "repeat_index": 2, "passed": False},
                ],
            }
        ]
    }

    assert failed_selection(result) == ["reasonmath-15/RM-04"]


def test_retry_failed_runs_only_failures_and_reports_consistency(tmp_path, capsys):
    previous = tmp_path / "baseline.json"
    previous.write_text(json.dumps(_baseline()))
    mock = tmp_path / "mock.json"
    mock.write_text(
        json.dumps(
            {
                "RM-04": _response("ANSWER: not-correct"),
                "SO-01": _response('{"title":"The Great Gatsby","year":1925}'),
            }
        )
    )
    result_path = tmp_path / "retry.json"

    exit_code = main(
        [
            "run",
            "--retry-failed",
            "--previous-result",
            str(previous),
            "--measured-tps",
            "100",
            "--mock-responses-from-json",
            str(mock),
            "--save-json",
            str(result_path),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    result = json.loads(result_path.read_text())
    assert result["selection"] == [
        "reasonmath-15/RM-04",
        "structoutput-15/SO-01",
    ]
    assert result["totals"] == {"passed": 3, "total": 6, "score": 0.5}
    assert result["retry_failed"]["baseline_totals"] == {
        "passed": 0,
        "total": 2,
        "score": 0.0,
    }
    assert result["retry_failed"]["systematic"] == 1
    assert result["retry_failed"]["flaky"] == 1
    assert result["retry_failed"]["scenarios"] == [
        {
            "pack_id": "reasonmath-15",
            "scenario_id": "RM-04",
            "retry_passed": 0,
            "retry_total": 3,
            "classification": "systematic",
        },
        {
            "pack_id": "structoutput-15",
            "scenario_id": "SO-01",
            "retry_passed": 3,
            "retry_total": 3,
            "classification": "flaky",
        },
    ]
    assert "Baseline pass@1 (official): 0 / 2 (0%)" in output
    assert "RETRY SAMPLE | 3 / 6 | 50%" in output
    assert "reasonmath-15/RM-04 | 0 / 3 | systematic" in output
    assert "structoutput-15/SO-01 | 3 / 3 | flaky" in output
    assert "Failure breakdown:" not in output


def test_retry_failed_intersects_with_pack_selector(tmp_path):
    previous = tmp_path / "baseline.json"
    previous.write_text(json.dumps(_baseline()))
    mock = tmp_path / "mock.json"
    mock.write_text(json.dumps({"SO-01": _response('{"title":"The Great Gatsby","year":1925}')}))
    result_path = tmp_path / "retry.json"

    assert main(
        [
            "run",
            "--retry-failed",
            "2",
            "--previous-result",
            str(previous),
            "--pack",
            "structoutput-15",
            "--measured-tps",
            "100",
            "--mock-responses-from-json",
            str(mock),
            "--save-json",
            str(result_path),
            "--output",
            "json",
        ]
    ) == 0

    result = json.loads(result_path.read_text())
    assert result["selection"] == ["structoutput-15/SO-01"]
    assert result["totals"]["total"] == 2
    assert result["retry_failed"]["failed_scenario_count"] == 1


def test_retry_failed_completed_baseline_is_clear_noop(tmp_path, capsys):
    previous = tmp_path / "baseline.json"
    previous.write_text(json.dumps(_baseline(all_passed=True)))

    assert main(["run", "--retry-failed", "--previous-result", str(previous)]) == 0
    assert "no failed pass@1 scenarios" in capsys.readouterr().out


def test_retry_failed_rejects_canonical_gate_and_source_overwrite(tmp_path, capsys):
    previous = tmp_path / "baseline.json"
    previous.write_text(json.dumps(_baseline()))

    assert main(
        [
            "run",
            "--retry-failed",
            "--previous-result",
            str(previous),
            "--exit-on-regression",
        ]
    ) == 1
    assert "diagnostic" in capsys.readouterr().err

    assert main(
        [
            "run",
            "--retry-failed",
            "--previous-result",
            str(previous),
            "--save-json",
            str(previous),
        ]
    ) == 1
    assert "must not overwrite" in capsys.readouterr().err


def test_retry_failed_completed_sidecar_keeps_diagnostics(
    tmp_path, monkeypatch, capsys
):
    previous = tmp_path / "baseline.json"
    previous.write_text(json.dumps(_baseline()))
    mock = tmp_path / "mock.json"
    mock.write_text(
        json.dumps(
            {
                "RM-04": _response("ANSWER: not-correct"),
                "SO-01": _response('{"title":"The Great Gatsby","year":1925}'),
            }
        )
    )
    result_path = tmp_path / "retry.json"
    original_run = Runner.run

    def crash_after_last_retry(self, *run_args, **run_kwargs):
        original_run(self, *run_args, **run_kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(Runner, "run", crash_after_last_retry)
    with pytest.raises(KeyboardInterrupt):
        main(
            [
                "run",
                "--retry-failed",
                "2",
                "--previous-result",
                str(previous),
                "--measured-tps",
                "100",
                "--mock-responses-from-json",
                str(mock),
                "--incremental",
                "--save-json",
                str(result_path),
            ]
        )
    monkeypatch.setattr(Runner, "run", original_run)
    capsys.readouterr()

    sidecar = Path(f"{result_path}.partial.jsonl")
    assert sidecar.is_file()
    assert len(sidecar.read_text().splitlines()) == 4

    assert main(["run", "--resume", str(sidecar)]) == 0
    assert not sidecar.exists()
    result = json.loads(result_path.read_text())
    assert result["retry_failed"]["attempts_per_scenario"] == 2
    assert result["retry_failed"]["systematic"] == 1
    assert result["retry_failed"]["flaky"] == 1


def test_retry_failed_rejects_an_already_repeated_baseline(tmp_path):
    baseline = _baseline()
    baseline["packs"][0]["scenarios"].append(
        {
            "id": "RM-04",
            "repeat_index": 2,
            "passed": True,
            "failure_mode": "passed",
        }
    )
    previous = tmp_path / "repeated.json"
    previous.write_text(json.dumps(baseline))

    with pytest.raises(ValueError, match="requires a pass@1 result"):
        load_retry_context(previous, 3)
