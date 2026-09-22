"""#145: --budget-from-timeout derives each request's token ceiling from the clock
that governs it (headroom x clock x measured TPS), delivers it as max_tokens
plus — on llama.cpp — the reasoning share, proves the knob is honoured at
startup, and records the budget with the score.

The numbers used here are the ones observed on the rig: llama.cpp serving a 9B
reasoning model at ~46 tok/s reached a 12,288-token ceiling in ~262 s, while an
unbounded arm hit the 900 s per-case clock instead; and a reasoning cap alone
(8,192, no total cap) still let a request reach 13,238 tokens through content.
"""

from __future__ import annotations

import json
from typing import ClassVar

import pytest

from benchlocal_cli.cli import _markdown, main
from benchlocal_cli.persistence import _build_result, _infer_config
from benchlocal_cli.runner import (
    _BUDGET_CONTROL_TOKENS,
    _REASONING_BUDGET_KEYS,
    Runner,
)
from benchlocal_cli.types import PackResult, RunResult, ScenarioResult, ScenarioRun

TPS = 46.0


def _thinking_meta(**overrides) -> dict:
    meta = {
        "pack_id": "reasonmath-15",
        "version": "1.0.0",
        "upstream_commit": "abc123",
        "sampling_defaults": {"max_tokens": 16384, "temperature": 0, "top_p": 1},
        "timeout_baseline_tokens": 1024,
        "default_thinking": "on",
        "default_max_seconds": 60,
    }
    meta.update(overrides)
    return meta


def _plain_meta(**overrides) -> dict:
    meta = {
        "pack_id": "toolcall-15",
        "version": "1.0.0",
        "upstream_commit": "abc123",
        "sampling_defaults": {"max_tokens": 1024, "temperature": 0, "top_p": 1},
        "default_thinking": "off",
        "default_max_seconds": 60,
    }
    meta.update(overrides)
    return meta


def _scenario(pack_id: str = "reasonmath-15", scenario_id: str = "RM-01") -> dict:
    return {"id": scenario_id, "pack_id": pack_id, "messages": [{"role": "user", "content": "solve"}]}


def _runner(**kwargs) -> Runner:
    defaults = dict(
        endpoint="http://localhost:9999",
        model="fake",
        measured_tps=TPS,
        budget_from_timeout=True,
        thinking_max_tokens=65536,
    )
    defaults.update(kwargs)
    return Runner(**defaults)


def _prepared(runner: Runner, family: str = "llama.cpp", pack_ids: list[str] | None = None, meta: dict | None = None):
    """Run the startup preparation with the engine family pinned and the pack
    catalogue faked, returning the warnings it produced."""
    warnings: list[str] = []
    runner._detect_engine_family = lambda: family  # type: ignore[method-assign]
    import benchlocal_cli.runner as runner_module

    fake_meta = meta or _thinking_meta()
    original = runner_module.load_pack
    runner_module.load_pack = lambda _pack_id: (fake_meta, [])  # type: ignore[assignment]
    try:
        runner._prepare_token_budget(pack_ids or ["reasonmath-15"], warnings)
    finally:
        runner_module.load_pack = original
    return warnings


# ------------------------------------------------------------------ arithmetic


def test_derivation_matches_the_observed_rig_numbers():
    runner = _runner()
    # 900 s clock at 46 tok/s with 0.8 headroom: 33,120 tokens — well under the
    # 65,536 ceiling, so the clock binds and the ceiling never would have.
    assert runner._tokens_for_clock(900, TPS) == 33120
    # The 262 s it took to reach 12,288 tokens: at that clock the derived
    # budget (9,641) is BELOW the ceiling — the clock cuts first, which is the
    # failure the issue describes.
    assert runner._tokens_for_clock(262, TPS) == 9641
    assert runner._tokens_for_clock(262, TPS) < 12288


def test_headroom_must_be_a_fraction_of_the_clock():
    with pytest.raises(ValueError):
        _runner(budget_headroom=1.5)
    with pytest.raises(ValueError):
        _runner(budget_headroom=0)
    assert _runner(budget_headroom=1.0).budget_headroom == 1.0


