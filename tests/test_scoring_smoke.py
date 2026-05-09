from __future__ import annotations

import json

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


def test_data_extract_pass_and_fail():
    scenario = {"id": "DE", "verifier": {"asserts": [{"kind": "field_exact_value", "field": "email", "value": "a@example.com"}, {"kind": "no_extra_fields", "allowed": ["email"]}]}}
    assert data_extract.score_scenario(scenario, _response('{"email":"a@example.com"}')).passed
    assert data_extract.score_scenario(scenario, _response('{"email":"b@example.com"}')).failure_mode == "verifier_fail"
