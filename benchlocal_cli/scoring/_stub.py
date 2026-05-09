"""Stub scoring for execution-backed packs not yet wired up.

Used by:
    - BugFind-15        (needs Docker sandbox to execute candidate fixes)
    - HermesAgent-20    (needs multi-tool harness: browser, cron, memory, artifact, trace)
    - CLI-40            (needs Linux exec sandbox)

When a runner attempts to score one of these packs, it returns:

    {
        "passed": False,
        "failure_mode": "verifier_not_implemented",
        "detail": "BugFind-15 requires Docker sandbox; install with `pip install benchlocal-cli[sandbox]` "
                  "and set `--enable-sandboxed-packs` to run.",
    }

This lets us ship pack content (scenarios, sampling defaults, expected outcomes)
in JSONL today, while deferring verifier infrastructure to a follow-up.

When the sandbox is implemented, replace this stub call with the real verifier
in the runner's pack-dispatch table.
"""

from __future__ import annotations

from benchlocal_cli.types import ScenarioResult


def score_scenario(scenario: dict, response: dict) -> ScenarioResult:
    """Return a verifier-not-implemented result for sandboxed packs."""
    pack = scenario.get("pack_id", "unknown")
    return ScenarioResult(
        scenario_id=str(scenario.get("id", "unknown")),
        passed=False,
        failure_mode="verifier_not_implemented",
        detail=(
            f"{pack} requires sandboxed verifier infrastructure not yet wired up. "
            f"See benchlocal_cli/scoring/_stub.py module docstring."
        ),
    )
