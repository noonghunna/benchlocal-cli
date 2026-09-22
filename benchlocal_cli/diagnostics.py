"""Aggregate completion and extraction diagnostics for saved pack results."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from benchlocal_cli.types import RUNAWAY_FAILURE_MODES

# #148: display/serialisation order for the runaway modes — the issue's own
# order, most common first. Any mode added to RUNAWAY_FAILURE_MODES later
# without a slot here is appended alphabetically rather than dropped.
_RUNAWAY_MODE_ORDER: tuple[str, ...] = ("token_limit", "timeout", "agent_runner_timeout")


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _result_value(run: Any, key: str, default: Any = None) -> Any:
    result = _get(run, "result")
    value = _get(result, key) if result is not None else None
    return _get(run, key, default) if value is None else value


def _iter_attempts(run: Any) -> Iterable[Any]:
    yield run
    attempts = _get(run, "retry_attempts", [])
    if isinstance(attempts, list):
        yield from (attempt for attempt in attempts if isinstance(attempt, Mapping))


def _iter_responses(raw_response: Any) -> Iterable[Mapping[str, Any]]:
    if not isinstance(raw_response, Mapping):
        return
    responses = raw_response.get("responses")
    if isinstance(responses, list):
        for response in responses:
            yield from _iter_responses(response)
        return
    yield raw_response


def _finish_reason(response: Mapping[str, Any]) -> str | None:
    choices = response.get("choices")
    if not (isinstance(choices, list) and choices and isinstance(choices[0], Mapping)):
        return None
    value = choices[0].get("finish_reason")
    return str(value) if value not in (None, "") else "unknown"


def _sorted_counts(values: Counter[str]) -> dict[str, int]:
    return {key: values[key] for key in sorted(values)}


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, -(-95 * len(ordered) // 100) - 1)]  # nearest rank


def _stream_summary(first_chunks: list[float], gaps: list[float], requests: int) -> dict[str, Any] | None:
    """#157: liveness over every streamed request in the pack. None when the
    run did not stream, so non-streaming output is unchanged."""
    if not requests:
        return None
    summary: dict[str, Any] = {"requests": requests}
    if gaps:
        summary["max_gap_s"] = max(gaps)
        summary["p95_gap_s"] = _p95(gaps)
    if first_chunks:
        summary["first_chunk_p50_s"] = sorted(first_chunks)[(len(first_chunks) - 1) // 2]
        summary["first_chunk_max_s"] = max(first_chunks)
    return summary


def pack_diagnostics(runs: Iterable[Any]) -> dict[str, Any] | None:
    """Summarize all saved completions, including nested retry/multi-turn calls."""

    finish_reasons: Counter[str] = Counter()
    extraction_methods: Counter[str] = Counter()
    extraction_issues: Counter[str] = Counter()
    response_fields: Counter[str] = Counter()
    stream_first: list[float] = []
    stream_gaps: list[float] = []
    stream_requests = 0

    for run in runs:
        for attempt in _iter_attempts(run):
            for response in _iter_responses(_get(attempt, "raw_response")):
                reason = _finish_reason(response)
                if reason is not None:
                    finish_reasons[reason] += 1

            trace = _result_value(attempt, "verifier_trace")
            if not isinstance(trace, Mapping):
                trace = {}
            method = trace.get("extraction_method")
            issue = trace.get("extraction_issue")
            source = trace.get("response_field_used") or _get(attempt, "response_field_used")
            if method:
                extraction_methods[str(method)] += 1
            if issue:
                extraction_issues[str(issue)] += 1
            if source:
                response_fields[str(source)] += 1
            stream = trace.get("stream")
            if isinstance(stream, Mapping):
                stream_requests += int(stream.get("requests") or 0)
                stream_first.extend(v for v in stream.get("first_chunk_s") or [] if isinstance(v, (int, float)))
                stream_gaps.extend(v for v in stream.get("max_gap_s") or [] if isinstance(v, (int, float)))

    diagnostics: dict[str, Any] = {}
    total = sum(finish_reasons.values())
    if total:
        length = finish_reasons.get("length", 0)
        diagnostics["finish_reasons"] = {
            "total": total,
            "length": length,
            "length_rate": length / total,
            "counts": _sorted_counts(finish_reasons),
        }
    if extraction_methods or extraction_issues or response_fields:
        diagnostics["extraction"] = {
            "methods": _sorted_counts(extraction_methods),
            "issues": _sorted_counts(extraction_issues),
            "response_fields": _sorted_counts(response_fields),
        }
    stream_summary = _stream_summary(stream_first, stream_gaps, stream_requests)
    if stream_summary is not None:
        diagnostics["stream"] = stream_summary
    return diagnostics or None


def _runaway_modes() -> list[str]:
    ordered = [mode for mode in _RUNAWAY_MODE_ORDER if mode in RUNAWAY_FAILURE_MODES]
    ordered.extend(sorted(RUNAWAY_FAILURE_MODES - set(ordered)))
    return ordered


def runaway_summary(runs: Iterable[Any]) -> dict[str, Any] | None:
    """#148: count the attempt-1 rows that never produced an answer.

    `verifier_fail` means the model answered and was wrong; `token_limit` /
    `timeout` / `agent_runner_timeout` mean it never finished. Both score as a
    fail — this does NOT touch the arithmetic — but a run whose losses are
    budget artifacts should say so where a human reads it.

    Counts exactly the rows the score counts: the top-level (attempt-1) result
    of each run, `verifier_not_implemented` excluded, so `count / total` is
    directly comparable to `passed / total`. Nested inline retries are ignored
    on purpose: pass@1 is charged for attempt 1, and so is this. For a
    single-scoreboard pack (aider) the rows are batches, not exercises, so
    `total` there is the batch count rather than the pack's `total`.

    Accepts ScenarioRun objects or their saved-JSON dicts. None when there are
    no counted rows (skipped / stubbed pack).
    """
    modes = dict.fromkeys(_runaway_modes(), 0)
    total = 0
    for run in runs:
        failure_mode = _result_value(run, "failure_mode")
        if failure_mode == "verifier_not_implemented":
            continue
        total += 1
        if bool(_result_value(run, "passed", False)):
            continue
        if failure_mode in modes:
            modes[str(failure_mode)] += 1
    if not total:
        return None
    count = sum(modes.values())
    return {"count": count, "total": total, "rate": count / total, "modes": modes}


def combine_runaway(summaries: Iterable[Mapping[str, Any] | None]) -> dict[str, Any] | None:
    """#148: sum per-pack runaway rollups into the run-level one."""
    present = [summary for summary in summaries if isinstance(summary, Mapping)]
    if not present:
        return None
    modes = dict.fromkeys(_runaway_modes(), 0)
    for summary in present:
        for mode, value in (summary.get("modes") or {}).items():
            modes[str(mode)] = modes.get(str(mode), 0) + int(value or 0)
    count = sum(int(summary.get("count") or 0) for summary in present)
    total = sum(int(summary.get("total") or 0) for summary in present)
    return {
        "count": count,
        "total": total,
        "rate": count / total if total else 0.0,
        "modes": modes,
    }