def test_answer_reserve_is_the_pack_baseline_capped_at_a_quarter():
    runner = _runner()
    assert runner._answer_reserve(_thinking_meta(), 12288) == 1024
    assert runner._answer_reserve(_thinking_meta(), 2000) == 500  # quarter of a small budget
    # baseline falls back to sampling_defaults.max_tokens
    assert runner._answer_reserve(_plain_meta(), 4000) == 1000


# ------------------------------------------------------------ startup prepare


def test_prepare_refuses_without_a_measured_rate():
    runner = Runner(endpoint="mock", model="mock", budget_from_timeout=True, mock_responses={"X": {}})
    runner._endpoint_reachable = lambda timeout=5.0: False  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="--measured-tps"):
        runner._prepare_token_budget(["toolcall-15"], [])


def test_prepare_is_a_no_op_when_the_flag_is_off():
    runner = _runner(budget_from_timeout=False)
    runner._prepare_token_budget(["reasonmath-15"], [])
    assert runner._token_budget_report is None


def test_prepare_records_engine_knob_and_control_on_llama_cpp():
    runner = _runner(budget_control=False)
    warnings = _prepared(runner, family="llama.cpp")

    report = runner._token_budget_report
    assert report["mode"] == "derived"
    assert report["measured_tps"] == TPS
    assert report["engine"] == "llama.cpp"
    assert report["reasoning_keys"] == list(_REASONING_BUDGET_KEYS)
    assert report["control"] == {"status": "skipped", "reason": "--no-budget-control"}
    assert warnings == []


def test_prepare_warns_loudly_when_the_engine_has_no_reasoning_knob():
    runner = _runner()
    warnings = _prepared(runner, family="unknown")

    report = runner._token_budget_report
    assert report["reasoning_keys"] is None
    assert report["control"]["status"] == "skipped"
    assert len(warnings) == 1
    assert "max_tokens only" in warnings[0]
    assert "reasonmath-15" in warnings[0]


def test_prepare_warns_when_extra_body_pins_a_budget_key():
    runner = _runner(budget_control=False, extra_body={"reasoning_budget_tokens": 4096})
    warnings = _prepared(runner)

    assert runner._token_budget_report["extra_body_locked"] == ["reasoning_budget_tokens"]
    assert any("reasoning_budget_tokens" in w and "left as given" in w for w in warnings)


# ------------------------------------------------------------ positive control


class _FakeHTTPResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = "text-body"
        self.headers: dict = {}

    def json(self) -> dict:
        return self.payload


