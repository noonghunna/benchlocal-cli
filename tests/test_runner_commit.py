"""#194: record the commit the runner ran from, next to runner_version.

runner_version reads pyproject.toml, which changes only in the release bump, so
every run from master between two releases is stamped with the previous release
however far the code has moved. runner_commit (`<short sha>[+dirty]`, recorded
only from a git checkout) tells those runs apart. It never fails a run, is
absent when unknown so older JSON stays byte-identical, and a delta between two
runs that both recorded one names a difference without gating on it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import benchlocal_cli
from benchlocal_cli import _git_commit, runner_commit
from benchlocal_cli import delta as delta_module
from benchlocal_cli import history as history_module
from benchlocal_cli.cli import _results_card_markdown, main
from benchlocal_cli.runner import Runner
from benchlocal_cli.types import RunResult

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["HOME"] = str(repo.parent)  # no user config (signing, hooks) in the way
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        cwd=repo, env=env, check=True, capture_output=True, encoding="utf-8",
    )
    return proc.stdout.strip()


def _repo_with_package(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    pkg = repo / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("x = 1\n")
    (repo / "README").write_text("readme\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "release")
    _git(repo, "tag", "v1.0.0")
    return repo, pkg


@pytest.fixture(autouse=True)
def _fresh_lookup():
    runner_commit.cache_clear()
    yield
    runner_commit.cache_clear()


# ------------------------------------------------------------------ the lookup


@needs_git
def test_a_commit_past_the_tag_stamps_differently_from_the_tag(tmp_path):
    repo, pkg = _repo_with_package(tmp_path)
    at_tag = _git_commit(pkg)
    assert at_tag == _git(repo, "rev-parse", "--short", "HEAD")

    (pkg / "fix.py").write_text("y = 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fix")
    past_tag = _git_commit(pkg)
    assert past_tag == _git(repo, "rev-parse", "--short", "HEAD")
    assert past_tag != at_tag


@needs_git
def test_a_modified_tracked_file_in_the_package_is_dirty(tmp_path):
    repo, pkg = _repo_with_package(tmp_path)
    head = _git(repo, "rev-parse", "--short", "HEAD")
    (pkg / "__init__.py").write_text("x = 3\n")
    assert _git_commit(pkg) == f"{head}+dirty"


@needs_git
def test_untracked_files_and_changes_outside_the_package_are_not_dirty(tmp_path):
    repo, pkg = _repo_with_package(tmp_path)
    head = _git(repo, "rev-parse", "--short", "HEAD")
    (pkg / "scratch.py").write_text("z = 0\n")  # untracked
    (repo / "README").write_text("edited\n")    # tracked, but not the package
    assert _git_commit(pkg) == head


@needs_git
def test_an_untracked_copy_inside_another_repo_is_not_stamped(tmp_path):
    # A site-packages copy in a project's .venv: git finds the project's repo, whose
    # HEAD says nothing about this code.
    repo, _ = _repo_with_package(tmp_path)
    (repo / ".gitignore").write_text(".venv/\n")
    copy = repo / ".venv" / "lib" / "pkg"
    copy.mkdir(parents=True)
    (copy / "__init__.py").write_text("x = 1\n")
    assert _git_commit(copy) is None


def test_not_a_checkout_is_not_stamped(tmp_path):
    pkg = tmp_path / "plain"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("x = 1\n")
    probe = subprocess.run(["git", "-C", str(pkg), "rev-parse"], capture_output=True) \
        if shutil.which("git") else None
    if probe is not None and probe.returncode == 0:
        pytest.skip("tmp_path is inside a git checkout on this machine")
    assert _git_commit(pkg) is None


@pytest.mark.parametrize("error", [
    FileNotFoundError("git"),
    subprocess.TimeoutExpired(["git"], 5),
    PermissionError("git"),
])
def test_no_git_or_a_stuck_git_is_not_stamped(monkeypatch, tmp_path, error):
    def _raise(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(benchlocal_cli._subprocess, "run", _raise)
    assert _git_commit(tmp_path) is None


@needs_git
def test_the_lookup_ignores_git_variables_from_a_calling_hook(monkeypatch, tmp_path):
    repo, pkg = _repo_with_package(tmp_path)
    other, _ = _repo_with_package(tmp_path / "other")
    _git(other, "commit", "--allow-empty", "-qm", "different head")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert _git_commit(pkg) == _git(repo, "rev-parse", "--short", "HEAD")


# ------------------------------------------------------------------ the result


def _result(**kwargs) -> RunResult:
    return RunResult(
        schema_version="1", runner_version="0.11.0", endpoint="mock", model="mock",
        mode="custom", started_at="2026-10-10T00:00:00Z", finished_at="2026-10-10T00:01:00Z",
        packs=[], totals={"passed": 0, "total": 0, "score": 0.0}, **kwargs,
    )


def test_the_field_is_absent_when_unknown_so_old_json_is_unchanged():
    assert "runner_commit" not in _result().to_dict()
    assert _result(runner_commit="abc1234").to_dict()["runner_commit"] == "abc1234"


def test_the_results_card_names_the_commit_beside_the_version():
    assert "benchlocal-cli v0.11.0, repeat = 1" in _results_card_markdown(_result())
    card = _results_card_markdown(_result(runner_commit="abc1234+dirty"))
    assert "benchlocal-cli v0.11.0 (abc1234+dirty), repeat = 1" in card


def test_history_records_the_commit_and_the_env_override_still_wins(monkeypatch):
    run = _result(runner_commit="abc1234").to_dict()
    monkeypatch.delenv("BENCHLOCAL_GIT_COMMIT", raising=False)
    assert history_module._row_from_run(run)["git_commit"] == "abc1234"
    assert history_module._row_from_run(_result().to_dict())["git_commit"] == ""
    monkeypatch.setenv("BENCHLOCAL_GIT_COMMIT", "fromenv")
    assert history_module._row_from_run(run)["git_commit"] == "fromenv"


# ------------------------------------------------------------------ delta


def _runner_warnings(tmp_path: Path, current: dict, previous: dict) -> list[str]:
    path = tmp_path / "previous.json"
    path.write_text(json.dumps(previous))
    return [w for w in delta_module.classify(current, path).warnings if "runner differs" in w]


def test_delta_names_a_different_commit_when_both_runs_recorded_one(tmp_path):
    cur = _result(runner_commit="bbb2222").to_dict()
    prev = _result(runner_commit="aaa1111").to_dict()
    assert _runner_warnings(tmp_path, cur, prev) == [
        "runner differs (current 0.11.0 @ bbb2222, previous 0.11.0 @ aaa1111); "
        "regressions and fixes may be harness effects"
    ]


def test_delta_compares_as_before_unless_both_runs_recorded_a_commit(tmp_path):
    with_commit = _result(runner_commit="aaa1111").to_dict()
    old = _result().to_dict()
    old_other_version = dict(old, runner_version="0.9.9")
    assert _runner_warnings(tmp_path, old, old_other_version) == []
    assert _runner_warnings(tmp_path, with_commit, old_other_version) == []
    assert _runner_warnings(tmp_path, old, with_commit) == []
    assert _runner_warnings(tmp_path, with_commit, dict(with_commit)) == []


def test_a_different_runner_does_not_gate_a_regression(tmp_path):
    # Informational only: the budget mismatch refuses --exit-on-regression, this does not.
    assert delta_module.budget_mismatch(
        _result(runner_commit="bbb2222").to_dict(),
        _write(tmp_path / "prev.json", _result(runner_commit="aaa1111").to_dict()),
    ) is None


def _write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data))
    return path


# ------------------------------------------------------------------ end to end

MOCK = {
    "SO-01": {"choices": [{"message": {"content": '{"title":"The Great Gatsby","year":1925}'}}],
              "usage": {"completion_tokens": 3}},
    "SO-02": {"choices": [{"message": {"content": "not json"}}], "usage": {"completion_tokens": 3}},
}


def _args(tmp_path: Path, out: Path) -> list[str]:
    mock = tmp_path / "mock.json"
    mock.write_text(json.dumps(MOCK))
    return ["run", "--endpoint", "mock", "--model", "mock", "--measured-tps", "100",
            "--mock-responses-from-json", str(mock), "--scenario", "structoutput-15/SO-01",
            "--scenario", "structoutput-15/SO-02", "--save-json", str(out)]


def _stamp(monkeypatch, value: str | None) -> None:
    monkeypatch.setattr("benchlocal_cli.cli.runner_commit", lambda: value)
    monkeypatch.setattr("benchlocal_cli.runner.runner_commit", lambda: value)


def test_a_live_run_records_the_commit(tmp_path, monkeypatch, capsys):
    _stamp(monkeypatch, "abc1234")
    out = tmp_path / "result.json"
    assert main(_args(tmp_path, out)) == 0
    assert json.loads(out.read_text())["runner_commit"] == "abc1234"


def test_a_run_without_git_proceeds_and_records_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))  # no git binary at all
    out = tmp_path / "result.json"
    assert main(_args(tmp_path, out)) == 0
    saved = json.loads(out.read_text())
    assert "runner_commit" not in saved
    assert saved["totals"]["total"] == 2


def test_journal_recovery_and_resume_keep_each_sessions_commit(tmp_path, monkeypatch, capsys):
    out = tmp_path / "result.json"
    original = Runner.run_scenario
    calls = 0

    def interrupt_on_second(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)

    _stamp(monkeypatch, "aaa1111")
    monkeypatch.setattr(Runner, "run_scenario", interrupt_on_second)
    with pytest.raises(KeyboardInterrupt):
        main([*_args(tmp_path, out), "--incremental"])
    monkeypatch.setattr(Runner, "run_scenario", original)

    sidecar = Path(f"{out}.partial.jsonl")
    from benchlocal_cli.persistence import result_from_journal

    # Recovery reads the journal's commit, not the commit of the process reading it.
    _stamp(monkeypatch, "zzz9999")
    assert result_from_journal(sidecar)["runner_commit"] == "aaa1111"

    _stamp(monkeypatch, "bbb2222")
    assert main(["run", "--resume", str(sidecar),
                 "--mock-responses-from-json", str(tmp_path / "mock.json")]) == 0
    resumed = json.loads(out.read_text())
    assert resumed["runner_commit"] == "bbb2222"
    assert ("resumed on a different runner commit (aaa1111 -> bbb2222); "
            "scenario rows come from both") in resumed["warnings"]


def test_a_resume_on_the_same_commit_adds_no_warning(tmp_path, monkeypatch, capsys):
    out = tmp_path / "result.json"
    original = Runner.run_scenario
    calls = 0

    def interrupt_on_second(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)

    _stamp(monkeypatch, "aaa1111")
    monkeypatch.setattr(Runner, "run_scenario", interrupt_on_second)
    with pytest.raises(KeyboardInterrupt):
        main([*_args(tmp_path, out), "--incremental"])
    monkeypatch.setattr(Runner, "run_scenario", original)
    assert main(["run", "--resume", str(Path(f"{out}.partial.jsonl")),
                 "--mock-responses-from-json", str(tmp_path / "mock.json")]) == 0
    resumed = json.loads(out.read_text())
    assert resumed["runner_commit"] == "aaa1111"
    assert not [w for w in resumed["warnings"] if "runner commit" in w]
