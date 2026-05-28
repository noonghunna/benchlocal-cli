"""Core orchestrator for pack loading, endpoint calls, scoring, and aggregation."""

from __future__ import annotations

import importlib
import json
import os
import signal
import statistics
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import datetime, timezone
from importlib import resources

import httpx

from benchlocal_cli import __version__
from benchlocal_cli.sandbox import SandboxClient, config_for_pack
from benchlocal_cli.scoring.common import content_with_source, sanitize_response_text_fields
from benchlocal_cli.types import PackResult, RunResult, ScenarioResult, ScenarioRun

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
) -> tuple[dict, dict]:
    sampling = dict(meta.get("sampling_defaults", {}))
    scenario_overrides = scenario.get("sampling_overrides") or {}
    if extra_body:
        sampling.update(extra_body)
    resolved_thinking = resolve_thinking_enabled(meta, thinking_enabled)
    sampling["chat_template_kwargs"] = {
        **dict(sampling.get("chat_template_kwargs") or {}),
        "enable_thinking": resolved_thinking,
    }
    sampling.update(scenario_overrides)
    request_thinking = bool(
        dict(sampling.get("chat_template_kwargs") or {}).get("enable_thinking", resolved_thinking)
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
    return {
        "transient_retries": int(existing.get("transient_retries") or 0)
        + int(new.get("transient_retries") or 0),
        "transient_errors": list(existing.get("transient_errors") or [])
        + list(new.get("transient_errors") or []),
    }


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


class Runner:
    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        timeout_per_case: float | None = None,
        enable_sandboxed_packs: bool = False,
        mock_responses: dict[str, dict] | None = None,
        thinking_enabled: bool | None = None,
        thinking_max_tokens: int = 16384,
        extra_body: dict | None = None,
        sandbox_image_tag: str = "latest",
        sandbox_log_dir: str | None = None,
        max_transient_retries: int = 3,
        sampling_overrides: dict | None = None,
        sampling_from_server: bool = False,
        thinking_sampler: dict | None = None,
        on_pack_complete: Callable[[PackResult], None] | None = None,
        on_scenario_complete: Callable[[ScenarioRun, int, int], None] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.timeout_per_case = None if timeout_per_case is None else float(timeout_per_case)
        self.enable_sandboxed_packs = enable_sandboxed_packs
        self.mock_responses = mock_responses or {}
        self.thinking_override = thinking_enabled
        self.thinking_enabled = bool(thinking_enabled)
        self.thinking_mode = thinking_mode_from_override(thinking_enabled)
        self.thinking_max_tokens = thinking_max_tokens
        self.extra_body = extra_body or {}
        self.sandbox_image_tag = sandbox_image_tag
        # If set, sandbox container stderr/stdout is captured to
        # `<sandbox_log_dir>/sandbox-<pack_id>.log` before container teardown.
        # See SandboxClient.stop(log_dir=...) for the snapshot.
        self.sandbox_log_dir = sandbox_log_dir
        self.max_transient_retries = max(0, int(max_transient_retries))
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

    def run(self, pack_ids: list[str], *, mode: str = "custom", repeat: int = 1) -> RunResult:
        started_at = _utc_now()
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
            # --sampling-from-server (#21): read server defaults before
            # any requests so we can tag the run and record what was used.
            if self.sampling_from_server:
                self._server_defaults = self._read_server_defaults(warnings)
            self._start_sandboxes(pack_ids, warnings)
            pack_results: list[PackResult] = []
            for pack_id in pack_ids:
                pack_result = self.run_pack(pack_id, repeat=repeat, warnings=warnings)
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
                warnings=warnings,
                sampling_overrides=dict(self.sampling_overrides) if self.sampling_overrides else None,
                sampling_source="server" if self.sampling_from_server else None,
                server_defaults=self._server_defaults if self.sampling_from_server else None,
            )
        finally:
            self._stop_sandboxes()
            signal.signal(signal.SIGINT, old_sigint)
            signal.signal(signal.SIGTERM, old_sigterm)

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
                resp = client.get(props_url)
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
                    )
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
            except Exception as exc:
                msg = (
                    f"skipping {pack_id}: sandbox unavailable ({exc}). "
                    f"Hint: ensure Docker is running and `bash tools/build-sandboxes.sh` has been run; "
                    f"or use --medium for the deterministic-only subset (no Docker needed)."
                )
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
        return float(value)

    def _timeout_budget_for_scenario(self, meta: dict, scenario: dict) -> float:
        if self.timeout_per_case is not None:
            return self.timeout_per_case
        value = (
            scenario.get("max_seconds_override")
            or meta.get("timeout_per_case_default")
            or meta.get("default_max_seconds")
            or DEFAULT_TIMEOUT_PER_CASE
        )
        return float(value)

    def run_pack(self, pack_id: str, *, repeat: int = 1, warnings: list[str] | None = None) -> PackResult:
        meta, scenarios = load_pack(pack_id)
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
            )

        runs: list[ScenarioRun] = []
        total_scenarios = len(scenarios) * repeat
        scenario_index = 0
        for repeat_index in range(1, repeat + 1):
            for scenario in scenarios:
                scenario_index += 1
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
                    variance=_repeat_variance(runs, repeat),
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
            variance=_repeat_variance(runs, repeat),
        )

    def run_scenario(self, meta: dict, scenario: dict, *, repeat_index: int = 1) -> ScenarioRun:
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
        )
        timeout = self._timeout_budget_for_scenario(meta, scenario)
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
        ):
            return self._run_multiturn_scenario(
                meta,
                scenario,
                sandbox_client,
                request,
                sampling,
                repeat_index,
                timeout,
            )

        if scenario["id"] in self.mock_responses:
            raw_response = self.mock_responses[scenario["id"]]
            latency = 0.0
        else:
            try:
                status_code, raw_response, transient_trace = self._post_chat(request, timeout)
                latency = time.perf_counter() - started
                if status_code >= 500:
                    result = ScenarioResult(scenario["id"], False, "server_error", f"HTTP {status_code}", latency)
                    result = self._inject_transient_trace(result, transient_trace)
                    return self._scenario_run(scenario, raw_response, request, sampling, status_code, result, repeat_index)
                if status_code >= 400:
                    result = ScenarioResult(scenario["id"], False, "http_error", f"HTTP {status_code}", latency)
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
        tokens = None
        usage = raw_response.get("usage") if isinstance(raw_response, dict) else None
        if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int):
            tokens = usage["completion_tokens"]
        result = replace(result, latency_seconds=latency, tokens_completion=tokens)
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

    def _post_chat(self, request: dict, timeout: float) -> tuple[int, dict, dict | None]:
        transient_errors: list[str] = []
        max_attempts = self.max_transient_retries + 1

        for attempt in range(1, max_attempts + 1):
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(_chat_url(self.endpoint), json=request)
            except httpx.TimeoutException as exc:
                transient_errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
                if attempt >= max_attempts:
                    trace = _transient_trace(transient_errors, attempt) or {}
                    raise _TransientPostFailure("timeout", f"timed out after {timeout}s", trace) from exc
                self._sleep_before_transient_retry(attempt)
                continue
            except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                transient_errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
                if attempt >= max_attempts:
                    trace = _transient_trace(transient_errors, attempt) or {}
                    raise _TransientPostFailure("http_error", str(exc), trace) from exc
                self._sleep_before_transient_retry(attempt)
                continue

            try:
                raw_response = response.json()
            except ValueError:
                raw_response = {"text": response.text}

            if response.status_code >= 500:
                transient_errors.append(f"attempt {attempt}: HTTP {response.status_code}")
                if attempt < max_attempts:
                    self._sleep_before_transient_retry(attempt)
                    continue

            return response.status_code, raw_response, _transient_trace(transient_errors, attempt)

        raise AssertionError("unreachable transient retry loop exit")

    @staticmethod
    def _inject_transient_trace(result: ScenarioResult, trace: dict | None) -> ScenarioResult:
        if not trace:
            return result
        existing = dict(result.verifier_trace) if isinstance(result.verifier_trace, dict) else {}
        existing.update(trace)
        return replace(result, verifier_trace=existing)

    @staticmethod
    def _sleep_before_transient_retry(attempt: int) -> None:
        delay = 2 ** (attempt - 1)
        if delay > 0:
            time.sleep(delay)

    @staticmethod
    def _message_from_response(raw_response: dict) -> dict:
        choices = raw_response.get("choices") if isinstance(raw_response, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                return dict(message)
        return {"role": "assistant", "content": ""}

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
        state_id: str | None = None
        ended = False

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
                # Endpoint resolution: opt-in via BENCHLOCAL_HERMES_RESOLVE_LOCALHOST=1.
                # When set, rewrites localhost/127.x/[::1] → host.docker.internal so
                # the hermes-agent inside the sandbox container can reach the runner's
                # host-side vLLM. Pairs with the --add-host flag already added by
                # sandbox.py:290-294. Default-off preserves existing hermes deployments
                # where service-name resolution (k8s, docker-compose internal) already
                # works without rewrite.
                hermes_endpoint = self.endpoint
                if os.environ.get("BENCHLOCAL_HERMES_RESOLVE_LOCALHOST") == "1":
                    from benchlocal_cli.sandbox import resolve_endpoint_for_container
                    hermes_endpoint = resolve_endpoint_for_container(self.endpoint)
                start_kwargs = {
                    "model_endpoint": hermes_endpoint,
                    "model_name": self.model,
                    "model_api_key": "dummy",  # vLLM doesn't validate; upstream still requires a value
                    "sampling": dict(sampling),
                }
            elif pack_id == "aider-polyglot-30":
                # v0.9.0: aider needs a container-reachable URL. Apply the
                # endpoint resolver (rewrites localhost → host.docker.internal).
                from benchlocal_cli.sandbox import resolve_endpoint_for_container
                start_kwargs = {
                    "model_endpoint": resolve_endpoint_for_container(self.endpoint),
                    "model_name": self.model,
                    "model_api_key": "benchlocal-cli-aider-polyglot",  # non-empty placeholder
                    "sampling": dict(sampling),
                }
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
                request = build_chat_request(history, sampling, self.model, tools=tools)
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
                    result = ScenarioResult(scenario["id"], False, "server_error", f"HTTP {status_code}")
                    break
                if status_code >= 400:
                    result = ScenarioResult(scenario["id"], False, "http_error", f"HTTP {status_code}")
                    break

                token_count = self._completion_tokens(raw_response)
                if token_count is not None:
                    tokens_total += token_count

                assistant_message = self._message_from_response(raw_response)
                assistant_messages.append(assistant_message)
                history.append(assistant_message)
                tool_calls.extend(self._tool_calls_from_message(assistant_message))

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
        result = replace(
            result,
            latency_seconds=latency,
            tokens_completion=tokens_total if tokens_total else None,
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
