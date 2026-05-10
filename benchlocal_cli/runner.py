"""Core orchestrator for pack loading, endpoint calls, scoring, and aggregation."""

from __future__ import annotations

import importlib
import json
import signal
import statistics
import time
from contextlib import suppress
from dataclasses import replace
from datetime import datetime, timezone
from importlib import resources

import httpx

from benchlocal_cli import __version__
from benchlocal_cli.sandbox import SandboxClient, config_for_pack
from benchlocal_cli.scoring.common import content_with_source
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
}

# Modes that require Docker sandbox containers. The runner will auto-enable
# sandboxed packs (no flag needed) and fail loud if Docker isn't available.
SANDBOX_MODES = {"full"}

# Just the sandboxed packs — used by `--sandboxed-only` for debug iteration
# on the verifier containers without paying the deterministic-pack cost.
SANDBOXED_PACK_IDS = ["bugfind-15", "hermesagent-20", "cli-40"]


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


def build_request(
    scenario: dict,
    meta: dict,
    model: str,
    *,
    thinking_enabled: bool = False,
    thinking_max_tokens: int = 4096,
    extra_body: dict | None = None,
) -> tuple[dict, dict]:
    sampling = dict(meta.get("sampling_defaults", {}))
    scenario_overrides = scenario.get("sampling_overrides") or {}
    if extra_body:
        sampling.update(extra_body)
    if thinking_enabled:
        sampling["chat_template_kwargs"] = {
            **dict(sampling.get("chat_template_kwargs") or {}),
            "enable_thinking": True,
        }
    else:
        sampling["chat_template_kwargs"] = {
            **dict(sampling.get("chat_template_kwargs") or {}),
            "enable_thinking": False,
        }
    sampling.update(scenario_overrides)
    if thinking_enabled:
        sampling["max_tokens"] = thinking_max_tokens
    request = {"model": model, "messages": scenario["messages"], **sampling}
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


