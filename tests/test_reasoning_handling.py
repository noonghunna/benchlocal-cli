from __future__ import annotations

from benchlocal_cli.runner import Runner, build_request
from benchlocal_cli.scoring.common import content_with_source, sanitize_reasoning_tags


def _meta(default_thinking: str | None = None) -> dict:
    meta = {
        "sampling_defaults": {
            "temperature": 0,
            "max_tokens": 1024,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        "default_max_seconds": 60,
    }
    if default_thinking is not None:
        meta["default_thinking"] = default_thinking
    return meta


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


def test_build_request_enable_thinking_default_budget_is_16k():
    request, _ = build_request(
        _scenario(),
        _meta(),
        "fake",
        thinking_enabled=True,
    )

    assert request["max_tokens"] == 16384


def test_build_request_enable_thinking_overrides_scenario_token_budget():
    request, _ = build_request(
        _scenario({"max_tokens": 512}),
        _meta(),
        "fake",
        thinking_enabled=True,
        thinking_max_tokens=4096,
    )

    assert request["max_tokens"] == 4096


def test_sampling_override_caps_base_arm_max_tokens():
    # #28: --max-tokens flows through sampling_overrides and caps the
    # base/no-think arm (overrides the per-pack default).
    request, _ = build_request(
        _scenario(),
        _meta(),
        "fake",
        sampling_overrides={"max_tokens": 512},
    )

    assert request["chat_template_kwargs"] == {"enable_thinking": False}
    assert request["max_tokens"] == 512  # pack default 1024 overridden


def test_thinking_budget_wins_over_sampling_override_max_tokens():
    # #28: the thinking arm always uses thinking_max_tokens, even when a smaller
    # --max-tokens was set for the base arm. The CLI resolves the precedence so
    # an explicit --thinking-max-tokens wins for the thinking arm.
    request, _ = build_request(
        _scenario(),
        _meta(default_thinking="on"),
        "fake",
        thinking_max_tokens=16384,
        sampling_overrides={"max_tokens": 4096},
    )

    assert request["chat_template_kwargs"] == {"enable_thinking": True}
    assert request["max_tokens"] == 16384


def test_extra_body_wins_over_defaults_but_loses_to_scenario_overrides():
    request, _ = build_request(
        _scenario({"foo": "scenario"}),
        _meta(),
        "fake",
        extra_body={"foo": "extra", "bar": "extra"},
    )

    assert request["foo"] == "scenario"
    assert request["bar"] == "extra"


def test_sanitize_reasoning_tags_strips_well_formed_blocks():
    assert sanitize_reasoning_tags("<think>hidden reasoning</think><solution>done</solution>") == "<solution>done</solution>"


def test_sanitize_reasoning_tags_strips_orphan_closing_tags():
    assert sanitize_reasoning_tags("</think> </think> <solution>done</solution>") == "<solution>done</solution>"


def test_sanitize_reasoning_tags_strips_orphan_opening_tags():
    assert sanitize_reasoning_tags("<think> <solution>done</solution>") == "<solution>done</solution>"


def test_sanitize_reasoning_tags_keeps_clean_content_byte_identical():
    clean = "  hello <not-think> world  "
    assert sanitize_reasoning_tags(clean) == clean


def test_sanitize_reasoning_tags_all_tags_yields_empty():
    assert sanitize_reasoning_tags("</think> </think> <think></think>") == ""


def test_content_with_source_sanitizes_leaked_think_tags():
    response = {"choices": [{"message": {"content": "<think>hidden</think>hello"}}]}

    assert content_with_source(response) == ("hello", "message.content")


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



def test_build_request_uses_pack_default_thinking_on():
    request, sampling = build_request(
        _scenario(),
        _meta(default_thinking="on"),
        "fake",
        thinking_max_tokens=4096,
    )

    assert request["chat_template_kwargs"] == {"enable_thinking": True}
    assert request["max_tokens"] == 4096
    assert sampling["chat_template_kwargs"] == {"enable_thinking": True}


def test_build_request_force_no_thinking_overrides_pack_default_on():
    request, _ = build_request(
        _scenario(),
        _meta(default_thinking="on"),
        "fake",
        thinking_enabled=False,
        thinking_max_tokens=4096,
    )

    assert request["chat_template_kwargs"] == {"enable_thinking": False}
    assert request["max_tokens"] == 1024


def test_scenario_can_disable_pack_default_thinking_without_token_bump():
    request, _ = build_request(
        _scenario({"chat_template_kwargs": {"enable_thinking": False}, "max_tokens": 512}),
        _meta(default_thinking="on"),
        "fake",
        thinking_max_tokens=4096,
    )

    assert request["chat_template_kwargs"] == {"enable_thinking": False}
    assert request["max_tokens"] == 512


def test_pack_result_records_effective_thinking_mode(monkeypatch):
    meta = _meta(default_thinking="on")
    meta.update({"version": "test", "upstream_commit": "local", "verifier_module": "instruct_follow"})
    monkeypatch.setattr(
        "benchlocal_cli.runner.load_pack",
        lambda pack_id: (meta, [_scenario()]),
    )

    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        mock_responses={"x": {"choices": [{"message": {"content": "hello"}}]}},
    )

    pack = runner.run_pack("instructfollow-15")
    assert pack.thinking_enabled is True
    assert pack.to_dict()["thinking_enabled"] is True

    forced = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        thinking_enabled=False,
        mock_responses={"x": {"choices": [{"message": {"content": "hello"}}]}},
    )
    pack_forced = forced.run_pack("instructfollow-15")
    assert pack_forced.thinking_enabled is False



