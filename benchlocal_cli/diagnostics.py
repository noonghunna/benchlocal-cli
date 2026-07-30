"""Aggregate completion and extraction diagnostics for saved pack results."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


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


def pack_diagnostics(runs: Iterable[Any]) -> dict[str, Any] | None:
    """Summarize all saved completions, including nested retry/multi-turn calls."""

    finish_reasons: Counter[str] = Counter()
    extraction_methods: Counter[str] = Counter()
    extraction_issues: Counter[str] = Counter()
    response_fields: Counter[str] = Counter()

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
    return diagnostics or None
