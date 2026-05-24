from __future__ import annotations

import json
from pathlib import Path

from benchlocal_cli.scoring import (
    _stub,
    data_extract,
    instruct_follow,
    reason_math,
    struct_output,
    tool_call,
)


def _response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}], "usage": {"completion_tokens": 3}}


def _tool(name: str, args: dict) -> dict:
    return {
        "choices": [
            {"message": {"tool_calls": [{"function": {"name": name, "arguments": json.dumps(args)}}]}}
        ]
    }


def _tools(*names: str) -> dict:
    return {
        "choices": [
            {"message": {"tool_calls": [{"function": {"name": n, "arguments": "{}"}} for n in names]}}
        ]
    }


def test_stub_returns_dataclass_result():
    result = _stub.score_scenario({"id": "BF-01", "pack_id": "bugfind-15"}, {})
    assert result.passed is False
    assert result.failure_mode == "verifier_not_implemented"


def test_tool_call_pass_and_fail():
    scenario = {"id": "TC", "verifier": {"asserts": [{"kind": "exact_function_name", "value": "get_weather"}, {"kind": "exact_arg_value", "arg": "location", "value": "Berlin"}]}}
    assert tool_call.score_scenario(scenario, _tool("get_weather", {"location": "Berlin"})).passed
    fail = tool_call.score_scenario(scenario, _tool("web_search", {"query": "Berlin weather"}))
    assert fail.failure_mode == "verifier_fail"


def test_instruct_follow_pass_and_fail():
    scenario = {"id": "IF", "verifier": {"asserts": [{"kind": "exact_length_words", "value": 3}, {"kind": "required_phrase", "value": "red"}]}}
    assert instruct_follow.score_scenario(scenario, _response("red blue green")).passed
    assert instruct_follow.score_scenario(scenario, _response("blue green")).failure_mode == "verifier_fail"


def test_struct_output_pass_and_fail():
    scenario = {"id": "SO", "verifier": {"asserts": [{"kind": "json_parse_required"}, {"kind": "jsonpath_assertion", "path": "$.user.email", "regex": r"@"}]}}
    assert struct_output.score_scenario(scenario, _response('{"user":{"email":"a@example.com"}}')).passed
    assert struct_output.score_scenario(scenario, _response('{"user":{}')).failure_mode == "invalid_json"


def test_reason_math_pass_and_fail():
    scenario = {"id": "RM", "verifier": {"asserts": [{"kind": "tolerance_numeric", "value": 3.14, "tolerance": 0.01}]}}
    assert reason_math.score_scenario(scenario, _response("ANSWER: 3.141")).passed
    assert reason_math.score_scenario(scenario, _response("ANSWER: 4")).failure_mode == "wrong_answer"


def test_multi_call_order_dependent_accepts_correct_prefix():
    # #434: a dependent chain run single-shot — a correct model emits the first
    # step(s) and waits for results. A correct in-order prefix must PASS.
    scenario = {"id": "TC-07", "verifier": {"asserts": [{"kind": "multi_call_order", "expected_names": ["search_files", "read_file", "get_contacts", "send_email"], "dependent": True}]}}
    assert tool_call.score_scenario(scenario, _tools("search_files")).passed                                       # correct first step only
    assert tool_call.score_scenario(scenario, _tools("search_files", "read_file", "get_contacts", "send_email")).passed  # full chain
    assert tool_call.score_scenario(scenario, _tools("web_search")).failure_mode == "verifier_fail"               # wrong first tool
    assert tool_call.score_scenario(scenario, _tools("search_files", "send_email")).failure_mode == "verifier_fail"  # diverges from chain


def test_multi_call_order_parallel_stays_strict():
    # #434: independent/parallel chain (no `dependent` flag, e.g. TC-06's two
    # translations) must still emit ALL expected calls.
    scenario = {"id": "TC-06", "verifier": {"asserts": [{"kind": "multi_call_order", "expected_names": ["translate_text", "translate_text"]}]}}
    assert tool_call.score_scenario(scenario, _tools("translate_text", "translate_text")).passed
    assert tool_call.score_scenario(scenario, _tools("translate_text")).failure_mode == "verifier_fail"


