"""#152: a 5xx raised by the engine rejecting the model's output is a deterministic
model defect, not transient infra. It is classified `model_output_unparseable`,
never retried at the transport layer, and retried inline only under
--retry-runaways. Genuine 5xx (`server_error`) keeps every retry it had.

Every "not retried" assertion here is paired with a positive control that shows
the same path DOES retry when the failure is genuinely transient, so a silently
inert guard cannot pass this file.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from benchlocal_cli.persistence import _scenario_run as scenario_run_from_dict
from benchlocal_cli.runner import (
    _INFRA_FAILURE_MODES,
    _MODEL_DEFECT_FAILURE_MODES,
    Runner,
    classify_server_error,
    is_model_output_rejection,
)
from benchlocal_cli.types import ScenarioResult, ScenarioRun

# Real llama.cpp shape: tools/server wraps the `common/chat.cpp` runtime_error as
# {"error": {"code", "message", "type": "server_error"}} — the issue's exact message.
LLAMA_TOOLCALL_500 = {
    "error": {
        "code": 500,
        "message": (
            "Failed to parse tool call arguments as JSON: "
            "[json.exception.parse_error.101] parse error at line 1, column 62072: "
            "syntax error while parsing value - invalid literal; last read: '<tool_call>'"
        ),
        "type": "server_error",
    }
}
# `common_chat_parse` rejecting output the chat format cannot parse; the message
# echoes the unparsed tail, which can be tens of kilobytes.
LLAMA_CHATPARSE_500 = {
    "error": {
        "code": 500,
        "message": "Failed to parse input at pos 1234: " + "<tool_call>" * 600,
        "type": "server_error",
    }
}
# Genuine transient infra: llama.cpp during model load answers this byte-identical
# body on every attempt. It must keep its retries.
LLAMA_LOADING_503 = {"error": {"code": 503, "message": "Loading model", "type": "unavailable_error"}}
CUDA_500 = {"error": {"message": "CUDA error: an illegal memory access was encountered"}}
OK_200 = {"choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": {"completion_tokens": 3}}


# ----------------------------------------------------------------- classifier


def test_classifier_recognises_llama_cpp_output_rejections():
    assert is_model_output_rejection(LLAMA_TOOLCALL_500)
    assert is_model_output_rejection(LLAMA_CHATPARSE_500)
    # bare-string and non-JSON-body shapes carry the same message
    assert is_model_output_rejection({"error": "Failed to parse tool call arguments as JSON: x"})
    assert is_model_output_rejection({"text": "Failed to parse input at pos 5: junk"})


def test_classifier_leaves_genuine_infra_alone():
    assert not is_model_output_rejection(LLAMA_LOADING_503)
    assert not is_model_output_rejection(CUDA_500)
    assert not is_model_output_rejection({"error": "busy"})
    assert not is_model_output_rejection({"text": "<html><body>502 Bad Gateway</body></html>"})
    assert not is_model_output_rejection({})
    assert not is_model_output_rejection(None)
    # "failed to parse" alone is not enough — the request-side rejections
    # ("Failed to parse messages", "Failed to parse tools") are client errors
    # about the request, not verdicts on the model's output.
    assert not is_model_output_rejection({"error": {"message": "Failed to parse messages: bad role"}})
    assert not is_model_output_rejection({"error": {"message": "Failed to parse tools: missing name"}})


def test_classify_splits_the_mode_and_keeps_the_historical_detail():
    mode, detail = classify_server_error(500, LLAMA_TOOLCALL_500)
    assert mode == "model_output_unparseable"
    assert detail.startswith("HTTP 500: engine rejected model output — ")
    assert "column 62072" in detail  # the generation's fingerprint survives

    # Unchanged path: exact historical detail, so pinned consumers see no drift.
    assert classify_server_error(503, LLAMA_LOADING_503) == ("server_error", "HTTP 503")
    assert classify_server_error(500, CUDA_500) == ("server_error", "HTTP 500")
    assert classify_server_error(502, {"text": "<html>502</html>"}) == ("server_error", "HTTP 502")


def test_classify_trims_a_multi_kilobyte_engine_message():
    mode, detail = classify_server_error(500, LLAMA_CHATPARSE_500)
    assert mode == "model_output_unparseable"
    assert "Failed to parse input at pos 1234" in detail
    assert detail.endswith("…")
    assert len(detail) < 320


def test_new_mode_is_a_model_defect_not_an_infra_mode():
    assert "model_output_unparseable" in _MODEL_DEFECT_FAILURE_MODES
    assert "model_output_unparseable" not in _INFRA_FAILURE_MODES
    assert "server_error" in _INFRA_FAILURE_MODES  # genuine 5xx still infra


# ------------------------------------------------------- transport layer fakes


class _FakeHTTPResponse:
    def __init__(self, payload: dict, status_code: int) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = "text-body"
        self.headers: dict = {}

    def json(self) -> dict:
        return self.payload


class _SequenceHTTPClient:
    """Serves a scripted list of (status, payload); a call past the end raises
    IndexError, so an unexpected retry fails the test loudly."""

    events: ClassVar[list[tuple[int, dict]]] = []
    calls = 0

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def __enter__(self) -> _SequenceHTTPClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, json: dict, **_kwargs) -> _FakeHTTPResponse:
        cls = type(self)
        status, payload = cls.events[cls.calls]
        cls.calls += 1
        return _FakeHTTPResponse(payload, status)


class _FakeSandbox:
    config = type("FakeConfig", (), {"multi_turn": False})()

    def verify(self, scenario: dict, response: dict, messages: list[dict]) -> ScenarioResult:
        return ScenarioResult(scenario["id"], True, "passed", "fake verifier")


class _FakeMultiTurnSandbox:
    config = type("FakeConfig", (), {"multi_turn": True})()

    def __init__(self) -> None:
        self.ended = 0

    def verify_multiturn_start(self, scenario: dict, **kwargs) -> dict:
        return {"scenario_state_id": "state-1", "prompt": scenario["messages"], "tools": []}

    def verify_multiturn_turn(self, scenario_state_id: str, model_response: dict) -> dict:
        return {"action": "verify-final", "passed": True, "failure_mode": "passed", "detail": "ok"}

    def verify_multiturn_end(self, scenario_state_id: str) -> dict:
        self.ended += 1
        return {"passed": False, "failure_mode": "timeout", "detail": "ended"}


def _install(monkeypatch, events: list[tuple[int, dict]]) -> None:
    import benchlocal_cli.runner as runner_module

    _SequenceHTTPClient.events = events
    _SequenceHTTPClient.calls = 0
    monkeypatch.setattr(runner_module.httpx, "Client", _SequenceHTTPClient)
    monkeypatch.setattr(runner_module.time, "sleep", lambda delay: None)


def _sandbox_meta() -> dict:
    return {"supports_sandboxed_only": True, "default_max_seconds": 60, "sampling_defaults": {"max_tokens": 16}}


def _single_scenario() -> dict:
    return {
        "id": "BF-01",
        "pack_id": "bugfind-15",
        "messages": [{"role": "user", "content": "fix it"}],
        "verifier": {"type": "_stub", "asserts": []},
    }


def _multiround_scenario() -> dict:
    return {
        "id": "CLI-01",
        "pack_id": "cli-40",
        "messages": [{"role": "user", "content": "run a command"}],
        "raw_scenario": {"kind": "multiround"},
        "verifier": {"type": "_stub", "asserts": []},
    }


def _runner(max_transient_retries: int = 3) -> Runner:
    return Runner(
        endpoint="http://localhost:9999",
        model="fake",
        enable_sandboxed_packs=True,
        max_transient_retries=max_transient_retries,
    )


# ------------------------------------------------------------ transport layer


def test_transport_does_not_retry_an_engine_rejection_of_model_output(monkeypatch):
    # ONE scripted event: any transport retry would IndexError past the script.
    _install(monkeypatch, [(500, LLAMA_TOOLCALL_500)])
    runner = _runner(max_transient_retries=3)
    runner._sandbox_clients["bugfind-15"] = _FakeSandbox()

    run = runner.run_scenario(_sandbox_meta(), _single_scenario())

    assert _SequenceHTTPClient.calls == 1
    assert run.result.passed is False
    assert run.result.failure_mode == "model_output_unparseable"
    assert "column 62072" in run.result.detail
    assert run.status_code == 500
    assert run.result.verifier_trace["transient_retries"] == 0
    assert run.result.verifier_trace["transient_errors"] == [
        "attempt 1: HTTP 500 (engine rejected model output; not retried)"
    ]


def test_transport_still_retries_genuine_5xx_even_when_bodies_are_identical(monkeypatch):
    # Positive control for the guard above: byte-identical `Loading model`
    # bodies are exactly what a booting llama.cpp returns, and they recover.
    _install(monkeypatch, [(503, LLAMA_LOADING_503), (503, LLAMA_LOADING_503), (200, OK_200)])
    runner = _runner(max_transient_retries=3)
    runner._sandbox_clients["bugfind-15"] = _FakeSandbox()

    run = runner.run_scenario(_sandbox_meta(), _single_scenario())

    assert run.result.passed is True
    assert _SequenceHTTPClient.calls == 3
    assert run.result.verifier_trace["transient_retries"] == 2


def test_transport_exhausts_retries_on_an_unrecognised_5xx_and_keeps_server_error(monkeypatch):
    _install(monkeypatch, [(500, CUDA_500)] * 4)
    runner = _runner(max_transient_retries=3)
    runner._sandbox_clients["bugfind-15"] = _FakeSandbox()

    run = runner.run_scenario(_sandbox_meta(), _single_scenario())

    assert _SequenceHTTPClient.calls == 4  # 1 + max_transient_retries, unchanged
    assert run.result.failure_mode == "server_error"
    assert run.result.detail == "HTTP 500"
    assert run.result.verifier_trace["transient_retries"] == 3


def test_multiturn_model_call_classifies_the_rejection_and_does_not_retry(monkeypatch):
    _install(monkeypatch, [(500, LLAMA_TOOLCALL_500)])
    runner = _runner(max_transient_retries=3)
    sandbox = _FakeMultiTurnSandbox()
    runner._sandbox_clients["cli-40"] = sandbox

    run = runner.run_scenario(_sandbox_meta(), _multiround_scenario())

    assert _SequenceHTTPClient.calls == 1
    assert run.result.failure_mode == "model_output_unparseable"
    assert "column 62072" in run.result.detail
    assert run.response_field_used == "multi_turn"
    assert sandbox.ended == 1  # the abandoned episode is still closed


# --------------------------------------------------------------- inline layer


def _attempt(scenario: dict, passed: bool, failure_mode: str, attempt: int, *, message: str | None) -> ScenarioRun:
    raw_response = None if message is None else {"error": {"code": 500, "message": message, "type": "server_error"}}
    detail = f"HTTP 500: engine rejected model output — {message}" if message else f"attempt {attempt}"
    return ScenarioRun(
        id=scenario["id"],
        result=ScenarioResult(scenario["id"], passed, failure_mode, detail, latency_seconds=float(attempt)),  # type: ignore[arg-type]
        raw_scenario=scenario,
        raw_response=raw_response,
        request={"attempt": attempt},
        sampling_params={"temperature": 0},
        status_code=200 if passed else 500,
    )


def _inline(
    monkeypatch,
    outcomes: Iterable[tuple[bool, str, str | None]],
    *,
    retry_failures: int = 3,
    retry_runaways: bool = False,
):
    meta = {"pack_id": "test-pack", "version": "1.0.0", "upstream_commit": "abc123", "sampling_defaults": {"max_tokens": 32}}
    scenario = {"id": "T-01", "pack_id": "test-pack", "messages": [{"role": "user", "content": "test"}]}
    monkeypatch.setattr("benchlocal_cli.runner.load_pack", lambda _pack_id: (meta, [scenario]))
    runner = Runner(endpoint="mock", model="mock", retry_failures=retry_failures, retry_runaways=retry_runaways)
    sequence = iter(outcomes)
    calls: list[tuple[bool, str]] = []

    def fake_run_scenario(_meta, current_scenario, *, repeat_index=1):
        passed, failure_mode, message = next(sequence)
        calls.append((passed, failure_mode))
        run = _attempt(current_scenario, passed, failure_mode, len(calls), message=message)
        run.repeat_index = repeat_index
        return run

    monkeypatch.setattr(runner, "run_scenario", fake_run_scenario)
    return runner.run_pack("test-pack"), calls


COL_A = "Failed to parse tool call arguments as JSON: parse error at line 1, column 62072"
COL_B = "Failed to parse tool call arguments as JSON: parse error at line 1, column 60688"
COL_C = "Failed to parse tool call arguments as JSON: parse error at line 1, column 57668"


def test_inline_does_not_retry_model_output_unparseable_by_default(monkeypatch):
    # A pass is scripted second: if the harness retried, it would "rescue" it.
    pack, calls = _inline(monkeypatch, [(False, "model_output_unparseable", COL_A), (True, "passed", None)])

    assert len(calls) == 1
    run = pack.scenarios[0]
    assert run.retry_eligible is False
    assert run.attempt_count == 1
    assert run.label == "fail"
    assert pack.pass_at_k["systematic"] == 1


def test_inline_keeps_the_infra_floor_for_genuine_server_error(monkeypatch):
    # Positive control: `server_error` still retries `max(3, retry_failures)`
    # even with model-verdict retries disabled — and identical bodies do not
    # stop it (the identity guard is scoped to the model-defect mode).
    pack, calls = _inline(
        monkeypatch,
        [(False, "server_error", None)] * 3 + [(True, "passed", None)],
        retry_failures=0,
    )

    assert len(calls) == 3
    assert pack.scenarios[0].attempt_count == 3
    assert "retry_stopped" not in (pack.scenarios[0].result.verifier_trace or {})


def test_retry_runaways_opts_the_model_defect_in_on_runaway_terms(monkeypatch):
    # Three DISTINCT generations (the issue's thinking arm samples at temp>0):
    # under --retry-runaways the mode gets the runaway ceiling, max(3, N).
    pack, calls = _inline(
        monkeypatch,
        [
            (False, "model_output_unparseable", COL_A),
            (False, "model_output_unparseable", COL_B),
            (False, "model_output_unparseable", COL_C),
            (True, "passed", None),
        ],
        retry_failures=0,
        retry_runaways=True,
    )

    assert len(calls) == 3
    assert pack.scenarios[0].attempt_count == 3
    assert pack.scenarios[0].label == "fail"
    assert "retry_stopped" not in (pack.scenarios[0].result.verifier_trace or {})


def test_identity_guard_stops_retrying_a_byte_identical_rejection(monkeypatch):
    # Same parse column twice == same generation twice: the third attempt (a
    # scripted pass, to make a phantom retry visible) must never be issued.
    pack, calls = _inline(
        monkeypatch,
        [
            (False, "model_output_unparseable", COL_A),
            (False, "model_output_unparseable", COL_A),
            (True, "passed", None),
        ],
        retry_runaways=True,
    )

    assert len(calls) == 2
    run = pack.scenarios[0]
    assert run.attempt_count == 2
    assert run.label == "fail"
    assert run.result.verifier_trace["retry_stopped"] == (
        "identical failure reproduced on attempt 2; remaining retries skipped"
    )
    assert len(run.retry_attempts) == 1  # the reproducing attempt is still recorded


def test_identity_guard_matches_against_every_earlier_attempt(monkeypatch):
    pack, calls = _inline(
        monkeypatch,
        [
            (False, "model_output_unparseable", COL_A),
            (False, "model_output_unparseable", COL_B),
            (False, "model_output_unparseable", COL_A),  # reproduces attempt 1
            (True, "passed", None),
        ],
        retry_failures=5,
        retry_runaways=True,
    )

    assert len(calls) == 3
    assert pack.scenarios[0].result.verifier_trace["retry_stopped"] == (
        "identical failure reproduced on attempt 3; remaining retries skipped"
    )


def test_identity_guard_does_not_touch_content_verdicts(monkeypatch):
    # Negative control: identical wrong answers at temp=0 keep their full pass@3
    # — collapsing that is a pass@k policy decision, not part of #152.
    pack, calls = _inline(monkeypatch, [(False, "wrong_answer", None)] * 3)

    assert len(calls) == 3
    assert pack.scenarios[0].attempt_count == 3
    assert "retry_stopped" not in (pack.scenarios[0].result.verifier_trace or {})


# ------------------------------------------------------------ misc plumbing


def test_truncation_reclassification_leaves_the_new_mode_alone():
    result = ScenarioResult("X", False, "model_output_unparseable", "HTTP 500: engine rejected model output — x")
    raw = {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
    assert Runner._reclassify_if_truncated(result, raw) == result


def test_saved_json_round_trips_the_new_mode():
    row = {
        "id": "TC-03",
        "passed": False,
        "failure_mode": "model_output_unparseable",
        "detail": "HTTP 500: engine rejected model output — Failed to parse tool call arguments as JSON",
        "latency_seconds": 267.0,
        "raw_response": LLAMA_TOOLCALL_500,
        "status_code": 500,
    }
    run = scenario_run_from_dict(row)
    assert run.result.failure_mode == "model_output_unparseable"
    assert run.status_code == 500
    assert run.raw_response == LLAMA_TOOLCALL_500
