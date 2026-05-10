"""Delta comparison between two RunResult JSONs.

Used by `benchlocal-cli run --previous-result PATH` to classify each
scenario as regression / fix / stable / new / dropped vs a prior run.

Per Codex review of the v0.8 brief:
- Scenario keying is `(pack_id, scenario_id)` not bare scenario_id (#1)
- Multi-repeat aggregates to per-(pack,scenario) pass-rate; threshold
  configurable via BENCHLOCAL_DELTA_PASS_THRESHOLD env (default 0.5) (#2)
- Markdown delta column rendered ONLY when --previous-result was passed
  (the cli.py callsite handles that) — preserves byte-stable output for
  pinned downstream parsers (#4)
- Schema-version mismatch produces a warning, not a refusal (#9)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


PASS_THRESHOLD = float(os.environ.get("BENCHLOCAL_DELTA_PASS_THRESHOLD", "0.5"))


@dataclass
class PackDelta:
    """Per-pack scenario classification counts vs a previous result."""
    pack_id: str
    regressions: int = 0
    fixes: int = 0
    stable_pass: int = 0
    stable_fail: int = 0
    new: int = 0
    dropped: int = 0
    regressions_list: list[str] = field(default_factory=list)
    fixes_list: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pack_id": self.pack_id,
            "regressions": self.regressions,
            "fixes": self.fixes,
            "stable_pass": self.stable_pass,
            "stable_fail": self.stable_fail,
            "new": self.new,
            "dropped": self.dropped,
            "regressions_list": self.regressions_list,
            "fixes_list": self.fixes_list,
        }

    @property
    def status(self) -> str:
        if self.regressions:
            return "regression"
        if self.fixes:
            return "improved"
        return "stable"


@dataclass
class RunDelta:
    """Aggregate delta across all packs."""
    previous_path: str
    schema_version_match: bool
    total_regressions: int = 0
    total_fixes: int = 0
    total_stable_pass: int = 0
    total_stable_fail: int = 0
    total_new: int = 0
    total_dropped: int = 0
    by_pack: list[PackDelta] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "previous_path": self.previous_path,
            "schema_version_match": self.schema_version_match,
            "total_regressions": self.total_regressions,
            "total_fixes": self.total_fixes,
            "total_stable_pass": self.total_stable_pass,
            "total_stable_fail": self.total_stable_fail,
            "total_new": self.total_new,
            "total_dropped": self.total_dropped,
            "by_pack": [d.to_dict() for d in self.by_pack],
            "warnings": self.warnings,
        }


def _scenario_pass_rate(runs: list[dict]) -> float:
    """Codex review #2: when --repeat N > 1, aggregate to per-scenario pass-rate.
    Same scenario id may appear multiple times (one per repeat). Pass-rate ≥
    PASS_THRESHOLD (default 0.5) → considered "passed" for delta classification.
    """
    if not runs:
        return 0.0
    passes = sum(1 for r in runs if r.get("passed"))
    return passes / len(runs)


def _build_scenario_map(packs: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Build {(pack_id, scenario_id): [run_dicts]} from a RunResult.to_dict()."""
    out: dict[tuple[str, str], list[dict]] = {}
    for pack in packs or []:
        pack_id = pack.get("pack_id") or "unknown"
        for run in pack.get("scenarios") or []:
            key = (pack_id, run.get("id") or "?")
            out.setdefault(key, []).append(run)
    return out


def load_previous_result(path: str | Path) -> dict:
    """Read a saved RunResult JSON. Returns the dict; raises on missing/invalid."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"--previous-result not found: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def classify(current: dict, previous_path: str | Path) -> RunDelta:
    """Compare current run dict to a previously-saved RunResult JSON."""
    previous = load_previous_result(previous_path)

    delta = RunDelta(
        previous_path=str(previous_path),
        schema_version_match=(
            current.get("schema_version") == previous.get("schema_version")
        ),
    )

    if not delta.schema_version_match:
        delta.warnings.append(
            f"schema_version mismatch (current={current.get('schema_version')!r}, "
            f"previous={previous.get('schema_version')!r}); proceeding best-effort"
        )

    current_map = _build_scenario_map(current.get("packs") or [])
    previous_map = _build_scenario_map(previous.get("packs") or [])

    # Index per-pack deltas by pack_id for accumulation
    pack_deltas: dict[str, PackDelta] = {}

    def _ensure(pack_id: str) -> PackDelta:
        if pack_id not in pack_deltas:
            pack_deltas[pack_id] = PackDelta(pack_id=pack_id)
        return pack_deltas[pack_id]

    # Walk current scenarios → classify against previous
    for (pack_id, scenario_id), runs in current_map.items():
        cur_pass = _scenario_pass_rate(runs) >= PASS_THRESHOLD
        prev_runs = previous_map.get((pack_id, scenario_id))
        d = _ensure(pack_id)
        if prev_runs is None:
            d.new += 1
            continue
        prev_pass = _scenario_pass_rate(prev_runs) >= PASS_THRESHOLD
        if cur_pass and prev_pass:
            d.stable_pass += 1
        elif cur_pass and not prev_pass:
            d.fixes += 1
            d.fixes_list.append(scenario_id)
        elif not cur_pass and prev_pass:
            d.regressions += 1
            d.regressions_list.append(scenario_id)
        else:
            d.stable_fail += 1

    # Walk previous → find scenarios dropped from current
    for (pack_id, scenario_id), _ in previous_map.items():
        if (pack_id, scenario_id) not in current_map:
            d = _ensure(pack_id)
            d.dropped += 1

    # Aggregate totals
    delta.by_pack = sorted(pack_deltas.values(), key=lambda p: p.pack_id)
    for d in delta.by_pack:
        delta.total_regressions += d.regressions
        delta.total_fixes += d.fixes
        delta.total_stable_pass += d.stable_pass
        delta.total_stable_fail += d.stable_fail
        delta.total_new += d.new
        delta.total_dropped += d.dropped

    return delta


def has_regressions(delta: RunDelta | None) -> bool:
    """True if --exit-on-regression should fire."""
    return delta is not None and delta.total_regressions > 0
