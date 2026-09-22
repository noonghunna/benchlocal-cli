"""Core orchestrator for pack loading, endpoint calls, scoring, and aggregation."""

from __future__ import annotations

import importlib
import json
import os
import re
import signal
import statistics
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import datetime, timezone
from importlib import resources

import httpx

from benchlocal_cli import __version__
from benchlocal_cli.diagnostics import combine_runaway, pack_diagnostics, runaway_summary
from benchlocal_cli.thinking_validity import thinking_validity_for_packs
from benchlocal_cli.sandbox import SandboxClient, SandboxConfig, config_for_pack
from benchlocal_cli.streaming import StreamStall, post_chat_streaming
from benchlocal_cli.scoring.common import content_with_source, sanitize_response_text_fields
from benchlocal_cli.types import (
    RUNAWAY_FAILURE_MODES,
    PackResult,
    RunResult,
    ScenarioResult,
    ScenarioRun,
)


class _SpendGuardExceeded(BaseException):
    """Cumulative token budget (`--max-total-tokens`) exceeded mid-run.

    Subclasses BaseException (like KeyboardInterrupt) so the per-scenario
    `except Exception` paths don't swallow it — it must propagate up to the
    top-level run() caller, which stops the run and reports partial results.
    """

    def __init__(self, tokens_used: int, cap: int) -> None:
        self.tokens_used = tokens_used
        self.cap = cap
        super().__init__(
            f"spend guard: cumulative {tokens_used} tokens exceeded --max-total-tokens={cap}"
        )


PACK_MODES = {
    # quick — 30 scenarios, no Docker, ~5-10 min
    "quick": ["toolcall-15", "instructfollow-15"],
    # medium — 75 scenarios = full deterministic suite, no Docker, ~15-25 min
    "medium": [
        "toolcall-15",
        "instructfollow-15",
        "structoutput-15",
        "dataextract-15",
        "reasonmath-15",
    ],
    # full — 150 scenarios = medium + sandboxed packs, requires Docker, ~25-40 min
    "full": [
        "toolcall-15",
        "instructfollow-15",
        "structoutput-15",
        "dataextract-15",
        "reasonmath-15",
        "bugfind-15",
        "hermesagent-20",
        "cli-40",
    ],
    # reasoning — opt-in pure/code reasoning suite. Kept separate from
    # --full so the canonical 8-pack loop stays fast and stable.
    "reasoning": [
        "humaneval-plus-30",
        "lcb-v6-30",
        "gpqa-diamond",
        "gsm-symbolic-30",
    ],
}

# Modes that require Docker sandbox containers. The runner will auto-enable
# sandboxed packs (no flag needed) and fail loud if Docker isn't available.
SANDBOX_MODES = {"full", "reasoning"}

# Just the sandboxed packs — used by `--sandboxed-only` for debug iteration
# on the verifier containers without paying the deterministic-pack cost.
SANDBOXED_PACK_IDS = ["bugfind-15", "hermesagent-20", "cli-40", "humaneval-plus-30", "lcb-v6-30"]
DEFAULT_TIMEOUT_PER_CASE = 60.0
# Reference emission length that a pack's flat `default_max_seconds` was
# written against. The timeout multiplier scales off THIS, never off the pack's
# own max_tokens — those are two different quantities that shared one field
# until #103, so raising a pack's emission budget silently shrank its clock.
# Packs may override with `timeout_baseline_tokens` in their meta.
DEFAULT_TIMEOUT_BASELINE_TOKENS = 1024
# Default cap on one runner-owned sandbox model call (--model-turn-timeout).
# Runner and CLI share this so the runner can tell when an operator changed it
# on a pack it cannot reach (#149).
DEFAULT_MODEL_TURN_TIMEOUT_S = 300.0
# #157: with --stream, the longest gap between two data events before a request
# is declared stalled. Deliberately generous — it must outlast any legitimate
# pause mid-generation — because it only has to beat the total budget, which on
# a thinking run can be many minutes.
DEFAULT_STREAM_STALL_TIMEOUT_S = 120.0

# #61: content-verdict failure modes that a token-cap truncation should override.
# A failure on any of these *while* finish_reason == "length" is confounded by
# truncation — the model was cut off mid-output — so it's reclassified to
# `token_limit`. Excludes passes (a verified-correct answer that happened to hit
# the cap is still correct) and infra/transport modes (timeout / http_error /
# server_error / verifier_not_implemented), which have no normal finish_reason.
_CONTENT_FAILURE_MODES = frozenset({
    "verifier_fail",
    "wrong_answer",
    "invalid_json",
    "no_answer_found",
    "missing_field",
    "extra_fields",
    "schema_violation",
    "wrong_structure",
})

# A multi-turn episode that stops at a turn cut off by the token cap is graded
# on the state its completed turns left behind. A failure there is the cap's
# doing, so it is relabelled `token_limit` like #61 does for single turns —
# including the cli sandbox's "agent loop ended before success", which is only
# true because the harness ended the loop at the truncated turn.
_TRUNCATED_TURN_RECLASSIFY_MODES = _CONTENT_FAILURE_MODES | {"agent_loop_exhausted"}

# Inline failure-only retries (#111) deliberately distinguish model verdicts
# from harness failures and expensive runaway behavior.
DEFAULT_INLINE_RETRY_ATTEMPTS = 3

# #152: a 5xx whose body says the ENGINE rejected the MODEL's output is not a
# server fault — it is a deterministic property of that generation, and every
# retry reproduces it byte-for-byte (the issue's evidence: each distinct parse
# column appeared exactly 4x across 48 engine 500s on a healthy server). Such a
# response is classified `model_output_unparseable`, never retried at the
# transport layer, and — because the generation typically ran to the cap before
# the parser refused it — retried inline only under --retry-runaways, like the
# other expensive never-finished failures.
#
# Patterns are matched against the engine's error message. Both are verified
# against llama.cpp `common/chat.cpp`: the tool-call argument parser and the
# chat-format parser (`common_chat_parse`) that rejects unparseable output.
# vLLM's tool parsers log and swallow parse failures (no 5xx), so they need no
# entry; an unrecognised 5xx stays `server_error` and keeps its retries.
_MODEL_OUTPUT_REJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Failed to parse tool call arguments as JSON", re.IGNORECASE),
    re.compile(r"Failed to parse input at pos \d+", re.IGNORECASE),
)
_MODEL_DEFECT_FAILURE_MODES = frozenset({
    "model_output_unparseable",
})
# Limit on how much of the engine's message is copied into `detail`; the
# chat-parser message echoes the unparsed tail of the output, which can be
# tens of kilobytes.
_ENGINE_MESSAGE_DETAIL_CHARS = 240

_INFRA_FAILURE_MODES = frozenset({
    "http_error",
    "server_error",
    # #157: a streamed request stopped producing events mid-stream. The
    # endpoint's fault, not the model's — unlike `timeout`, which with
    # streaming means the model was still producing tokens when the budget ran
    # out (a runaway).
    "stall",
    "agent_runner_crashed",
    "model_endpoint_unreachable",
    "result_json_malformed",
})
# #148: the taxonomy itself now lives in types.py so the summary/JSON rollup
# shares it; this alias keeps the runner's historical name.
_RUNAWAY_FAILURE_MODES = RUNAWAY_FAILURE_MODES

_REASONING_HISTORY_FIELDS = frozenset({
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "codex_reasoning_items",
})


def strip_reasoning_history(messages: list[dict]) -> list[dict]:
    """Return an outgoing-history copy without prior assistant reasoning fields.

    Captured messages remain untouched. This intentionally operates only on the
    request copy so saved raw responses, assistant_messages, and conversation
    forensics retain the model's complete output.
    """
    sanitized: list[dict] = []
    for message in messages:
        outgoing = dict(message)
        if outgoing.get("role") == "assistant":
            for field in _REASONING_HISTORY_FIELDS:
                outgoing.pop(field, None)
        sanitized.append(outgoing)
    return sanitized


def pack_default_thinking(meta: dict) -> bool:
    """Return the pack-declared default thinking mode. Missing means off."""
    value = meta.get("default_thinking", "off")
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"on", "true", "1", "yes"}


def resolve_thinking_enabled(meta: dict, override: bool | None) -> bool:
    """Resolve per-pack thinking: None = pack default; bool = force all."""
    return pack_default_thinking(meta) if override is None else bool(override)


def thinking_mode_from_override(override: bool | None) -> str:
    if override is True:
        return "force-on"
    if override is False:
        return "force-off"
    return "pack-defaults"


THINKING_CONTROL_ENABLE = "enable_thinking"
THINKING_CONTROL_EFFORT = "reasoning_effort"
THINKING_CONTROL_NONE = "none"


def thinking_control_from_template(template: str) -> str:
    """Resolve the model's reasoning switch from its live chat template.

    The order is compatibility-sensitive: templates that mention both controls
    already work with enable_thinking, so keep their existing request shape.
    """
    if "enable_thinking" in template:
        return THINKING_CONTROL_ENABLE
    if "reasoning_effort" in template:
        return THINKING_CONTROL_EFFORT
    return THINKING_CONTROL_NONE


def _effort_is_enabled(value: object, fallback: bool) -> bool:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) > 0
    return str(value).strip().lower() not in {"", "0", "0.0", "none", "off", "false"}


def _apply_thinking_control(
    sampling: dict,
    control: str,
    enabled: bool,
    reasoning_effort: str | float,
) -> None:
    """Apply one resolved reasoning control while preserving unrelated kwargs."""
    kwargs = dict(sampling.get("chat_template_kwargs") or {})
    kwargs.pop(THINKING_CONTROL_ENABLE, None)
    kwargs.pop(THINKING_CONTROL_EFFORT, None)

    if control == THINKING_CONTROL_EFFORT:
        value: str | float = reasoning_effort if enabled else "none"
        kwargs[THINKING_CONTROL_EFFORT] = value
        # OpenAI-standard location. Keep the kwargs copy because llama.cpp does
        # not yet forward this field into templates that read reasoning_effort.
        sampling[THINKING_CONTROL_EFFORT] = value
    elif control == THINKING_CONTROL_ENABLE:
        kwargs[THINKING_CONTROL_ENABLE] = enabled

    sampling["chat_template_kwargs"] = kwargs


def _request_thinking_enabled(sampling: dict, control: str, fallback: bool) -> bool:
    kwargs = dict(sampling.get("chat_template_kwargs") or {})
    if control == THINKING_CONTROL_EFFORT:
        value = sampling.get(THINKING_CONTROL_EFFORT, kwargs.get(THINKING_CONTROL_EFFORT))
        return _effort_is_enabled(value, fallback)
    if control == THINKING_CONTROL_ENABLE:
        return bool(kwargs.get(THINKING_CONTROL_ENABLE, fallback))
    return fallback


def _thinking_extra_body(sampling: dict, control: str) -> dict:
    """Return only reasoning fields for a sandbox-owned model client."""
    kwargs = dict(sampling.get("chat_template_kwargs") or {})
    body: dict = {"chat_template_kwargs": kwargs}
    if control == THINKING_CONTROL_EFFORT and THINKING_CONTROL_EFFORT in sampling:
        body[THINKING_CONTROL_EFFORT] = sampling[THINKING_CONTROL_EFFORT]
    return body


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _duration_between(started_at: str | None, finished_at: str | None) -> float | None:
    """#146: wall-clock seconds between two ISO-8601 stamps (`Z` or offset).

    None when either stamp is missing or unparseable, so a hand-built or
    pre-#146 result simply carries no duration rather than a wrong one.
    """
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
        return max(0.0, (end - start).total_seconds())
    except (TypeError, ValueError):
        return None


def _pack_path(pack_id: str):
    return resources.files("benchlocal_cli").joinpath("packs", f"{pack_id}.jsonl")


def load_pack(pack_id: str) -> tuple[dict, list[dict]]:
    path = _pack_path(pack_id)
    if not path.is_file():
        raise FileNotFoundError(f"unknown pack: {pack_id}")
    meta: dict | None = None
    scenarios: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if line_no == 1 and record.get("__meta__") is True:
                meta = record
            else:
                record.setdefault("pack_id", pack_id)
                scenarios.append(record)
    if meta is None:
        raise ValueError(f"{pack_id} missing metadata line")
    return meta, scenarios


def list_packs() -> list[dict]:
    packs: list[dict] = []
    pack_dir = resources.files("benchlocal_cli").joinpath("packs")
    for item in sorted(pack_dir.iterdir(), key=lambda p: p.name):
        if item.name.endswith(".jsonl"):
            meta, _ = load_pack(item.name.removesuffix(".jsonl"))
            packs.append(meta)
    return packs


# Sampling params that are stripped from the request when
# --sampling-from-server is active, so the server applies its own defaults.
_SAMPLING_KEYS = frozenset({
    "temperature", "top_p", "top_k", "min_p", "repeat_penalty",
    "presence_penalty", "frequency_penalty", "dynatemp_range",
    "dynatemp_exponent", "typical_p", "seed", "mirostat",
    "mirostat_tau", "mirostat_eta",
})

DEFAULT_THINKING_SAMPLER = {
    "temperature": 1.0,
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
}

# #145: --budget-from-timeout derives each request's token ceiling from the
# clock that actually governs it, `floor(headroom x clock x measured_tps)`,
# instead of holding the ceiling fixed and letting a capped clock silently
# become the tighter limit. The headroom covers what the clock pays for besides
# decode: prefill, verification, turn overhead, and the probe's empty-context
# optimism.
DEFAULT_BUDGET_HEADROOM = 0.8
# Per-request reasoning-budget keys honoured by llama.cpp-family servers. Both
# spellings are sent because trees differ: `tools/server/server-common.cpp`
# reads `thinking_budget_tokens` from the body (only when the server was not
# booted with --reasoning-budget), newer trees read `reasoning_budget_tokens`
# with the other as fallback. vLLM / SGLang / cloud endpoints drop both
# silently — which is why the knob is only claimed for llama.cpp and why the
# startup control below exists.
_REASONING_BUDGET_KEYS: tuple[str, ...] = ("thinking_budget_tokens", "reasoning_budget_tokens")
# The startup positive control: a 128-token reasoning budget on a prompt that
# invites a long trace must not come back as a multi-thousand-token completion.
_BUDGET_CONTROL_TOKENS = 128
_BUDGET_CONTROL_MAX_TOKENS = 2048
_BUDGET_CONTROL_FAIL_ABOVE = 1024
_BUDGET_CONTROL_PROMPT = (
    "Think this through carefully and at length before answering, considering "
    "several approaches: a train leaves at 09:40 and travels 217 km at 68 km/h, "
    "then waits 25 minutes and returns at 51 km/h. At what time is it back?"
)
# Packs whose agent makes its own model calls inside the container. A
# per-request budget derived from the runner's per-case clock has no meaning
# for an open-ended episode there (same reasoning as #149's clock floor).
_AGENT_OWNED_PACKS = frozenset({"hermesagent-20", "aider-polyglot-30"})


