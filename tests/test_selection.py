from __future__ import annotations

import json

import pytest

from benchlocal_cli.cli import main
from benchlocal_cli.history import append_run
from benchlocal_cli.selection import (
    intersect_selection,
    parse_scenarios_file,
    requested_ids,
    validate_selection,
)


def _response(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"completion_tokens": 3},
    }


def _run_args(tmp_path, *selection: str) -> tuple[list[str], object]:
    mock_path = tmp_path / "mock.json"
    result_path = tmp_path / "result.json"
    mock_path.write_text(
        json.dumps(
            {
                "SO-01": _response('{"title":"The Great Gatsby","year":1925}'),
                "RM-04": _response("ANSWER: not-correct"),
            }
        )
    )
    args = [
        "run",
        "--endpoint",
        "mock",
        "--model",
        "mock",
        "--measured-tps",
        "100",
        "--mock-responses-from-json",
        str(mock_path),
        "--save-json",
        str(result_path),
    ]
    for value in selection:
        args.extend(["--scenario", value])
    return args, result_path


def test_parse_scenarios_file_supports_comments_and_deduplicates(tmp_path):
    source = tmp_path / "selection.txt"
    source.write_text(
        "# targeted checks\n"
        "cli-40/CLI-34  # safety case\n"
        "\n"
        "reasonmath-15/RM-04\n"
        "cli-40/CLI-34\n"
    )

    assert requested_ids([], str(source)) == [
        "cli-40/CLI-34",
        "reasonmath-15/RM-04",
    ]
    assert parse_scenarios_file(source)[0] == "cli-40/CLI-34"


def test_validate_selection_reports_near_matches():
    with pytest.raises(ValueError) as exc_info:
        validate_selection(["cli-40/CLI-034"])

    message = str(exc_info.value)
    assert "unknown scenario selection" in message
    assert "near matches:" in message
    assert "cli-40/CLI-34" in message


def test_selection_intersects_with_pack_set():
    canonical, by_pack = validate_selection(
        ["structoutput-15/SO-01", "reasonmath-15/RM-04"]
    )

    filtered, filtered_by_pack = intersect_selection(
        canonical, by_pack, ["structoutput-15"]
    )

    assert filtered == ["structoutput-15/SO-01"]
    assert filtered_by_pack == {"structoutput-15": ["SO-01"]}


def test_cli_selection_marks_json_and_stdout_partial(tmp_path, capsys):
    args, result_path = _run_args(
        tmp_path,
        "structoutput-15/SO-01",
        "reasonmath-15/RM-04",
    )

    assert main(args) == 0

    output = capsys.readouterr().out
    data = json.loads(result_path.read_text())
    assert data["selection"] == [
        "reasonmath-15/RM-04",
        "structoutput-15/SO-01",
    ]
    assert data["mode"] == "custom"
    assert data["totals"]["total"] == 2
    assert {
        pack["pack_id"]: (pack["scenario_count"], pack["catalog_scenario_count"])
        for pack in data["packs"]
    } == {
        "reasonmath-15": (1, 15),
        "structoutput-15": (1, 15),
    }
    assert "[PARTIAL SELECTION: 2 scenarios]" in output
    assert "partial — 1 of 15 selected" in output


def test_selected_subset_delta_compares_only_selected_scenarios(tmp_path):
    previous = tmp_path / "previous.json"
    previous.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "packs": [
                    {
                        "pack_id": "reasonmath-15",
                        "scenarios": [{"id": "RM-04", "passed": True}],
                    },
                    {
                        "pack_id": "structoutput-15",
                        "scenarios": [
                            {"id": "SO-01", "passed": True},
                            {"id": "SO-02", "passed": True},
                        ],
                    },
                ],
            }
        )
    )
    args, result_path = _run_args(
        tmp_path,
        "structoutput-15/SO-01",
        "reasonmath-15/RM-04",
    )
    args.extend(["--previous-result", str(previous)])

    assert main(args) == 0

    data = json.loads(result_path.read_text())
    assert data["delta"]["total_regressions"] == 1
    assert data["delta"]["total_stable_pass"] == 1
    assert data["delta"]["total_dropped"] == 0


def test_partial_history_and_rescore_require_explicit_opt_in(tmp_path, capsys):
    partial = {
        "selection": ["structoutput-15/SO-01"],
        "packs": [],
        "totals": {"passed": 0, "total": 0, "score": 0.0},
    }
    history = tmp_path / "history.csv"
    with pytest.raises(ValueError, match="--allow-partial"):
        append_run(partial, history)
    append_run(partial, history, allow_partial=True)
    assert history.is_file()

    source = tmp_path / "partial.json"
    source.write_text(json.dumps(partial))
    assert main(["rescore", str(source)]) == 1
    assert "--allow-partial" in capsys.readouterr().err
