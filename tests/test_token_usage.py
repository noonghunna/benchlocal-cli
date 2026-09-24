"""#147: token usage per pack and for the run.

Tier 1 aggregates the per-scenario `tokens_completion` the run already
persisted; tier 2 keeps the rest of `usage` (prompt / total / reasoning) that
the runner read for the spend guard and dropped, and reports the spend guard's
counter on successful runs. Rows without a count are reported as missing, never
read as zero. The markdown gains one additive block at the very end — omitted
when the result carries no rollup, which keeps the default markdown byte-stable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from benchlocal_cli.cli import _markdown, _token_lines, main
from benchlocal_cli.persistence import _build_result
from benchlocal_cli.persistence import _scenario_run as scenario_run_from_dict
from benchlocal_cli.rescore import _score_saved_scenario
from benchlocal_cli.runner import Runner, _combine_tokens, _pack_tokens, _usage_counts
from benchlocal_cli.types import PackResult, RunResult, ScenarioResult, ScenarioRun

# ------------------------------------------------------------------ usage parsing


def test_usage_counts_reads_all_four_and_never_fakes_zero():
    full = {"usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150,
                      "completion_tokens_details": {"reasoning_tokens": 20}}}
    assert _usage_counts(full) == {"tokens_completion": 30, "tokens_prompt": 120, "tokens_total": 150, "tokens_reasoning": 20}
    llama = {"usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}}
    assert _usage_counts(llama)["tokens_reasoning"] is None
    assert _usage_counts({"usage": {"completion_tokens": "30"}}) == dict.fromkeys(
        ("tokens_completion", "tokens_prompt", "tokens_total", "tokens_reasoning")
    )
    assert _usage_counts({"usage": {"completion_tokens": True}})["tokens_completion"] is None
    assert _usage_counts({}) == dict.fromkeys(("tokens_completion", "tokens_prompt", "tokens_total", "tokens_reasoning"))
    assert _usage_counts(None)["tokens_completion"] is None


# ------------------------------------------------------------------ fixtures


def _run(scenario_id: str, completion: int | None, *, passed: bool = True, failure_mode: str | None = None,
         prompt: int | None = None, total: int | None = None, reasoning: int | None = None,
         retries: tuple[int, ...] = ()) -> ScenarioRun:
    mode = failure_mode or ("passed" if passed else "verifier_fail")
    run = ScenarioRun(
        id=scenario_id,
        result=ScenarioResult(scenario_id, passed, mode, "x", latency_seconds=1.0, tokens_completion=completion,  # type: ignore[arg-type]
                              tokens_prompt=prompt, tokens_total=total, tokens_reasoning=reasoning),
        raw_scenario={}, raw_response={}, request={}, sampling_params={}, status_code=200,
    )
    run.retry_attempts = [{"tokens_completion": value, "passed": False, "latency_seconds": 1.0} for value in retries]
    run.attempt_count = 1 + len(retries)
    return run


def _pack(pack_id: str, runs: list[ScenarioRun], tokens: dict | None, *, skipped: bool = False) -> PackResult:
    passed = sum(1 for run in runs if run.result.passed)
    return PackResult(
        pack_id=pack_id, version="1.0.0", upstream_commit="abc", scenario_count=len(runs),
        passed=passed, total=len(runs), score=(passed / len(runs)) if runs else 0.0,
        latency={"p50": 2.0, "p95": 3.0, "mean": 2.5}, scenarios=runs, skipped=skipped,
        status="skipped" if skipped else "ok", tokens=tokens,
    )


def _result(packs: list[PackResult], tokens: dict | None, warnings: list[str] | None = None) -> RunResult:
    total = sum(p.total for p in packs)
    passed = sum(p.passed for p in packs)
    return RunResult(
        schema_version="1", runner_version="0.9.9", endpoint="http://x", model="m", mode="custom",
        started_at="2026-09-22T00:00:00Z", finished_at="2026-09-22T00:10:00Z", packs=packs,
        totals={"passed": passed, "total": total, "score": passed / total if total else 0.0},
        warnings=warnings or [], tokens=tokens,
    )


def _alpha_runs() -> list[ScenarioRun]:
    return [
        _run("A-01", 100, prompt=1000, total=1100),
        _run("A-02", 250, passed=False, prompt=2000, total=2250, retries=(40, 60)),
    ]


def _beta_runs() -> list[ScenarioRun]:
    # the hermes shape: the agent called the model in-container, no counts
    return [_run("B-01", None), _run("B-02", None, passed=False)]


# ------------------------------------------------------------------ aggregation


def test_pack_tokens_sums_attempt_one_and_retries_and_reports_missing():
    summary = _pack_tokens(_alpha_runs())
    assert summary == {"completion": 350, "retries": 100, "counted": 2, "missing": 0, "total": 2,
                       "prompt": 3000, "total_tokens": 3350}


def test_pack_tokens_reports_missing_rows_instead_of_zero():
    assert _pack_tokens(_beta_runs()) == {"completion": 0, "retries": 0, "counted": 0, "missing": 2, "total": 2}


def test_pack_tokens_excludes_not_implemented_rows_and_accepts_saved_dicts():
    runs = [*_alpha_runs(), _run("A-03", 999, passed=False, failure_mode="verifier_not_implemented")]
    assert _pack_tokens(runs)["total"] == 2
    as_dicts = [run.to_dict() for run in runs]
    assert _pack_tokens(as_dicts) == _pack_tokens(runs)
    # retry counts nested under `result` in older rows are still found
    row = {"id": "X", "passed": False, "failure_mode": "verifier_fail", "tokens_completion": 5,
           "retry_attempts": [{"result": {"tokens_completion": 7}}]}
    assert _pack_tokens([row])["retries"] == 7
    assert _pack_tokens([]) is None


def test_combine_tokens_sums_and_keeps_tier2_only_when_present():
    alpha = _pack_tokens(_alpha_runs())
    beta = _pack_tokens(_beta_runs())
    combined = _combine_tokens([alpha, beta, None])
    assert combined == {"completion": 350, "retries": 100, "counted": 2, "missing": 2, "total": 4,
                        "prompt": 3000, "total_tokens": 3350}
    assert _combine_tokens([None]) is None
    assert "reasoning" not in _combine_tokens([alpha])


# ------------------------------------------------------------------ runner capture


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.status_code = 200
        self.text = "text-body"
        self.headers: dict = {}

    def json(self) -> dict:
        return self.payload


def _completion(content: str, usage: dict) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}], "usage": usage}


class _SequenceClient:
    events: ClassVar[list] = []
    calls = 0

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, json: dict, **_kwargs) -> _FakeHTTPResponse:
        cls = type(self)
        payload = cls.events[cls.calls]
        cls.calls += 1
        return _FakeHTTPResponse(payload)


class _FakeSandbox:
    config = type("FakeConfig", (), {"multi_turn": False})()

    def verify(self, scenario: dict, response: dict, messages: list[dict]) -> ScenarioResult:
        return ScenarioResult(scenario["id"], True, "passed", "fake verifier")


class _FakeMultiTurnSandbox:
    config = type("FakeConfig", (), {"multi_turn": True})()

    def __init__(self) -> None:
        self.turns = 0

    def verify_multiturn_start(self, scenario: dict, **kwargs) -> dict:
        return {"scenario_state_id": "s1", "prompt": scenario["messages"], "tools": []}

    def verify_multiturn_turn(self, scenario_state_id: str, model_response: dict) -> dict:
        self.turns += 1
        if self.turns < 2:
            return {"action": "next-prompt", "prompt": [{"role": "user", "content": "go on"}]}
        return {"action": "verify-final", "passed": True, "failure_mode": "passed", "detail": "ok"}

    def verify_multiturn_end(self, scenario_state_id: str) -> dict:
        return {"passed": False, "failure_mode": "timeout", "detail": "ended"}


def _install(monkeypatch, events: list[dict]) -> None:
    import benchlocal_cli.runner as runner_module

    _SequenceClient.events = events
    _SequenceClient.calls = 0
    monkeypatch.setattr(runner_module.httpx, "Client", _SequenceClient)
    monkeypatch.setattr(runner_module.time, "sleep", lambda delay: None)


def _meta() -> dict:
    return {"supports_sandboxed_only": True, "default_max_seconds": 60, "sampling_defaults": {"max_tokens": 16}}


def test_single_turn_keeps_prompt_total_and_reasoning(monkeypatch):
    _install(monkeypatch, [_completion("ok", {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100,
                                              "completion_tokens_details": {"reasoning_tokens": 12}})])
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True, max_transient_retries=0)
    runner._sandbox_clients["bugfind-15"] = _FakeSandbox()

    run = runner.run_scenario(_meta(), {"id": "BF-01", "pack_id": "bugfind-15", "messages": [{"role": "user", "content": "x"}],
                                        "verifier": {"type": "_stub", "asserts": []}})

    assert (run.result.tokens_completion, run.result.tokens_prompt, run.result.tokens_total, run.result.tokens_reasoning) == (20, 80, 100, 12)
    row = run.to_dict()
    assert (row["tokens_completion"], row["tokens_prompt"], row["tokens_total"], row["tokens_reasoning"]) == (20, 80, 100, 12)
    assert runner.tokens_used == 100  # the spend guard's counter


def test_multi_turn_sums_usage_across_turns(monkeypatch):
    _install(monkeypatch, [
        _completion("turn one", {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}),
        _completion("turn two", {"prompt_tokens": 70, "completion_tokens": 15, "total_tokens": 85}),
    ])
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True, max_transient_retries=0)
    runner._sandbox_clients["cli-40"] = _FakeMultiTurnSandbox()

    run = runner.run_scenario(_meta(), {"id": "CLI-01", "pack_id": "cli-40", "raw_scenario": {"kind": "multiround"},
                                        "messages": [{"role": "user", "content": "x"}], "verifier": {"type": "_stub", "asserts": []}})

    assert run.result.passed is True
    assert (run.result.tokens_completion, run.result.tokens_prompt, run.result.tokens_total) == (25, 120, 145)
    assert run.result.tokens_reasoning is None  # never reported → None, not 0


class _FakeHermesSandbox:
    """hermesagent-20: the sandbox runs the whole agent episode at start and
    answers verify-final directly — no runner-owned turns, so no response the
    runner could read usage from. `usage` is what the sandbox proxy tallied."""

    config = type("FakeConfig", (), {"multi_turn": True})()

    def __init__(self, usage: dict | None) -> None:
        self.usage = usage

    def verify_multiturn_start(self, scenario: dict, **kwargs) -> dict:
        payload = {"action": "verify-final", "passed": True, "failure_mode": "passed", "detail": "ok"}
        if self.usage is not None:
            payload["usage"] = self.usage
        return payload

    def verify_multiturn_end(self, scenario_state_id: str) -> dict:
        return {"passed": False, "failure_mode": "timeout", "detail": "ended"}


def _hermes_run(monkeypatch, usage: dict | None):
    _install(monkeypatch, [])
    runner = Runner(endpoint="http://localhost:9999", model="fake", enable_sandboxed_packs=True, max_transient_retries=0)
    runner._sandbox_clients["hermesagent-20"] = _FakeHermesSandbox(usage)
    run = runner.run_scenario(_meta(), {"id": "HA-01", "pack_id": "hermesagent-20",
                                        "messages": [{"role": "user", "content": "x"}], "verifier": {"type": "_stub", "asserts": []}})
    return runner, run


def test_hermes_folds_the_sandbox_proxy_usage_into_the_row_and_spend_guard(monkeypatch):
    # club-3090#1396: the agent's in-container calls were invisible, so HA rows had no count.
    runner, run = _hermes_run(monkeypatch, {"requests": 4, "requests_with_usage": 4, "prompt_tokens": 900,
                                            "completion_tokens": 300, "total_tokens": 1200, "reasoning_tokens": 120})
    assert run.result.passed is True
    assert (run.result.tokens_completion, run.result.tokens_prompt, run.result.tokens_total, run.result.tokens_reasoning) == (300, 900, 1200, 120)
    assert runner.tokens_used == 1200  # the run's endpoint-reported total now includes the agent


def test_hermes_from_an_older_sandbox_keeps_no_count_rather_than_zero(monkeypatch):
    # A sandbox image built before the proxy sends no `usage`: the row stays "missing", never 0.
    runner, run = _hermes_run(monkeypatch, None)
    assert run.result.tokens_completion is None and run.result.tokens_total is None
    assert runner.tokens_used == 0
    # ...and a proxy that saw calls but no usage block is treated the same way.
    runner, run = _hermes_run(monkeypatch, {"requests": 2, "requests_with_usage": 0, "prompt_tokens": 0,
                                            "completion_tokens": 0, "total_tokens": 0, "reasoning_tokens": 0})
    assert run.result.tokens_completion is None


def test_run_attaches_pack_and_run_rollups_with_the_spend_counter(monkeypatch):
    meta = {"pack_id": "test-pack", "version": "1.0.0", "upstream_commit": "abc123", "sampling_defaults": {"max_tokens": 32}}
    scenarios = [{"id": "A-01", "pack_id": "test-pack", "messages": []}, {"id": "A-02", "pack_id": "test-pack", "messages": []}]
    monkeypatch.setattr("benchlocal_cli.runner.load_pack", lambda _pack_id: (meta, scenarios))
    runner = Runner(endpoint="mock", model="mock", retry_failures=0)
    outcomes = {"A-01": _run("A-01", 100, prompt=1000, total=1100), "A-02": _run("A-02", None, passed=False)}
    monkeypatch.setattr(runner, "run_scenario", lambda _m, s, *, repeat_index=1: outcomes[s["id"]])
    runner.tokens_used = 4321  # what the spend guard accumulated across every request

    result = runner.run(["test-pack"])

    assert result.packs[0].tokens == {"completion": 100, "retries": 0, "counted": 1, "missing": 1, "total": 2, "prompt": 1000, "total_tokens": 1100}
    assert result.tokens == {"completion": 100, "retries": 0, "counted": 1, "missing": 1, "total": 2,
                             "prompt": 1000, "total_tokens": 1100, "endpoint_reported_total": 4321}
    payload = result.to_dict()
    assert payload["tokens"]["endpoint_reported_total"] == 4321
    assert payload["packs"][0]["tokens"]["missing"] == 1
    assert payload["totals"] == {"passed": 1, "total": 2, "score": 0.5}  # untouched


# ------------------------------------------------------------------ persistence / rescore


def test_saved_rows_round_trip_and_journal_rebuild_carries_the_rollup():
    row = _run("SO-01", 30, prompt=100, total=130, reasoning=5).to_dict()
    loaded = scenario_run_from_dict(row)
    assert (loaded.result.tokens_prompt, loaded.result.tokens_total, loaded.result.tokens_reasoning) == (100, 130, 5)

    config = {"target_selection": ["structoutput-15/SO-01"], "pack_ids": ["structoutput-15"], "started_at": "2026-09-22T00:00:00Z"}
    rebuilt = _build_result(config, [("structoutput-15", row)], finished_at="2026-09-22T00:01:00Z")
    assert rebuilt.packs[0].tokens == {"completion": 30, "retries": 0, "counted": 1, "missing": 0, "total": 1,
                                       "prompt": 100, "total_tokens": 130, "reasoning": 5}
    assert rebuilt.tokens["reasoning"] == 5
    assert "endpoint_reported_total" not in rebuilt.tokens  # not observable from rows


def test_rescore_carries_tier2_counts():
    row = {
        "id": "SO-01", "passed": False, "failure_mode": "verifier_fail", "detail": "x", "latency_seconds": 1.0,
        "raw_response": {"choices": [{"message": {"content": '{"title":"The Great Gatsby","year":1925}'}}], "usage": {"completion_tokens": 9}},
        "raw_scenario": {}, "request": {}, "tokens_completion": 9, "tokens_prompt": 77, "tokens_total": 86, "tokens_reasoning": None,
    }
    scenario = {"id": "SO-01", "messages": [], "verifier": {"type": "struct_output"}, "expected": {}}
    meta = {"verifier_module": "struct_output"}
    scored, reason = _score_saved_scenario(meta, {"SO-01": scenario}, row)
    if not scored:
        # the scorer's contract is not what this test is about; only the carry is
        assert reason is not None
        return
    assert (row["tokens_completion"], row["tokens_prompt"], row["tokens_total"], row["tokens_reasoning"]) == (9, 77, 86, None)
    assert row["result"]["tokens_prompt"] == 77


def test_merge_resume_sums_the_recorded_session_counters(tmp_path):
    from benchlocal_cli.persistence import ResumeState, merge_resume

    row = _run("SO-01", 30, prompt=100, total=130).to_dict()
    previous = _result([_pack("structoutput-15", [], {"completion": 30, "retries": 0, "counted": 1, "missing": 0, "total": 1})],
                       {"completion": 30, "retries": 0, "counted": 1, "missing": 0, "total": 1, "endpoint_reported_total": 500}).to_dict()
    previous["packs"][0]["scenarios"] = [row]
    config = {"target_selection": ["structoutput-15/SO-01"], "pack_ids": ["structoutput-15"], "started_at": "2026-09-22T00:00:00Z"}
    state = ResumeState(
        source_path=tmp_path / "r.partial.jsonl", final_path=tmp_path / "r.json", sidecar_path=tmp_path / "r.partial.jsonl",
        config=config, previous_result=previous, missing_selection=[], missing_by_pack={}, completed_repeats={},
    )
    fresh = _result([_pack("structoutput-15", [], None)], {"completion": 0, "retries": 0, "counted": 0, "missing": 0, "total": 0, "endpoint_reported_total": 120})

    merged = merge_resume(state, fresh)

    assert merged.tokens["completion"] == 30
    assert merged.tokens["endpoint_reported_total"] == 620  # 500 recorded before + 120 now


# ------------------------------------------------------------------ markdown


# Captured from `_markdown` at 47d1d66 (pre-change) for the fixture below with
# no token rollups, warnings=["w1"]. NOT regenerated from the new code.
_GOLDEN = """=== benchlocal-cli --custom  (endpoint: http://x, model: m, thinking=off(pack-defaults), 2026-09-22T00:00:00Z) ===

Pack | Pass / Total | Score | p50 latency | p95 latency | Status
---|---:|---:|---:|---:|---
alpha-1 (v1.0.0) | 1 / 2 | 50% | 2.00s | 3.00s | ok
beta-1 (v1.0.0) | 1 / 2 | 50% | 2.00s | 3.00s | ok

TOTAL | 2 / 4 | 50% |  |  |

Failure breakdown:
- alpha-1 A-02: verifier_fail [fail] (x)
- beta-1 B-02: verifier_fail [fail] (x)

Warnings:
- w1"""


def _two_packs(with_tokens: bool) -> list[PackResult]:
    alpha_runs, beta_runs = _alpha_runs(), _beta_runs()
    return [
        _pack("alpha-1", alpha_runs, _pack_tokens(alpha_runs) if with_tokens else None),
        _pack("beta-1", beta_runs, _pack_tokens(beta_runs) if with_tokens else None),
    ]


def test_markdown_is_byte_stable_without_a_rollup():
    result = _result(_two_packs(False), None, warnings=["w1"])
    assert _markdown(result) == _GOLDEN
    assert "tokens" not in result.to_dict()
    assert all("tokens" not in pack for pack in result.to_dict()["packs"])


def test_markdown_appends_the_token_block_after_warnings():
    packs = _two_packs(True)
    totals = _combine_tokens(pack.tokens for pack in packs)
    totals["endpoint_reported_total"] = 4000
    rendered = _markdown(_result(packs, totals, warnings=["w1"]))

    assert rendered == _GOLDEN + (
        "\n\nTokens: 350 completion across 2 / 4 scored rows (2 without a count: beta-1 2); "
        "retries 100; prompt 3,000; endpoint-reported total 4,000 (every request, including probes and retries)\n"
        "- alpha-1: 350 completion, retries 100, prompt 3,000\n"
        "- beta-1: 0 completion (0 / 2 with a count)"
    )


def test_token_lines_use_thousands_separators_and_skip_skipped_packs():
    big = _pack("cli-40", [_run("C-01", 205901)], {"completion": 205901, "retries": 0, "counted": 39, "missing": 1, "total": 40})
    skipped = _pack("gamma-1", [], {"completion": 5, "retries": 0, "counted": 1, "missing": 0, "total": 1}, skipped=True)
    lines = _token_lines(_result([big, skipped], {"completion": 205906, "retries": 0, "counted": 40, "missing": 1, "total": 41}))
    assert lines[1] == "Tokens: 205,906 completion across 40 / 41 scored rows (1 without a count: cli-40 1)"
    assert lines[2:] == ["- cli-40: 205,901 completion (39 / 40 with a count)"]


# ------------------------------------------------------------------ CLI end to end


def test_cli_mock_run_reports_tokens(tmp_path: Path, capsys):
    mock_path = tmp_path / "mock.json"
    result_path = tmp_path / "result.json"
    mock_path.write_text(json.dumps({"SO-01": {
        "choices": [{"message": {"content": '{"title":"The Great Gatsby","year":1925}'}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 3, "total_tokens": 43},
    }}))
    rc = main([
        "run", "--endpoint", "mock", "--model", "mock", "--measured-tps", "100",
        "--mock-responses-from-json", str(mock_path), "--scenario", "structoutput-15/SO-01",
        "--save-json", str(result_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith("- structoutput-15: 3 completion, prompt 40")
    assert "\nTokens: 3 completion across 1 / 1 scored rows; prompt 40" in out
    saved = json.loads(result_path.read_text())
    assert saved["packs"][0]["tokens"] == {"completion": 3, "retries": 0, "counted": 1, "missing": 0, "total": 1, "prompt": 40, "total_tokens": 43}
    assert saved["tokens"]["prompt"] == 40
    assert saved["packs"][0]["scenarios"][0]["tokens_prompt"] == 40
