"""#187: a fixed --max-tokens is a token budget, not a sampling override.

It rides in `sampling_overrides` (#28), and three places used to count it as a
sampler: the run header said `⚠ NON-CANONICAL (sampling: max_tokens=4096)`, the
run warned "non-canonical sampling overrides active", and --exit-on-regression
refused the run. club-3090 passes --max-tokens 4096 on every default run, so all
three fired on canonical runs. A budget is now reported as a budget, and the CI
gate compares budgets instead of refusing them — the answer budget and, when both
runs think, the thinking budget.
"""

from __future__ import annotations

import json

from benchlocal_cli.cli import main
from benchlocal_cli.runner import distribution_overrides

GOOD = {"SO-01": {"choices": [{"message": {"content": '{"title":"The Great Gatsby","year":1925}'}}]}}


def _run(tmp_path, capsys, *extra, name="run.json"):
    mock = tmp_path / "mock.json"
    mock.write_text(json.dumps(GOOD))
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


def test_distribution_overrides_drops_only_max_tokens():
    assert distribution_overrides({"max_tokens": 4096}) == {}
    assert distribution_overrides({"temperature": 0.7, "max_tokens": 4096}) == {"temperature": 0.7}
    assert distribution_overrides(None) == {}


def test_budget_only_run_is_a_budget_not_non_canonical(tmp_path, capsys):
    rc, out, _err, result = _run(tmp_path, capsys, "--max-tokens", "4096")
    assert rc == 0
    header = _header(out)
    assert "NON-CANONICAL" not in header
    assert "[TOKEN BUDGET: fixed 4,096 per answer]" in header
    warnings = " | ".join(result.get("warnings") or [])
    assert "non-canonical sampling overrides" not in warnings
    assert "fixed token budget max_tokens=4096" in warnings


def test_sampler_override_still_non_canonical_without_the_budget(tmp_path, capsys):
    rc, out, _err, result = _run(tmp_path, capsys, "--temperature", "0.7", "--max-tokens", "4096")
    assert rc == 0
    header = _header(out)
    assert "⚠ NON-CANONICAL (sampling: temperature=0.7)" in header
    assert "max_tokens=4096)" not in header  # the budget is not listed as a sampler...
    assert "[TOKEN BUDGET: fixed 4,096 per answer]" in header  # ...it is listed as a budget
    warnings = " | ".join(result.get("warnings") or [])
    assert "non-canonical sampling overrides active (temperature=0.7)" in warnings


def test_canonical_run_has_neither_tag(tmp_path, capsys):
    rc, out, _err, _result = _run(tmp_path, capsys)
    assert rc == 0
    header = _header(out)
    assert "NON-CANONICAL" not in header and "TOKEN BUDGET" not in header


def test_exit_on_regression_gates_at_the_same_budget(tmp_path, capsys):
    rc, *_ = _run(tmp_path, capsys, "--max-tokens", "4096", name="baseline.json")
    assert rc == 0
    baseline = str(tmp_path / "baseline.json")
    rc, _out, err, result = _run(
        tmp_path, capsys, "--max-tokens", "4096",
        "--previous-result", baseline, "--exit-on-regression", name="again.json",
    )
    assert rc == 0, err
    assert not any("token budget differs" in w for w in result["delta"]["warnings"])


def test_exit_on_regression_refuses_a_different_budget(tmp_path, capsys):
    _run(tmp_path, capsys, "--max-tokens", "4096", name="baseline.json")
    baseline = str(tmp_path / "baseline.json")
    rc, _out, err, _ = _run(
        tmp_path, capsys, "--max-tokens", "2048",
        "--previous-result", baseline, "--exit-on-regression", name="smaller.json",
    )
    assert rc == 1
    # --max-tokens also sets the thinking budget, so both differences are named
    assert ("token budget differs (current max_tokens=2048, previous max_tokens=4096; "
            "current thinking_max_tokens=2048, previous thinking_max_tokens=4096)") in err
    rc, _out, err, _ = _run(
        tmp_path, capsys,
        "--previous-result", baseline, "--exit-on-regression", name="packbudgets.json",
    )
    assert rc == 1
    assert "current pack budgets, previous max_tokens=4096" in err


def test_exit_on_regression_still_refuses_sampler_overrides(tmp_path, capsys):
    _run(tmp_path, capsys, name="baseline.json")
    rc, _out, err, _ = _run(
        tmp_path, capsys, "--temperature", "0.7",
        "--previous-result", str(tmp_path / "baseline.json"), "--exit-on-regression",
        name="sampled.json",
    )
    assert rc == 1
    assert "sampling overrides" in err


def test_delta_warns_when_budgets_differ(tmp_path, capsys):
    _run(tmp_path, capsys, name="baseline.json")
    rc, _out, _err, result = _run(
        tmp_path, capsys, "--max-tokens", "4096",
        "--previous-result", str(tmp_path / "baseline.json"), name="budgeted.json",
    )
    assert rc == 0
    assert any(
        "token budget differs (current max_tokens=4096, previous pack budgets; "
        "current thinking_max_tokens=4096, previous thinking_max_tokens=16384)" in w
        for w in result["delta"]["warnings"]
    )


def test_exit_on_regression_refuses_a_different_thinking_budget(tmp_path, capsys):
    # Same answer budget, different thinking budget: the gate used to pass it.
    _run(tmp_path, capsys, "--max-tokens", "4096", "--thinking-max-tokens", "16384", name="baseline.json")
    rc, _out, err, _ = _run(
        tmp_path, capsys, "--max-tokens", "4096", "--thinking-max-tokens", "8192",
        "--previous-result", str(tmp_path / "baseline.json"), "--exit-on-regression",
        name="lessthink.json",
    )
    assert rc == 1
    assert ("token budget differs (current thinking_max_tokens=8192, "
            "previous thinking_max_tokens=16384)") in err


def test_thinking_budget_is_not_compared_when_a_run_does_not_think(tmp_path, capsys):
    # A thinking-off run records no thinking budget; nothing to compare against.
    _run(tmp_path, capsys, "--no-thinking", "--max-tokens", "4096",
         "--thinking-max-tokens", "16384", name="baseline.json")
    rc, _out, err, result = _run(
        tmp_path, capsys, "--no-thinking", "--max-tokens", "4096", "--thinking-max-tokens", "8192",
        "--previous-result", str(tmp_path / "baseline.json"), "--exit-on-regression",
        name="again.json",
    )
    assert rc == 0, err
    assert not any("token budget differs" in w for w in result["delta"]["warnings"])
