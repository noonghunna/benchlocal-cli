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


def test_hermes_grade_passes_with_required_keywords_and_tool_use():
    """v0.7.3 _grade() — upstream agent ran successfully, called expected tools,
    final response contains required keywords."""
    server = _load("hermes_server", "sandboxes/hermes/server.py")
    scenario = {
        "id": "HA-01",
        "raw_scenario": {
            "kind": "memory_replace_contradiction",
            "expected": {"required_keywords": ["cockroachdb", "retained", "memory"]},
        },
    }
    upstream_result = {
        "ok": True,
        "completed": True,
        "partial": False,
        "finalResponse": "Done. CockroachDB is now retained in memory; the stale PostgreSQL fact is gone.",
        "messages": [],
        "toolEvents": [
            {"phase": "start", "name": "memory_set", "args": {"key": "db", "value": "CockroachDB"}},
            {"phase": "complete", "name": "memory_set", "args": {}, "result": {"ok": True}},
        ],
        "inputTokens": 100,
        "outputTokens": 30,
    }

    passed, mode, detail, meta = server._grade("HA-01", scenario, upstream_result)

    assert passed is True
    assert mode == "passed"
    assert "memory_set" in meta["tool_names"]
    assert sorted(meta["keyword_hits"]) == ["cockroachdb", "memory", "retained"]


def test_hermes_grade_fails_when_keywords_missing():
    """v0.7.3 _grade() — agent ran but final response doesn't reflect success_case."""
    server = _load("hermes_server", "sandboxes/hermes/server.py")
    scenario = {
        "id": "HA-02",
        "raw_scenario": {
            "kind": "memory_replace_contradiction",
            "expected": {"required_keywords": ["cockroachdb", "retained", "memory"]},
        },
    }
    upstream_result = {
        "ok": True,
        "completed": True,
        "finalResponse": "Sure, I noted that.",
        "toolEvents": [{"phase": "complete", "name": "memory_set"}],
    }

    passed, mode, detail, meta = server._grade("HA-02", scenario, upstream_result)

    assert passed is False
    assert mode == "verifier_fail"
    assert "lacks upstream success-case evidence" in detail


def test_hermes_grade_accepts_mock_pass_marker():
    """The BENCHLOCAL_PASS:<id> marker still short-circuits to pass."""
    server = _load("hermes_server", "sandboxes/hermes/server.py")
    scenario = {"id": "HA-03", "raw_scenario": {"kind": "skill_run", "expected": {}}}
    upstream_result = {
        "ok": True,
        "completed": True,
        "finalResponse": "BENCHLOCAL_PASS:HA-03",
        "toolEvents": [],
    }

    passed, mode, detail, meta = server._grade("HA-03", scenario, upstream_result)

    assert passed is True
    assert mode == "passed"
    assert meta["mode"] == "mock-marker"


def test_hermes_grade_soft_passes_on_tool_use_plus_one_keyword():
    """v0.7.3 _grade() soft-pass — agent called tools to completion AND
    mentioned ≥1 success-case keyword in its own words. This catches scenarios
    where the agent did the right thing but described it differently than
    upstream's success_case prose.
    """
    server = _load("hermes_server", "sandboxes/hermes/server.py")
    scenario = {
        "id": "HA-soft",
        "raw_scenario": {
            "kind": "memory_replace_contradiction",
            "expected": {"required_keywords": ["cockroachdb", "retained", "stale", "postgresql-only", "fact", "gone"]},
        },
    }
    upstream_result = {
        "ok": True,
        "completed": True,
        "finalResponse": "Noted. I've saved that the project moved to CockroachDB.",
        "toolEvents": [
            {"phase": "start", "name": "memory", "args": {"action": "add"}},
            {"phase": "complete", "name": "memory", "result": {"ok": True}},
        ],
    }

    passed, mode, detail, meta = server._grade("HA-soft", scenario, upstream_result)

    assert passed is True
    assert mode == "passed"
    assert meta["tool_event_count"] == 2
    assert meta["keyword_hits"] == ["cockroachdb"]


def test_hermes_grade_fails_on_one_keyword_without_tool_use():
    """Don't soft-pass on a single keyword unless the agent actually drove tools."""
    server = _load("hermes_server", "sandboxes/hermes/server.py")
    scenario = {
        "id": "HA-no-tools",
        "raw_scenario": {
            "kind": "memory_replace_contradiction",
            "expected": {"required_keywords": ["cockroachdb", "retained", "stale", "memory"]},
        },
    }
    # Agent emitted a chat-only response that happened to mention "cockroachdb"
    # but didn't call any tools — Pattern E lucky-pass that we want to reject.
    upstream_result = {
        "ok": True,
        "completed": True,
        "finalResponse": "Sure, I'll remember CockroachDB.",
        "toolEvents": [],
    }

    passed, mode, detail, meta = server._grade("HA-no-tools", scenario, upstream_result)

    assert passed is False
    assert mode == "verifier_fail"


def test_hermes_grade_fails_on_empty_response_no_tools():
    """Agent gave up silently (no final response, no tool use)."""
    server = _load("hermes_server", "sandboxes/hermes/server.py")
    scenario = {"id": "HA-04", "raw_scenario": {"kind": "memory_replace_contradiction", "expected": {}}}
    upstream_result = {"ok": True, "completed": True, "finalResponse": "", "toolEvents": []}

    passed, mode, detail, meta = server._grade("HA-04", scenario, upstream_result)

    assert passed is False
    assert mode == "wrong_answer"
