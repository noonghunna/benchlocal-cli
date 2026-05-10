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




# ============================================================================
# v0.7.4 — upstream Node grader proxy (replaces v0.7.3 keyword-match _grade)
# ============================================================================


def _hermes_server():
    return _load("hermes_server", "sandboxes/hermes/server.py")


def test_hermes_translate_request_normalizes_endpoint_and_filters_generation():
    server = _hermes_server()
    req = {
        "scenario_id": "HA-01",
        "scenario": {"id": "HA-01"},
        "model_endpoint": "http://10.0.0.5:8001/v1/chat/completions",
        "model_name": "qwen3.6-27b-autoround",
        "model_api_key": "sk-test",
        "sampling": {"temperature": 0.6, "top_p": 0.95, "max_tokens": 256, "ignored": "x"},
    }
    out = server._translate_request(req)
    assert out["scenarioId"] == "HA-01"
    assert out["model"]["inferenceBaseUrl"] == "http://10.0.0.5:8001/v1"
    assert out["model"]["exposedModel"] == "qwen3.6-27b-autoround"
    assert out["model"]["apiKey"] == "sk-test"
    assert out["generation"] == {"temperature": 0.6, "top_p": 0.95, "max_tokens": 256}
    assert "runId" in out and len(out["runId"]) > 0


def test_hermes_normalize_base_url_ensures_v1_suffix():
    """Codex review #10: cover all input shapes. The OpenAI client expects
    base_url ending in /v1 (it appends /chat/completions itself)."""
    server = _hermes_server()
    for endpoint in [
        "http://host:8001",
        "http://host:8001/",
        "http://host:8001/v1",
        "http://host:8001/v1/",
        "http://host:8001/v1/chat/completions",
        "http://host:8001/chat/completions",
    ]:
        assert server._normalize_base_url(endpoint) == "http://host:8001/v1", \
            f"failed for {endpoint!r}"


def test_hermes_translate_upstream_pass_response():
    server = _hermes_server()
    upstream = {
        "scenarioId": "HA-01",
        "status": "pass",
        "score": 100,
        "summary": "Replaced contradictory memory entry successfully.",
        "note": None,
        "rawLog": "long log string ..." * 100,
        "output": {"memory": ["CockroachDB"]},
        "verifier": {"status": "pass", "details": {"outcomeSatisfied": True, "outcomeScore": 80, "nativeUseScore": 10, "safetyScore": 10}},
        "timings": {"durationMs": 12500},
    }
    out = server._translate_upstream_result("HA-01", upstream, elapsed_s=12.5)
    assert out["action"] == "verify-final"
    assert out["passed"] is True
    assert out["failure_mode"] == "passed"
    assert "Replaced contradictory" in out["detail"]
    trace = out["trace"]
    assert trace["upstream_status"] == "pass"
    assert trace["upstream_score"] == 100
    assert trace["upstream_verifier"]["details"]["outcomeScore"] == 80
    assert trace["schema_version"] == "2"
    assert trace["upstream_raw"]["rawLog"].startswith("<truncated")  # capped


def test_hermes_translate_upstream_partial_collapses_to_fail():
    """Codex finding: binary-pass semantics — partial → fail in failure_mode,
    but upstream_status preserves the original signal."""
    server = _hermes_server()
    upstream = {
        "scenarioId": "HA-08",
        "status": "partial",
        "score": 60,
        "summary": "Partial — agent created the file but contents incomplete.",
        "verifier": {"status": "partial"},
    }
    out = server._translate_upstream_result("HA-08", upstream, elapsed_s=8.0)
    assert out["passed"] is False
    assert out["failure_mode"] == "verifier_fail"
    assert out["trace"]["upstream_status"] == "partial"
    assert out["trace"]["upstream_score"] == 60


def test_hermes_translate_upstream_fail_with_network_note_classifies_unreachable():
    server = _hermes_server()
    upstream = {
        "scenarioId": "HA-11",
        "status": "fail",
        "score": 0,
        "summary": "Failed to reach model endpoint.",
        "note": "Connection refused: getaddrinfo ENOTFOUND",
    }
    out = server._translate_upstream_result("HA-11", upstream, elapsed_s=2.5)
    assert out["passed"] is False
    assert out["failure_mode"] == "model_endpoint_unreachable"


def test_hermes_translate_upstream_fail_with_timeout_note_classifies_timeout():
    server = _hermes_server()
    upstream = {
        "scenarioId": "HA-05",
        "status": "fail",
        "score": 0,
        "summary": "Test run timed out after 300s.",
        "note": "Agent loop timed out before reaching the verifier",
    }
    out = server._translate_upstream_result("HA-05", upstream, elapsed_s=300.5)
    assert out["passed"] is False
    assert out["failure_mode"] == "agent_runner_timeout"


