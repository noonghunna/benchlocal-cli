from __future__ import annotations

import json

from benchlocal_cli.cli import main
from benchlocal_cli.runner import PACK_MODES, Runner, load_pack
from benchlocal_cli.scoring import answer_match


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
