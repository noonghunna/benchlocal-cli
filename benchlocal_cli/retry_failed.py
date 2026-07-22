"""Failure-conditioned retry diagnostics for saved benchmark results."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from benchlocal_cli.delta import load_previous_result

if TYPE_CHECKING:
    from benchlocal_cli.types import RunResult


def _qualified_runs(result: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for pack in result.get("packs") or []:
        pack_id = str(pack.get("pack_id") or "")
        if not pack_id:
            continue
        for run in pack.get("scenarios") or []:
            scenario_id = str(run.get("id") or "")
            if scenario_id:
                grouped.setdefault(f"{pack_id}/{scenario_id}", []).append(run)
    return grouped


def failed_selection(result: dict) -> list[str]:
    """Return scenarios that failed their first (pass@1) arm, in result order."""
    failed: list[str] = []
    for qualified_id, runs in _qualified_runs(result).items():
        baseline = min(
            runs,
            key=lambda run: int(run.get("repeat_index") or 1),
        )
        if (
            not bool(baseline.get("passed"))
            and baseline.get("failure_mode") != "verifier_not_implemented"
        ):
            failed.append(qualified_id)
    return failed


def load_retry_context(path: str | Path, attempts: int) -> tuple[dict, dict]:
    if attempts < 1:
        raise ValueError("--retry-failed must be at least 1")
    baseline = load_previous_result(path)
    grouped = _qualified_runs(baseline)
    if not grouped:
        raise ValueError("--previous-result contains no scenario results")
    if any(
        int(run.get("repeat_index") or 1) > 1
        for runs in grouped.values()
        for run in runs
    ):
        raise ValueError(
            "--retry-failed requires a pass@1 result; --repeat results already characterize variance"
        )
    selection = failed_selection(baseline)
    totals = baseline.get("totals") or {}
    context = {
        "source": str(path),
        "attempts_per_scenario": attempts,
        "baseline_totals": {
            "passed": int(totals.get("passed") or 0),
            "total": int(totals.get("total") or 0),
            "score": float(totals.get("score") or 0.0),
        },
        "selection": selection,
    }
    return baseline, context


def build_retry_diagnostic(result: RunResult, context: dict) -> dict:
    grouped: dict[str, list] = {}
    for pack in result.packs:
        for run in pack.scenarios:
            grouped.setdefault(f"{pack.pack_id}/{run.id}", []).append(run)

    scenarios: list[dict] = []
    systematic = 0
    flaky = 0
    not_run = 0
    for qualified_id in context.get("selection") or []:
        runs = [
            run for run in grouped.get(qualified_id, [])
            if run.result.failure_mode != "verifier_not_implemented"
        ]
        passed = sum(1 for run in runs if run.result.passed)
        total = len(runs)
        if total == 0:
            classification = "not-run"
            not_run += 1
        elif passed == 0:
            classification = "systematic"
            systematic += 1
        else:
            # The baseline arm failed, so any passing retry proves inconsistency.
            classification = "flaky"
            flaky += 1
        pack_id, scenario_id = qualified_id.split("/", 1)
        scenarios.append(
            {
                "pack_id": pack_id,
                "scenario_id": scenario_id,
                "retry_passed": passed,
                "retry_total": total,
                "classification": classification,
            }
        )

    return {
        "source": str(context.get("source") or ""),
        "attempts_per_scenario": int(context.get("attempts_per_scenario") or 0),
        "baseline_totals": dict(context.get("baseline_totals") or {}),
        "failed_scenario_count": len(scenarios),
        "systematic": systematic,
        "flaky": flaky,
        "not_run": not_run,
        "scenarios": scenarios,
    }
