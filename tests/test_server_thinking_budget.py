"""--server-thinking-budget: record the reasoning budget the SERVER applies.

club-3090's vLLM/SGLang composes can cap reasoning server-side, with a budget
chosen by effort. The caller resolves that budget from the serving config and
passes it here, so the results JSON records it and a delta between two runs
under different server budgets is flagged instead of reading as regressions.

It is informational only: no request ever carries it. A thinking-off run does
not record it (the budget has no effect there), like thinking_max_tokens.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import benchlocal_cli.runner as runner_module
from benchlocal_cli.cli import _parse_server_thinking_budget, main
from benchlocal_cli.delta import budget_mismatch
from benchlocal_cli.persistence import load_resume, result_from_journal
from benchlocal_cli.runner import Runner

GOOD = '{"title":"The Great Gatsby","year":1925}'


def _write_mock(tmp_path: Path, content: str = GOOD) -> Path:
    mock = tmp_path / "mock.json"
    mock.write_text(json.dumps({"SO-01": {"choices": [{"message": {"content": content}}]}}))
    return mock


def _run(tmp_path, capsys, *extra, name="run.json", content=GOOD):
    mock = _write_mock(tmp_path, content)
    out = tmp_path / name
    rc = main(
        [
            "run", "--endpoint", "http://mock", "--model", "mock",
            "--scenario", "structoutput-15/SO-01", "--measured-tps", "100",
            "--mock-responses-from-json", str(mock), "--save-json", str(out), *extra,
        ]
    )
    captured = capsys.readouterr()
    result = json.loads(out.read_text()) if out.is_file() else None
    return rc, captured.out, captured.err, result


def _header(stdout: str) -> str:
    return next(line for line in stdout.splitlines() if line.startswith("=== benchlocal-cli"))


# ------------------------------------------------------------------ parsing


@pytest.mark.parametrize("raw, expected", [("0", 0), ("8192", 8192), (" 4096 ", 4096)])
def test_parse_accepts_non_negative_integers(raw, expected):
    assert _parse_server_thinking_budget(raw) == expected


@pytest.mark.parametrize("raw", ["-1", "1.5", "", "abc", "+5", "1e3", "8k"])
def test_parse_rejects_anything_else(raw):
    import argparse

    with pytest.raises(argparse.ArgumentTypeError, match="non-negative integer"):
        _parse_server_thinking_budget(raw)


@pytest.mark.parametrize("arg", ["--server-thinking-budget=-1", "--server-thinking-budget=1.5",
                                 "--server-thinking-budget=abc", "--server-thinking-budget="])
def test_cli_refuses_a_bad_value_before_running(tmp_path, capsys, arg):
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, capsys, arg)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "argument --server-thinking-budget: must be a non-negative integer" in err
    assert not (tmp_path / "run.json").exists()


# ------------------------------------------------------------------ recording + header


def test_recorded_and_shown_in_the_header(tmp_path, capsys):
    rc, out, _err, result = _run(tmp_path, capsys, "--server-thinking-budget", "8192",
                                 "--max-tokens", "4096")
    assert rc == 0
    assert result["server_thinking_budget"] == 8192
    header = _header(out)
    # next to the token-budget tag
    assert "[TOKEN BUDGET: fixed 4,096 per answer] [SERVER THINKING BUDGET: 8,192] ===" in header


def test_zero_is_a_value_not_absence(tmp_path, capsys):
    rc, out, _err, result = _run(tmp_path, capsys, "--server-thinking-budget", "0")
    assert rc == 0
    assert result["server_thinking_budget"] == 0
    assert "[SERVER THINKING BUDGET: 0]" in _header(out)


def test_absent_flag_leaves_json_and_header_unchanged(tmp_path, capsys):
    rc, out, _err, result = _run(tmp_path, capsys)
    assert rc == 0
    assert "server_thinking_budget" not in result
    assert "SERVER THINKING BUDGET" not in _header(out)


def test_not_recorded_on_a_thinking_off_run(tmp_path, capsys):
    rc, out, _err, result = _run(tmp_path, capsys, "--no-thinking", "--server-thinking-budget", "8192")
    assert rc == 0
    assert "server_thinking_budget" not in result  # no thinking: the budget had no effect
    assert "SERVER THINKING BUDGET" not in _header(out)


def test_recorded_on_a_thinking_on_run(tmp_path, capsys):
    rc, out, _err, result = _run(tmp_path, capsys, "--enable-thinking", "--server-thinking-budget", "4096")
    assert rc == 0
    assert result["server_thinking_budget"] == 4096
    assert "[SERVER THINKING BUDGET: 4,096]" in _header(out)


# ------------------------------------------------------------------ never sent


class _Resp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


def _wire_run(tmp_path: Path, monkeypatch, *extra: str) -> list[tuple[str, str, object]]:
    """Run against a fake endpoint and return everything sent over httpx, in order."""
    sent: list[tuple[str, str, object]] = []

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

        def get(self, url, **kwargs):
            sent.append(("GET", url, None))
            return _Resp({}, status=404)  # no /props: vLLM/SGLang-shaped

        def post(self, url, json=None, **kwargs):
            sent.append(("POST", url, copy.deepcopy(json)))
            return _Resp({
                "choices": [{"message": {"role": "assistant", "content": GOOD}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 3, "total_tokens": 43},
            })

    monkeypatch.setattr(runner_module.httpx, "Client", _Client)
    out = tmp_path / f"wire-{len(list(tmp_path.glob('wire-*.json')))}.json"
    rc = main([
        "run", "--endpoint", "http://fake:1", "--model", "fake",
        "--scenario", "structoutput-15/SO-01", "--measured-tps", "100",
        "--save-json", str(out), *extra,
    ])
    assert rc == 0
    posts = [entry for entry in sent if entry[0] == "POST"]
    assert posts, "the fake endpoint saw no chat request"
    return sent


@pytest.mark.parametrize("thinking", [(), ("--enable-thinking",), ("--no-thinking",)])
def test_request_payload_is_identical_with_and_without_it(tmp_path, monkeypatch, capsys, thinking):
    without = _wire_run(tmp_path, monkeypatch, *thinking)
    with_budget = _wire_run(tmp_path, monkeypatch, *thinking, "--server-thinking-budget", "8192")
    with_zero = _wire_run(tmp_path, monkeypatch, *thinking, "--server-thinking-budget", "0")
    assert with_budget == without
    assert with_zero == without


def test_payload_comparison_can_fail(tmp_path, monkeypatch, capsys):
    # Positive control: a flag that DOES change the request is seen by the same
    # harness, so the equality above is not vacuous.
    base = _wire_run(tmp_path, monkeypatch, "--enable-thinking", "--thinking-max-tokens", "16384")
    changed = _wire_run(tmp_path, monkeypatch, "--enable-thinking", "--thinking-max-tokens", "8192")
    assert changed != base


# ------------------------------------------------------------------ persistence


def test_journal_recovered_and_resumed_result_keeps_it(tmp_path, monkeypatch, capsys):
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
            "--server-thinking-budget", "8192", "--repeat", "2", "--incremental",
        ])
    monkeypatch.setattr(Runner, "run_scenario", original)

    sidecar = Path(f"{out}.partial.jsonl")
    assert result_from_journal(sidecar)["server_thinking_budget"] == 8192

    # A resume that does not pass the flag restores it from the journal.
    assert main(["run", "--resume", str(sidecar), "--mock-responses-from-json", str(mock)]) == 0
    final = json.loads(out.read_text())
    assert final["server_thinking_budget"] == 8192

    # ...and resuming from the final JSON reads it back into the run config.
    assert load_resume(out).config["server_thinking_budget"] == 8192


def _interrupted_journal(tmp_path, monkeypatch, *extra: str) -> tuple[Path, Path]:
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
            "--repeat", "2", "--incremental", *extra,
        ])
    monkeypatch.setattr(Runner, "run_scenario", original)
    return Path(f"{out}.partial.jsonl"), mock


def test_resume_accepts_the_same_budget_again(tmp_path, monkeypatch, capsys):
    sidecar, mock = _interrupted_journal(tmp_path, monkeypatch, "--server-thinking-budget", "8192")
    assert main(["run", "--resume", str(sidecar), "--mock-responses-from-json", str(mock),
                 "--server-thinking-budget", "8192"]) == 0
    assert json.loads((tmp_path / "r.json").read_text())["server_thinking_budget"] == 8192


def test_resume_refuses_a_different_budget(tmp_path, monkeypatch, capsys):
    sidecar, mock = _interrupted_journal(tmp_path, monkeypatch, "--server-thinking-budget", "8192")
    capsys.readouterr()
    rc = main(["run", "--resume", str(sidecar), "--mock-responses-from-json", str(mock),
               "--server-thinking-budget", "4096"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "--server-thinking-budget 4096 differs from the original run's (8192)" in err


def test_resume_refuses_a_budget_the_original_run_did_not_record(tmp_path, monkeypatch, capsys):
    sidecar, mock = _interrupted_journal(tmp_path, monkeypatch)
    capsys.readouterr()
    rc = main(["run", "--resume", str(sidecar), "--mock-responses-from-json", str(mock),
               "--server-thinking-budget", "0"])
    assert rc == 1
    assert "differs from the original run's (none recorded)" in capsys.readouterr().err


def test_retry_failed_keeps_the_baseline_budget(tmp_path, capsys):
    # A wrong answer, so the retry has a failed scenario to re-run.
    rc, *_ = _run(tmp_path, capsys, "--server-thinking-budget", "8192", name="baseline.json",
                  content="nope")
    assert rc == 0
    retry = tmp_path / "retry.json"
    assert main([
        "run", "--retry-failed", "1", "--previous-result", str(tmp_path / "baseline.json"),
        "--measured-tps", "100", "--mock-responses-from-json", str(tmp_path / "mock.json"),
        "--save-json", str(retry),
    ]) == 0
    assert json.loads(retry.read_text())["server_thinking_budget"] == 8192


# ------------------------------------------------------------------ delta.budget_mismatch


def _saved(tmp_path: Path, name: str, **fields) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"schema_version": "1", "packs": [], **fields}))
    return path


def test_mismatch_when_both_recorded_and_different(tmp_path):
    previous = _saved(tmp_path, "prev.json", server_thinking_budget=8192)
    assert budget_mismatch({"server_thinking_budget": 4096}, previous) == (
        "token budget differs (current server_thinking_budget=4096, "
        "previous server_thinking_budget=8192)"
    )
    # 0 is a recorded budget, not a missing one
    assert "current server_thinking_budget=0, previous server_thinking_budget=8192" in (
        budget_mismatch({"server_thinking_budget": 0}, previous) or ""
    )


def test_no_mismatch_when_equal(tmp_path):
    previous = _saved(tmp_path, "prev.json", server_thinking_budget=8192)
    assert budget_mismatch({"server_thinking_budget": 8192}, previous) is None


def test_no_mismatch_when_either_run_did_not_record_one(tmp_path):
    old = _saved(tmp_path, "old.json")  # a result from before the field existed
    assert budget_mismatch({"server_thinking_budget": 8192}, old) is None
    recorded = _saved(tmp_path, "new.json", server_thinking_budget=8192)
    assert budget_mismatch({}, recorded) is None


def test_old_results_compare_exactly_as_before(tmp_path):
    # Neither run recorded it: the existing budget comparisons are untouched.
    previous = _saved(tmp_path, "prev.json", sampling_overrides={"max_tokens": 4096},
                      thinking_max_tokens=16384)
    assert budget_mismatch({"sampling_overrides": {"max_tokens": 4096},
                            "thinking_max_tokens": 16384}, previous) is None
    assert budget_mismatch({"sampling_overrides": {"max_tokens": 2048},
                            "thinking_max_tokens": 16384}, previous) == (
        "token budget differs (current max_tokens=2048, previous max_tokens=4096)"
    )


def test_named_alongside_the_other_budgets(tmp_path):
    previous = _saved(tmp_path, "prev.json", sampling_overrides={"max_tokens": 4096},
                      thinking_max_tokens=16384, server_thinking_budget=8192)
    assert budget_mismatch({"sampling_overrides": {"max_tokens": 2048}, "thinking_max_tokens": 8192,
                            "server_thinking_budget": 4096}, previous) == (
        "token budget differs (current max_tokens=2048, previous max_tokens=4096; "
        "current thinking_max_tokens=8192, previous thinking_max_tokens=16384; "
        "current server_thinking_budget=4096, previous server_thinking_budget=8192)"
    )


def test_delta_warns_when_server_budgets_differ(tmp_path, capsys):
    _run(tmp_path, capsys, "--server-thinking-budget", "8192", name="baseline.json")
    rc, _out, _err, result = _run(
        tmp_path, capsys, "--server-thinking-budget", "4096",
        "--previous-result", str(tmp_path / "baseline.json"), name="again.json",
    )
    assert rc == 0
    assert any(
        "token budget differs (current server_thinking_budget=4096, "
        "previous server_thinking_budget=8192); regressions and fixes may be budget effects" in w
        for w in result["delta"]["warnings"]
    )


def test_delta_quiet_when_the_previous_result_has_no_server_budget(tmp_path, capsys):
    _run(tmp_path, capsys, name="baseline.json")
    rc, _out, _err, result = _run(
        tmp_path, capsys, "--server-thinking-budget", "4096",
        "--previous-result", str(tmp_path / "baseline.json"), name="again.json",
    )
    assert rc == 0
    assert not any("token budget differs" in w for w in result["delta"]["warnings"])


def test_exit_on_regression_refuses_a_different_server_budget(tmp_path, capsys):
    _run(tmp_path, capsys, "--server-thinking-budget", "8192", name="baseline.json")
    baseline = str(tmp_path / "baseline.json")
    rc, _out, err, _ = _run(
        tmp_path, capsys, "--server-thinking-budget", "4096",
        "--previous-result", baseline, "--exit-on-regression", name="other.json",
    )
    assert rc == 1
    assert ("token budget differs (current server_thinking_budget=4096, "
            "previous server_thinking_budget=8192)") in err
    rc, _out, err, _ = _run(
        tmp_path, capsys, "--server-thinking-budget", "8192",
        "--previous-result", baseline, "--exit-on-regression", name="same.json",
    )
    assert rc == 0, err


def test_exit_on_regression_ignores_server_budgets_of_thinking_off_runs(tmp_path, capsys):
    # Thinking off: the server budget never applied, so it is not recorded or compared.
    _run(tmp_path, capsys, "--no-thinking", "--server-thinking-budget", "8192", name="baseline.json")
    rc, _out, err, result = _run(
        tmp_path, capsys, "--no-thinking", "--server-thinking-budget", "4096",
        "--previous-result", str(tmp_path / "baseline.json"), "--exit-on-regression",
        name="again.json",
    )
    assert rc == 0, err
    assert not any("token budget differs" in w for w in result["delta"]["warnings"])
