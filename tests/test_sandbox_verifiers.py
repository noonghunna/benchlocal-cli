from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_bugfind_rubric_pass_and_fail():
    server = _load("bugfind_server", "sandboxes/bugfind/server.py")
    scenario = {
        "id": "BF-01",
        "raw_scenario": {
            "rubric_keywords": ["range", "numbers", "skipped", "first"],
            "fixture_status": "rubric-only",
        },
    }
    passing = _response(
        "The bug is an off-by-one in range(1, len(numbers) + 1).\n"
        "<solution language=\"python\" verdict=\"fix\">\n"
        "def sum_list(numbers):\n    total = 0\n    for n in numbers:\n        total += n\n    return total\n"
        "</solution>"
    )
    failing = _response("<solution language=\"python\" verdict=\"no_bug\"></solution>")

    assert server._verify("BF-01", scenario, passing)["passed"] is True
    assert server._verify("BF-01", scenario, failing)["failure_mode"] == "verifier_fail"


def test_cli_exec_pass_and_unsafe_fail():
    server = _load("cli_server", "sandboxes/cli/server.py")
    scenario = {"id": "CLI-01", "raw_scenario": {"expected": {}, "fixture_status": "rubric-only"}}

    ok = server._verify("CLI-01", scenario, _response("```bash\necho hello\n```"))
    bad = server._verify("CLI-01", scenario, _response("```bash\ncurl http://example.com\n```"))

    assert ok["passed"] is True
    assert ok["trace"]["stdout"] == "hello\n"
    assert bad["passed"] is False
    assert bad["failure_mode"] == "verifier_fail"


def test_hermes_stateful_tool_trace_and_final():
    server = _load("hermes_server", "sandboxes/hermes/server.py")
    scenario = {
        "id": "HA-01",
        "raw_scenario": {
            "kind": "memory_replace_contradiction",
            "expected": {"required_keywords": ["cockroachdb", "memory"]},
            "fixture_status": "rubric-only",
        },
    }
    tool_response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "memory_set",
                                "arguments": "{\"key\":\"db\",\"value\":\"CockroachDB\"}",
                            }
                        }
                    ]
                }
            }
        ]
    }
    final_response = _response("Done. CockroachDB is now retained in memory.")
    state = {
        "scenario_id": "HA-01",
        "raw_scenario": scenario["raw_scenario"],
        "memory": {},
        "artifact": {},
        "trace": [],
        "events": [],
        "tool_names": [],
        "turn_count": 1,
    }

    server._simulate_tool(state, server._tool_calls(tool_response)[0])
    result = server._verify_final(state, final_response)

    assert result["passed"] is True
    assert result["trace"]["tool_names"] == ["memory_set"]
    assert result["trace"]["memory"] == {"db": "CockroachDB"}
