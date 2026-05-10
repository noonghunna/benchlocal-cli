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
    # Full upstream verifier payload — preserved for post-run forensics.
    # For sandboxed packs, this is the complete dict returned by the
    # upstream JS runtime (rawLog, notes, score, verifier subscores, etc.)
    # so a failure can be diagnosed without re-running the scenario. None
    # for in-process verifiers and for runs where the payload is unavailable.
    verifier_trace: dict | None = None

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
    turn_count: int | None = None
    assistant_messages: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    # Full conversation history for multi-turn scenarios — list of OpenAI-shape
    # messages (system, user, assistant, tool) across all turns. Empty for
    # single-turn scenarios where the request `messages` field already captures
    # the full input. Populated by the runner's multi-turn loop.
    conversation: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["passed"] = self.result.passed
        data["failure_mode"] = self.result.failure_mode
        data["detail"] = self.result.detail
        data["latency_seconds"] = self.result.latency_seconds
        data["tokens_completion"] = self.result.tokens_completion
        data["verifier_trace"] = self.result.verifier_trace
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
    # v0.8: populated when `--previous-result PATH` was passed to the run.
    # None means delta wasn't computed (the default; preserves saved-JSON
    # back-compat with v0.7.x readers per Codex review #9).
    delta: dict | None = None

    def to_dict(self) -> dict:
        out = {
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
        if self.delta is not None:
            out["delta"] = self.delta
        return out
