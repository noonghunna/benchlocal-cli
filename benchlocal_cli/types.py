"""Shared result types for runner and deterministic scorers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

FailureMode = Literal[
    "passed",
    "verifier_fail",
    "wrong_answer",
    "invalid_json",
    "no_answer_found",
    "missing_field",
    "extra_fields",
    "schema_violation",
    "wrong_structure",
    "timeout",
    "http_error",
    "server_error",
    "verifier_not_implemented",
]


@dataclass
class ScenarioResult:
    scenario_id: str
    passed: bool
    failure_mode: FailureMode
    detail: str
    latency_seconds: float = 0.0
    tokens_completion: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScenarioRun:
    id: str
    result: ScenarioResult
    raw_scenario: dict
    raw_response: dict | None
    request: dict
    sampling_params: dict
    status_code: int | None
    repeat_index: int = 1
    response_field_used: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["passed"] = self.result.passed
        data["failure_mode"] = self.result.failure_mode
        data["detail"] = self.result.detail
        data["latency_seconds"] = self.result.latency_seconds
        data["tokens_completion"] = self.result.tokens_completion
        return data


@dataclass
class PackResult:
    pack_id: str
    version: str
    upstream_commit: str
    scenario_count: int
    passed: int
    total: int
    score: float
    latency: dict[str, float | None]
    scenarios: list[ScenarioRun] = field(default_factory=list)
    skipped: bool = False
    status: str = "ok"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pack_id": self.pack_id,
            "version": self.version,
            "upstream_commit": self.upstream_commit,
            "scenario_count": self.scenario_count,
            "passed": self.passed,
            "total": self.total,
            "score": self.score,
            "latency": self.latency,
            "skipped": self.skipped,
            "status": self.status,
            "warnings": self.warnings,
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
        }


@dataclass
class RunResult:
    schema_version: str
    runner_version: str
    endpoint: str
    model: str
    mode: str
    started_at: str
    finished_at: str
    packs: list[PackResult]
    totals: dict[str, float | int]
    thinking_enabled: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "runner_version": self.runner_version,
            "endpoint": self.endpoint,
            "model": self.model,
            "mode": self.mode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "packs": [pack.to_dict() for pack in self.packs],
            "totals": self.totals,
            "thinking_enabled": self.thinking_enabled,
            "warnings": self.warnings,
        }
