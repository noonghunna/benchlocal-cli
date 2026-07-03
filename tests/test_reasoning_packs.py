from __future__ import annotations

import argparse
import json

from benchlocal_cli.cli import _mode_from_args, main
from benchlocal_cli.runner import PACK_MODES, Runner, load_pack
from benchlocal_cli.scoring import answer_match


def _mode_ns(**kw) -> argparse.Namespace:
    base = dict(pack=None, quick=False, medium=False, full=False,
                reasoning=False, reasoning_packs=False)
    base.update(kw)
    return argparse.Namespace(**base)


# #65 — --reasoning renamed to --reasoning-packs (pack-set selector), with the
# old flag kept as a hidden, deprecated, back-compat alias.
def test_reasoning_packs_selects_reasoning_mode():
    assert _mode_from_args(_mode_ns(reasoning_packs=True)) == "reasoning"


def test_deprecated_reasoning_alias_still_selects_reasoning_mode():
    assert _mode_from_args(_mode_ns(reasoning=True)) == "reasoning"


def test_default_mode_is_medium():
    assert _mode_from_args(_mode_ns()) == "medium"


def test_reasoning_packs_visible_in_help(capsys):
    import pytest
    with pytest.raises(SystemExit):
        main(["run", "--help"])
    out = capsys.readouterr().out
    assert "--reasoning-packs" in out  # primary flag is documented


def test_reasoning_packs_mutually_exclusive_with_full():
    import pytest
    # pack-set selectors are mutually exclusive — argparse exits at parse time.
    with pytest.raises(SystemExit):
        main(["run", "--reasoning-packs", "--full", "--endpoint", "x", "--model", "y"])


def _response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}], "usage": {"completion_tokens": 3}}


def test_reasoning_mode_is_separate_from_full():
    assert "reasoning" in PACK_MODES
    assert "humaneval-plus-30" in PACK_MODES["reasoning"]
    assert "lcb-v6-30" in PACK_MODES["reasoning"]
    assert "gsm-symbolic-30" in PACK_MODES["reasoning"]
    assert "gpqa-diamond" in PACK_MODES["reasoning"]
    assert "humaneval-plus-30" not in PACK_MODES["full"]
    assert "lcb-v6-30" not in PACK_MODES["full"]


def test_reasoning_pack_metadata_defaults_to_thinking_on():
    for pack_id in ("humaneval-plus-30", "lcb-v6-30", "gpqa-diamond", "gsm-symbolic-30"):
        meta, scenarios = load_pack(pack_id)
        assert meta["default_thinking"] == "on"
        assert meta["suite"] == "reasoning"
        if pack_id != "gpqa-diamond":
            assert len(scenarios) == 30


def test_all_packs_carry_timeout_reference_tps():
    # Guard for #54: every deterministic + reasoning pack must carry
    # timeout_reference_tps so per-case timeout budgets scale by measured rig TPS.
    # The agentic packs already had it; these are the ones that regressed.
    # Catches the build-packs.js generator (or a manual edit) dropping the field.
    deterministic = (
        "toolcall-15",
        "instructfollow-15",
        "structoutput-15",
        "dataextract-15",
        "reasonmath-15",
    )
    reasoning = (
        "humaneval-plus-30",
        "lcb-v6-30",
        "gsm-symbolic-30",
        "gpqa-diamond",
        "bugfind-15",
    )
    for pack_id in deterministic + reasoning:
        meta, _ = load_pack(pack_id)
        assert meta.get("timeout_reference_tps") == 100, pack_id


def test_sandbox_safety_policy_intent_is_explicit_in_pack_metadata():
    for pack_id in ("cli-40", "hermesagent-20"):
        meta, _ = load_pack(pack_id)
        policy = meta.get("safety_policy")
        assert policy["mode"] == "implicit_benchmark_local", pack_id
        assert "not explicit policy-following tests" in policy["description"]



