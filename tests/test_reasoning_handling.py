from __future__ import annotations

from benchlocal_cli.runner import Runner, build_request
from benchlocal_cli.scoring.common import content_with_source


def _meta() -> dict:
    return {
        "sampling_defaults": {
            "temperature": 0,
            "max_tokens": 1024,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        "default_max_seconds": 60,
    }


def _scenario(overrides: dict | None = None) -> dict:
    return {
        "id": "x",
        "messages": [{"role": "user", "content": "Say hello."}],
        "verifier": {"type": "instruct_follow", "asserts": [{"kind": "required_phrase", "value": "hello"}]},
        "sampling_overrides": overrides or {},
    }


def test_build_request_injects_thinking_off_by_default():
    request, _ = build_request(_scenario(), _meta(), "fake")

    assert request["chat_template_kwargs"] == {"enable_thinking": False}


def test_build_request_enable_thinking_bumps_tokens():
    request, _ = build_request(
        _scenario(),
        _meta(),
        "fake",
        thinking_enabled=True,
        thinking_max_tokens=4096,
    )

    assert request["chat_template_kwargs"] == {"enable_thinking": True}
    assert request["max_tokens"] == 4096


def test_build_request_enable_thinking_overrides_scenario_token_budget():
    request, _ = build_request(
        _scenario({"max_tokens": 512}),
        _meta(),
        "fake",
        thinking_enabled=True,
        thinking_max_tokens=4096,
    )

    assert request["max_tokens"] == 4096


def test_extra_body_wins_over_defaults_but_loses_to_scenario_overrides():
    request, _ = build_request(
        _scenario({"foo": "scenario"}),
        _meta(),
        "fake",
        extra_body={"foo": "extra", "bar": "extra"},
    )

    assert request["foo"] == "scenario"
    assert request["bar"] == "extra"


def test_content_fallback_reads_reasoning_content():
    response = {"choices": [{"message": {"content": "", "reasoning_content": "hello from thought"}}]}

    assert content_with_source(response) == ("hello from thought", "message.reasoning_content")


def test_runner_json_records_thinking_state_and_response_field():
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        thinking_enabled=True,
        mock_responses={"x": {"choices": [{"message": {"content": "", "reasoning": "hello"}}]}},
    )

    direct = runner.run_scenario(_meta(), _scenario(), repeat_index=1)
    assert direct.response_field_used == "message.reasoning"

    run = runner.run([], mode="quick")
    assert run.to_dict()["thinking_enabled"] is True