def test_reason_math_exact_string_is_key_agnostic():
    # #435: single-value answer — the pack contract is a bare value, so the key
    # name must not matter; the value within the ANSWER line is what's checked.
    scenario = {"id": "RM-07", "verifier": {"asserts": [{"kind": "exact_string", "value": "avg_speed=48 km/h"}]}}
    assert reason_math.score_scenario(scenario, _response("ANSWER: avg_speed=48 km/h")).passed       # exact (unchanged)
    assert reason_math.score_scenario(scenario, _response("ANSWER: average_speed=48 km/h")).passed   # key synonym (the reported bug)
    assert reason_math.score_scenario(scenario, _response("ANSWER: 48 km/h")).passed                 # bare value (what the prompt asks)
    assert reason_math.score_scenario(scenario, _response("ANSWER: 50 km/h")).failure_mode == "wrong_answer"  # wrong value still fails


def test_reason_math_exact_string_multi_value_key_agnostic():
    # #435: multi-value answers match each value key-agnostically; a missing
    # value still fails.
    scenario = {"id": "RM-13", "verifier": {"asserts": [{"kind": "exact_string", "value": "amount=$5721.24; interest=$721.24"}]}}
    assert reason_math.score_scenario(scenario, _response("ANSWER: total=$5721.24; earned=$721.24")).passed
    assert reason_math.score_scenario(scenario, _response("ANSWER: total=$5721.24")).failure_mode == "wrong_answer"


def test_data_extract_pass_and_fail():
    scenario = {"id": "DE", "verifier": {"asserts": [{"kind": "field_exact_value", "field": "email", "value": "a@example.com"}, {"kind": "no_extra_fields", "allowed": ["email"]}]}}
    assert data_extract.score_scenario(scenario, _response('{"email":"a@example.com"}')).passed
    assert data_extract.score_scenario(scenario, _response('{"email":"b@example.com"}')).failure_mode == "verifier_fail"



def _pack_record(pack: str, scenario_id: str) -> dict:
    path = Path(__file__).resolve().parents[1] / "benchlocal_cli" / "packs" / pack
    for line in path.read_text().splitlines():
        record = json.loads(line)
        if record.get("id") == scenario_id:
            return record
    raise AssertionError(f"{scenario_id} not found in {pack}")


def test_tool_call_pack_preserves_dependent_chain_metadata():
    scenario = _pack_record("toolcall-15.jsonl", "TC-15")
    assertion = scenario["verifier"]["asserts"][0]
    assert assertion["kind"] == "multi_call_order"
    assert assertion["expected_names"] == ["web_search", "calculator"]
    assert assertion["dependent"] is True


def test_reason_math_upstream_metadata_accepts_alias_and_requires_trace():
    scenario = _pack_record("reasonmath-15.jsonl", "RM-02")
    assertion = scenario["verifier"]["asserts"][0]
    assert assertion["accepted_answers"] == ["ANSWER: kg=0.313"]
    assert assertion["partial_answers"] == ["ANSWER: grams=312.5"]
    assert assertion["checkpoints"] == ["grams=312.5", "kg=0.3125"]

    full = reason_math.score_scenario(scenario, _response("grams=312.5\nkg=0.3125\nANSWER: kg=0.313"))
    assert full.passed
    assert full.verifier_trace["upstream_style_score"] == 100

    answer_only = reason_math.score_scenario(scenario, _response("ANSWER: kg=0.313"))
    assert answer_only.failure_mode == "wrong_answer"
    assert answer_only.verifier_trace["upstream_style_score"] == 70


def test_data_extract_expected_scoring_uses_full_upstream_shape():
    scenario = {
        "id": "DE-02",
        "expected": {
            "items": [
                {"name": "Americano", "price": 4.75},
                {"name": "Latte", "price": 5.50},
            ],
            "total": 10.25,
        },
        "verifier": {"asserts": []},
    }
    response = _response(json.dumps({
        "total": 10.25,
        "items": [
            {"name": "Latte", "price": 5.50},
            {"name": "Americano", "price": 4.75},
        ],
        "extra_note": "ignored for score but traced",
    }))
    result = data_extract.score_scenario(scenario, response)
    assert result.passed
    assert result.verifier_trace["upstream_style_score"] == 100
    assert result.verifier_trace["compliance_notes"] == ["extra top-level fields: extra_note"]

    bad = _response(json.dumps({
        "total": 10.25,
        "items": [{"name": "Americano", "price": 4.00}],
    }))
    failed = data_extract.score_scenario(scenario, bad)
    assert failed.failure_mode == "verifier_fail"
    assert failed.verifier_trace["upstream_style_score"] < 85