class Runner:
    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        timeout_per_case: float = 60.0,
        enable_sandboxed_packs: bool = False,
        mock_responses: dict[str, dict] | None = None,
        thinking_enabled: bool = False,
        thinking_max_tokens: int = 4096,
        extra_body: dict | None = None,
        sandbox_image_tag: str = "latest",
        sandbox_log_dir: str | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.timeout_per_case = timeout_per_case
        self.enable_sandboxed_packs = enable_sandboxed_packs
        self.mock_responses = mock_responses or {}
        self.thinking_enabled = thinking_enabled
        self.thinking_max_tokens = thinking_max_tokens
        self.extra_body = extra_body or {}
        self.sandbox_image_tag = sandbox_image_tag
        # If set, sandbox container stderr/stdout is captured to
        # `<sandbox_log_dir>/sandbox-<pack_id>.log` before container teardown.
        # See SandboxClient.stop(log_dir=...) for the snapshot.
        self.sandbox_log_dir = sandbox_log_dir
        self._sandbox_clients: dict[str, SandboxClient] = {}

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
            self._start_sandboxes(pack_ids, warnings)
            pack_results = [self.run_pack(pack_id, repeat=repeat, warnings=warnings) for pack_id in pack_ids]
            total = sum(pack.total for pack in pack_results)
            passed = sum(pack.passed for pack in pack_results)
            finished_at = _utc_now()
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
                warnings=warnings,
            )
        finally:
            self._stop_sandboxes()
            signal.signal(signal.SIGINT, old_sigint)
            signal.signal(signal.SIGTERM, old_sigterm)

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
                client = SandboxClient(config_for_pack(pack_id, self.sandbox_image_tag))
                client.start()
                self._sandbox_clients[pack_id] = client
            except Exception as exc:
                msg = (
                    f"skipping {pack_id}: sandbox unavailable ({exc}). "
                    f"Hint: ensure Docker is running and `bash tools/build-sandboxes.sh` has been run; "
                    f"or use --medium for the deterministic-only subset (no Docker needed)."
                )
                warnings.append(msg)
                print(f"⚠️  {msg}", file=sys.stderr, flush=True)

    def _stop_sandboxes(self) -> None:
        # Capture container logs before docker-rm wipes them — useful for
        # post-run forensics on sandbox-side errors (verifier exceptions,
        # upstream Node tracebacks, mock-marker warnings).
        log_dir = self.sandbox_log_dir
        for client in list(self._sandbox_clients.values()):
            client.stop(log_dir=log_dir)
        self._sandbox_clients.clear()

    def run_pack(self, pack_id: str, *, repeat: int = 1, warnings: list[str] | None = None) -> PackResult:
        meta, scenarios = load_pack(pack_id)
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
            )

        runs: list[ScenarioRun] = []
        for repeat_index in range(1, repeat + 1):
            for scenario in scenarios:
                runs.append(self.run_scenario(meta, scenario, repeat_index=repeat_index))

        counted = [run for run in runs if run.result.failure_mode != "verifier_not_implemented"]
        passed = sum(1 for run in counted if run.result.passed)
        total = len(counted)
        latencies = [run.result.latency_seconds for run in counted if run.result.latency_seconds > 0]
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
        )

    def run_scenario(self, meta: dict, scenario: dict, *, repeat_index: int = 1) -> ScenarioRun:
        request, sampling = build_request(
            scenario,
            meta,
            self.model,
            thinking_enabled=self.thinking_enabled,
            thinking_max_tokens=self.thinking_max_tokens,
            extra_body=self.extra_body,
        )
        scenario_timeout = scenario.get("max_seconds_override") or meta.get("default_max_seconds") or self.timeout_per_case
        timeout = min(float(scenario_timeout), float(self.timeout_per_case))
        started = time.perf_counter()
        status_code: int | None = None
        raw_response: dict | None = None

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
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(_chat_url(self.endpoint), json=request)
                latency = time.perf_counter() - started
                status_code = response.status_code
                try:
                    raw_response = response.json()
                except ValueError:
                    raw_response = {"text": response.text}
                if response.status_code >= 500:
                    result = ScenarioResult(scenario["id"], False, "server_error", f"HTTP {response.status_code}", latency)
                    return self._scenario_run(scenario, raw_response, request, sampling, status_code, result, repeat_index)
                if response.status_code >= 400:
                    result = ScenarioResult(scenario["id"], False, "http_error", f"HTTP {response.status_code}", latency)
                    return self._scenario_run(scenario, raw_response, request, sampling, status_code, result, repeat_index)
            except httpx.TimeoutException:
                latency = time.perf_counter() - started
                result = ScenarioResult(scenario["id"], False, "timeout", f"timed out after {timeout}s", latency)
                return self._scenario_run(scenario, None, request, sampling, None, result, repeat_index)
            except httpx.HTTPError as exc:
                latency = time.perf_counter() - started
                result = ScenarioResult(scenario["id"], False, "http_error", str(exc), latency)
                return self._scenario_run(scenario, None, request, sampling, None, result, repeat_index)

        assert raw_response is not None
        response_field_used = content_with_source(raw_response)[1]
        if meta.get("supports_sandboxed_only") and scenario.get("pack_id") in self._sandbox_clients:
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

    def _post_chat(self, request: dict, timeout: float) -> tuple[int, dict]:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(_chat_url(self.endpoint), json=request)
        try:
            raw_response = response.json()
        except ValueError:
            raw_response = {"text": response.text}
        return response.status_code, raw_response

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
        assistant_messages: list[dict] = []
        tool_calls: list[dict] = []
        tokens_total = 0
        state_id: str | None = None
        ended = False

        try:
            start_kwargs: dict = {}
            if scenario.get("pack_id") == "hermesagent-20":
                # Hermes upstream agent-runner makes its own model calls; pass
                # the runner's endpoint + model so the sandbox can spawn the
                # upstream agent against the same target the runner is benching.
                # Sampling is included so upstream's request_overrides
                # (temperature, top_p, max_tokens, etc.) match runner defaults.
                start_kwargs = {
                    "model_endpoint": self.endpoint,
                    "model_name": self.model,
                    "model_api_key": "dummy",  # vLLM doesn't validate; upstream still requires a value
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
                        if k not in ("passed", "failure_mode", "detail", "action")
                    } or None
                result = ScenarioResult(
                    scenario_id=scenario["id"],
                    passed=bool(start_payload.get("passed")),
                    failure_mode=start_payload.get("failure_mode", "verifier_fail"),
                    detail=str(start_payload.get("detail", "")),
                    latency_seconds=latency,
                    verifier_trace=early_trace,
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
                    status_code, raw_response = self._post_chat(request, timeout)
                except httpx.TimeoutException:
                    result = ScenarioResult(scenario["id"], False, "timeout", f"timed out after {timeout}s")
                    break
                except httpx.HTTPError as exc:
                    result = ScenarioResult(scenario["id"], False, "http_error", str(exc))
                    break

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
