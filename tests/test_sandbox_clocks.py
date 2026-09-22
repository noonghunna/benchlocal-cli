"""#149: the runner-side clocks do not reach agent-owned sandboxes.

`--timeout-per-case` and `--model-turn-timeout` bound runner-side HTTP calls,
but hermes (and aider) run their agent inside the container and make their own
model calls, so only the in-container subprocess cap applies. These tests pin
three things: an explicit `--timeout-per-case` floors that cap (never lowers
it), the explicit env override still wins, and the effective clocks are logged
at run start with a persisted warning for any knob that was set but is inert.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from benchlocal_cli.runner import DEFAULT_MODEL_TURN_TIMEOUT_S, Runner
from benchlocal_cli.sandbox import config_for_pack, resolve_episode_cap

_ENV = "BENCHLOCAL_HERMES_SUBPROCESS_TIMEOUT_S"


@pytest.fixture(autouse=True)
def _clean_hermes_env(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    # Keep config_for_pack away from any host hermes-agent install.
    monkeypatch.setenv("HERMES_AGENT_FORCE_BAKED", "1")
    monkeypatch.delenv("HERMES_AGENT_HOST_PATH", raising=False)


def _env(config) -> dict[str, str]:
    return dict(config.env)


# ---------------------------------------------------------------------------
# resolve_episode_cap
# ---------------------------------------------------------------------------


def test_hermes_default_cap_is_the_300s_guard():
    cap = resolve_episode_cap("hermesagent-20", environ={})
    assert cap is not None
    assert cap.seconds == 300.0
    assert cap.source == "default"
    assert cap.env_name == "HERMES_SUBPROCESS_TIMEOUT_S"


def test_hermes_explicit_timeout_per_case_floors_the_cap():
    cap = resolve_episode_cap("hermesagent-20", timeout_per_case=900, environ={})
    assert cap.seconds == 900.0
    assert cap.source == "budget"


def test_hermes_timeout_per_case_never_lowers_the_cap():
    cap = resolve_episode_cap("hermesagent-20", timeout_per_case=120, environ={})
    assert cap.seconds == 300.0
    assert cap.source == "default"


def test_hermes_auto_scaled_budget_does_not_floor_the_cap():
    # Only the EXPLICIT per-case value floors it: the scaled budget carries the
    # thinking-token multiplier calibrated for one completion, not an episode.
    cap = resolve_episode_cap("hermesagent-20", batch_timeout_s=4800, environ={})
    assert cap.seconds == 300.0
    assert cap.source == "default"


def test_hermes_env_override_wins_over_timeout_per_case():
    cap = resolve_episode_cap(
        "hermesagent-20", timeout_per_case=900, environ={_ENV: "600"}
    )
    assert cap.seconds == 600.0
    assert cap.source == "env"
    assert cap.override_env == _ENV


def test_hermes_blank_env_override_is_unset():
    cap = resolve_episode_cap("hermesagent-20", timeout_per_case=900, environ={_ENV: "  "})
    assert cap.seconds == 900.0
    assert cap.source == "budget"


@pytest.mark.parametrize("value", ["abc", "0", "-5"])
def test_hermes_invalid_env_override_fails_loud(value):
    with pytest.raises(ValueError, match=_ENV):
        resolve_episode_cap("hermesagent-20", environ={_ENV: value})


def test_aider_batch_budget_floors_the_batch_cap():
    default = resolve_episode_cap("aider-polyglot-30", environ={})
    raised = resolve_episode_cap("aider-polyglot-30", batch_timeout_s=4000, environ={})
    lowered = resolve_episode_cap("aider-polyglot-30", batch_timeout_s=1800, environ={})
    assert (default.seconds, default.source) == (3600.0, "default")
    assert (raised.seconds, raised.source) == (4000.0, "budget")
    assert (lowered.seconds, lowered.source) == (3600.0, "default")
    assert default.env_name == "AIDER_BENCHMARK_TIMEOUT_S"


@pytest.mark.parametrize("pack_id", ["cli-40", "bugfind-15", "humaneval-plus-30", "lcb-v6-30"])
def test_runner_owned_packs_have_no_episode_cap(pack_id):
    assert resolve_episode_cap(pack_id, timeout_per_case=900, environ={}) is None


# ---------------------------------------------------------------------------
# config_for_pack — what actually reaches the container
# ---------------------------------------------------------------------------


def test_hermes_config_default_is_unchanged():
    config = config_for_pack("hermesagent-20")
    assert _env(config)["HERMES_SUBPROCESS_TIMEOUT_S"] == "300"
    assert config.request_timeout_s == 900.0
    assert config.episode_cap.source == "default"


def test_hermes_config_timeout_per_case_reaches_the_container():
    config = config_for_pack("hermesagent-20", batch_timeout_s=900, timeout_per_case=900)
    assert _env(config)["HERMES_SUBPROCESS_TIMEOUT_S"] == "900"
    assert config.episode_cap.seconds == 900.0
    # The outer /verify-start read must outlast the inner kill.
    assert config.request_timeout_s == 1200.0


def test_hermes_config_read_timeout_tracks_a_large_cap_with_headroom():
    config = config_for_pack("hermesagent-20", batch_timeout_s=1200, timeout_per_case=1200)
    assert _env(config)["HERMES_SUBPROCESS_TIMEOUT_S"] == "1200"
    assert config.request_timeout_s == 1500.0


def test_hermes_config_env_override_still_wins(monkeypatch):
    monkeypatch.setenv(_ENV, "600")
    config = config_for_pack("hermesagent-20", batch_timeout_s=900, timeout_per_case=900)
    assert _env(config)["HERMES_SUBPROCESS_TIMEOUT_S"] == "600"
    assert config.episode_cap.source == "env"
    assert config.request_timeout_s == 900.0


def test_aider_config_batch_floor_is_unchanged():
    default = config_for_pack("aider-polyglot-30")
    raised = config_for_pack("aider-polyglot-30", batch_timeout_s=4000)
    assert _env(default)["AIDER_BENCHMARK_TIMEOUT_S"] == "3600"
    assert default.request_timeout_s == 3900.0
    assert _env(raised)["AIDER_BENCHMARK_TIMEOUT_S"] == "4000"
    assert raised.request_timeout_s == 4300.0
    assert raised.episode_cap.source == "budget"


def test_runner_owned_config_carries_no_cap():
    assert config_for_pack("cli-40", batch_timeout_s=300).episode_cap is None


# ---------------------------------------------------------------------------
# Runner._start_sandboxes — the clock line and inert-knob warnings
# ---------------------------------------------------------------------------


class _RecordingSandboxClient:
    instances: ClassVar[list[_RecordingSandboxClient]] = []

    def __init__(self, config, model_endpoint=None) -> None:
        self.config = config
        self.model_endpoint = model_endpoint
        self.started = False
        type(self).instances.append(self)

    def start(self, run_dir=None) -> None:
        self.started = True

    def stop(self, log_dir=None) -> None:
        return None


def _start(monkeypatch, pack_id: str, **runner_kwargs) -> tuple[list[str], _RecordingSandboxClient]:
    _RecordingSandboxClient.instances = []
    monkeypatch.setattr("benchlocal_cli.runner.SandboxClient", _RecordingSandboxClient)
    runner = Runner(
        endpoint="http://localhost:9999",
        model="fake",
        enable_sandboxed_packs=True,
        # Skip the startup TPS probe so the default budget path never hits the network.
        measured_tps=100,
        **runner_kwargs,
    )
    warnings: list[str] = []
    runner._start_sandboxes([pack_id], warnings)
    (client,) = _RecordingSandboxClient.instances
    assert client.started
    return warnings, client


def test_start_logs_hermes_clocks_with_timeout_per_case_floor(monkeypatch, capsys):
    warnings, client = _start(
        monkeypatch, "hermesagent-20", timeout_per_case=900, thinking_enabled=False
    )
    err = capsys.readouterr().err
    assert (
        "[runner] hermesagent-20 clocks: episode 900s (floor from --timeout-per-case 900; "
        "default 300s), verify-start read 1200s, model-turn n/a "
        "(agent makes its own model calls in-container)"
    ) in err
    assert dict(client.config.env)["HERMES_SUBPROCESS_TIMEOUT_S"] == "900"
    assert warnings == []


def test_start_logs_hermes_default_and_flags_inert_scaled_budget(monkeypatch, capsys):
    # hermes defaults to thinking ON, so the auto-scaled per-case budget is
    # 300s x (16384 / 1024) — large, and inert for the episode. Say so.
    warnings, client = _start(monkeypatch, "hermesagent-20")
    err = capsys.readouterr().err
    assert "[runner] hermesagent-20 clocks: episode 300s (default; raise with --timeout-per-case or " + _ENV in err
    assert "auto-scaled per-case budget 4800s does not apply" in err
    assert dict(client.config.env)["HERMES_SUBPROCESS_TIMEOUT_S"] == "300"
    assert warnings == []


def test_start_warns_when_env_override_undercuts_timeout_per_case(monkeypatch, capsys):
    monkeypatch.setenv(_ENV, "600")
    warnings, _client = _start(
        monkeypatch, "hermesagent-20", timeout_per_case=900, thinking_enabled=False
    )
    err = capsys.readouterr().err
    assert "episode 600s (" + _ENV + "; default 300s; --timeout-per-case 900 not applied)" in err
    assert len(warnings) == 1
    assert warnings[0].startswith(f"hermesagent-20: {_ENV}=600 overrides --timeout-per-case 900")
    assert "scenarios are cut at 600s" in warnings[0]


@pytest.mark.parametrize("model_turn_timeout", [900, 0])
def test_start_warns_when_model_turn_timeout_cannot_apply(monkeypatch, capsys, model_turn_timeout):
    warnings, _client = _start(
        monkeypatch,
        "hermesagent-20",
        timeout_per_case=900,
        thinking_enabled=False,
        model_turn_timeout=model_turn_timeout,
    )
    err = capsys.readouterr().err
    assert len(warnings) == 1
    assert warnings[0].startswith(
        "hermesagent-20: --model-turn-timeout/BENCHLOCAL_MODEL_TURN_TIMEOUT="
    )
    assert "does not apply" in warnings[0]
    assert "episode cap is 900s (HERMES_SUBPROCESS_TIMEOUT_S)" in warnings[0]
    assert warnings[0] in err


def test_default_model_turn_timeout_raises_no_warning(monkeypatch):
    warnings, _client = _start(
        monkeypatch,
        "hermesagent-20",
        timeout_per_case=900,
        thinking_enabled=False,
        model_turn_timeout=DEFAULT_MODEL_TURN_TIMEOUT_S,
    )
    assert warnings == []


def test_start_logs_aider_batch_clock(monkeypatch, capsys):
    warnings, client = _start(
        monkeypatch, "aider-polyglot-30", timeout_per_case=4000, thinking_enabled=False
    )
    err = capsys.readouterr().err
    assert (
        "[runner] aider-polyglot-30 clocks: batch 4000s (floor from --timeout-per-case 4000; "
        "default 3600s), verify-start read 4300s, model-turn n/a"
    ) in err
    assert dict(client.config.env)["AIDER_BENCHMARK_TIMEOUT_S"] == "4000"
    assert warnings == []


def test_start_logs_runner_owned_clocks_for_cli_pack(monkeypatch, capsys):
    warnings, _client = _start(monkeypatch, "cli-40", thinking_enabled=False)
    err = capsys.readouterr().err
    assert (
        "[runner] cli-40 clocks: per-case 300s (runner-side HTTP), model-turn 300s, "
        "verify http 300s"
    ) in err
    assert warnings == []
