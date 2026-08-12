from __future__ import annotations

import argparse

import pytest

from benchlocal_cli.cli import _parse_reasoning_effort
from benchlocal_cli.runner import (
    THINKING_CONTROL_EFFORT,
    THINKING_CONTROL_ENABLE,
    THINKING_CONTROL_NONE,
    Runner,
    _endpoint_base,
    build_request,
    thinking_control_from_template,
)
from benchlocal_cli.types import RunResult


def _meta(default_thinking: str = "off") -> dict:
    return {
        "sampling_defaults": {
            "temperature": 0,
            "max_tokens": 1024,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        "default_thinking": default_thinking,
    }


def _scenario() -> dict:
    return {
        "id": "x",
        "messages": [{"role": "user", "content": "Say hello."}],
        "verifier": {"type": "instruct_follow", "asserts": []},
    }


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("{% if enable_thinking %}think{% endif %}", THINKING_CONTROL_ENABLE),
        ("Thinking effort: {{ reasoning_effort }}", THINKING_CONTROL_EFFORT),
        (
            "{{ reasoning_effort }} {% if enable_thinking %}think{% endif %}",
            THINKING_CONTROL_ENABLE,
        ),
        ("{{ messages }}", THINKING_CONTROL_NONE),
    ],
)
def test_template_detection_prefers_compatible_qwen_control(template, expected):
    assert thinking_control_from_template(template) == expected


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://host:8000", "http://host:8000"),
        ("http://host:8000/v1", "http://host:8000"),
        ("http://host:8000/v1/chat/completions", "http://host:8000"),
        ("http://host:8000/chat/completions/", "http://host:8000"),
    ],
)
def test_endpoint_base_normalizes_supported_endpoint_forms(endpoint, expected):
    assert _endpoint_base(endpoint) == expected


def test_effort_control_sends_standard_and_template_copy_when_off():
    request, sampling = build_request(
        _scenario(),
        _meta(default_thinking="on"),
        "fake",
        thinking_enabled=False,
        thinking_control=THINKING_CONTROL_EFFORT,
        reasoning_effort="xhigh",
    )

    assert request["reasoning_effort"] == "none"
    assert request["chat_template_kwargs"] == {"reasoning_effort": "none"}
    assert "enable_thinking" not in request["chat_template_kwargs"]
    assert sampling["reasoning_effort"] == "none"
    assert request["max_tokens"] == 1024


def test_effort_control_uses_configured_on_value_and_thinking_budget():
    request, _ = build_request(
        _scenario(),
        _meta(),
        "fake",
        thinking_enabled=True,
        thinking_max_tokens=4096,
        thinking_control=THINKING_CONTROL_EFFORT,
        reasoning_effort=0.75,
    )

    assert request["reasoning_effort"] == 0.75
    assert request["chat_template_kwargs"] == {"reasoning_effort": 0.75}
    assert request["max_tokens"] == 4096


def test_no_detected_control_does_not_send_qwen_key():
    request, _ = build_request(
        _scenario(),
        _meta(),
        "fake",
        thinking_enabled=False,
        thinking_control=THINKING_CONTROL_NONE,
    )

    assert request["chat_template_kwargs"] == {}
    assert "reasoning_effort" not in request


def test_props_detection_selects_effort_and_announces(monkeypatch, capsys):
    runner = Runner(endpoint="http://localhost:9999", model="fake")
    monkeypatch.setattr(
        runner,
        "_props_chat_template",
        lambda: "Thinking effort: {{ reasoning_effort }}",
    )
    warnings: list[str] = []

    runner._resolve_thinking_control(warnings)

    assert runner.thinking_control == THINKING_CONTROL_EFFORT
    assert runner.thinking_control_source == "GET /props chat_template"
    assert warnings == []
    assert "thinking control 'reasoning_effort'" in capsys.readouterr().err


def test_props_detection_reports_template_with_no_switch(monkeypatch):
    runner = Runner(endpoint="http://localhost:9999", model="fake")
    monkeypatch.setattr(runner, "_props_chat_template", lambda: "{{ messages }}")
    warnings: list[str] = []

    runner._resolve_thinking_control(warnings)

    assert runner.thinking_control == THINKING_CONTROL_NONE
    assert any("cannot be enforced" in warning for warning in warnings)


def test_behavioral_probe_is_opt_in(monkeypatch):
    runner = Runner(endpoint="http://localhost:9999", model="fake")
    monkeypatch.setattr(runner, "_props_chat_template", lambda: None)
    monkeypatch.setattr(
        runner,
        "_probe_thinking_control",
        lambda: pytest.fail("probe must remain opt-in"),
    )

    runner._resolve_thinking_control([])

    assert runner.thinking_control == THINKING_CONTROL_ENABLE