def _thinking_sampler_for(meta: dict, override: dict | None) -> dict:
    if override is not None:
        return dict(override)
    pack_sampler = meta.get("thinking_sampler")
    if isinstance(pack_sampler, dict):
        return dict(pack_sampler)
    return dict(DEFAULT_THINKING_SAMPLER)


def _reference_date_context(scenario: dict) -> str | None:
    date = scenario.get("benchmark_reference_date")
    day = scenario.get("benchmark_reference_day")
    if not date and not day:
        return None
    if date and day:
        return f"Benchmark reference date: {date} ({day})."
    if date:
        return f"Benchmark reference date: {date}."
    return f"Benchmark reference day: {day}."


def messages_with_reference_context(scenario: dict) -> list[dict]:
    """Return scenario messages with benchmark reference-date context injected."""
    messages = [dict(message) for message in scenario["messages"]]
    context = _reference_date_context(scenario)
    if not context:
        return messages
    for message in messages:
        if message.get("role") == "system":
            content = str(message.get("content") or "")
            if context not in content:
                message["content"] = f"{content.rstrip()}\n\n{context}" if content else context
            return messages
    return [{"role": "system", "content": context}, *messages]


def build_request(
    scenario: dict,
    meta: dict,
    model: str,
    *,
    thinking_enabled: bool | None = None,
    thinking_max_tokens: int = 16384,
    extra_body: dict | None = None,
    sampling_overrides: dict | None = None,
    sampling_from_server: bool = False,
    thinking_sampler: dict | None = None,
    thinking_control: str = THINKING_CONTROL_ENABLE,
    reasoning_effort: str | float = "high",
) -> tuple[dict, dict]:
    sampling = dict(meta.get("sampling_defaults", {}))
    scenario_overrides = scenario.get("sampling_overrides") or {}
    if extra_body:
        sampling.update(extra_body)
    resolved_thinking = resolve_thinking_enabled(meta, thinking_enabled)
    _apply_thinking_control(
        sampling,
        thinking_control,
        resolved_thinking,
        reasoning_effort,
    )
    sampling.update(scenario_overrides)
    request_thinking = _request_thinking_enabled(
        sampling,
        thinking_control,
        resolved_thinking,
    )
    if request_thinking and not sampling_from_server:
        sampling.update(_thinking_sampler_for(meta, thinking_sampler))
    if sampling_overrides:
        sampling.update(sampling_overrides)
    if request_thinking:
        sampling["max_tokens"] = thinking_max_tokens
    # --sampling-from-server (#21): strip all sampling params from the
    # request so the server applies its own configured defaults. Keep
    # max_tokens (length budget) and chat_template_kwargs (thinking gate).
    if sampling_from_server:
        sampling = {
            k: v for k, v in sampling.items()
            if k not in _SAMPLING_KEYS
        }
    request = {"model": model, "messages": messages_with_reference_context(scenario), **sampling}
    if scenario.get("tools"):
        request["tools"] = scenario["tools"]
    return request, sampling


def _apply_cli_thinking_controls(
    scenario: dict,
    request: dict,
    sampling: dict,
    thinking_max_tokens: int,
    thinking_control: str,
    preserve_native_controls: bool = False,
) -> None:
    """Add provider-native reasoning controls for CLI-40 model requests."""
    if scenario.get("pack_id") != "cli-40":
        return

    request_thinking = _request_thinking_enabled(sampling, thinking_control, False)
    if preserve_native_controls:
        for key in ("enable_thinking", "thinking_budget"):
            if key in sampling:
                request[key] = sampling[key]
        return
    if thinking_control != THINKING_CONTROL_ENABLE or not request_thinking:
        # Effort-controlled model: the resolved effort control already owns
        # this request. Answer-only arm (#129): do NOT send the top-level
        # Qwen pair (enable_thinking=true + thinking_budget=1) — it
        # contradicts the harness-level chat_template_kwargs.enable_thinking=
        # false, and budget-honoring endpoints then run the model thinking-ON
        # truncated to one token: reasoning leaks into content and the
        # no-thinking arm is scored on contaminated output. Defer to
        # chat_template_kwargs, which the other packs use cleanly for no-think.
        sampling.pop("enable_thinking", None)
        sampling.pop("thinking_budget", None)
        request.pop("enable_thinking", None)
        request.pop("thinking_budget", None)
        return
    # Thinking is enabled: preserve CLI-40's compatibility-sensitive Qwen
    # shape (top-level enable_thinking + thinking_budget).
    sampling.setdefault("enable_thinking", True)
    sampling.setdefault("thinking_budget", max(1, int(thinking_max_tokens)))
    request["enable_thinking"] = sampling["enable_thinking"]
    request["thinking_budget"] = sampling["thinking_budget"]


def build_chat_request(
    messages: list[dict],
    sampling: dict,
    model: str,
    *,
    tools: list[dict] | None = None,
) -> dict:
    request = {"model": model, "messages": messages, **sampling}
    if tools:
        request["tools"] = tools
    return request