class _CapturingClient:
    """Answers every POST with a scripted completion and records the bodies."""

    completion_tokens: ClassVar[int | None] = 300
    requests: ClassVar[list[dict]] = []

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def __enter__(self) -> _CapturingClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, json: dict, **_kwargs) -> _FakeHTTPResponse:
        type(self).requests.append(json)
        usage = {} if self.completion_tokens is None else {"completion_tokens": self.completion_tokens}
        return _FakeHTTPResponse(
            {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": usage}
        )


def _install_client(monkeypatch, completion_tokens: int | None) -> None:
    import benchlocal_cli.runner as runner_module

    _CapturingClient.completion_tokens = completion_tokens
    _CapturingClient.requests = []
    monkeypatch.setattr(runner_module.httpx, "Client", _CapturingClient)
    monkeypatch.setattr(runner_module.time, "sleep", lambda delay: None)


def test_control_passes_on_a_short_completion_and_sends_both_keys(monkeypatch):
    _install_client(monkeypatch, completion_tokens=300)
    runner = _runner()
    warnings = _prepared(runner)

    assert runner._token_budget_report["control"] == {"status": "applied", "completion_tokens": 300}
    assert warnings == []
    probe = _CapturingClient.requests[0]
    assert probe["thinking_budget_tokens"] == _BUDGET_CONTROL_TOKENS
    assert probe["reasoning_budget_tokens"] == _BUDGET_CONTROL_TOKENS
    assert probe["chat_template_kwargs"] == {"enable_thinking": True}


def test_control_refuses_the_run_when_the_budget_was_ignored(monkeypatch):
    # The issue's failure: a full 8-pack ran for three hours believing a
    # 32,768 budget was in force while the flag had never reached the server.
    _install_client(monkeypatch, completion_tokens=1800)
    runner = _runner()
    with pytest.raises(ValueError, match="control FAILED") as excinfo:
        _prepared(runner)
    assert "--no-budget-control" in str(excinfo.value)


def test_control_is_inconclusive_without_usage(monkeypatch):
    _install_client(monkeypatch, completion_tokens=None)
    runner = _runner()
    warnings = _prepared(runner)

    assert runner._token_budget_report["control"]["status"] == "inconclusive"
    assert any("inconclusive" in w for w in warnings)


def test_control_is_skipped_when_no_pack_thinks(monkeypatch):
    _install_client(monkeypatch, completion_tokens=1800)  # would fail if sent
    runner = _runner()
    _prepared(runner, meta=_plain_meta(), pack_ids=["toolcall-15"])

    assert runner._token_budget_report["control"]["status"] == "skipped"
    assert _CapturingClient.requests == []


# ------------------------------------------------------------ per-pack announce


def test_announce_records_clock_binding_and_the_coherent_pair(capsys):
    runner = _runner(budget_control=False, timeout_per_case=900)
    _prepared(runner)
    warnings: list[str] = []

    runner._announce_token_budget("reasonmath-15", _thinking_meta(), warnings)

    entry = runner._token_budget_report["packs"]["reasonmath-15"]
    assert entry["applies"] is True
    assert entry["clock_s"] == 900
    assert entry["derived"] == 33120
    assert entry["ceiling"] == 65536
    assert entry["budget"] == 33120
    assert entry["binds"] == "clock"
    assert entry["answer_reserve"] == 1024
    assert entry["reasoning"] == 33120 - 1024  # reasoning + reserve == budget
    assert entry["delivery"] == ["max_tokens", "thinking_budget_tokens", "reasoning_budget_tokens"]
    assert warnings == []
    line = capsys.readouterr().err
    assert "reasonmath-15 token budget: 33120 (clock binds: derived 33120 = 0.8 x 900s x 46.0 tok/s, ceiling 65536)" in line
    assert "reasoning 32096 + answer reserve 1024; delivered as max_tokens + thinking_budget_tokens + reasoning_budget_tokens" in line


def test_announce_says_when_the_ceiling_binds_instead():
    runner = _runner(budget_control=False, timeout_per_case=900, thinking_max_tokens=12288)
    _prepared(runner)

    runner._announce_token_budget("reasonmath-15", _thinking_meta(), [])

    entry = runner._token_budget_report["packs"]["reasonmath-15"]
    assert entry["derived"] == 33120
    assert entry["budget"] == 12288
    assert entry["binds"] == "ceiling"


def test_announce_warns_when_the_clock_cannot_fit_the_answer():
    runner = _runner(budget_control=False, timeout_per_case=10)
    _prepared(runner)
    warnings: list[str] = []

    runner._announce_token_budget("reasonmath-15", _thinking_meta(), warnings)

    assert runner._token_budget_report["packs"]["reasonmath-15"]["budget"] == 368
    assert len(warnings) == 1
    assert "below the pack's nominal answer size 1024" in warnings[0]


def test_agent_owned_packs_are_not_budgeted(capsys):
    runner = _runner(budget_control=False)
    _prepared(runner)

    runner._announce_token_budget("hermesagent-20", _plain_meta(pack_id="hermesagent-20"), [])

    entry = runner._token_budget_report["packs"]["hermesagent-20"]
    assert entry["applies"] is False
    assert "in-container" in entry["reason"]
    assert "hermesagent-20 token budget: n/a" in capsys.readouterr().err


# ------------------------------------------------------------ per-request apply


def _built(runner: Runner, meta: dict, scenario: dict, clock: float) -> tuple[dict, dict]:
    from benchlocal_cli.runner import _apply_cli_thinking_controls, build_request

    request, sampling = build_request(
        scenario, meta, runner.model,
        thinking_enabled=runner.thinking_override,
        thinking_max_tokens=runner.thinking_max_tokens,
        extra_body=runner.extra_body,
        thinking_control=runner.thinking_control,
    )
    _apply_cli_thinking_controls(scenario, request, sampling, runner.thinking_max_tokens, runner.thinking_control)
    runner._apply_token_budget(meta, scenario, request, sampling, clock)
    return request, sampling


def test_thinking_request_gets_max_tokens_and_the_reasoning_share():
    runner = _runner(budget_control=False)
    _prepared(runner)

    request, sampling = _built(runner, _thinking_meta(), _scenario(), clock=900)

    assert request["max_tokens"] == 33120
    assert request["thinking_budget_tokens"] == 33120 - 1024
    assert request["reasoning_budget_tokens"] == 33120 - 1024
    # the pair is coherent: reasoning + reserve == total, so the overrun cannot
    # relocate into content the way an isolated reasoning cap let it
    assert request["reasoning_budget_tokens"] + 1024 == request["max_tokens"]
    assert sampling["max_tokens"] == 33120  # persisted in sampling_params too


def test_non_thinking_request_is_clamped_to_its_own_ceiling_without_reasoning_keys():
    runner = _runner(budget_control=False)
    _prepared(runner, meta=_plain_meta(), pack_ids=["toolcall-15"])

    request, _ = _built(runner, _plain_meta(), _scenario("toolcall-15", "TC-01"), clock=60)

    # derived 2208 > pack ceiling 1024: the ceiling binds, request unchanged
    assert request["max_tokens"] == 1024
    assert not any(key in request for key in _REASONING_BUDGET_KEYS)

    request, _ = _built(runner, _plain_meta(), _scenario("toolcall-15", "TC-01"), clock=10)
    assert request["max_tokens"] == 368  # 0.8 x 10 x 46: clock binds
    assert not any(key in request for key in _REASONING_BUDGET_KEYS)


def test_unknown_engine_gets_max_tokens_only():
    runner = _runner(budget_control=False)
    _prepared(runner, family="unknown")

    request, _ = _built(runner, _thinking_meta(), _scenario(), clock=900)

    assert request["max_tokens"] == 33120
    assert not any(key in request for key in _REASONING_BUDGET_KEYS)


def test_extra_body_keys_are_left_as_given():
    runner = _runner(budget_control=False, extra_body={"reasoning_budget_tokens": 4096})
    _prepared(runner)

    request, _ = _built(runner, _thinking_meta(), _scenario(), clock=900)

    assert request["max_tokens"] == 33120
    assert request["reasoning_budget_tokens"] == 4096  # operator's value wins
    assert request["thinking_budget_tokens"] == 33120 - 1024  # the unlocked key is derived


def test_cli40_provider_native_pair_carries_the_same_share():
    runner = _runner(budget_control=False)
    _prepared(runner)
    meta = _thinking_meta(pack_id="cli-40", supports_sandboxed_only=True)

    request, _ = _built(runner, meta, _scenario("cli-40", "CLI-01"), clock=300)

    assert request["max_tokens"] == 11040
    assert request["enable_thinking"] is True
    assert request["thinking_budget"] == request["reasoning_budget_tokens"]


def test_flag_off_leaves_the_request_byte_identical():
    # Negative control for the whole feature: with the flag off no new key
    # appears and max_tokens is the arm's ceiling exactly as before.
    runner = _runner(budget_from_timeout=False)
    request, _ = _built(runner, _thinking_meta(), _scenario(), clock=900)
    assert request["max_tokens"] == 65536
    assert not any(key in request for key in _REASONING_BUDGET_KEYS)


# ------------------------------------------------------------ report + markdown


def _result(token_budget: dict | None) -> RunResult:
    run = ScenarioRun(
        id="RM-01",
        result=ScenarioResult("RM-01", True, "passed", "ok", latency_seconds=2.0),
        raw_scenario={}, raw_response={}, request={}, sampling_params={}, status_code=200,
    )
    pack = PackResult(
        pack_id="reasonmath-15", version="1.0.0", upstream_commit="abc", scenario_count=1,
        passed=1, total=1, score=1.0, latency={"p50": 2.0, "p95": 2.0, "mean": 2.0},
        scenarios=[run], thinking_enabled=True,
    )
    return RunResult(
        schema_version="1", runner_version="0.9.9", endpoint="http://x", model="m", mode="custom",
        started_at="2026-09-22T00:00:00Z", finished_at="2026-09-22T00:01:00Z", packs=[pack],
        totals={"passed": 1, "total": 1, "score": 1.0}, thinking_enabled=True, thinking_mode="force-on",
        token_budget=token_budget,
    )


# Captured from `_markdown` at 47d1d66 (pre-change) for the fixture above.
_GOLDEN_HEADER = "=== benchlocal-cli --custom  (endpoint: http://x, model: m, thinking=on, 2026-09-22T00:00:00Z) ==="


def test_markdown_header_is_byte_stable_without_a_derived_budget():
    rendered = _markdown(_result(None))
    assert rendered.splitlines()[0] == _GOLDEN_HEADER
    assert "TOKEN BUDGET" not in rendered
    assert "token_budget" not in _result(None).to_dict()


def test_markdown_header_tags_a_derived_budget_and_json_carries_it():
    budget = {"mode": "derived", "headroom": 0.8, "measured_tps": 46.0, "engine": "llama.cpp", "packs": {}}
    result = _result(budget)
    rendered = _markdown(result)
    assert rendered.splitlines()[0] == _GOLDEN_HEADER[:-4] + " [TOKEN BUDGET: derived from clock x 46.0 tok/s x 0.8] ==="
    # everything after the header is untouched
    assert rendered.splitlines()[1:] == _markdown(_result(None)).splitlines()[1:]
    assert result.to_dict()["token_budget"] == budget


def test_journal_rebuild_and_resume_config_carry_the_budget_mode():
    config = {
        "target_selection": ["reasonmath-15/RM-01"],
        "pack_ids": ["reasonmath-15"],
        "token_budget": {"mode": "derived", "headroom": 0.8},
    }
    rebuilt = _build_result(config, [], finished_at="2026-09-22T00:01:00Z")
    assert rebuilt.token_budget == {"mode": "derived", "headroom": 0.8}

    inferred = _infer_config(_result({"mode": "derived", "headroom": 0.7}).to_dict(), __import__("pathlib").Path("r.json"))
    assert inferred["budget_from_timeout"] is True
    assert inferred["budget_headroom"] == 0.7
    inferred = _infer_config(_result(None).to_dict(), __import__("pathlib").Path("r.json"))
    assert inferred["budget_from_timeout"] is False


# ------------------------------------------------------------ CLI end to end


def test_cli_mock_run_records_the_derived_budget(tmp_path):
    mock_path = tmp_path / "mock.json"
    result_path = tmp_path / "result.json"
    mock_path.write_text(json.dumps({"SO-01": {
        "choices": [{"message": {"content": '{"title":"The Great Gatsby","year":1925}'}}],
        "usage": {"completion_tokens": 3},
    }}))
    rc = main([
        "run", "--endpoint", "mock", "--model", "mock",
        "--mock-responses-from-json", str(mock_path),
        "--scenario", "structoutput-15/SO-01",
        "--measured-tps", "46", "--timeout-per-case", "10",
        "--budget-from-timeout", "--budget-headroom", "0.5",
        "--save-json", str(result_path), "--output", "json",
    ])
    assert rc == 0
    saved = json.loads(result_path.read_text())
    budget = saved["token_budget"]
    assert budget["mode"] == "derived"
    assert budget["headroom"] == 0.5
    assert budget["measured_tps"] == 46.0
    assert budget["engine"] == "unknown"  # mock traffic never touches an endpoint
    entry = budget["packs"]["structoutput-15"]
    assert entry["derived"] == 230  # 0.5 x 10 s x 46 tok/s
    assert entry["budget"] == 230 and entry["binds"] == "clock"
    # the applied ceiling is self-documenting on the persisted request
    assert saved["packs"][0]["scenarios"][0]["request"]["max_tokens"] == 230


def test_cli_rejects_a_headroom_outside_the_unit_interval(capsys):
    rc = main(["run", "--endpoint", "mock", "--model", "mock", "--budget-headroom", "1.5", "--quick"])
    assert rc == 1
    assert "--budget-headroom must be in (0, 1]" in capsys.readouterr().err
