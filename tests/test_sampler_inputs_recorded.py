"""#188: --thinking-sampler and --extra-body are recorded in the results JSON.

The resume journal kept both, the result dropped both. So a results JSON could
not tell a canonical thinking leg from one whose sampler --thinking-sampler had
replaced, nor a run whose --extra-body carried temperature / presence_penalty
from one that did not. A --retry-failed of such a run re-ran under a different
sampler and classified the difference as flaky/systematic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchlocal_cli.cli import main
from benchlocal_cli.persistence import result_from_journal
from benchlocal_cli.runner import Runner

SAMPLER = {"temperature": 0.6, "top_p": 0.95, "top_k": 20}
EXTRA = {"presence_penalty": 1.5}
GOOD = '{"title":"The Great Gatsby","year":1925}'


def _args(tmp_path: Path, out: Path, *extra: str, content: str = GOOD) -> list[str]:
    mock = tmp_path / "mock.json"
    mock.write_text(json.dumps({"SO-01": {"choices": [{"message": {"content": content}}]}}))
    return [
        "run", "--endpoint", "mock", "--model", "mock",
        "--scenario", "structoutput-15/SO-01", "--measured-tps", "100",
        "--mock-responses-from-json", str(mock), "--save-json", str(out), *extra,
    ]


def _with_both(*extra: str) -> tuple[str, ...]:
    return ("--thinking-sampler", json.dumps(SAMPLER), "--extra-body", json.dumps(EXTRA), *extra)


def test_recorded_on_a_thinking_run(tmp_path, capsys):
    out = tmp_path / "r.json"
    assert main(_args(tmp_path, out, *_with_both("--enable-thinking"))) == 0
    result = json.loads(out.read_text())
    assert result["thinking_sampler"] == SAMPLER
    assert result["extra_body"] == EXTRA


def test_thinking_sampler_not_recorded_where_it_has_no_effect(tmp_path, capsys):
    out = tmp_path / "r.json"
    assert main(_args(tmp_path, out, *_with_both("--no-thinking"))) == 0
    result = json.loads(out.read_text())
    assert "thinking_sampler" not in result  # thinking off: the flag changed nothing
    assert result["extra_body"] == EXTRA  # merged into every request either way


def test_canonical_result_is_unchanged(tmp_path, capsys):
    out = tmp_path / "r.json"
    assert main(_args(tmp_path, out)) == 0
    result = json.loads(out.read_text())
    assert "thinking_sampler" not in result and "extra_body" not in result


def test_results_card_names_them(tmp_path, capsys):
    out, card = tmp_path / "r.json", tmp_path / "card.md"
    assert main(_args(tmp_path, out, *_with_both("--enable-thinking"),
                      "--report", "md", "--report-out", str(card))) == 0
    row = next(line for line in card.read_text().splitlines() if line.startswith("Sampling |"))
    assert "extra-body presence_penalty=1.5" in row
    assert "thinking sampler temperature=0.6, top_p=0.95, top_k=20" in row


def test_retry_failed_reruns_under_the_baseline_sampler(tmp_path, capsys):
    baseline = tmp_path / "baseline.json"
    # A wrong answer, so the retry has a failed scenario to re-run.
    assert main(_args(tmp_path, baseline, *_with_both("--enable-thinking"), content="nope")) == 0
    retry = tmp_path / "retry.json"
    mock = tmp_path / "mock.json"
    assert main([
        "run", "--retry-failed", "1", "--previous-result", str(baseline),
        "--measured-tps", "100", "--mock-responses-from-json", str(mock),
        "--save-json", str(retry),
    ]) == 0
    result = json.loads(retry.read_text())
    assert result["thinking_sampler"] == SAMPLER
    assert result["extra_body"] == EXTRA


def test_journal_recovered_result_keeps_them(tmp_path, monkeypatch, capsys):
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
        main(_args(tmp_path, out, *_with_both("--enable-thinking"), "--repeat", "2", "--incremental"))
    monkeypatch.setattr(Runner, "run_scenario", original)

    sidecar = Path(f"{out}.partial.jsonl")
    recovered = result_from_journal(sidecar)
    assert recovered["thinking_sampler"] == SAMPLER
    assert recovered["extra_body"] == EXTRA

    # and a resume finishes with them recorded
    assert main(["run", "--resume", str(sidecar),
                 "--mock-responses-from-json", str(tmp_path / "mock.json")]) == 0
    final = json.loads(out.read_text())
    assert final["thinking_sampler"] == SAMPLER
    assert final["extra_body"] == EXTRA
