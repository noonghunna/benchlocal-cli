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
    # #61: completion hit the token cap (finish_reason == "length") and was
    # truncated mid-output — distinct from a content verdict so "overthought /
    # looped" is legible vs "ran to completion but wrong" (verifier_fail).
    "token_limit",
    "timeout",
    "agent_loop_exhausted",
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
    # v0.9.0: first-class pass-rate metrics for "scoreboard" packs that
    # internally aggregate multiple sub-results into one scenario verdict
    # (e.g., aider-polyglot-30 runs 30 exercises and reports the aggregate).
    # All optional / default None; non-scoreboard packs leave these unset.
    # Codex 2nd-pass review #1: promote out of `verifier_trace` so v0.8
    # `--previous-result` delta + markdown output can render real
    # "23/30 → 20/30 (-10pp)" deltas instead of just threshold flips.
    pass_rate: float | None = None
    passed_count: int | None = None
    total_count: int | None = None

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
    thinking_enabled: bool = False
    variance: dict[str, float | int | None] | None = None
    # Present only for a selected subset. scenario_count is the selected count;
    # this records the pack's complete catalog size for honest human rendering.
    catalog_scenario_count: int | None = None

    def to_dict(self) -> dict:
        out = {
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
            "thinking_enabled": self.thinking_enabled,
            "variance": self.variance,
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
        }
        if self.catalog_scenario_count is not None:
            out["catalog_scenario_count"] = self.catalog_scenario_count
        return out


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
    thinking_mode: str = "pack-defaults"
    warnings: list[str] = field(default_factory=list)
    # v0.8: populated when `--previous-result PATH` was passed to the run.
    # None means delta wasn't computed (the default; preserves saved-JSON
    # back-compat with v0.7.x readers per Codex review #9).
    delta: dict | None = None
    # v0.9.1: CLI-level sampling overrides (--temperature, --top-p, etc.).
    # None means the run used the pack's default sampling (canonical).
    # Non-None means the run traded reproducibility for recommended-temp
    # evaluation; results should NOT be compared to the temp=0 baseline.
    sampling_overrides: dict | None = None
    # v0.9.2: --sampling-from-server (#21): "server" when sampling was
    # inherited from the serving config; None otherwise. Mutually exclusive
    # with sampling_overrides in practice (enforced by cli.py).
    sampling_source: str | None = None
    # v0.9.2: the actual server defaults read back from /props (llama.cpp)
    # or None if the endpoint didn't expose them (vLLM). Only populated
    # when sampling_source == "server".
    server_defaults: dict | None = None
    # Ordered, pack-qualified IDs for targeted runs. Optional/additive so schema
    # version 1 readers remain compatible with ordinary and historical results.
    selection: list[str] | None = None

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
            "thinking_mode": self.thinking_mode,
            "warnings": self.warnings,
        }
        if self.delta is not None:
            out["delta"] = self.delta
        if self.sampling_overrides is not None:
            out["sampling_overrides"] = self.sampling_overrides
        if self.sampling_source is not None:
            out["sampling_source"] = self.sampling_source
        if self.server_defaults is not None:
            out["server_defaults"] = self.server_defaults
        if self.selection is not None:
            out["selection"] = self.selection
        return out
