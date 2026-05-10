"""Core orchestrator for pack loading, endpoint calls, scoring, and aggregation."""

from __future__ import annotations

import importlib
import json
import signal
import statistics
import time
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
        for client in list(self._sandbox_clients.values()):
            client.stop()
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
    def _scenario_run(
        scenario: dict,
        raw_response: dict | None,
        request: dict,
        sampling: dict,
        status_code: int | None,
        result: ScenarioResult,
        repeat_index: int,
        response_field_used: str | None = None,
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
        )
