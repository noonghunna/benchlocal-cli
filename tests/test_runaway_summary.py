"""#148: runaways (token_limit / timeout / agent_runner_timeout) are surfaced
separately from verifier_fail — without touching the score and without
changing a single byte of the default markdown.

The byte-stability half is the regression that would break club-3090's
`quality-test.sh`, so it is pinned against a literal captured from the
pre-#148 `_markdown` rather than against the current code.
"""

from __future__ import annotations

from dataclasses import replace

from benchlocal_cli.cli import _markdown
from benchlocal_cli.diagnostics import combine_runaway, runaway_summary
from benchlocal_cli.persistence import _aggregate_pack
from benchlocal_cli.persistence import _scenario_run as _scenario_run_from_dict
from benchlocal_cli.rescore import _recompute_pack, _recompute_totals
from benchlocal_cli.runner import Runner
from benchlocal_cli.types import PackResult, RunResult, ScenarioResult, ScenarioRun

# Captured verbatim from `_markdown` at 47d1d66 (pre-#148) for the fixture in
# `_golden_result()`. Do NOT regenerate this from the current code: the point
# is that the default output has not moved.
_GOLDEN = "\n".join(
    [
        "=== benchlocal-cli --custom  (endpoint: mock, model: mock-model, thinking=off, "
        "2026-07-30T00:00:00Z) ===",
        "",
        "Pack | Pass / Total | Score | p50 latency | p95 latency | Status",
        "---|---:|---:|---:|---:|---",
        "alpha-2 (v1.0.0) | 1 / 2 | 50% | 2.00s | 2.00s | ok",
        "beta-1 (v1.0.0) | 1 / 1 | 100% | 3.00s | 3.00s | ok",
        "",
        "TOTAL | 2 / 3 | 67% |  |  |",
        "",
        "Failure breakdown:",
        "- alpha-2 A-02: verifier_fail [fail] (expected 42)",
    ]
)


def _scenario(sid: str, passed: bool, mode: str, detail: str, latency: float) -> ScenarioRun:
    return ScenarioRun(
        id=sid,
        result=ScenarioResult(
            scenario_id=sid,
            passed=passed,
            failure_mode=mode,  # type: ignore[arg-type]
            detail=detail,
            latency_seconds=latency,
        ),
        raw_scenario={"id": sid},
        raw_response={"choices": []},
        request={},
        sampling_params={"temperature": 0},
        status_code=200,
    )