def test_default_template_control_remains_silent(monkeypatch, capsys):
    runner = Runner(endpoint="http://localhost:9999", model="fake")
    monkeypatch.setattr(
        runner,
        "_props_chat_template",
        lambda: "{% if enable_thinking %}think{% endif %}",
    )

    runner._resolve_thinking_control([])

    assert runner.thinking_control == THINKING_CONTROL_ENABLE
    assert capsys.readouterr().err == ""


def test_behavioral_probe_finds_effort_and_sends_both_locations(monkeypatch):
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        probe_thinking_control=True,
    )
    requests: list[dict] = []

    monkeypatch.setattr(runner, "_endpoint_reachable", lambda: True)

    def fake_post(request, timeout, *, max_attempts=None):
        requests.append(request)
        content = "" if len(requests) == 1 else "OK"
        return 200, {"choices": [{"message": {"content": content}}]}, None

    monkeypatch.setattr(runner, "_post_chat", fake_post)

    assert runner._probe_thinking_control() == THINKING_CONTROL_EFFORT
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert requests[1]["reasoning_effort"] == "none"
    assert requests[1]["chat_template_kwargs"] == {"reasoning_effort": "none"}


def test_behavioral_probe_stops_when_enable_thinking_works(monkeypatch):
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        probe_thinking_control=True,
    )
    requests: list[dict] = []
    monkeypatch.setattr(runner, "_endpoint_reachable", lambda: True)

    def fake_post(request, timeout, *, max_attempts=None):
        requests.append(request)
        return 200, {"choices": [{"message": {"content": "OK"}}]}, None

    monkeypatch.setattr(runner, "_post_chat", fake_post)

    assert runner._probe_thinking_control() == THINKING_CONTROL_ENABLE
    assert len(requests) == 1
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_behavioral_probe_reports_no_working_switch(monkeypatch):
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        probe_thinking_control=True,
    )
    monkeypatch.setattr(runner, "_endpoint_reachable", lambda: True)
    monkeypatch.setattr(
        runner,
        "_post_chat",
        lambda request, timeout, *, max_attempts=None: (
            200,
            {"choices": [{"message": {"content": ""}}]},
            None,
        ),
    )

    assert runner._probe_thinking_control() == THINKING_CONTROL_NONE


def test_unreachable_behavioral_probe_is_inconclusive(monkeypatch):
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        probe_thinking_control=True,
    )
    monkeypatch.setattr(runner, "_endpoint_reachable", lambda: False)

    assert runner._probe_thinking_control() is None


def test_inconclusive_probe_keeps_compatibility_default(monkeypatch, capsys):
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        probe_thinking_control=True,
    )
    monkeypatch.setattr(runner, "_props_chat_template", lambda: None)
    monkeypatch.setattr(runner, "_probe_thinking_control", lambda: None)

    runner._resolve_thinking_control([])

    assert runner.thinking_control == THINKING_CONTROL_ENABLE
    assert capsys.readouterr().err == ""


def test_explicit_effort_skips_detection(monkeypatch):
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        reasoning_effort="minimal",
    )
    monkeypatch.setattr(
        runner,
        "_props_chat_template",
        lambda: pytest.fail("explicit effort must skip detection"),
    )

    runner._resolve_thinking_control([])

    assert runner.thinking_control == THINKING_CONTROL_EFFORT
    assert runner.reasoning_effort == "minimal"


def _result(**overrides) -> RunResult:
    values = {
        "schema_version": "1",
        "runner_version": "test",
        "endpoint": "http://localhost:8000",
        "model": "fake",
        "mode": "custom",
        "started_at": "2026-08-12T00:00:00Z",
        "finished_at": "2026-08-12T00:00:01Z",
        "packs": [],
        "totals": {"passed": 0, "total": 0, "score": 0.0},
    }
    values.update(overrides)
    return RunResult(**values)


def test_default_result_json_remains_compatible():
    result = _result().to_dict()

    assert "thinking_control" not in result
    assert "reasoning_effort" not in result


def test_effort_result_json_records_resolved_control():
    result = _result(
        thinking_control=THINKING_CONTROL_EFFORT,
        reasoning_effort=0.75,
    ).to_dict()

    assert result["thinking_control"] == THINKING_CONTROL_EFFORT
    assert result["reasoning_effort"] == 0.75


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("none", "none"),
        ("XHIGH", "xhigh"),
        ("0.0", 0.0),
        ("0.99", 0.99),
    ],
)
def test_reasoning_effort_cli_parser(raw, expected):
    assert _parse_reasoning_effort(raw) == expected


@pytest.mark.parametrize("raw", ["", "ultra", "-0.1", "1.0"])
def test_reasoning_effort_cli_parser_rejects_invalid_values(raw):
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_reasoning_effort(raw)