def test_rescore_regrades_saved_reason_math_reasoning_channel(tmp_path):
    source = tmp_path / "run.json"
    target = tmp_path / "rescored.json"
    source.write_text(json.dumps({
        "schema_version": "1",
        "runner_version": "test",
        "endpoint": "saved",
        "model": "saved",
        "mode": "custom",
        "started_at": "2026-07-03T00:00:00Z",
        "finished_at": "2026-07-03T00:00:01Z",
        "packs": [{
            "pack_id": "reasonmath-15",
            "version": "1.0.0",
            "upstream_commit": "test",
            "scenario_count": 1,
            "passed": 0,
            "total": 1,
            "score": 0.0,
            "latency": {"p50": 0.1, "p95": 0.1, "mean": 0.1},
            "scenarios": [{
                "id": "RM-02",
                "passed": False,
                "failure_mode": "wrong_answer",
                "detail": "old trace axis cap",
                "latency_seconds": 0.1,
                "tokens_completion": 12,
                "result": {
                    "scenario_id": "RM-02",
                    "passed": False,
                    "failure_mode": "wrong_answer",
                    "detail": "old trace axis cap",
                    "latency_seconds": 0.1,
                    "tokens_completion": 12,
                    "verifier_trace": {"upstream_style_score": 70},
                },
                "raw_scenario": {"id": "RM-02"},
                "raw_response": {
                    "choices": [{
                        "message": {
                            "content": "ANSWER: kg=0.313",
                            "reasoning": "grams=312.5\nkg=0.3125",
                        }
                    }],
                    "usage": {"completion_tokens": 12},
                },
                "request": {},
                "sampling_params": {},
                "status_code": 200,
            }],
        }],
        "totals": {"passed": 0, "total": 1, "score": 0.0},
    }))

    assert main(["rescore", str(source), "--pack", "reasonmath-15", "--output", str(target)]) == 0

    rescored = json.loads(target.read_text())
    scenario = rescored["packs"][0]["scenarios"][0]
    assert scenario["passed"] is True
    assert scenario["result"]["verifier_trace"]["trace_axis_points"] == 2
    assert scenario["result"]["verifier_trace"]["trace_sources"] == ["message.reasoning"]
    assert rescored["totals"] == {"passed": 1, "total": 1, "score": 1.0}
    assert rescored["rescored"]["scenarios"] == 1

def test_answer_match_numeric_prefers_final_answer_line():
    scenario = {"id": "GSM", "verifier": {"asserts": [{"kind": "exact_numeric", "value": "20"}]}}
    assert answer_match.score_scenario(scenario, _response("Rough work mentions 20, but final says ANSWER: 21")).failure_mode == "wrong_answer"
    assert answer_match.score_scenario(scenario, _response("Work...\nANSWER: 20")).passed


def test_answer_match_letter():
    scenario = {"id": "GPQA", "verifier": {"asserts": [{"kind": "exact_letter", "value": "C"}]}}
    assert answer_match.score_scenario(scenario, _response("ANSWER: C")).passed
    assert answer_match.score_scenario(scenario, _response("ANSWER: A")).failure_mode == "wrong_answer"


def test_gated_gpqa_pack_skips_without_counting():
    runner = Runner(endpoint="http://localhost:9999", model="fake")
    pack = runner.run_pack("gpqa-diamond")
    assert pack.skipped is True
    assert pack.status == "dataset-unavailable"
    assert pack.total == 0


def test_repeat_variance_is_recorded(monkeypatch):
    meta = {
        "version": "test",
        "upstream_commit": "local",
        "verifier_module": "answer_match",
        "sampling_defaults": {"max_tokens": 16},
        "default_thinking": "on",
    }
    scenarios = [
        {"id": "a", "messages": [{"role": "user", "content": "a"}], "verifier": {"type": "answer_match", "asserts": [{"kind": "exact_letter", "value": "A"}]}},
        {"id": "b", "messages": [{"role": "user", "content": "b"}], "verifier": {"type": "answer_match", "asserts": [{"kind": "exact_letter", "value": "B"}]}},
    ]
    monkeypatch.setattr("benchlocal_cli.runner.load_pack", lambda pack_id: (meta, scenarios))
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        mock_responses={
            "a": _response("ANSWER: A"),
            "b": _response("ANSWER: A"),
        },
    )
    pack = runner.run_pack("fake-reasoning", repeat=2)
    assert pack.variance is not None
    assert pack.variance["repeat"] == 2
    assert pack.variance["std"] == 0.0
    assert pack.to_dict()["variance"]["cv"] == 0.0


def test_cli_list_marks_gpqa_gated(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "gpqa-diamond | 0.1.0 | 0 | answer_match | on | gated" in out
