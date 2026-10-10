"""#198 — a pack's version names the scorer that produced its scores.

A pack's `version` moves when its scenarios or its scorer change (#183 took
StructOutput-15 to 2.1.0). Comparisons across that boundary measure the scorer
as much as the model, so:

- `--previous-result` warns, `--exit-on-regression` refuses;
- `--retry-failed` refuses (a scorer change would read as "flaky");
- `--resume` refuses (the merged result is relabelled with the installed pack);
- `rescore` relabels what it re-grades and records the change.

An "older" result is made by running against the installed pack and relabelling
its structoutput-15 version, which is what a result saved before #183 looks like.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchlocal_cli.cli import main
from benchlocal_cli.delta import classify, pack_version_mismatch
from benchlocal_cli.runner import Runner, load_pack

GOOD = '{"title":"The Great Gatsby","year":1925}'
INSTALLED = load_pack("structoutput-15")[0]["version"]
OLDER = "1.9.9" if INSTALLED != "1.9.9" else "1.9.8"


def _write_mock(tmp_path: Path, content: str = GOOD) -> Path:
    mock = tmp_path / "mock.json"
    mock.write_text(json.dumps({"SO-01": {"choices": [{"message": {"content": content}}]}}))
    return mock


def _run(tmp_path, capsys, *extra, name="run.json", content=GOOD):
    mock = _write_mock(tmp_path, content)
    out = tmp_path / name
    rc = main([
        "run", "--endpoint", "http://mock", "--model", "mock",
        "--scenario", "structoutput-15/SO-01", "--measured-tps", "100",
        "--mock-responses-from-json", str(mock), "--save-json", str(out), *extra,
    ])
    captured = capsys.readouterr()
    result = json.loads(out.read_text()) if out.is_file() else None
    return rc, captured.out, captured.err, result


def _relabel(path: Path, version: str = OLDER) -> Path:
    data = json.loads(path.read_text())
    for pack in data["packs"]:
        if pack["pack_id"] == "structoutput-15":
            pack["version"] = version
    path.write_text(json.dumps(data))
    return path


def _result(*packs: tuple[str, str | None]) -> dict:
    return {"packs": [{"pack_id": pid, **({"version": v} if v is not None else {})} for pid, v in packs]}


# ------------------------------------------------------------------ delta.pack_version_mismatch


def test_names_each_pack_and_both_versions():
    msg = pack_version_mismatch(
        _result(("structoutput-15", "2.1.0"), ("toolcall-15", "1.0.1"), ("instructfollow-15", "2.0.0")),
        _result(("structoutput-15", "2.0.0"), ("toolcall-15", "1.0.1"), ("instructfollow-15", "1.9.0")),
    )
    assert msg == (
        "pack version differs (instructfollow-15 current 2.0.0, previous 1.9.0; "
        "structoutput-15 current 2.1.0, previous 2.0.0)"
    )


def test_equal_versions_are_not_a_mismatch():
    same = _result(("structoutput-15", "2.1.0"), ("toolcall-15", "1.0.1"))
    assert pack_version_mismatch(same, same) is None


def test_a_pack_only_one_run_has_is_not_a_mismatch():
    assert pack_version_mismatch(
        _result(("structoutput-15", "2.1.0")),
        _result(("structoutput-15", "2.1.0"), ("toolcall-15", "1.0.0")),
    ) is None


def test_a_pack_without_a_recorded_version_compares_as_before():
    assert pack_version_mismatch(_result(("structoutput-15", "2.1.0")), _result(("structoutput-15", None))) is None
    assert pack_version_mismatch({}, {"packs": None}) is None


# ------------------------------------------------------------------ --previous-result / --exit-on-regression


def test_delta_warns_across_a_pack_version(tmp_path, capsys):
    _run(tmp_path, capsys, name="baseline.json")
    _relabel(tmp_path / "baseline.json")
    rc, _out, err, result = _run(tmp_path, capsys, "--previous-result", str(tmp_path / "baseline.json"),
                                 name="now.json")
    assert rc == 0, err
    assert (
        f"pack version differs (structoutput-15 current {INSTALLED}, previous {OLDER}); "
        "regressions and fixes may be scorer effects"
    ) in result["delta"]["warnings"]


def test_delta_quiet_at_the_same_pack_version(tmp_path, capsys):
    _run(tmp_path, capsys, name="baseline.json")
    rc, _out, err, result = _run(tmp_path, capsys, "--previous-result", str(tmp_path / "baseline.json"),
                                 name="now.json")
    assert rc == 0, err
    assert not any("pack version differs" in w for w in result["delta"]["warnings"])


def test_classify_warns_from_saved_results(tmp_path):
    previous = tmp_path / "prev.json"
    previous.write_text(json.dumps(_result(("structoutput-15", "2.0.0"))))
    delta = classify(_result(("structoutput-15", "2.1.0")), previous)
    assert any(w.startswith("pack version differs (structoutput-15") for w in delta.warnings)


def test_exit_on_regression_refuses_across_a_pack_version(tmp_path, capsys):
    _run(tmp_path, capsys, name="baseline.json")
    _relabel(tmp_path / "baseline.json")
    rc, _out, err, result = _run(
        tmp_path, capsys, "--previous-result", str(tmp_path / "baseline.json"),
        "--exit-on-regression", name="gated.json",
    )
    assert rc == 1
    assert result is None  # refused before running
    assert (
        f"--exit-on-regression is blocked: pack version differs (structoutput-15 current "
        f"{INSTALLED}, previous {OLDER}). Rescore the previous result"
    ) in err


def test_exit_on_regression_runs_at_the_same_pack_version(tmp_path, capsys):
    _run(tmp_path, capsys, name="baseline.json")
    rc, _out, err, _ = _run(
        tmp_path, capsys, "--previous-result", str(tmp_path / "baseline.json"),
        "--exit-on-regression", name="gated.json",
    )
    assert rc == 0, err


# ------------------------------------------------------------------ --retry-failed


def test_retry_failed_refuses_a_baseline_from_an_older_pack(tmp_path, capsys):
    _run(tmp_path, capsys, name="baseline.json", content="nope")  # a failure to retry
    _relabel(tmp_path / "baseline.json")
    rc = main([
        "run", "--retry-failed", "1", "--previous-result", str(tmp_path / "baseline.json"),
        "--measured-tps", "100", "--mock-responses-from-json", str(tmp_path / "mock.json"),
        "--save-json", str(tmp_path / "retry.json"),
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert (
        f"--retry-failed: pack version differs (structoutput-15 current {INSTALLED}, previous "
        f"{OLDER}); a retry would count scorer changes as flaky — rescore the baseline"
    ) in err
    assert not (tmp_path / "retry.json").exists()


def test_retry_failed_runs_at_the_same_pack_version(tmp_path, capsys):
    _run(tmp_path, capsys, name="baseline.json", content="nope")
    assert main([
        "run", "--retry-failed", "1", "--previous-result", str(tmp_path / "baseline.json"),
        "--measured-tps", "100", "--mock-responses-from-json", str(tmp_path / "mock.json"),
        "--save-json", str(tmp_path / "retry.json"),
    ]) == 0


# ------------------------------------------------------------------ --resume


def _interrupted_journal(tmp_path, monkeypatch) -> tuple[Path, Path]:
    mock = _write_mock(tmp_path)
    out = tmp_path / "r.json"
    calls = 0
    original = Runner.run_scenario

    def interrupt_on_second(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Runner, "run_scenario", interrupt_on_second)
    with pytest.raises(KeyboardInterrupt):
        main([
            "run", "--endpoint", "http://mock", "--model", "mock",
            "--scenario", "structoutput-15/SO-01", "--measured-tps", "100",
            "--mock-responses-from-json", str(mock), "--save-json", str(out),
            "--repeat", "2", "--incremental",
        ])
    monkeypatch.setattr(Runner, "run_scenario", original)
    return Path(f"{out}.partial.jsonl"), mock


def _relabel_journal(sidecar: Path, version: str = OLDER) -> None:
    lines = []
    for line in sidecar.read_text().splitlines():
        record = json.loads(line)
        record["pack"]["version"] = version
        lines.append(json.dumps(record))
    sidecar.write_text("\n".join(lines) + "\n")


def test_resume_refuses_rows_from_an_older_pack(tmp_path, monkeypatch, capsys):
    sidecar, mock = _interrupted_journal(tmp_path, monkeypatch)
    _relabel_journal(sidecar)
    capsys.readouterr()
    assert main(["run", "--resume", str(sidecar), "--mock-responses-from-json", str(mock)]) == 1
    assert (
        f"--resume: rows were scored by a different pack version (structoutput-15 {OLDER} "
        f"(installed {INSTALLED})); a resumed run keeps one scorer per pack"
    ) in capsys.readouterr().err
    assert not (tmp_path / "r.json").exists()


def test_resume_continues_at_the_same_pack_version(tmp_path, monkeypatch, capsys):
    sidecar, mock = _interrupted_journal(tmp_path, monkeypatch)
    assert main(["run", "--resume", str(sidecar), "--mock-responses-from-json", str(mock)]) == 0
    result = json.loads((tmp_path / "r.json").read_text())
    assert [p["version"] for p in result["packs"] if p["pack_id"] == "structoutput-15"] == [INSTALLED]


def test_resume_of_a_saved_result_refuses_an_older_label(tmp_path, monkeypatch, capsys):
    sidecar, mock = _interrupted_journal(tmp_path, monkeypatch)
    # Finish it, then relabel the saved result and resume it.
    assert main(["run", "--resume", str(sidecar), "--mock-responses-from-json", str(mock)]) == 0
    _relabel(tmp_path / "r.json")
    capsys.readouterr()
    assert main(["run", "--resume", str(tmp_path / "r.json"), "--mock-responses-from-json", str(mock)]) == 1
    assert "--resume: rows were scored by a different pack version" in capsys.readouterr().err


# ------------------------------------------------------------------ rescore


def _rescore(tmp_path: Path, source: Path) -> dict:
    out = tmp_path / "rescored.json"
    assert main(["rescore", str(source), "--allow-partial", "--output", str(out)]) == 0
    return json.loads(out.read_text())


def _so(result: dict) -> dict:
    return next(p for p in result["packs"] if p["pack_id"] == "structoutput-15")


def test_rescore_relabels_with_the_scoring_pack_and_records_it(tmp_path, capsys):
    _run(tmp_path, capsys, name="old.json")
    data = _rescore(tmp_path, _relabel(tmp_path / "old.json"))
    assert _so(data)["version"] == INSTALLED
    assert data["rescored"]["pack_versions"] == {"structoutput-15": {"from": OLDER, "to": INSTALLED}}


def test_rescore_at_the_same_version_records_no_change(tmp_path, capsys):
    _run(tmp_path, capsys, name="same.json")
    data = _rescore(tmp_path, tmp_path / "same.json")
    assert _so(data)["version"] == INSTALLED
    assert "pack_versions" not in data["rescored"]


def test_rescore_of_a_partly_regradable_pack_keeps_its_label(tmp_path, capsys):
    _run(tmp_path, capsys, "--repeat", "2", name="old.json")
    path = _relabel(tmp_path / "old.json")
    data = json.loads(path.read_text())
    _so(data)["scenarios"][1].pop("raw_response")  # this row cannot be re-graded
    path.write_text(json.dumps(data))
    rescored = _rescore(tmp_path, path)
    assert _so(rescored)["version"] == OLDER
    assert rescored["rescored"]["pack_versions"] == {
        "structoutput-15": {"from": OLDER, "to": INSTALLED, "partial": True}
    }


def test_a_rescored_result_clears_the_gate(tmp_path, capsys):
    _run(tmp_path, capsys, name="baseline.json")
    _relabel(tmp_path / "baseline.json")
    _rescore(tmp_path, tmp_path / "baseline.json")
    rc, _out, err, _ = _run(
        tmp_path, capsys, "--previous-result", str(tmp_path / "rescored.json"),
        "--exit-on-regression", name="gated.json",
    )
    assert rc == 0, err