def _negative_control_response(text: str) -> dict:
    """#62 tier-1: a synthetic OpenAI completion carrying deliberate junk content,
    fed to every scenario in negative-control mode. Shaped like a real response
    (choices[0].message.content + finish_reason) so it flows through the normal
    scoring path; any verifier that PASSes this is under-checking."""
    return {
        "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# #147: the `usage` block of one completion, as the four counts a run reports.
_USAGE_FIELDS = ("tokens_completion", "tokens_prompt", "tokens_total", "tokens_reasoning")


def _usage_counts(raw_response: dict | None) -> dict[str, int | None]:
    """Read `usage` from one response: completion / prompt / total, plus
    `completion_tokens_details.reasoning_tokens` where the endpoint splits
    reasoning out (OpenAI-style cloud endpoints; llama.cpp reasons inline so
    it reports none). Missing or non-integer values are None, never 0."""
    usage = raw_response.get("usage") if isinstance(raw_response, dict) else None
    if not isinstance(usage, dict):
        return dict.fromkeys(_USAGE_FIELDS)

    def _int(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    details = usage.get("completion_tokens_details")
    reasoning = _int(details.get("reasoning_tokens")) if isinstance(details, dict) else None
    return {
        "tokens_completion": _int(usage.get("completion_tokens")),
        "tokens_prompt": _int(usage.get("prompt_tokens")),
        "tokens_total": _int(usage.get("total_tokens")),
        "tokens_reasoning": reasoning,
    }


def _add_usage(total: dict[str, int | None], raw_response: dict | None) -> None:
    """Accumulate one turn's counts into a multi-turn total (None + n = n)."""
    for key, value in _usage_counts(raw_response).items():
        if value is not None:
            total[key] = (total.get(key) or 0) + value


def _endpoint_base(endpoint: str) -> str:
    """Strip supported OpenAI endpoint suffixes to the serving base URL."""
    base = endpoint.rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions", "/v1"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _chat_url(endpoint: str) -> str:
    """Normalize an endpoint URL to the OpenAI chat-completions path.

    Accepts any of these and returns the same final URL:
        http://host:port
        http://host:port/
        http://host:port/v1
        http://host:port/v1/
        http://host:port/v1/chat/completions
    """
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/v1/chat/completions"):
        return endpoint
    if endpoint.endswith("/v1"):
        return f"{endpoint}/chat/completions"
    return f"{endpoint}/v1/chat/completions"


class _TransientPostFailure(Exception):
    def __init__(self, failure_mode: str, detail: str, trace: dict) -> None:
        super().__init__(detail)
        self.failure_mode = failure_mode
        self.detail = detail
        self.trace = trace


def _transient_trace(errors: list[str], attempt: int) -> dict | None:
    if not errors:
        return None
    return {
        "transient_retries": max(0, attempt - 1),
        "transient_errors": list(errors),
    }


def _merge_transient_trace(existing: dict | None, new: dict | None) -> dict | None:
    if not new:
        return existing
    if not existing:
        return dict(new)
    merged: dict = {}
    if any(key in trace for trace in (existing, new) for key in ("transient_retries", "transient_errors")):
        merged["transient_retries"] = int(existing.get("transient_retries") or 0) + int(
            new.get("transient_retries") or 0
        )
        merged["transient_errors"] = list(existing.get("transient_errors") or []) + list(
            new.get("transient_errors") or []
        )
    stream = _merge_stream_trace(existing.get("stream"), new.get("stream"))
    if stream is not None:
        merged["stream"] = stream
    return merged


def _stream_trace(stats: dict) -> dict:
    """#157: one streamed request's liveness stats, in the list form that merges."""
    return {
        "requests": 1,
        "first_chunk_s": [stats.get("first_chunk_s")],
        "max_gap_s": [stats.get("max_gap_s")],
    }


def _merge_stream_trace(a: dict | None, b: dict | None) -> dict | None:
    if not a:
        return dict(b) if b else None
    if not b:
        return dict(a)
    return {
        "requests": int(a.get("requests") or 0) + int(b.get("requests") or 0),
        "first_chunk_s": list(a.get("first_chunk_s") or []) + list(b.get("first_chunk_s") or []),
        "max_gap_s": list(a.get("max_gap_s") or []) + list(b.get("max_gap_s") or []),
    }


def _with_stream(trace: dict | None, stream: dict | None) -> dict | None:
    if stream is None:
        return trace
    out = dict(trace or {})
    out["stream"] = stream
    return out


def _engine_error_message(raw_response: dict | None) -> str:
    """Best-effort extraction of the engine's error text from a non-2xx body.

    Accepts the OpenAI/llama.cpp shape `{"error": {"message": ...}}`, a bare
    `{"error": "..."}`, and the `{"text": ...}` fallback `_post_chat` builds for
    a non-JSON body. Returns "" when nothing textual is found.
    """
    if not isinstance(raw_response, dict):
        return ""
    error = raw_response.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    if isinstance(error, str):
        return error
    for key in ("message", "detail", "text"):
        value = raw_response.get(key)
        if isinstance(value, str):
            return value
    return ""


def is_model_output_rejection(raw_response: dict | None) -> bool:
    """#152: does this 5xx body say the engine refused to parse the model's output?"""
    message = _engine_error_message(raw_response)
    return bool(message) and any(
        pattern.search(message) for pattern in _MODEL_OUTPUT_REJECTION_PATTERNS
    )


def classify_server_error(status_code: int, raw_response: dict | None) -> tuple[str, str]:
    """#152: split a 5xx into (failure_mode, detail).

    `server_error` keeps its historical `HTTP <code>` detail (transient infra,
    retried). `model_output_unparseable` carries the engine's message, trimmed,
    so the parse position — the fingerprint of the generation — survives into
    the saved JSON and the failure breakdown.
    """
    if not is_model_output_rejection(raw_response):
        return "server_error", f"HTTP {status_code}"
    message = " ".join(_engine_error_message(raw_response).split())
    if len(message) > _ENGINE_MESSAGE_DETAIL_CHARS:
        message = message[: _ENGINE_MESSAGE_DETAIL_CHARS - 1] + "…"
    return "model_output_unparseable", f"HTTP {status_code}: engine rejected model output — {message}"


def _latency(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "mean": None}
    sorted_values = sorted(values)
    p95_index = min(len(sorted_values) - 1, max(0, round(0.95 * (len(sorted_values) - 1))))
    return {
        "p50": statistics.median(sorted_values),
        "p95": sorted_values[p95_index],
        "mean": statistics.mean(sorted_values),
    }


def _repeat_variance(runs: list[ScenarioRun], repeat: int) -> dict[str, float | int | None] | None:
    """Per-pack pass-rate variance across repeats.

    For repeat=1 this stays None to keep default output compact. For repeat>1,
    compute one pass-rate per repeat arm and report population stddev + CV.
    """
    if repeat <= 1:
        return None
    rates: list[float] = []
    for idx in range(1, repeat + 1):
        counted = [
            run for run in runs
            if run.repeat_index == idx and run.result.failure_mode != "verifier_not_implemented"
        ]
        if not counted:
            continue
        rates.append(sum(1 for run in counted if run.result.passed) / len(counted))
    if not rates:
        return {"repeat": repeat, "mean": None, "std": None, "cv": None}
    mean = statistics.mean(rates)
    std = statistics.pstdev(rates) if len(rates) > 1 else 0.0
    cv = (std / mean) if mean else None
    return {"repeat": repeat, "mean": mean, "std": std, "cv": cv}


def _pass_at_k_summary(
    runs: list[ScenarioRun], configured_k: int
) -> dict[str, float | int] | None:
    """Aggregate nested inline retries without changing strict pass@1 fields."""
    counted = [
        run for run in runs
        if run.result.failure_mode != "verifier_not_implemented"
    ]
    if not counted:
        return None
    max_attempts = max((run.attempt_count for run in counted), default=1)
    if configured_k <= 1 and any(run.retry_eligible for run in counted):
        # Infra failures retain the standard three-attempt ceiling even when
        # model-verdict retries are disabled.
        configured_k = DEFAULT_INLINE_RETRY_ATTEMPTS
    k = max(int(configured_k), max_attempts)
    if k <= 1:
        return None
    passed = sum(
        1
        for run in counted
        if (run.result.passed if run.pass_at_k is None else bool(run.pass_at_k))
    )
    flaky = [
        run for run in counted
        if (run.label or "").startswith("pass@") and run.label != "pass@1"
    ]
    credited_flaky = sum(1 for run in flaky if bool(run.pass_at_k))
    safety_flaky = sum(1 for run in flaky if not run.best_of_n_eligible)
    total = len(counted)
    return {
        "k": k,
        "passed": passed,
        "total": total,
        "score": passed / total if total else 0.0,
        "credited_flaky": credited_flaky,
        "safety_flaky": safety_flaky,
        "systematic": sum(
            1 for run in counted if not run.result.passed and run.label == "fail"
        ),
        "retried_scenarios": sum(1 for run in counted if run.attempt_count > 1),
        "retry_attempts": sum(max(0, run.attempt_count - 1) for run in counted),
    }


def _pack_tokens(runs: list) -> dict | None:
    """#147: token usage for a pack, from fields that are already persisted.

    `completion` sums attempt-1 `tokens_completion` over the rows the score
    counts (`verifier_not_implemented` excluded), so it lines up with
    `passed / total`; `retries` sums the nested inline-retry attempts, which
    cost tokens too but are not attempt 1. `counted` / `missing` say how many
    scored rows carried a count — a pack whose agent calls the model from
    inside its container (hermesagent-20) reports none, and that gap is
    reported rather than silently read as zero. `prompt`, `total_tokens` and
    `reasoning` (tier 2) are present only when at least one row has them.
    Accepts ScenarioRun objects or their saved-JSON dicts. None when the
    pack has no counted rows.
    """

    def _field(run: object, key: str) -> object:
        if isinstance(run, dict):
            result = run.get("result")
            if isinstance(result, dict) and key in result:
                return result.get(key)
            return run.get(key)
        result = getattr(run, "result", None)
        return getattr(result, key, None) if result is not None else getattr(run, key, None)

    def _int(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    completion = 0
    counted = 0
    total = 0
    retries = 0
    tier2: dict[str, int | None] = {"prompt": None, "total_tokens": None, "reasoning": None}
    for run in runs:
        if _field(run, "failure_mode") == "verifier_not_implemented":
            continue
        total += 1
        value = _int(_field(run, "tokens_completion"))
        if value is not None:
            completion += value
            counted += 1
        for key, field in (("prompt", "tokens_prompt"), ("total_tokens", "tokens_total"), ("reasoning", "tokens_reasoning")):
            extra = _int(_field(run, field))
            if extra is not None:
                tier2[key] = (tier2[key] or 0) + extra
        attempts = run.get("retry_attempts") if isinstance(run, dict) else getattr(run, "retry_attempts", None)
        for attempt in attempts or []:
            if isinstance(attempt, dict):
                retry_value = _int(attempt.get("tokens_completion"))
                if retry_value is None and isinstance(attempt.get("result"), dict):
                    retry_value = _int(attempt["result"].get("tokens_completion"))
                if retry_value is not None:
                    retries += retry_value
    if not total:
        return None
    summary: dict = {
        "completion": completion,
        "retries": retries,
        "counted": counted,
        "missing": total - counted,
        "total": total,
    }
    for key, value in tier2.items():
        if value is not None:
            summary[key] = value
    return summary


def _combine_tokens(summaries) -> dict | None:
    """#147: sum per-pack token rollups into the run-level one."""
    present = [summary for summary in summaries if isinstance(summary, dict)]
    if not present:
        return None
    combined: dict = {
        key: sum(int(summary.get(key) or 0) for summary in present)
        for key in ("completion", "retries", "counted", "missing", "total")
    }
    for key in ("prompt", "total_tokens", "reasoning"):
        values = [summary[key] for summary in present if isinstance(summary.get(key), int)]
        if values:
            combined[key] = sum(values)
    # `total` is the row count; the endpoint's summed usage.total_tokens is
    # `total_tokens`, so the two cannot be confused.
    return combined


def _combine_pass_at_k(packs: list[PackResult]) -> dict[str, float | int] | None:
    summaries = [pack.pass_at_k for pack in packs if pack.pass_at_k is not None]
    if not summaries:
        return None
    passed = sum(int(summary["passed"]) for summary in summaries)
    total = sum(int(summary["total"]) for summary in summaries)
    return {
        "k": max(int(summary["k"]) for summary in summaries),
        "passed": passed,
        "total": total,
        "score": passed / total if total else 0.0,
        "credited_flaky": sum(int(summary.get("credited_flaky", 0)) for summary in summaries),
        "safety_flaky": sum(int(summary.get("safety_flaky", 0)) for summary in summaries),
        "systematic": sum(int(summary.get("systematic", 0)) for summary in summaries),
        "retried_scenarios": sum(int(summary.get("retried_scenarios", 0)) for summary in summaries),
        "retry_attempts": sum(int(summary.get("retry_attempts", 0)) for summary in summaries),
    }


class Runner:
    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        timeout_per_case: float | None = None,
        timeout_ceiling_s: float | None = None,
        model_turn_timeout: float | None = 300.0,
        measured_tps: float | None = None,
        reference_tps: float | None = None,
        timeout_scale_down: bool = False,
        budget_from_timeout: bool = False,
        budget_headroom: float = DEFAULT_BUDGET_HEADROOM,
        budget_control: bool = True,
        enable_sandboxed_packs: bool = False,
        mock_responses: dict[str, dict] | None = None,
        negative_control: str | None = None,
        thinking_enabled: bool | None = None,
        thinking_max_tokens: int = 16384,
        reasoning_effort: str | float | None = None,
        probe_thinking_control: bool = False,
        extra_body: dict | None = None,
        api_key: str | None = None,
        max_total_tokens: int | None = None,
        request_delay: float = 0.0,
        sandbox_image_tag: str = "latest",
        sandbox_log_dir: str | None = None,
        max_transient_retries: int = 3,
        retry_on_timeout: bool = False,
        stream: bool = False,
        stream_stall_timeout: float = DEFAULT_STREAM_STALL_TIMEOUT_S,
        preserve_reasoning_history: bool = False,
        retry_failures: int = DEFAULT_INLINE_RETRY_ATTEMPTS,
        retry_runaways: bool = False,
        inline_retries_enabled: bool = True,
        sampling_overrides: dict | None = None,
        sampling_from_server: bool = False,
        thinking_sampler: dict | None = None,
        on_pack_complete: Callable[[PackResult], None] | None = None,
        on_scenario_complete: Callable[[ScenarioRun, int, int], None] | None = None,
        on_progress_event: Callable[[dict], None] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.timeout_per_case = None if timeout_per_case is None else float(timeout_per_case)
        if timeout_ceiling_s is not None and float(timeout_ceiling_s) < 0:
            raise ValueError("timeout_ceiling_s must be non-negative")
        self.timeout_ceiling_s = (
            None if timeout_ceiling_s is None else float(timeout_ceiling_s)
        )
        if model_turn_timeout is not None and float(model_turn_timeout) < 0:
            raise ValueError("model_turn_timeout must be non-negative")
        self.model_turn_timeout = (
            None
            if model_turn_timeout is None or float(model_turn_timeout) == 0
            else float(model_turn_timeout)
        )
        # #149: remembered so the sandbox clock line can warn when this knob was
        # set on a pack it cannot reach (hermes/aider make their own model calls).
        self._model_turn_timeout_customised = (
            model_turn_timeout is None
            or float(model_turn_timeout) != DEFAULT_MODEL_TURN_TIMEOUT_S
        )
        self.measured_tps_override = None if measured_tps is None else float(measured_tps)
        self.reference_tps_override = None if reference_tps is None else float(reference_tps)
        self.timeout_scale_down = bool(timeout_scale_down)
        self._measured_decode_tps: float | None = self.measured_tps_override
        # #145: derive the token ceiling from the clock instead of the reverse.
        self.budget_from_timeout = bool(budget_from_timeout)
        self.budget_headroom = float(budget_headroom)
        if not 0.0 < self.budget_headroom <= 1.0:
            raise ValueError("budget_headroom must be in (0, 1]")
        self.budget_control = bool(budget_control)
        self._engine_family: str | None = None
        self._token_budget_report: dict | None = None
        self._timeout_scaling_note: str | None = None
        self._timeout_scaling_note_emitted = False
        self.enable_sandboxed_packs = enable_sandboxed_packs
        self.mock_responses = mock_responses or {}
        # #62 tier-1: when set, every scenario is served this junk text instead of
        # calling the endpoint — a negative control for verifier false-positives.
        self.negative_control = negative_control
        self.thinking_override = thinking_enabled
        self.thinking_enabled = bool(thinking_enabled)
        self.thinking_mode = thinking_mode_from_override(thinking_enabled)
        self.thinking_max_tokens = thinking_max_tokens
        self.reasoning_effort: str | float = (
            reasoning_effort if reasoning_effort is not None else "high"
        )
        self.probe_thinking_control = bool(probe_thinking_control)
        # An explicit effort value selects the standard effort control even when
        # the endpoint cannot expose /props. Otherwise preserve the historical
        # Qwen request shape until template detection or an opt-in probe proves
        # a different control.
        self.thinking_control = (
            THINKING_CONTROL_EFFORT
            if reasoning_effort is not None
            else THINKING_CONTROL_ENABLE
        )
        self.thinking_control_source = (
            "explicit --reasoning-effort"
            if reasoning_effort is not None
            else "compatibility default"
        )
        self._thinking_control_resolved = reasoning_effort is not None
        self._thinking_control_announced = False
        self.extra_body = extra_body or {}
        self.api_key = api_key
        # Bearer auth for cloud OpenAI-compatible endpoints (OpenRouter, DashScope, …);
        # empty for local vLLM / llama.cpp, which need no auth. Applied to every
        # model call + the reachability/props probes.
        self._request_headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # Cumulative token spend guard (cloud-cost safety). None = unlimited.
        self.max_total_tokens = max_total_tokens
        self.tokens_used = 0
        # Proactive pacing (cloud throttle avoidance): min seconds between model
        # requests. 0 = no pacing (local / generous providers). Complements the
        # 429 retry — pace to avoid, retry to recover.
        self.request_delay = float(request_delay or 0.0)
        self._last_request_monotonic = 0.0
        self.sandbox_image_tag = sandbox_image_tag
        # If set, sandbox container stderr/stdout is captured to
        # `<sandbox_log_dir>/sandbox-<pack_id>.log` before container teardown.
        # See SandboxClient.stop(log_dir=...) for the snapshot.
        self.sandbox_log_dir = sandbox_log_dir
        self.max_transient_retries = max(0, int(max_transient_retries))
        # A timeout means the per-request budget was genuinely exceeded; retrying
        # just burns another full budget for the same outcome (#58). Default to
        # failing fast on the first timeout. Connection errors / HTTP 5xx are
        # genuinely transient and keep retrying regardless of this flag.
        self.retry_on_timeout = bool(retry_on_timeout)
        # #157: stream model calls so a stalled endpoint is caught by the gap
        # since the last event, not by the total budget. Opt-in: off, the
        # transport and every saved byte are unchanged.
        self.stream = bool(stream)
        if float(stream_stall_timeout) <= 0:
            raise ValueError("stream_stall_timeout must be positive")
        self.stream_stall_timeout = float(stream_stall_timeout)
        # Reasoning is output-only for the default OpenAI-compatible/Qwen/R1
        # path. Providers whose tool loops require signed/encrypted reasoning
        # continuity can opt back into replay explicitly.
        self.preserve_reasoning_history = bool(preserve_reasoning_history)
        if int(retry_failures) < 0:
            raise ValueError("retry_failures must be non-negative")
        self.retry_failures = int(retry_failures)
        self.retry_runaways = bool(retry_runaways)
        self.inline_retries_enabled = bool(inline_retries_enabled)
        # CLI-level sampling overrides (--temperature, --top-p, etc.).
        # When set, the run is tagged as non-canonical in the output.
        self.sampling_overrides = sampling_overrides or {}
        # --sampling-from-server (#21): omit sampling params from requests
        # so the server applies its own configured defaults. Mutually
        # exclusive with sampling_overrides (enforced in cli.py).
        self.sampling_from_server = sampling_from_server
        self.thinking_sampler = None if thinking_sampler is None else dict(thinking_sampler)
        # Populated by _read_server_defaults() before the run starts.
        self._server_defaults: dict | None = None
        self._sandbox_clients: dict[str, SandboxClient] = {}
        # Callbacks for incremental progress (#23)
        self._on_pack_complete = on_pack_complete
        self._on_scenario_complete = on_scenario_complete
        self._on_progress_event = on_progress_event
        self.aider_progress_poll_s = self._aider_progress_poll_interval()

    @staticmethod
    def _aider_progress_poll_interval() -> float:
        try:
            value = float(os.environ.get("BENCHLOCAL_AIDER_PROGRESS_POLL_S", "5"))
        except (TypeError, ValueError):
            return 5.0
        return max(0.25, value)

    def run(
        self,
        pack_ids: list[str],
        *,
        mode: str = "custom",
        repeat: int = 1,
        selection: dict[str, list[str]] | None = None,
        selection_ids: list[str] | None = None,
        completed_repeats: dict[str, dict[str, set[int]]] | None = None,
        started_at: str | None = None,
    ) -> RunResult:
        started_at = started_at or _utc_now()
        warnings: list[str] = []
        old_sigint = signal.getsignal(signal.SIGINT)
        old_sigterm = signal.getsignal(signal.SIGTERM)

        def _cleanup_and_raise(signum, frame):  # type: ignore[no-untyped-def]
            self._stop_sandboxes()
            previous = old_sigint if signum == signal.SIGINT else old_sigterm
            if callable(previous):
                previous(signum, frame)
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, _cleanup_and_raise)
        signal.signal(signal.SIGTERM, _cleanup_and_raise)
        try:
            # Resolve the request-level reasoning control before any pack or
            # sandbox starts. /props is metadata-only; inference probing remains
            # opt-in because it consumes real endpoint responses.
            self._resolve_thinking_control(warnings)
            # --sampling-from-server (#21): read server defaults before
            # any requests so we can tag the run and record what was used.
            if self.sampling_from_server:
                self._server_defaults = self._read_server_defaults(warnings)
            # #145: resolve the derived token budget (TPS, engine knob, the
            # positive control) before any pack runs, so a budget that cannot
            # be applied fails here and not three hours in.
            self._prepare_token_budget(pack_ids, warnings)
            self._start_sandboxes(pack_ids, warnings)
            pack_results: list[PackResult] = []
            for pack_id in pack_ids:
                pack_started = time.perf_counter()
                pack_result = self.run_pack(
                    pack_id,
                    repeat=repeat,
                    warnings=warnings,
                    scenario_ids=selection.get(pack_id) if selection is not None else None,
                    completed_repeats=(completed_repeats or {}).get(pack_id),
                )
                # #146: wall clock around the whole pack — inline retries,
                # verification and inter-scenario overhead included, none of
                # which the per-scenario latency percentiles see.
                pack_result.duration_s = time.perf_counter() - pack_started
                pack_results.append(pack_result)
                if self._on_pack_complete is not None:
                    self._on_pack_complete(pack_result)
            total = sum(pack.total for pack in pack_results)
            passed = sum(pack.passed for pack in pack_results)
            finished_at = _utc_now()
            # Tag non-canonical sampling runs
            if self.sampling_overrides:
                override_desc = ", ".join(f"{k}={v}" for k, v in self.sampling_overrides.items())
                warnings.append(
                    f"non-canonical sampling overrides active ({override_desc}) — "
                    f"results are NOT comparable to the default temp=0 baseline"
                )
            if self._timeout_scaling_note:
                warnings.append(self._timeout_scaling_note)
            # #126: a reasoning arm that never thought, or a no-thinking arm
            # the server forced reasoning onto, produces plausible-but-invalid
            # numbers. Detect both from the responses already collected and
            # fail loudly. Synthetic traffic is skipped: its response shape is
            # not the model's.
            thinking_validity: dict | None = None
            if self.negative_control is None and not self.mock_responses:
                observations, validity_warnings = thinking_validity_for_packs(pack_results)
                warnings.extend(validity_warnings)
                thinking_validity = observations or None
            if self.sampling_from_server:
                if self._server_defaults:
                    sd_desc = ", ".join(f"{k}={v}" for k, v in self._server_defaults.items())
                    warnings.append(
                        f"sampling inherited from server ({sd_desc}) — "
                        f"results are NOT comparable to the default temp=0 baseline"
                    )
                else:
                    warnings.append(
                        "sampling inherited from server (value not exposed by endpoint) — "
                        "results are NOT comparable to the default temp=0 baseline"
                    )
            return RunResult(
                schema_version="1",
                runner_version=__version__,
                endpoint=self.endpoint,
                model=self.model,
                mode=mode,
                started_at=started_at,
                finished_at=finished_at,
                packs=pack_results,
                totals={"passed": passed, "total": total, "score": (passed / total if total else 0.0)},
                thinking_enabled=self.thinking_enabled,
                thinking_mode=self.thinking_mode,
                thinking_control=self.thinking_control,
                reasoning_effort=(
                    self.reasoning_effort
                    if self.thinking_control == THINKING_CONTROL_EFFORT
                    else None
                ),
                warnings=warnings,
                duration_s=_duration_between(started_at, finished_at),
                thinking_validity=thinking_validity,
                sampling_overrides=dict(self.sampling_overrides) if self.sampling_overrides else None,
                tokens=self._run_tokens(pack_results),
                sampling_source="server" if self.sampling_from_server else None,
                server_defaults=self._server_defaults if self.sampling_from_server else None,
                token_budget=self._token_budget_report,
                selection=selection_ids,
                pass_at_k=_combine_pass_at_k(pack_results),
                repeat=repeat,
                runaway=combine_runaway(pack.runaway for pack in pack_results),
            )
        finally:
            self._stop_sandboxes()
            signal.signal(signal.SIGINT, old_sigint)
            signal.signal(signal.SIGTERM, old_sigterm)

    def _run_tokens(self, pack_results: list[PackResult]) -> dict | None:
        """#147: the per-pack rollups summed, plus the spend guard's counter.

        `tokens_used` sums `usage.total_tokens` over EVERY request the runner
        made — probes, transport retries, inline retries, multi-turn turns —
        and until now surfaced only in the exception when --max-total-tokens
        tripped. On a successful run it is the closest thing to the bill.
        """
        combined = _combine_tokens(pack.tokens for pack in pack_results)
        if combined is None and not self.tokens_used:
            return None
        combined = combined or {"completion": 0, "retries": 0, "counted": 0, "missing": 0, "total": 0}
        combined["endpoint_reported_total"] = int(self.tokens_used)
        return combined

    def _props_chat_template(self) -> str | None:
        """Return llama.cpp's live chat template, or None when unavailable."""
        base = _endpoint_base(self.endpoint)
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{base}/props", headers=self._request_headers)
            if response.status_code != 200:
                return None
            template = response.json().get("chat_template")
            return template if isinstance(template, str) and template else None
        except (httpx.HTTPError, ValueError, TypeError):
            return None

    @staticmethod
    def _message_content(response: dict) -> str:
        if not isinstance(response, dict):
            return ""
        choices = response.get("choices") if isinstance(response, dict) else None
        if not (isinstance(choices, list) and choices and isinstance(choices[0], dict)):
            return ""
        message = choices[0].get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        return content.strip() if isinstance(content, str) else ""

    def _probe_thinking_control(self) -> str | None:
        """Probe known off-switches; None means the probe was inconclusive."""
        if not self._endpoint_reachable():
            return None
        saw_successful_response = False
        for control in (THINKING_CONTROL_ENABLE, THINKING_CONTROL_EFFORT):
            fields: dict = {}
            _apply_thinking_control(fields, control, False, self.reasoning_effort)
            request = {
                "model": self.model,
                "messages": [{"role": "user", "content": "Say OK."}],
                "max_tokens": 24,
                "temperature": 0.0,
                **fields,
            }
            try:
                status, response, _trace = self._post_chat(
                    request, 90.0, max_attempts=1
                )
            except (httpx.HTTPError, _TransientPostFailure, TypeError, ValueError):
                continue
            if 200 <= status < 300:
                saw_successful_response = True
                if self._message_content(response):
                    return control
        return THINKING_CONTROL_NONE if saw_successful_response else None

    def _announce_thinking_control(self, warnings: list[str]) -> None:
        if self._thinking_control_announced or self.thinking_control == THINKING_CONTROL_ENABLE:
            return
        self._thinking_control_announced = True
        print(
            "benchlocal-cli: thinking control "
            f"{self.thinking_control!r} ({self.thinking_control_source})",
            file=sys.stderr,
            flush=True,
        )
        if self.thinking_control == THINKING_CONTROL_NONE:
            warnings.append(
                "no request-level thinking control detected; thinking-on/off arms "
                "cannot be enforced for this model"
            )

    def _resolve_thinking_control(self, warnings: list[str]) -> None:
        if not self._thinking_control_resolved:
            # Mock/negative-control runs must not consume or wait on an endpoint.
            if self.mock_responses or self.negative_control is not None:
                self._thinking_control_resolved = True
            else:
                template = self._props_chat_template()
                if template is not None:
                    self.thinking_control = thinking_control_from_template(template)
                    self.thinking_control_source = "GET /props chat_template"
                    self._thinking_control_resolved = True
                elif self.probe_thinking_control:
                    probed = self._probe_thinking_control()
                    if probed is not None:
                        self.thinking_control = probed
                        self.thinking_control_source = "behavioral probe"
                    # Inconclusive means unreachable/failed: retain the safe
                    # compatibility default instead of misclassifying no switch.
                    self._thinking_control_resolved = True
                else:
                    self._thinking_control_resolved = True
        self._announce_thinking_control(warnings)

    def _read_server_defaults(self, warnings: list[str]) -> dict | None:
        """Query the server for its effective sampling defaults (#21).

        llama.cpp: GET /props → default_generation_settings.params
        vLLM: no clean endpoint; returns None (tagged as 'value not exposed').
        """
        import sys
        endpoint = self.endpoint.rstrip("/")
        # Normalise: strip /v1 or /v1/chat/completions to get the base URL
        base = endpoint
        for suffix in ("/v1/chat/completions", "/v1", "/chat/completions"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        props_url = f"{base}/props"
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(props_url, headers=self._request_headers)
            if resp.status_code == 200:
                body = resp.json()
                params = (body.get("default_generation_settings") or {}).get("params") or {}
                # Extract the sampling keys we care about
                result: dict = {}
                for key in ("temperature", "top_p", "top_k", "min_p", "repeat_penalty"):
                    if key in params:
                        result[key] = params[key]
                if result:
                    return result
                # /props exists but no recognised keys — unusual
                print("benchlocal-cli: warning — /props returned no recognised sampling keys", file=sys.stderr, flush=True)
                return None
            # 404 = not llama.cpp (likely vLLM or other engine)
            if resp.status_code == 404:
                return None
            print(f"benchlocal-cli: warning — /props returned HTTP {resp.status_code}", file=sys.stderr, flush=True)
            return None
        except Exception as exc:
            print(f"benchlocal-cli: warning — could not read server defaults: {exc}", file=sys.stderr, flush=True)
            return None

    def _start_sandboxes(self, pack_ids: list[str], warnings: list[str]) -> None:
        import sys

        if not self.enable_sandboxed_packs:
            return
        for pack_id in pack_ids:
            try:
                meta, _ = load_pack(pack_id)
            except Exception as exc:
                msg = f"could not inspect {pack_id} for sandbox use: {exc}"
                warnings.append(msg)
                print(f"⚠️  {msg}", file=sys.stderr, flush=True)
                continue
            if not meta.get("supports_sandboxed_only") or pack_id in self._sandbox_clients:
                continue
            try:
                # #3: pass the per-case budget through so the aider batch
                # (one /verify-start spanning all 30 exercises) honors a raised
                # --timeout-per-case on slow rigs instead of the default cap.
                client = SandboxClient(
                    config_for_pack(
                        pack_id,
                        self.sandbox_image_tag,
                        batch_timeout_s=self._timeout_budget_for_meta(meta),
                        # #149: only the EXPLICIT per-case value floors the
                        # hermes episode cap — see resolve_episode_cap for why
                        # the auto-scaled budget deliberately does not.
                        timeout_per_case=self.timeout_per_case,
                    ),
                    model_endpoint=self.endpoint,
                )
                # #6: when --sandbox-log-dir is set, give the sandbox a writable
                # host run-dir so per-unit artifacts persist live (survive --rm/
                # crash/timeout). No-op for packs without a run_output_dir.
                run_dir = (
                    os.path.join(self.sandbox_log_dir, f"{pack_id}-run")
                    if self.sandbox_log_dir else None
                )
                client.start(run_dir=run_dir)
                self._sandbox_clients[pack_id] = client
                self._announce_sandbox_clocks(pack_id, client.config, meta, warnings)
            except Exception as exc:
                msg = (
                    f"skipping {pack_id}: sandbox unavailable ({exc}). "
                    f"Hint: the sandboxed packs need pre-built Docker images (benchlocal-sandbox-*) "
                    f"that aren't auto-pulled — and the build tooling is NOT in the pip package, it "
                    f"lives in a benchlocal-cli checkout. Build them once: "
                    f"`git clone https://github.com/noonghunna/benchlocal-cli && "
                    f"bash benchlocal-cli/tools/build-sandboxes.sh`. "
                    f"Or run a deterministic-only subset (no Docker needed)."
                )
                warnings.append(msg)
                print(f"⚠️  {msg}", file=sys.stderr, flush=True)

    def _announce_sandbox_clocks(
        self, pack_id: str, config: SandboxConfig, meta: dict, warnings: list[str]
    ) -> None:
        """#149: print the clocks that actually govern a sandboxed pack.

        Three knobs exist and, for the agent-owned packs (hermes, aider), two
        are inert: `--timeout-per-case` bounds the runner's HTTP read and
        `--model-turn-timeout` bounds one runner-owned model call, but those
        agents make their own model calls inside the container, so only the
        in-container episode cap applies. A setting that is accepted and
        ignored is indistinguishable from one that took effect — so say which
        is which up front, and warn (persisted in the result) when an inert
        knob was set.
        """
        budget = self._timeout_budget_for_meta(meta)
        cap = getattr(config, "episode_cap", None)
        if cap is None:
            # Runner-owned model calls (cli-40, bugfind-15, code-reasoning):
            # the per-case budget bounds each call and the turn watchdog trims it.
            turn = (
                "off"
                if self.model_turn_timeout is None
                else f"{self.model_turn_timeout:.0f}s"
            )
            print(
                f"[runner] {pack_id} clocks: per-case {budget:.0f}s (runner-side HTTP), "
                f"model-turn {turn}, verify http {config.request_timeout_s:.0f}s",
                file=sys.stderr,
                flush=True,
            )
            return

        unit = "batch" if pack_id == "aider-polyglot-30" else "episode"
        explicit = self.timeout_per_case
        if cap.source == "env":
            origin = f"{cap.override_env}; default {cap.default_s:.0f}s"
            if explicit is not None:
                origin += f"; --timeout-per-case {explicit:.0f} not applied"
        elif cap.source == "budget":
            origin = (
                f"floor from --timeout-per-case {explicit:.0f}"
                if explicit is not None
                else f"floor from auto-scaled per-case budget {budget:.0f}s"
            )
            origin += f"; default {cap.default_s:.0f}s"
        else:
            origin = "default; raise with --timeout-per-case"
            if cap.override_env:
                origin += f" or {cap.override_env}"
            if explicit is None and budget > cap.seconds:
                origin += f"; auto-scaled per-case budget {budget:.0f}s does not apply"
        print(
            f"[runner] {pack_id} clocks: {unit} {cap.seconds:.0f}s ({origin}), "
            f"verify-start read {config.request_timeout_s:.0f}s, "
            f"model-turn n/a (agent makes its own model calls in-container)",
            file=sys.stderr,
            flush=True,
        )

        inert: list[str] = []
        if cap.source == "env" and explicit is not None and explicit > cap.seconds:
            inert.append(
                f"{pack_id}: {cap.override_env}={cap.seconds:.0f} overrides "
                f"--timeout-per-case {explicit:.0f} for the {unit} cap — "
                f"scenarios are cut at {cap.seconds:.0f}s"
            )
        if self._model_turn_timeout_customised:
            value = (
                "0 (disabled)"
                if self.model_turn_timeout is None
                else f"{self.model_turn_timeout:.0f}"
            )
            inert.append(
                f"{pack_id}: --model-turn-timeout/BENCHLOCAL_MODEL_TURN_TIMEOUT={value} "
                f"does not apply — the agent makes its own model calls in-container; "
                f"the {unit} cap is {cap.seconds:.0f}s ({cap.env_name})"
            )
        for msg in inert:
            warnings.append(msg)
            print(f"⚠️  {msg}", file=sys.stderr, flush=True)

    def _inject_sandbox_log_file(self, result: ScenarioResult, pack_id: str | None) -> ScenarioResult:
        """v0.8.1: stamp `verifier_trace.sandbox_log_file` so `inspect --logs DIR`
        can resolve which log goes with which scenario without guessing.

        Records the relative filename `sandbox-<pack_id>.log` (the same file
        SandboxClient.stop() writes when log_dir is set). v0.8.1 inspect
        joins this with the user-supplied --logs DIR. No-op when
        --sandbox-log-dir wasn't set or the result is from a non-sandboxed pack.
        """
        if not self.sandbox_log_dir or not pack_id:
            return result
        existing = dict(result.verifier_trace) if isinstance(result.verifier_trace, dict) else {}
        existing.setdefault("sandbox_log_file", f"sandbox-{pack_id}.log")
        return replace(result, verifier_trace=existing)

    def _stop_sandboxes(self) -> None:
        # Capture container logs before docker-rm wipes them — useful for
        # post-run forensics on sandbox-side errors (verifier exceptions,
        # upstream Node tracebacks, mock-marker warnings).
        log_dir = self.sandbox_log_dir
        for client in list(self._sandbox_clients.values()):
            client.stop(log_dir=log_dir)
        self._sandbox_clients.clear()

    def _timeout_budget_for_meta(self, meta: dict) -> float:
        if self.timeout_per_case is not None:
            return self.timeout_per_case
        value = meta.get("timeout_per_case_default") or meta.get("default_max_seconds") or DEFAULT_TIMEOUT_PER_CASE
        return self._scale_timeout_budget(float(value), meta)

    def _timeout_budget_for_scenario(self, meta: dict, scenario: dict) -> float:
        if self.timeout_per_case is not None:
            return self.timeout_per_case
        value = (
            scenario.get("max_seconds_override")
            or meta.get("timeout_per_case_default")
            or meta.get("default_max_seconds")
            or DEFAULT_TIMEOUT_PER_CASE
        )
        return self._scale_timeout_budget(float(value), meta)

    def _model_request_timeout(self, meta: dict, scenario_timeout: float) -> float:
        """Bound one runner-owned sandbox model call without shrinking the scenario budget."""
        if not meta.get("supports_sandboxed_only") or self.model_turn_timeout is None:
            return scenario_timeout
        return min(scenario_timeout, self.model_turn_timeout)

    def _scale_timeout_budget(self, base_seconds: float, meta: dict) -> float:
        # The returned budget is a deliberately-generous CEILING, not an SLA.
        # It composes two independent factors, both required for thinking-ON runs:
        #
        #   1. rig-speed scale  = reference_tps / measured_decode_tps
        #        Probe measures EMPTY-context decode TPS, which over-estimates the
        #        loaded rate (~3x on a filled KV cache). That over-estimate makes
        #        the budget *more* generous, which is safe for a ceiling.
        #   2. token-budget multiplier = effective_max_tokens / timeout_baseline_tokens
        #        A raised emission ceiling (thinking-ON's 16384, or --max-tokens on
        #        either arm) legitimately decodes far longer than the flat base
        #        budget. Applies to BOTH arms since #103 — the arm a scenario runs
        #        in doesn't change how long N tokens take to decode, and leaving the
        #        non-thinking arm unscaled just converted token_limit into timeout.
        #        The baseline is its own field: scaling off the pack's own
        #        max_tokens made the ratio self-cancelling.
        #
        # CRITICAL: the token-budget multiplier must apply EVEN WHEN no reference_tps is
        # set (deterministic/reasoning packs). A previous attempt gated it behind the
        # reference check, making it dead code for exactly the packs that broke (#54).
        # Do NOT "tighten" this budget back toward the empty-context probe estimate —
        # at the loaded decode rate a 16384-token thinking response needs ~1490s, far
        # above the naive probe-based estimate, so the generous margin is intentional.
        note_parts: list[str] = []
        budget = base_seconds

        scale = self._reference_speed_scale(meta)
        if scale is not None:
            budget *= scale
            note_parts.append(
                f"timeout scaling active: measured_decode_tps={self._measured_decode_tps:.1f}"
            )
            reference = self.reference_tps_override or meta.get("timeout_reference_tps")
            note_parts.append(f"reference_tps={float(reference):.1f}")
            note_parts.append(f"scale={scale:.2f}")

        multiplier = self._token_budget_timeout_multiplier(meta)
        if multiplier > 1.0:
            budget *= multiplier
            effective_max = self._effective_max_tokens(meta)
            baseline = self._timeout_baseline_tokens(meta)
            note_parts.append(
                f"token-budget-multiplier={effective_max}/{baseline}={multiplier:.2f}"
            )

        ceiling = self._timeout_ceiling_for_meta(meta)
        if ceiling is not None and budget > ceiling:
            budget = ceiling
            note_parts.append(f"ceiling-clamped={ceiling:.0f}s")

        if note_parts:
            if not note_parts[0].startswith("timeout scaling active"):
                note_parts.insert(0, "timeout scaling active")
            self._timeout_scaling_note = ", ".join(note_parts)
            self._emit_timeout_scaling_note_once()
        return budget

    def _timeout_ceiling_for_meta(self, meta: dict) -> float | None:
        value = (
            self.timeout_ceiling_s
            if self.timeout_ceiling_s is not None
            else meta.get("timeout_ceiling_s")
        )
        if value is None:
            return None
        try:
            ceiling = float(value)
        except (TypeError, ValueError):
            return None
        return ceiling if ceiling > 0 else None

    def _reference_speed_scale(self, meta: dict) -> float | None:
        """rig-speed scale (reference_tps / measured_tps), or None when inapplicable."""
        reference = self.reference_tps_override or meta.get("timeout_reference_tps")
        if not reference:
            return None
        try:
            reference_tps = float(reference)
        except (TypeError, ValueError):
            return None
        if reference_tps <= 0:
            return None
        measured_tps = self._timeout_measured_tps()
        if measured_tps is None or measured_tps <= 0:
            return None
        scale = reference_tps / measured_tps
        if not self.timeout_scale_down:
            scale = max(1.0, scale)
        return scale

    def _emit_timeout_scaling_note_once(self) -> None:
        if self._timeout_scaling_note_emitted or not self._timeout_scaling_note:
            return
        print(f"[runner] {self._timeout_scaling_note}", file=sys.stderr, flush=True)
        self._timeout_scaling_note_emitted = True

    def _timeout_baseline_tokens(self, meta: dict) -> int:
        """Emission length the pack's flat time budget was calibrated for.

        Defaults to the pack's own `sampling_defaults.max_tokens`, which is
        what every pack's `default_max_seconds` was written against — so this
        is behaviour-preserving for packs that don't opt out. A pack that
        RAISES its emission ceiling without re-timing its clock must pin
        `timeout_baseline_tokens` to the old value, or the ratio self-cancels
        and the budget silently shrinks (#103: reasonmath went to 16384 while
        its 60s clock still assumes 1024).

        The two are separate quantities: `max_tokens` is how much the model may
        emit, this is how much the clock was sized for. They coincided by
        accident, not by design.
        """
        explicit = meta.get("timeout_baseline_tokens")
        if explicit:
            try:
                value = int(explicit)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
        sampling_defaults = meta.get("sampling_defaults") or {}
        try:
            value = int(sampling_defaults.get("max_tokens") or 0)
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT_BASELINE_TOKENS
        return value if value > 0 else DEFAULT_TIMEOUT_BASELINE_TOKENS

    def _effective_max_tokens(self, meta: dict) -> int | None:
        """Emission ceiling for the arm that is actually running."""
        if resolve_thinking_enabled(meta, self.thinking_override):
            return self.thinking_max_tokens
        override = self.sampling_overrides.get("max_tokens")
        if override is not None:
            try:
                return int(override)
            except (TypeError, ValueError):
                pass
        sampling_defaults = meta.get("sampling_defaults") or {}
        try:
            return int(sampling_defaults.get("max_tokens") or 0) or None
        except (TypeError, ValueError):
            return None

    def _token_budget_timeout_multiplier(self, meta: dict) -> float:
        """Scale the clock with the emission ceiling — for BOTH arms.

        This was thinking-only until #103, so a non-thinking arm raised via
        --max-tokens kept the flat budget and merely traded `token_limit`
        failures for `timeout` ones. The arm a scenario runs in doesn't change
        how long N tokens take to decode.
        """
        effective = self._effective_max_tokens(meta)
        if effective is None or effective <= 0:
            return 1.0
        baseline = self._timeout_baseline_tokens(meta)
        if effective <= baseline:
            return 1.0
        return float(effective) / float(baseline)

    # ------------------------------------------------------------------ #145
    # Derive the token ceiling from the clock (--budget-from-timeout).
    #
    # Timeout scaling (#46/#54/#103/#110) derives the CLOCK from measured TPS
    # and holds the TOKEN ceiling fixed. When the clock is capped — by
    # --timeout-ceiling-s, by --timeout-per-case, or by an operator who will
    # not run a six-hour suite — the clock silently becomes the tighter limit
    # again and every long trace dies as `timeout`, indistinguishable from a
    # stuck model. This is the dual direction: fix the clock, derive the
    # tokens, and say so in the result.

    def _tokens_for_clock(self, clock_s: float, measured_tps: float) -> int:
        return max(0, int(self.budget_headroom * float(clock_s) * float(measured_tps)))

    def _answer_reserve(self, meta: dict, budget: int) -> int:
        """Tokens held back from reasoning so the final answer still fits.

        The pack's `timeout_baseline_tokens` (what its clock was sized for —
        the non-thinking answer length) is the natural reserve, capped at a
        quarter of the budget so reasoning always keeps the larger share.
        """
        baseline = self._timeout_baseline_tokens(meta)
        return max(0, min(baseline, budget // 4))

    def _detect_engine_family(self) -> str:
        """`llama.cpp` when GET /props answers (the only family with a
        per-request reasoning-budget knob), else `unknown`. Mock and
        negative-control runs never touch the endpoint."""
        if self.mock_responses or self.negative_control is not None:
            return "unknown"
        base = _endpoint_base(self.endpoint)
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{base}/props", headers=self._request_headers)
            if response.status_code == 200 and isinstance(response.json(), dict):
                return "llama.cpp"
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        return "unknown"

    def _prepare_token_budget(self, pack_ids: list[str], warnings: list[str]) -> None:
        if not self.budget_from_timeout:
            return
        measured_tps = self._timeout_measured_tps()
        if measured_tps is None or measured_tps <= 0:
            raise ValueError(
                "--budget-from-timeout: cannot derive a token budget without a measured "
                "decode rate — the startup TPS probe did not run or failed "
                f"({self._timeout_scaling_note or 'no note'}); pass --measured-tps N "
                "(use the SLOWEST arm's rate when comparing configs) or fix the endpoint"
            )
        self._engine_family = self._detect_engine_family()
        reasoning_keys = list(_REASONING_BUDGET_KEYS) if self._engine_family == "llama.cpp" else None
        thinking_packs = [
            pack_id for pack_id in pack_ids
            if pack_id not in _AGENT_OWNED_PACKS
            and resolve_thinking_enabled(load_pack(pack_id)[0], self.thinking_override)
        ]
        locked = sorted(
            key for key in ("max_tokens", "thinking_budget", *_REASONING_BUDGET_KEYS)
            if key in self.extra_body
        )
        report: dict = {
            "mode": "derived",
            "headroom": self.budget_headroom,
            "measured_tps": measured_tps,
            "engine": self._engine_family,
            "reasoning_keys": reasoning_keys,
            "control": {"status": "skipped", "reason": "no thinking-enabled pack in this run"},
            "packs": {},
        }
        if locked:
            report["extra_body_locked"] = locked
            warnings.append(
                "--budget-from-timeout: --extra-body already sets "
                f"{', '.join(locked)}; those keys are left as given and the derived "
                "budget is not applied to them"
            )
        if thinking_packs and reasoning_keys is None:
            warnings.append(
                "--budget-from-timeout: no per-request reasoning-budget control for this "
                "engine (GET /props not served, so not llama.cpp) — the derived budget is "
                "enforced through max_tokens only, so reasoning may consume the whole "
                f"budget and truncate the answer (thinking packs: {', '.join(thinking_packs)})"
            )
            report["control"] = {"status": "skipped", "reason": "engine has no reasoning-budget knob"}
        elif thinking_packs and reasoning_keys is not None:
            if not self.budget_control:
                report["control"] = {"status": "skipped", "reason": "--no-budget-control"}
            elif self.mock_responses or self.negative_control is not None:
                report["control"] = {"status": "skipped", "reason": "synthetic traffic"}
            else:
                report["control"] = self._verify_budget_control(measured_tps, warnings)
        self._token_budget_report = report
        knob = " + ".join(reasoning_keys) if reasoning_keys else "none (max_tokens only)"
        control = report["control"]
        control_text = control["status"] + (
            f" ({control['reason']})" if control.get("reason") else
            f" ({_BUDGET_CONTROL_TOKENS}-token budget → {control.get('completion_tokens')} completion tokens)"
            if control.get("completion_tokens") is not None else ""
        )
        print(
            f"[runner] token budget: derived from the per-request clock "
            f"(--budget-from-timeout, headroom {self.budget_headroom:g}, "
            f"measured {measured_tps:.1f} tok/s, engine {self._engine_family}, "
            f"reasoning knob {knob}, control {control_text})",
            file=sys.stderr,
            flush=True,
        )

    def _verify_budget_control(self, measured_tps: float, warnings: list[str]) -> dict:
        """Positive control: prove the engine honours a per-request reasoning budget.

        Sends one thinking-ON request with a 128-token budget on a prompt that
        invites a long trace. A multi-thousand-token completion means the
        knob was dropped — exactly the silent failure the issue describes
        (a full 8-pack ran for three hours believing a 32,768 budget was in
        force) — so the run refuses to start. A short completion is
        `applied`; no usage in the response is `inconclusive` (warned, not
        fatal).
        """
        request: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": _BUDGET_CONTROL_PROMPT}],
            "temperature": 0,
            "max_tokens": _BUDGET_CONTROL_MAX_TOKENS,
        }
        _apply_thinking_control(request, self.thinking_control, True, self.reasoning_effort)
        for key in _REASONING_BUDGET_KEYS:
            request[key] = _BUDGET_CONTROL_TOKENS
        timeout = max(60.0, 2.0 * _BUDGET_CONTROL_MAX_TOKENS / measured_tps)
        try:
            status, response, _trace = self._post_chat(request, timeout, max_attempts=1)
        except (httpx.HTTPError, _TransientPostFailure) as exc:
            warnings.append(f"--budget-from-timeout: reasoning-budget control inconclusive ({exc})")
            return {"status": "inconclusive", "reason": str(exc)}
        if status >= 400:
            warnings.append(
                f"--budget-from-timeout: reasoning-budget control inconclusive (HTTP {status})"
            )
            return {"status": "inconclusive", "reason": f"HTTP {status}"}
        tokens = self._completion_tokens(response)
        if tokens is None:
            warnings.append(
                "--budget-from-timeout: reasoning-budget control inconclusive "
                "(endpoint reported no usage.completion_tokens)"
            )
            return {"status": "inconclusive", "reason": "no usage in response"}
        if tokens > _BUDGET_CONTROL_FAIL_ABOVE:
            raise ValueError(
                f"--budget-from-timeout: reasoning-budget control FAILED — a "
                f"{_BUDGET_CONTROL_TOKENS}-token budget ({' / '.join(_REASONING_BUDGET_KEYS)}) "
                f"produced {tokens} completion tokens, so the engine ignored the per-request "
                "budget (server booted with --reasoning-budget? a tree that reads a different "
                "key?). Refusing to run a whole suite on a budget that is not in force; pass "
                "--no-budget-control to proceed with max_tokens enforcement only"
            )
        return {"status": "applied", "completion_tokens": tokens}

    def _announce_token_budget(self, pack_id: str, meta: dict, warnings: list[str] | None) -> None:
        report = self._token_budget_report
        if report is None or pack_id in report["packs"]:
            return
        ceiling = self._effective_max_tokens(meta)
        if pack_id in _AGENT_OWNED_PACKS:
            report["packs"][pack_id] = {
                "applies": False,
                "reason": "agent makes its own model calls in-container",
                "ceiling": ceiling,
            }
            print(
                f"[runner] {pack_id} token budget: n/a — agent makes its own model calls "
                f"in-container; ceiling {ceiling} unchanged",
                file=sys.stderr,
                flush=True,
            )
            return
        clock = self._model_request_timeout(meta, self._timeout_budget_for_meta(meta))
        tps = float(report["measured_tps"])
        derived = self._tokens_for_clock(clock, tps)
        budget = min(derived, int(ceiling)) if ceiling else derived
        binds = "clock" if ceiling is None or derived < int(ceiling) else "ceiling"
        thinking = resolve_thinking_enabled(meta, self.thinking_override)
        reserve = self._answer_reserve(meta, budget) if thinking else None
        reasoning = (budget - reserve) if thinking else None
        delivery = ["max_tokens"]
        if thinking and report.get("reasoning_keys"):
            delivery.extend(report["reasoning_keys"])
        report["packs"][pack_id] = {
            "applies": True,
            "clock_s": clock,
            "derived": derived,
            "ceiling": ceiling,
            "budget": budget,
            "binds": binds,
            "reasoning": reasoning,
            "answer_reserve": reserve,
            "delivery": delivery,
        }
        share = f", reasoning {reasoning} + answer reserve {reserve}" if thinking else ""
        print(
            f"[runner] {pack_id} token budget: {budget} ({binds} binds: derived {derived} = "
            f"{self.budget_headroom:g} x {clock:.0f}s x {tps:.1f} tok/s, ceiling "
            f"{ceiling if ceiling is not None else 'none'}){share}; delivered as "
            f"{' + '.join(delivery)}",
            file=sys.stderr,
            flush=True,
        )
        baseline = self._timeout_baseline_tokens(meta)
        if budget < baseline and warnings is not None:
            warnings.append(
                f"{pack_id}: derived token budget {budget} is below the pack's nominal answer "
                f"size {baseline} — the clock is too tight for this pack; expect token_limit"
            )

    def _apply_token_budget(
        self, meta: dict, scenario: dict, request: dict, sampling: dict, request_timeout: float
    ) -> None:
        """Per request: ceiling = min(arm ceiling, headroom x clock x tps); on a
        thinking request also the reasoning share, so the pair stays coherent
        (a reasoning cap alone relocates the overrun into content)."""
        report = self._token_budget_report
        if report is None or scenario.get("pack_id") in _AGENT_OWNED_PACKS:
            return
        derived = self._tokens_for_clock(request_timeout, float(report["measured_tps"]))
        ceiling = sampling.get("max_tokens")
        try:
            ceiling = int(ceiling) if ceiling is not None else None
        except (TypeError, ValueError):
            ceiling = None
        budget = min(derived, ceiling) if ceiling else derived
        locked = set(report.get("extra_body_locked") or ())
        if "max_tokens" not in locked:
            request["max_tokens"] = sampling["max_tokens"] = budget
        thinking = _request_thinking_enabled(
            sampling, self.thinking_control, resolve_thinking_enabled(meta, self.thinking_override)
        )
        if not thinking:
            return
        reasoning = max(1, budget - self._answer_reserve(meta, budget))
        for key in report.get("reasoning_keys") or ():
            if key not in locked:
                request[key] = sampling[key] = reasoning
        # CLI-40's provider-native Qwen pair carries the same quantity.
        if "thinking_budget" in request and "thinking_budget" not in locked:
            request["thinking_budget"] = sampling["thinking_budget"] = reasoning

    def _timeout_measured_tps(self) -> float | None:
        # #62: negative-control never calls the endpoint — skip the decode probe
        # entirely (no endpoint to probe) and use static pack budgets.
        if self.negative_control is not None:
            return None
        if self._measured_decode_tps is not None:
            return self._measured_decode_tps
        try:
            self._measured_decode_tps = self._probe_decode_tps()
        except Exception as exc:  # noqa: BLE001
            self._timeout_scaling_note = f"timeout TPS probe failed; using static pack budgets ({exc})"
            return None
        return self._measured_decode_tps

    def _endpoint_reachable(self, timeout: float = 5.0) -> bool:
        """Cheap host-side reachability check before the TPS probe.

        A blackholed endpoint (silently drops packets) would otherwise make the
        probe's `_post_chat` calls block for their full read timeout. Probe a
        lightweight `GET .../v1/models` (falling back to `.../models`) with a
        short timeout and NO transient retry; if nothing answers, the caller
        skips the decode probe and falls back to static pack budgets.
        """
        base = _endpoint_base(self.endpoint)
        for suffix in ("/v1/models", "/models"):
            url = f"{base}{suffix}"
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.get(url, headers=self._request_headers)
            except httpx.HTTPError:
                continue
            # Any HTTP response (even 4xx/5xx) means the host is reachable and
            # answering; only transport-level failures count as unreachable.
            if response.status_code < 500:
                return True
        return False

    def _probe_decode_tps(self) -> float | None:
        if not self._endpoint_reachable():
            self._timeout_scaling_note = (
                "endpoint unreachable on preflight; skipping TPS probe, using static pack budgets"
            )
            return None
        samples: list[float] = []
        prompt = (
            "Write a concise local-inference benchmark note of about 200 tokens. "
            "Use plain prose and no lists."
        )
        for _ in range(3):
            request = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "top_p": 1,
                "max_tokens": 200,
            }
            if self.thinking_control == THINKING_CONTROL_ENABLE:
                # Preserve the historical near-off shape for Qwen/thinking-only
                # endpoints: they can reject false, while budget=1 avoids
                # measuring full reasoning-decode rate.
                request["chat_template_kwargs"] = {"enable_thinking": True}
                request["thinking_budget"] = 1
            else:
                _apply_thinking_control(
                    request,
                    self.thinking_control,
                    False,
                    self.reasoning_effort,
                )
            started = time.perf_counter()
            # The probe must never be the thing that hangs a run: bounded read
            # budget and NO transient retry (max_attempts=1). Combined with the
            # preflight above and the #58 no-retry-on-timeout fix, a dead or
            # blackholed endpoint costs at most one short timeout per sample
            # instead of 3 x (retry loop x 120s).
            _status, response, _trace = self._post_chat(request, 30.0, max_attempts=1)
            elapsed = max(time.perf_counter() - started, 1e-6)
            tokens = self._completion_tokens(response)
            if tokens is None:
                text = content_with_source(response)[0]
                tokens = max(1, len(text.split())) if text else None
            if tokens:
                samples.append(float(tokens) / elapsed)
        if not samples:
            return None
        return statistics.mean(samples)

    def _inline_retry_limit(self, meta: dict, run: ScenarioRun) -> int:
        """Return the total-attempt cap for the latest failed attempt."""
        if run.result.passed or run.result.failure_mode == "verifier_not_implemented":
            return 1
        mode = run.result.failure_mode
        if mode in _CONTENT_FAILURE_MODES:
            # A single-scoreboard scenario represents many independent units;
            # best-of-N at the outer boolean would misstate its real pass rate.
            if meta.get("_architecture") == "single-scoreboard":
                return 1
            return max(1, self.retry_failures)
        if mode in _MODEL_DEFECT_FAILURE_MODES:
            # #152: the engine rejected the generation itself. A model verdict,
            # not a harness fault — so it must NOT inherit the infra floor
            # below — and typically a repetition loop that ran to the cap, so
            # each attempt costs what a runaway costs. Off by default;
            # --retry-runaways opts it in on the same terms as the runaways.
            if self.retry_runaways:
                return max(DEFAULT_INLINE_RETRY_ATTEMPTS, self.retry_failures)
            return 1
        if mode in _INFRA_FAILURE_MODES:
            # Harness failures remain retryable even when model-verdict retries
            # are disabled. This complements request-level transient retries.
            return max(DEFAULT_INLINE_RETRY_ATTEMPTS, self.retry_failures)
        if mode in _RUNAWAY_FAILURE_MODES and self.retry_runaways:
            return max(DEFAULT_INLINE_RETRY_ATTEMPTS, self.retry_failures)
        return 1

    @staticmethod
    def _best_of_n_eligible(scenario: dict) -> bool:
        raw = scenario.get("raw_scenario")
        return not bool(
            scenario.get("no_best_of_n")
            or (isinstance(raw, dict) and raw.get("no_best_of_n"))
        )

    def _run_scenario_with_inline_retries(
        self,
        meta: dict,
        scenario: dict,
        *,
        repeat_index: int,
    ) -> ScenarioRun:
        """Run pass@1, then nest only retry-eligible failure attempts."""
        baseline = self.run_scenario(meta, scenario, repeat_index=repeat_index)
        baseline.best_of_n_eligible = self._best_of_n_eligible(scenario)
        if baseline.result.failure_mode == "verifier_not_implemented":
            return baseline

        current = baseline
        baseline.retry_eligible = self._inline_retry_limit(meta, current) > 1
        seen_failures: set[tuple[str, str]] = set()
        if (fingerprint := self._failure_fingerprint(current)) is not None:
            seen_failures.add(fingerprint)
        while not current.result.passed:
            attempt_limit = self._inline_retry_limit(meta, current)
            if baseline.attempt_count >= attempt_limit:
                break
            current = self.run_scenario(meta, scenario, repeat_index=repeat_index)
            baseline.retry_attempts.append(current.to_dict())
            baseline.attempt_count += 1
            # #152: a retry that reproduces an earlier attempt's failure
            # byte-for-byte is deterministic; further attempts cannot differ.
            fingerprint = self._failure_fingerprint(current)
            if fingerprint is not None and fingerprint in seen_failures:
                note = (
                    f"identical failure reproduced on attempt {baseline.attempt_count}; "
                    f"remaining retries skipped"
                )
                trace = dict(baseline.result.verifier_trace or {})
                trace["retry_stopped"] = note
                baseline.result = replace(baseline.result, verifier_trace=trace)
                break
            if fingerprint is not None:
                seen_failures.add(fingerprint)

        passed_attempt: int | None = 1 if baseline.result.passed else None
        if passed_attempt is None:
            for attempt_index, retry in enumerate(baseline.retry_attempts, start=2):
                if retry.get("passed"):
                    passed_attempt = attempt_index
                    break
        baseline.label = f"pass@{passed_attempt}" if passed_attempt is not None else "fail"
        baseline.pass_at_k = bool(
            baseline.result.passed
            or (passed_attempt is not None and baseline.best_of_n_eligible)
        )
        return baseline

    @staticmethod
    def _failure_fingerprint(run: ScenarioRun) -> tuple[str, str] | None:
        """#152: identity of a failure whose retries can only reproduce it.

        Only `_MODEL_DEFECT_FAILURE_MODES` are fingerprinted: the engine's
        message carries the parse position, which pins the generation. Infra
        failures are deliberately excluded — llama.cpp answers `503 Loading
        model` with a byte-identical body on every attempt while it boots, and
        an identical body there says nothing about determinism. Content
        verdicts are excluded too: under the canonical temp=0 arms an identical
        wrong answer is the common case, and collapsing pass@3 to pass@2 for
        it is a pass@k policy decision, not this fix.
        """
        result = run.result
        if result.passed or result.failure_mode not in _MODEL_DEFECT_FAILURE_MODES:
            return None
        message = _engine_error_message(run.raw_response) or result.detail
        return (str(result.failure_mode), message)

    def _configured_pass_at_k(self, meta: dict, repeat: int) -> int:
        if (
            repeat != 1
            or self.negative_control is not None
            or not self.inline_retries_enabled
        ):
            return 0
        if meta.get("_architecture") == "single-scoreboard":
            return 0
        if self.retry_failures > 1:
            return self.retry_failures
        return DEFAULT_INLINE_RETRY_ATTEMPTS if self.retry_runaways else 0

    def run_pack(
        self,
        pack_id: str,
        *,
        repeat: int = 1,
        warnings: list[str] | None = None,
        scenario_ids: list[str] | None = None,
        completed_repeats: dict[str, set[int]] | None = None,
    ) -> PackResult:
        meta, scenarios = load_pack(pack_id)
        catalog_scenario_count = len(scenarios)
        if scenario_ids is not None:
            wanted = set(scenario_ids)
            scenarios = [scenario for scenario in scenarios if scenario["id"] in wanted]
        selected_catalog_count = catalog_scenario_count if scenario_ids is not None else None
        if meta.get("requires_dataset_access"):
            warning = meta.get("dataset_access_note") or f"skipping {pack_id}: dataset access required"
            if warnings is not None:
                warnings.append(warning)
            return PackResult(
                pack_id=pack_id,
                version=meta["version"],
                upstream_commit=meta["upstream_commit"],
                scenario_count=len(scenarios),
                passed=0,
                total=0,
                score=0.0,
                latency=_latency([]),
                scenarios=[],
                skipped=True,
                status="dataset-unavailable",
                warnings=[warning],
                thinking_enabled=resolve_thinking_enabled(meta, self.thinking_override),
                catalog_scenario_count=selected_catalog_count,
            )
        if meta.get("supports_sandboxed_only") and not self.enable_sandboxed_packs:
            warning = f"skipping {pack_id}: sandboxed verifier not enabled"
            if warnings is not None:
                warnings.append(warning)
            return PackResult(
                pack_id=pack_id,
                version=meta["version"],
                upstream_commit=meta["upstream_commit"],
                scenario_count=len(scenarios),
                passed=0,
                total=0,
                score=0.0,
                latency=_latency([]),
                scenarios=[],
                skipped=True,
                status="stubbed",
                warnings=[warning],
                thinking_enabled=resolve_thinking_enabled(meta, self.thinking_override),
                catalog_scenario_count=selected_catalog_count,
            )
        if meta.get("supports_sandboxed_only") and pack_id not in self._sandbox_clients:
            warning = f"skipping {pack_id}: sandbox unavailable"
            if warnings is not None:
                warnings.append(warning)
            return PackResult(
                pack_id=pack_id,
                version=meta["version"],
                upstream_commit=meta["upstream_commit"],
                scenario_count=len(scenarios),
                passed=0,
                total=0,
                score=0.0,
                latency=_latency([]),
                scenarios=[],
                skipped=True,
                status="sandbox-unavailable",
                warnings=[warning],
                thinking_enabled=resolve_thinking_enabled(meta, self.thinking_override),
                catalog_scenario_count=selected_catalog_count,
            )

        self._announce_token_budget(pack_id, meta, warnings)

        runs: list[ScenarioRun] = []
        completed_repeats = completed_repeats or {}
        total_scenarios = sum(
            1
            for repeat_index in range(1, repeat + 1)
            for scenario in scenarios
            if repeat_index not in completed_repeats.get(scenario["id"], set())
        )
        scenario_index = 0
        for repeat_index in range(1, repeat + 1):
            for scenario in scenarios:
                if repeat_index in completed_repeats.get(scenario["id"], set()):
                    continue
                scenario_index += 1
                if (
                    repeat == 1
                    and self.negative_control is None
                    and self.inline_retries_enabled
                ):
                    run = self._run_scenario_with_inline_retries(
                        meta, scenario, repeat_index=repeat_index
                    )
                else:
                    # --repeat is the symmetric variance path and negative
                    # controls must never multiply deterministic junk samples.
                    run = self.run_scenario(meta, scenario, repeat_index=repeat_index)
                runs.append(run)
                if self._on_scenario_complete is not None:
                    self._on_scenario_complete(run, scenario_index, total_scenarios)

        counted = [run for run in runs if run.result.failure_mode != "verifier_not_implemented"]
        latencies = [run.result.latency_seconds for run in counted if run.result.latency_seconds > 0]

        # #3: single-scoreboard packs (aider) run their entire sub-suite inside
        # one scenario. Surface the real per-unit X/Y in the pack headline
        # rather than collapsing to a binary 1/1 or 0/1 — which buried both the
        # true success rate (16/30 shown as "1/1 = 100%") and graceful partial
        # results on timeout (18/30 shown as "0/1 = 0%").
        if meta.get("_architecture") == "single-scoreboard" and counted:
            sr = counted[0].result
            if sr.total_count is not None:
                sb_passed = sr.passed_count or 0
                sb_total = sr.total_count
                sb_score = sr.pass_rate if sr.pass_rate is not None else (
                    sb_passed / sb_total if sb_total else 0.0
                )
                # Surface error failure modes (timeout/crash/unreachable) in the
                # status column. A clean pass or a below-threshold "verifier_fail"
                # both ran fine, so the score conveys the outcome (status "ok").
                sb_status = (
                    sr.failure_mode
                    if sr.failure_mode in (
                        "agent_runner_timeout", "agent_runner_crashed",
                        "model_endpoint_unreachable", "server_error",
                        "result_json_malformed",
                    )
                    else "ok"
                )
                return PackResult(
                    pack_id=pack_id,
                    version=meta["version"],
                    upstream_commit=meta["upstream_commit"],
                    scenario_count=len(scenarios),
                    passed=sb_passed,
                    total=sb_total,
                    score=sb_score,
                    latency=_latency(latencies),
                    scenarios=runs,
                    status=sb_status,
                    thinking_enabled=resolve_thinking_enabled(meta, self.thinking_override),
                    catalog_scenario_count=selected_catalog_count,
                    variance=_repeat_variance(runs, repeat),
                    tokens=_pack_tokens(runs),
                    pass_at_k=_pass_at_k_summary(
                        runs,
                        self._configured_pass_at_k(meta, repeat),
                    ),
                    diagnostics=pack_diagnostics(runs),
                    runaway=runaway_summary(runs),
                )

        passed = sum(1 for run in counted if run.result.passed)
        total = len(counted)
        return PackResult(
            pack_id=pack_id,
            version=meta["version"],
            upstream_commit=meta["upstream_commit"],
            scenario_count=len(scenarios),
            passed=passed,
            total=total,
            score=(passed / total if total else 0.0),
            latency=_latency(latencies),
            scenarios=runs,
            status="ok" if total else "stubbed",
            thinking_enabled=resolve_thinking_enabled(meta, self.thinking_override),
            catalog_scenario_count=selected_catalog_count,
            variance=_repeat_variance(runs, repeat),
            tokens=_pack_tokens(runs),
            pass_at_k=_pass_at_k_summary(
                runs,
                self._configured_pass_at_k(meta, repeat),
            ),
            diagnostics=pack_diagnostics(runs),
            runaway=runaway_summary(runs),
        )

    def run_scenario(self, meta: dict, scenario: dict, *, repeat_index: int = 1) -> ScenarioRun:
        # The clock is resolved first: under --budget-from-timeout (#145) the
        # request's token ceiling is a function of it.
        timeout = self._timeout_budget_for_scenario(meta, scenario)
        request_timeout = self._model_request_timeout(meta, timeout)
        request, sampling = build_request(
            scenario,
            meta,
            self.model,
            thinking_enabled=self.thinking_override,
            thinking_max_tokens=self.thinking_max_tokens,
            extra_body=self.extra_body,
            sampling_overrides=self.sampling_overrides or None,
            sampling_from_server=self.sampling_from_server,
            thinking_sampler=self.thinking_sampler,
            thinking_control=self.thinking_control,
            reasoning_effort=self.reasoning_effort,
        )
        _apply_cli_thinking_controls(
            scenario,
            request,
            sampling,
            self.thinking_max_tokens,
            self.thinking_control,
            preserve_native_controls=any(
                key in self.extra_body
                for key in ("enable_thinking", "thinking_budget")
            ),
        )
        self._apply_token_budget(meta, scenario, request, sampling, request_timeout)
        started = time.perf_counter()
        status_code: int | None = None
        raw_response: dict | None = None
        transient_trace: dict | None = None

        sandbox_client = self._sandbox_clients.get(scenario.get("pack_id"))
        if (
            meta.get("supports_sandboxed_only")
            and sandbox_client is not None
            and getattr(getattr(sandbox_client, "config", None), "multi_turn", False)
            and self._should_use_multiturn(scenario)
            and scenario["id"] not in self.mock_responses
            and self.negative_control is None
        ):
            return self._run_multiturn_scenario(
                meta,
                scenario,
                sandbox_client,
                request,
                sampling,
                repeat_index,
                request_timeout,
            )

        if self.negative_control is not None:
            raw_response = _negative_control_response(self.negative_control)
            latency = 0.0
        elif scenario["id"] in self.mock_responses:
            raw_response = self.mock_responses[scenario["id"]]
            latency = 0.0
        else:
            try:
                status_code, raw_response, transient_trace = self._post_chat(request, request_timeout)
                latency = time.perf_counter() - started
                if status_code >= 500:
                    failure_mode, detail = classify_server_error(status_code, raw_response)  # #152
                    result = ScenarioResult(scenario["id"], False, failure_mode, detail, latency)
                    result = self._inject_transient_trace(result, transient_trace)
                    return self._scenario_run(scenario, raw_response, request, sampling, status_code, result, repeat_index)
                if status_code >= 400:
                    result = ScenarioResult(scenario["id"], False, "http_error", f"HTTP {status_code}", latency)
                    result = self._inject_transient_trace(result, transient_trace)
                    return self._scenario_run(scenario, raw_response, request, sampling, status_code, result, repeat_index)
            except _TransientPostFailure as exc:
                latency = time.perf_counter() - started
                result = ScenarioResult(scenario["id"], False, exc.failure_mode, exc.detail, latency)
                result = self._inject_transient_trace(result, exc.trace)
                return self._scenario_run(scenario, None, request, sampling, None, result, repeat_index)
            except httpx.TimeoutException:
                latency = time.perf_counter() - started
                result = ScenarioResult(scenario["id"], False, "timeout", f"timed out after {timeout}s", latency)
                return self._scenario_run(scenario, None, request, sampling, None, result, repeat_index)
            except httpx.HTTPError as exc:
                latency = time.perf_counter() - started
                result = ScenarioResult(scenario["id"], False, "http_error", str(exc), latency)
                return self._scenario_run(scenario, None, request, sampling, None, result, repeat_index)

        assert raw_response is not None
        raw_response = sanitize_response_text_fields(raw_response)
        response_field_used = content_with_source(raw_response)[1]
        sandboxed_path = (
            meta.get("supports_sandboxed_only")
            and scenario.get("pack_id") in self._sandbox_clients
        )
        if sandboxed_path:
            result = self._sandbox_clients[scenario["pack_id"]].verify(
                scenario,
                raw_response,
                request["messages"],
            )
        else:
            module_name = scenario.get("verifier", {}).get("type") or meta.get("verifier_module")
            module = importlib.import_module(f"benchlocal_cli.scoring.{module_name}")
            result = module.score_scenario(scenario, raw_response)
        latency = time.perf_counter() - started if scenario["id"] in self.mock_responses else latency
        result = replace(result, latency_seconds=latency, **_usage_counts(raw_response))  # #147
        result = self._reclassify_if_truncated(result, raw_response)  # #61
        result = self._inject_transient_trace(result, transient_trace)
        if sandboxed_path:
            result = self._inject_sandbox_log_file(result, scenario.get("pack_id"))
        return self._scenario_run(
            scenario,
            raw_response,
            request,
            sampling,
            status_code,
            result,
            repeat_index,
            response_field_used=response_field_used,
        )

    @staticmethod
    def _reclassify_if_truncated(result: ScenarioResult, raw_response: dict | None) -> ScenarioResult:
        """#61: a failed completion that hit the token cap (finish_reason == "length")
        is a truncation, not a content verdict. Reclassify it to `token_limit` so
        "overthought / looped until the budget ran out" is legible at a glance vs
        "ran to completion but produced a wrong answer". Only content-failure modes
        are overridden; passes and infra failures are left untouched. The original
        verdict is preserved in `detail` for forensics."""
        if result.passed or result.failure_mode not in _CONTENT_FAILURE_MODES:
            return result
        if not isinstance(raw_response, dict):
            return result
        choices = raw_response.get("choices")
        if not (isinstance(choices, list) and choices and isinstance(choices[0], dict)):
            return result
        if choices[0].get("finish_reason") != "length":
            return result
        return replace(
            result,
            failure_mode="token_limit",
            detail=(
                f"output truncated at token limit (finish_reason=length); "
                f"underlying verdict was {result.failure_mode}: {result.detail}"
            ),
        )

    @staticmethod
    def _should_use_multiturn(scenario: dict) -> bool:
        pack_id = scenario.get("pack_id")
        if pack_id == "hermesagent-20":
            return True
        if pack_id == "aider-polyglot-30":
            # v0.9.0: aider-polyglot-30 uses /verify-start with verify-final
            # early-out (single-scoreboard pack — 1 batch = 1 scenario).
            return True
        if pack_id == "cli-40":
            return (scenario.get("raw_scenario") or {}).get("kind") == "multiround"
        return False

    def _max_turns_for(self, scenario: dict) -> int:
        explicit = scenario.get("max_turns") or (scenario.get("raw_scenario") or {}).get("max_turns")
        if isinstance(explicit, int) and explicit > 0:
            return explicit
        if scenario.get("pack_id") == "hermesagent-20":
            return 20
        return 15

    def _post_chat(
        self, request: dict, timeout: float, *, max_attempts: int | None = None
    ) -> tuple[int, dict, dict | None]:
        transient_errors: list[str] = []
        # `max_attempts` lets callers (e.g. the startup TPS probe) opt out of the
        # transient-retry loop so they can never hang a run on a dead endpoint.
        if max_attempts is None:
            max_attempts = self.max_transient_retries + 1
        else:
            max_attempts = max(1, int(max_attempts))

        # Proactive pacing: keep >= request_delay seconds between model requests so we
        # stay under provider rate limits — avoids the 429 rather than recovering from it
        # (a min-interval throttle, so it adds nothing when requests are already slower).
        if self.request_delay > 0:
            wait = self.request_delay - (time.monotonic() - self._last_request_monotonic)
            if wait > 0:
                time.sleep(wait)
        self._last_request_monotonic = time.monotonic()

        stream: dict | None = None  # #157: liveness stats over every streamed attempt
        for attempt in range(1, max_attempts + 1):
            try:
                if self.stream:
                    response, stats = post_chat_streaming(
                        _chat_url(self.endpoint),
                        request,
                        self._request_headers,
                        timeout=timeout,
                        stall_timeout=self.stream_stall_timeout,
                        client_factory=httpx.Client,
                    )
                    stream = _merge_stream_trace(stream, _stream_trace(stats))
                else:
                    with httpx.Client(timeout=timeout) as client:
                        response = client.post(_chat_url(self.endpoint), json=request, headers=self._request_headers)
            except StreamStall as exc:
                # #157: the endpoint went quiet mid-stream. Not retried here —
                # like a timeout (#58), a retry against a server that just
                # stopped answering costs another full allowance; the inline
                # infra-retry policy decides whether the scenario reruns.
                stream = _merge_stream_trace(stream, _stream_trace(exc.stats))
                transient_errors.append(f"attempt {attempt}: StreamStall: {exc}")
                trace = _with_stream(_transient_trace(transient_errors, attempt), stream) or {}
                raise _TransientPostFailure("stall", str(exc), trace) from exc
            except httpx.TimeoutException as exc:
                transient_errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
                # A timeout means the budget was genuinely exceeded — retrying just
                # burns another full budget for the same outcome (#58). Fail fast
                # unless explicitly configured to retry timeouts.
                if attempt >= max_attempts or not self.retry_on_timeout:
                    trace = _with_stream(_transient_trace(transient_errors, attempt), stream) or {}
                    raise _TransientPostFailure("timeout", f"timed out after {timeout}s", trace) from exc
                self._sleep_before_transient_retry(attempt)
                continue
            except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                transient_errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
                if attempt >= max_attempts:
                    trace = _with_stream(_transient_trace(transient_errors, attempt), stream) or {}
                    raise _TransientPostFailure("http_error", str(exc), trace) from exc
                self._sleep_before_transient_retry(attempt)
                continue

            try:
                raw_response = response.json()
            except ValueError:
                raw_response = {"text": response.text}

            # 429 (rate-limited) and 5xx are transient — retry with backoff. 429 is the
            # common cloud-provider throttle (OpenRouter/DashScope/…), so without this a
            # rate-limit hit becomes a scored failure and depresses the result. Honor the
            # server's Retry-After when present. Other 4xx stay hard failures (real client
            # errors). Exhausted retries fall through and return the status (caller fails it).
            if response.status_code == 429 or response.status_code >= 500:
                transient_errors.append(f"attempt {attempt}: HTTP {response.status_code}")
                if response.status_code >= 500 and is_model_output_rejection(raw_response):
                    # #152: the engine rejected the model's output. Deterministic —
                    # a retry reproduces the same generation and the same
                    # rejection — so return it now and let the caller classify.
                    transient_errors[-1] += " (engine rejected model output; not retried)"
                    return (
                        response.status_code,
                        raw_response,
                        _with_stream(_transient_trace(transient_errors, attempt), stream),
                    )
                if attempt < max_attempts:
                    retry_after = (
                        self._retry_after_seconds(response)
                        if response.status_code == 429
                        else None
                    )
                    self._sleep_before_transient_retry(
                        attempt,
                        retry_after=retry_after,
                        status_code=response.status_code,
                    )
                    continue

            # Spend guard: accumulate reported usage (cloud-cost safety). Trips on
            # the cumulative cap mid-run; _SpendGuardExceeded propagates past the
            # per-scenario except-Exception handlers to the top-level run() caller.
            self.tokens_used += int((raw_response.get("usage") or {}).get("total_tokens") or 0)
            if self.max_total_tokens is not None and self.tokens_used > self.max_total_tokens:
                raise _SpendGuardExceeded(self.tokens_used, self.max_total_tokens)

            return (
                response.status_code,
                raw_response,
                _with_stream(_transient_trace(transient_errors, attempt), stream),
            )

        raise AssertionError("unreachable transient retry loop exit")


    def _verify_aider_start_with_progress(
        self,
        sandbox_client: SandboxClient,
        scenario: dict,
        start_kwargs: dict,
    ) -> dict:
        import threading

        done = threading.Event()
        holder: dict = {}

        def _target() -> None:
            try:
                holder["payload"] = sandbox_client.verify_multiturn_start(scenario, **start_kwargs)
            except BaseException as exc:  # noqa: BLE001
                holder["exc"] = exc
            finally:
                done.set()

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        seen: set[str] = set()
        while not done.wait(self.aider_progress_poll_s):
            self._poll_aider_progress(sandbox_client, seen)
        self._poll_aider_progress(sandbox_client, seen)
        thread.join()
        if "exc" in holder:
            raise holder["exc"]
        return holder.get("payload") or {}

    def _poll_aider_progress(self, sandbox_client: SandboxClient, seen: set[str]) -> None:
        if self._on_progress_event is None:
            return
        try:
            payload = sandbox_client.verify_progress()
        except Exception:  # noqa: BLE001
            return
        completed = payload.get("completed_exercises")
        if not isinstance(completed, list):
            return
        total = int(payload.get("total_expected") or 0)
        for item in completed:
            if not isinstance(item, dict):
                continue
            exercise_id = str(item.get("id") or "")
            if not exercise_id or exercise_id in seen:
                continue
            seen.add(exercise_id)
            event = dict(item)
            event.setdefault("pack_id", "aider-polyglot-30")
            event["index"] = len(seen)
            event["total"] = total
            self._on_progress_event(event)

    @staticmethod
    def _inject_transient_trace(result: ScenarioResult, trace: dict | None) -> ScenarioResult:
        if not trace:
            return result
        existing = dict(result.verifier_trace) if isinstance(result.verifier_trace, dict) else {}
        existing.update(trace)
        return replace(result, verifier_trace=existing)

    @staticmethod
    def _sleep_before_transient_retry(
        attempt: int,
        retry_after: float | None = None,
        status_code: int | None = None,
    ) -> None:
        # A minute-window throttle needs materially longer recovery than a brief
        # connection/server error. Honor Retry-After first; otherwise let three
        # default 429 retries span 70 seconds (10 + 20 + 40) while preserving the
        # existing short exponential schedule for other transient failures.
        if retry_after is not None and retry_after > 0:
            delay = min(float(retry_after), 120.0)
        elif status_code == 429:
            delay = min(10 * (2 ** (attempt - 1)), 60.0)
        else:
            delay = 2 ** (attempt - 1)
        if delay > 0:
            time.sleep(delay)

    @staticmethod
    def _retry_after_seconds(response) -> float | None:
        """Parse a 429/503 `Retry-After` header (delta-seconds form), else None.

        Defensive: test doubles / responses without `.headers` → None (backoff).
        HTTP-date form is unsupported and falls back to exponential backoff.
        """
        headers = getattr(response, "headers", None) or {}
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if not raw:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _message_from_response(raw_response: dict) -> dict:
        choices = raw_response.get("choices") if isinstance(raw_response, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                return dict(message)
        return {"role": "assistant", "content": ""}

    @staticmethod
    def _finish_reason(raw_response: dict | None) -> str | None:
        choices = raw_response.get("choices") if isinstance(raw_response, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            reason = choices[0].get("finish_reason")
            return reason if isinstance(reason, str) else None
        return None

    @staticmethod
    def _completion_tokens(raw_response: dict | None) -> int | None:
        usage = raw_response.get("usage") if isinstance(raw_response, dict) else None
        if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int):
            return usage["completion_tokens"]
        return None

    @staticmethod
    def _tool_calls_from_message(message: dict) -> list[dict]:
        calls = message.get("tool_calls")
        return calls if isinstance(calls, list) else []

    def _run_multiturn_scenario(
        self,
        meta: dict,
        scenario: dict,
        sandbox_client: SandboxClient,
        initial_request: dict,
        sampling: dict,
        repeat_index: int,
        timeout: float,
    ) -> ScenarioRun:
        started = time.perf_counter()
        status_code: int | None = None
        raw_responses: list[dict] = []
        transient_trace: dict | None = None
        assistant_messages: list[dict] = []
        tool_calls: list[dict] = []
        tokens_total = 0
        usage_sum: dict[str, int | None] = {}  # #147: prompt/total/reasoning across turns
        state_id: str | None = None
        ended = False
        truncated_turn: int | None = None

        try:
            start_kwargs: dict = {}
            pack_id = scenario.get("pack_id")
            if pack_id == "hermesagent-20":
                # Hermes upstream agent-runner makes its own model calls; pass
                # the runner's endpoint + model so the sandbox can spawn the
                # upstream agent against the same target the runner is benching.
                # Sampling is included so upstream's request_overrides
                # (temperature, top_p, max_tokens, etc.) match runner defaults.
                #
                # Endpoint resolution: loopback endpoints can never be Docker
                # or k8s service names, so rewrite them unconditionally. Keep
                # BENCHLOCAL_HERMES_RESOLVE_LOCALHOST as the opt-in for
                # non-loopback hosts where deployment topology is ambiguous.
                from benchlocal_cli.sandbox import endpoint_is_loopback, resolve_endpoint_for_container
                hermes_endpoint = self.endpoint
                if (
                    endpoint_is_loopback(self.endpoint)
                    or os.environ.get("BENCHLOCAL_HERMES_RESOLVE_LOCALHOST") == "1"
                ):
                    hermes_endpoint = resolve_endpoint_for_container(self.endpoint)
                start_kwargs = {
                    "model_endpoint": hermes_endpoint,
                    "model_name": self.model,
                    "model_api_key": self.api_key or "dummy",  # forward the real key for cloud endpoints; "dummy" for local vLLM (doesn't validate)
                    "sampling": dict(sampling),
                    # HermesAgent makes its own model calls, so the resolved
                    # thinking mode and arm budget must cross the sandbox
                    # protocol explicitly. They are not generation overrides.
                    "enable_thinking": _request_thinking_enabled(
                        sampling,
                        self.thinking_control,
                        False,
                    ),
                    "thinking_budget": self.thinking_max_tokens,
                    "preserve_reasoning_history": self.preserve_reasoning_history,
                }
                if self.thinking_control != THINKING_CONTROL_ENABLE:
                    start_kwargs["thinking_extra_body"] = _thinking_extra_body(
                        sampling, self.thinking_control
                    )
            elif pack_id == "aider-polyglot-30":
                # v0.9.0: aider needs a container-reachable URL. Apply the
                # endpoint resolver (rewrites localhost → host.docker.internal).
                from benchlocal_cli.sandbox import resolve_endpoint_for_container
                start_kwargs = {
                    "model_endpoint": resolve_endpoint_for_container(self.endpoint),
                    "model_name": self.model,
                    "model_api_key": self.api_key or "benchlocal-cli-aider-polyglot",  # forward the real key for cloud endpoints; placeholder for local
                    "sampling": dict(sampling),
                }
                if self.thinking_control != THINKING_CONTROL_ENABLE:
                    start_kwargs["thinking_extra_body"] = _thinking_extra_body(
                        sampling, self.thinking_control
                    )
            if pack_id == "aider-polyglot-30" and self._on_progress_event is not None:
                start_payload = self._verify_aider_start_with_progress(sandbox_client, scenario, start_kwargs)
            else:
                start_payload = sandbox_client.verify_multiturn_start(scenario, **start_kwargs)
            if start_payload.get("action") == "verify-final":
                latency = time.perf_counter() - started
                # Preserve upstream forensics in the early-out path too —
                # otherwise hermes scenarios that grade on the first call lose
                # their `trace` payload (toolEvents, finalResponse, etc.)
                # before reaching the saved JSON.
                early_trace: dict | None = None
                if isinstance(start_payload, dict):
                    early_trace = {
                        k: v for k, v in start_payload.items()
                        if k not in (
                            "passed", "failure_mode", "detail", "action",
                            # v0.9.0: pass_rate / passed_count / total_count are
                            # promoted to first-class ScenarioResult fields
                            # (Codex 2nd-pass #1) — don't double-include.
                            "pass_rate", "passed_count", "total_count",
                        )
                    } or None
                result = ScenarioResult(
                    scenario_id=scenario["id"],
                    passed=bool(start_payload.get("passed")),
                    failure_mode=start_payload.get("failure_mode", "verifier_fail"),
                    detail=str(start_payload.get("detail", "")),
                    latency_seconds=latency,
                    verifier_trace=early_trace,
                    pass_rate=start_payload.get("pass_rate"),
                    passed_count=start_payload.get("passed_count"),
                    total_count=start_payload.get("total_count"),
                )
                result = self._inject_sandbox_log_file(result, scenario.get("pack_id"))
                return self._scenario_run(
                    scenario,
                    {"multi_turn": True, "responses": [], "final_verifier_payload": start_payload},
                    initial_request,
                    sampling,
                    None,
                    result,
                    repeat_index,
                    response_field_used="multi_turn",
                    turn_count=0,
                )
            state_id = str(start_payload.get("scenario_state_id") or "")
            if not state_id:
                latency = time.perf_counter() - started
                result = ScenarioResult(
                    scenario["id"],
                    False,
                    "server_error",
                    "multi-turn sandbox did not return scenario_state_id",
                    latency_seconds=latency,
                )
                return self._scenario_run(
                    scenario,
                    {"multi_turn": True, "responses": [], "final_verifier_payload": start_payload},
                    initial_request,
                    sampling,
                    None,
                    result,
                    repeat_index,
                    response_field_used="multi_turn",
                    turn_count=0,
                )
            history = list(start_payload.get("prompt") or scenario.get("messages", []))
            tools = start_payload.get("tools") if isinstance(start_payload.get("tools"), list) else []
            max_turns = self._max_turns_for(scenario)
            result: ScenarioResult | None = None
            final_payload: dict | None = None

            for _turn in range(1, max_turns + 1):
                request_history = (
                    history
                    if self.preserve_reasoning_history
                    else strip_reasoning_history(history)
                )
                request = build_chat_request(request_history, sampling, self.model, tools=tools)
                try:
                    status_code, raw_response, turn_transient_trace = self._post_chat(request, timeout)
                    transient_trace = _merge_transient_trace(transient_trace, turn_transient_trace)
                except _TransientPostFailure as exc:
                    transient_trace = _merge_transient_trace(transient_trace, exc.trace)
                    result = ScenarioResult(scenario["id"], False, exc.failure_mode, exc.detail)
                    break
                except httpx.TimeoutException:
                    result = ScenarioResult(scenario["id"], False, "timeout", f"timed out after {timeout}s")
                    break
                except httpx.HTTPError as exc:
                    result = ScenarioResult(scenario["id"], False, "http_error", str(exc))
                    break

                raw_response = sanitize_response_text_fields(raw_response)
                raw_responses.append(raw_response)
                if status_code >= 500:
                    failure_mode, detail = classify_server_error(status_code, raw_response)  # #152
                    result = ScenarioResult(scenario["id"], False, failure_mode, detail)
                    break
                if status_code >= 400:
                    result = ScenarioResult(scenario["id"], False, "http_error", f"HTTP {status_code}")
                    break

                token_count = self._completion_tokens(raw_response)
                if token_count is not None:
                    tokens_total += token_count
                _add_usage(usage_sum, raw_response)

                assistant_message = self._message_from_response(raw_response)
                assistant_messages.append(assistant_message)
                history.append(assistant_message)
                tool_calls.extend(self._tool_calls_from_message(assistant_message))

                if self._finish_reason(raw_response) == "length":
                    # The turn was cut off at the token cap, so any tool call in
                    # it is partial. Executing it is wrong, and sending it back
                    # as history is worse: llama.cpp validates tool-call
                    # arguments in incoming history and answers 500, which read
                    # as the model's fault (#152). Stop here and grade what the
                    # completed turns did, as a max-turns ending does.
                    truncated_turn = _turn
                    end_payload = sandbox_client.verify_multiturn_end(state_id)
                    final_payload = end_payload
                    ended = True
                    result = ScenarioResult(
                        scenario_id=scenario["id"],
                        passed=bool(end_payload.get("passed")),
                        failure_mode=end_payload.get("failure_mode", "verifier_fail"),
                        detail=str(end_payload.get("detail", "")),
                    )
                    break

                turn_payload = sandbox_client.verify_multiturn_turn(state_id, raw_response)
                action = turn_payload.get("action")
                if action == "verify-final":
                    final_payload = turn_payload
                    ended = True
                    result = ScenarioResult(
                        scenario_id=scenario["id"],
                        passed=bool(turn_payload.get("passed")),
                        failure_mode=turn_payload.get("failure_mode", "verifier_fail"),
                        detail=str(turn_payload.get("detail", "")),
                    )
                    break
                if action != "next-prompt":
                    result = ScenarioResult(
                        scenario["id"],
                        False,
                        "server_error",
                        f"unexpected sandbox action: {action}",
                    )
                    break
                next_prompt = turn_payload.get("prompt")
                if isinstance(next_prompt, list):
                    history.extend(next_prompt)
                next_tools = turn_payload.get("tools")
                if isinstance(next_tools, list):
                    tools = next_tools
            else:
                end_payload = sandbox_client.verify_multiturn_end(state_id)
                final_payload = end_payload
                ended = True
                result = ScenarioResult(
                    scenario_id=scenario["id"],
                    passed=bool(end_payload.get("passed")),
                    failure_mode=end_payload.get("failure_mode", "timeout"),
                    detail=str(end_payload.get("detail", "")),
                )

            if result is None:
                result = ScenarioResult(scenario["id"], False, "server_error", "multi-turn loop exited without result")
            if (
                truncated_turn is not None
                and not result.passed
                and result.failure_mode in _TRUNCATED_TURN_RECLASSIFY_MODES
            ):
                result = replace(
                    result,
                    failure_mode="token_limit",
                    detail=(
                        f"turn {truncated_turn} truncated at token limit (finish_reason=length); "
                        f"episode stopped there and graded; underlying verdict was "
                        f"{result.failure_mode}: {result.detail}"
                    ),
                )
        finally:
            if state_id and not ended:
                with suppress(Exception):
                    sandbox_client.verify_multiturn_end(state_id)

        latency = time.perf_counter() - started
        # Extract the upstream verifier trace from the final payload (or
        # whichever sandbox response was authoritative) for post-run forensics.
        verifier_trace: dict | None = None
        if isinstance(final_payload, dict):
            verifier_trace = {
                k: v for k, v in final_payload.items()
                if k not in ("passed", "failure_mode", "detail", "action")
            } or None
        if truncated_turn is not None:
            verifier_trace = {**(verifier_trace or {}), "truncated_turn": truncated_turn}
        result = replace(
            result,
            latency_seconds=latency,
            tokens_completion=tokens_total if tokens_total else None,
            tokens_prompt=usage_sum.get("tokens_prompt"),
            tokens_total=usage_sum.get("tokens_total"),
            tokens_reasoning=usage_sum.get("tokens_reasoning"),
            verifier_trace=verifier_trace,
        )
        result = self._inject_transient_trace(result, transient_trace)
        result = self._inject_sandbox_log_file(result, scenario.get("pack_id"))
        raw_response: dict = {
            "multi_turn": True,
            "responses": raw_responses,
            "final_verifier_payload": final_payload,
        }
        return self._scenario_run(
            scenario,
            raw_response,
            initial_request,
            sampling,
            status_code,
            result,
            repeat_index,
            response_field_used="multi_turn",
            turn_count=len(assistant_messages),
            assistant_messages=assistant_messages,
            tool_calls=tool_calls,
            conversation=history,
        )

    @staticmethod
    def _scenario_run(
        scenario: dict,
        raw_response: dict | None,
        request: dict,
        sampling: dict,
        status_code: int | None,
        result: ScenarioResult,
        repeat_index: int,
        response_field_used: str | None = None,
        turn_count: int | None = None,
        assistant_messages: list[dict] | None = None,
        tool_calls: list[dict] | None = None,
        conversation: list[dict] | None = None,
    ) -> ScenarioRun:
        return ScenarioRun(
            id=scenario["id"],
            result=result,
            raw_scenario=scenario,
            raw_response=raw_response,
            request=request,
            sampling_params=sampling,
            status_code=status_code,
            repeat_index=repeat_index,
            response_field_used=response_field_used,
            turn_count=turn_count,
            assistant_messages=assistant_messages or [],
            tool_calls=tool_calls or [],
            conversation=conversation or [],
        )
