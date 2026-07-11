from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchlocal_cli.cli import main
from benchlocal_cli.runner import Runner


def _response(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"completion_tokens": 3},
    }


def _base_args(tmp_path: Path, result_path: Path) -> list[str]:
    mock_path = tmp_path / "mock.json"
    mock_path.write_text(
        json.dumps(
            {
                "SO-01": _response('{"title":"The Great Gatsby","year":1925}'),
                "SO-04": _response("[package]\nname = \"my_cli\""),
            }
        )
    )
    return [
        "run",
        "--scenario",
        "structoutput-15/SO-01",
        "--scenario",
        "structoutput-15/SO-04",
        "--endpoint",
        "mock",
        "--model",
        "mock",
        "--measured-tps",
        "100",
        "--repeat",
        "2",
        "--mock-responses-from-json",
        str(mock_path),
        "--save-json",
        str(result_path),
    ]


def _without_timestamps(data: dict) -> dict:
    copied = json.loads(json.dumps(data))
    copied.pop("started_at", None)
    copied.pop("finished_at", None)
    return copied


def test_kill_mid_run_journal_inspect_resume_matches_reference(
    tmp_path, monkeypatch, capsys
):
    # Mock scoring latency is normally real wall time. Freeze it so two logically
    # identical mock runs can be compared with only timestamps removed.
    monkeypatch.setattr("benchlocal_cli.runner.time.perf_counter", lambda: 1.0)

    reference_path = tmp_path / "reference.json"
    assert main(_base_args(tmp_path, reference_path)) == 0
    capsys.readouterr()

    resumed_path = tmp_path / "resumed.json"
    interrupted_args = _base_args(tmp_path, resumed_path) + ["--incremental"]
    original_run_scenario = Runner.run_scenario
    calls = 0

    def interrupt_after_two(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise KeyboardInterrupt
        return original_run_scenario(self, *args, **kwargs)

    monkeypatch.setattr(Runner, "run_scenario", interrupt_after_two)
    with pytest.raises(KeyboardInterrupt):
        main(interrupted_args)
    monkeypatch.setattr(Runner, "run_scenario", original_run_scenario)

    sidecar = Path(f"{resumed_path}.partial.jsonl")
    assert sidecar.is_file()
    records = [json.loads(line) for line in sidecar.read_text().splitlines()]
    assert len(records) == 2
    assert all(record["journal_version"] == "1" for record in records)
    assert [(record["scenario"]["id"], record["scenario"]["repeat_index"]) for record in records] == [
        ("SO-01", 1),
        ("SO-04", 1),
    ]

    assert main(["inspect", str(sidecar), "--format", "json"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert {(row["id"], row["repeat_index"]) for row in inspected} == {
        ("SO-01", 1),
        ("SO-04", 1),
    }

    mock_path = tmp_path / "mock.json"
    assert main(
        [
            "run",
            "--resume",
            str(sidecar),
            "--mock-responses-from-json",
            str(mock_path),
        ]
    ) == 0

    assert not sidecar.exists()
    resumed = json.loads(resumed_path.read_text())
    reference = json.loads(reference_path.read_text())
    assert _without_timestamps(resumed) == _without_timestamps(reference)
    assert [
        (row["id"], row["repeat_index"])
        for row in resumed["packs"][0]["scenarios"]
    ] == [
        ("SO-01", 1),
        ("SO-04", 1),
        ("SO-01", 2),
        ("SO-04", 2),
    ]


def test_resume_completed_result_is_clear_noop(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("benchlocal_cli.runner.time.perf_counter", lambda: 1.0)
    result_path = tmp_path / "complete.json"
    assert main(_base_args(tmp_path, result_path)) == 0
    capsys.readouterr()

    assert main(["run", "--resume", str(result_path)]) == 0
    output = capsys.readouterr().out
    assert "resume complete" in output
    assert "no scenarios remain" in output

def test_resume_complete_sidecar_finalizes_without_model_call(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr("benchlocal_cli.runner.time.perf_counter", lambda: 1.0)
    result_path = tmp_path / "crashed-after-last.json"
    args = _base_args(tmp_path, result_path) + ["--incremental"]
    original_run = Runner.run

    def crash_after_last_scenario(self, *run_args, **run_kwargs):
        original_run(self, *run_args, **run_kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(Runner, "run", crash_after_last_scenario)
    with pytest.raises(KeyboardInterrupt):
        main(args)
    monkeypatch.setattr(Runner, "run", original_run)

    sidecar = Path(f"{result_path}.partial.jsonl")
    assert sidecar.is_file()
    assert not result_path.exists()
    assert len(sidecar.read_text().splitlines()) == 4

    assert main(["run", "--resume", str(sidecar)]) == 0
    output = capsys.readouterr().out
    assert "no scenarios remain" in output
    assert f"finalized {result_path}" in output
    assert result_path.is_file()
    assert not sidecar.exists()
    assert json.loads(result_path.read_text())["totals"] == {
        "passed": 4,
        "score": 1.0,
        "total": 4,
    }
