"""#61 token_limit reclassification + #62 tier-1 negative-control (grader fidelity)."""

from __future__ import annotations

from benchlocal_cli.runner import Runner, _negative_control_response
from benchlocal_cli.types import ScenarioResult


def _resp(finish_reason: str) -> dict:
    return {"choices": [{"message": {"content": "x"}, "finish_reason": finish_reason}]}


# ---------------------------------------------------------------------------
# #61 — finish_reason == "length" reclassifies a content-failure to token_limit
# ---------------------------------------------------------------------------

def test_truncated_content_failure_becomes_token_limit():
    r = Runner._reclassify_if_truncated(
        ScenarioResult("a", False, "verifier_fail", "wrong"), _resp("length")
    )
    assert r.failure_mode == "token_limit"
    assert "finish_reason=length" in r.detail
    assert "verifier_fail" in r.detail  # original verdict preserved for forensics


def test_all_content_modes_reclassified_at_length():
    for mode in (
        "verifier_fail", "wrong_answer", "invalid_json", "no_answer_found",
        "missing_field", "extra_fields", "schema_violation", "wrong_structure",
    ):
        r = Runner._reclassify_if_truncated(ScenarioResult("a", False, mode, "x"), _resp("length"))
        assert r.failure_mode == "token_limit", mode


def test_non_length_finish_reason_unchanged():
    r = Runner._reclassify_if_truncated(
        ScenarioResult("a", False, "verifier_fail", "wrong"), _resp("stop")
    )
    assert r.failure_mode == "verifier_fail"


def test_pass_never_reclassified_even_at_length():
    r = Runner._reclassify_if_truncated(ScenarioResult("a", True, "passed", "ok"), _resp("length"))
    assert r.passed and r.failure_mode == "passed"


def test_infra_failures_not_reclassified():
    for mode in ("server_error", "http_error", "timeout", "verifier_not_implemented"):
        r = Runner._reclassify_if_truncated(ScenarioResult("a", False, mode, "x"), _resp("length"))
        assert r.failure_mode == mode, mode


def test_missing_choices_unchanged():
    r = Runner._reclassify_if_truncated(
        ScenarioResult("a", False, "wrong_answer", "x"), {"usage": {}}
    )
    assert r.failure_mode == "wrong_answer"


# ---------------------------------------------------------------------------
# #62 tier-1 — negative-control junk-mock
# ---------------------------------------------------------------------------

def test_negative_control_response_shape():
    resp = _negative_control_response("(no answer)")
    assert resp["choices"][0]["message"]["content"] == "(no answer)"
    assert resp["choices"][0]["finish_reason"] == "stop"
    assert resp["usage"]["completion_tokens"] == 0


def test_negative_control_run_is_offline_and_junk_fails_toolcall():
    # The endpoint is intentionally bogus: if negative-control ever touched the
    # network this would error/hang. Instead every scenario is served junk
    # locally, so the run completes and junk (no tool_calls) fails toolcall-15.
    runner = Runner(
        endpoint="http://negative-control.invalid",
        model="x",
        negative_control="(no answer)",
    )
    result = runner.run(["toolcall-15"])
    pack = next(p for p in result.packs if p.pack_id == "toolcall-15")
    assert pack.total == 15
    assert pack.passed == 0  # junk has no tool_calls → no false-positives on this pack