def test_hermes_translate_upstream_fail_default_classifies_verifier_fail():
    server = _hermes_server()
    upstream = {
        "scenarioId": "HA-03",
        "status": "fail",
        "score": 0,
        "summary": "Memory injection check failed.",
        "note": "Outcome verifier rejected the final state.",
    }
    out = server._translate_upstream_result("HA-03", upstream, elapsed_s=4.5)
    assert out["passed"] is False
    assert out["failure_mode"] == "verifier_fail"


def test_hermes_cap_upstream_for_trace_under_budget():
    server = _hermes_server()
    upstream = {"scenarioId": "HA-01", "status": "pass", "score": 100, "summary": "ok", "rawLog": "x" * 30000}
    capped = server._cap_upstream_for_trace(upstream, max_bytes=server.UPSTREAM_RAW_MAX_BYTES)
    import json as _json
    assert len(_json.dumps(capped)) <= server.UPSTREAM_RAW_MAX_BYTES
    # Headline fields preserved
    assert capped["scenarioId"] == "HA-01"
    assert capped["status"] == "pass"
    assert capped["score"] == 100
    assert capped["summary"] == "ok"
    assert capped["rawLog"].startswith("<truncated")


def test_hermes_cap_upstream_for_trace_drops_largest_keys_when_still_over():
    server = _hermes_server()
    # Build a payload that's still over budget after rawLog stub.
    upstream = {
        "scenarioId": "HA-01",
        "status": "pass",
        "score": 100,
        "summary": "ok",
        "rawLog": "x" * 50,  # small enough that stub fits
        "huge_field_a": "a" * 10000,
        "huge_field_b": "b" * 10000,
    }
    capped = server._cap_upstream_for_trace(upstream, max_bytes=4096)
    import json as _json
    assert len(_json.dumps(capped)) <= 4096
    # Headline preserved; one or both large fields dropped.
    assert capped["scenarioId"] == "HA-01"
    assert capped["status"] == "pass"
    dropped = [k for k, v in capped.items() if isinstance(v, str) and v.startswith("<dropped")]
    assert dropped, "expected at least one large key to be dropped"


def test_hermes_mock_pass_response_has_v074_schema():
    server = _hermes_server()
    out = server._mock_pass_response("HA-99")
    assert out["passed"] is True
    assert out["failure_mode"] == "passed"
    trace = out["trace"]
    assert trace["mock_pass"] is True
    assert trace["upstream_status"] == "pass"
    assert trace["schema_version"] == "2"


def test_hermes_verify_start_short_circuits_on_mock_marker():
    server = _hermes_server()
    req = {
        "scenario_id": "HA-99",
        "scenario": {
            "id": "HA-99",
            "messages": [{"role": "user", "content": "BENCHLOCAL_PASS:HA-99"}],
        },
        "model_endpoint": "http://localhost:8001",
        "model_name": "fake",
    }
    out = server._verify_start_via_upstream(req)
    assert out["passed"] is True
    assert out["trace"]["mock_pass"] is True


def test_hermes_verify_start_refuses_when_install_missing(monkeypatch, tmp_path):
    """Diagnostic chain: install presence is the first thing checked.
    Install absence is a more fundamental error than missing endpoint, so
    surface that first."""
    server = _hermes_server()
    monkeypatch.setattr(server, "HERMES_AGENT_PATH", tmp_path / "definitely-missing")
    req = {"scenario_id": "HA-01", "scenario": {"id": "HA-01"}}
    out = server._verify_start_via_upstream(req)
    assert out["passed"] is False
    assert out["failure_mode"] == "server_error"
    assert "hermes-agent install missing" in out["detail"]


def test_hermes_verify_start_refuses_when_endpoint_missing(monkeypatch, tmp_path):
    """When install is present but endpoint missing, surface the endpoint
    error (the second check in the diagnostic chain)."""
    server = _hermes_server()
    install = tmp_path / "fake-hermes"
    install.mkdir()
    (install / "run_agent.py").write_text("# stub")
    monkeypatch.setattr(server, "HERMES_AGENT_PATH", install)
    req = {"scenario_id": "HA-01", "scenario": {"id": "HA-01"}}
    out = server._verify_start_via_upstream(req)
    assert out["passed"] is False
    assert out["failure_mode"] == "server_error"
    assert "model_endpoint" in out["detail"]