def _pack(pack_id: str, runs: list[ScenarioRun]) -> PackResult:
    passed = sum(1 for run in runs if run.result.passed)
    latencies = sorted(run.result.latency_seconds for run in runs)
    return PackResult(
        pack_id=pack_id,
        version="1.0.0",
        upstream_commit="abc123",
        scenario_count=len(runs),
        passed=passed,
        total=len(runs),
        score=passed / len(runs),
        latency={"p50": latencies[len(latencies) // 2], "p95": latencies[-1]},
        scenarios=runs,
    )


def _golden_result() -> RunResult:
    packs = [
        _pack(
            "alpha-2",
            [
                _scenario("A-01", True, "passed", "correct", 1.0),
                _scenario("A-02", False, "verifier_fail", "expected 42", 2.0),
            ],
        ),
        _pack("beta-1", [_scenario("B-01", True, "passed", "correct", 3.0)]),
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
        totals={"passed": 2, "total": 3, "score": 2 / 3},
        thinking_enabled=False,
        thinking_mode="force-off",
    )


# ---------------------------------------------------------------------------
# Byte-stability of the default markdown
# ---------------------------------------------------------------------------


def test_default_markdown_is_byte_identical_to_pre_148_golden():
    # A result with no rollup at all (hand-built / pre-#148 JSON).
    assert _markdown(_golden_result()) == _GOLDEN


def test_zero_runaway_rollup_leaves_markdown_byte_identical():
    # The same result carrying the rollup the aggregator now attaches. With a
    # zero count it must render exactly the same bytes: no line, no blank.
    result = _golden_result()
    result.packs = [replace(pack, runaway=runaway_summary(pack.scenarios)) for pack in result.packs]
    result.runaway = combine_runaway(pack.runaway for pack in result.packs)

    assert result.runaway == {
        "count": 0,
        "total": 3,
        "rate": 0.0,
        "modes": {"token_limit": 0, "timeout": 0, "agent_runner_timeout": 0},
    }
    assert _markdown(result) == _GOLDEN
    # ...while the JSON does carry it, where there is no byte-stability constraint.
    assert result.to_dict()["runaway"]["count"] == 0
    assert result.to_dict()["packs"][0]["runaway"]["total"] == 2


# ---------------------------------------------------------------------------
# runaway_summary / combine_runaway
# ---------------------------------------------------------------------------


def test_runaway_summary_counts_attempt_one_rows_only():
    runs = [
        _scenario("S-01", True, "passed", "ok", 1.0),
        _scenario("S-02", False, "verifier_fail", "wrong", 1.0),
        _scenario("S-03", False, "token_limit", "cut", 1.0),
        _scenario("S-04", False, "timeout", "slow", 1.0),
        _scenario("S-05", False, "agent_runner_timeout", "episode", 1.0),
        _scenario("S-06", False, "verifier_not_implemented", "n/a", 1.0),
    ]
    # A rescued retry does not un-count the attempt-1 runaway: pass@1 is
    # charged for attempt 1, and so is this.
    runs[2].retry_attempts = [{"passed": True, "failure_mode": "passed"}]
    runs[2].label = "pass@2"

    assert runaway_summary(runs) == {
        "count": 3,
        "total": 5,
        "rate": 0.6,
        "modes": {"token_limit": 1, "timeout": 1, "agent_runner_timeout": 1},
    }


def test_runaway_summary_accepts_saved_json_rows_and_ignores_passed_length_hits():
    rows = [
        # A verified-correct answer that hit the cap is still a pass (#61).
        {"id": "S-01", "passed": True, "failure_mode": "passed"},
        {"id": "S-02", "result": {"passed": False, "failure_mode": "timeout"}},
        {"id": "S-03", "passed": False, "failure_mode": "wrong_answer"},
    ]
    assert runaway_summary(rows) == {
        "count": 1,
        "total": 3,
        "rate": 1 / 3,
        "modes": {"token_limit": 0, "timeout": 1, "agent_runner_timeout": 0},
    }


def test_runaway_summary_is_none_for_empty_or_stubbed_packs():
    assert runaway_summary([]) is None
    assert runaway_summary([_scenario("S-01", False, "verifier_not_implemented", "", 0.0)]) is None
    assert combine_runaway([None, None]) is None


def test_combine_runaway_sums_packs():
    combined = combine_runaway(
        [
            {"count": 2, "total": 10, "rate": 0.2, "modes": {"token_limit": 2}},
            None,
            {"count": 1, "total": 5, "rate": 0.2, "modes": {"agent_runner_timeout": 1}},
        ]
    )
    assert combined == {
        "count": 3,
        "total": 15,
        "rate": 0.2,
        "modes": {"token_limit": 2, "timeout": 0, "agent_runner_timeout": 1},
    }


# ---------------------------------------------------------------------------
# End to end through Runner.run → markdown + JSON
# ---------------------------------------------------------------------------


def _run(monkeypatch, outcomes: list[tuple[bool, str]]) -> RunResult:
    meta = {
        "pack_id": "test-pack",
        "version": "1.0.0",
        "upstream_commit": "abc123",
        "sampling_defaults": {"max_tokens": 32},
    }
    scenarios = [
        {"id": f"T-{index:02d}", "pack_id": "test-pack", "messages": [{"role": "user", "content": "x"}]}
        for index in range(1, len(outcomes) + 1)
    ]
    monkeypatch.setattr("benchlocal_cli.runner.load_pack", lambda _pack_id: (meta, scenarios))
    runner = Runner(endpoint="mock", model="mock", inline_retries_enabled=False)
    by_id = {
        scenario["id"]: outcome for scenario, outcome in zip(scenarios, outcomes, strict=True)
    }

    def fake_run_scenario(_meta, scenario, *, repeat_index=1):
        passed, mode = by_id[scenario["id"]]
        return _scenario(scenario["id"], passed, mode, f"detail {mode}", 1.0)

    monkeypatch.setattr(runner, "run_scenario", fake_run_scenario)
    return runner.run(["test-pack"])


def test_runner_reports_runaways_in_markdown_and_json_without_rescoring(monkeypatch):
    result = _run(
        monkeypatch,
        [(True, "passed"), (False, "verifier_fail"), (False, "token_limit"), (False, "timeout")],
    )

    # Score arithmetic untouched: a runaway is still a fail.
    assert result.totals == {"passed": 1, "total": 4, "score": 0.25}
    assert result.packs[0].score == 0.25
    assert result.runaway == {
        "count": 2,
        "total": 4,
        "rate": 0.5,
        "modes": {"token_limit": 1, "timeout": 1, "agent_runner_timeout": 0},
    }

    rendered = _markdown(result)
    lines = rendered.splitlines()
    summary = (
        "Runaway: 2 / 4 scenarios never finished (token_limit 1, timeout 1) — still counted "
        "as failures. Each is either a budget artifact (the cap cut off an answer still in "
        "progress) or a model loop that would never finish; inspect the output to tell which."
    )
    assert summary in lines
    assert "- test-pack: 2 / 4 (token_limit 1, timeout 1)" in lines
    # Additive placement: after the TOTAL row, before the failure breakdown, so
    # the table a parser reads first is untouched.
    total_index = next(i for i, line in enumerate(lines) if line.startswith("TOTAL |"))
    breakdown_index = lines.index("Failure breakdown:")
    assert total_index < lines.index(summary) < breakdown_index
    assert lines[lines.index(summary) - 1] == ""

    data = result.to_dict()
    assert data["runaway"] == result.runaway
    assert data["packs"][0]["runaway"] == result.runaway


def test_runner_omits_runaway_line_when_every_failure_is_a_verdict(monkeypatch):
    result = _run(monkeypatch, [(True, "passed"), (False, "verifier_fail")])

    assert result.runaway["count"] == 0
    assert "Runaway" not in _markdown(result)
    assert "runaway" in result.to_dict()


# ---------------------------------------------------------------------------
# Journal/resume rebuild and rescore recompute agree with the live run
# ---------------------------------------------------------------------------


def test_journal_rebuild_matches_live_rollup(monkeypatch):
    live = _run(
        monkeypatch,
        [(True, "passed"), (False, "agent_runner_timeout"), (False, "verifier_fail")],
    )
    meta = {"pack_id": "test-pack", "version": "1.0.0", "upstream_commit": "abc123"}
    monkeypatch.setattr("benchlocal_cli.persistence.load_pack", lambda _pack_id: (meta, []))
    rows = [_scenario_run_from_dict(run.to_dict()) for run in live.packs[0].scenarios]

    rebuilt = _aggregate_pack(
        "test-pack",
        rows,
        scenario_count=3,
        catalog_scenario_count=None,
        repeat=1,
        thinking_override=None,
        retry_failures=0,
        retry_runaways=False,
    )

    assert rebuilt.runaway == live.packs[0].runaway
    assert rebuilt.runaway["modes"]["agent_runner_timeout"] == 1


def test_rescore_recomputes_pack_and_run_rollups():
    pack = {
        "pack_id": "test-pack",
        "scenarios": [
            {"id": "S-01", "passed": True, "failure_mode": "passed"},
            {"id": "S-02", "passed": False, "failure_mode": "token_limit"},
        ],
        "runaway": {"count": 99, "total": 99, "rate": 1.0, "modes": {}},
    }
    data = {"packs": [pack, {"pack_id": "skipped-1", "skipped": True, "scenarios": []}]}

    _recompute_pack(pack)
    _recompute_totals(data)

    assert pack["runaway"] == {
        "count": 1,
        "total": 2,
        "rate": 0.5,
        "modes": {"token_limit": 1, "timeout": 0, "agent_runner_timeout": 0},
    }
    assert data["runaway"] == pack["runaway"]
    assert data["totals"] == {"passed": 1, "total": 2, "score": 0.5}
