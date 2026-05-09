"""Core orchestrator for pack loading, endpoint calls, scoring, and aggregation."""

from __future__ import annotations

import importlib
import json
import statistics
import time
from dataclasses import replace
from datetime import datetime, timezone
from importlib import resources

import httpx

from benchlocal_cli import __version__
from benchlocal_cli.types import PackResult, RunResult, ScenarioResult, ScenarioRun

PACK_MODES = {
    "quick": ["toolcall-15", "instructfollow-15"],
    "medium": ["toolcall-15", "instructfollow-15", "structoutput-15", "dataextract-15"],
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


def build_request(scenario: dict, meta: dict, model: str) -> tuple[dict, dict]:
    sampling = dict(meta.get("sampling_defaults", {}))
    sampling.update(scenario.get("sampling_overrides") or {})
    request = {"model": model, "messages": scenario["messages"], **sampling}
    if scenario.get("tools"):
        request["tools"] = scenario["tools"]
    return request, sampling


def _chat_url(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/v1/chat/completions"):
        return endpoint
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
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.timeout_per_case = timeout_per_case
        self.enable_sandboxed_packs = enable_sandboxed_packs
        self.mock_responses = mock_responses or {}

    def run(self, pack_ids: list[str], *, mode: str = "custom", repeat: int = 1) -> RunResult:
        started_at = _utc_now()
        warnings: list[str] = []
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
            warnings=warnings,
        )

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
        request, sampling = build_request(scenario, meta, self.model)
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
        module_name = scenario.get("verifier", {}).get("type") or meta.get("verifier_module")
        module = importlib.import_module(f"benchlocal_cli.scoring.{module_name}")
        result = module.score_scenario(scenario, raw_response)
        latency = time.perf_counter() - started if scenario["id"] in self.mock_responses else latency
        tokens = None
        usage = raw_response.get("usage") if isinstance(raw_response, dict) else None
        if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int):
            tokens = usage["completion_tokens"]
        result = replace(result, latency_seconds=latency, tokens_completion=tokens)
        return self._scenario_run(scenario, raw_response, request, sampling, status_code, result, repeat_index)

    @staticmethod
    def _scenario_run(
        scenario: dict,
        raw_response: dict | None,
        request: dict,
        sampling: dict,
        status_code: int | None,
        result: ScenarioResult,
        repeat_index: int,
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
        )