def test_thinking_on_uses_recommended_sampler_over_greedy_defaults():
    request, sampling = build_request(
        _scenario({"temperature": 0, "top_p": 1}),
        _meta(default_thinking="on"),
        "fake",
        thinking_max_tokens=4096,
    )

    assert request["chat_template_kwargs"] == {"enable_thinking": True}
    assert request["temperature"] == 1.0
    assert request["top_p"] == 0.95
    assert request["top_k"] == 20
    assert request["min_p"] == 0.0
    assert request["max_tokens"] == 4096
    assert sampling["temperature"] == 1.0


def test_thinking_off_preserves_greedy_defaults():
    request, _ = build_request(
        _scenario(),
        _meta(default_thinking="off"),
        "fake",
    )

    assert request["chat_template_kwargs"] == {"enable_thinking": False}
    assert request["temperature"] == 0
    assert "top_k" not in request
    assert "min_p" not in request


def test_sampling_from_server_strips_recommended_thinking_sampler():
    request, _ = build_request(
        _scenario(),
        _meta(default_thinking="on"),
        "fake",
        sampling_from_server=True,
        thinking_max_tokens=4096,
    )

    assert request["chat_template_kwargs"] == {"enable_thinking": True}
    assert request["max_tokens"] == 4096
    assert "temperature" not in request
    assert "top_p" not in request
    assert "top_k" not in request
    assert "min_p" not in request


def test_cli_sampling_override_wins_over_recommended_thinking_sampler():
    request, _ = build_request(
        _scenario(),
        _meta(default_thinking="on"),
        "fake",
        sampling_overrides={"temperature": 0.7, "top_p": 0.8, "top_k": 40, "min_p": 0.05},
        thinking_max_tokens=4096,
    )

    assert request["temperature"] == 0.7
    assert request["top_p"] == 0.8
    assert request["top_k"] == 40
    assert request["min_p"] == 0.05
    assert request["max_tokens"] == 4096


def test_custom_thinking_sampler_is_configurable():
    request, _ = build_request(
        _scenario(),
        _meta(default_thinking="on"),
        "fake",
        thinking_sampler={"temperature": 0.6, "top_p": 0.9},
        thinking_max_tokens=4096,
    )

    assert request["temperature"] == 0.6
    assert request["top_p"] == 0.9
    assert "top_k" not in request


def test_reference_date_metadata_is_injected_into_messages():
    scenario = _scenario()
    scenario.update({"benchmark_reference_date": "2026-03-20", "benchmark_reference_day": "Friday"})
    scenario["messages"] = [
        {"role": "system", "content": "Use available tools."},
        {"role": "user", "content": "Schedule this for next Monday."},
    ]

    request, _ = build_request(scenario, _meta(), "fake")

    assert "Benchmark reference date: 2026-03-20 (Friday)." in request["messages"][0]["content"]
    assert scenario["messages"][0]["content"] == "Use available tools."
